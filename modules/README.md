# modules/

Verified third-party HuRI modules, each a git submodule pinned at a reviewed
tag of its author's repository: `modules/<name>`, listed in
[`registry.toml`](../registry.toml) with

```toml
source = { git = "https://github.com/acme/huri-module-servo", tag = "0.1.0", submodule = "modules/servo" }
```

None is listed yet. The curated, first-party modules are not here: they live
in [`packages/`](../packages).

- **Adding one**: `git submodule add <repository URL> modules/<name>`, check
  out the reviewed tag, add the registry entry, then
  `python check_registry.py --run-tests --strict` (see the README,
  "Adding a verified third-party module", and [ACCEPTANCE.md](../ACCEPTANCE.md)).
- **Bumping one**: check out the new tag in the submodule and set the entry's
  `tag` to it, in one pull request.
- **Getting their code**: clone with `git clone --recursive`, or run
  `git submodule update --init` in an existing clone. Without it these folders
  are empty, and `check_registry.py` skips their entries (`--strict` fails).

`check_registry.py` checks that each checked-out submodule is at its entry's
`tag`; `huri hub install <name>` installs it from `git+<repository URL>@<tag>`,
never from this folder.
