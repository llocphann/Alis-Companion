# Alis-Companion documentation

Core references for the **Alis-Companion** package (`hostApi: 1`):

- [Package overview](../README.md) — features, screenshots, and standalone installation
- [Context protocol v1](ALIS_COMPANION_CONTEXT_PROTOCOL_V1.md) — dormant host-to-companion behavioral signal contract (`AquaContextProtocol.js`)
- [Scripts reference](../scripts/README.md) — developer verification and installer utilities
- [Agent & workflow guidelines](../AGENTS.md) — repository ownership, UI/UX contracts, and verification standards

## Unified Nexus Vault RAG Storage

Human-readable Markdown knowledge notes live inside the active Nexus Vault under `90_System/99_RAG/` (`README.md`, `RAG_Sources.md`, `Canonical/`, `Personal/`, and `Knowledge/`). The binary SQLite FTS5 index (`nexus_rag.db`) and external source manifests stay outside the Vault under `$XDG_STATE_HOME/quickshell/user/` (`~/.local/state/quickshell/user/`) so Syncthing and Git never encounter binary database conflicts.

**Issue tracker:** [Alis-Companion GitHub Issues](https://github.com/llocphann/Alis-Companion/issues).
