import json

import pytest


def test_browser_debug_tools_roundtrip(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.browser_debug, "launch_debug_browser", lambda **kwargs: {"session_id": "s1", "port": 9222})
    monkeypatch.setattr(main.browser_debug, "list_browser_tabs", lambda session_id: {"session_id": session_id, "count": 1, "tabs": [{"id": "t1"}]})
    monkeypatch.setattr(main.browser_debug, "get_browser_console_logs", lambda session_id, tab_id, level=None: {"supported": False, "logs": []})
    monkeypatch.setattr(main.browser_debug, "get_browser_network_requests", lambda session_id, tab_id: {"supported": False, "requests": []})
    monkeypatch.setattr(main.browser_debug, "get_browser_dom_text", lambda session_id, tab_id: {"supported": True, "text": "hello"})
    monkeypatch.setattr(main.browser_debug, "click_dom_element", lambda session_id, tab_id, selector: {"supported": False, "selector": selector})

    launched = json.loads(main.LaunchDebugBrowser(browser="edge"))
    tabs = json.loads(main.ListBrowserTabs("s1"))
    logs = json.loads(main.GetBrowserConsoleLogs("s1", "t1"))
    requests = json.loads(main.GetBrowserNetworkRequests("s1", "t1"))
    dom = json.loads(main.GetBrowserDomText("s1", "t1"))
    click = json.loads(main.ClickDomElement("s1", "t1", "#submit"))

    assert launched["session_id"] == "s1"
    assert tabs["count"] == 1
    assert logs["supported"] is False
    assert requests["supported"] is False
    assert dom["supported"] is True
    assert click["selector"] == "#submit"
