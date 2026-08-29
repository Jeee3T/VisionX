"""Pick a speech-to-text backend, or explain clearly why there is none."""

from __future__ import annotations

import logging
import threading

from voice_assistant.speech.base import (
    NullSpeechRecognizer,
    SpeechRecognizer,
    SpeechUnavailableError,
)
from voice_assistant.speech.whisper_recognizer import (
    DEFAULT_MODEL,
    FasterWhisperRecognizer,
    OpenAIWhisperRecognizer,
)

logger = logging.getLogger(__name__)

BACKENDS = {
    "faster-whisper": FasterWhisperRecognizer,
    "openai-whisper": OpenAIWhisperRecognizer,
}
PREFERENCE = ("faster-whisper", "openai-whisper")

_lock = threading.Lock()
_instance: SpeechRecognizer | None = None


def build_speech_recognizer(backend: str | None = None,
                            model_size: str = DEFAULT_MODEL) -> SpeechRecognizer:
    """Build a recognizer. Returns NullSpeechRecognizer rather than raising."""
    order = (backend,) if backend else PREFERENCE
    reasons: list[str] = []

    for name in order:
        factory = BACKENDS.get(name)
        if factory is None:
            reasons.append(f"'{name}' is not a known speech backend")
            continue
        try:
            return factory(model_size=model_size)
        except SpeechUnavailableError as exc:
            reasons.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - never let STT setup break the API
            reasons.append(f"{name}: {exc}")

    detail = "; ".join(reasons) if reasons else "no backends were tried"
    logger.warning("No speech-to-text backend available (%s)", detail)
    return NullSpeechRecognizer(
        "Speech-to-text is unavailable, so voice commands cannot be transcribed "
        f"({detail}). Install a backend with `pip install -r backend/requirements-voice.txt` "
        "and restart the API. Gesture control is unaffected."
    )


def get_speech_recognizer(backend: str | None = None,
                          model_size: str = DEFAULT_MODEL) -> SpeechRecognizer:
    """Process-wide singleton: Whisper weights are loaded once, not per request."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = build_speech_recognizer(backend, model_size)
        return _instance


def reset_speech_recognizer() -> None:
    global _instance
    with _lock:
        _instance = None


def probe() -> dict:
    """Which backends are importable - cheap enough to call from a status endpoint."""
    import importlib.util

    return {
        "faster-whisper": importlib.util.find_spec("faster_whisper") is not None,
        "openai-whisper": importlib.util.find_spec("whisper") is not None,
    }
