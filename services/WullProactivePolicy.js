.pragma library

// The caller supplies consent, coarse host state and one clock. No effects.
var EVENTS = ["reminder", "checkin", "playful"]
var MAX_UNANSWERED = 3
var BASE_BACKOFF_MS = 30 * 60 * 1000
var MAX_BACKOFF_MS = 4 * 60 * 60 * 1000

function fresh(day) {
    return {day:day, dailyCount:0, reminder:0, checkin:0, playful:0,
        unanswered:0, dismissals:0, lastNudgeMs:0, reminderKeys:[]}
}

function quietMinute(current, start, end) {
    if (![current,start,end].every(n => Number.isInteger(n) && n >= 0 && n < 1440)) return null
    return start === end ? false : start < end ? current >= start && current < end
        : current >= start || current < end
}

function stateFor(value, day) {
    if (!value || value.day !== day) return fresh(day)
    return {day:day, dailyCount:value.dailyCount, reminder:value.reminder,
        checkin:value.checkin, playful:value.playful, unanswered:value.unanswered,
        dismissals:value.dismissals, lastNudgeMs:value.lastNudgeMs,
        reminderKeys:Array.from(value.reminderKeys ?? [])}
}

function decide(value, p) {
    if (!p || !EVENTS.includes(p.event) || !Number.isSafeInteger(p.nowMs) || p.nowMs < 0
            || !/^\d{4}-\d{2}-\d{2}$/.test(p.day ?? "")) return {eligible:false, reason:"invalid_request"}
    if (p.optedIn !== true || p.manual === true || p.talkEnabled !== true)
        return {eligible:false, reason:"disabled_or_manual"}
    if (p.visible !== true || p.blocked !== false || p.chatOpen === true
            || p.contextOpen === true || p.busy === true)
        return {eligible:false, reason:"host_ineligible"}
    if (p.quietEnabled !== false) {
        const quiet = quietMinute(p.minuteOfDay, p.quietStart, p.quietEnd)
        if (quiet === null) return {eligible:false, reason:"invalid_quiet_config"}
        if (quiet) return {eligible:false, reason:"quiet_hours"}
    }
    const s = stateFor(value, p.day)
    if (![s.dailyCount,s.reminder,s.checkin,s.playful,s.unanswered,s.dismissals]
            .every(n => Number.isSafeInteger(n) && n >= 0)) return {eligible:false, reason:"invalid_state"}
    if (!Number.isInteger(p.dailyCap) || p.dailyCap < 1 || p.dailyCap > 20
            || s.dailyCount >= p.dailyCap) return {eligible:false, reason:"daily_cap"}
    if (!Number.isInteger(p.eventCap) || p.eventCap < 1 || p.eventCap > 10
            || s[p.event] >= p.eventCap) return {eligible:false, reason:"event_cap"}
    if (s.unanswered >= MAX_UNANSWERED || s.dismissals >= MAX_UNANSWERED)
        return {eligible:false, reason:"ignored_stop"}
    if (p.event !== "reminder" && p.idle !== true) return {eligible:false, reason:"not_idle"}
    if (!Number.isSafeInteger(s.lastNudgeMs) || s.lastNudgeMs < 0 || s.lastNudgeMs > p.nowMs)
        return {eligible:false, reason:"invalid_clock"}
    if (p.event === "reminder" && (typeof p.key !== "string" || p.key.length > 100
            || !/^(todo|calendar|journal):\d{1,4}$/.test(p.key)
            || (p.preview !== true && s.reminderKeys.includes(p.key))))
        return {eligible:false, reason:"duplicate_or_invalid_reminder"}
    const backoff = Math.max(Number(p.intervalMs) || 0,
        Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** Math.max(s.unanswered,s.dismissals)))
    if (s.lastNudgeMs && p.nowMs - s.lastNudgeMs < backoff)
        return {eligible:false, reason:"cooldown"}
    return {eligible:true, reason:"eligible"}
}

function record(value, p) {
    if (p?.preview === true || !decide(value, p).eligible) return null
    const s = stateFor(value, p.day)
    s.dailyCount++; s[p.event]++; s.unanswered++; s.lastNudgeMs = p.nowMs
    if (p.event === "reminder") s.reminderKeys = s.reminderKeys.slice(-127).concat([p.key])
    return s
}
