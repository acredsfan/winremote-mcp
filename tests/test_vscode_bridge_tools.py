import json

import pytest


def test_vscode_bridge_tools_roundtrip(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.vscode_bridge, "list_vscode_windows", lambda: [{"window_id": "hwnd:1", "title": "a.py - Visual Studio Code"}])
    monkeypatch.setattr(main.vscode_bridge, "get_active_file", lambda: {"supported": True, "active_file": {"file_name": "a.py"}})
    monkeypatch.setattr(main.vscode_bridge, "list_open_files", lambda: {"supported": True, "count": 1, "open_files": [{"file_name": "a.py"}]})
    monkeypatch.setattr(main.vscode_bridge, "get_diagnostics", lambda: {"supported": True, "diagnostic_count": 1, "diagnostics": [{"severity": "error", "message": "x"}]})
    monkeypatch.setattr(main.vscode_bridge, "read_problems_panel", lambda max_lines=200: {"supported": True, "item_count": 1, "items": [{"severity": "warning", "message": "y"}]})
    monkeypatch.setattr(main.vscode_bridge, "read_terminal", lambda lines=200: {"supported": True, "source": "controlled-terminal", "output": "ok"})
    monkeypatch.setattr(main.vscode_bridge, "open_file", lambda path, line=None: {"supported": True, "path": path, "line": line, "exit_code": 0})
    monkeypatch.setattr(main.vscode_bridge, "run_command", lambda command_id: {"supported": True, "command_id": command_id, "exit_code": 0})

    windows = json.loads(main.VSCodeListWindows())
    active = json.loads(main.VSCodeGetActiveFile())
    open_files = json.loads(main.VSCodeListOpenFiles())
    diagnostics = json.loads(main.VSCodeGetDiagnostics())
    problems = json.loads(main.VSCodeReadProblemsPanel(lines=50))
    terminal = json.loads(main.VSCodeReadTerminal(lines=40))
    opened = json.loads(main.VSCodeOpenFile("README.md", line=10))
    ran = json.loads(main.VSCodeRunCommand("workbench.action.files.save"))

    assert windows["count"] == 1
    assert active["active_file"]["file_name"] == "a.py"
    assert open_files["count"] == 1
    assert diagnostics["diagnostic_count"] == 1
    assert problems["item_count"] == 1
    assert terminal["source"] == "controlled-terminal"
    assert opened["line"] == 10
    assert ran["command_id"] == "workbench.action.files.save"
