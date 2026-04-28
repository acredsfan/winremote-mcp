"""Lightweight polling-based file watch utilities."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _file_hash(path: Path, max_bytes: int = 1_000_000) -> str | None:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return None
        data = path.read_bytes()
        return hashlib.sha1(data).hexdigest()[:16]
    except Exception:
        return None


@dataclass
class WatchState:
    watch_id: str
    root: Path
    recursive: bool
    ignore_patterns: list[str] = field(default_factory=list)
    baseline: dict[str, dict[str, Any]] = field(default_factory=dict)
    changes: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=_utc_now_iso)


_WATCHES: dict[str, WatchState] = {}


def _is_ignored(path: Path, root: Path, ignore_patterns: list[str]) -> bool:
    rel = str(path.relative_to(root)).replace("\\", "/")
    return any(fnmatch.fnmatch(rel, pattern) for pattern in ignore_patterns)


def _scan(root: Path, *, recursive: bool, ignore_patterns: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    if recursive:
        iterator = root.rglob("*")
    else:
        iterator = root.glob("*")

    for path in iterator:
        if not path.is_file():
            continue
        if _is_ignored(path, root, ignore_patterns):
            continue
        try:
            stat = path.stat()
        except Exception:
            continue

        rel = str(path.relative_to(root)).replace("\\", "/")
        result[rel] = {
            "path": str(path),
            "relative_path": rel,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "sha1": _file_hash(path),
        }
    return result


def start_file_watch(
    root: str,
    *,
    recursive: bool = True,
    ignore_patterns: list[str] | None = None,
) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise FileNotFoundError(f"Watch root must be an existing directory: {root}")

    watch_id = f"watch_{uuid4().hex[:10]}"
    patterns = [p.strip() for p in (ignore_patterns or []) if p and p.strip()]
    baseline = _scan(root_path, recursive=recursive, ignore_patterns=patterns)

    state = WatchState(
        watch_id=watch_id,
        root=root_path,
        recursive=bool(recursive),
        ignore_patterns=patterns,
        baseline=baseline,
    )
    _WATCHES[watch_id] = state

    return {
        "watch_id": watch_id,
        "root": str(root_path),
        "recursive": state.recursive,
        "ignore_patterns": state.ignore_patterns,
        "baseline_file_count": len(baseline),
        "started_at": state.started_at,
    }


def _compute_changes(state: WatchState) -> list[dict[str, Any]]:
    current = _scan(state.root, recursive=state.recursive, ignore_patterns=state.ignore_patterns)
    prev = state.baseline

    changes: list[dict[str, Any]] = []

    prev_keys = set(prev)
    curr_keys = set(current)

    for rel in sorted(curr_keys - prev_keys):
        info = current[rel]
        changes.append(
            {
                "type": "created",
                "watch_id": state.watch_id,
                "root": str(state.root),
                "relative_path": rel,
                "path": info["path"],
                "size": info["size"],
                "sha1": info["sha1"],
                "timestamp": _utc_now_iso(),
            }
        )

    for rel in sorted(prev_keys - curr_keys):
        info = prev[rel]
        changes.append(
            {
                "type": "deleted",
                "watch_id": state.watch_id,
                "root": str(state.root),
                "relative_path": rel,
                "path": info["path"],
                "size": info.get("size"),
                "sha1": info.get("sha1"),
                "timestamp": _utc_now_iso(),
            }
        )

    for rel in sorted(prev_keys & curr_keys):
        before = prev[rel]
        after = current[rel]
        changed = (before.get("mtime") != after.get("mtime")) or (before.get("size") != after.get("size"))
        if changed:
            changes.append(
                {
                    "type": "modified",
                    "watch_id": state.watch_id,
                    "root": str(state.root),
                    "relative_path": rel,
                    "path": after["path"],
                    "size_before": before.get("size"),
                    "size_after": after.get("size"),
                    "sha1_before": before.get("sha1"),
                    "sha1_after": after.get("sha1"),
                    "timestamp": _utc_now_iso(),
                }
            )

    state.baseline = current
    state.changes.extend(changes)
    return changes


def list_file_changes(*, watch_id: str | None = None, since_seconds: int | None = None) -> list[dict[str, Any]]:
    if watch_id:
        state = _WATCHES.get(watch_id)
        if state is None:
            raise ValueError(f"Unknown watch_id: {watch_id}")
        _compute_changes(state)
        changes = list(state.changes)
    else:
        changes = []
        for state in _WATCHES.values():
            _compute_changes(state)
            changes.extend(state.changes)

    if since_seconds is not None and since_seconds > 0:
        now = datetime.now(tz=timezone.utc)
        threshold = now.timestamp() - since_seconds

        def _is_recent(item: dict[str, Any]) -> bool:
            try:
                ts = datetime.fromisoformat(str(item.get("timestamp"))).timestamp()
                return ts >= threshold
            except Exception:
                return False

        changes = [item for item in changes if _is_recent(item)]

    changes.sort(key=lambda item: str(item.get("timestamp") or ""))
    return changes


def stop_file_watch(watch_id: str) -> dict[str, Any]:
    state = _WATCHES.pop(watch_id, None)
    if state is None:
        raise ValueError(f"Unknown watch_id: {watch_id}")

    _compute_changes(state)
    return {
        "watch_id": watch_id,
        "stopped": True,
        "root": str(state.root),
        "started_at": state.started_at,
        "stopped_at": _utc_now_iso(),
        "change_count": len(state.changes),
    }
