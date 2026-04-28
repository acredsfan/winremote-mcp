from pathlib import Path

import pytest

from winremote import selectors


def test_save_and_load_selector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(selectors, "_selectors_root", lambda: tmp_path)

    saved = selectors.save_ui_selector("vscode-terminal", {"query": "Terminal", "window_title": "Visual Studio Code"})
    assert saved["success"] is True

    loaded = selectors.load_ui_selector("vscode-terminal")
    assert loaded["query"] == "Terminal"
    assert loaded["window_title"] == "Visual Studio Code"


def test_find_selector_uses_desktop_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(selectors, "_selectors_root", lambda: tmp_path)
    selectors.save_ui_selector("save-btn", {"query": "Save", "window_title": "App"})

    monkeypatch.setattr(
        selectors.desktop,
        "find_ui_elements_with_context",
        lambda **kwargs: {
            "matches": [{"label": "Save", "center": {"x": 10, "y": 20}, "match": {"score": 95}}],
            "searched_element_count": 4,
            "searchable_preview": [],
            "summary": {"control_count": 4},
            "recommendations": [],
        },
    )

    result = selectors.find_ui_selector("save-btn")
    assert result["count"] == 1
    assert result["matches"][0]["label"] == "Save"


def test_click_selector_clicks_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(selectors, "_selectors_root", lambda: tmp_path)
    selectors.save_ui_selector("save-btn", {"query": "Save", "window_title": "App"})

    monkeypatch.setattr(
        selectors.desktop,
        "find_ui_elements_with_context",
        lambda **kwargs: {
            "matches": [{"label": "Save", "class": "Button", "center": {"x": 10, "y": 20}, "match": {"score": 95}}],
            "searched_element_count": 4,
            "searchable_preview": [],
            "summary": {"control_count": 4},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(selectors.desktop, "validate_screen_point", lambda x, y: None)

    called = {"clicked": False}
    import pyautogui  # noqa: PLC0415

    monkeypatch.setattr(pyautogui, "click", lambda x, y, button="left": called.__setitem__("clicked", True))

    result = selectors.click_ui_selector("save-btn")
    assert result["success"] is True
    assert called["clicked"] is True
