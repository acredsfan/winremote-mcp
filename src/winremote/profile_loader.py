"""Profile TOML loader for agent-specific tool policies."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


def _profiles_dir() -> Path:
    return Path(__file__).resolve().parent / "profiles"


def profile_name_to_filename(profile_name: str) -> str:
    value = str(profile_name or "").strip().lower()
    return f"{value}.toml"


def load_profile_toml(profile_name: str, *, profiles_dir: Path | None = None) -> dict[str, Any]:
    folder = profiles_dir or _profiles_dir()
    path = folder / profile_name_to_filename(profile_name)
    if not path.exists():
        raise FileNotFoundError(f"Profile TOML not found: {profile_name}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid profile TOML payload for {profile_name}")
    return data


def list_profile_tomls(*, profiles_dir: Path | None = None) -> list[str]:
    folder = profiles_dir or _profiles_dir()
    if not folder.exists():
        return []
    return sorted(path.stem for path in folder.glob("*.toml"))
