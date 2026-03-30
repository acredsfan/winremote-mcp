"""Helpers for Roblox Studio playtest automation and log/harness access."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


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
