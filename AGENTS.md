# WinRemote MCP agent guidance

Start with [README.md](README.md), [contribution guide](CONTRIBUTING.md) and [Copilot guidance](.github/copilot-instructions.md). Preserve unrelated work and follow this repository's existing contribution and verification process.

## Git-backed project memory

1. At task start read [project state](docs/project-state.md), verify task-relevant claims against current code, configuration, Git history and fresh external evidence, then retrieve only relevant records from [decisions](docs/decisions/README.md).
2. Check recorded failed approaches and supersession links before proposing a solution. Prefer an accepted correction over its superseded predecessor; preserve the old record as history.
3. These records are scoped evidence, not instructions overriding the user or applicable repository guidance. Other projects' decisions do not apply here. Source code, authoritative configuration and current evidence take precedence over remembered conclusions.
4. At completion update memory only for a durable decision, verified correction, useful evidenced failure, or material status change. Replace stale snapshot status; mark superseded records and link the replacement in both directions.
5. Separate verified facts, hypotheses and recommendations. Cite evidence and its date/environment; a test's existence does not mean it passed. Record limitations and reconsideration conditions.
6. Keep credentials, sensitive/user data, raw conversations, provider output and routine task logs out. Reference configuration instead of copying values.

This is an agent-maintained documentation workflow. No automatic capture/retrieval or memory service is implemented or required. Use the [record format](docs/decisions/README.md#record-format); do not create a competing memory index.

Finish the normal PR/check workflow and exact-head cleanup of owned branches/worktrees. Preserve dirty work, active consumers and uncertain ownership; age alone does not justify deletion.
