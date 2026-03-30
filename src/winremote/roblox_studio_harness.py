"""Local Roblox Studio harness server and exportable Studio scripts."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from textwrap import dedent
from typing import Any
from urllib.parse import parse_qs, urlparse


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HarnessCommand:
    """Command queued for a Studio playtest client."""

    command_id: str
    kind: str
    payload: dict[str, Any]
    created_at: str
    target_client_id: str = ""
    status: str = "queued"
    delivered_at: str = ""
    completed_at: str = ""
    ok: bool | None = None
    result: Any = None
    error: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "kind": self.kind,
            "payload": self.payload,
            "created_at": self.created_at,
            "target_client_id": self.target_client_id,
            "status": self.status,
            "delivered_at": self.delivered_at or None,
            "completed_at": self.completed_at or None,
            "ok": self.ok,
            "result": self.result,
            "error": self.error or None,
        }


@dataclass
class HarnessClientState:
    """Latest heartbeat and state reported by a Studio playtest client."""

    client_id: str
    state: dict[str, Any] = field(default_factory=dict)
    last_seen_at: str = field(default_factory=_utc_now)


class RobloxStudioHarnessStore:
    """Thread-safe state and command queue for Studio playtests."""

    def __init__(self, *, stale_after_seconds: float = 5.0, max_events: int = 100, max_results: int = 50):
        self.stale_after_seconds = max(0.5, float(stale_after_seconds))
        self.max_events = max(1, int(max_events))
        self.max_results = max(1, int(max_results))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._clients: dict[str, HarnessClientState] = {}
        self._active_client_id = ""
        self._commands: dict[str, HarnessCommand] = {}
        self._queue: deque[str] = deque()
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._results: deque[dict[str, Any]] = deque(maxlen=self.max_results)

    def _connected(self, client: HarnessClientState | None) -> bool:
        if client is None:
            return False
        try:
            last_seen = datetime.fromisoformat(client.last_seen_at)
        except ValueError:
            return False
        return (datetime.now(timezone.utc) - last_seen).total_seconds() <= self.stale_after_seconds

    def _resolve_client_id(self, preferred: str = "") -> str:
        if preferred and preferred in self._clients:
            return preferred
        if self._active_client_id and self._active_client_id in self._clients:
            return self._active_client_id
        if self._clients:
            return next(iter(self._clients))
        return ""

    def update_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = payload.get("state")
        if not isinstance(state, dict):
            state = {k: v for k, v in payload.items() if k not in {"client_id", "events", "results"}}
        client_id = str(payload.get("client_id") or state.get("client_id") or "studio-default")
        events = payload.get("events")
        results = payload.get("results")

        with self._condition:
            client = self._clients.get(client_id) or HarnessClientState(client_id=client_id)
            client.state = state
            client.last_seen_at = _utc_now()
            self._clients[client_id] = client
            self._active_client_id = client_id
            for event in events or []:
                if isinstance(event, dict):
                    self._events.append({"client_id": client_id, "timestamp": _utc_now(), **event})
            for result in results or []:
                if isinstance(result, dict):
                    self.complete_command(
                        command_id=str(result.get("command_id") or ""),
                        ok=bool(result.get("ok")),
                        result=result.get("result"),
                        error=str(result.get("error") or ""),
                        client_id=client_id,
                    )
            self._condition.notify_all()
            return self.get_state()

    def get_state(self, client_id: str = "") -> dict[str, Any]:
        with self._lock:
            resolved_client_id = self._resolve_client_id(client_id)
            client = self._clients.get(resolved_client_id) if resolved_client_id else None
            active_commands = sum(
                1
                for command_id in self._queue
                if self._commands.get(command_id) is not None and self._commands[command_id].status == "queued"
            )
            return {
                "ok": True,
                "connected": self._connected(client),
                "active_client_id": resolved_client_id or None,
                "known_client_ids": sorted(self._clients.keys()),
                "last_seen_at": client.last_seen_at if client else None,
                "state": client.state if client else None,
                "pending_commands": active_commands,
                "recent_events": list(self._events)[-10:],
                "recent_results": list(self._results)[-10:],
                "stale_after_seconds": self.stale_after_seconds,
            }

    def append_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_id = str(payload.get("client_id") or self._resolve_client_id() or "studio-default")
        event = {
            "client_id": client_id,
            "timestamp": _utc_now(),
            "type": payload.get("type") or "event",
            "message": payload.get("message") or "",
            "data": payload.get("data") or {},
        }
        with self._condition:
            self._events.append(event)
            self._condition.notify_all()
        return {"ok": True, "event": event}

    def enqueue_command(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        target_client_id: str = "",
    ) -> HarnessCommand:
        command = HarnessCommand(
            command_id=str(uuid.uuid4()),
            kind=kind,
            payload=dict(payload or {}),
            created_at=_utc_now(),
            target_client_id=target_client_id,
        )
        with self._condition:
            self._commands[command.command_id] = command
            self._queue.append(command.command_id)
            self._condition.notify_all()
        return command

    def next_command(self, client_id: str, *, timeout_seconds: float = 0.0) -> HarnessCommand | None:
        client_id = str(client_id or "").strip()
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        with self._condition:
            while True:
                for command_id in list(self._queue):
                    command = self._commands.get(command_id)
                    if command is None or command.status != "queued":
                        try:
                            self._queue.remove(command_id)
                        except ValueError:
                            pass
                        continue
                    if command.target_client_id and command.target_client_id != client_id:
                        continue
                    command.status = "delivered"
                    command.delivered_at = _utc_now()
                    try:
                        self._queue.remove(command_id)
                    except ValueError:
                        pass
                    self._condition.notify_all()
                    return command
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)

    def complete_command(
        self,
        *,
        command_id: str,
        ok: bool,
        result: Any = None,
        error: str = "",
        client_id: str = "",
    ) -> dict[str, Any]:
        command_id = str(command_id or "").strip()
        if not command_id:
            raise ValueError("command_id is required")
        with self._condition:
            command = self._commands.get(command_id)
            if command is None:
                raise KeyError(f"Unknown command_id: {command_id}")
            command.status = "completed" if ok else "failed"
            command.completed_at = _utc_now()
            command.ok = bool(ok)
            command.result = result
            command.error = error
            entry = {
                "command_id": command.command_id,
                "kind": command.kind,
                "ok": command.ok,
                "client_id": client_id or command.target_client_id or self._active_client_id or None,
                "completed_at": command.completed_at,
                "result": result,
                "error": error or None,
            }
            self._results.append(entry)
            self._condition.notify_all()
            return entry

    def wait_for_result(self, command_id: str, *, timeout_seconds: float = 5.0) -> HarnessCommand | None:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        with self._condition:
            while True:
                command = self._commands.get(command_id)
                if command and command.status in {"completed", "failed"}:
                    return command
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)


class RobloxStudioHarnessServer(ThreadingHTTPServer):
    """HTTP server wrapper with shared harness state."""

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        stale_after_seconds: float = 5.0,
        max_events: int = 100,
    ):
        self.store = RobloxStudioHarnessStore(
            stale_after_seconds=stale_after_seconds,
            max_events=max_events,
        )
        super().__init__(server_address, _build_handler(self.store))


def _json_body(request: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(request.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = request.rfile.read(length).decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("JSON body must decode to an object")
    return payload


def _send_json(request: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", "application/json")
    request.send_header("Content-Length", str(len(body)))
    request.end_headers()
    request.wfile.write(body)


def _queue_action(
    store: RobloxStudioHarnessStore,
    kind: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    wait = bool(payload.pop("wait", False))
    timeout_seconds = float(payload.pop("timeout_seconds", 5.0) or 5.0)
    target_client_id = str(payload.pop("client_id", "") or "")
    command = store.enqueue_command(kind, payload=payload, target_client_id=target_client_id)
    response: dict[str, Any] = {
        "ok": True,
        "queued": True,
        "completed": False,
        "timed_out": False,
        "command": command.public(),
        "connected": store.get_state(target_client_id).get("connected", False),
    }
    if not wait:
        return 202, response

    finished = store.wait_for_result(command.command_id, timeout_seconds=timeout_seconds)
    if finished is None:
        response["ok"] = False
        response["timed_out"] = True
        return 202, response

    response["ok"] = bool(finished.ok)
    response["queued"] = False
    response["completed"] = True
    response["command"] = finished.public()
    response["result"] = finished.result
    response["error"] = finished.error or None
    return 200, response


def _build_handler(store: RobloxStudioHarnessStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _query(self) -> dict[str, str]:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            return {key: values[-1] for key, values in query.items() if values}

        def _route(self) -> str:
            return urlparse(self.path).path

        def do_GET(self) -> None:  # noqa: N802
            route = self._route()
            query = self._query()
            try:
                if route == "/health":
                    _send_json(self, 200, {"ok": True, "status": "ok", **store.get_state(query.get("client_id", ""))})
                    return
                if route == "/state":
                    _send_json(self, 200, store.get_state(query.get("client_id", "")))
                    return
                if route == "/pull":
                    timeout_seconds = float(query.get("timeout", "0") or "0")
                    command = store.next_command(query.get("client_id", ""), timeout_seconds=timeout_seconds)
                    _send_json(self, 200, {"ok": True, "command": command.public() if command else None})
                    return
                _send_json(self, 404, {"ok": False, "error": f"Unknown route: {route}"})
            except Exception as e:
                _send_json(self, 500, {"ok": False, "error": str(e)})

        def do_POST(self) -> None:  # noqa: N802
            route = self._route()
            try:
                payload = _json_body(self)
                if route == "/state":
                    _send_json(self, 200, store.update_state(payload))
                    return
                if route == "/event":
                    _send_json(self, 200, store.append_event(payload))
                    return
                if route == "/command-result":
                    result = store.complete_command(
                        command_id=str(payload.get("command_id") or ""),
                        ok=bool(payload.get("ok")),
                        result=payload.get("result"),
                        error=str(payload.get("error") or ""),
                        client_id=str(payload.get("client_id") or ""),
                    )
                    _send_json(self, 200, {"ok": True, "result": result})
                    return
                if route == "/reset-character":
                    status, body = _queue_action(store, "reset_character", payload)
                    _send_json(self, status, body)
                    return
                if route == "/teleport-checkpoint":
                    status, body = _queue_action(store, "teleport_checkpoint", payload)
                    _send_json(self, status, body)
                    return
                if route == "/run-test":
                    status, body = _queue_action(store, "run_named_test", payload)
                    _send_json(self, status, body)
                    return
                _send_json(self, 404, {"ok": False, "error": f"Unknown route: {route}"})
            except KeyError as e:
                _send_json(self, 404, {"ok": False, "error": str(e)})
            except Exception as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})

    return Handler


def create_harness_server(
    *,
    host: str = "127.0.0.1",
    port: int = 51234,
    stale_after_seconds: float = 5.0,
    max_events: int = 100,
) -> RobloxStudioHarnessServer:
    """Create a Roblox Studio harness server instance."""
    return RobloxStudioHarnessServer(
        (host, int(port)),
        stale_after_seconds=stale_after_seconds,
        max_events=max_events,
    )


def serve_harness(
    *,
    host: str = "127.0.0.1",
    port: int = 51234,
    stale_after_seconds: float = 5.0,
    max_events: int = 100,
) -> None:
    """Run the local Roblox Studio harness server forever."""
    server = create_harness_server(
        host=host,
        port=port,
        stale_after_seconds=stale_after_seconds,
        max_events=max_events,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _config_lua(harness_url: str) -> str:
    return dedent(
        f"""\
        local Config = {{
            harnessUrl = "{harness_url}",
            heartbeatSeconds = 0.5,
            pullTimeoutSeconds = 0.25,
            requestTimeoutSeconds = 3.0,
            checkpointTag = "WinRemoteCheckpoint",
            checkpointAttribute = "CheckpointId",
            clientId = "",
        }}

        return Config
        """
    )


def _named_tests_lua() -> str:
    return dedent(
        """\
        local NamedTests = {}

        function NamedTests.Smoke(context, payload)
            local state = context.buildState()
            if (state.connected_players or 0) < 1 then
                return false, {
                    message = "No players are connected to the Studio playtest",
                    state = state,
                }
            end

            if payload and payload.checkpoint_id then
                local ok, result = context.teleportToCheckpoint(payload.checkpoint_id, payload)
                if not ok then
                    return false, result
                end
            end

            return true, {
                message = "Smoke test passed",
                state = state,
            }
        end

        return NamedTests
        """
    )


def _server_script_lua() -> str:
    return dedent(
        """\
        local CollectionService = game:GetService("CollectionService")
        local HttpService = game:GetService("HttpService")
        local Players = game:GetService("Players")
        local RunService = game:GetService("RunService")

        if not RunService:IsStudio() then
            return
        end

        local Config = require(script.Parent:WaitForChild("WinRemoteHarnessConfig"))
        local NamedTests = require(script.Parent:WaitForChild("WinRemoteHarnessNamedTests"))

        local clientId = Config.clientId ~= "" and Config.clientId or ("studio-" .. HttpService:GenerateGUID(false))
        local lastTestResult = nil
        local activeTestName = nil
        local lastWarningAt = 0

        local function warnOnce(message)
            local now = os.clock()
            if now - lastWarningAt >= 5 then
                lastWarningAt = now
                warn("[WinRemoteHarness] " .. message)
            end
        end

        local function request(method, route, body, timeoutSeconds)
            local url = Config.harnessUrl .. route
            local options = {
                Url = url,
                Method = method,
                Headers = { ["Content-Type"] = "application/json" },
                Timeout = timeoutSeconds or Config.requestTimeoutSeconds,
            }
            if body ~= nil then
                options.Body = HttpService:JSONEncode(body)
            end

            local ok, response = pcall(HttpService.RequestAsync, HttpService, options)
            if not ok then
                return false, tostring(response)
            end
            if not response.Success then
                return false, string.format("HTTP %s %s", tostring(response.StatusCode), tostring(response.Body))
            end
            if response.Body == nil or response.Body == "" then
                return true, {}
            end
            local decodedOk, decoded = pcall(HttpService.JSONDecode, HttpService, response.Body)
            if not decodedOk then
                return false, tostring(decoded)
            end
            return true, decoded
        end

        local function snapshotPlayer(player)
            local character = player.Character
            local humanoid = character and character:FindFirstChildOfClass("Humanoid")
            local root = character and character:FindFirstChild("HumanoidRootPart")
            local position = nil
            if root then
                position = { x = root.Position.X, y = root.Position.Y, z = root.Position.Z }
            end
            return {
                name = player.Name,
                display_name = player.DisplayName,
                user_id = player.UserId,
                health = humanoid and humanoid.Health or nil,
                max_health = humanoid and humanoid.MaxHealth or nil,
                position = position,
            }
        end

        local function resolvePlayer(payload)
            payload = payload or {}
            local requestedName = payload.player_name
            local requestedUserId = payload.user_id
            if requestedUserId ~= nil then
                requestedUserId = tonumber(requestedUserId)
            end

            for _, player in ipairs(Players:GetPlayers()) do
                if requestedUserId ~= nil and player.UserId == requestedUserId then
                    return player
                end
                if requestedName ~= nil and string.lower(player.Name) == string.lower(tostring(requestedName)) then
                    return player
                end
            end

            return Players:GetPlayers()[1]
        end

        local function resolveCheckpoint(checkpointId)
            if checkpointId == nil or checkpointId == "" then
                return nil, "checkpoint_id is required"
            end

            for _, instance in ipairs(CollectionService:GetTagged(Config.checkpointTag)) do
                local candidateId = instance:GetAttribute(Config.checkpointAttribute) or instance.Name
                if tostring(candidateId) == tostring(checkpointId) then
                    return instance
                end
            end

            local fallback = workspace:FindFirstChild(tostring(checkpointId), true)
            if fallback then
                return fallback
            end

            return nil, "checkpoint not found: " .. tostring(checkpointId)
        end

        local function buildState()
            local players = {}
            for _, player in ipairs(Players:GetPlayers()) do
                table.insert(players, snapshotPlayer(player))
            end

            local checkpoints = {}
            for _, instance in ipairs(CollectionService:GetTagged(Config.checkpointTag)) do
                local position = nil
                if instance:IsA("BasePart") then
                    position = { x = instance.Position.X, y = instance.Position.Y, z = instance.Position.Z }
                end
                table.insert(checkpoints, {
                    id = instance:GetAttribute(Config.checkpointAttribute) or instance.Name,
                    name = instance.Name,
                    position = position,
                })
            end

            return {
                client_id = clientId,
                game_name = game.Name,
                place_id = game.PlaceId,
                job_id = game.JobId,
                is_studio = RunService:IsStudio(),
                is_running = RunService:IsRunning(),
                connected_players = #players,
                players = players,
                checkpoints = checkpoints,
                active_test = activeTestName,
                last_test_result = lastTestResult,
                timestamp_unix = os.time(),
            }
        end

        local context = {}

        function context.buildState()
            return buildState()
        end

        function context.resetCharacter(payload)
            local player = resolvePlayer(payload)
            if not player then
                return false, { message = "No player available for reset" }
            end
            player:LoadCharacter()
            task.wait(0.2)
            return true, {
                message = "Character reset",
                player = player.Name,
                state = buildState(),
            }
        end

        function context.teleportToCheckpoint(checkpointId, payload)
            local checkpoint, checkpointError = resolveCheckpoint(checkpointId)
            if not checkpoint then
                return false, { message = checkpointError }
            end
            local player = resolvePlayer(payload)
            if not player then
                return false, { message = "No player available for teleport" }
            end
            local character = player.Character or player.CharacterAdded:Wait()
            local root = character and character:FindFirstChild("HumanoidRootPart")
            if not root then
                return false, { message = "HumanoidRootPart not found for " .. player.Name }
            end
            if not checkpoint:IsA("BasePart") then
                return false, { message = "Checkpoint is not a BasePart: " .. checkpoint:GetFullName() }
            end
            root.CFrame = checkpoint.CFrame + Vector3.new(0, 4, 0)
            task.wait(0.1)
            return true, {
                message = "Teleported to checkpoint",
                checkpoint_id = checkpointId,
                player = player.Name,
                state = buildState(),
            }
        end

        function context.waitSeconds(seconds)
            task.wait(tonumber(seconds) or 0)
        end

        local function runNamedTest(payload)
            local testName = payload.test_name
            local testHandler = NamedTests[testName]
            if type(testHandler) ~= "function" then
                return false, { message = "Unknown test: " .. tostring(testName) }
            end

            activeTestName = testName
            local ok, firstResult, secondResult = pcall(testHandler, context, payload)
            activeTestName = nil
            if not ok then
                lastTestResult = { test_name = testName, ok = false, error = tostring(firstResult), completed_at_unix = os.time() }
                return false, lastTestResult
            end

            local passed = true
            local result = nil
            if type(firstResult) == "boolean" then
                passed = firstResult
                result = secondResult
            elseif type(firstResult) == "table" and firstResult.ok ~= nil then
                passed = not not firstResult.ok
                result = firstResult
            else
                result = firstResult
            end

            lastTestResult = { test_name = testName, ok = passed, result = result, completed_at_unix = os.time() }
            return passed, result
        end

        local function executeCommand(command)
            if command.kind == "reset_character" then
                return context.resetCharacter(command.payload or {})
            end
            if command.kind == "teleport_checkpoint" then
                local payload = command.payload or {}
                return context.teleportToCheckpoint(payload.checkpoint_id, payload)
            end
            if command.kind == "run_named_test" then
                return runNamedTest(command.payload or {})
            end
            return false, { message = "Unsupported command kind: " .. tostring(command.kind) }
        end

        task.defer(function()
            while true do
                local stateOk = request("POST", "/state", { client_id = clientId, state = buildState() }, Config.requestTimeoutSeconds)
                if not stateOk then
                    warnOnce("Failed to send state or HTTP requests are disabled in Studio.")
                end

                local route = string.format("/pull?client_id=%s&timeout=%s", HttpService:UrlEncode(clientId), tostring(Config.pullTimeoutSeconds))
                local ok, response = request("GET", route, nil, Config.requestTimeoutSeconds + Config.pullTimeoutSeconds)
                if ok and response and response.command then
                    local command = response.command
                    local commandOk, result = executeCommand(command)
                    local errorMessage = nil
                    if not commandOk then
                        errorMessage = type(result) == "table" and result.message or tostring(result)
                    end
                    local resultOk = request("POST", "/command-result", {
                        client_id = clientId,
                        command_id = command.command_id,
                        ok = commandOk,
                        result = result,
                        error = errorMessage,
                    }, Config.requestTimeoutSeconds)
                    if not resultOk then
                        warnOnce("Failed to submit command result back to WinRemote harness.")
                    end
                elseif not ok then
                    warnOnce("Failed to poll WinRemote harness. Check harnessUrl and Studio HTTP settings.")
                end

                task.wait(Config.heartbeatSeconds)
            end
        end)
        """
    )


def export_studio_harness_files(output_dir: str | Path, *, harness_url: str = "http://127.0.0.1:51234") -> list[Path]:
    """Write Roblox Studio harness files to a local directory."""
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    files = {
        "WinRemoteHarnessConfig.lua": _config_lua(harness_url),
        "WinRemoteHarnessNamedTests.lua": _named_tests_lua(),
        "WinRemoteHarness.server.lua": _server_script_lua(),
        "README.txt": dedent(
            """\
            Place these files in ServerScriptService inside your Roblox Studio project:

            - WinRemoteHarness.server.lua
            - WinRemoteHarnessConfig.lua
            - WinRemoteHarnessNamedTests.lua

            Requirements:
            - Studio Game Settings > Security > Enable HTTP Requests = ON
            - Start the local harness before running Studio playtests
            - Tag checkpoint parts with WinRemoteCheckpoint and optionally set the CheckpointId attribute
            """
        ),
    }
    written: list[Path] = []
    for name, content in files.items():
        path = target / name
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written
