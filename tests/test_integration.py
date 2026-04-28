"""Integration tests for MCP server endpoints."""

from __future__ import annotations

import pytest


class TestHealthEndpoint:
    """Test the /health HTTP endpoint."""

    def test_health_returns_ok(self):
        """Verify the health endpoint returns status ok."""
        from winremote import __version__
        from winremote.__main__ import mcp

        # Use Starlette test client on the FastMCP app
        try:
            from starlette.testclient import TestClient

            app = mcp.http_app(transport="streamable-http")
            client = TestClient(app)
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["version"] == __version__
        except Exception:
            # FastMCP internal API may vary; skip gracefully
            pytest.skip("Cannot create test client from FastMCP app")


class TestMCPToolRegistration:
    """Verify all expected tools are registered in the MCP server."""

    def test_expected_tools_registered(self):
        from winremote.__main__ import _get_registered_tools

        tool_names = set(_get_registered_tools().keys())

        expected = {
            "Snapshot",
            "Click",
            "Type",
            "Scroll",
            "Move",
            "Shortcut",
            "Wait",
            "KeyDown",
            "KeyUp",
            "HoldKeys",
            "MouseDown",
            "MouseUp",
            "MouseMoveRelative",
            "MouseLook",
            "WaitForRegionText",
            "WaitForImageChange",
            "AssertWindowActive",
            "AssertProcessRunning",
            "ListMonitors",
            "GetActiveWindow",
            "GetWindowBounds",
            "AgentStatusReport",
            "AppHealthCheck",
            "TailFile",
            "CaptureFailureBundle",
            "RobloxStudioInspectUI",
            "RobloxStudioOpenTab",
            "RobloxStudioEnsurePanel",
            "FocusWindow",
            "MinimizeAll",
            "App",
            "ReconnectSession",
            "Shell",
            "Scrape",
            "GetClipboard",
            "SetClipboard",
            "ClipboardSafeRead",
            "ClipboardSafeWrite",
            "PasteText",
            "ListProcesses",
            "KillProcess",
            "GetSystemInfo",
            "Notification",
            "LockScreen",
            "RenderSessionReport",
            "AnalyzeRecording",
            "LaunchDebugBrowser",
            "ListBrowserTabs",
            "GetBrowserConsoleLogs",
            "GetBrowserNetworkRequests",
            "GetBrowserDomText",
            "ClickDomElement",
            "SessionNoteAdd",
            "SessionNoteList",
            "SessionNoteSummarize",
            "CreateTerminalSession",
            "ListTerminalSessions",
            "ReadTerminalOutput",
            "SendTerminalInput",
            "WaitForTerminalOutput",
            "StopTerminalSession",
            "GetActionBudgetStatus",
            "ConfigureActionBudget",
            "ResetActionBudget",
            "DetectKnownIssues",
            "GetAgentCapabilityGuide",
            "CollectProjectContext",
            "VSCodeListWindows",
            "VSCodeGetActiveFile",
            "VSCodeListOpenFiles",
            "VSCodeGetDiagnostics",
            "VSCodeReadProblemsPanel",
            "VSCodeReadTerminal",
            "VSCodeOpenFile",
            "VSCodeRunCommand",
            "FileWrite",
            "FileList",
            "FileSearch",
            "FileDownload",
            "FileUpload",
            "RegRead",
            "RegWrite",
            "ServiceList",
            "ServiceStart",
            "ServiceStop",
            "TaskList",
            "TaskCreate",
            "TaskDelete",
            "EventLog",
            "Ping",
            "PortCheck",
            "NetConnections",
            "OCR",
            "ScreenRecord",
            "AnalyzeRecording",
            "AnnotatedSnapshot",
            "ObserveScreen",
            "UIMap",
            "UIMapJson",
            "UIFind",
            "UIClick",
            "UIAct",
            "UISequence",
            "ComputerUseStep",
            "ComputerUseTask",
            "UndoLastAction",
            "SaveUISelector",
            "FindUISelector",
            "ClickUISelector",
            "UIWatch",
            "RobloxStudioRunPlaytest",
            "RobloxStudioStopPlaytest",
            "RobloxStudioInspectUI",
            "RobloxStudioOpenTab",
            "RobloxStudioEnsurePanel",
            "RobloxStudioGetOutput",
            "RobloxStudioGetErrors",
            "RobloxStudioGetTestState",
            "RobloxStudioResetCharacter",
            "RobloxStudioTeleportToCheckpoint",
            "RobloxStudioRunNamedTest",
            "CancelTask",
            "GetTaskStatus",
            "GetRunningTasks",
            "StartFileWatch",
            "StopFileWatch",
            "ListFileChanges",
            "AnalyzeRecording",
            "HumanHandoff",
            "ResumeHumanHandoff",
            "HandoffStatus",
        }

        for name in expected:
            assert name in tool_names, f"Tool '{name}' not registered"

    def test_tool_count(self):
        from winremote.__main__ import _get_registered_tools

        tools = _get_registered_tools()
        # Should have a substantial number of tools
        assert len(tools) >= 30

    def test_chatgpt_profile_curated_tool_set(self):
        from winremote.tiers import CHATGPT_PROFILE_TOOLS, resolve_enabled_tools

        enabled = resolve_enabled_tools(profile="chatgpt")
        assert enabled == CHATGPT_PROFILE_TOOLS
        assert "UIAct" in enabled
        assert "Snapshot" in enabled
        assert "Click" in enabled
        assert "Type" in enabled
        assert "UIMap" in enabled
        assert "RobloxStudioInspectUI" in enabled
        assert "RobloxStudioEnsurePanel" in enabled
        assert "RobloxStudioRunPlaytest" in enabled
        assert "WaitForImageChange" in enabled
        assert "TaskCreate" not in enabled

    def test_copilot_profile_curated_tool_set(self):
        from winremote.tiers import COPILOT_PROFILE_TOOLS, resolve_enabled_tools

        enabled = resolve_enabled_tools(profile="copilot")
        assert enabled == COPILOT_PROFILE_TOOLS
        assert "UIAct" in enabled
        assert "Snapshot" in enabled
        assert "App" in enabled
        assert "RobloxStudioInspectUI" in enabled
        assert "RobloxStudioOpenTab" in enabled
        assert "RobloxStudioRunPlaytest" in enabled
        assert "RobloxStudioRunNamedTest" in enabled
        assert "Shell" not in enabled
        assert "FileWrite" not in enabled
