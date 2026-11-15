"""The engine-agnostic half of HuRI's text-to-speech modules."""

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, AsyncGenerator

import numpy as np
from huri_events.audio import Audio
from huri_events.text import Token

from huri_core.core.module import ModuleWithHandle

if TYPE_CHECKING:
    from ray.serve import handle

_END_AUDIO = object()  # sentinel pushed into the audio queue when synthesis ends


class StreamingTTS(ModuleWithHandle):
    """Streaming TTS module — bistream tokens-in / audio-out, engine-agnostic.

    Opens one synthesis session per utterance (delimited by `token.end`). Each
    incoming token is pushed straight into the model's text generator so audio
    starts coming back before the LLM has finished producing the response.
    No clause buffering on our side — CosyVoice's frontend handles segmentation
    and stitches LM calls together across the whole utterance.

    It drives any deployment exposing ``get_sample_rate()``,
    ``start_session(sid)``, ``push_text(sid, text, end)``,
    ``stream_audio(sid)`` (an async generator of Audio) and ``end_session(sid)``
    (ends an interrupted session, idempotent); an engine package subclasses it
    and sets ``_handle_cls`` to its deployment.

    input: huri.token (Token)
    output: huri.audio.out (Audio)
    """

    input_type = "huri.token"
    output_type = "huri.audio.out"
    input_event = Token
    output_event = Audio

    def __init__(self, _handle: "handle.DeploymentHandle"):
        super().__init__(_handle)
        self._session_id: str | None = None
        self._audio_q: asyncio.Queue | None = None
        self._stream_task: asyncio.Task | None = None
        # The EventGraph fans each token out as its own concurrent process()
        # task on this shared instance. This lock serialises session setup and
        # text pushes so tokens reach CosyVoice's text queue in arrival order,
        # exactly once. asyncio.Lock wakes waiters FIFO and tokens are created
        # in order, so order is preserved — crucially the end-of-utterance token
        # can no longer overtake a content token (which would truncate synthesis
        # and silently drop trailing words).
        self._push_lock = asyncio.Lock()

    async def process(self, token: Token) -> AsyncGenerator[Audio, None]:
        # Acquire BEFORE any await so lock-acquisition order matches token order.
        # Setup + push happen under the lock; only the first token of an
        # utterance goes on to drain/yield audio (outside the lock, so pushes of
        # later tokens are never blocked by the long-running drain).
        opened = finished = False
        stream_task = None
        try:
            async with self._push_lock:
                sid = self._session_id
                if sid is None:
                    sid = self._session_id = str(uuid.uuid4())
                    audio_q = self._audio_q = asyncio.Queue()
                    opened = True
                    print(f"[TTS-client] [{sid}] opening new utterance session")
                    await self._handle.start_session.remote(sid)
                    stream_task = self._stream_task = asyncio.create_task(
                        self._drain_audio(sid, audio_q)
                    )
                print(
                    f"[TTS-client] [{sid}] push token: {token.text!r} (end={token.end})"
                )
                await self._handle.push_text.remote(sid, token.text, token.end)

            if not opened:
                return

            assert stream_task is not None
            count = 0
            while True:
                item = await audio_q.get()
                if item is _END_AUDIO:
                    break
                count += 1
                print(f"[TTS-client] [{sid}] yield chunk #{count}")
                yield item
            await stream_task
            finished = True
            print(f"[TTS-client] [{sid}] utterance complete ({count} chunks)")

            sample_rate = await self._handle.get_sample_rate.remote()
            yield Audio(
                data=np.array([], dtype=np.float32), sample_rate=sample_rate, end=True
            )
        finally:
            if opened:
                if stream_task is not None:
                    stream_task.cancel()  # interrupted: stop draining
                # No await before this reset: a second cancel must not skip it.
                self._session_id = None
                self._audio_q = None
                self._stream_task = None
                if not finished:
                    # shield: a second cancel must not drop the replica cleanup
                    await asyncio.shield(self._handle.end_session.remote(sid))

    async def _drain_audio(self, session_id: str, audio_q: asyncio.Queue) -> None:
        try:
            # Any: the Ray response type differs with/without ray installed
            # (CI lints without it), so a `type: ignore` would flip between
            # needed and unused.
            response: Any = self._handle.options(stream=True).stream_audio.remote(
                session_id
            )
            count = 0
            pts = 0.0
            async for audio in response:
                count += 1
                audio.pts = pts
                pts += audio.data.shape[0] / audio.sample_rate
                print(
                    f"[TTS-client] [{session_id}] drain received chunk #{count} "
                    f"pts={audio.pts:.3f}s next={pts:.3f}s",
                )
                await audio_q.put(audio)
        except asyncio.CancelledError:
            # Ray Serve does not pass a caller's cancel on to the replica.
            response.cancel()
            raise
        except Exception as e:
            print(f"[TTS-client] [{session_id}] drain task FAILED: {e!r}")
            raise
        finally:
            await audio_q.put(_END_AUDIO)
            print(f"[TTS-client] [{session_id}] drain task finished")
