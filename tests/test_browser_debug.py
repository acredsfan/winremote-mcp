from types import SimpleNamespace

import pytest

from winremote import browser_debug


def test_launch_debug_browser_returns_session(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(browser_debug, "_find_browser_executable", lambda browser: "C:/Browser/browser.exe")
    monkeypatch.setattr(browser_debug.subprocess, "Popen", lambda cmd: SimpleNamespace(pid=4242, cmd=cmd))
    monkeypatch.setattr(browser_debug, "_debug_root", lambda: tmp_path)

    payload = browser_debug.launch_debug_browser(browser="edge", remote_debugging_port=9333)

    assert payload["session_id"].startswith("browser_")
    assert payload["port"] == 9333
    assert payload["pid"] == 4242


def test_list_browser_tabs(monkeypatch: pytest.MonkeyPatch):
    session_id = "browser_test"
    browser_debug._SESSIONS[session_id] = {"port": 9222}

    monkeypatch.setattr(
        browser_debug,
        "_json_get",
        lambda url: [
            {
                "id": "tab1",
                "title": "Example",
                "url": "https://example.com",
                "type": "page",
                "webSocketDebuggerUrl": "ws://localhost/devtools/page/tab1",
            }
        ],
    )

    payload = browser_debug.list_browser_tabs(session_id)
    assert payload["count"] == 1
    assert payload["tabs"][0]["id"] == "tab1"


def test_console_logs_returns_structured_placeholder(monkeypatch: pytest.MonkeyPatch):
    session_id = "browser_test_logs"
    browser_debug._SESSIONS[session_id] = {"port": 9222}
    monkeypatch.setattr(browser_debug, "_json_get", lambda url: [{"id": "tab1", "url": "https://example.com"}])

    payload = browser_debug.get_browser_console_logs(session_id, "tab1")
    assert payload["supported"] is False
    assert isinstance(payload["logs"], list)
