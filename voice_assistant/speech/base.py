"""The speech-to-text seam.

VisionX does not train a speech recogniser and never will: mature pretrained
models exist and reimplementing one would be worse in every dimension. What the
project owns is the interface, so the backend can be swapped without touching
the voice pipeline above it.

    SpeechRecognizer (abstract)
      +- FasterWhisperRecognizer   local, CTranslate2, no PyTorch
      +- OpenAIWhisperRecognizer   local, reference implementation
      +- NullSpeechRecognizer      none installed - fails with instructions

The output is text. Everything downstream - intent classification, parameter
extraction, dispatch - works on text and does not know or care what produced it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class SpeechRecognizerError(RuntimeError):
    """Transcription failed."""


class SpeechUnavailableError(SpeechRecognizerError):
    """No speech-to-text backend is installed or loadable."""


@dataclass
class Transcription:
    text: str
    backend: str
    model: str = ""
    language: str = ""
    duration_seconds: float = 0.0
    processing_seconds: float = 0.0
    # Whisper reports an average log-probability per segment, not a calibrated
    # confidence. It is surfaced for display and thresholding, clearly named.
    average_logprob: float | None = None
    no_speech_probability: float | None = None
    segments: list[dict] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "backend": self.backend,
            "model": self.model,
            "language": self.language,
            "durationSeconds": round(self.duration_seconds, 3),
            "processingSeconds": round(self.processing_seconds, 3),
            "averageLogprob": self.average_logprob,
            "noSpeechProbability": self.no_speech_probability,
        }


class SpeechRecognizer(ABC):
    name = "abstract"

    @abstractmethod
    def transcribe(self, audio: bytes, filename: str = "utterance.webm") -> Transcription:
        """Transcribe one short utterance. Raises SpeechRecognizerError on failure."""

    @property
    def available(self) -> bool:
        return True

    def warm_up(self) -> None:
        """Optional: load weights ahead of the first utterance."""

    def describe(self) -> dict:
        return {"backend": self.name, "available": self.available}


class NullSpeechRecognizer(SpeechRecognizer):
    """Stands in when nothing is installed, so the failure is explicit and readable."""

    name = "none"

    def __init__(self, reason: str = ""):
        self.reason = reason or (
            "No speech-to-text backend is installed. Install one with "
            "`pip install -r backend/requirements-voice.txt` (faster-whisper), then restart "
            "the VisionX API. Gesture control is unaffected."
        )

    @property
    def available(self) -> bool:
        return False

    def transcribe(self, audio: bytes, filename: str = "utterance.webm") -> Transcription:
        raise SpeechUnavailableError(self.reason)

    def describe(self) -> dict:
        return {"backend": self.name, "available": False, "reason": self.reason}
