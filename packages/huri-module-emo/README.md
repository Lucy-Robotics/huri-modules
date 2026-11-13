# huri-module-emo

Speech emotion from prosody (wav2vec2 classifier) and its aggregator, and the
emotion HuRI expresses (the "out emotion").

| Module | Topics | What it does |
|---|---|---|
| `emo` | `huri.voice` → `huri.emotion` | Estimates the emotion of speech from its prosody |
| `eag` | `huri.emotion` → `huri.partial_question` | Aggregates emotion estimates over an utterance |
| `emo_react` | `huri.question` → `huri.emotion.out` | Reacts to the user's emotion with HuRI's own expression as the question arrives |
| `emo_out` | `huri.token` → `huri.emotion.out` | Expresses the emotion of HuRI's reply, sentence by sentence |

```sh
pip install -e 'packages/huri-module-emo[model]'   # from a huri-modules checkout, next to huri-core and huri-events
HURI_MODULES=emo,eag,emo_react,emo_out ...   # enable it on the server
```

`emo` and `emo_out` need the `model` extra (torch and transformers, for their
classifiers); `eag` and `emo_react` need none of it, so a host that runs only
those installs the package without the extra
(`[tool.huri-modules.extras]` in `pyproject.toml` says which module needs which
extra; `huri hub install` and `scripts/install_local.sh` add it). Hardware pins
of `emo`: `requirements-<variant>.emo.txt` (`cpu`, `nvidia`, `amd`; install them
before the package).

## The out emotion: `huri.emotion.out`

`huri.emotion` is what HuRI **senses** in the user's voice. `huri.emotion.out`
is what HuRI **shows**: one `Expression` event (`huri_events.emotion`, JSON)
that the face, the voice and the gestures can follow, so they agree.

```
huri.question ──> emo_react ──┐  the user's emotion, as soon as the question lands
huri.token    ──> emo_out   ──┴─> huri.emotion.out ──> face / voice / gestures / clients
```

| Field | Meaning |
|---|---|
| `scores` | over `EXPRESSIONS`: neutral, Ekman's six (joy, sadness, surprise, fear, anger, disgust) and three a listener shows (concern, calm, interest); they sum to 1 |
| `label` | the strongest expression other than neutral, or `neutral` when none reaches 0.1 (`LABEL_MIN_SCORE`) |
| `intensity` | `1 - scores["neutral"]`: how far from a neutral face |
| `valence`, `arousal` | in [-1, 1], score-weighted from Russell's circumplex (`AFFECT`): to blend a voice or a gesture style rather than switch it |
| `source` | `reaction` (`emo_react`) or `reply` (`emo_out`) |
| `text` | the sentence of the reply it was read from, or null |
| `end` | the reply's last expression: go back to neutral once the voice has ended |

A consumer needs only huri-events (numpy, no model):

```python
from huri_events.emotion import Expression

expression = Expression.from_wire(payload)
weights = expression.face()   # 52 ARKit blend shape weights, huri_events.face.ARKIT_NAMES order
```

`face()` sums, per expression, the ARKit shapes of its facial action units
(Ekman's FACS: a smile and raised cheeks for joy, raised inner brows and a
frown for sadness, raised inner brows and pressed lips without a frown for
concern, a soft smile for calm, raised outer brows for interest...) weighted
by its score; `FACE` holds the table.
`Expression.from_scores({"joy": 0.6}, "reply")` builds a valid event from any
partial scores (the rest is neutral).

### `emo_react`

When the question arrives, HuRI has the user's emotion (from `qag`) seconds
before the first word of its answer: `emo_react` shows a reaction at once,
during the thinking. It is empathy, not mirroring: joy for a happy user,
concern with a little sadness for a sad one, calm (de-escalation) for an
angry one, calm and concern (reassurance) for an afraid one, attentive
interest for a neutral one. A question without an emotion (typed) resets the face to neutral.
It reads every score of the user's emotion, not only its label, and remembers
the reaction for the session so `emo_out` can colour the reply with it.

| Arg | Default | Meaning |
|---|---|---|
| `reactions` | `DEFAULT_REACTIONS` (`huri_emo/react.py`) | user emotion label → expression scores; covers `emo`'s `neu`, `hap`, `sad`, `ang` and the full words |
| `strength` | 0.6 | share of the face the reaction takes, times the user's score |

### `emo_out`

Reads the emotion of each sentence of the streamed reply with a text
classifier, ahead of the voice (the TTS lags the tokens), so a consumer can set
the face as the sentence is spoken. A sentence ends at `.`, `!`, `?`, `…`
followed by a space, or at a new line; the end token flushes the rest and
yields the reply's last expression with `end` true (an empty reply yields one
too).

The classifier is `SamLowe/roberta-base-go_emotions` (MIT, 28 GoEmotions
labels), grouped into the expressions with GoEmotions' own Ekman mapping
(`GO_EMOTIONS_EKMAN`), except where HuRI is the speaker: `caring` and
`remorse` ("I'm sorry to hear that") are concern, `curiosity` and `confusion`
are interest (a question raises the brows without looking surprised), `relief`
is calm; `HURI_EMO_OUT_MODEL` sets another Hugging Face model
whose labels are GoEmotions' or Ekman's. It runs on CPU: about 26 ms a sentence
on a laptop CPU. Its `EmoOutHandle` deployment needs an entry in the Serve
config, like `EMOHandle`.

| Arg | Default | Meaning |
|---|---|---|
| `empathy` | 0.3 | share of the session's `emo_react` reaction mixed into each sentence |
| `gains` | anger 0.3, disgust 0.3, fear 0.5 | display rules: how much of each expression a social robot shows; the removed share goes to neutral |
| `min_chars` | 12 | a shorter sentence joins the next ("Oh. I see.") |
| `max_chars` | 200 | text without punctuation is cut at a space past this |

A distribution of
[huri-modules](https://github.com/Lucy-Robotics/huri-modules), versioned in its
`pyproject.toml` (see RELEASING.md there). It depends on `huri-core` and
`huri-events`, which are not on PyPI: without a checkout, install them from git
in the same pip command, so pip never looks them up on an index:

```sh
ref=0.1.0   # a huri-modules release tag
core=0.4.0  # HURI_CORE_REF in that release's versions.env
pip install \
  "huri-core[server] @ git+https://github.com/Lucy-Robotics/huri-core.git@$core" \
  "huri-events @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-events" \
  "huri-module-emo[model] @ git+https://github.com/Lucy-Robotics/huri-modules.git@$ref#subdirectory=packages/huri-module-emo"
```
