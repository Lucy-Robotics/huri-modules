"""What the integration tests (and the pipeline benchmark) fake: the model
deployments behind huri_core.testing.LocalHandle, WebRTC's detector, the
client's socket, and one spoken turn through the curated pipeline."""

import asyncio
import json
import struct

import numpy as np
from huri_events import Audio, Motion

FRAME = np.full(320, 8000, dtype=np.int16).tobytes()  # 20 ms at 16 kHz


class FakeWhisper:
    async def transcribe(self, audio, language):
        return "hello robot"


class FakeEmotionModel:
    async def predict(self, audio):
        return {"label": "hap", "confidence": 0.9, "scores": {"hap": 0.9}}


class FakeLLM:
    def __init__(self):
        self.questions = []

    async def stream(self, query):
        self.questions.append(query.question)
        for delta in ("You said: ", query.question):
            yield delta

    async def save_conversation(self, user_id, history):
        pass


class FakeVoice:
    """The TTS deployment surface: 10 samples per character."""

    def __init__(self):
        self.sessions = {}

    async def get_sample_rate(self):
        return 16000

    async def start_session(self, sid):
        self.sessions[sid] = ([], asyncio.Event())

    async def push_text(self, sid, text, end):
        texts, done = self.sessions[sid]
        texts.append(text)
        if end:
            done.set()

    async def stream_audio(self, sid):
        texts, done = self.sessions[sid]
        await done.wait()
        for text in texts:
            if text:
                yield Audio(np.zeros(len(text) * 10, dtype=np.float32), 16000)


class FakeGestures:
    async def infer(self, window, sr):
        frames = max(1, int(len(window) / sr * 30))
        return Motion(
            poses=np.zeros((frames, 165), dtype=np.float32),
            expressions=np.zeros((frames, 100), dtype=np.float32),
            trans=np.zeros((frames, 3), dtype=np.float32),
        )


class Scripted:
    """WebRTC's detector, scripted: speech, then silence."""

    def __init__(self, speech_frames):
        self.left = speech_frames

    def is_speech(self, frame, sample_rate):
        self.left -= 1
        return self.left >= 0


class FakeWS:
    """What the server's Sender writes to: the client's socket."""

    def __init__(self):
        self.frames = []

    async def send_bytes(self, payload):
        n = struct.unpack(">H", payload[:2])[0]
        self.frames.append((payload[2 : 2 + n].decode(), payload[2 + n :]))

    async def send_json(self, message):
        self.frames.append((message["topic"], json.loads(json.dumps(message["data"]))))


PIPELINE = {
    "vad": {"silence_duration": 0.06, "block_duration": 0.02},
    "stt": {"transcribe_window": 0.2, "transcribe_step": 0.1, "block_duration": 0.02},
    "tag": {},
    "emo": {"analysis_window": 0.2, "block_duration": 0.02},
    "eag": {},
    "qag": {"use_emotion": True},
    "rag": {},
    "tts": {},
    "mov": {"min_chunk_sec": 0.01},
}
SUBSCRIBED = ["huri.token", "huri.audio.out", "huri.motion"]
