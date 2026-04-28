"""Browser debug bridge helpers using Chrome DevTools remote debugging endpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any
from urllib.request import urlopen
from uuid import uuid4


_SESSIONS: dict[str, dict[str, Any]] = {}


def _debug_root() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "WinRemoteMCP" / "browser-debug"
    return Path.home() / ".local" / "share" / "WinRemoteMCP" / "browser-debug"


def _find_browser_executable(browser: str) -> str:
    normalized = str(browser or "edge").strip().lower()
    candidates: list[str]
    if normalized == "chrome":
        candidates = [
            os.environ.get("WINREMOTE_CHROME_PATH", ""),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        candidates = [
            os.environ.get("WINREMOTE_EDGE_PATH", ""),
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f"{normalized} executable not found")


def _json_get(url: str, *, timeout: float = 3.0) -> Any:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="ignore"))


def _get_tab(session_id: str, tab_id: str) -> dict[str, Any]:
    tabs_payload = list_browser_tabs(session_id)
    tabs = tabs_payload.get("tabs") or []
    target = next((tab for tab in tabs if str(tab.get("id")) == str(tab_id)), None)
    if target is None:
        raise ValueError(f"tab_id not found in active debug session: {tab_id}")
    return target


def _create_ws(url: str, *, timeout: float = 2.0):
    try:
        import websocket  # type: ignore
    except Exception as e:  # pragma: no cover - optional dependency branch
        raise RuntimeError(
            "websocket-client is required for CDP WebSocket features. "
            "Install with: pip install websocket-client"
        ) from e

    ws = websocket.create_connection(url, timeout=timeout)
    ws.settimeout(timeout)
    return ws


def _ws_recv_json(ws) -> dict[str, Any] | None:
    try:
        raw = ws.recv()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _cdp_command(
    ws,
    *,
    method: str,
    params: dict[str, Any] | None = None,
    message_id: int,
    drain_events: list[dict[str, Any]] | None = None,
    timeout_seconds: float = 2.0,
) -> tuple[int, dict[str, Any] | None]:
    payload = {"id": int(message_id), "method": method}
    if params:
        payload["params"] = params
    ws.send(json.dumps(payload))

    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while time.monotonic() < deadline:
        msg = _ws_recv_json(ws)
        if not msg:
            continue
        if "id" in msg and int(msg.get("id") or 0) == int(message_id):
            return message_id + 1, msg
        if drain_events is not None and "method" in msg:
            drain_events.append(msg)
    return message_id + 1, None


def _collect_cdp_events(
    ws_url: str,
    *,
    enable_methods: list[tuple[str, dict[str, Any] | None]],
    event_methods: set[str],
    collect_seconds: float = 0.75,
) -> tuple[bool, list[dict[str, Any]], str | None]:
    ws = None
    try:
        ws = _create_ws(ws_url, timeout=2.0)
        message_id = 1
        drained: list[dict[str, Any]] = []
        for method, params in enable_methods:
            message_id, _ = _cdp_command(
                ws,
                method=method,
                params=params,
                message_id=message_id,
                drain_events=drained,
                timeout_seconds=2.0,
            )

        deadline = time.monotonic() + max(0.1, float(collect_seconds))
        events: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            msg = _ws_recv_json(ws)
            if not msg:
                continue
            method = str(msg.get("method") or "")
            if method in event_methods:
                events.append(msg)
        return True, events, None
    except Exception as e:
        return False, [], str(e)
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _cdp_eval(ws_url: str, expression: str, *, return_by_value: bool = True) -> tuple[bool, Any, str | None]:
    ws = None
    try:
        ws = _create_ws(ws_url, timeout=2.0)
        message_id = 1
        message_id, _ = _cdp_command(ws, method="Runtime.enable", message_id=message_id, timeout_seconds=2.0)
        message_id, result = _cdp_command(
            ws,
            method="Runtime.evaluate",
            params={"expression": expression, "returnByValue": bool(return_by_value)},
            message_id=message_id,
            timeout_seconds=2.0,
        )
        if not result:
            return False, None, "No CDP response"
        if "error" in result:
            return False, None, str(result.get("error"))
        payload = (((result.get("result") or {}).get("result")) or {})
        return True, payload.get("value"), None
    except Exception as e:
        return False, None, str(e)
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def launch_debug_browser(
    *,
    browser: str = "edge",
    url: str | None = None,
    user_data_dir: str | None = None,
    remote_debugging_port: int = 9222,
) -> dict[str, Any]:
    executable = _find_browser_executable(browser)
    session_id = f"browser_{uuid4().hex[:12]}"

    if user_data_dir:
        profile_dir = Path(user_data_dir)
    else:
        profile_dir = _debug_root() / session_id / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    command = [
        executable,
        f"--remote-debugging-port={int(remote_debugging_port)}",
        f"--user-data-dir={str(profile_dir)}",
    ]
    if url:
        command.append(str(url))

    process = subprocess.Popen(command)
    time.sleep(0.2)

    payload = {
        "session_id": session_id,
        "browser": str(browser or "edge").strip().lower(),
        "port": int(remote_debugging_port),
        "user_data_dir": str(profile_dir),
        "pid": int(getattr(process, "pid", 0) or 0),
        "debug_url": f"http://127.0.0.1:{int(remote_debugging_port)}",
        "cdp_json_version": f"http://127.0.0.1:{int(remote_debugging_port)}/json/version",
        "cdp_json_list": f"http://127.0.0.1:{int(remote_debugging_port)}/json/list",
    }
    _SESSIONS[session_id] = {"process": process, **payload}
    return payload


def list_browser_tabs(session_id: str) -> dict[str, Any]:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise ValueError(f"Unknown browser debug session: {session_id}")

    tabs = _json_get(f"http://127.0.0.1:{session['port']}/json/list")
    if not isinstance(tabs, list):
        tabs = []

    normalized = []
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        normalized.append(
            {
                "id": tab.get("id"),
                "title": tab.get("title"),
                "url": tab.get("url"),
                "type": tab.get("type"),
                "webSocketDebuggerUrl": tab.get("webSocketDebuggerUrl"),
            }
        )

    return {"session_id": session_id, "count": len(normalized), "tabs": normalized}


def get_browser_console_logs(session_id: str, tab_id: str, level: str | None = None) -> dict[str, Any]:
    normalized_level = str(level or "").strip().lower()
    tab = _get_tab(session_id, tab_id)
    ws_url = str(tab.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        return {
            "session_id": session_id,
            "tab_id": tab_id,
            "supported": False,
            "reason": "Tab does not expose webSocketDebuggerUrl",
            "logs": [],
        }

    ok, events, err = _collect_cdp_events(
        ws_url,
        enable_methods=[("Runtime.enable", None), ("Log.enable", None)],
        event_methods={"Runtime.consoleAPICalled", "Log.entryAdded"},
        collect_seconds=0.9,
    )
    if not ok:
        return {
            "session_id": session_id,
            "tab_id": tab_id,
            "supported": False,
            "reason": f"CDP console capture unavailable: {err}",
            "logs": [],
        }

    logs: list[dict[str, Any]] = []
    for event in events:
        method = str(event.get("method") or "")
        params = event.get("params") or {}
        if method == "Runtime.consoleAPICalled":
            args = params.get("args") or []
            rendered = []
            for arg in args:
                if not isinstance(arg, dict):
                    continue
                rendered.append(str(arg.get("value") or arg.get("description") or ""))
            entry = {
                "level": str(params.get("type") or "log").lower(),
                "message": " ".join(part for part in rendered if part).strip(),
                "source": "Runtime.consoleAPICalled",
                "timestamp": params.get("timestamp"),
            }
            logs.append(entry)
        elif method == "Log.entryAdded":
            entry_data = params.get("entry") or {}
            entry = {
                "level": str(entry_data.get("level") or "log").lower(),
                "message": str(entry_data.get("text") or ""),
                "source": "Log.entryAdded",
                "timestamp": entry_data.get("timestamp"),
                "url": entry_data.get("url"),
            }
            logs.append(entry)

    if normalized_level:
        logs = [item for item in logs if str(item.get("level") or "") == normalized_level]

    return {
        "session_id": session_id,
        "tab_id": tab_id,
        "supported": True,
        "reason": None,
        "log_count": len(logs),
        "logs": logs[:500],
    }


def get_browser_network_requests(session_id: str, tab_id: str) -> dict[str, Any]:
    tab = _get_tab(session_id, tab_id)
    ws_url = str(tab.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        return {
            "session_id": session_id,
            "tab_id": tab_id,
            "supported": False,
            "reason": "Tab does not expose webSocketDebuggerUrl",
            "requests": [],
        }

    ok, events, err = _collect_cdp_events(
        ws_url,
        enable_methods=[("Network.enable", None), ("Page.enable", None)],
        event_methods={"Network.requestWillBeSent", "Network.loadingFailed", "Network.responseReceived"},
        collect_seconds=0.9,
    )
    if not ok:
        return {
            "session_id": session_id,
            "tab_id": tab_id,
            "supported": False,
            "reason": f"CDP network capture unavailable: {err}",
            "requests": [],
        }

    requests: list[dict[str, Any]] = []
    for event in events:
        method = str(event.get("method") or "")
        params = event.get("params") or {}
        if method == "Network.requestWillBeSent":
            req = params.get("request") or {}
            requests.append(
                {
                    "event": method,
                    "requestId": params.get("requestId"),
                    "url": req.get("url"),
                    "method": req.get("method"),
                    "timestamp": params.get("timestamp"),
                }
            )
        elif method == "Network.responseReceived":
            res = params.get("response") or {}
            requests.append(
                {
                    "event": method,
                    "requestId": params.get("requestId"),
                    "url": res.get("url"),
                    "status": res.get("status"),
                    "mimeType": res.get("mimeType"),
                    "timestamp": params.get("timestamp"),
                }
            )
        elif method == "Network.loadingFailed":
            requests.append(
                {
                    "event": method,
                    "requestId": params.get("requestId"),
                    "errorText": params.get("errorText"),
                    "canceled": params.get("canceled"),
                    "timestamp": params.get("timestamp"),
                }
            )

    return {
        "session_id": session_id,
        "tab_id": tab_id,
        "supported": True,
        "reason": None,
        "request_count": len(requests),
        "requests": requests[:1000],
    }


def get_browser_dom_text(session_id: str, tab_id: str) -> dict[str, Any]:
    tab = _get_tab(session_id, tab_id)
    ws_url = str(tab.get("webSocketDebuggerUrl") or "")

    if ws_url:
        ok, value, err = _cdp_eval(ws_url, "(document.body && document.body.innerText) ? document.body.innerText : ''")
        if ok:
            text = str(value or "")
            return {
                "session_id": session_id,
                "tab_id": tab_id,
                "supported": True,
                "source": "cdp-runtime-evaluate",
                "url": tab.get("url"),
                "text": text[:12000],
            }
        fallback_reason = err
    else:
        fallback_reason = "Tab does not expose webSocketDebuggerUrl"

    url = str(tab.get("url") or "")
    if not url.startswith("http"):
        return {
            "session_id": session_id,
            "tab_id": tab_id,
            "supported": False,
            "reason": f"DOM text extraction unavailable: {fallback_reason}",
            "text": "",
        }

    try:
        with urlopen(url, timeout=4.0) as response:
            html_content = response.read().decode("utf-8", errors="ignore")
        text = re.sub(r"<script[\\s\\S]*?</script>", " ", html_content, flags=re.IGNORECASE)
        text = re.sub(r"<style[\\s\\S]*?</style>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return {
            "session_id": session_id,
            "tab_id": tab_id,
            "supported": True,
            "source": "http_fetch_fallback",
            "reason": fallback_reason,
            "url": url,
            "text": text[:12000],
        }
    except Exception as e:
        return {
            "session_id": session_id,
            "tab_id": tab_id,
            "supported": False,
            "reason": f"DOM text fetch failed: {e}",
            "text": "",
        }


def click_dom_element(session_id: str, tab_id: str, selector: str) -> dict[str, Any]:
    tab = _get_tab(session_id, tab_id)
    ws_url = str(tab.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        return {
            "session_id": session_id,
            "tab_id": tab_id,
            "selector": selector,
            "supported": False,
            "reason": "Tab does not expose webSocketDebuggerUrl",
        }

    safe_selector = json.dumps(str(selector or ""))
    expression = (
        "(() => {"
        f"const el = document.querySelector({safe_selector});"
        "if (!el) return {ok:false, reason:'selector-not-found'};"
        "el.click();"
        "return {ok:true};"
        "})()"
    )
    ok, value, err = _cdp_eval(ws_url, expression)
    if not ok:
        return {
            "session_id": session_id,
            "tab_id": tab_id,
            "selector": selector,
            "supported": False,
            "reason": f"CDP DOM click failed: {err}",
        }

    result = value if isinstance(value, dict) else {"ok": bool(value)}
    return {
        "session_id": session_id,
        "tab_id": tab_id,
        "selector": selector,
        "supported": True,
        "clicked": bool(result.get("ok")),
        "reason": result.get("reason"),
    }
