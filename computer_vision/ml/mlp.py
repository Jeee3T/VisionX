"""The personalized gesture model artifact: storage, inference and ONNX export.

Two runtimes ship for the same weights:

  * NumPy      - always available, ~40 microseconds per frame, no extra dependency
  * ONNX       - used when `onnxruntime` is installed and a .onnx file is present

Both must agree. `GestureModelArtifact.self_check()` asserts they do, and the
export CLI refuses to write an ONNX file whose outputs drift from the NumPy path.

Files written per model:
    gesture_model.npz             portable weights (this is the source of truth)
    gesture_model.onnx            optional exported graph
    gesture_model.metadata.json   version, classes, dataset, metrics
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from computer_vision.ml.canonicalization import FEATURE_VERSION, feature_dimension

logger = logging.getLogger(__name__)

WEIGHTS_FILE = "gesture_model.npz"
ONNX_FILE = "gesture_model.onnx"
METADATA_FILE = "gesture_model.metadata.json"

ARTIFACT_FORMAT = 1


class ModelLoadError(RuntimeError):
    """A model directory is missing, incomplete, corrupt or version-incompatible."""


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / np.clip(exponent.sum(axis=1, keepdims=True), 1e-12, None)


@dataclass
class GestureModelArtifact:
    """A trained per-user gesture classifier plus everything needed to trust it."""

    classes: list[str]
    weights: list[np.ndarray]
    biases: list[np.ndarray]
    mean: np.ndarray
    scale: np.ndarray
    metadata: dict = field(default_factory=dict)
    _session: object | None = field(default=None, repr=False, compare=False)

    # --- properties ----------------------------------------------------------
    @property
    def feature_version(self) -> str:
        return str(self.metadata.get("featureVersion") or FEATURE_VERSION)

    @property
    def feature_dimension(self) -> int:
        return int(self.metadata.get("featureDimension") or self.weights[0].shape[0])

    @property
    def model_version(self) -> str:
        return str(self.metadata.get("modelVersion") or "unknown")

    @property
    def parameter_count(self) -> int:
        return int(sum(w.size for w in self.weights) + sum(b.size for b in self.biases))

    # --- inference -----------------------------------------------------------
    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Class probabilities for a (N, D) batch or a single (D,) vector."""
        matrix = np.atleast_2d(np.asarray(features, dtype=np.float32))
        if matrix.shape[1] != self.feature_dimension:
            raise ValueError(
                f"Model expects {self.feature_dimension} features, received {matrix.shape[1]}."
            )
        if self._session is not None:
            return self._predict_onnx(matrix)
        return self._predict_numpy(matrix)

    def _predict_numpy(self, matrix: np.ndarray) -> np.ndarray:
        activation = (matrix - self.mean) / self.scale
        for index, (weight, bias) in enumerate(zip(self.weights, self.biases)):
            activation = activation @ weight + bias
            if index < len(self.weights) - 1:
                activation = np.maximum(activation, 0.0)
        return _softmax(activation.astype(np.float64)).astype(np.float32)

    def _predict_onnx(self, matrix: np.ndarray) -> np.ndarray:
        outputs = self._session.run(["probabilities"], {"features": matrix.astype(np.float32)})
        return np.asarray(outputs[0], dtype=np.float32)

    def predict(self, features: np.ndarray) -> tuple[str, float, dict[str, float]]:
        """Top class, its probability, and the full distribution for one sample."""
        probabilities = self.predict_proba(features)[0]
        index = int(np.argmax(probabilities))
        distribution = {
            name: round(float(value), 5) for name, value in zip(self.classes, probabilities)
        }
        return self.classes[index], float(probabilities[index]), distribution

    def self_check(self, tolerance: float = 1e-4) -> None:
        """Assert the active runtime matches the NumPy reference on random input."""
        if self._session is None:
            return
        probe = np.random.default_rng(0).normal(size=(8, self.feature_dimension)).astype(np.float32)
        reference = self._predict_numpy(probe)
        actual = self._predict_onnx(probe)
        drift = float(np.abs(reference - actual).max())
        if drift > tolerance:
            raise ModelLoadError(
                f"ONNX runtime output drifts from the NumPy reference by {drift:.2e}."
            )

    # --- persistence ---------------------------------------------------------
    def save(self, directory: Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "artifactFormat": np.asarray(ARTIFACT_FORMAT),
            "classes": np.asarray(self.classes, dtype=object),
            "mean": self.mean.astype(np.float32),
            "scale": self.scale.astype(np.float32),
            "layerCount": np.asarray(len(self.weights)),
        }
        for index, (weight, bias) in enumerate(zip(self.weights, self.biases)):
            payload[f"W{index}"] = weight.astype(np.float32)
            payload[f"b{index}"] = bias.astype(np.float32)
        np.savez(directory / WEIGHTS_FILE, **payload)
        (directory / METADATA_FILE).write_text(
            json.dumps(self.metadata, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        return directory / WEIGHTS_FILE

    @classmethod
    def load(cls, directory: Path, prefer_onnx: bool = True) -> "GestureModelArtifact":
        directory = Path(directory)
        weights_path = directory / WEIGHTS_FILE
        if not weights_path.exists():
            raise ModelLoadError(f"No gesture model at {weights_path}.")

        try:
            archive = np.load(weights_path, allow_pickle=True)
            layer_count = int(archive["layerCount"])
            classes = [str(name) for name in archive["classes"]]
            weights = [np.asarray(archive[f"W{i}"], dtype=np.float32) for i in range(layer_count)]
            biases = [np.asarray(archive[f"b{i}"], dtype=np.float32) for i in range(layer_count)]
            mean = np.asarray(archive["mean"], dtype=np.float32)
            scale = np.asarray(archive["scale"], dtype=np.float32)
        except Exception as exc:  # noqa: BLE001 - any corruption lands here
            raise ModelLoadError(f"Gesture model at {weights_path} is corrupt: {exc}") from exc

        metadata: dict = {}
        metadata_path = directory / METADATA_FILE
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ModelLoadError(f"Model metadata at {metadata_path} is not valid JSON: {exc}") from exc

        if not weights or weights[0].ndim != 2:
            raise ModelLoadError("Gesture model contains no usable layers.")
        if len(classes) != weights[-1].shape[1]:
            raise ModelLoadError(
                f"Model declares {len(classes)} classes but its output layer has "
                f"{weights[-1].shape[1]} units."
            )
        if mean.shape != (weights[0].shape[0],) or scale.shape != mean.shape:
            raise ModelLoadError("Model scaler does not match the input layer.")

        declared = metadata.get("featureVersion")
        if declared and declared != FEATURE_VERSION:
            raise ModelLoadError(
                f"Model was trained on feature version '{declared}' but this build produces "
                f"'{FEATURE_VERSION}'. Retrain the personalized model."
            )
        if weights[0].shape[0] != feature_dimension(True):
            raise ModelLoadError(
                f"Model expects {weights[0].shape[0]} features, this build produces "
                f"{feature_dimension(True)}. Retrain the personalized model."
            )

        artifact = cls(
            classes=classes, weights=weights, biases=biases,
            mean=mean, scale=np.where(np.abs(scale) < 1e-8, 1.0, scale), metadata=metadata,
        )

        if prefer_onnx and (directory / ONNX_FILE).exists():
            artifact._session = _open_onnx_session(directory / ONNX_FILE)
            if artifact._session is not None:
                try:
                    artifact.self_check()
                except ModelLoadError as exc:
                    logger.warning("%s Falling back to the NumPy runtime.", exc)
                    artifact._session = None
        return artifact

    @property
    def runtime(self) -> str:
        return "onnxruntime" if self._session is not None else "numpy"


def _open_onnx_session(path: Path):
    try:
        import onnxruntime
    except ImportError:
        return None
    try:
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        options.intra_op_num_threads = 1   # the camera loop already owns a core
        return onnxruntime.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
    except Exception as exc:  # noqa: BLE001 - a bad .onnx must never break recognition
        logger.warning("Could not open %s (%s); using the NumPy runtime.", path, exc)
        return None


# --- construction from scikit-learn -------------------------------------------
def from_sklearn(classifier, scaler, classes: list[str], metadata: dict) -> GestureModelArtifact:
    """Wrap a fitted MLPClassifier + StandardScaler into a portable artifact."""
    if getattr(classifier, "out_activation_", "softmax") != "softmax":
        raise ValueError(
            f"Expected a softmax output layer, found '{classifier.out_activation_}'. "
            "The exported graph and the NumPy runtime both assume softmax."
        )
    if classifier.activation != "relu":
        raise ValueError(f"Expected ReLU hidden activations, found '{classifier.activation}'.")

    scale = np.asarray(scaler.scale_, dtype=np.float32)
    scale = np.where(np.abs(scale) < 1e-8, 1.0, scale)
    return GestureModelArtifact(
        classes=list(classes),
        weights=[np.asarray(w, dtype=np.float32) for w in classifier.coefs_],
        biases=[np.asarray(b, dtype=np.float32) for b in classifier.intercepts_],
        mean=np.asarray(scaler.mean_, dtype=np.float32),
        scale=scale,
        metadata=metadata,
    )


# --- ONNX export --------------------------------------------------------------
def export_onnx(artifact: GestureModelArtifact, directory: Path, verify: bool = True) -> Path:
    """Write the artifact as an ONNX graph and verify it against the NumPy path.

    The graph is built by hand (Sub, Div, Gemm, Relu, Softmax, ArgMax) rather than
    through a converter, so exactly the arithmetic implemented above is exported
    and there is no converter version to drift against.
    """
    try:
        from onnx import TensorProto, checker, helper, numpy_helper
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ModelLoadError(
            "ONNX export needs the `onnx` package: pip install -r backend/requirements-ml.txt"
        ) from exc

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    dimension = artifact.feature_dimension

    initializers = [
        numpy_helper.from_array(artifact.mean.astype(np.float32), "scaler_mean"),
        numpy_helper.from_array(artifact.scale.astype(np.float32), "scaler_scale"),
    ]
    nodes = [
        helper.make_node("Sub", ["features", "scaler_mean"], ["centred"]),
        helper.make_node("Div", ["centred", "scaler_scale"], ["scaled"]),
    ]

    cursor = "scaled"
    for index, (weight, bias) in enumerate(zip(artifact.weights, artifact.biases)):
        initializers.append(numpy_helper.from_array(weight.astype(np.float32), f"W{index}"))
        initializers.append(numpy_helper.from_array(bias.astype(np.float32), f"b{index}"))
        output = f"gemm{index}"
        nodes.append(helper.make_node("Gemm", [cursor, f"W{index}", f"b{index}"], [output], alpha=1.0, beta=1.0))
        cursor = output
        if index < len(artifact.weights) - 1:
            activated = f"relu{index}"
            nodes.append(helper.make_node("Relu", [cursor], [activated]))
            cursor = activated

    nodes.append(helper.make_node("Softmax", [cursor], ["probabilities"], axis=1))
    nodes.append(helper.make_node("ArgMax", ["probabilities"], ["label"], axis=1, keepdims=0))

    graph = helper.make_graph(
        nodes,
        "visionx_gesture_mlp",
        [helper.make_tensor_value_info("features", TensorProto.FLOAT, ["batch", dimension])],
        [
            helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, ["batch", len(artifact.classes)]),
            helper.make_tensor_value_info("label", TensorProto.INT64, ["batch"]),
        ],
        initializer=initializers,
    )
    model = helper.make_model(
        graph,
        producer_name="visionx",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    # Pin the IR version: newer `onnx` releases default to an IR that shipped
    # onnxruntime builds refuse to load. Opset 13 with IR 8 is understood by every
    # onnxruntime from 1.12 onward.
    model.ir_version = 8
    model.doc_string = json.dumps(
        {
            "modelVersion": artifact.model_version,
            "featureVersion": artifact.feature_version,
            "classes": artifact.classes,
            "exportedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
    checker.check_model(model)

    target = directory / ONNX_FILE
    target.write_bytes(model.SerializeToString())

    if verify:
        import importlib.util

        if importlib.util.find_spec("onnxruntime") is None:
            logger.warning(
                "onnxruntime is not installed - exported %s without a runtime check. "
                "Inference falls back to the NumPy runtime, which is the source of truth.", target,
            )
            return target

        session = _open_onnx_session(target)
        if session is None:
            target.unlink(missing_ok=True)
            raise ModelLoadError(
                "The exported ONNX graph could not be loaded by the installed onnxruntime; "
                "the export was discarded. The NumPy runtime is unaffected."
            )
        else:
            probe = np.random.default_rng(11).normal(size=(16, dimension)).astype(np.float32)
            reference = artifact._predict_numpy(probe)
            actual = np.asarray(session.run(["probabilities"], {"features": probe})[0])
            drift = float(np.abs(reference - actual).max())
            if drift > 1e-4:
                target.unlink(missing_ok=True)
                raise ModelLoadError(f"Refusing to ship an ONNX export that drifts by {drift:.2e}.")
            logger.info("ONNX export verified against the NumPy runtime (max drift %.2e).", drift)
    return target
