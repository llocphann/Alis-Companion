# Alis-Companion documentation

Core references for the **Alis-Companion** package (`hostApi: 1`):

- [Package overview](../README.md) — features, screenshots, and standalone installation
- [Context protocol v1](ALIS_COMPANION_CONTEXT_PROTOCOL_V1.md) — dormant host-to-companion behavioral signal contract (`AquaContextProtocol.js`)
- [Scripts reference](../scripts/README.md) — developer verification and installer utilities
- [Agent & workflow guidelines](../AGENTS.md) — repository ownership, UI/UX contracts, and verification standards

## Unified Nexus Vault RAG Storage

Human-readable Markdown knowledge notes live inside the active Nexus Vault under `90_System/99_RAG/` (`README.md`, `RAG_Sources.md`, `Canonical/`, `Personal/`, and `Knowledge/`). The binary SQLite FTS5 index (`nexus_rag.db`) and external source manifests stay outside the Vault under `$XDG_STATE_HOME/quickshell/user/` (`~/.local/state/quickshell/user/`) so Syncthing and Git never encounter binary database conflicts.

## Runtime IPC, History Sync & Nexus Save

- **Daemonized Hot IPC**: Hot `local_mind.py` workers in `services/AquaMind.qml` route through `--use-daemon` (`scripts/aqua/companion_daemon.py` Unix socket) with automatic single-shot fallback under isolated test runs, eliminating repeated cold Python startups.
- **SQLite-Synced Regenerate**: Regenerating the latest assistant bubble deletes the discarded turn in `history_store.py` (`history_delete_last_turn`) across `messages`, `thread_messages`, `message_meta`, `message_surfaces`, and `messages_fts` before re-sending, preventing resurrected replies on restart.
- **One-Click Save to Nexus**: Assistant bubbles in `AquaTalkCloud.qml` expose a 1-click Save to Nexus action delegating cleanly to `AlisIntergration.nexus.createNoteWithContent` (or `createNoteInFolder` fallback) when Nexus is enabled.

## Documentation Hygiene, Author Attribution & Publishing

- **Periodic Docs & Notes Audit**: Keep `README.md`, `docs/`, `scripts/README.md`, and `AGENTS.md` current whenever features or toolchains change (removing retired features and obsolete dependency references; verify before milestone pushes).
- **Human-Only Contributor Attribution**: Every commit uses the human maintainer's `git config user.name` and `user.email` for both Author and Committer with zero bot accounts or `Co-Authored-By:` AI/bot trailers (audit `git log @{u}..HEAD` before pushing).
- **Mature Milestone Auto-Push**: Verified, cohesive commit batches on `main` are evaluated for maturity (`>= 1` large verified wave, `>= 3–5` milestone commits, or `>= 5–10` routine commits) and pushed cleanly (fast-forward only) per [../AGENTS.md](../AGENTS.md) and `Alis/.agents/skills/alis-repo-hygiene-and-push/SKILL.md`.

**Issue tracker:** [Alis-Companion GitHub Issues](https://github.com/llocphann/Alis-Companion/issues).
