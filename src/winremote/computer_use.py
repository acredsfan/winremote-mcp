"""High-level computer-use step orchestration utilities."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyautogui

from winremote import action_undo
from winremote import desktop
from winremote.safety import RiskAssessment, SafetyPolicy, assess_action_risk
from winremote.session_manager import SessionManager


@dataclass
class ComputerUseStepResult:
    success: bool
    goal: str
    action_taken: str | None
    strategy: str
    risk_level: str
    risk_reason: str
    blocked: bool
    confirmation_required: bool
    target: dict[str, Any] | None
    before_observation: dict[str, Any] | None
    after_observation: dict[str, Any] | None
    verification_result: dict[str, Any] | None
    session_id: str | None
    next_suggested_step: str | None
    error: str | None = None


@dataclass
class ComputerUseTaskResult:
    success: bool
    goal: str
    session_id: str
    total_steps: int
    attempted_steps: int
    completed_steps: int
    blocked: bool
    stopped_reason: str
    steps: list[dict[str, Any]]
    next_suggested_step: str | None


def _sessions_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "WinRemoteMCP" / "sessions"
    return Path.home() / ".local" / "share" / "WinRemoteMCP" / "sessions"


def _action_type(action: str, text: str) -> str:
    if action == "type" or (action == "auto" and text):
        return "type"
    return "click"


def _target_app(window_title: str, search_window: dict[str, Any] | None) -> str | None:
    if isinstance(search_window, dict):
        process = search_window.get("process_name")
        if process:
            return str(process)
        label = search_window.get("label")
        if label:
            return str(label)
    if window_title.strip():
        return window_title.strip()
    return None


def computer_use_step(
    *,
    goal: str,
    window_title: str = "",
    target_query: str = "",
    action: str = "auto",
    text: str = "",
    match_mode: str = "auto",
    include_text: bool = False,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    confirm_risky: bool = False,
    dry_run: bool = False,
    session_id: str | None = None,
    session_manager: SessionManager | None = None,
    safety_policy: SafetyPolicy | None = None,
) -> dict[str, Any]:
    """Execute one bounded observe->plan->act->verify computer-use step."""
    normalized_goal = (goal or "").strip()
    if not normalized_goal:
        raise ValueError("goal is required")

    query = (target_query or normalized_goal).strip()
    before_observation = desktop.observe_screen(
        window_title=window_title,
        include_text=include_text,
        max_elements=min(max_elements, 40),
        min_width=min_width,
        min_height=min_height,
        reset=False,
        update_baseline=False,
    )

    search_result = desktop.find_ui_elements_with_context(
        query=query,
        window_title=window_title,
        include_text=include_text,
        max_results=1,
        match_mode=match_mode,
        max_elements=max_elements,
        min_width=min_width,
        min_height=min_height,
    )
    matches = search_result.get("matches") or []

    if not matches:
        result = ComputerUseStepResult(
            success=False,
            goal=normalized_goal,
            action_taken=None,
            strategy="none",
            risk_level="low",
            risk_reason="No matching UI target",
            blocked=False,
            confirmation_required=False,
            target=None,
            before_observation=before_observation,
            after_observation=None,
            verification_result={"target_found": False},
            session_id=session_id,
            next_suggested_step="Use UIFind/ObserveScreen to refine the query or add window_title context.",
            error="No matching UI target found",
        )
        return asdict(result)

    target = matches[0]
    target_label = str(target.get("label") or target.get("class") or query)
    app_name = _target_app(window_title, search_result.get("window"))
    effective_action = _action_type(action.strip().lower() if action else "auto", text)

    risk = assess_action_risk(
        effective_action,
        target_label,
        app_name,
        policy=safety_policy or SafetyPolicy(),
    )

    if risk.confirmation_required and not confirm_risky:
        result = ComputerUseStepResult(
            success=False,
            goal=normalized_goal,
            action_taken=None,
            strategy="uia",
            risk_level=risk.level,
            risk_reason=risk.reason,
            blocked=True,
            confirmation_required=True,
            target={
                "label": target.get("label"),
                "class": target.get("class"),
                "center": target.get("center"),
                "match": target.get("match"),
            },
            before_observation=before_observation,
            after_observation=None,
            verification_result={"target_found": True, "executed": False},
            session_id=session_id,
            next_suggested_step="Re-run with confirm_risky=true if this action is intended.",
            error="Risky action requires confirmation",
        )
        return asdict(result)

    if dry_run:
        result = ComputerUseStepResult(
            success=True,
            goal=normalized_goal,
            action_taken=f"dry_run:{effective_action}",
            strategy="uia",
            risk_level=risk.level,
            risk_reason=risk.reason,
            blocked=False,
            confirmation_required=risk.confirmation_required,
            target={
                "label": target.get("label"),
                "class": target.get("class"),
                "center": target.get("center"),
                "match": target.get("match"),
            },
            before_observation=before_observation,
            after_observation=None,
            verification_result={"target_found": True, "executed": False, "dry_run": True},
            session_id=session_id,
            next_suggested_step="Run without dry_run to execute this action.",
        )
        return asdict(result)

    center = target.get("center") or {}
    x = int(center.get("x", 0) or 0)
    y = int(center.get("y", 0) or 0)
    desktop.validate_screen_point(x, y)

    action_taken = None
    if effective_action == "type":
        pyautogui.click(x, y)
        if text:
            pyautogui.typewrite(text, interval=0.02) if text.isascii() else pyautogui.write(text)
        action_taken = f"typed:{len(text)}"
        action_undo.set_last_action(
            {
                "type": "typed",
                "text_length": len(text),
                "target": {"x": x, "y": y, "label": target_label},
            }
        )
    else:
        pyautogui.click(x, y)
        action_taken = "clicked"
        action_undo.set_last_action(
            {
                "type": "click",
                "target": {"x": x, "y": y, "label": target_label},
            }
        )

    after_observation = desktop.observe_screen(
        window_title=window_title,
        include_text=include_text,
        max_elements=min(max_elements, 40),
        min_width=min_width,
        min_height=min_height,
        reset=False,
        update_baseline=False,
    )
    verification = {
        "target_found": True,
        "executed": True,
        "changed": bool(after_observation.get("changed")),
        "change_ratio": after_observation.get("change_ratio"),
    }

    resolved_session_id = session_id
    manager = session_manager
    if manager is None:
        manager = SessionManager(_sessions_root())

    if not resolved_session_id:
        resolved_session = manager.create_session()
        resolved_session_id = resolved_session.session_id

    manager.record_action(
        resolved_session_id,
        {
            "type": "computer_use_step",
            "goal": normalized_goal,
            "query": query,
            "action": effective_action,
            "target_label": target_label,
            "center": {"x": x, "y": y},
            "risk_level": risk.level,
            "risk_reason": risk.reason,
            "verification": verification,
        },
    )

    result = ComputerUseStepResult(
        success=True,
        goal=normalized_goal,
        action_taken=action_taken,
        strategy="uia",
        risk_level=risk.level,
        risk_reason=risk.reason,
        blocked=False,
        confirmation_required=risk.confirmation_required,
        target={
            "label": target.get("label"),
            "class": target.get("class"),
            "center": target.get("center"),
            "match": target.get("match"),
        },
        before_observation=before_observation,
        after_observation=after_observation,
        verification_result=verification,
        session_id=resolved_session_id,
        next_suggested_step=(
            "Continue with another ComputerUseStep for the next UI objective."
            if verification.get("changed")
            else "No visible change detected; verify target or try an alternate action/query."
        ),
    )
    return asdict(result)


def computer_use_task(
    *,
    goal: str,
    window_title: str = "",
    target_query: str = "",
    action: str = "auto",
    text: str = "",
    match_mode: str = "auto",
    include_text: bool = False,
    max_elements: int = 100,
    min_width: int = 4,
    min_height: int = 4,
    confirm_risky: bool = False,
    dry_run: bool = False,
    max_steps: int = 5,
    max_failures: int = 2,
    stop_on_first_success: bool = True,
    step_queries: list[str] | None = None,
    session_id: str | None = None,
    session_manager: SessionManager | None = None,
    safety_policy: SafetyPolicy | None = None,
) -> dict[str, Any]:
    """Run bounded multi-step computer-use loop with trace continuity."""
    normalized_goal = (goal or "").strip()
    if not normalized_goal:
        raise ValueError("goal is required")

    max_steps = max(1, int(max_steps))
    max_failures = max(1, int(max_failures))

    manager = session_manager or SessionManager(_sessions_root())
    resolved_session_id = session_id
    if not resolved_session_id:
        resolved_session_id = manager.create_session().session_id

    planned_queries = [q.strip() for q in (step_queries or []) if q and q.strip()]
    steps: list[dict[str, Any]] = []
    completed_steps = 0
    failure_count = 0
    blocked = False
    stopped_reason = "max_steps_reached"

    for index in range(max_steps):
        current_query = (
            planned_queries[index]
            if index < len(planned_queries)
            else (target_query.strip() or normalized_goal)
        )

        step_result = computer_use_step(
            goal=normalized_goal,
            window_title=window_title,
            target_query=current_query,
            action=action,
            text=text,
            match_mode=match_mode,
            include_text=include_text,
            max_elements=max_elements,
            min_width=min_width,
            min_height=min_height,
            confirm_risky=confirm_risky,
            dry_run=dry_run,
            session_id=resolved_session_id,
            session_manager=manager,
            safety_policy=safety_policy,
        )
        step_result["step_index"] = index + 1
        step_result["step_query"] = current_query
        steps.append(step_result)

        if step_result.get("success"):
            completed_steps += 1
            if stop_on_first_success:
                stopped_reason = "success"
                break
        else:
            failure_count += 1
            if step_result.get("blocked"):
                blocked = True
                stopped_reason = "blocked"
                break
            if failure_count >= max_failures:
                stopped_reason = "max_failures_reached"
                break
    else:
        stopped_reason = "max_steps_reached"

    task_success = completed_steps > 0 and not blocked
    manager.record_action(
        resolved_session_id,
        {
            "type": "computer_use_task_summary",
            "goal": normalized_goal,
            "total_steps": max_steps,
            "attempted_steps": len(steps),
            "completed_steps": completed_steps,
            "blocked": blocked,
            "stopped_reason": stopped_reason,
            "success": task_success,
        },
    )

    next_suggested_step = None
    if blocked:
        next_suggested_step = "Review the blocked step and re-run with confirm_risky=true if appropriate."
    elif not task_success:
        next_suggested_step = "Refine target_query/window_title or lower strictness before retrying."
    elif stopped_reason == "success":
        next_suggested_step = "Proceed with the next UI objective in a new ComputerUseTask/ComputerUseStep."

    result = ComputerUseTaskResult(
        success=task_success,
        goal=normalized_goal,
        session_id=resolved_session_id,
        total_steps=max_steps,
        attempted_steps=len(steps),
        completed_steps=completed_steps,
        blocked=blocked,
        stopped_reason=stopped_reason,
        steps=steps,
        next_suggested_step=next_suggested_step,
    )
    return asdict(result)
