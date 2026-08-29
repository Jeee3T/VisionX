"""Re-export a trained gesture model to ONNX from its portable weights.

    python -m computer_vision.ml.training.export_gesture_model --user <id>

Training already exports ONNX. This exists for the cases where it could not:
`onnx` was not installed at training time, the .onnx was deleted, or a newer
onnxruntime needs a fresh graph. The .npz weights are always the source of
truth - the export is verified against the NumPy runtime before it is kept.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from computer_vision.ml import registry
from computer_vision.ml.mlp import GestureModelArtifact, ModelLoadError, export_onnx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", type=Path, default=None, help="model directory")
    parser.add_argument("--user", default=None, help="export this user's model")
    parser.add_argument("--output", type=Path, default=None, help="write the .onnx elsewhere")
    parser.add_argument("--no-verify", action="store_true", help="skip the NumPy agreement check")
    args = parser.parse_args(argv)

    directory = args.model or (registry.model_dir(args.user) if args.user else None)
    if directory is None:
        print("error: pass --model <dir> or --user <id>", file=sys.stderr)
        return 2

    try:
        artifact = GestureModelArtifact.load(directory, prefer_onnx=False)
        target = export_onnx(artifact, args.output or directory, verify=not args.no_verify)
    except ModelLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    registry.invalidate()
    print(json.dumps({
        "modelVersion": artifact.model_version,
        "featureVersion": artifact.feature_version,
        "classes": len(artifact.classes),
        "parameters": artifact.parameter_count,
        "onnx": str(target),
        "sizeBytes": target.stat().st_size,
        "verified": not args.no_verify,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
