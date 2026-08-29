"""Machine-learning layer for VisionX gesture recognition.

VisionX-trained components live here. MediaPipe's pretrained hand model stays
exactly where it was (`computer_vision/hand_detection`) and is untouched: this
package only ever consumes the 21 landmarks it produces.

    MediaPipe landmarks -> canonicalization -> personalized MLP -> GestureResult

Everything in this package is optional. If a user has no personalized model the
runtime falls back to the original geometric recognizer, which is never removed.
"""
