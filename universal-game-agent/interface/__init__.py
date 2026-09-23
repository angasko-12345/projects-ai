"""External game interface: pixels in, abstract controller actions out.

Learning code MUST NOT depend on game internals; this package is the only
place that touches OS input and screen pixels.
"""
__all__ = [
    "GameInterface",
    "ScreenCapture",
    "WindowCapture",
    "MSSBackend",
    "SyntheticBackend",
    "ActionMapper",
    "ActionDef",
    "RecordingBackend",
    "SendInputBackend",
    "WindowManager",
    "WindowNotFoundError",
    "WindowLostError",
    "pc_action_table",
]

_MODULES = {
    "GameInterface": "adapter",
    "ScreenCapture": "capture",
    "WindowCapture": "capture",
    "MSSBackend": "capture",
    "SyntheticBackend": "capture",
    "ActionMapper": "controller",
    "ActionDef": "controller",
    "RecordingBackend": "controller",
    "SendInputBackend": "controller",
    "WindowManager": "window",
    "WindowNotFoundError": "window",
    "WindowLostError": "window",
    "pc_action_table": "controller",
}


def __getattr__(name: str):
    if name in _MODULES:
        from importlib import import_module

        return getattr(import_module(f".{_MODULES[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
