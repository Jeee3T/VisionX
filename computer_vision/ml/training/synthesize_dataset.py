"""Generate a synthetic gesture dataset so the pipeline can run without a camera.

    python -m computer_vision.ml.training.synthesize_dataset --recordings 6 --frames 60

The output is written as an ordinary dataset under a *separate subject*
(`synthetic:v1`), so it can never be silently mixed into a real user's training
data: training selects a subject explicitly.

Read `computer_vision/ml/synthetic.py` before trusting anything measured on it.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from computer_vision.ml import paths
from computer_vision.ml.dataset import (
    GESTURE_CLASSES,
    GestureSample,
    QualityConfig,
    assess_frame,
    dataset_files,
    hand_box_area,
    new_recording_id,
    write_manifest,
    write_recording,
)
from computer_vision.ml.synthetic import (
    SYNTHETIC_SUBJECT,
    generate_recording,
    verify_against_geometric_recognizer,
)


def build(recordings: int, frames: int, seed: int, root: Path | None, version: str) -> dict:
    rng = random.Random(seed)
    aspect = 4 / 3
    quality = QualityConfig()
    written = 0
    rejected = 0

    for label in GESTURE_CLASSES:
        for _ in range(recordings):
            recording_id = new_recording_id("syn")
            samples: list[GestureSample] = []
            previous = None
            for landmarks in generate_recording(label, frames=frames, rng=rng, aspect=aspect):
                ok, _reason, features = assess_frame(
                    landmarks, detection_score=rng.uniform(0.82, 0.99),
                    brightness=rng.uniform(60.0, 190.0), previous_features=previous,
                    aspect=aspect, config=quality,
                )
                if not ok:
                    rejected += 1
                    continue
                previous = features
                samples.append(GestureSample(
                    label=label,
                    features=features.tolist(),
                    recording_id=recording_id,
                    subject_id=SYNTHETIC_SUBJECT,
                    landmarks=landmarks.tolist(),
                    aspect=aspect,
                    detection_score=0.9,
                    brightness=120.0,
                    hand_box_area=hand_box_area(landmarks),
                    handedness="Right",
                ))
            if samples:
                write_recording(samples, root=root, version=version)
                written += len(samples)

    return {"samplesWritten": written, "framesRejected": rejected}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recordings", type=int, default=6, help="recordings per class (default 6)")
    parser.add_argument("--frames", type=int, default=60, help="frames per recording (default 60)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dataset-version", default=paths.DATASET_VERSION)
    parser.add_argument("--root", type=Path, default=None, help="dataset root (default data/gesture)")
    parser.add_argument("--force", action="store_true", help="write even if this subject already has data")
    parser.add_argument("--verify", action="store_true",
                        help="also report agreement with the shipped geometric recognizer")
    args = parser.parse_args(argv)

    existing = dataset_files(args.root, args.dataset_version, SYNTHETIC_SUBJECT)
    if existing and not args.force:
        print(
            f"{len(existing)} synthetic recording(s) already exist under "
            f"{(args.root or paths.gesture_data_root()) / args.dataset_version}. "
            "Datasets are versioned and never overwritten - pass --force to add more, or "
            "use --dataset-version to start a new version.",
            file=sys.stderr,
        )
        return 1

    stats = build(args.recordings, args.frames, args.seed, args.root, args.dataset_version)
    from computer_vision.ml.dataset import load_dataset

    samples = load_dataset(args.root, args.dataset_version, SYNTHETIC_SUBJECT)
    manifest = write_manifest(
        load_dataset(args.root, args.dataset_version), root=args.root, version=args.dataset_version
    )

    print(json.dumps({
        "subject": SYNTHETIC_SUBJECT,
        "datasetVersion": args.dataset_version,
        "recordingsPerClass": args.recordings,
        "framesPerRecording": args.frames,
        "samplesForSubject": len(samples),
        "manifest": str(manifest),
        **stats,
    }, indent=2))

    if args.verify:
        print("\nAgreement between the generator and the shipped geometric recognizer:")
        print(json.dumps(verify_against_geometric_recognizer(seed=args.seed), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
