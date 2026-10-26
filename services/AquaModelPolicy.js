.pragma library

// Companion policy only; catalog and inference transport remain Alis-owned.
function isLocal(model) {
    if (!model || model.local !== true) return false
    if (model.api_format === "gguf")
        return typeof model.gguf_path === "string" && model.gguf_path.startsWith("/")
    if (model.local_verified !== true || !["ollama", "lmstudio"].includes(model.provider_id)
            || !/^[0-9a-f]{64}$/.test(String(model.local_fingerprint ?? ""))) return false
    if (!["openai", "openai-response"].includes(model.api_format)) return false
    const match = /^https?:\/\/([^/?#\s\\]+)(?:[/?#]|$)/i.exec(String(model.endpoint ?? ""))
    if (!match) return false
    const authority = /^(localhost|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|\[::1\])(?::(\d{1,5}))?$/i.exec(match[1])
    if (!authority || (authority[2] && (Number(authority[2]) < 1 || Number(authority[2]) > 65535))) return false
    return !authority[1].startsWith("127.") || authority[1].split(".").every(part => Number(part) <= 255 && String(Number(part)) === part)
}

function allowed(model, localOnly) { return !!model && (!localOnly || isLocal(model)) }

// Snapshot transport identity without credentials. Catalog entries may be
// replaced or edited in place while retaining the same model ID.
function identity(model) {
    if (!model) return ""
    const fields = ["model", "endpoint", "api_format", "provider_id", "auth_scheme",
        "requires_key", "key_id", "local", "local_verified", "local_fingerprint", "gguf_path", "runtime_path",
        "max_output_tokens", "capabilities", "extraParams"]
    return JSON.stringify(fields.map(key => model[key] ?? null))
}
