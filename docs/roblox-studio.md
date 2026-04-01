# Roblox Studio Harness

WinRemote's `RobloxStudio*` tools can work in two modes:

- Desktop-only mode: Studio is driven through window focus, keyboard, mouse, screenshots, OCR, and logs.
- Harness-backed mode: a local HTTP harness plus a small Luau package inside the Studio playtest exposes structured state and test actions.

If you want unattended playtests, use the harness-backed mode.

## Start The Local Harness

Run this on the Windows machine where WinRemote and Roblox Studio are installed:

```bash
winremote-mcp roblox-studio serve-harness
```

Default bind:

- `http://127.0.0.1:51234`

You can override host or port:

```bash
winremote-mcp roblox-studio serve-harness --host 127.0.0.1 --port 51234
```

## Export The Studio Files

```bash
winremote-mcp roblox-studio export-harness --output-dir .\\roblox-studio-harness
```

This writes:

- `WinRemoteHarness.server.lua`
- `WinRemoteHarnessConfig.lua`
- `WinRemoteHarnessNamedTests.lua`
- `README.txt`

Copy the three `.lua` files into `ServerScriptService` in your Roblox Studio project.

## Studio Requirements

- Turn on `Game Settings > Security > Enable HTTP Requests`
- Keep `WinRemoteHarnessConfig.lua` pointed at the correct harness URL
- Tag checkpoint parts with `WinRemoteCheckpoint`
- Optionally add a `CheckpointId` attribute to checkpoint parts for stable IDs

## What The Harness Reports

The playtest script reports structured state to WinRemote, including:

- active client id
- player list
- player health
- player positions
- known checkpoints
- current named test
- last named test result

That state is returned through `RobloxStudioGetTestState`.

## Supported Harness Actions

- `RobloxStudioResetCharacter`
- `RobloxStudioTeleportToCheckpoint`
- `RobloxStudioRunNamedTest`

These queue commands through the local harness and wait for the playtest script to execute them.

## Named Tests

Customize `WinRemoteHarnessNamedTests.lua` for project-specific validation.

The default file includes a `Smoke` test. Named test functions receive:

- `context`
- `payload`

Available `context` helpers:

- `context.buildState()`
- `context.resetCharacter(payload)`
- `context.teleportToCheckpoint(checkpointId, payload)`
- `context.waitSeconds(seconds)`

Return one of these shapes:

```lua
return true, { message = "passed" }
```

```lua
return false, { message = "failed because ..." }
```

## Typical Flow

1. Start `winremote-mcp --profile chatgpt` or let VS Code Insiders run `winremote-mcp copilot-launch`
2. If you are not using `copilot-launch`, start `winremote-mcp roblox-studio serve-harness`
3. Open Roblox Studio
4. Put the exported harness files into `ServerScriptService`
5. Enable HTTP requests in Studio settings
6. Let ChatGPT or GitHub Copilot Chat edit code, launch playtests, and call `RobloxStudioGetTestState` or `RobloxStudioRunNamedTest`

## Recommended Tool Usage

- Use `RobloxStudioRunPlaytest` to enter Play Solo
- Use `RobloxStudioGetOutput` and `RobloxStudioGetErrors` for Studio log feedback
- Use `RobloxStudioGetTestState` for structured runtime state
- Use `RobloxStudioRunNamedTest` for explicit pass/fail checks
- Use screenshots and raw input as fallback, not as the primary oracle
