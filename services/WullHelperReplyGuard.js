.pragma library
.import "WullReplyGuard.js" as PublicReply

// Validate helper data before it can replace UI history, journal or receipts.
// Unknown metadata remains compatible within the bounded JSON envelope.
var invalidMessage = "The local helper did not return a valid reply."

function object(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value)
}

function string(value, maximum, empty) {
    return typeof value === "string" && value.length <= maximum
        && (empty || value.trim().length > 0)
}

function integer(value, minimum, maximum) {
    return Number.isSafeInteger(value) && value >= minimum && value <= maximum
}

function positiveId(value) { return integer(value, 1, Number.MAX_SAFE_INTEGER) }
function minute(value) { return integer(value, 0, 1439) }
function date(value) { return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) }
function path(value, empty) { return string(value, 4096, empty) && !/[\u0000\r\n]/.test(value) }

function history(result) {
    return Array.isArray(result.messages) && result.messages.length <= 100
        && typeof result.hasMore === "boolean" && result.messages.every(row =>
            object(row) && positiveId(row.id) && ["user", "assistant"].includes(row.role)
            && string(row.content, 6000, false)
            && integer(row.createdMs, 0, Number.MAX_SAFE_INTEGER)
            && string(row.model, 160, true))
}

function context(result) {
    return date(result.date) && string(result.mood, 60, true) && string(result.energy, 60, true)
        && path(result.journalPath, true)
        && Array.isArray(result.schedule) && result.schedule.length <= 16
        && result.schedule.every(row => object(row) && minute(row.start)
            && (row.end === null || minute(row.end)) && string(row.title, 180, false)
            && string(row.source, 255, false) && ["schedule", "calisthenics", "cardio"].includes(row.kind))
        && (result.vaults === undefined || (Array.isArray(result.vaults) && result.vaults.length <= 2
            && result.vaults.every(value => string(value, 255, false))))
}

function checkIn(result) {
    const choices = result.field === "mood" ? ["terrible", "bad", "okay", "good", "great"]
        : result.field === "energy" ? ["drained", "low", "medium", "high", "peak"] : []
    return result.saved === true && choices.includes(result.value)
        && date(result.date) && path(result.journalPath, false)
}

function chat(result) {
    const userId = result.userMessageId, assistantId = result.assistantMessageId
    return string(result.text, 6000, false) && PublicReply.normalizeText(result.text, result.expression).ok
        && typeof result.historySaved === "boolean"
        && ((userId === 0 && assistantId === 0)
            || (positiveId(userId) && positiveId(assistantId) && assistantId > userId))
}

function probe(result) {
    return typeof result.ready === "boolean" && string(result.endpoint, 4096, false)
        && Array.isArray(result.models) && result.models.length <= 32
        && result.models.every(row => object(row) && string(row.name, 120, false)
            && integer(row.size, 0, Number.MAX_SAFE_INTEGER) && typeof row.thinking === "boolean")
}

function validResult(action, result) {
    if (!object(result)) return false
    if (action === "history") return history(result)
    if (action === "history_append") return positiveId(result.userMessageId)
        && positiveId(result.assistantMessageId) && result.assistantMessageId > result.userMessageId
    if (action === "history_clear") return result.cleared === true
    if (action === "context") return context(result)
    if (action === "check_in") return checkIn(result)
    if (action === "chat") return chat(result)
    if (action === "probe") return probe(result)
    return false
}

function invalid() { return {ok:false, malformed:true, error:{message:invalidMessage}} }

function parse(action, raw, exitCode) {
    if (typeof raw !== "string" || raw.length > 32768 || exitCode !== 0) return invalid()
    let envelope
    try { envelope = JSON.parse(raw) } catch (e) { return invalid() }
    if (!object(envelope) || typeof envelope.ok !== "boolean") return invalid()
    if (envelope.ok === false) {
        if (!object(envelope.error) || !string(envelope.error.message, 160, false)) return invalid()
        return {ok:false, error:envelope.error}
    }
    return validResult(action, envelope.result) ? {ok:true, result:envelope.result} : invalid()
}
