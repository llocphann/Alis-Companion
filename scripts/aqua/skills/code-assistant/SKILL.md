---
name: code-assistant
description: Explore project structure (repo map), search symbols, run LSP diagnostics, and execute sandboxed project tests.
triggers: ["code-assistant", "code review", "repo map", "lsp", "diagnostics", "kiem tra code", "review code"]
---

# Code Assistant Skill

Use this skill when the user wants to inspect a codebase, find symbols/definitions, check syntax or LSP diagnostics, or run project unit tests.

## Workflow
1. Build a token-budgeted repository map (`build_repo_map`) to understand key modules and entrypoints.
2. Locate relevant symbols or references (`query_lsp` / `search_code`).
3. Check diagnostics (`query_lsp(action="diagnostics")`) and run tests (`run_project_tests`) inside the sandbox.
