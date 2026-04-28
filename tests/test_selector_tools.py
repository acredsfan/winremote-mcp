import json

import pytest


def test_selector_tools_roundtrip(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)
    monkeypatch.setattr(main.selectors, "save_ui_selector", lambda name, selector: {"success": True, "name": name, "selector": selector})
    monkeypatch.setattr(
        main.selectors,
        "find_ui_selector",
        lambda **kwargs: {"name": kwargs["name"], "count": 1, "matches": [{"label": "Save", "center": {"x": 1, "y": 2}}], "search": {}},
    )
    monkeypatch.setattr(main.selectors, "click_ui_selector", lambda **kwargs: {"success": True, "name": kwargs["name"], "target": {"label": "Save"}})

    saved = json.loads(main.SaveUISelector("save-btn", query="Save", window_title="App"))
    assert saved["success"] is True

    found = json.loads(main.FindUISelector("save-btn"))
    assert found["count"] == 1

    clicked = json.loads(main.ClickUISelector("save-btn"))
    assert clicked["success"] is True
