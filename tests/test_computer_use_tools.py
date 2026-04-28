import json

import pytest


def test_computer_use_task_tool_invokes_module(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "HAS_WIN32", True)

    def _fake_task(**kwargs):
        return {
            "success": True,
            "goal": kwargs["goal"],
            "session_id": kwargs.get("session_id") or "session_abc",
            "total_steps": kwargs.get("max_steps", 5),
            "attempted_steps": 1,
            "completed_steps": 1,
            "blocked": False,
            "stopped_reason": "success",
            "steps": [{"success": True, "step_index": 1}],
            "next_suggested_step": "next",
        }

    monkeypatch.setattr(main.computer_use, "computer_use_task", _fake_task)

    raw = main.ComputerUseTask(
        goal="Open Start menu",
        max_steps=3,
        step_queries_csv="Start,Search",
    )
    payload = json.loads(raw)

    assert payload["success"] is True
    assert payload["goal"] == "Open Start menu"
    assert payload["total_steps"] == 3
    assert payload["steps"][0]["step_index"] == 1
