"""`huri hub`: registry + GitHub topic, offline (every URL is faked).

The registry is written inline, in the schema of huri-modules' registry.toml,
so these tests need none of its modules (tests/test_hub_registry.py at the
repository root reads the real file).
"""

import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from huri_hub import cli
from huri_hub import registry as reg
from huri_hub import web

REGISTRY_TEXT = """
schema = 2

[[events]]
package = "huri-events"
namespace = "huri"
source = { path = "packages/huri-events" }

[[module]]
name = "stt"
package = "huri-module-stt"
summary = "Speech to text"
huri_core = ">=0.4.0.dev0,<0.5"
verified = 2026-10-12
source = { path = "packages/huri-module-stt" }

[[module]]
name = "lights"
package = "huri-module-lights"
summary = "Mood lighting, a verified third-party module"
huri_core = ">=0.4.0.dev0,<0.5"
verified = 2026-10-12
[module.source]  # pinned as a git submodule of huri-modules, at the same tag
git = "https://github.com/acme/huri-lights"
tag = "0.2.0"
submodule = "modules/lights"
subdirectory = "module"
"""


@pytest.fixture
def checkout(tmp_path):
    """A local huri-modules checkout: its registry, its checker, one package."""
    (tmp_path / "registry.toml").write_text(REGISTRY_TEXT)
    (tmp_path / "check_registry.py").write_text("")
    (tmp_path / "packages" / "huri-module-stt").mkdir(parents=True)
    (tmp_path / "packages" / "huri-events").mkdir()
    return tmp_path


@pytest.fixture
def registry(checkout):
    return str(checkout / "registry.toml")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """No ambient HURI_* setting, and no checkout around the current directory."""
    for var in ("HURI_MODULES_REF", "HURI_REGISTRY", "HURI_REF"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "installed_core_version", lambda: "0.4.0")
    monkeypatch.setattr(cli, "installed_distributions", lambda: {})


SERVO_PYPROJECT = """
[project]
name = "huri-module-servo"
[project.entry-points."huri.modules"]
servo = "huri_servo.module:Servo"
"""
SERVO_MANIFEST = """
name = "servo"
summary = "Gesture to servo angle arrays"
input = { topic = "huri.motion", event = "huri_events.motion:Motion" }
output = { topic = "acme.servo_angles", event = "huri_events_servo:ServoAngles" }
requires = { actuation = true }
huri_core = ">=0.4.0.dev0,<0.5"
"""
OLD_PYPROJECT = SERVO_PYPROJECT.replace("servo", "oldie")
OLD_MANIFEST = SERVO_MANIFEST.replace("servo", "oldie").replace(
    ">=0.4.0.dev0,<0.5", ">=0.2,<0.3"
)


def raw(repo, path):
    return web.RAW.format(repo=repo, ref="main", path=path)


SEARCH = {
    "items": [
        {
            "full_name": "acme/huri-module-servo",
            "description": "servos",
            "html_url": "https://github.com/acme/huri-module-servo",
            "default_branch": "main",
        },
        {
            "full_name": "bob/huri-module-oldie",
            "description": "old",
            "html_url": "https://github.com/bob/huri-module-oldie",
            "default_branch": "main",
        },
        {
            "full_name": "eve/not-a-module",
            "description": "no pyproject",
            "html_url": "https://github.com/eve/not-a-module",
            "default_branch": "main",
        },
    ]
}
WEB = {
    raw("acme/huri-module-servo", "pyproject.toml"): SERVO_PYPROJECT,
    raw("acme/huri-module-servo", "huri_servo/huri_module.toml"): SERVO_MANIFEST,
    raw("bob/huri-module-oldie", "pyproject.toml"): OLD_PYPROJECT,
    raw("bob/huri-module-oldie", "huri_oldie/huri_module.toml"): OLD_MANIFEST,
}


def fetch(url):
    if url.startswith(f"{web.GITHUB_API}/search/repositories"):
        assert "topic%3Ahuri-module" in url
        return json.dumps(SEARCH)
    return WEB.get(url)


def run(argv, capsys):
    code = cli.main(argv, fetch=fetch)
    return code, capsys.readouterr().out


def pip_targets(out):
    """The targets of the printed pip command, as the shell splits it."""
    line = next(line for line in out.splitlines() if line.startswith("$ "))
    args = shlex.split(line[2:])
    return args[args.index("install") + 1 :]


def test_search_lists_tagged_repositories(capsys):
    code, out = run(["search", "servo"], capsys)
    assert code == 0 and "acme/huri-module-servo" in out and "3 repositories" in out


def test_list_merges_verified_and_community_for_the_core_version(capsys, registry):
    code, out = run(["list", "--registry", registry, "--core", "0.4.0"], capsys)
    lines = {line.split()[0]: line for line in out.splitlines()[:-1]}
    assert "verified" in lines["stt"] and "verified" in lines["lights"]
    assert "community" in lines["servo"]
    assert "oldie" not in lines  # supports huri-core 0.2.x only
    assert out.splitlines()[-1] == f"{len(lines)} module(s) for huri-core 0.4.0"


def test_list_all_and_verified_filters(capsys, registry):
    _, out = run(["list", "--registry", registry, "--core", "0.4.0", "--all"], capsys)
    assert "oldie" in out
    _, out = run(["list", "--registry", registry, "--verified"], capsys)
    assert "servo" not in out and "stt" in out


def test_a_newer_core_hides_modules_built_for_this_one(capsys, registry):
    _, out = run(
        ["list", "--registry", registry, "--core", "0.5.0", "--no-crawl"], capsys
    )
    assert out.strip() == "0 module(s) for huri-core 0.5.0"


def test_install_a_curated_module_from_a_checkout(capsys, checkout):
    code, out = run(
        ["install", "stt", "--dry-run", "--registry", str(checkout)], capsys
    )
    assert code == 0
    assert "stt: verified" in out
    packages = checkout / "packages"
    # The events come from the same checkout, in the same pip command: they
    # are on no package index.
    stt, events = packages / "huri-module-stt", packages / "huri-events"
    assert f"pip install {stt} {events}\n" in out
    assert "not enabled" in out and "HURI_MODULES" in out


def test_the_checkout_around_the_current_directory_is_found(
    capsys, checkout, monkeypatch
):
    (checkout / "packages" / "huri-module-stt" / "src").mkdir()
    monkeypatch.chdir(checkout / "packages" / "huri-module-stt" / "src")
    code, out = run(["install", "stt", "--dry-run"], capsys)
    assert code == 0
    assert f"pip install {checkout / 'packages' / 'huri-module-stt'}" in out


def test_a_registry_without_its_checker_is_not_a_checkout(tmp_path):
    (tmp_path / "registry.toml").write_text(REGISTRY_TEXT)
    assert reg.find_local_registry(tmp_path) is None
    (tmp_path / "check_registry.py").write_text("")
    assert reg.find_local_registry(tmp_path / "a" / "b") == tmp_path / "registry.toml"
    assert reg.find_local_registry(tmp_path) == tmp_path / "registry.toml"


def test_huri_registry_points_at_another_registry(capsys, registry, monkeypatch):
    monkeypatch.setenv("HURI_REGISTRY", registry)
    _, out = run(["list", "--verified"], capsys)
    assert "stt" in out and "lights" in out


def test_a_verified_third_party_module_installs_at_its_reviewed_tag(capsys, registry):
    code, out = run(["install", "lights", "--dry-run", "--registry", registry], capsys)
    assert code == 0 and "lights: verified" in out
    target = "git+https://github.com/acme/huri-lights@0.2.0#subdirectory=module"
    assert pip_targets(out)[0] == target


def test_a_git_source_must_pin_a_tag(capsys, tmp_path):
    text = REGISTRY_TEXT.replace('tag = "0.2.0"\n', "")
    (tmp_path / "registry.toml").write_text(text)
    argv = ["list", "--registry", str(tmp_path / "registry.toml"), "--verified"]
    assert cli.main(argv, fetch=fetch) == 1
    assert "'lights': a git source must pin a `tag`" in (capsys.readouterr().err)


def test_a_module_for_another_core_is_not_installed(capsys, registry, monkeypatch):
    monkeypatch.setattr(cli, "installed_core_version", lambda: "0.5.0")
    argv = ["install", "stt", "--dry-run", "--registry", registry]
    assert cli.main(argv, fetch=fetch) == 1
    err = capsys.readouterr().err
    assert "supports huri-core >=0.4.0.dev0,<0.5, but 0.5.0" in err
    assert "HURI_MODULES_REF" in err


def test_a_community_module_is_only_installed_on_request(capsys, registry):
    code = cli.main(
        ["install", "servo", "--dry-run", "--registry", registry], fetch=fetch
    )
    err = capsys.readouterr().err
    assert code == 1 and "not reviewed" in err and "--community" in err

    argv = ["install", "servo", "--dry-run", "--community", "--registry", registry]
    code, out = run(argv, capsys)
    assert code == 0 and "community (not reviewed)" in out
    assert "pip install git+https://github.com/acme/huri-module-servo.git" in out


def test_install_by_url_and_unknown_name(capsys, registry):
    code, out = run(["install", "git+https://example.org/x.git", "--dry-run"], capsys)
    assert code == 0 and "pip install git+https://example.org/x.git" in out
    code = cli.main(
        ["install", "nope", "--dry-run", "--registry", registry], fetch=fetch
    )
    assert code == 1 and "no module named 'nope'" in capsys.readouterr().err


def test_git_never_prompts_for_credentials():
    env = web.git_env({"PATH": "/bin", "SSH_ASKPASS": "/usr/bin/ksshaskpass"})
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GCM_INTERACTIVE"] == "never"
    assert env["GIT_ASKPASS"] and "SSH_ASKPASS" not in env and env["PATH"] == "/bin"


def test_install_runs_pip_with_git_unable_to_prompt(monkeypatch, capsys):
    seen = {}

    def call(command, env=None):
        seen.update(command=command, env=env)
        return 1  # as when git cannot read a private repository

    monkeypatch.setenv("SSH_ASKPASS", "/usr/bin/ksshaskpass")
    monkeypatch.setattr(cli.subprocess, "call", call)
    code = cli.main(["install", "git+https://example.org/private.git"], fetch=fetch)
    assert code == 1
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "SSH_ASKPASS" not in seen["env"]
    assert "not prompted for credentials" in capsys.readouterr().err


def test_remote_registry_installs_from_huri_modules_at_the_same_ref(monkeypatch):
    modules = {
        m.name: m
        for m in reg.load_registry(
            "https://example.org/registry.toml", fetch=lambda u: REGISTRY_TEXT
        )
    }
    repo = "git+https://github.com/Lucy-Robotics/huri-modules.git"
    assert modules["stt"].install == (
        f"{repo}@main#subdirectory=packages/huri-module-stt"
    )
    monkeypatch.setenv("HURI_MODULES_REF", "0.1.0")
    stt, _ = reg.load_registry("https://x/registry.toml", fetch=lambda u: REGISTRY_TEXT)
    assert stt.install == f"{repo}@0.1.0#subdirectory=packages/huri-module-stt"


def test_a_local_registry_outside_a_checkout_installs_from_github(tmp_path):
    (tmp_path / "registry.toml").write_text(REGISTRY_TEXT)
    stt, _ = reg.load_registry(str(tmp_path / "registry.toml"))
    assert stt.install == reg.curated_install("packages/huri-module-stt")


def test_registry_and_installs_use_one_huri_modules_ref(monkeypatch):
    raw = "https://raw.githubusercontent.com/Lucy-Robotics/huri-modules"
    assert reg.modules_ref() == "main"
    assert reg.default_registry() == f"{raw}/main/registry.toml"
    monkeypatch.setenv("HURI_MODULES_REF", "0.1.0")
    assert reg.default_registry() == f"{raw}/0.1.0/registry.toml"
    assert reg.curated_install("packages/huri-events").startswith(
        "git+https://github.com/Lucy-Robotics/huri-modules.git@0.1.0#"
    )


def test_the_default_registry_is_read_from_github(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # not in a checkout
    seen = []

    def remote(url):
        seen.append(url)
        return REGISTRY_TEXT if url == reg.default_registry() else fetch(url)

    assert cli.main(["list", "--verified"], fetch=remote) == 0
    assert seen[0] == reg.default_registry()
    assert "stt" in capsys.readouterr().out


PYPROJECT = tomllib.loads(
    (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
)["project"]


def test_huri_hub_is_a_huri_command_and_a_script_of_its_own():
    # huri-core's `huri` runs the `huri.commands` entry points: `huri hub`.
    assert PYPROJECT["entry-points"]["huri.commands"] == {"hub": "huri_hub.cli:main"}
    assert PYPROJECT["scripts"] == {"huri-hub": "huri_hub.cli:main"}
    # `huri --help` shows the first line of the target's docstring.
    assert (cli.main.__doc__ or "").startswith("Find and install HuRI modules")


def test_huri_runs_it_as_huri_hub(capsys, registry):
    from huri_core import cli as core_cli

    # A huri-core from before the huri.commands dispatcher runs its own, older
    # `huri hub` instead: huri-hub needs a newer one (RELEASING.md). Only a
    # deliberate run against such a core may skip this.
    if not hasattr(core_cli, "COMMANDS_GROUP") and os.environ.get(
        "HURI_ALLOW_OLD_CORE"
    ):
        pytest.skip("HURI_ALLOW_OLD_CORE: this huri-core's `huri` has no dispatcher")
    assert hasattr(core_cli, "COMMANDS_GROUP"), (
        "this huri-core's `huri` does not run the huri.commands entry points; "
        "huri-hub needs a huri-core with that dispatcher"
    )
    assert core_cli.main(["hub", "list", "--verified", "--registry", registry]) == 0
    assert "stt" in capsys.readouterr().out


def test_python_dash_m_runs_the_command():
    res = subprocess.run(
        [sys.executable, "-m", "huri_hub", "--help"], capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("usage: python -m huri_hub ")
    assert "install" in res.stdout


@pytest.mark.parametrize(
    "argv0,argv,prog",
    [
        ("/venv/bin/huri-hub", None, "huri-hub"),
        # huri-core's `huri` passes the arguments: `huri hub`, whatever ran it.
        ("/venv/bin/huri", ["--help"], "huri hub"),
        ("/venv/bin/huri-hub", ["--help"], "huri hub"),
    ],
)
def test_usage_names_the_command_as_it_was_run(capsys, monkeypatch, argv0, argv, prog):
    monkeypatch.setattr(sys, "argv", [argv0, "--help"])
    with pytest.raises(SystemExit):
        cli.main(argv)
    assert capsys.readouterr().out.startswith(f"usage: {prog} ")


def test_errors_name_the_command_as_it_was_run(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv", ["huri-hub", "list", "--registry", str(tmp_path / "none")]
    )
    assert cli.main() == 1
    assert capsys.readouterr().err.startswith("huri-hub: no registry at ")


def test_help_and_a_missing_command(capsys):
    with pytest.raises(SystemExit) as exit_:
        cli.main(["--help"])
    assert exit_.value.code == 0 and "search" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_:
        cli.main([])
    assert exit_.value.code == 2


def test_a_missing_registry_is_a_readable_error(capsys, tmp_path):
    argv = ["list", "--registry", "https://example.org/none.toml"]
    assert cli.main(argv, fetch=lambda url: None) == 1
    assert "no registry at" in capsys.readouterr().err
    argv = ["list", "--registry", str(tmp_path / "none.toml")]
    assert cli.main(argv, fetch=fetch) == 1
    assert "no registry at" in capsys.readouterr().err


def test_a_malformed_registry_is_a_readable_error(capsys, tmp_path):
    (tmp_path / "registry.toml").write_text("[[module]\n")
    argv = ["list", "--registry", str(tmp_path / "registry.toml")]
    assert cli.main(argv, fetch=fetch) == 1
    assert "not valid TOML" in capsys.readouterr().err


def test_a_name_claimed_by_several_repositories_is_refused(capsys, registry):
    twin = dict(SEARCH["items"][0], full_name="mallory/huri-module-servo")
    twin["html_url"] = "https://github.com/mallory/huri-module-servo"
    pages = dict(WEB)
    pages[raw("mallory/huri-module-servo", "pyproject.toml")] = SERVO_PYPROJECT
    pages[raw("mallory/huri-module-servo", "huri_servo/huri_module.toml")] = (
        SERVO_MANIFEST
    )

    def two_claimants(url):
        if url.startswith(f"{web.GITHUB_API}/search/repositories"):
            return json.dumps({"items": SEARCH["items"] + [twin]})
        return pages.get(url)

    argv = ["install", "servo", "--dry-run", "--community", "--registry", registry]
    assert cli.main(argv, fetch=two_claimants) == 1
    err = capsys.readouterr().err
    assert "2 community repositories provide 'servo'" in err
    assert "acme/huri-module-servo" in err and "mallory/huri-module-servo" in err


def test_an_invalid_core_version_is_a_readable_error(capsys, registry):
    assert (
        cli.main(["list", "--core", "zero.four", "--registry", registry], fetch=fetch)
        == 1
    )
    assert "invalid huri-core version 'zero.four'" in capsys.readouterr().err


def test_its_dependencies_are_its_own():
    # Version ranges and TOML: huri-hub declares them, so it needs no extra of
    # huri-core (the base install, with no third-party dependency, is enough).
    requires = PYPROJECT["dependencies"]
    assert "huri-core>=0.4.0.dev0,<0.5" in requires
    assert "packaging>=23" in requires
    assert "tomli>=2; python_version < '3.11'" in requires


TTS_ENTRIES = """
[[module]]
name = "tts"
package = "huri-module-tts"
huri_core = ">=0.4.0.dev0,<0.5"
verified = 2026-10-12
source = { path = "packages/huri-module-tts" }
extra = "piper"

[[module]]
name = "servo"
package = "huri-module-servo"
huri_core = ">=0.4.0.dev0,<0.5"
verified = 2026-10-12
extra = "gpio"
[module.source]
git = "https://github.com/acme/huri-module-servo"
tag = "0.1.0"
submodule = "modules/servo"
"""


def test_an_entry_with_an_extra_installs_its_package_with_it(capsys, checkout):
    (checkout / "registry.toml").write_text(REGISTRY_TEXT + TTS_ENTRIES)
    tts = checkout / "packages" / "huri-module-tts"
    tts.mkdir()
    code, out = run(
        ["install", "tts", "--dry-run", "--registry", str(checkout)], capsys
    )
    assert code == 0
    events = checkout / "packages" / "huri-events"
    # Quoted, so the printed command can be pasted ([..] is a shell glob).
    assert f"pip install '{tts}[piper]' {events}\n" in out


def test_the_printed_command_keeps_a_named_target_one_argument(capsys, monkeypatch):
    """`<package>[<extra>] @ <url>` holds spaces and a glob: printed as one
    shell word, it pastes as is."""
    monkeypatch.setenv("HURI_MODULES_REF", "0.1.0")
    url = "https://example.org/registry.toml"
    remote = {url: REGISTRY_TEXT + TTS_ENTRIES}
    code = cli.main(
        ["install", "tts", "--dry-run", "--registry", url], fetch=remote.get
    )
    out = capsys.readouterr().out
    assert code == 0
    command = next(line[2:] for line in out.splitlines() if line.startswith("$ "))
    target = (
        "huri-module-tts[piper] @ git+https://github.com/Lucy-Robotics/"
        "huri-modules.git@0.1.0#subdirectory=packages/huri-module-tts"
    )
    assert target in shlex.split(command)
    readme = (
        "https://github.com/Lucy-Robotics/huri-modules/tree/0.1.0/"
        "packages/huri-module-tts#readme"
    )
    assert f"in its README: {readme}" in out


def test_a_curated_module_points_at_its_readme_in_the_checkout(capsys, checkout):
    _, out = run(["install", "stt", "--dry-run", "--registry", str(checkout)], capsys)
    readme = checkout / "packages" / "huri-module-stt" / "README.md"
    assert f"in its README: {readme}" in out


def test_the_distributions_huri_module_tts_replaced_are_refused(
    capsys, checkout, monkeypatch
):
    """Left installed, they register `tts` with another target: huri-core
    would then load no module at all."""
    (checkout / "registry.toml").write_text(REGISTRY_TEXT + TTS_ENTRIES)
    (checkout / "packages" / "huri-module-tts").mkdir()
    installed = {
        "huri-tts": set(),
        "huri-module-tts-piper": {"tts"},
        "huri-module-tts": {"tts", "tts_cosyvoice"},
        "huri-module-stt": {"stt"},
    }
    monkeypatch.setattr(cli, "installed_distributions", lambda: installed)
    argv = ["install", "tts", "--dry-run", "--registry", str(checkout)]
    assert cli.main(argv, fetch=fetch) == 1
    err = capsys.readouterr().err
    assert "pip uninstall huri-module-tts-piper huri-tts" in err
    # Reinstalling a package over itself, or next to unrelated ones, is fine.
    del installed["huri-tts"], installed["huri-module-tts-piper"]
    code, _ = run(argv, capsys)
    assert code == 0


def test_another_distribution_registering_the_name_is_refused():
    assert cli.stale_distributions(
        "stt", "huri-module-stt", {"acme-stt": {"stt"}, "huri-module-stt": {"stt"}}
    ) == ["acme-stt"]
    assert cli.stale_distributions("stt", "Huri_Module_Stt", {}) == []


def test_an_extra_names_the_package_in_a_url_target(monkeypatch):
    monkeypatch.setenv("HURI_MODULES_REF", "0.1.0")
    url = "https://example.org/registry.toml"
    modules = {
        m.name: m
        for m in reg.load_registry(url, fetch=lambda u: REGISTRY_TEXT + TTS_ENTRIES)
    }
    repo = "git+https://github.com/Lucy-Robotics/huri-modules.git@0.1.0"
    assert modules["tts"].extra == "piper"
    assert modules["tts"].install == (
        f"huri-module-tts[piper] @ {repo}#subdirectory=packages/huri-module-tts"
    )
    assert modules["servo"].install == (
        "huri-module-servo[gpio] @ git+https://github.com/acme/huri-module-servo@0.1.0"
    )
    assert modules["stt"].extra is None


LIB_PYPROJECT = """
[project]
name = "huri-module-foo"
dependencies = [
  "huri-core[server]>=0.4.0.dev0,<0.5",
  "huri-events>=0.1,<0.2",
  "huri-foo-lib>=0.1,<0.2",
  "numpy>=1.26",
]
"""
LIB_LIB_PYPROJECT = """
[project]
name = "huri-foo-lib"
dependencies = ["huri-core>=0.4.0.dev0,<0.5", "huri-events>=0.1,<0.2"]
"""
FOO_ENTRY = """
[[module]]
name = "foo"
package = "huri-module-foo"
huri_core = ">=0.4.0.dev0,<0.5"
verified = 2026-10-12
source = { path = "packages/huri-module-foo" }
"""


def test_install_stt_brings_huri_events_from_huri_modules_at_the_same_ref(
    capsys, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)  # not in a checkout
    monkeypatch.setenv("HURI_MODULES_REF", "0.1.0")
    remote = {reg.default_registry(): REGISTRY_TEXT}
    code, out = (
        cli.main(["install", "stt", "--dry-run"], fetch=remote.get),
        capsys.readouterr().out,
    )
    assert code == 0
    repo = "git+https://github.com/Lucy-Robotics/huri-modules.git@0.1.0"
    stt = f"{repo}#subdirectory=packages/huri-module-stt"
    events = f"{repo}#subdirectory=packages/huri-events"
    assert pip_targets(out) == [stt, events]


def test_a_curated_module_brings_the_curated_libraries_it_depends_on(capsys, checkout):
    # No curated library is left today (huri-module-tts folds its own in); a
    # module that depends on one still gets it from huri-modules.
    (checkout / "registry.toml").write_text(REGISTRY_TEXT + FOO_ENTRY)
    packages = checkout / "packages"
    for name, pyproject in [
        ("huri-module-foo", LIB_PYPROJECT),
        ("huri-foo-lib", LIB_LIB_PYPROJECT),
    ]:
        (packages / name).mkdir()
        (packages / name / "pyproject.toml").write_text(pyproject)
    code, out = run(
        ["install", "foo", "--dry-run", "--registry", str(checkout)], capsys
    )
    assert code == 0
    foo, events, lib = (
        packages / "huri-module-foo",
        packages / "huri-events",
        packages / "huri-foo-lib",
    )
    assert f"pip install {foo} {events} {lib}\n" in out


def test_a_verified_third_party_module_also_gets_the_curated_events(
    capsys, registry, checkout
):
    _, out = run(["install", "lights", "--dry-run", "--registry", registry], capsys)
    lights = "git+https://github.com/acme/huri-lights@0.2.0#subdirectory=module"
    assert pip_targets(out) == [lights, str(checkout / "packages" / "huri-events")]


def test_a_huri_modules_registry_url_installs_from_its_own_ref(capsys, monkeypatch):
    url = web.RAW.format(repo=reg.HURI_MODULES_REPO, ref="0.1.0", path="registry.toml")
    registry = reg.read_registry(url, fetch=lambda u: REGISTRY_TEXT)
    repo = "git+https://github.com/Lucy-Robotics/huri-modules.git"
    assert registry.ref == "0.1.0"
    stt = registry.modules[0]
    assert stt.install == f"{repo}@0.1.0#subdirectory=packages/huri-module-stt"
    assert registry.companions(stt, fetch=lambda u: None) == [
        f"{repo}@0.1.0#subdirectory=packages/huri-events"
    ]
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("HURI_MODULES_REF", "main")
    assert reg.read_registry(url, fetch=lambda u: REGISTRY_TEXT).ref == "0.1.0"
    assert "HURI_MODULES_REF=main ignored" in capsys.readouterr().err


def test_another_registry_url_names_the_ref_curated_packages_come_from(capsys):
    reg.read_registry(
        "https://example.org/registry.toml", fetch=lambda u: REGISTRY_TEXT
    )
    assert "installed from huri-modules@main" in capsys.readouterr().err


@pytest.mark.parametrize(
    "entry, error",
    [
        (
            'summary = "no name"\nsource = { path = "packages/x" }',
            "registry module #3 has no `name`",
        ),
        (
            'name = "bad"\nhuri_core = "bogus"\nsource = { path = "packages/x" }',
            "'bad': invalid huri_core range 'bogus'",
        ),
        ('name = "nosource"', "'nosource' has no `path` or `git` source"),
        (
            'name = "both"\nsource = { path = "packages/x", git = "https://x", '
            'tag = "1", submodule = "modules/x" }',
            "'both': a source is either `path` or `git`, not both",
        ),
        (
            'name = "plain"\nsource = { git = "http://x", tag = "1", '
            'submodule = "modules/x" }',
            "'plain': a git source must be an https URL",
        ),
        (
            'name = "nosub"\nsource = { git = "https://x", tag = "1" }',
            "'nosub': git source lacks ['submodule']",
        ),
        (
            'name = "extra"\nsource = { path = "packages/x", ref = "main" }',
            "'extra': path source has unknown ['ref']",
        ),
        (
            'name = "empty"\nsource = { git = "https://x", tag = "1", '
            'submodule = "modules/x", subdirectory = "" }',
            "'empty': every `source` value must be a non-empty string",
        ),
        (
            'name = "undated"\nverified = "yesterday"\n'
            'source = { path = "packages/x" }',
            "'undated': 'verified' must be a date (YYYY-MM-DD)",
        ),
        (
            'name = "stt"\nverified = 2026-10-12\nsource = { path = "packages/x" }',
            "registry entry 'stt': listed twice",
        ),
        (
            'name = "typo"\nextras = "piper"\nsource = { path = "packages/x" }',
            "registry entry 'typo': unknown key(s) ['extras']",
        ),
        (
            'name = "upper"\npackage = "x"\nextra = "Piper"\n'
            'source = { path = "packages/x" }',
            "'upper': `extra` must be the name of an extra of its package",
        ),
        (
            'name = "nopackage"\nextra = "piper"\nsource = { path = "packages/x" }',
            "'nopackage': `package` must be a non-empty string",
        ),
    ],
)
def test_a_malformed_registry_entry_is_a_readable_error(capsys, tmp_path, entry, error):
    if "verified" not in entry:
        entry = f"verified = 2026-10-12\n{entry}"
    text = REGISTRY_TEXT + f"\n[[module]]\n{entry}\n"
    (tmp_path / "registry.toml").write_text(text)
    argv = ["list", "--no-crawl", "--registry", str(tmp_path / "registry.toml")]
    assert cli.main(argv, fetch=fetch) == 1
    err = capsys.readouterr().err
    assert error in err and "Traceback" not in err


@pytest.mark.parametrize(
    "entry, error",
    [
        (
            'namespace = "acme"\nsource = { path = "packages/acme-events" }',
            "registry events '#2': `package` must be a non-empty string",
        ),
        (
            'package = "acme-events"\nsource = { path = "packages/acme-events" }',
            "registry events 'acme-events': `namespace` must be a non-empty string",
        ),
        (
            'package = "acme-events"\nnamespace = "acme"\n'
            'source = { git = "https://x", tag = "1", submodule = "modules/x" }',
            "'acme-events': event packages are curated, a `path` source",
        ),
    ],
)
def test_a_malformed_events_entry_is_a_readable_error(capsys, tmp_path, entry, error):
    text = REGISTRY_TEXT + f"\n[[events]]\n{entry}\n"
    (tmp_path / "registry.toml").write_text(text)
    argv = ["list", "--no-crawl", "--registry", str(tmp_path / "registry.toml")]
    assert cli.main(argv, fetch=fetch) == 1
    err = capsys.readouterr().err
    assert error in err and "Traceback" not in err


def test_a_registry_without_schema_is_read_as_this_one():
    # Lenient for `huri hub`; huri-modules' CI requires the key.
    text = REGISTRY_TEXT.replace("schema = 2\n", "")
    assert "schema" not in text
    assert reg.parse(text, "r.toml")["module"][0]["name"] == "stt"
    with pytest.raises(web.HubError, match="missing `schema`"):
        reg.parse(text, "r.toml", require_schema=True)


@pytest.mark.parametrize("schema", [1, 3])
def test_another_registry_schema_is_a_readable_error(capsys, tmp_path, schema):
    # Schema 1 had no `extra`: this huri-hub would install its modules without
    # their engine, so it does not read it either; schema 3 is a newer hub's.
    (tmp_path / "registry.toml").write_text(f"schema = {schema}\n")
    argv = ["list", "--registry", str(tmp_path / "registry.toml")]
    assert cli.main(argv, fetch=fetch) == 1
    err = capsys.readouterr().err
    assert f"registry schema {schema}; this huri-hub reads schema 2" in err


def test_list_and_install_name_the_registry_they_read(capsys, registry):
    for argv in (["list", "--no-crawl"], ["install", "stt", "--dry-run"]):
        assert cli.main([*argv, "--registry", registry], fetch=fetch) == 0
        assert f"registry: {registry}" in capsys.readouterr().err


def test_stale_settings_are_not_ignored_silently(capsys, checkout, monkeypatch):
    monkeypatch.setenv("HURI_REF", "0.3.0")
    monkeypatch.chdir(checkout)
    assert cli.main(["list", "--verified"], fetch=fetch) == 0
    assert "HURI_REF is ignored: it was renamed HURI_MODULES_REF" in (
        capsys.readouterr().err
    )
    monkeypatch.setenv("HURI_MODULES_REF", "0.1.0")
    assert cli.main(["list", "--verified"], fetch=fetch) == 0
    err = capsys.readouterr().err
    assert "HURI_MODULES_REF=0.1.0 ignored: using the huri-modules checkout" in err
