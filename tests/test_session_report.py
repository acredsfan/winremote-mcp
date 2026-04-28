import json
from pathlib import Path

from winremote import session_report


def test_render_session_report_writes_html(tmp_path: Path):
    session_id = "session_test"
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    (session_dir / "manifest.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "started_at": "2026-04-28T12:01:02+00:00",
                "ended_at": "2026-04-28T12:03:02+00:00",
            }
        ),
        encoding="utf-8",
    )
    (session_dir / "actions.jsonl").write_text(
        json.dumps({"timestamp": "2026-04-28T12:01:05+00:00", "type": "computer_use_step", "goal": "Click Run"}) + "\n",
        encoding="utf-8",
    )
    (session_dir / "events.jsonl").write_text(
        json.dumps({"timestamp": "2026-04-28T12:01:06+00:00", "type": "click", "summary": "Clicked Run"}) + "\n",
        encoding="utf-8",
    )

    payload = session_report.render_session_report(session_id, root_dir=tmp_path)
    report_path = Path(payload["report_path"])

    assert payload["session_id"] == session_id
    assert payload["action_count"] == 1
    assert payload["event_count"] == 1
    assert report_path.exists()

    html = report_path.read_text(encoding="utf-8")
    assert "Session Report" in html
    assert "Timeline" in html


def test_render_session_report_missing_session_raises(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        session_report.render_session_report("missing", root_dir=tmp_path)
