from winremote import agent_capabilities


def test_agent_capability_guide_for_copilot_cli():
    payload = agent_capabilities.get_agent_capability_guide("github-copilot-cli")
    assert payload["recommended_profile"] == "copilot-cli"
    assert payload["best_terminal_tool"] == "ReadTerminalOutput"


def test_agent_capability_guide_defaults_unknown_client():
    payload = agent_capabilities.get_agent_capability_guide("unknown-client")
    assert payload["recommended_profile"] == "chatgpt-full"
