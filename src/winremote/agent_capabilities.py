"""Agent capability negotiation guide for WinRemote clients."""

from __future__ import annotations

from typing import Any


_DEFAULT_GUIDE = {
    "supports_images": True,
    "best_observation_tool": "ObserveScreen",
    "best_terminal_tool": "ReadTerminalOutput",
    "recommended_profile": "chatgpt-full",
    "avoid_tools": ["Snapshot"],
    "notes": [
        "Prefer semantic UI tools before coordinate clicks.",
        "Use controlled terminal tools for command output when possible.",
    ],
}


_CLIENT_MAP: dict[str, dict[str, Any]] = {
    "github-copilot-cli": {
        "supports_images": False,
        "best_observation_tool": "ObserveScreen",
        "best_terminal_tool": "ReadTerminalOutput",
        "recommended_profile": "copilot-cli",
        "avoid_tools": ["Snapshot"],
        "notes": ["Prefer concise JSON outputs.", "Use terminal/session tools before screenshot loops."],
    },
    "github-copilot-chat": {
        "supports_images": True,
        "best_observation_tool": "ObserveScreen",
        "best_terminal_tool": "ReadTerminalOutput",
        "recommended_profile": "copilot-chat",
        "avoid_tools": [],
        "notes": ["Use semantic desktop tools first.", "Use Snapshot only when visual evidence is required."],
    },
    "chatgpt": {
        "supports_images": True,
        "best_observation_tool": "ObserveScreen",
        "best_terminal_tool": "ReadTerminalOutput",
        "recommended_profile": "chatgpt-full",
        "avoid_tools": [],
        "notes": ["Prefer semantic action tools and bounded loops."],
    },
    "claude-code": {
        "supports_images": True,
        "best_observation_tool": "ObserveScreen",
        "best_terminal_tool": "ReadTerminalOutput",
        "recommended_profile": "claude-code",
        "avoid_tools": [],
        "notes": ["Use verification-heavy workflows and explicit confirmations for risky actions."],
    },
}


def get_agent_capability_guide(client_name: str | None = None) -> dict[str, Any]:
    key = str(client_name or "").strip().lower()
    payload = dict(_DEFAULT_GUIDE)
    payload.update(_CLIENT_MAP.get(key, {}))
    return {
        "client": key or "unknown",
        "supports_images": payload["supports_images"],
        "best_observation_tool": payload["best_observation_tool"],
        "best_terminal_tool": payload["best_terminal_tool"],
        "recommended_profile": payload["recommended_profile"],
        "avoid_tools": payload["avoid_tools"],
        "notes": payload["notes"],
    }
