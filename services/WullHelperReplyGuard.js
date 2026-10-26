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

function validPreferenceValue(field, value) {
    const allowed = {
        personality: ["calm", "balanced", "energetic", "lively", "shy"],
        appearanceFrequency: ["always", "frequent", "occasional", "rare", "normal"],
        dialogueTone: ["gentle", "witty", "playful", "concise"],
        familiarity: ["reserved", "neutral", "familiar", "warm"],
        proactive: ["rare", "occasional", "regular", "often", "manual"],
        proactiveQuietEnabled: [true, false]
    }
    return allowed[field] ? allowed[field].includes(value) : false
}

function validToolReceipt(row) {
    return object(row)
        && typeof row.receiptId === "string" && /^rcpt_[0-9a-f]{16}$/.test(row.receiptId)
        && typeof row.proposalId === "string" && /^prop_[0-9a-f]{16}$/.test(row.proposalId)
        && string(row.field, 64, false) && string(row.configPath, 128, false)
        && validPreferenceValue(row.field, row.beforeValue)
        && validPreferenceValue(row.field, row.afterValue)
        && integer(row.appliedAtMs, 0, Number.MAX_SAFE_INTEGER)
        && typeof row.digest === "string" && /^[0-9a-f]{64}$/.test(row.digest)
        && typeof row.rolledBack === "boolean"
}

function validResult(action, result) {
    if (!object(result)) return false
    if (action === "tool_inspect") return ["host_metadata", "repo_metadata", "doc_metadata", "active_app"].includes(result.kind)
        && typeof result.hostAvailable === "boolean"
        && string(result.packageId, 120, false)
        && string(result.version, 80, false)
        && result.localOnlyDefault === true
        && Array.isArray(result.capabilities) && result.capabilities.length <= 16
        && result.capabilities.every(v => string(v, 80, false))
        && Array.isArray(result.headings) && result.headings.length <= 16
        && result.headings.every(v => string(v, 160, false))
    if (action === "tool_route") return typeof result.allowed === "boolean"
        && ["reflex", "brain", "blocked"].includes(result.route)
        && typeof result.ragUsed === "boolean"
        && string(result.reason, 80, false)
        && Array.isArray(result.snippets) && result.snippets.length <= 3
        && result.snippets.every(v => string(v, 320, false))
        && integer(result.estimatedTokens, 0, 16384)
    if (action === "tool_propose") return typeof result.proposalId === "string"
        && /^prop_[0-9a-f]{16}$/.test(result.proposalId)
        && string(result.field, 64, false) && string(result.configPath, 128, false)
        && validPreferenceValue(result.field, result.beforeValue)
        && validPreferenceValue(result.field, result.afterValue)
        && integer(result.createdMs, 0, Number.MAX_SAFE_INTEGER)
        && integer(result.expiresMs, result.createdMs, Number.MAX_SAFE_INTEGER)
        && result.status === "pending"
    if (action === "tool_confirm" || action === "tool_rollback")
        return validToolReceipt(result) && typeof result.idempotent === "boolean"
    if (action === "tool_cancel") return typeof result.proposalId === "string"
        && /^prop_[0-9a-f]{16}$/.test(result.proposalId)
        && result.cancelled === true && result.status === "cancelled"
    if (action === "tool_receipts") return Array.isArray(result.receipts)
        && result.receipts.length <= 128 && result.receipts.every(validToolReceipt)
    if (action === "agent_file_op") return ["access", "edit", "copy", "cut", "delete", "cancel_delete"].includes(result.op)
        && ["completed", "confirm_1_required", "confirm_2_required", "cancelled"].includes(result.status)
        && integer(result.confirmStage, 0, 2)
        && path(result.targetPath, true)
        && path(result.destPath, true)
        && string(result.summary, 320, false)
        && Array.isArray(result.lines) && result.lines.length <= 24
        && result.lines.every(v => string(v, 220, false))
    if (action === "memory_list") return Array.isArray(result.memories) && result.memories.length<=128
        && result.memories.every(row=>object(row) && positiveId(row.id)
            && ["aqua","octo","shared"].includes(row.scope) && string(row.text,320,false)
            && !/[\u0000-\u001f\u007f]/.test(row.text) && row.source==="explicit_user"
            && integer(row.created_ms,0,Number.MAX_SAFE_INTEGER) && integer(row.expires_ms,0,Number.MAX_SAFE_INTEGER))
    if (action === "memory_save") return result.saved===true && positiveId(result.id)
    if (action === "memory_forget") return typeof result.deleted==="boolean"
    if (action === "memory_forget_all") return integer(result.deletedCount,0,128)
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
    if (typeof raw !== "string" || raw.length > (action==="memory_list" ? 98304 : 32768) || exitCode !== 0) return invalid()
    let envelope
    try { envelope = JSON.parse(raw) } catch (e) { return invalid() }
    if (!object(envelope) || typeof envelope.ok !== "boolean") return invalid()
    if (envelope.ok === false) {
        if (!object(envelope.error) || !string(envelope.error.message, 160, false)) return invalid()
        return {ok:false, error:envelope.error}
    }
    return validResult(action, envelope.result) ? {ok:true, result:envelope.result} : invalid()
}
