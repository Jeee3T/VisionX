"""Evaluate a trained voice intent model against a dataset split.

    python -m voice_assistant.training.evaluate_intent_model --split test

Re-runs the same seeded stratified split as training, so `--split test` evaluates
exactly the utterances training never saw. `--split all` includes the training
utterances and is a sanity check only, not a generalisation estimate.

Reports the same shape as the gesture model (both use multimodal/reporting.py):
accuracy, macro F1, per-intent precision/recall, confusion matrix, the
NO_COMMAND false-positive rate, and the command-level accuracy that includes
parameter extraction.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from computer_vision.ml import paths
from multimodal.reporting import classification_summary, format_report
from voice_assistant.intent.classifier import IntentModel, IntentModelError, default_model_dir
from voice_assistant.intent.dataset import VoiceDatasetError, load
from voice_assistant.intent.intents import INTENT_CLASSES, INTENT_INDEX, NO_COMMAND
from voice_assistant.training.train_intent_model import command_level, reliability


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", type=Path, default=None, help="model directory")
    parser.add_argument("--dataset-version", default=paths.DATASET_VERSION)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--split", choices=("train", "validation", "test", "all"), default="test")
    parser.add_argument("--seed", type=int, default=42, help="must match the training seed")
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        model = IntentModel.load(args.model or default_model_dir())
    except IntentModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        rows = load(args.dataset_version, args.root)
    except VoiceDatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    labels = np.asarray([INTENT_INDEX[row.intent] for row in rows], dtype=np.int64)

    if args.split == "all":
        chosen = list(range(len(rows)))
    else:
        from sklearn.model_selection import train_test_split

        index = np.arange(len(rows))
        train_index, holdout = train_test_split(
            index, test_size=args.val_ratio + args.test_ratio,
            random_state=args.seed, stratify=labels,
        )
        relative_test = args.test_ratio / (args.val_ratio + args.test_ratio)
        val_index, test_index = train_test_split(
            holdout, test_size=relative_test, random_state=args.seed, stratify=labels[holdout],
        )
        chosen = {"train": train_index, "validation": val_index, "test": test_index}[args.split]

    texts = [rows[i].text for i in chosen]
    gold = labels[list(chosen)]
    parameters = [rows[i].parameters for i in chosen]

    probabilities = model.predict_proba(texts)
    predictions = probabilities.argmax(axis=1)

    summary = classification_summary(
        gold, predictions, list(INTENT_CLASSES),
        probabilities=probabilities, null_class=NO_COMMAND,
    )
    summary["reliability"] = reliability(probabilities, predictions == gold)
    summary["commandLevel"] = command_level(
        texts, [INTENT_CLASSES[i] for i in gold], parameters,
        [INTENT_CLASSES[i] for i in predictions],
    )

    print(f"model {model.model_version} on {args.dataset_version} / {args.split} split\n")
    print(format_report(summary, f"{args.split.capitalize()} split"))

    print("\nreliability (is a probability of p right about p of the time?)")
    print(f"{'range':<12}{'count':>8}{'mean p':>10}{'accuracy':>11}")
    for row in summary["reliability"]:
        print(f"{row['range']:<12}{row['count']:>8}{row['meanConfidence']:>10.3f}"
              f"{row['accuracy']:>11.3f}")

    command = summary["commandLevel"]
    print(f"\ncommand-level accuracy (intent AND parameters)  {command['commandAccuracy']:.4f}")
    print(f"intent-only accuracy                           {command['intentAccuracy']:.4f}")
    for failure in command["failures"]:
        print(f"  miss: {failure}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "modelVersion": model.model_version,
            "datasetVersion": args.dataset_version,
            "split": args.split,
            "metrics": summary,
        }, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
