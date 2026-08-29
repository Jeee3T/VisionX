"""Evaluate a trained personalized gesture model against a dataset split.

    python -m computer_vision.ml.training.evaluate_gesture_model \
        --model computer_vision/models/users/<id> --subject user:<id> --split test

Re-runs the same seeded split as training, so `--split test` evaluates exactly
the recordings training never saw. Use `--split all` only for a sanity check:
it includes the training recordings and its numbers are not a generalisation
estimate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from computer_vision.ml import paths, registry
from computer_vision.ml.dataset import (
    GESTURE_CLASSES,
    NULL_CLASS,
    DatasetError,
    assert_no_leakage,
    load_dataset,
    split_by_recording,
    to_matrix,
)
from computer_vision.ml.mlp import GestureModelArtifact, ModelLoadError
from multimodal.reporting import classification_summary, format_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", type=Path, default=None, help="model directory")
    parser.add_argument("--user", default=None, help="evaluate this user's model")
    parser.add_argument("--subject", default=None, help="dataset subject (default: the model's own)")
    parser.add_argument("--dataset-version", default=paths.DATASET_VERSION)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--split", choices=("train", "validation", "test", "all"), default="test")
    parser.add_argument("--seed", type=int, default=42, help="must match the training seed")
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--json", type=Path, default=None, help="write the metrics as JSON")
    args = parser.parse_args(argv)

    directory = args.model or (registry.model_dir(args.user) if args.user else None)
    if directory is None:
        print("error: pass --model <dir> or --user <id>", file=sys.stderr)
        return 2

    try:
        artifact = GestureModelArtifact.load(directory)
    except ModelLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    subject = args.subject or artifact.metadata.get("subject")
    if not subject:
        print("error: the model records no subject; pass --subject", file=sys.stderr)
        return 2

    try:
        samples = load_dataset(args.root, args.dataset_version, subject)
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.split == "all":
        chosen = samples
    else:
        train, validation, test = split_by_recording(
            samples,
            ratios=(1.0 - args.val_ratio - args.test_ratio, args.val_ratio, args.test_ratio),
            seed=args.seed,
        )
        assert_no_leakage(train, validation, test)
        chosen = {"train": train, "validation": validation, "test": test}[args.split]

    if not chosen:
        print(f"error: the '{args.split}' split is empty.", file=sys.stderr)
        return 2

    features, labels, _ = to_matrix(chosen)
    probabilities = artifact.predict_proba(features)
    predictions = probabilities.argmax(axis=1)

    summary = classification_summary(
        labels, predictions, list(GESTURE_CLASSES),
        probabilities=probabilities, null_class=NULL_CLASS,
    )
    header = (
        f"model {artifact.model_version} ({artifact.runtime} runtime) on "
        f"{subject} / {args.split} split"
    )
    print(header)
    if artifact.metadata.get("synthetic"):
        print("NOTE: this model was trained on SYNTHETIC data - these numbers validate the "
              "pipeline, not real-world recognition accuracy.")
    print()
    print(format_report(summary, f"{args.split.capitalize()} split"))

    payload = {
        "modelVersion": artifact.model_version,
        "runtime": artifact.runtime,
        "subject": subject,
        "split": args.split,
        "synthetic": bool(artifact.metadata.get("synthetic")),
        "recordings": len({s.recording_id for s in chosen}),
        "metrics": summary,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
