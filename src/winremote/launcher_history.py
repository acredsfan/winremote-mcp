"""Local JSONL append-only event history store for the winremote tray launcher."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Data directory resolution
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "~")).expanduser()
    else:
        base = Path("~/.config").expanduser()
    d = base / "winremote"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

class EventType:
    SERVER_START = "server_start"
    SERVER_STOP = "server_stop"
    SERVER_RESTART = "server_restart"
    SERVER_ERROR = "server_error"
    SERVER_HEALTHY = "server_healthy"
    SERVER_DEGRADED = "server_degraded"
    SERVER_PROFILE_CHANGE = "server_profile_change"
    TUNNEL_START = "tunnel_start"
    TUNNEL_STOP = "tunnel_stop"
    TUNNEL_ERROR = "tunnel_error"
    TUNNEL_URL = "tunnel_url"
    TOOL_CALL = "tool_call"
    LAUNCHER_START = "launcher_start"
    LAUNCHER_STOP = "launcher_stop"


@dataclass
class HistoryEvent:
    event_type: str
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def to_jsonl_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HistoryEvent:
        return cls(
            event_type=d["event_type"],
            timestamp=d.get("timestamp", 0.0),
            data=d.get("data", {}),
        )


# ---------------------------------------------------------------------------
# History store
# ---------------------------------------------------------------------------

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB before rotation
MAX_RETENTION_DAYS = 30


class HistoryStore:
    """Thread-safe append-only JSONL event history store.

    Events are written to ``<data_dir>/launcher_history.jsonl``.
    The file is rotated (old >30-day events trimmed) when it exceeds 10 MB.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_data_dir() / "launcher_history.jsonl")
        self._lock = threading.Lock()
        # Ensure parent exists
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(self, event: HistoryEvent) -> None:
        """Append an event to the store (thread-safe)."""
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(event.to_jsonl_line() + "\n")
            # Opportunistically rotate if file is large
            try:
                if self._path.stat().st_size > MAX_FILE_SIZE_BYTES:
                    self._rotate()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def tail(self, n: int = 100) -> list[HistoryEvent]:
        """Return the last *n* events (most recent last)."""
        return list(self._iter_events())[-n:]

    def query(
        self,
        *,
        since: float | None = None,
        event_type: str | None = None,
        limit: int = 500,
    ) -> list[HistoryEvent]:
        """Return events filtered by timestamp and/or type."""
        results: list[HistoryEvent] = []
        for evt in self._iter_events():
            if since is not None and evt.timestamp < since:
                continue
            if event_type is not None and evt.event_type != event_type:
                continue
            results.append(evt)
        return results[-limit:]

    def all_events(self) -> list[HistoryEvent]:
        return list(self._iter_events())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _iter_events(self) -> Iterator[HistoryEvent]:
        with self._lock:
            if not self._path.exists():
                return
            try:
                lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                yield HistoryEvent.from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError):
                continue

    def _rotate(self) -> None:
        """Drop events older than MAX_RETENTION_DAYS from the file."""
        cutoff = time.time() - MAX_RETENTION_DAYS * 86400
        kept: list[str] = []
        try:
            lines = self._path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for line in lines:
            line_s = line.strip()
            if not line_s:
                continue
            try:
                evt = json.loads(line_s)
                if evt.get("timestamp", 0) >= cutoff:
                    kept.append(line_s)
            except (json.JSONDecodeError, KeyError):
                kept.append(line_s)
        self._path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
