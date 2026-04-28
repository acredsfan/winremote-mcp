"""Known issue detection heuristics for desktop automation workflows."""

from __future__ import annotations

import re
from typing import Any


def _issue(
    issue_type: str,
    confidence: float,
    evidence: str,
    suggested_next_tool: str,
    suggested_fix: str,
) -> dict[str, Any]:
    return {
        "type": issue_type,
        "confidence": round(float(confidence), 2),
        "evidence": evidence,
        "suggested_next_tool": suggested_next_tool,
        "suggested_fix": suggested_fix,
    }


def detect_known_issues(
    *,
    active_window_title: str = "",
    terminal_output: str = "",
    ocr_text: str = "",
    browser_console_text: str = "",
    browser_network_text: str = "",
) -> dict[str, Any]:
    text = "\n".join([active_window_title, terminal_output, ocr_text, browser_console_text, browser_network_text]).lower()

    checks: list[tuple[str, float, str, str, str, str]] = [
        ("port_in_use", 0.92, r"address already in use|eaddrinuse|port .* already", "ReadTerminalOutput", "Stop the conflicting process or choose a different port."),
        ("package_install_failed", 0.9, r"pip .* failed|npm err|install failed|could not find a version", "ReadTerminalOutput", "Review dependency errors and retry with corrected package/version."),
        ("authentication_expired", 0.89, r"token expired|session expired|unauthorized|401", "GetBrowserConsoleLogs", "Refresh auth token/session and retry the operation."),
        ("login_loop", 0.84, r"login|sign in", "ObserveScreen", "Check auth middleware/redirect conditions to break the loop."),
        ("modal_dialog_blocking", 0.8, r"dialog|modal|are you sure|confirm", "UIFind", "Close or confirm the modal before continuing."),
        ("terminal_waiting_input", 0.82, r"press any key|\[y/n\]|enter password|continue\?", "SendTerminalInput", "Provide expected interactive input or run non-interactive flags."),
        ("browser_certificate_warning", 0.86, r"your connection is not private|certificate|net::err_cert", "GetBrowserDomText", "Fix local cert/trust setup or bypass in non-production environments."),
        ("cloudflare_tunnel_disconnected", 0.88, r"cloudflare|cloudflared.*(disconnected|error)", "TailFile", "Restart tunnel and inspect tunnel logs/credentials."),
        ("app_not_responding", 0.87, r"not responding|application hang|hung", "AppHealthCheck", "Restart or refocus the target app before additional actions."),
        ("mfa_waiting_human", 0.85, r"verification code|mfa|two-factor|authenticator", "HumanHandoff", "Request human completion of MFA before resuming automation."),
    ]

    issues: list[dict[str, Any]] = []
    for issue_type, confidence, pattern, next_tool, fix in checks:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        evidence = match.group(0)
        issues.append(_issue(issue_type, confidence, evidence, next_tool, fix))

    # login_loop requires repeated auth terms to avoid too many false positives
    login_issue = next((item for item in issues if item["type"] == "login_loop"), None)
    if login_issue:
        auth_mentions = len(re.findall(r"login|sign in|authenticate", text, flags=re.IGNORECASE))
        if auth_mentions < 2:
            issues = [item for item in issues if item["type"] != "login_loop"]

    return {
        "issues": issues,
        "count": len(issues),
        "has_blockers": any(item["confidence"] >= 0.85 for item in issues),
    }
