# GitHub Copilot CLI Setup

WinRemote's `copilot-cli` profile is tuned for terminal-first GitHub Copilot CLI workflows.

Use this profile when your agent is operating from a shell/CLI session instead of Copilot Chat in VS Code.

If you already use `winremote-tray` to auto-start and manage the WinRemote server, you usually do **not** need a separate server-launch command in Copilot CLI. In that case, Copilot CLI should connect to the already-running local server over **HTTP**.

If you are looking at the setup wizard in your screenshot, fill the fields like this:

- **Unique name for this MCP server**: `winremote` or `winremoteCopilotCli`
- **Server Type**: `HTTP`
- **URL**: `http://127.0.0.1:8090/mcp`
- **Environment Variables**: leave blank
- **Client ID**: leave blank for the tray-managed local server setup; if you intentionally configured OAuth in the tray, use that Client ID here
- **Tools**: `*`

## Recommended Startup

There are two valid setups:

### A. Tray-managed server (recommended if you use `winremote-tray`)

1. Start `winremote-tray`.
2. Make sure the tray is configured to auto-start the server.
3. Select the `copilot-cli` profile in the tray/settings.
4. If you need OAuth, set `OAuth client ID` / `secret` in the tray settings first.
5. In Copilot CLI, add an MCP server that connects to the running local server over **HTTP**.

For authorization or headers, leave them blank unless you enabled auth on the server. If your wizard still shows a Client ID field, leave it empty unless you intentionally configured OAuth on the WinRemote server.

This is the best fit when the tray app is already keeping the server alive.

### B. Direct launch from Copilot CLI

Use the dedicated launcher if you want Copilot CLI itself to start the server process:

```bash
winremote-mcp copilot-cli-launch
```

What it does:

1. starts WinRemote over **stdio**
2. uses profile **`copilot-cli`**
3. does **not** auto-start the Roblox Studio harness

To launch the harness from the GUI instead, open the dashboard and use the **Roblox Studio Harness** card on the Status tab or the tray menu.

If you need to run manually without the helper command, this is equivalent:

```bash
winremote-mcp --transport stdio --profile copilot-cli
```

## Copilot CLI MCP Server Entry

Use the following server entry in your Copilot CLI MCP configuration:

```json
{
  "servers": {
    "winremoteCopilotCli": {
      "command": "python",
      "args": [
        "-m",
        "winremote",
        "copilot-cli-launch"
      ],
      "env": {
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

If your Copilot CLI version uses a different path or registration command for MCP server configuration, keep the same `command` and `args` values above and apply them through your version's MCP config workflow.

If your Copilot CLI wizard is already pointed at a running tray-managed server, prefer the HTTP setup above instead of `copilot-cli-launch`.

## Profile Notes

`copilot-cli` extends the `copilot` profile and keeps a conservative policy for potentially risky system actions.

Current profile file:

- `src/winremote/profiles/copilot-cli.toml`

## Quick Verification Checklist

1. Start WinRemote with `winremote-mcp copilot-cli-launch`.
2. Confirm Copilot CLI can see/connect to the `winremoteCopilotCli` MCP server.
3. Run one simple read-only action (for example, list monitors or observe the active window).
4. If needed, inspect startup options and run the equivalent manual command:

```bash
winremote-mcp copilot-cli-launch --help
winremote-mcp --transport stdio --profile copilot-cli
```

## Common Connection Pitfalls

- Choosing `STDIO` when `winremote-tray` is already running the server. In that case, use `HTTP` and point to `http://127.0.0.1:8090/mcp`.
- Using `copilot-launch` (Chat-focused) instead of `copilot-cli-launch` (CLI-focused).
- Using HTTP transport config for a CLI session that expects stdio.
- Pointing the CLI to a stale Python environment where `winremote-mcp` is not installed.
- Forgetting to restart Copilot CLI after updating MCP server configuration.

## Related Guides

- [GitHub Copilot Chat Setup](copilot-chat.md)
- [Usage Guide](usage.md)
