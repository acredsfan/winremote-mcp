"""Best-effort VS Code bridge helpers (MVP, no extension required)."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from . import desktop, ocr, terminal_sessions


def _find_code_cli() -> str | None:
    explicit = os.environ.get("WINREMOTE_VSCODE_CLI", "").strip()
    if explicit:
        candidate = Path(explicit)
        if candidate.exists():
            return str(candidate)

    for name in ("code", "code-insiders", "codium"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def _run_code_cli(args: list[str], *, timeout: float = 8.0) -> dict[str, Any]:
    cli = _find_code_cli()
    if not cli:
        return {
            "supported": False,
            "reason": "VS Code CLI not found (set WINREMOTE_VSCODE_CLI or install `code` shell command).",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "command": [],
        }

    command = [cli, *args]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return {
            "supported": True,
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
            "exit_code": int(result.returncode),
            "command": command,
        }
    except Exception as e:
        return {
            "supported": False,
            "reason": f"VS Code CLI execution failed: {e}",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "command": command,
        }


def _is_vscode_window(window: desktop.WindowInfo) -> bool:
    title = str(window.title or "").lower()
    process_name = str(window.process_name or "").lower()
    if "visual studio code" in title:
        return True
    return process_name in {
        "code.exe",
        "code - insiders.exe",
        "codium.exe",
    }


def _extract_active_file_from_title(title: str) -> dict[str, Any]:
    raw = str(title or "").strip()
    if not raw:
        return {"raw_title": raw, "file_name": None, "workspace": None}

    # Typical examples:
    # - "main.py - myproj - Visual Studio Code"
    # - "README.md — myproj - Visual Studio Code"
    # - "Visual Studio Code"
    core = raw
    marker = " - visual studio code"
    lower = raw.lower()
    idx = lower.rfind(marker)
    if idx >= 0:
        core = raw[:idx].strip(" -")

    if not core or core.lower() == "visual studio code":
        return {"raw_title": raw, "file_name": None, "workspace": None}

    workspace = None
    file_name = core
    if " - " in core:
        first, second = core.split(" - ", 1)
        file_name = first.strip()
        workspace = second.strip() or None
    elif " — " in core:
        first, second = core.split(" — ", 1)
        file_name = first.strip()
        workspace = second.strip() or None

    return {
        "raw_title": raw,
        "file_name": file_name or None,
        "workspace": workspace,
    }


def list_vscode_windows() -> list[dict[str, Any]]:
    windows = desktop.enumerate_windows()
    result: list[dict[str, Any]] = []
    for window in windows:
        if not _is_vscode_window(window):
            continue
        result.append(
            {
                "window_id": f"hwnd:{window.handle}",
                "title": window.title,
                "process_name": window.process_name,
                "pid": window.pid,
                "monitor_id": window.monitor_id,
                "bounds": {
                    "left": window.rect[0],
                    "top": window.rect[1],
                    "right": window.rect[2],
                    "bottom": window.rect[3],
                },
                "is_visible": bool(window.visible),
            }
        )
    return result


def get_active_file() -> dict[str, Any]:
    windows = list_vscode_windows()
    if not windows:
        return {
            "supported": False,
            "reason": "No visible VS Code windows found",
            "active_file": None,
        }

    active = windows[0]
    extracted = _extract_active_file_from_title(str(active.get("title") or ""))
    payload = {
        "supported": True,
        "active_window": active,
        "active_file": {
            "file_name": extracted.get("file_name"),
            "workspace": extracted.get("workspace"),
            "path": None,
            "line": None,
            "column": None,
        },
    }

    status_payload = _run_code_cli(["--status"], timeout=4.0)
    payload["cli_status"] = {
        "supported": status_payload.get("supported", False),
        "exit_code": status_payload.get("exit_code"),
    }
    return payload


def list_open_files() -> dict[str, Any]:
    windows = list_vscode_windows()
    if not windows:
        return {
            "supported": False,
            "reason": "No visible VS Code windows found",
            "open_files": [],
        }

    seen: set[str] = set()
    open_files: list[dict[str, Any]] = []
    for window in windows:
        extracted = _extract_active_file_from_title(str(window.get("title") or ""))
        file_name = str(extracted.get("file_name") or "").strip()
        if not file_name or file_name in seen:
            continue
        seen.add(file_name)
        open_files.append(
            {
                "file_name": file_name,
                "workspace": extracted.get("workspace"),
                "path": None,
                "source": "window-title",
            }
        )

    return {
        "supported": True,
        "count": len(open_files),
        "open_files": open_files,
    }


def read_problems_panel(*, max_lines: int = 200) -> dict[str, Any]:
    text = ""
    try:
        text = ocr.run_ocr()
    except Exception as e:
        return {
            "supported": False,
            "reason": f"Problems panel OCR failed: {e}",
            "items": [],
            "text_excerpt": "",
        }

    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    items: list[dict[str, Any]] = []
    severity_re = re.compile(r"\b(error|warning|info|hint)\b", re.IGNORECASE)
    for line in lines[: max(10, int(max_lines))]:
        hit = severity_re.search(line)
        if not hit:
            continue
        items.append(
            {
                "severity": hit.group(1).lower(),
                "message": line,
                "source": "ocr",
            }
        )

    return {
        "supported": True,
        "item_count": len(items),
        "items": items,
        "text_excerpt": "\n".join(lines[:40]),
    }


def get_diagnostics() -> dict[str, Any]:
    problems = read_problems_panel(max_lines=300)
    return {
        "supported": bool(problems.get("supported")),
        "source": "problems-panel-ocr",
        "diagnostics": problems.get("items", []),
        "diagnostic_count": int(problems.get("item_count") or 0),
        "text_excerpt": problems.get("text_excerpt", ""),
    }


def read_terminal(*, lines: int = 200) -> dict[str, Any]:
    try:
        sessions = terminal_sessions.list_terminal_sessions()
    except Exception:
        sessions = []

    if sessions:
        terminal_id = str((sessions[0] or {}).get("terminal_id") or "")
        if terminal_id:
            try:
                payload = terminal_sessions.read_terminal_output(terminal_id, lines=max(10, int(lines)))
                return {
                    "supported": True,
                    "source": "controlled-terminal",
                    "terminal_id": terminal_id,
                    "output": payload.get("output", ""),
                    "line_count": payload.get("line_count"),
                }
            except Exception:
                pass

    try:
        text = ocr.run_ocr()
        excerpt = "\n".join(str(text or "").splitlines()[: max(20, int(lines))])
        return {
            "supported": True,
            "source": "ocr-fallback",
            "terminal_id": None,
            "output": excerpt,
            "line_count": len(excerpt.splitlines()) if excerpt else 0,
        }
    except Exception as e:
        return {
            "supported": False,
            "source": "none",
            "reason": f"Unable to read terminal output: {e}",
            "output": "",
            "line_count": 0,
        }


def open_file(path: str, *, line: int | None = None) -> dict[str, Any]:
    file_path = Path(path).expanduser()
    target = str(file_path)
    args = ["--reuse-window"]
    if line is not None and int(line) > 0:
        args.extend(["--goto", f"{target}:{int(line)}"])
    else:
        args.append(target)

    run = _run_code_cli(args)
    return {
        "supported": bool(run.get("supported")),
        "path": target,
        "line": int(line) if line is not None and int(line) > 0 else None,
        "exists": file_path.exists(),
        "exit_code": run.get("exit_code"),
        "stderr": run.get("stderr"),
        "stdout": run.get("stdout"),
        "command": run.get("command"),
        "reason": run.get("reason"),
    }


def run_command(command_id: str) -> dict[str, Any]:
    cmd = str(command_id or "").strip()
    if not cmd:
        raise ValueError("command_id is required")

    run = _run_code_cli(["--reuse-window", "--command", cmd])
    return {
        "supported": bool(run.get("supported")),
        "command_id": cmd,
        "exit_code": run.get("exit_code"),
        "stderr": run.get("stderr"),
        "stdout": run.get("stdout"),
        "command": run.get("command"),
        "reason": run.get("reason"),
    }
