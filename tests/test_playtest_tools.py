"""Tests for game-style input, assertions, and Roblox Studio helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pyautogui


def _call_tool(tool_name, **kwargs):
    from winremote.__main__ import _get_registered_tools

    tool = _get_registered_tools()[tool_name]
    return tool.fn(**kwargs)


def _parse_task_wrapped_json(result: str):
    if result.startswith("[task:"):
        _, _, result = result.partition("] ")
    return json.loads(result)


class TestHeldInputTools:
    def test_key_down_and_up(self):
        result = _call_tool("KeyDown", key="w")
        assert "Held key down" in result
        pyautogui.keyDown.assert_called_with("w")

        result = _call_tool("KeyUp", key="w")
        assert "Released key" in result
        pyautogui.keyUp.assert_called_with("w")

    def test_hold_keys(self):
        with patch("time.sleep") as _sleep:
            result = _call_tool("HoldKeys", keys="w+a", duration_seconds=1.5)
        assert "Held keys w, a" in result
        pyautogui.keyDown.assert_any_call("w")
        pyautogui.keyDown.assert_any_call("a")
        pyautogui.keyUp.assert_any_call("a")
        pyautogui.keyUp.assert_any_call("w")

    def test_mouse_down_up_and_relative_move(self):
        result = _call_tool("MouseDown", button="right", x=100, y=200)
        assert "Held mouse button down" in result
        pyautogui.moveTo.assert_called_with(100, 200)
        pyautogui.mouseDown.assert_called_with(button="right")

        result = _call_tool("MouseUp", button="right")
        assert "Released mouse button" in result
        pyautogui.mouseUp.assert_called_with(button="right")

        result = _call_tool("MouseMoveRelative", dx=25, dy=-10)
        assert "Moved mouse relatively" in result
        pyautogui.moveRel.assert_called()

    def test_mouse_look(self):
        result = _call_tool("MouseLook", dx=30, dy=-15, duration=0.3, steps=3)
        assert "Performed mouse look" in result
        assert pyautogui.moveRel.call_count == 3


class TestAssertionAndWaitTools:
    def test_wait_for_region_text(self):
        with patch("winremote.__main__.ocr.run_ocr", side_effect=["nothing", "Quest Complete"]):
            result = _call_tool(
                "WaitForRegionText",
                query="Quest Complete",
                left=0,
                top=0,
                right=100,
                bottom=50,
                timeout_seconds=1,
                poll_interval=0.01,
            )
        payload = _parse_task_wrapped_json(result)
        assert payload["satisfied"] is True
        assert payload["matched"] is True

    def test_wait_for_image_change(self):
        with patch("winremote.__main__.desktop.observe_screen") as mock_observe:
            mock_observe.side_effect = [
                {"target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "changed": None, "changed_regions": [], "ui_summary": None, "searchable_preview": [], "recommendations": []},
                {"target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "changed": True, "change_ratio": 0.2, "changed_regions": [{"left": 0, "top": 0, "right": 10, "bottom": 10}], "ui_summary": None, "searchable_preview": [], "recommendations": []},
                {"target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "changed": True, "change_ratio": 0.2, "changed_regions": [{"left": 0, "top": 0, "right": 10, "bottom": 10}], "ui_summary": None, "searchable_preview": [], "recommendations": []},
            ]
            result = _call_tool("WaitForImageChange", window_title="Roblox Studio", timeout_seconds=1, poll_interval=0.01)
        payload = _parse_task_wrapped_json(result)
        assert payload["satisfied"] is True
        assert payload["observation"]["changed"] is True

    def test_assert_process_running(self):
        with patch("winremote.__main__._process_matches", return_value=[{"pid": 1, "name": "RobloxStudioBeta.exe"}]):
            result = _call_tool("AssertProcessRunning", name="RobloxStudioBeta")
        payload = _parse_task_wrapped_json(result)
        assert payload["matched"] is True
        assert payload["count"] == 1

    def test_assert_window_active(self):
        with patch("winremote.__main__.desktop.HAS_WIN32", True), patch("winremote.__main__.desktop.win32gui.GetForegroundWindow", return_value=123), patch(
            "winremote.__main__.desktop.win32gui.GetWindowText",
            return_value="Roblox Studio",
        ):
            result = _call_tool("AssertWindowActive", title="Roblox")
        payload = _parse_task_wrapped_json(result)
        assert payload["matched"] is True

    def test_tail_file(self, tmp_path: Path):
        path = tmp_path / "studio.log"
        path.write_text("a\nb\nerror c\n", encoding="utf-8")
        result = _call_tool("TailFile", path=str(path), lines=2, contains="error")
        payload = _parse_task_wrapped_json(result)
        assert payload["line_count"] == 1
        assert "error c" in payload["text"]


class TestRobloxStudioTools:
    def test_run_and_stop_playtest(self):
        with patch("winremote.__main__.desktop.focus_window", return_value="Focused Roblox Studio"), patch(
            "winremote.__main__.desktop.observe_screen"
        ) as mock_observe, patch("winremote.__main__.roblox_studio.find_latest_studio_log", return_value=Path("C:/tmp/studio.log")):
            mock_observe.side_effect = [
                {"target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "changed": None, "changed_regions": [], "ui_summary": None, "searchable_preview": [], "recommendations": []},
                {"target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "changed": True, "change_ratio": 0.1, "changed_regions": [{"left": 0, "top": 0, "right": 10, "bottom": 10}], "ui_summary": None, "searchable_preview": [], "recommendations": []},
                {"target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "changed": True, "change_ratio": 0.1, "changed_regions": [{"left": 0, "top": 0, "right": 10, "bottom": 10}], "ui_summary": None, "searchable_preview": [], "recommendations": []},
                {"target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "changed": None, "changed_regions": [], "ui_summary": None, "searchable_preview": [], "recommendations": []},
                {"target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "changed": True, "change_ratio": 0.1, "changed_regions": [{"left": 0, "top": 0, "right": 10, "bottom": 10}], "ui_summary": None, "searchable_preview": [], "recommendations": []},
                {"target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "changed": True, "change_ratio": 0.1, "changed_regions": [{"left": 0, "top": 0, "right": 10, "bottom": 10}], "ui_summary": None, "searchable_preview": [], "recommendations": []},
            ]

            result = _call_tool("RobloxStudioRunPlaytest")
            payload = _parse_task_wrapped_json(result)
            assert payload["status"] == "completed"
            pyautogui.hotkey.assert_called_with("f5")

            result = _call_tool("RobloxStudioStopPlaytest")
            payload = _parse_task_wrapped_json(result)
            assert payload["status"] == "completed"

    def test_get_output_and_errors(self):
        with patch("winremote.__main__.roblox_studio.read_latest_studio_log", return_value={"path": "x", "text": "hello"}):
            result = _call_tool("RobloxStudioGetOutput", lines=20)
        payload = _parse_task_wrapped_json(result)
        assert payload["text"] == "hello"

        with patch("winremote.__main__.roblox_studio.read_latest_studio_errors", return_value={"path": "x", "text": "error"}):
            result = _call_tool("RobloxStudioGetErrors", lines=20)
        payload = _parse_task_wrapped_json(result)
        assert payload["text"] == "error"

    def test_harness_backed_tools(self):
        with patch("winremote.__main__.roblox_studio.harness_request", return_value={"ok": True, "data": {"state": "ok"}}):
            result = _call_tool("RobloxStudioGetTestState")
            payload = _parse_task_wrapped_json(result)
            assert payload["ok"] is True

            result = _call_tool("RobloxStudioResetCharacter")
            payload = _parse_task_wrapped_json(result)
            assert payload["ok"] is True

            result = _call_tool("RobloxStudioTeleportToCheckpoint", checkpoint_id="boss")
            payload = _parse_task_wrapped_json(result)
            assert payload["ok"] is True

            result = _call_tool("RobloxStudioRunNamedTest", test_name="Smoke")
            payload = _parse_task_wrapped_json(result)
            assert payload["ok"] is True

    def test_harness_actions_wait_for_completion(self):
        with patch("winremote.__main__.roblox_studio.harness_request", return_value={"ok": True}) as mock_harness:
            _call_tool("RobloxStudioResetCharacter", timeout_seconds=7)
            _call_tool("RobloxStudioTeleportToCheckpoint", checkpoint_id="boss", timeout_seconds=8)
            _call_tool("RobloxStudioRunNamedTest", test_name="Smoke", timeout_seconds=9)

        reset_call = mock_harness.call_args_list[0]
        teleport_call = mock_harness.call_args_list[1]
        test_call = mock_harness.call_args_list[2]

        assert reset_call.kwargs["payload"]["wait"] is True
        assert reset_call.kwargs["payload"]["timeout_seconds"] == 7
        assert teleport_call.kwargs["payload"]["checkpoint_id"] == "boss"
        assert teleport_call.kwargs["payload"]["wait"] is True
        assert test_call.kwargs["payload"]["test_name"] == "Smoke"
        assert test_call.kwargs["payload"]["wait"] is True
