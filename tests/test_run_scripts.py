"""The run scripts the installer writes into .huri-local/: env.sh carries the GPU
environment start.sh used to set on its own, and stop.sh stops Ray only when
start.sh started it (otherwise it removes only huri-app).

generate_run_scripts() is lifted out of scripts/install_local.sh and run against
a temporary repo; ray, serve and python are fakes on PATH that log their
arguments."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_local.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="the installer is a bash script"
)

# `ray status` succeeds when $FAKE/ray.up exists; `ray start` creates it and
# points the session link at a new session, as Ray does.
FAKE_RAY = """#!/bin/sh
echo "ray $*" >>"{log}"
case "$1" in
  status) [ -e "{fake}/ray.up" ] ;;
  start)
    touch "{fake}/ray.up"
    mkdir -p "$RAY_TMPDIR/ray/session_ours"
    ln -sfn "$RAY_TMPDIR/ray/session_ours" "$RAY_TMPDIR/ray/session_latest" ;;
  stop) rm -f "{fake}/ray.up" ;;
esac
"""
FAKE_SERVE = """#!/bin/sh
echo "serve $*" >>"{log}"
[ "$1" = status ] && echo "status: RUNNING"
exit 0
"""
FAKE_PYTHON = """#!/bin/sh
echo "python $*" >>"{log}"
"""
DELETE_HURI_APP = 'serve.delete("huri-app")'


def section(text, start, end):
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def generate(tmp_path, **settings):
    """Run generate_run_scripts() with the given installer variables; return the
    state directory it wrote into."""
    repo = tmp_path / "repo"
    state = repo / ".huri-local"
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "activate").write_text("")
    (repo / "config").mkdir(parents=True)
    variables = {
        "REPO_ROOT": repo,
        "STATE_DIR": state,
        "VENV_DIR": venv,
        "DRY_RUN": 0,
        "HAS_DOCKER": "",
        "GFX_OVERRIDE": "",
        "GPU_VENDOR": "nvidia",
        "GPU_COUNT": 1,
        "GPU_INDEX": 0,
        "GPU_NAME": "test gpu",
        "QDRANT_REMOTE": 1,
        "QDRANT_URL": "http://qdrant.example:6333",
        "NEED_OLLAMA": 0,
        "LLM_URL": "http://llm.example",
        "LLM_PROVIDER": "api",
        "VERIFY_SSL": 1,
        "CPU_CORES": 4,
        "P_RAY_GPUS": 1,
        "INSTALL_CLI": 1,
        **settings,
    }
    script = tmp_path / "generate.sh"
    script.write_text(
        "set -Eeuo pipefail\n"
        + "".join(f'{k}="{v}"\n' for k, v in variables.items())
        + '_log() { :; }\nok() { :; }\nrun() { "$@"; }\n'
        + section(INSTALLER.read_text(), "write_file() {", "have() {")
        + section(INSTALLER.read_text(), "client_command() {", "verify() {")
        + "generate_run_scripts\n"
    )
    subprocess.run(["bash", str(script)], check=True, capture_output=True)
    return state


@pytest.fixture
def fakes(tmp_path):
    fake = tmp_path / "fake"
    fake.mkdir()
    log = tmp_path / "calls.log"
    for name, body in (
        ("ray", FAKE_RAY),
        ("serve", FAKE_SERVE),
        ("python", FAKE_PYTHON),
    ):
        exe = fake / name
        exe.write_text(body.format(log=log, fake=fake))
        exe.chmod(0o755)
    ray_tmp = tmp_path / "raytmp"
    (ray_tmp / "ray").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HURI_", "HIP_"))}
    env.update(PATH=f"{fake}:{env['PATH']}", RAY_TMPDIR=str(ray_tmp))
    env.pop("HSA_OVERRIDE_GFX_VERSION", None)
    return fake, log, env


def run(script, env):
    return subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, check=True
    )


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


def sourced(state, *names):
    """The values of `names` in a shell that sourced env.sh."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HURI_", "HIP_"))}
    env.pop("HSA_OVERRIDE_GFX_VERSION", None)
    shown = "; ".join(f'echo "${{{n}-<unset>}}"' for n in names)
    out = subprocess.run(
        ["bash", "-c", f'source "{state}/env.sh" && {shown}'],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.splitlines()


def test_env_sh_exports_the_amd_gpu_environment(tmp_path):
    state = generate(
        tmp_path, GPU_VENDOR="amd", GPU_COUNT=2, GPU_INDEX=1, GFX_OVERRIDE="11.0.0"
    )
    assert sourced(state, "HIP_VISIBLE_DEVICES", "HSA_OVERRIDE_GFX_VERSION") == [
        "1",
        "11.0.0",
    ]
    # start.sh gets them from env.sh, not from a second copy of its own.
    start = (state / "start.sh").read_text()
    assert "export HIP_VISIBLE_DEVICES" not in start
    assert "export HSA_OVERRIDE_GFX_VERSION" not in start


def test_env_sh_sets_no_gpu_variable_a_single_gpu_does_not_need(tmp_path):
    state = generate(tmp_path, GPU_VENDOR="amd", GPU_COUNT=1)
    assert sourced(state, "HIP_VISIBLE_DEVICES", "HSA_OVERRIDE_GFX_VERSION") == [
        "<unset>",
        "<unset>",
    ]


def test_stop_stops_the_ray_head_start_started(tmp_path, fakes):
    fake, log, env = fakes
    state = generate(tmp_path)
    run(state / "start.sh", env)
    assert "ray start --head --num-cpus=4 --num-gpus=1 " in "\n".join(calls(log)) + " "
    assert (state / "ray.owned").exists()

    out = run(state / "stop.sh", env)
    assert "serve shutdown -y" in calls(log)
    assert "ray stop" in calls(log)
    assert not (state / "ray.owned").exists()
    assert "stopping the ray head" in out.stdout


def assert_left_running(log, out):
    """stop.sh removed huri-app alone and said how to apply env changes."""
    assert "ray stop" not in calls(log)
    assert "serve shutdown -y" not in calls(log)
    assert any(DELETE_HURI_APP in c for c in calls(log))
    assert "leaving ray running" in out.stdout
    assert "run `ray stop` yourself" in out.stdout


def test_stop_leaves_a_ray_it_did_not_start_running(tmp_path, fakes):
    fake, log, env = fakes
    (fake / "ray.up").touch()  # someone else's cluster
    state = generate(tmp_path)
    out = run(state / "start.sh", env)
    assert not [c for c in calls(log) if c.startswith("ray start")]
    assert not (state / "ray.owned").exists()
    assert "reusing it" in out.stdout

    out = run(state / "stop.sh", env)
    assert_left_running(log, out)


def test_stop_leaves_a_later_ray_session_running(tmp_path, fakes):
    # start.sh started Ray, it died (a reboot), someone else started another:
    # the stale record must not make stop.sh stop theirs.
    fake, log, env = fakes
    state = generate(tmp_path)
    run(state / "start.sh", env)
    ray_dir = Path(env["RAY_TMPDIR"]) / "ray"
    (ray_dir / "session_theirs").mkdir()
    (ray_dir / "session_latest").unlink()
    (ray_dir / "session_latest").symlink_to(ray_dir / "session_theirs")

    out = run(state / "stop.sh", env)
    assert_left_running(log, out)
    assert not (state / "ray.owned").exists()


def test_stop_leaves_ray_running_when_start_could_not_record_the_session(
    tmp_path, fakes
):
    # An empty record cannot tell start.sh's head from a later one.
    fake, log, env = fakes
    state = generate(tmp_path)
    run(state / "start.sh", env)
    (state / "ray.owned").write_text("")

    out = run(state / "stop.sh", env)
    assert_left_running(log, out)
    assert "could not record" in out.stdout
    assert not (state / "ray.owned").exists()
