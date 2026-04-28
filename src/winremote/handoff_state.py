"""Global human handoff pause/resume state."""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class HandoffState:
    paused: bool = False
    message: str | None = None
    resume_trigger: str = "manual"
    resume_at_epoch: float | None = None
    requested_at_epoch: float | None = None


_STATE = HandoffState()


def _refresh_timeout() -> None:
    if _STATE.paused and _STATE.resume_at_epoch is not None and time.time() >= _STATE.resume_at_epoch:
        _STATE.paused = False
        _STATE.message = None
        _STATE.resume_trigger = "manual"
        _STATE.resume_at_epoch = None
        _STATE.requested_at_epoch = None


def request_handoff(
    *,
    message: str,
    pause_input_tools: bool = True,
    resume_trigger: str = "manual",
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    _STATE.paused = bool(pause_input_tools)
    _STATE.message = message
    _STATE.resume_trigger = str(resume_trigger or "manual")
    _STATE.requested_at_epoch = time.time()
    _STATE.resume_at_epoch = None
    if timeout_seconds is not None and timeout_seconds > 0:
        _STATE.resume_at_epoch = time.time() + float(timeout_seconds)
    return status()


def resume_handoff() -> dict[str, Any]:
    _STATE.paused = False
    _STATE.message = None
    _STATE.resume_trigger = "manual"
    _STATE.resume_at_epoch = None
    _STATE.requested_at_epoch = None
    return status()


def is_paused() -> bool:
    _refresh_timeout()
    return _STATE.paused


def status() -> dict[str, Any]:
    _refresh_timeout()
    payload = asdict(_STATE)
    payload["paused"] = _STATE.paused
    return payload
