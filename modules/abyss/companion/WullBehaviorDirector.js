.pragma library

// Companion intent arbitration, pure JS with no desktop reads or side effects.
// Original Hadanion policy; informed by Mochi's domain/animation separation
// and Mak1zu's reasoned, bounded proactive decisions. No sprites or Go code.
// A caller owns the clock, opt-in, lifecycle, visibility and all actual effects.
var AGENT_WORDS = ["working", "activity", "needs_input", "prompt_waiting", "finished", "ended"]
var CATEGORIES = ["none", "terminal", "editor", "other"]
var TOKEN_RE = /^[0-9a-f]{16}$/
var STALE_MS = 15 * 60 * 1000
var GRACE_MS = 45 * 1000
var CELEBRATE_MS = 60 * 1000
var CONTEXT_SETTLE_MS = 700
var MAX_SESSIONS = 16
var MIN_BOUNDS = 0
var MAX_BOUNDS = 9007199254740991

function newState() {
    return {epoch:0, focus:"none", focusSince:0, sessions:Object.create(null),
            lastMode:"idle", lastReason:"initial", pending:null}
}

function finiteTime(nowMs) {
    return typeof nowMs==="number" && Number.isFinite(nowMs)
        && Number.isSafeInteger(nowMs) && nowMs>=MIN_BOUNDS && nowMs<=MAX_BOUNDS
}

function validAgent(input) {
    if (!input || typeof input!=="object" || Array.isArray(input)
            || Object.keys(input).some(k=>k!=="word" && k!=="token")
            || typeof input.word!=="string" || AGENT_WORDS.indexOf(input.word)<0
            || typeof input.token!=="string" || !TOKEN_RE.test(input.token))
        return false
    return true
}

function keys(s) { return Object.keys(s.sessions) }

function prune(s, nowMs) {
    const removed=[]
    for (const key of keys(s)) {
        if (nowMs-s.sessions[key].seen >= STALE_MS) {
            delete s.sessions[key]
            removed.push(key)
        }
    }
    return removed.length
}

function acceptAgent(s, input, nowMs) {
    if (!finiteTime(nowMs) || !validAgent(input))
        return {accepted:false,reason:"invalid_event"}
    prune(s, nowMs)
    const word=input.word, token=input.token
    let session=s.sessions[token]
    let finishedLong=false
    if (word==="working") {
        if (!session) {
            // Cap strictly; forgotten sessions lose all previous work credit.
            if (keys(s).length>=MAX_SESSIONS) {
                let oldest=keys(s)[0]
                for (const k of keys(s))
                    if (s.sessions[k].seen<s.sessions[oldest].seen) oldest=k
                delete s.sessions[oldest]
            }
            session={started:nowMs,seen:nowMs,waiting:-1,nudged:false}
            s.sessions[token]=session
        } else {
            session.started=nowMs
            session.seen=nowMs
            session.waiting=-1
            session.nudged=false
        }
    } else if (word==="needs_input" || word==="prompt_waiting") {
        // Never manufacture a session from a hook that arrived after "ended".
        if (!session) return {accepted:false,reason:"unknown_session"}
        session.seen=nowMs
        if (session.waiting<0) session.waiting=nowMs
        if (word==="prompt_waiting") session.waiting=Math.min(session.waiting,nowMs-GRACE_MS)
    } else if (word==="activity") {
        if (!session) return {accepted:false,reason:"unknown_session"}
        if (session.waiting>=0) session.started=nowMs // a wait is not active work
        session.seen=nowMs
        session.waiting=-1
        session.nudged=false
    } else {
        if (!session) return {accepted:false,reason:"unknown_session"}
        finishedLong=word==="finished" && session.waiting<0 && nowMs-session.started>=CELEBRATE_MS
        delete s.sessions[token]
    }
    // Intentionally no token, prompt, filename, process or session details in return.
    return {accepted:true,reason:"accepted",finishedLong:finishedLong}
}

function setFocus(s, category, nowMs) {
    if (!finiteTime(nowMs) || CATEGORIES.indexOf(category)<0)
        return {accepted:false,reason:"invalid_focus"}
    if (category!==s.focus) {
        s.focus=category
        s.focusSince=nowMs
    }
    return {accepted:true,reason:"accepted"}
}

function resolvedAgent(s, nowMs) {
    let working=false, waiting=false
    for (const key of keys(s)) {
        const q=s.sessions[key]
        if (q.waiting<0 || nowMs-q.waiting<GRACE_MS)
            working=true
        else waiting=true
    }
    return {working:working,waiting:waiting}
}

function snapshot(s, host, nowMs) {
    if (!finiteTime(nowMs) || !host || typeof host!=="object")
        return {mode:"idle",reason:"invalid_context",expression:"idle",epoch:s.epoch}
    prune(s, nowMs)
    if (host.enabled!==true || host.visible!==true || host.locked===true
        || host.fullscreen===true || host.gameMode===true) {
        return {mode:"hidden",reason:"host_suppressed",expression:"idle",epoch:s.epoch}
    }
    if (host.dragging===true || host.chatOpen===true || host.modalOpen===true) {
        return {mode:"interactive",reason:"human_interaction_priority",expression:"idle",epoch:s.epoch}
    }
    if (host.manualTravel===true || host.portalTravel===true) {
        return {mode:"travel",reason:"existing_locomotion_priority",expression:"idle",epoch:s.epoch}
    }
    if (host.optedIn!==true || host.dnd===true || host.quiet===true) {
        return {mode:"idle",reason:"ambient_consent_or_quiet",expression:"idle",epoch:s.epoch}
    }
    const agent=resolvedAgent(s,nowMs)
    if (agent.working || agent.waiting)
        return {mode:"cowork",reason:"agent_lifecycle",
                expression:agent.working ? "working" : "thinking",epoch:s.epoch}
    if (s.focus!=="none" && s.focus!=="other" && nowMs-s.focusSince>=CONTEXT_SETTLE_MS
        && host.idle!==true)
        return {mode:"cowork",reason:"focused_app_category",expression:"working",epoch:s.epoch}
    return {mode:"idle",reason:"no_live_context",expression:"idle",epoch:s.epoch}
}

function transition(s,host,nowMs) {
    const proposed=snapshot(s,host,nowMs)
    if (proposed.mode!==s.lastMode || proposed.reason!==s.lastReason) {
        s.epoch++
        s.pending=null // revoked performance; an old completion cannot re-enter
        s.lastMode=proposed.mode
        s.lastReason=proposed.reason
    }
    return {mode:proposed.mode,reason:proposed.reason,expression:proposed.expression,epoch:s.epoch}
}

function beginPresentation(s,phase,nowMs) {
    // Only the presentation controller may request intro/loop/outro.
    if (["intro","loop","outro"].indexOf(phase)<0 || !finiteTime(nowMs))
        return {ok:false,reason:"invalid_presentation"}
    s.epoch++
    s.pending={epoch:s.epoch,phase:phase}
    return {ok:true,phase:phase,epoch:s.epoch}
}

function completePresentation(s,epoch) {
    // A stale animation signal must never mutate the current sequence.
    if (!s.pending || s.pending.epoch!==epoch)
        return {accepted:false,reason:"stale_completion"}
    const phase=s.pending.phase
    s.pending=null
    return {accepted:true,phase:phase}
}

function pollNeedsInput(s,host,nowMs) {
    const mode=snapshot(s,host,nowMs)
    if (mode.mode!=="cowork" || mode.reason!=="agent_lifecycle")
        return {eligible:false,reason:"not_presenting_cowork"}
    for (const key of keys(s)) {
        const q=s.sessions[key]
        if (q.waiting>=0 && nowMs-q.waiting>=GRACE_MS && !q.nudged) {
            q.nudged=true
            return {eligible:true,reason:"one_time_permission_hint",expression:"alert"}
        }
    }
    return {eligible:false,reason:"no_confirmed_wait"}
}

// Explicit chat can propose a face while its conversation is open. All
// placement, travel, Popup, portal and animation ownership stays with the
// existing presentation controllers. No model-selected duration or geometry.
function proposeAiExpression(s,host,input,nowMs) {
    const expressions=["idle","happy","excited","thinking","working","surprised","sleepy","sad","alert"]
    if(!finiteTime(nowMs) || !s || !Number.isSafeInteger(s.epoch)
            || !input || typeof input!=="object" || Array.isArray(input)
            || Object.keys(input).length!==1 || typeof input.expression!=="string"
            || !expressions.includes(input.expression))
        return {accepted:false,reason:"invalid_proposal"}
    if(!host || host.enabled!==true || host.visible!==true || host.motionEnabled!==true
            || host.locked===true || host.fullscreen===true || host.gameMode===true
            || host.dragging===true || host.manualTravel===true || host.portalTravel===true
            || host.modalOpen===true || host.dnd===true || host.quiet===true)
        return {accepted:false,reason:"presentation_owned"}
    return {accepted:true,expression:input.expression,intensity:.5,ttlMs:3000,epoch:s.epoch}
}

function proposeAmbientExpression(s,host,input,nowMs) {
    if(!host || host.ambientOptIn!==true || host.idle!==true || host.chatOpen===true)
        return {accepted:false,reason:"ambient_not_permitted"}
    const proposal=proposeAiExpression(s,host,input,nowMs)
    if(!proposal.accepted)return proposal
    // Every coarse event uses the same small, finite face-only presentation.
    // No model, source or caller chooses movement, a prop or a timer duration.
    return {accepted:true,expression:proposal.expression,intensity:.25,ttlMs:2000,epoch:proposal.epoch}
}
