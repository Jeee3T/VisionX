"""The layer both input modalities share.

Gesture recognition and the voice assistant are two front ends onto one command
pipeline. This package holds what they have in common:

  * `command.py`   - the structured CommandIntent every modality emits
  * `context.py`   - the shared live context multimodal commands resolve against
  * `reporting.py` - one classification-report format for both trained models

Nothing here imports Flask, MongoDB, PyAutoGUI or MediaPipe.
"""
