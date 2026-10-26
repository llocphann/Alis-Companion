# Alis-Companion: Mochi × Mak1zu × Alis — verified integration design

**Date:** 2026-10-09. **Nature:** comparative architecture/evidence and implementation contract; NOT a second active TODO. Active visual tasks remain in [Aqua/Octo Companion TODO](../to-do/cloud-bot/ABYSS_WATER_DROPLET_COMPANION.md); active AI/memory tasks remain in [Local AI TODO](../to-do/cloud-bot/AQUA_LOCAL_AI.md).

## Immutable inputs and scope

| Source | Revision inspected | Where its value lies |
| --- | --- | --- |
| [Mochi Desktop](https://github.com/miflow13/mochi-desktop/tree/471342631206c40d56bbba016578926e16ca8d0b) | `main@471342631206c40d56bbba016578926e16ca8d0b` | Semantic desktop context, distinct state/presentation/animation owners, intro → loop → outro, minimal agent lifecycle signals, non-punitive bond |
| [Mak1zu](https://github.com/snowarch/mak1zu/tree/a30fef7cc10324684fd4ae9123d9c29cbd00ca9e) | `main@a30fef7cc10324684fd4ae9123d9c29cbd00ca9e` | Persona, measurable short replies, output guard, memory with sources and deletion, pure quiet-time/backoff policy, prompts/procedures with budgets |
| [Alis](https://github.com/llocphann/Alis/tree/d31e54b9e7444c623f185b4d740344040a39b62a) | `dev@d31e54b9e7444c623f185b4d740344040a39b62a` | Stable Quickshell host, shared AI provider/session, Niri surfaces/fullscreen/game/locks, Todo/Obsidian, live Abyss theme |
| [Alis-Companion](https://github.com/llocphann/Alis-Companion) | `main` (see commits/CI receipts below) | Aqua/Octo actor, Aqua motion/presence/portal/curiosity, Companion native IPC, optional runtime bundle |

Source basis: Mochi's [state-machine audit](https://github.com/miflow13/mochi-desktop/blob/471342631206c40d56bbba016578926e16ca8d0b/docs/STATE_MACHINE_AUDIT.md), [terminal coworking](https://github.com/miflow13/mochi-desktop/blob/471342631206c40d56bbba016578926e16ca8d0b/src/mochi/presence/terminal_cowork.py), [agent lifecycle](https://github.com/miflow13/mochi-desktop/blob/471342631206c40d56bbba016578926e16ca8d0b/src/mochi/agent_activity.py), [AmbiSense](https://github.com/miflow13/mochi-desktop/blob/471342631206c40d56bbba016578926e16ca8d0b/docs/ambisense.md), [bond dialogue](https://github.com/miflow13/mochi-desktop/blob/471342631206c40d56bbba016578926e16ca8d0b/docs/FR-11_BOND_PHASE_DIALOGUE.md), Mak1zu's [architecture](https://github.com/snowarch/mak1zu/blob/a30fef7cc10324684fd4ae9123d9c29cbd00ca9e/docs/ARCHITECTURE.md), [security](https://github.com/snowarch/mak1zu/blob/a30fef7cc10324684fd4ae9123d9c29cbd00ca9e/docs/SECURITY.md), and [presence rules](https://github.com/snowarch/mak1zu/blob/a30fef7cc10324684fd4ae9123d9c29cbd00ca9e/engine/presence.go). The [archived Mak1zu research snapshot](archive/COMPANION_MAK1ZU_RESEARCH_20261008.md) preserves historic findings but is **retired as a current roadmap**; this synthesis and the two canonical TODOs supersede its planning.

## Decision: one native Alis-Companion actor; three separable authorities

1. **Alis host owns permissions and desktop facts.** Niri/Quickshell readiness, lock/game/fullscreen, output and edge selection, ownership of real UI surfaces, optional model/data consent. Alis-Companion must receive only approved semantic categories and validated facts. Never read an editor buffer, raw keyboard, browser title, coding prompt, agent output, clipboard, file path or screenshot merely to trigger an ambient animation. A configured local model does not prove cloud isolation if the selected Alis provider can access an external endpoint.
2. **Alis-Companion deterministic engine owns mode arbitration and visual action.** Exact ordering: hidden/locked/fullscreen/game → direct chat/drag/modal → deliberate travel/portal → optional, privacy-reduced cowork context → ambient idle. A long-lived cowork session is NOT the rendering state. One visual actor, one active intro/loop/outro performance, one generation/epoch per accepted transition; completion from a replaced animation is stale. Portal still means **distant travel**, never ordinary appearance or opening a laptop. No secondary GTK/XWayland sprite actor or new popup to depict coworking.
3. **Shared AI owns inference, not physical actions.** Aqua and Octo have distinct compact persona rules, common text+expression schema and one guarded public reply boundary. Missing AI/model, model crash or privacy disable must not stop Aqua's physics/motion. Tool claims and model-proposed permissions never mutate settings or files; explicit Alis user actions must own any real side effect and provide receipts.

## Implemented, tested offline; exact status

| Alis-Companion code | What is materially implemented | What is explicitly NOT implemented |
| --- | --- | --- |
| [`AquaBehaviorDirector.js`](../modules/abyss/companion/AquaBehaviorDirector.js) and [tests](../scripts/test-aqua-behavior-director.cjs) | Standalone pure deterministic mode arbiter with human-input precedence, opt-in/quiet/host gates, bounded anonymous concurrent agent sessions, 700 ms focus settle, stale callback epochs, 45 s waiting grace, 60 s long-run celebration eligibility, 15 min session expiry | **NOT wired to live Niri focus or coding-agent hooks.** No new animation or laptop asset, no real celebration/hint yet |
| [`AquaPersona.js`](../services/AquaPersona.js), [`persona.py`](../scripts/aqua/persona.py) and [tests](../scripts/test-aqua-persona.cjs) | Distinct compact Aqua/Octo prompts; common JSON text+expression, no made-up actions/appointments; shared AI and one-shot helper use these rules | Model quality, dialect/translation, factual grounding and token-accurate latency NOT measured |
| [`AquaReplyGuard.js`](../services/AquaReplyGuard.js), [`reply_guard.py`](../scripts/aqua/reply_guard.py) | Filters internal protocol sentinels; bounded reply and expression before Alis-Companion UI/local history, basic malformed-envelope fallback protection | Not a complete adversarial prompt-injection guard, action authorization engine, or proof of user safety |
| [`aqua-companion-voice-eval.py`](../scripts/aqua-companion-voice-eval.py), [synthetic scenarios](../scripts/fixtures/alis-companion-voice-scenarios.json) | Paired Aqua/Octo evaluation fixture/aggregate diagnostics without storing user conversations or invoking a model | Actual comparative voice evaluation awaits chosen model and permission |
| [`memory_store.py`](../scripts/aqua/memory_store.py), [proactive prototype](../scripts/aqua-proactive-policy-prototype.js) | Offline opt-in-only memory/forget schema prototype and fake-clock proactive gates; testable with no desktop | Both **DORMANT**, no UI, no auto extraction, no background model use, no permission to persist data |
| [G0/G1 renderer test plan](ALIS_COMPANION_RENDERER_OPTIMIZATION_DECISION_20261008.md) | Source-pinned A/A → negative → candidate sequence and read-only per-PID CPU/PSS measurement tools | Physical G0 remains inconclusive; no E1 production shader change, no verified GPU or memory saving |

**Do not label a new source module as a shipped feature.** The Behavior Director is a tested coordination *contract* awaiting explicit host signal/visual integration. Current Alis-Companion 3D animation and all existing users' desktop behaviors remain the production baseline.

## What is better adapted to Alis than either upstream implementation

- Event input does not imply desktop surveillance: the host is the only place that may reduce authorized app/activity into `none/terminal/editor/other`. Coding-agent input is only `working/activity/needs_input/prompt_waiting/finished/ended` plus a validated fixed-size opaque session token. Unknown/extra fields are rejected; no prompt, path, command, response or tool body is forwarded, printed or stored by the director.
- **Privacy-first, zero-inference ambient loop:** animation/co-working is deterministic. No unsolicited AI inference, autonomous diary, new daemon, cloud fallback, hidden GPU process or scope-expanding tools. A user-facing mode needs opt-in and meaningful off/quiet controls before connecting a signal source.
- Three layers remain independent even when visuals are richer: the actor may work by the editor, but a user drag immediately owns motion; compositor lockdown suppresses the entire activity; the actual blender action has versioned start/stop epochs so a late completion cannot reset the user interaction.
- The existing 3D renderer retains its Aqua refraction, Octo tentacles, compositor-appropriate transparency and theme feedback. Mochi's sprite animation catalog provides **behavior design reference**, not a permissible static texture shortcut or a measurement of Alis-Companion performance.
- State-derived "bond" must never produce guilt, exclusivity, manipulative streaks, dependency or guessed mental state. No reward system or background memory inference is approved by this document.

## Exact future release/qualification gates

**V0 / code qualification:** deterministic Node/Python test fixtures and GitHub Actions syntax/QSB checks; check Alis-Companion `main` commit and receipt. Never claim this proves GPU or installed host.

**V1 / narrow event bridge:** define a versioned, one-way, explicitly enabled Alis host contract with authenticated/owned local transport, strict semantic allowlists and opt-out. Test multiple agents, stale event order, invalid messages, reconnect, input non-interference and cross-output transitions. Do not ask users to install broad desktop event hooks to test a pure policy.

**V2 / 3D laptop and animation:** author Aqua and Octo performances using existing Blender native/procedural rig and one actor. Explicit intro/loop/outro and cancellation, true cursor/drag/portal collision policy, no additional window, no asset borrowing. Preserve all existing 60 clips and strict lossless renderer gates. No performance claim without G0/G1 local evidence.

**V3 / user value:** actual pinned-model paired persona evaluation, bounded output, opt-in memory UX (inspect/delete/revoke), local-only policy verification, quiet-hours/dismissal state backed by explicit settings, and same-session integration across Alis/Companion. Test the background inference budget is zero while disabled.

**Licensing:** Mochi repository code is MIT, Mak1zu is Apache-2.0 with NOTICE and separate brand artwork limitations. This work borrows *design principles* only, with independently authored JS/Python; no upstream source, sprites, persona or copyrighted artwork is imported.

**Branch/ownership rule:** Alis-Companion `main` only for Companion. Alis `dev` host integration separately after tests; **never touch Alis `stable`**.
