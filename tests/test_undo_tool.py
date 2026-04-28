import json


def test_undo_last_action_tool(monkeypatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.action_undo, "undo_last_action", lambda strategy="auto": {"success": True, "strategy": strategy})

    raw = main.UndoLastAction(strategy="auto")
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["strategy"] == "auto"
