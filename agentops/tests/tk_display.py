"""Shared Tk display detection for GUI tests.

Tkinter needs a real X/Wayland display to create a root window. Headless CI
runners have no ``$DISPLAY``, so ``tk.Tk()`` raises ``TclError`` and every GUI
test errors out for an environmental reason rather than a product defect.

Testing policy: tests that need a real Tk root are skipped when no display is
available. Production GUI code is never faked -- the desktop application
still fails loudly without a display. Headless coverage of controller logic
lives in the non-Tk test classes, which run everywhere.
"""

import sys
import tkinter as tk
import unittest

from contextlib import suppress


def display_available() -> bool:
    """True when this interpreter can open a Tk root window."""
    if sys.platform == "win32" or sys.platform == "darwin":
        # Windows and macOS have a display server available to the session.
        return True
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    with suppress(tk.TclError):
        root.destroy()
    return True


#: Decorator form, for classes and test methods that build a Tk root.
requires_display = unittest.skipUnless(
    display_available(), "no Tk display available (headless environment)"
)