from pathlib import Path

from winremote import session_notes


def test_session_note_add_list_and_summarize(tmp_path: Path):
    added = session_notes.add_session_note(
        "Login redirects to login page",
        tags=["issue", "auth"],
        session_id="s1",
        root_dir=tmp_path,
    )
    assert added["success"] is True

    session_notes.add_session_note(
        "Check middleware ordering",
        tags=["hypothesis"],
        session_id="s1",
        root_dir=tmp_path,
    )

    listed = session_notes.list_session_notes(session_id="s1", root_dir=tmp_path)
    assert len(listed) == 2

    filtered = session_notes.list_session_notes(tags=["auth"], session_id="s1", root_dir=tmp_path)
    assert len(filtered) == 1

    summary = session_notes.summarize_session_notes(session_id="s1", root_dir=tmp_path)
    assert summary["count"] == 2
    assert "Recent notes" in summary["summary"]
