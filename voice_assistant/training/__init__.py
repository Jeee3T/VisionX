"""Reproducible training pipeline for the VisionX voice intent classifier.

    python -m voice_assistant.training.build_intent_dataset   # author -> versioned dataset
    python -m voice_assistant.training.train_intent_model     # train + evaluate + save
    python -m voice_assistant.training.evaluate_intent_model  # evaluate an existing model

Speech-to-text is NOT trained here - VisionX uses pretrained Whisper for that.
Only the intent classifier is a VisionX-trained model.
"""
