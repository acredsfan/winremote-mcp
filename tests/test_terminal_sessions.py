from types import SimpleNamespace

import pytest

from winremote import terminal_sessions


class _FakeStdin:
    def __init__(self):
        self.buffer = []

    def write(self, text: str):
        self.buffer.append(text)

    def flush(self):
        return None


class _FakeProcess:
    def __init__(self, pid: int = 1234):
        self.pid = pid
        self.stdin = _FakeStdin()
        self.stdout = None
        self._poll = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True
        self._poll = 0

    def kill(self):
        self.killed = True
        self._poll = -9


def test_terminal_session_create_list_send_read_stop(tmp_path, monkeypatch: pytest.MonkeyPatch):
    terminal_sessions._SESSIONS.clear()
    monkeypatch.setattr(terminal_sessions, "_sessions_root", lambda: tmp_path)
    monkeypatch.setattr(terminal_sessions, "_spawn_process", lambda **kwargs: _FakeProcess())
    monkeypatch.setattr(terminal_sessions, "_start_reader_thread", lambda session: None)

    created = terminal_sessions.create_terminal_session(shell="powershell", cwd=str(tmp_path))
    tid = created["terminal_id"]

    listed = terminal_sessions.list_terminal_sessions()
    assert any(item["terminal_id"] == tid for item in listed)

    sent = terminal_sessions.send_terminal_input(tid, "echo hi", press_enter=True)
    assert sent["success"] is True

    session = terminal_sessions._SESSIONS[tid]
    terminal_sessions._append_line(session, "line1\n")
    terminal_sessions._append_line(session, "line2\n")
    out = terminal_sessions.read_terminal_output(tid, lines=10)
    assert out["line_count"] == 2
    assert "line1" in out["output"]

    waited = terminal_sessions.wait_for_terminal_output(tid, expected_text="line2", timeout_seconds=0.1, poll_interval=0.01)
    assert waited["satisfied"] is True

    stopped = terminal_sessions.stop_terminal_session(tid, force=False)
    assert stopped["success"] is True


def test_wait_for_terminal_output_times_out(tmp_path, monkeypatch: pytest.MonkeyPatch):
    terminal_sessions._SESSIONS.clear()
    monkeypatch.setattr(terminal_sessions, "_sessions_root", lambda: tmp_path)
    monkeypatch.setattr(terminal_sessions, "_spawn_process", lambda **kwargs: _FakeProcess())
    monkeypatch.setattr(terminal_sessions, "_start_reader_thread", lambda session: None)

    created = terminal_sessions.create_terminal_session(shell="powershell")
    tid = created["terminal_id"]

    waited = terminal_sessions.wait_for_terminal_output(tid, expected_text="never-here", timeout_seconds=0.05, poll_interval=0.01)
    assert waited["satisfied"] is False
    assert waited["timed_out"] is True
