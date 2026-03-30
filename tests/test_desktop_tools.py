"""Unit tests for desktop control tools (Click, Type, Scroll, Move, Shortcut, Wait, etc.)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pyautogui
from PIL import Image
from winremote import desktop

# The MCP tools are wrapped by task_manager; access the original functions
# via the module-level function objects before they're decorated, or call .fn
# Actually, the functions in __main__ are replaced by FunctionTool after @mcp.tool.
# We need to access the underlying function. Let's import and call the wrapped fn.


def _call_tool(tool_name, **kwargs):
    """Call an MCP tool by name, going through the task-manager-wrapped fn."""
    from winremote.__main__ import _get_registered_tools

    tool = _get_registered_tools()[tool_name]
    return tool.fn(**kwargs)


def _parse_task_wrapped_json(result: str):
    """Strip task wrapper prefix and parse JSON payload."""
    if result.startswith("[task:"):
        _, _, result = result.partition("] ")
    return json.loads(result)


def _mock_monitor_context(mock_desktop):
    monitor_info = [
        {
            "monitor_id": 1,
            "primary": True,
            "rect": {"left": 0, "top": 0, "right": 1920, "bottom": 1080},
            "size": {"width": 1920, "height": 1080},
            "scale": 1.0,
        }
    ]
    virtual_screen = {"left": 0, "top": 0, "right": 1920, "bottom": 1080, "width": 1920, "height": 1080}
    mock_desktop.get_monitor_info.return_value = monitor_info
    mock_desktop.get_virtual_screen_bounds.return_value = virtual_screen
    mock_desktop.validate_screen_point.return_value = monitor_info[0]


class TestClick:
    def test_left_click(self):
        result = _call_tool("Click", x=100, y=200)
        assert "Clicked left at (100,200)" in result

    def test_right_click(self):
        result = _call_tool("Click", x=50, y=60, button="right")
        assert "right" in result

    def test_double_click(self):
        result = _call_tool("Click", x=10, y=20, action="double")
        assert "Double-clicked" in result

    def test_hover(self):
        result = _call_tool("Click", x=300, y=400, action="hover")
        assert "Hovered" in result

    def test_click_error(self):
        pyautogui.click.side_effect = Exception("display error")
        result = _call_tool("Click", x=0, y=0)
        assert "error" in result.lower()
        pyautogui.click.side_effect = None

    def test_click_out_of_bounds(self):
        with patch("winremote.__main__.desktop.validate_screen_point", side_effect=ValueError("outside the virtual screen bounds")):
            result = _call_tool("Click", x=99999, y=99999)
            assert "outside the virtual screen bounds" in result


class TestType:
    def test_basic_type(self):
        result = _call_tool("Type", text="hello")
        assert "Typed 5 chars" in result

    def test_type_at_coords(self):
        _call_tool("Type", text="abc", x=100, y=200)
        pyautogui.click.assert_called_with(100, 200)

    def test_type_with_clear(self):
        _call_tool("Type", text="new", clear=True)
        pyautogui.hotkey.assert_called_with("ctrl", "a")

    def test_type_with_enter(self):
        _call_tool("Type", text="cmd", press_enter=True)
        pyautogui.press.assert_called_with("enter")

    def test_type_unicode(self):
        result = _call_tool("Type", text="你好")
        assert "Typed 2 chars" in result


class TestScroll:
    def test_vertical_scroll(self):
        result = _call_tool("Scroll", amount=3)
        assert "vertically" in result

    def test_horizontal_scroll(self):
        result = _call_tool("Scroll", amount=-2, horizontal=True)
        assert "horizontally" in result

    def test_scroll_at_position(self):
        _call_tool("Scroll", amount=5, x=100, y=200)
        pyautogui.moveTo.assert_called_with(100, 200)


class TestMove:
    def test_move(self):
        result = _call_tool("Move", x=500, y=600)
        assert "Moved to (500,600)" in result

    def test_drag(self):
        result = _call_tool("Move", x=700, y=800, drag=True, start_x=100, start_y=100)
        assert "Dragged" in result


class TestShortcut:
    def test_shortcut(self):
        result = _call_tool("Shortcut", keys="ctrl+c")
        assert "Executed shortcut" in result

    def test_complex_shortcut(self):
        _call_tool("Shortcut", keys="ctrl+shift+s")
        pyautogui.hotkey.assert_called_with("ctrl", "shift", "s")


class TestWait:
    def test_wait(self):
        with patch("time.sleep") as _mock_sleep:
            result = _call_tool("Wait", seconds=0.01)
            assert "Waited" in result


class TestMinimizeAll:
    def test_minimize_all(self):
        result = _call_tool("MinimizeAll")
        assert "Minimized" in result or "task:" in result


class TestFocusWindow:
    def test_no_win32(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = False
            result = _call_tool("FocusWindow", title="notepad")
            assert "pywin32" in result or "Error" in result

    def test_with_title(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            mock_desktop.focus_window.return_value = "Focused window"
            result = _call_tool("FocusWindow", title="notepad")
            assert "Focused" in result or "task:" in result


class TestReconnectSession:
    def test_session_found_and_reconnected(self):
        with patch("winremote.__main__._ensure_session_connected") as mock_ensure:
            mock_ensure.return_value = None  # Success

            result = _call_tool("ReconnectSession")

            assert isinstance(result, list)
            assert len(result) == 1
            assert "connected to console" in result[0].text.lower()

    def test_session_already_connected(self):
        with patch("winremote.__main__._ensure_session_connected") as mock_ensure:
            mock_ensure.return_value = None  # Already connected

            result = _call_tool("ReconnectSession")

            assert isinstance(result, list)
            assert len(result) == 1
            assert "connected to console" in result[0].text.lower()

    def test_reconnect_failed(self):
        with patch("winremote.__main__._ensure_session_connected") as mock_ensure:
            mock_ensure.return_value = "Access denied"

            result = _call_tool("ReconnectSession")

            assert isinstance(result, list)
            assert len(result) == 1
            assert "failed" in result[0].text.lower()
            assert "access denied" in result[0].text.lower()

    def test_force_reconnect(self):
        from unittest.mock import MagicMock

        mock_result_query = MagicMock()
        mock_result_query.returncode = 0
        mock_result_query.stdout = """SESSIONNAME       USERNAME                 ID  STATE   TYPE        DEVICE
 console                                    0  Conn    wdcon
 rdp-tcp#0         testuser                 1  Active  rdpwd
"""

        mock_result_tscon = MagicMock()
        mock_result_tscon.returncode = 0

        with patch("winremote.__main__.subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [mock_result_query, mock_result_tscon]

            result = _call_tool("ReconnectSession", force=True)

            assert isinstance(result, list)
            assert len(result) == 1
            assert "connected to console" in result[0].text.lower()


class TestSnapshotAutoReconnect:
    def test_snapshot_screenshot_fails_then_succeeds_after_reconnect(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            # First call fails, second succeeds
            _mock_monitor_context(mock_desktop)
            mock_desktop.take_screenshot.side_effect = [Exception("screen grab failed"), "base64data"]
            mock_desktop.enumerate_windows.return_value = []
            mock_desktop.get_interactive_elements.return_value = []
            mock_desktop._get_system_language.return_value = "en-US"

            with patch("winremote.__main__._ensure_session_connected") as mock_ensure:
                mock_ensure.return_value = None  # Success

                with patch("winremote.__main__.time.sleep"):
                    result = _call_tool("Snapshot")

                    # Should succeed after reconnect
                    assert isinstance(result, list)
                    # Should have called ensure_session_connected
                    mock_ensure.assert_called_once()
                    # Should have retried screenshot
                    assert mock_desktop.take_screenshot.call_count == 2

    def test_snapshot_non_screen_error_not_retried(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            # Non-screen-related error should not trigger reconnect
            _mock_monitor_context(mock_desktop)
            mock_desktop.take_screenshot.side_effect = Exception("some other error")

            result = _call_tool("Snapshot")

            assert isinstance(result, list)
            text_parts = [x for x in result if isinstance(x, str)]
            assert text_parts
            assert "error" in text_parts[0].lower()

    def test_snapshot_reconnect_fails(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            _mock_monitor_context(mock_desktop)
            mock_desktop.take_screenshot.side_effect = Exception("screen grab failed")

            with patch("winremote.__main__._ensure_session_connected") as mock_ensure:
                mock_ensure.return_value = "Failed to reconnect"

                result = _call_tool("Snapshot")

                assert isinstance(result, list)
                text_parts = [x for x in result if isinstance(x, str)]
                assert text_parts
                assert "error" in text_parts[0].lower()


class TestUIMap:
    def test_no_win32(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = False
            result = _call_tool("UIMap")
            assert "pywin32" in result or "Error" in result

    def test_empty_map(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.map_ui_elements.return_value = []
            result = _call_tool("UIMap")
            assert "No UI elements detected" in result

    def test_successful_map(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.map_ui_elements.return_value = [
                {
                    "index": 0,
                    "label": "Roblox Studio",
                    "monitor_id": 1,
                    "process_name": "RobloxStudioBeta.exe",
                    "pid": 1234,
                    "rect": {"left": 10, "top": 20, "right": 510, "bottom": 420},
                },
                {
                    "index": 1,
                    "label": "Inventory",
                    "element_id": "abc123",
                    "class": "Button",
                    "monitor_id": 1,
                    "center": {"x": 100, "y": 180},
                    "rect": {"left": 60, "top": 160, "right": 140, "bottom": 200},
                    "ocr_text": "Inventory",
                },
            ]
            result = _call_tool("UIMap", window_title="Roblox")
            assert "Target window: Roblox Studio" in result
            assert "Inventory" in result
            assert "center=(100,180)" in result


class TestUIMapJson:
    def test_successful_map_json(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.summarize_ui_map.return_value = {"control_count": 1, "notes": []}
            mock_desktop.map_ui_elements.return_value = [
                {
                    "index": 0,
                    "label": "Roblox Studio",
                    "monitor_id": 1,
                    "rect": {"left": 10, "top": 20, "right": 510, "bottom": 420},
                },
                {
                    "index": 1,
                    "label": "Inventory",
                    "element_id": "abc123",
                    "class": "Button",
                    "monitor_id": 1,
                    "center": {"x": 100, "y": 180},
                    "rect": {"left": 60, "top": 160, "right": 140, "bottom": 200},
                },
            ]
            result = _call_tool("UIMapJson", window_title="Roblox")
            payload = _parse_task_wrapped_json(result)
            assert payload["requested_window_title"] == "Roblox"
            assert payload["window"]["label"] == "Roblox Studio"
            assert payload["monitors"][0]["monitor_id"] == 1
            assert "coordinate_spaces" in payload
            assert payload["summary"]["control_count"] == 1
            assert payload["count"] == 1
            assert payload["controls"][0]["label"] == "Inventory"


class TestUIFind:
    def test_returns_structured_matches(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.return_value = {
                "matches": [
                    {
                        "index": 1,
                        "label": "Inventory",
                        "class": "Button",
                        "monitor_id": 1,
                        "center": {"x": 100, "y": 180},
                        "rect": {"left": 60, "top": 160, "right": 140, "bottom": 200},
                        "match": {"score": 100, "type": "exact", "field": "label", "value": "Inventory", "confidence": "high", "reason": "exact on label"},
                    }
                ],
                "searched_element_count": 2,
                "searchable_preview": [{"index": 1, "label": "Inventory", "class": "Button"}],
                "summary": {"control_count": 1, "notes": []},
                "recommendations": [],
            }
            result = _call_tool("UIFind", query="Inventory")
            payload = _parse_task_wrapped_json(result)
            assert payload["query"] == "Inventory"
            assert payload["count"] == 1
            assert payload["target"]["mode"] == "window"
            assert payload["search"]["searched_element_count"] == 2
            assert payload["searched_element_count"] == 2
            assert payload["monitors"][0]["monitor_id"] == 1
            assert payload["summary"]["control_count"] == 1
            assert payload["matches"][0]["match"]["score"] == 100


class TestUIClick:
    def test_clicks_best_match(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.return_value = {
                "matches": [
                    {
                        "index": 1,
                        "label": "Inventory",
                        "element_id": "abc123",
                        "class": "Button",
                        "monitor_id": 1,
                        "center": {"x": 100, "y": 180},
                        "rect": {"left": 60, "top": 160, "right": 140, "bottom": 200},
                        "match": {"score": 100, "type": "exact", "field": "label", "value": "Inventory"},
                    }
                ],
                "recommendations": [],
            }
            result = _call_tool("UIClick", query="Inventory")
            pyautogui.click.assert_called_with(100, 180, button="left")
            assert "Inventory" in result
            assert "(100,180)" in result

    def test_no_match(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.return_value = {
                "matches": [],
                "recommendations": ["Try a broader query or inspect summary.searchable_preview."],
            }
            result = _call_tool("UIClick", query="Missing")
            assert "No UI element matched 'Missing'" in result


class TestDesktopFindUIElementsWithContext:
    def test_window_title_can_match_window(self):
        mapped = [
            {
                "index": 0,
                "type": "window",
                "label": "Windows PowerShell",
                "window_text": "Windows PowerShell",
                "class": "Window",
                "monitor_id": 1,
                "center": {"x": 960, "y": 576},
                "relative_center": {"x": 960, "y": 576},
                "element_id": "window-1",
            },
            {
                "index": 1,
                "type": "control",
                "label": "DesktopWindowXamlSource",
                "window_text": "DesktopWindowXamlSource",
                "class": "Windows.UI.Composition.DesktopWindowContentBridge",
                "monitor_id": 1,
                "center": {"x": 960, "y": 575},
                "relative_center": {"x": 960, "y": 575},
                "element_id": "control-1",
            },
        ]
        with patch("winremote.desktop.map_ui_elements", return_value=mapped):
            result = desktop.find_ui_elements_with_context(query="Windows PowerShell", window_title="Windows PowerShell")

        assert result["matches"]
        assert result["matches"][0]["type"] == "window"
        assert result["matches"][0]["match"]["type"] == "exact"
        assert result["summary"]["control_count"] == 1


class TestUIWatch:
    def test_baseline_created(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.watch_ui_elements.return_value = {
                "window_title": "Roblox Studio",
                "window": {"label": "Roblox Studio", "monitor_id": 1, "rect": {"left": 0, "top": 0, "right": 500, "bottom": 400}},
                "baseline_created": True,
                "baseline_reset": False,
                "previous_count": 0,
                "current_count": 3,
                "diff": {"added": [], "removed": [], "moved": [], "text_changed": [], "summary": {"added": 0, "removed": 0, "moved": 0, "text_changed": 0}},
                "summary": {"control_count": 3, "notes": []},
                "searchable_preview": [{"label": "Inventory"}],
                "recommendations": ["Baseline created."],
            }
            result = _call_tool("UIWatch", window_title="Roblox Studio")
            payload = _parse_task_wrapped_json(result)
            assert payload["baseline_created"] is True
            assert payload["target"]["title"] == "Roblox Studio"
            assert payload["search"]["searchable_preview"][0]["label"] == "Inventory"
            assert payload["monitors"][0]["monitor_id"] == 1
            assert payload["current_count"] == 3

    def test_diff_reported(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.watch_ui_elements.return_value = {
                "window_title": "Roblox Studio",
                "baseline_created": False,
                "baseline_reset": False,
                "previous_count": 3,
                "current_count": 4,
                "diff": {
                    "added": [{"label": "Recent", "class": "Button"}],
                    "removed": [],
                    "moved": [{"label": "Inventory", "class": "Button"}],
                    "text_changed": [],
                    "summary": {"added": 1, "removed": 0, "moved": 1, "text_changed": 0},
                },
            }
            result = _call_tool("UIWatch", window_title="Roblox Studio")
            payload = _parse_task_wrapped_json(result)
            assert payload["baseline_created"] is False
            assert payload["diff"]["summary"]["added"] == 1
            assert payload["diff"]["summary"]["moved"] == 1


class TestObserveScreen:
    def test_returns_structured_json(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            mock_desktop.normalize_region.return_value = (10, 20, 210, 220)
            mock_desktop.observe_screen.return_value = {
                "target": {
                    "mode": "window",
                    "window": {"title": "Roblox Studio"},
                    "bounds": {"left": 10, "top": 20, "right": 210, "bottom": 220, "width": 200, "height": 200},
                    "captured_monitors": [1],
                },
                "baseline_created": True,
                "baseline_reset": False,
                "changed": None,
                "change_ratio": None,
                "changed_tiles": 0,
                "changed_regions": [],
                "screen_digest": "abc123",
                "grid": {"columns": 6, "rows": 6},
                "sample_size": {"width": 96, "height": 96},
                "window_count": 1,
                "windows": [{"title": "Roblox Studio"}],
                "ui_scope": "target_window",
                "ui_summary": {"control_count": 1, "notes": [], "searchable_preview": [{"label": "Inventory"}]},
                "searchable_preview": [{"label": "Inventory"}],
                "monitors": [{"monitor_id": 1}],
                "virtual_screen": {"left": 0, "top": 0, "right": 1920, "bottom": 1080, "width": 1920, "height": 1080},
                "recommendations": ["Baseline created."],
            }

            result = _call_tool("ObserveScreen", window_title="Roblox Studio", include_text=True)

            payload = _parse_task_wrapped_json(result)
            assert payload["target"]["mode"] == "window"
            assert payload["baseline_created"] is True
            assert payload["search"]["searched_element_count"] == 1
            assert payload["screen_digest"] == "abc123"
            assert payload["coordinate_spaces"]["center"].startswith("Absolute")

    def test_supports_region_observation(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            mock_desktop.normalize_region.return_value = (0, 0, 100, 100)
            mock_desktop.observe_screen.return_value = {
                "target": {
                    "mode": "region",
                    "window": None,
                    "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100, "width": 100, "height": 100},
                    "captured_monitors": [1],
                },
                "baseline_created": False,
                "baseline_reset": False,
                "changed": True,
                "change_ratio": 0.25,
                "changed_tiles": 4,
                "changed_regions": [{"left": 0, "top": 0, "right": 50, "bottom": 50, "width": 50, "height": 50, "tile_count": 4}],
                "screen_digest": "def456",
                "grid": {"columns": 4, "rows": 4},
                "sample_size": {"width": 64, "height": 64},
                "window_count": 0,
                "windows": [],
                "ui_scope": None,
                "ui_summary": None,
                "searchable_preview": [],
                "monitors": [{"monitor_id": 1}],
                "virtual_screen": {"left": 0, "top": 0, "right": 1920, "bottom": 1080, "width": 1920, "height": 1080},
                "recommendations": ["Screen change detected."],
            }

            result = _call_tool("ObserveScreen", right=100, bottom=100, grid_size=4)

            payload = _parse_task_wrapped_json(result)
            assert payload["target"]["mode"] == "region"
            assert payload["changed"] is True
            assert payload["changed_regions"][0]["tile_count"] == 4


class TestUIAct:
    def test_clicks_and_reports_change(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.return_value = {
                "window": {"label": "Roblox Studio"},
                "matches": [
                    {
                        "label": "Inventory",
                        "class": "Button",
                        "element_id": "abc123",
                        "monitor_id": 1,
                        "center": {"x": 100, "y": 180},
                        "relative_center": {"x": 50, "y": 80},
                        "rect": {"left": 60, "top": 160, "right": 140, "bottom": 200},
                        "match": {"score": 100, "type": "exact", "field": "label", "value": "Inventory"},
                    }
                ],
                "searched_element_count": 2,
                "searchable_preview": [{"label": "Inventory"}],
                "summary": {"control_count": 1, "notes": []},
                "recommendations": [],
            }
            mock_desktop.validate_screen_point.return_value = {"monitor_id": 1}
            mock_desktop.focus_window.return_value = "Focused window"
            mock_desktop.observe_screen.side_effect = [
                {"baseline_created": True, "changed": None, "changed_regions": []},
                {"baseline_created": False, "changed": True, "changed_regions": [{"left": 0, "top": 0, "right": 50, "bottom": 50}]},
                {"baseline_created": False, "changed": True, "changed_regions": [{"left": 0, "top": 0, "right": 50, "bottom": 50}]},
            ]

            result = _call_tool("UIAct", query="Inventory")

            payload = _parse_task_wrapped_json(result)
            pyautogui.moveTo.assert_called_with(100, 180)
            pyautogui.click.assert_called_with(100, 180, button="left")
            assert payload["status"] == "completed"
            assert payload["target"]["label"] == "Inventory"
            assert payload["search"]["searched_element_count"] == 2
            assert payload["observation_after"]["changed"] is True

    def test_type_action_types_text(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.return_value = {
                "window": {"label": "Notepad"},
                "matches": [
                    {
                        "label": "Editor",
                        "class": "Edit",
                        "element_id": "edit1",
                        "monitor_id": 1,
                        "center": {"x": 20, "y": 30},
                        "relative_center": {"x": 10, "y": 10},
                        "rect": {"left": 0, "top": 0, "right": 40, "bottom": 60},
                        "match": {"score": 96, "type": "contains", "field": "label", "value": "Editor"},
                    }
                ],
                "searched_element_count": 1,
                "searchable_preview": [{"label": "Editor"}],
                "summary": {"control_count": 1, "notes": []},
                "recommendations": [],
            }
            mock_desktop.validate_screen_point.return_value = {"monitor_id": 1}
            mock_desktop.focus_window.return_value = "Focused window"
            mock_desktop.observe_screen.return_value = {"baseline_created": True, "changed": None, "changed_regions": []}

            result = _call_tool("UIAct", query="Editor", action="type", text="hello", clear=True, press_enter=True, wait_for_change=False)

            payload = _parse_task_wrapped_json(result)
            pyautogui.click.assert_called_with(20, 30, button="left")
            pyautogui.hotkey.assert_called_with("ctrl", "a")
            pyautogui.press.assert_called_with("enter")
            assert payload["interaction"].startswith("Typed 5 chars")

    def test_no_match_returns_structured_payload(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.return_value = {
                "matches": [],
                "searched_element_count": 3,
                "searchable_preview": [{"label": "Inventory"}],
                "summary": {"control_count": 3, "notes": []},
                "recommendations": ["Try a broader query."],
            }

            result = _call_tool("UIAct", query="Missing")

            payload = _parse_task_wrapped_json(result)
            assert payload["status"] == "no-match"
            assert payload["search"]["searched_element_count"] == 3
            assert payload["recommendations"] == ["Try a broader query."]

    def test_waits_for_semantic_query_after_action(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.side_effect = [
                {
                    "window": {"label": "Roblox Studio"},
                    "matches": [
                        {
                            "label": "Inventory",
                            "class": "Button",
                            "element_id": "abc123",
                            "monitor_id": 1,
                            "center": {"x": 100, "y": 180},
                            "relative_center": {"x": 50, "y": 80},
                            "rect": {"left": 60, "top": 160, "right": 140, "bottom": 200},
                            "match": {"score": 100, "type": "exact", "field": "label", "value": "Inventory"},
                        }
                    ],
                    "searched_element_count": 2,
                    "searchable_preview": [{"label": "Inventory"}],
                    "summary": {"control_count": 1, "notes": []},
                    "recommendations": [],
                },
                {
                    "matches": [],
                    "searched_element_count": 3,
                    "searchable_preview": [{"label": "Loading"}],
                    "summary": {"control_count": 3, "notes": []},
                    "recommendations": [],
                },
                {
                    "matches": [
                        {
                            "label": "Save Complete",
                            "class": "Text",
                            "element_id": "done1",
                            "match": {"score": 99, "type": "contains", "field": "label", "value": "Save Complete"},
                        }
                    ],
                    "searched_element_count": 4,
                    "searchable_preview": [{"label": "Save Complete"}],
                    "summary": {"control_count": 4, "notes": []},
                    "recommendations": [],
                },
            ]
            mock_desktop.validate_screen_point.return_value = {"monitor_id": 1}
            mock_desktop.focus_window.return_value = "Focused window"
            mock_desktop.observe_screen.side_effect = [
                {"baseline_created": True, "changed": None, "changed_regions": []},
                {"baseline_created": False, "changed": True, "changed_regions": [{"left": 0, "top": 0, "right": 50, "bottom": 50}]},
                {"baseline_created": False, "changed": True, "changed_regions": [{"left": 0, "top": 0, "right": 50, "bottom": 50}]},
            ]

            result = _call_tool(
                "UIAct",
                query="Inventory",
                wait_for_query="Save Complete",
                wait_until="appear",
                timeout_seconds=1,
            )

            payload = _parse_task_wrapped_json(result)
            assert payload["status"] == "completed"
            assert payload["wait_condition"]["satisfied"] is True
            assert payload["wait_condition"]["target"]["label"] == "Save Complete"


class TestUISequence:
    def test_runs_compact_multistep_flow(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.return_value = {
                "window": {"label": "Roblox Studio"},
                "matches": [
                    {
                        "label": "Inventory",
                        "class": "Button",
                        "element_id": "abc123",
                        "monitor_id": 1,
                        "center": {"x": 100, "y": 180},
                        "relative_center": {"x": 50, "y": 80},
                        "rect": {"left": 60, "top": 160, "right": 140, "bottom": 200},
                        "match": {"score": 100, "type": "exact", "field": "label", "value": "Inventory"},
                    }
                ],
                "searched_element_count": 2,
                "searchable_preview": [{"label": "Inventory"}],
                "summary": {"control_count": 1, "notes": []},
                "recommendations": [],
            }
            mock_desktop.validate_screen_point.return_value = {"monitor_id": 1}
            mock_desktop.focus_window.return_value = "Focused window"
            mock_desktop.observe_screen.side_effect = [
                {"baseline_created": True, "changed": None, "changed_regions": [], "target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "searchable_preview": [], "recommendations": []},
                {"baseline_created": False, "changed": True, "change_ratio": 0.2, "changed_regions": [{"left": 0, "top": 0, "right": 50, "bottom": 50}], "target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "searchable_preview": [], "recommendations": []},
                {"baseline_created": False, "changed": True, "change_ratio": 0.2, "changed_regions": [{"left": 0, "top": 0, "right": 50, "bottom": 50}], "target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "searchable_preview": [], "recommendations": []},
                {"baseline_created": False, "changed": False, "change_ratio": 0.0, "changed_regions": [], "target": {"mode": "window", "window": {"title": "Roblox Studio"}}, "searchable_preview": [{"label": "Inventory"}], "recommendations": []},
            ]

            result = _call_tool(
                "UISequence",
                steps_json='[{"action":"click","query":"Inventory"},{"action":"observe","update_baseline":false}]',
            )

            payload = _parse_task_wrapped_json(result)
            assert payload["executed_steps"] == 2
            assert payload["results"][0]["result"]["target"]["label"] == "Inventory"
            assert payload["results"][0]["result"]["search"]["searched_element_count"] == 2
            assert payload["results"][0]["result"]["observation_after"]["changed"] is True
            assert payload["results"][1]["result"]["changed"] is False

    def test_stops_on_no_match_by_default(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.return_value = {
                "matches": [],
                "searched_element_count": 3,
                "searchable_preview": [{"label": "Inventory"}],
                "summary": {"control_count": 3, "notes": []},
                "recommendations": ["Try a broader query."],
            }

            result = _call_tool(
                "UISequence",
                steps_json='[{"action":"click","query":"Missing"},{"action":"wait","seconds":0.1}]',
            )

            payload = _parse_task_wrapped_json(result)
            assert payload["executed_steps"] == 1
            assert payload["results"][0]["status"] == "no-match"

    def test_waitfor_step_reports_semantic_wait_result(self):
        with patch("winremote.__main__.desktop") as mock_desktop:
            mock_desktop.HAS_WIN32 = True
            _mock_monitor_context(mock_desktop)
            mock_desktop.find_ui_elements_with_context.side_effect = [
                {
                    "matches": [],
                    "searched_element_count": 3,
                    "searchable_preview": [{"label": "Loading"}],
                    "summary": {"control_count": 3, "notes": []},
                    "recommendations": [],
                },
                {
                    "matches": [
                        {
                            "label": "Save Complete",
                            "class": "Text",
                            "element_id": "done1",
                            "match": {"score": 99, "type": "contains", "field": "label", "value": "Save Complete"},
                        }
                    ],
                    "searched_element_count": 4,
                    "searchable_preview": [{"label": "Save Complete"}],
                    "summary": {"control_count": 4, "notes": []},
                    "recommendations": [],
                },
            ]
            mock_desktop.observe_screen.side_effect = [
                {"baseline_created": False, "changed": True, "changed_regions": [{"left": 0, "top": 0, "right": 50, "bottom": 50}]},
                {"baseline_created": False, "changed": True, "changed_regions": [{"left": 0, "top": 0, "right": 50, "bottom": 50}]},
            ]

            result = _call_tool(
                "UISequence",
                steps_json='[{"action":"waitfor","query":"Save Complete","timeout_seconds":1}]',
            )

            payload = _parse_task_wrapped_json(result)
            assert payload["executed_steps"] == 1
            assert payload["results"][0]["action"] == "waitfor"
            assert payload["results"][0]["result"]["satisfied"] is True
            assert payload["results"][0]["result"]["target"]["label"] == "Save Complete"


class TestDesktopObserveScreen:
    def test_first_observation_creates_baseline(self):
        desktop._SCREEN_OBSERVE_BASELINES.clear()
        desktop._UI_MAP_CACHE.clear()
        metadata = {
            "bounds": {"left": 0, "top": 0, "right": 60, "bottom": 60, "width": 60, "height": 60},
            "captured_monitors": [1],
            "monitors": [{"monitor_id": 1}],
            "virtual_screen": {"left": 0, "top": 0, "right": 60, "bottom": 60, "width": 60, "height": 60},
        }
        mapped = [
            {
                "index": 0,
                "type": "window",
                "label": "Demo",
                "window_text": "Demo",
                "class": "Window",
                "monitor_id": 1,
                "center": {"x": 30, "y": 30},
                "relative_center": {"x": 30, "y": 30},
                "element_id": "window-1",
            },
            {
                "index": 1,
                "type": "control",
                "label": "Play",
                "window_text": "Play",
                "class": "Button",
                "monitor_id": 1,
                "center": {"x": 20, "y": 20},
                "relative_center": {"x": 20, "y": 20},
                "element_id": "control-1",
            },
        ]

        with patch("winremote.desktop.capture_image", return_value=(Image.new("RGB", (60, 60), "black"), metadata)), patch(
            "winremote.desktop.enumerate_windows",
            return_value=[],
        ), patch("winremote.desktop.map_ui_elements", return_value=mapped):
            payload = desktop.observe_screen(reset=True)

        assert payload["baseline_created"] is True
        assert payload["changed"] is None
        assert payload["ui_summary"]["control_count"] == 1
        assert payload["searchable_preview"][0]["label"] == "Play"

    def test_second_observation_reports_changed_regions(self):
        desktop._SCREEN_OBSERVE_BASELINES.clear()
        desktop._UI_MAP_CACHE.clear()
        metadata = {
            "bounds": {"left": 0, "top": 0, "right": 80, "bottom": 80, "width": 80, "height": 80},
            "captured_monitors": [1],
            "monitors": [{"monitor_id": 1}],
            "virtual_screen": {"left": 0, "top": 0, "right": 80, "bottom": 80, "width": 80, "height": 80},
        }

        with patch(
            "winremote.desktop.capture_image",
            side_effect=[
                (Image.new("RGB", (80, 80), "black"), metadata),
                (Image.new("RGB", (80, 80), "white"), metadata),
            ],
        ), patch("winremote.desktop.enumerate_windows", return_value=[]), patch(
            "winremote.desktop.map_ui_elements",
            return_value=[],
        ):
            baseline = desktop.observe_screen(reset=True, grid_size=4)
            changed = desktop.observe_screen(grid_size=4)

        assert baseline["baseline_created"] is True
        assert changed["baseline_created"] is False
        assert changed["changed"] is True
        assert changed["change_ratio"] > 0
        assert changed["changed_regions"]


class TestDesktopUIMapCache:
    def setup_method(self):
        desktop._UI_MAP_CACHE.clear()

    def test_map_ui_elements_reuses_recent_cache(self):
        mapped = [{"index": 0, "label": "Demo"}]

        with patch("winremote.desktop._map_ui_elements_uncached", return_value=mapped) as mock_uncached:
            first = desktop.map_ui_elements(window_title="Demo")
            second = desktop.map_ui_elements(window_title="Demo")

        assert first == mapped
        assert second == mapped
        assert mock_uncached.call_count == 1

    def test_invalidate_ui_map_cache_forces_refresh(self):
        first_map = [{"index": 0, "label": "Demo"}]
        second_map = [{"index": 0, "label": "Demo 2"}]

        with patch("winremote.desktop._map_ui_elements_uncached", side_effect=[first_map, second_map]) as mock_uncached:
            first = desktop.map_ui_elements(window_title="Demo")
            cleared = desktop.invalidate_ui_map_cache("Demo")
            second = desktop.map_ui_elements(window_title="Demo")

        assert first == first_map
        assert second == second_map
        assert cleared >= 1
        assert mock_uncached.call_count == 2

    def test_observe_screen_refreshes_ui_map_after_detected_change(self):
        desktop._SCREEN_OBSERVE_BASELINES.clear()
        metadata = {
            "bounds": {"left": 0, "top": 0, "right": 80, "bottom": 80, "width": 80, "height": 80},
            "captured_monitors": [1],
            "monitors": [{"monitor_id": 1}],
            "virtual_screen": {"left": 0, "top": 0, "right": 80, "bottom": 80, "width": 80, "height": 80},
        }
        first_mapped = [
            {"index": 0, "type": "window", "label": "Demo", "window_text": "Demo", "class": "Window", "monitor_id": 1, "center": {"x": 40, "y": 40}, "relative_center": {"x": 40, "y": 40}, "element_id": "window-1"},
            {"index": 1, "type": "control", "label": "Play", "window_text": "Play", "class": "Button", "monitor_id": 1, "center": {"x": 20, "y": 20}, "relative_center": {"x": 20, "y": 20}, "element_id": "control-1"},
        ]
        second_mapped = [
            {"index": 0, "type": "window", "label": "Demo", "window_text": "Demo", "class": "Window", "monitor_id": 1, "center": {"x": 40, "y": 40}, "relative_center": {"x": 40, "y": 40}, "element_id": "window-1"},
            {"index": 1, "type": "control", "label": "Pause", "window_text": "Pause", "class": "Button", "monitor_id": 1, "center": {"x": 20, "y": 20}, "relative_center": {"x": 20, "y": 20}, "element_id": "control-2"},
        ]

        with patch(
            "winremote.desktop.capture_image",
            side_effect=[
                (Image.new("RGB", (80, 80), "black"), metadata),
                (Image.new("RGB", (80, 80), "white"), metadata),
            ],
        ), patch("winremote.desktop.enumerate_windows", return_value=[]), patch(
            "winremote.desktop._map_ui_elements_uncached",
            side_effect=[first_mapped, second_mapped],
        ) as mock_uncached:
            baseline = desktop.observe_screen(reset=True)
            changed = desktop.observe_screen()

        assert baseline["searchable_preview"][0]["label"] == "Play"
        assert changed["changed"] is True
        assert changed["searchable_preview"][0]["label"] == "Pause"
        assert mock_uncached.call_count == 2
