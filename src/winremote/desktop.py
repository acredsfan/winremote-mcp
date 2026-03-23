"""Win32 desktop interactions — screenshots, window enumeration, UI elements."""

from __future__ import annotations

import base64
import ctypes
import io
import locale
import re
from dataclasses import dataclass
from typing import Optional

import pyautogui

# Win32 imports (will fail on non-Windows — caught at tool level)
try:
    import win32api  # noqa: F401
    import win32clipboard
    import win32con
    import win32gui
    import win32process

    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

from PIL import ImageGrab

_UI_WATCH_BASELINES: dict[str, list[dict]] = {}

# Enable DPI awareness so screenshots capture native resolution (e.g. 4K)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tobool(v: bool | str) -> bool:
    """Handle MCP's bool-as-string quirk."""
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def _get_system_language() -> str:
    """Return current Windows display language."""
    try:
        return locale.getdefaultlocale()[0] or "en_US"
    except Exception:
        return "en_US"


# ---------------------------------------------------------------------------
# Window info
# ---------------------------------------------------------------------------


@dataclass
class WindowInfo:
    handle: int
    title: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    visible: bool
    pid: int = 0

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def enumerate_windows() -> list[WindowInfo]:
    """List all visible top-level windows."""
    if not HAS_WIN32:
        raise RuntimeError("pywin32 not installed — run `pip install pywin32`")
    results: list[WindowInfo] = []

    def _cb(hwnd: int, _extra: None) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        rect = win32gui.GetWindowRect(hwnd)
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        results.append(WindowInfo(handle=hwnd, title=title, rect=rect, visible=True, pid=pid))
        return True

    win32gui.EnumWindows(_cb, None)
    return results


def get_interactive_elements() -> list[dict]:
    """Simplified accessibility tree — enumerate child windows with class/text."""
    if not HAS_WIN32:
        raise RuntimeError("pywin32 not installed — run `pip install pywin32`")
    fg = win32gui.GetForegroundWindow()
    if not fg:
        return []
    elements: list[dict] = []
    idx = [0]

    def _cb(hwnd: int, _extra: None) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        cls = win32gui.GetClassName(hwnd)
        text = win32gui.GetWindowText(hwnd)
        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        idx[0] += 1
        elements.append(
            {
                "index": idx[0],
                "class": cls,
                "text": text,
                "rect": {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]},
            }
        )
        return True

    try:
        win32gui.EnumChildWindows(fg, _cb, None)
    except Exception:
        pass
    return elements


def _find_window_by_title(title: str) -> WindowInfo | None:
    """Best-effort fuzzy find a top-level window by title."""
    title = (title or "").strip()
    if not title:
        return None
    windows = enumerate_windows()
    q = title.lower()

    # Prefer exact/contains matches first
    exact = [w for w in windows if w.title.lower() == q]
    if exact:
        return exact[0]
    contains = [w for w in windows if q in w.title.lower()]
    if contains:
        return contains[0]

    try:
        from thefuzz import fuzz

        best: WindowInfo | None = None
        best_score = 0
        for w in windows:
            score = fuzz.partial_ratio(q, w.title.lower())
            if score > best_score:
                best = w
                best_score = score
        if best is not None and best_score >= 50:
            return best
    except Exception:
        pass
    return None


def map_ui_elements(
    window_title: str = "",
    include_text: bool = False,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
) -> list[dict]:
    """Map UI elements and their coordinates for a target window or foreground window.

    Returns top-level window and child controls with absolute coordinates.
    If include_text=True, also runs OCR on each element bbox and includes extracted text.
    """
    if not HAS_WIN32:
        raise RuntimeError("pywin32 not installed — run `pip install pywin32`")

    target_hwnd: int | None = None
    target_window: WindowInfo | None = None
    if window_title:
        target_window = _find_window_by_title(window_title)
        if target_window is None:
            raise ValueError(f"No window matching '{window_title}'")
        target_hwnd = target_window.handle
    else:
        target_hwnd = win32gui.GetForegroundWindow()
        if target_hwnd:
            rect = win32gui.GetWindowRect(target_hwnd)
            target_window = WindowInfo(
                handle=target_hwnd,
                title=win32gui.GetWindowText(target_hwnd),
                rect=rect,
                visible=True,
                pid=0,
            )

    if not target_hwnd or target_window is None:
        raise RuntimeError("No active window found")

    left, top, right, bottom = target_window.rect
    results: list[dict] = [
        {
            "index": 0,
            "type": "window",
            "class": "Window",
            "handle": target_hwnd,
            "label": target_window.title,
            "rect": {"left": left, "top": top, "right": right, "bottom": bottom},
            "relative_rect": {"left": 0, "top": 0, "right": right - left, "bottom": bottom - top},
            "size": {"width": right - left, "height": bottom - top},
            "center": {"x": (left + right) // 2, "y": (top + bottom) // 2},
            "relative_center": {"x": (right - left) // 2, "y": (bottom - top) // 2},
        }
    ]

    idx = 0

    def _cb(hwnd: int, _extra: None) -> bool:
        nonlocal idx
        if len(results) - 1 >= max_elements:
            return False
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            r = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        w = r[2] - r[0]
        h = r[3] - r[1]
        if w < min_width or h < min_height:
            return True
        idx += 1
        cls = ""
        text = ""
        try:
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            pass
        try:
            text = win32gui.GetWindowText(hwnd)
        except Exception:
            pass

        label = text or cls or f"element-{idx}"
        item = {
            "index": idx,
            "type": "control",
            "handle": hwnd,
            "class": cls,
            "label": label,
            "window_text": text,
            "rect": {"left": r[0], "top": r[1], "right": r[2], "bottom": r[3]},
            "relative_rect": {
                "left": r[0] - left,
                "top": r[1] - top,
                "right": r[2] - left,
                "bottom": r[3] - top,
            },
            "size": {"width": w, "height": h},
            "center": {"x": (r[0] + r[2]) // 2, "y": (r[1] + r[3]) // 2},
            "relative_center": {"x": ((r[0] + r[2]) // 2) - left, "y": ((r[1] + r[3]) // 2) - top},
        }

        if include_text:
            try:
                from winremote import ocr

                ocr_text = ocr.run_ocr(left=r[0], top=r[1], right=r[2], bottom=r[3])
                if ocr_text:
                    ocr_text = re.sub(r"\s+", " ", ocr_text).strip()
                if ocr_text:
                    item["ocr_text"] = ocr_text[:500]
            except Exception:
                pass

        results.append(item)
        return True

    try:
        win32gui.EnumChildWindows(target_hwnd, _cb, None)
    except Exception:
        pass

    return results


def _normalize_search_text(value: str | None) -> str:
    """Normalize UI text for matching."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _element_search_fields(element: dict) -> dict[str, str]:
    """Return searchable text fields for a mapped UI element."""
    return {
        "label": str(element.get("label") or ""),
        "window_text": str(element.get("window_text") or ""),
        "class": str(element.get("class") or ""),
        "ocr_text": str(element.get("ocr_text") or ""),
    }


def find_ui_elements(
    query: str,
    window_title: str = "",
    include_text: bool = False,
    max_results: int = 5,
    match_mode: str = "auto",
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
) -> list[dict]:
    """Find best matching UI elements for a text/class query.

    Supported match modes: auto, exact, contains, fuzzy, regex.
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required")

    if match_mode not in {"auto", "exact", "contains", "fuzzy", "regex"}:
        raise ValueError("match_mode must be one of: auto, exact, contains, fuzzy, regex")

    mapped = map_ui_elements(
        window_title=window_title,
        include_text=include_text,
        max_elements=max_elements,
        min_width=min_width,
        min_height=min_height,
    )
    elements = mapped[1:]
    q_norm = _normalize_search_text(query)
    q_tokens = set(q_norm.split())
    try:
        from thefuzz import fuzz
    except Exception:
        fuzz = None

    pattern = None
    if match_mode == "regex":
        pattern = re.compile(query, re.IGNORECASE)

    matches: list[dict] = []
    for el in elements:
        best_score = 0
        best_type = ""
        best_field = ""
        best_value = ""
        for field_name, raw_value in _element_search_fields(el).items():
            value = _normalize_search_text(raw_value)
            if not value:
                continue

            score = 0
            match_type = ""
            if match_mode == "regex":
                if pattern and pattern.search(raw_value):
                    score = 100
                    match_type = "regex"
            else:
                allow_exact = match_mode in {"auto", "exact"}
                allow_contains = match_mode in {"auto", "contains"}
                allow_fuzzy = match_mode in {"auto", "fuzzy"}

                if allow_exact and value == q_norm:
                    score = 100
                    match_type = "exact"
                elif allow_contains and q_norm in value:
                    score = 90
                    match_type = "contains"
                elif allow_contains and q_tokens:
                    overlap = len(q_tokens & set(value.split()))
                    if overlap:
                        score = max(score, min(85, 60 + overlap * 10))
                        match_type = match_type or "tokens"
                if allow_fuzzy and fuzz is not None:
                    fuzzy_score = int(fuzz.partial_ratio(q_norm, value))
                    if fuzzy_score >= max(60, score):
                        score = fuzzy_score
                        match_type = "fuzzy"

            if score > best_score:
                best_score = score
                best_type = match_type
                best_field = field_name
                best_value = raw_value

        if best_score > 0:
            item = dict(el)
            item["match"] = {
                "query": query,
                "score": best_score,
                "type": best_type,
                "field": best_field,
                "value": best_value,
            }
            matches.append(item)

    matches.sort(key=lambda item: (-item["match"]["score"], item.get("index", 0)))
    return matches[:max_results]


def _watch_key(
    window_title: str,
    include_text: bool,
    max_elements: int,
    min_width: int,
    min_height: int,
) -> str:
    """Build a stable cache key for UI watch baselines."""
    return "|".join(
        [
            window_title.strip().lower() or "__foreground__",
            "1" if include_text else "0",
            str(max_elements),
            str(min_width),
            str(min_height),
        ]
    )


def _watch_identity(element: dict) -> tuple[str, str]:
    """Return a stable-ish identity for UI diffing."""
    return (
        _normalize_search_text(element.get("label") or element.get("window_text") or ""),
        _normalize_search_text(element.get("class") or ""),
    )


def _with_occurrence_keys(elements: list[dict]) -> dict[tuple[str, str, int], dict]:
    """Assign occurrence-based identities so duplicate labels/classes can be diffed."""
    counters: dict[tuple[str, str], int] = {}
    keyed: dict[tuple[str, str, int], dict] = {}
    ordered = sorted(
        elements,
        key=lambda el: (
            el.get("relative_center", {}).get("y", 0),
            el.get("relative_center", {}).get("x", 0),
            el.get("index", 0),
        ),
    )
    for el in ordered:
        ident = _watch_identity(el)
        occurrence = counters.get(ident, 0)
        counters[ident] = occurrence + 1
        keyed[(ident[0], ident[1], occurrence)] = el
    return keyed


def diff_ui_maps(previous: list[dict], current: list[dict]) -> dict:
    """Diff two UI maps and report added/removed/moved/text-changed controls."""
    prev_controls = previous[1:] if previous else []
    curr_controls = current[1:] if current else []
    prev_keyed = _with_occurrence_keys(prev_controls)
    curr_keyed = _with_occurrence_keys(curr_controls)

    prev_keys = set(prev_keyed)
    curr_keys = set(curr_keyed)

    added = [curr_keyed[key] for key in sorted(curr_keys - prev_keys)]
    removed = [prev_keyed[key] for key in sorted(prev_keys - curr_keys)]
    moved: list[dict] = []
    text_changed: list[dict] = []

    for key in sorted(prev_keys & curr_keys):
        prev_el = prev_keyed[key]
        curr_el = curr_keyed[key]
        prev_rel_center = prev_el.get("relative_center") or prev_el.get("center") or {"x": 0, "y": 0}
        curr_rel_center = curr_el.get("relative_center") or curr_el.get("center") or {"x": 0, "y": 0}
        prev_rel_rect = prev_el.get("relative_rect") or prev_el.get("rect") or {}
        curr_rel_rect = curr_el.get("relative_rect") or curr_el.get("rect") or {}

        if prev_rel_center != curr_rel_center or prev_rel_rect != curr_rel_rect:
            moved.append(
                {
                    "label": curr_el.get("label") or curr_el.get("class") or "",
                    "class": curr_el.get("class") or "",
                    "from": {
                        "center": prev_el.get("center"),
                        "relative_center": prev_el.get("relative_center"),
                        "rect": prev_el.get("rect"),
                        "relative_rect": prev_el.get("relative_rect"),
                    },
                    "to": {
                        "center": curr_el.get("center"),
                        "relative_center": curr_el.get("relative_center"),
                        "rect": curr_el.get("rect"),
                        "relative_rect": curr_el.get("relative_rect"),
                    },
                }
            )

        prev_text = {
            "window_text": prev_el.get("window_text") or "",
            "ocr_text": prev_el.get("ocr_text") or "",
        }
        curr_text = {
            "window_text": curr_el.get("window_text") or "",
            "ocr_text": curr_el.get("ocr_text") or "",
        }
        if prev_text != curr_text:
            text_changed.append(
                {
                    "label": curr_el.get("label") or curr_el.get("class") or "",
                    "class": curr_el.get("class") or "",
                    "from": prev_text,
                    "to": curr_text,
                }
            )

    return {
        "added": added,
        "removed": removed,
        "moved": moved,
        "text_changed": text_changed,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "moved": len(moved),
            "text_changed": len(text_changed),
        },
    }


def watch_ui_elements(
    window_title: str = "",
    include_text: bool = False,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    reset: bool = False,
    update_baseline: bool = True,
) -> dict:
    """Capture and diff UI state against a cached baseline for a window."""
    current = map_ui_elements(
        window_title=window_title,
        include_text=include_text,
        max_elements=max_elements,
        min_width=min_width,
        min_height=min_height,
    )
    key = _watch_key(window_title, include_text, max_elements, min_width, min_height)
    previous = None if reset else _UI_WATCH_BASELINES.get(key)

    if reset or previous is None:
        _UI_WATCH_BASELINES[key] = current
        return {
            "window_title": window_title or None,
            "baseline_created": True,
            "baseline_reset": bool(reset),
            "previous_count": 0 if previous is None else max(0, len(previous) - 1),
            "current_count": max(0, len(current) - 1),
            "diff": {"added": [], "removed": [], "moved": [], "text_changed": [], "summary": {"added": 0, "removed": 0, "moved": 0, "text_changed": 0}},
        }

    diff = diff_ui_maps(previous, current)
    if update_baseline:
        _UI_WATCH_BASELINES[key] = current
    return {
        "window_title": window_title or None,
        "baseline_created": False,
        "baseline_reset": False,
        "previous_count": max(0, len(previous) - 1),
        "current_count": max(0, len(current) - 1),
        "diff": diff,
    }


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


def _get_monitor_bbox(monitor: int) -> tuple[int, int, int, int] | None:
    """Get bounding box for a specific monitor (1-indexed). Returns None for all monitors."""
    if monitor <= 0:
        return None  # all monitors
    try:
        if HAS_WIN32:
            monitors = win32api.EnumDisplayMonitors()
            if monitor <= len(monitors):
                _hmon, _hdc, rect = monitors[monitor - 1]
                return (rect[0], rect[1], rect[2], rect[3])
            raise IndexError(f"Monitor {monitor} not found (have {len(monitors)})")
        else:
            raise RuntimeError("pywin32 needed for specific monitor selection")
    except Exception:
        raise


def take_screenshot(quality: int = 75, max_width: int = 0, monitor: int = 0) -> str:
    """Capture screen, return base64 JPEG. Resizes if wider than max_width.

    Args:
        quality: JPEG quality 1-100.
        max_width: Max width in pixels. 0=no resize (native resolution).
        monitor: 0=all monitors, 1/2/3=specific monitor.
    """
    if monitor == 0:
        img = ImageGrab.grab(all_screens=True)
    else:
        bbox = _get_monitor_bbox(monitor)
        img = ImageGrab.grab(bbox=bbox)
    # Resize if needed
    if max_width > 0 and img.width > max_width:
        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), resample=3)  # LANCZOS
    # Convert to JPEG
    if img.mode in ("RGBA", "LA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------


def focus_window(title: Optional[str] = None, handle: Optional[int] = None) -> str:
    """Bring a window to the foreground. Fuzzy-match title if provided."""
    if not HAS_WIN32:
        return "Error: pywin32 not installed — run `pip install pywin32`"

    hwnd = None
    if handle:
        hwnd = handle
    elif title:
        from thefuzz import fuzz

        best_score = 0
        for w in enumerate_windows():
            score = fuzz.partial_ratio(title.lower(), w.title.lower())
            if score > best_score:
                best_score = score
                hwnd = w.handle
        if best_score < 50:
            return f"No window matching '{title}' (best score {best_score})"

    if not hwnd:
        return "No window found"

    try:
        # Restore if minimized
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return f"Focused window handle={hwnd} title='{win32gui.GetWindowText(hwnd)}'"
    except Exception as e:
        return f"Failed to focus: {e}"


def minimize_all() -> str:
    """Win+D — show desktop."""
    try:
        pyautogui.hotkey("win", "d")
        return "Minimized all windows"
    except Exception as e:
        return f"Failed: {e}"


def launch_app(name: str, args: str = "") -> str:
    """Launch application via PowerShell Start-Process."""
    import subprocess

    try:
        cmd = f'Start-Process "{name}"'
        if args:
            cmd += f' -ArgumentList "{args}"'
        subprocess.run(["powershell", "-Command", cmd], timeout=10, capture_output=True)
        return f"Launched {name}"
    except Exception as e:
        return f"Failed to launch {name}: {e}"


def resize_window(handle: int, width: int, height: int) -> str:
    """Resize a window by handle."""
    if not HAS_WIN32:
        return "Error: pywin32 not installed — run `pip install pywin32`"
    try:
        rect = win32gui.GetWindowRect(handle)
        win32gui.MoveWindow(handle, rect[0], rect[1], width, height, True)
        return f"Resized {handle} to {width}x{height}"
    except Exception as e:
        return f"Failed: {e}"


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


def get_clipboard() -> str:
    if not HAS_WIN32:
        return "Error: pywin32 not installed — run `pip install pywin32`"
    try:
        win32clipboard.OpenClipboard()
        data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return data
    except Exception as e:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return f"Error: {e}"


def set_clipboard(text: str) -> str:
    if not HAS_WIN32:
        return "Error: pywin32 not installed — run `pip install pywin32`"
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return "Clipboard set"
    except Exception as e:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Lock screen
# ---------------------------------------------------------------------------


def lock_screen() -> str:
    try:
        ctypes.windll.user32.LockWorkStation()
        return "Screen locked"
    except Exception as e:
        return f"Failed: {e}"


# ---------------------------------------------------------------------------
# Toast notification
# ---------------------------------------------------------------------------


def show_notification(title: str, message: str) -> str:
    """Show a Windows toast notification via PowerShell."""
    import subprocess

    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = @"
<toast>
  <visual><binding template="ToastGeneric">
    <text>{title}</text>
    <text>{message}</text>
  </binding></visual>
</toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("winremote-mcp").Show($toast)
"""
    try:
        subprocess.run(["powershell", "-Command", ps], timeout=10, capture_output=True)
        return "Notification shown"
    except Exception as e:
        return f"Failed: {e}"
