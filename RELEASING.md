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

Packages pin huri-core with a range of one minor version: today
`huri-core>=0.4.0.dev0,<0.5` (in each `pyproject.toml`, `huri_module.toml` and
`registry.toml` entry), which becomes `>=0.4,<0.5` once huri-core `0.4.0` is
tagged (step 1 of the checklist below).

### HURI_CORE_REF

One line, a git ref of [huri-core](https://github.com/Lucy-Robotics/huri-core):
the core this repository is built and tested against. `dev` on the `dev`
branch, a release tag (`0.4.0`) on `main`. Everything reads it:

- `./install.sh` installs `huri-core[server,client]` from git at that ref
  (`HURI_CORE_REF=<ref>` in the environment overrides the file;
  `HURI_CORE_PATH=/path/to/huri-core` installs a local checkout, editable,
  instead);
- the Docker images take it as the `HURI_CORE_REF` build arg, defaulting to
  the file (`docker build --build-arg HURI_CORE_REF="$(cat HURI_CORE_REF)" ...`);
- CI installs it for the blocking test job, and runs the same tests against
  huri-core `dev` as a non-blocking canary, so a breaking core change is seen
  here before core releases it.

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
     `huri-core` dependency of each `pyproject.toml` under `packages/`, the
     `huri_core` key of each `huri_module.toml` and of each
     `registry.toml` entry (they must agree, `check_registry.py` checks it);
     for the first release, `>=0.4.0.dev0,<0.5` becomes `>=0.4,<0.5`;
   - bumps the version of every package that changed since the last release;
   - updates the compatibility table (here and in README.md).
2. CI must be green on that PR: lint, `check_registry.py`, every package's
   tests and the full pipeline test (`tests/integration`), against the pinned
   core. Run the install workflow (`install.yml`, `workflow_dispatch`) and,
   when the GPU paths changed, `gpu.yml` and the GPU images (`docker.yml` with
   `gpu_images`).
3. Merge it, then merge `dev` into `main` with a PR named `Release X.Y.Z`.
4. Tag `main`'s merge commit `X.Y.Z` and push the tag.
5. On `dev`, set `HURI_CORE_REF` back to `dev` if the next changes should
   follow huri-core's development branch (they usually should).

A patch release (`0.1.1`) is the same without step 1's range changes: fixes
only, against the same huri-core minor.

## Verified third-party modules

A verified third-party module is a `registry.toml` entry with a git source,
pinned as a git submodule under `modules/<name>` at a tag of its own
repository (see README.md, "Adding a verified third-party module"). Its version
is its author's; what this repository pins is the reviewed tag:

- bumping it is a pull request that checks out the new tag in the submodule and
  sets the entry's `tag` to it; CI checks the entry again (`check_registry.py`
  verifies the checkout is at `tag`);
- on a huri-core minor release, the release PR re-checks every listed module
  against the new core: entries whose `huri_core` range does not include it are
  bumped to a tag that supports it, or delisted in the same release
  (ACCEPTANCE.md, "Keeping it true").
