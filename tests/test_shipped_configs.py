"""The shipped client configs (config/client_*.yaml) are sessions the curated
modules can serve: every module exists, every topic is registered and wired."""

from pathlib import Path

import pytest
from omegaconf import OmegaConf

from huri_core.core.compat import check_session
from huri_core.core.dataclasses.config import ClientConfig
from huri_core.core.plugins import installed_modules, load_events, load_modules

ROOT = Path(__file__).resolve().parents[1]
# Not the template, nor what the installer writes (gitignored, may be absent).
CLIENT_CONFIGS = sorted(
    p
    for p in (ROOT / "config").glob("client_*.yaml")
    if "template" not in p.name and not p.name.endswith(".generated.yaml")
)


def load(path):
    raw = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    return ClientConfig.from_dict({**raw, "user_id": "test"})


def stub(cls):
    """An instance without running __init__ (no handle, no model)."""
    return cls.__new__(cls)


def test_there_are_shipped_configs():
    assert {p.name for p in CLIENT_CONFIGS} >= {"client_full.yaml", "client_text.yaml"}


@pytest.mark.parametrize("path", CLIENT_CONFIGS, ids=lambda p: p.name)
def test_shipped_client_configs_are_complete_sessions(path):
    config = load(path)
    modules = load_modules(installed_modules())
    pipeline = [stub(modules[m.key]) for m in config.modules.values()]
    check_session(pipeline, sent=config.send, subscribed=config.subscribe)


@pytest.mark.parametrize("path", CLIENT_CONFIGS, ids=lambda p: p.name)
def test_shipped_client_configs_use_registered_topics(path):
    config = load(path)
    events = load_events()
    used = config.send + config.subscribe
    assert used and all(topic in events for topic in used), used
