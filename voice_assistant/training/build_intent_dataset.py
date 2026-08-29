"""Serialise the hand-authored utterances into the versioned dataset.

    python -m voice_assistant.training.build_intent_dataset --overwrite

The authored source of record is `voice_assistant/data/utterances.py`. This step
attaches the expected parameters to each utterance by running the real extractor,
so the dataset also pins parameter-extraction behaviour: if extraction regresses,
`evaluate_intent_model` reports it as a command-level error rather than hiding it
behind a still-correct intent label.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from computer_vision.ml import paths
from voice_assistant.data.utterances import UTTERANCES
from voice_assistant.intent.dataset import Utterance, VoiceDatasetError, validate, write
from voice_assistant.intent.intents import INTENT_CLASSES
from voice_assistant.intent.parameters import extract


def build(version: str) -> tuple[list[Utterance], list[str]]:
    rows: list[Utterance] = []
    warnings: list[str] = []

    for intent in INTENT_CLASSES:
        for text in UTTERANCES[intent]:
            extraction = extract(intent, text)
            if not extraction.ok:
                warnings.append(f"{intent}: '{text}' -> {extraction.error}")
            rows.append(Utterance(
                text=text, intent=intent,
                parameters=extraction.parameters, dataset_version=version,
            ))
    return rows, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset-version", default=paths.DATASET_VERSION)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    rows, warnings = build(args.dataset_version)
    if warnings:
        print("parameter extraction could not resolve every utterance:", file=sys.stderr)
        for line in warnings:
            print(f"  {line}", file=sys.stderr)

    try:
        stats = validate(rows)
        path = write(rows, args.dataset_version, args.root, overwrite=args.overwrite)
    except VoiceDatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "dataset": str(path),
        "datasetVersion": args.dataset_version,
        "utterances": stats["total"],
        "intents": len(INTENT_CLASSES),
        "perIntent": stats["perIntent"],
        "duplicateTexts": stats["duplicateTexts"],
        "extractionWarnings": len(warnings),
    }, indent=2))
    return 0 if not warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
