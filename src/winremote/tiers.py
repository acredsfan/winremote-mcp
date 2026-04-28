"""Tool tier definitions and access control."""

from __future__ import annotations

from winremote.profile_loader import list_profile_tomls, load_profile_toml

_BUILTIN_PROFILES = {"default", "chatgpt", "copilot", "excel", "claude"}
try:
    _TOML_PROFILES = set(list_profile_tomls())
except Exception:
    _TOML_PROFILES = set()

VALID_PROFILES = _BUILTIN_PROFILES | _TOML_PROFILES

TOOL_TIERS = {
    "tier1": {
        "Snapshot",
        "AnnotatedSnapshot",
        "UIMap",
        "UIMapJson",
        "UIFind",
        "UIWatch",
        "ObserveScreen",
        "GetClipboard",
        "ClipboardSafeRead",
        "GetSystemInfo",
        "ListProcesses",
        "FileList",
        "FileSearch",
        "RegRead",
        "ServiceList",
        "TaskList",
        "EventLog",
        "Ping",
        "PortCheck",
        "NetConnections",
        "OCR",
        "ScreenRecord",
        "ListRecordings",
        "GetRecordingManifest",
        "AnalyzeRecording",
        "RenderSessionReport",
        "ListBrowserTabs",
        "GetBrowserConsoleLogs",
        "GetBrowserNetworkRequests",
        "GetBrowserDomText",
        "SessionNoteList",
        "SessionNoteSummarize",
        "ListTerminalSessions",
        "ReadTerminalOutput",
        "WaitForTerminalOutput",
        "GetActionBudgetStatus",
        "DetectKnownIssues",
        "GetAgentCapabilityGuide",
        "CollectProjectContext",
        "VSCodeListWindows",
        "VSCodeGetActiveFile",
        "VSCodeListOpenFiles",
        "VSCodeGetDiagnostics",
        "VSCodeReadProblemsPanel",
        "VSCodeReadTerminal",
        "Notification",
        "Wait",
        "WaitForChange",
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
        "RobloxStudioGetOutput",
        "RobloxStudioGetErrors",
        "RobloxStudioGetTestState",
        "GetTaskStatus",
        "GetRunningTasks",
        "StartFileWatch",
        "StopFileWatch",
        "ListFileChanges",
        "HumanHandoff",
        "ResumeHumanHandoff",
        "HandoffStatus",
    },
    "tier2": {
        "Click",
        "UIClick",
        "UIAct",
        "UISequence",
        "ComputerUseStep",
        "ComputerUseTask",
        "UndoLastAction",
        "SaveUISelector",
        "FindUISelector",
        "ClickUISelector",
        "LaunchDebugBrowser",
        "ClickDomElement",
        "SessionNoteAdd",
        "CreateTerminalSession",
        "SendTerminalInput",
        "StopTerminalSession",
        "ConfigureActionBudget",
        "ResetActionBudget",
        "VSCodeOpenFile",
        "VSCodeRunCommand",
        "Type",
        "ClipboardSafeWrite",
        "PasteText",
        "Move",
        "Scroll",
        "KeyDown",
        "KeyUp",
        "HoldKeys",
        "MouseDown",
        "MouseUp",
        "MouseMoveRelative",
        "MouseLook",
        "Shortcut",
        "FocusWindow",
        "MinimizeAll",
        "App",
        "RobloxStudioOpenTab",
        "RobloxStudioEnsurePanel",
        "RobloxStudioRunPlaytest",
        "RobloxStudioStopPlaytest",
        "RobloxStudioResetCharacter",
        "RobloxStudioTeleportToCheckpoint",
        "RobloxStudioRunNamedTest",
        "Scrape",
        "CancelTask",
        "ReconnectSession",
        "StartScreenRecording",
        "StopScreenRecording",
    },
    "tier3": {
        "Shell",
        "FileRead",
        "FileWrite",
        "FileDownload",
        "FileUpload",
        "KillProcess",
        "RegWrite",
        "ServiceStart",
        "ServiceStop",
        "TaskCreate",
        "TaskDelete",
        "SetClipboard",
        "LockScreen",
    },
}

ALL_TOOLS = TOOL_TIERS["tier1"] | TOOL_TIERS["tier2"] | TOOL_TIERS["tier3"]
CHATGPT_PROFILE_TOOLS = {
    "ObserveScreen",
    "UIFind",
    "UIWatch",
    "UIAct",
    "UISequence",
    "ComputerUseStep",
    "ComputerUseTask",
    "UndoLastAction",
    "SaveUISelector",
    "FindUISelector",
    "ClickUISelector",
    "Snapshot",
    "AnnotatedSnapshot",
    "UIMap",
    "UIMapJson",
    "UIClick",
    "OCR",
    "ListRecordings",
    "GetRecordingManifest",
    "AnalyzeRecording",
    "RenderSessionReport",
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
    "GetActionBudgetStatus",
    "DetectKnownIssues",
    "GetAgentCapabilityGuide",
    "CollectProjectContext",
    "VSCodeListWindows",
    "VSCodeGetActiveFile",
    "VSCodeListOpenFiles",
    "VSCodeGetDiagnostics",
    "VSCodeReadProblemsPanel",
    "VSCodeReadTerminal",
    "StopTerminalSession",
    "ConfigureActionBudget",
    "ResetActionBudget",
    "VSCodeOpenFile",
    "VSCodeRunCommand",
    "Click",
    "Type",
    "Move",
    "Scroll",
    "KeyDown",
    "KeyUp",
    "HoldKeys",
    "MouseDown",
    "MouseUp",
    "MouseMoveRelative",
    "MouseLook",
    "FocusWindow",
    "App",
    "Shortcut",
    "Wait",
    "WaitForChange",
    "WaitForRegionText",
    "WaitForImageChange",
    "AssertWindowActive",
    "AssertProcessRunning",
    "ListMonitors",
    "GetActiveWindow",
    "GetWindowBounds",
    "AgentStatusReport",
    "AppHealthCheck",
    "AgentStatusReport",
    "AppHealthCheck",
    "TailFile",
    "CaptureFailureBundle",
    "RobloxStudioInspectUI",
    "RobloxStudioOpenTab",
    "RobloxStudioEnsurePanel",
    "Shell",
    "FileRead",
    "FileWrite",
    "FileList",
    "FileSearch",
    "GetClipboard",
    "ClipboardSafeRead",
    "ClipboardSafeWrite",
    "PasteText",
    "SetClipboard",
    "GetSystemInfo",
    "ListProcesses",
    "Notification",
    "RobloxStudioRunPlaytest",
    "RobloxStudioStopPlaytest",
    "RobloxStudioGetOutput",
    "RobloxStudioGetErrors",
    "RobloxStudioGetTestState",
    "RobloxStudioResetCharacter",
    "RobloxStudioTeleportToCheckpoint",
    "RobloxStudioRunNamedTest",
    "ReconnectSession",
    "StartScreenRecording",
    "StopScreenRecording",
    "StartFileWatch",
    "StopFileWatch",
    "ListFileChanges",
    "HumanHandoff",
    "ResumeHumanHandoff",
    "HandoffStatus",
}
COPILOT_PROFILE_TOOLS = CHATGPT_PROFILE_TOOLS - {
    "Shell",
    "FileRead",
    "FileWrite",
    "FileList",
    "FileSearch",
}
CLAUDE_PROFILE_TOOLS = set(CHATGPT_PROFILE_TOOLS)
EXCEL_PROFILE_TOOLS = {
    # Observation
    "ObserveScreen",
    "Snapshot",
    "AnnotatedSnapshot",
    "UIMap",
    "UIMapJson",
    "UIFind",
    "UIWatch",
    "OCR",
    "ListRecordings",
    "GetRecordingManifest",
    "AnalyzeRecording",
    "RenderSessionReport",
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
    "GetActionBudgetStatus",
    "DetectKnownIssues",
    "GetAgentCapabilityGuide",
    "CollectProjectContext",
    "VSCodeListWindows",
    "VSCodeGetActiveFile",
    "VSCodeListOpenFiles",
    "VSCodeGetDiagnostics",
    "VSCodeReadProblemsPanel",
    "VSCodeReadTerminal",
    "StopTerminalSession",
    "ConfigureActionBudget",
    "ResetActionBudget",
    "VSCodeOpenFile",
    "VSCodeRunCommand",
    # UI interaction
    "Click",
    "UIClick",
    "UIAct",
    "UISequence",
    "ComputerUseStep",
    "ComputerUseTask",
    "UndoLastAction",
    "SaveUISelector",
    "FindUISelector",
    "ClickUISelector",
    "Type",
    "Move",
    "Scroll",
    "KeyDown",
    "KeyUp",
    "HoldKeys",
    "Shortcut",
    "FocusWindow",
    "App",
    # Clipboard (essential for Excel data workflows)
    "GetClipboard",
    "ClipboardSafeRead",
    "ClipboardSafeWrite",
    "PasteText",
    "SetClipboard",
    # File operations (reading/writing data files and running macros)
    "Shell",
    "FileRead",
    "FileWrite",
    "FileList",
    "FileSearch",
    # Waiting and assertions
    "Wait",
    "WaitForChange",
    "WaitForRegionText",
    "WaitForImageChange",
    "AssertWindowActive",
    "AssertProcessRunning",
    "ListMonitors",
    "GetActiveWindow",
    "GetWindowBounds",
    # System utilities
    "GetSystemInfo",
    "ListProcesses",
    "Notification",
    # Diagnostics
    "TailFile",
    "CaptureFailureBundle",
    "RobloxStudioInspectUI",
    "RobloxStudioOpenTab",
    "RobloxStudioEnsurePanel",
    "ReconnectSession",
    "StartScreenRecording",
    "StopScreenRecording",
    "StartFileWatch",
    "StopFileWatch",
    "ListFileChanges",
    "HumanHandoff",
    "ResumeHumanHandoff",
    "HandoffStatus",
}
_NAME_LOOKUP = {name.lower(): name for name in ALL_TOOLS}


def _resolve_profile_tools(profile_name: str, *, _seen: set[str] | None = None) -> set[str]:
    """Resolve profile tools from built-ins or TOML profiles (supports extends)."""
    name = normalize_profile_name(profile_name)
    if _seen is None:
        _seen = set()
    if name in _seen:
        raise ValueError(f"Profile inheritance cycle detected at: {name}")
    _seen.add(name)

    if name == "chatgpt":
        return set(CHATGPT_PROFILE_TOOLS)
    if name == "copilot":
        return set(COPILOT_PROFILE_TOOLS)
    if name == "claude":
        return set(CLAUDE_PROFILE_TOOLS)
    if name == "excel":
        return set(EXCEL_PROFILE_TOOLS)
    if name == "default":
        return set(TOOL_TIERS["tier1"]) | set(TOOL_TIERS["tier2"])

    profile_data = load_profile_toml(name)
    extends_name = str(profile_data.get("extends") or "").strip().lower()
    if extends_name:
        enabled = _resolve_profile_tools(extends_name, _seen=_seen)
    else:
        enabled = set(TOOL_TIERS["tier1"]) | set(TOOL_TIERS["tier2"])

    enabled_from_file = profile_data.get("enabled_tools") or []
    disabled_from_file = profile_data.get("disabled_tools") or []

    if enabled_from_file:
        enabled |= set(normalize_tool_names([str(item) for item in enabled_from_file]))
    if disabled_from_file:
        enabled -= set(normalize_tool_names([str(item) for item in disabled_from_file]))
    return enabled


def parse_tool_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def normalize_tool_names(tool_names: list[str]) -> list[str]:
    normalized = []
    unknown = []
    for name in tool_names:
        hit = _NAME_LOOKUP.get(name.lower())
        if hit:
            normalized.append(hit)
        else:
            unknown.append(name)
    if unknown:
        allowed = ", ".join(sorted(ALL_TOOLS))
        raise ValueError(f"Unknown tools: {', '.join(unknown)}. Allowed tools: {allowed}")
    return normalized


def normalize_profile_name(profile: str | None) -> str:
    value = str(profile or "default").strip().lower()
    if value not in VALID_PROFILES:
        raise ValueError(f"Unknown profile: {profile}. Allowed profiles: {', '.join(sorted(VALID_PROFILES))}")
    return value


def resolve_enabled_tools(
    *,
    profile: str = "default",
    enable_tier3: bool = False,
    disable_tier2: bool = False,
    enable_all: bool = False,
    explicit_tools: list[str] | None = None,
    exclude_tools: list[str] | None = None,
) -> set[str]:
    """Resolve active tools.

    Precedence: explicit tools > tier toggles.
    """
    explicit_tools = explicit_tools or []
    exclude_tools = exclude_tools or []
    profile = normalize_profile_name(profile)

    if explicit_tools:
        enabled = set(normalize_tool_names(explicit_tools))
    elif profile in VALID_PROFILES and profile != "default":
        enabled = _resolve_profile_tools(profile)
    elif enable_all:
        enabled = set(ALL_TOOLS)
    else:
        enabled = set(TOOL_TIERS["tier1"])
        if not disable_tier2:
            enabled |= TOOL_TIERS["tier2"]
        if enable_tier3:
            enabled |= TOOL_TIERS["tier3"]

    if exclude_tools:
        enabled -= set(normalize_tool_names(exclude_tools))

    return enabled


def get_tier_names(enabled_tools: set[str]) -> list[str]:
    enabled_tiers = []
    if TOOL_TIERS["tier1"] & enabled_tools:
        enabled_tiers.append("1")
    if TOOL_TIERS["tier2"] & enabled_tools:
        enabled_tiers.append("2")
    if TOOL_TIERS["tier3"] & enabled_tools:
        enabled_tiers.append("3")
    return enabled_tiers


def _get_registered_tools(mcp) -> dict[str, object]:
    # fastmcp 2.x
    tool_mgr = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_mgr, "_tools", None)
    if isinstance(tools, dict):
        return tools

    # fastmcp 3.x
    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", None)
    if isinstance(components, dict):
        out: dict[str, object] = {}
        for comp_key, comp in components.items():
            if not isinstance(comp_key, str) or not comp_key.startswith("tool:"):
                continue
            name = getattr(comp, "name", None)
            if not isinstance(name, str) or not name:
                name = comp_key.split(":", 1)[1].split("@", 1)[0]
            out[name] = comp
        return out

    raise RuntimeError("Unsupported fastmcp internals: cannot locate registered tools")


def _remove_tool(mcp, name: str) -> None:
    # fastmcp 2.x
    tool_mgr = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_mgr, "_tools", None)
    if isinstance(tools, dict):
        tools.pop(name, None)
        return

    # fastmcp 3.x
    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", None)
    if isinstance(components, dict):
        keys_to_remove = [
            k
            for k, v in components.items()
            if isinstance(k, str)
            and k.startswith("tool:")
            and ((getattr(v, "name", None) == name) or k.split(":", 1)[1].split("@", 1)[0] == name)
        ]
        for k in keys_to_remove:
            components.pop(k, None)


def filter_tools(mcp, enabled_tools: set[str]) -> dict[str, int]:
    all_tools = list(_get_registered_tools(mcp).keys())
    total_count = len(all_tools)
    for name in all_tools:
        if name not in enabled_tools:
            _remove_tool(mcp, name)
    return {"enabled": len(enabled_tools), "disabled": total_count - len(enabled_tools), "total": total_count}
