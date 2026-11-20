# Alis-Companion agent workflow — repository-owned GitHub Issues

These rules apply to Local and Cloud AI editing Companion. The canonical task tracker is **[Alis-Companion GitHub Issues](https://github.com/llocphann/Alis-Companion/issues)** (not Alis Issues). Issues migrated from Alis keep their stable `AC-*` / `VIS-*` codes; old Alis Issues marked MOVED are closed historical links.

1. Work on **Alis-Companion `main`**. Read current HEAD and affected files before every edit; never alter Alis `stable` or modify user data without explicit approval. The Alis shell host is on `dev`; changes to host source are separately coordinated there.
2. For new or changed Companion tasks, search **open and closed** Issues here by code and behavior, including historical Alis redirects. Update or reopen the matching canonical Issue; create a new one only for a distinct outcome. Never store active tasks or progress in Markdown.
3. Preserve task codes `AC-F##`, `AC-R##`, `AC-I##` and other existing Companion codes. Label one type (`enhancement` / `bug` / `documentation`), primary `area:companion`, and `complexity:high`, `status:blocked`, `status:deferred` or `status:awaiting-owner` only when warranted.
4. Use concise **Goal / Constraints / Done when** descriptions. Post meaningful milestone comments with exact Companion and Alis host SHAs, test exit/PASS/FAIL/SKIP and remaining blockers; never imply native GPU/Wayland tests passed when unrun.
5. Unfinished Issues remain **open**; bot-reported complete remains **open awaiting Owner verification**; close as completed only after explicit Owner approval. Preserve existing `AC-F01`–`AC-F10` final-phase deferral unless Owner changes priority.
6. Cross-repository host work: the Issue for Companion-owned behavior remains **here**; link a separately scoped Alis or Alis-Intergration dependency if necessary. Do not create a duplicate active backlog in Alis.
7. Preserve original shader/animation quality, optional/default-off behavior, security and consent, existing data and compatibility. Do not automatically download models, activate sensors, execute privileged services or touch an Obsidian vault. Validate with focused tests and real Quickshell/Niri Owner acceptance as separate gates.

8. **Strict functional preservation for optimizations and improvements:** Inventory original Aqua/Octo behavior and its dependent host/package contracts (rendering/shaders, animation clips and entry/exit transitions, pointer/grip/portal/focus, chat, preferences, privacy, multi-output and disabled/default-off behavior). Compare before/after at pinned Companion + Alis SHAs, rerun affected original and cross-feature tests, and preserve every working or Owner-accepted function. No silent feature removal, weakened quality or test-oracle changes to make an optimization pass; any intentional trade-off requires explicit prior Owner approval.
9. **Patch existing data, not only new data:** A bugfix affecting persisted Companion state must support existing user preferences, old chat/history, memories, journal/task references, older release/schema versions and cached runtime state alongside newly created records. Trace affected load/save/refresh paths. When necessary, provide versioned, idempotent, restart-safe migration/backfill or read-time compatibility; preserve IDs, timestamps, user edits, revocation/permissions and unrelated bytes. Never silently reset, duplicate, drop or overwrite older records. Test upgrade using sanitized historical fixtures, mixed old/new content, repeated migration, failure/retry and restart/rollback. Do not mutate a real user vault or private user data in tests.
10. **Release/Owner gate:** Attach exact source and host SHAs and scoped/full PASS/FAIL/SKIP/UNRUN receipts for fresh and legacy data, behavior and visuals. Keep unresolved regressions or outdated user data OPEN for repair; do not claim DONE or Owner-ready solely because source fixtures pass. Real installed Quickshell/Niri, GPU, both casts, and Owner acceptance remain separate mandatory gates when affected.
11. **Standalone `/swarm`, Chatbox Right Sidebar, Concise Non-Overlapping UI, Deterministic `Escape` & Silent Verification:** Companion `/swarm` is 100% standalone inside `Alis-Companion` (`swarm_store.py` + `agent_runtime.py` + `AquaSwarmPane.qml`; never connect to or call Antigravity's `swarm` tools). Trigger via `/swarm` in Chatbox or the top-right Right Sidebar toggle (`Board` | `Members` | `Graph`), auto-expanding popup width when open. The Right Sidebar is strictly for display & management with **no separate text inputs** — all prompts and `@member` messages share the Chatbox composer. Inline Left Edgebar `Pocket` uses `embeddedPocketOpen` (never `AquaPocket.show()`). Embedded Companion AI settings must use responsive breakpoints and `Layout.minimumWidth: 0` so controls/text never overlap or clip. Keep `WlrKeyboardFocus.OnDemand` active while `popupOpen` is true and route `Escape` cleanly through `AbyssPerimeter` (`hasPerimeterEscapeRouter` / `handleEscape()`). Keep Companion UI copy concise and center button icon+label pairs cleanly (`anchors.centerIn: parent`). All tests and stutter probes must run in 100% Silent / Zero-Focus mode (`Alis/.agents/skills/alis-silent-verify/SKILL.md`).
12. **User-Facing `README.md`, Calm Bubble Thinking, Hybrid UI+Deep Thinking, Ephemeral Mascot Chat & Single-Voice Streaming TTS:** Keep `README.md` strictly user-facing with real feature showcases and visual screenshots under `assets/screenshots/` (no internal regression ledgers, test logs, or links to retired `to-do/` or `archive/` files; internal architecture belongs in `docs/` or `AGENTS.md`). Preserve slow, unchanged orbiting bubbles while idle (`thinkingBlend === 0`) and keep active **Bubble-Integrated Thinking** calm (`thinkingDriftPhase` at `1.25x` idle speed, `thinkingPhase` `3600ms`, `WaterDropletBody.qml` + `AquaThinkingAura.qml` with 4 stage tints `thinking`/`deep`/`tool`/`drafting` — never fast spin, overhead multi-ring clutter, or 3-dot popups). Handle mixed UI+Deep prompts via a two-stage pipeline (`hybrid_ui_thinking`: Stage 1 UI/Desktop grounding with `effort="off"` -> Stage 2 Deep Thinking reasoning over Stage 1 observations), never collapsing both intents into one. Generate lightweight context-aware **ephemeral Mascot remarks** (`ambient_commentary.py` + `AquaMind.showMascotRemark`) strictly outside chat history/SQLite/TTS, never cold-loading local models, with opt-in default-off ephemeral visual peek (`<= 480px` deleted in `finally:`) and privacy/battery guards. Maintain sound-reactive voice input with a **single fixed multilingual voice** (`desktop_control.CANONICAL_NEURAL_VOICE = "en-US-AvaMultilingualNeural"` via `edge-tts` + `tts_stream_player.py` sentence-chunked streaming playback; zero male/`espeak-ng`/`vi-VN-HoaiMyNeural` fallback switching), Dual-Channel `displayText` / `spokenText` separation with natural unit speech distillation (`_clean_text_for_tts`), Compound Multi-Intent Orchestration (`compound_task`), Cloud-AI BLUF synthesis-first presentation with Arch/Niri/Alis platform grounding, smart local system/weather tool routing, Auto Thinking Effort selection, and Unified Nexus Vault RAG (`90_System/99_RAG/` Markdown notes + external `nexus_rag.db` under `$XDG_STATE_HOME/quickshell/user/`).

## Behavioral guidelines to reduce common LLM coding mistakes

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

Tradeoff: These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

These guidelines are working if: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

[Companion Issues](https://github.com/llocphann/Alis-Companion/issues) · [Alis host Issues](https://github.com/llocphann/Alis/issues) · [Integration Issues](https://github.com/llocphann/Alis-Intergration/issues).
