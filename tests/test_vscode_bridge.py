from types import SimpleNamespace

from winremote import vscode_bridge


def _window(title: str, process_name: str = "Code.exe", handle: int = 100):
    return SimpleNamespace(
        handle=handle,
        title=title,
        process_name=process_name,
        pid=1234,
        monitor_id=1,
        rect=(10, 20, 810, 620),
        visible=True,
    )


def test_list_vscode_windows_filters_results(monkeypatch):
    monkeypatch.setattr(
        vscode_bridge.desktop,
        "enumerate_windows",
        lambda: [
            _window("main.py - app - Visual Studio Code", "Code.exe", 101),
            _window("Notepad", "notepad.exe", 102),
        ],
    )

    windows = vscode_bridge.list_vscode_windows()
    assert len(windows) == 1
    assert windows[0]["window_id"] == "hwnd:101"
    assert "Visual Studio Code" in windows[0]["title"]


def test_get_active_file_uses_window_title(monkeypatch):
    monkeypatch.setattr(
        vscode_bridge,
        "list_vscode_windows",
        lambda: [
            {
                "window_id": "hwnd:201",
                "title": "server.py - web-api - Visual Studio Code",
                "process_name": "Code.exe",
                "pid": 1,
                "monitor_id": 1,
                "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100},
                "is_visible": True,
            }
        ],
    )
    monkeypatch.setattr(vscode_bridge, "_run_code_cli", lambda args, timeout=8.0: {"supported": True, "exit_code": 0})

    payload = vscode_bridge.get_active_file()
    assert payload["supported"] is True
    assert payload["active_file"]["file_name"] == "server.py"
    assert payload["active_file"]["workspace"] == "web-api"


def test_read_terminal_prefers_controlled_session(monkeypatch):
    monkeypatch.setattr(vscode_bridge.terminal_sessions, "list_terminal_sessions", lambda: [{"terminal_id": "t1"}])
    monkeypatch.setattr(
        vscode_bridge.terminal_sessions,
        "read_terminal_output",
        lambda terminal_id, lines=200: {"output": "build ok", "line_count": 1},
    )

    payload = vscode_bridge.read_terminal(lines=50)
    assert payload["supported"] is True
    assert payload["source"] == "controlled-terminal"
    assert payload["terminal_id"] == "t1"


def test_open_file_uses_cli(monkeypatch):
    monkeypatch.setattr(
        vscode_bridge,
        "_run_code_cli",
        lambda args, timeout=8.0: {
            "supported": True,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "command": ["code", *args],
        },
    )

    payload = vscode_bridge.open_file("README.md", line=12)
    assert payload["supported"] is True
    assert payload["line"] == 12
    assert payload["exit_code"] == 0
