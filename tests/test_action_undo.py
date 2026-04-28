from winremote import action_undo


def test_undo_last_action_no_action():
    action_undo._LAST_ACTION = None
    result = action_undo.undo_last_action()
    assert result["success"] is False
    assert result["undoable"] is False


def test_undo_typed_uses_ctrl_z(monkeypatch):
    called = {"hotkey": False}

    monkeypatch.setattr(action_undo.pyautogui, "hotkey", lambda *keys: called.__setitem__("hotkey", keys == ("ctrl", "z")))
    action_undo.set_last_action({"type": "typed"})

    result = action_undo.undo_last_action()
    assert result["success"] is True
    assert result["strategy"] == "ctrl_z"
    assert called["hotkey"] is True


def test_undo_clipboard_restore(monkeypatch):
    restored = {"value": None}

    monkeypatch.setattr(action_undo.desktop, "set_clipboard", lambda text: restored.__setitem__("value", text) or "ok")
    action_undo.set_last_action({"type": "clipboard_overwrite", "previous_clipboard": "old"})

    result = action_undo.undo_last_action()
    assert result["success"] is True
    assert result["strategy"] == "restore_clipboard"
    assert restored["value"] == "old"


def test_undo_non_undoable_click():
    action_undo.set_last_action({"type": "click"})
    result = action_undo.undo_last_action()
    assert result["success"] is False
    assert result["undoable"] is False
