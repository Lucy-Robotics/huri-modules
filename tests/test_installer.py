"""The installer's (scripts/install_local.sh) dealings with git: versions.env is
where the refs come from, git never prompts for credentials, --plan-only makes
no network call, and an unreachable repository is a clear error.

git is a fake on PATH that records how it was called (and with which
environment) and fails the way GitHub does for a missing or private repository.
Nothing here touches the network."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_local.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="the installer is a bash script"
)

FAKE_GIT = """#!/bin/sh
{{
  echo "argv: $*"
  echo "GIT_TERMINAL_PROMPT=${{GIT_TERMINAL_PROMPT-<unset>}}"
  echo "GIT_ASKPASS=${{GIT_ASKPASS-<unset>}}"
  echo "SSH_ASKPASS=${{SSH_ASKPASS-<unset>}}"
}} >>"{log}"
{body}
"""
UNREACHABLE = (
    'echo "remote: Repository not found." >&2\n'
    "echo \"fatal: Authentication failed for 'https://github.com/x.git/'\" >&2\n"
    "exit 128"
)


def fake_git(tmp_path, body=UNREACHABLE):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "git.log"
    git = bin_dir / "git"
    git.write_text(FAKE_GIT.format(log=log, body=body))
    git.chmod(0o755)
    return bin_dir, log


def env_with(bin_dir, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("HURI_")}
    env.update(PATH=f"{bin_dir}:{env['PATH']}", SSH_ASKPASS="/usr/bin/ksshaskpass")
    env.update(extra)
    return env


def section(text, start, end):
    """The lines of the installer from the one starting with `start` up to (not
    including) the next one starting with `end`."""
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def git_library(tmp_path, versions):
    """The installer's git environment, versions.env reader and ref resolver,
    as a script that runs `"$@"` with them, against a versions.env file."""
    text = INSTALLER.read_text()
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "versions.env").write_text(versions)
    script = tmp_path / "lib.sh"
    script.write_text(
        "set -Eeuo pipefail\n"
        f'REPO_ROOT="{repo}"\n'
        'have() { command -v "$1" >/dev/null 2>&1; }\n'
        "_log() { :; }\n"
        'die() { echo "DIE: $*" >&2; exit 1; }\n'
        + section(text, "# --- Git, never interactive", "# --- Pinned versions")
        + section(text, "resolve_ref() {", "# resolve_refs")
        + '"$@"\n'
    )
    return script


def test_versions_env_names_both_refs():
    text = (ROOT / "versions.env").read_text()
    keys = re.findall(r"(?m)^([A-Z_]+)=(\S+)$", text)
    assert {k for k, _ in keys} == {"HURI_CORE_REF", "HURI_CLI_REF"}
    # Shell-sourceable, as the docs and CI use it.
    out = subprocess.run(
        ["sh", "-c", '. ./versions.env && echo "$HURI_CORE_REF $HURI_CLI_REF"'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.split() == [v for _, v in keys]


def test_versions_get_reads_quoted_and_commented_values(tmp_path):
    bin_dir, _ = fake_git(tmp_path)
    lib = git_library(
        tmp_path,
        '# comment\nHURI_CORE_REF=dev\nHURI_CORE_REF="0.4.0"  # pinned\n'
        "  HURI_CLI_REF='0.1.0'\n",
    )
    for key, want in (("HURI_CORE_REF", "0.4.0"), ("HURI_CLI_REF", "0.1.0")):
        got = subprocess.run(
            ["bash", str(lib), "versions_get", key],
            env=env_with(bin_dir),
            capture_output=True,
            text=True,
        )
        assert got.stdout.strip() == want, got.stderr


def test_unreachable_repository_fails_without_prompting(tmp_path):
    bin_dir, log = fake_git(tmp_path)
    lib = git_library(tmp_path, "HURI_CORE_REF=dev\n")
    out = subprocess.run(
        [
            "bash",
            str(lib),
            "resolve_ref",
            "huri-core",
            "https://github.com/Lucy-Robotics/huri-core.git",
            "dev",
            "HURI_CORE_PATH",
        ],
        env=env_with(bin_dir),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode != 0
    assert "cannot reach huri-core at ref dev" in out.stderr
    assert "check HURI_CORE_REF in versions.env" in out.stderr
    assert "set HURI_CORE_PATH to a local checkout" in out.stderr
    calls = log.read_text()
    assert "argv: -c credential.helper= ls-remote" in calls
    assert "GIT_TERMINAL_PROMPT=0" in calls
    assert "GIT_ASKPASS=/bin/true" in calls
    # A desktop's GUI askpass would prompt even without a terminal.
    assert "SSH_ASKPASS=<unset>" in calls


def test_a_ref_from_the_environment_is_blamed_on_the_environment(tmp_path):
    bin_dir, _ = fake_git(tmp_path)
    lib = git_library(tmp_path, "HURI_CORE_REF=dev\n")
    out = subprocess.run(
        ["bash", str(lib), "resolve_ref", "huri-core", "u", "foo", "HURI_CORE_PATH"],
        env=env_with(bin_dir, HURI_CORE_REF="foo"),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode != 0
    assert "check HURI_CORE_REF (set in the environment)" in out.stderr
    assert "versions.env" not in out.stderr


def test_missing_ref_names_the_ref_to_fix(tmp_path):
    bin_dir, _ = fake_git(tmp_path, body="exit 0")  # reachable, no such ref
    lib = git_library(tmp_path, "HURI_CLI_REF=nope\n")
    out = subprocess.run(
        [
            "bash",
            str(lib),
            "resolve_ref",
            "huri-cli",
            "https://github.com/Lucy-Robotics/HuRI-CLI.git",
            "nope",
            "HURI_CLI_PATH",
        ],
        env=env_with(bin_dir),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode != 0
    assert "huri-cli has no tag or branch nope" in out.stderr
    assert "check HURI_CLI_REF in versions.env" in out.stderr


def test_a_tag_wins_and_is_peeled(tmp_path):
    body = (
        'printf "%s\\trefs/heads/0.1.0\\n" aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n'
        'printf "%s\\trefs/tags/0.1.0\\n" bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n'
        'printf "%s\\trefs/tags/0.1.0^{}\\n" cccccccccccccccccccccccccccccccccccccccc\n'
    )
    bin_dir, _ = fake_git(tmp_path, body=body)
    lib = git_library(tmp_path, "HURI_CLI_REF=0.1.0\n")
    out = subprocess.run(
        ["bash", str(lib), "resolve_ref", "huri-cli", "u", "0.1.0", "HURI_CLI_PATH"],
        env=env_with(bin_dir),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.stdout.strip() == "c" * 40, out.stderr


def test_a_commit_hash_needs_no_network(tmp_path):
    bin_dir, log = fake_git(tmp_path)
    lib = git_library(tmp_path, "")
    out = subprocess.run(
        ["bash", str(lib), "resolve_ref", "huri-core", "u", "d" * 40, "X_PATH"],
        env=env_with(bin_dir),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.stdout.strip() == "d" * 40, out.stderr
    assert not log.exists()


def installer_copy(tmp_path, versions=None):
    """A copy of what the installer reads at plan time, so its .huri-local/
    lands in tmp_path, not in the checkout."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "install.sh", repo)
    shutil.copy(INSTALLER, repo / "scripts")
    if versions is None:
        shutil.copy(ROOT / "versions.env", repo)
    else:
        (repo / "versions.env").write_text(versions)
    return repo


def supported_python():
    return sys.version_info[:2] in ((3, 10), (3, 11), (3, 12))


@pytest.mark.skipif(not supported_python(), reason="the planner wants 3.10-3.12")
@pytest.mark.parametrize("flags", [[], ["--no-cli"]], ids=["cli", "no-cli"])
def test_plan_only_makes_no_network_call(tmp_path, flags):
    bin_dir, log = fake_git(tmp_path)
    repo = installer_copy(tmp_path)
    out = subprocess.run(
        [
            str(repo / "install.sh"),
            "--plan-only",
            "--profile",
            "cpu",
            "--python",
            sys.executable,
            *flags,
        ],
        cwd=repo,
        env=env_with(bin_dir),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert not log.exists(), log.read_text()
    assert "git ref dev of https://github.com/Lucy-Robotics/huri-core" in out.stdout
    if flags:
        assert re.search(r"client\s+none \(--no-cli\)", out.stdout)
    else:
        assert re.search(
            r"client\s+huri-cli \(git ref dev of "
            r"https://github.com/Lucy-Robotics/HuRI-CLI\)",
            out.stdout,
        )
    plan = (repo / ".huri-local" / "plan.env").read_text()
    assert ("HURI_CLI=none" if flags else "HURI_CLI=dev") in plan


def test_a_missing_cli_ref_is_refused_up_front(tmp_path):
    bin_dir, log = fake_git(tmp_path)
    repo = installer_copy(tmp_path, versions="HURI_CORE_REF=dev\n")
    out = subprocess.run(
        [str(repo / "install.sh"), "--plan-only", "--python", sys.executable],
        cwd=repo,
        env=env_with(bin_dir),
        capture_output=True,
        text=True,
    )
    assert out.returncode == 2
    assert "no HURI_CLI_REF" in out.stderr and "--no-cli" in out.stderr
    assert not log.exists()


@pytest.mark.skipif(not supported_python(), reason="the planner wants 3.10-3.12")
def test_the_environment_overrides_versions_env(tmp_path):
    bin_dir, log = fake_git(tmp_path)
    repo = installer_copy(tmp_path, versions="HURI_CORE_REF=dev\n")
    out = subprocess.run(
        [
            str(repo / "install.sh"),
            "--plan-only",
            "--profile",
            "cpu",
            "--python",
            sys.executable,
        ],
        cwd=repo,
        env=env_with(bin_dir, HURI_CORE_REF="0.4.0", HURI_CLI_REF="0.1.0"),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "git ref 0.4.0 of https://github.com/Lucy-Robotics/huri-core" in out.stdout
    assert "git ref 0.1.0 of https://github.com/Lucy-Robotics/HuRI-CLI" in out.stdout
    assert not log.exists()


def test_help_documents_the_cli_switches():
    out = subprocess.run(
        ["bash", str(INSTALLER), "--help"], capture_output=True, text=True
    )
    assert out.returncode == 0
    for word in ("--no-cli", "HURI_CLI_REF", "HURI_CLI_PATH", "versions.env"):
        assert word in out.stdout


@pytest.mark.skipif(not supported_python(), reason="the planner wants 3.10-3.12")
@pytest.mark.parametrize("name", ["core", "cli"])
@pytest.mark.parametrize("valid", [False, True], ids=["invalid", "valid"])
def test_a_local_checkout_path_is_checked_and_needs_no_git(tmp_path, name, valid):
    bin_dir, log = fake_git(tmp_path)
    repo = installer_copy(tmp_path)
    checkout = tmp_path / f"huri-{name}"
    checkout.mkdir()
    if valid:
        (checkout / "pyproject.toml").write_text("[project]\n")
        (checkout / f"huri_{name}").mkdir()
    var = f"HURI_{name.upper()}_PATH"
    out = subprocess.run(
        [
            str(repo / "install.sh"),
            "--plan-only",
            "--profile",
            "cpu",
            "--python",
            sys.executable,
        ],
        cwd=repo,
        env=env_with(bin_dir, **{var: str(checkout)}),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if valid:
        assert out.returncode == 0, out.stdout + out.stderr
        assert f"editable from {checkout} ({var})" in out.stdout
    else:
        assert out.returncode == 2
        assert f"{var}='{checkout}' is not a huri-{name} checkout" in out.stderr
    assert not log.exists()


STUB = """#!/bin/sh
echo "$(basename "$0") $*" >>"{log}"
exit 0
"""


@pytest.mark.skipif(not supported_python(), reason="the planner wants 3.10-3.12")
def test_an_unreachable_ref_fails_before_anything_is_installed(tmp_path):
    bin_dir, log = fake_git(tmp_path)
    changes = tmp_path / "changes.log"
    for tool in ("sudo", "apt-get", "dnf", "pacman", "zypper", "brew", "pip", "uv"):
        stub = bin_dir / tool
        stub.write_text(STUB.format(log=changes))
        stub.chmod(0o755)
    repo = installer_copy(tmp_path)
    out = subprocess.run(
        [
            str(repo / "install.sh"),
            "--yes",
            "--profile",
            "cpu",
            # The smallest plan, so the disk/RAM checks pass on any machine.
            "--modules",
            "rag",
            "--skip-models",
            "--skip-services",
            "--python",
            sys.executable,
        ],
        cwd=repo,
        env=env_with(bin_dir),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode != 0
    assert "cannot reach huri-core at ref dev" in out.stdout + out.stderr
    assert "ls-remote" in log.read_text()
    assert not changes.exists(), changes.read_text()
    assert not (repo / ".venv").exists()
