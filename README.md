# huri-modules

The one repository to clone to try or deploy [HuRI](https://github.com/Lucy-Robotics/HuRI-landing),
the open-source conversational pipeline for humanoid robots and virtual avatars:
speech to text, retrieval-augmented answers from an LLM, emotion, speech
synthesis and co-speech gestures, served to many clients (robots) at once by a
Ray Serve application.

It holds everything around the framework that you need to run it:

- the curated event vocabulary, [`huri-events`](packages/huri-events) (the
  `huri.*` topics and their wire formats);
- the curated, first-party modules, one distribution each under [`packages/`](packages);
- [`registry.toml`](registry.toml), the "verified" index (tier 3) of curated and
  reviewed third-party modules, its CI check [`check_registry.py`](check_registry.py)
  and the listing criteria, [`ACCEPTANCE.md`](ACCEPTANCE.md);
- [`huri-hub`](packages/huri-hub), the `huri hub` command that finds and
  installs modules from that registry and the community (see
  [Finding modules](#finding-modules-huri-hub));
- the installer ([`install.sh`](install.sh)), the Docker images ([`deploy/`](deploy)),
  the Helm chart ([`helm/`](helm)) and the shipped configs ([`config/`](config));
- [`huri-cli`](examples/cli), the reference command-line client.

The framework itself, [`huri-core`](https://github.com/Lucy-Robotics/huri-core),
lives in its own repository and is installed from git at the ref in
[`HURI_CORE_REF`](HURI_CORE_REF) (see [Versions](#versions-and-compatibility)).
To write a module of your own, start from
[`huri-plugin-template`](https://github.com/Lucy-Robotics/huri-plugin-template).

## Quick start

```sh
git clone https://github.com/Lucy-Robotics/huri-modules.git && cd huri-modules
./install.sh --plan-only      # what would run on this machine?
./install.sh                  # show the plan, then install it
.huri-local/start.sh          # start everything
```

Then, in a second terminal:

```sh
source .huri-local/env.sh
huri-cli --config config/client_local.generated.yaml
```

You get a `>>` prompt; typing or speaking both work. `\exit` quits the client
and leaves the server running.

**Use the installer, not a bare `pip install`.** It picks the right torch build
for your GPU, applies `constraints.txt` (without which every Ray Serve replica
crashes — see the comments in that file), creates the virtualenv, installs
huri-core at the pinned ref, downloads the models and writes a Serve config that
matches your hardware. The manual path is documented
[below](#usage-without-the-installer) for people who need it.

## Prerequisites

The installer installs the system packages itself; you only need Python, git, a
package manager it recognises (`apt`, `pacman`, `dnf`, `zypper`) and `sudo`.

|          | Requirement                                                                                        |
| -------- | -------------------------------------------------------------------------------------------------- |
| Python   | **3.10 – 3.12** (3.12 recommended; 3.13+ is not supported yet — `numpy 1.26` has no wheels for it) |
| Disk     | ~8 GiB for the default voice pipeline, ~20 GiB with CosyVoice TTS                                  |
| RAM      | ~16 GiB for the default plan; the installer refuses a plan that will not fit                       |
| Network  | github.com (huri-core), huggingface.co (models) and the Ollama/PyPI mirrors                        |
| Optional | An input device if you want speech in, a GPU for faster STT                                        |

Ubuntu 24.04:

```sh
sudo apt install python3.12 python3.12-venv python3.12-dev python3-pip git
```

Ubuntu 22.04 — its Python is 3.10, which **is supported**, so nothing extra is
needed:

```sh
sudo apt install python3 python3-venv python3-dev python3-pip git
```

Arch / EndeavourOS — `pacman`'s `python` is newer than 3.12, so install a
supported interpreter separately and point the installer at it:

```sh
uv python install 3.12        # or: yay -S python312, or pyenv
./install.sh --python "$(uv python find 3.12)"
```

Everything else (a C toolchain, `ffmpeg`, `libsndfile`, `portaudio`, and the
ROCm runtime on AMD) is installed for you by the `system` stage. Ollama and
Qdrant are installed and started too unless you point at remote ones.

## What runs where

The installer probes the machine and runs the largest pipeline that actually
works on it. Nothing here is aspirational — these are measured outcomes:

| Module                                                     | CPU only                         | NVIDIA       | AMD / ROCm                                |
| ---------------------------------------------------------- | -------------------------------- | ------------ | ----------------------------------------- |
| **STT** (faster-whisper)                                   | ✅ `int8`, realtime for `base`   | ✅ `float16` | ✅ `float16`                              |
| **TTS — piper** (default without an NVIDIA GPU)            | ✅ **~30x faster than realtime** | ✅           | ✅ (runs on CPU; it does not need a GPU)  |
| **TTS — cosyvoice** (default on NVIDIA with ≥4.4 GiB free) | ⚠️ ~7x _slower_ than realtime    | ✅ `fp16`    | ❌ vocoder faults in a MIOpen convolution |
| **RAG / LLM** (Ollama)                                     | ✅ tier picked from free RAM     | ✅           | ✅                                        |
| **EMO** (prosody)                                          | ✅ always CPU                    | ✅           | ✅                                        |
| **MOV / gesture** (EMAGE)                                  | ❌ slower than realtime          | ✅           | ❌ not in the ROCm build                  |

So **a machine with no GPU at all still gets voice in and voice out** — that is
the point of Piper being the default. A GPU buys faster STT, and on NVIDIA it
additionally unlocks CosyVoice and gesture generation.

## Choosing what to run

The plan is a starting point, not a cage.

```sh
# Only the modules you name (subset of vad,stt,tag,emo,eag,qag,rag,tts,mov)
./install.sh --modules vad,stt,tag,qag,rag              # voice in, text out
./install.sh --modules rag                              # text in, text out

# Where things run
./install.sh --stt-device cpu                           # keep the GPU free
./install.sh --tts-engine cosyvoice --voice-sample me.wav \
             --voice-transcript "exactly what is said in it"

# Keep a module the plan dropped
./install.sh --force-tts --force-gesture --force-emo
```

`--modules` sets `HURI_MODULES`, the server's allow-list: it decides which of
the installed modules the server registers (see [Modules as plugins](#modules-as-plugins))
and therefore which Serve deployments exist. The **client** config must list the
same module names — a client asking for a module the server did not register is
rejected with a `session_error` naming both sets.

`./install.sh --help` lists every flag.

## The curated modules

| Package | Modules | What it does |
|---|---|---|
| [`huri-events`](packages/huri-events) | — | the `huri.*` event classes and wire formats (light: huri-core + numpy) |
| [`huri-module-vad`](packages/huri-module-vad) | `vad` | detects speech in microphone frames (WebRTC VAD) |
| [`huri-module-stt`](packages/huri-module-stt) | `stt` | transcribes speech with faster-whisper (CPU or GPU) |
| [`huri-module-tag`](packages/huri-module-tag) | `tag` | stitches overlapping transcript passes into one sentence |
| [`huri-module-emo`](packages/huri-module-emo) | `emo`, `eag` | estimates the emotion of speech from its prosody, and aggregates it over an utterance |
| [`huri-module-rag`](packages/huri-module-rag) | `rag`, `qag` | answers from memory with an LLM, streaming tokens; joins a transcript and its emotion into a question; ingestion and memory tools |
| [`huri-module-mov`](packages/huri-module-mov) | `mov` | SMPL-X co-speech gestures (EMAGE, vendored model code) |
| [`huri-module-tts`](packages/huri-module-tts) | `tts`, `tts_cosyvoice` | speaks the reply: `tts` is the default engine, Piper (ONNX, any CPU; extra `piper`); `tts_cosyvoice` clones a voice with CosyVoice3 (NVIDIA; extra `cosyvoice`), wired instead of `tts` |
| [`huri-hub`](packages/huri-hub) | — | `huri hub`: finds and installs modules (a tool, not in the registry; it reads it) |

Each has its own `pyproject.toml` (and version), `huri_module.toml` manifest(s),
tests and, for GPU modules, hardware pins (`requirements-<variant>.txt`, see
[Module dependencies per hardware](#module-dependencies-per-hardware)). A
package whose modules need different stacks gives each module an extra
(`[tool.huri-modules.extras]`): huri-module-tts installs no engine by itself,
`huri-module-tts[piper]` brings Piper's and `[cosyvoice]` CosyVoice's;
`huri-module-emo[model]` is `emo`'s classifier (torch, transformers) and
`huri-module-rag[rag]` is `rag`'s retrieval stack, which their aggregators
`eag` and `qag` do not need. Without a
checkout, one installs from this repository at a release tag with
`#subdirectory=`. Neither huri-core nor huri-events is on PyPI, so a module's
HuRI dependencies go on the same pip command line, from git, and pip never
looks them up on an index:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # the huri-core ref in that release's HURI_CORE_REF file
pip install \
  "huri-core[server] @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events" \
  "huri-module-stt @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-stt"
```

Each package's README has its full command (with the extra a TTS module
needs: `"huri-module-tts[piper] @ ...#subdirectory=packages/huri-module-tts"`).
`huri hub install stt` (the [`huri-hub`](#finding-modules-huri-hub) package)
does the same for any module of the registry: it installs the module (with its
extra) and huri-events from git, in one pip command, at the huri-modules ref it
reads the registry from.

## Versions and compatibility

This repository has its own semver, tagged `X.Y.Z` (no `v`) on `main`; `dev`
is the integration branch. A release is a tested set: every package of
`packages/` at its own version, plus the huri-core ref in
[`HURI_CORE_REF`](HURI_CORE_REF) they were all tested against — a core release
tag on `main`; on `dev`, that tag too, or huri-core's `dev` while the modules
follow the next core minor (with their ranges moved to it in the same change).
The installer, the Docker images and CI all read that one file.

| huri-modules | huri-core |
|---|---|
| 0.1.x | 0.4.x |

CI tests every change against the pinned core (blocking) and against
huri-core's `dev` branch (non-blocking), so a breaking core change shows up
before it is released (that canary is red by design between a core minor
release and the change that moves the modules to the next minor). [RELEASING.md](RELEASING.md) has the policy and the
release checklist.

To develop huri-core and the modules together, point the installer at a local
checkout instead of the git ref: `HURI_CORE_PATH=../huri-core ./install.sh`
installs it editable (and `HURI_CORE_REF=<ref> ./install.sh` picks another ref).

## Docker images

`deploy/Dockerfile.{base,nvidia,amd}` build one image per hardware target (the
Helm values pick them per worker group). Each installs its hardware's heavy
dependencies from the requirement files, then `deploy/install_huri.sh` installs
huri-core from git at the `HURI_CORE_REF` build arg (default: the
`HURI_CORE_REF` file) and every package under `packages/` (all curated events
and modules: the head node imports each enabled module to build the app), and
**fails the build** if a module or event does not load or the versions do not
satisfy the modules' manifests.

The images carry one TTS engine: CosyVoice3 (`tts_cosyvoice`, deployment
`CosyVoiceTTSHandle`), whose stack the nvidia image installs
(`requirements-nvidia.txt`), as the Helm examples in `deploy/examples` and
`config/huri.yaml` serve it. No image installs Piper's `piper-tts`: its
`onnxruntime` would overwrite the nvidia image's `onnxruntime-gpu`, so `tts`
(Piper) loads there but its replica fails on its first utterance. Piper is
for bare metal (`./install.sh`, `config/huri_cpu.yaml`).

When `HURI_CORE_REF` is a branch (`dev`), Docker's layer cache keeps the core
of the first build: pass the commit it is at instead,
`--build-arg HURI_CORE_REF="$(git ls-remote https://github.com/Lucy-Robotics/huri-core.git refs/heads/dev | cut -f1)"`,
or build with `--no-cache`. A release tag does not move, so it needs neither.

```sh
docker build -f deploy/Dockerfile.base --build-arg HURI_CORE_REF="$(cat HURI_CORE_REF)" -t huri:base .
docker run --rm -p 8000:8000 -e HURI_MODULES=tag,qag huri:base \
  python -c "import ray; from ray import serve; from huri_core.app import build_app; ray.init(); serve.start(http_options={'host': '0.0.0.0'}); serve.run(build_app(), name='HuRI', blocking=True)"
python deploy/smoke_session.py ws://127.0.0.1:8000/session   # a real session, no model needed
```

A branch ref (`dev`) is resolved when that layer is built, and Docker's cache
keeps the commit it got until the layer is rebuilt (`--no-cache`); a release
tag does not move. CI (`.github/workflows/docker.yml`) builds and runs the base
image on pull requests; the GPU images are built on demand (`workflow_dispatch`).

## Local install (single machine, no Kubernetes)

`./install.sh` (implemented by `scripts/install_local.sh`) is the bare-metal
counterpart of the Docker images in `deploy/` and the Helm chart in `helm/`. It
probes the machine (GPU vendor, VRAM, RAM, disk, Python), works out which parts
of the pipeline actually fit, and then installs only those: system packages, a
virtualenv with the right torch build, huri-core and the curated packages, the
model weights, Qdrant and Ollama, plus a Ray Serve config matching the plan.

It generates:

| Path                                             | What                                              |
| ------------------------------------------------ | ------------------------------------------------- |
| `config/huri_local.generated.yaml`               | Ray Serve config for this machine                 |
| `config/client_local.generated.yaml`             | matching client config                            |
| `.huri-local/huri.env`                           | the same runtime env vars as a plain `.env`       |
| `.huri-local/secrets.env`                        | LLM API key, `0600`, never in the configs         |
| `.huri-local/env.sh`                             | venv + `huri.env` + `secrets.env` in one `source` |
| `.huri-local/start.sh` / `stop.sh` / `status.sh` | run the stack                                     |
| `.huri-local/plan.env`                           | the hardware plan it acted on                     |

```sh
.huri-local/start.sh                 # services + ray head + serve deploy
source .huri-local/env.sh
huri-cli --config config/client_local.generated.yaml
.huri-local/status.sh                # what is up, locally and remotely
.huri-local/stop.sh --all
```

`start.sh` waits for the application to report `RUNNING` and exits non-zero with
the replica errors if it does not, so a failed deploy is never reported as
success. `status.sh` reads the _application_ status, not the HTTP proxy's health
route (which is green even when every deployment is dead).

`source .huri-local/env.sh` gives a shell configured exactly like a Serve
replica (model paths, `HURI_MODULES`, STT device, API key), so
`python -m huri_core.launch_huri` and `python -m huri_rag.ingestion` work
without `serve deploy`.

Re-run a single stage with `--only`:

```sh
./install.sh --only config          # regenerate the configs
./install.sh --only verify          # re-check this machine
./install.sh --only python,models   # reinstall deps (huri-core included) and weights
```

### Module dependencies per hardware

Each module package declares its own dependencies (`pyproject.toml`) and, where
the hardware matters, exact pins per variant: `requirements-<variant>.txt`
(`cpu`, `nvidia`, `amd`) for every module of the package, and
`requirements-<variant>.<module>.txt` for one module only (e.g.
`packages/huri-module-tts/requirements-nvidia.tts_cosyvoice.txt`: CosyVoice's
stack, which a host running Piper's `tts` never gets). A module also brings the
dependencies of its extra, when its package maps it to one
(`[tool.huri-modules.extras]`, e.g. `tts = "piper"`). huri-module-emo and
huri-module-rag split the same way: `emo`'s torch and transformers are its
`model` extra and its pins `requirements-<variant>.emo.txt`, `rag`'s Qdrant and
sentence-transformers its `rag` extra and `requirements-amd.rag.txt`, so a host
that plans only the aggregators `eag` and `qag` (which import neither) stays
light.
The installer merges them for the modules it planned and the host's variant —
`scripts/module_requirements.py --variant nvidia --modules stt,rag,...` — and
**stops before installing anything heavy** if two modules pin the same package
to different versions, naming both. The merged file
(`.huri-local/requirements-modules.generated.txt`) is installed last.

### Feeding the RAG memory

Conversation memory works out of the box (`RAGHandle` creates its collection on
demand). Document retrieval needs an explicit ingest, and **the embedding model
must match the one the server queries with**, or the vectors will not line up:

```sh
source .huri-local/env.sh
python -m huri_rag.ingestion --help
python -m huri_rag.ingestion \
  --embedding-model "$HURI_EMBED_MODEL" --embedding-url "$HURI_EMBED_URL" ...
```

Until you do, `--only verify` and the `RAGHandle` startup log both warn that the
`documents` collection is missing and that retrieval will return nothing.

### Using a remote LLM instead of a local one

Any endpoint that is not on localhost is treated as already running: it is not
installed, not started, and costs no local VRAM — which is what frees a small
GPU to run TTS and gesture generation.

```sh
# self-hosted vLLM over HTTPS with a private CA / self-signed cert
./install.sh \
  --llm-url https://llm.example.lan --llm-provider vllm \
  --llm-model Qwen3.5-4B-GGUF --no-verify-ssl \
  --embed-url https://embedding.example.lan --embed-model bge-large-en-v1.5-gguf-Q4_K_M

# hosted OpenAI-compatible API (key goes to .huri-local/secrets.env, 0600)
./install.sh \
  --llm-url https://api.example.com --llm-provider api \
  --llm-model some-model --llm-api-key "$MY_KEY"

# remote Qdrant too — then nothing but HuRI itself runs locally
./install.sh --qdrant-url https://qdrant.example.lan
```

The provider selects the wire protocol (`ollama` → `/api/chat`, `vllm`/`api` →
`/v1/chat/completions`, only `api` sends the bearer token) and is inferred from
the URL and whether a key was given. Re-point an existing install without
reinstalling anything: `./install.sh --only config --llm-url ... --llm-model ...`
then restart (`.huri-local/stop.sh && .huri-local/start.sh`).

## Troubleshooting

| Symptom                                                        | Cause / fix                                                                                                                                                          |
| -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Task was killed due to the node running low on memory`        | The plan did fit but Ollama's KV cache grew. Use a smaller tier: `--llm-model mistral:7b`, and `ollama stop <big-model>`.                                            |
| `torch sees the GPU but cannot launch a kernel on it`          | The torch build has no code objects for your GPU. `--only config` sets `HSA_OVERRIDE_GFX_VERSION` when a compatible same-family arch exists; otherwise run CPU-only. |
| Client exits with `session_error: Unknown module 'x'`          | The client config lists a module the server did not register. Match it to `HURI_MODULES`, or regenerate both with `--only config`.                                   |
| Client exits with `session_error: Module 'x' is installed but not enabled` | The server's `HURI_MODULES` does not list it (unset enables nothing). Add it there, or drop it from the client config. |
| The server refuses to start: the installed `huri-core` is not in a module's range | The core in the venv is not the one this checkout is tested with. `./install.sh --only python` reinstalls huri-core at the commit `HURI_CORE_REF` names now (a branch such as `dev` included); or check out the huri-modules release matching your core (see [Versions](#versions-and-compatibility)). |
| `qdrant is not answering … HuRI does not manage this instance` | Something else already owned the port when you installed, so HuRI never created its own. Start it yourself, or free the port and re-run `--only services`.           |
| No audio from the client                                       | Check TTS is actually enabled (`grep HURI_MODULES config/huri_local.generated.yaml`) — with `--modules` or a dropped plan there may be no TTS at all.                |
| No module loads: `entry point 'tts' is registered twice` | An environment from before huri-module-tts still has the three packages it replaced. `./install.sh --only python` removes them; otherwise `pip uninstall huri-tts huri-module-tts-piper huri-module-tts-cosyvoice`, then install huri-module-tts. |
| Install fails with no explanation                              | Every command's output is in `.huri-local/install.log`, and the error line names the real command.                                                                   |

## Usage without the installer

Both entrypoints need huri-core, the curated packages and their dependencies
installed in the active venv ([below](#manual-dependency-install)); the
generated `config/huri_local.generated.yaml` is the reference for what the env
vars must contain.

```sh
ray start --head --num-cpus=8 --num-gpus=1     # --num-gpus >= the config's total
serve deploy config/huri_local.generated.yaml  # or your own Serve config
```

We use a Ray Serve config file, doc [here](https://docs.ray.io/en/latest/serve/configure-serve-deployment.html).
`serve run <config>` works too but blocks; `serve deploy` returns immediately.

Without Serve at all (single process, no `deployments:` overrides — note that
the RAG settings then come from `RAGDeploymentConfig`'s env fallbacks, not from
a Serve `user_config`):

```sh
python -m huri_core.launch_huri
```

Client — the reference CLI is its own package, [examples/cli](examples/cli):

```sh
pip install --no-deps -e examples/cli
huri-cli --config config/client_local.generated.yaml
```

Client config template: [config/client_template.yaml](config/client_template.yaml).
Server-side examples: [config/huri.yaml](config/huri.yaml) (GPU) and
[config/huri_cpu.yaml](config/huri_cpu.yaml).

### Manual dependency install

Only if you are not using `./install.sh`. `-c constraints.txt` is not optional —
see that file's header for what breaks without it.

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -c constraints.txt -r serve_requirements.txt -r requirements.txt
# NVIDIA, for CosyVoice TTS + EMAGE gesture:
pip install -c constraints.txt -r requirements-nvidia.txt
# AMD: install the ROCm runtime from your distro, then the official ROCm wheels
# (repo.radeon.com's ".lw." wheels omit consumer RDNA3 — see scripts/install_local.sh)
pip install -c constraints.txt --index-url https://download.pytorch.org/whl/rocm6.4 \
  torch==2.8.0+rocm6.4 torchaudio==2.8.0+rocm6.4
pip install -c constraints.txt -r requirements-amd.txt

# huri-core at the ref this checkout is tested against, then the curated
# events and modules and the CLI (their dependencies come from the files above)
pip install -c constraints.txt \
  "huri-core[server,client] @ git+https://github.com/Lucy-Robotics/huri-core.git@$(cat HURI_CORE_REF)"
for p in packages/*/; do pip install -c constraints.txt --no-deps -e "$p"; done
pip install --no-deps -e examples/cli
# the TTS engine, in no file above: Piper (module tts) is huri-module-tts's
# `piper` extra; CosyVoice (tts_cosyvoice), see packages/huri-module-tts
pip install -c constraints.txt -e 'packages/huri-module-tts[piper]'
```

Note that `requirements.txt` also contains the lint/test toolchain.

### Installing as packages, without a checkout

`huri-core` is pip-installable from its repository, with its heavy parts behind
extras so a client never installs Ray or FastAPI; the curated packages install
from this repository with `#subdirectory=`:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # the huri-core ref in that release's HURI_CORE_REF file
CORE="git+https://github.com/Lucy-Robotics/huri-core.git@$core"
MODS="git+https://github.com/Lucy-Robotics/huri-modules.git@$ref"
# a client: the SDK, the curated topics and the reference CLI (no Ray)
pip install "huri-core[client] @ $CORE" \
  "huri-events @ $MODS#subdirectory=packages/huri-events" \
  "huri-cli @ $MODS#subdirectory=examples/cli"
# a server: Ray Serve + FastAPI, then the modules (see each package's README)
pip install "huri-core[server] @ $CORE" \
  "huri-events @ $MODS#subdirectory=packages/huri-events"
```

huri-core, huri-events and huri-cli are not on PyPI: always give them
as git URLs on the same command line as what depends on them, so pip never
looks them up on an index. Use release tags, a huri-core `0.4.x` with a
huri-modules `0.1.x` (see [Versions](#versions-and-compatibility)); without a
ref pip takes the repository's default branch. `[server]` is Ray Serve + FastAPI and the pins that
keep them working; each module package declares its own dependencies, and the
GPU ones ship `requirements-<variant>[.<module>].txt` pins next to it.

## Testing

```sh
make lint                              # black, isort, flake8, mypy (as CI; `make format` fixes the first two)
make test                              # pytest: tests/ and packages/*/tests
make check                             # both
python check_registry.py --run-tests   # the registry against its packages
python scripts/check_links.py          # the pages' relative links and anchors
scripts/test_install.sh                # the installer in clean Ubuntu containers
```

`tests/` holds what spans the curated set: the packages and the registry agree,
the shipped client configs are complete sessions, the curated events and
modules are discovered from their own distributions, the whole pipeline runs in
one session (`tests/integration`: real modules on the real bus, faked model
deployments), and the CLI works. huri-core's own tests live in its repository.

### Testing a module alone

`huri_core.testing` runs one module in-process — no Ray, no server, only the
base `huri-core` install. `ModuleHarness` builds the module like the server
does, checks the events in and out against its `input_event` / `output_event`,
and returns what it emitted; `LocalHandle` stands in for the Serve deployment
of a `ModuleWithHandle`:

```python
from huri_core.testing import LocalHandle, ModuleHarness


class FakeWhisper:  # the deployment's methods the module calls
    async def transcribe(self, audio, language):
        return "hello"


stt = ModuleHarness(STT, handle=LocalHandle(FakeWhisper()), language="en")
assert stt.run(Voice(frames), Voice(None))[-1] == Transcript("hello", True)
```

See [`packages/huri-module-stt/tests/test_stt.py`](packages/huri-module-stt/tests/test_stt.py).

## Folder structure

- **packages/huri-events** — `huri-events`, the curated event classes (one per
  `huri.*` topic, with its wire format): a separate, light distribution
  (huri-core + numpy) that clients install to decode the topics
- **packages/huri-module-\*** — the curated modules (see
  [the table above](#the-curated-modules))
- **modules/** — verified third-party modules, as git submodules pinned at a
  tag (see [modules/README.md](modules/README.md))
- **packages/huri-hub** — `huri hub`, which finds and installs modules, and the
  one reader of the registry's schema
- **registry.toml**, **check_registry.py**, **ACCEPTANCE.md** — the verified
  index, its CI check (with huri-hub's reader) and the listing criteria
- **HURI_CORE_REF** — the huri-core ref this repository is built and tested
  against; **RELEASING.md** — versions and releases
- **install.sh**, **scripts/** — the local installer (`install_local.sh`), the
  per-hardware requirement merger (`module_requirements.py`), the installer
  portability test (`test_install.sh`) and the pages' link check
  (`check_links.py`)
- **config/** — Serve configs and client configs (`client_*.yaml`)
- **deploy/** — the Docker images, `install_huri.sh`, a session smoke test and
  Helm values examples (`deploy/examples`); **helm/** — the KubeRay chart
- **examples/cli** — `huri-cli`, the reference command-line client: a separate
  package built on the client SDK (microphone and prompt in; text, speech and a
  gesture view out)
- **tests/** — the curated set's tests; **tests/integration** — the whole
  curated pipeline in one session

## Modules as plugins

The server finds modules and events through Python entry points, so any
installed package can provide them; nothing has to be added to huri-core or to
this repository. The curated modules are registered the same way, each in its
package's `pyproject.toml`, which is why they must be installed
(`pip install -e packages/<name>`, done by the installer and the images) and not
just checked out.

```toml
[project.entry-points."huri.modules"]
servo = "huri_servo.module:Servo"          # usable as `name: servo`

[project.entry-points."huri.events"]
"acme.servo_angles" = "huri_servo.events:ServoAngles"
```

Event topics are namespaced, `<namespace>.<name>`. The curated topics are
`huri.audio.in`, `huri.audio.out`, `huri.voice`, `huri.transcript`,
`huri.emotion`, `huri.partial_question`, `huri.question`, `huri.token` and
`huri.motion`, registered by [`huri-events`](packages/huri-events), which owns
the `huri.` namespace; huri-core itself defines no concrete event. Any other
package registers its topics under a single namespace of its own (`acme.*`),
which no other package may use — so a replacement event (`acme.motion`) can live
next to the curated one instead of overriding it.

`HURI_MODULES` is the server's allow-list, and it is **default-deny**: unset or
empty, the server enables no module and rejects every session, because any
installed package can register one. List each module explicitly
(`HURI_MODULES=vad,stt,tag,qag,rag`); the installer and the shipped serve
configs do.

A module that can **move hardware** declares it (`requires.actuation = true` in
its manifest, or `actuation = True` on its class), and the server refuses to
start with it enabled unless its operator also lists it in
`HURI_ACTUATION_MODULES` — a separate, explicit consent, independent of
`HURI_MODULES` and of any verified mark. Active actuation modules are logged as
a warning at startup. A module that is not packaged at all can be tried by import path:
list it in `HURI_MODULES` (`HURI_MODULES=stt,my_pkg.servo:Servo`) and reference
it from the client config with `module: my_pkg.servo:Servo` instead of `name:`.
A client can never make the server import a path the server did not list.

### Module manifest (`huri_module.toml`)

A module package ships one `huri_module.toml` per module, next to its code, so
tooling can reason about the module without importing it:

```toml
name = "servo"                     # its huri.modules entry point name
summary = "Gesture to servo angle arrays"
input = { topic = "huri.motion", event = "huri_events.motion:Motion" }
output = { topic = "acme.servo_angles", event = "huri_events_servo:ServoAngles" }  # omit for a terminal module
events = ["huri-events>=0.1,<0.2", "huri-events-servo>=0.1,<0.2"]                  # optional
requires = { gpu = false, actuation = false }                                      # optional
huri_core = ">=0.4,<0.5"
```

At startup the server checks every enabled module that has one: its topics and
event classes must match the module class, and the installed `huri-core` and
event packages must be in the declared ranges — otherwise it refuses to start
and says which. (huri-core's `huri_core/core/manifest.py` is the reference
parser; an unpackaged module's manifest sits next to its code and is named after
its class, lowercased.)

### Finding modules: `huri hub`

```sh
huri hub search gesture             # GitHub repositories tagged `huri-module`
huri hub list                       # modules supporting the installed huri-core
huri hub list --verified            # only this registry
huri hub list --core 0.5.0 --all    # another version; --all keeps unsupported ones
huri hub install stt                # pip-installs a verified module; never enables it
huri hub install servo --community  # an unreviewed one: only when asked explicitly
```

The command comes from [`huri-hub`](packages/huri-hub), a light package of this
repository (huri-core's base install and `packaging`: no Ray, no torch).
`huri` is huri-core's command, which runs what installed packages register in
its `huri.commands` entry point group; `huri-hub ...` and
`python -m huri_hub ...` are the same command without it. `./install.sh`
installs it (not the Docker images: a pod runs the modules of its image);
otherwise, from git:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # the huri-core ref in that release's HURI_CORE_REF file
pip install \
  "huri-core @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-hub @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-hub"
```

It reads this repository's `registry.toml` from, in order:

- `--registry PATH|URL|CHECKOUT` (`list` and `install`);
- `HURI_REGISTRY`, a path or URL;
- the huri-modules checkout the current directory is in;
- else GitHub, at the huri-modules ref `HURI_MODULES_REF` (a release tag of
  this repository; `main` when unset).

It installs curated modules from this repository at that same ref
(`git+https://github.com/Lucy-Robotics/huri-modules.git@<ref>#subdirectory=packages/<name>`),
or from the checkout it read, with the extra of their package they need
(`extra`) and the curated packages they depend on (huri-events) in the same
pip command; verified third-party ones at
their pinned tag. A module whose `huri_core` range excludes the installed
huri-core is refused rather than letting pip replace huri-core. Community
modules are read from their `pyproject.toml` and `huri_module.toml` on GitHub
without installing them, and listed when their manifest supports the huri-core
version. Nothing found is ever loaded: enabling a module stays the server
operator's `HURI_MODULES` decision. `GITHUB_TOKEN` raises GitHub's rate limit.

`huri_hub.registry` is the one reader of the registry schema: `check_registry.py`
checks the real file with it, so what CI accepts is what `huri hub` reads.

## The registry: listing a module

Being listed in `registry.toml` is HuRI's "verified" mark (tier 3): a reviewed
pull request, checked by CI (`check_registry.py`); [ACCEPTANCE.md](ACCEPTANCE.md)
says what a listing requires. An entry's `source` is one of two kinds:

```toml
# a curated module of this repository
source = { path = "packages/huri-module-stt" }
# a verified third-party module, pinned as a git submodule at a tag
source = { git = "https://github.com/acme/huri-module-servo", tag = "0.1.0", submodule = "modules/servo" }
# (optional key: subdirectory = "<dir>", when the package is not at the root of its repository)
```

An entry may also name `extra = "<extra>"`, the extra of its package the module
needs (huri-module-tts: `tts` needs `piper`, `tts_cosyvoice` needs
`cosyvoice`; `emo` needs `model`, `rag` needs `rag`); `huri hub` installs `"<package>[<extra>] @ <source>"`, and
`check_registry.py` checks it against the package's
`[tool.huri-modules.extras]`. The registry is `schema = 2` since `extra`
exists: huri-hub 0.1, which would have installed those modules without their
engine, refuses it and says to upgrade.

### Adding a verified third-party module

A third-party module stays in its author's repository; this repository pins
the reviewed tag of it as a git submodule. CI checks that the submodule comes
from the entry's `git` URL (in `.gitmodules`) and is at the commit the entry's
`tag` names in that repository, which is what `huri hub install` installs
(`git+<git>@<tag>`); a tag the author moves afterwards fails the next CI run:

```sh
git submodule add https://github.com/acme/huri-module-servo modules/servo
git -C modules/servo checkout 0.1.0          # the reviewed tag
git add .gitmodules modules/servo
```

Then add its `[[module]]` entry to `registry.toml` — the `name`, `package`,
`summary`, `huri_core` and `verified` keys of [ACCEPTANCE.md](ACCEPTANCE.md), and
`source = { git = "https://github.com/acme/huri-module-servo", tag = "0.1.0", submodule = "modules/servo" }`
— install it (`pip install -e modules/servo`, or
`pip install -e modules/servo/<subdirectory>` when the entry has one;
`python check_registry.py --install-dirs` prints those directories, which is
what CI installs) and run `python check_registry.py --run-tests --strict`: with
its submodule checked out, the entry is checked like a curated one, and the
checkout must be at the commit of `tag` in the entry's repository (this reads
its tags with `git ls-remote`, so it needs the network).
Bumping it later is the same pull request with a new tag
(`git -C modules/servo checkout 0.2.0` and `tag = "0.2.0"`).

Clone with `git clone --recursive` (or run `git submodule update --init`) to
get those modules' code; without it their folders are empty, and
`check_registry.py` skips them and names them (`--strict` fails instead). The
installer and the images install the curated packages only; a verified
third-party module is installed like any other module (`huri hub install <name>`).

## Client SDK

`huri_core.core.client.HuRIClient` (`huri-core[client]`, plus `huri-events`
for the curated topics: see [Installing as packages](#installing-as-packages-without-a-checkout))
is all a use case needs to talk to a
server: it owns the websocket, the session handshake and the wire encoding, and
calls your handlers.

```python
from huri_core.core.client import HuRIClient
from huri_events.question import RAGQuestion
from huri_events.speech import Transcript
from huri_events.text import Token


async def print_token(token: Token) -> None:
    print(token.text, end="", flush=True)


client = HuRIClient(
    "ws://localhost:8000/session",
    modules={"rag": {"name": "rag"}},
    sends=["huri.question"],
)
client.on("huri.token", Token, print_token)  # before connecting
async with client:
    await client.send("huri.question", RAGQuestion(Transcript("Hello!", True), None))
    await client.wait_closed()
```

Handlers of one topic run in arrival order; different topics run concurrently.
A client YAML (`config/client_*.yaml`) holds the same three things — `modules`,
`send` and `subscribe` — and the reference CLI ([examples/cli](examples/cli),
`huri-cli --config ...`) adds its device settings under `cli:`.

## More

- [HuRI](https://github.com/Lucy-Robotics/HuRI-landing) — where to start, the
  architecture and how to contribute
- [huri-core](https://github.com/Lucy-Robotics/huri-core) — the framework, its
  public surface and its releases
- [huri-plugin-template](https://github.com/Lucy-Robotics/huri-plugin-template) —
  the starting point for a new module
- HuRI's complete developer documentation is available
  [here](https://docs.sentience-robotics.fr/share/p1x9ikjkhf/p/hu-ri-documentation-f4amcndYQg).
