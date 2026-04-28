"""Action budget and rate-limiter safeguards for automation loops."""

from __future__ import annotations

import time
from typing import Any


_DEFAULT_POLICY = {
    "max_clicks_per_minute": 20,
    "max_keystrokes_per_minute": 2000,
    "max_shell_commands_per_minute": 10,
    "max_computer_use_steps": 25,
    "pause_on_repeated_failure": True,
    "repeated_failure_threshold": 3,
}

_POLICY = dict(_DEFAULT_POLICY)

_COUNTERS: dict[str, list[float]] = {
    "click": [],
    "keystroke": [],
    "shell": [],
    "computer_use_step": [],
}

_FAILURE_COUNTS: dict[str, int] = {}
_PAUSED = False
_PAUSE_REASON = ""


def _trim_recent(timestamps: list[float], window_seconds: float = 60.0) -> list[float]:
    now = time.monotonic()
    return [ts for ts in timestamps if now - ts <= window_seconds]


def _limit_for(action_kind: str) -> int | None:
    mapping = {
        "click": "max_clicks_per_minute",
        "keystroke": "max_keystrokes_per_minute",
        "shell": "max_shell_commands_per_minute",
        "computer_use_step": "max_computer_use_steps",
    }
    key = mapping.get(action_kind)
    if not key:
        return None
    try:
        return int(_POLICY.get(key) or 0)
    except Exception:
        return None


def check_and_record(action_kind: str, amount: int = 1) -> tuple[bool, str | None]:
    global _PAUSED, _PAUSE_REASON
    if _PAUSED:
        return False, _PAUSE_REASON or "Action budget paused"

    limit = _limit_for(action_kind)
    if limit is None or limit <= 0:
        return True, None

    bucket = _COUNTERS.setdefault(action_kind, [])
    bucket[:] = _trim_recent(bucket)
    if len(bucket) + max(1, int(amount)) > limit:
        _PAUSED = True
        _PAUSE_REASON = f"Action budget exceeded for {action_kind}: {len(bucket)} + {amount} > {limit} per minute"
        return False, _PAUSE_REASON

    now = time.monotonic()
    for _ in range(max(1, int(amount))):
        bucket.append(now)
    return True, None


def record_failure(scope: str = "global") -> None:
    global _PAUSED, _PAUSE_REASON
    key = str(scope or "global")
    _FAILURE_COUNTS[key] = _FAILURE_COUNTS.get(key, 0) + 1

    threshold = int(_POLICY.get("repeated_failure_threshold") or 0)
    if bool(_POLICY.get("pause_on_repeated_failure")) and threshold > 0 and _FAILURE_COUNTS[key] >= threshold:
        _PAUSED = True
        _PAUSE_REASON = f"Repeated failures reached threshold for {key}: {_FAILURE_COUNTS[key]}"


def record_success(scope: str = "global") -> None:
    key = str(scope or "global")
    _FAILURE_COUNTS[key] = 0


def status() -> dict[str, Any]:
    return {
        "paused": _PAUSED,
        "pause_reason": _PAUSE_REASON,
        "policy": dict(_POLICY),
        "recent_counts": {name: len(_trim_recent(values)) for name, values in _COUNTERS.items()},
        "failure_counts": dict(_FAILURE_COUNTS),
    }


def configure(**overrides: Any) -> dict[str, Any]:
    for key, value in overrides.items():
        if key not in _POLICY or value is None:
            continue
        if isinstance(_DEFAULT_POLICY.get(key), bool):
            _POLICY[key] = bool(value)
        else:
            _POLICY[key] = int(value)
    return status()


def reset(*, unpause: bool = True) -> dict[str, Any]:
    global _PAUSED, _PAUSE_REASON
    for key in list(_COUNTERS.keys()):
        _COUNTERS[key].clear()
    _FAILURE_COUNTS.clear()
    if unpause:
        _PAUSED = False
        _PAUSE_REASON = ""
    return status()
