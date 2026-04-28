from winremote import known_issues


def test_detect_known_issues_matches_port_and_auth():
    payload = known_issues.detect_known_issues(
        active_window_title="Visual Studio Code",
        terminal_output="Error: Address already in use: 8000",
        ocr_text="Unauthorized 401 token expired",
    )

    issue_types = {item["type"] for item in payload["issues"]}
    assert "port_in_use" in issue_types
    assert "authentication_expired" in issue_types


def test_detect_known_issues_returns_empty_when_no_signals():
    payload = known_issues.detect_known_issues(active_window_title="Editor", terminal_output="All good")
    assert payload["count"] == 0
