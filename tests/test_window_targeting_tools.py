import json
from types import SimpleNamespace

import pytest


def _window(handle: int, title: str):
    return SimpleNamespace(
        handle=handle,
        title=title,
        pid=1234,
        process_name="Code.exe",
        monitor_id=1,
        rect=(10, 20, 410, 320),
    )


def test_list_monitors_tool(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "get_monitor_info", lambda: [{"monitor_id": 1, "rect": {"left": 0, "top": 0, "right": 1920, "bottom": 1080}}])
    monkeypatch.setattr(main.desktop, "get_virtual_screen_bounds", lambda monitors: {"left": 0, "top": 0, "right": 1920, "bottom": 1080})

    payload = json.loads(main.ListMonitors())
    assert payload["count"] == 1
    assert payload["monitors"][0]["monitor_id"] == 1


def test_get_active_window_tool(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)
    monkeypatch.setattr(main.desktop.win32gui, "GetForegroundWindow", lambda: 55)
    monkeypatch.setattr(main.desktop.win32gui, "GetWindowText", lambda hwnd: "Visual Studio Code")
    monkeypatch.setattr(main.desktop, "enumerate_windows", lambda: [_window(55, "Visual Studio Code")])

    payload = json.loads(main.GetActiveWindow())
    assert payload["found"] is True
    assert payload["handle"] == 55
    assert payload["title"] == "Visual Studio Code"


def test_get_window_bounds_tool(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)
    monkeypatch.setattr(main.desktop.win32gui, "GetWindowRect", lambda hwnd: (1, 2, 11, 22))

    payload = json.loads(main.GetWindowBounds("hwnd:123"))
    assert payload["window_id"] == "hwnd:123"
    assert payload["rect"]["width"] == 10
    assert payload["rect"]["height"] == 20
