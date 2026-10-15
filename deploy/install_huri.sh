#!/bin/sh
# Installs HuRI into an image: huri-core from its repository at a git ref, plus
# every distribution under packages/ (the curated events and modules) from the
# build context, except huri-hub.
#
#   install_huri.sh SRC_DIR [HURI_CORE_REF]
#
# SRC_DIR holds packages/ and the versions.env file of this repository; an
# empty or absent HURI_CORE_REF argument (the Dockerfiles' build arg) means
# the HURI_CORE_REF of that file, the ref this repository is tested against.
# (The images do not install huri-cli, so HURI_CLI_REF is not read here.)
#
# --no-deps: each image installs the heavy, hardware-specific dependencies
# first, from its own requirement files (which also satisfy huri-core's
# [server] extra); these packages only add the code. Every curated module is
# installed, whatever the image's hardware: the head node imports every enabled
# module class to build the app and bind its deployments (module code imports
# its heavy dependencies lazily), while HURI_MODULES and the Serve config
# decide what actually runs where.
#
# TTS: the images carry CosyVoice3's stack only (module tts_cosyvoice, the
# nvidia image's requirement file). Piper's piper-tts (module tts, the `piper`
# extra of huri-module-tts) is in none of them: its onnxruntime would replace
# onnxruntime-gpu. `tts` loads, but its replica fails at its first utterance.
#
# huri-hub (`huri hub`) is left out: it pip-installs modules on a workstation,
# while an image runs the modules baked into it at build time, and a pod that
# installed more at runtime would drift from its image.
#
# The build fails here, rather than at deploy time, if a module or event does
# not load or the installed versions do not fit together.
set -eu
src="${1:?usage: install_huri.sh SRC_DIR [HURI_CORE_REF]}"
ref="${2:-}"
# The last HURI_CORE_REF= line of versions.env, without quotes, comment or
# blanks. Read, not sourced: nothing in the file runs in the build.
[ -n "$ref" ] || ref="$(sed -n 's/^[[:space:]]*HURI_CORE_REF=//p' "$src/versions.env" \
  | tail -n 1 | sed 's/[[:space:]]#.*$//' | tr -d "[:space:]\"'")"
[ -n "$ref" ] || { echo "install_huri.sh: no HURI_CORE_REF in $src/versions.env" >&2; exit 1; }
# Never wait for credentials: a missing or private repository (HTTP 401) must
# fail the build, not hang it on a prompt.
export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/true
unset SSH_ASKPASS
echo "huri-core: git ref $ref"

set --
for pkg in "$src"/packages/*/; do
  case "$pkg" in */packages/huri-hub/) continue ;; esac
  set -- "$@" "$pkg"
done
pip install --no-cache-dir --no-deps \
  "huri-core @ git+https://github.com/Lucy-Robotics/huri-core.git@$ref" \
  "$@"

python - <<'PY'
from huri_core.core.compat import check_manifests, check_modules
from huri_core.core.plugins import (
    find_manifest,
    installed_modules,
    load_events,
    load_modules,
)

events = load_events()
modules = load_modules(installed_modules())
check_modules(modules, events)
manifests = {name: find_manifest(name) for name in modules}
missing = sorted(name for name, manifest in manifests.items() if manifest is None)
if missing:
    raise SystemExit(f"curated module(s) without a huri_module.toml: {missing}")
check_manifests(modules, manifests, events)
print(f"HuRI: {len(events)} event topics, modules {sorted(modules)}")
PY
