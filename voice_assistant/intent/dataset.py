"""The versioned voice-intent dataset: format, loading and validation.

    data/voice_intents/<version>/utterances.jsonl
    data/voice_intents/<version>/manifest.json

One JSON object per line:

    {
      "text":       "go to slide seven",
      "intent":     "GO_TO_SLIDE",
      "parameters": {"slideNumber": 7},
      "source":     "authored",
      "datasetVersion": "v1",
      "featureVersion": "voice-text-v1"
    }

`parameters` holds what the extractor is expected to produce for this utterance,
using the dispatcher's own key names - so the dataset doubles as the regression
suite for parameter extraction, not just for classification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from computer_vision.ml import paths
from voice_assistant.intent.intents import INTENT_CLASSES, NO_COMMAND
from voice_assistant.intent.normalize import FEATURE_VERSION, normalize

DATASET_FILE = "utterances.jsonl"
MANIFEST_FILE = "manifest.json"


class VoiceDatasetError(RuntimeError):
    """The intent dataset is missing, malformed or unusable."""


@dataclass
class Utterance:
    text: str
    intent: str
    parameters: dict
    source: str = "authored"
    dataset_version: str = paths.DATASET_VERSION
    feature_version: str = FEATURE_VERSION

    def to_json(self) -> dict:
        return {
            "text": self.text,
            "intent": self.intent,
            "parameters": self.parameters,
            "source": self.source,
            "datasetVersion": self.dataset_version,
            "featureVersion": self.feature_version,
        }

    @classmethod
    def from_json(cls, row: dict) -> "Utterance":
        return cls(
            text=str(row["text"]),
            intent=str(row["intent"]),
            parameters=dict(row.get("parameters") or {}),
            source=str(row.get("source") or "authored"),
            dataset_version=str(row.get("datasetVersion") or paths.DATASET_VERSION),
            feature_version=str(row.get("featureVersion") or FEATURE_VERSION),
        )


def dataset_path(version: str = paths.DATASET_VERSION, root: Path | None = None) -> Path:
    return (root or paths.voice_data_root()) / version / DATASET_FILE


def load(version: str = paths.DATASET_VERSION, root: Path | None = None) -> list[Utterance]:
    path = dataset_path(version, root)
    if not path.exists():
        raise VoiceDatasetError(
            f"No voice intent dataset at {path}. Build it with "
            "`python -m voice_assistant.training.build_intent_dataset`."
        )

    rows: list[Utterance] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            utterance = Utterance.from_json(json.loads(line))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise VoiceDatasetError(f"{path}:{number} is malformed: {exc}") from exc
        if utterance.intent not in INTENT_CLASSES:
            raise VoiceDatasetError(f"{path}:{number} has unknown intent '{utterance.intent}'.")
        if not utterance.text.strip():
            raise VoiceDatasetError(f"{path}:{number} has empty text.")
        rows.append(utterance)

    if not rows:
        raise VoiceDatasetError(f"{path} contains no usable utterances.")
    return rows


def validate(rows: list[Utterance], min_per_intent: int = 20) -> dict:
    """Structural quality gate. Refuses to train on a dataset that cannot work."""
    counts = {intent: 0 for intent in INTENT_CLASSES}
    for row in rows:
        counts[row.intent] += 1

    missing = [intent for intent, count in counts.items() if count == 0]
    if missing:
        raise VoiceDatasetError(f"No utterances for {missing}.")
    if counts[NO_COMMAND] < min_per_intent:
        raise VoiceDatasetError(
            f"Only {counts[NO_COMMAND]} {NO_COMMAND} utterances. Without a large, realistic "
            "negative class the model fires commands during ordinary speech."
        )
    thin = {intent: count for intent, count in counts.items() if count < min_per_intent}

    normalized = [normalize(row.text) for row in rows]
    duplicates = len(normalized) - len(set(normalized))
    collisions: dict[str, set[str]] = {}
    for text, row in zip(normalized, rows):
        collisions.setdefault(text, set()).add(row.intent)
    conflicting = {text: sorted(intents) for text, intents in collisions.items() if len(intents) > 1}
    if conflicting:
        raise VoiceDatasetError(
            f"The same utterance is labelled with more than one intent: {conflicting}"
        )

    return {
        "total": len(rows),
        "perIntent": counts,
        "thinIntents": thin,
        "duplicateTexts": duplicates,
    }


def write(rows: list[Utterance], version: str = paths.DATASET_VERSION,
          root: Path | None = None, overwrite: bool = False) -> Path:
    path = dataset_path(version, root)
    if path.exists() and not overwrite:
        raise VoiceDatasetError(
            f"{path} already exists. Datasets are versioned, not overwritten: pass "
            "--overwrite deliberately, or build a new --dataset-version."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.to_json(), ensure_ascii=False) + "\n")

    stats = validate(rows)
    (path.parent / MANIFEST_FILE).write_text(
        json.dumps({
            "datasetVersion": version,
            "featureVersion": FEATURE_VERSION,
            "intents": list(INTENT_CLASSES),
            "utterances": stats["total"],
            "perIntent": stats["perIntent"],
            "duplicateTexts": stats["duplicateTexts"],
            "authoredBy": "hand-written in voice_assistant/data/utterances.py",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
