"""Parameter extraction, deliberately separate from intent classification.

The classifier decides *what* the presenter wants. This decides *how much* or
*which slide*. Keeping them apart means a classifier improvement cannot silently
break number handling, and a number-handling bug cannot be mistaken for a
misclassification - the failures are attributable.

Only parameters the CommandDispatcher already accepts are ever produced
(`COMMAND_PARAMETERS` in the gesture mapper is the shared contract).
"""

from __future__ import annotations

from dataclasses import dataclass

from voice_assistant.intent.normalize import parse_numbers, tokenize

# Words that mark the following number as a slide *reference* rather than a count.
SLIDE_CUES = {"slide", "slides", "number", "page", "no", "num"}
# Words that mark the following number as a step count.
COUNT_CUES = {"forward", "ahead", "back", "backwards", "backward", "by", "skip", "advance", "move"}


@dataclass
class Extraction:
    parameters: dict
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def extract(intent: str, text: str) -> Extraction:
    """Pull the parameters an intent needs out of the raw transcript."""
    tokens = tokenize(text)
    numbers = parse_numbers(tokens)

    if intent == "GO_TO_SLIDE":
        return _slide_number(tokens, numbers)
    if intent in ("NEXT_SLIDE", "PREVIOUS_SLIDE"):
        return _step_count(tokens, numbers)
    return Extraction({})


def _preceded_by(tokens: list[str], start: int, vocabulary: set[str], window: int = 2) -> bool:
    for offset in range(1, window + 1):
        index = start - offset
        if index < 0:
            break
        if tokens[index] in vocabulary:
            return True
    return False


def _slide_number(tokens: list[str], numbers: list[tuple[int, int, int]]) -> Extraction:
    if not numbers:
        return Extraction({}, "I did not catch a slide number.")

    # Prefer a number that follows a slide cue: in "go to slide 7 of 20" the 7 is
    # the target and the 20 is not.
    for value, start, _end in numbers:
        if _preceded_by(tokens, start, SLIDE_CUES):
            return Extraction({"slideNumber": value})

    # "the seventh slide" - an ordinal directly before the word 'slide'.
    for value, _start, end in numbers:
        if end < len(tokens) and tokens[end] in ("slide", "slides", "page"):
            return Extraction({"slideNumber": value})

    return Extraction({"slideNumber": numbers[0][0]})


def _step_count(tokens: list[str], numbers: list[tuple[int, int, int]]) -> Extraction:
    """A bare "next slide" has no count. Only an explicit number produces one."""
    for value, start, end in numbers:
        # "back to slide 3" is a slide reference the classifier already routed
        # here by mistake - refuse to turn it into a 3-slide jump.
        if _preceded_by(tokens, start, SLIDE_CUES):
            continue
        followed_by_slides = end < len(tokens) and tokens[end] in ("slide", "slides")
        if followed_by_slides or _preceded_by(tokens, start, COUNT_CUES, window=3):
            # 1 is the default, so "go back one slide" carries no parameter at all.
            # Emitting count=1 would only add noise to telemetry and the UI.
            if value <= 1:
                continue
            return Extraction({"count": value})
    return Extraction({})
