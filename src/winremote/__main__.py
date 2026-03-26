"""winremote-mcp — CLI entry point and MCP tool definitions."""

from __future__ import annotations

import base64
import json
import os
import platform
import subprocess
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

from winremote import __version__, desktop, network, ocr, process_mgr, recording, registry, services
from winremote.config import discover_config_path, load_config
from winremote.security import IPAllowlistMiddleware, parse_ip_allowlist
from winremote.taskmanager import manager as task_manager
from winremote.tiers import ALL_TOOLS, get_tier_names, parse_tool_csv, resolve_enabled_tools

load_dotenv()

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

mcp = FastMCP(
    "winremote-mcp",
    instructions=(
        "Windows Remote MCP Server. Provides desktop control, window management, "
        "shell execution, file operations, network tools, registry, services, "
        "and system management tools for a Windows machine."
    ),
)


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
) -> list:
    """Capture desktop screenshot, window list, and interactive UI elements.

    Args:
        use_vision: Include screenshot image (default True).
        quality: JPEG quality 1-100 (default 75). Lower = smaller.
        max_width: Max image width in pixels. 0=native resolution (default). Set to e.g. 1920 to downscale.
        monitor: Monitor to capture. 0=all monitors (default), 1/2/3=specific monitor.

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
                b64 = desktop.take_screenshot(quality=quality, max_width=max_width, monitor=monitor)
            except Exception as screenshot_error:
                # Check if a disconnected session is the cause
                reconnect_result = _ensure_session_connected()
                if reconnect_result is not None:
                    # Session wasn't disconnected (or reconnect failed) — not a session issue
                    return [f"Snapshot error: {screenshot_error}"]
                # Session was disconnected and reconnected, retry
                try:
                    b64 = desktop.take_screenshot(quality=quality, max_width=max_width, monitor=monitor)
                except Exception as retry_error:
                    return [f"Snapshot error (after session reconnect): {retry_error}"]
            parts.append(ImageContent(type="image", data=b64, mimeType="image/jpeg"))

        # Window list
        windows = desktop.enumerate_windows()
        win_lines = [f"**System Language:** {desktop._get_system_language()}"]
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
        if cwd:
            command = f"cd {cwd}; {command}"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[STDERR] {result.stderr}"
        if result.returncode != 0:
            output += f"\n[Exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except Exception as e:
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
) -> list:
    """Take a screenshot with numbered labels on interactive UI elements.

    Draws red rectangles and white numbered labels on each interactive element,
    making it easy for AI agents to identify click targets visually.

    Args:
        max_elements: Maximum number of elements to annotate (default 30).
        quality: JPEG quality 1-100 (default 75).
        max_width: Max image width in pixels. 0=native resolution (default).
    """
    try:
        import io

        from PIL import ImageDraw, ImageFont

        # Take screenshot (auto-reconnect session if grab fails)
        try:
            img, capture_meta = desktop.capture_image()
        except Exception as screenshot_error:
            reconnect_result = _ensure_session_connected()
            if reconnect_result is not None:
                return [TextContent(type="text", text=f"AnnotatedSnapshot error: {screenshot_error}")]
            try:
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
        mapped = desktop.map_ui_elements(max_elements=max_elements)
        elements = mapped[1:] if len(mapped) > 1 else []
        if not elements:
            # Return screenshot with no annotations
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return [
                ImageContent(type="image", data=b64, mimeType="image/jpeg"),
                TextContent(type="text", text="No interactive elements found."),
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

        text_summary = f"**Annotated {len(element_lines)} elements:**\n" + "\n".join(element_lines)
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
        payload = {
            "query": query,
            "window_title": window_title or None,
            "match_mode": match_mode,
            "count": len(search_result["matches"]),
            "matches": search_result["matches"],
            "searched_element_count": search_result["searched_element_count"],
            "searchable_preview": search_result["searchable_preview"],
            "summary": search_result["summary"],
            "recommendations": search_result["recommendations"],
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


def _compact_observation_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a compact observation summary suitable for low-overhead chat flows."""
    if payload is None:
        return None
    return {
        "target": {
            "mode": ((payload.get("target") or {}).get("mode")),
            "window_title": (((payload.get("target") or {}).get("window") or {}).get("title")),
        },
        "changed": payload.get("changed"),
        "change_ratio": payload.get("change_ratio"),
        "changed_regions": (payload.get("changed_regions") or [])[:3],
        "searchable_preview": (payload.get("searchable_preview") or [])[:5],
        "recommendations": (payload.get("recommendations") or [])[:2],
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
        "searched_element_count": payload.get("searched_element_count"),
        "searchable_preview": (payload.get("searchable_preview") or [])[:5],
        "recommendations": (payload.get("recommendations") or [])[:2],
        "observation_after": _compact_observation_payload(payload.get("observation_after")),
    }


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
    if not matches:
        return {
            "query": query,
            "window_title": window_title or None,
            "status": "no-match",
            "action": action,
            "search": {
                "searched_element_count": search_result.get("searched_element_count", 0),
                "searchable_preview": search_result.get("searchable_preview", []),
                "summary": search_result.get("summary"),
                "recommendations": search_result.get("recommendations", []),
            },
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
            "searched_element_count": search_result.get("searched_element_count", 0),
            "searchable_preview": search_result.get("searchable_preview", []),
            "summary": search_result.get("summary"),
            "recommendations": search_result.get("recommendations", []),
        },
        "observation_before": observation_before,
        "observation_after": observation_after,
        "wait_for_change": wait_for_change,
        "wait_condition": wait_condition,
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
                            "observation_after": _compact_observation_payload(action_payload.get("observation_after")),
                            "wait_condition": _compact_wait_payload(action_payload.get("wait_condition")),
                            "recommendations": (action_payload.get("search") or {}).get("recommendations", [])[:2],
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


# ================================== CLI ====================================


@click.group(invoke_without_command=True)
@click.option("--transport", default="streamable-http", type=click.Choice(["stdio", "streamable-http"]))
@click.option("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1; use 0.0.0.0 for remote access)")
@click.option("--port", default=8090, type=int)
@click.option("--reload", is_flag=True, default=False, help="Enable hot reload (streamable-http only)")
@click.option("--auth-key", default=None, envvar="WINREMOTE_AUTH_KEY", help="API key for authentication")
@click.option("--config", default=None, help="Path to winremote.toml config file")
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

    enabled_tools = resolve_enabled_tools(
        enable_tier3=enable_tier3,
        disable_tier2=disable_tier2,
        enable_all=enable_all,
        explicit_tools=selected_tools,
        exclude_tools=excluded_tools,
    )
    _apply_tool_filter(enabled_tools)
    enabled_tiers = get_tier_names(enabled_tools)

    # ---- OAuth setup ----
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
            mcp.custom_route(path, methods=methods)(handler)

        oauth_validator = lambda tok: validate_oauth_token(oauth_store, tok)  # noqa: E731

    # ---- Middleware ----
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
                tiers_line = f"[tiers: {','.join(enabled_tiers)}]"
                tools_line = f"[tools: {len(enabled_tools)}/{len(ALL_TOOLS)}]"
                pad = " " * 10  # align with uvicorn log text
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
        uvicorn_args = {}
        if reload:
            uvicorn_args["reload"] = True
        if ssl_certfile and ssl_keyfile:
            uvicorn_args["ssl_certfile"] = ssl_certfile
            uvicorn_args["ssl_keyfile"] = ssl_keyfile
        if uvicorn_args:
            run_kwargs["uvicorn_args"] = uvicorn_args
        mcp.run(**run_kwargs)


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


@cli.command()
def health():
    """Print health status JSON."""
    import json

    click.echo(json.dumps({"status": "ok", "version": __version__}))


if __name__ == "__main__":
    cli()
