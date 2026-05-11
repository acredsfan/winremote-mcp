"""Server subprocess lifecycle manager for the winremote-mcp tray launcher."""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import psutil


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class ServerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"   # process alive but /health not responding
    STOPPING = "stopping"
    ERROR = "error"


# States from which Start is a valid action
_STARTABLE = {ServerState.STOPPED, ServerState.ERROR}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

VALID_PROFILES = ("default", "chatgpt", "copilot", "copilot-cli", "claude", "excel")

PROFILE_DESCRIPTIONS: dict[str, str] = {
    "default": "Tier 1 + Tier 2 tools (all observation + interaction)",
    "chatgpt": "Full ChatGPT set — observation, UI, Shell, File I/O, Roblox",
    "copilot": "Copilot Chat set — ChatGPT minus Shell/File (VS Code provides those)",
    "copilot-cli": "Copilot CLI set — Copilot profile with conservative system actions",
    "claude": "Claude set — same as ChatGPT",
    "excel": "Excel set — observation, UI interaction, clipboard, shell",
}


@dataclass
class ServerSettings:
    """Launch parameters for the winremote-mcp server."""

    config_path: Path | None = None
    profile: str = "default"
    host: str = "127.0.0.1"
    port: int = 8090
    auth_key: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    transport: str = "streamable-http"
    enable_tier3: bool = False
    disable_tier2: bool = False
    ip_allowlist: list[str] = field(default_factory=list)
    selected_tools: list[str] = field(default_factory=list)
    excluded_tools: list[str] = field(default_factory=list)
    python_executable: str = field(default_factory=lambda: sys.executable)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ServerManager:
    """Manages the winremote-mcp server as a background subprocess.

    Callbacks run on a background thread — UI consumers must marshal to the
    main thread if needed (e.g., via ``root.after()`` or a queue).
    """

    def __init__(
        self,
        settings: ServerSettings,
        on_state_change: Callable[[ServerState, str], None] | None = None,
        on_log_line: Callable[[str, bool], None] | None = None,
        poll_interval: float = 4.0,
        start_timeout: float = 30.0,
    ) -> None:
        self.settings = settings
        self.on_state_change = on_state_change
        self.on_log_line = on_log_line
        self.poll_interval = poll_interval
        self.start_timeout = start_timeout

        self._process: subprocess.Popen | None = None
        self._state = ServerState.STOPPED
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._start_time: float | None = None
        self._port_conflict_pid: int | None = None
        self._port_conflict_name: str | None = None
        self._runtime_disable_ssl = False
        self._ssl_retry_attempted = False

        # Background threads (daemon — exit with main process)
        self._poll_thread: threading.Thread | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> ServerState:
        return self._state

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def uptime_seconds(self) -> float | None:
        return (time.monotonic() - self._start_time) if self._start_time else None

    @property
    def port_conflict_pid(self) -> int | None:
        return self._port_conflict_pid

    @property
    def port_conflict_name(self) -> str | None:
        return self._port_conflict_name

    @property
    def has_port_conflict(self) -> bool:
        return self._port_conflict_pid is not None

    @property
    def port_conflict_summary(self) -> str:
        if not self._port_conflict_pid:
            return "–"
        proc_name = self._port_conflict_name or "unknown"
        return f"PID {self._port_conflict_pid} ({proc_name})"

    @property
    def base_url(self) -> str:
        s = self.settings
        scheme = "https" if (s.ssl_certfile and not self._runtime_disable_ssl) else "http"
        return f"{scheme}://{s.host}:{s.port}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the server subprocess. Returns *True* if started successfully."""
        with self._lock:
            if self._state not in _STARTABLE:
                return False
            self._port_conflict_pid = None
            self._port_conflict_name = None
            self._runtime_disable_ssl = False
            self._ssl_retry_attempted = False
            self._set_state(ServerState.STARTING, "Launching server process")

        conflict = self._find_port_conflict()
        if conflict is not None:
            pid, name = conflict
            with self._lock:
                self._port_conflict_pid = pid
                self._port_conflict_name = name
                self._set_state(
                    ServerState.ERROR,
                    f"Port {self.settings.port} already in use by PID {pid} ({name})",
                )
            return False

        proc, err = self._spawn_process()
        if proc is None:
            with self._lock:
                self._set_state(ServerState.ERROR, f"Failed to launch: {err}")
            return False

        with self._lock:
            self._process = proc
            self._start_time = time.monotonic()
            self._stop_event.clear()

        self._start_stream_threads(proc)
        self._poll_thread = threading.Thread(target=self._poll_health, daemon=True)

        self._poll_thread.start()
        return True

    def stop(self, timeout: float = 8.0) -> None:
        """Gracefully stop the server subprocess."""
        with self._lock:
            if self._state in (ServerState.STOPPED, ServerState.STOPPING):
                return
            proc = self._process
            self._set_state(ServerState.STOPPING, "Sending termination signal")
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
            self._start_time = None
            self._set_state(ServerState.STOPPED, "Server stopped")

    def restart(self) -> bool:
        """Stop then start the server with the current settings."""
        self.stop()
        return self.start()

    def stop_conflicting_process(self, timeout: float = 8.0) -> tuple[bool, str]:
        """Terminate the process currently occupying our configured port, if known."""
        pid = self._port_conflict_pid
        if not pid:
            return False, "No conflicting process recorded for this port."

        if pid == os.getpid():
            return False, "Refusing to terminate the launcher process itself."

        try:
            proc = psutil.Process(pid)
            proc_name = proc.name()
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

            self._port_conflict_pid = None
            self._port_conflict_name = None
            return True, f"Stopped conflicting process PID {pid} ({proc_name})."
        except psutil.NoSuchProcess:
            self._port_conflict_pid = None
            self._port_conflict_name = None
            return True, f"Conflicting process PID {pid} is no longer running."
        except psutil.AccessDenied:
            return False, f"Access denied while stopping PID {pid}."
        except Exception as exc:
            return False, f"Failed to stop conflicting process PID {pid}: {exc}"

    def update_settings(self, new_settings: ServerSettings) -> None:
        """Replace settings; takes effect on the next start."""
        with self._lock:
            self.settings = new_settings

    # ------------------------------------------------------------------
    # Command builder
    # ------------------------------------------------------------------

    def _build_command(self) -> list[str]:
        s = self.settings
        cmd = [
            s.python_executable, "-m", "winremote",
            "--transport", s.transport,
            "--host", s.host,
            "--port", str(s.port),
            "--profile", s.profile,
        ]
        if s.config_path:
            cmd += ["--config", str(s.config_path)]
        if s.auth_key:
            cmd += ["--auth-key", s.auth_key]
        if s.oauth_client_id:
            cmd += ["--oauth-client-id", s.oauth_client_id]
        if s.oauth_client_secret:
            cmd += ["--oauth-client-secret", s.oauth_client_secret]
        if s.ssl_certfile and not self._runtime_disable_ssl:
            cmd += ["--ssl-certfile", s.ssl_certfile]
        if s.ssl_keyfile and not self._runtime_disable_ssl:
            cmd += ["--ssl-keyfile", s.ssl_keyfile]
        if s.enable_tier3:
            cmd.append("--enable-tier3")
        if s.disable_tier2:
            cmd.append("--disable-tier2")
        if s.selected_tools:
            cmd += ["--tools", ",".join(s.selected_tools)]
        if s.excluded_tools:
            cmd += ["--exclude-tools", ",".join(s.excluded_tools)]
        if s.ip_allowlist:
            cmd += ["--ip-allowlist", ",".join(s.ip_allowlist)]
        return cmd

    # ------------------------------------------------------------------
    # Background I/O threads
    # ------------------------------------------------------------------

    def _read_stream(self, stream, is_stderr: bool) -> None:
        try:
            for line in stream:
                stripped = line.rstrip("\n")
                if stripped and self.on_log_line:
                    self.on_log_line(stripped, is_stderr)
        except Exception:
            pass

    def _spawn_process(self) -> tuple[subprocess.Popen | None, str | None]:
        cmd = self._build_command()
        kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            return subprocess.Popen(cmd, **kwargs), None
        except Exception as exc:
            return None, str(exc)

    def _start_stream_threads(self, proc: subprocess.Popen) -> None:
        self._stdout_thread = threading.Thread(
            target=self._read_stream, args=(proc.stdout, False), daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stream, args=(proc.stderr, True), daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _retry_start_without_ssl(self) -> bool:
        """Attempt a one-time restart without SSL when HTTPS health checks fail."""
        with self._lock:
            has_ssl = bool(self.settings.ssl_certfile and self.settings.ssl_keyfile)
            if not has_ssl or self._runtime_disable_ssl or self._ssl_retry_attempted:
                return False
            proc = self._process
            self._ssl_retry_attempted = True
            self._runtime_disable_ssl = True
            self._set_state(
                ServerState.STARTING,
                "HTTPS health check failed; retrying launch without SSL",
            )

        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass

        new_proc, err = self._spawn_process()
        if new_proc is None:
            with self._lock:
                self._start_time = None
                self._set_state(
                    ServerState.ERROR,
                    f"SSL fallback launch failed: {err}",
                )
            return False

        with self._lock:
            self._process = new_proc
            self._start_time = time.monotonic()

        self._start_stream_threads(new_proc)
        return True

    def _poll_health(self) -> None:
        """Poll /health until stop_event is set or process exits."""
        consecutive_fail = 0
        start_ts = time.monotonic()

        while not self._stop_event.is_set():
            # Check if process exited unexpectedly
            proc = self._process
            if proc is not None and proc.poll() is not None:
                with self._lock:
                    if self._state not in (ServerState.STOPPED, ServerState.STOPPING):
                        self._start_time = None
                        self._set_state(
                            ServerState.ERROR, "Server process exited unexpectedly"
                        )
                return

            healthy = self._check_health()

            if healthy:
                consecutive_fail = 0
                if self._state != ServerState.RUNNING:
                    with self._lock:
                        self._set_state(ServerState.RUNNING, "Health check passed")
            else:
                consecutive_fail += 1
                elapsed = time.monotonic() - start_ts

                if self._state == ServerState.STARTING:
                    if elapsed > self.start_timeout:
                        if self._retry_start_without_ssl():
                            consecutive_fail = 0
                            start_ts = time.monotonic()
                            self._stop_event.wait(timeout=self.poll_interval)
                            continue
                        with self._lock:
                            self._set_state(
                                ServerState.DEGRADED,
                                f"/health unreachable after {self.start_timeout:.0f}s",
                            )
                elif self._state == ServerState.RUNNING and consecutive_fail >= 3:
                    with self._lock:
                        self._set_state(ServerState.DEGRADED, "/health not responding")

            self._stop_event.wait(timeout=self.poll_interval)

    def _check_health(self) -> bool:
        """Return True if the server's /health endpoint responds with status=ok."""
        s = self.settings
        url = f"{self.base_url}/health"
        req = urllib.request.Request(url)
        if s.auth_key:
            req.add_header("Authorization", f"Bearer {s.auth_key}")

        try:
            if s.ssl_certfile and not self._runtime_disable_ssl:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
                    data = json.loads(resp.read())
            else:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data = json.loads(resp.read())
            return data.get("status") == "ok"
        except Exception:
            return False

    def _find_port_conflict(self) -> tuple[int, str] | None:
        """Return (pid, process_name) for a listener on our configured port, if any."""
        target_port = self.settings.port
        target_host = (self.settings.host or "").strip().lower()
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN:
                continue
            if not conn.laddr or conn.laddr.port != target_port:
                continue
            if not self._host_matches_listener(target_host, conn.laddr.ip):
                continue
            if not conn.pid:
                continue
            # Ignore if it somehow points back to our current managed process.
            if self._process is not None and conn.pid == self._process.pid:
                continue
            try:
                name = psutil.Process(conn.pid).name()
            except Exception:
                name = "unknown"
            return conn.pid, name
        return None

    @staticmethod
    def _host_matches_listener(configured_host: str, listener_ip: str) -> bool:
        listener = (listener_ip or "").lower()

        any_bind = {"0.0.0.0", "::", ""}
        loopback = {"127.0.0.1", "::1", "localhost"}

        if configured_host in loopback:
            return listener in loopback or listener in any_bind
        if configured_host in any_bind:
            return True
        return listener == configured_host

    # ------------------------------------------------------------------
    # Internal state helper
    # ------------------------------------------------------------------

    def _set_state(self, new_state: ServerState, reason: str = "") -> None:
        """Set state and fire callback. Caller must hold _lock (or be in initial start)."""
        self._state = new_state
        if self.on_state_change:
            # Callback outside the lock to avoid deadlock
            try:
                self.on_state_change(new_state, reason)
            except Exception:
                pass
