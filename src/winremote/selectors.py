"""Persistent UI selector storage and resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from winremote import desktop


def _selectors_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "WinRemoteMCP" / "selectors"
    return Path.home() / ".local" / "share" / "WinRemoteMCP" / "selectors"


def _selector_file(name: str) -> Path:
    safe = "".join(ch for ch in name.strip() if ch.isalnum() or ch in {"-", "_", "."})
    if not safe:
        raise ValueError("name is required")
    return _selectors_root() / f"{safe}.json"


def save_ui_selector(name: str, selector: dict[str, Any]) -> dict[str, Any]:
    path = _selector_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(selector)
    payload.setdefault("name", name)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"success": True, "name": name, "path": str(path), "selector": payload}


def load_ui_selector(name: str) -> dict[str, Any]:
    path = _selector_file(name)
    if not path.exists():
        raise FileNotFoundError(f"Selector not found: {name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Selector file is invalid")
    return payload


def find_ui_selector(name: str, *, max_results: int = 1, include_text: bool = True) -> dict[str, Any]:
    selector = load_ui_selector(name)
    query = str(selector.get("query") or selector.get("label") or "").strip()
    if not query:
        raise ValueError("Saved selector is missing query/label")

    window_title = str(selector.get("window_title") or "")
    match_mode = str(selector.get("match_mode") or "auto")

    search_result = desktop.find_ui_elements_with_context(
        query=query,
        window_title=window_title,
        include_text=include_text,
        max_results=max_results,
        match_mode=match_mode,
    )

    return {
        "name": name,
        "selector": selector,
        "count": len(search_result.get("matches") or []),
        "matches": search_result.get("matches") or [],
        "search": {
            "searched_element_count": search_result.get("searched_element_count", 0),
            "searchable_preview": search_result.get("searchable_preview", []),
            "summary": search_result.get("summary"),
            "recommendations": search_result.get("recommendations", []),
        },
    }


def click_ui_selector(name: str, *, button: str = "left") -> dict[str, Any]:
    found = find_ui_selector(name=name, max_results=1)
    matches = found.get("matches") or []
    if not matches:
        return {
            "success": False,
            "name": name,
            "reason": "No matching UI element found",
            "result": found,
        }

    target = matches[0]
    center = target.get("center") or {}
    x = int(center.get("x", 0) or 0)
    y = int(center.get("y", 0) or 0)
    desktop.validate_screen_point(x, y)

    import pyautogui

    pyautogui.click(x, y, button=button)
    return {
        "success": True,
        "name": name,
        "button": button,
        "target": {
            "label": target.get("label"),
            "class": target.get("class"),
            "center": target.get("center"),
            "match": target.get("match"),
        },
    }
