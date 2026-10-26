// DORMANT deterministic, inspectable policy proposal, NOT loaded by QML.
// No inference, timers, notifications, state persistence or reads.
// Original implementation inspired by Mak1zu's quiet-hour/backoff principle.
// Future product use needs UI preferences, user approval and real host QA.
var MAX_UNANSWERED = 3
var BASE_BACKOFF_MS = 30*60*1000
var MAX_BACKOFF_MS = 4*60*60*1000

function inQuietMinute(current, start, end) {
    if (![current,start,end].every(n => Number.isInteger(n) && n>=0 && n<1440))
        return null
    if (start===end) return false
    return start<end ? current>=start && current<end
                     : current>=start || current<end
}

function decide(p) {
    if (!p || typeof p!=="object" || !Number.isFinite(p.nowMs)
            || p.nowMs<0 || !["reminder","checkin","playful"].includes(p.event))
        return {eligible:false,reason:"invalid_request"}
    if (p.optedIn !== true || p.manual === true || p.talkEnabled !== true)
        return {eligible:false,reason:"disabled_or_manual"}
    if (p.visible !== true || p.dnd === true || p.fullscreen === true
            || p.chatOpen === true || p.contextOpen === true || p.busy === true)
        return {eligible:false,reason:"host_ineligible"}
    if (p.quietEnabled === true) {
        const quiet = inQuietMinute(p.minuteOfDay,p.quietStartMinute,p.quietEndMinute)
        if (quiet===null) return {eligible:false,reason:"invalid_quiet_config"}
        if (quiet) return {eligible:false,reason:"quiet_hours"}
    }
    if (!Number.isInteger(p.dailyCount) || p.dailyCount<0
            || !Number.isInteger(p.dailyCap) || p.dailyCap<1 || p.dailyCap>20
            || p.dailyCount>=p.dailyCap)
        return {eligible:false,reason:"daily_cap"}
    if (!Number.isInteger(p.unanswered) || p.unanswered<0 ||
            !Number.isInteger(p.dismissals) || p.dismissals<0)
        return {eligible:false,reason:"invalid_streak"}
    if (p.unanswered>=MAX_UNANSWERED || p.dismissals>=MAX_UNANSWERED)
        return {eligible:false,reason:"ignored_stop"}
    if ((p.event==="checkin" || p.event==="playful") && p.idle !== true)
        return {eligible:false,reason:"not_idle"}
    if (!Number.isFinite(p.lastNudgeMs) || p.lastNudgeMs<0 || p.lastNudgeMs>p.nowMs)
        return {eligible:false,reason:"invalid_clock"}
    const backoff = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** p.unanswered)
    if (p.nowMs-p.lastNudgeMs<backoff)
        return {eligible:false,reason:"cooldown",remainingMs:backoff-(p.nowMs-p.lastNudgeMs)}
    return {eligible:true,reason:"eligible",backoffMs:backoff}
}
