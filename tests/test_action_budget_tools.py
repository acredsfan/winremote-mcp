import json

import pytest


def test_action_budget_tools_and_enforcement(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    json.loads(main.ResetActionBudget())
    json.loads(main.ConfigureActionBudget(max_clicks_per_minute=1, max_keystrokes_per_minute=2))

    monkeypatch.setattr(main.desktop, "validate_screen_point", lambda x, y: None)
    monkeypatch.setattr(main.pyautogui, "click", lambda x, y, button="left": None)
    monkeypatch.setattr(main.pyautogui, "typewrite", lambda text, interval=0.02: None)
    monkeypatch.setattr(main.pyautogui, "write", lambda text: None)

    first_click = main.Click(10, 10)
    second_click = main.Click(11, 11)

    assert "Clicked" in first_click
    assert "Action blocked by budget policy" in second_click

    status = json.loads(main.GetActionBudgetStatus())
    assert status["paused"] is True

    reset = json.loads(main.ResetActionBudget())
    assert reset["paused"] is False

    typed = main.Type("ok")
    assert "Typed" in typed
