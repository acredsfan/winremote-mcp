"""Tests for TunnelManager (cloudflared subprocess lifecycle)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from winremote.launcher_tunnel import TunnelManager, TunnelSettings, TunnelState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**kwargs) -> TunnelSettings:
    kwargs.setdefault("target_url", "http://127.0.0.1:8090")
    return TunnelSettings(**kwargs)


def _make_manager(settings=None, **kwargs) -> TunnelManager:
    return TunnelManager(settings=settings or _settings(), **kwargs)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_initial_state_stopped():
    mgr = _make_manager()
    assert mgr.state == TunnelState.STOPPED
    assert mgr.pid is None
    assert mgr.public_url is None
    assert mgr.uptime_seconds is None


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------

def test_settings_resolve_binary_from_path(tmp_path: Path):
    exe = tmp_path / "cloudflared.exe"
    exe.write_text("stub")
    s = _settings(cloudflared_path=str(exe))
    assert s.resolve_binary() == str(exe)


def test_settings_resolve_binary_missing_explicit():
    s = _settings(cloudflared_path="/nonexistent/cloudflared")
    assert s.resolve_binary() is None


def test_settings_resolve_binary_from_system_path():
    s = _settings()
    import shutil
    expected = shutil.which("cloudflared")
    assert s.resolve_binary() == expected  # may be None if not installed


# ---------------------------------------------------------------------------
# Missing binary transitions to ERROR without starting
# ---------------------------------------------------------------------------

def test_start_error_when_binary_missing():
    s = _settings(cloudflared_path="/absolutely/nonexistent/cloudflared")
    states: list[TunnelState] = []
    mgr = TunnelManager(settings=s, on_state_change=lambda st, r: states.append(st))

    result = mgr.start()
    assert result is False
    assert mgr.state == TunnelState.ERROR
    assert TunnelState.ERROR in states


# ---------------------------------------------------------------------------
# Start rejected when already running
# ---------------------------------------------------------------------------

def test_start_rejected_when_starting():
    mgr = _make_manager()
    mgr._state = TunnelState.STARTING
    result = mgr.start()
    assert result is False


def test_start_rejected_when_connected():
    mgr = _make_manager()
    mgr._state = TunnelState.CONNECTED
    result = mgr.start()
    assert result is False


# ---------------------------------------------------------------------------
# Command builder — quick tunnel mode
# ---------------------------------------------------------------------------

def test_command_quick_tunnel():
    s = _settings()
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command("/usr/bin/cloudflared")
    assert cmd[0] == "/usr/bin/cloudflared"
    assert "tunnel" in cmd
    assert "--url" in cmd
    assert "http://127.0.0.1:8090" in cmd


def test_command_named_tunnel(tmp_path: Path):
    cfg = tmp_path / "tunnel.yaml"
    cfg.write_text("tunnel: my-tunnel")
    s = _settings(config_path=cfg)
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command("/usr/bin/cloudflared")
    assert "--config" in cmd
    assert str(cfg) in cmd
    assert "run" in cmd


def test_command_token_tunnel():
    s = _settings(api_token="eyJhbGci.fake.token")
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command("/usr/bin/cloudflared")
    assert cmd[0] == "/usr/bin/cloudflared"
    assert "tunnel" in cmd
    assert "run" in cmd
    assert "--token" in cmd
    assert "eyJhbGci.fake.token" in cmd
    # Must NOT fall back to --url mode
    assert "--url" not in cmd
    assert "--config" not in cmd


def test_command_token_overrides_config(tmp_path: Path):
    """api_token takes priority over config_path."""
    cfg = tmp_path / "tunnel.yaml"
    cfg.write_text("tunnel: my-tunnel")
    s = _settings(api_token="tok123", config_path=cfg)
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command("/usr/bin/cloudflared")
    assert "--token" in cmd
    assert "tok123" in cmd
    assert "--config" not in cmd


def test_command_api_managed_mode_uses_created_token():
    s = _settings(
        api_token=None,
        cloudflare_api_key="cf_api_token",
        cloudflare_account_id="acct_123",
        cloudflare_zone_id="zone_456",
        tunnel_dns_name="mcp.example.com",
        tunnel_name="winremote-mcp",
    )
    mgr = _make_manager(settings=s)

    with patch.object(mgr, "_cf_api_request") as api_mock:
        api_mock.side_effect = [
            {
                "success": True,
                "result": {
                    "id": "tun_abc",
                    "token": "run_tok_xyz",
                },
            },
            {"success": True, "result": []},
            {"success": True, "result": {"id": "dns_1"}},
        ]

        cmd = mgr._build_command("/usr/bin/cloudflared")

    assert cmd == [
        "/usr/bin/cloudflared",
        "tunnel",
        "run",
        "--token",
        "run_tok_xyz",
    ]


def test_upsert_dns_record_updates_existing_record():
    s = _settings(
        cloudflare_api_key="cf_api_token",
        cloudflare_zone_id="zone_456",
    )
    mgr = _make_manager(settings=s)

    with patch.object(mgr, "_cf_api_request") as api_mock:
        api_mock.side_effect = [
            {
                "success": True,
                "result": [{"id": "dns_existing"}],
            },
            {"success": True, "result": {"id": "dns_existing"}},
        ]

        mgr._upsert_dns_record("mcp.example.com", "tun_abc.cfargotunnel.com")

    assert api_mock.call_count == 2
    assert api_mock.call_args_list[1].args[0] == "PUT"


def test_cf_api_request_raises_on_unsuccessful_payload():
    s = _settings(cloudflare_api_key="cf_api_token")
    mgr = _make_manager(settings=s)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"success":false,"errors":[{"message":"invalid token"}]}'

    with patch("winremote.launcher_tunnel.request.urlopen", return_value=_Resp()):
        with pytest.raises(RuntimeError, match="invalid token"):
            mgr._cf_api_request("GET", "/zones/demo/dns_records", None)


def test_command_includes_extra_args():
    s = _settings(extra_args=["--loglevel", "debug"])
    mgr = _make_manager(settings=s)
    cmd = mgr._build_command("/usr/bin/cloudflared")
    assert "--loglevel" in cmd
    assert "debug" in cmd


# ---------------------------------------------------------------------------
# Popen failure transitions to ERROR
# ---------------------------------------------------------------------------

def test_start_popen_failure_transitions_to_error(tmp_path: Path):
    exe = tmp_path / "cloudflared"
    exe.write_text("stub")
    s = _settings(cloudflared_path=str(exe))
    states: list[TunnelState] = []
    mgr = TunnelManager(settings=s, on_state_change=lambda st, r: states.append(st))

    with patch.object(subprocess, "Popen", side_effect=OSError("boom")):
        result = mgr.start()

    assert result is False
    assert mgr.state == TunnelState.ERROR


# ---------------------------------------------------------------------------
# Log line parsing — URL extraction
# ---------------------------------------------------------------------------

def test_process_log_line_extracts_url():
    mgr = _make_manager()
    mgr._state = TunnelState.STARTING
    states_seen: list[TunnelState] = []
    mgr.on_state_change = lambda s, r: states_seen.append(s)

    mgr._process_log_line(
        "2024-01-01 10:00:00 INF | https://abc-def-mno.trycloudflare.com | connection registered"
    )

    assert mgr.public_url == "https://abc-def-mno.trycloudflare.com"
    assert TunnelState.CONNECTED in states_seen


def test_process_log_line_ignores_non_url_lines():
    mgr = _make_manager()
    mgr._state = TunnelState.STARTING
    mgr._process_log_line("Starting cloudflared version 2024.1.0")
    assert mgr.public_url is None
    assert mgr.state == TunnelState.STARTING


def test_process_log_line_registered_tunnel_connection():
    """Token-mode cloudflared emits 'Registered tunnel connection' (reversed word order)."""
    mgr = _make_manager()
    mgr._state = TunnelState.STARTING
    states_seen: list[TunnelState] = []
    mgr.on_state_change = lambda s, r: states_seen.append(s)

    mgr._process_log_line(
        "2024-01-01T10:00:00Z INF Registered tunnel connection connIndex=0 ip=198.41.200.193"
    )

    assert TunnelState.CONNECTED in states_seen


# ---------------------------------------------------------------------------
# Stop from stopped is a no-op
# ---------------------------------------------------------------------------

def test_stop_when_already_stopped():
    mgr = _make_manager()
    mgr.stop()  # should not raise
    assert mgr.state == TunnelState.STOPPED


# ---------------------------------------------------------------------------
# Update settings
# ---------------------------------------------------------------------------

def test_update_settings_replaces():
    mgr = _make_manager()
    new_s = _settings(target_url="http://127.0.0.1:9001")
    mgr.update_settings(new_s)
    assert mgr.settings.target_url == "http://127.0.0.1:9001"


# ---------------------------------------------------------------------------
# Uptime
# ---------------------------------------------------------------------------

def test_uptime_none_when_stopped():
    mgr = _make_manager()
    assert mgr.uptime_seconds is None


def test_uptime_positive_after_start_set():
    mgr = _make_manager()
    mgr._start_time = time.monotonic() - 10.0
    assert mgr.uptime_seconds >= 10.0
