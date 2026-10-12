# WIN-001: Text-first inspection and truthful handoff persistence

Recorded/reviewed: 2026-10-12.
Project/component scope: WinRemote MCP / UI payloads and human pause state.
Status: accepted.
Supersedes: none.

## Decision or observed outcome

Verified source facts: screenshots are held in memory and sent to the client; deleting temp files cannot reduce attached conversation payloads. `handoff_state._STATE` is a module-global dataclass, not a durable database. A configured positive timeout can resume that process-local handoff.

## Reason and alternatives

Repeated screenshots inflate client context; semantic UI/text inspection is preferred when it provides the needed evidence. A pause flag does not prove durable cross-restart coordination, continuous screen vision or human clearance. Respect the actual user's current authorization and pause constraints even if the tool flag expires.

## Evidence

- [Existing inspection guidance](../../.github/copilot-instructions.md).
- [Handoff implementation](../../src/winremote/handoff_state.py).
- [Manual/timeout handoff specifications](../../tests/test_handoff.py) and [tool guard specifications](../../tests/test_handoff_tools.py).
- [Profile/configuration authority](../../src/winremote/config.py).

## Environment and limitations

Source inspection only; no UI interaction or handoff timeout executed here. Tool results are discrete observations and can become stale. Configuration, not this note, owns permission/tier defaults.

## Reconsideration

Revisit if persistent handoff storage or streaming observation is implemented and verified; until then do not claim restart durability or continuous vision.
