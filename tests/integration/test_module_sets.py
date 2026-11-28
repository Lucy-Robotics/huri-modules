"""Any module set the installer accepts answers, by voice and by text.

The installer (scripts/install_local.sh) refuses a --modules list that cannot
answer (module_set_problems) and writes the client config matching the one it
accepts (client_config). Each of the 511 non-empty sets of the curated modules
goes through the first; each accepted one, with the client config written for
it, goes through the server's websocket endpoint in-process: real module
classes, real bus, fake models (fakes.py).
"""

import asyncio
import itertools
import json
import shutil
import struct
import subprocess
from pathlib import Path

import huri_vad.module
import numpy as np
import pytest
import yaml

from huri_core.core.huri import HuRI
from huri_core.core.manifest import load_manifest
from huri_core.core.plugins import load_events, load_modules
from huri_core.testing import LocalHandle
from tests.integration.fakes import (
    FakeEmotionModel,
    FakeGestures,
    FakeLLM,
    FakeVoice,
    FakeWhisper,
    Scripted,
)

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install_local.sh"
CURATED = ["vad", "stt", "tag", "emo", "eag", "qag", "rag", "tts", "mov"]

# @serve.ingress subclasses the class it decorates; its direct base is HuRI as
# written, which runs without Serve.
Server = HuRI.func_or_class.__mro__[1]

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="the installer is a bash script"
)


def section(text, start, end):
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def installer(body, **variables):
    """Run `body` after the installer's module-set functions."""
    text = INSTALLER.read_text()
    script = (
        "set -Eeuo pipefail\n"
        + "".join(f'{k}="{v}"\n' for k, v in variables.items())
        + section(text, "tts_module() {", "# --- ROCm helpers")
        + section(text, "client_config() {", "generate_configs() {")
        + body
    )
    return subprocess.run(
        ["bash", "-c", script], check=True, capture_output=True, text=True
    ).stdout


def all_sets():
    for n in range(1, len(CURATED) + 1):
        yield from (",".join(s) for s in itertools.combinations(CURATED, n))


@pytest.fixture(scope="module")
def accepted():
    """The sets module_set_problems finds nothing wrong with."""
    out = installer(
        f"for s in {' '.join(all_sets())}; do\n"
        '  [[ -z "$(module_set_problems "$s")" ]] && echo "$s"\n'
        "done\n"
    )
    return out.split()


def test_the_installer_accepts_exactly_the_sets_that_answer(accepted):
    text = ["rag", "rag,tts", "rag,tts,mov"]
    voice = [
        "vad,stt,tag" + emotion + ",qag,rag" + out
        for emotion in ("", ",emo,eag")
        for out in ("", ",tts", ",tts,mov")
    ]
    assert sorted(accepted) == sorted(text + voice)


def test_a_refused_set_names_what_it_misses():
    out = installer('module_set_problems "vad,stt,emo,eag,qag,rag"\n')
    assert out.split("\n")[:2] == ["  stt needs tag", "  qag needs tag"]
    assert "rag" in installer('module_set_problems ""\n')


def hub(sets):
    return installer(
        f"for s in {' '.join(sets)}; do\n"
        '  p="$(module_set_problems "$s")"; echo "$s: $p"\n'
        "done\n",
        REPO_ROOT=str(ROOT),
    )


def test_hub_modules_are_accepted_when_something_feeds_them():
    out = hub(
        ["rag,think", "vad,stt,tag,qag,rag,think", "vad,stt,tag,qag,rag,tts,lips"]
    )
    assert [line.endswith(": ") for line in out.splitlines()] == [True] * 3


def test_a_hub_module_nothing_feeds_is_refused():
    out = hub(["think", "vad,stt,tag,qag,rag,lips"])
    assert "think needs huri.question" in out
    assert "lips needs huri.audio.out" in out


def test_the_installer_reads_the_ports_huri_core_reads():
    paths = sorted((ROOT / "packages").glob("*/*/huri_module.toml")) + sorted(
        (ROOT / "packages").glob("*/*/manifests/*/huri_module.toml")
    )
    assert paths
    manifests = [load_manifest(p) for p in paths]
    body = "".join(
        f'echo "{m.name} $(manifest_port {m.name} input)'
        f' $(manifest_port {m.name} output)"\n'
        for m in manifests
    )
    got = [
        line.split(" ") for line in installer(body, REPO_ROOT=str(ROOT)).splitlines()
    ]
    want = [
        [m.name, m.input.topic, m.output.topic if m.output else ""] for m in manifests
    ]
    assert got == want


# --- the accepted sets, through the server ------------------------------------

FRAME = np.full(480, 8000, dtype=np.int16).tobytes()  # 30 ms at 16 kHz
# Short windows so a few frames make a sentence; the rest comes from the
# generated config.
FAST = {
    "vad": {"silence_duration": 0.09},
    "stt": {"transcribe_window": 0.2, "transcribe_step": 0.1},
    "emo": {"analysis_window": 0.2},
    "mov": {"min_chunk_sec": 0.01},
}


def client_config(modules):
    raw = yaml.safe_load(
        installer("client_config\n", P_MODULES=modules, TTS_ENGINE="piper")
    )
    for name, extra in FAST.items():
        if name in raw["modules"]:
            raw["modules"][name].setdefault("args", {}).update(extra)
    # ${cli.audio_in.frame_duration}, resolved the way the client does.
    for module in raw["modules"].values():
        for key, value in (module.get("args") or {}).items():
            if value == "${cli.audio_in.frame_duration}":
                module["args"][key] = raw["cli"]["audio_in"]["frame_duration"]
    return raw


def frame(topic, payload):
    return struct.pack("!H", len(topic)) + topic.encode() + payload


class Client:
    """The websocket the endpoint talks to: the handshake, `incoming`, then
    it waits for the server to go quiet before it disconnects."""

    def __init__(self, handshake, incoming):
        self.handshake, self.incoming, self.sent = handshake, list(incoming), []

    async def accept(self):
        pass

    async def receive_json(self):
        return self.handshake

    async def receive(self):
        if self.incoming:
            await asyncio.sleep(0.002)
            message = self.incoming.pop(0)
            key = "bytes" if isinstance(message, bytes) else "text"
            return {"type": "websocket.receive", key: message}
        seen, quiet = -1, 0
        while quiet < 15:  # 0.3 s without anything new
            seen, quiet = len(self.sent), (quiet + 1 if len(self.sent) == seen else 0)
            await asyncio.sleep(0.02)
        return {"type": "websocket.disconnect"}

    async def send_json(self, message):
        self.sent.append(message)

    async def send_bytes(self, payload):
        n = struct.unpack("!H", payload[:2])[0]
        self.sent.append({"topic": payload[2 : 2 + n].decode()})

    async def close(self, code=None, reason=None):
        pass


def converse(raw, incoming, monkeypatch):
    """One session: the topics the server sent back, in order."""
    monkeypatch.setattr(huri_vad.module.webrtcvad, "Vad", lambda *_: Scripted(10))
    names = list(raw["modules"])
    fakes = {
        "stt": FakeWhisper(),
        "emo": FakeEmotionModel(),
        "rag": FakeLLM(),
        "tts": FakeVoice(),
        "mov": FakeGestures(),
    }
    server = Server(
        load_modules(names),
        {n: LocalHandle(fakes[n]) for n in names if n in fakes},
        dict(load_events()),
        [],
    )
    ws = Client({**raw, "user_id": "alice"}, incoming)
    asyncio.run(asyncio.wait_for(server.run_session(ws), timeout=20))
    assert ws.sent[0]["type"] == "session_init", ws.sent[0]
    return [m["topic"] for m in ws.sent[1:]]


SPOKEN = [frame("huri.audio.in", FRAME)] * 25
TYPED = [
    json.dumps(
        {
            "topic": "huri.question",
            "data": {"transcript": {"text": "hi", "end": True}, "emotion": None},
        }
    )
]


ACCEPTED_SETS = [
    "rag",
    "rag,tts",
    "rag,tts,mov",
    "vad,stt,tag,qag,rag",
    "vad,stt,tag,qag,rag,tts",
    "vad,stt,tag,qag,rag,tts,mov",
    "vad,stt,tag,emo,eag,qag,rag",
    "vad,stt,tag,emo,eag,qag,rag,tts",
    "vad,stt,tag,emo,eag,qag,rag,tts,mov",
]


def test_the_sets_below_are_the_accepted_ones(accepted):
    assert sorted(ACCEPTED_SETS) == sorted(accepted)


@pytest.mark.parametrize("modules", ACCEPTED_SETS)
def test_the_generated_client_config_answers_a_typed_question(modules, monkeypatch):
    raw = client_config(modules)
    assert sorted(raw["modules"]) == sorted(modules.split(","))
    assert "huri.question" in raw["send"]

    # Everything subscribed comes back, huri.question included: the server
    # passes on the question it got, typed or spoken.
    assert set(converse(raw, TYPED, monkeypatch)) == set(raw["subscribe"])


@pytest.mark.parametrize("modules", [m for m in ACCEPTED_SETS if "vad" in m])
def test_the_generated_client_config_answers_a_spoken_question(modules, monkeypatch):
    raw = client_config(modules)
    assert raw["send"] == ["huri.audio.in", "huri.question"]

    assert set(converse(raw, SPOKEN, monkeypatch)) == set(raw["subscribe"])


@pytest.mark.parametrize("modules", [m for m in ACCEPTED_SETS if "vad" not in m])
def test_without_vad_the_microphone_is_not_sent(modules):
    assert client_config(modules)["send"] == ["huri.question"]


@pytest.mark.parametrize("modules", [m for m in ACCEPTED_SETS if "vad" in m])
def test_the_generated_client_config_quiets_microphone_audio(modules):
    assert client_config(modules)["log"] == {"topics": {"huri.audio.in": "WARNING"}}


@pytest.mark.parametrize("modules", [m for m in ACCEPTED_SETS if "vad" not in m])
def test_without_vad_there_is_no_log_section(modules):
    assert "log" not in client_config(modules)
