"""The installer's (scripts/install_local.sh) dealings with git and with the
client: versions.env is where the core ref comes from, git never prompts for
credentials, --plan-only makes no network call, an unreachable repository is a
clear error, and huri-cli is the newest release that fits the installed core.

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

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install_local.sh"


def shipped_ref(key):
    """The ref the shipped versions.env names: `dev`, or a tag in a release."""
    for line in (ROOT / "versions.env").read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise KeyError(key)


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


def test_versions_env_names_only_the_core_ref():
    # huri-cli is not pinned: the installer takes its newest release that fits.
    text = (ROOT / "versions.env").read_text()
    keys = re.findall(r"(?m)^([A-Z_]+)=(\S+)$", text)
    assert {k for k, _ in keys} == {"HURI_CORE_REF"}
    # Shell-sourceable, as the docs and CI use it.
    out = subprocess.run(
        ["sh", "-c", '. ./versions.env && echo "$HURI_CORE_REF"'],
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
        "  OTHER_REF='0.1.0'\n",
    )
    for key, want in (("HURI_CORE_REF", "0.4.0"), ("OTHER_REF", "0.1.0")):
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
    lib = git_library(tmp_path, "HURI_CORE_REF=nope\n")
    out = subprocess.run(
        [
            "bash",
            str(lib),
            "resolve_ref",
            "huri-core",
            "https://github.com/Lucy-Robotics/huri-core.git",
            "nope",
            "HURI_CORE_PATH",
        ],
        env=env_with(bin_dir),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode != 0
    assert "huri-core has no tag or branch nope" in out.stderr
    assert "check HURI_CORE_REF in versions.env" in out.stderr


def test_a_tag_wins_and_is_peeled(tmp_path):
    body = (
        'printf "%s\\trefs/heads/0.1.0\\n" aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n'
        'printf "%s\\trefs/tags/0.1.0\\n" bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n'
        'printf "%s\\trefs/tags/0.1.0^{}\\n" cccccccccccccccccccccccccccccccccccccccc\n'
    )
    bin_dir, _ = fake_git(tmp_path, body=body)
    lib = git_library(tmp_path, "HURI_CORE_REF=0.1.0\n")
    out = subprocess.run(
        ["bash", str(lib), "resolve_ref", "huri-core", "u", "0.1.0", "HURI_CORE_PATH"],
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
    core = shipped_ref("HURI_CORE_REF")
    assert f"git ref {core} of https://github.com/Lucy-Robotics/huri-core" in out.stdout
    if flags:
        assert re.search(r"client\s+none \(--no-cli\)", out.stdout)
    else:
        assert re.search(
            r"client\s+huri-cli \(newest PyPI release that supports this huri-core\)",
            out.stdout,
        )
    plan = (repo / ".huri-local" / "plan.env").read_text()
    assert ("HURI_CLI=none" if flags else "HURI_CLI=pypi") in plan


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
        env=env_with(bin_dir, HURI_CORE_REF="0.4.0"),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "git ref 0.4.0 of https://github.com/Lucy-Robotics/huri-core" in out.stdout
    assert not log.exists()


def test_help_documents_the_cli_switches():
    out = subprocess.run(
        ["bash", str(INSTALLER), "--help"], capture_output=True, text=True
    )
    assert out.returncode == 0
    for word in ("--no-cli", "HURI_CLI_PATH", "versions.env"):
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


FAKE_PIP = """#!/bin/sh
echo "$*" >>"{log}"
case "$*" in *"install"*"huri-cli"*) exit {rc} ;; esac
"""


def install_cli(tmp_path, pip_rc):
    """The installer's install_cli, with a fake pip that records its calls and
    exits `pip_rc` when asked for huri-cli. VPY is this Python, so the pins are
    the huri-core and huri-events installed here."""
    log = tmp_path / "pip.log"
    pip = tmp_path / "pip"
    pip.write_text(FAKE_PIP.format(log=log, rc=pip_rc))
    pip.chmod(0o755)
    script = tmp_path / "cli.sh"
    script.write_text(
        "set -Eeuo pipefail\n"
        f'PIP="{pip}"; VPY="{sys.executable}"; STATE_DIR="{tmp_path}"\n'
        'LOG_FILE=install.log; INSTALL_CLI=1; HURI_CLI_PATH=""; DRY_RUN=0\n'
        "CLI_UNAVAILABLE=0\n"
        'run() { "$@"; }\n'
        'info() { :; }; note() { :; }; warn() { echo "WARN $*"; }\n'
        "cli_source() { :; }\n"
        + section(INSTALLER.read_text(), "install_cli() {", "report_detection() {")
        + 'install_cli -c constraints.txt\necho "CLI_UNAVAILABLE=$CLI_UNAVAILABLE"\n'
    )
    out = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    return out, log.read_text().splitlines()


def installed_pins():
    from importlib.metadata import version

    return [f"{d}=={version(d)}" for d in ("huri-core", "huri-events")]


def test_the_client_is_the_newest_release_that_fits_the_installed_core(tmp_path):
    out, calls = install_cli(tmp_path, pip_rc=0)
    assert out.returncode == 0, out.stderr
    pins = tmp_path / "cli-constraints.txt"
    # Core and events stay as installed: pip may not downgrade them to fit.
    assert calls == [f"install -c constraints.txt -c {pins} --upgrade huri-cli"]
    assert pins.read_text().split() == installed_pins()
    assert "CLI_UNAVAILABLE=0" in out.stdout


def test_no_release_for_this_core_installs_the_server_without_the_client(tmp_path):
    out, calls = install_cli(tmp_path, pip_rc=1)
    assert out.returncode == 0, out.stderr
    # No older client left behind that rejects this core.
    assert calls[-1] == "uninstall -y huri-cli"
    assert "CLI_UNAVAILABLE=1" in out.stdout
    warning = next(line for line in out.stdout.splitlines() if line.startswith("WARN"))
    assert "no huri-cli release on PyPI supports" in warning
    assert all(pin in warning for pin in installed_pins())


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
    core = shipped_ref("HURI_CORE_REF")
    assert f"cannot reach huri-core at ref {core}" in out.stdout + out.stderr
    assert "ls-remote" in log.read_text()
    assert not changes.exists(), changes.read_text()
    assert not (repo / ".venv").exists()


def test_every_package_manager_installs_zstd_for_ollama():
    lists = re.findall(r"SYS_PKGS=\(([^)]+)\)", INSTALLER.read_text())
    assert len(lists) == 4
    assert all("zstd" in packages.split() for packages in lists)


def plan_on(tmp_path, total_mb, free_mb, *flags):
    """The --plan-only output on a faked CPU-only machine with that much RAM."""
    bin_dir, _ = fake_git(tmp_path)
    repo = installer_copy(tmp_path)
    script = repo / "scripts" / "install_local.sh"
    head, tail = script.read_text().rsplit('main "$@"', 1)
    assert not tail.strip()
    fakes = (
        f"detect_host() {{ CPU_CORES=8; RAM_TOTAL_MB={total_mb}; "
        f"RAM_FREE_MB={free_mb}; DISK_FREE_MB=500000; }}\n"
        f'detect_python() {{ PY_BIN="{sys.executable}"; PY_VERSION=3.12.0; }}\n'
        'detect_tools() { HAS_DOCKER=""; HAS_OLLAMA=0; }\n'
    )
    script.write_text(head + fakes + 'main "$@"\n')
    out = subprocess.run(
        [str(repo / "install.sh"), "--plan-only", "--profile", "cpu", *flags],
        cwd=repo,
        env=env_with(bin_dir),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


def test_a_busy_machine_gets_the_largest_tier_that_fits_the_free_ram(tmp_path):
    out = plan_on(tmp_path, 16384, 11264)
    assert re.search(
        r"rag/llm\s+ollama llama3\.2:3b.*left of 11\.0 GiB available RAM", out
    )
    assert "spare RAM" not in out


def test_a_very_busy_machine_keeps_the_smallest_tier_and_warns(tmp_path):
    out = plan_on(tmp_path, 16384, 6144)
    assert re.search(
        r"rag/llm\s+ollama qwen2\.5:1\.5b.*6\.0 GiB available RAM is short", out
    )
    assert "is free of" in out


def test_the_user_picks_the_model_on_a_busy_machine(tmp_path):
    out = plan_on(tmp_path, 15360, 3584, "--llm-model", "mistral:7b")
    assert "ollama mistral:7b" in out
    assert "only 3.5 GiB is free of the 11.9 GiB the plan needs" in out
    assert "close other applications" in out and "--llm-model" in out


def test_an_unreadable_free_ram_falls_back_to_the_total(tmp_path):
    out = plan_on(tmp_path, 15360, 0)
    assert "ollama mistral:7b" in out


def test_enough_ram_is_free(tmp_path):
    out = plan_on(tmp_path, 15360, 14336)
    assert "ollama mistral:7b" in out
    assert re.search(r"ram\s+11\.9 GiB needed / 14\.0 GiB available", out)
    assert "is free of" not in out


def test_a_user_selected_model_does_not_fit_in_the_free_ram(tmp_path):
    out = plan_on(tmp_path, 65536, 8192, "--llm-model", "qwen2.5:14b")
    assert re.search(r"rag/llm\s+ollama qwen2\.5:14b.*user-selected", out)
    assert "only 8.0 GiB is free of the 16.3 GiB the plan needs" in out
    assert "left of 8.0 GiB available RAM" in out


def test_a_remote_llm_gets_no_smaller_model_hint(tmp_path):
    out = plan_on(
        tmp_path,
        4096,
        1024,
        "--llm-url",
        "http://example.invalid:11434",
        "--llm-model",
        "foo",
    )
    assert "is free of" in out
    note = next(line for line in out.splitlines() if "close other applications" in line)
    assert "--llm-model" not in note
