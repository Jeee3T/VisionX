"""Continuous listening: wake word in, command out.

The trained pipeline is untouched. This package sits *around* it, deciding which
words are a command at all, and hands whatever it captures to the existing
Whisper -> TF-IDF -> logistic-regression path exactly as push-to-talk always did.
"""

from voice_assistant.wake.wake_word import (
    ACTION_ARMED,
    ACTION_CAPTURING,
    ACTION_EXECUTE,
    ACTION_IDLE,
    ACTION_TIMEOUT,
    DEFAULT_TERMINATORS,
    DEFAULT_WAKE_WORDS,
    STATE_CAPTURING,
    STATE_LISTENING,
    WakeWordSession,
    WakeWordResult,
    strip_terminator,
    strip_wake_word,
)

__all__ = [
    "ACTION_ARMED",
    "ACTION_CAPTURING",
    "ACTION_EXECUTE",
    "ACTION_IDLE",
    "ACTION_TIMEOUT",
    "DEFAULT_TERMINATORS",
    "DEFAULT_WAKE_WORDS",
    "STATE_CAPTURING",
    "STATE_LISTENING",
    "WakeWordResult",
    "WakeWordSession",
    "strip_terminator",
    "strip_wake_word",
]
