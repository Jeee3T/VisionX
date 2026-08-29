"""Local Whisper backends.

Audio never leaves the machine. That matters here more than usual: the VisionX
backend already runs on the presenter's own computer (it drives that computer's
keyboard), so "local" is the natural deployment, not a compromise.

Two implementations, preferred in this order:

  faster-whisper  CTranslate2 runtime, no PyTorch, ~5x faster on CPU
  openai-whisper  the reference implementation; needs torch and ffmpeg on PATH
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

from voice_assistant.speech.base import (
    SpeechRecognizer,
    SpeechRecognizerError,
    SpeechUnavailableError,
    Transcription,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("VISIONX_WHISPER_MODEL", "base.en")
MAX_AUDIO_BYTES = 8 * 1024 * 1024      # a short utterance; refuse a long recording
MAX_UTTERANCE_SECONDS = 20.0


def _write_temp(audio: bytes, filename: str) -> Path:
    suffix = Path(filename or "utterance.webm").suffix or ".webm"
    handle = tempfile.NamedTemporaryFile(prefix="visionx_utt_", suffix=suffix, delete=False)
    try:
        handle.write(audio)
        handle.flush()
    finally:
        handle.close()
    return Path(handle.name)


class FasterWhisperRecognizer(SpeechRecognizer):
    """Whisper via CTranslate2. The default: fast on CPU and no PyTorch."""

    name = "faster-whisper"

    def __init__(self, model_size: str = DEFAULT_MODEL, device: str = "cpu",
                 compute_type: str = "int8", language: str | None = "en"):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise SpeechUnavailableError(
                "faster-whisper is not installed. Run "
                "`pip install -r backend/requirements-voice.txt`."
            ) from exc

        self.model_size = model_size
        self.language = language
        try:
            self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        except Exception as exc:  # noqa: BLE001 - download failure, bad model name, no disk
            raise SpeechUnavailableError(
                f"Could not load the Whisper model '{model_size}': {exc}"
            ) from exc
        logger.info("Speech-to-text ready: faster-whisper '%s' on %s", model_size, device)

    def transcribe(self, audio: bytes, filename: str = "utterance.webm") -> Transcription:
        if not audio:
            raise SpeechRecognizerError("The recording was empty.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise SpeechRecognizerError(
                "That recording is too long. Voice commands are short utterances - "
                "press the button, say the command, release."
            )

        path = _write_temp(audio, filename)
        started = time.perf_counter()
        try:
            segments, info = self._model.transcribe(
                str(path),
                language=self.language,
                beam_size=1,                       # a 3-word command needs no beam search
                vad_filter=True,                   # trim silence around the utterance
                condition_on_previous_text=False,  # each utterance is independent
            )
            collected = []
            logprobs = []
            no_speech = []
            for segment in segments:
                collected.append(segment.text)
                if getattr(segment, "avg_logprob", None) is not None:
                    logprobs.append(float(segment.avg_logprob))
                if getattr(segment, "no_speech_prob", None) is not None:
                    no_speech.append(float(segment.no_speech_prob))
        except Exception as exc:  # noqa: BLE001 - decode failures, corrupt audio
            raise SpeechRecognizerError(f"Could not transcribe the recording: {exc}") from exc
        finally:
            path.unlink(missing_ok=True)

        return Transcription(
            text=" ".join(part.strip() for part in collected).strip(),
            backend=self.name,
            model=self.model_size,
            language=getattr(info, "language", self.language or ""),
            duration_seconds=float(getattr(info, "duration", 0.0) or 0.0),
            processing_seconds=time.perf_counter() - started,
            average_logprob=(sum(logprobs) / len(logprobs)) if logprobs else None,
            no_speech_probability=(max(no_speech) if no_speech else None),
        )

    def warm_up(self) -> None:
        try:
            import numpy as np

            silence = np.zeros(16000, dtype="float32")
            list(self._model.transcribe(silence, language=self.language, beam_size=1)[0])
        except Exception:  # noqa: BLE001 - warm-up is best effort
            logger.debug("Whisper warm-up failed", exc_info=True)

    def describe(self) -> dict:
        return {"backend": self.name, "available": True, "model": self.model_size,
                "language": self.language, "local": True}


class OpenAIWhisperRecognizer(SpeechRecognizer):
    """The reference `openai-whisper` package. Needs torch and ffmpeg on PATH."""

    name = "openai-whisper"

    def __init__(self, model_size: str = DEFAULT_MODEL, language: str | None = "en"):
        try:
            import whisper
        except ImportError as exc:
            raise SpeechUnavailableError("openai-whisper is not installed.") from exc

        self.model_size = model_size
        self.language = language
        try:
            self._model = whisper.load_model(model_size)
        except Exception as exc:  # noqa: BLE001
            raise SpeechUnavailableError(
                f"Could not load the Whisper model '{model_size}': {exc}"
            ) from exc
        logger.info("Speech-to-text ready: openai-whisper '%s'", model_size)

    def transcribe(self, audio: bytes, filename: str = "utterance.webm") -> Transcription:
        if not audio:
            raise SpeechRecognizerError("The recording was empty.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise SpeechRecognizerError("That recording is too long for a voice command.")

        path = _write_temp(audio, filename)
        started = time.perf_counter()
        try:
            result = self._model.transcribe(str(path), language=self.language, fp16=False)
        except Exception as exc:  # noqa: BLE001
            raise SpeechRecognizerError(
                f"Could not transcribe the recording: {exc}. openai-whisper needs ffmpeg "
                "installed and on PATH."
            ) from exc
        finally:
            path.unlink(missing_ok=True)

        segments = result.get("segments") or []
        logprobs = [s["avg_logprob"] for s in segments if "avg_logprob" in s]
        return Transcription(
            text=str(result.get("text", "")).strip(),
            backend=self.name,
            model=self.model_size,
            language=str(result.get("language") or self.language or ""),
            processing_seconds=time.perf_counter() - started,
            average_logprob=(sum(logprobs) / len(logprobs)) if logprobs else None,
        )

    def describe(self) -> dict:
        return {"backend": self.name, "available": True, "model": self.model_size,
                "language": self.language, "local": True}
