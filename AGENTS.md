# Alis-Companion agent workflow — repository-owned GitHub Issues

These rules apply to Local and Cloud AI editing Companion. The canonical task tracker is **[Alis-Companion GitHub Issues](https://github.com/llocphann/Alis-Companion/issues)** (not Alis Issues). Issues migrated from Alis keep their stable `AC-*` / `VIS-*` codes; old Alis Issues marked MOVED are closed historical links.

1. Work on **Alis-Companion `main`**. Read current HEAD and affected files before every edit; never alter Alis `stable` or modify user data without explicit approval. The Alis shell host is on `dev`; changes to host source are separately coordinated there.
2. For new or changed Companion tasks, search **open and closed** Issues here by code and behavior, including historical Alis redirects. Update or reopen the matching canonical Issue; create a new one only for a distinct outcome. Never store active tasks or progress in Markdown.
3. Preserve task codes `AC-F##`, `AC-R##`, `AC-I##` and other existing Companion codes. Label one type (`enhancement` / `bug` / `documentation`), primary `area:companion`, and `complexity:high`, `status:blocked`, `status:deferred` or `status:awaiting-owner` only when warranted.
4. Use concise **Goal / Constraints / Done when** descriptions. Post meaningful milestone comments with exact Companion and Alis host SHAs, test exit/PASS/FAIL/SKIP and remaining blockers; never imply native GPU/Wayland tests passed when unrun.
5. Unfinished Issues remain **open**; bot-reported complete remains **open awaiting Owner verification**; close as completed only after explicit Owner approval. Preserve existing `AC-F01`–`AC-F10` final-phase deferral unless Owner changes priority.
6. Cross-repository host work: the Issue for Companion-owned behavior remains **here**; link a separately scoped Alis or Alis-Intergration dependency if necessary. Do not create a duplicate active backlog in Alis.
7. Preserve original shader/animation quality, optional/default-off behavior, security and consent, existing data and compatibility. Do not automatically download models, activate sensors, execute privileged services or touch an Obsidian vault. Validate with focused tests and real Quickshell/Niri Owner acceptance as separate gates.

[Companion Issues](https://github.com/llocphann/Alis-Companion/issues) · [Alis host Issues](https://github.com/llocphann/Alis/issues) · [Integration Issues](https://github.com/llocphann/Alis-Intergration/issues).
