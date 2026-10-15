# huri-tts

The engine-agnostic half of HuRI's text-to-speech modules: `StreamingTTS`
opens one synthesis session per utterance, pushes each `huri.token` into it in
order and streams `huri.audio.out` back with presentation timestamps. An engine
package subclasses it and sets `_handle_cls` to a Serve deployment exposing
`get_sample_rate()`, `start_session(sid)`, `push_text(sid, text, end)` and
`stream_audio(sid)`.

A library only: it registers no module. Used by `huri-module-tts-piper` and
`huri-module-tts-cosyvoice`.

A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). It depends on `huri-core` and
`huri-events`, which are not on PyPI: without a checkout, install them from git
in the same pip command, so pip never looks them up on an index:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # the huri-core ref in that release's HURI_CORE_REF file
pip install \
  "huri-core[server] @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events" \
  "huri-tts @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-tts"
```
