"""Controlled terminal session management for agent workflows."""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
from uuid import uuid4


@dataclass
class TerminalSession:
    terminal_id: str
    shell: str
    cwd: str | None
    log_path: str
    process: subprocess.Popen[Any]
    created_at: float = field(default_factory=time.time)
    output_buffer: collections.deque[str] = field(default_factory=lambda: collections.deque(maxlen=5000))
    lock: threading.Lock = field(default_factory=threading.Lock)


_SESSIONS: dict[str, TerminalSession] = {}


def _sessions_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "WinRemoteMCP" / "sessions" / "terminal_default" / "terminals"
    return Path.home() / ".local" / "share" / "WinRemoteMCP" / "sessions" / "terminal_default" / "terminals"


def _resolve_shell_command(shell: str) -> list[str]:
    normalized = str(shell or "powershell").strip().lower()
    if normalized == "cmd":
        return ["cmd.exe", "/Q", "/K"]
    if normalized == "python":
        return [sys.executable, "-i", "-u"]
    if normalized == "git-bash":
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ]
        for candidate in candidates:
            if Path(candidate).exists():
                return [candidate, "-i"]
        return ["bash", "-i"]
    return [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
    ]


def _spawn_process(*, shell: str, cwd: str | None = None, env: dict[str, str] | None = None) -> subprocess.Popen[Any]:
    cmd = _resolve_shell_command(shell)
    return subprocess.Popen(
        cmd,
        cwd=cwd or None,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )


def _append_line(session: TerminalSession, line: str) -> None:
    text = str(line or "")
    with session.lock:
        session.output_buffer.append(text)
    with open(session.log_path, "a", encoding="utf-8", errors="ignore") as f:
        f.write(text)


def _start_reader_thread(session: TerminalSession) -> threading.Thread:
    def _reader() -> None:
        stream = session.process.stdout
        if stream is None:
            return
        while True:
            try:
                line = stream.readline()
            except Exception:
                break
            if line == "":
                break
            _append_line(session, line)

    thread = threading.Thread(target=_reader, daemon=True, name=f"terminal-reader-{session.terminal_id}")
    thread.start()
    return thread


def create_terminal_session(
    *,
    shell: str = "powershell",
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = _sessions_root()
    root.mkdir(parents=True, exist_ok=True)

    terminal_id = f"term_{uuid4().hex[:12]}"
    log_path = root / f"{terminal_id}.log"
    process = _spawn_process(shell=shell, cwd=cwd, env=env)

    session = TerminalSession(
        terminal_id=terminal_id,
        shell=shell,
        cwd=cwd,
        log_path=str(log_path),
        process=process,
    )
    _SESSIONS[terminal_id] = session
    _start_reader_thread(session)

    return {
        "terminal_id": terminal_id,
        "shell": shell,
        "cwd": cwd,
        "pid": int(getattr(process, "pid", 0) or 0),
        "log_path": str(log_path),
        "running": process.poll() is None,
    }


def list_terminal_sessions() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session in _SESSIONS.values():
        out.append(
            {
                "terminal_id": session.terminal_id,
                "shell": session.shell,
                "cwd": session.cwd,
                "pid": int(getattr(session.process, "pid", 0) or 0),
                "running": session.process.poll() is None,
                "created_at": session.created_at,
                "log_path": session.log_path,
            }
        )
    out.sort(key=lambda item: item["created_at"], reverse=True)
    return out


def read_terminal_output(terminal_id: str, *, lines: int = 200) -> dict[str, Any]:
    session = _SESSIONS.get(terminal_id)
    if session is None:
        raise ValueError(f"Unknown terminal session: {terminal_id}")

    with session.lock:
        recent = list(session.output_buffer)[-max(1, int(lines)):]

    return {
        "terminal_id": terminal_id,
        "running": session.process.poll() is None,
        "line_count": len(recent),
        "lines": recent,
        "output": "".join(recent),
    }


def send_terminal_input(terminal_id: str, text: str, *, press_enter: bool = True) -> dict[str, Any]:
    session = _SESSIONS.get(terminal_id)
    if session is None:
        raise ValueError(f"Unknown terminal session: {terminal_id}")
    if session.process.poll() is not None:
        return {
            "terminal_id": terminal_id,
            "success": False,
            "running": False,
            "reason": "Terminal process has exited",
        }

    stdin = session.process.stdin
    if stdin is None:
        return {
            "terminal_id": terminal_id,
            "success": False,
            "running": True,
            "reason": "stdin is unavailable for this session",
        }

    payload = str(text or "")
    stdin.write(payload)
    if press_enter:
        stdin.write("\n")
    stdin.flush()

    return {
        "terminal_id": terminal_id,
        "success": True,
        "running": True,
        "sent_text_length": len(payload),
        "press_enter": bool(press_enter),
    }


def wait_for_terminal_output(
    terminal_id: str,
    *,
    expected_text: str,
    timeout_seconds: float = 60.0,
    poll_interval: float = 0.25,
) -> dict[str, Any]:
    session = _SESSIONS.get(terminal_id)
    if session is None:
        raise ValueError(f"Unknown terminal session: {terminal_id}")

    needle = str(expected_text or "").strip().lower()
    if not needle:
        raise ValueError("expected_text is required")

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    attempts = 0
    while True:
        attempts += 1
        current = read_terminal_output(terminal_id, lines=400)
        haystack = str(current.get("output") or "").lower()
        matched = needle in haystack
        if matched:
            return {
                "terminal_id": terminal_id,
                "satisfied": True,
                "timed_out": False,
                "attempts": attempts,
                "matched": True,
                "output_excerpt": str(current.get("output") or "")[-3000:],
            }
        if time.monotonic() >= deadline:
            return {
                "terminal_id": terminal_id,
                "satisfied": False,
                "timed_out": True,
                "attempts": attempts,
                "matched": False,
                "output_excerpt": str(current.get("output") or "")[-3000:],
            }
        time.sleep(max(0.05, float(poll_interval)))


def stop_terminal_session(terminal_id: str, *, force: bool = False) -> dict[str, Any]:
    session = _SESSIONS.get(terminal_id)
    if session is None:
        raise ValueError(f"Unknown terminal session: {terminal_id}")

    process = session.process
    running = process.poll() is None
    if running:
        if force:
            process.kill()
        else:
            process.terminate()

    return {
        "terminal_id": terminal_id,
        "success": True,
        "force": bool(force),
        "running_before": running,
        "running_after": process.poll() is None,
    }
