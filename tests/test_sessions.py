"""Representative sessions the curated modules serve: every module exists, every
topic is registered and wired. The sessions are the installer's (the client
config it generates, config/client_local.generated.yaml) at both ends of its
range; clients' own example configs live with them (huri-cli: its config/).

config/client_template.yaml, the generic session template huri-core's errors
point to, is a ClientConfig too, and carries no client's device section."""

from pathlib import Path

import pytest
import yaml
from omegaconf import OmegaConf

from huri_core.core.compat import check_session
from huri_core.core.dataclasses.config import ClientConfig
from huri_core.core.plugins import installed_modules, load_events, load_modules

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "config" / "client_template.yaml"

AUDIO = {"block_duration": 0.03}
SESSIONS = {
    # Text in, text out: what a host with no audio stack runs.
    "text": {
        "send": ["huri.question"],
        "subscribe": ["huri.token"],
        "modules": {"rag": {"name": "rag", "args": {"language": "en"}}},
    },
    # Voice in, voice + emotion + gesture out: the installer's full plan.
    "full": {
        "send": ["huri.audio.in", "huri.question"],
        "subscribe": ["huri.token", "huri.audio.out", "huri.motion"],
        "modules": {
            "vad": {"name": "vad", "args": {"vad_agressiveness": 3, **AUDIO}},
            "stt": {"name": "stt", "args": {"language": "en", **AUDIO}},
            "tag": {"name": "tag", "args": {"min_prefix": 3}},
            "emo": {"name": "emo", "args": AUDIO},
            "eag": {"name": "eag"},
            "qag": {"name": "qag", "args": {"use_emotion": True}},
            "rag": {"name": "rag", "args": {"language": "en"}},
            "tts": {"name": "tts"},
            "mov": {"name": "mov"},
        },
    },
    # Voice in, text and voice out, without emotion: QAG must not wait for one.
    "voice": {
        "send": ["huri.audio.in", "huri.question"],
        "subscribe": ["huri.token", "huri.audio.out"],
        "modules": {
            "vad": {"name": "vad", "args": AUDIO},
            "stt": {"name": "stt", "args": AUDIO},
            "tag": {"name": "tag"},
            "qag": {"name": "qag", "args": {"use_emotion": False}},
            "rag": {"name": "rag"},
            "tts_cosyvoice": {"name": "tts_cosyvoice"},
        },
    },
}


def session(raw):
    return ClientConfig.from_dict(
        {"huri_url": "ws://localhost:8000/session", **raw, "user_id": "test"}
    )


def stub(cls):
    """An instance without running __init__ (no handle, no model)."""
    return cls.__new__(cls)


@pytest.mark.parametrize("name", SESSIONS)
def test_curated_modules_serve_the_session(name):
    config = session(SESSIONS[name])
    modules = load_modules(installed_modules())
    pipeline = [stub(modules[m.key]) for m in config.modules.values()]
    check_session(pipeline, sent=config.send, subscribed=config.subscribe)


@pytest.mark.parametrize("name", SESSIONS)
def test_the_session_uses_registered_topics(name):
    config = session(SESSIONS[name])
    events = load_events()
    used = config.send + config.subscribe
    assert used and all(topic in events for topic in used), used


def test_the_template_is_a_generic_client_config():
    raw = OmegaConf.to_container(OmegaConf.load(TEMPLATE), resolve=True)
    config = ClientConfig.from_dict({**raw, "user_id": "test"})
    assert config.send and config.subscribe and config.modules
    # Device sections belong to a client (huri-cli reads `cli:`), not here.
    assert set(yaml.safe_load(TEMPLATE.read_text())) == {
        "huri_url",
        "send",
        "subscribe",
        "modules",
    }
