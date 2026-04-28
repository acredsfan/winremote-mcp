import pytest

from winremote import browser_debug


def test_console_logs_cdp_parsing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        browser_debug,
        "_get_tab",
        lambda session_id, tab_id: {"id": tab_id, "webSocketDebuggerUrl": "ws://local/tab"},
    )
    monkeypatch.setattr(
        browser_debug,
        "_collect_cdp_events",
        lambda ws_url, enable_methods, event_methods, collect_seconds=0.75: (
            True,
            [
                {
                    "method": "Runtime.consoleAPICalled",
                    "params": {
                        "type": "error",
                        "args": [{"value": "boom"}],
                        "timestamp": 1.0,
                    },
                }
            ],
            None,
        ),
    )

    payload = browser_debug.get_browser_console_logs("s1", "t1", level="error")
    assert payload["supported"] is True
    assert payload["log_count"] == 1
    assert payload["logs"][0]["message"] == "boom"


def test_network_requests_cdp_parsing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        browser_debug,
        "_get_tab",
        lambda session_id, tab_id: {"id": tab_id, "webSocketDebuggerUrl": "ws://local/tab"},
    )
    monkeypatch.setattr(
        browser_debug,
        "_collect_cdp_events",
        lambda ws_url, enable_methods, event_methods, collect_seconds=0.75: (
            True,
            [
                {
                    "method": "Network.requestWillBeSent",
                    "params": {
                        "requestId": "r1",
                        "request": {"url": "https://example.com", "method": "GET"},
                        "timestamp": 1.0,
                    },
                }
            ],
            None,
        ),
    )

    payload = browser_debug.get_browser_network_requests("s1", "t1")
    assert payload["supported"] is True
    assert payload["request_count"] == 1
    assert payload["requests"][0]["url"] == "https://example.com"


def test_dom_text_prefers_cdp(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        browser_debug,
        "_get_tab",
        lambda session_id, tab_id: {
            "id": tab_id,
            "url": "https://example.com",
            "webSocketDebuggerUrl": "ws://local/tab",
        },
    )
    monkeypatch.setattr(browser_debug, "_cdp_eval", lambda ws_url, expression, return_by_value=True: (True, "hello world", None))

    payload = browser_debug.get_browser_dom_text("s1", "t1")
    assert payload["supported"] is True
    assert payload["source"] == "cdp-runtime-evaluate"
    assert "hello world" in payload["text"]


def test_click_dom_element_uses_cdp(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        browser_debug,
        "_get_tab",
        lambda session_id, tab_id: {"id": tab_id, "webSocketDebuggerUrl": "ws://local/tab"},
    )
    monkeypatch.setattr(browser_debug, "_cdp_eval", lambda ws_url, expression, return_by_value=True: (True, {"ok": True}, None))

    payload = browser_debug.click_dom_element("s1", "t1", "#submit")
    assert payload["supported"] is True
    assert payload["clicked"] is True
