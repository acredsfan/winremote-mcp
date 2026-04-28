import json
from types import SimpleNamespace

import pytest


def _window(handle: int, title: str, pid: int = 101, process_name: str = "Code.exe", monitor_id: int = 1):
    return SimpleNamespace(
        handle=handle,
        title=title,
        pid=pid,
        process_name=process_name,
        monitor_id=monitor_id,
        rect=(10, 20, 410, 320),
        visible=True,
    )


def test_app_health_check_running_and_active(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main, "_process_matches", lambda **kwargs: [{"pid": 999, "name": "Code.exe", "status": "running"}])
    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)
    monkeypatch.setattr(main.desktop, "enumerate_windows", lambda: [_window(123, "Visual Studio Code")])
    monkeypatch.setattr(main.desktop.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(main.desktop.win32gui, "GetWindowText", lambda hwnd: "Visual Studio Code")

    raw = main.AppHealthCheck(process_name="Code", window_title="Code")
    payload = json.loads(raw)

    assert payload["running"] is True
    assert payload["active"] is True
    assert payload["responsive"] is True


def test_agent_status_report_recommends_stop_recording(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)
    monkeypatch.setattr(main.desktop, "enumerate_windows", lambda: [_window(123, "Visual Studio Code")])
    monkeypatch.setattr(main.desktop.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(main.recording, "list_active_recordings", lambda: [{"recording_id": "rec_123"}])
    monkeypatch.setattr(main.task_manager, "list_tasks", lambda status=None: [] if status in {"running", "pending"} else [])

    raw = main.AgentStatusReport()
    payload = json.loads(raw)

    assert payload["recording_active"] is True
    assert payload["recommended_next_tool"] == "StopScreenRecording"
    assert payload["active_window"]["title"] == "Visual Studio Code"


def test_agent_status_report_recommends_wait_for_change_with_running_task(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)
    monkeypatch.setattr(main.desktop, "enumerate_windows", lambda: [_window(123, "Terminal")])
    monkeypatch.setattr(main.desktop.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(main.recording, "list_active_recordings", lambda: [])

    def _list_tasks(status=None):
        if status == "running":
            return [{"task_id": "abc", "tool_name": "Shell", "status": "running"}]
        if status == "pending":
            return []
        return [{"task_id": "abc", "tool_name": "Shell", "status": "running"}]

    monkeypatch.setattr(main.task_manager, "list_tasks", _list_tasks)

    raw = main.AgentStatusReport()
    payload = json.loads(raw)

    assert payload["recommended_next_tool"] == "WaitForChange"
    assert len(payload["running_tasks"]) == 1
