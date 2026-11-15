# Contributing to huri-modules

HuRI's contribution guide is
[huri-core's CONTRIBUTING.md](https://github.com/Lucy-Robotics/huri-core/blob/main/CONTRIBUTING.md):
which repository takes which change, how to submit a pull request, the commit
format, the conventions and the response times. This page adds what is
specific to huri-modules.

- **Branch from `dev`** and open the pull request to `dev`.
- **Run `make check`** before pushing: black, isort, flake8, mypy on the files
  CI gates, and pytest over `tests/` and `packages/*/tests`. `make format`
  fixes black and isort. A change to the installer can also be run in clean
  Ubuntu containers with `scripts/test_install.sh`.
- **One package, one version, one bump per release**: the first pull request
  that changes a package under `packages/` after its last release bumps the
  `version` in its `pyproject.toml`, and its pins in `meta/pyproject.toml`
  (`tests/test_metapackage.py` checks them). If that version is not on PyPI
  yet, leave it, unless your change needs a bigger bump than the pending one:
  a minor change raises a pending patch to the minor. A change to the wire
  format of `huri-events` is a minor bump while `0.x`
  ([Package versions](RELEASING.md#package-versions)). A forgotten bump is
  caught at release: the **Release** check fails a package that changed while
  its version is already on PyPI ("changed without a version bump").
- **A curated (official) module**, new or existing, is a package under
  `packages/` (`packages/huri-module-<name>`), with its `registry.toml` entry
  ([ACCEPTANCE.md](ACCEPTANCE.md)). A new one starts with an issue labelled
  `discussion`: the maintainers decide which modules join the curated set.
- **Your own module** does not come here: it lives in your own repository,
  made from
  [huri-plugin-template](https://github.com/Lucy-Robotics/huri-plugin-template)
  (GPL-3.0, like huri-core, so a module you distribute is GPL-3.0 too).
  Listing it as verified in `registry.toml` (a submodule under `modules/<name>`)
  follows [ACCEPTANCE.md](ACCEPTANCE.md).
- **The huri-core and huri-cli versions** this checkout is tested with are in
  [`versions.env`](versions.env); [RELEASING.md](RELEASING.md) says when they
  move.
