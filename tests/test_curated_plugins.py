"""The curated events and modules, as the server discovers them: registered by
their own distributions (huri-events, huri-module-*), under the `huri.`
namespace, wired into one pipeline, and enabled only through HURI_MODULES."""

import os
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from huri_core.core import plugins
from huri_core.core.compat import check_modules
from huri_core.core.dataclasses.config import ModuleConfig
from huri_core.core.factory import ModuleFactory
from huri_core.core.module import Module

ROOT = Path(__file__).resolve().parents[1]
CURATED = {
    "vad",
    "stt",
    "tag",
    "emo",
    "eag",
    "qag",
    "rag",
    "mov",
    "tts",
    "tts_cosyvoice",
}


def distribution(group, name):
    (ep,) = [ep for ep in entry_points(group=group) if ep.name == name]
    return ep.dist.name.replace("_", "-").lower()


def test_curated_modules_and_events_are_registered_by_their_distributions():
    """Needs the packages installed (pip install -e packages/*), like the
    server: huri-core itself registers no module and no event."""
    modules = plugins.load_modules(["vad", "tag", "qag", "eag"])
    assert set(modules) == {"vad", "tag", "qag", "eag"}
    events = plugins.load_events()
    for module in modules.values():
        assert module.input_type in events
        assert module.output_type is None or module.output_type in events

    assert set(plugins.installed_modules()) >= CURATED
    for name in CURATED:
        assert distribution(plugins.MODULES_GROUP, name).startswith("huri-module-")
    for topic in events:
        if topic.startswith("huri."):
            assert distribution(plugins.EVENTS_GROUP, topic) == "huri-events"


def test_curated_event_topics_are_namespaced():
    events = plugins.load_events()
    assert events, "huri-events must be installed (pip install -e packages/*)"
    assert all(topic.startswith("huri.") for topic in events), sorted(events)


def test_curated_module_topics_are_namespaced():
    for name, module in plugins.load_modules(plugins.installed_modules()).items():
        topics = [module.input_type, module.output_type]
        assert all(t is None or t.startswith("huri.") for t in topics), name


def test_curated_modules_fit_the_installed_events():
    check_modules(
        plugins.load_modules(plugins.installed_modules()), plugins.load_events()
    )


def test_pipeline_topics_are_connected():
    """Every module's input must be some other module's output (or an entrypoint)."""
    modules = plugins.load_modules(plugins.installed_modules())
    produced = {m.output_type for m in modules.values() if m.output_type}
    entrypoints = {"huri.audio.in", "huri.question"}  # fed by the client
    dangling = {
        name: m.input_type
        for name, m in modules.items()
        if m.input_type not in produced and m.input_type not in entrypoints
    }
    assert not dangling, f"modules subscribed to topics nobody emits: {dangling}"


def test_server_without_allow_list_rejects_every_session(monkeypatch):
    """With HURI_MODULES unset, a session asking for an installed module is
    refused, and told why. Builds the registry the way HuRI.__init__ does."""
    monkeypatch.delenv("HURI_MODULES", raising=False)
    enabled = plugins.load_modules(plugins.parse_selection(os.getenv("HURI_MODULES")))
    factory = ModuleFactory(handles={}, installed=plugins.installed_modules())
    for name, cls in enabled.items():
        factory.register(name, cls)

    assert factory.registered() == []
    config = ModuleConfig.from_dict({"name": "tag"})
    with pytest.raises(ValueError, match="installed but not enabled.*HURI_MODULES"):
        factory.create_from_config("user", {"tag": config})


class Servo(Module):
    """Moves hardware: needs HURI_ACTUATION_MODULES besides HURI_MODULES."""

    input_type, output_type = "huri.motion", None
    actuation = True

    async def process(self, data):
        return None


def build_app(**env):
    """Import the server app the way Serve does, with these settings."""
    return subprocess.run(
        [sys.executable, "-c", "import huri_core.app"],
        cwd=ROOT,
        env={**os.environ, "PYTHONWARNINGS": "ignore", **env},
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_server_app_builds_with_curated_modules_and_the_actuation_opt_in():
    pytest.importorskip("ray")
    servo = f"{__name__}:Servo"
    refused = build_app(HURI_MODULES=f"tag,{servo}")
    assert refused.returncode != 0
    assert "HURI_ACTUATION_MODULES" in refused.stderr

    allowed = build_app(HURI_MODULES=f"tag,{servo}", HURI_ACTUATION_MODULES=servo)
    assert allowed.returncode == 0, allowed.stderr
