"""Win32 desktop interactions — screenshots, window enumeration, UI elements."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import io
import locale
import re
from dataclasses import dataclass
from typing import Any, Optional

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


def _safe_process_name(pid: int) -> str:
    """Best-effort process name lookup."""
    if not pid:
        return ""
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return ""


def _get_monitor_dpi_scale(monitor_handle: int) -> tuple[int, int, float, bool]:
    """Return DPI metadata for a monitor handle."""
    try:
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        result = ctypes.windll.shcore.GetDpiForMonitor(
            int(monitor_handle),
            0,
            ctypes.byref(dpi_x),
            ctypes.byref(dpi_y),
        )
        if result == 0:
            scale = round(dpi_x.value / 96.0, 4) if dpi_x.value else 1.0
            return dpi_x.value, dpi_y.value, scale, False
    except Exception:
        pass
    return 96, 96, 1.0, True


def get_monitor_info() -> list[dict]:
    """Return monitor metadata in virtual-screen coordinates."""
    if not HAS_WIN32:
        raise RuntimeError("pywin32 not installed — run `pip install pywin32`")

    monitors = []
    try:
        enum = list(win32api.EnumDisplayMonitors())
    except Exception as e:
        raise RuntimeError(f"Failed to enumerate monitors: {e}")

    for idx, (handle, _hdc, rect) in enumerate(enum, start=1):
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        is_primary = idx == 1
        try:
            info = win32api.GetMonitorInfo(handle)
            work = info.get("Work") or rect
            is_primary = bool(info.get("Flags", 0) & 1)
        except Exception:
            work = rect

        dpi_x, dpi_y, scale, fallback = _get_monitor_dpi_scale(handle)
        monitors.append(
            {
                "monitor_id": idx,
                "handle": int(handle),
                "primary": is_primary,
                "rect": {"left": left, "top": top, "right": right, "bottom": bottom},
                "work_rect": {"left": work[0], "top": work[1], "right": work[2], "bottom": work[3]},
                "size": {"width": width, "height": height},
                "dpi": {"x": dpi_x, "y": dpi_y},
                "scale": scale,
                "dpi_fallback": fallback,
            }
        )

    monitors.sort(key=lambda item: (not item["primary"], item["monitor_id"]))
    return monitors


def get_virtual_screen_bounds(monitors: list[dict] | None = None) -> dict:
    """Return the full virtual-screen bounds across all monitors."""
    monitors = monitors or get_monitor_info()
    if not monitors:
        raise RuntimeError("No monitors detected")
    left = min(m["rect"]["left"] for m in monitors)
    top = min(m["rect"]["top"] for m in monitors)
    right = max(m["rect"]["right"] for m in monitors)
    bottom = max(m["rect"]["bottom"] for m in monitors)
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
    }


def get_monitor_for_point(x: int, y: int, monitors: list[dict] | None = None) -> dict | None:
    """Return the monitor containing a point in virtual-screen coordinates."""
    monitors = monitors or get_monitor_info()
    for monitor in monitors:
        rect = monitor["rect"]
        if rect["left"] <= x < rect["right"] and rect["top"] <= y < rect["bottom"]:
            return monitor
    return None


def get_monitor_for_rect(rect: tuple[int, int, int, int], monitors: list[dict] | None = None) -> dict | None:
    """Return the monitor that contains the center point of a rect."""
    left, top, right, bottom = rect
    return get_monitor_for_point((left + right) // 2, (top + bottom) // 2, monitors=monitors)


def normalize_region(
    left: int,
    top: int,
    right: int,
    bottom: int,
    monitors: list[dict] | None = None,
) -> tuple[int, int, int, int]:
    """Order and clamp a region to the virtual-screen bounds."""
    monitors = monitors or get_monitor_info()
    virtual = get_virtual_screen_bounds(monitors)

    if left > right:
        left, right = right, left
    if top > bottom:
        top, bottom = bottom, top

    left = max(left, virtual["left"])
    top = max(top, virtual["top"])
    right = min(right, virtual["right"])
    bottom = min(bottom, virtual["bottom"])

    if left >= right or top >= bottom:
        raise ValueError(
            "Requested region is outside the virtual screen bounds "
            f"({virtual['left']},{virtual['top']},{virtual['right']},{virtual['bottom']})"
        )
    return left, top, right, bottom


def validate_screen_point(x: int, y: int, monitors: list[dict] | None = None) -> dict:
    """Validate a point against the virtual screen and return monitor info."""
    monitors = monitors or get_monitor_info()
    monitor = get_monitor_for_point(x, y, monitors=monitors)
    if monitor is None:
        virtual = get_virtual_screen_bounds(monitors)
        raise ValueError(
            f"Point ({x},{y}) is outside the virtual screen bounds "
            f"({virtual['left']},{virtual['top']},{virtual['right']},{virtual['bottom']})"
        )
    return monitor


def capture_image(
    *,
    monitor: int = 0,
    left: int | None = None,
    top: int | None = None,
    right: int | None = None,
    bottom: int | None = None,
) -> tuple[Any, dict]:
    """Capture an image and return both the PIL image and capture metadata."""
    monitors = get_monitor_info() if HAS_WIN32 else []

    if all(v is not None for v in (left, top, right, bottom)):
        assert left is not None and top is not None and right is not None and bottom is not None
        bbox = normalize_region(left, top, right, bottom, monitors=monitors)
        img = ImageGrab.grab(bbox=bbox)
        bounds = {"left": bbox[0], "top": bbox[1], "right": bbox[2], "bottom": bbox[3]}
        captured_monitors = [
            m["monitor_id"]
            for m in monitors
            if not (
                m["rect"]["right"] <= bbox[0]
                or m["rect"]["left"] >= bbox[2]
                or m["rect"]["bottom"] <= bbox[1]
                or m["rect"]["top"] >= bbox[3]
            )
        ]
    elif monitor > 0:
        bbox = _get_monitor_bbox(monitor)
        if bbox is None:
            raise ValueError(f"Monitor {monitor} not found")
        img = ImageGrab.grab(bbox=bbox)
        bounds = {"left": bbox[0], "top": bbox[1], "right": bbox[2], "bottom": bbox[3]}
        captured_monitors = [monitor]
    else:
        img = ImageGrab.grab(all_screens=True)
        virtual = get_virtual_screen_bounds(monitors) if monitors else {"left": 0, "top": 0, "right": img.width, "bottom": img.height, "width": img.width, "height": img.height}
        bounds = {"left": virtual["left"], "top": virtual["top"], "right": virtual["right"], "bottom": virtual["bottom"]}
        captured_monitors = [m["monitor_id"] for m in monitors] if monitors else []

    metadata = {
        "bounds": {**bounds, "width": bounds["right"] - bounds["left"], "height": bounds["bottom"] - bounds["top"]},
        "captured_monitors": captured_monitors,
        "monitors": monitors,
        "virtual_screen": get_virtual_screen_bounds(monitors) if monitors else {"left": bounds["left"], "top": bounds["top"], "right": bounds["right"], "bottom": bounds["bottom"], "width": bounds["right"] - bounds["left"], "height": bounds["bottom"] - bounds["top"]},
    }
    return img, metadata


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
    process_name: str = ""
    monitor_id: int = 0

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
    monitors = get_monitor_info()

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
        monitor = get_monitor_for_rect(rect, monitors=monitors)
        results.append(
            WindowInfo(
                handle=hwnd,
                title=title,
                rect=rect,
                visible=True,
                pid=pid,
                process_name=_safe_process_name(pid),
                monitor_id=(monitor or {}).get("monitor_id", 0),
            )
        )
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
    monitors = get_monitor_info()
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
        monitor = get_monitor_for_rect(rect, monitors=monitors)
        idx[0] += 1
        elements.append(
            {
                "index": idx[0],
                "class": cls,
                "text": text,
                "rect": {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]},
                "monitor_id": (monitor or {}).get("monitor_id", 0),
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

    monitors = get_monitor_info()
    virtual_screen = get_virtual_screen_bounds(monitors)

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
            try:
                _, pid = win32process.GetWindowThreadProcessId(target_hwnd)
            except Exception:
                pid = 0
            monitor = get_monitor_for_rect(rect, monitors=monitors)
            target_window = WindowInfo(
                handle=target_hwnd,
                title=win32gui.GetWindowText(target_hwnd),
                rect=rect,
                visible=True,
                pid=pid,
                process_name=_safe_process_name(pid),
                monitor_id=(monitor or {}).get("monitor_id", 0),
            )

    if not target_hwnd or target_window is None:
        raise RuntimeError("No active window found")

    left, top, right, bottom = target_window.rect
    target_monitor = get_monitor_for_rect(target_window.rect, monitors=monitors)
    results: list[dict] = [
        {
            "index": 0,
            "type": "window",
            "class": "Window",
            "handle": target_hwnd,
            "element_id": _make_element_id(target_hwnd, 0, "Window", target_window.title),
            "parent_handle": 0,
            "label": target_window.title,
            "window_text": target_window.title,
            "rect": {"left": left, "top": top, "right": right, "bottom": bottom},
            "relative_rect": {"left": 0, "top": 0, "right": right - left, "bottom": bottom - top},
            "size": {"width": right - left, "height": bottom - top},
            "center": {"x": (left + right) // 2, "y": (top + bottom) // 2},
            "relative_center": {"x": (right - left) // 2, "y": (bottom - top) // 2},
            "monitor_id": (target_monitor or {}).get("monitor_id", 0),
            "monitor": target_monitor,
            "on_primary_monitor": bool((target_monitor or {}).get("primary", False)),
            "pid": target_window.pid,
            "process_name": target_window.process_name,
            "virtual_screen": virtual_screen,
            "coordinate_hint": "Use center for absolute screen clicks, relative_center for window-relative automation.",
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
        parent_handle = 0
        try:
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            pass
        try:
            text = win32gui.GetWindowText(hwnd)
        except Exception:
            pass
        try:
            parent_handle = win32gui.GetParent(hwnd) or 0
        except Exception:
            parent_handle = 0
        monitor = get_monitor_for_rect(r, monitors=monitors)

        label = text or cls or f"element-{idx}"
        item = {
            "index": idx,
            "type": "control",
            "handle": hwnd,
            "element_id": _make_element_id(hwnd, parent_handle, cls, label),
            "parent_handle": parent_handle,
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
            "monitor_id": (monitor or {}).get("monitor_id", 0),
            "monitor": monitor,
            "on_primary_monitor": bool((monitor or {}).get("primary", False)),
            "coordinate_hint": "Use center for absolute screen clicks, relative_center when anchoring to the mapped window.",
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
        "type": str(element.get("type") or ""),
        "label": str(element.get("label") or ""),
        "window_text": str(element.get("window_text") or ""),
        "class": str(element.get("class") or ""),
        "process_name": str(element.get("process_name") or ""),
        "ocr_text": str(element.get("ocr_text") or ""),
    }


def _search_preview_item(element: dict) -> dict:
    """Return a compact preview for search diagnostics."""
    return {
        "index": element.get("index"),
        "type": element.get("type"),
        "label": element.get("label") or "",
        "class": element.get("class") or "",
        "window_text": element.get("window_text") or "",
        "monitor_id": element.get("monitor_id", 0),
        "center": element.get("center"),
        "relative_center": element.get("relative_center"),
        "element_id": element.get("element_id") or "",
    }


def summarize_ui_map(mapped: list[dict], preview_limit: int = 12) -> dict:
    """Build a compact, automation-friendly summary for a UI map."""
    window = mapped[0] if mapped else None
    controls = mapped[1:] if len(mapped) > 1 else []

    class_counts: dict[str, int] = {}
    monitor_counts: dict[str, int] = {}
    preview: list[dict] = []
    seen_preview_keys: set[tuple[str, str, str]] = set()
    window_text_controls = 0
    ocr_text_controls = 0
    custom_rendered_controls = 0

    custom_rendered_classes = {
        "Chrome Legacy Window",
        "Intermediate D3D Window",
        "Windows.UI.Composition.DesktopWindowContentBridge",
    }

    for control in controls:
        class_name = str(control.get("class") or "(unknown)")
        class_counts[class_name] = class_counts.get(class_name, 0) + 1

        monitor_key = str(control.get("monitor_id") or 0)
        monitor_counts[monitor_key] = monitor_counts.get(monitor_key, 0) + 1

        if control.get("window_text"):
            window_text_controls += 1
        if control.get("ocr_text"):
            ocr_text_controls += 1
        if class_name in custom_rendered_classes:
            custom_rendered_controls += 1

        preview_key = (
            str(control.get("label") or ""),
            class_name,
            str(control.get("type") or ""),
        )
        if preview_key not in seen_preview_keys and len(preview) < preview_limit:
            preview.append(_search_preview_item(control))
            seen_preview_keys.add(preview_key)

    notes: list[str] = []
    if not controls:
        notes.append(
            "No child controls were exposed by Win32 for this window. Try OCR, AnnotatedSnapshot, or a broader Snapshot to target custom-drawn UI."
        )
    elif len(controls) <= 2:
        notes.append(
            f"Only {len(controls)} child controls were exposed by Win32 for this window, so custom-rendered UI may require OCR or image-based targeting."
        )
    if controls and custom_rendered_controls == len(controls):
        notes.append(
            "All exposed controls appear to be compositor or graphics surfaces rather than semantic widgets. Prefer OCR or AnnotatedSnapshot for precise click targets."
        )

    return {
        "window_title": (window or {}).get("label") if window else None,
        "window_monitor_id": (window or {}).get("monitor_id", 0) if window else 0,
        "control_count": len(controls),
        "class_counts": dict(sorted(class_counts.items(), key=lambda item: (-item[1], item[0]))),
        "monitor_counts": dict(sorted(monitor_counts.items(), key=lambda item: int(item[0]))),
        "text_observability": {
            "window_text_controls": window_text_controls,
            "ocr_text_controls": ocr_text_controls,
            "controls_without_text": max(0, len(controls) - window_text_controls - ocr_text_controls),
        },
        "searchable_preview": preview,
        "notes": notes,
    }


def _build_search_diagnostics(mapped: list[dict], query: str, matches: list[dict], preview_limit: int = 12) -> dict:
    """Build search diagnostics and next-step hints for UIFind/UIClick."""
    candidates = mapped if mapped else []
    summary = summarize_ui_map(mapped, preview_limit=preview_limit)
    recommendations: list[str] = []

    if not matches:
        recommendations.append(
            f"No exact UI match was found for '{query}'. Review summary.searchable_preview for nearby labels/classes to refine the query."
        )
        if summary["notes"]:
            recommendations.extend(summary["notes"])

    return {
        "searched_element_count": len(candidates),
        "searchable_preview": [
            _search_preview_item(element)
            for element in candidates[:preview_limit]
        ],
        "summary": summary,
        "recommendations": recommendations,
    }


def _make_element_id(handle: int, parent_handle: int, class_name: str, label: str) -> str:
    """Build a stable-ish element identifier."""
    raw = f"{handle}:{parent_handle}:{class_name}:{_normalize_search_text(label)}"
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


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
    return find_ui_elements_with_context(
        query=query,
        window_title=window_title,
        include_text=include_text,
        max_results=max_results,
        match_mode=match_mode,
        max_elements=max_elements,
        min_width=min_width,
        min_height=min_height,
    )["matches"]


def find_ui_elements_with_context(
    query: str,
    window_title: str = "",
    include_text: bool = False,
    max_results: int = 5,
    match_mode: str = "auto",
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
) -> dict:
    """Find UI matches and include diagnostics about the search space."""
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
    elements = mapped
    q_norm = _normalize_search_text(query)
    q_tokens = set(q_norm.split())
    try:
        from thefuzz import fuzz
    except Exception:
        fuzz = None

    pattern = None
    if match_mode == "regex":
        pattern = re.compile(query, re.IGNORECASE)

    match_priority = {"": 0, "fuzzy": 1, "tokens": 2, "contains": 3, "regex": 4, "exact": 5}

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
                    if fuzzy_score > max(60, score):
                        score = fuzzy_score
                        match_type = "fuzzy"

            if score > best_score or (
                score == best_score and match_priority.get(match_type, 0) > match_priority.get(best_type, 0)
            ):
                best_score = score
                best_type = match_type
                best_field = field_name
                best_value = raw_value

        if best_score > 0:
            item = dict(el)
            confidence = "high" if best_score >= 90 else "medium" if best_score >= 75 else "low"
            reason = f"{best_type or 'matched'} on {best_field}" if best_field else "matched"
            item["match"] = {
                "query": query,
                "normalized_query": q_norm,
                "score": best_score,
                "type": best_type,
                "field": best_field,
                "value": best_value,
                "confidence": confidence,
                "reason": reason,
            }
            matches.append(item)

    matches.sort(key=lambda item: (-item["match"]["score"], item.get("index", 0)))
    limited_matches = matches[:max_results]
    diagnostics = _build_search_diagnostics(mapped, query, limited_matches)
    return {
        "window": mapped[0] if mapped else None,
        "mapped": mapped,
        "matches": limited_matches,
        "searched_element_count": diagnostics["searched_element_count"],
        "searchable_preview": diagnostics["searchable_preview"],
        "summary": diagnostics["summary"],
        "recommendations": diagnostics["recommendations"],
    }


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
    element_id = str(element.get("element_id") or "")
    if element_id:
        return (element_id, _normalize_search_text(element.get("class") or ""))
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
                    "index": curr_el.get("index"),
                    "element_id": curr_el.get("element_id"),
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
                    "index": curr_el.get("index"),
                    "element_id": curr_el.get("element_id"),
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
    img, _metadata = capture_image(monitor=monitor)
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
