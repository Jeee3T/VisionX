"""The VisionX voice assistant.

    microphone -> speech-to-text -> intent classifier -> CommandIntent
               -> the existing CommandDispatcher -> PowerPoint

Speech-to-text is a pretrained third-party model (Whisper). The intent
classifier is trained by VisionX on the project's own dataset. Nothing in this
package touches PyAutoGUI or PowerPoint: it produces CommandIntents and the
existing dispatcher does the rest.
"""
