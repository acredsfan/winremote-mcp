"""Session note persistence helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _sessions_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "WinRemoteMCP" / "sessions"
    return Path.home() / ".local" / "share" / "WinRemoteMCP" / "sessions"


def _session_dir(session_id: str, *, root_dir: Path | None = None) -> Path:
    sid = (session_id or "default").strip() or "default"
    root = root_dir or _sessions_root()
    path = root / sid
    path.mkdir(parents=True, exist_ok=True)
    return path


def _notes_path(session_id: str, *, root_dir: Path | None = None) -> Path:
    return _session_dir(session_id, root_dir=root_dir) / "notes.jsonl"


def _parse_tags(tags: list[str] | None = None) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        value = str(tag or "").strip().lower()
        if value and value not in out:
            out.append(value)
    return out


def add_session_note(note: str, *, tags: list[str] | None = None, session_id: str = "default", root_dir: Path | None = None) -> dict[str, Any]:
    text = str(note or "").strip()
    if not text:
        raise ValueError("note is required")
    payload = {
        "timestamp": _utc_now_iso(),
        "note": text,
        "tags": _parse_tags(tags),
    }
    path = _notes_path(session_id, root_dir=root_dir)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return {"success": True, "session_id": (session_id or "default"), "note": payload, "notes_path": str(path)}


def list_session_notes(*, tags: list[str] | None = None, session_id: str = "default", root_dir: Path | None = None) -> list[dict[str, Any]]:
    path = _notes_path(session_id, root_dir=root_dir)
    if not path.exists():
        return []

    wanted = set(_parse_tags(tags))
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
            if not isinstance(item, dict):
                continue
            item_tags = set(_parse_tags(item.get("tags") or []))
            if wanted and not (wanted & item_tags):
                continue
            out.append(item)
        except Exception:
            continue
    return out


def summarize_session_notes(*, session_id: str = "default", root_dir: Path | None = None) -> dict[str, Any]:
    notes = list_session_notes(session_id=session_id, root_dir=root_dir)
    if not notes:
        return {"session_id": session_id, "count": 0, "summary": "No session notes available."}

    tag_counts: dict[str, int] = {}
    for item in notes:
        for tag in _parse_tags(item.get("tags") or []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    top_tags = sorted(tag_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:6]
    latest = notes[-5:]
    summary_lines = [
        f"Session '{session_id}' has {len(notes)} notes.",
        "Top tags: " + (", ".join([f"{name}({count})" for name, count in top_tags]) if top_tags else "none"),
        "Recent notes:",
    ]
    summary_lines.extend([f"- {item.get('note')}" for item in latest])

    return {
        "session_id": session_id,
        "count": len(notes),
        "top_tags": [{"tag": name, "count": count} for name, count in top_tags],
        "summary": "\n".join(summary_lines),
        "notes": notes,
    }
