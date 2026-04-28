import json

import pytest


def test_analyze_recording_tool_invokes_module(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    captured = {}

    def _fake_analyze(**kwargs):
        captured.update(kwargs)
        return {
            "recording_id": kwargs["recording_id"],
            "keyframes_extracted": 2,
            "analysis_path": "C:/Temp/analysis.md",
        }

    monkeypatch.setattr(main.recording, "analyze_recording", _fake_analyze)

    raw = main.AnalyzeRecording(
        recording_id="rec_123",
        question="What failed?",
        extract_keyframes="true",
        include_ocr="false",
        include_ui_context="true",
        include_event_timeline="false",
        output_format="debug_report",
    )
    payload = json.loads(raw)

    assert payload["recording_id"] == "rec_123"
    assert payload["keyframes_extracted"] == 2
    assert captured["recording_id"] == "rec_123"
    assert captured["include_ocr"] is False
    assert captured["include_event_timeline"] is False
