"""Loading and running the VisionX-trained intent classifier.

The model is a scikit-learn pipeline (word + character TF-IDF -> multinomial
logistic regression) serialised with joblib. Unlike the gesture model this one
does not run in a real-time loop - it runs once per spoken utterance - so a
scikit-learn dependency at inference time is the right trade for keeping the
training and serving code identical.

Security note: joblib uses pickle, so loading a model executes code from the
file. The model here is produced locally by the project's own training command
and stored under `voice_assistant/models/`. The recorded SHA-256 catches
corruption and accidental replacement; it is not a defence against someone who
can already write to that directory.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from computer_vision.ml import paths
from voice_assistant.intent.intents import INTENT_CLASSES, NO_COMMAND
from voice_assistant.intent.normalize import FEATURE_VERSION

logger = logging.getLogger(__name__)

MODEL_FILE = "intent_model.joblib"
METADATA_FILE = "intent_model.metadata.json"
MODEL_SUBDIR = "intent"


class IntentModelError(RuntimeError):
    """The intent model is missing, corrupt or version-incompatible."""


@dataclass
class IntentPrediction:
    intent: str
    probability: float
    distribution: dict[str, float] = field(default_factory=dict)
    model_version: str = ""

    @property
    def is_command(self) -> bool:
        return self.intent != NO_COMMAND


def default_model_dir() -> Path:
    return paths.voice_model_root() / MODEL_SUBDIR


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


class IntentModel:
    def __init__(self, pipeline, classes: list[str], metadata: dict):
        self.pipeline = pipeline
        self.classes = classes
        self.metadata = metadata

    # --- properties ----------------------------------------------------------
    @property
    def model_version(self) -> str:
        return str(self.metadata.get("modelVersion") or "unknown")

    @property
    def feature_version(self) -> str:
        return str(self.metadata.get("featureVersion") or FEATURE_VERSION)

    # --- inference -----------------------------------------------------------
    def predict_proba(self, texts: list[str]) -> np.ndarray:
        """Probabilities widened to the full INTENT_CLASSES order."""
        raw = self.pipeline.predict_proba(list(texts))
        wide = np.zeros((len(texts), len(INTENT_CLASSES)), dtype=np.float64)
        for column, name in enumerate(self.classes):
            wide[:, INTENT_CLASSES.index(name)] = raw[:, column]
        return wide

    def predict(self, text: str) -> IntentPrediction:
        if not str(text or "").strip():
            return IntentPrediction(NO_COMMAND, 1.0, {NO_COMMAND: 1.0}, self.model_version)
        probabilities = self.predict_proba([text])[0]
        index = int(np.argmax(probabilities))
        return IntentPrediction(
            intent=INTENT_CLASSES[index],
            probability=float(probabilities[index]),
            distribution={
                name: round(float(value), 4)
                for name, value in zip(INTENT_CLASSES, probabilities)
                if value >= 0.005
            },
            model_version=self.model_version,
        )

    # --- persistence ---------------------------------------------------------
    def save(self, directory: Path) -> Path:
        import joblib

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / MODEL_FILE
        joblib.dump(self.pipeline, target, compress=3)

        metadata = dict(self.metadata)
        metadata["classes"] = list(self.classes)
        metadata["sha256"] = sha256(target)
        (directory / METADATA_FILE).write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        self.metadata = metadata
        return target

    @classmethod
    def load(cls, directory: Path | None = None, verify_checksum: bool = True) -> "IntentModel":
        directory = Path(directory or default_model_dir())
        model_path = directory / MODEL_FILE
        metadata_path = directory / METADATA_FILE

        if not model_path.exists():
            raise IntentModelError(
                f"No voice intent model at {model_path}. Train one with "
                "`python -m voice_assistant.training.train_intent_model`."
            )

        metadata: dict = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise IntentModelError(f"{metadata_path} is not valid JSON: {exc}") from exc

        expected = metadata.get("sha256")
        if verify_checksum and expected:
            actual = sha256(model_path)
            if actual != expected:
                raise IntentModelError(
                    f"{model_path} does not match the checksum recorded when it was trained "
                    "(the file has been modified or is corrupt). Retrain the intent model."
                )

        declared = metadata.get("featureVersion")
        if declared and declared != FEATURE_VERSION:
            raise IntentModelError(
                f"Model was trained on text feature version '{declared}' but this build "
                f"produces '{FEATURE_VERSION}'. Retrain the intent model."
            )

        try:
            import joblib

            pipeline = joblib.load(model_path)
        except Exception as exc:  # noqa: BLE001 - corrupt pickle, missing sklearn, version skew
            raise IntentModelError(f"Could not load {model_path}: {exc}") from exc

        classes = [str(name) for name in metadata.get("classes") or []]
        if not classes:
            classes = [str(name) for name in getattr(pipeline, "classes_", INTENT_CLASSES)]
        unknown = [name for name in classes if name not in INTENT_CLASSES]
        if unknown:
            raise IntentModelError(f"Model predicts unknown intents: {unknown}")

        # A stored model that cannot answer is worse than no model - prove it works.
        try:
            pipeline.predict_proba(["next slide"])
        except Exception as exc:  # noqa: BLE001
            raise IntentModelError(f"The loaded intent model cannot run inference: {exc}") from exc

        return cls(pipeline, classes, metadata)


def model_status(directory: Path | None = None) -> dict:
    """Everything the voice settings screen needs, never raising."""
    directory = Path(directory or default_model_dir())
    if not (directory / MODEL_FILE).exists():
        return {"available": False, "error": None, "modelVersion": None}
    try:
        model = IntentModel.load(directory)
    except IntentModelError as exc:
        return {"available": False, "error": str(exc), "modelVersion": None}
    return {
        "available": True,
        "error": None,
        "modelVersion": model.model_version,
        "featureVersion": model.feature_version,
        "trainedAt": model.metadata.get("trainedAt"),
        "intents": model.metadata.get("classes"),
        "metrics": model.metadata.get("metrics"),
        "datasetVersion": model.metadata.get("datasetVersion"),
        "utterances": model.metadata.get("trainingSamples"),
        "sizeBytes": (directory / MODEL_FILE).stat().st_size,
    }
