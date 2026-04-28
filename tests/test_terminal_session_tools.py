import json

import pytest


def test_terminal_session_tools_roundtrip(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.terminal_sessions, "create_terminal_session", lambda **kwargs: {"terminal_id": "t1", "shell": kwargs.get("shell", "powershell")})
    monkeypatch.setattr(main.terminal_sessions, "list_terminal_sessions", lambda: [{"terminal_id": "t1", "running": True}])
    monkeypatch.setattr(main.terminal_sessions, "read_terminal_output", lambda terminal_id, lines=200: {"terminal_id": terminal_id, "line_count": 1, "output": "ok"})
    monkeypatch.setattr(main.terminal_sessions, "send_terminal_input", lambda terminal_id, text, press_enter=True: {"terminal_id": terminal_id, "success": True, "sent_text_length": len(text)})
    monkeypatch.setattr(main.terminal_sessions, "wait_for_terminal_output", lambda terminal_id, expected_text, timeout_seconds=60.0, poll_interval=0.25: {"terminal_id": terminal_id, "satisfied": True})
    monkeypatch.setattr(main.terminal_sessions, "stop_terminal_session", lambda terminal_id, force=False: {"terminal_id": terminal_id, "success": True, "force": force})

    created = json.loads(main.CreateTerminalSession(shell="powershell"))
    listed = json.loads(main.ListTerminalSessions())
    read_payload = json.loads(main.ReadTerminalOutput("t1"))
    sent = json.loads(main.SendTerminalInput("t1", "echo hi"))
    waited = json.loads(main.WaitForTerminalOutput("t1", "hi"))
    stopped = json.loads(main.StopTerminalSession("t1", force=True))

    assert created["terminal_id"] == "t1"
    assert listed[0]["terminal_id"] == "t1"
    assert read_payload["terminal_id"] == "t1"
    assert sent["success"] is True
    assert waited["satisfied"] is True
    assert stopped["force"] is True
