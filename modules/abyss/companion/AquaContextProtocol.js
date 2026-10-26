.pragma library
.import "AquaBehaviorDirector.js" as Director

// Dormant, pure receiving contract. Alis must authenticate the peer and
// explicitly bind each fresh generation; packets cannot grant permission.
// No transport, compositor observation, timer, animation or actor is started.
var VERSION = 1
var MAX_FRAME_LENGTH = 2048
var MAX_EVENT_AGE_MS = 5000
var CONNECTION_TTL_MS = 15000
var GENERATION_RE = /^[0-9a-f]{16}$/
var FIELDS = ["version", "generation", "sequence", "issuedAtMs", "kind", "payload"]

function newState() {
    return {enabled:false, generation:"", sequence:0, lastReceivedMs:-1,
            lastClockMs:-1, director:Director.newState()}
}

function deny(reason) { return {accepted:false, reason:reason} }

function discard(s) {
    // Keep completion epochs increasing across disconnects and restarts.
    const epoch = s.director.epoch + 1
    s.director = Director.newState()
    s.director.epoch = epoch
    s.enabled = false
    s.sequence = 0
    s.lastReceivedMs = -1
}

function configure(s, enabled, generation, nowMs) {
    if (typeof enabled !== "boolean")
        return deny("invalid_configuration")
    if (!enabled) {
        const changed = s.enabled
        if (changed) discard(s)
        if (Director.finiteTime(nowMs)) s.lastClockMs = nowMs
        return {accepted:true, changed:changed}
    }
    if (!Director.finiteTime(nowMs)) return deny("invalid_configuration")
    if (typeof generation !== "string" || !GENERATION_RE.test(generation))
        return deny("invalid_generation")
    if (generation === s.generation) {
        if (!s.enabled) return deny("fresh_generation_required")
        if (nowMs < s.lastClockMs) {
            discard(s)
            return deny("invalid_clock")
        }
        // Re-reading consent never renews liveness or resets replay protection.
        return {accepted:true, changed:false}
    }
    discard(s)
    s.enabled = true
    s.generation = generation
    s.lastReceivedMs = nowMs
    s.lastClockMs = nowMs
    return {accepted:true, changed:true}
}

function live(s, nowMs) {
    if (!s.enabled) return "disabled"
    if (!Director.finiteTime(nowMs) || nowMs < s.lastClockMs) {
        discard(s)
        return "invalid_clock"
    }
    s.lastClockMs = nowMs
    if (nowMs - s.lastReceivedMs > CONNECTION_TTL_MS) {
        discard(s)
        return "disconnected"
    }
    return ""
}

function object(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value)
}

function exactFields(value, fields) {
    if (!object(value)) return false
    const keys = Object.keys(value)
    return keys.length === fields.length && keys.every(key => fields.indexOf(key) >= 0)
}

function accept(s, frame, peerVerified, nowMs) {
    if (!s.enabled) return deny("disabled")
    if (peerVerified !== true) return deny("untrusted_peer")
    const problem = live(s, nowMs)
    if (problem) return deny(problem)
    if (typeof frame !== "string" || frame.length > MAX_FRAME_LENGTH)
        return deny("invalid_frame")
    let event
    try { event = JSON.parse(frame) }
    catch (_error) { return deny("invalid_frame") }
    if (!exactFields(event, FIELDS) || event.version !== VERSION
        || typeof event.generation !== "string" || !GENERATION_RE.test(event.generation)
        || !Number.isSafeInteger(event.sequence) || event.sequence < 1
        || !Director.finiteTime(event.issuedAtMs))
        return deny("invalid_frame")
    if (event.generation !== s.generation) return deny("old_generation")
    if (event.sequence <= s.sequence) return deny("out_of_order")
    if (event.issuedAtMs > nowMs || nowMs - event.issuedAtMs > MAX_EVENT_AGE_MS)
        return deny("stale_event")
    let result
    if (event.kind === "agent") {
        if (!Director.validAgent(event.payload)) return deny("invalid_payload")
        result = Director.acceptAgent(s.director, event.payload, nowMs)
    } else if (event.kind === "focus") {
        if (!exactFields(event.payload, ["category"])
            || Director.CATEGORIES.indexOf(event.payload.category) < 0)
            return deny("invalid_payload")
        result = Director.setFocus(s.director, event.payload.category, nowMs)
    } else if (event.kind === "heartbeat") {
        if (!exactFields(event.payload, [])) return deny("invalid_payload")
        result = {accepted:true}
    } else return deny("unknown_kind")
    if (!result.accepted) return deny(result.reason)
    s.sequence = event.sequence
    s.lastReceivedMs = nowMs
    return {accepted:true, reason:"accepted", finishedLong:result.finishedLong === true}
}

function observe(s, host, nowMs) {
    live(s, nowMs)
    const context = Object.assign({}, host, {
        optedIn:s.enabled && host !== null && host !== undefined && host.optedIn === true
    })
    return Director.transition(s.director, context, nowMs)
}
