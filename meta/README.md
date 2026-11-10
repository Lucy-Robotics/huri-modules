# huri-modules

The curated modules of [HuRI](https://github.com/Lucy-Robotics/huri-core#huri-ecosystem),
the realtime conversational pipeline for robots, picked with extras:

```bash
pip install "huri-modules[vad,stt,tts]"   # just those three
pip install "huri-modules[all]"           # every module, and huri hub
```

This package has no code. Each extra installs one module's own package
(`huri-module-vad`, ...) at the exact version tested in this huri-modules
release, so a server sees only the modules you picked. The version of
`huri-modules` is the release of the
[huri-modules repository](https://github.com/Lucy-Robotics/huri-modules), which
lists what each release contains and the huri-core it supports
(huri-modules 0.1.x: huri-core 0.4.x).

| Extra | Module | Package |
|---|---|---|
| `vad` | detects speech in microphone frames | `huri-module-vad` |
| `stt` | transcribes speech (faster-whisper) | `huri-module-stt` |
| `tag` | stitches overlapping transcript passes into one sentence | `huri-module-tag` |
| `emo` | estimates the emotion of speech from its prosody | `huri-module-emo[model]` |
| `eag` | aggregates emotion estimates over an utterance | `huri-module-emo` |
| `rag` | answers questions from memory with an LLM | `huri-module-rag[rag]` |
| `qag` | joins a transcript and its emotion into a question | `huri-module-rag` |
| `tts` | speaks the reply with Piper (CPU) | `huri-module-tts[piper]` |
| `tts-cosyvoice` | speaks the reply in a cloned voice with CosyVoice3 | `huri-module-tts[cosyvoice]` |
| `mov` | generates gestures matching the speech (EMAGE) | `huri-module-mov` |
| `think` | moves like someone thinking until the answer starts | `huri-module-think` |
| `think-say` | thinks aloud in the TTS's Piper voice until the answer starts | `huri-module-think` |
| `think-token` | silences `think_say` once the answer is being written | `huri-module-think` |
| `think-stop` | ends the thinking when the answer is heard | `huri-module-think` |
| `lips` | lip sync: ARKit blend shapes in step with the voice (LAM) | `huri-module-lips-sync` |
| `tts-phonemes` | Piper text to speech carrying its phoneme timings, for lip sync | `huri-module-lips-sync` |
| `events` | the `huri.*` events only | `huri-events` |
| `hub` | `huri hub` | `huri-hub` |

A module still has to be enabled on the server (`HURI_MODULES`). pip installs
the default PyTorch build: for a GPU machine (CUDA or ROCm), for CosyVoice
(which pip cannot install), and for the models and services some modules
need (Qdrant and Ollama for `rag`), use the repository's `install.sh`.
