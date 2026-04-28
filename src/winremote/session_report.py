"""Local HTML report rendering for WinRemote session traces."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any


def _sessions_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "WinRemoteMCP" / "sessions"
    return Path.home() / ".local" / "share" / "WinRemoteMCP" / "sessions"


def _load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = raw.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                out.append(payload)
        except Exception:
            continue
    return out


def _artifact_links(session_dir: Path) -> list[str]:
    links: list[str] = []
    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.mp4", "*.webm"):
        for path in sorted(session_dir.glob(pattern))[:50]:
            rel = path.name
            links.append(f'<li><a href="{html.escape(rel)}">{html.escape(rel)}</a></li>')
    return links


def render_session_report(session_id: str, *, root_dir: Path | None = None) -> dict[str, Any]:
    root = root_dir or _sessions_root()
    session_dir = root / session_id
    if not session_dir.exists():
        raise FileNotFoundError(f"Session not found: {session_id}")

    manifest = _load_json(session_dir / "manifest.json", default={"session_id": session_id})
    actions = _load_jsonl(session_dir / "actions.jsonl")
    events = _load_jsonl(session_dir / "events.jsonl")

    timeline: list[dict[str, Any]] = []
    for item in actions:
        timeline.append(
            {
                "timestamp": item.get("timestamp") or "",
                "source": "action",
                "type": item.get("type") or item.get("tool") or "action",
                "summary": item.get("goal") or item.get("action") or item.get("summary") or "",
                "raw": item,
            }
        )
    for item in events:
        timeline.append(
            {
                "timestamp": item.get("timestamp") or "",
                "source": "event",
                "type": item.get("type") or "event",
                "summary": item.get("summary") or item.get("message") or item.get("action") or "",
                "raw": item,
            }
        )
    timeline.sort(key=lambda entry: str(entry.get("timestamp") or ""))

    artifact_rows = _artifact_links(session_dir)

    timeline_rows = []
    for row in timeline[:200]:
        timeline_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('timestamp') or ''))}</td>"
            f"<td>{html.escape(str(row.get('source') or ''))}</td>"
            f"<td>{html.escape(str(row.get('type') or ''))}</td>"
            f"<td>{html.escape(str(row.get('summary') or ''))}</td>"
            "</tr>"
        )

    report_html = f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>WinRemote Session Report - {html.escape(session_id)}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 20px; color: #1f2937; }}
    h1, h2 {{ margin: 0.4em 0; }}
    .card {{ border: 1px solid #d1d5db; border-radius: 10px; padding: 12px; margin: 12px 0; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f9fafb; }}
    code {{ background: #f3f4f6; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Session Report: {html.escape(session_id)}</h1>
  <div class=\"card\">
    <h2>Summary</h2>
    <p><strong>Started:</strong> {html.escape(str(manifest.get('started_at') or ''))}</p>
    <p><strong>Ended:</strong> {html.escape(str(manifest.get('ended_at') or ''))}</p>
    <p><strong>Actions:</strong> {len(actions)} | <strong>Events:</strong> {len(events)} | <strong>Timeline entries:</strong> {len(timeline)}</p>
  </div>

  <div class=\"card\">
    <h2>Artifacts</h2>
    <ul>
      {''.join(artifact_rows) if artifact_rows else '<li>No direct media artifacts in session root.</li>'}
    </ul>
  </div>

  <div class=\"card\">
    <h2>Timeline</h2>
    <table>
      <thead><tr><th>Timestamp</th><th>Source</th><th>Type</th><th>Summary</th></tr></thead>
      <tbody>
        {''.join(timeline_rows) if timeline_rows else '<tr><td colspan="4">No timeline entries available.</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class=\"card\">
    <h2>Manifest JSON</h2>
    <pre>{html.escape(json.dumps(manifest, indent=2))}</pre>
  </div>
</body>
</html>
""".strip()

    report_path = session_dir / "report.html"
    report_path.write_text(report_html, encoding="utf-8")

    return {
        "session_id": session_id,
        "report_path": str(report_path),
        "action_count": len(actions),
        "event_count": len(events),
        "timeline_count": len(timeline),
        "artifact_count": len(artifact_rows),
    }
