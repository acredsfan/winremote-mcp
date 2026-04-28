"""Local project context collection utilities for coding-agent workflows."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any


def _safe_run(args: list[str], cwd: Path, timeout: float = 5.0) -> str:
    try:
        result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return (result.stderr or result.stdout or "").strip()
        return (result.stdout or "").strip()
    except Exception as e:
        return f"error: {e}"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _collect_files(root: Path, max_files: int) -> list[str]:
    out: list[str] = []
    for path in root.rglob("*"):
        if len(out) >= max_files:
            break
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if any(part in {".git", ".venv", "node_modules", "__pycache__"} for part in rel.parts):
            continue
        out.append(str(rel).replace("\\", "/"))
    return out


def collect_project_context(
    *,
    root: str,
    max_files: int = 200,
    include_git_status: bool = True,
    include_package_scripts: bool = True,
    include_recent_errors: bool = True,
) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise ValueError(f"root is not a directory: {root}")

    files = _collect_files(root_path, max(10, int(max_files)))

    git_info: dict[str, Any] = {}
    if include_git_status:
        branch = _safe_run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root_path)
        status = _safe_run(["git", "status", "--short"], root_path)
        git_info = {
            "branch": branch,
            "status_short": status.splitlines()[:200],
        }

    package_scripts: dict[str, str] = {}
    detected_package_manager = None
    if include_package_scripts:
        package_json = _read_json(root_path / "package.json")
        if package_json and isinstance(package_json.get("scripts"), dict):
            package_scripts = {str(k): str(v) for k, v in package_json["scripts"].items()}
            detected_package_manager = "npm"
        elif (root_path / "pyproject.toml").exists():
            detected_package_manager = "python"
        elif (root_path / "requirements.txt").exists():
            detected_package_manager = "python"

    recent_errors: list[str] = []
    if include_recent_errors:
        for rel in [".pytest_cache/README.md", "pytest.log", "logs/latest.log"]:
            path = root_path / rel
            if path.exists() and path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore")
                if text:
                    recent_errors.extend(text.splitlines()[-40:])

    return {
        "root": str(root_path),
        "file_count": len(files),
        "files": files,
        "git": git_info,
        "package_manager": detected_package_manager,
        "package_scripts": package_scripts,
        "recent_errors": recent_errors[:200],
    }
