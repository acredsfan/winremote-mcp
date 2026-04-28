# API Reference

## Scope and source of truth

This page is a **practical reference** for commonly used WinRemote MCP tools.

Because tool signatures can evolve, your MCP client's tool schema (for example, Copilot/Claude tool metadata) is the runtime source of truth for exact argument contracts.

## Core desktop and UI tools

### Snapshot
Capture a screenshot plus textual desktop context (windows + interactive elements).

**Common arguments:**
- `use_vision` (bool): include image payload
- `quality` (int): JPEG quality
- `max_width` (int): optional downscale width
- `monitor` (int): `0` for all monitors, or specific monitor id
- `window_title` (str, optional): capture only a specific window (fuzzy/contains title match)

### AnnotatedSnapshot
Capture screenshot with numbered UI overlays for mapped interactive elements.

**Common arguments:**
- `max_elements` (int)
- `quality` (int)
- `max_width` (int)
- `window_title` (str, optional): restrict capture/annotation to one window

### ObserveScreen
Low-bandwidth GUI observation (no screenshot attachment by default).

Useful for:
- detecting whether UI changed
- getting changed regions
- obtaining searchable previews before using OCR/snapshots

### UIMap / UIMapJson
Map controls and return coordinate-rich UI structure.

Useful for:
- absolute screen clicks (`center` / `rect`)
- window-relative automation (`relative_center` / `relative_rect`)

### UIFind / UIClick / UIAct / UISequence / UIWatch
Semantic-first UI interaction stack:

- `UIFind`: find candidate controls
- `UIClick`: click best semantic match
- `UIAct`: find + act + optionally observe/wait
- `UISequence`: run compact multi-step workflows server-side
- `UIWatch`: diff UI map changes over time

## Input and interaction tools

- `Click`
- `Type`
- `Scroll`
- `Move`
- `Shortcut`
- `Wait`
- `KeyDown` / `KeyUp`
- `MouseDown` / `MouseUp`
- `MouseMoveRelative` / `MouseLook`
- `WaitForRegionText`
- `WaitForImageChange`

## Window and app tools

- `FocusWindow`
- `MinimizeAll`
- `App` (`launch`, `switch`, `resize`)

## Diagnostics and debugging helpers

- `TailFile`
- `CaptureFailureBundle`
- `AssertWindowActive`
- `AssertProcessRunning`

## Roblox Studio tools

### Studio editor/navigation
- `RobloxStudioInspectUI`
- `RobloxStudioOpenTab`
- `RobloxStudioEnsurePanel`

### Playtest and harness tools
- `RobloxStudioRunPlaytest`
- `RobloxStudioStopPlaytest`
- `RobloxStudioGetOutput`
- `RobloxStudioGetErrors`
- `RobloxStudioGetTestState`
- `RobloxStudioResetCharacter`
- `RobloxStudioTeleportToCheckpoint`
- `RobloxStudioRunNamedTest`

## System and admin tools

- `Shell`
- `GetClipboard` / `SetClipboard`
- `ListProcesses` / `KillProcess`
- `GetSystemInfo`
- `Notification`
- `LockScreen`
- `ReconnectSession`

## File tools

- `FileRead`
- `FileWrite`
- `FileList`
- `FileSearch`
- `FileDownload`
- `FileUpload`

## Registry, services, tasks, and network

- Registry: `RegRead`, `RegWrite`
- Services: `ServiceList`, `ServiceStart`, `ServiceStop`
- Scheduled tasks: `TaskList`, `TaskCreate`, `TaskDelete`
- Network: `Scrape`, `Ping`, `PortCheck`, `NetConnections`
- Event log: `EventLog`

## Security notes

- Prefer localhost-only binding unless remote access is required.
- Use authentication (`--auth-key`) for remote HTTP access.
- Keep destructive tools (tier-3 style operations) disabled unless needed.

For setup and operational examples, see:

- `docs/usage.md`
- `docs/copilot-chat.md`
- `docs/chatgpt.md`
