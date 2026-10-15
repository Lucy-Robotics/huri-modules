# Releasing huri-modules

## Versions

This repository has its own [semver](https://semver.org), independent of
huri-core's. Releases are git tags `X.Y.Z` (no `v` prefix) on `main`'s merge
commit; `dev` is the integration branch. The first release is `0.1.0`.

A huri-modules release is a **tested set**: every package under `packages/`
at the version in its own `pyproject.toml`, plus the huri-core ref they were
all tested against together, in [`HURI_CORE_REF`](HURI_CORE_REF).

### Package versions

Each package (`packages/*`, `examples/cli`) keeps its own `version` in its
`pyproject.toml`, bumped by the pull request that changes it (semver, per
package):

- `huri-events`: a change to a wire format, a topic or an event class's
  public fields is a **minor** bump while it is `0.x` (`0.1` → `0.2`), since
  modules and clients pin `huri-events>=0.1,<0.2`; adding a topic is a minor
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
  `HURI_MODULES_REF` to an older release), a patch otherwise.
  huri-hub needs a huri-core whose `huri` runs the `huri.commands` entry
  points (the first `0.4.0` tag has it): an older core runs its own built-in
  `huri hub` instead. `HURI_CORE_REF` must point at such a core, or
  huri-hub's `test_huri_runs_it_as_huri_hub` fails (`HURI_ALLOW_OLD_CORE=1`
  skips it, for a deliberate run against an older core only).

Packages pin huri-core with a range of one minor version: today
`huri-core>=0.4.0.dev0,<0.5` (in each `pyproject.toml`, `huri_module.toml` and
`registry.toml` entry, and in `examples/cli/pyproject.toml`), which becomes
`>=0.4,<0.5` once huri-core `0.4.0` is tagged (step 1 of the checklist below).
The range and `HURI_CORE_REF` always move together: a range that does not
contain the core `HURI_CORE_REF` installs fails the tests, the image builds
(`deploy/install_huri.sh` checks the manifests) and the server's startup.

### HURI_CORE_REF

One line, a git ref of [huri-core](https://github.com/Lucy-Robotics/huri-core):
the core this repository is built and tested against. A release tag (`0.4.0`)
on `main`; on `dev`, the same tag, or huri-core's `dev` while work on the next
core minor is under way (see step 5 below). Before the first release it is
`dev`, against core `0.4.0.dev0`. Everything reads it:

- `./install.sh` installs `huri-core[server,client]` from git at that ref
  (`HURI_CORE_REF=<ref>` in the environment overrides the file;
  `HURI_CORE_PATH=/path/to/huri-core` installs a local checkout, editable,
  instead);
- the Docker images take it as the `HURI_CORE_REF` build arg, defaulting to
  the file (`docker build --build-arg HURI_CORE_REF="$(cat HURI_CORE_REF)" ...`);
- CI installs it for the blocking test job, and runs the same tests against
  huri-core `dev` as a non-blocking canary, so a breaking core change is seen
  here before core releases it. Once core has released `X.Y.0` and bumped its
  `dev` to `X.(Y+1).0.dev0`, the canary is **red by design** (core `dev` is
  outside every module's `>=X.Y,<X.(Y+1)` range) until the pull request of
  step 5 widens the ranges; in that window, read its log for failures other
  than the range checks.

### Compatibility

| huri-modules | huri-core |
|---|---|
| 0.1.x | 0.4.x |

A huri-modules minor line supports one huri-core minor line (huri-core may break
its public surface in a minor bump while it is `0.x`, see its RELEASING.md). A
huri-core patch release needs no huri-modules release; a huri-core minor
release needs a huri-modules minor release (`0.2.0` for huri-core `0.5.x`).
`huri hub` reads the registry at `HURI_MODULES_REF` (`main`, the latest
release, by default): pick the huri-modules tag of this table that matches your
huri-core.

## Who releases, and how

Repository maintainers (admins of `Lucy-Robotics/huri-modules`) cut releases,
after the huri-core release they target is tagged:

1. On `dev`, open a release PR that:
   - sets `HURI_CORE_REF` to the huri-core release tag (`0.4.0`);
   - if huri-core's minor changed, moves every range to the new minor: the
     `huri-core` dependency of each `pyproject.toml` under `packages/` and of
     `examples/cli/pyproject.toml`, the `huri_core` key of each
     `huri_module.toml` and of each `registry.toml` entry (they must agree,
     `check_registry.py` checks it); for the first release,
     `>=0.4.0.dev0,<0.5` becomes `>=0.4,<0.5`;
   - bumps the version of every package that changed since the last release;
   - updates the compatibility table (here and in README.md).
2. CI must be green on that PR: lint, `check_registry.py`, every package's
   tests and the full pipeline test (`tests/integration`), against the pinned
   core. Run the install workflow (`install.yml`, `workflow_dispatch`) and,
   when the GPU paths changed, `gpu.yml` and the GPU images (`docker.yml` with
   `gpu_images`).
3. Merge it, then merge `dev` into `main` with a PR named `Release X.Y.Z`.
4. Tag `main`'s merge commit `X.Y.Z` and push the tag.
5. On `dev`, leave `HURI_CORE_REF` at the released core tag. Do **not** set
   it back to `dev` on its own: core `dev` is bumped to the next minor's
   `.dev0` right after a core release, which is outside the `>=X.Y,<X.(Y+1)`
   ranges, so CI, the image builds and `./install.sh` would all fail. When work
   starts on the next core minor, one pull request sets `HURI_CORE_REF` to
   `dev` **and** moves every range to `>=X.(Y+1).0.dev0,<X.(Y+2)` (each package
   `pyproject.toml`, `examples/cli/pyproject.toml`, each `huri_module.toml` and
   each `registry.toml` entry, in agreement). Until that pull request merges,
   the `dev canary` CI job is red by design.

A patch release (`0.1.1`) is the same without step 1's range changes: fixes
only, against the same huri-core minor.

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
