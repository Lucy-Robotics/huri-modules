"""A module nobody has written yet works next to the curated ones.

Two ways a new module reaches a server, both built here in a temporary
directory, nothing installed:

- packaged: a distribution registering a module (`huri.modules` entry point)
  and an event of its own namespace (`acme.subtitle`), with its manifest;
- by import path (`acme_echo:Echo`), no packaging: here, a stand-in for RAG.

Each goes through the server's startup checks (huri_core.app.build_app's,
without Ray) and a session through its websocket endpoint, with the curated
modules around it.
"""

import asyncio
import json
import os
import textwrap
from pathlib import Path

import pytest
from huri_events.text import Token

from huri_core.core.compat import check_actuation, check_manifests, check_modules
from huri_core.core.huri import HuRI
from huri_core.core.plugins import (
    find_manifest,
    installed_modules,
    load_events,
    load_modules,
)
from huri_core.testing import LocalHandle

from .test_full_pipeline import FakeLLM, FakeVoice

ROOT = Path(__file__).resolve().parents[2]
Server = HuRI.func_or_class.__mro__[1]


def curated_core_range():
    """The huri_core range the curated manifests declare: a new module made
    today declares the same."""
    manifest = ROOT / "packages/huri-module-rag/huri_rag/manifests/rag/huri_module.toml"
    for line in manifest.read_text().splitlines():
        if line.startswith("huri_core"):
            return line.split("=", 1)[1].strip()
    raise AssertionError("no huri_core range in the RAG manifest")


SUBTITLES = '''
from dataclasses import dataclass

from huri_core.core.events import JsonEvent
from huri_core.core.module import Module
from huri_events.text import Token


@dataclass
class Subtitle(JsonEvent):
    text: str


class Subtitles(Module):
    """The streamed answer as one subtitle per reply."""

    input_type = "huri.token"
    output_type = "acme.subtitle"
    input_event = Token
    output_event = Subtitle

    def __init__(self, prefix=""):
        self.prefix = prefix
        self.parts = []

    async def process(self, token):
        if not token.end:
            self.parts.append(token.text)
            return None
        text, self.parts = "".join(self.parts).strip(), []
        return Subtitle(self.prefix + text) if text else None
'''

ECHO = '''
from huri_core.core.module import Module
from huri_events.question import RAGQuestion
from huri_events.text import Token


class Echo(Module):
    """Answers every question with the question itself."""

    input_type = "huri.question"
    output_type = "huri.token"
    input_event = RAGQuestion
    output_event = Token

    async def process(self, question):
        yield Token("Echo: " + question.transcript.text, False)
        yield Token("", True)
'''


@pytest.fixture
def new_modules(tmp_path, monkeypatch):
    """acme-subtitles 0.1.0 as pip would install it, and acme_echo.py alone."""
    package = tmp_path / "acme_subtitles"
    package.mkdir()
    (package / "__init__.py").write_text(SUBTITLES)
    (package / "huri_module.toml").write_text(textwrap.dedent(f"""\
            name = "subtitles"
            summary = "The answer as subtitles"
            input = {{ topic = "huri.token", event = "huri_events.text:Token" }}
            output = {{ topic = "acme.subtitle", event = "acme_subtitles:Subtitle" }}
            events = ["huri-events>=0.1,<0.3"]
            requires = {{ gpu = false, actuation = false }}
            huri_core = {curated_core_range()}
            """))
    dist = tmp_path / "acme_subtitles-0.1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: acme-subtitles\nVersion: 0.1.0\n"
    )
    (dist / "entry_points.txt").write_text(
        "[huri.modules]\nsubtitles = acme_subtitles:Subtitles\n\n"
        "[huri.events]\nacme.subtitle = acme_subtitles:Subtitle\n"
    )
    (tmp_path / "acme_echo.py").write_text(ECHO)
    monkeypatch.syspath_prepend(os.fspath(tmp_path))


def start_server(selection, handles):
    """build_app's checks, then the server, without Ray."""
    modules = load_modules(selection)
    events = load_events()
    check_modules(modules, events)
    manifests = {n: m for n in modules if (m := find_manifest(n)) is not None}
    check_manifests(modules, manifests, events)
    actuating = check_actuation(modules, manifests, [])
    return Server(modules, handles, dict(events), actuating)


class Client:
    def __init__(self, handshake, *incoming):
        self.handshake, self.incoming, self.sent = handshake, list(incoming), []

    async def accept(self):
        pass

    async def receive_json(self):
        return self.handshake

    async def receive(self):
        await asyncio.sleep(0.1)  # the previous message goes through the bus
        if not self.incoming:
            return {"type": "websocket.disconnect"}
        return {"type": "websocket.receive", "text": self.incoming.pop(0)}

    async def send_json(self, message):
        self.sent.append(message)

    async def send_bytes(self, payload):
        self.sent.append({"topic": "<bytes>"})

    async def close(self, code=None, reason=None):
        pass


def ask(server, modules, subscribe, text="hi"):
    question = {"transcript": {"text": text, "end": True}, "emotion": None}
    ws = Client(
        {
            "user_id": "alice",
            "huri_url": "ws://x",
            "send": ["huri.question"],
            "subscribe": subscribe,
            "modules": modules,
        },
        json.dumps({"topic": "huri.question", "data": question}),
    )
    asyncio.run(asyncio.wait_for(server.run_session(ws), timeout=10))
    return ws.sent


def test_a_packaged_module_is_found_like_a_curated_one(new_modules):
    assert "subtitles" in installed_modules()
    assert find_manifest("subtitles").output.topic == "acme.subtitle"
    assert "acme.subtitle" in load_events()


def test_a_packaged_module_with_its_own_event_joins_the_pipeline(new_modules):
    llm = FakeLLM()
    server = start_server(
        ["rag", "tts", "subtitles"],
        {
            "rag": LocalHandle(llm),
            "tts": LocalHandle(FakeVoice()),
        },
    )
    sent = ask(
        server,
        {
            "rag": {"name": "rag"},
            "tts": {"name": "tts"},
            "subtitles": {"name": "subtitles", "args": {"prefix": "> "}},
        },
        subscribe=["huri.token", "huri.audio.out", "acme.subtitle"],
    )

    assert sent[0]["type"] == "session_init"
    subtitles = [m["data"] for m in sent if m.get("topic") == "acme.subtitle"]
    assert subtitles == [{"text": "> You said: hi"}]
    # The curated modules around it are unaffected.
    assert any(m.get("topic") == "<bytes>" for m in sent)  # TTS audio
    assert llm.questions == ["hi"]


def test_a_module_by_import_path_stands_in_for_a_curated_one(new_modules):
    server = start_server(["acme_echo:Echo", "subtitles"], {})
    sent = ask(
        server,
        {
            "echo": {"module": "acme_echo:Echo"},
            "subtitles": {"name": "subtitles"},
        },
        subscribe=["huri.token", "acme.subtitle"],
    )

    tokens = [
        Token.from_wire(m["data"]) for m in sent if m.get("topic") == "huri.token"
    ]
    assert "".join(t.text for t in tokens) == "Echo: hi"
    assert [m["data"] for m in sent if m.get("topic") == "acme.subtitle"] == [
        {"text": "Echo: hi"}
    ]


def test_a_module_the_server_did_not_enable_is_refused_by_name(new_modules):
    server = start_server(["rag"], {"rag": LocalHandle(FakeLLM())})
    (error,) = ask(
        server,
        {"rag": {"name": "rag"}, "subtitles": {"name": "subtitles"}},
        subscribe=["huri.token", "acme.subtitle"],
    )
    assert error["type"] == "session_error"
    assert "'subtitles' is installed but not enabled" in error["error"]
    assert error["server_modules"] == ["rag"]
