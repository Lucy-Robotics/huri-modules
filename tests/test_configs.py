"""The reference Serve configs (config/huri*.yaml) must run on any checkout."""

import re
from pathlib import Path

import pytest
import yaml
from huri_rag.rag import RAGDeploymentConfig

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = sorted((ROOT / "config").glob("huri*.yaml"))
CONFIGS = [p for p in CONFIGS if not p.name.endswith(".generated.yaml")]


def _app(path: Path) -> dict:
    (app,) = yaml.safe_load(path.read_text())["applications"]
    return app


def _deployment(path: Path, name: str) -> dict:
    (dep,) = [d for d in _app(path)["deployments"] if d["name"] == name]
    return dep


def test_both_references_are_checked():
    assert {p.name for p in CONFIGS} == {"huri.yaml", "huri_cpu.yaml"}


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_env_vars_hold_no_host_or_helm_only_path(path):
    env = _app(path)["runtime_env"]["env_vars"]
    # A developer's home or the chart's /models mount does not exist elsewhere.
    absolute = {k: v for k, v in env.items() if str(v).startswith("/")}
    assert absolute == {}
    assert "PYTHONPATH" not in env


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_env_vars_are_read_by_something(path):
    # OLLAMA_* were set for years and read by nothing: RAG takes user_config.
    env = _app(path)["runtime_env"]["env_vars"]
    assert not [k for k in env if k.startswith("OLLAMA_")]


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_rag_user_config_uses_keys_rag_reads(path):
    user_config = _deployment(path, "RAGHandle")["user_config"]
    assert set(user_config) <= set(RAGDeploymentConfig.model_fields)
    # The key is a secret: HURI_LLM_API_KEY, never a committed config.
    assert "llm_api_key" not in user_config


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_embedding_model_matches_the_installer_default(path):
    installer = (ROOT / "scripts" / "install_local.sh").read_text()
    default = re.search(r'^EMBED_MODEL_DEFAULT="([^"]+)"', installer, re.M)
    assert default is not None
    user_config = _deployment(path, "RAGHandle")["user_config"]
    assert user_config["embedding_model"] == default.group(1)
