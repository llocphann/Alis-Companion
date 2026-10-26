.pragma library
.import "../../modules/abyss/companion/AquaBehaviorDirector.js" as Director

// Staging-only presentation controller. No Timer, desktop reads, actor/window,
// inference or IPC. The existing actor owner supplies one monotonic clock and
// the existing Director state. Assets/cowork is excluded from runtime packages.
var EXIT_GRACE_MS = 1000
var BLEND_MS = 140
var CUE_COOLDOWN_MS = 6000
var CLIPS = ["laptop_open", "laptop_typing_loop", "laptop_thinking_loop",
    "laptop_agent_loop", "laptop_pause", "laptop_close", "laptop_success", "laptop_alert"]

function neutral(channel) {
    return channel.indexOf("scale") === 0 || channel.endsWith("Curl") || channel === "eyeOpen" ? 1 : 0
}

function newState(character, clips) {
    if (["aqua", "octo"].indexOf(character) < 0 || !clips
        || CLIPS.some(name => !clips[name] || !Number.isFinite(clips[name].duration)
            || clips[name].duration < 1000 || clips[name].duration > 3200))
        throw new Error("Verified staged laptop curves are required")
    const channels = ["scaleX", "scaleY", "lift", "normal", "height", "journey"]
    for (const name of CLIPS) for (const channel of Object.keys(clips[name].tracks))
        if (channels.indexOf(channel) < 0) channels.push(channel)
    return {character:character, clips:clips, channels:channels, phase:"none", clip:"",
        started:0, duration:0, reverseFrom:0, epoch:-1, lastTick:-1,
        lostSince:-1, lastCue:-CUE_COOLDOWN_MS, blendFrom:null, finished:"", reason:"initial"}
}

function sample(s, clip, channel, progress) {
    const keys = s.clips[clip]?.tracks[channel]
    if (!keys) return neutral(channel)
    const t = Math.max(0, Math.min(1, progress))
    for (let i=1; i<keys.length; ++i) {
        const a=keys[i-1], b=keys[i]
        if (t<=b[0]) return a[1]+(b[1]-a[1])*(t-a[0])/(b[0]-a[0])
    }
    return keys[keys.length-1][1]
}

function progress(s, nowMs) {
    if (s.phase === "none") return 0
    const elapsed = Math.max(0, nowMs-s.started)
    if (s.phase === "loop") return (elapsed%s.duration)/s.duration
    const fraction = Math.min(1, elapsed/Math.max(1, s.duration))
    return s.reverseFrom > 0 ? s.reverseFrom*(1-fraction) : fraction
}

function pose(s, nowMs) {
    const values = {}, p = progress(s, nowMs)
    const blend = s.blendFrom ? Math.min(1, Math.max(0, (nowMs-s.started)/BLEND_MS)) : 1
    for (const channel of s.channels) {
        const target = s.phase === "none" ? neutral(channel) : sample(s, s.clip, channel, p)
        values[channel] = s.blendFrom ? s.blendFrom[channel]*(1-blend)+target*blend : target
    }
    return values
}

function view(s, nowMs) {
    const values = pose(s, nowMs)
    return {character:s.character, phase:s.phase, clip:s.clip, epoch:s.epoch,
        progress:progress(s, nowMs), active:s.phase!=="none", loop:s.phase==="loop",
        renderProp:s.phase!=="none" && values.laptopVisible>=.5,
        pose:values, reason:s.reason}
}

function ticket(s, director, nowMs) {
    const phase = s.phase === "cue" ? "loop" : s.phase
    const next = Director.beginPresentation(director, phase, nowMs)
    s.epoch = next.epoch
}

function start(s, director, phase, clip, nowMs, blend, reverseFrom=0) {
    const from = blend ? pose(s, nowMs) : null
    s.phase=phase; s.clip=clip; s.started=nowMs; s.reverseFrom=reverseFrom
    s.finished=""
    s.duration=s.clips[clip].duration*(reverseFrom>0 ? reverseFrom : 1)
    s.blendFrom=from
    ticket(s, director, nowMs)
}

function yieldNow(s, director, reason) {
    if (s.phase!=="none") {
        // Revocation also works when the newly supplied clock is invalid.
        const revoked=Director.beginPresentation(director, "outro", Math.max(0,s.lastTick))
        Director.completePresentation(director, revoked.epoch)
        s.epoch=revoked.epoch
    }
    s.phase="none"; s.clip=""; s.blendFrom=null; s.reverseFrom=0
    s.duration=0; s.lostSince=-1; s.finished=""; s.reason=reason
}

function desiredLoop(intent) {
    return intent.expression === "thinking" ? "laptop_thinking_loop"
        : intent.reason === "agent_lifecycle" ? "laptop_agent_loop" : "laptop_typing_loop"
}

function complete(s, director, epoch, nowMs) {
    if (!Director.finiteTime(nowMs) || nowMs<s.lastTick || s.phase==="none"
        || s.phase==="loop" || epoch!==s.epoch || nowMs-s.started<s.duration)
        return {accepted:false, reason:"stale_or_early_completion"}
    const result=Director.completePresentation(director, epoch)
    if (!result.accepted) return result
    s.finished=s.phase; s.lastTick=nowMs
    s.phase="none"; s.clip=""; s.blendFrom=null; s.reverseFrom=0
    return {accepted:true, phase:result.phase}
}

function update(s, director, host, nowMs) {
    if (!Director.finiteTime(nowMs) || nowMs<s.lastTick) {
        yieldNow(s, director, "invalid_clock")
        return view(s, Math.max(0,s.lastTick))
    }
    s.lastTick=nowMs
    const intent=Director.transition(director, host, nowMs)
    // These host values are supplied by the permitted actor owner, not a model.
    if (!host || host.coworkEnabled!==true || host.optedIn!==true
        || host.motionEnabled!==true || host.grounded!==true || host.character!==s.character
        || host.dnd===true || host.quiet===true
        || intent.mode==="hidden" || intent.mode==="interactive" || intent.mode==="travel") {
        yieldNow(s, director, "higher_priority_or_disabled")
        return view(s, nowMs)
    }
    const wanted=intent.mode==="cowork"
    if (wanted) s.lostSince=-1
    else if (s.lostSince<0) s.lostSince=nowMs

    if (s.phase!=="none" && director.pending?.epoch!==s.epoch)
        ticket(s, director, nowMs) // fresh ownership, same timing/pose

    if (!wanted && s.phase!=="none" && s.phase!=="outro"
        && nowMs-s.lostSince>=EXIT_GRACE_MS) {
        const reverse=s.phase==="intro" ? progress(s,nowMs) : 0
        start(s,director,"outro",reverse>0 ? "laptop_open" : "laptop_close",nowMs,reverse===0,reverse)
        s.reason="context_ended"
    }
    if (s.phase!=="none" && s.phase!=="loop" && nowMs-s.started>=s.duration) {
        const done=complete(s,director,s.epoch,nowMs)
        if (!done.accepted) {
            yieldNow(s,director,"lost_presentation_ownership")
            return view(s,nowMs)
        }
    }
    if (s.finished!=="") {
        const completed=s.finished
        s.finished=""
        if (completed==="outro") {
            s.reason="closed"
            return view(s,nowMs) // final hidden prop precedes any new intro
        }
        if (wanted) start(s,director,"loop",desiredLoop(intent),nowMs,false)
        else start(s,director,"loop","laptop_pause",nowMs,false)
    }
    if (wanted && s.phase==="none") {
        start(s,director,"intro","laptop_open",nowMs,false)
        s.reason="context_started"
    } else if (s.phase==="loop") {
        const clip=wanted ? desiredLoop(intent) : "laptop_pause"
        if (s.clip!==clip) start(s,director,"loop",clip,nowMs,true)
        s.reason=wanted ? "context_active" : "context_settling"
    }
    return view(s,nowMs)
}

function cue(s, director, kind, nowMs) {
    // Optional confirmed host success/input cues; never a model action protocol.
    if (!Director.finiteTime(nowMs) || nowMs<s.lastTick || s.phase!=="loop"
        || ["success","alert"].indexOf(kind)<0 || nowMs-s.lastCue<CUE_COOLDOWN_MS)
        return {accepted:false, reason:"cue_unavailable"}
    s.lastTick=nowMs; s.lastCue=nowMs
    start(s,director,"cue","laptop_"+kind,nowMs,true)
    s.reason="confirmed_host_cue"
    return {accepted:true, epoch:s.epoch}
}
