import json

import pytest


def test_known_issue_and_capability_tools(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(main.desktop, "HAS_WIN32", False)
    monkeypatch.setattr(main.known_issues, "detect_known_issues", lambda **kwargs: {"issues": [{"type": "port_in_use"}], "count": 1, "has_blockers": True})
    monkeypatch.setattr(main.agent_capabilities, "get_agent_capability_guide", lambda client_name=None: {"client": client_name or "unknown", "recommended_profile": "copilot-cli"})

    issues = json.loads(main.DetectKnownIssues(target_app="VS Code", include_screenshot_analysis=False, include_terminal_analysis=False, include_browser_analysis=False))
    guide = json.loads(main.GetAgentCapabilityGuide("github-copilot-cli"))

    assert issues["count"] == 1
    assert issues["issues"][0]["type"] == "port_in_use"
    assert guide["recommended_profile"] == "copilot-cli"
