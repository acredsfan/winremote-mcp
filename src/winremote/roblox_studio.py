"""Helpers for Roblox Studio playtest automation and log/harness access."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from winremote import ocr


_STUDIO_INSPECTION_CACHE: dict[str, dict[str, Any]] = {}
_STUDIO_INSPECTION_CACHE_TTL_SECONDS = 1.0

_STUDIO_TAB_REGIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("home_tab", "Home", ("home",)),
    ("model_tab", "Model", ("model",)),
    ("test_tab", "Test", ("test",)),
    ("view_tab", "View", ("view",)),
    ("plugins_tab", "Plugins", ("plugins", "plugin")),
)

_STUDIO_OCR_REGIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("left_panel", "Toolbox", ("toolbox", "inventory", "models", "creator store", "marketplace", "asset manager")),
    ("right_panel_top", "Explorer", ("explorer",)),
    ("right_panel_bottom", "Properties", ("properties", "property")),
    ("bottom_panel", "Output", ("output", "console")),
)

_STUDIO_PANEL_TOGGLE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Toolbox", ("toolbox", "tool box", "inventory", "models", "creator store", "marketplace")),
    ("Explorer", ("explorer",)),
    ("Properties", ("properties", "property")),
    ("Output", ("output", "console")),
)


def _normalize_text(value: Any) -> str:
    """Normalize free-form OCR/UI text for matching."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def is_studio_window(window: dict[str, Any] | None = None, *, title: str = "", process_name: str = "") -> bool:
    """Return whether a window payload looks like Roblox Studio."""
    title_value = _normalize_text((window or {}).get("label") or (window or {}).get("title") or title)
    process_value = _normalize_text((window or {}).get("process_name") or process_name)
    return (
        "roblox studio" in title_value
        or "robloxstudio" in title_value.replace(" ", "")
        or "robloxstudio" in process_value.replace(" ", "")
    )


def normalize_studio_tab_name(name: str) -> str:
    """Return the canonical Roblox Studio ribbon tab label."""
    normalized = _normalize_text(name)
    if not normalized:
        raise ValueError("tab_name is required")

    alias_map: dict[str, str] = {}
    for _region_id, label, aliases in _STUDIO_TAB_REGIONS:
        alias_map[_normalize_text(label)] = label
        for alias in aliases:
            alias_map[_normalize_text(alias)] = label

    hit = alias_map.get(normalized)
    if hit is None:
        allowed = ", ".join(label for _region_id, label, _aliases in _STUDIO_TAB_REGIONS)
        raise ValueError(f"Unsupported Roblox Studio tab '{name}'. Supported tabs: {allowed}")
    return hit


def normalize_studio_panel_name(name: str) -> str:
    """Return the canonical Roblox Studio editor panel label."""
    normalized = _normalize_text(name)
    if not normalized:
        raise ValueError("panel_name is required")

    alias_map: dict[str, str] = {}
    for label, aliases in _STUDIO_PANEL_TOGGLE_ALIASES:
        alias_map[_normalize_text(label)] = label
        for alias in aliases:
            alias_map[_normalize_text(alias)] = label

    hit = alias_map.get(normalized)
    if hit is None:
        allowed = ", ".join(label for label, _aliases in _STUDIO_PANEL_TOGGLE_ALIASES)
        raise ValueError(f"Unsupported Roblox Studio panel '{name}'. Supported panels: {allowed}")
    return hit


def _studio_rect(window: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    """Return window geometry as left/top/right/bottom/width/height."""
    rect = (window.get("rect") or {}) if isinstance(window, dict) else {}
    left = int(rect.get("left", 0))
    top = int(rect.get("top", 0))
    right = int(rect.get("right", left))
    bottom = int(rect.get("bottom", top))
    width = max(1, right - left)
    height = max(1, bottom - top)
    return left, top, right, bottom, width, height


def _studio_cache_key(window: dict[str, Any]) -> str:
    """Build a stable cache key for Studio UI inspection."""
    left, top, right, bottom, _width, _height = _studio_rect(window)
    title = _normalize_text(window.get("label") or window.get("title") or "roblox studio")
    monitor_id = int(window.get("monitor_id", 0) or 0)
    return "|".join([title or "roblox studio", str(monitor_id), str(left), str(top), str(right), str(bottom)])


def invalidate_studio_inspection_cache(window_title: str = "") -> int:
    """Invalidate cached Studio OCR/heuristic inspections."""
    if not window_title:
        cleared = len(_STUDIO_INSPECTION_CACHE)
        _STUDIO_INSPECTION_CACHE.clear()
        return cleared

    needle = _normalize_text(window_title)
    keys_to_remove = [key for key in list(_STUDIO_INSPECTION_CACHE) if key.split("|", 1)[0] == needle]
    for key in keys_to_remove:
        _STUDIO_INSPECTION_CACHE.pop(key, None)
    return len(keys_to_remove)


def _rect_payload(left: int, top: int, right: int, bottom: int) -> dict[str, int]:
    """Return a JSON-friendly rect payload."""
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _make_studio_candidate(
    *,
    window: dict[str, Any],
    region_id: str,
    label: str,
    aliases: tuple[str, ...],
    left: int,
    top: int,
    right: int,
    bottom: int,
    class_name: str,
    source: str,
    ocr_text_value: str = "",
    priority: int = 0,
    index: int = 0,
) -> dict[str, Any]:
    """Build a synthetic UI candidate for Studio chrome."""
    window_left, window_top, _window_right, _window_bottom, _width, _height = _studio_rect(window)
    rect = _rect_payload(left, top, right, bottom)
    relative_rect = _rect_payload(left - window_left, top - window_top, right - window_left, bottom - window_top)
    raw_id = "|".join([
        _studio_cache_key(window),
        region_id,
        class_name,
        _normalize_text(label),
    ])
    candidate = {
        "index": index,
        "type": "control",
        "class": class_name,
        "label": label,
        "window_text": label,
        "rect": rect,
        "relative_rect": relative_rect,
        "size": {"width": max(1, right - left), "height": max(1, bottom - top)},
        "center": {"x": (left + right) // 2, "y": (top + bottom) // 2},
        "relative_center": {"x": ((left + right) // 2) - window_left, "y": ((top + bottom) // 2) - window_top},
        "element_id": hashlib.sha1(raw_id.encode("utf-8", errors="ignore")).hexdigest()[:16],
        "monitor_id": int(window.get("monitor_id", 0) or 0),
        "process_name": window.get("process_name") or "",
        "source": source,
        "region_id": region_id,
        "aliases": list(aliases),
        "priority": priority,
    }
    cleaned_ocr = re.sub(r"\s+", " ", ocr_text_value).strip()
    if cleaned_ocr:
        candidate["ocr_text"] = cleaned_ocr[:500]
    return candidate


def _build_studio_tab_candidates(window: dict[str, Any]) -> list[dict[str, Any]]:
    """Return narrow heuristic regions for the main Studio ribbon tabs."""
    left, top, right, _bottom, width, height = _studio_rect(window)
    margin = max(8, min(width, height) // 120)
    tab_top = top + margin
    tab_height = max(24, min(40, int(height * 0.045)))
    tab_width = max(68, min(120, int(width * 0.075)))
    tab_gap = max(6, min(20, int(width * 0.01)))
    tab_start = left + max(52, int(width * 0.06))

    candidates: list[dict[str, Any]] = []
    for offset, (region_id, label, aliases) in enumerate(_STUDIO_TAB_REGIONS):
        region_left = tab_start + offset * (tab_width + tab_gap)
        region_right = min(region_left + tab_width, right - margin)
        if region_right <= region_left:
            continue
        candidates.append(
            _make_studio_candidate(
                window=window,
                region_id=region_id,
                label=label,
                aliases=aliases,
                left=region_left,
                top=tab_top,
                right=region_right,
                bottom=tab_top + tab_height,
                class_name="RobloxStudioHeuristicRegion",
                source="roblox_ocr_fallback",
                priority=20 - offset,
                index=1000 + offset,
            )
        )
    return candidates


def _build_studio_ocr_candidates(window: dict[str, Any]) -> list[dict[str, Any]]:
    """OCR strategic Studio dock regions that are useful for editor automation."""
    left, top, right, bottom, width, height = _studio_rect(window)
    margin = max(8, min(width, height) // 120)
    chrome_top = min(bottom - margin, top + max(92, min(140, int(height * 0.12))))
    bottom_height = max(120, min(260, int(height * 0.22)))
    bottom_top = max(chrome_top + 60, bottom - bottom_height)
    left_width = max(180, min(360, int(width * 0.24)))
    right_width = max(220, min(430, int(width * 0.26)))
    right_left = max(left + margin, right - right_width)
    content_bottom = max(chrome_top + 80, bottom_top - margin)
    right_mid = chrome_top + max(130, int((content_bottom - chrome_top) * 0.46))

    planned_regions = (
        (
            "left_panel",
            "Toolbox",
            _STUDIO_OCR_REGIONS[0][2],
            left + margin,
            chrome_top,
            min(left + left_width, right - margin),
            content_bottom,
            40,
            2000,
        ),
        (
            "right_panel_top",
            "Explorer",
            _STUDIO_OCR_REGIONS[1][2],
            right_left,
            chrome_top,
            right - margin,
            min(right_mid, content_bottom),
            30,
            2001,
        ),
        (
            "right_panel_bottom",
            "Properties",
            _STUDIO_OCR_REGIONS[2][2],
            right_left,
            min(right_mid, content_bottom),
            right - margin,
            content_bottom,
            25,
            2002,
        ),
        (
            "bottom_panel",
            "Output",
            _STUDIO_OCR_REGIONS[3][2],
            left + int(width * 0.18),
            bottom_top,
            right - int(width * 0.18),
            bottom - margin,
            15,
            2003,
        ),
    )

    candidates: list[dict[str, Any]] = []
    for region_id, label, aliases, region_left, region_top, region_right, region_bottom, priority, index in planned_regions:
        if region_right - region_left < 32 or region_bottom - region_top < 24:
            continue
        region_text = ocr.run_ocr(left=region_left, top=region_top, right=region_right, bottom=region_bottom)
        candidates.append(
            _make_studio_candidate(
                window=window,
                region_id=region_id,
                label=label,
                aliases=aliases,
                left=region_left,
                top=region_top,
                right=region_right,
                bottom=region_bottom,
                class_name="RobloxStudioOCRRegion",
                source="roblox_ocr_fallback",
                ocr_text_value=region_text,
                priority=priority,
                index=index,
            )
        )
    return candidates


def _build_studio_ribbon_command_candidates(window: dict[str, Any]) -> list[dict[str, Any]]:
    """OCR ribbon command slots below the main tab strip.

    These slots are broader than semantic controls, but they provide cheap, window-relative
    targets for common Studio editor commands like Explorer, Properties, Toolbox, and Output.
    """
    left, top, right, _bottom, width, height = _studio_rect(window)
    margin = max(8, min(width, height) // 120)
    ribbon_top = top + max(54, min(86, int(height * 0.07)))
    ribbon_bottom = top + max(134, min(210, int(height * 0.21)))
    ribbon_left = left + max(120, int(width * 0.15))
    ribbon_right = right - max(120, int(width * 0.1))
    if ribbon_right - ribbon_left < 120 or ribbon_bottom - ribbon_top < 32:
        return []

    slot_count = max(6, min(10, int(width / 170)))
    slot_width = max(60, int((ribbon_right - ribbon_left) / slot_count))

    candidates: list[dict[str, Any]] = []
    for index in range(slot_count):
        slot_left = ribbon_left + index * slot_width
        slot_right = ribbon_right if index == slot_count - 1 else min(ribbon_right, slot_left + slot_width)
        if slot_right - slot_left < 36:
            continue
        region_text = ocr.run_ocr(left=slot_left, top=ribbon_top, right=slot_right, bottom=ribbon_bottom)
        candidates.append(
            _make_studio_candidate(
                window=window,
                region_id=f"ribbon_slot_{index + 1}",
                label=f"Ribbon Slot {index + 1}",
                aliases=(),
                left=slot_left,
                top=ribbon_top,
                right=slot_right,
                bottom=ribbon_bottom,
                class_name="RobloxStudioRibbonOCRRegion",
                source="roblox_ocr_fallback",
                ocr_text_value=region_text,
                priority=12,
                index=3000 + index,
            )
        )
    return candidates


def _score_studio_candidate(candidate: dict[str, Any], query: str) -> dict[str, Any] | None:
    """Score a Studio fallback candidate against a search query."""
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return None
    query_tokens = set(normalized_query.split())

    fields: list[tuple[str, str, str]] = []
    label = str(candidate.get("label") or "")
    region_id = str(candidate.get("region_id") or "")
    ocr_text_value = str(candidate.get("ocr_text") or "")
    fields.append(("label", _normalize_text(label), label))
    fields.append(("region_id", _normalize_text(region_id), region_id))
    for alias in candidate.get("aliases") or []:
        alias_text = str(alias or "")
        fields.append(("alias", _normalize_text(alias_text), alias_text))
    if ocr_text_value:
        fields.append(("ocr_text", _normalize_text(ocr_text_value), ocr_text_value))

    best_score = 0
    best_type = ""
    best_field = ""
    best_value = ""
    for field_name, normalized_value, raw_value in fields:
        if not normalized_value:
            continue
        score = 0
        match_type = ""
        if normalized_value == normalized_query:
            score = 96 if field_name == "ocr_text" else 92
            match_type = "exact"
        elif normalized_query in normalized_value:
            score = 94 if field_name == "ocr_text" else 88
            match_type = "contains"
        elif query_tokens:
            overlap = len(query_tokens & set(normalized_value.split()))
            if overlap:
                score = min(84, 60 + overlap * 12)
                match_type = "tokens"

        if score > best_score:
            best_score = score
            best_type = match_type
            best_field = field_name
            best_value = raw_value

    if best_score <= 0:
        return None

    item = dict(candidate)
    confidence = "high" if best_score >= 90 else "medium" if best_score >= 75 else "low"
    item["match"] = {
        "query": query,
        "normalized_query": normalized_query,
        "score": best_score,
        "type": best_type,
        "field": best_field,
        "value": best_value,
        "confidence": confidence,
        "reason": f"{best_type or 'matched'} on {best_field}" if best_field else "matched",
    }
    return item


def _studio_searchable_preview(candidates: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Return compact preview items from Studio fallback candidates."""
    preview: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (str(candidate.get("label") or ""), str(candidate.get("region_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        preview.append(
            {
                "index": candidate.get("index"),
                "type": candidate.get("type"),
                "label": candidate.get("label") or "",
                "class": candidate.get("class") or "",
                "window_text": candidate.get("window_text") or "",
                "monitor_id": candidate.get("monitor_id", 0),
                "center": candidate.get("center"),
                "relative_center": candidate.get("relative_center"),
                "element_id": candidate.get("element_id") or "",
                "source": candidate.get("source") or "",
                "ocr_text": str(candidate.get("ocr_text") or "")[:120],
            }
        )
        if len(preview) >= limit:
            break
    return preview


def inspect_studio_ui_regions(
    window: dict[str, Any],
    *,
    query: str = "",
    max_results: int = 5,
    use_cache: bool = True,
    include_ribbon: bool = False,
) -> dict[str, Any]:
    """Inspect strategic Roblox Studio chrome regions for text-first UI fallback.

    Returns a compact set of synthetic elements that semantic tools can use when
    Win32 control enumeration exposes only compositor surfaces.
    """
    if not is_studio_window(window):
        return {
            "inspection_mode": "roblox_ocr_fallback",
            "window_title": (window or {}).get("label") or (window or {}).get("title"),
            "regions": [],
            "tabs": [],
            "panels": [],
            "ribbon_regions": [],
            "matches": [],
            "searchable_preview": [],
            "notes": ["Roblox Studio fallback skipped because the target window did not look like Studio."],
            "searched_region_count": 0,
        }

    cache_key = _studio_cache_key(window)
    cached = _STUDIO_INSPECTION_CACHE.get(cache_key)
    cache_is_fresh = use_cache and cached is not None and (time.monotonic() - cached.get("created_at", 0.0)) <= _STUDIO_INSPECTION_CACHE_TTL_SECONDS
    if cache_is_fresh and cached is not None:
        tab_candidates = list(cached.get("tabs", []))
        ocr_candidates = list(cached.get("ocr_regions", []))
        ribbon_candidates = list(cached.get("ribbon_regions", [])) if include_ribbon else []
        if include_ribbon and not ribbon_candidates:
            ribbon_candidates = _build_studio_ribbon_command_candidates(window)
            cached["ribbon_regions"] = list(ribbon_candidates)
            cached["created_at"] = time.monotonic()
    else:
        tab_candidates = _build_studio_tab_candidates(window)
        ocr_candidates = _build_studio_ocr_candidates(window)
        ribbon_candidates = _build_studio_ribbon_command_candidates(window) if include_ribbon else []
        _STUDIO_INSPECTION_CACHE[cache_key] = {
            "created_at": time.monotonic(),
            "tabs": list(tab_candidates),
            "ocr_regions": list(ocr_candidates),
            "ribbon_regions": list(ribbon_candidates),
        }

    non_empty_ocr_regions = [candidate for candidate in ocr_candidates if candidate.get("ocr_text")]
    non_empty_ribbon_regions = [candidate for candidate in ribbon_candidates if candidate.get("ocr_text")]
    all_candidates = [*tab_candidates, *ocr_candidates, *ribbon_candidates]
    preview_candidates = [*tab_candidates, *non_empty_ocr_regions, *non_empty_ribbon_regions]

    matches: list[dict[str, Any]] = []
    if query:
        for candidate in all_candidates:
            matched = _score_studio_candidate(candidate, query)
            if matched is not None:
                matches.append(matched)
        matches.sort(
            key=lambda item: (
                -(item.get("match") or {}).get("score", 0),
                -int(item.get("priority", 0) or 0),
                int(item.get("index", 0) or 0),
            )
        )
        matches = matches[: max(1, int(max_results))]

    notes = [
        "Roblox Studio OCR fallback inspects the ribbon tabs and major dock regions before escalating to screenshots.",
    ]
    if include_ribbon:
        notes.append("Ribbon command slots were OCR-scanned so editor-only commands can be targeted without a full screenshot.")
    if non_empty_ocr_regions:
        notes.append("Fallback detected readable Studio chrome text. Reuse these regions with UIFind/UIAct before asking the user to intervene.")
    else:
        notes.append("Fallback did not detect readable dock text; heuristic ribbon-tab targets are still available for Home, Model, Test, View, and Plugins.")

    return {
        "inspection_mode": "roblox_ocr_fallback",
        "window_title": window.get("label") or window.get("title"),
        "regions": non_empty_ocr_regions,
        "tabs": tab_candidates,
        "panels": non_empty_ocr_regions,
        "ribbon_regions": non_empty_ribbon_regions,
        "matches": matches,
        "searchable_preview": _studio_searchable_preview(preview_candidates),
        "notes": notes,
        "searched_region_count": len(all_candidates),
    }


def candidate_log_directories() -> list[Path]:
    """Return candidate Roblox log directories for Windows installs."""
    paths: list[Path] = []
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        paths.append(Path(local_appdata) / "Roblox" / "logs")
        paths.append(
            Path(local_appdata)
            / "Packages"
            / "ROBLOXCorporation.ROBLOX_55nm5eh3cm0pr"
            / "LocalState"
            / "logs"
        )
    return paths


def find_latest_studio_log() -> Path | None:
    """Return the newest Roblox Studio log file if one exists."""
    newest: tuple[float, Path] | None = None
    for directory in candidate_log_directories():
        if not directory.exists():
            continue
        for path in directory.glob("*Studio*"):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, path)
    return newest[1] if newest else None


def tail_file(path: str | Path, *, lines: int = 100, encoding: str = "utf-8", contains: str = "") -> dict[str, Any]:
    """Tail a text file and optionally filter lines."""
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    if not p.is_file():
        raise ValueError(f"Not a file: {p}")

    raw = p.read_text(encoding=encoding, errors="replace").splitlines()
    if contains:
        needle = contains.lower()
        raw = [line for line in raw if needle in line.lower()]

    tail_lines = raw[-max(1, int(lines)) :]
    return {
        "path": str(p),
        "line_count": len(tail_lines),
        "total_matching_lines": len(raw),
        "lines": tail_lines,
        "text": "\n".join(tail_lines),
    }


def read_latest_studio_log(*, lines: int = 200, contains: str = "") -> dict[str, Any]:
    """Tail the latest Roblox Studio log file."""
    latest = find_latest_studio_log()
    if latest is None:
        raise FileNotFoundError("No Roblox Studio log file found")
    payload = tail_file(latest, lines=lines, contains=contains)
    payload["latest_studio_log"] = True
    return payload


def read_latest_studio_errors(*, lines: int = 200) -> dict[str, Any]:
    """Return likely error/warning lines from the latest Studio log."""
    latest = find_latest_studio_log()
    if latest is None:
        raise FileNotFoundError("No Roblox Studio log file found")

    content = latest.read_text(encoding="utf-8", errors="replace").splitlines()
    needles = ("error", "warn", "exception", "fail")
    filtered = [line for line in content if any(needle in line.lower() for needle in needles)]
    tail_lines = filtered[-max(1, int(lines)) :]
    return {
        "path": str(latest),
        "line_count": len(tail_lines),
        "total_matching_lines": len(filtered),
        "lines": tail_lines,
        "text": "\n".join(tail_lines),
    }


def _default_harness_url() -> str:
    return os.environ.get("WINREMOTE_ROBLOX_STUDIO_HARNESS_URL", "http://127.0.0.1:51234")


def harness_request(
    method: str,
    route: str,
    *,
    payload: dict[str, Any] | None = None,
    harness_url: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Call a local Roblox Studio test harness endpoint."""
    base = (harness_url or _default_harness_url()).rstrip("/")
    path = route if route.startswith("/") else f"/{route}"
    url = f"{base}{path}"
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                data = json.loads(raw or "{}")
            else:
                data = {"raw": raw}
            return {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "url": url,
                "data": data,
            }
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": e.code,
            "url": url,
            "error": raw or str(e),
        }
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "error": str(e),
        }
