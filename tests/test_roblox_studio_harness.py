"""Tests for the local Roblox Studio harness server and export helpers."""

from __future__ import annotations

import json
import threading
import urllib.request
from unittest.mock import MagicMock

from click.testing import CliRunner

from winremote.__main__ import cli
from winremote.roblox_studio_harness import create_harness_server


def _request_json(base_url: str, method: str, route: str, payload: dict | None = None) -> dict:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base_url}{route}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


class TestRobloxStudioHarnessStore:
    def test_export_harness_command_writes_files(self, tmp_path):
        result = CliRunner().invoke(
            cli,
            [
                "roblox-studio",
                "export-harness",
                "--output-dir",
                str(tmp_path),
                "--harness-url",
                "http://127.0.0.1:61234",
            ],
        )
        assert result.exit_code == 0
        assert (tmp_path / "WinRemoteHarness.server.lua").exists()
        assert (tmp_path / "WinRemoteHarnessConfig.lua").read_text(encoding="utf-8").find("61234") >= 0

    def test_http_harness_round_trip(self):
        server = create_harness_server(host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        try:
            state = _request_json(
                base_url,
                "POST",
                "/state",
                {
                    "client_id": "studio-a",
                    "state": {"connected_players": 1, "players": [{"name": "Builder"}]},
                },
            )
            assert state["connected"] is True
            assert state["active_client_id"] == "studio-a"

            waited_response: dict[str, object] = {}

            def _queue_test():
                waited_response.update(
                    _request_json(
                        base_url,
                        "POST",
                        "/run-test",
                        {"test_name": "Smoke", "wait": True, "timeout_seconds": 1.0},
                    )
                )

            waiter = threading.Thread(target=_queue_test, daemon=True)
            waiter.start()

            command = _request_json(base_url, "GET", "/pull?client_id=studio-a&timeout=0.1")
            assert command["command"]["kind"] == "run_named_test"
            command_id = command["command"]["command_id"]

            result = _request_json(
                base_url,
                "POST",
                "/command-result",
                {
                    "client_id": "studio-a",
                    "command_id": command_id,
                    "ok": True,
                    "result": {"passed": True, "notes": "Smoke ok"},
                },
            )
            assert result["ok"] is True

            waiter.join(timeout=2)
            assert waited_response["completed"] is True
            assert waited_response["ok"] is True
            assert waited_response["result"]["passed"] is True

            latest = _request_json(base_url, "GET", "/state")
            assert latest["recent_results"][-1]["command_id"] == command_id
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_ensure_copilot_harness_running_starts_when_needed(self, monkeypatch):
        import winremote.__main__ as mod

        monkeypatch.setattr(
            mod.roblox_studio,
            "harness_request",
            MagicMock(
                side_effect=[
                    {"ok": False, "error": "offline"},
                    {"ok": False, "error": "still starting"},
                    {"ok": True, "data": {"status": "ok"}},
                ]
            ),
        )
        launcher = MagicMock()
        monkeypatch.setattr(mod, "_launch_harness_process", launcher)
        monkeypatch.setattr(mod.time, "sleep", lambda _: None)

        started = mod._ensure_copilot_harness_running()

        assert started is True
        launcher.assert_called_once()

    def test_ensure_copilot_harness_running_noops_when_healthy(self, monkeypatch):
        import winremote.__main__ as mod

        monkeypatch.setattr(
            mod.roblox_studio,
            "harness_request",
            MagicMock(return_value={"ok": True, "data": {"status": "ok"}}),
        )
        launcher = MagicMock()
        monkeypatch.setattr(mod, "_launch_harness_process", launcher)

        started = mod._ensure_copilot_harness_running()

        assert started is False
        launcher.assert_not_called()

    def test_copilot_launch_uses_copilot_profile(self, monkeypatch):
        import winremote.__main__ as mod

        ensure = MagicMock(return_value=False)
        run_server = MagicMock()
        monkeypatch.setattr(mod, "_ensure_copilot_harness_running", ensure)
        monkeypatch.setattr(mod, "_run_mcp_server", run_server)

        result = CliRunner().invoke(cli, ["copilot-launch"])

        assert result.exit_code == 0
        ensure.assert_called_once()
        run_server.assert_called_once()
        assert run_server.call_args.kwargs["transport"] == "stdio"
        assert run_server.call_args.kwargs["profile"] == "copilot"
