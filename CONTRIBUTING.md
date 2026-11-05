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
- **One package, one version**: bump the `version` in the `pyproject.toml` of
  each package under `packages/` that you change. A change to the wire format
  of `huri-events` is a minor bump while `0.x`.
- **A new module** lives in its own repository, made from
  [huri-plugin-template](https://github.com/Lucy-Robotics/huri-plugin-template).
  Listing it in `registry.toml` follows [ACCEPTANCE.md](ACCEPTANCE.md).
- **The huri-core and huri-cli versions** this checkout is tested with are in
  [`versions.env`](versions.env); [RELEASING.md](RELEASING.md) says when they
  move.
