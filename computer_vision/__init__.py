"""VisionX computer vision engine.

Layering rule (never violated): Recognition -> Mapping -> Dispatch -> Control.
Nothing in this package imports PyAutoGUI or touches the OS input stack; the
engine only emits command names through a callback supplied by the caller.
"""
