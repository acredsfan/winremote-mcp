# ChatGPT Full-MCP Setup

WinRemote's `chatgpt` profile is for ChatGPT full-MCP chat usage against a remote Windows host. It favors semantic GUI tools over raw coordinate loops so the model can work more like an operator and less like a pixel script.

## Recommended Startup

ChatGPT requires a remote MCP endpoint. In practice, use HTTPS and expose the server on a reachable host/IP:

```bash
winremote-mcp \
  --host 0.0.0.0 \
  --port 8090 \
  --ssl-certfile cert.pem \
  --ssl-keyfile key.pem \
  --profile chatgpt
```

If you want OAuth instead of a static bearer key:

```bash
winremote-mcp \
  --host 0.0.0.0 \
  --port 8090 \
  --ssl-certfile cert.pem \
  --ssl-keyfile key.pem \
  --oauth-client-id my-client \
  --oauth-client-secret my-secret \
  --profile chatgpt
```

## Config File

```toml
[server]
host = "0.0.0.0"
port = 8090
profile = "chatgpt"
ssl_certfile = "C:/Users/you/cert.pem"
ssl_keyfile = "C:/Users/you/key.pem"

[security]
oauth_client_id = "my-client"
oauth_client_secret = "my-secret"
```

## What The ChatGPT Profile Exposes

The `chatgpt` profile keeps the tool surface focused on semantic desktop work:

- `ObserveScreen`, `UIFind`, `UIWatch`, `UIAct`, `UISequence`
- `Snapshot`, `OCR`
- `FocusWindow`, `App`, `Shortcut`
- `Shell`, `FileRead`, `FileWrite`, `FileList`, `FileSearch`
- `GetClipboard`, `SetClipboard`, `GetSystemInfo`, `ListProcesses`, `Notification`

It intentionally leaves out raw coordinate tools like `Click`, `Move`, `Scroll`, and broader admin/destructive tools unless you opt into them with `--tools` or a different profile.

## Connector Setup In ChatGPT

Create a custom MCP connector in ChatGPT and point it at:

- `https://YOUR_HOST:8090/mcp`

Choose one auth path:

- Bearer token: start WinRemote with `--auth-key ...` and configure the connector to send `Authorization: Bearer ...`
- OAuth: enable `--oauth-client-id` / `--oauth-client-secret`; WinRemote exposes OAuth discovery, authorization, token, registration, and refresh-token support

If your OAuth client requests `offline_access`, WinRemote issues refresh tokens and supports the `refresh_token` grant so the connector can stay authorized longer.

## Recommended Tool Workflow

For most GUI tasks, use this order:

1. `FocusWindow` or `App` to target the application.
2. `ObserveScreen` to learn whether the UI changed without requesting pixels.
3. `UIFind` to locate semantic targets by label/class/OCR text.
4. `UIAct` for one action plus observation/wait.
5. `UISequence` for short multi-step routines in one round trip.
6. `Snapshot` only when the model truly needs pixels.

## Prompt Patterns

- "Observe the current window and tell me the next low-bandwidth step."
- "Find the Save button and click it, then wait for Save Complete."
- "Run this GUI sequence server-side and return a compact summary."
- "Use semantic tools first. Only request a screenshot if the UI cannot be understood from ObserveScreen or UIFind."

## Notes

- `--tools` and `--exclude-tools` still take precedence over `--profile chatgpt`.
- The `chatgpt` profile targets ChatGPT full-MCP chat usage, not search/fetch connector or deep-research workflows.
