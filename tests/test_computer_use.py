from pathlib import Path

import pytest

import winremote.computer_use as computer_use
from winremote.session_manager import SessionManager


@pytest.fixture
def fake_desktop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        computer_use.desktop,
        "observe_screen",
        lambda **kwargs: {"changed": True, "change_ratio": 0.2, "target": {"mode": "window"}},
    )
    monkeypatch.setattr(
        computer_use.desktop,
        "find_ui_elements_with_context",
        lambda **kwargs: {
            "window": {"label": "TestApp", "process_name": "TestApp.exe"},
            "matches": [
                {
                    "label": "Save",
                    "class": "Button",
                    "center": {"x": 100, "y": 200},
                    "match": {"score": 95},
                }
            ],
        },
    )
    monkeypatch.setattr(computer_use.desktop, "validate_screen_point", lambda x, y: None)


@pytest.fixture
def fake_pyautogui(monkeypatch: pytest.MonkeyPatch):
    calls = {"click": 0, "typewrite": 0, "write": 0}

    def _click(*args, **kwargs):
        calls["click"] += 1

    def _typewrite(*args, **kwargs):
        calls["typewrite"] += 1

    def _write(*args, **kwargs):
        calls["write"] += 1

    monkeypatch.setattr(computer_use.pyautogui, "click", _click)
    monkeypatch.setattr(computer_use.pyautogui, "typewrite", _typewrite)
    monkeypatch.setattr(computer_use.pyautogui, "write", _write)
    return calls


def test_computer_use_step_blocks_risky_action(fake_desktop, fake_pyautogui):
    computer_use.desktop.find_ui_elements_with_context = lambda **kwargs: {
        "window": {"label": "TestApp", "process_name": "TestApp.exe"},
        "matches": [
            {
                "label": "Delete Account",
                "class": "Button",
                "center": {"x": 100, "y": 200},
                "match": {"score": 95},
            }
        ],
    }
    result = computer_use.computer_use_step(
        goal="Delete account",
        target_query="Delete Account",
        action="click",
        confirm_risky=False,
        dry_run=False,
    )
    assert result["success"] is False
    assert result["blocked"] is True
    assert result["confirmation_required"] is True
    assert fake_pyautogui["click"] == 0


def test_computer_use_step_dry_run(fake_desktop, fake_pyautogui):
    result = computer_use.computer_use_step(
        goal="Click Save",
        target_query="Save",
        action="click",
        dry_run=True,
    )
    assert result["success"] is True
    assert result["action_taken"] == "dry_run:click"
    assert result["verification_result"]["dry_run"] is True
    assert fake_pyautogui["click"] == 0


def test_computer_use_step_executes_and_records(fake_desktop, fake_pyautogui, tmp_path: Path):
    manager = SessionManager(tmp_path)
    session = manager.create_session("computer_use_test")

    result = computer_use.computer_use_step(
        goal="Click Save",
        target_query="Save",
        action="click",
        dry_run=False,
        confirm_risky=True,
        session_id=session.session_id,
        session_manager=manager,
    )

    assert result["success"] is True
    assert result["action_taken"] == "clicked"
    assert result["session_id"] == session.session_id
    assert fake_pyautogui["click"] == 1

    lines = (session.path / "actions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "computer_use_step" in lines[0]


def test_computer_use_step_no_match(monkeypatch: pytest.MonkeyPatch, fake_pyautogui):
    monkeypatch.setattr(computer_use.desktop, "observe_screen", lambda **kwargs: {"changed": False})
    monkeypatch.setattr(
        computer_use.desktop,
        "find_ui_elements_with_context",
        lambda **kwargs: {"window": None, "matches": []},
    )

    result = computer_use.computer_use_step(goal="Find Unknown", target_query="Unknown")
    assert result["success"] is False
    assert result["strategy"] == "none"
    assert result["error"] == "No matching UI target found"


def test_computer_use_task_stops_on_first_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls = {"count": 0}

    def _step(**kwargs):
        calls["count"] += 1
        return {
            "success": True,
            "blocked": False,
            "verification_result": {"changed": True},
            "next_suggested_step": "next",
        }

    monkeypatch.setattr(computer_use, "computer_use_step", _step)

    manager = SessionManager(tmp_path)
    result = computer_use.computer_use_task(
        goal="Open settings",
        max_steps=4,
        stop_on_first_success=True,
        session_manager=manager,
    )

    assert result["success"] is True
    assert result["attempted_steps"] == 1
    assert result["completed_steps"] == 1
    assert result["stopped_reason"] == "success"


def test_computer_use_task_stops_on_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def _step(**kwargs):
        return {
            "success": False,
            "blocked": True,
            "verification_result": {"changed": False},
            "next_suggested_step": "confirm",
        }

    monkeypatch.setattr(computer_use, "computer_use_step", _step)

    manager = SessionManager(tmp_path)
    result = computer_use.computer_use_task(
        goal="Delete item",
        max_steps=3,
        max_failures=2,
        session_manager=manager,
    )

    assert result["success"] is False
    assert result["blocked"] is True
    assert result["stopped_reason"] == "blocked"
    assert result["attempted_steps"] == 1


def test_computer_use_task_stops_on_max_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls = {"count": 0}

    def _step(**kwargs):
        calls["count"] += 1
        return {
            "success": False,
            "blocked": False,
            "verification_result": {"changed": False},
            "next_suggested_step": "retry",
        }

    monkeypatch.setattr(computer_use, "computer_use_step", _step)

    manager = SessionManager(tmp_path)
    result = computer_use.computer_use_task(
        goal="Find panel",
        max_steps=5,
        max_failures=2,
        session_manager=manager,
    )

    assert result["success"] is False
    assert result["blocked"] is False
    assert result["stopped_reason"] == "max_failures_reached"
    assert result["attempted_steps"] == 2
