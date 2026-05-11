"""winremote-mcp — CLI entry point and MCP tool definitions."""

from __future__ import annotations

import base64
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import pyautogui
from click.core import ParameterSource
from dotenv import load_dotenv
from fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

try:
    from mcp.types import ToolAnnotations
except ImportError:
    from fastmcp.tools import ToolAnnotations

from starlette.middleware import Middleware
from starlette.responses import JSONResponse

from winremote import __version__, action_budget, action_undo, agent_capabilities, browser_debug, computer_use, desktop, file_watcher, handoff_state, known_issues, network, ocr, process_mgr, project_context, recording, redaction, registry, roblox_studio, roblox_studio_harness, selectors, services, session_notes, session_report, terminal_sessions, vscode_bridge
from winremote.config import RedactionConfig, discover_config_path, load_config
from winremote.security import IPAllowlistMiddleware, parse_ip_allowlist
from winremote.taskmanager import manager as task_manager
from winremote.tiers import ALL_TOOLS, VALID_PROFILES, get_tier_names, parse_tool_csv, resolve_enabled_tools

load_dotenv()

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

DEFAULT_SERVER_INSTRUCTIONS = (
    "Windows Remote MCP Server. Provides desktop control, window management, "
    "shell execution, file operations, network tools, registry, services, "
    "and system management tools for a Windows machine."
)

CHATGPT_SERVER_INSTRUCTIONS = (
    "Windows Remote MCP Server for ChatGPT full-MCP workflows. Prefer semantic GUI "
    "tools first: ObserveScreen, UIFind, UIAct, UISequence, UIWatch, UIMap, and "
    "AnnotatedSnapshot. Use Snapshot only when pixels are actually required. FocusWindow "
    "or App should target the app before interaction. Use Click, Type, Move, Scroll, and "
    "Shortcut as fallbacks for custom-drawn interfaces when the semantic path is not enough. "
    "For Roblox Studio, prefer RobloxStudioInspectUI, RobloxStudioOpenTab, and RobloxStudioEnsurePanel "
    "for editor layout and navigation, then use UIFind, UIAct, and UISequence for detailed interactions. "
    "Those semantic tools automatically retry with a Studio-aware OCR fallback over ribbon tabs and dock regions "
    "before resorting to screenshots."
)

CLAUDE_SERVER_INSTRUCTIONS = (
    "Windows Remote MCP Server for Claude Desktop and Claude Code workflows. Prefer semantic GUI "
    "tools first: ObserveScreen, UIFind, UIAct, UISequence, UIWatch, UIMap, and "
    "AnnotatedSnapshot. Use Snapshot only when pixels are actually required. FocusWindow "
    "or App should target the app before interaction. Use Click, Type, Move, Scroll, and "
    "Shortcut as fallbacks for custom-drawn interfaces when the semantic path is not enough. "
    "For Roblox Studio, prefer RobloxStudioInspectUI, RobloxStudioOpenTab, and RobloxStudioEnsurePanel "
    "for editor layout and navigation, then use UIFind, UIAct, and UISequence for detailed interactions. "
    "Those semantic tools automatically retry with a Studio-aware OCR fallback over ribbon tabs and dock regions "
    "before resorting to screenshots."
)

COPILOT_SERVER_INSTRUCTIONS = (
    "Windows Remote MCP Server for GitHub Copilot Chat workflows inside VS Code Insiders. "
    "Copilot already has strong workspace editing and terminal tools, so prefer those for repo changes. "
    "Use this MCP server for desktop interaction like a human developer: focusing windows, launching apps, "
    "driving custom GUIs, operating Roblox Studio, running playtests, and querying the Roblox Studio harness. "
    "Prefer semantic GUI tools first: ObserveScreen, UIFind, UIAct, UISequence, UIWatch, and UIMap. "
    "For Roblox Studio editor work, use RobloxStudioInspectUI, RobloxStudioOpenTab, and RobloxStudioEnsurePanel "
    "before lower-level clicks. "
    "Use Snapshot only when pixels are actually required, and use Click, Type, Move, Scroll, and Shortcut "
    "as fallbacks for custom-drawn interfaces. UIFind, UIAct, and UISequence automatically retry with a "
    "Studio-aware OCR fallback over ribbon tabs and dock regions before resorting to screenshots."
)

EXCEL_SERVER_INSTRUCTIONS = (
    "Windows Remote MCP Server configured for Microsoft Excel automation. "
    "Use FocusWindow or App to launch and focus Excel before interacting. "
    "Prefer semantic GUI tools first: UIFind, UIAct, UISequence, UIMapJson, and ObserveScreen to inspect "
    "ribbons, cells, dialogs, and the Name Box. Use OCR or UIFind to read cell values and formula bar content. "
    "Use Type and Shortcut for entering formulas, applying formatting shortcuts, and navigating the grid. "
    "Use SetClipboard and GetClipboard for bulk data transfers and pasting computed values. "
    "Use Shell, FileRead, and FileWrite to run VBA macros via COM automation scripts, read CSV/XLSX data, "
    "or write helper scripts. Use Snapshot or AnnotatedSnapshot only when visual confirmation is required. "
    "Keep screenshots rare and low-resolution — prefer text-based inspection whenever possible."
)

mcp = FastMCP(
    "winremote-mcp",
    instructions=DEFAULT_SERVER_INSTRUCTIONS,
)

PROFILE_CHOICES = sorted(VALID_PROFILES)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "ok", "version": __version__})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tobool(v: bool | str) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def _check_win32(tool_name: str = "This tool") -> str | None:
    """Return an error string if pywin32 is unavailable, else None."""
    if not desktop.HAS_WIN32:
        return f"Error: pywin32 not installed — {tool_name} requires it. Run `pip install pywin32` on the Windows host."
    return None


def _check_handoff_pause(tool_name: str) -> str | None:
    """Return pause message if human handoff currently pauses action tools."""
    if handoff_state.is_paused():
        state = handoff_state.status()
        reason = state.get("message") or "Awaiting human resume"
        return f"{tool_name} paused: {reason}"
    return None


def _check_action_budget(action_kind: str, amount: int = 1) -> str | None:
    """Return an error message when action budget policy blocks execution."""
    allowed, reason = action_budget.check_and_record(action_kind, amount=amount)
    if allowed:
        return None
    return f"Action blocked by budget policy: {reason}"


def _monitor_context() -> dict:
    """Best-effort monitor metadata for tool payloads."""
    try:
        monitors = desktop.get_monitor_info()
        virtual_screen = desktop.get_virtual_screen_bounds(monitors)
        return {"monitors": monitors, "virtual_screen": virtual_screen}
    except Exception:
        return {"monitors": [], "virtual_screen": None}


def _ui_coordinate_spaces() -> dict[str, str]:
    """Describe coordinate semantics for structured UI payloads."""
    return {
        "center": "Absolute virtual-screen coordinates in pixels; safe for direct click/move actions.",
        "rect": "Absolute virtual-screen rectangle in pixels.",
        "relative_center": "Coordinates relative to the mapped window's top-left corner.",
        "relative_rect": "Rectangle relative to the mapped window's top-left corner.",
    }


def _server_instructions_for_profile(profile: str) -> str:
    """Return profile-specific server instructions."""
    normalized = str(profile or "default").strip().lower()
    if normalized in {"chatgpt", "chatgpt-full"}:
        return CHATGPT_SERVER_INSTRUCTIONS
    if normalized in {"claude", "claude-code"}:
        return CLAUDE_SERVER_INSTRUCTIONS
    if normalized in {"copilot", "copilot-chat", "copilot-cli", "codex-cli", "gemini-cli", "qwen-code"}:
        return COPILOT_SERVER_INSTRUCTIONS
    if normalized == "excel":
        return EXCEL_SERVER_INSTRUCTIONS
    return DEFAULT_SERVER_INSTRUCTIONS


def _trim_recommendations(recommendations: list[str] | None, limit: int = 4) -> list[str]:
    """Keep recommendations compact and deduplicated."""
    seen: set[str] = set()
    trimmed: list[str] = []
    for item in recommendations or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        trimmed.append(text)
        if len(trimmed) >= limit:
            break
    return trimmed


def _compact_window_target(window: dict[str, Any] | None, *, mode: str | None = None) -> dict[str, Any] | None:
    """Return a compact window/target descriptor."""
    if not window:
        return {"mode": mode} if mode else None

    target = {
        "mode": mode,
        "title": window.get("title") or window.get("label"),
        "handle": window.get("handle"),
        "pid": window.get("pid"),
        "process_name": window.get("process_name"),
        "monitor_id": window.get("monitor_id"),
        "rect": window.get("rect"),
    }
    return {key: value for key, value in target.items() if value is not None}


def _search_payload(
    *,
    searched_element_count: int | None = None,
    searchable_preview: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
    recommendations: list[str] | None = None,
    ui_scope: str | None = None,
) -> dict[str, Any]:
    """Build a consistent search/inspection payload."""
    return {
        "searched_element_count": searched_element_count or 0,
        "searchable_preview": (searchable_preview or [])[:5],
        "summary": summary,
        "ui_scope": ui_scope,
        "recommendations": _trim_recommendations(recommendations),
    }


def _compact_observation_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a compact observation summary suitable for low-overhead chat flows."""
    if payload is None:
        return None
    return {
        "target": {
            "mode": ((payload.get("target") or {}).get("mode")),
            "window_title": (((payload.get("target") or {}).get("window") or {}).get("title")),
            "captured_monitors": ((payload.get("target") or {}).get("captured_monitors")) or [],
            "bounds": ((payload.get("target") or {}).get("bounds")),
        },
        "changed": payload.get("changed"),
        "change_ratio": payload.get("change_ratio"),
        "changed_regions": (payload.get("changed_regions") or [])[:3],
        "search": _search_payload(
            searched_element_count=((payload.get("ui_summary") or {}).get("control_count")),
            searchable_preview=payload.get("searchable_preview"),
            summary=payload.get("ui_summary"),
            recommendations=payload.get("recommendations"),
            ui_scope=payload.get("ui_scope"),
        ),
        "recommendations": _trim_recommendations(payload.get("recommendations"), limit=2),
    }


def _compact_wait_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a compact semantic-wait summary for low-overhead chat flows."""
    if payload is None:
        return None
    return {
        "query": payload.get("query"),
        "wait_until": payload.get("wait_until"),
        "satisfied": payload.get("satisfied"),
        "timed_out": payload.get("timed_out"),
        "target": payload.get("target"),
        "search": _search_payload(
            searched_element_count=payload.get("searched_element_count"),
            searchable_preview=payload.get("searchable_preview"),
            summary=payload.get("summary"),
            recommendations=payload.get("recommendations"),
        ),
        "recommendations": _trim_recommendations(payload.get("recommendations"), limit=2),
        "observation_after": _compact_observation_payload(payload.get("observation_after")),
    }


def _normalized_key_list(keys: str | list[str]) -> list[str]:
    """Normalize keys from either a '+'-delimited string or a list."""
    if isinstance(keys, list):
        parts = [str(part).strip().lower() for part in keys]
    else:
        raw = str(keys or "").replace(",", "+")
        parts = [part.strip().lower() for part in raw.split("+")]
    parts = [part for part in parts if part]
    if not parts:
        raise ValueError("At least one key is required")
    return parts


def _find_window_by_title_fragment(title: str) -> desktop.WindowInfo | None:
    """Return the best matching window for a title fragment."""
    target = (title or "").strip().lower()
    if not target:
        return None
    windows = desktop.enumerate_windows()
    exact = [window for window in windows if window.title.strip().lower() == target]
    if exact:
        return exact[0]
    contains = [window for window in windows if target in window.title.lower()]
    return contains[0] if contains else None


def _process_matches(*, pid: int = 0, name: str = "") -> list[dict[str, Any]]:
    """Return process matches for assertions and diagnostics."""
    import psutil

    needle = (name or "").strip().lower()
    matches: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "status"]):
        try:
            info = proc.info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        proc_name = str(info.get("name") or "")
        proc_pid = int(info.get("pid") or 0)
        if pid and proc_pid != pid:
            continue
        if needle and needle not in proc_name.lower():
            continue
        matches.append(
            {
                "pid": proc_pid,
                "name": proc_name,
                "status": info.get("status"),
            }
        )
    return matches


def _wait_for_region_text(
    *,
    query: str,
    left: int,
    top: int,
    right: int,
    bottom: int,
    wait_until: str = "appear",
    timeout_seconds: float = 5.0,
    poll_interval: float = 0.5,
    lang: str = "eng",
) -> dict[str, Any]:
    """Poll OCR on a region until text appears or disappears."""
    wait_until = _normalize_wait_until(wait_until)
    needle = (query or "").strip().lower()
    if not needle:
        raise ValueError("query is required")
    timeout_seconds = max(0.0, float(timeout_seconds))
    poll_interval = max(0.05, float(poll_interval))
    left, top, right, bottom = desktop.normalize_region(left, top, right, bottom)
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_text = ""

    while True:
        attempts += 1
        last_text = ocr.run_ocr(left=left, top=top, right=right, bottom=bottom, lang=lang)
        has_match = needle in last_text.lower()
        satisfied = has_match if wait_until == "appear" else not has_match
        if satisfied:
            return {
                "query": query,
                "wait_until": wait_until,
                "satisfied": True,
                "timed_out": False,
                "attempts": attempts,
                "region": {"left": left, "top": top, "right": right, "bottom": bottom},
                "matched": has_match,
                "text_excerpt": last_text[:1000],
            }
        if time.monotonic() >= deadline:
            return {
                "query": query,
                "wait_until": wait_until,
                "satisfied": False,
                "timed_out": True,
                "attempts": attempts,
                "region": {"left": left, "top": top, "right": right, "bottom": bottom},
                "matched": has_match,
                "text_excerpt": last_text[:1000],
            }
        time.sleep(poll_interval)


def _wait_for_image_change(
    *,
    window_title: str = "",
    monitor: int = 0,
    left: int = 0,
    top: int = 0,
    right: int = 0,
    bottom: int = 0,
    timeout_seconds: float = 5.0,
    poll_interval: float = 0.25,
    min_change_ratio: float = 0.02,
    grid_size: int = 6,
    reset_baseline: bool = True,
) -> dict[str, Any]:
    """Wait for a visible change in a region, monitor, or window."""
    region = {}
    if left or top or right or bottom:
        left, top, right, bottom = desktop.normalize_region(left, top, right, bottom)
        region = {"left": left, "top": top, "right": right, "bottom": bottom}

    baseline = None
    if reset_baseline:
        baseline = desktop.observe_screen(
            window_title=window_title,
            monitor=monitor,
            grid_size=grid_size,
            reset=True,
            update_baseline=True,
            **region,
        )
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    attempts = 0
    last_payload = baseline
    while True:
        attempts += 1
        last_payload = desktop.observe_screen(
            window_title=window_title,
            monitor=monitor,
            grid_size=grid_size,
            reset=False,
            update_baseline=False,
            **region,
        )
        change_ratio = float(last_payload.get("change_ratio") or 0.0)
        if bool(last_payload.get("changed")) and change_ratio >= min_change_ratio:
            desktop.observe_screen(
                window_title=window_title,
                monitor=monitor,
                grid_size=grid_size,
                reset=False,
                update_baseline=True,
                **region,
            )
            return {
                "satisfied": True,
                "timed_out": False,
                "attempts": attempts,
                "min_change_ratio": min_change_ratio,
                "observation": last_payload,
            }
        if time.monotonic() >= deadline:
            return {
                "satisfied": False,
                "timed_out": True,
                "attempts": attempts,
                "min_change_ratio": min_change_ratio,
                "observation": last_payload,
            }
        time.sleep(max(0.05, float(poll_interval)))


def _studio_focus() -> str:
    """Best-effort focus for Roblox Studio."""
    return desktop.focus_window(title="Roblox Studio")


def _studio_harness_payload(route: str, *, payload: dict[str, Any] | None = None, harness_url: str = "", timeout: float = 10.0) -> dict[str, Any]:
    """Call the local Roblox Studio harness and return its payload."""
    return roblox_studio.harness_request("POST", route, payload=payload, harness_url=harness_url, timeout=timeout)


def _studio_window_payload() -> dict[str, Any]:
    """Return the current Roblox Studio window as a JSON-friendly payload."""
    studio_window = _find_window_by_title_fragment("Roblox Studio")
    if studio_window is None:
        raise RuntimeError("Roblox Studio window not found")
    return {
        "handle": studio_window.handle,
        "label": studio_window.title,
        "title": studio_window.title,
        "rect": {
            "left": studio_window.rect[0],
            "top": studio_window.rect[1],
            "right": studio_window.rect[2],
            "bottom": studio_window.rect[3],
        },
        "size": {"width": studio_window.width, "height": studio_window.height},
        "pid": studio_window.pid,
        "process_name": studio_window.process_name,
        "monitor_id": studio_window.monitor_id,
    }


def _compact_studio_candidate(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a compact Roblox Studio editor target payload."""
    if not candidate:
        return None
    payload = {
        "label": candidate.get("label"),
        "class": candidate.get("class"),
        "source": candidate.get("source"),
        "region_id": candidate.get("region_id"),
        "element_id": candidate.get("element_id"),
        "monitor_id": candidate.get("monitor_id", 0),
        "center": candidate.get("center"),
        "relative_center": candidate.get("relative_center"),
        "rect": candidate.get("rect"),
        "ocr_text": str(candidate.get("ocr_text") or "")[:160] or None,
        "match": candidate.get("match"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _compact_studio_inspection(inspection: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a compact Studio inspection summary."""
    if inspection is None:
        return None
    return {
        "inspection_mode": inspection.get("inspection_mode"),
        "window_title": inspection.get("window_title"),
        "tabs": [_compact_studio_candidate(item) for item in (inspection.get("tabs") or [])[:5]],
        "panels": [_compact_studio_candidate(item) for item in (inspection.get("panels") or [])[:5]],
        "ribbon_regions": [_compact_studio_candidate(item) for item in (inspection.get("ribbon_regions") or [])[:6]],
        "matches": [_compact_studio_candidate(item) for item in (inspection.get("matches") or [])[:5]],
        "searchable_preview": (inspection.get("searchable_preview") or [])[:8],
        "notes": _trim_recommendations(inspection.get("notes"), limit=4),
    }


def _studio_click_target(target: dict[str, Any], *, button: str = "left") -> None:
    """Move to and click a Studio UI target candidate."""
    center = target.get("center") or {}
    x = int(center.get("x", 0) or 0)
    y = int(center.get("y", 0) or 0)
    desktop.validate_screen_point(x, y)
    pyautogui.moveTo(x, y)
    pyautogui.click(x, y, button=button)


def _studio_open_tab_action(
    tab_name: str,
    *,
    focus_window: bool = True,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    """Open a top-level Roblox Studio ribbon tab and return a structured result."""
    canonical_tab = roblox_studio.normalize_studio_tab_name(tab_name)
    focus_result = _studio_focus() if focus_window else None
    desktop.invalidate_ui_map_cache("Roblox Studio")
    window_before = _studio_window_payload()
    inspection_before = roblox_studio.inspect_studio_ui_regions(
        window_before,
        query=canonical_tab,
        max_results=6,
        use_cache=False,
    )
    tab_matches = [
        item
        for item in (inspection_before.get("matches") or [])
        if str(item.get("class") or "") == "RobloxStudioHeuristicRegion" and str(item.get("region_id") or "").endswith("_tab")
    ]
    if not tab_matches:
        tab_matches = [item for item in (inspection_before.get("tabs") or []) if roblox_studio.normalize_studio_tab_name(item.get("label") or "") == canonical_tab]

    if not tab_matches:
        return {
            "status": "no-match",
            "tab_name": canonical_tab,
            "focus_result": focus_result,
            "window": window_before,
            "inspection_before": _compact_studio_inspection(inspection_before),
            "recommendations": [
                f"Could not locate the {canonical_tab} tab in Roblox Studio. Use RobloxStudioInspectUI for a fresh editor snapshot.",
            ],
        }

    target = tab_matches[0]
    pre_observation = desktop.observe_screen(window_title="Roblox Studio", reset=True, update_baseline=True)
    _studio_click_target(target)
    desktop.invalidate_ui_map_cache("Roblox Studio")
    post_wait = _wait_for_image_change(
        window_title="Roblox Studio",
        timeout_seconds=max(0.5, float(timeout_seconds)),
        poll_interval=0.2,
        min_change_ratio=0.005,
        reset_baseline=False,
    )
    desktop.invalidate_ui_map_cache("Roblox Studio")
    window_after = _studio_window_payload()
    inspection_after = roblox_studio.inspect_studio_ui_regions(
        window_after,
        include_ribbon=True,
        use_cache=False,
    )
    return {
        "status": "completed",
        "tab_name": canonical_tab,
        "focus_result": focus_result,
        "window": window_after,
        "target": _compact_studio_candidate(target),
        "observation_before": _compact_observation_payload(pre_observation),
        "observation_after": _compact_observation_payload(post_wait.get("observation")),
        "changed": post_wait.get("satisfied"),
        "inspection_before": _compact_studio_inspection(inspection_before),
        "inspection_after": _compact_studio_inspection(inspection_after),
        "recommendations": [
            f"Opened the {canonical_tab} tab. Reuse inspection_after.ribbon_regions or general semantic tools before asking the user to locate controls manually.",
        ],
    }


def _ensure_session_connected() -> str | None:
    """Reconnect disconnected Windows session to console if needed.

    Returns None on success, error string on failure.
    """
    try:
        # Query current sessions
        result = subprocess.run(
            ["query", "session"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return f"Failed to query sessions: {result.stderr}"

        session_lines = result.stdout.strip().split("\n")
        user_session_id = None
        session_status = None

        # Parse session output to find user session
        for line in session_lines[1:]:  # Skip header
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 3:
                session_name = parts[0]
                username = parts[1] if parts[1] != ">" else parts[2]
                session_id = parts[2] if parts[1] != ">" else parts[1]
                state = parts[3] if parts[1] != ">" else parts[2]

                # Look for a user session (not services or console without user)
                if (
                    username
                    and username.lower() not in ["", "services"]
                    and session_name.lower() not in ["services", "console"]
                    and session_id.isdigit()
                ):
                    user_session_id = int(session_id)
                    session_status = state.lower()
                    break

        if user_session_id is None:
            return "No user session found to reconnect"

        # If session is already active, no need to reconnect
        if session_status == "active":
            return None

        # Reconnect session to console
        result = subprocess.run(
            ["tscon", str(user_session_id), "/dest:console"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            return None  # Success
        else:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return f"Failed to reconnect session {user_session_id}: {error_msg}"

    except subprocess.TimeoutExpired:
        return "Session reconnect operation timed out"
    except Exception as e:
        return f"Session reconnect error: {e}"


# ============================= DESKTOP CONTROL =============================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Snapshot",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def Snapshot(
    use_vision: bool | str = True,
    quality: int = 75,
    max_width: int = 0,
    monitor: int = 0,
    window_title: str = "",
) -> list:
    """Capture desktop screenshot, window list, and interactive UI elements.

    Args:
        use_vision: Include screenshot image (default True).
        quality: JPEG quality 1-100 (default 75). Lower = smaller.
        max_width: Max image width in pixels. 0=native resolution (default). Set to e.g. 1920 to downscale.
        monitor: Monitor to capture. 0=all monitors (default), 1/2/3=specific monitor.
        window_title: Optional target window title for window-only capture. When set, takes priority over monitor.

    Returns a list containing:
    - Screenshot image as JPEG (if use_vision=True)
    - Text summary of windows and UI elements
    """
    try:
        parts = []
        use_vision = _tobool(use_vision)
        monitor_ctx = _monitor_context()

        # Screenshot (auto-reconnect session if grab fails)
        if use_vision:
            try:
                b64 = desktop.take_screenshot(
                    quality=quality,
                    max_width=max_width,
                    monitor=monitor,
                    window_title=window_title,
                )
            except Exception as screenshot_error:
                # Check if a disconnected session is the cause
                reconnect_result = _ensure_session_connected()
                if reconnect_result is not None:
                    # Session wasn't disconnected (or reconnect failed) — not a session issue
                    return [f"Snapshot error: {screenshot_error}"]
                # Session was disconnected and reconnected, retry
                try:
                    b64 = desktop.take_screenshot(
                        quality=quality,
                        max_width=max_width,
                        monitor=monitor,
                        window_title=window_title,
                    )
                except Exception as retry_error:
                    return [f"Snapshot error (after session reconnect): {retry_error}"]
            parts.append(ImageContent(type="image", data=b64, mimeType="image/jpeg"))

        # Window list
        windows = desktop.enumerate_windows()
        win_lines = [f"**System Language:** {desktop._get_system_language()}"]
        if window_title.strip():
            win_lines.append(f"**Screenshot Target:** Window '{window_title.strip()}'")
            win_lines.append("")
        if monitor_ctx["monitors"]:
            win_lines.extend(["", "**Monitors:**"])
            for mon in monitor_ctx["monitors"]:
                rect = mon["rect"]
                win_lines.append(
                    "  "
                    f"Monitor {mon['monitor_id']}"
                    f"{' (primary)' if mon.get('primary') else ''}"
                    f": {mon['size']['width']}x{mon['size']['height']}"
                    f" at ({rect['left']},{rect['top']}) -> ({rect['right']},{rect['bottom']})"
                    f", scale={mon.get('scale', 1.0)}"
                )
        win_lines.extend(["", "**Windows:**"])
        for w in windows:
            proc = f" pid={w.pid} {w.process_name}" if getattr(w, 'pid', 0) else ""
            mon = f" monitor={getattr(w, 'monitor_id', 0)}" if getattr(w, 'monitor_id', 0) else ""
            win_lines.append(
                f"  [{w.handle}] {w.title} ({w.width}x{w.height} at {w.rect[0]},{w.rect[1]}){mon}{proc}"
            )

        active_window = None
        try:
            mapped_foreground = desktop.map_ui_elements(max_elements=12)
            if mapped_foreground:
                active_window = mapped_foreground[0]
        except Exception:
            active_window = None

        if active_window:
            rect = active_window.get("rect") or {}
            win_lines.extend(
                [
                    "",
                    "**Active Window:**",
                    (
                        "  "
                        f"{active_window.get('label') or active_window.get('window_text') or '(unknown)'}"
                        f" | monitor={active_window.get('monitor_id', 0)}"
                        f" | rect=({rect.get('left', 0)},{rect.get('top', 0)},{rect.get('right', 0)},{rect.get('bottom', 0)})"
                    ),
                ]
            )

        # Interactive elements from foreground window
        elements = desktop.get_interactive_elements()
        if elements:
            win_lines.append("")
            win_lines.append("**Interactive Elements (foreground window):**")
            for el in elements[:50]:  # limit
                r = el["rect"]
                cx = (r["left"] + r["right"]) // 2
                cy = (r["top"] + r["bottom"]) // 2
                label = el["text"] or el["class"]
                monitor_suffix = f" monitor={el.get('monitor_id', 0)}" if el.get("monitor_id") else ""
                win_lines.append(f"  [{el['index']}] {label} — center ({cx},{cy}){monitor_suffix}")

        win_lines.extend(
            [
                "",
                "**Suggested Next Step:**",
                (
                    "  Prefer ObserveScreen, UIFind, UIAct, or UISequence for semantic GUI work. "
                    "Use Snapshot again only when you need fresh pixels."
                ),
            ]
        )

        parts.append(TextContent(type="text", text="\n".join(win_lines)))
        return parts
    except Exception as e:
        return [f"Snapshot error: {e}"]


@mcp.tool(
    annotations=ToolAnnotations(
        title="Click",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Click(
    x: int,
    y: int,
    button: str = "left",
    action: str = "click",
) -> str:
    """Mouse click at screen coordinates.

    Args:
        x: X coordinate.
        y: Y coordinate.
        button: 'left', 'right', or 'middle'.
        action: 'click', 'double', or 'hover'.
    """
    try:
        paused = _check_handoff_pause("Click")
        if paused:
            return paused
        budget_block = _check_action_budget("click", amount=1)
        if budget_block:
            return budget_block
        desktop.validate_screen_point(x, y)
        if action == "hover":
            pyautogui.moveTo(x, y)
            return f"Hovered at ({x},{y})"
        elif action == "double":
            pyautogui.doubleClick(x, y, button=button)
            return f"Double-clicked {button} at ({x},{y})"
        else:
            pyautogui.click(x, y, button=button)
            return f"Clicked {button} at ({x},{y})"
    except Exception as e:
        return f"Click error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Type",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Type(
    text: str,
    x: int = 0,
    y: int = 0,
    clear: bool | str = False,
    press_enter: bool | str = False,
) -> str:
    """Type text, optionally at specific coordinates.

    Args:
        text: Text to type.
        x: X coordinate (0 = current position).
        y: Y coordinate (0 = current position).
        clear: Clear existing content first (Ctrl+A, Delete).
        press_enter: Press Enter after typing.
    """
    try:
        paused = _check_handoff_pause("Type")
        if paused:
            return paused
        keystroke_budget = max(1, len(text or ""))
        budget_block = _check_action_budget("keystroke", amount=keystroke_budget)
        if budget_block:
            return budget_block
        if x and y:
            pyautogui.click(x, y)
            time.sleep(0.1)
        if _tobool(clear):
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("delete")
            time.sleep(0.05)
        pyautogui.typewrite(text, interval=0.02) if text.isascii() else pyautogui.write(text)
        if _tobool(press_enter):
            pyautogui.press("enter")
        return f"Typed {len(text)} chars"
    except Exception as e:
        return f"Type error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Scroll",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Scroll(
    amount: int,
    x: int = 0,
    y: int = 0,
    horizontal: bool | str = False,
) -> str:
    """Scroll at a position.

    Args:
        amount: Scroll amount (positive=up/right, negative=down/left).
        x: X coordinate (0 = current).
        y: Y coordinate (0 = current).
        horizontal: Horizontal scroll instead of vertical.
    """
    try:
        if x and y:
            pyautogui.moveTo(x, y)
        if _tobool(horizontal):
            pyautogui.hscroll(amount)
        else:
            pyautogui.scroll(amount)
        direction = "horizontally" if _tobool(horizontal) else "vertically"
        return f"Scrolled {amount} {direction}"
    except Exception as e:
        return f"Scroll error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Move",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Move(
    x: int,
    y: int,
    drag: bool | str = False,
    start_x: int = 0,
    start_y: int = 0,
    duration: float = 0.3,
) -> str:
    """Move mouse or drag to position.

    Args:
        x: Target X.
        y: Target Y.
        drag: If true, drag from start position to target.
        start_x: Drag start X.
        start_y: Drag start Y.
        duration: Movement duration in seconds.
    """
    try:
        if _tobool(drag):
            if start_x and start_y:
                pyautogui.moveTo(start_x, start_y)
            pyautogui.drag(x - pyautogui.position()[0], y - pyautogui.position()[1], duration=duration)
            return f"Dragged to ({x},{y})"
        else:
            pyautogui.moveTo(x, y, duration=duration)
            return f"Moved to ({x},{y})"
    except Exception as e:
        return f"Move error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Shortcut",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Shortcut(keys: str) -> str:
    """Execute keyboard shortcut.

    Args:
        keys: Shortcut string, e.g. 'ctrl+c', 'alt+tab', 'win+e'.
    """
    try:
        parts = [k.strip() for k in keys.lower().split("+")]
        pyautogui.hotkey(*parts)
        return f"Executed shortcut: {keys}"
    except Exception as e:
        return f"Shortcut error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Wait",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def Wait(seconds: float = 1.0) -> str:
    """Pause execution.

    Args:
        seconds: Seconds to wait.
    """
    time.sleep(seconds)
    return f"Waited {seconds}s"


@mcp.tool(
    annotations=ToolAnnotations(
        title="WaitForChange",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def WaitForChange(
    watch_for: str = "screen_change",
    target: str = "",
    timeout_seconds: float = 30.0,
    poll_interval_ms: int = 500,
) -> str:
    """Wait for a change condition and return evidence.

    Args:
        watch_for: One of screen_change, window_title, process_start,
            process_stop, text_appears, text_disappears, file_created,
            file_modified, terminal_output.
        target: Condition target (text/process name/path/title fragment).
        timeout_seconds: Max wait time.
        poll_interval_ms: Polling interval in milliseconds.
    """
    mode = str(watch_for or "screen_change").strip().lower()
    timeout_seconds = max(0.0, float(timeout_seconds))
    poll_interval = max(0.05, float(poll_interval_ms) / 1000.0)
    deadline = time.monotonic() + timeout_seconds

    try:
        if mode == "screen_change":
            payload = _wait_for_image_change(
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
                min_change_ratio=0.01,
                grid_size=6,
            )
            payload["watch_for"] = mode
            payload["target"] = target or None
            payload["observation"] = _compact_observation_payload(payload.get("observation"))
            return json.dumps(payload, indent=2)

        if mode == "window_title":
            err = _check_win32("WaitForChange")
            if err:
                return err
            baseline_hwnd = desktop.win32gui.GetForegroundWindow() if desktop.HAS_WIN32 else 0
            baseline_title = desktop.win32gui.GetWindowText(baseline_hwnd) if baseline_hwnd else ""
            target_l = target.strip().lower()

            while True:
                hwnd = desktop.win32gui.GetForegroundWindow() if desktop.HAS_WIN32 else 0
                title = desktop.win32gui.GetWindowText(hwnd) if hwnd else ""
                changed = title != baseline_title
                matched = (target_l in title.lower()) if target_l else changed
                if matched:
                    return json.dumps(
                        {
                            "watch_for": mode,
                            "satisfied": True,
                            "timed_out": False,
                            "target": target or None,
                            "baseline_title": baseline_title,
                            "current_title": title,
                            "current_handle": hwnd,
                        },
                        indent=2,
                    )
                if time.monotonic() >= deadline:
                    return json.dumps(
                        {
                            "watch_for": mode,
                            "satisfied": False,
                            "timed_out": True,
                            "target": target or None,
                            "baseline_title": baseline_title,
                            "current_title": title,
                            "current_handle": hwnd,
                        },
                        indent=2,
                    )
                time.sleep(poll_interval)

        if mode in {"process_start", "process_stop"}:
            needle = target.strip().lower()
            if not needle:
                return "WaitForChange error: target process name is required for process_start/process_stop"

            baseline_count = len(_process_matches(name=needle))
            while True:
                count = len(_process_matches(name=needle))
                satisfied = (count > 0) if mode == "process_start" else (count == 0)
                if satisfied:
                    return json.dumps(
                        {
                            "watch_for": mode,
                            "target": target,
                            "satisfied": True,
                            "timed_out": False,
                            "baseline_count": baseline_count,
                            "current_count": count,
                        },
                        indent=2,
                    )
                if time.monotonic() >= deadline:
                    return json.dumps(
                        {
                            "watch_for": mode,
                            "target": target,
                            "satisfied": False,
                            "timed_out": True,
                            "baseline_count": baseline_count,
                            "current_count": count,
                        },
                        indent=2,
                    )
                time.sleep(poll_interval)

        if mode in {"text_appears", "text_disappears", "terminal_output"}:
            needle = target.strip().lower()
            if not needle:
                return f"WaitForChange error: target text is required for {mode}"

            while True:
                text = ocr.run_ocr() or ""
                has_match = needle in text.lower()
                satisfied = has_match if mode in {"text_appears", "terminal_output"} else not has_match
                if satisfied:
                    return json.dumps(
                        {
                            "watch_for": mode,
                            "target": target,
                            "satisfied": True,
                            "timed_out": False,
                            "matched": has_match,
                            "text_excerpt": text[:1000],
                        },
                        indent=2,
                    )
                if time.monotonic() >= deadline:
                    return json.dumps(
                        {
                            "watch_for": mode,
                            "target": target,
                            "satisfied": False,
                            "timed_out": True,
                            "matched": has_match,
                            "text_excerpt": text[:1000],
                        },
                        indent=2,
                    )
                time.sleep(poll_interval)

        if mode in {"file_created", "file_modified"}:
            if not target.strip():
                return f"WaitForChange error: target path is required for {mode}"
            path = Path(target).expanduser()
            baseline_exists = path.exists()
            baseline_mtime = path.stat().st_mtime if baseline_exists else None

            while True:
                exists = path.exists()
                mtime = path.stat().st_mtime if exists else None
                satisfied = False
                if mode == "file_created":
                    satisfied = exists
                elif mode == "file_modified":
                    satisfied = exists and baseline_mtime is not None and mtime is not None and mtime > baseline_mtime

                if satisfied:
                    return json.dumps(
                        {
                            "watch_for": mode,
                            "target": str(path),
                            "satisfied": True,
                            "timed_out": False,
                            "baseline_exists": baseline_exists,
                            "exists": exists,
                            "baseline_mtime": baseline_mtime,
                            "mtime": mtime,
                        },
                        indent=2,
                    )
                if time.monotonic() >= deadline:
                    return json.dumps(
                        {
                            "watch_for": mode,
                            "target": str(path),
                            "satisfied": False,
                            "timed_out": True,
                            "baseline_exists": baseline_exists,
                            "exists": exists,
                            "baseline_mtime": baseline_mtime,
                            "mtime": mtime,
                        },
                        indent=2,
                    )
                time.sleep(poll_interval)

        return (
            "WaitForChange error: watch_for must be one of "
            "screen_change, window_title, process_start, process_stop, text_appears, "
            "text_disappears, file_created, file_modified, terminal_output"
        )
    except Exception as e:
        return f"WaitForChange error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="KeyDown",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def KeyDown(key: str) -> str:
    """Press and hold a keyboard key until KeyUp is called."""
    try:
        pyautogui.keyDown(str(key).strip().lower())
        return f"Held key down: {key}"
    except Exception as e:
        return f"KeyDown error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="KeyUp",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def KeyUp(key: str) -> str:
    """Release a previously held keyboard key."""
    try:
        pyautogui.keyUp(str(key).strip().lower())
        return f"Released key: {key}"
    except Exception as e:
        return f"KeyUp error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="HoldKeys",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def HoldKeys(keys: str, duration_seconds: float = 1.0) -> str:
    """Hold one or more keys for a duration, then release them."""
    normalized = _normalized_key_list(keys)
    try:
        for key in normalized:
            pyautogui.keyDown(key)
        time.sleep(max(0.0, float(duration_seconds)))
        for key in reversed(normalized):
            pyautogui.keyUp(key)
        return f"Held keys {', '.join(normalized)} for {duration_seconds}s"
    except Exception as e:
        return f"HoldKeys error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="MouseDown",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def MouseDown(button: str = "left", x: int = 0, y: int = 0) -> str:
    """Hold a mouse button down, optionally moving first."""
    try:
        if x or y:
            desktop.validate_screen_point(x, y)
            pyautogui.moveTo(x, y)
        pyautogui.mouseDown(button=button)
        return f"Held mouse button down: {button}"
    except Exception as e:
        return f"MouseDown error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="MouseUp",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def MouseUp(button: str = "left", x: int = 0, y: int = 0) -> str:
    """Release a mouse button, optionally moving first."""
    try:
        if x or y:
            desktop.validate_screen_point(x, y)
            pyautogui.moveTo(x, y)
        pyautogui.mouseUp(button=button)
        return f"Released mouse button: {button}"
    except Exception as e:
        return f"MouseUp error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="MouseMoveRelative",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def MouseMoveRelative(dx: int, dy: int, duration: float = 0.0) -> str:
    """Move the cursor relative to its current position."""
    try:
        x, y = pyautogui.position()
        target_x = x + int(dx)
        target_y = y + int(dy)
        desktop.validate_screen_point(target_x, target_y)
        if hasattr(pyautogui, "moveRel"):
            pyautogui.moveRel(dx, dy, duration=duration)
        else:
            pyautogui.moveTo(target_x, target_y, duration=duration)
        return f"Moved mouse relatively by ({dx},{dy})"
    except Exception as e:
        return f"MouseMoveRelative error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="MouseLook",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def MouseLook(dx: int, dy: int, duration: float = 0.1, steps: int = 3) -> str:
    """Perform a segmented relative mouse move useful for camera/look controls."""
    try:
        steps = max(1, int(steps))
        step_dx = dx / steps
        step_dy = dy / steps
        step_duration = max(0.0, float(duration)) / steps
        for _ in range(steps):
            if hasattr(pyautogui, "moveRel"):
                pyautogui.moveRel(step_dx, step_dy, duration=step_duration)
            else:
                x, y = pyautogui.position()
                pyautogui.moveTo(x + step_dx, y + step_dy, duration=step_duration)
        return f"Performed mouse look by ({dx},{dy}) in {steps} steps"
    except Exception as e:
        return f"MouseLook error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="WaitForRegionText",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def WaitForRegionText(
    query: str,
    left: int,
    top: int,
    right: int,
    bottom: int,
    wait_until: str = "appear",
    timeout_seconds: float = 5.0,
    poll_interval: float = 0.5,
    lang: str = "eng",
) -> str:
    """Wait until OCR text appears or disappears within a screen region."""
    try:
        payload = _wait_for_region_text(
            query=query,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            wait_until=wait_until,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
            lang=lang,
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"WaitForRegionText error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="WaitForImageChange",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def WaitForImageChange(
    window_title: str = "",
    monitor: int = 0,
    left: int = 0,
    top: int = 0,
    right: int = 0,
    bottom: int = 0,
    timeout_seconds: float = 5.0,
    poll_interval: float = 0.25,
    min_change_ratio: float = 0.02,
    grid_size: int = 6,
) -> str:
    """Wait until a window, monitor, or region visibly changes."""
    try:
        payload = _wait_for_image_change(
            window_title=window_title,
            monitor=monitor,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
            min_change_ratio=min_change_ratio,
            grid_size=grid_size,
        )
        payload["observation"] = _compact_observation_payload(payload.get("observation"))
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"WaitForImageChange error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="AssertWindowActive",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def AssertWindowActive(title: str) -> str:
    """Assert that the active window matches a title fragment."""
    err = _check_win32("AssertWindowActive")
    if err:
        return err
    try:
        if not getattr(desktop, "HAS_WIN32", False):
            return json.dumps({"ok": False, "matched": False, "reason": "pywin32 not available"})
        foreground_handle = desktop.win32gui.GetForegroundWindow()
        foreground_title = desktop.win32gui.GetWindowText(foreground_handle) if foreground_handle else ""
        matched = title.strip().lower() in foreground_title.strip().lower()
        return json.dumps(
            {
                "ok": matched,
                "matched": matched,
                "expected_title": title,
                "window": {"handle": foreground_handle, "title": foreground_title},
            },
            indent=2,
        )
    except Exception as e:
        return f"AssertWindowActive error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="AssertProcessRunning",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def AssertProcessRunning(name: str = "", pid: int = 0) -> str:
    """Assert that a process is running by name fragment or PID."""
    try:
        matches = _process_matches(pid=pid, name=name)
        return json.dumps(
            {
                "ok": bool(matches),
                "matched": bool(matches),
                "query": {"name": name or None, "pid": pid or None},
                "matches": matches[:10],
                "count": len(matches),
            },
            indent=2,
        )
    except Exception as e:
        return f"AssertProcessRunning error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="AppHealthCheck",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def AppHealthCheck(process_name: str = "", window_title: str = "") -> str:
    """Check if an app is running, visible, active, and responsive."""
    try:
        process_query = process_name.strip()
        title_query = window_title.strip().lower()

        process_matches = _process_matches(name=process_query) if process_query else []
        windows = desktop.enumerate_windows() if desktop.HAS_WIN32 else []
        matching_windows = [w for w in windows if title_query in w.title.lower()] if title_query else []

        active_hwnd = desktop.win32gui.GetForegroundWindow() if desktop.HAS_WIN32 else 0
        active_title = desktop.win32gui.GetWindowText(active_hwnd) if desktop.HAS_WIN32 and active_hwnd else ""

        responsive = True
        if desktop.HAS_WIN32:
            try:
                import ctypes

                for w in matching_windows[:1]:
                    if hasattr(ctypes.windll.user32, "IsHungAppWindow"):
                        responsive = not bool(ctypes.windll.user32.IsHungAppWindow(int(w.handle)))
            except Exception:
                responsive = True

        app_window = matching_windows[0] if matching_windows else None
        running = bool(process_matches) if process_query else bool(matching_windows)
        active = bool(active_title and ((title_query and title_query in active_title.lower()) or (app_window and app_window.handle == active_hwnd)))

        payload = {
            "running": running,
            "responsive": responsive,
            "visible": bool(app_window.visible) if app_window else False,
            "active": active,
            "process_name": process_query or (process_matches[0]["name"] if process_matches else None),
            "pid": process_matches[0]["pid"] if process_matches else (app_window.pid if app_window else None),
            "window_title": app_window.title if app_window else None,
            "window_handle": app_window.handle if app_window else None,
            "reason": (
                "No matching process or window found"
                if not running
                else "App appears healthy" if responsive else "App window may be unresponsive"
            ),
        }
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"AppHealthCheck error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ListMonitors",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ListMonitors() -> str:
    """List monitor metadata and virtual-screen layout."""
    try:
        monitors = desktop.get_monitor_info()
        return json.dumps(
            {
                "count": len(monitors),
                "monitors": monitors,
                "virtual_screen": desktop.get_virtual_screen_bounds(monitors),
            },
            indent=2,
        )
    except Exception as e:
        return f"ListMonitors error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetActiveWindow",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetActiveWindow() -> str:
    """Get active foreground window metadata."""
    err = _check_win32("GetActiveWindow")
    if err:
        return err
    try:
        hwnd = desktop.win32gui.GetForegroundWindow()
        title = desktop.win32gui.GetWindowText(hwnd) if hwnd else ""
        windows = desktop.enumerate_windows()
        active = next((w for w in windows if w.handle == hwnd), None)
        if active is None:
            return json.dumps({"handle": hwnd, "title": title, "found": False}, indent=2)
        return json.dumps(
            {
                "found": True,
                "handle": active.handle,
                "title": active.title,
                "pid": active.pid,
                "process_name": active.process_name,
                "monitor_id": active.monitor_id,
                "rect": {
                    "left": active.rect[0],
                    "top": active.rect[1],
                    "right": active.rect[2],
                    "bottom": active.rect[3],
                },
            },
            indent=2,
        )
    except Exception as e:
        return f"GetActiveWindow error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetWindowBounds",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetWindowBounds(window_id: str) -> str:
    """Get window bounds by id format hwnd:<number> or raw integer string."""
    err = _check_win32("GetWindowBounds")
    if err:
        return err
    try:
        raw = window_id.strip()
        if raw.lower().startswith("hwnd:"):
            raw = raw.split(":", 1)[1].strip()
        hwnd = int(raw)
        rect = desktop.win32gui.GetWindowRect(hwnd)
        return json.dumps(
            {
                "window_id": f"hwnd:{hwnd}",
                "rect": {
                    "left": rect[0],
                    "top": rect[1],
                    "right": rect[2],
                    "bottom": rect[3],
                    "width": max(0, rect[2] - rect[0]),
                    "height": max(0, rect[3] - rect[1]),
                },
            },
            indent=2,
        )
    except Exception as e:
        return f"GetWindowBounds error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="AgentStatusReport",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def AgentStatusReport(
    include_windows: bool | str = True,
    include_recent_actions: bool | str = True,
    include_errors: bool | str = True,
    include_recommended_next_tool: bool | str = True,
) -> str:
    """Return a concise snapshot of current desktop/agent state."""
    try:
        windows = desktop.enumerate_windows() if desktop.HAS_WIN32 else []
        active_hwnd = desktop.win32gui.GetForegroundWindow() if desktop.HAS_WIN32 else 0
        active_window = next((w for w in windows if w.handle == active_hwnd), None)

        running_tasks = task_manager.list_tasks("running")
        pending_tasks = task_manager.list_tasks("pending")
        active_recordings = recording.list_active_recordings()

        errors: list[str] = []
        if _tobool(include_errors):
            if active_window and "not responding" in (active_window.title or "").lower():
                errors.append("Active window title indicates potential unresponsive app")
            if not active_window:
                errors.append("No active foreground window detected")

        recommended_next_tool = None
        if _tobool(include_recommended_next_tool):
            if running_tasks:
                recommended_next_tool = "WaitForChange"
            elif active_recordings:
                recommended_next_tool = "StopScreenRecording"
            elif errors:
                recommended_next_tool = "CaptureFailureBundle"
            else:
                recommended_next_tool = "ObserveScreen"

        payload: dict[str, Any] = {
            "active_window": (
                {
                    "handle": active_window.handle,
                    "title": active_window.title,
                    "pid": active_window.pid,
                    "process_name": active_window.process_name,
                    "monitor_id": active_window.monitor_id,
                    "rect": {
                        "left": active_window.rect[0],
                        "top": active_window.rect[1],
                        "right": active_window.rect[2],
                        "bottom": active_window.rect[3],
                    },
                }
                if active_window
                else None
            ),
            "recording_active": bool(active_recordings),
            "active_recordings": active_recordings,
            "running_tasks": running_tasks,
            "pending_tasks": pending_tasks,
            "errors": errors,
            "recommended_next_tool": recommended_next_tool,
        }

        if _tobool(include_windows):
            payload["windows"] = [
                {
                    "handle": w.handle,
                    "title": w.title,
                    "pid": w.pid,
                    "process_name": w.process_name,
                    "monitor_id": w.monitor_id,
                }
                for w in windows[:30]
            ]

        if _tobool(include_recent_actions):
            payload["recent_tasks"] = task_manager.list_tasks()[:10]

        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"AgentStatusReport error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="TailFile",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def TailFile(path: str, lines: int = 100, encoding: str = "utf-8", contains: str = "") -> str:
    """Read the tail of a text file, optionally filtering matching lines."""
    try:
        payload = roblox_studio.tail_file(path, lines=lines, encoding=encoding, contains=contains)
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"TailFile error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="CaptureFailureBundle",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def CaptureFailureBundle(
    window_title: str = "",
    log_path: str = "",
    log_lines: int = 120,
    use_vision: bool | str = True,
    quality: int = 60,
    max_width: int = 1600,
) -> list:
    """Capture a compact debugging bundle with screenshot, observation, processes, and recent logs."""
    try:
        parts = []
        use_vision = _tobool(use_vision)
        if use_vision:
            parts.extend(Snapshot(use_vision=True, quality=quality, max_width=max_width))

        observation = desktop.observe_screen(window_title=window_title, reset=False, update_baseline=False)
        processes = _process_matches(name="Roblox")[:10]
        logs = None
        if log_path:
            logs = roblox_studio.tail_file(log_path, lines=log_lines)
        else:
            try:
                logs = roblox_studio.read_latest_studio_errors(lines=log_lines)
            except Exception:
                logs = None

        payload = {
            "window_title": window_title or None,
            "observation": _compact_observation_payload(observation),
            "processes": processes,
            "logs": logs,
        }
        parts.append(TextContent(type="text", text=json.dumps(payload, indent=2)))
        return parts
    except Exception as e:
        return [f"CaptureFailureBundle error: {e}"]


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioRunPlaytest",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def RobloxStudioRunPlaytest(
    mode: str = "play_solo",
    focus_window: bool | str = True,
    shortcut_override: str = "",
    timeout_seconds: float = 5.0,
) -> str:
    """Start a Roblox Studio playtest and wait for visible change."""
    try:
        shortcuts = {
            "play_solo": "f5",
        }
        mode_key = str(mode or "play_solo").strip().lower()
        shortcut = shortcut_override.strip() or shortcuts.get(mode_key)
        if not shortcut:
            return (
                "RobloxStudioRunPlaytest error: unsupported mode. "
                "Use mode='play_solo' or provide shortcut_override."
            )

        focus_result = _studio_focus() if _tobool(focus_window) else None
        pre_payload = desktop.observe_screen(window_title="Roblox Studio", reset=True, update_baseline=True)
        pyautogui.hotkey(*_normalized_key_list(shortcut))
        post_payload = _wait_for_image_change(
            window_title="Roblox Studio",
            timeout_seconds=max(0.5, float(timeout_seconds)),
            poll_interval=0.25,
            min_change_ratio=0.01,
            reset_baseline=False,
        )
        latest_log = roblox_studio.find_latest_studio_log()
        payload = {
            "status": "completed",
            "mode": mode_key,
            "shortcut": shortcut,
            "focus_result": focus_result,
            "pre_observation": _compact_observation_payload(pre_payload),
            "post_observation": _compact_observation_payload(post_payload.get("observation")),
            "changed": post_payload.get("satisfied"),
            "studio_log_path": str(latest_log) if latest_log else None,
        }
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"RobloxStudioRunPlaytest error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioStopPlaytest",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def RobloxStudioStopPlaytest(
    focus_window: bool | str = True,
    shortcut_override: str = "",
    timeout_seconds: float = 5.0,
) -> str:
    """Stop a Roblox Studio playtest and wait for visible change."""
    try:
        shortcut = shortcut_override.strip() or "shift+f5"
        focus_result = _studio_focus() if _tobool(focus_window) else None
        pre_payload = desktop.observe_screen(window_title="Roblox Studio", reset=True, update_baseline=True)
        pyautogui.hotkey(*_normalized_key_list(shortcut))
        post_payload = _wait_for_image_change(
            window_title="Roblox Studio",
            timeout_seconds=max(0.5, float(timeout_seconds)),
            poll_interval=0.25,
            min_change_ratio=0.01,
            reset_baseline=False,
        )
        payload = {
            "status": "completed",
            "shortcut": shortcut,
            "focus_result": focus_result,
            "pre_observation": _compact_observation_payload(pre_payload),
            "post_observation": _compact_observation_payload(post_payload.get("observation")),
            "changed": post_payload.get("satisfied"),
        }
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"RobloxStudioStopPlaytest error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioGetOutput",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def RobloxStudioGetOutput(lines: int = 200, contains: str = "") -> str:
    """Tail the latest Roblox Studio log, useful as an output/error stream."""
    try:
        payload = roblox_studio.read_latest_studio_log(lines=lines, contains=contains)
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"RobloxStudioGetOutput error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioGetErrors",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def RobloxStudioGetErrors(lines: int = 200) -> str:
    """Return likely error/warning lines from the latest Roblox Studio log."""
    try:
        payload = roblox_studio.read_latest_studio_errors(lines=lines)
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"RobloxStudioGetErrors error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioGetTestState",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def RobloxStudioGetTestState(harness_url: str = "", timeout_seconds: float = 5.0) -> str:
    """Query a local Roblox Studio test harness for structured runtime state."""
    payload = roblox_studio.harness_request("GET", "/state", harness_url=harness_url, timeout=timeout_seconds)
    return json.dumps(payload, indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioResetCharacter",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def RobloxStudioResetCharacter(harness_url: str = "", timeout_seconds: float = 5.0) -> str:
    """Reset the current Studio playtest character through a local test harness."""
    payload = _studio_harness_payload(
        "/reset-character",
        payload={"wait": True, "timeout_seconds": timeout_seconds},
        harness_url=harness_url,
        timeout=timeout_seconds,
    )
    return json.dumps(payload, indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioTeleportToCheckpoint",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def RobloxStudioTeleportToCheckpoint(checkpoint_id: str, harness_url: str = "", timeout_seconds: float = 5.0) -> str:
    """Teleport to a named checkpoint through a local Studio harness."""
    payload = _studio_harness_payload(
        "/teleport-checkpoint",
        payload={"checkpoint_id": checkpoint_id, "wait": True, "timeout_seconds": timeout_seconds},
        harness_url=harness_url,
        timeout=timeout_seconds,
    )
    return json.dumps(payload, indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioRunNamedTest",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def RobloxStudioRunNamedTest(
    test_name: str,
    payload_json: str = "",
    harness_url: str = "",
    timeout_seconds: float = 30.0,
) -> str:
    """Run a named Studio-side test through a local harness."""
    try:
        payload: dict[str, Any] = {"test_name": test_name}
        if payload_json.strip():
            extra = json.loads(payload_json)
            if not isinstance(extra, dict):
                return "RobloxStudioRunNamedTest error: payload_json must decode to an object"
            payload.update(extra)
        payload.setdefault("wait", True)
        payload.setdefault("timeout_seconds", timeout_seconds)
        result = _studio_harness_payload("/run-test", payload=payload, harness_url=harness_url, timeout=timeout_seconds)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"RobloxStudioRunNamedTest error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioInspectUI",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def RobloxStudioInspectUI(
    query: str = "",
    include_ribbon: bool | str = False,
    focus_window: bool | str = True,
    refresh: bool | str = False,
    max_results: int = 8,
) -> str:
    """Inspect Roblox Studio editor chrome in a structured, low-token format.

    Returns likely tab, panel, and optional ribbon-command regions so agents can
    reason about the Studio layout without escalating immediately to screenshots.
    """
    try:
        focus_result = _studio_focus() if _tobool(focus_window) else None
        if _tobool(refresh):
            desktop.invalidate_ui_map_cache("Roblox Studio")
        window = _studio_window_payload()
        inspection = roblox_studio.inspect_studio_ui_regions(
            window,
            query=query.strip(),
            max_results=max_results,
            use_cache=not _tobool(refresh),
            include_ribbon=_tobool(include_ribbon),
        )
        payload = {
            "status": "completed",
            "query": query or None,
            "include_ribbon": _tobool(include_ribbon),
            "focus_result": focus_result,
            "window": window,
            "inspection_mode": inspection.get("inspection_mode"),
            "tabs": [_compact_studio_candidate(item) for item in (inspection.get("tabs") or [])],
            "panels": [_compact_studio_candidate(item) for item in (inspection.get("panels") or [])],
            "ribbon_regions": [_compact_studio_candidate(item) for item in (inspection.get("ribbon_regions") or [])],
            "matches": [_compact_studio_candidate(item) for item in (inspection.get("matches") or [])],
            "searchable_preview": inspection.get("searchable_preview") or [],
            "recommendations": _trim_recommendations(inspection.get("notes"), limit=5),
            "coordinate_spaces": _ui_coordinate_spaces(),
        }
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"RobloxStudioInspectUI error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioOpenTab",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def RobloxStudioOpenTab(tab_name: str, focus_window: bool | str = True, timeout_seconds: float = 2.0) -> str:
    """Open a Roblox Studio ribbon tab such as Home, Model, Test, View, or Plugins."""
    try:
        payload = _studio_open_tab_action(
            tab_name,
            focus_window=_tobool(focus_window),
            timeout_seconds=timeout_seconds,
        )
        payload["coordinate_spaces"] = _ui_coordinate_spaces()
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"RobloxStudioOpenTab error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="RobloxStudioEnsurePanel",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def RobloxStudioEnsurePanel(panel_name: str, focus_window: bool | str = True, timeout_seconds: float = 2.0) -> str:
    """Ensure a common Roblox Studio editor panel is visible and return its likely bounds.

    Supported panels: Toolbox, Explorer, Properties, Output.
    """
    try:
        canonical_panel = roblox_studio.normalize_studio_panel_name(panel_name)
        focus_result = _studio_focus() if _tobool(focus_window) else None
        desktop.invalidate_ui_map_cache("Roblox Studio")
        window_before = _studio_window_payload()
        inspection_before = roblox_studio.inspect_studio_ui_regions(
            window_before,
            query=canonical_panel,
            use_cache=False,
        )
        visible_matches = [
            item for item in (inspection_before.get("matches") or []) if str(item.get("class") or "") == "RobloxStudioOCRRegion"
        ]
        if visible_matches:
            target = visible_matches[0]
            _studio_click_target(target)
            payload = {
                "status": "already-visible",
                "panel_name": canonical_panel,
                "focus_result": focus_result,
                "window": window_before,
                "target": _compact_studio_candidate(target),
                "inspection_before": _compact_studio_inspection(inspection_before),
                "satisfied": True,
                "recommendations": [
                    f"{canonical_panel} already looked visible, so the tool focused its dock region instead of toggling more UI.",
                ],
                "coordinate_spaces": _ui_coordinate_spaces(),
            }
            return json.dumps(payload, indent=2)

        view_tab = _studio_open_tab_action("View", focus_window=False, timeout_seconds=max(0.5, timeout_seconds))
        if view_tab.get("status") != "completed":
            payload = {
                "status": "no-match",
                "panel_name": canonical_panel,
                "focus_result": focus_result,
                "window": window_before,
                "inspection_before": _compact_studio_inspection(inspection_before),
                "view_tab": view_tab,
                "satisfied": False,
                "recommendations": [
                    f"Could not activate the View tab, so {canonical_panel} could not be ensured automatically.",
                ],
                "coordinate_spaces": _ui_coordinate_spaces(),
            }
            return json.dumps(payload, indent=2)

        desktop.invalidate_ui_map_cache("Roblox Studio")
        window_ribbon = _studio_window_payload()
        ribbon_inspection = roblox_studio.inspect_studio_ui_regions(
            window_ribbon,
            query=canonical_panel,
            include_ribbon=True,
            use_cache=False,
        )
        ribbon_matches = [
            item for item in (ribbon_inspection.get("matches") or []) if str(item.get("class") or "") == "RobloxStudioRibbonOCRRegion"
        ]
        if not ribbon_matches:
            payload = {
                "status": "no-match",
                "panel_name": canonical_panel,
                "focus_result": focus_result,
                "window": window_ribbon,
                "inspection_before": _compact_studio_inspection(inspection_before),
                "view_tab": view_tab,
                "ribbon_inspection": _compact_studio_inspection(ribbon_inspection),
                "satisfied": False,
                "recommendations": [
                    f"The View ribbon was opened, but no OCR ribbon slot matched {canonical_panel}. Use RobloxStudioInspectUI(include_ribbon=true) for a refreshed editor snapshot.",
                ],
                "coordinate_spaces": _ui_coordinate_spaces(),
            }
            return json.dumps(payload, indent=2)

        ribbon_target = ribbon_matches[0]
        pre_observation = desktop.observe_screen(window_title="Roblox Studio", reset=True, update_baseline=True)
        _studio_click_target(ribbon_target)
        desktop.invalidate_ui_map_cache("Roblox Studio")
        post_wait = _wait_for_image_change(
            window_title="Roblox Studio",
            timeout_seconds=max(0.5, float(timeout_seconds)),
            poll_interval=0.2,
            min_change_ratio=0.005,
            reset_baseline=False,
        )
        desktop.invalidate_ui_map_cache("Roblox Studio")
        window_after = _studio_window_payload()
        inspection_after = roblox_studio.inspect_studio_ui_regions(
            window_after,
            query=canonical_panel,
            use_cache=False,
        )
        after_visible_matches = [
            item for item in (inspection_after.get("matches") or []) if str(item.get("class") or "") == "RobloxStudioOCRRegion"
        ]
        target_after = after_visible_matches[0] if after_visible_matches else ribbon_target
        satisfied = bool(after_visible_matches)
        payload = {
            "status": "completed" if satisfied else "uncertain",
            "panel_name": canonical_panel,
            "focus_result": focus_result,
            "window": window_after,
            "target": _compact_studio_candidate(target_after),
            "view_tab": view_tab,
            "inspection_before": _compact_studio_inspection(inspection_before),
            "ribbon_inspection": _compact_studio_inspection(ribbon_inspection),
            "inspection_after": _compact_studio_inspection(inspection_after),
            "observation_before": _compact_observation_payload(pre_observation),
            "observation_after": _compact_observation_payload(post_wait.get("observation")),
            "changed": post_wait.get("satisfied"),
            "satisfied": satisfied,
            "recommendations": [
                (
                    f"{canonical_panel} now looks visible in the Studio dock layout."
                    if satisfied
                    else f"{canonical_panel} was toggled from the View ribbon, but the dock OCR did not confirm visibility. Use RobloxStudioInspectUI for a fresh panel snapshot before escalating to screenshots."
                ),
            ],
            "coordinate_spaces": _ui_coordinate_spaces(),
        }
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"RobloxStudioEnsurePanel error: {e}"


# =========================== WINDOW MANAGEMENT ============================


@mcp.tool(
    annotations=ToolAnnotations(
        title="FocusWindow",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def FocusWindow(title: str = "", handle: int = 0) -> str:
    """Bring a window to the foreground.

    Args:
        title: Window title (fuzzy matched).
        handle: Window handle (exact).
    """
    err = _check_win32("FocusWindow")
    if err:
        return err
    try:
        return desktop.focus_window(title=title or None, handle=handle or None)
    except Exception as e:
        return f"FocusWindow error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="MinimizeAll",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def MinimizeAll() -> str:
    """Minimize all windows (Win+D — show desktop)."""
    try:
        return desktop.minimize_all()
    except Exception as e:
        return f"MinimizeAll error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="App",
        destructiveHint=False,
        openWorldHint=True,
    )
)
def App(
    action: str = "launch",
    name: str = "",
    args: str = "",
    handle: int = 0,
    width: int = 0,
    height: int = 0,
) -> str:
    """Launch, switch to, or resize an application.

    Args:
        action: 'launch', 'switch', or 'resize'.
        name: Application name or path (for launch/switch).
        args: Arguments (for launch).
        handle: Window handle (for resize/switch).
        width: New width (for resize).
        height: New height (for resize).
    """
    try:
        if action == "launch":
            return desktop.launch_app(name, args)
        elif action == "switch":
            err = _check_win32("App(switch)")
            if err:
                return err
            return desktop.focus_window(title=name or None, handle=handle or None)
        elif action == "resize":
            err = _check_win32("App(resize)")
            if err:
                return err
            if not handle:
                return "resize requires a window handle"
            return desktop.resize_window(handle, width, height)
        return f"Unknown action: {action}"
    except Exception as e:
        return f"App error: {e}"


# =========================== REMOTE MANAGEMENT ============================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Shell",
        destructiveHint=True,
        openWorldHint=True,
    )
)
def Shell(command: str, timeout: int = 30, cwd: str = "") -> str:
    """Execute a PowerShell command.

    Args:
        command: PowerShell command to execute.
        timeout: Timeout in seconds (default 30).
        cwd: Working directory. If provided, the command runs inside that directory.
    """
    try:
        budget_block = _check_action_budget("shell", amount=1)
        if budget_block:
            return budget_block
        if cwd:
            command = f"cd {cwd}; {command}"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            action_budget.record_success("shell")
        else:
            action_budget.record_failure("shell")
        output = result.stdout
        if result.stderr:
            output += f"\n[STDERR] {result.stderr}"
        if result.returncode != 0:
            output += f"\n[Exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        action_budget.record_failure("shell")
        return f"Command timed out after {timeout}s"
    except Exception as e:
        action_budget.record_failure("shell")
        return f"Shell error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetClipboard",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetClipboard() -> str:
    """Read the Windows clipboard text content."""
    err = _check_win32("GetClipboard")
    if err:
        return err
    try:
        return desktop.get_clipboard()
    except Exception as e:
        return f"GetClipboard error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="SetClipboard",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def SetClipboard(text: str) -> str:
    """Set the Windows clipboard text content.

    Args:
        text: Text to place on clipboard.
    """
    err = _check_win32("SetClipboard")
    if err:
        return err
    try:
        return desktop.set_clipboard(text)
    except Exception as e:
        return f"SetClipboard error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ClipboardSafeRead",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ClipboardSafeRead(redact_secrets: bool | str = True) -> str:
    """Read clipboard text with optional secret redaction."""
    err = _check_win32("ClipboardSafeRead")
    if err:
        return err
    try:
        text = desktop.get_clipboard()
        if not isinstance(text, str):
            text = str(text)
        if _tobool(redact_secrets):
            text = redaction.redact_text(text, RedactionConfig().patterns)
        return json.dumps(
            {
                "success": True,
                "length": len(text),
                "redacted": _tobool(redact_secrets),
                "text": text,
            },
            indent=2,
        )
    except Exception as e:
        return f"ClipboardSafeRead error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ClipboardSafeWrite",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def ClipboardSafeWrite(
    text: str,
    confirm_if_sensitive: bool | str = False,
    redact_in_logs: bool | str = True,
) -> str:
    """Write clipboard text with optional sensitivity confirmation."""
    err = _check_win32("ClipboardSafeWrite")
    if err:
        return err
    try:
        patterns = RedactionConfig().patterns
        redacted = redaction.redact_text(text, patterns)
        sensitive = redacted != text
        if sensitive and _tobool(confirm_if_sensitive):
            return json.dumps(
                {
                    "success": False,
                    "blocked": True,
                    "reason": "Sensitive-looking text detected; set confirm_if_sensitive=false or provide approved content.",
                },
                indent=2,
            )

        previous_clipboard = desktop.get_clipboard()
        write_text = text
        desktop.set_clipboard(write_text)
        action_undo.set_last_action(
            {
                "type": "clipboard_overwrite",
                "previous_clipboard": previous_clipboard,
                "new_length": len(write_text),
            }
        )
        payload_text = redacted if _tobool(redact_in_logs) else write_text
        return json.dumps(
            {
                "success": True,
                "sensitive": sensitive,
                "length": len(write_text),
                "logged_text": payload_text,
            },
            indent=2,
        )
    except Exception as e:
        return f"ClipboardSafeWrite error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="PasteText",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def PasteText(
    text: str,
    target_window: str = "",
    restore_clipboard: bool | str = True,
) -> str:
    """Paste text via clipboard, optionally restoring prior clipboard value."""
    err = _check_win32("PasteText")
    if err:
        return err
    paused = _check_handoff_pause("PasteText")
    if paused:
        return paused
    try:
        previous = desktop.get_clipboard()
        if target_window.strip():
            desktop.focus_window(title=target_window.strip())
            time.sleep(0.1)

        desktop.set_clipboard(text)
        action_undo.set_last_action(
            {
                "type": "clipboard_overwrite",
                "previous_clipboard": previous,
                "new_length": len(text),
            }
        )
        pyautogui.hotkey("ctrl", "v")

        restored = False
        if _tobool(restore_clipboard):
            desktop.set_clipboard(previous)
            restored = True

        return json.dumps(
            {
                "success": True,
                "pasted_length": len(text),
                "restore_clipboard": _tobool(restore_clipboard),
                "restored": restored,
                "target_window": target_window.strip() or None,
            },
            indent=2,
        )
    except Exception as e:
        return f"PasteText error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="UndoLastAction",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def UndoLastAction(strategy: str = "auto") -> str:
    """Attempt to undo the most recent reversible action."""
    try:
        payload = action_undo.undo_last_action(strategy=strategy)
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"UndoLastAction error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="HumanHandoff",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def HumanHandoff(
    message: str,
    pause_input_tools: bool | str = True,
    show_notification: bool | str = True,
    resume_trigger: str = "manual",
    timeout_seconds: float = 0.0,
) -> str:
    """Pause action tools and hand control to a human."""
    try:
        timeout = float(timeout_seconds)
        state = handoff_state.request_handoff(
            message=message,
            pause_input_tools=_tobool(pause_input_tools),
            resume_trigger=resume_trigger,
            timeout_seconds=timeout if timeout > 0 else None,
        )
        if _tobool(show_notification):
            try:
                desktop.show_notification("WinRemote Human Handoff", message)
            except Exception:
                pass
        return json.dumps(
            {
                "success": True,
                "pause_input_tools": _tobool(pause_input_tools),
                "resume_trigger": resume_trigger,
                "timeout_seconds": timeout if timeout > 0 else None,
                "state": state,
            },
            indent=2,
        )
    except Exception as e:
        return f"HumanHandoff error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ResumeHumanHandoff",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ResumeHumanHandoff() -> str:
    """Resume action tools after a handoff pause."""
    try:
        state = handoff_state.resume_handoff()
        return json.dumps({"success": True, "state": state}, indent=2)
    except Exception as e:
        return f"ResumeHumanHandoff error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="HandoffStatus",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def HandoffStatus() -> str:
    """Get current human-handoff pause status."""
    try:
        return json.dumps(handoff_state.status(), indent=2)
    except Exception as e:
        return f"HandoffStatus error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="RenderSessionReport",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def RenderSessionReport(session_id: str) -> str:
    """Render a local HTML report for a recorded session trace."""
    try:
        return json.dumps(session_report.render_session_report(session_id), indent=2)
    except Exception as e:
        return f"RenderSessionReport error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="LaunchDebugBrowser",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def LaunchDebugBrowser(
    browser: str = "edge",
    url: str = "",
    user_data_dir: str = "",
    remote_debugging_port: int = 9222,
) -> str:
    """Launch Edge/Chrome with remote debugging enabled."""
    try:
        payload = browser_debug.launch_debug_browser(
            browser=browser,
            url=url or None,
            user_data_dir=user_data_dir or None,
            remote_debugging_port=remote_debugging_port,
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"LaunchDebugBrowser error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ListBrowserTabs",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ListBrowserTabs(session_id: str) -> str:
    """List tabs for a launched browser debug session."""
    try:
        return json.dumps(browser_debug.list_browser_tabs(session_id), indent=2)
    except Exception as e:
        return f"ListBrowserTabs error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetBrowserConsoleLogs",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetBrowserConsoleLogs(session_id: str, tab_id: str, level: str = "") -> str:
    """Get browser console logs for a tab (structured MVP output)."""
    try:
        return json.dumps(browser_debug.get_browser_console_logs(session_id, tab_id, level or None), indent=2)
    except Exception as e:
        return f"GetBrowserConsoleLogs error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetBrowserNetworkRequests",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetBrowserNetworkRequests(session_id: str, tab_id: str) -> str:
    """Get browser network requests for a tab (structured MVP output)."""
    try:
        return json.dumps(browser_debug.get_browser_network_requests(session_id, tab_id), indent=2)
    except Exception as e:
        return f"GetBrowserNetworkRequests error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetBrowserDomText",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetBrowserDomText(session_id: str, tab_id: str) -> str:
    """Get visible DOM/page text for a tab (best-effort)."""
    try:
        return json.dumps(browser_debug.get_browser_dom_text(session_id, tab_id), indent=2)
    except Exception as e:
        return f"GetBrowserDomText error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ClickDomElement",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def ClickDomElement(session_id: str, tab_id: str, selector: str) -> str:
    """Click a DOM element by selector (structured MVP output)."""
    try:
        paused = _check_handoff_pause("ClickDomElement")
        if paused:
            return paused
        return json.dumps(browser_debug.click_dom_element(session_id, tab_id, selector), indent=2)
    except Exception as e:
        return f"ClickDomElement error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="SessionNoteAdd",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def SessionNoteAdd(note: str, tags_csv: str = "", session_id: str = "default") -> str:
    """Add a local session note with optional tags."""
    try:
        tags = [item.strip() for item in str(tags_csv or "").split(",") if item.strip()]
        return json.dumps(session_notes.add_session_note(note, tags=tags, session_id=session_id), indent=2)
    except Exception as e:
        return f"SessionNoteAdd error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="SessionNoteList",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def SessionNoteList(tags_csv: str = "", session_id: str = "default") -> str:
    """List local session notes, optionally filtered by tags."""
    try:
        tags = [item.strip() for item in str(tags_csv or "").split(",") if item.strip()]
        return json.dumps(session_notes.list_session_notes(tags=tags, session_id=session_id), indent=2)
    except Exception as e:
        return f"SessionNoteList error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="SessionNoteSummarize",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def SessionNoteSummarize(session_id: str = "default") -> str:
    """Summarize local session notes for the given session id."""
    try:
        return json.dumps(session_notes.summarize_session_notes(session_id=session_id), indent=2)
    except Exception as e:
        return f"SessionNoteSummarize error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="CreateTerminalSession",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def CreateTerminalSession(shell: str = "powershell", cwd: str = "", env_json: str = "") -> str:
    """Create a controlled terminal session owned by WinRemote."""
    try:
        budget_block = _check_action_budget("shell", amount=1)
        if budget_block:
            return budget_block
        env_payload: dict[str, str] | None = None
        if env_json.strip():
            parsed = json.loads(env_json)
            if not isinstance(parsed, dict):
                return "CreateTerminalSession error: env_json must decode to an object"
            env_payload = {str(k): str(v) for k, v in parsed.items()}
        payload = terminal_sessions.create_terminal_session(
            shell=shell,
            cwd=cwd.strip() or None,
            env=env_payload,
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"CreateTerminalSession error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ListTerminalSessions",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ListTerminalSessions() -> str:
    """List controlled terminal sessions."""
    try:
        return json.dumps(terminal_sessions.list_terminal_sessions(), indent=2)
    except Exception as e:
        return f"ListTerminalSessions error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ReadTerminalOutput",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ReadTerminalOutput(terminal_id: str, lines: int = 200) -> str:
    """Read recent buffered output from a controlled terminal session."""
    try:
        return json.dumps(terminal_sessions.read_terminal_output(terminal_id, lines=lines), indent=2)
    except Exception as e:
        return f"ReadTerminalOutput error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="SendTerminalInput",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def SendTerminalInput(terminal_id: str, text: str, press_enter: bool | str = True) -> str:
    """Send text input to a controlled terminal session."""
    try:
        paused = _check_handoff_pause("SendTerminalInput")
        if paused:
            return paused
        keystroke_budget = max(1, len(text or ""))
        budget_block = _check_action_budget("keystroke", amount=keystroke_budget)
        if budget_block:
            return budget_block
        payload = terminal_sessions.send_terminal_input(terminal_id, text, press_enter=_tobool(press_enter))
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"SendTerminalInput error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="WaitForTerminalOutput",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def WaitForTerminalOutput(
    terminal_id: str,
    expected_text: str,
    timeout_seconds: float = 60.0,
    poll_interval: float = 0.25,
) -> str:
    """Wait until expected text appears in controlled terminal output."""
    try:
        payload = terminal_sessions.wait_for_terminal_output(
            terminal_id,
            expected_text=expected_text,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"WaitForTerminalOutput error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="StopTerminalSession",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def StopTerminalSession(terminal_id: str, force: bool | str = False) -> str:
    """Stop a controlled terminal session."""
    try:
        paused = _check_handoff_pause("StopTerminalSession")
        if paused:
            return paused
        payload = terminal_sessions.stop_terminal_session(terminal_id, force=_tobool(force))
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"StopTerminalSession error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetActionBudgetStatus",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetActionBudgetStatus() -> str:
    """Return current action-budget policy and recent counter state."""
    try:
        return json.dumps(action_budget.status(), indent=2)
    except Exception as e:
        return f"GetActionBudgetStatus error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ConfigureActionBudget",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def ConfigureActionBudget(
    max_clicks_per_minute: int = 20,
    max_keystrokes_per_minute: int = 2000,
    max_shell_commands_per_minute: int = 10,
    max_computer_use_steps: int = 25,
    pause_on_repeated_failure: bool | str = True,
    repeated_failure_threshold: int = 3,
) -> str:
    """Configure runtime limits for automation action budgets."""
    try:
        payload = action_budget.configure(
            max_clicks_per_minute=max_clicks_per_minute,
            max_keystrokes_per_minute=max_keystrokes_per_minute,
            max_shell_commands_per_minute=max_shell_commands_per_minute,
            max_computer_use_steps=max_computer_use_steps,
            pause_on_repeated_failure=_tobool(pause_on_repeated_failure),
            repeated_failure_threshold=repeated_failure_threshold,
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"ConfigureActionBudget error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ResetActionBudget",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def ResetActionBudget(unpause: bool | str = True) -> str:
    """Reset counters and optionally clear paused state."""
    try:
        return json.dumps(action_budget.reset(unpause=_tobool(unpause)), indent=2)
    except Exception as e:
        return f"ResetActionBudget error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="DetectKnownIssues",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def DetectKnownIssues(
    target_app: str = "",
    include_screenshot_analysis: bool | str = True,
    include_terminal_analysis: bool | str = True,
    include_browser_analysis: bool | str = True,
) -> str:
    """Detect common blocked states and suggest safe next debugging steps."""
    try:
        active_title = ""
        if desktop.HAS_WIN32:
            hwnd = desktop.win32gui.GetForegroundWindow()
            active_title = desktop.win32gui.GetWindowText(hwnd) if hwnd else ""

        ocr_text = ""
        if _tobool(include_screenshot_analysis):
            try:
                ocr_text = ocr.run_ocr()[:4000]
            except Exception:
                ocr_text = ""

        terminal_text = ""
        if _tobool(include_terminal_analysis):
            sessions = terminal_sessions.list_terminal_sessions()
            if sessions:
                latest = sessions[0]
                try:
                    terminal_payload = terminal_sessions.read_terminal_output(latest["terminal_id"], lines=300)
                    terminal_text = str(terminal_payload.get("output") or "")
                except Exception:
                    terminal_text = ""

        browser_console_text = ""
        browser_network_text = ""
        if _tobool(include_browser_analysis):
            browser_console_text = "browser diagnostics unavailable in MVP"
            browser_network_text = "browser diagnostics unavailable in MVP"

        payload = known_issues.detect_known_issues(
            active_window_title=f"{target_app} {active_title}".strip(),
            terminal_output=terminal_text,
            ocr_text=ocr_text,
            browser_console_text=browser_console_text,
            browser_network_text=browser_network_text,
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"DetectKnownIssues error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetAgentCapabilityGuide",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetAgentCapabilityGuide(client_name: str = "") -> str:
    """Return recommended WinRemote tool usage guidance for a client agent."""
    try:
        payload = agent_capabilities.get_agent_capability_guide(client_name or None)
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"GetAgentCapabilityGuide error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="CollectProjectContext",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def CollectProjectContext(
    root: str,
    max_files: int = 200,
    include_git_status: bool | str = True,
    include_package_scripts: bool | str = True,
    include_recent_errors: bool | str = True,
) -> str:
    """Collect local project context: files, git status, scripts, and recent errors."""
    try:
        payload = project_context.collect_project_context(
            root=root,
            max_files=max_files,
            include_git_status=_tobool(include_git_status),
            include_package_scripts=_tobool(include_package_scripts),
            include_recent_errors=_tobool(include_recent_errors),
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"CollectProjectContext error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="VSCodeListWindows",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def VSCodeListWindows() -> str:
    """List visible VS Code windows with bounds and process metadata."""
    try:
        payload = {
            "windows": vscode_bridge.list_vscode_windows(),
        }
        payload["count"] = len(payload["windows"])
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"VSCodeListWindows error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="VSCodeGetActiveFile",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def VSCodeGetActiveFile() -> str:
    """Get best-effort active VS Code file info without requiring an extension."""
    try:
        return json.dumps(vscode_bridge.get_active_file(), indent=2)
    except Exception as e:
        return f"VSCodeGetActiveFile error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="VSCodeListOpenFiles",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def VSCodeListOpenFiles() -> str:
    """List best-effort open files from VS Code window titles."""
    try:
        return json.dumps(vscode_bridge.list_open_files(), indent=2)
    except Exception as e:
        return f"VSCodeListOpenFiles error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="VSCodeGetDiagnostics",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def VSCodeGetDiagnostics() -> str:
    """Get best-effort VS Code diagnostics (MVP via Problems panel OCR parsing)."""
    try:
        return json.dumps(vscode_bridge.get_diagnostics(), indent=2)
    except Exception as e:
        return f"VSCodeGetDiagnostics error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="VSCodeReadProblemsPanel",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def VSCodeReadProblemsPanel(lines: int = 200) -> str:
    """Read best-effort Problems panel content using local OCR parsing."""
    try:
        return json.dumps(vscode_bridge.read_problems_panel(max_lines=lines), indent=2)
    except Exception as e:
        return f"VSCodeReadProblemsPanel error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="VSCodeReadTerminal",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def VSCodeReadTerminal(lines: int = 200) -> str:
    """Read VS Code terminal output (controlled sessions first, OCR fallback)."""
    try:
        return json.dumps(vscode_bridge.read_terminal(lines=lines), indent=2)
    except Exception as e:
        return f"VSCodeReadTerminal error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="VSCodeOpenFile",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def VSCodeOpenFile(path: str, line: int = 0) -> str:
    """Open a file in VS Code via CLI using --reuse-window and optional --goto."""
    try:
        budget_block = _check_action_budget("shell", amount=1)
        if budget_block:
            return budget_block
        payload = vscode_bridge.open_file(path, line=(line if line > 0 else None))
        if payload.get("exit_code") == 0:
            action_budget.record_success("shell")
        elif payload.get("supported"):
            action_budget.record_failure("shell")
        return json.dumps(payload, indent=2)
    except Exception as e:
        action_budget.record_failure("shell")
        return f"VSCodeOpenFile error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="VSCodeRunCommand",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def VSCodeRunCommand(command_id: str) -> str:
    """Run a VS Code command id via CLI (best-effort, profile/policy-governed)."""
    try:
        budget_block = _check_action_budget("shell", amount=1)
        if budget_block:
            return budget_block
        payload = vscode_bridge.run_command(command_id)
        if payload.get("exit_code") == 0:
            action_budget.record_success("shell")
        elif payload.get("supported"):
            action_budget.record_failure("shell")
        return json.dumps(payload, indent=2)
    except Exception as e:
        action_budget.record_failure("shell")
        return f"VSCodeRunCommand error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="SaveUISelector",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def SaveUISelector(
    name: str,
    query: str = "",
    window_title: str = "",
    match_mode: str = "auto",
    selector_json: str = "",
) -> str:
    """Save a reusable UI selector by name."""
    try:
        if selector_json.strip():
            selector = json.loads(selector_json)
            if not isinstance(selector, dict):
                return "SaveUISelector error: selector_json must decode to an object"
        else:
            selector = {
                "query": query,
                "window_title": window_title,
                "match_mode": match_mode,
            }
        return json.dumps(selectors.save_ui_selector(name, selector), indent=2)
    except Exception as e:
        return f"SaveUISelector error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="FindUISelector",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def FindUISelector(name: str, max_results: int = 1, include_text: bool | str = True) -> str:
    """Resolve a saved selector into current UI matches."""
    err = _check_win32("FindUISelector")
    if err:
        return err
    try:
        payload = selectors.find_ui_selector(
            name=name,
            max_results=max_results,
            include_text=_tobool(include_text),
        )
        payload["coordinate_spaces"] = _ui_coordinate_spaces()
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"FindUISelector error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ClickUISelector",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def ClickUISelector(name: str, button: str = "left") -> str:
    """Click the best current match for a saved selector."""
    err = _check_win32("ClickUISelector")
    if err:
        return err
    paused = _check_handoff_pause("ClickUISelector")
    if paused:
        return paused
    try:
        payload = selectors.click_ui_selector(name=name, button=button)
        payload["coordinate_spaces"] = _ui_coordinate_spaces()
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"ClickUISelector error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="StartFileWatch",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def StartFileWatch(root: str, recursive: bool | str = True, ignore_patterns_csv: str = "") -> str:
    """Start watching a directory for file changes (polling-based)."""
    try:
        patterns = [item.strip() for item in ignore_patterns_csv.split(",") if item.strip()]
        payload = file_watcher.start_file_watch(
            root,
            recursive=_tobool(recursive),
            ignore_patterns=patterns,
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"StartFileWatch error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="StopFileWatch",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def StopFileWatch(watch_id: str) -> str:
    """Stop a running file watcher by watch id."""
    try:
        return json.dumps(file_watcher.stop_file_watch(watch_id), indent=2)
    except Exception as e:
        return f"StopFileWatch error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ListFileChanges",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ListFileChanges(watch_id: str = "", since_seconds: int = 0) -> str:
    """List file changes detected by active watcher(s)."""
    try:
        payload = file_watcher.list_file_changes(
            watch_id=watch_id.strip() or None,
            since_seconds=since_seconds if since_seconds > 0 else None,
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"ListFileChanges error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ListProcesses",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ListProcesses(
    filter: str = "",
    sort_by: str = "memory",
    limit: int = 30,
) -> str:
    """List running processes with CPU and memory usage.

    Args:
        filter: Fuzzy filter by process name.
        sort_by: Sort by 'cpu', 'memory', or 'name'.
        limit: Max number of processes to return.
    """
    try:
        return process_mgr.list_processes(filter_name=filter, sort_by=sort_by, limit=limit)
    except Exception as e:
        return f"ListProcesses error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="KillProcess",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def KillProcess(pid: int = 0, name: str = "") -> str:
    """Kill a process by PID or name.

    Args:
        pid: Process ID.
        name: Process name (fuzzy matched).
    """
    try:
        return process_mgr.kill_process(pid=pid, name=name)
    except Exception as e:
        return f"KillProcess error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetSystemInfo",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetSystemInfo() -> str:
    """Get system information: CPU, memory, disk, network, uptime."""
    try:
        return process_mgr.get_system_info()
    except Exception as e:
        return f"GetSystemInfo error: {e}"


def _ensure_session_connected(force: bool = False) -> str | None:
    """Reconnect a disconnected desktop session to console.

    Returns None on success or if already connected, error string on failure.
    """
    try:
        result = subprocess.run(["query", "session"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return f"Failed to query sessions: {result.stderr}"

        lines = result.stdout.strip().split("\n")
        user_session_id = None
        is_disconnected = False

        for line in lines[1:]:
            line = line.lstrip(">").strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            # Format: sessionname username ID state ...
            # or:     sessionname          ID state ... (no user)
            name = parts[0].lower()
            if name in ("services", "rdp-tcp"):
                continue
            # Find the numeric session ID and state
            for i, p in enumerate(parts[1:], 1):
                if p.isdigit():
                    sid = int(p)
                    if i + 1 < len(parts):
                        state = parts[i + 1].lower()
                        # Check if there's a username before the ID
                        has_user = i > 1 and not parts[i - 1].isdigit()
                        if has_user or name == "console":
                            user_session_id = sid
                            # Chinese Windows: 已断开=Disc, 运行中=Active
                            is_disconnected = state in (
                                "disc",
                                "断开",
                                "已断开",
                                "disconnected",
                            )
                    break

        if user_session_id is None:
            return "No user session found"

        if not is_disconnected and not force:
            return None  # Already connected

        result = subprocess.run(
            ["tscon", str(user_session_id), "/dest:console"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return f"tscon failed: {err}"
        time.sleep(1)  # Wait for session to stabilize
        return None
    except subprocess.TimeoutExpired:
        return "Session reconnect timed out"
    except Exception as e:
        return f"Session reconnect error: {e}"


@mcp.tool(annotations=ToolAnnotations(title="ReconnectSession", readOnlyHint=False))
def ReconnectSession(force: bool = False) -> list:
    """Reconnect a disconnected Windows desktop session to the console.

    This enables screenshot and UI automation tools to work when no RDP
    client is actively connected. Runs 'tscon' to attach the user's
    session to the console.

    Args:
        force: Reconnect even if session appears active (default False).
    """
    err = _ensure_session_connected(force=force)
    if err:
        return [TextContent(type="text", text=f"ReconnectSession failed: {err}")]
    return [TextContent(type="text", text="Session connected to console")]


@mcp.tool(
    annotations=ToolAnnotations(
        title="Notification",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Notification(title: str = "winremote-mcp", message: str = "") -> str:
    """Show a Windows toast notification.

    Args:
        title: Notification title.
        message: Notification body text.
    """
    try:
        return desktop.show_notification(title, message)
    except Exception as e:
        return f"Notification error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="LockScreen",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def LockScreen() -> str:
    """Lock the Windows workstation."""
    try:
        return desktop.lock_screen()
    except Exception as e:
        return f"LockScreen error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="Scrape",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def Scrape(url: str) -> str:
    """Fetch URL content and return as markdown.

    Args:
        url: URL to fetch.
    """
    try:
        import urllib.request

        from markdownify import markdownify

        req = urllib.request.Request(url, headers={"User-Agent": "winremote-mcp/0.3"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        md = markdownify(html, heading_style="ATX", strip=["script", "style"])
        # Truncate
        if len(md) > 50000:
            md = md[:50000] + "\n\n[... truncated]"
        return md
    except Exception as e:
        return f"Scrape error: {e}"


# ============================== FILE OPERATIONS ============================


@mcp.tool(
    annotations=ToolAnnotations(
        title="FileRead",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def FileRead(path: str, encoding: str = "utf-8") -> str:
    """Read file content. Returns base64 for binary files.

    Args:
        path: File path.
        encoding: Text encoding (default utf-8). Use 'binary' for base64 output.
    """
    try:
        p = Path(path)
        if not p.exists():
            return f"File not found: {path}"
        if encoding == "binary":
            data = p.read_bytes()
            return base64.b64encode(data).decode()
        else:
            text = p.read_text(encoding=encoding, errors="replace")
            if len(text) > 100000:
                text = text[:100000] + "\n\n[... truncated at 100KB]"
            return text
    except Exception as e:
        return f"FileRead error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="FileWrite",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def FileWrite(path: str, content: str, encoding: str = "utf-8", append: bool | str = False) -> str:
    """Write content to a file.

    Args:
        path: File path.
        content: Content to write.
        encoding: Text encoding (default utf-8).
        append: Append instead of overwrite.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if _tobool(append) else "w"
        with open(p, mode, encoding=encoding) as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"FileWrite error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="FileList",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def FileList(path: str = ".", show_hidden: bool | str = False) -> str:
    """List directory contents with size and modification date.

    Args:
        path: Directory path.
        show_hidden: Include hidden files/folders.
    """
    try:
        from tabulate import tabulate

        p = Path(path)
        if not p.is_dir():
            return f"Not a directory: {path}"

        rows = []
        for item in sorted(p.iterdir()):
            name = item.name
            if not _tobool(show_hidden) and name.startswith("."):
                continue
            try:
                stat = item.stat()
                size = stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                kind = "DIR" if item.is_dir() else "FILE"
                if item.is_dir():
                    size_str = "<DIR>"
                elif size < 1024:
                    size_str = f"{size}B"
                elif size < 1048576:
                    size_str = f"{size // 1024}KB"
                else:
                    size_str = f"{size // 1048576}MB"
                rows.append([kind, name, size_str, mtime])
            except Exception:
                rows.append(["?", name, "?", "?"])

        if not rows:
            return "Directory is empty."
        return tabulate(rows, headers=["Type", "Name", "Size", "Modified"], tablefmt="simple")
    except Exception as e:
        return f"FileList error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="FileSearch",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def FileSearch(pattern: str, path: str = ".", recursive: bool | str = True, limit: int = 50) -> str:
    """Search files by name pattern.

    Args:
        pattern: Glob pattern (e.g. '*.py', 'report*').
        path: Root directory to search.
        recursive: Search subdirectories.
        limit: Max results.
    """
    try:
        p = Path(path)
        if _tobool(recursive):
            matches = list(p.rglob(pattern))
        else:
            matches = list(p.glob(pattern))

        if not matches:
            return f"No files matching '{pattern}' in {path}"

        lines = []
        for m in matches[:limit]:
            try:
                size = m.stat().st_size
                lines.append(f"  {m} ({size} bytes)")
            except Exception:
                lines.append(f"  {m}")

        result = f"Found {len(matches)} files"
        if len(matches) > limit:
            result += f" (showing first {limit})"
        result += ":\n" + "\n".join(lines)
        return result
    except Exception as e:
        return f"FileSearch error: {e}"


# ========================== FILE TRANSFER (BINARY) =========================


@mcp.tool(
    annotations=ToolAnnotations(
        title="FileDownload",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def FileDownload(path: str) -> str:
    """Download a file as base64-encoded content. Use for binary files.

    Args:
        path: File path to download.
    """
    try:
        p = Path(path)
        if not p.exists():
            return f"File not found: {path}"
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode()
        return f"base64:{len(data)}bytes:{b64}"
    except Exception as e:
        return f"FileDownload error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="FileUpload",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def FileUpload(path: str, data_base64: str) -> str:
    """Upload a file from base64-encoded content. Use for binary files.

    Args:
        path: Destination file path.
        data_base64: Base64-encoded file content.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = base64.b64decode(data_base64)
        p.write_bytes(data)
        return f"Written {len(data)} bytes to {path}"
    except Exception as e:
        return f"FileUpload error: {e}"


# ============================== REGISTRY ===================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="RegRead",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def RegRead(key: str, value_name: str) -> str:
    """Read a Windows registry value.

    Args:
        key: Registry key path, e.g. "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion".
        value_name: Name of the value to read.
    """
    try:
        return registry.reg_read(key, value_name)
    except Exception as e:
        return f"RegRead error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="RegWrite",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def RegWrite(key: str, value_name: str, data: str, reg_type: str = "REG_SZ") -> str:
    """Write a Windows registry value.

    Args:
        key: Registry key path, e.g. "HKCU\\SOFTWARE\\MyApp".
        value_name: Name of the value to write.
        data: Value data. For REG_DWORD/REG_QWORD pass as string number. For REG_MULTI_SZ use | separator.
        reg_type: Registry type: REG_SZ, REG_EXPAND_SZ, REG_DWORD, REG_QWORD, REG_BINARY, REG_MULTI_SZ.
    """
    try:
        return registry.reg_write(key, value_name, data, reg_type)
    except Exception as e:
        return f"RegWrite error: {e}"


# ============================= SERVICES ====================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="ServiceList",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ServiceList(filter: str = "") -> str:
    """List Windows services.

    Args:
        filter: Filter by service name or display name (substring match).
    """
    try:
        return services.service_list(filter)
    except Exception as e:
        return f"ServiceList error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ServiceStart",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def ServiceStart(name: str) -> str:
    """Start a Windows service.

    Args:
        name: Service name.
    """
    try:
        return services.service_start(name)
    except Exception as e:
        return f"ServiceStart error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ServiceStop",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def ServiceStop(name: str) -> str:
    """Stop a Windows service.

    Args:
        name: Service name.
    """
    try:
        return services.service_stop(name)
    except Exception as e:
        return f"ServiceStop error: {e}"


# ========================= SCHEDULED TASKS =================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="TaskList",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def TaskList(filter: str = "") -> str:
    """List Windows scheduled tasks.

    Args:
        filter: Filter by task name (substring match).
    """
    try:
        return services.task_list(filter)
    except Exception as e:
        return f"TaskList error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="TaskCreate",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def TaskCreate(name: str, command: str, schedule: str) -> str:
    """Create a Windows scheduled task.

    Args:
        name: Task name.
        command: Command to execute.
        schedule: Schedule type (ONCE, DAILY, WEEKLY, MONTHLY, ONSTART, ONLOGON, ONIDLE).
    """
    try:
        return services.task_create(name, command, schedule)
    except Exception as e:
        return f"TaskCreate error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="TaskDelete",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def TaskDelete(name: str) -> str:
    """Delete a Windows scheduled task.

    Args:
        name: Task name.
    """
    try:
        return services.task_delete(name)
    except Exception as e:
        return f"TaskDelete error: {e}"


# ============================= NETWORK =====================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Ping",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def Ping(host: str, count: int = 4) -> str:
    """Ping a host.

    Args:
        host: Hostname or IP address.
        count: Number of ping requests (default 4).
    """
    try:
        return network.ping(host, count)
    except Exception as e:
        return f"Ping error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="PortCheck",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def PortCheck(host: str, port: int, timeout: float = 5.0) -> str:
    """Check if a TCP port is open.

    Args:
        host: Hostname or IP address.
        port: Port number.
        timeout: Connection timeout in seconds (default 5).
    """
    try:
        return network.port_check(host, port, timeout)
    except Exception as e:
        return f"PortCheck error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="NetConnections",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def NetConnections(filter: str = "", limit: int = 50) -> str:
    """List network connections.

    Args:
        filter: Filter connections by local/remote address, status, or PID.
        limit: Maximum number of connections to return (default 50).
    """
    try:
        return network.net_connections(filter, limit=limit)
    except Exception as e:
        return f"NetConnections error: {e}"


# ============================ EVENT LOG ====================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="EventLog",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def EventLog(log_name: str = "System", count: int = 20, level: str = "") -> str:
    """Read Windows Event Log entries.

    Args:
        log_name: Log name (System, Application, Security, etc.).
        count: Number of entries to retrieve (default 20).
        level: Filter by level: critical, error, warning, information, verbose.
    """
    try:
        return services.event_log(log_name, count, level)
    except Exception as e:
        return f"EventLog error: {e}"


# ============================== OCR ========================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="OCR",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def OCR(
    left: int = 0,
    top: int = 0,
    right: int = 0,
    bottom: int = 0,
    lang: str = "eng",
) -> str:
    """Extract text from screen using OCR. Captures a region or the full screen.

    Uses pytesseract if available, falls back to Windows built-in OCR engine.

    Args:
        left: Left edge of region (0 = full screen).
        top: Top edge of region.
        right: Right edge of region.
        bottom: Bottom edge of region.
        lang: OCR language for pytesseract (default 'eng').
    """
    try:
        region = {}
        if left or top or right or bottom:
            left, top, right, bottom = desktop.normalize_region(left, top, right, bottom)
            region = {"left": left, "top": top, "right": right, "bottom": bottom}
        text = ocr.run_ocr(**region, lang=lang) if region else ocr.run_ocr(lang=lang)
        if not text:
            return "(no text detected)"
        return text
    except ImportError as e:
        return f"OCR error: {e}"
    except Exception as e:
        return f"OCR error: {e}"


# ========================== SCREEN RECORDING ===============================


@mcp.tool(
    annotations=ToolAnnotations(
        title="ScreenRecord",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ScreenRecord(
    duration: float = 3.0,
    fps: int = 5,
    left: int = 0,
    top: int = 0,
    right: int = 0,
    bottom: int = 0,
    max_width: int = 800,
) -> list:
    """Record the screen and return an animated GIF.

    Args:
        duration: Recording length in seconds (default 3, max 10).
        fps: Frames per second (default 5, max 10).
        left: Left edge of capture region (0 = full screen).
        top: Top edge of capture region.
        right: Right edge of capture region.
        bottom: Bottom edge of capture region.
        max_width: Max width of output GIF (default 800).
    """
    try:
        region = {}
        if left or top or right or bottom:
            left, top, right, bottom = desktop.normalize_region(left, top, right, bottom)
            region = {"left": left, "top": top, "right": right, "bottom": bottom}
        b64 = recording.record_screen(duration=duration, fps=fps, max_width=max_width, **region)
        return [
            ImageContent(type="image", data=b64, mimeType="image/gif"),
            TextContent(
                type="text",
                text=f"Recorded {duration}s at {fps}fps ({len(b64) * 3 // 4 // 1024}KB GIF)",
            ),
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"ScreenRecord error: {e}")]


@mcp.tool(
    annotations=ToolAnnotations(
        title="StartScreenRecording",
        readOnlyHint=False,
        openWorldHint=False,
    )
)
def StartScreenRecording(
    target: str = "monitor",
    left: int = 0,
    top: int = 0,
    right: int = 0,
    bottom: int = 0,
    fps: int = 5,
    max_width: int = 800,
    max_duration_seconds: float = 30.0,
    redact_secrets: bool | str = True,
) -> str:
    """Start a stateful screen recording session.

    Args:
        target: Capture target mode (monitor/window/region/all_monitors). Current
            phase supports monitor/full-screen and explicit region.
        left/top/right/bottom: Optional region bounds when target is region.
        fps: Capture fps (1-10).
        max_width: Max output width used by GIF backend.
        max_duration_seconds: Max bounded duration recorded on stop.
        redact_secrets: Whether to mark this recording as redaction-enabled.
    """
    try:
        region = {}
        normalized_target = str(target or "monitor").strip().lower()
        if normalized_target == "region" or left or top or right or bottom:
            left, top, right, bottom = desktop.normalize_region(left, top, right, bottom)
            region = {"left": left, "top": top, "right": right, "bottom": bottom}

        handle = recording.start_recording(
            target=normalized_target,
            fps=fps,
            max_width=max_width,
            max_duration_seconds=max_duration_seconds,
            redact_secrets=_tobool(redact_secrets),
            **region,
        )
        return json.dumps(
            {
                "recording_id": handle.recording_id,
                "target": handle.target,
                "started_at": handle.started_at,
                "max_duration_seconds": handle.max_duration_seconds,
                "fps": handle.fps,
                "max_width": handle.max_width,
                "region": {
                    "left": handle.left,
                    "top": handle.top,
                    "right": handle.right,
                    "bottom": handle.bottom,
                },
                "redact_secrets": handle.redact_secrets,
            },
            indent=2,
        )
    except Exception as e:
        return f"StartScreenRecording error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="StopScreenRecording",
        readOnlyHint=False,
        openWorldHint=False,
    )
)
def StopScreenRecording(recording_id: str, save_format: str = "mp4") -> str:
    """Stop a stateful recording and persist artifact + manifest.

    Args:
        recording_id: Recording id returned by StartScreenRecording.
        save_format: Requested output format (mp4/webm/gif/frames).
    """
    try:
        result = recording.stop_recording(recording_id, save_format=save_format)
        return json.dumps(
            {
                "recording_id": result.recording_id,
                "success": result.success,
                "started_at": result.started_at,
                "ended_at": result.ended_at,
                "duration_seconds": result.duration_seconds,
                "output_path": result.output_path,
                "output_format": result.output_format,
                "manifest_path": result.manifest_path,
                "note": result.note,
            },
            indent=2,
        )
    except Exception as e:
        return f"StopScreenRecording error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ListRecordings",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ListRecordings() -> str:
    """List recordings available in local recording storage."""
    try:
        return json.dumps(recording.list_recordings(), indent=2)
    except Exception as e:
        return f"ListRecordings error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="GetRecordingManifest",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetRecordingManifest(recording_id: str) -> str:
    """Return manifest details for a recording id."""
    try:
        return json.dumps(recording.get_recording_manifest(recording_id), indent=2)
    except Exception as e:
        return f"GetRecordingManifest error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="AnalyzeRecording",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def AnalyzeRecording(
    recording_id: str,
    question: str = "",
    extract_keyframes: bool | str = True,
    keyframe_interval_seconds: float = 2.0,
    include_ocr: bool | str = True,
    include_ui_context: bool | str = True,
    include_event_timeline: bool | str = True,
    output_format: str = "debug_report",
) -> str:
    """Analyze a recording and produce a local-first structured report."""
    try:
        payload = recording.analyze_recording(
            recording_id=recording_id,
            question=question or None,
            extract_keyframes=_tobool(extract_keyframes),
            keyframe_interval_seconds=keyframe_interval_seconds,
            include_ocr=_tobool(include_ocr),
            include_ui_context=_tobool(include_ui_context),
            include_event_timeline=_tobool(include_event_timeline),
            output_format=str(output_format or "debug_report").strip().lower(),
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"AnalyzeRecording error: {e}"


# ======================== ANNOTATED SNAPSHOT ===============================


@mcp.tool(
    annotations=ToolAnnotations(
        title="AnnotatedSnapshot",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def AnnotatedSnapshot(
    max_elements: int = 30,
    quality: int = 75,
    max_width: int = 0,
    window_title: str = "",
) -> list:
    """Take a screenshot with numbered labels on interactive UI elements.

    Draws red rectangles and white numbered labels on each interactive element,
    making it easy for AI agents to identify click targets visually.

    Args:
        max_elements: Maximum number of elements to annotate (default 30).
        quality: JPEG quality 1-100 (default 75).
        max_width: Max image width in pixels. 0=native resolution (default).
        window_title: Optional target window title for window-only capture and annotation.
    """
    try:
        import io

        from PIL import ImageDraw, ImageFont

        # Take screenshot (auto-reconnect session if grab fails)
        try:
            target_window_title = window_title.strip()
            if target_window_title:
                target_window = desktop._find_window_by_title(target_window_title)
                if target_window is None:
                    return [
                        TextContent(
                            type="text",
                            text=f"AnnotatedSnapshot error: No window matching '{target_window_title}'",
                        )
                    ]
                img, capture_meta = desktop.capture_image(
                    left=target_window.rect[0],
                    top=target_window.rect[1],
                    right=target_window.rect[2],
                    bottom=target_window.rect[3],
                )
            else:
                img, capture_meta = desktop.capture_image()
        except Exception as screenshot_error:
            reconnect_result = _ensure_session_connected()
            if reconnect_result is not None:
                return [TextContent(type="text", text=f"AnnotatedSnapshot error: {screenshot_error}")]
            try:
                if target_window_title:
                    target_window = desktop._find_window_by_title(target_window_title)
                    if target_window is None:
                        return [
                            TextContent(
                                type="text",
                                text=f"AnnotatedSnapshot error: No window matching '{target_window_title}'",
                            )
                        ]
                    img, capture_meta = desktop.capture_image(
                        left=target_window.rect[0],
                        top=target_window.rect[1],
                        right=target_window.rect[2],
                        bottom=target_window.rect[3],
                    )
                else:
                    img, capture_meta = desktop.capture_image()
            except Exception as retry_error:
                return [
                    TextContent(
                        type="text",
                        text=f"AnnotatedSnapshot error (after session reconnect): {retry_error}",
                    )
                ]
        original_width = img.width
        original_left = capture_meta["bounds"]["left"]
        original_top = capture_meta["bounds"]["top"]
        if max_width > 0 and img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)))
        scale = img.width / original_width if original_width else 1.0

        # Get interactive elements
        mapped = desktop.map_ui_elements(window_title=window_title, max_elements=max_elements)
        elements = mapped[1:] if len(mapped) > 1 else []
        if not elements:
            # Return screenshot with no annotations
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            b64 = base64.b64encode(buf.getvalue()).decode()
            text_note = "No interactive elements found."
            if window_title.strip():
                text_note = f"No interactive elements found for window '{window_title.strip()}'."
            return [
                ImageContent(type="image", data=b64, mimeType="image/jpeg"),
                TextContent(type="text", text=text_note),
            ]

        draw = ImageDraw.Draw(img)

        # Try to get a font
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except Exception:
            font = ImageFont.load_default()

        element_lines = []
        for el in elements[:max_elements]:
            idx = el["index"]
            r = el["rect"]
            x1 = int((r["left"] - original_left) * scale)
            y1 = int((r["top"] - original_top) * scale)
            x2 = int((r["right"] - original_left) * scale)
            y2 = int((r["bottom"] - original_top) * scale)

            # Draw red rectangle
            draw.rectangle([x1, y1, x2, y2], outline="red", width=2)

            # Draw label background + number
            label = str(idx)
            bbox = font.getbbox(label)
            lw = bbox[2] - bbox[0] + 6
            lh = bbox[3] - bbox[1] + 4
            draw.rectangle([x1, y1 - lh - 2, x1 + lw, y1 - 2], fill="red")
            draw.text((x1 + 3, y1 - lh - 1), label, fill="white", font=font)

            # Build text description
            cx = (r["left"] + r["right"]) // 2
            cy = (r["top"] + r["bottom"]) // 2
            name = el.get("label") or el.get("window_text") or el.get("class")
            element_lines.append(
                f"  [{idx}] {name} — center ({cx},{cy}) monitor={el.get('monitor_id', 0)}"
            )

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode()

        target_prefix = (
            f"**Target Window:** {window_title.strip()}\n\n"
            if window_title.strip()
            else ""
        )
        text_summary = target_prefix + f"**Annotated {len(element_lines)} elements:**\n" + "\n".join(element_lines)
        return [
            ImageContent(type="image", data=b64, mimeType="image/jpeg"),
            TextContent(type="text", text=text_summary),
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"AnnotatedSnapshot error: {e}")]


@mcp.tool(
    annotations=ToolAnnotations(
        title="UIMap",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def UIMap(
    window_title: str = "",
    include_text: bool | str = False,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
) -> str:
    """Map visible UI controls to absolute screen coordinates.

    Useful for finding button/panel locations in apps like Roblox Studio.

    Args:
        window_title: Optional target window title (fuzzy/contains matched). Empty = foreground window.
        include_text: If true, run OCR on each element bbox and include extracted text.
        max_elements: Maximum number of child controls to return (default 100).
        min_width: Minimum element width in px (default 4).
        min_height: Minimum element height in px (default 4).
    """
    err = _check_win32("UIMap")
    if err:
        return err
    try:
        mapped = desktop.map_ui_elements(
            window_title=window_title,
            include_text=_tobool(include_text),
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
        )
        if not mapped:
            return "No UI elements detected."

        header = mapped[0]
        monitor_ctx = _monitor_context()
        lines = [
            f"Target window: {header['label']}",
            (
                f"Window rect: ({header['rect']['left']},{header['rect']['top']})"
                f" -> ({header['rect']['right']},{header['rect']['bottom']})"
            ),
            f"Window monitor: {header.get('monitor_id', 0)}",
            f"Process: {header.get('process_name', '') or 'unknown'} (pid={header.get('pid', 0)})",
            f"Mapped controls: {max(0, len(mapped) - 1)}",
            "",
        ]
        if monitor_ctx["monitors"]:
            lines.append("Monitors:")
            for mon in monitor_ctx["monitors"]:
                rect = mon["rect"]
                lines.append(
                    f"  Monitor {mon['monitor_id']}{' (primary)' if mon.get('primary') else ''}: "
                    f"({rect['left']},{rect['top']}) -> ({rect['right']},{rect['bottom']})"
                )
            lines.append("")
        for el in mapped[1:]:
            c = el["center"]
            r = el["rect"]
            line = (
                f"[{el['index']}] {el.get('label', '')}"
                f" | id={el.get('element_id', '')}"
                f" | class={el.get('class', '')}"
                f" | monitor={el.get('monitor_id', 0)}"
                f" | center=({c['x']},{c['y']})"
                f" | rect=({r['left']},{r['top']},{r['right']},{r['bottom']})"
            )
            if el.get("ocr_text"):
                line += f" | ocr='{el['ocr_text']}'"
            lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        return f"UIMap error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="UIMapJson",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def UIMapJson(
    window_title: str = "",
    include_text: bool | str = False,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
) -> str:
    """Map visible UI controls and return structured JSON.

    Useful for programmatic UI automation flows that need exact coordinates.
    """
    err = _check_win32("UIMapJson")
    if err:
        return err
    try:
        import json

        mapped = desktop.map_ui_elements(
            window_title=window_title,
            include_text=_tobool(include_text),
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
        )
        monitor_ctx = _monitor_context()
        payload = {
            "requested_window_title": window_title or None,
            "window": mapped[0] if mapped else None,
            "controls": mapped[1:] if len(mapped) > 1 else [],
            "count": max(0, len(mapped) - 1),
            "include_text": _tobool(include_text),
            "summary": desktop.summarize_ui_map(mapped),
            "monitors": monitor_ctx["monitors"],
            "virtual_screen": monitor_ctx["virtual_screen"],
            "coordinate_spaces": _ui_coordinate_spaces(),
        }
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"UIMapJson error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="UIFind",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def UIFind(
    query: str,
    window_title: str = "",
    include_text: bool | str = False,
    max_results: int = 5,
    match_mode: str = "auto",
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
) -> str:
    """Find matching UI elements by label, class, window text, or OCR text.

    Returns structured JSON sorted by best match first.
    """
    err = _check_win32("UIFind")
    if err:
        return err
    try:
        import json

        search_result = desktop.find_ui_elements_with_context(
            query=query,
            window_title=window_title,
            include_text=_tobool(include_text),
            max_results=max_results,
            match_mode=match_mode,
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
        )
        monitor_ctx = _monitor_context()
        search_payload = _search_payload(
            searched_element_count=search_result["searched_element_count"],
            searchable_preview=search_result["searchable_preview"],
            summary=search_result["summary"],
            recommendations=search_result["recommendations"],
        )
        payload = {
            "query": query,
            "window_title": window_title or None,
            "match_mode": match_mode,
            "count": len(search_result["matches"]),
            "matches": search_result["matches"],
            "target": _compact_window_target(search_result.get("window"), mode="window"),
            "search": search_payload,
            "searched_element_count": search_result["searched_element_count"],
            "searchable_preview": search_result["searchable_preview"],
            "summary": search_result["summary"],
            "recommendations": search_payload["recommendations"],
            "monitors": monitor_ctx["monitors"],
            "virtual_screen": monitor_ctx["virtual_screen"],
            "coordinate_spaces": _ui_coordinate_spaces(),
        }
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"UIFind error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="UIClick",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def UIClick(
    query: str,
    window_title: str = "",
    include_text: bool | str = False,
    match_mode: str = "auto",
    button: str = "left",
    action: str = "click",
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
) -> str:
    """Find the best-matching UI element and click it by center coordinates.

    Args mirror UIFind plus click action options.
    """
    err = _check_win32("UIClick")
    if err:
        return err
    paused = _check_handoff_pause("UIClick")
    if paused:
        return paused
    try:
        search_result = desktop.find_ui_elements_with_context(
            query=query,
            window_title=window_title,
            include_text=_tobool(include_text),
            max_results=1,
            match_mode=match_mode,
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
        )
        matches = search_result["matches"]
        if not matches:
            recommendations = search_result.get("recommendations") or []
            if recommendations:
                return f"No UI element matched '{query}'. {recommendations[0]}"
            return f"No UI element matched '{query}'"

        target = matches[0]
        center = target["center"]
        x = center["x"]
        y = center["y"]
        desktop.validate_screen_point(x, y)
        if action == "hover":
            pyautogui.moveTo(x, y)
            verb = "Hovered"
        elif action == "double":
            pyautogui.doubleClick(x, y, button=button)
            verb = f"Double-clicked {button}"
        else:
            pyautogui.click(x, y, button=button)
            verb = f"Clicked {button}"

        label = target.get("label") or target.get("class") or "(unnamed element)"
        score = target.get("match", {}).get("score", "?")
        return (
            f"{verb} '{label}' at ({x},{y}) using query '{query}' "
            f"(score={score}, monitor={target.get('monitor_id', 0)}, id={target.get('element_id', '')})"
        )
    except Exception as e:
        return f"UIClick error: {e}"


def _normalize_wait_until(wait_until: str) -> str:
    """Normalize a semantic wait mode."""
    value = str(wait_until or "appear").strip().lower()
    aliases = {
        "appear": "appear",
        "present": "appear",
        "exists": "appear",
        "visible": "appear",
        "show": "appear",
        "shown": "appear",
        "disappear": "disappear",
        "absent": "disappear",
        "missing": "disappear",
        "gone": "disappear",
        "hide": "disappear",
        "hidden": "disappear",
    }
    normalized = aliases.get(value)
    if normalized is None:
        raise ValueError("wait_until must be one of: appear, disappear")
    return normalized


def _wait_for_ui_query(
    *,
    query: str,
    window_title: str = "",
    include_text: bool = False,
    match_mode: str = "auto",
    wait_until: str = "appear",
    timeout_seconds: float = 2.0,
    poll_interval: float = 0.25,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    grid_size: int = 6,
) -> dict[str, Any]:
    """Wait for a semantic UI query to appear or disappear with minimal overhead."""
    query = (query or "").strip()
    if not query:
        raise ValueError("wait_for_query requires a non-empty query")

    wait_until = _normalize_wait_until(wait_until)
    timeout_seconds = max(0.0, float(timeout_seconds))
    poll_interval = max(0.05, float(poll_interval))

    def _search(*, use_cache: bool) -> dict[str, Any]:
        return desktop.find_ui_elements_with_context(
            query=query,
            window_title=window_title,
            include_text=include_text,
            max_results=1,
            match_mode=match_mode,
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
            use_cache=use_cache,
        )

    desktop.invalidate_ui_map_cache(window_title)
    search_result = _search(use_cache=False)
    matches = search_result.get("matches") or []

    def _is_satisfied(found_matches: list[dict[str, Any]]) -> bool:
        is_present = bool(found_matches)
        return is_present if wait_until == "appear" else not is_present

    observation_after = None
    if _is_satisfied(matches):
        observation_after = desktop.observe_screen(
            window_title=window_title,
            include_text=include_text,
            max_elements=min(max_elements, 40),
            min_width=min_width,
            min_height=min_height,
            grid_size=grid_size,
            reset=False,
            update_baseline=True,
        )
        return {
            "query": query,
            "wait_until": wait_until,
            "satisfied": True,
            "timed_out": False,
            "status": "completed",
            "target": {
                "label": ((matches[0] if matches else {}).get("label")),
                "class": ((matches[0] if matches else {}).get("class")),
                "element_id": ((matches[0] if matches else {}).get("element_id")),
                "match": ((matches[0] if matches else {}).get("match")),
            } if matches else None,
            "searched_element_count": search_result.get("searched_element_count", 0),
            "searchable_preview": search_result.get("searchable_preview", []),
            "summary": search_result.get("summary"),
            "recommendations": search_result.get("recommendations", []),
            "observation_after": observation_after,
        }

    deadline = time.monotonic() + timeout_seconds
    last_observation = None
    timed_out = False
    while True:
        last_observation = desktop.observe_screen(
            window_title=window_title,
            include_text=include_text,
            max_elements=min(max_elements, 40),
            min_width=min_width,
            min_height=min_height,
            grid_size=grid_size,
            reset=False,
            update_baseline=False,
        )

        should_refresh = bool(last_observation.get("changed"))
        if should_refresh:
            desktop.invalidate_ui_map_cache(window_title)
            search_result = _search(use_cache=False)
            matches = search_result.get("matches") or []
            if _is_satisfied(matches):
                break

        if time.monotonic() >= deadline:
            timed_out = True
            desktop.invalidate_ui_map_cache(window_title)
            search_result = _search(use_cache=False)
            matches = search_result.get("matches") or []
            break

        time.sleep(poll_interval)

    observation_after = desktop.observe_screen(
        window_title=window_title,
        include_text=include_text,
        max_elements=min(max_elements, 40),
        min_width=min_width,
        min_height=min_height,
        grid_size=grid_size,
        reset=False,
        update_baseline=True,
    )
    satisfied = _is_satisfied(matches)
    return {
        "query": query,
        "wait_until": wait_until,
        "satisfied": satisfied,
        "timed_out": timed_out and not satisfied,
        "status": "completed" if satisfied else "timeout",
        "target": {
            "label": ((matches[0] if matches else {}).get("label")),
            "class": ((matches[0] if matches else {}).get("class")),
            "element_id": ((matches[0] if matches else {}).get("element_id")),
            "match": ((matches[0] if matches else {}).get("match")),
        } if matches else None,
        "searched_element_count": search_result.get("searched_element_count", 0),
        "searchable_preview": search_result.get("searchable_preview", []),
        "summary": search_result.get("summary"),
        "recommendations": search_result.get("recommendations", []),
        "observation_after": observation_after,
    }


def _run_ui_action(
    *,
    query: str,
    window_title: str = "",
    include_text: bool = False,
    match_mode: str = "auto",
    button: str = "left",
    action: str = "click",
    text: str = "",
    clear: bool = False,
    press_enter: bool = False,
    wait_for_change: bool = True,
    wait_for_query: str = "",
    wait_match_mode: str = "auto",
    wait_until: str = "appear",
    timeout_seconds: float = 2.0,
    poll_interval: float = 0.25,
    focus_window: bool = True,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    grid_size: int = 6,
) -> dict[str, Any]:
    """Shared implementation for UIAct and UISequence."""
    query = (query or "").strip()
    if not query:
        return {"status": "error", "error": "query is required"}

    timeout_seconds = max(0.0, float(timeout_seconds))
    poll_interval = max(0.05, float(poll_interval))
    wait_for_query = (wait_for_query or "").strip()

    search_result = desktop.find_ui_elements_with_context(
        query=query,
        window_title=window_title,
        include_text=include_text,
        max_results=1,
        match_mode=match_mode,
        max_elements=max_elements,
        min_width=min_width,
        min_height=min_height,
    )
    matches = search_result.get("matches") or []
    search_payload = _search_payload(
        searched_element_count=search_result.get("searched_element_count", 0),
        searchable_preview=search_result.get("searchable_preview"),
        summary=search_result.get("summary"),
        recommendations=search_result.get("recommendations"),
    )
    if not matches:
        return {
            "query": query,
            "window_title": window_title or None,
            "status": "no-match",
            "action": action,
            "target": _compact_window_target(search_result.get("window"), mode="window"),
            "search": search_payload,
            "recommendations": search_payload["recommendations"],
        }

    target = matches[0]
    center = target["center"]
    x = center["x"]
    y = center["y"]
    desktop.validate_screen_point(x, y)

    target_window_title = window_title or ((search_result.get("window") or {}).get("label") or "")
    focus_result = None
    if focus_window and target_window_title:
        focus_result = desktop.focus_window(title=target_window_title)
        time.sleep(0.1)

    should_wait = wait_for_change or bool(wait_for_query)

    observation_before = desktop.observe_screen(
        window_title=target_window_title,
        include_text=include_text,
        max_elements=min(max_elements, 40),
        min_width=min_width,
        min_height=min_height,
        grid_size=grid_size,
        reset=True,
        update_baseline=True,
    ) if should_wait else None

    pyautogui.moveTo(x, y)
    interaction_summary = None
    if action == "hover":
        interaction_summary = f"Hovered '{target.get('label') or target.get('class') or query}'"
    elif action == "double":
        pyautogui.doubleClick(x, y, button=button)
        interaction_summary = f"Double-clicked {button} on '{target.get('label') or target.get('class') or query}'"
    elif action == "right_click":
        pyautogui.click(x, y, button="right")
        interaction_summary = f"Right-clicked '{target.get('label') or target.get('class') or query}'"
    elif action == "type":
        pyautogui.click(x, y, button=button)
        time.sleep(0.1)
        if clear:
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("delete")
            time.sleep(0.05)
        if text:
            pyautogui.typewrite(text, interval=0.02) if text.isascii() else pyautogui.write(text)
        if press_enter:
            pyautogui.press("enter")
        interaction_summary = f"Typed {len(text)} chars into '{target.get('label') or target.get('class') or query}'"
    else:
        pyautogui.click(x, y, button=button)
        interaction_summary = f"Clicked {button} on '{target.get('label') or target.get('class') or query}'"

    desktop.invalidate_ui_map_cache(target_window_title)

    observation_after = None
    wait_condition = None
    if wait_for_query:
        wait_condition = _wait_for_ui_query(
            query=wait_for_query,
            window_title=target_window_title,
            include_text=include_text,
            match_mode=wait_match_mode,
            wait_until=wait_until,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
            grid_size=grid_size,
        )
        observation_after = wait_condition.get("observation_after")
    elif wait_for_change:
        deadline = time.monotonic() + timeout_seconds
        while True:
            observation_after = desktop.observe_screen(
                window_title=target_window_title,
                include_text=include_text,
                max_elements=min(max_elements, 40),
                min_width=min_width,
                min_height=min_height,
                grid_size=grid_size,
                reset=False,
                update_baseline=False,
            )
            if observation_after.get("changed"):
                desktop.invalidate_ui_map_cache(target_window_title)
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_interval)

        if observation_after is not None:
            desktop.observe_screen(
                window_title=target_window_title,
                include_text=include_text,
                max_elements=min(max_elements, 40),
                min_width=min_width,
                min_height=min_height,
                grid_size=grid_size,
                reset=False,
                update_baseline=True,
            )

    payload: dict[str, Any] = {
        "query": query,
        "window_title": target_window_title or None,
        "status": "completed",
        "action": action,
        "button": button,
        "interaction": interaction_summary,
        "focus_result": focus_result,
        "target": {
            "label": target.get("label"),
            "class": target.get("class"),
            "element_id": target.get("element_id"),
            "monitor_id": target.get("monitor_id", 0),
            "center": target.get("center"),
            "relative_center": target.get("relative_center"),
            "rect": target.get("rect"),
            "match": target.get("match"),
        },
        "search": {
            "searched_element_count": search_payload["searched_element_count"],
            "searchable_preview": search_payload["searchable_preview"],
            "summary": search_payload["summary"],
            "ui_scope": search_payload["ui_scope"],
            "recommendations": search_payload["recommendations"],
        },
        "observation_before": observation_before,
        "observation_after": observation_after,
        "wait_for_change": wait_for_change,
        "wait_condition": wait_condition,
        "recommendations": search_payload["recommendations"],
    }
    payload["coordinate_spaces"] = _ui_coordinate_spaces()
    return payload


@mcp.tool(
    annotations=ToolAnnotations(
        title="UIWatch",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def UIWatch(
    window_title: str = "",
    include_text: bool | str = False,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    reset: bool | str = False,
    update_baseline: bool | str = True,
) -> str:
    """Diff the current UI map against the previous snapshot for the same window.

    First call (or reset=True) stores a baseline. Later calls report added,
    removed, moved, and text-changed controls.
    """
    err = _check_win32("UIWatch")
    if err:
        return err
    try:
        payload = desktop.watch_ui_elements(
            window_title=window_title,
            include_text=_tobool(include_text),
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
            reset=_tobool(reset),
            update_baseline=_tobool(update_baseline),
        )
        payload["target"] = _compact_window_target(payload.get("window"), mode="window")
        payload["search"] = _search_payload(
            searched_element_count=((payload.get("summary") or {}).get("control_count")),
            searchable_preview=payload.get("searchable_preview"),
            summary=payload.get("summary"),
            recommendations=payload.get("recommendations"),
            ui_scope="target_window" if window_title else "foreground_window",
        )
        payload["recommendations"] = payload["search"]["recommendations"]
        payload.update(_monitor_context())
        payload["coordinate_spaces"] = _ui_coordinate_spaces()
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"UIWatch error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ObserveScreen",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ObserveScreen(
    window_title: str = "",
    include_text: bool | str = False,
    monitor: int = 0,
    left: int = 0,
    top: int = 0,
    right: int = 0,
    bottom: int = 0,
    max_elements: int = 40,
    min_width: int = 4,
    min_height: int = 4,
    grid_size: int = 6,
    reset: bool | str = False,
    update_baseline: bool | str = True,
) -> str:
    """Observe the GUI without attaching a screenshot to chat.

    Captures a tiny in-memory digest, compares it to the prior digest for the
    same target, and returns text/JSON describing whether the screen changed,
    where it changed, and which UI elements are likely relevant.
    """
    err = _check_win32("ObserveScreen")
    if err:
        return err
    try:
        region = {}
        if left or top or right or bottom:
            left, top, right, bottom = desktop.normalize_region(left, top, right, bottom)
            region = {"left": left, "top": top, "right": right, "bottom": bottom}

        observation_payload = desktop.observe_screen(
            window_title=window_title,
            include_text=_tobool(include_text),
            monitor=monitor,
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
            grid_size=grid_size,
            reset=_tobool(reset),
            update_baseline=_tobool(update_baseline),
            **region,
        )
        observation_payload["search"] = _search_payload(
            searched_element_count=((observation_payload.get("ui_summary") or {}).get("control_count")),
            searchable_preview=observation_payload.get("searchable_preview"),
            summary=observation_payload.get("ui_summary"),
            recommendations=observation_payload.get("recommendations"),
            ui_scope=observation_payload.get("ui_scope"),
        )
        observation_payload["recommendations"] = observation_payload["search"]["recommendations"]
        observation_payload["coordinate_spaces"] = _ui_coordinate_spaces()
        return json.dumps(observation_payload, indent=2)
    except Exception as e:
        return f"ObserveScreen error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="UIAct",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def UIAct(
    query: str,
    window_title: str = "",
    include_text: bool | str = False,
    match_mode: str = "auto",
    button: str = "left",
    action: str = "click",
    text: str = "",
    clear: bool | str = False,
    press_enter: bool | str = False,
    wait_for_change: bool | str = True,
    wait_for_query: str = "",
    wait_match_mode: str = "auto",
    wait_until: str = "appear",
    timeout_seconds: float = 2.0,
    poll_interval: float = 0.25,
    focus_window: bool | str = True,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    grid_size: int = 6,
) -> str:
    """Find a UI element, act on it, and observe the result server-side.

    This bundles semantic search, click/hover/double/type interaction, and
    optional wait-for-change observation into one tool call so the chat does
    not need repeated screenshot-heavy loops.
    """
    err = _check_win32("UIAct")
    if err:
        return err
    paused = _check_handoff_pause("UIAct")
    if paused:
        return paused
    try:
        payload = _run_ui_action(
            query=query,
            window_title=window_title,
            include_text=_tobool(include_text),
            match_mode=match_mode,
            button=button,
            action=action,
            text=text,
            clear=_tobool(clear),
            press_enter=_tobool(press_enter),
            wait_for_change=_tobool(wait_for_change),
            wait_for_query=wait_for_query,
            wait_match_mode=wait_match_mode,
            wait_until=wait_until,
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,
            focus_window=_tobool(focus_window),
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
            grid_size=grid_size,
        )
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"UIAct error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ComputerUseStep",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def ComputerUseStep(
    goal: str,
    window_title: str = "",
    target_query: str = "",
    action: str = "auto",
    text: str = "",
    match_mode: str = "auto",
    include_text: bool | str = False,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    confirm_risky: bool | str = False,
    dry_run: bool | str = False,
    session_id: str = "",
) -> str:
    """Execute one high-level Observe -> Plan -> Act -> Verify desktop step.

    This tool wraps semantic target search, risk gating, bounded action, and
    post-action verification in a single structured response.
    """
    err = _check_win32("ComputerUseStep")
    if err:
        return err
    paused = _check_handoff_pause("ComputerUseStep")
    if paused:
        return paused
    try:
        payload = computer_use.computer_use_step(
            goal=goal,
            window_title=window_title,
            target_query=target_query,
            action=action,
            text=text,
            match_mode=match_mode,
            include_text=_tobool(include_text),
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
            confirm_risky=_tobool(confirm_risky),
            dry_run=_tobool(dry_run),
            session_id=session_id.strip() or None,
        )
        payload["coordinate_spaces"] = _ui_coordinate_spaces()
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"ComputerUseStep error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="ComputerUseTask",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def ComputerUseTask(
    goal: str,
    window_title: str = "",
    target_query: str = "",
    action: str = "auto",
    text: str = "",
    match_mode: str = "auto",
    include_text: bool | str = False,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    confirm_risky: bool | str = False,
    dry_run: bool | str = False,
    max_steps: int = 5,
    max_failures: int = 2,
    stop_on_first_success: bool | str = True,
    step_queries_csv: str = "",
    session_id: str = "",
) -> str:
    """Run a bounded multi-step computer-use loop with shared session trace.

    Executes up to max_steps calls to ComputerUseStep-style logic and returns
    aggregated step-by-step evidence.
    """
    err = _check_win32("ComputerUseTask")
    if err:
        return err
    paused = _check_handoff_pause("ComputerUseTask")
    if paused:
        return paused
    try:
        step_queries = [item.strip() for item in step_queries_csv.split(",") if item.strip()]
        payload = computer_use.computer_use_task(
            goal=goal,
            window_title=window_title,
            target_query=target_query,
            action=action,
            text=text,
            match_mode=match_mode,
            include_text=_tobool(include_text),
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
            confirm_risky=_tobool(confirm_risky),
            dry_run=_tobool(dry_run),
            max_steps=max_steps,
            max_failures=max_failures,
            stop_on_first_success=_tobool(stop_on_first_success),
            step_queries=step_queries,
            session_id=session_id.strip() or None,
        )
        payload["coordinate_spaces"] = _ui_coordinate_spaces()
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"ComputerUseTask error: {e}"


@mcp.tool(
    annotations=ToolAnnotations(
        title="UISequence",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def UISequence(
    steps_json: str,
    window_title: str = "",
    include_text: bool | str = False,
    compact: bool | str = True,
    continue_on_error: bool | str = False,
    default_timeout_seconds: float = 2.0,
    default_poll_interval: float = 0.25,
    max_steps: int = 8,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    grid_size: int = 6,
) -> str:
    """Run a compact multi-step GUI workflow server-side.

    Accepts a JSON list of steps so the agent can execute a short GUI routine
    in one round trip and return a concise summary instead of per-step chat churn.
    Supported step actions: click, double, hover, right_click, type, observe,
    wait, waitfor, and shortcut.
    """
    err = _check_win32("UISequence")
    if err:
        return err
    try:
        compact = _tobool(compact)
        continue_on_error = _tobool(continue_on_error)
        include_text = _tobool(include_text)

        raw = json.loads(steps_json)
        steps = raw.get("steps") if isinstance(raw, dict) else raw
        if not isinstance(steps, list) or not steps:
            return "UISequence error: steps_json must decode to a non-empty list or {'steps': [...]}"
        if len(steps) > max_steps:
            return f"UISequence error: received {len(steps)} steps but max_steps={max_steps}"

        results: list[dict[str, Any]] = []
        current_window_title = window_title

        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                error_result = {"step": index, "status": "error", "error": "each step must be an object"}
                results.append(error_result)
                if not continue_on_error:
                    break
                continue

            step_action = str(step.get("action") or step.get("type") or "click").strip().lower()
            step_window_title = str(step.get("window_title") or current_window_title or "")

            try:
                if step_action == "observe":
                    observation = desktop.observe_screen(
                        window_title=step_window_title,
                        include_text=include_text if "include_text" not in step else _tobool(step.get("include_text", False)),
                        monitor=int(step.get("monitor", 0) or 0),
                        max_elements=min(max_elements, int(step.get("max_elements", max_elements) or max_elements)),
                        min_width=int(step.get("min_width", min_width) or min_width),
                        min_height=int(step.get("min_height", min_height) or min_height),
                        grid_size=int(step.get("grid_size", grid_size) or grid_size),
                        reset=_tobool(step.get("reset", False)),
                        update_baseline=_tobool(step.get("update_baseline", True)),
                    )
                    step_result: dict[str, Any] = {
                        "step": index,
                        "action": "observe",
                        "status": "completed",
                        "result": _compact_observation_payload(observation) if compact else observation,
                    }
                elif step_action == "wait":
                    seconds = max(0.0, float(step.get("seconds", step.get("duration", 1.0)) or 0.0))
                    time.sleep(seconds)
                    step_result = {
                        "step": index,
                        "action": "wait",
                        "status": "completed",
                        "result": {"seconds": seconds},
                    }
                elif step_action == "waitfor":
                    wait_payload = _wait_for_ui_query(
                        query=str(step.get("query") or ""),
                        window_title=step_window_title,
                        include_text=include_text if "include_text" not in step else _tobool(step.get("include_text", False)),
                        match_mode=str(step.get("match_mode") or step.get("wait_match_mode") or "auto"),
                        wait_until=str(step.get("wait_until") or "appear"),
                        timeout_seconds=float(step.get("timeout_seconds", default_timeout_seconds) or default_timeout_seconds),
                        poll_interval=float(step.get("poll_interval", default_poll_interval) or default_poll_interval),
                        max_elements=int(step.get("max_elements", max_elements) or max_elements),
                        min_width=int(step.get("min_width", min_width) or min_width),
                        min_height=int(step.get("min_height", min_height) or min_height),
                        grid_size=int(step.get("grid_size", grid_size) or grid_size),
                    )
                    step_result = {
                        "step": index,
                        "action": "waitfor",
                        "status": wait_payload.get("status", "completed"),
                        "result": _compact_wait_payload(wait_payload) if compact else wait_payload,
                    }
                elif step_action == "shortcut":
                    keys = str(step.get("keys") or "").strip()
                    if not keys:
                        raise ValueError("shortcut step requires keys")
                    pyautogui.hotkey(*[k.strip() for k in keys.lower().split("+") if k.strip()])
                    step_result = {
                        "step": index,
                        "action": "shortcut",
                        "status": "completed",
                        "result": {"keys": keys},
                    }
                else:
                    action_payload = _run_ui_action(
                        query=str(step.get("query") or ""),
                        window_title=step_window_title,
                        include_text=include_text if "include_text" not in step else _tobool(step.get("include_text", False)),
                        match_mode=str(step.get("match_mode") or "auto"),
                        button=str(step.get("button") or "left"),
                        action=step_action,
                        text=str(step.get("text") or ""),
                        clear=_tobool(step.get("clear", False)),
                        press_enter=_tobool(step.get("press_enter", False)),
                        wait_for_change=_tobool(step.get("wait_for_change", True)),
                        timeout_seconds=float(step.get("timeout_seconds", default_timeout_seconds) or default_timeout_seconds),
                        poll_interval=float(step.get("poll_interval", default_poll_interval) or default_poll_interval),
                        focus_window=_tobool(step.get("focus_window", True)),
                        max_elements=int(step.get("max_elements", max_elements) or max_elements),
                        min_width=int(step.get("min_width", min_width) or min_width),
                        min_height=int(step.get("min_height", min_height) or min_height),
                        grid_size=int(step.get("grid_size", grid_size) or grid_size),
                    )
                    current_window_title = str(action_payload.get("window_title") or current_window_title or "")
                    action_result: dict[str, Any]
                    if compact:
                        action_result = {
                            "status": action_payload.get("status"),
                            "interaction": action_payload.get("interaction"),
                            "target": {
                                "label": ((action_payload.get("target") or {}).get("label")),
                                "class": ((action_payload.get("target") or {}).get("class")),
                                "monitor_id": ((action_payload.get("target") or {}).get("monitor_id")),
                                "match": ((action_payload.get("target") or {}).get("match")),
                            },
                            "search": {
                                "searched_element_count": (((action_payload.get("search") or {}).get("searched_element_count")) or 0),
                                "searchable_preview": (((action_payload.get("search") or {}).get("searchable_preview")) or [])[:5],
                                "summary": ((action_payload.get("search") or {}).get("summary")),
                            },
                            "observation_after": _compact_observation_payload(action_payload.get("observation_after")),
                            "wait_condition": _compact_wait_payload(action_payload.get("wait_condition")),
                            "recommendations": (action_payload.get("recommendations") or [])[:2],
                        }
                        if action_payload.get("status") == "no-match":
                            action_result["searchable_preview"] = (action_payload.get("search") or {}).get("searchable_preview", [])[:5]
                    else:
                        action_result = action_payload
                    step_result = {
                        "step": index,
                        "action": step_action,
                        "status": action_payload.get("status", "completed"),
                        "result": action_result,
                    }

                results.append(step_result)
                if step_result.get("status") not in {"completed", "no-match"} and not continue_on_error:
                    break
                if step_result.get("status") == "no-match" and not continue_on_error:
                    break
            except Exception as step_error:
                step_result = {
                    "step": index,
                    "action": step_action,
                    "status": "error",
                    "error": str(step_error),
                }
                results.append(step_result)
                if not continue_on_error:
                    break

        completed_steps = sum(1 for item in results if item.get("status") == "completed")
        payload: dict[str, Any] = {
            "status": "completed" if results and all(item.get("status") == "completed" for item in results) else "partial",
            "step_count": len(steps),
            "executed_steps": len(results),
            "completed_steps": completed_steps,
            "compact": compact,
            "window_title": current_window_title or window_title or None,
            "results": results,
        }
        return json.dumps(payload, indent=2)
    except Exception as e:
        return f"UISequence error: {e}"


# ================================ Task Management ================================


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def CancelTask(task_id: str) -> str:
    """Cancel a running or pending task by its task ID.

    Args:
        task_id: The task ID returned when the tool was invoked (e.g. from [task:abc123]).
    """
    result = task_manager.cancel_task(task_id)
    if "error" in result:
        return f"Cancel failed: {result['error']}"
    return f"Cancelled task {task_id} ({result['tool_name']})"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def GetTaskStatus(task_id: str = "") -> str:
    """Get status of a specific task or list recent tasks.

    Args:
        task_id: If provided, get status of this task. If empty, list recent tasks.
    """
    import json

    if task_id:
        info = task_manager.get_task(task_id)
        if info is None:
            return f"Task {task_id} not found"
        return json.dumps(info, indent=2)
    tasks = task_manager.list_tasks()
    if not tasks:
        return "No tasks in history."
    lines = ["Recent tasks:"]
    for t in tasks[:20]:
        dur = f" ({t['duration']}s)" if t["duration"] is not None else ""
        err = f" — {t['error']}" if t.get("error") else ""
        lines.append(f"  [{t['task_id']}] {t['tool_name']} → {t['status']}{dur}{err}")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def GetRunningTasks() -> str:
    """List all currently running and pending tasks."""

    running = task_manager.list_tasks("running")
    pending = task_manager.list_tasks("pending")
    all_active = running + pending
    if not all_active:
        return "No active tasks."
    lines = [f"Active tasks ({len(all_active)}):"]
    for t in all_active:
        dur = f" ({t['duration']}s)" if t["duration"] is not None else ""
        lines.append(f"  [{t['task_id']}] {t['tool_name']} [{t['category']}] {t['status']}{dur}")
    return "\n".join(lines)


# ====================== Apply task manager wrapping ========================


def _get_registered_tools() -> dict[str, object]:
    """Return tool-name -> tool object map across fastmcp 2.x/3.x internals."""
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


def _remove_tool(name: str) -> None:
    """Remove a tool by name across fastmcp 2.x/3.x internals."""
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
        return


def _wrap_all_tools():
    """Wrap all registered MCP tools with task manager for error resilience + concurrency."""
    # Skip wrapping the task management tools themselves
    skip = {"CancelTask", "GetTaskStatus", "GetRunningTasks"}
    for name, tool in _get_registered_tools().items():
        if name in skip:
            continue
        original_fn = getattr(tool, "fn", None)
        if callable(original_fn):
            tool.fn = task_manager.wrap_sync_tool(name, original_fn)


_wrap_all_tools()


def _param_explicit(ctx: click.Context, name: str) -> bool:
    src = ctx.get_parameter_source(name)
    return src in {ParameterSource.COMMANDLINE, ParameterSource.ENVIRONMENT}


def _choose_value(ctx: click.Context, name: str, cli_value, config_value, default_value):
    if _param_explicit(ctx, name):
        return cli_value
    if config_value is not None:
        return config_value
    return default_value


def _apply_tool_filter(enabled_tools: set[str]) -> None:
    for tool_name in list(_get_registered_tools().keys()):
        if tool_name not in enabled_tools:
            _remove_tool(tool_name)


def _harness_url(host: str, port: int) -> str:
    """Return the Roblox Studio harness base URL."""
    return f"http://{host}:{int(port)}"


def _is_harness_healthy(*, host: str = "127.0.0.1", port: int = 51234, timeout_seconds: float = 1.0) -> bool:
    """Return whether the Roblox Studio harness is responding on the expected URL."""
    payload = roblox_studio.harness_request(
        "GET",
        "/health",
        harness_url=_harness_url(host, port),
        timeout=timeout_seconds,
    )
    data = payload.get("data") or {}
    return bool(payload.get("ok")) and str(data.get("status") or "").lower() == "ok"


def _launch_harness_process(*, host: str = "127.0.0.1", port: int = 51234, stale_after: float = 5.0, max_events: int = 100) -> subprocess.Popen:
    """Launch the Roblox Studio harness as a detached background process."""
    command = [
        sys.executable,
        "-m",
        "winremote",
        "roblox-studio",
        "serve-harness",
        "--host",
        host,
        "--port",
        str(int(port)),
        "--stale-after",
        str(float(stale_after)),
        "--max-events",
        str(int(max_events)),
    ]
    popen_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess,
            "DETACHED_PROCESS",
            0,
        )
    return subprocess.Popen(command, **popen_kwargs)


def _ensure_copilot_harness_running(
    *,
    host: str = "127.0.0.1",
    port: int = 51234,
    stale_after: float = 5.0,
    max_events: int = 100,
    startup_timeout: float = 5.0,
    poll_interval: float = 0.2,
) -> bool:
    """Ensure the Roblox Studio harness is running for Copilot Chat workflows."""
    if _is_harness_healthy(host=host, port=port):
        return False

    _launch_harness_process(host=host, port=port, stale_after=stale_after, max_events=max_events)
    deadline = time.monotonic() + max(0.5, float(startup_timeout))
    while time.monotonic() < deadline:
        if _is_harness_healthy(host=host, port=port):
            return True
        time.sleep(max(0.05, float(poll_interval)))

    raise click.ClickException(
        "Roblox Studio harness did not become healthy in time. "
        f"Expected it at {_harness_url(host, port)}/health."
    )


def _run_mcp_server(
    *,
    transport: str,
    host: str,
    port: int,
    reload: bool,
    auth_key: str | None,
    profile: str,
    enable_all: bool,
    enable_tier3: bool,
    disable_tier2: bool,
    selected_tools: list[str],
    excluded_tools: list[str],
    allowlist_entries: list[str],
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
    oauth_client_id: str | None,
    oauth_client_secret: str | None,
) -> None:
    """Start the MCP server after configuration has been resolved."""
    enabled_tools = resolve_enabled_tools(
        profile=profile,
        enable_tier3=enable_tier3,
        disable_tier2=disable_tier2,
        enable_all=enable_all,
        explicit_tools=selected_tools,
        exclude_tools=excluded_tools,
    )
    mcp.instructions = _server_instructions_for_profile(profile)
    _apply_tool_filter(enabled_tools)
    enabled_tiers = get_tier_names(enabled_tools)

    oauth_store = None
    oauth_validator = None
    use_oauth = bool(oauth_client_id or oauth_client_secret)

    if use_oauth and transport != "stdio":
        from winremote.oauth import OAuthStore, build_oauth_routes, validate_oauth_token

        oauth_store = OAuthStore()
        scheme = "https" if (ssl_certfile and ssl_keyfile) else "http"
        issuer = f"{scheme}://{host}:{port}"

        routes = build_oauth_routes(
            store=oauth_store,
            issuer=issuer,
            configured_client_id=oauth_client_id,
            configured_client_secret=oauth_client_secret,
        )
        for path, (handler, methods) in routes.items():
            # Primary OAuth endpoints (spec-compliant root paths)
            mcp.custom_route(path, methods=methods)(handler)

            # Compatibility aliases for clients that incorrectly resolve OAuth
            # discovery/token endpoints relative to /mcp.
            prefixed_path = f"/mcp{path}"
            mcp.custom_route(prefixed_path, methods=methods)(handler)

        oauth_validator = lambda tok: validate_oauth_token(oauth_store, tok)  # noqa: E731

    middleware: list[Middleware] = []

    if allowlist_entries:
        allowlist_networks = parse_ip_allowlist(allowlist_entries)
        middleware.append(Middleware(IPAllowlistMiddleware, allowlist=allowlist_networks))

    if auth_key:
        from winremote.auth import AuthKeyMiddleware

        middleware.append(Middleware(AuthKeyMiddleware, auth_key=auth_key, oauth_validator=oauth_validator))
    elif oauth_validator:
        from winremote.auth import OAuthOnlyMiddleware

        middleware.append(Middleware(OAuthOnlyMiddleware, oauth_validator=oauth_validator))

    import logging

    class BannerFilter(logging.Filter):
        """Inject our banner after uvicorn's 'Application startup complete' log."""

        _shown = False

        def filter(self, record):
            if not self._shown and "Application startup complete" in record.getMessage():
                self._shown = True
                auth_line = "[auth ON]" if auth_key else "[no auth]"
                ssl_line = "[https ON]" if (ssl_certfile and ssl_keyfile) else ""
                oauth_line = "[oauth ON]" if use_oauth else ""
                bind_line = f"[{host}:{port}]"
                profile_line = f"[profile: {profile}]"
                tiers_line = f"[tiers: {','.join(enabled_tiers)}]"
                tools_line = f"[tools: {len(enabled_tools)}/{len(ALL_TOOLS)}]"
                pad = " " * 10
                ver_line = f"winremote-mcp v{__version__}"
                lines = [
                    f"{pad}+----------------------------------+",
                    f"{pad}|  {ver_line:<32s}|",
                    f"{pad}|  by dddabtc                      |",
                    f"{pad}|  github.com/dddabtc              |",
                    f"{pad}|  {auth_line:<32s}|",
                    *([f"{pad}|  {ssl_line:<32s}|"] if ssl_line else []),
                    *([f"{pad}|  {oauth_line:<32s}|"] if oauth_line else []),
                    f"{pad}|  {bind_line:<32s}|",
                    f"{pad}|  {profile_line:<32s}|",
                    f"{pad}|  {tiers_line:<16s}{tools_line:<16s}|",
                    f"{pad}+----------------------------------+",
                ]
                if host == "0.0.0.0" and not auth_key:
                    lines.append(f"{pad}  WARNING: open to network without auth!")
                    lines.append(f"{pad}  Use --auth-key for security.")
                if enable_all:
                    lines.append(f"{pad}  INFO: High-risk Tier 3 tools enabled!")
                print("\n" + "\n".join(lines) + "\n", flush=True)
            return True

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        logging.getLogger("uvicorn.error").addFilter(BannerFilter())
        run_kwargs = dict(transport="streamable-http", host=host, port=port)
        if middleware:
            run_kwargs["middleware"] = middleware
        if platform.system() == "Windows":
            os.environ.setdefault("NO_COLOR", "1")
        uvicorn_config = {}
        if reload:
            uvicorn_config["reload"] = True
        if ssl_certfile and ssl_keyfile:
            uvicorn_config["ssl_certfile"] = ssl_certfile
            uvicorn_config["ssl_keyfile"] = ssl_keyfile
        if uvicorn_config:
            run_kwargs["uvicorn_config"] = uvicorn_config
        mcp.run(**run_kwargs)


# ================================== CLI ====================================


@click.group(invoke_without_command=True)
@click.option("--transport", default="streamable-http", type=click.Choice(["stdio", "streamable-http"]))
@click.option("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1; use 0.0.0.0 for remote access)")
@click.option("--port", default=8090, type=int)
@click.option("--reload", is_flag=True, default=False, help="Enable hot reload (streamable-http only)")
@click.option("--auth-key", default=None, envvar="WINREMOTE_AUTH_KEY", help="API key for authentication")
@click.option("--config", default=None, help="Path to winremote.toml config file")
@click.option(
    "--profile",
    default="default",
    type=click.Choice(PROFILE_CHOICES),
    help="Tool and instruction profile",
)
@click.option(
    "--enable-all",
    is_flag=True,
    default=False,
    help="Enable all tools including high-risk Tier 3 tools (backward-compatible)",
)
@click.option("--enable-tier3", is_flag=True, default=False, help="Enable tier3 destructive tools")
@click.option("--disable-tier2", is_flag=True, default=False, help="Disable tier2 interactive tools")
@click.option("--tools", default="", help="Comma-separated tools to enable (highest precedence)")
@click.option("--exclude-tools", default="", help="Comma-separated tools to disable")
@click.option("--ip-allowlist", default="", help="Comma-separated IPs/CIDRs allowed to access HTTP transport")
@click.option("--ssl-certfile", default=None, help="Path to SSL certificate file for HTTPS")
@click.option("--ssl-keyfile", default=None, help="Path to SSL private key file for HTTPS")
@click.option("--oauth-client-id", default=None, envvar="WINREMOTE_OAUTH_CLIENT_ID", help="OAuth client ID whitelist")
@click.option("--oauth-client-secret", default=None, envvar="WINREMOTE_OAUTH_CLIENT_SECRET", help="OAuth client secret")
@click.pass_context
def cli(
    ctx,
    transport: str,
    host: str,
    port: int,
    reload: bool,
    auth_key: str | None,
    config: str | None,
    profile: str,
    enable_all: bool,
    enable_tier3: bool,
    disable_tier2: bool,
    tools: str,
    exclude_tools: str,
    ip_allowlist: str,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
    oauth_client_id: str | None,
    oauth_client_secret: str | None,
):
    """Start the winremote MCP server."""
    if ctx.invoked_subcommand is not None:
        return  # subcommand will handle it

    config_path = discover_config_path(config)
    cfg = load_config(config_path)

    host = _choose_value(ctx, "host", host, cfg.server.host, "127.0.0.1")
    port = int(_choose_value(ctx, "port", port, cfg.server.port, 8090))
    auth_key = _choose_value(ctx, "auth_key", auth_key, cfg.server.auth_key, None)
    profile = _choose_value(ctx, "profile", profile, cfg.server.profile, "default")
    ssl_certfile = _choose_value(ctx, "ssl_certfile", ssl_certfile, cfg.server.ssl_certfile, None)
    ssl_keyfile = _choose_value(ctx, "ssl_keyfile", ssl_keyfile, cfg.server.ssl_keyfile, None)
    oauth_client_id = _choose_value(ctx, "oauth_client_id", oauth_client_id, cfg.security.oauth_client_id, None)
    oauth_client_secret = _choose_value(
        ctx,
        "oauth_client_secret",
        oauth_client_secret,
        cfg.security.oauth_client_secret,
        None,
    )

    enable_tier3 = bool(_choose_value(ctx, "enable_tier3", enable_tier3, cfg.security.enable_tier3, False))
    disable_tier2 = bool(_choose_value(ctx, "disable_tier2", disable_tier2, cfg.security.disable_tier2, False))

    cli_tools = parse_tool_csv(tools)
    cli_excluded = parse_tool_csv(exclude_tools)
    cli_allowlist = parse_tool_csv(ip_allowlist)

    selected_tools = cli_tools if _param_explicit(ctx, "tools") else cfg.tools.enable
    excluded_tools = cli_excluded if _param_explicit(ctx, "exclude_tools") else cfg.tools.exclude
    allowlist_entries = cli_allowlist if _param_explicit(ctx, "ip_allowlist") else cfg.security.ip_allowlist

    _run_mcp_server(
        transport=transport,
        host=host,
        port=port,
        reload=reload,
        auth_key=auth_key,
        profile=profile,
        enable_all=enable_all,
        enable_tier3=enable_tier3,
        disable_tier2=disable_tier2,
        selected_tools=selected_tools,
        excluded_tools=excluded_tools,
        allowlist_entries=allowlist_entries,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        oauth_client_id=oauth_client_id,
        oauth_client_secret=oauth_client_secret,
    )


@cli.command(name="copilot-launch")
@click.option("--harness-host", default="127.0.0.1", help="Bind address for the local Roblox Studio harness")
@click.option("--harness-port", default=51234, type=int, help="Bind port for the local Roblox Studio harness")
@click.option("--harness-stale-after", default=5.0, type=float, help="Seconds before a Studio client is treated as disconnected")
@click.option("--harness-max-events", default=100, type=int, help="Maximum recent harness events to retain in memory")
@click.option(
    "--skip-harness",
    is_flag=True,
    default=False,
    help="Start only the Copilot Chat MCP server and do not auto-launch the Roblox Studio harness",
)
def copilot_launch(
    harness_host: str,
    harness_port: int,
    harness_stale_after: float,
    harness_max_events: int,
    skip_harness: bool,
) -> None:
    """Launch the Copilot Chat stdio server and auto-start the Roblox harness if needed.

    For GitHub Copilot CLI, use ``copilot-cli-launch`` instead.
    """
    if not skip_harness:
        os.environ.setdefault("WINREMOTE_ROBLOX_STUDIO_HARNESS_URL", _harness_url(harness_host, harness_port))
        _ensure_copilot_harness_running(
            host=harness_host,
            port=harness_port,
            stale_after=harness_stale_after,
            max_events=harness_max_events,
        )

    _run_mcp_server(
        transport="stdio",
        host="127.0.0.1",
        port=8090,
        reload=False,
        auth_key=None,
        profile="copilot",
        enable_all=False,
        enable_tier3=False,
        disable_tier2=False,
        selected_tools=[],
        excluded_tools=[],
        allowlist_entries=[],
        ssl_certfile=None,
        ssl_keyfile=None,
        oauth_client_id=None,
        oauth_client_secret=None,
    )


@cli.command(name="copilot-cli-launch")
def copilot_cli_launch() -> None:
    """Launch the GitHub Copilot CLI stdio server using the ``copilot-cli`` profile.

    This command does not auto-start the Roblox Studio harness.
    """
    _run_mcp_server(
        transport="stdio",
        host="127.0.0.1",
        port=8090,
        reload=False,
        auth_key=None,
        profile="copilot-cli",
        enable_all=False,
        enable_tier3=False,
        disable_tier2=False,
        selected_tools=[],
        excluded_tools=[],
        allowlist_entries=[],
        ssl_certfile=None,
        ssl_keyfile=None,
        oauth_client_id=None,
        oauth_client_secret=None,
    )


@cli.command()
@click.option("--certfile", default="cert.pem", help="Output certificate file (default: cert.pem)")
@click.option("--keyfile", default="key.pem", help="Output private key file (default: key.pem)")
@click.option("--days", default=825, type=int, help="Certificate validity in days (default: 825)")
@click.option("--hostname", default="localhost", help="Hostname/IP to include in SAN (default: localhost)")
def gencert(certfile: str, keyfile: str, days: int, hostname: str) -> None:
    """Generate a self-signed TLS certificate for HTTPS mode."""
    import datetime
    import ipaddress

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        click.echo("[ERROR] 'cryptography' package not found. Run: pip install cryptography")
        raise SystemExit(1)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    san_entries: list = [x509.DNSName("localhost"), x509.DNSName(hostname)]
    try:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(hostname)))
    except ValueError:
        pass
    san_entries.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open(keyfile, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    with open(certfile, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    click.echo(f"[OK] Private key : {keyfile}")
    click.echo(f"[OK] Certificate : {certfile}")
    click.echo(f"[OK] Valid for   : {days} days")
    click.echo(f"[OK] SAN hosts   : {', '.join(str(e.value) for e in san_entries)}")
    click.echo("")
    click.echo("Start the server with:")
    click.echo(f"  winremote-mcp --host 0.0.0.0 --auth-key YOUR_KEY --ssl-certfile {certfile} --ssl-keyfile {keyfile}")


@cli.command()
def install():
    """Create a Windows scheduled task for auto-start."""
    import getpass
    import os

    username = getpass.getuser()

    # Create start_mcp.bat for Chinese Windows compatibility
    python_exe = subprocess.run(["where", "python"], capture_output=True, text=True).stdout.strip().split("\n")[0]
    bat_content = f"""@echo off
rem winremote-mcp startup script with UTF-8 encoding for Chinese Windows
set PYTHONIOENCODING=utf-8
"{python_exe}" -m winremote %*
"""

    # Write batch file to user's profile directory
    user_profile = os.environ.get("USERPROFILE", ".")
    bat_path = os.path.join(user_profile, "start_mcp.bat")

    try:
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(bat_content)
        click.echo(f"[OK] Created startup script: {bat_path}")
    except Exception as e:
        click.echo(f"[ERROR] Failed to create startup script: {e}")
        return

    # Create scheduled task using the batch file
    task_cmd = f'schtasks /Create /SC ONSTART /TN "WinRemoteMCP" /TR "{bat_path}" /RU {username} /F'
    try:
        result = subprocess.run(task_cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            click.echo("[OK] Scheduled task 'WinRemoteMCP' created for auto-start.")
            click.echo("The server will start automatically on system boot.")
            click.echo("Note: Uses start_mcp.bat for Chinese Windows compatibility.")
        else:
            click.echo(f"[ERROR] Failed to create task:\n{result.stderr or result.stdout}")
    except Exception as e:
        click.echo(f"[ERROR] Error: {e}")


@cli.command()
def uninstall():
    """Remove the WinRemoteMCP scheduled task."""
    import os

    task_cmd = 'schtasks /Delete /TN "WinRemoteMCP" /F'
    try:
        result = subprocess.run(task_cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            click.echo("[OK] Scheduled task 'WinRemoteMCP' removed.")
        else:
            click.echo(f"[ERROR] Failed to remove task:\n{result.stderr or result.stdout}")
    except Exception as e:
        click.echo(f"[ERROR] Error: {e}")

    # Also remove the batch file
    user_profile = os.environ.get("USERPROFILE", ".")
    bat_path = os.path.join(user_profile, "start_mcp.bat")
    try:
        if os.path.exists(bat_path):
            os.remove(bat_path)
            click.echo(f"[OK] Removed startup script: {bat_path}")
    except Exception as e:
        click.echo(f"[ERROR] Failed to remove startup script: {e}")


@cli.group(name="roblox-studio")
def roblox_studio_cli():
    """Helpers for Roblox Studio playtest harness setup."""


@roblox_studio_cli.command(name="serve-harness")
@click.option("--host", default="127.0.0.1", help="Bind address for the local harness")
@click.option("--port", default=51234, type=int, help="Bind port for the local harness")
@click.option("--stale-after", default=5.0, type=float, help="Seconds before a Studio client is treated as disconnected")
@click.option("--max-events", default=100, type=int, help="Maximum recent events to retain in memory")
def serve_roblox_studio_harness(host: str, port: int, stale_after: float, max_events: int) -> None:
    """Run the local Roblox Studio harness used by RobloxStudio* MCP tools."""
    click.echo(f"[OK] Roblox Studio harness listening on http://{host}:{port}")
    click.echo("[OK] Use Ctrl+C to stop it")
    roblox_studio_harness.serve_harness(
        host=host,
        port=port,
        stale_after_seconds=stale_after,
        max_events=max_events,
    )


@roblox_studio_cli.command(name="export-harness")
@click.option("--output-dir", default="roblox-studio-harness", help="Directory to write Studio harness files into")
@click.option("--harness-url", default="http://127.0.0.1:51234", help="Harness URL Studio should call during playtests")
def export_roblox_studio_harness(output_dir: str, harness_url: str) -> None:
    """Export the Luau files that run inside a Roblox Studio playtest."""
    written = roblox_studio_harness.export_studio_harness_files(output_dir, harness_url=harness_url)
    click.echo("[OK] Wrote Roblox Studio harness files:")
    for path in written:
        click.echo(f"  {path}")


@cli.group(name="sessions")
def sessions_cli():
    """Helpers for local session artifacts and reports."""


@sessions_cli.command(name="render")
@click.argument("session_id")
def render_session_report_command(session_id: str) -> None:
    """Render a local HTML report for a session id."""
    payload = session_report.render_session_report(session_id)
    click.echo(f"[OK] Rendered report: {payload['report_path']}")


@cli.command()
def health():
    """Print health status JSON."""
    import json

    click.echo(json.dumps({"status": "ok", "version": __version__}))


if __name__ == "__main__":
    cli()
