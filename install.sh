#!/usr/bin/env bash
# Installs HuRI on this machine: probes the hardware, plans which curated
# modules fit, installs huri-core and huri-cli (at the git refs in versions.env;
# --no-cli skips the client), the curated events and modules with the right
# variant of their dependencies, and writes the matching server and client
# configs. scripts/install_local.sh is the
# implementation, and every one of its flags applies:
#
#   ./install.sh --plan-only                    # what would run on this machine?
#   ./install.sh                                # show the plan, then install it
#   ./install.sh --modules rag --tts-engine piper ...
#   ./install.sh --help
#   HURI_CORE_PATH=../huri-core ./install.sh    # with a local huri-core checkout
#   HURI_CLI_PATH=../huri-cli ./install.sh      # with a local huri-cli checkout
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$here/scripts/install_local.sh" "$@"
