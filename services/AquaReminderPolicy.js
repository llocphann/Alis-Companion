.pragma library

var DAY_MS = 86400000
var MINUTE_MS = 60000
var HOUR_MS = 3600000

function title(value) {
    return typeof value === "string" ? value.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 180) : ""
}

function day(date) {
    return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0")
}

function validDate(value) {
    if (value && typeof value === "object" && typeof value.getTime === "function")
        return Number.isFinite(value.getTime()) ? new Date(value.getTime()) : null
    if (typeof value === "number" && Number.isFinite(value) && value > 0) {
        const parsed = new Date(value)
        return Number.isFinite(parsed.getTime()) ? parsed : null
    }
    if (typeof value === "string" && value.length > 0 && value.length <= 80) {
        const parsed = new Date(value)
        return Number.isFinite(parsed.getTime()) ? parsed : null
    }
    return null
}

function midnight(value) {
    const d = new Date(value)
    d.setHours(0, 0, 0, 0)
    return d
}

function addDays(value, count) {
    const d = new Date(value)
    d.setDate(d.getDate() + count)
    return d
}

function civilMs(value) {
    return Date.UTC(value.getFullYear(), value.getMonth(), value.getDate())
}

function civilIndex(value) {
    return civilMs(value) / DAY_MS
}

function safeId(value) {
    if (Number.isInteger(value) && value >= 0) return String(value)
    if (typeof value === "string" && /^[A-Za-z0-9_-]{1,32}$/.test(value.trim())) return value.trim()
    return ""
}

function supportedRecurrence(mode) {
    return ["daily", "weekly", "monthly", "yearly"].includes(mode)
}

function recurringVirtualMatches(baseValue, originalValue, mode) {
    const from = validDate(baseValue), target = validDate(originalValue)
    if (!from || !target || target <= from
            || from.getHours() !== target.getHours()
            || from.getMinutes() !== target.getMinutes()
            || from.getSeconds() !== target.getSeconds()
            || from.getMilliseconds() !== target.getMilliseconds()) return false
    const days = (civilMs(target) - civilMs(from)) / DAY_MS
    if (mode === "daily") return Number.isInteger(days) && days >= 1
    if (mode === "weekly") return Number.isInteger(days) && days >= 7 && days % 7 === 0
    if (mode === "monthly")
        return target.getDate() === from.getDate()
            && 12 * (target.getFullYear() - from.getFullYear()) + target.getMonth() - from.getMonth() >= 1
    if (mode === "yearly")
        return target.getMonth() === from.getMonth()
            && target.getDate() === from.getDate()
            && target.getFullYear() > from.getFullYear()
    return false
}

function generateSeriesDates(anchor, dayStart, dayEnd, recurrence, canProject, durationMs) {
    const dates = [anchor]
    if (!canProject || !supportedRecurrence(recurrence)) return dates
    const earliest = new Date(dayStart.getTime() - durationMs - 2 * DAY_MS)
    const through = dayEnd
    function range(first, last, create) {
        let startIdx = first
        if (last - startIdx + 1 > 1024) startIdx = last - 1023
        for (let idx = startIdx; idx <= last; idx++) {
            const candidate = create(idx)
            if (candidate) dates.push(candidate)
        }
    }
    if (recurrence === "daily" || recurrence === "weekly") {
        const step = recurrence === "daily" ? 1 : 7
        const first = Math.max(1, Math.floor((civilIndex(earliest) - civilIndex(anchor)) / step) - 1)
        const last = Math.ceil((civilIndex(through) - civilIndex(anchor)) / step)
        range(first, last, n => addDays(anchor, n * step))
    } else if (recurrence === "monthly") {
        const firstMonth = (earliest.getFullYear() - anchor.getFullYear()) * 12 + earliest.getMonth() - anchor.getMonth()
        const lastMonth = (through.getFullYear() - anchor.getFullYear()) * 12 + through.getMonth() - anchor.getMonth()
        range(Math.max(1, firstMonth - 1), lastMonth + 1, m => {
            const month = new Date(anchor.getFullYear(), anchor.getMonth() + m, 1, 12)
            const monthly = new Date(month.getFullYear(), month.getMonth(), anchor.getDate(),
                anchor.getHours(), anchor.getMinutes(), anchor.getSeconds(), anchor.getMilliseconds())
            return monthly.getMonth() === month.getMonth() ? monthly : null
        })
    } else if (recurrence === "yearly") {
        range(Math.max(1, earliest.getFullYear() - anchor.getFullYear() - 1),
            through.getFullYear() - anchor.getFullYear() + 1, y => {
                const yearly = new Date(anchor.getFullYear() + y, anchor.getMonth(), anchor.getDate(),
                    anchor.getHours(), anchor.getMinutes(), anchor.getSeconds(), anchor.getMilliseconds())
                return yearly.getMonth() === anchor.getMonth() ? yearly : null
            })
    }
    return dates
}

// Support EventFutureSchedule.js ("this and following" branch rules) when present.
function clockTuple(value) {
    return [value.getHours(), value.getMinutes(), value.getSeconds(), value.getMilliseconds()]
}

function branchSignature(mode, start, end) {
    return JSON.stringify([mode, mode === "daily" ? 0
        : mode === "weekly" ? start.getDay()
        : mode === "monthly" ? start.getDate() : [start.getMonth(), start.getDate()],
        clockTuple(start), (civilMs(end) - civilMs(start)) / DAY_MS, clockTuple(end)])
}

function validFutureRule(rule, mode) {
    const start = validDate(rule?.startDate), end = validDate(rule?.endDate)
    return Boolean(rule && rule.version === 1 && rule.mode === mode
        && typeof rule.key === "string" && rule.key.length > 0
        && Number.isSafeInteger(rule.fromCycle)
        && Number.isSafeInteger(rule.anchorCycle)
        && validDate(rule.rootStartDate) && validDate(rule.rootEndDate)
        && start && end && end - start >= 15 * MINUTE_MS)
}

function futureRules(event) {
    const mode = event?.recurrence ?? event?.repeat
    return supportedRecurrence(mode) && Array.isArray(event?.plannerFutureSchedule)
        ? event.plannerFutureSchedule.filter(rule => validFutureRule(rule, mode)) : []
}

function cycleOf(start, value, mode) {
    if (mode === "daily" || mode === "weekly")
        return Math.floor((civilMs(value) - civilMs(start)) / DAY_MS / (mode === "daily" ? 1 : 7))
    if (mode === "monthly")
        return 12 * (value.getFullYear() - start.getFullYear()) + value.getMonth() - start.getMonth()
    return value.getFullYear() - start.getFullYear()
}

function cycleIndex(value, mode) {
    if (mode === "daily") return civilMs(value) / DAY_MS
    if (mode === "weekly") return Math.floor((civilMs(value) / DAY_MS - 4) / 7)
    if (mode === "monthly") return value.getFullYear() * 12 + value.getMonth()
    return value.getFullYear()
}

function atCycle(startValue, endValue, mode, step) {
    const start = validDate(startValue), end = validDate(endValue)
    if (!start || !end || end <= start || !Number.isInteger(step)) return null
    let year = start.getFullYear(), month = start.getMonth(), dateNum = start.getDate()
    if (mode === "daily" || mode === "weekly") {
        const moved = new Date(year, month, dateNum + step * (mode === "daily" ? 1 : 7), 12)
        year = moved.getFullYear(); month = moved.getMonth(); dateNum = moved.getDate()
    } else if (mode === "monthly") {
        const target = new Date(year, month + step, 1, 12)
        year = target.getFullYear(); month = target.getMonth()
    } else if (mode === "yearly") {
        year += step
    } else return null
    const begin = new Date(year, month, dateNum, start.getHours(), start.getMinutes(),
        start.getSeconds(), start.getMilliseconds())
    if (begin.getFullYear() !== year || begin.getMonth() !== month || begin.getDate() !== dateNum)
        return null
    const finish = new Date(year, month, dateNum + (civilMs(end) - civilMs(start)) / DAY_MS,
        end.getHours(), end.getMinutes(), end.getSeconds(), end.getMilliseconds())
    return finish > begin ? { startDate: begin.toISOString(), endDate: finish.toISOString() } : null
}

function projectFutureScheduleForDay(event, rules, dayStart, dayEnd) {
    const entries = Array.isArray(event.occurrences) && event.occurrences.length ? event.occurrences : [event]
    function anchorOf(slot) {
        const s = validDate(slot?.plannerSeriesStartDate || slot?.startDate || slot?.dateTime)
        let e = validDate(slot?.plannerSeriesEndDate || slot?.endDate)
        if (!s) return null
        if (!e || e <= s) e = new Date(s.getTime() + HOUR_MS)
        return { start: s, end: e }
    }
    function branchInfo(index) {
        const slot = entries[index], range = slot && anchorOf(slot)
        if (!range) return null
        const key = slot.plannerBranchKey || branchSignature(event.recurrence, range.start, range.end)
        const known = rules.find(r => r.key === key)
        if (known) return { key: key, rootStartDate: known.rootStartDate, rootEndDate: known.rootEndDate }
        let earliest = range
        const candidates = entries.concat(Array.isArray(event.repeatTemplate) ? event.repeatTemplate : [],
            Array.isArray(event.selectedTemplate) ? event.selectedTemplate : [])
        for (const candidate of candidates) {
            const other = anchorOf(candidate)
            if (other && branchSignature(event.recurrence, other.start, other.end) === key
                    && other.start < earliest.start) earliest = other
        }
        return { key: key, rootStartDate: earliest.start.toISOString(), rootEndDate: earliest.end.toISOString() }
    }
    function rootRange(info, cycle) {
        return atCycle(info.rootStartDate, info.rootEndDate, event.recurrence,
            cycle - cycleIndex(validDate(info.rootStartDate), event.recurrence))
    }
    function resolved(info, cycle) {
        let selected = null
        for (const rule of rules) {
            if (rule.key === info.key && rule.fromCycle <= cycle) selected = rule
        }
        return selected
            ? atCycle(selected.startDate, selected.endDate, event.recurrence, cycle - selected.anchorCycle)
            : rootRange(info, cycle)
    }
    function pendingOverride(info, cycle) {
        const orig = rootRange(info, cycle)
        if (!orig || !Array.isArray(event.plannerOverrides)) return null
        return event.plannerOverrides.find(item => {
            const source = Number.isInteger(item?.occurrenceIndex) && branchInfo(item.occurrenceIndex)
            const s = validDate(item?.startDate), e = validDate(item?.endDate)
            return source && source.key === info.key && item.originalStartDate === orig.startDate
                && item.materializedOccurrenceIndex === undefined && item.forwardedEventId === undefined
                && s && e && e - s >= 15 * MINUTE_MS
        }) || null
    }
    const result = [], saved = new Set()
    entries.forEach((slot, index) => {
        const info = branchInfo(index), start = validDate(slot?.startDate || slot?.dateTime)
        let finish = validDate(slot?.endDate)
        if (!info || !start) return
        if (!finish || finish <= start) finish = new Date(start.getTime() + HOUR_MS)
        const cycle = Number.isInteger(slot.plannerScheduleCycle)
            ? slot.plannerScheduleCycle : cycleIndex(anchorOf(slot).start, event.recurrence)
        saved.add(JSON.stringify([info.key, cycle]))
        if (start < dayEnd && finish > dayStart)
            result.push({ slotIndex: index, startDate: start, endDate: finish, recurring: true })
    })
    if (!Array.isArray(event.occurrences) && event.notified === true) return result
    const foundBranches = new Map()
    const firstIdx = Array.isArray(event.repeatTemplate) && event.repeatTemplate.length
        ? Math.max(0, Number(event.repeatCycleStart) || 0)
        : Array.isArray(event.selectedTemplate) && event.selectedTemplate.length
            ? Math.max(0, entries.length - event.selectedTemplate.length) : 0
    entries.forEach((slot, index) => {
        if (index < firstIdx) return
        const info = branchInfo(index)
        if (!info) return
        const cycle = Number.isInteger(slot.plannerScheduleCycle)
            ? slot.plannerScheduleCycle : cycleIndex(anchorOf(slot).start, event.recurrence)
        const prev = foundBranches.get(info.key)
        if (!prev || cycle >= prev.cycle)
            foundBranches.set(info.key, Object.assign({}, info, { index: index, cycle: cycle }))
    })
    for (const info of foundBranches.values()) {
        const candidates = new Set()
        const sources = [{ startDate: info.rootStartDate,
            anchorCycle: cycleIndex(validDate(info.rootStartDate), event.recurrence) }]
            .concat(rules.filter(r => r.key === info.key))
        for (const rule of sources) {
            const first = rule.anchorCycle + cycleOf(validDate(rule.startDate), dayStart, event.recurrence) - 3
            const last = rule.anchorCycle + cycleOf(validDate(rule.startDate), dayEnd, event.recurrence) + 3
            for (let c = first; c <= last && candidates.size < 1024; c++) candidates.add(c)
        }
        for (const cycle of Array.from(candidates).sort((a, b) => a - b)) {
            if (cycle <= info.cycle || saved.has(JSON.stringify([info.key, cycle]))) continue
            const range = pendingOverride(info, cycle) || resolved(info, cycle)
            const s = validDate(range?.startDate), e = validDate(range?.endDate)
            if (!s || !e || s >= dayEnd || e <= dayStart) continue
            result.push({ slotIndex: info.index, startDate: s, endDate: e, recurring: true })
        }
    }
    return result
}

function projectEventForDay(event, dayStart, dayEnd, today) {
    const mode = event?.recurrence ?? event?.repeat
    const normalizedEvent = mode && !event?.recurrence ? Object.assign({}, event, { recurrence: mode }) : event
    const fRules = futureRules(normalizedEvent)
    if (fRules.length > 0) {
        return projectFutureScheduleForDay(normalizedEvent, fRules, dayStart, dayEnd)
            .filter(occ => day(occ.startDate) === today)
    }
    const grouped = Array.isArray(event.occurrences) && event.occurrences.length > 0
    const ranges = grouped ? event.occurrences.slice(0, 512) : [event]
    const recurrence = supportedRecurrence(mode) ? mode : "none"
    const repeatingGroup = Array.isArray(event.repeatTemplate)
        && event.repeatTemplate.length > 0 && recurrence !== "none"
    const selectedStart = recurrence === "none" && grouped
        && Array.isArray(event.selectedTemplate) && event.selectedTemplate.length > 0
        ? Math.max(0, ranges.length - event.selectedTemplate.length) : 0
    const occurrences = []

    for (let slotIndex = selectedStart; slotIndex < ranges.length; slotIndex++) {
        const slot = ranges[slotIndex]
        if (!slot || typeof slot !== "object" || slot.cancelled === true || slot.deleted === true)
            continue
        const base = validDate(slot.startDate ?? slot.dateTime ?? slot.startTime)
        if (!base) continue
        const rawEnd = slot.endDate ?? slot.endTime
        if (rawEnd !== undefined && rawEnd !== null && rawEnd !== "") {
            const explicitEnd = validDate(rawEnd)
            if (!explicitEnd || explicitEnd <= base) continue
        }
        const baseEnd = validDate(rawEnd) || new Date(base.getTime() + HOUR_MS)
        const duration = Math.max(MINUTE_MS, baseEnd - base)
        const activeGroup = repeatingGroup && slotIndex >= Number(event.repeatCycleStart || 0)
        if (repeatingGroup && !activeGroup && slot.notified === true)
            continue

        if (recurrence === "none") {
            if (day(base) === today)
                occurrences.push({ slotIndex: slotIndex, startDate: base, endDate: baseEnd, recurring: false })
            continue
        }

        const anchor = validDate(slot.plannerSeriesStartDate) || base
        const anchorEnd = validDate(slot.plannerSeriesEndDate) || baseEnd
        const seriesDuration = anchorEnd && anchorEnd > anchor ? anchorEnd - anchor : duration
        const canProject = repeatingGroup ? activeGroup : (slot.notified !== true)
        const overrides = Array.isArray(event.plannerOverrides)
            ? event.plannerOverrides.filter(item => item
                && item.occurrenceIndex === slotIndex
                && item.materializedOccurrenceIndex === undefined
                && item.forwardedEventId === undefined) : []

        function overrideFor(occurrence) {
            return overrides.find(item => item.originalStartDate === occurrence.toISOString()) || null
        }

        const generated = generateSeriesDates(anchor, dayStart, dayEnd, recurrence, canProject, seriesDuration)
        let sawAnchor = false
        const seenVirtual = new Set()

        for (const occurrence of generated) {
            const saved = occurrence.getTime() === anchor.getTime()
            if (saved) sawAnchor = true
            else seenVirtual.add(occurrence.toISOString())
            if (!saved && occurrence.getTime() === base.getTime() && base.getTime() !== anchor.getTime())
                continue
            let corr = saved ? null : overrideFor(occurrence)
            const corrStart = corr && validDate(corr.startDate)
            const corrEnd = corr && validDate(corr.endDate)
            if (!corrStart || !corrEnd || corrEnd - corrStart < 15 * MINUTE_MS)
                corr = null
            const actualStart = corr ? corrStart : (saved ? base : occurrence)
            const actualEnd = corr ? corrEnd : new Date(actualStart.getTime() + (saved ? duration : seriesDuration))
            if (day(actualStart) === today)
                occurrences.push({ slotIndex: slotIndex, startDate: actualStart, endDate: actualEnd, recurring: true })
        }

        for (const item of overrides) {
            if (seenVirtual.has(item.originalStartDate)) continue
            const orig = validDate(item.originalStartDate)
            const corrStart = validDate(item.startDate), corrEnd = validDate(item.endDate)
            if (orig && corrStart && corrEnd && corrEnd - corrStart >= 15 * MINUTE_MS
                    && recurringVirtualMatches(anchor, orig, recurrence)
                    && day(corrStart) === today) {
                occurrences.push({ slotIndex: slotIndex, startDate: corrStart, endDate: corrEnd, recurring: true })
            }
        }

        if (!sawAnchor && anchor.getTime() !== base.getTime() && day(base) === today)
            occurrences.push({ slotIndex: slotIndex, startDate: base, endDate: baseEnd, recurring: true })
    }
    return occurrences
}

// Collect consented Todo and Weekly Planner Event reminders for today.
// Obsidian schedules/journals are never used as a reminder source (AC-F08).
function collect(now, consent, todo, planner) {
    const current = validDate(now)
    if (!current) return []
    const today = day(current), dayStart = midnight(current), dayEnd = addDays(dayStart, 1), rows = []
    if (consent?.todo === true && Array.isArray(todo)) {
        for (const row of todo.slice(0, 128)) {
            if (!row || row.done !== false || row.sourceDate !== today || typeof row.startTime !== "string")
                continue
            const time = /^(\d{1,2}):(\d{2})$/.exec(row.startTime)
            if (!time || Number(time[1]) >= 24 || Number(time[2]) >= 60) continue
            const start = Number(time[1]) * 60 + Number(time[2])
            const label = title(row.content)
            if (label) {
                rows.push({
                    start: start,
                    title: label,
                    kind: "task",
                    source: "todo",
                    leadMinutes: 10,
                    key: "todo:" + start
                })
            }
        }
    }
    const plannerAllowed = consent?.planner === true || consent?.calendar === true
    if (plannerAllowed && Array.isArray(planner)) {
        for (const event of planner.slice(0, 128)) {
            if (!event || typeof event !== "object" || Array.isArray(event)) continue
            if (event.allDay === true || event.cancelled === true || event.deleted === true
                    || String(event.status ?? "").toUpperCase() === "CANCELLED") continue
            const label = title(event.title ?? event.summary)
            if (!label) continue
            const id = safeId(event.id ?? event.uid)
            const rawLead = Number.isInteger(event.reminderMinutes) ? event.reminderMinutes
                : (Number.isInteger(event.leadMinutes) ? event.leadMinutes : 10)
            const leadMinutes = rawLead > 0 ? Math.min(60, Math.max(10, rawLead)) : 10
            const occurrences = projectEventForDay(event, dayStart, dayEnd, today)
            const seenInEvent = new Set()
            for (const occ of occurrences) {
                const start = occ.startDate.getHours() * 60 + occ.startDate.getMinutes()
                if (!Number.isInteger(start) || start < 0 || start >= 1440) continue
                const key = id
                    ? ("planner:" + id + ":" + occ.slotIndex + ":" + start)
                    : ("planner:" + start)
                if (seenInEvent.has(key)) continue
                seenInEvent.add(key)
                rows.push({
                    start: start,
                    title: label,
                    kind: "agenda",
                    source: "planner",
                    eventId: id,
                    occurrenceIndex: occ.slotIndex,
                    occurrenceDate: today,
                    startIso: occ.startDate.toISOString(),
                    recurring: occ.recurring === true,
                    leadMinutes: leadMinutes,
                    key: key
                })
            }
        }
    }
    rows.sort((a, b) => a.start - b.start || String(a.key).localeCompare(String(b.key)))
    return rows
}
