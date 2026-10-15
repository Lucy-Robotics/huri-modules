# Listing a module in huri-modules

Being listed in `registry.toml` is HuRI's "verified" mark (tier 3). It is a
trust label, not a gate: any module can be used without it (tiers 0–2). A
listing is a pull request to
[huri-modules](https://github.com/Lucy-Robotics/huri-modules) adding one
`[[module]]` entry: a curated module adds its package under `packages/`
(`source = { path = "packages/<package>" }`); a third-party module stays in its
own repository and comes as a git submodule under `modules/<name>`, pinned at a
tag of that repository
(`source = { git = "<repository URL>", tag = "<tag>", submodule = "modules/<name>" }`,
see README.md, "Adding a verified third-party module").

## What a listing requires

CI (`check_registry.py --run-tests --strict`) checks the mechanical part:

- a third-party submodule is registered in `.gitmodules` with the entry's
  `git` URL, and is checked out at the commit the entry's `tag` names in that
  repository (read with `git ls-remote`): the commit reviewed and tested here
  is the one `huri hub install` gets from `git+<git>@<tag>`, as long as the
  author does not move the tag (the next CI run on this repository reports a
  moved tag);
- the package installs and provides the module under the listed name;
- it ships a `huri_module.toml` whose `summary` and `huri_core` match the entry;
- HuRI's startup checks pass against the pinned `huri-core` (`HURI_CORE_REF`)
  and the curated events: its topics are registered, with the event classes it
  declares, and the installed versions are in its manifest's ranges;
- every topic it produces belongs to an event package its manifest declares;
- its own tests pass (with `huri_core.testing`, no Ray, no GPU).

A maintainer reviews the rest:

- **License**: OSI-approved and compatible with HuRI's GPL-3.0.
- **Scope**: one clear job, described by its summary; no network access or
  side effects beyond what the summary says.
- **Namespace**: the topics it introduces use its own `<name>.` namespace,
  never `huri.` (reserved for `huri-events`).
- **Actuation**: a module that can move hardware declares
  `requires.actuation = true` and documents its limits; the server will not
  load it without an explicit operator opt-in.
- **Maintenance**: a named maintainer and an issue tracker.

Reviewers are the HuRI maintainers (admins of the huri-modules repository).
Merging the PR is the verification; there is no separate approval step.

## Keeping it true

- Bumping a listed module is a pull request moving its pin (a curated
  package's code and version; a third-party submodule's checkout and `tag`);
  CI runs again.
- When `huri-core` publishes a new minor version, every listed module is
  re-checked against it (CI on the huri-modules release PR that moves
  `HURI_CORE_REF`, see RELEASING.md); entries that fail are fixed or delisted in
  the same release.
- A verified mark is revoked (the entry removed) when the module stops passing
  CI and its maintainer does not respond within a release cycle, or on a
  security or safety problem, immediately.
