# winremote-mcp for GitHub Copilot Chat

## Setup

### 1. Install on Windows

```bash
pip install winremote-mcp
```

If you are working from this repository directly, an editable install also works well:

```bash
pip install -e .
```

### 2. Open the workspace in VS Code Insiders

This repository already includes:

- `.vscode/mcp.json`
- `.vscode/settings.json`

That configuration tells Copilot Chat to launch:

```bash
winremote-mcp copilot-launch
```

The launcher automatically starts the Roblox Studio harness in the background if needed, then starts the MCP server over stdio using the `copilot` profile.

### 3. Verify in Copilot Chat

Open Copilot Chat in **Agent** mode and check the tools picker. You should see the WinRemote tools available through the `winremoteCopilot` server.

## Why the `copilot` profile exists

GitHub Copilot Chat already has strong built-in workspace tools for:

- editing files
- searching the codebase
- running terminal commands
- managing notebooks and tasks

The `copilot` profile therefore focuses on desktop interaction and Roblox Studio workflows instead of duplicating general local file and shell access.

## Best use cases

- launch and focus Windows applications
- inspect custom GUI state with semantic tools
- operate Roblox Studio like a human developer would
- run and monitor Studio playtests
- query harness-backed test state and named tests
- capture focused debugging bundles when UI automation goes sideways

## Suggested prompt patterns

- "Use workspace tools to change the script, then use WinRemote to open Roblox Studio and run the playtest."
- "Observe the current Studio window and tell me the next low-bandwidth step."
- "Run the Smoke test through the Roblox harness and summarize the result."
- "Drive the Studio UI semantically first; only use screenshots if needed."
