"""Reproducible command-line training pipeline for the personalized gesture model.

    python -m computer_vision.ml.training.synthesize_dataset     # smoke-test data
    python -m computer_vision.ml.training.train_gesture_model    # train + evaluate + export
    python -m computer_vision.ml.training.evaluate_gesture_model # evaluate an existing model
    python -m computer_vision.ml.training.export_gesture_model   # re-export ONNX

Every entry point takes --seed and is deterministic for a fixed seed, dataset
and scikit-learn version.
"""
