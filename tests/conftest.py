"""Shared fixtures for winremote-mcp tests.

Mocks win32 and display-dependent modules so tests run on headless Linux.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

from PIL import Image

# ---------------------------------------------------------------------------
# Must happen before ANY winremote import
# ---------------------------------------------------------------------------

os.environ.setdefault("DISPLAY", ":0")

# Mock all problematic native modules
_mock_modules = [
    "Xlib",
    "Xlib.display",
    "Xlib.xauth",
    "Xlib.error",
    "Xlib.protocol",
    "Xlib.protocol.display",
    "Xlib.protocol.rq",
    "Xlib.support",
    "Xlib.support.connect",
    "Xlib.support.unix_connect",
    "Xlib.ext",
    "Xlib.ext.xtest",
    "Xlib.X",
    "Xlib.XK",
    "Xlib.keysymdef",
    "Xlib.keysymdef.latin1",
    "mouseinfo",
    "win32api",
    "win32gui",
    "win32con",
    "win32process",
    "win32clipboard",
    "winreg",
]

for mod_name in _mock_modules:
    if mod_name not in sys.modules:
        m = MagicMock()
        m.__path__ = []
        m.__file__ = f"<mock {mod_name}>"
        m.__spec__ = None
        sys.modules[mod_name] = m

# Now import pyautogui safely
import pyautogui  # noqa: E402

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_pyautogui(monkeypatch):
    """Prevent any real mouse/keyboard actions during tests."""
    monkeypatch.setattr(pyautogui, "click", MagicMock())
    monkeypatch.setattr(pyautogui, "doubleClick", MagicMock())
    monkeypatch.setattr(pyautogui, "moveTo", MagicMock())
    monkeypatch.setattr(pyautogui, "drag", MagicMock())
    monkeypatch.setattr(pyautogui, "moveRel", MagicMock())
    monkeypatch.setattr(pyautogui, "scroll", MagicMock())
    monkeypatch.setattr(pyautogui, "hscroll", MagicMock())
    monkeypatch.setattr(pyautogui, "hotkey", MagicMock())
    monkeypatch.setattr(pyautogui, "press", MagicMock())
    monkeypatch.setattr(pyautogui, "keyDown", MagicMock())
    monkeypatch.setattr(pyautogui, "keyUp", MagicMock())
    monkeypatch.setattr(pyautogui, "mouseDown", MagicMock())
    monkeypatch.setattr(pyautogui, "mouseUp", MagicMock())
    monkeypatch.setattr(pyautogui, "typewrite", MagicMock())
    monkeypatch.setattr(pyautogui, "write", MagicMock())
    monkeypatch.setattr(pyautogui, "position", MagicMock(return_value=(500, 500)))

    import winremote.desktop as desktop

    monitor_info = [
        {
            "monitor_id": 1,
            "primary": True,
            "rect": {"left": 0, "top": 0, "right": 1920, "bottom": 1080},
            "work_rect": {"left": 0, "top": 0, "right": 1920, "bottom": 1040},
            "size": {"width": 1920, "height": 1080},
            "dpi": {"x": 96, "y": 96},
            "scale": 1.0,
            "dpi_fallback": True,
        }
    ]
    virtual_screen = {"left": 0, "top": 0, "right": 1920, "bottom": 1080, "width": 1920, "height": 1080}
    dummy_image = Image.new("RGB", (1920, 1080), "black")

    monkeypatch.setattr(desktop, "get_monitor_info", MagicMock(return_value=monitor_info))
    monkeypatch.setattr(desktop, "get_virtual_screen_bounds", MagicMock(return_value=virtual_screen))
    monkeypatch.setattr(desktop, "validate_screen_point", MagicMock(return_value=monitor_info[0]))
    monkeypatch.setattr(desktop, "capture_image", MagicMock(return_value=(dummy_image, {"bounds": virtual_screen, "captured_monitors": [1], "monitors": monitor_info, "virtual_screen": virtual_screen})))
