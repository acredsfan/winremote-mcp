"""Configuration loading and merge utilities for winremote-mcp."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8090
    auth_key: str | None = None
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    profile: str = "default"


def _default_localappdata_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "WinRemoteMCP"
    return Path.home() / ".local" / "share" / "WinRemoteMCP"


@dataclass
class SecurityConfig:
    ip_allowlist: list[str] = field(default_factory=list)
    enable_tier3: bool = False
    disable_tier2: bool = False
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None


@dataclass
class PathsConfig:
    root_dir: str = field(default_factory=lambda: str(_default_localappdata_root()))
    bin_dir: str = field(default_factory=lambda: str(_default_localappdata_root() / "bin"))
    sessions_dir: str = field(default_factory=lambda: str(_default_localappdata_root() / "sessions"))
    recordings_dir: str = field(default_factory=lambda: str(_default_localappdata_root() / "recordings"))
    selectors_dir: str = field(default_factory=lambda: str(_default_localappdata_root() / "selectors"))


@dataclass
class RedactionConfig:
    enabled: bool = True
    blur_screenshots: bool = True
    redact_event_text: bool = True
    redact_clipboard: bool = True
    patterns: list[str] = field(
        default_factory=lambda: [
            r"sk-[A-Za-z0-9_-]{20,}",
            r"(?i)bearer\s+[A-Za-z0-9._-]{12,}",
            r"AKIA[0-9A-Z]{16}",
            r"xox[baprs]-[A-Za-z0-9-]+",
            r"\b\d{3}-\d{2}-\d{4}\b",
            r"\b(?:\d[ -]*?){13,19}\b",
        ]
    )


@dataclass
class SafetyConfig:
    allowed_apps: list[str] = field(default_factory=list)
    denied_apps: list[str] = field(default_factory=list)
    confirmation_required_patterns: list[str] = field(
        default_factory=lambda: [
            "submit",
            "buy",
            "send",
            "delete",
            "pay",
            "confirm",
            "agree",
            "publish",
            "upload",
        ]
    )
    redact_password_fields: bool = True


@dataclass
class ToolsConfig:
    enable: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


@dataclass
class WinRemoteConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    redaction: RedactionConfig = field(default_factory=RedactionConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    source_path: Path | None = None


def discover_config_path(explicit_path: str | None) -> Path | None:
    """Find config path using precedence: explicit > cwd > ~/.config."""
    if explicit_path:
        path = Path(explicit_path).expanduser()
        return path

    cwd_path = Path.cwd() / "winremote.toml"
    if cwd_path.exists():
        return cwd_path

    user_path = Path("~/.config/winremote/winremote.toml").expanduser()
    if user_path.exists():
        return user_path
    return None


def _list_of_strings(raw: object, key: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(i, str) for i in raw):
        raise ValueError(f"{key} must be an array of strings")
    return raw


def _choice_string(raw: object, key: str, *, allowed: set[str]) -> str:
    value = str(raw).strip().lower()
    if value not in allowed:
        raise ValueError(f"{key} must be one of: {', '.join(sorted(allowed))}")
    return value


def load_config(path: Path | None) -> WinRemoteConfig:
    """Load and validate TOML config file. Returns defaults when path is None."""
    cfg = WinRemoteConfig()
    if path is None:
        return cfg

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    data = tomllib.loads(path.read_text(encoding="utf-8"))

    server = data.get("server", {})
    security = data.get("security", {})
    paths = data.get("paths", {})
    redaction = data.get("redaction", {})
    safety = data.get("safety", {})
    tools = data.get("tools", {})

    if "host" in server:
        cfg.server.host = str(server["host"])
    if "port" in server:
        cfg.server.port = int(server["port"])
    if "auth_key" in server:
        cfg.server.auth_key = str(server["auth_key"])
    if "ssl_certfile" in server:
        cfg.server.ssl_certfile = str(server["ssl_certfile"]) or None
    if "ssl_keyfile" in server:
        cfg.server.ssl_keyfile = str(server["ssl_keyfile"]) or None
    if "profile" in server:
        cfg.server.profile = _choice_string(
            server["profile"],
            "server.profile",
            allowed={"default", "chatgpt", "copilot", "copilot-cli", "excel", "claude"},
        )

    if "ip_allowlist" in security:
        cfg.security.ip_allowlist = _list_of_strings(security["ip_allowlist"], "security.ip_allowlist")
    if "enable_tier3" in security:
        cfg.security.enable_tier3 = bool(security["enable_tier3"])
    if "disable_tier2" in security:
        cfg.security.disable_tier2 = bool(security["disable_tier2"])
    if "oauth_client_id" in security:
        cfg.security.oauth_client_id = str(security["oauth_client_id"]) or None
    if "oauth_client_secret" in security:
        cfg.security.oauth_client_secret = str(security["oauth_client_secret"]) or None

    if "root_dir" in paths:
        cfg.paths.root_dir = str(paths["root_dir"])
    if "bin_dir" in paths:
        cfg.paths.bin_dir = str(paths["bin_dir"])
    if "sessions_dir" in paths:
        cfg.paths.sessions_dir = str(paths["sessions_dir"])
    if "recordings_dir" in paths:
        cfg.paths.recordings_dir = str(paths["recordings_dir"])
    if "selectors_dir" in paths:
        cfg.paths.selectors_dir = str(paths["selectors_dir"])

    if "enabled" in redaction:
        cfg.redaction.enabled = bool(redaction["enabled"])
    if "blur_screenshots" in redaction:
        cfg.redaction.blur_screenshots = bool(redaction["blur_screenshots"])
    if "redact_event_text" in redaction:
        cfg.redaction.redact_event_text = bool(redaction["redact_event_text"])
    if "redact_clipboard" in redaction:
        cfg.redaction.redact_clipboard = bool(redaction["redact_clipboard"])
    if "patterns" in redaction:
        cfg.redaction.patterns = _list_of_strings(redaction["patterns"], "redaction.patterns")

    if "allowed_apps" in safety:
        cfg.safety.allowed_apps = _list_of_strings(safety["allowed_apps"], "safety.allowed_apps")
    if "denied_apps" in safety:
        cfg.safety.denied_apps = _list_of_strings(safety["denied_apps"], "safety.denied_apps")
    if "confirmation_required_patterns" in safety:
        cfg.safety.confirmation_required_patterns = _list_of_strings(
            safety["confirmation_required_patterns"], "safety.confirmation_required_patterns"
        )
    if "redact_password_fields" in safety:
        cfg.safety.redact_password_fields = bool(safety["redact_password_fields"])

    if "enable" in tools:
        cfg.tools.enable = _list_of_strings(tools["enable"], "tools.enable")
    if "exclude" in tools:
        cfg.tools.exclude = _list_of_strings(tools["exclude"], "tools.exclude")

    cfg.source_path = path
    return cfg
