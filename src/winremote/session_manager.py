"""Session artifact management for WinRemote runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class SessionInfo:
    session_id: str
    path: Path


class SessionManager:
    """Create and update per-session trace artifacts."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self, session_id: str | None = None) -> SessionInfo:
        sid = session_id or f"session_{uuid4().hex[:12]}"
        session_path = self.base_dir / sid
        session_path.mkdir(parents=True, exist_ok=True)

        manifest_path = session_path / "manifest.json"
        if not manifest_path.exists():
            manifest_path.write_text(
                json.dumps(
                    {
                        "session_id": sid,
                        "started_at": _utc_now_iso(),
                        "ended_at": None,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        self._ensure_file(session_path / "actions.jsonl")
        self._ensure_file(session_path / "events.jsonl")
        return SessionInfo(session_id=sid, path=session_path)

    def close_session(self, session_id: str) -> None:
        manifest = self.load_manifest(session_id)
        manifest["ended_at"] = _utc_now_iso()
        self._manifest_path(session_id).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def load_manifest(self, session_id: str) -> dict[str, object]:
        return json.loads(self._manifest_path(session_id).read_text(encoding="utf-8"))

    def record_action(self, session_id: str, action: dict[str, object]) -> None:
        self._append_jsonl(self._session_path(session_id) / "actions.jsonl", action)

    def record_event(self, session_id: str, event: dict[str, object]) -> None:
        self._append_jsonl(self._session_path(session_id) / "events.jsonl", event)

    def _session_path(self, session_id: str) -> Path:
        path = self.base_dir / session_id
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return path

    def _manifest_path(self, session_id: str) -> Path:
        return self._session_path(session_id) / "manifest.json"

    def _append_jsonl(self, path: Path, payload: dict[str, object]) -> None:
        record = dict(payload)
        if "timestamp" not in record:
            record["timestamp"] = _utc_now_iso()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _ensure_file(path: Path) -> None:
        if not path.exists():
            path.write_text("", encoding="utf-8")
