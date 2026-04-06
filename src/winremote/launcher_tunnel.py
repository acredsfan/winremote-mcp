"""Cloudflare Tunnel subprocess lifecycle manager for the winremote tray launcher."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable
from urllib import parse, request
from urllib.error import HTTPError, URLError


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class TunnelState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    CONNECTED = "connected"
    STOPPING = "stopping"
    ERROR = "error"


_STARTABLE = {TunnelState.STOPPED, TunnelState.ERROR}

# Pattern cloudflared emits when a tunnel URL is ready (quick-tunnel mode)
_URL_PATTERN = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com", re.IGNORECASE)
# Named / token tunnel "connected" patterns
# cloudflared may emit either "connection ... registered" or "Registered tunnel connection"
_CONNECTED_PATTERN = re.compile(
    r"(connection .* registered|registered .* connection|tunnel .* connected)",
    re.IGNORECASE,
)
# Any "failed" or "error" pattern
_ERROR_PATTERN = re.compile(r"(failed|error|unable to|fatal)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass
class TunnelSettings:
    """Parameters for launching cloudflared."""

    # Path to the cloudflared binary. If None, we try PATH.
    cloudflared_path: str | None = None
    # Cloudflare Tunnel token (Zero Trust dashboard → Tunnels → Add a tunnel).
    # When set, cloudflared is launched as: cloudflared tunnel run --token <token>
    # This takes priority over config_path.
    api_token: str | None = None
    # Cloudflare account API token (Bearer token for Cloudflare REST API).
    # Used to auto-create a tunnel + DNS route when api_token is not provided.
    cloudflare_api_key: str | None = None
    cloudflare_account_id: str | None = None
    cloudflare_zone_id: str | None = None
    tunnel_dns_name: str | None = None
    tunnel_name: str = "winremote-mcp"
    # For named tunnel: path to the cloudflared config YAML
    config_path: Path | None = None
    # Target local URL that cloudflared will expose
    target_url: str = "http://127.0.0.1:8090"
    # Extra args appended to the cloudflared command
    extra_args: list[str] = field(default_factory=list)

    def resolve_binary(self) -> str | None:
        """Return the resolved cloudflared binary path or None if not found."""
        import shutil
        if self.cloudflared_path:
            p = Path(self.cloudflared_path)
            return str(p) if p.is_file() else None
        return shutil.which("cloudflared")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class TunnelManager:
    """Manages a cloudflared subprocess for exposing the local MCP server.

    Two cloudflared modes are supported:
    - **Quick tunnel** (no config): ``cloudflared tunnel --url <target>``
    - **Named tunnel** (with config YAML): ``cloudflared tunnel --config <path> run``

    Callbacks run on a background thread — UI consumers must marshal to the
    main thread if needed.
    """

    def __init__(
        self,
        settings: TunnelSettings,
        on_state_change: Callable[[TunnelState, str], None] | None = None,
        on_log_line: Callable[[str, bool], None] | None = None,
        start_timeout: float = 30.0,
    ) -> None:
        self.settings = settings
        self.on_state_change = on_state_change
        self.on_log_line = on_log_line
        self.start_timeout = start_timeout

        self._process: subprocess.Popen | None = None
        self._state = TunnelState.STOPPED
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._public_url: str | None = None
        self._start_time: float | None = None

        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._managed_tunnel_token: str | None = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> TunnelState:
        return self._state

    @property
    def public_url(self) -> str | None:
        """Public URL exposed by cloudflared, if known."""
        return self._public_url

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def uptime_seconds(self) -> float | None:
        return (time.monotonic() - self._start_time) if self._start_time else None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the cloudflared tunnel. Returns *True* on successful launch."""
        with self._lock:
            if self._state not in _STARTABLE:
                # Self-heal: if the underlying process has already exited but
                # the state hasn't been updated yet (background thread race),
                # reset so the user can restart immediately.
                if self._process is not None and self._process.poll() is not None:
                    self._process = None
                    self._public_url = None
                    self._start_time = None
                    self._set_state(TunnelState.STOPPED, "Process exited; resetting for restart")
                else:
                    return False

        binary = self.settings.resolve_binary()
        if binary is None:
            with self._lock:
                self._set_state(
                    TunnelState.ERROR,
                    "cloudflared binary not found. Install it from "
                    "https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/",
                )
            return False

        cmd = self._build_command(binary)

        kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            proc = subprocess.Popen(cmd, **kwargs)
        except Exception as exc:
            with self._lock:
                self._set_state(TunnelState.ERROR, f"Failed to launch cloudflared: {exc}")
            return False

        with self._lock:
            self._process = proc
            self._public_url = None
            self._start_time = time.monotonic()
            self._stop_event.clear()
            self._set_state(TunnelState.STARTING, "cloudflared starting")

        self._stdout_thread = threading.Thread(
            target=self._read_stream, args=(proc.stdout, False), daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stream, args=(proc.stderr, True), daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

        # Watchdog: promote to ERROR if not connected within start_timeout
        threading.Thread(target=self._watchdog, daemon=True).start()
        return True

    def stop(self, timeout: float = 8.0) -> None:
        """Terminate the cloudflared subprocess."""
        with self._lock:
            if self._state in (TunnelState.STOPPED, TunnelState.STOPPING):
                return
            proc = self._process
            self._set_state(TunnelState.STOPPING, "Stopping tunnel")
            self._stop_event.set()

        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass

        with self._lock:
            self._process = None
            self._public_url = None
            self._start_time = None
            self._set_state(TunnelState.STOPPED, "Tunnel stopped")

    def update_settings(self, new_settings: TunnelSettings) -> None:
        """Replace settings; takes effect on the next start."""
        with self._lock:
            self.settings = new_settings

    # ------------------------------------------------------------------
    # Command builder
    # ------------------------------------------------------------------

    def _build_command(self, binary: str) -> list[str]:
        s = self.settings
        if s.api_token:
            # Token mode: authenticates via Cloudflare Zero Trust tunnel token
            cmd = [binary, "tunnel", "run", "--token", s.api_token]
        elif self._is_api_managed_mode(s):
            token = self._ensure_managed_tunnel_token()
            cmd = [binary, "tunnel", "run", "--token", token]
        elif s.config_path:
            # Named tunnel mode
            cmd = [binary, "tunnel", "--config", str(s.config_path), "run"]
        else:
            # Quick tunnel mode
            cmd = [binary, "tunnel", "--url", s.target_url]
        cmd.extend(s.extra_args)
        return cmd

    def _is_api_managed_mode(self, s: TunnelSettings) -> bool:
        return bool(
            (s.cloudflare_api_key or "").strip()
            and (s.cloudflare_account_id or "").strip()
            and (s.cloudflare_zone_id or "").strip()
            and (s.tunnel_dns_name or "").strip()
        )

    def _ensure_managed_tunnel_token(self) -> str:
        if self._managed_tunnel_token:
            return self._managed_tunnel_token

        s = self.settings
        tunnel_name = (s.tunnel_name or "winremote-mcp").strip() or "winremote-mcp"
        dns_name = (s.tunnel_dns_name or "").strip()

        if not dns_name:
            raise RuntimeError("Managed tunnel mode requires a DNS name")

        create = self._cf_api_request(
            "POST",
            f"/accounts/{s.cloudflare_account_id}/cfd_tunnel",
            {
                "name": tunnel_name,
                "config_src": "cloudflare",
            },
        )

        result = create.get("result") or {}
        tunnel_id = str(result.get("id") or "").strip()
        token = str(result.get("token") or "").strip()
        if not tunnel_id or not token:
            raise RuntimeError("Cloudflare did not return a tunnel id/token")

        cname_target = f"{tunnel_id}.cfargotunnel.com"
        self._upsert_dns_record(dns_name, cname_target)

        self._managed_tunnel_token = token
        return token

    def _upsert_dns_record(self, dns_name: str, cname_target: str) -> None:
        s = self.settings
        query = parse.urlencode({"type": "CNAME", "name": dns_name})
        existing = self._cf_api_request(
            "GET",
            f"/zones/{s.cloudflare_zone_id}/dns_records?{query}",
            None,
        )
        records = existing.get("result") or []

        if records:
            rec = records[0]
            rec_id = rec.get("id")
            if not rec_id:
                raise RuntimeError("Cloudflare DNS record missing id")
            self._cf_api_request(
                "PUT",
                f"/zones/{s.cloudflare_zone_id}/dns_records/{rec_id}",
                {
                    "type": "CNAME",
                    "name": dns_name,
                    "content": cname_target,
                    "proxied": True,
                    "ttl": 1,
                },
            )
            return

        self._cf_api_request(
            "POST",
            f"/zones/{s.cloudflare_zone_id}/dns_records",
            {
                "type": "CNAME",
                "name": dns_name,
                "content": cname_target,
                "proxied": True,
                "ttl": 1,
            },
        )

    def _cf_api_request(self, method: str, path: str, payload: dict | None) -> dict:
        s = self.settings
        token = (s.cloudflare_api_key or "").strip()
        if not token:
            raise RuntimeError("Missing Cloudflare API token")

        url = "https://api.cloudflare.com/client/v4" + path
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        req = request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

        try:
            with request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            raise RuntimeError(f"Cloudflare API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Cloudflare API request failed: {exc.reason}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Cloudflare API returned invalid JSON") from exc

        if not data.get("success", False):
            errors = data.get("errors") or []
            msg = "; ".join(
                str(e.get("message") or e.get("code") or e) for e in errors
            ) or "unknown Cloudflare API error"
            raise RuntimeError(msg)

        return data

    # ------------------------------------------------------------------
    # Background I/O threads
    # ------------------------------------------------------------------

    def _read_stream(self, stream, is_stderr: bool) -> None:
        try:
            for line in stream:
                stripped = line.rstrip("\n")
                if not stripped:
                    continue
                if self.on_log_line:
                    self.on_log_line(stripped, is_stderr)
                self._process_log_line(stripped)
        except Exception:
            pass
        finally:
            # Stream closed → process ended
            with self._lock:
                if self._state not in (TunnelState.STOPPED, TunnelState.STOPPING):
                    self._set_state(TunnelState.ERROR, "cloudflared process exited")

    def _process_log_line(self, line: str) -> None:
        """Infer tunnel state changes from log output."""
        url_match = _URL_PATTERN.search(line)
        if url_match:
            url = url_match.group(0)
            with self._lock:
                self._public_url = url
                if self._state != TunnelState.CONNECTED:
                    self._set_state(TunnelState.CONNECTED, f"Tunnel URL: {url}")
            return

        if _CONNECTED_PATTERN.search(line):
            with self._lock:
                if self._state != TunnelState.CONNECTED:
                    self._set_state(TunnelState.CONNECTED, "Tunnel connected")
            return

        if _ERROR_PATTERN.search(line):
            with self._lock:
                if self._state == TunnelState.STARTING:
                    # Only error during startup, not after it's been running
                    pass  # Let watchdog handle timeout, not every warning line

    def _watchdog(self) -> None:
        """Promote STARTING → ERROR if not connected within start_timeout seconds."""
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return
            with self._lock:
                if self._state not in (TunnelState.STARTING,):
                    return
            time.sleep(1)

        with self._lock:
            if self._state == TunnelState.STARTING:
                self._set_state(
                    TunnelState.ERROR,
                    f"Tunnel did not connect within {self.start_timeout:.0f}s",
                )

    # ------------------------------------------------------------------
    # Internal state helper
    # ------------------------------------------------------------------

    def _set_state(self, new_state: TunnelState, reason: str = "") -> None:
        self._state = new_state
        if self.on_state_change:
            try:
                self.on_state_change(new_state, reason)
            except Exception:
                pass
