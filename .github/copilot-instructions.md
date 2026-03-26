# Copilot instructions for `winremote-mcp`

This repository exposes Windows desktop automation tools that can return large image payloads. In VS Code chat, image outputs increase conversation size quickly because the screenshot data is attached to the interaction. Treat screenshots as expensive.

## Prefer text-first desktop inspection

When working with WinRemote tools, use this order of preference:

1. `UIMapJson`, `UIFind`, or `UIWatch` for structure, coordinates, and UI changes
2. `OCR` for readable on-screen text
3. `Snapshot` with `use_vision=false` when only window and monitor context is needed
4. `Snapshot` with `use_vision=true` only when a visual check is required
5. `AnnotatedSnapshot` only when visual labeling is necessary for ambiguous targets
6. `ScreenRecord` only when motion or timing matters and no text/structure-based tool can answer the question

## Keep image outputs small

If an image tool is necessary, prefer lightweight parameters unless the user explicitly asks for maximum fidelity:

- `Snapshot`: use `quality=45-60` and `max_width=1280` by default
- Limit capture to a single `monitor` when possible
- Prefer targeted OCR or UI search over repeated full-screen snapshots
- Avoid back-to-back screenshots when `UIWatch` or a focused follow-up tool can detect the change
- `AnnotatedSnapshot`: use the smallest useful `max_elements`, `quality=45-60`, and `max_width=1280`
- `ScreenRecord`: keep it short and small, such as `duration<=2`, `fps<=3`, `max_width<=480`, unless the task truly needs more detail

## Live-view expectations

Do not assume the agent has continuous live screen vision. The available tools are request/response tools, so the agent sees the desktop through snapshots, OCR, UI maps, and other discrete tool results. If the user asks for "live" viewing, explain that the practical approximation is sparse, low-resolution snapshots only when needed.

## Avoid unnecessary screenshot churn

- Reuse the most recent textual understanding of the UI when possible
- Only request a fresh screenshot after a meaningful state change or when a prior view is no longer trustworthy
- If the task can be completed from coordinates, OCR, or UI search results, do not request another image
- When reporting on screen state, summarize the result in text instead of asking for another confirmatory screenshot unless necessary

## Important implementation note

Screenshots in this project are already handled in memory rather than being written to temporary files. Deleting temp files does not reduce VS Code chat size; reducing image frequency and payload size does.
