"""The wake-word state machine: "Vision <command> OK".

The microphone listens continuously. Almost everything it hears is a talk, not a
command, so something has to decide which words are addressed to VisionX. That is
this file, and it is deliberately *pure text*: transcripts in, decisions out, no
audio, no model, no I/O. It can therefore be tested exhaustively, which matters
because it is the component standing between a presenter's ordinary speech and
their slides.

    [LISTENING]  --"vision"-->  [CAPTURING]  --"ok"-->  execute  -->  [LISTENING]

Both boundaries can arrive in one breath or across several segments, so both are
handled the same way:

    "vision go to next slide ok"      one segment  -> execute immediately
    "vision" / "go to next slide" / "ok"   three   -> execute on the third

What survives is the text between them - "go to next slide" - which is handed to
the existing trained intent model. Nothing here classifies anything; it only
decides *when* there is something to classify, so the confidence bands, the
NO_COMMAND class and the parameter extraction all keep working exactly as before.

Why a terminator at all: without one, VisionX would have to guess when the
presenter stopped talking to it, and every guess would eventually be wrong in
front of an audience. "OK" is an explicit end, so a command runs when the
presenter says it does.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from voice_assistant.intent.normalize import normalize

# --- vocabulary ---------------------------------------------------------------
# Whisper hears a short spoken word several ways, and a wake word that works two
# times in three is worse than none - so genuine mis-transcriptions are included.
# What is NOT included is any real English word, however close it sounds: a false
# wake is only cheap if what follows is not a command, and after a stray wake word
# the rest of the sentence often *is* one ("...to move on to the next slide").
# The confidence gate cannot save us there, so the vocabulary does the work.
DEFAULT_WAKE_WORDS: tuple[str, ...] = (
    "vision", "visions", "visionx", "vision x", "vishen", "vison", "wision",
)
# NOT wake words, however close they sound: "envision" and "provision" are
# ordinary words a presenter says in ordinary sentences ("we need to provision
# more servers"). Accepting them turned the sentence that followed into a
# command, and the confidence gate could not catch it because the words really
# were a command phrase.

# A wake word directly after one of these is part of a sentence, not a summons:
# "our vision going forward…", "the vision is simple…". This is the cheapest
# reliable signal that "vision" was being *used* rather than *said to* VisionX.
NON_ADDRESSING_PREFIXES: frozenset[str] = frozenset({
    "the", "a", "an", "our", "my", "your", "their", "his", "her", "its",
    "this", "that", "these", "those", "of", "for", "with", "without",
    "to", "in", "on", "about", "whose", "no", "any", "some", "every",
    "one", "another", "such", "same", "new", "old", "clear", "shared",
    "long", "term", "overall", "strategic", "company", "companys",
    "products", "product",
})

# "OK" is heard as "okay", and Whisper likes to append a full stop that
# normalisation has already removed by the time we look.
DEFAULT_TERMINATORS: tuple[str, ...] = ("ok", "okay", "o k", "okey", "okey dokey")

# --- states -------------------------------------------------------------------
STATE_LISTENING = "LISTENING"     # waiting for the wake word
STATE_CAPTURING = "CAPTURING"     # wake word heard; collecting the command

# --- actions the caller must take ---------------------------------------------
ACTION_IDLE = "IDLE"              # nothing addressed to VisionX in this segment
ACTION_ARMED = "ARMED"            # wake word heard, now listening for a command
ACTION_CAPTURING = "CAPTURING"    # more command words collected, no terminator yet
ACTION_EXECUTE = "EXECUTE"        # terminator heard - interpret and run `command`
ACTION_TIMEOUT = "TIMEOUT"        # the presenter never finished; back to listening

# A command that never ends must not capture the rest of the talk.
DEFAULT_CAPTURE_TIMEOUT = 12.0
# The longest a captured command may get before we stop believing it is one.
# Every command VisionX can run fits in six words ("go back two slides", "go to
# slide seventeen"); ten leaves room for filler. It used to be 24, which was long
# enough to swallow a whole sentence of ordinary speech after a stray wake word
# and hand the intent model something that genuinely reads as a command.
MAX_COMMAND_WORDS = 10


def _word_pattern(phrases) -> re.Pattern:
    """Match any phrase, but only on whole-word boundaries.

    Whole words matter: "envisioning the future" must not arm VisionX, and
    "provisional" must not either. The phrases are sorted longest-first so
    "vision x" wins over "vision" when both could match.
    """
    ordered = sorted({p.strip() for p in phrases if p and p.strip()}, key=len, reverse=True)
    if not ordered:
        # A pattern that can never match, rather than an empty alternation (which
        # matches everywhere).
        return re.compile(r"(?!x)x")
    joined = "|".join(re.escape(phrase) for phrase in ordered)
    return re.compile(rf"(?<!\w)(?:{joined})(?!\w)")


@dataclass
class WakeWordResult:
    """What one segment of speech did to the state machine."""

    action: str
    state: str
    command: str = ""            # the text between the wake word and the terminator
    heard: str = ""              # the normalised segment, for the UI
    matched_wake: str = ""
    matched_terminator: str = ""
    buffered: str = ""           # everything captured so far
    # Every command completed in this segment, in the order spoken. Usually empty
    # or one entry; a segment that carries "…slide five ok vision next slide ok"
    # carries two, and the caller must run both rather than only the last.
    commands: list[str] = field(default_factory=list)

    @property
    def should_execute(self) -> bool:
        return self.action == ACTION_EXECUTE and bool(self.command.strip())

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "state": self.state,
            "command": self.command,
            "heard": self.heard,
            "buffered": self.buffered,
            "matchedWake": self.matched_wake,
            "matchedTerminator": self.matched_terminator,
            "shouldExecute": self.should_execute,
            "commands": list(self.commands),
        }


@dataclass
class WakeWordSession:
    """One presenter's continuous-listening state.

    Thread-safe on its own. Flask is threaded, and a presenter with the session
    open in two tabs - or the text endpoint firing alongside the audio one - has
    two requests mutating one session at once. Interleaved `reset()`/`_append()`
    loses a buffered command or double-fires one, so `observe()` is serialised
    here rather than relying on every caller to remember.
    """

    wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS
    terminators: tuple[str, ...] = DEFAULT_TERMINATORS
    capture_timeout: float = DEFAULT_CAPTURE_TIMEOUT
    max_command_words: int = MAX_COMMAND_WORDS

    state: str = STATE_LISTENING
    _buffer: list[str] = field(default_factory=list)
    _armed_at: float = 0.0
    _last_wake: str = ""

    def __post_init__(self) -> None:
        self._wake_pattern = _word_pattern(self.wake_words)
        self._terminator_pattern = _word_pattern(self.terminators)
        self._lock = threading.RLock()

    # --- lifecycle -----------------------------------------------------------
    def reset(self) -> None:
        """Back to listening, dropping any half-captured command."""
        # Re-entrant: the internal steps call this while `observe` holds the lock.
        with self._lock:
            self.state = STATE_LISTENING
            self._buffer = []
            self._armed_at = 0.0
            self._last_wake = ""

    @property
    def buffered(self) -> str:
        return " ".join(self._buffer).strip()

    @property
    def is_capturing(self) -> bool:
        return self.state == STATE_CAPTURING

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "buffered": self.buffered,
            "wakeWords": list(self.wake_words),
            "terminators": list(self.terminators),
            "captureTimeout": self.capture_timeout,
            "armedFor": round(time.time() - self._armed_at, 2) if self._armed_at else 0.0,
        }

    # --- the state machine ---------------------------------------------------
    def observe(self, transcript: str, now: float | None = None) -> WakeWordResult:
        """Feed one transcribed segment in. Returns what to do about it.

        The segment is consumed **left to right**, one event at a time, so that
        wake words and terminators are handled in the order they were actually
        spoken. Order matters more than it looks:

            "…slide five ok"  +  "vision"     arriving as one 3-second segment

        must complete the first command and *then* re-arm. Handling the wake word
        first - which is what a single-pass implementation does - silently threw
        the finished command away.

        A segment can therefore complete more than one command; every completed
        command is reported, in order, on `.commands`.
        """
        with self._lock:
            return self._observe_locked(transcript, now)

    def _observe_locked(self, transcript: str, now: float | None) -> WakeWordResult:
        now = time.time() if now is None else now
        heard = normalize(transcript)

        # A capture that has gone on too long is abandoned rather than left open:
        # a wake word picked up by accident must not swallow the next 20 minutes.
        timed_out = False
        abandoned = ""
        if self.state == STATE_CAPTURING and self._armed_at and \
                now - self._armed_at > self.capture_timeout:
            abandoned = self.buffered
            self.reset()
            timed_out = True
            if not heard:
                return WakeWordResult(action=ACTION_TIMEOUT, state=self.state,
                                      heard="", buffered=abandoned)
            # Fall through and process this segment from LISTENING. It may well
            # contain the wake word *and* the command - abandoning it too would
            # make the presenter say the whole thing twice.

        if not heard:
            return WakeWordResult(
                action=ACTION_CAPTURING if self.is_capturing else ACTION_IDLE,
                state=self.state, heard="", buffered=self.buffered,
            )

        result = self._consume(heard, now)
        if timed_out and result.action == ACTION_IDLE:
            # Nothing in the segment was addressed to VisionX, so the timeout is
            # still the most useful thing to report.
            return WakeWordResult(action=ACTION_TIMEOUT, state=self.state,
                                  heard=heard, buffered=abandoned)
        return result

    def _consume(self, heard: str, now: float) -> WakeWordResult:
        """Walk one segment, handling wake words and terminators in order.

        The loop always makes progress: every branch either consumes text up to
        and including a match, or breaks. `remaining` is therefore strictly
        shorter each pass, because no pattern here can match the empty string.
        """
        remaining = heard
        commands: list[str] = []
        last: WakeWordResult | None = None

        while True:
            wake = self._first_wake_match(remaining)
            # A terminator only means anything while a command is being captured;
            # "ok, so as I was saying" while listening is just speech.
            terminator = self._first_terminator_match(remaining) \
                if self.state == STATE_CAPTURING else None

            # Whichever comes first in the segment is what happened first.
            if terminator is not None and (wake is None or terminator.start() < wake.start()):
                last = self._finish(remaining[: terminator.start()], terminator, heard)
                if last.action != ACTION_TIMEOUT:
                    commands.append(last.command)
                # Either way the capture is over and the machine is listening
                # again, so keep scanning: the rest of the segment may hold
                # another wake word, and dropping it would lose a command.
                remaining = remaining[terminator.end():].strip()
                if not remaining:
                    break
                continue

            if wake is not None:
                if self.state == STATE_CAPTURING:
                    # Words before the wake word are command text - the presenter
                    # kept talking, then started over.
                    overflow = self._append(remaining[: wake.start()])
                    if overflow is not None:
                        # The capture ran away. Abandon it, but do NOT abandon the
                        # segment: the wake word we just found is still ahead, and
                        # returning here lost every command after it.
                        last = overflow
                last = self._arm(wake, heard, now)
                remaining = remaining[wake.end():].strip()
                if not remaining:
                    break
                continue

            # No more events in this segment.
            if self.state == STATE_CAPTURING:
                overflow = self._append(remaining)
                if overflow is not None:
                    return self._with(overflow, commands, heard)
                last = WakeWordResult(
                    action=ACTION_CAPTURING, state=self.state, heard=heard,
                    matched_wake=self._last_wake, buffered=self.buffered,
                )
            elif last is None:
                # Ordinary speech. The overwhelmingly common case, and it must be
                # free of consequences.
                last = WakeWordResult(action=ACTION_IDLE, state=self.state, heard=heard)
            break

        return self._with(last, commands, heard)

    # --- steps ---------------------------------------------------------------
    def _arm(self, wake, heard: str, now: float) -> WakeWordResult:
        """Start (or restart) a capture at this wake word."""
        self.state = STATE_CAPTURING
        self._armed_at = now
        self._buffer = []
        self._last_wake = wake.group(0)
        return WakeWordResult(
            action=ACTION_ARMED, state=self.state, heard=heard,
            matched_wake=self._last_wake, buffered="",
        )

    def _finish(self, before: str, terminator, heard: str) -> WakeWordResult:
        """Close the capture at this terminator and emit the command.

        The length cap applies here too, not only to segments that arrive without
        a terminator: "Vision, so anyway what I wanted to say before we move on to
        the next slide, OK" completes in one segment and must not be handed to the
        intent model as "…move on to the next slide".
        """
        overflow = self._append(before.strip())
        if overflow is not None:
            return overflow
        command = self.buffered
        wake = self._last_wake
        self.reset()
        return WakeWordResult(
            action=ACTION_EXECUTE, state=self.state, command=command, heard=heard,
            matched_wake=wake, matched_terminator=terminator.group(0), buffered=command,
        )

    @staticmethod
    def _with(result: WakeWordResult, commands: list[str], heard: str) -> WakeWordResult:
        result.commands = [c for c in commands if c.strip()]
        # Always the whole segment, never the fragment an inner step happened to
        # be looking at - the UI shows this as "what I heard".
        result.heard = heard
        return result

    # --- helpers -------------------------------------------------------------
    def _append(self, text: str) -> WakeWordResult | None:
        """Add words to the capture. Returns a TIMEOUT result if it got absurd."""
        for word in text.split():
            if word:
                self._buffer.append(word)
        if len(self._buffer) <= self.max_command_words:
            return None
        # Far too long to be "next slide". Abandon rather than hand a paragraph
        # to the intent model - and a run-on capture is much more likely to be
        # ordinary speech that happened to follow the wake word.
        buffered = self.buffered
        self.reset()
        # `heard` is filled in by the caller, which knows the whole segment; the
        # fragment passed here would show up in the UI as a truncated transcript.
        return WakeWordResult(action=ACTION_TIMEOUT, state=self.state,
                              heard="", buffered=buffered)

    def _first_wake_match(self, heard: str):
        """The first *addressed* wake word in `heard`, if any.

        A wake word directly after a determiner or possessive is part of a
        sentence, not a summons: "our vision going forward", "the vision is
        simple". Skipping those is what stops a talk that is *about* vision from
        driving the deck - and the confidence gate cannot help there, because the
        words that follow really are a command phrase.
        """
        for candidate in self._wake_pattern.finditer(heard):
            preceding = heard[: candidate.start()].split()
            if preceding and preceding[-1] in NON_ADDRESSING_PREFIXES:
                continue
            return candidate
        return None

    def _first_terminator_match(self, heard: str):
        return self._terminator_pattern.search(heard)


# --- convenience --------------------------------------------------------------
def strip_wake_word(text: str, wake_words=DEFAULT_WAKE_WORDS) -> str:
    """Everything after the last wake word. Returns the text unchanged if absent."""
    heard = normalize(text)
    pattern = _word_pattern(wake_words)
    match = None
    for candidate in pattern.finditer(heard):
        match = candidate
    return heard[match.end():].strip() if match else heard


def strip_terminator(text: str, terminators=DEFAULT_TERMINATORS) -> str:
    """Everything before the first terminator. Returns the text unchanged if absent."""
    heard = normalize(text)
    match = _word_pattern(terminators).search(heard)
    return heard[: match.start()].strip() if match else heard
