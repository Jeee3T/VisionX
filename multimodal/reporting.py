"""One evaluation report format for every VisionX-trained classifier.

Both the gesture model and the voice intent model are judged the same way, and
both surface FALSE COMMAND RATE - the operational metric that actually matters,
because wrongly changing a slide during a talk is far worse than ignoring an
input the presenter can simply repeat.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support


def classification_summary(
    y_true,
    y_pred,
    classes: list[str],
    probabilities: np.ndarray | None = None,
    null_class: str | None = None,
    thresholds: tuple[float, ...] = (0.5, 0.6, 0.7, 0.75, 0.8, 0.9),
) -> dict:
    """Accuracy, macro/weighted F1, per-class metrics, confusion matrix, FCR."""
    y_true = np.asarray(y_true, dtype=np.int64)
    y_pred = np.asarray(y_pred, dtype=np.int64)
    labels = list(range(len(classes)))

    if y_true.size == 0:
        return {"support": 0, "note": "empty split - no metrics computed"}

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )
    macro = precision_recall_fscore_support(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    weighted = precision_recall_fscore_support(y_true, y_pred, labels=labels, average="weighted", zero_division=0)

    summary = {
        "support": int(y_true.size),
        "accuracy": float((y_true == y_pred).mean()),
        "macroPrecision": float(macro[0]),
        "macroRecall": float(macro[1]),
        "macroF1": float(macro[2]),
        "weightedF1": float(weighted[2]),
        "classes": list(classes),
        "perClass": {
            classes[i]: {
                "precision": float(precision[i]),
                "recall": float(recall[i]),
                "f1": float(f1[i]),
                "support": int(support[i]),
            }
            for i in labels
        },
        "confusionMatrix": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
    }

    if null_class and null_class in classes:
        summary["falseCommandRate"] = _false_command_rate(
            y_true, y_pred, classes, null_class, probabilities, thresholds
        )
    return summary


def _false_command_rate(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    classes: list[str],
    null_class: str,
    probabilities: np.ndarray | None,
    thresholds: tuple[float, ...],
) -> dict:
    """Share of non-command inputs that would have executed a real command.

    Two numbers, because both failure paths matter:
      * `fromNull`    - a NULL/OTHER input classified as a command
      * `wrongCommand`- a command input classified as a *different* command,
                        which is worse than a miss: it fires the wrong action.
    """
    null_index = classes.index(null_class)
    null_mask = y_true == null_index
    command_mask = ~null_mask

    report: dict = {
        "nullSupport": int(null_mask.sum()),
        "commandSupport": int(command_mask.sum()),
        "fromNull": float((y_pred[null_mask] != null_index).mean()) if null_mask.any() else 0.0,
        "wrongCommand": float(
            ((y_pred[command_mask] != y_true[command_mask]) & (y_pred[command_mask] != null_index)).mean()
        ) if command_mask.any() else 0.0,
    }

    if probabilities is not None and len(probabilities):
        probabilities = np.asarray(probabilities, dtype=np.float64)
        confidence = probabilities.max(axis=1)
        gated: dict[str, dict] = {}
        for threshold in thresholds:
            fires = (y_pred != null_index) & (confidence >= threshold)
            false_fires = fires & (y_true != y_pred)
            true_fires = fires & (y_true == y_pred)
            gated[f"{threshold:.2f}"] = {
                "falseCommandRate": float(false_fires.sum() / max(1, y_true.size)),
                "commandRecall": float(
                    true_fires.sum() / max(1, int(command_mask.sum()))
                ),
                "firedShare": float(fires.mean()),
            }
        report["byConfidenceGate"] = gated
    return report


def format_report(summary: dict, title: str = "Evaluation") -> str:
    """Human-readable report for the training CLIs."""
    if not summary.get("support"):
        return f"{title}: no samples in this split."

    lines = [
        f"{title}",
        "=" * len(title),
        f"samples          {summary['support']}",
        f"accuracy         {summary['accuracy']:.4f}",
        f"macro F1         {summary['macroF1']:.4f}",
        f"weighted F1      {summary['weightedF1']:.4f}",
        f"macro precision  {summary['macroPrecision']:.4f}",
        f"macro recall     {summary['macroRecall']:.4f}",
        "",
        f"{'class':<22}{'prec':>8}{'recall':>8}{'f1':>8}{'support':>9}",
        "-" * 55,
    ]
    for name, row in summary["perClass"].items():
        lines.append(
            f"{name:<22}{row['precision']:>8.3f}{row['recall']:>8.3f}"
            f"{row['f1']:>8.3f}{row['support']:>9}"
        )

    fcr = summary.get("falseCommandRate")
    if fcr:
        lines += [
            "",
            "false command rate",
            "------------------",
            f"NULL input read as a command   {fcr['fromNull']:.4f}  (n={fcr['nullSupport']})",
            f"command read as a WRONG command{fcr['wrongCommand']:>7.4f}  (n={fcr['commandSupport']})",
        ]
        if fcr.get("byConfidenceGate"):
            lines.append(f"{'gate':<8}{'false cmd':>12}{'cmd recall':>13}{'fired':>9}")
            for gate, row in fcr["byConfidenceGate"].items():
                lines.append(
                    f"{gate:<8}{row['falseCommandRate']:>12.4f}"
                    f"{row['commandRecall']:>13.4f}{row['firedShare']:>9.3f}"
                )

    lines += ["", "confusion matrix (rows = true, cols = predicted)"]
    header = "".join(f"{name[:6]:>8}" for name in summary["classes"])
    lines.append(f"{'':<22}{header}")
    for name, row in zip(summary["classes"], summary["confusionMatrix"]):
        lines.append(f"{name:<22}" + "".join(f"{value:>8}" for value in row))
    return "\n".join(lines)
