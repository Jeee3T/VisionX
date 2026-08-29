"""Train a personalized gesture classifier from collected landmark recordings.

    python -m computer_vision.ml.training.train_gesture_model --subject synthetic:v1
    python -m computer_vision.ml.training.train_gesture_model --user 66f0ab... 

Pipeline
--------
1. load every recording for one subject at one dataset version
2. validate labels against the repository's real pose library
3. split BY RECORDING into train/validation/test (never by frame - see dataset.py)
4. standardise features, then fit an MLP; the validation split selects the
   regularisation strength, the test split is touched exactly once at the end
5. report accuracy, macro/weighted F1, per-class metrics, confusion matrix and
   the false command rate
6. save portable weights + metadata, and export ONNX

Deterministic for a fixed --seed, dataset and scikit-learn version.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from computer_vision.ml import paths, registry
from computer_vision.ml.canonicalization import FEATURE_VERSION, describe as describe_features
from computer_vision.ml.dataset import (
    GESTURE_CLASSES,
    NULL_CLASS,
    DatasetError,
    assert_no_leakage,
    class_counts,
    load_dataset,
    recording_counts,
    split_by_recording,
    to_matrix,
)
from computer_vision.ml.mlp import export_onnx, from_sklearn, ModelLoadError
from multimodal.reporting import classification_summary, format_report

ALPHA_GRID = (1e-5, 1e-4, 1e-3, 1e-2)
MIN_RECORDINGS_PER_CLASS = 2


def resolve_output(args) -> Path:
    if args.output:
        return Path(args.output)
    if args.user:
        return registry.model_dir(args.user)
    subject = args.subject or ""
    if subject.startswith("user:"):
        return registry.model_dir(subject.split(":", 1)[1])
    return paths.user_model_root() / paths.safe_component(subject or "default")


def validate(samples, subject: str) -> dict:
    """Reject a dataset that cannot produce an honest model, and say why."""
    counts = class_counts(samples)
    recordings = recording_counts(samples)
    present = [name for name, count in counts.items() if count > 0]
    missing = [name for name in GESTURE_CLASSES if counts[name] == 0]

    if len(present) < 3:
        raise DatasetError(
            f"Subject '{subject}' has samples for only {len(present)} class(es): {present}. "
            "Collect at least three, including the OTHER/NULL class."
        )
    if counts[NULL_CLASS] == 0:
        raise DatasetError(
            f"No '{NULL_CLASS}' (other / no command) samples. Without them the model has no way "
            "to reject natural hand movement and will fire commands during ordinary gesturing."
        )
    thin = {name: recordings[name] for name in present if recordings[name] < MIN_RECORDINGS_PER_CLASS}
    return {"present": present, "missing": missing, "thinClasses": thin,
            "samplesPerClass": counts, "recordingsPerClass": recordings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--subject", help="dataset subject id, e.g. 'synthetic:v1' or 'user:<id>'")
    source.add_argument("--user", help="user id; shorthand for --subject user:<id> writing to that user's model dir")
    parser.add_argument("--dataset-version", default=paths.DATASET_VERSION)
    parser.add_argument("--root", type=Path, default=None, help="dataset root (default data/gesture)")
    parser.add_argument("--output", type=Path, default=None, help="model output directory")
    parser.add_argument("--hidden", type=int, nargs="+", default=[64, 32], help="hidden layer sizes")
    parser.add_argument("--max-iter", type=int, default=600)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--no-onnx", action="store_true", help="skip the ONNX export")
    parser.add_argument("--report", type=Path, default=None, help="also write the metrics as JSON here")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    subject = args.subject or (f"user:{args.user}" if args.user else "synthetic:v1")
    started = time.time()

    try:
        samples = load_dataset(args.root, args.dataset_version, subject)
        report = validate(samples, subject)
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    train, val, test = split_by_recording(
        samples,
        ratios=(1.0 - args.val_ratio - args.test_ratio, args.val_ratio, args.test_ratio),
        seed=args.seed,
    )
    assert_no_leakage(train, val, test)

    x_train, y_train, _ = to_matrix(train)
    x_val, y_val, _ = to_matrix(val)
    x_test, y_test, _ = to_matrix(test)

    if not args.quiet:
        print(f"subject            {subject}")
        print(f"dataset version    {args.dataset_version}")
        print(f"feature version    {FEATURE_VERSION} ({x_train.shape[1]} dims)")
        print(f"recordings         {len({s.recording_id for s in samples})}")
        print(f"split (frames)     train={len(train)}  val={len(val)}  test={len(test)}")
        print(f"split (recordings) train={len({s.recording_id for s in train})}  "
              f"val={len({s.recording_id for s in val})}  test={len({s.recording_id for s in test})}")
        if report["missing"]:
            print(f"warning: no samples for {report['missing']} - the model cannot predict them")
        if report["thinClasses"]:
            print(f"warning: fewer than {MIN_RECORDINGS_PER_CLASS} recordings for {report['thinClasses']}")

    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler().fit(x_train)
    best = None
    search: list[dict] = []

    for alpha in ALPHA_GRID:
        classifier = MLPClassifier(
            hidden_layer_sizes=tuple(args.hidden),
            activation="relu",
            solver="adam",
            alpha=alpha,
            batch_size=min(128, max(8, len(x_train))),
            learning_rate_init=1e-3,
            max_iter=args.max_iter,
            shuffle=True,
            random_state=args.seed,
            early_stopping=False,
            n_iter_no_change=25,
            tol=1e-5,
        )
        import warnings
        from sklearn.exceptions import ConvergenceWarning

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            classifier.fit(scaler.transform(x_train), y_train)

        if len(x_val):
            score = float((classifier.predict(scaler.transform(x_val)) == y_val).mean())
        else:
            score = float((classifier.predict(scaler.transform(x_train)) == y_train).mean())
        search.append({"alpha": alpha, "validationAccuracy": round(score, 5), "iterations": int(classifier.n_iter_)})
        if best is None or score > best[0]:
            best = (score, alpha, classifier)

    validation_accuracy, alpha, classifier = best
    if not args.quiet:
        print(f"\nalpha search       {[(row['alpha'], row['validationAccuracy']) for row in search]}")
        print(f"selected alpha     {alpha} (validation accuracy {validation_accuracy:.4f})\n")

    # `classifier.classes_` holds only the labels present in training data.
    present_classes = [int(index) for index in classifier.classes_]

    def expand(probabilities: np.ndarray) -> np.ndarray:
        """Widen predictions back to the full class list so indexes stay stable."""
        wide = np.zeros((probabilities.shape[0], len(GESTURE_CLASSES)), dtype=np.float32)
        for column, index in enumerate(present_classes):
            wide[:, index] = probabilities[:, column]
        return wide

    summaries: dict[str, dict] = {}
    for name, features, labels in (("validation", x_val, y_val), ("test", x_test, y_test)):
        if not len(features):
            summaries[name] = {"support": 0, "note": "split is empty"}
            continue
        probabilities = expand(classifier.predict_proba(scaler.transform(features)))
        predictions = probabilities.argmax(axis=1)
        summaries[name] = classification_summary(
            labels, predictions, list(GESTURE_CLASSES),
            probabilities=probabilities, null_class=NULL_CLASS,
        )

    if not args.quiet:
        for name in ("validation", "test"):
            print(format_report(summaries[name], f"{name.capitalize()} split"))
            print()

    # --- artifact ------------------------------------------------------------
    model_version = f"gm_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{abs(hash(subject)) % 10000:04d}"
    metadata = {
        "modelVersion": model_version,
        "modelType": "mlp-softmax",
        "featureVersion": FEATURE_VERSION,
        "featureDimension": int(x_train.shape[1]),
        "featureSpec": describe_features(),
        "classes": list(GESTURE_CLASSES),
        "nullClass": NULL_CLASS,
        "subject": subject,
        "datasetVersion": args.dataset_version,
        "synthetic": subject.startswith("synthetic"),
        "trainedAt": datetime.now(timezone.utc).isoformat(),
        "trainingSeconds": round(time.time() - started, 2),
        "seed": args.seed,
        "architecture": {
            "hidden": list(args.hidden),
            "activation": "relu",
            "output": "softmax",
            "alpha": alpha,
            "maxIter": args.max_iter,
            "iterations": int(classifier.n_iter_),
        },
        "trainingSamples": {
            "train": len(train), "validation": len(val), "test": len(test), "total": len(samples),
        },
        "trainingRecordings": {
            "train": len({s.recording_id for s in train}),
            "validation": len({s.recording_id for s in val}),
            "test": len({s.recording_id for s in test}),
        },
        "samplesPerClass": report["samplesPerClass"],
        "recordingsPerClass": report["recordingsPerClass"],
        "missingClasses": report["missing"],
        "alphaSearch": search,
        "metrics": summaries,
        "toolVersions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit-learn": __import__("sklearn").__version__,
        },
    }

    artifact = from_sklearn(classifier, scaler, list(GESTURE_CLASSES), metadata)
    artifact.weights[-1] = _widen_output(artifact.weights[-1], present_classes, len(GESTURE_CLASSES))
    artifact.biases[-1] = _widen_bias(artifact.biases[-1], present_classes, len(GESTURE_CLASSES))

    reference = expand(classifier.predict_proba(scaler.transform(x_train[:32]))) if len(x_train) else None
    if reference is not None:
        drift = float(np.abs(artifact.predict_proba(x_train[:32]) - reference).max())
        if drift > 1e-4:
            print(f"error: the exported artifact drifts from scikit-learn by {drift:.2e}", file=sys.stderr)
            return 3
        metadata["sklearnAgreement"] = {"maxProbabilityDrift": drift}

    output = resolve_output(args)
    artifact.save(output)
    onnx_path = None
    if not args.no_onnx:
        try:
            onnx_path = str(export_onnx(artifact, output))
        except ModelLoadError as exc:
            print(f"warning: ONNX export skipped - {exc}", file=sys.stderr)
    registry.invalidate()

    print(json.dumps({
        "modelVersion": model_version,
        "output": str(output),
        "onnx": onnx_path,
        "runtime": artifact.runtime,
        "parameters": artifact.parameter_count,
        "validationAccuracy": summaries["validation"].get("accuracy"),
        "testAccuracy": summaries["test"].get("accuracy"),
        "testMacroF1": summaries["test"].get("macroF1"),
        "synthetic": metadata["synthetic"],
    }, indent=2))

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return 0


def _widen_output(weight: np.ndarray, present: list[int], total: int) -> np.ndarray:
    """Map an output layer trained on a class subset back onto the full class list.

    A class with no training samples gets a large negative bias rather than a zero
    row, so it receives ~0 probability instead of an arbitrary share of the mass.
    """
    if weight.shape[1] == total:
        return weight
    wide = np.zeros((weight.shape[0], total), dtype=np.float32)
    for column, index in enumerate(present):
        wide[:, index] = weight[:, column]
    return wide


def _widen_bias(bias: np.ndarray, present: list[int], total: int) -> np.ndarray:
    if bias.shape[0] == total:
        return bias
    wide = np.full((total,), -30.0, dtype=np.float32)
    for column, index in enumerate(present):
        wide[index] = bias[column]
    return wide


if __name__ == "__main__":
    raise SystemExit(main())
