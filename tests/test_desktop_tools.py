"""Unit tests for desktop control tools (Click, Type, Scroll, Move, Shortcut, Wait, etc.)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pyautogui
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
                "baseline_created": True,
                "baseline_reset": False,
                "previous_count": 0,
                "current_count": 3,
                "diff": {"added": [], "removed": [], "moved": [], "text_changed": [], "summary": {"added": 0, "removed": 0, "moved": 0, "text_changed": 0}},
            }
            result = _call_tool("UIWatch", window_title="Roblox Studio")
            payload = _parse_task_wrapped_json(result)
            assert payload["baseline_created"] is True
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
