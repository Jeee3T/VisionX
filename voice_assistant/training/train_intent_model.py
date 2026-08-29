"""Train the VisionX voice intent classifier.

    python -m voice_assistant.training.train_intent_model

Word (1-2 gram) plus character (3-5 gram, word-bounded) TF-IDF into a multinomial
logistic regression. Small, fast, and well suited to a few hundred short,
domain-specific utterances; character n-grams also absorb the small transcription
differences Whisper produces.

The validation split selects the regularisation strength. The test split is used
exactly once, and reports:

  * accuracy, macro F1, per-intent precision/recall, confusion matrix
  * NO_COMMAND false-positive rate  - ordinary speech read as a command
  * COMMAND-LEVEL accuracy          - the resolved (command, parameters) pair,
                                      which is what actually reaches PowerPoint
  * a reliability table             - are the probabilities worth thresholding on

Deterministic for a fixed --seed, dataset and scikit-learn version.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from computer_vision.ml import paths
from multimodal.reporting import classification_summary, format_report
from voice_assistant.intent.classifier import IntentModel, default_model_dir
from voice_assistant.intent.dataset import VoiceDatasetError, load, validate
from voice_assistant.intent.intents import INTENT_CLASSES, INTENT_INDEX, NO_COMMAND, command_for
from voice_assistant.intent.normalize import FEATURE_VERSION, normalize
from voice_assistant.intent.parameters import extract

C_GRID = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)


def build_pipeline(C: float, seed: int):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    features = FeatureUnion([
        ("word", TfidfVectorizer(
            preprocessor=normalize, analyzer="word", ngram_range=(1, 2),
            sublinear_tf=True, min_df=1,
        )),
        ("char", TfidfVectorizer(
            preprocessor=normalize, analyzer="char_wb", ngram_range=(3, 5),
            sublinear_tf=True, min_df=2,
        )),
    ])
    return Pipeline([
        ("features", features),
        ("classifier", LogisticRegression(
            C=C, max_iter=2000, solver="lbfgs", random_state=seed,
        )),
    ])


def reliability(probabilities: np.ndarray, correct: np.ndarray, bins: int = 5) -> list[dict]:
    """Is a probability of 0.8 right about 80% of the time? Binned answer."""
    confidence = probabilities.max(axis=1)
    edges = np.linspace(0.0, 1.0, bins + 1)
    table = []
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (confidence >= low) & (confidence < high if high < 1.0 else confidence <= 1.0)
        if not mask.any():
            continue
        table.append({
            "range": f"{low:.1f}-{high:.1f}",
            "count": int(mask.sum()),
            "meanConfidence": round(float(confidence[mask].mean()), 4),
            "accuracy": round(float(correct[mask].mean()), 4),
        })
    return table


def command_level(texts: list[str], expected_intents: list[str], expected_parameters: list[dict],
                  predicted: list[str]) -> dict:
    """Accuracy of the (command, parameters) pair that actually reaches dispatch."""
    exact = 0
    intent_only = 0
    failures: list[dict] = []

    for text, want_intent, want_parameters, got_intent in zip(
        texts, expected_intents, expected_parameters, predicted
    ):
        if got_intent != want_intent:
            failures.append({"text": text, "expected": want_intent, "predicted": got_intent})
            continue
        intent_only += 1
        if got_intent == NO_COMMAND:
            exact += 1
            continue
        _command, fixed = command_for(got_intent)
        extraction = extract(got_intent, text)
        resolved = {**fixed, **extraction.parameters}
        want = {**command_for(want_intent)[1], **want_parameters}
        if resolved == want:
            exact += 1
        else:
            failures.append({
                "text": text, "expected": want_intent,
                "expectedParameters": want, "resolvedParameters": resolved,
            })

    total = max(1, len(texts))
    return {
        "commandAccuracy": round(exact / total, 4),
        "intentAccuracy": round(intent_only / total, 4),
        "failures": failures[:25],
        "failureCount": len(failures),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset-version", default=paths.DATASET_VERSION)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None, help="model directory")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        rows = load(args.dataset_version, args.root)
        stats = validate(rows)
    except VoiceDatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from sklearn.model_selection import train_test_split

    texts = [row.text for row in rows]
    labels = np.asarray([INTENT_INDEX[row.intent] for row in rows], dtype=np.int64)
    parameters = [row.parameters for row in rows]

    idx = np.arange(len(rows))
    train_idx, holdout_idx = train_test_split(
        idx, test_size=args.val_ratio + args.test_ratio,
        random_state=args.seed, stratify=labels,
    )
    relative_test = args.test_ratio / (args.val_ratio + args.test_ratio)
    val_idx, test_idx = train_test_split(
        holdout_idx, test_size=relative_test,
        random_state=args.seed, stratify=labels[holdout_idx],
    )

    def subset(indexes):
        return ([texts[i] for i in indexes], labels[indexes], [parameters[i] for i in indexes])

    x_train, y_train, _ = subset(train_idx)
    x_val, y_val, _ = subset(val_idx)
    x_test, y_test, p_test = subset(test_idx)

    if not args.quiet:
        print(f"dataset version   {args.dataset_version}")
        print(f"feature version   {FEATURE_VERSION}")
        print(f"utterances        {stats['total']}  ({len(INTENT_CLASSES)} intents)")
        print(f"split             train={len(x_train)}  val={len(x_val)}  test={len(x_test)}")
        if stats["thinIntents"]:
            print(f"warning: thin intents {stats['thinIntents']}")

    search = []
    best = None
    for C in C_GRID:
        pipeline = build_pipeline(C, args.seed)
        pipeline.fit(x_train, y_train)
        predictions = pipeline.predict(x_val)
        from sklearn.metrics import f1_score

        score = float(f1_score(y_val, predictions, average="macro", zero_division=0))
        search.append({"C": C, "validationMacroF1": round(score, 5)})
        if best is None or score > best[0]:
            best = (score, C, pipeline)

    validation_f1, C, pipeline = best
    if not args.quiet:
        print(f"\nC search          {[(row['C'], row['validationMacroF1']) for row in search]}")
        print(f"selected C        {C} (validation macro F1 {validation_f1:.4f})\n")

    present = [INTENT_CLASSES[int(i)] for i in pipeline.named_steps["classifier"].classes_]

    def widen(raw: np.ndarray) -> np.ndarray:
        wide = np.zeros((raw.shape[0], len(INTENT_CLASSES)), dtype=np.float64)
        for column, name in enumerate(present):
            wide[:, INTENT_CLASSES.index(name)] = raw[:, column]
        return wide

    summaries: dict[str, dict] = {}
    for name, features, gold in (("validation", x_val, y_val), ("test", x_test, y_test)):
        probabilities = widen(pipeline.predict_proba(features))
        predictions = probabilities.argmax(axis=1)
        summaries[name] = classification_summary(
            gold, predictions, list(INTENT_CLASSES),
            probabilities=probabilities, null_class=NO_COMMAND,
        )

    test_probabilities = widen(pipeline.predict_proba(x_test))
    test_predictions = test_probabilities.argmax(axis=1)
    correct = (test_predictions == y_test)

    summaries["test"]["reliability"] = reliability(test_probabilities, correct)
    summaries["test"]["commandLevel"] = command_level(
        x_test, [INTENT_CLASSES[i] for i in y_test], p_test,
        [INTENT_CLASSES[i] for i in test_predictions],
    )

    if not args.quiet:
        for name in ("validation", "test"):
            print(format_report(summaries[name], f"{name.capitalize()} split"))
            print()
        print("reliability (is a probability of p right about p of the time?)")
        print(f"{'range':<12}{'count':>8}{'mean p':>10}{'accuracy':>11}")
        for row in summaries["test"]["reliability"]:
            print(f"{row['range']:<12}{row['count']:>8}{row['meanConfidence']:>10.3f}{row['accuracy']:>11.3f}")
        command = summaries["test"]["commandLevel"]
        print(f"\ncommand-level accuracy (intent AND parameters)  {command['commandAccuracy']:.4f}")
        print(f"intent-only accuracy                           {command['intentAccuracy']:.4f}")
        for failure in command["failures"]:
            print(f"  miss: {failure}")
        print()

    model_version = f"vi_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    metadata = {
        "modelVersion": model_version,
        "modelType": "tfidf-word+char / logistic-regression",
        "featureVersion": FEATURE_VERSION,
        "intents": list(INTENT_CLASSES),
        "nullClass": NO_COMMAND,
        "datasetVersion": args.dataset_version,
        "trainedAt": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "hyperparameters": {"C": C, "solver": "lbfgs", "maxIter": 2000},
        "cSearch": search,
        "trainingSamples": {
            "train": len(x_train), "validation": len(x_val),
            "test": len(x_test), "total": stats["total"],
        },
        "utterancesPerIntent": stats["perIntent"],
        "metrics": summaries,
        "toolVersions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scikit-learn": __import__("sklearn").__version__,
        },
    }

    output = args.output or default_model_dir()
    model = IntentModel(pipeline, present, metadata)
    path = model.save(output)

    print(json.dumps({
        "modelVersion": model_version,
        "output": str(output),
        "model": str(path),
        "sizeBytes": path.stat().st_size,
        "validationMacroF1": summaries["validation"].get("macroF1"),
        "testAccuracy": summaries["test"].get("accuracy"),
        "testMacroF1": summaries["test"].get("macroF1"),
        "commandAccuracy": summaries["test"]["commandLevel"]["commandAccuracy"],
        "noCommandFalsePositiveRate": summaries["test"]["falseCommandRate"]["fromNull"],
    }, indent=2))

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
