"""Track and undo the most recent reversible action."""

from __future__ import annotations

from typing import Any

import pyautogui

from winremote import desktop

_LAST_ACTION: dict[str, Any] | None = None


def set_last_action(action: dict[str, Any]) -> None:
    global _LAST_ACTION
    _LAST_ACTION = dict(action)


def get_last_action() -> dict[str, Any] | None:
    return dict(_LAST_ACTION) if _LAST_ACTION else None


def undo_last_action(strategy: str = "auto") -> dict[str, Any]:
    global _LAST_ACTION

    if _LAST_ACTION is None:
        return {
            "success": False,
            "undoable": False,
            "reason": "No prior action recorded",
            "strategy": strategy,
        }

    action_type = str(_LAST_ACTION.get("type") or "").strip().lower()
    chosen = strategy.strip().lower() if strategy else "auto"

    if chosen in {"auto", "ctrl_z"} and action_type in {"typed", "text_input"}:
        pyautogui.hotkey("ctrl", "z")
        result = {
            "success": True,
            "undoable": True,
            "strategy": "ctrl_z",
            "reason": "Issued Ctrl+Z to undo text input",
            "action_type": action_type,
        }
        _LAST_ACTION = None
        return result

    if chosen in {"auto", "restore_clipboard"} and action_type == "clipboard_overwrite":
        previous = _LAST_ACTION.get("previous_clipboard")
        if previous is None:
            return {
                "success": False,
                "undoable": False,
                "strategy": "restore_clipboard",
                "reason": "No previous clipboard snapshot available",
                "action_type": action_type,
            }
        desktop.set_clipboard(str(previous))
        result = {
            "success": True,
            "undoable": True,
            "strategy": "restore_clipboard",
            "reason": "Clipboard content restored",
            "action_type": action_type,
        }
        _LAST_ACTION = None
        return result

    return {
        "success": False,
        "undoable": False,
        "strategy": chosen,
        "reason": "Last action is not safely undoable",
        "action_type": action_type,
    }
