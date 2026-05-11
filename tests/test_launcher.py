"""Tests for the ServerManager subprocess lifecycle."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil
import pytest

from winremote.launcher import (
    PROFILE_DESCRIPTIONS,
    VALID_PROFILES,
    ServerManager,
    ServerSettings,
    ServerState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**kwargs) -> ServerSettings:
    kwargs.setdefault("host", "127.0.0.1")
    kwargs.setdefault("port", 19999)
    kwargs.setdefault("transport", "streamable-http")
    return ServerSettings(**kwargs)


def _make_manager(settings=None, **manager_kwargs) -> ServerManager:
    return ServerManager(
        settings=settings or _settings(),
        **manager_kwargs,
    )


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def test_valid_profiles():
    assert set(VALID_PROFILES) == {"default", "chatgpt", "copilot", "copilot-cli", "claude", "excel"}


def test_profile_descriptions_cover_all_profiles():
    for p in VALID_PROFILES:
        assert p in PROFILE_DESCRIPTIONS
        assert PROFILE_DESCRIPTIONS[p]


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_state_stopped():
    mgr = _make_manager()
    assert mgr.state == ServerState.STOPPED
    assert mgr.pid is None
    assert mgr.uptime_seconds is None


# ---------------------------------------------------------------------------
# Command builder
# ---------------------------------------------------------------------------

def test_command_includes_profile():
    s = _settings(profile="chatgpt")
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command()
    assert "--profile" in cmd
    idx = cmd.index("--profile")
    assert cmd[idx + 1] == "chatgpt"


def test_command_includes_transport():
    mgr = _make_manager()
    cmd = mgr._build_command()
    assert "--transport" in cmd
    assert "streamable-http" in cmd


def test_command_includes_config_when_given(tmp_path):
    cfg = tmp_path / "winremote.toml"
    cfg.write_text("")
    s = _settings(config_path=cfg)
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command()
    assert "--config" in cmd
    assert str(cfg) in cmd


def test_command_no_config_when_none():
    mgr = _make_manager()
    cmd = mgr._build_command()
    assert "--config" not in cmd


def test_command_includes_auth_key():
    s = _settings(auth_key="secret123")
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command()
    assert "--auth-key" in cmd
    assert "secret123" in cmd


def test_command_includes_oauth_client_credentials():
    s = _settings(
        oauth_client_id="copilot-cli",
        oauth_client_secret="client-secret",
    )
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command()
    assert "--oauth-client-id" in cmd
    assert "copilot-cli" in cmd
    assert "--oauth-client-secret" in cmd
    assert "client-secret" in cmd


def test_command_includes_enable_tier3():
    s = _settings(enable_tier3=True)
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command()
    assert "--enable-tier3" in cmd


def test_command_no_enable_tier3_when_false():
    mgr = _make_manager()
    cmd = mgr._build_command()
    assert "--enable-tier3" not in cmd


def test_command_includes_ip_allowlist():
    s = _settings(ip_allowlist=["192.168.1.0/24", "10.0.0.1"])
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command()
    assert "--ip-allowlist" in cmd
    idx = cmd.index("--ip-allowlist")
    assert "192.168.1.0/24,10.0.0.1" in cmd[idx + 1]


def test_command_includes_tools_csv():
    s = _settings(selected_tools=["Snapshot", "UIFind"])
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command()
    assert "--tools" in cmd
    idx = cmd.index("--tools")
    assert cmd[idx + 1] == "Snapshot,UIFind"


def test_command_includes_exclude_tools_csv():
    s = _settings(excluded_tools=["Shell", "FileWrite"])
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command()
    assert "--exclude-tools" in cmd
    idx = cmd.index("--exclude-tools")
    assert cmd[idx + 1] == "Shell,FileWrite"


# ---------------------------------------------------------------------------
# Start prevents duplicate launches
# ---------------------------------------------------------------------------

def test_start_rejected_when_already_starting():
    mgr = _make_manager()
    mgr._state = ServerState.STARTING
    result = mgr.start()
    assert result is False


def test_start_rejected_when_running():
    mgr = _make_manager()
    mgr._state = ServerState.RUNNING
    result = mgr.start()
    assert result is False


def test_start_allowed_from_error():
    """Start is allowed from ERROR state (retry scenario)."""
    mgr = _make_manager()
    mgr._state = ServerState.ERROR

    with patch.object(subprocess, "Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.stderr = iter([])
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        result = mgr.start()
        # Stop the background threads cleanly
        mgr._stop_event.set()
    assert result is True


# ---------------------------------------------------------------------------
# Failed Popen transitions to ERROR
# ---------------------------------------------------------------------------

def test_start_transitions_to_error_on_popen_failure():
    mgr = _make_manager()
    states: list[ServerState] = []
    mgr.on_state_change = lambda s, r: states.append(s)

    with patch.object(subprocess, "Popen", side_effect=FileNotFoundError("not found")):
        result = mgr.start()

    assert result is False
    assert ServerState.ERROR in states
    assert mgr.state == ServerState.ERROR


# ---------------------------------------------------------------------------
# State change callback
# ---------------------------------------------------------------------------

def test_state_change_callback_fires():
    called: list[tuple[ServerState, str]] = []
    mgr = _make_manager(on_state_change=lambda s, r: called.append((s, r)))

    with patch.object(subprocess, "Popen", side_effect=OSError("boom")):
        mgr.start()

    assert any(s == ServerState.ERROR for s, _ in called)


# ---------------------------------------------------------------------------
# Stop from stopped is a no-op
# ---------------------------------------------------------------------------

def test_stop_when_already_stopped():
    mgr = _make_manager()
    # Should not raise
    mgr.stop()
    assert mgr.state == ServerState.STOPPED


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def test_check_health_returns_false_on_connection_error():
    mgr = _make_manager()
    # No server running → connection refused
    result = mgr._check_health()
    assert result is False


def test_base_url_http():
    s = _settings(host="127.0.0.1", port=8090)
    mgr = _make_manager(settings=s)
    assert mgr.base_url == "http://127.0.0.1:8090"


def test_base_url_https_when_ssl():
    s = _settings(host="127.0.0.1", port=8090, ssl_certfile="/certs/cert.pem")
    mgr = _make_manager(settings=s)
    assert mgr.base_url == "https://127.0.0.1:8090"


# ---------------------------------------------------------------------------
# Update settings
# ---------------------------------------------------------------------------

def test_update_settings_replaces_settings():
    mgr = _make_manager()
    new_s = _settings(profile="excel", port=9001)
    mgr.update_settings(new_s)
    assert mgr.settings.profile == "excel"
    assert mgr.settings.port == 9001


# ---------------------------------------------------------------------------
# Uptime
# ---------------------------------------------------------------------------

def test_uptime_none_when_stopped():
    mgr = _make_manager()
    assert mgr.uptime_seconds is None


def test_uptime_positive_after_start_time_set():
    mgr = _make_manager()
    mgr._start_time = time.monotonic() - 5.0
    assert mgr.uptime_seconds >= 5.0


# ---------------------------------------------------------------------------
# Port conflict detection and remediation
# ---------------------------------------------------------------------------

def test_start_sets_error_when_port_conflict_detected():
    mgr = _make_manager(settings=_settings(host="127.0.0.1", port=8090))
    with patch.object(mgr, "_find_port_conflict", return_value=(4321, "python.exe")):
        ok = mgr.start()

    assert ok is False
    assert mgr.state == ServerState.ERROR
    assert mgr.has_port_conflict is True
    assert mgr.port_conflict_pid == 4321
    assert "python" in (mgr.port_conflict_name or "")


def test_find_port_conflict_matches_listener_on_port(monkeypatch):
    mgr = _make_manager(settings=_settings(host="127.0.0.1", port=8090))

    fake_conn = SimpleNamespace(
        status="LISTEN",
        laddr=SimpleNamespace(ip="0.0.0.0", port=8090),
        pid=2222,
    )

    with patch("winremote.launcher.psutil.net_connections", return_value=[fake_conn]), patch(
        "winremote.launcher.psutil.Process"
    ) as p_cls:
        p_cls.return_value.name.return_value = "python.exe"
        found = mgr._find_port_conflict()

    assert found == (2222, "python.exe")


@pytest.mark.parametrize(
    "host,listener_ip,expected",
    [
        ("127.0.0.1", "127.0.0.1", True),
        ("127.0.0.1", "0.0.0.0", True),
        ("localhost", "::", True),
        ("0.0.0.0", "127.0.0.1", True),
        ("192.168.1.10", "192.168.1.10", True),
        ("192.168.1.10", "0.0.0.0", False),
    ],
)
def test_host_matches_listener(host, listener_ip, expected):
    assert ServerManager._host_matches_listener(host, listener_ip) is expected


def test_stop_conflicting_process_success():
    mgr = _make_manager()
    mgr._port_conflict_pid = 3333
    mgr._port_conflict_name = "python.exe"

    proc = MagicMock()
    proc.name.return_value = "python.exe"
    with patch("winremote.launcher.psutil.Process", return_value=proc):
        ok, msg = mgr.stop_conflicting_process()

    assert ok is True
    assert "Stopped conflicting process" in msg
    proc.terminate.assert_called_once()
    assert mgr.has_port_conflict is False


def test_stop_conflicting_process_access_denied():
    mgr = _make_manager()
    mgr._port_conflict_pid = 4444
    mgr._port_conflict_name = "python.exe"

    with patch(
        "winremote.launcher.psutil.Process",
        side_effect=psutil.AccessDenied(pid=4444),
    ):
        ok, msg = mgr.stop_conflicting_process()

    assert ok is False
    assert "Access denied" in msg


def test_build_command_omits_ssl_when_runtime_disabled():
    s = _settings(
        ssl_certfile="C:/tmp/cert.pem",
        ssl_keyfile="C:/tmp/key.pem",
    )
    mgr = _make_manager(settings=s)

    cmd_with_ssl = mgr._build_command()
    assert "--ssl-certfile" in cmd_with_ssl
    assert "--ssl-keyfile" in cmd_with_ssl

    mgr._runtime_disable_ssl = True
    cmd_without_ssl = mgr._build_command()
    assert "--ssl-certfile" not in cmd_without_ssl
    assert "--ssl-keyfile" not in cmd_without_ssl


def test_retry_start_without_ssl_relaunches_once():
    s = _settings(
        ssl_certfile="C:/tmp/cert.pem",
        ssl_keyfile="C:/tmp/key.pem",
    )
    mgr = _make_manager(settings=s)

    old_proc = MagicMock()
    old_proc.wait.return_value = 0
    old_proc.stdout = iter([])
    old_proc.stderr = iter([])
    mgr._process = old_proc

    new_proc = MagicMock()
    new_proc.stdout = iter([])
    new_proc.stderr = iter([])

    with patch.object(mgr, "_spawn_process", return_value=(new_proc, None)) as spawn_mock, patch.object(
        mgr, "_start_stream_threads"
    ) as stream_mock:
        ok = mgr._retry_start_without_ssl()

    assert ok is True
    assert mgr._runtime_disable_ssl is True
    assert mgr._ssl_retry_attempted is True
    assert mgr._process is new_proc
    old_proc.terminate.assert_called_once()
    spawn_mock.assert_called_once()
    stream_mock.assert_called_once_with(new_proc)

    # One-shot guard: second retry is blocked.
    with patch.object(mgr, "_spawn_process") as spawn_mock_again:
        ok_again = mgr._retry_start_without_ssl()
    assert ok_again is False
    spawn_mock_again.assert_not_called()
