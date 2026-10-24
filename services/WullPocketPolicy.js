.pragma library

function urls(values) {
    if (!Array.isArray(values) || values.length < 1 || values.length > 8) return null
    if (values.some(value => typeof value !== "string" || value.length > 12288
            || !/^file:\/\/\//.test(value) || /[\u0000-\u001f\u007f-\u009f]/.test(value))) return null
    return values.slice()
}

function reply(raw, action, exitCode) {
    if (typeof raw !== "string" || raw.length > 512 * 1024) return null
    let value
    try {value = JSON.parse(raw)} catch (_) {return null}
    if (!value || Object.keys(value).sort().join(",") !== "action,changed,items,ok,version"
            || value.version !== 1 || value.ok !== true || exitCode !== 0 || value.action !== action
            || !Array.isArray(value.items) || value.items.length > 32
            || !Number.isSafeInteger(value.changed) || value.changed < 0 || value.changed > 32) return null
    const ids = new Set(), paths = new Set()
    for (const item of value.items) {
        if (!item || Object.keys(item).sort().join(",") !== "created_ms,id,modified_ms,name,path,size,source"
                || !Number.isSafeInteger(item.id) || item.id < 1 || ids.has(item.id)
                || typeof item.path !== "string" || item.path.length < 2 || item.path.length > 4096
                || !item.path.startsWith("/") || paths.has(item.path)
                || typeof item.name !== "string" || !item.name || item.name.length > 255
                || /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u206f]/.test(item.name + item.path)
                || !["picker", "drop"].includes(item.source)
                || [item.size,item.modified_ms,item.created_ms].some(n => !Number.isSafeInteger(n) || n < 0)) return null
        ids.add(item.id); paths.add(item.path)
    }
    return value
}

function error(raw) {
    let code = ""
    try {code = JSON.parse(raw).error} catch (_) {}
    const messages = {
        selection_limit: "Choose up to eight local files at a time.",
        pocket_full: "Pocket holds up to 32 references. Remove a reference first.",
        local_files_only: "Choose a local file. Remote links are not supported.",
        invalid_file_url: "This file reference is not supported.",
        regular_files_only: "Choose regular files. Folders, links and devices are not supported.",
        file_unavailable: "A selected file is unavailable or uses a symbolic link.",
        unsupported_store_version: "This Pocket store needs a compatible Companion release.",
        invalid_stored_metadata: "Pocket could not read its saved references. Your originals are unchanged."
    }
    return messages[code] ?? "Pocket could not complete the request. Reopen Pocket to refresh saved references."
}
