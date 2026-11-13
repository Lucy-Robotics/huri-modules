# Releasing huri-modules

## Versions

This repository has its own [semver](https://semver.org), independent of
huri-core's. Releases are git tags `X.Y.Z` (no `v` prefix) on `main`'s merge
commit; `dev` is the integration branch. The first release is `0.1.0`.

A huri-modules release is a **tested set**: every package under `packages/`
at the version in its own `pyproject.toml`, plus the refs of the other
repositories they were all tested against together, in
[`versions.env`](versions.env): huri-core (`HURI_CORE_REF`) and huri-cli
(`HURI_CLI_REF`), the reference client the installer adds.

### Package versions

Each package (`packages/*`) keeps its own `version` in its
`pyproject.toml`, bumped by the pull request that changes it (semver, per
package):

- `huri-events`: a change to a wire format, a topic or an event class's
  public fields is a **minor** bump while it is `0.x` (`0.1` → `0.2`), since
  modules and clients pin `huri-events>=0.3,<0.4`; adding a topic is a minor
  bump too; a fix that keeps the wire format is a patch.
- a module: a change to its topics, events, manifest or `user_config` keys is
  a minor bump while `0.x`; anything else that changes behaviour is a patch.
- `huri-hub` (`huri hub`) is versioned like the others, and holds the one
  reader of `registry.toml`'s schema (`huri_hub/registry.py`, which
  `check_registry.py` uses too). A change of that schema (a new key, a new
  `source` kind, a new `schema` number) is a change to huri-hub and its version
  bump, in the same pull request and release as the registry that needs it: a
  minor bump while `0.x` when an older huri-hub cannot read the new registry
  (`huri hub` then tells its users to upgrade huri-hub or pin
  `HURI_MODULES_REF` to an older release), a patch otherwise. A new key an
  older huri-hub would ignore, installing something incomplete, is a new
  `schema` number, so that huri-hub refuses the registry instead (schema 2,
  huri-hub 0.2.0: the module key `extra`, without which an older huri-hub
  installed a TTS module without its engine).
  huri-hub needs a huri-core whose `huri` runs the `huri.commands` entry
  points (the first `0.4.0` tag has it): an older core runs its own built-in
  `huri hub` instead. `HURI_CORE_REF` must point at such a core, or
  huri-hub's `test_huri_runs_it_as_huri_hub` fails (`HURI_ALLOW_OLD_CORE=1`
  skips it, for a deliberate run against an older core only).

Packages pin huri-core with a range of one minor version: today
`huri-core>=0.4,<0.5` (in each `pyproject.toml`, `huri_module.toml` and
`registry.toml` entry). While work targets an untagged core minor, the lower
bound is its `.dev0` (`>=0.5.0.dev0,<0.6`, step 5 of the checklist below).
The range and `HURI_CORE_REF` always move together: a range that does not
contain the core `HURI_CORE_REF` installs fails the tests, the image builds
(`deploy/install_huri.sh` checks the manifests) and the server's startup.

### The `huri-modules` metapackage

[`meta/`](meta) builds `huri-modules`, the name on PyPI for the whole set: no
code, one extra per `registry.toml` module (`pip install "huri-modules[vad,stt]"`),
each pinning `<package>[<registry extra>]==<its version>`, plus `events`, `hub`
and `all`. Its `version` **is** the huri-modules release (`0.1.1` is release
`0.1.1`), so `huri-modules==X.Y.Z` installs exactly the set tagged `X.Y.Z`.
It lives outside `packages/`, which holds the module and event packages only.
`tests/test_metapackage.py` fails when a module, a package version or a
registry `extra` changes without it.

### versions.env

Shell-sourceable `KEY=value` lines (comments allowed), one per repository this
one is built and tested against. Read with `. ./versions.env`; the installer
and `deploy/install_huri.sh` parse it without running it.

`HURI_CORE_REF`, a git ref of [huri-core](https://github.com/Lucy-Robotics/huri-core):
the core this repository is built and tested against. A release tag (`0.4.0`)
on `main`; on `dev`, the same tag, or huri-core's `dev` while work on the next
core minor is under way (see step 5 below). Everything reads it:

- `./install.sh` installs `huri-core[server,client]` from git at that ref
  (`HURI_CORE_REF=<ref>` in the environment overrides the file;
  `HURI_CORE_PATH=/path/to/huri-core` installs a local checkout, editable,
  instead);
- the Docker images take it as the `HURI_CORE_REF` build arg, defaulting to
  the file (`docker build --build-arg HURI_CORE_REF="$(. ./versions.env && echo "$HURI_CORE_REF")" ...`);
- CI installs it for the blocking test job, and runs the same tests against
  huri-core `dev` as a canary, so a breaking core change is seen here before
  core releases it. The canary job stays green and reports its failing steps
  as warnings: a failure there is about core, not about the pull request, and
  must not hold up its merge. Once core has released `X.Y.0` and bumped its
  `dev` to `X.(Y+1).0.dev0`, the canary **warns by design** (core `dev` is
  outside every module's `>=X.Y,<X.(Y+1)` range) until the pull request of
  step 5 widens the ranges; in that window, read its log for failures other
  than the range checks.

`HURI_CLI_REF`, a git ref of [HuRI-CLI](https://github.com/Lucy-Robotics/HuRI-CLI),
the reference client: a use case of HuRI with its own repository, versions
(tags `X.Y.Z`, first `0.1.0`) and CI, depending on `huri-core[client]` and
`huri-events` by version range. A huri-cli release tag on `main`; on `dev`, a
tag or huri-cli's `dev`. Only `./install.sh` reads it: it installs huri-cli
from git at that ref (`HURI_CLI_REF=<ref>` overrides the file,
`HURI_CLI_PATH=/path/to/huri-cli` installs a local checkout editable,
`--no-cli` skips it). The Docker images do not carry the CLI. huri-cli's
ranges must accept the core at `HURI_CORE_REF` and this repository's
huri-events, or the installer's resolver fails: when a huri-events minor bump
or a core minor moves, the matching huri-cli release comes first.

No git call of the installer (nor of `huri hub`, `check_registry.py` or CI)
may prompt for credentials: a ref that cannot be read fails at once and names
the key to fix.

### Compatibility

| huri-modules | huri-core |
|---|---|
| 0.1.x | 0.4.x |
| 0.2.x | 0.4.x |

A huri-modules minor line supports one huri-core minor line (huri-core may break
its public surface in a minor bump while it is `0.x`, see its RELEASING.md). A
huri-core patch release needs no huri-modules release; a huri-core minor
release needs a huri-modules minor release: `0.2.0` is built against huri-core
`0.4.1`, and huri-core `0.5.x` will need huri-modules `0.3.0`.
`huri hub` reads the registry at `HURI_MODULES_REF` (`main`, the latest
release, by default): pick the huri-modules tag of this table that matches your
huri-core.

## Who releases, and how

Repository maintainers (admins of `Lucy-Robotics/huri-modules`) cut releases,
after the huri-core release they target is tagged:

1. On `dev`, open a release PR that:
   - sets `HURI_CORE_REF` in `versions.env` to the huri-core release tag
     (`0.4.0`), and `HURI_CLI_REF` to the huri-cli release tag that supports
     that core and this release's huri-events (`0.1.0`): both refs are tags in
     a release, never a branch;
   - if huri-core's minor changed, moves every range to the new minor: the
     `huri-core` dependency of each `pyproject.toml` under `packages/`, the
     `huri_core` key of each
     `huri_module.toml` and of each `registry.toml` entry (they must agree,
     `check_registry.py` checks it): `>=X.Y.0.dev0,<X.(Y+1)` becomes
     `>=X.Y,<X.(Y+1)`;
   - bumps the version of every package that changed since the last release;
   - sets `meta/pyproject.toml`'s `version` to `X.Y.Z` and its pins to the
     package versions (`tests/test_metapackage.py` checks the pins);
   - updates the compatibility table (here and in README.md).
2. CI must be green on that PR: lint, `check_registry.py`, every package's
   tests and the full pipeline test (`tests/integration`), against the pinned
   core. Run the install workflow (`install.yml`, `workflow_dispatch`: it
   installs huri-cli at `HURI_CLI_REF` too) and,
   when the GPU paths changed, `gpu.yml` and the GPU images (`docker.yml` with
   `gpu_images`).
3. Merge it, then open the PR from `dev` into `main`, named `Release X.Y.Z`.
   Its **Release** check (`.github/workflows/release.yml`) fails, naming the
   culprit, when the release would be incomplete: any distribution's version
   (`meta/` or `packages/`) is a pre-release or not canonical; the metapackage
   version is already tagged or already on PyPI; a package under `packages/`
   changed since the previous release tag while its version is already on PyPI
   (changed without a version bump: PyPI would silently keep the old code; a
   package new since the previous tag counts as changed); `HURI_CORE_REF` or
   `HURI_CLI_REF` is not a release tag; a dependency is a direct URL, which
   PyPI refuses; or `tests/test_metapackage.py` fails. It also builds and
   `twine check`s every distribution. Its job summary lists what merging will
   publish: the tag, the GitHub release, and one row per package (`upload` or
   `already on PyPI, skipped`) and the metapackage. Never merge a release PR
   whose Release check is red: it is not a required status check, since it
   would fail every other PR into `main`.
4. Merge it. The Release workflow then tags `main`'s merge commit `X.Y.Z`
   (no `v` prefix), publishes the GitHub release (notes generated from the
   merged pull requests since the previous tag, headed by the huri-core and
   huri-cli refs it was tested against; edit them afterwards if needed), and
   waits for a maintainer to approve the `pypi` environment before uploading
   every package whose version is not on PyPI yet, then the metapackage. It
   ends by installing `huri-modules[events,hub]==X.Y.Z` from PyPI and checking
   the versions. PyPI never accepts a version twice. A failed upload: "Re-run
   failed jobs" on that run; or, while `main` is still at the release commit,
   run the Release workflow by hand on `main`.
5. On `dev`, leave `HURI_CORE_REF` at the released core tag (and
   `HURI_CLI_REF` at its tag, or set it back to huri-cli's `dev`). Do **not** set
   it back to `dev` on its own: core `dev` is bumped to the next minor's
   `.dev0` right after a core release, which is outside the `>=X.Y,<X.(Y+1)`
   ranges, so CI, the image builds and `./install.sh` would all fail. When work
   starts on the next core minor, one pull request sets `HURI_CORE_REF` to
   `dev` **and** moves every range to `>=X.(Y+1).0.dev0,<X.(Y+2)` (each package
   `pyproject.toml`, each `huri_module.toml` and
   each `registry.toml` entry, in agreement). Until that pull request merges,
   the `dev canary` CI job warns by design.

A patch release (`0.1.1`) is the same without step 1's range changes: fixes
only, against the same huri-core minor.

### Publishing setup (once)

The Release workflow uploads with PyPI's trusted publishing: no token is
stored. Each of these PyPI projects has, on pypi.org (Manage → Publishing), a
GitHub publisher with owner `Lucy-Robotics`, repository `huri-modules`,
workflow `release.yml` and environment `pypi`:

- `huri-modules` (the metapackage), `huri-events` and `huri-hub`;
- every `huri-module-*` under `packages/` (`huri-module-emo`, `-mov`, `-rag`,
  `-stt`, `-tag`, `-tts`, `-vad` today).

A package that is not on PyPI yet needs a *pending* publisher (pypi.org →
Your account → Publishing, same values, with the project name) before the
release that first uploads it; the release PR's summary marks such rows "new
project". PyPI allows **one** pending GitHub publisher at a time for this
owner, repository, workflow and environment, so a release with several new
projects goes one at a time:

1. register the pending publisher for one new project, merge the release PR;
2. the run uploads that project, then fails at the next new one (after the
   tag, the GitHub release and the packages before it are out);
3. register the pending publisher of that project (the first one is now a
   normal publisher), then run **Release** by hand on `main` (it resumes);
4. repeat until the metapackage is uploaded.

Do not upload a new package by hand beforehand: the plan would then block it
as "changed without a version bump".

The repository's `pypi` environment, created before any publisher, requires a
maintainer's approval and only accepts `main`. If the repository moves to
another GitHub owner, add the new publisher to each project and remove the old
one.

By hand, if the workflow cannot run: create the tag on `main`'s merge commit
and the GitHub release first if the workflow did not. Build each package whose
version is not on PyPI yet into `dist/packages`
(`python -m build -o dist/packages packages/<pkg>`) and the metapackage into
`dist/meta` (`python -m build -o dist/meta meta`), then
`twine check --strict dist/packages/* dist/meta/*`,
`twine upload --skip-existing dist/packages/*`, and only then
`twine upload dist/meta/*` (the metapackage pins the packages).

## Verified third-party modules

A verified third-party module is a `registry.toml` entry with a git source,
pinned as a git submodule under `modules/<name>` at a tag of its own
repository (see README.md, "Adding a verified third-party module"). Its version
is its author's; what this repository pins is the reviewed tag:

- bumping it is a pull request that checks out the new tag in the submodule and
  sets the entry's `tag` to it; CI checks the entry again (`check_registry.py`
  verifies that `.gitmodules` points at the entry's `git` URL and that the
  checkout is at the commit `tag` names there);
- on a huri-core minor release, the release PR re-checks every listed module
  against the new core: entries whose `huri_core` range does not include it are
  bumped to a tag that supports it, or delisted in the same release
  (ACCEPTANCE.md, "Keeping it true").
