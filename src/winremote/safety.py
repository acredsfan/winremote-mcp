"""Safety policy and risk assessment helpers for desktop actions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


HIGH_RISK_KEYWORDS = {
    "submit",
    "buy",
    "send",
    "delete",
    "pay",
    "confirm",
    "agree",
    "publish",
    "upload",
}

HIGH_RISK_DOMAINS = {
    "bank",
    "banking",
    "medical",
    "health",
    "government",
    "gov",
    "irs",
}

PROMPT_INJECTION_PATTERNS = [
    r"(?i)ignore (all|previous|prior) instructions",
    r"(?i)you are now",
    r"(?i)reveal (your )?(system|hidden) prompt",
    r"(?i)act as",
]


@dataclass
class SafetyPolicy:
    allowed_apps: list[str] = field(default_factory=list)
    denied_apps: list[str] = field(default_factory=list)
    confirmation_required_patterns: list[str] = field(default_factory=lambda: sorted(HIGH_RISK_KEYWORDS))
    redact_password_fields: bool = True


@dataclass
class RiskAssessment:
    level: str
    reason: str
    confirmation_required: bool


def detect_prompt_injection(text: str) -> bool:
    """Return True when common prompt-injection strings are detected."""
    return any(re.search(pattern, text) is not None for pattern in PROMPT_INJECTION_PATTERNS)


def assess_action_risk(
    action_type: str,
    target_label: str | None,
    target_app: str | None,
    *,
    policy: SafetyPolicy | None = None,
) -> RiskAssessment:
    """Assess action risk and whether explicit confirmation should be required."""
    configured_policy = policy or SafetyPolicy()
    label = (target_label or "").strip().lower()
    app = (target_app or "").strip().lower()

    if app and configured_policy.denied_apps and app in {a.lower() for a in configured_policy.denied_apps}:
        return RiskAssessment(
            level="high",
            reason=f"Target app '{target_app}' is denied by policy",
            confirmation_required=True,
        )

    if configured_policy.allowed_apps and app and app not in {a.lower() for a in configured_policy.allowed_apps}:
        return RiskAssessment(
            level="high",
            reason=f"Target app '{target_app}' is outside allowlist",
            confirmation_required=True,
        )

    trigger_words = {p.strip().lower() for p in configured_policy.confirmation_required_patterns if p.strip()}
    if any(word and word in label for word in trigger_words):
        return RiskAssessment(
            level="high",
            reason=f"Detected destructive/sensitive label text: '{target_label}'",
            confirmation_required=True,
        )

    if any(domain in label for domain in HIGH_RISK_DOMAINS) or any(domain in app for domain in HIGH_RISK_DOMAINS):
        return RiskAssessment(
            level="high",
            reason="Detected high-risk domain context",
            confirmation_required=True,
        )

    if action_type.lower() in {"type_secret", "paste_secret"}:
        return RiskAssessment(
            level="high",
            reason="Action involves potentially sensitive input",
            confirmation_required=True,
        )

    return RiskAssessment(level="low", reason="No elevated risk signals detected", confirmation_required=False)
