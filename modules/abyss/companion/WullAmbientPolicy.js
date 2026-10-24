.pragma library

// Coarse observations only. No session countdown, model, text, file or player
// identity belongs here. The existing visual Director grants each reaction.
var COOLDOWN_MS=120000
function newState() {return {baseline:null,lastClock:-1,lastReaction:-1}}
function validTime(value) {return Number.isSafeInteger(value) && value>=0}
function valid(input) {
    return input && typeof input==="object" && !Array.isArray(input)
        && Object.keys(input).length===3 && Object.keys(input).every(k=>["focus","power","media"].includes(k))
        && ["unavailable","off","focus","break","paused"].includes(input.focus)
        && ["unavailable","normal","low","charging"].includes(input.power)
        && ["unavailable","paused","playing"].includes(input.media)
}
function revoke(state) {state.baseline=null}
function observe(state,input,allowed,now) {
    if(!validTime(now) || now<state.lastClock) {
        state.baseline=null
        if(validTime(now))state.lastClock=now
        return {eligible:false,reason:"invalid_clock"}
    }
    state.lastClock=now
    if(!valid(input) || typeof allowed!=="boolean") {
        state.baseline=null
        return {eligible:false,reason:"invalid_observation"}
    }
    const before=state.baseline
    state.baseline={focus:input.focus,power:input.power,media:input.media}
    // Observe suppressed transitions too. A popup closing or a lock ending
    // must never replay an old battery/media event as a fresh reaction.
    if(!allowed)return {eligible:false,reason:"suppressed"}
    if(!before)return {eligible:false,reason:"initial_snapshot"}
    let expression="",reason="unchanged"
    if(input.focus!==before.focus && input.focus==="focus") {
        expression="working";reason="focus_started"
    } else if(before.focus==="focus" && input.focus==="break") {
        expression="happy";reason="focus_break"
    } else if(input.focus==="focus")return {eligible:false,reason:"quiet_focus"}
    else if(input.power!==before.power && input.power==="low") {
        expression="alert";reason="battery_low"
    } else if(before.power==="low" && input.power==="charging") {
        expression="happy";reason="charging_after_low"
    } else if(input.media!==before.media && input.media==="playing") {
        expression="happy";reason="playback_started"
    }
    if(!expression)return {eligible:false,reason:reason}
    if(state.lastReaction>=0 && now-state.lastReaction<COOLDOWN_MS)
        return {eligible:false,reason:"cooldown"}
    state.lastReaction=now
    return {eligible:true,reason:reason,expression:expression}
}
