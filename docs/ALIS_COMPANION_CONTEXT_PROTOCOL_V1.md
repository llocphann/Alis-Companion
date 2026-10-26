# Alis-Companion context protocol v1

Status: **dormant receiving contract**, not a running host bridge. The implementation is [AquaContextProtocol.js](../modules/abyss/companion/AquaContextProtocol.js); its synthetic regression is [test-aqua-context-protocol.cjs](../scripts/test-aqua-context-protocol.cjs). Visual work remains in the [canonical TODO](../to-do/cloud-bot/ABYSS_WATER_DROPLET_COMPANION.md).

The receiver feeds the existing Behavior Director. It neither creates a second actor nor owns movement, presentation, input, a process or a timer. No production QML imports it. Alis must own permission, semantic reduction, peer authentication and transport before any live integration. The optional host/package API remains v1; this document versions a separate future event frame.

## Owner boundary

1. A trusted Alis owner explicitly calls `configure(state, true, generation, nowMs)` after opt-in, with a fresh anonymous 16-character lowercase hexadecimal generation. The owner must mint a new value for every reconnect and never recycle one within its lifetime; this library rejects reuse of the most recent disconnected generation. Default state is disabled. A packet cannot enable the receiver.
2. The host must authenticate its local transport peer, bind that peer to the current generation and enforce a bounded frame read. For a future Unix socket, verify native peer credentials; a UID, boolean, token or source name supplied inside JSON is not authentication. `peerVerified` is an assertion by the trusted owner, not an implemented credential check.
3. The owner calls `accept(state, frame, peerVerified, nowMs)` with monotonic local time. No app title, prompt, command, file, URL, screenshot, PID or user identity belongs in a frame.
4. The existing presentation owner may call `observe(state, hostPolicy, nowMs)`. Hidden/lock/fullscreen/game, direct chat/drag/modal and explicit travel retain their established priority. Opt-out, quiet and DND suppress contextual presentation. No packet directly plays an animation or opens a surface.
5. Consent removal calls `configure(state, false, "", nowMs)` and succeeds even when the owner clock is unavailable. Transport expiry or clock regression also discards focus/sessions and invalidates presentation completions. Reconnection requires a fresh trusted generation; ordinary setting reads do not renew liveness.

## Frame

All six fields are mandatory and additional fields are rejected:

```json
{
  "version": 1,
  "generation": "0123456789abcdef",
  "sequence": 1,
  "issuedAtMs": 1000000,
  "kind": "agent",
  "payload": {"word": "working", "token": "aaaaaaaaaaaaaaaa"}
}
```

| Field | Contract |
| --- | --- |
| `version` | Numeric integer 1 |
| `generation` | Exact currently bound 16-character lowercase hexadecimal value |
| `sequence` | Positive safe integer, strictly greater than the last accepted sequence; gaps allowed |
| `issuedAtMs` | Safe nonnegative integer from the same monotonic clock; never future, age at most 5 seconds |
| `kind` | `agent`, `focus` or `heartbeat` |
| `payload` | Exact schema below; no additional fields |

The JS frame bound is 2,048 UTF-16 code units. A host transport must enforce its own byte bound before decoding; all valid frame vocabulary and tokens are ASCII. Malformed, unsupported, stale, duplicated or unauthenticated input does not consume sequence, renew liveness or acquire work credit. The trusted receiver clock may still advance when authenticated malformed data arrives.

| Kind | Payload |
| --- | --- |
| `agent` | Exactly `word` and `token`. Word is `working|activity|needs_input|prompt_waiting|finished|ended`; token is anonymous 16-character lowercase hexadecimal |
| `focus` | Exactly `category`, one of `none|terminal|editor|other` |
| `heartbeat` | Empty object; renews transport liveness without resetting active work or presentation |

A connection expires after more than 15 seconds without an accepted frame. A future host may send bounded heartbeats within this interval while the opted-in source is active. This library schedules nothing. Existing Director limits remain: 16 anonymous sessions, 15-minute session expiry, 700 ms focus settle, 45-second permission wait and 60-second uninterrupted work before a success proposal. Transport generations preserve increasing completion epochs across replacement; a completion from an earlier receiver cannot reset new intent.

Receipts contain only acceptance/reason and a bounded success eligibility boolean. Packet payloads are not logged or persisted. The actor's cast, shader, authored clips, portal policy, native process and AI provider remain owned by their existing components.

## Integration still required

The contract does not supply a compositor monitor, coding-agent hook, permission UI, peer credential verification or a heartbeat source. These need an independently reviewed Alis integration and owned-host lifecycle/visibility tests. The laptop prop and contextual animation are not implemented by this protocol. Synthetic PASS cannot qualify desktop observation, user-visible animation, hardware cost or permission grant.
