import json

import pytest


def test_session_note_tools_roundtrip(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.session_notes, "add_session_note", lambda note, tags=None, session_id="default": {"success": True, "session_id": session_id, "note": note, "tags": tags or []})
    monkeypatch.setattr(main.session_notes, "list_session_notes", lambda tags=None, session_id="default": [{"note": "n1", "tags": tags or []}])
    monkeypatch.setattr(main.session_notes, "summarize_session_notes", lambda session_id="default": {"count": 1, "summary": "ok"})

    add_payload = json.loads(main.SessionNoteAdd("note-1", tags_csv="issue,auth", session_id="s1"))
    list_payload = json.loads(main.SessionNoteList(tags_csv="issue", session_id="s1"))
    summary_payload = json.loads(main.SessionNoteSummarize(session_id="s1"))

    assert add_payload["success"] is True
    assert add_payload["session_id"] == "s1"
    assert len(list_payload) == 1
    assert summary_payload["count"] == 1
