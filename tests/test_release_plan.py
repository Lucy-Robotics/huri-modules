"""scripts/release_plan.py: what blocks a release, and what one tags and uploads
(offline: PyPI faked, a throwaway git repo for the tags)."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_plan", ROOT / "scripts" / "release_plan.py"
)
assert spec and spec.loader
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)

TAGS = ["0.1.0", "0.2.0"]
REFS = {"HURI_CORE_REF": "0.4.1", "HURI_CLI_REF": "0.1.1"}
META = plan.Dist("huri-modules", "0.3.0", "meta", [])
EVENTS = plan.Dist("huri-events", "0.3.0", "packages/huri-events", [])
HUB = plan.Dist("huri-hub", "0.2.0", "packages/huri-hub", ["huri-core>=0.4,<0.5"])
ON_PYPI = ("huri-events==0.2.0", "huri-hub==0.2.0", "huri-modules==0.2.0")


def pypi(*released):
    """A fake PyPI holding `released` ("name==version" strings)."""
    return lambda name, version: f"{name}=={version}" in released


def projects(*released, new=()):
    """A fake PyPI's project lookup: whatever has a release, bar `new`."""
    return lambda name: name not in new and any(
        r.split("==")[0] == name for r in released
    )


def check(meta=META, packages=(EVENTS, HUB), tags=TAGS, released=ON_PYPI, **kw):
    kw.setdefault("changed", [])
    kw.setdefault("refs", REFS)
    return plan.problems(meta, list(packages), tags, pypi(*released), **kw)


# --- problems(): the release PR's checks ---


def test_a_consistent_release_has_no_problem():
    assert check(changed=[EVENTS.path]) == []


def test_a_pre_release_metapackage_is_refused():
    (problem,) = check(meta=META._replace(version="0.3.0.dev0"))
    assert "huri-modules 0.3.0.dev0 is a pre-release" in problem


def test_an_already_tagged_version_is_refused():
    (problem,) = check(meta=META._replace(version="0.2.0"), released=())
    assert "tag 0.2.0 already exists" in problem


def test_a_metapackage_version_already_on_pypi_is_refused():
    (problem,) = check(released=ON_PYPI + ("huri-modules==0.3.0",))
    assert "huri-modules 0.3.0 is already on PyPI" in problem


def test_a_changed_package_whose_version_is_on_pypi_is_refused():
    (problem,) = check(changed=[HUB.path])
    assert "huri-hub changed without a version bump" in problem


def test_a_changed_package_with_a_new_version_is_fine():
    assert check(packages=[EVENTS], changed=[EVENTS.path]) == []


def test_an_unchanged_package_already_on_pypi_is_fine():
    assert check(changed=[]) == []


@pytest.mark.parametrize("ref", ["dev", "main", "0.5.0.dev0", "v0.4.1", ""])
@pytest.mark.parametrize("key", ["HURI_CORE_REF", "HURI_CLI_REF"])
def test_a_ref_that_is_not_a_release_tag_is_refused(key, ref):
    (problem,) = check(refs={**REFS, key: ref})
    assert problem.startswith(f"{key} is ")


def test_a_pre_release_or_non_canonical_package_version_is_refused():
    (pre,) = check(packages=[EVENTS._replace(version="0.3.0rc1")])
    assert "huri-events 0.3.0rc1 is a pre-release" in pre
    (canon,) = check(packages=[EVENTS._replace(version="v0.3.0")])
    assert "huri-events v0.3.0 is not canonical: write it 0.3.0" in canon


def test_a_direct_url_dependency_is_refused_in_a_package_and_in_meta():
    url = "huri-core @ git+https://github.com/Lucy-Robotics/huri-core.git@0.4.1"
    problems = check(
        meta=META._replace(requires=[url]), packages=[HUB._replace(requires=[url])]
    )
    assert len(problems) == 2
    assert all("a direct URL" in p for p in problems)
    assert "huri-hub depends on" in problems[0]
    assert "huri-modules depends on" in problems[1]


def test_release_tags_are_the_final_versions_oldest_first():
    tags = ["0.1.1", "0.1.0", "0.2.0.dev0", "v0.0.9", "nightly", "0.10.0"]
    assert [str(v) for v in plan.release_tags(tags)] == ["0.1.0", "0.1.1", "0.10.0"]


# --- main(): the decision that tags and uploads ---


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def write(repo, path, text):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)


def dist(repo, path, name, version, deps=()):
    write(
        repo,
        f"{path}/pyproject.toml",
        f'[project]\nname = "{name}"\nversion = "{version}"\n'
        f"dependencies = {list(deps)!r}\n".replace("'", '"'),
    )


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)


def release(repo, version, refs="HURI_CORE_REF=0.4.1\nHURI_CLI_REF=0.1.1\n"):
    """A commit at which events, hub and meta are all at `version`."""
    write(repo, "versions.env", "# refs\n" + refs)
    dist(repo, "meta", "huri-modules", version)
    dist(repo, "packages/huri-events", "huri-events", version)
    dist(repo, "packages/huri-hub", "huri-hub", version)
    commit(repo, f"release {version}")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Releases 0.1.0 and 0.2.0, tagged, all on the fake PyPI."""
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "release test")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "release@example.invalid")
    git(tmp_path, "init", "-q", "-b", "main")
    for version in TAGS:
        release(tmp_path, version)
        git(tmp_path, "tag", version)
    monkeypatch.setattr(plan, "ROOT", tmp_path)
    return tmp_path


def run(tmp_path, monkeypatch, event, ref="refs/heads/main", released=ON_PYPI, new=()):
    out, summary = tmp_path / "out.txt", tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    monkeypatch.setenv("GITHUB_REF", ref)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = plan.main(pypi(*released), projects(*released, new=new))
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    return code, outputs, summary.read_text() if summary.exists() else ""


def release_pr(repo):
    """0.3.0: huri-events bumped, huri-hub untouched, huri-module-lips-sync new."""
    release(repo, "0.3.0")
    dist(repo, "packages/huri-hub", "huri-hub", "0.2.0")
    dist(repo, "packages/huri-module-lips-sync", "huri-module-lips-sync", "0.1.0")
    commit(repo, "Release 0.3.0")


def test_the_release_pr_shows_what_merging_it_will_publish(repo, monkeypatch):
    release_pr(repo)
    code, out, summary = run(repo, monkeypatch, "pull_request", "refs/pull/1/merge")
    assert code == 0 and out["release"] == "false"
    assert (out["version"], out["previous"]) == ("0.3.0", "0.2.0")
    assert (out["core"], out["cli"]) == ("0.4.1", "0.1.1")
    assert out["uploads"] == (
        "huri-events==0.3.0 huri-module-lips-sync==0.1.0 huri-modules==0.3.0"
    )
    rows = [line for line in summary.splitlines() if line.startswith("| ")]
    assert rows[1:] == [
        "| tag `0.3.0` on main's merge commit | create |",
        "| GitHub release `0.3.0` | publish, marked latest |",
        "| PyPI `huri-events 0.3.0` | upload |",
        "| PyPI `huri-hub 0.2.0` | already on PyPI, skipped |",
        "| PyPI `huri-module-lips-sync 0.1.0` | upload (new project: needs its "
        "pending publisher first) |",
        "| PyPI `huri-modules 0.3.0` | upload |",
    ]


def test_several_new_projects_warn_about_the_one_pending_publisher_at_a_time(
    repo, monkeypatch
):
    release_pr(repo)
    dist(repo, "packages/huri-module-think", "huri-module-think", "0.1.0")
    commit(repo, "think")
    code, out, summary = run(repo, monkeypatch, "pull_request")
    assert code == 0 and out["release"] == "false"
    assert (
        "Warning: 2 new PyPI projects (huri-module-lips-sync, huri-module-think)"
        in (summary)
    )
    assert "one pending publisher at a time" in summary


def test_a_single_new_project_does_not_warn(repo, monkeypatch):
    release_pr(repo)
    code, _, summary = run(repo, monkeypatch, "pull_request")
    assert code == 0 and "Warning" not in summary
    assert "needs its pending publisher first" in summary


def test_with_no_previous_release_every_package_counts_as_changed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(plan, "ROOT", tmp_path)
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "release test")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "release@example.invalid")
    git(tmp_path, "init", "-q", "-b", "main")
    release(tmp_path, "0.1.0")
    code, _, summary = run(
        tmp_path, monkeypatch, "pull_request", released=("huri-hub==0.1.0",)
    )
    assert code == 1
    assert "huri-hub changed without a version bump" in summary


def test_a_package_changed_without_a_version_bump_blocks_the_release_pr(
    repo, monkeypatch
):
    release_pr(repo)
    write(repo, "packages/huri-hub/huri_hub.py", "x = 1\n")
    commit(repo, "change hub")
    code, out, summary = run(repo, monkeypatch, "pull_request")
    assert code == 1 and out == {"release": "false"}
    assert "huri-hub changed without a version bump" in summary
    assert "huri-events" not in summary


def test_a_package_new_since_the_previous_release_counts_as_changed(repo, monkeypatch):
    release_pr(repo)
    released = ON_PYPI + ("huri-module-lips-sync==0.1.0",)
    code, _, summary = run(repo, monkeypatch, "pull_request", released=released)
    assert code == 1
    assert "huri-module-lips-sync changed without a version bump" in summary


def test_a_blocked_release_pr_fails(repo, monkeypatch):
    code, out, summary = run(repo, monkeypatch, "pull_request")  # 0.2.0, tagged
    assert code == 1 and out == {"release": "false"}
    assert "tag 0.2.0 already exists" in summary


def test_versions_env_on_dev_blocks_the_release_pr(repo, monkeypatch):
    release_pr(repo)
    write(repo, "versions.env", "HURI_CORE_REF=dev\nHURI_CLI_REF=0.1.1\n")
    commit(repo, "core on dev")
    code, _, summary = run(repo, monkeypatch, "pull_request")
    assert code == 1 and "HURI_CORE_REF is 'dev'" in summary


def test_merging_the_release_pr_releases_it(repo, monkeypatch):
    release_pr(repo)
    code, out, _ = run(repo, monkeypatch, "push")
    assert code == 0
    assert (out["release"], out["create"], out["version"]) == ("true", "true", "0.3.0")


def test_a_merge_into_main_that_is_not_a_release_publishes_nothing(repo, monkeypatch):
    code, out, summary = run(repo, monkeypatch, "push")  # 0.2.0, tagged
    assert code == 0 and out == {"release": "false"}
    assert "not a release" in summary


def test_a_manual_run_at_the_tagged_commit_resumes_the_upload(repo, monkeypatch):
    release_pr(repo)
    git(repo, "tag", "0.3.0")
    uploaded_before_the_failure = ON_PYPI + ("huri-events==0.3.0",)
    code, out, _ = run(
        repo, monkeypatch, "workflow_dispatch", released=uploaded_before_the_failure
    )
    assert code == 0
    assert (out["release"], out["create"], out["version"]) == ("true", "false", "0.3.0")
    assert out["uploads"] == "huri-module-lips-sync==0.1.0 huri-modules==0.3.0"


def test_a_manual_run_on_main_after_the_release_commit_is_refused(repo, monkeypatch):
    release_pr(repo)
    git(repo, "tag", "0.3.0")
    commit(repo, "a later merge")
    code, out, _ = run(repo, monkeypatch, "workflow_dispatch")
    assert code == 1 and out == {"release": "false"}


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_a_release_from_another_branch_is_refused(repo, monkeypatch, event):
    release_pr(repo)
    code, out, _ = run(repo, monkeypatch, event, ref="refs/heads/dev")
    assert code == 1 and out == {"release": "false"}


def test_the_previous_release_of_a_patch_on_an_older_line_is_its_own(repo, monkeypatch):
    git(repo, "tag", "0.4.0")  # a newer line exists
    release_pr(repo)
    _, out, _ = run(repo, monkeypatch, "pull_request")
    assert out["previous"] == "0.2.0"
