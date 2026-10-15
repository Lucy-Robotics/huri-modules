from huri_tts.cosyvoice.handle import CosyVoiceTTSHandle
from huri_tts.streaming import StreamingTTS


class CosyVoiceTTS(StreamingTTS):
    """Text to speech with CosyVoice3: zero-shot voice cloning from a reference
    sample (HURI_VOICE_SAMPLE_PATH + HURI_VOICE_TRANSCRIPT). Needs an NVIDIA GPU
    to run in real time, and the CosyVoice sources on PYTHONPATH
    (install_cosyvoice.sh)."""

    _handle_cls = CosyVoiceTTSHandle
