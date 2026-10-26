.pragma library

// Coarse observations only. No session countdown, model, text, file or player
// identity belongs here. The existing visual Director grants each reaction.
var COOLDOWN_MS=120000
function newState() {return {baseline:null,lastClock:-1,lastReaction:-1}}
function validTime(value) {return Number.isSafeInteger(value) && value>=0}
function valid(input) {
    if(!input || typeof input!=="object" || Array.isArray(input))return false
    const keys=Object.keys(input)
    if(keys.length<3 || keys.length>5)return false
    if(!keys.includes("focus") || !keys.includes("power") || !keys.includes("media"))return false
    if(!keys.every(k=>["focus","power","media","idle","result"].includes(k)))return false
    return ["unavailable","off","focus","break","paused"].includes(input.focus)
        && ["unavailable","normal","low","charging"].includes(input.power)
        && ["unavailable","paused","playing"].includes(input.media)
        && (input.idle===undefined || ["unavailable","active","idle"].includes(input.idle))
        && (input.result===undefined || ["unavailable","none","success","failure"].includes(input.result))
}
function performance(reason,focus) {
    if(reason==="focus_started")
        return {activity:"reading",prop:"book",gesture:"inspect",expression:"working",ttlMs:3600}
    if(reason==="focus_quiet_thought")
        return {activity:"quiet_thought",prop:"book",gesture:"inspect",expression:"thinking",ttlMs:3200}
    if(reason==="focus_break")
        return {activity:"coffee",prop:"coffee",gesture:"reach",expression:"happy",ttlMs:3600}
    if(reason==="quiet_thought")
        return {activity:"quiet_thought",prop:focus==="focus" ? "book" : "thought",gesture:"inspect",expression:"thinking",ttlMs:2600}
    if(reason==="result_success")
        return {activity:"celebrate",prop:"",gesture:"delight",expression:"happy",ttlMs:2400}
    if(reason==="result_failure")
        return {activity:"alert",prop:"",gesture:"startle",expression:"alert",ttlMs:2000}
    if(reason==="battery_low")
        return {activity:"alert",prop:"",gesture:"",expression:"alert",ttlMs:2000}
    if(reason==="charging_after_low")
        return {activity:"relief",prop:"",gesture:"wave",expression:"happy",ttlMs:2000}
    if(reason==="playback_started")
        return {activity:"listen",prop:"",gesture:"wave",expression:"happy",ttlMs:2000}
    return {activity:"none",prop:"",gesture:"",expression:"idle",ttlMs:0}
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
    const idle=input.idle ?? "unavailable"
    const result=input.result ?? "unavailable"
    const before=state.baseline
    state.baseline={focus:input.focus,power:input.power,media:input.media,idle:idle,result:result}
    // Observe suppressed transitions too. A popup closing or a lock ending
    // must never replay an old battery/media event as a fresh reaction.
    if(!allowed)return {eligible:false,reason:"suppressed"}
    if(!before)return {eligible:false,reason:"initial_snapshot"}
    let expression="",reason="unchanged"
    if(input.focus!==before.focus && input.focus==="focus") {
        expression="working";reason="focus_started"
    } else if(before.focus==="focus" && input.focus==="break") {
        expression="happy";reason="focus_break"
    } else if(input.focus==="focus") {
        if(before.idle!=="unavailable" && idle!==before.idle && idle==="idle") {
            expression="thinking";reason="focus_quiet_thought"
        } else if(before.result!=="unavailable" && result!==before.result && result==="success") {
            expression="happy";reason="result_success"
        } else if(before.result!=="unavailable" && result!==before.result && result==="failure") {
            expression="alert";reason="result_failure"
        } else return {eligible:false,reason:"quiet_focus"}
    } else if(before.result!=="unavailable" && result!==before.result && result==="success") {
        expression="happy";reason="result_success"
    } else if(before.result!=="unavailable" && result!==before.result && result==="failure") {
        expression="alert";reason="result_failure"
    } else if(input.power!==before.power && input.power==="low") {
        expression="alert";reason="battery_low"
    } else if(before.power==="low" && input.power==="charging") {
        expression="happy";reason="charging_after_low"
    } else if(input.media!==before.media && input.media==="playing") {
        expression="happy";reason="playback_started"
    } else if(before.idle!=="unavailable" && idle!==before.idle && idle==="idle") {
        expression="thinking";reason="quiet_thought"
    }
    if(!expression)return {eligible:false,reason:reason}
    if(state.lastReaction>=0 && now-state.lastReaction<COOLDOWN_MS)
        return {eligible:false,reason:"cooldown"}
    state.lastReaction=now
    return {eligible:true,reason:reason,expression:expression}
}
