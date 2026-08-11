"""One-time setup: fetch the pretrained MediaPipe hand model.

    python scripts/download_model.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from computer_vision.hand_detection.hand_detector import MODEL_PATH, ensure_model  # noqa: E402

if __name__ == "__main__":
    path = ensure_model()
    print(f"Hand model ready: {path} ({path.stat().st_size / 1_000_000:.1f} MB)")
    if path != MODEL_PATH:  # pragma: no cover
        print("Warning: model stored outside the expected directory.")
