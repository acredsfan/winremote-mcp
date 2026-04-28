import json

import pytest


def test_render_session_report_tool_invokes_module(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(
        main.session_report,
        "render_session_report",
        lambda session_id: {
            "session_id": session_id,
            "report_path": "C:/Temp/report.html",
            "action_count": 1,
            "event_count": 2,
            "timeline_count": 3,
            "artifact_count": 0,
        },
    )

    raw = main.RenderSessionReport("session_abc")
    payload = json.loads(raw)

    assert payload["session_id"] == "session_abc"
    assert payload["report_path"].endswith("report.html")
