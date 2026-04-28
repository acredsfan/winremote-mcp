import json

import pytest


def test_clipboard_safe_read_redacts(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)
    monkeypatch.setattr(main.desktop, "get_clipboard", lambda: "token sk-abcdefghijklmnopqrstuvwxyz1234")

    raw = main.ClipboardSafeRead(redact_secrets=True)
    payload = json.loads(raw)

    assert payload["success"] is True
    assert payload["redacted"] is True
    assert "[REDACTED]" in payload["text"]


def test_clipboard_safe_write_blocks_sensitive_when_requested(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)

    raw = main.ClipboardSafeWrite(
        text="Bearer abcdefghijklmnop",
        confirm_if_sensitive=True,
        redact_in_logs=True,
    )
    payload = json.loads(raw)

    assert payload["success"] is False
    assert payload["blocked"] is True


def test_clipboard_safe_write_allows_and_redacts_logs(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    stored = {"value": ""}
    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)
    monkeypatch.setattr(main.desktop, "set_clipboard", lambda text: stored.__setitem__("value", text) or "Clipboard set")

    raw = main.ClipboardSafeWrite(
        text="Bearer abcdefghijklmnop",
        confirm_if_sensitive=False,
        redact_in_logs=True,
    )
    payload = json.loads(raw)

    assert payload["success"] is True
    assert payload["sensitive"] is True
    assert stored["value"] == "Bearer abcdefghijklmnop"
    assert payload["logged_text"] == "[REDACTED]"


def test_paste_text_restores_clipboard(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    state = {"clipboard": "initial", "pasted": False}

    def _get_clipboard():
        return state["clipboard"]

    def _set_clipboard(value: str):
        state["clipboard"] = value
        return "Clipboard set"

    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)
    monkeypatch.setattr(main.desktop, "get_clipboard", _get_clipboard)
    monkeypatch.setattr(main.desktop, "set_clipboard", _set_clipboard)
    monkeypatch.setattr(main.desktop, "focus_window", lambda title=None, handle=None: "Focused")
    monkeypatch.setattr(main.pyautogui, "hotkey", lambda *keys: state.__setitem__("pasted", keys == ("ctrl", "v")))

    raw = main.PasteText("hello", target_window="Notepad", restore_clipboard=True)
    payload = json.loads(raw)

    assert payload["success"] is True
    assert payload["restored"] is True
    assert payload["target_window"] == "Notepad"
    assert state["pasted"] is True
    assert state["clipboard"] == "initial"
