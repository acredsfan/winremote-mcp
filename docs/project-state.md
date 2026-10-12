# WinRemote MCP project state

Reviewed: 2026-10-12. Scope: Git-tracked source/document inspection at `5c0c82f4a274fa4293707b00530c220e58899f82`; this is a snapshot, not a live monitor.

## Verified source state

- Profiles/configuration own exposed tools and permissions.
- The human handoff state is currently process-local, with manual or explicit timeout resume.
- Screenshot payload size affects client conversation growth; deleting temporary files does not remove attached images.

## Unresolved and next action

Installed service/desktop access, current target window and cross-restart handoff persistence are unverified. Git documentation does not grant desktop clearance or enable a tool tier.

Recommendation: inspect the selected profile, current target/UI state and applicable handoff state before changing automation behavior.

## Recovery sources

- [Configuration](../src/winremote/config.py)
- [Handoff implementation](../src/winremote/handoff_state.py)
- [Contribution guide](contributing.md)
- [Text-first inspection guidance](../.github/copilot-instructions.md)
Start with [decisions and lessons](decisions/README.md). Verify the relevant source and fresh status before acting. Update this snapshot when material status changes; preserve durable reasoning in decision records.

No application, deployment, bench or hardware tests were run for this documentation change. Linked tests establish source contracts, not a new execution pass.
