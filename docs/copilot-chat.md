# GitHub Copilot Chat Setup

WinRemote's `copilot` profile is tuned for GitHub Copilot Chat in **VS Code Insiders**.

Unlike the `chatgpt` profile, this profile assumes Copilot already has strong built-in workspace editing, search, and terminal tools. The WinRemote MCP server is therefore focused on the things Copilot cannot do natively inside the editor:

- launch and focus desktop applications
- interact with custom GUIs like Roblox Studio
- observe windows and UI changes semantically
- run Roblox Studio playtests
- use the Roblox Studio harness for structured runtime state and named tests

## Recommended Startup

Use the dedicated launcher:

```bash
winremote-mcp copilot-launch
```

What it does:

1. checks whether the local Roblox Studio harness is already healthy at `http://127.0.0.1:51234`
2. starts the harness in the background if needed
3. starts WinRemote over **stdio** with `--profile copilot`

That makes it a good fit for workspace MCP configuration in VS Code Insiders.

## Workspace MCP Configuration

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "winremoteCopilot": {
      "command": "python",
      "args": [
        "-m",
        "winremote",
        "copilot-launch"
      ],
      "env": {
        "PYTHONPATH": "${workspaceFolder}/src",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

This repository already includes that file for you.

## VS Code Insiders Settings

The workspace also enables MCP auto-start:

```json
{
  "chat.mcp.autostart": true
}
```

With that in place, opening the workspace in VS Code Insiders is enough for Copilot Chat to discover and start the WinRemote server automatically.

## What The Copilot Profile Exposes

The `copilot` profile keeps tools that match a human developer workflow:

- `ObserveScreen`, `UIFind`, `UIWatch`, `UIAct`, `UISequence`
- `RobloxStudioInspectUI`, `RobloxStudioOpenTab`, `RobloxStudioEnsurePanel`
- `Snapshot`, `AnnotatedSnapshot`, `UIMap`, `UIMapJson`, `UIClick`, `OCR`
- `Click`, `Type`, `Move`, `Scroll`, `Shortcut`, `Wait`
- `FocusWindow`, `App`, `ReconnectSession`
- `GetClipboard`, `SetClipboard`, `GetSystemInfo`, `ListProcesses`, `Notification`
- `TailFile`, `CaptureFailureBundle`
- all `RobloxStudio*` playtest and harness-backed tools

It intentionally leaves out broad file and shell mutation tools such as `Shell`, `FileRead`, `FileWrite`, `FileList`, and `FileSearch`, because Copilot already has better native tools for local repo work.

## Roblox Studio Flow

For Roblox Studio work, the ideal loop is:

1. Let Copilot edit the workspace with its built-in file and terminal tools.
2. Use `App` or `FocusWindow` to target Roblox Studio.
3. Use `RobloxStudioInspectUI`, `RobloxStudioOpenTab`, and `RobloxStudioEnsurePanel` first for Studio editor layout and navigation.
4. Use `ObserveScreen`, `UIFind`, `UIAct`, or `UISequence` for detailed low-bandwidth GUI work.
   - When Studio exposes mostly custom-rendered surfaces, `UIFind`, `UIAct`, and `UISequence` automatically retry with a Studio-aware OCR fallback over ribbon tabs and major dock regions (`Toolbox`, `Explorer`, `Properties`, `Output`) before escalating to screenshots.
5. Use `RobloxStudioRunPlaytest` to launch Play Solo.
6. Use `RobloxStudioGetTestState`, `RobloxStudioRunNamedTest`, `RobloxStudioResetCharacter`, or `RobloxStudioTeleportToCheckpoint` for harness-backed operations.
7. Fall back to screenshots and raw input only when semantic tools are not enough.

## Example Prompts

- "Open Roblox Studio and bring it to the foreground."
- "Use semantic tools first and tell me what changed in Roblox Studio after I press Play."
- "Run a Studio playtest, then call the Smoke named test through the harness."
- "Find the Toolbox panel and interact with it like a human developer would."
- "Use workspace tools to edit the script, then use WinRemote to run the playtest and inspect the output."

## Notes

- `--tools` and `--exclude-tools` still override the profile if you launch WinRemote manually.
- `copilot-launch` is intended for **local stdio** use in VS Code Insiders.
- If you want remote HTTP access or OAuth-based external connectors, use the `chatgpt` or `default` flows instead.
