.pragma library

// Pure, bounded public-output adapter for shared-AI Companion replies.
// No tools, permissions, disk access, model retries or side effects.
// Original Hadanion code; no Mak1zu implementation imported.
var expressions = ["idle", "happy", "excited", "thinking", "working",
                   "surprised", "sleepy", "sad", "alert"]
var internalTag = /<\/?\s*(think(?:ing)?|analysis|tool(?:_call|_result)?|function_call|system|assistant|developer)\b/i
var protocolToken = /<[|｜]|\[\s*\/?\s*INST\s*\]|<<\s*\/?\s*SYS\s*>>|<\/?\s*(start_of_turn|end_of_turn|bos|eos)\s*>/i
var functionEnvelope = /\[\[\s*Function\s*:/i
var controlBytes = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g
// Reject mixed prose/metadata and concrete, unreceipted first-person desktop
// claims. This is a public-text check, never permission to execute an action.
var mixedMetadata = /\{\s*["']?(?:text|expression|tool_calls|function_call)["']?\s*:|\bexpression\s*:\s*["']?[a-z]+["']?\s*[}.,]?\s*$/i
var actionClaim = /\bI(?:['’](?:ve|ll|m))?(?:\s+(?:have|already|just|will|can|decide\s+to|decided\s+to|am\s+going\s+to|going\s+to)){0,3}\s+(?:change[ds]?|delet(?:e[ds]?|ing)|rearrang(?:e[ds]?|ing)|install(?:ed)?|open(?:ed)?|save[ds]?|schedul(?:e[ds]?|ing)|send|sent|writ(?:e|ten)|wrote|updat(?:e[ds]?|ing)|run|ran|execut(?:e[ds]?|ing))\s+(?:(?:your|the|a|an|this|that|my|these|those)\s+)?(?:desktop|wallpaper|files?|folders?|icons?|settings?|apps?|programs?|scripts?|commands?|reminders?|appointments?|messages?|memory|memories)\b/i

// Chat has no deletion tool or receipt. Explicit requests to forget stored
// conversations must get an application fact, not an inferred success claim.
// This does not clear, migrate or rewrite any already saved record.
var privacyNotice = "Nothing was deleted. Alis can store chats locally; use Clear conversation to clear them or memory controls to forget saved memories. Obsidian copies and backups may remain."
var englishDeletion = /^(?:(?:please|can you|could you|would you|will you|i want you to|i would like you to)\s+){0,2}(forget|delete|erase|clear|remove|wipe)\s+(.+)$/
var englishHistory = /^(?:(?:all(?: of)?|my|our|your|the|this|these|those|previous|old|entire|saved|local|current|past|earlier)\s+){0,5}(?:chats?|conversations?|history|messages?|memories|memory|(?:what|everything|anything) (?:i|we) (?:said|told|wrote|shared|asked))\b/
var vietnameseDeletion = /^(?:(?:hay|vui long|lam on|ban co the|ban hay|xin)\s+){0,2}(quen|xoa)\s+(.+)$/
var vietnameseHistory = /^(?:(?:tat ca|het|cac|nhung|toan bo|cu|da luu|truoc do)\s+){0,5}(?:doan chat|cuoc tro chuyen|tin nhan|lich su|ky uc|bo nho|thong tin da luu|nhung gi (?:toi|chung ta) (?:da )?(?:noi|viet|chia se))\b/

function privacyReply(prompt) {
    if (typeof prompt !== "string" || prompt.length > 1200) return null
    const normalized = prompt.trim().toLowerCase().normalize("NFD")
        .replace(/[\u0300-\u036f]/g,"").replace(/đ/g,"d").replace(/\s+/g," ")
    const en = normalized.match(englishDeletion)
    const vi = normalized.match(vietnameseDeletion)
    const requested = en && (englishHistory.test(en[2])
        || (en[1]==="forget" && /^(?:everything|anything|all|me)\b/.test(en[2])))
        || vi && (vietnameseHistory.test(vi[2])
            || (vi[1]==="xoa" && vi[2].startsWith("bo ") && vietnameseHistory.test(vi[2].slice(3)))
            || (vi[1]==="quen" && /^(?:tat ca|het|moi thu|toi)\b/.test(vi[2])))
    return requested ? {ok:true,text:privacyNotice,expression:"idle"} : null
}

function forChatRequest(guarded, prompt) {
    // Keep malformed/protocol rejection ahead of this capability response.
    return guarded.ok ? privacyReply(prompt) || guarded : guarded
}

function normalizeText(text, expression) {
    if (typeof text !== "string") return {ok:false,reason:"invalid_text"}
    // Control removal can reveal a protocol marker. Inspect the complete
    // candidate before the public-text limit can hide a trailing envelope.
    const candidate = text.replace(controlBytes,"")
    if (internalTag.test(candidate) || protocolToken.test(candidate) || functionEnvelope.test(candidate))
        return {ok:false,reason:"internal_protocol"}
    if (mixedMetadata.test(candidate)) return {ok:false,reason:"malformed_structured_output"}
    if (actionClaim.test(candidate)) return {ok:false,reason:"unverified_action_claim"}
    const value = candidate.trim().slice(0,420)
    if (!value) return {ok:false,reason:"empty_text"}
    const semantic = expressions.indexOf(expression) >= 0 ? expression : "idle"
    return {ok:true,text:value,expression:semantic}
}

function parse(raw) {
    if (typeof raw !== "string" || raw.length > 32768)
        return {ok:false,reason:"invalid_envelope"}
    const cleaned = raw.trim().replace(/^\x60{3}(?:json)?\s*|\s*\x60{3}$/g,"")
    let parsed
    try { parsed=JSON.parse(cleaned) } catch(e) {
        // Never echo a malformed structured JSON/tool envelope as a user reply.
        if (/^[\[{]/.test(cleaned))
            return {ok:false,reason:"malformed_structured_output"}
        // Keep the pre-existing plain-text fallback for real plain prose.
        return normalizeText(cleaned,"idle")
    }
    if (parsed===null || typeof parsed !== "object" || Array.isArray(parsed)
            || Object.keys(parsed).some(key=>key!=="text" && key!=="expression"))
        return {ok:false,reason:"invalid_response_object"}
    return normalizeText(parsed.text, parsed.expression)
}
