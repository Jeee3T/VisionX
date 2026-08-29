"""Text normalisation shared by training and inference.

Whatever the classifier is trained on, it must see at inference time. Both paths
call `normalize()`, so a change here changes both - which is why the feature
version is recorded in the model metadata and checked on load.

Speech-to-text output is messy in specific ways this handles: no reliable
punctuation, numbers written either as digits or as words, filler at the front of
almost every utterance, and inconsistent contractions.
"""

from __future__ import annotations

import re

FEATURE_VERSION = "voice-text-v1"

_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30,
}
SCALES = {"hundred": 100}

CONTRACTIONS = {
    "let's": "lets", "don't": "dont", "can't": "cant", "won't": "wont",
    "i'm": "im", "we're": "were", "that's": "thats", "it's": "its",
    "i'd": "id", "i'll": "ill", "you'll": "youll", "we'll": "well",
    "didn't": "didnt", "doesn't": "doesnt", "isn't": "isnt", "aren't": "arent",
}


def normalize(text: str) -> str:
    """Lowercase, expand contractions, drop punctuation, collapse whitespace."""
    lowered = str(text or "").lower().strip()
    for source, target in CONTRACTIONS.items():
        lowered = lowered.replace(source, target)
    stripped = _PUNCTUATION.sub(" ", lowered)
    return _WHITESPACE.sub(" ", stripped).strip()


def tokenize(text: str) -> list[str]:
    normalized = normalize(text)
    return normalized.split() if normalized else []


def parse_numbers(tokens: list[str]) -> list[tuple[int, int, int]]:
    """Find every number in a token list.

    Returns `(value, start token index, end token index exclusive)` so a caller
    can look at the words immediately before a number and decide what it means -
    "slide seven" and "forward seven slides" contain the same number and mean
    entirely different things.
    """
    found: list[tuple[int, int, int]] = []
    index = 0

    while index < len(tokens):
        token = tokens[index]

        if token.isdigit():
            found.append((int(token), index, index + 1))
            index += 1
            continue

        if token in ORDINALS:
            found.append((ORDINALS[token], index, index + 1))
            index += 1
            continue

        if token in TENS:
            value = TENS[token]
            end = index + 1
            # "twenty three" and hyphen-free "twenty-three" both arrive as two tokens.
            if end < len(tokens) and tokens[end] in UNITS and 1 <= UNITS[tokens[end]] <= 9:
                value += UNITS[tokens[end]]
                end += 1
            elif end < len(tokens) and tokens[end] in ORDINALS and 1 <= ORDINALS[tokens[end]] <= 9:
                value += ORDINALS[tokens[end]]
                end += 1
            found.append((value, index, end))
            index = end
            continue

        if token in UNITS:
            value = UNITS[token]
            end = index + 1
            # "one hundred", "one hundred and five"
            if end < len(tokens) and tokens[end] in SCALES:
                value *= SCALES[tokens[end]]
                end += 1
                if end < len(tokens) and tokens[end] == "and":
                    end += 1
                if end < len(tokens) and tokens[end] in TENS:
                    value += TENS[tokens[end]]
                    end += 1
                    if end < len(tokens) and tokens[end] in UNITS:
                        value += UNITS[tokens[end]]
                        end += 1
                elif end < len(tokens) and tokens[end] in UNITS:
                    value += UNITS[tokens[end]]
                    end += 1
            found.append((value, index, end))
            index = end
            continue

        if token in SCALES:
            found.append((SCALES[token], index, index + 1))
            index += 1
            continue

        index += 1

    return found
