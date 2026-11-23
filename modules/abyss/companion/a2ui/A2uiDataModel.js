.pragma library

// Stateless A2UI v1.0 data model, JSON Pointer (RFC 6901), binding resolver, and client-side guard.

var A2UI_VERSION = "1.0"
var MAX_SURFACE_BYTES = 6144
var MAX_COMPONENTS = 40
var MAX_DEPTH = 6
var MAX_POINTER_DEPTH = 12

function _stripCtrl(s) {
    return String(s ?? "").replace(/[\x00-\x1f\x7f]+/g, "")
}

function _makeAllowMap(keys) {
    const m = Object.create(null)
    for (let i = 0; i < keys.length; ++i)
        m[keys[i]] = true
    return m
}

function _hasOwn(obj, key) {
    return obj !== null && obj !== undefined && Object.prototype.hasOwnProperty.call(obj, key)
}

var FORBIDDEN_OBJECT_KEYS = _makeAllowMap(["__proto__", "constructor", "prototype"])

function _isSafeObjectKey(key) {
    const s = String(key ?? "")
    return s.length > 0 && !_hasOwn(FORBIDDEN_OBJECT_KEYS, s)
}

var ALLOWED_COMPONENTS = _makeAllowMap([
    "Column", "Row", "Card", "Divider", "Text", "Icon", "Button",
    "List", "ChoicePicker", "CheckBox", "TextField", "Slider", "Tabs", "Image"
])

var FORBIDDEN_COMPONENTS = _makeAllowMap([
    "Video", "AudioPlayer", "DateTimeInput", "Modal"
])

var SAFE_INSTANT_CLIENT_ACTIONS = _makeAllowMap([
    "weather_refresh",
    "music_toggle", "music_next", "music_prev", "music_shuffle", "music_stop", "music_play_query",
    "pomodoro_toggle", "pomodoro_start", "pomodoro_pause", "pomodoro_reset", "pomodoro_preset",
    "stopwatch_toggle", "stopwatch_reset",
    "night_light_set", "night_light_toggle",
    "gamemode_set", "gamemode_toggle",
    "dnd_set", "dnd_toggle",
    "nexus_open", "nexus_open_note", "nexus_open_daily"
])

var ALLOWED_FUNCTION_CALLS = _makeAllowMap([
    "openUrl", "clientAction", "copyText", "openFile"
])

var PRIVILEGED_FUNCTION_CALLS = _makeAllowMap([
    "openFile"
])

var ALLOWED_SERVER_EVENTS = _makeAllowMap([
    "confirm_action", "send_prompt", "followup_prompt", "choice_submit",
    "checklist_toggle", "plan_toggle", "rollback_receipt", "run_tests",
    "open_editor", "save_pocket", "form_submit", "slider_change"
])

var PRIVILEGED_SERVER_EVENTS = _makeAllowMap([
    "confirm_action", "rollback_receipt", "run_tests", "open_editor"
])

var TAINTED_ALLOWED_SERVER_EVENTS = _makeAllowMap([
    "choice_submit", "checklist_toggle", "plan_toggle", "form_submit", "slider_change"
])

var _SECRET_FIELD_RE = /(?:password|passwd|sudo|secret|api[_-]?key|credential|passphrase|pin_code|mật[\s_-]?khẩu|mat[\s_-]?khau)/i
var _HTTP_URL_RE = /^https?:\/\/[^\s\x00-\x1f\x7f]{3,1020}$/i
var _ICON_NAME_RE = /^[a-z][a-z0-9_]{0,47}$/

function utf8ByteLength(str) {
    const s = String(str ?? "")
    let bytes = 0
    for (let i = 0; i < s.length; ++i) {
        const code = s.charCodeAt(i)
        if (code < 0x80) {
            bytes += 1
        } else if (code < 0x800) {
            bytes += 2
        } else if (code >= 0xd800 && code <= 0xdbff && i + 1 < s.length) {
            const next = s.charCodeAt(i + 1)
            if (next >= 0xdc00 && next <= 0xdfff) {
                bytes += 4
                i += 1
            } else {
                bytes += 3
            }
        } else {
            bytes += 3
        }
    }
    return bytes
}

function isSafeFilePath(rawPath) {
    const s = String(rawPath ?? "").trim()
    if (!s || /[\r\n\x00]/.test(s))
        return false
    return !/^(?:https?|javascript|data|ftp|smb):/i.test(s)
}

function _isValidPointerStr(p) {
    if (typeof p !== "string" || !p.startsWith("/") || p === "/" || p.length > 160 || /[\x00-\x1f\x7f]/.test(p))
        return false
    const rawParts = p.slice(1).split("/")
    if (rawParts.length === 0 || rawParts.length > MAX_POINTER_DEPTH)
        return false
    for (let i = 0; i < rawParts.length; ++i) {
        if (rawParts[i].length === 0)
            return false
        const tok = rawParts[i].replace(/~1/g, "/").replace(/~0/g, "~")
        if (!_isSafeObjectKey(tok))
            return false
    }
    return true
}

function hasMalformedPointer(binding) {
    if (isPlainObject(binding)) {
        const hasAt = Object.prototype.hasOwnProperty.call(binding, "@path")
        const hasPlain = Object.prototype.hasOwnProperty.call(binding, "path")
        if (!hasAt && !hasPlain)
            return false
        if (hasAt && !_isValidPointerStr(binding["@path"]))
            return true
        if (hasPlain && !_isValidPointerStr(binding.path))
            return true
        if (hasAt && hasPlain && binding["@path"] !== binding.path)
            return true
        return false
    }
    if (typeof binding === "string" && binding.startsWith("@path:")) {
        return !_isValidPointerStr(binding.slice(6).trim())
    }
    return false
}

function extractPointer(binding) {
    if (hasMalformedPointer(binding))
        return ""
    if (isPlainObject(binding)) {
        if (Object.prototype.hasOwnProperty.call(binding, "@path"))
            return _isValidPointerStr(binding["@path"]) ? binding["@path"] : ""
        if (Object.prototype.hasOwnProperty.call(binding, "path"))
            return _isValidPointerStr(binding.path) ? binding.path : ""
    }
    if (typeof binding === "string" && binding.startsWith("@path:")) {
        const p = binding.slice(6).trim()
        if (_isValidPointerStr(p))
            return p
    }
    return ""
}

function extractFunctionCall(action) {
    if (!isPlainObject(action))
        return null
    let fc = null
    if (action.functionCall !== undefined) {
        if (action["@call"] !== undefined || action.call !== undefined)
            return null
        fc = action.functionCall
    } else if (action["@call"] !== undefined) {
        if (action.call !== undefined && action["@call"] !== action.call)
            return null
        fc = (typeof action["@call"] === "string") ? action : action["@call"]
    } else if (typeof action.call === "string") {
        fc = action
    }
    if (!isPlainObject(fc))
        return null
    const hasAt = Object.prototype.hasOwnProperty.call(fc, "@call")
    const hasPlain = Object.prototype.hasOwnProperty.call(fc, "call")
    if (hasAt && typeof fc["@call"] !== "string")
        return null
    if (hasPlain && typeof fc.call !== "string")
        return null
    if (hasAt && hasPlain && fc["@call"].trim() !== fc.call.trim())
        return null
    let callName = ""
    if (hasAt)
        callName = fc["@call"].trim()
    else if (hasPlain)
        callName = fc.call.trim()
    if (!callName)
        return null
    return { call: callName, args: isPlainObject(fc.args) ? fc.args : ({}) }
}

function _resolveArgValuesForScopes(rawArg, dataModel, scopeList) {
    if (hasMalformedPointer(rawArg))
        return null
    const ptr = extractPointer(rawArg)
    if (!ptr)
        return [rawArg]
    const scopes = (Array.isArray(scopeList) && scopeList.length > 0) ? scopeList : [null]
    const values = []
    for (let i = 0; i < scopes.length; ++i) {
        const v = getByPath(dataModel, ptr, scopes[i])
        if (v !== undefined && v !== null)
            values.push(v)
    }
    return values
}

function validateActionSpec(action, opts, dataModel, scopeList) {
    if (!isPlainObject(action))
        return false
    const untrustedGen = Boolean(opts && opts.untrustedGenerative)
    const tainted = Boolean(opts && opts.tainted)
    const checkPointerArgs = Boolean(opts && opts._checkPointerArgs)
    const emptyTemplateScope = Boolean(opts && opts._emptyTemplateScope)
    const hasFcKey = (action.functionCall !== undefined || action["@call"] !== undefined || action.call !== undefined)
    const hasEventKey = (action.event !== undefined)
    if (hasFcKey && hasEventKey)
        return false
    if (hasFcKey) {
        const fc = extractFunctionCall(action)
        if (!fc || !_hasOwn(ALLOWED_FUNCTION_CALLS, fc.call))
            return false
        if (tainted)
            return false
        if (untrustedGen && _hasOwn(PRIVILEGED_FUNCTION_CALLS, fc.call))
            return false
        const dm = isPlainObject(dataModel) ? dataModel : {}
        const args = fc.args || {}
        const argKeys = Object.keys(args)
        for (let i = 0; i < argKeys.length; ++i) {
            if (hasMalformedPointer(args[argKeys[i]]))
                return false
        }
        if (fc.call === "openUrl") {
            const urlPtr = extractPointer(args.url)
            if (urlPtr) {
                if (checkPointerArgs) {
                    const vals = _resolveArgValuesForScopes(args.url, dm, scopeList)
                    if (!vals || (vals.length === 0 && !emptyTemplateScope) || !vals.every(v => isSafeHttpUrl(v)))
                        return false
                }
            } else if (!isSafeHttpUrl(args.url)) {
                return false
            }
        } else if (fc.call === "clientAction") {
            const actPtr = extractPointer(args.action)
            if (actPtr) {
                if (checkPointerArgs) {
                    const vals = _resolveArgValuesForScopes(args.action, dm, scopeList)
                    if (!vals || (vals.length === 0 && !emptyTemplateScope) || !vals.every(v => isSafeClientAction(v)))
                        return false
                }
            } else if (!isSafeClientAction(args.action)) {
                return false
            }
        } else if (fc.call === "openFile") {
            const pPtr = extractPointer(args.path)
            if (pPtr) {
                if (checkPointerArgs) {
                    const vals = _resolveArgValuesForScopes(args.path, dm, scopeList)
                    if (!vals || (vals.length === 0 && !emptyTemplateScope) || !vals.every(v => isSafeFilePath(v)))
                        return false
                }
            } else if (!isSafeFilePath(args.path)) {
                return false
            }
        } else if (fc.call === "copyText") {
            const txtPtr = extractPointer(args.text)
            if (!txtPtr && !String(args.text ?? "").trim())
                return false
        }
        return true
    }
    if (isPlainObject(action.event)) {
        const evName = String(action.event.name ?? "").trim()
        if (!_hasOwn(ALLOWED_SERVER_EVENTS, evName))
            return false
        if (untrustedGen && _hasOwn(PRIVILEGED_SERVER_EVENTS, evName))
            return false
        if (isPlainObject(action.event.context)) {
            const ctxKeys = Object.keys(action.event.context)
            for (let i = 0; i < ctxKeys.length; ++i) {
                const ck = ctxKeys[i]
                if (!_isSafeObjectKey(ck))
                    return false
                if (hasMalformedPointer(action.event.context[ck]))
                    return false
                if (tainted && _stripCtrl(ck).trim().toLowerCase() === "prompt")
                    return false
            }
        }
        if (tainted && !_hasOwn(TAINTED_ALLOWED_SERVER_EVENTS, evName))
            return false
        return true
    }
    return false
}

function isPlainObject(v) {
    return v !== null && typeof v === "object" && !Array.isArray(v)
}

function deepClone(v) {
    if (v === null || v === undefined || typeof v !== "object")
        return v
    try {
        return JSON.parse(JSON.stringify(v))
    } catch (e) {
        return v
    }
}

function safeIconName(raw) {
    let s = String(raw ?? "").trim()
    if (!s)
        return "info"
    s = s.replace(/([a-z0-9])([A-Z])/g, "$1_$2").replace(/[-\s]+/g, "_").toLowerCase().replace(/[^a-z0-9_]/g, "").slice(0, 48)
    return _ICON_NAME_RE.test(s) ? s : "info"
}

function isLocalImageSource(src) {
    const s = String(src ?? "").trim()
    if (!s || /[\r\n\x00]/.test(s))
        return false
    const low = s.toLowerCase()
    if (low.startsWith("http://") || low.startsWith("https://") || low.startsWith("data:") || low.startsWith("javascript:") || low.startsWith("ftp://"))
        return false
    return low.startsWith("file:///") || s.startsWith("/")
}

function toLocalImageUrl(src) {
    if (!isLocalImageSource(src))
        return ""
    const s = String(src).trim()
    return s.startsWith("/") ? ("file://" + s) : s
}

function isSafeHttpUrl(url) {
    return _HTTP_URL_RE.test(String(url ?? "").trim())
}

function isSafeClientAction(actionName) {
    const k = String(actionName ?? "").trim().toLowerCase()
    return _hasOwn(SAFE_INSTANT_CLIENT_ACTIONS, k)
}

function decodePointer(path) {
    const p = String(path ?? "")
    if (!p || p === "/")
        return []
    const stripped = p.startsWith("/") ? p.slice(1) : p
    return stripped.split("/").map(t => t.replace(/~1/g, "/").replace(/~0/g, "~"))
}

function getByPath(dataModel, path, scopeData) {
    const p = String(path ?? "")
    if (!p)
        return undefined
    if (p !== "/" && !_isValidPointerStr(p))
        return undefined
    const tokens = decodePointer(p)
    for (let i = 0; i < tokens.length; ++i) {
        if (!_isSafeObjectKey(tokens[i]))
            return undefined
    }
    // In ChildList template scopes, look up relative path inside scopeData first before falling back to root dataModel.
    if (isPlainObject(scopeData) || Array.isArray(scopeData)) {
        const scoped = _lookupTokens(scopeData, tokens)
        if (scoped !== undefined)
            return scoped
    }
    return _lookupTokens(dataModel, tokens)
}

function _lookupTokens(rootObj, tokens) {
    let cur = rootObj
    for (let i = 0; i < tokens.length; ++i) {
        const tok = tokens[i]
        if (!_isSafeObjectKey(tok))
            return undefined
        if (Array.isArray(cur)) {
            const idx = Number(tok)
            if (Number.isInteger(idx) && idx >= 0 && idx < cur.length)
                cur = cur[idx]
            else
                return undefined
        } else if (isPlainObject(cur) && Object.prototype.hasOwnProperty.call(cur, tok)) {
            cur = cur[tok]
        } else {
            return undefined
        }
    }
    return cur
}

// A2UI v1.0 `updateDataModel`: setting value === null DELETES the target key!
function setByPath(dataModel, path, value) {
    const out = isPlainObject(dataModel) ? deepClone(dataModel) : {}
    const p = String(path ?? "")
    if (p === "/") {
        if (value === null)
            return {}
        return isPlainObject(value) ? _sanitizeDataModelValue(value, 0) : out
    }
    if (!_isValidPointerStr(p))
        return out
    const tokens = decodePointer(p)
    for (let i = 0; i < tokens.length; ++i) {
        if (!_isSafeObjectKey(tokens[i]) || _SECRET_FIELD_RE.test(_stripCtrl(tokens[i])))
            return out
    }
    if (tokens.length === 0)
        return out
    let parent = out
    for (let i = 0; i < tokens.length - 1; ++i) {
        const tok = tokens[i]
        if (Array.isArray(parent)) {
            const idx = Number(tok)
            if (!Number.isInteger(idx) || idx < 0 || idx >= parent.length)
                return out
            parent = parent[idx]
        } else if (isPlainObject(parent)) {
            const existing = _hasOwn(parent, tok) ? parent[tok] : undefined
            if (!isPlainObject(existing) && !Array.isArray(existing))
                parent[tok] = {}
            parent = parent[tok]
        } else {
            return out
        }
    }
    const leaf = tokens[tokens.length - 1]
    if (Array.isArray(parent)) {
        const idx = Number(leaf)
        if (Number.isInteger(idx) && idx >= 0 && idx < parent.length) {
            if (value === null)
                parent.splice(idx, 1)
            else
                parent[idx] = _sanitizeDataModelValue(value, 1)
        }
    } else if (isPlainObject(parent)) {
        if (value === null)
            delete parent[leaf]
        else
            parent[leaf] = _sanitizeDataModelValue(value, 1)
    }
    return out
}

function applyPatches(dataModel, patches) {
    let cur = isPlainObject(dataModel) ? deepClone(dataModel) : {}
    if (!Array.isArray(patches))
        return cur
    for (let i = 0; i < patches.length; ++i) {
        const item = patches[i]
        const ptr = extractPointer(item)
        if (ptr) {
            cur = setByPath(cur, ptr, item.value === undefined ? null : item.value)
        }
    }
    return cur
}

function resolveValue(binding, dataModel, scopeData, fallback) {
    if (binding === undefined || binding === null)
        return fallback
    const ptr = extractPointer(binding)
    if (ptr) {
        const val = getByPath(dataModel, ptr, scopeData)
        return val !== undefined ? val : fallback
    }
    if (typeof binding === "string")
        return binding
    if (isPlainObject(binding)) {
        if (binding.literalString !== undefined)
            return String(binding.literalString)
        if (binding.literalNumber !== undefined)
            return Number(binding.literalNumber)
        if (binding.literalBoolean !== undefined)
            return Boolean(binding.literalBoolean)
    }
    return binding
}

function resolveAbsolutePath(bindingPath, scopePath) {
    const p = String(bindingPath ?? "")
    if (!p)
        return ""
    if (scopePath && scopePath.startsWith("/") && !p.startsWith(scopePath + "/") && p !== scopePath) {
        // Combine item-relative pointer like "/done" with scopePath like "/steps/0" -> "/steps/0/done"
        const suffix = p.startsWith("/") ? p : ("/" + p)
        return scopePath + suffix
    }
    return p
}

function resolveContext(ctxSpec, dataModel, scopeData) {
    const out = Object.create(null)
    if (!isPlainObject(ctxSpec))
        return out
    const keys = Object.keys(ctxSpec)
    for (let i = 0; i < keys.length; ++i) {
        const k = keys[i]
        if (!_isSafeObjectKey(k))
            continue
        const raw = ctxSpec[k]
        const ptr = extractPointer(raw)
        if (ptr)
            out[k] = getByPath(dataModel, ptr, scopeData)
        else
            out[k] = raw
    }
    return out
}

function buildComponentMap(components) {
    const map = Object.create(null)
    if (!Array.isArray(components))
        return map
    for (let i = 0; i < components.length; ++i) {
        const c = components[i]
        if (isPlainObject(c) && typeof c.id === "string" && _isSafeObjectKey(c.id))
            map[c.id] = c
    }
    return map
}

function surfaceMetaFlag(surface, key) {
    const meta = surface?.createSurface?.surfaceMeta
    return isPlainObject(meta) ? Boolean(meta[key]) : false
}

function isInteractiveSurface(surface) {
    const cs = surface?.createSurface
    if (!isPlainObject(cs))
        return false
    if (cs.surfaceMeta && cs.surfaceMeta.interactive === true)
        return true
    const comps = Array.isArray(cs.components) ? cs.components : []
    for (let i = 0; i < comps.length; ++i) {
        const c = comps[i]
        if (!isPlainObject(c))
            continue
        if (c.component === "ChoicePicker" || c.component === "CheckBox" || c.component === "TextField" || c.component === "Slider")
            return true
        if (c.component === "Button" && c.action?.event?.name === "confirm_action")
            return true
    }
    return false
}

function _sanitizeDataModelValue(val, depth) {
    if (depth > 6)
        return null
    if (val === null || val === undefined || typeof val === "boolean")
        return val
    if (typeof val === "number")
        return Number.isFinite(val) ? val : null
    if (typeof val === "string")
        return val.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "").slice(0, 1200)
    if (Array.isArray(val))
        return val.slice(0, 32).map(item => _sanitizeDataModelValue(item, depth + 1))
    if (isPlainObject(val)) {
        const out = {}
        const keys = Object.keys(val).slice(0, 32)
        for (let i = 0; i < keys.length; ++i) {
            const ks = String(keys[i] ?? "").replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "").trim().slice(0, 64)
            if (!_isSafeObjectKey(ks) || _SECRET_FIELD_RE.test(ks))
                continue
            out[ks] = _sanitizeDataModelValue(val[keys[i]], depth + 1)
        }
        return out
    }
    return String(val).slice(0, 240)
}

function ensureOutcomeComponent(surface) {
    if (!isPlainObject(surface) || !isPlainObject(surface.createSurface))
        return surface
    const cs = surface.createSurface
    const comps = Array.isArray(cs.components) ? cs.components : null
    if (!comps || comps.length >= MAX_COMPONENTS)
        return surface
    for (let i = 0; i < comps.length; ++i) {
        const c = comps[i]
        if (!isPlainObject(c))
            continue
        if (c.id === "outcome_txt" || extractPointer(c.text) === "/outcomeSummary")
            return surface
    }
    let bodyCol = null
    for (let i = 0; i < comps.length; ++i) {
        const c = comps[i]
        if (isPlainObject(c) && c.id === "body_col" && c.component === "Column" && Array.isArray(c.children)) {
            bodyCol = c
            break
        }
    }
    if (!bodyCol)
        return surface
    comps.push({
        id: "outcome_txt",
        component: "Text",
        variant: "caption",
        tone: "default",
        text: { path: "/outcomeSummary" },
        visible: { path: "/outcomeSummary" },
        maxLines: 3
    })
    let insertIdx = bodyCol.children.indexOf("actions_row")
    if (insertIdx < 0)
        insertIdx = bodyCol.children.indexOf("act_row")
    if (insertIdx < 0) {
        const stIdx = bodyCol.children.indexOf("status_txt")
        const stAlt = bodyCol.children.indexOf("st_txt")
        if (stIdx >= 0)
            insertIdx = stIdx + 1
        else if (stAlt >= 0)
            insertIdx = stAlt + 1
        else
            insertIdx = bodyCol.children.length
    }
    bodyCol.children.splice(insertIdx, 0, "outcome_txt")
    return surface
}

function validateSurface(raw, opts) {
    if (!isPlainObject(raw) || raw.version !== A2UI_VERSION || !isPlainObject(raw.createSurface))
        return null
    const cs = raw.createSurface
    const sid = String(cs.surfaceId ?? "").trim()
    if (!sid || !/^[A-Za-z0-9_.:-]{1,80}$/.test(sid))
        return null
    const comps = cs.components
    if (!Array.isArray(comps) || comps.length === 0 || comps.length > MAX_COMPONENTS)
        return null
    let serialized = ""
    try {
        serialized = JSON.stringify(raw)
    } catch (e) {
        return null
    }
    if (utf8ByteLength(serialized) > MAX_SURFACE_BYTES)
        return null

    const rawDm = isPlainObject(cs.dataModel) ? cs.dataModel : {}
    const dm = _sanitizeDataModelValue(rawDm, 0)
    const map = Object.create(null)
    for (let i = 0; i < comps.length; ++i) {
        const c = comps[i]
        if (!isPlainObject(c) || typeof c.id !== "string" || !_isSafeObjectKey(c.id) || _hasOwn(map, c.id))
            return null
        const type = String(c.component ?? "")
        if (_hasOwn(FORBIDDEN_COMPONENTS, type) || !_hasOwn(ALLOWED_COMPONENTS, type))
            return null
        if (c.visible !== undefined && typeof c.visible !== "boolean" && (hasMalformedPointer(c.visible) || !extractPointer(c.visible)))
            return null
        if (type === "Card" && (typeof c.child !== "string" || !c.child.trim()))
            return null
        if (type === "List" && !Array.isArray(c.children) && !isPlainObject(c.children))
            return null
        if ((type === "Column" || type === "Row" || type === "List") && isPlainObject(c.children)) {
            if (typeof c.children.componentId !== "string" || !c.children.componentId || hasMalformedPointer(c.children) || !extractPointer(c.children))
                return null
        }
        if (type === "ChoicePicker") {
            if (!Array.isArray(c.options) || c.options.length === 0)
                return null
            for (let oi = 0; oi < c.options.length; ++oi) {
                const o = c.options[oi]
                if (isPlainObject(o) && (hasMalformedPointer(o.label) || hasMalformedPointer(o.value)))
                    return null
            }
            if (hasMalformedPointer(c.value) || hasMalformedPointer(c.label))
                return null
        }
        if (type === "CheckBox") {
            if (hasMalformedPointer(c.value) || hasMalformedPointer(c.label))
                return null
        }
        if (type === "Slider") {
            if (hasMalformedPointer(c.value) || hasMalformedPointer(c.label))
                return null
            const _parseSliderNum = (raw, fallback) => {
                if (raw === undefined) return fallback
                if (raw === null || typeof raw === "boolean" || typeof raw === "object") return NaN
                if (typeof raw === "string" && raw.trim() === "") return NaN
                return Number(raw)
            }
            const lo = _parseSliderNum(c.min, 0)
            const hi = _parseSliderNum(c.max, 100)
            const step = _parseSliderNum(c.step, 1)
            if (!Number.isFinite(lo) || !Number.isFinite(hi) || !Number.isFinite(step) || hi <= lo || step <= 0)
                return null
        }
        if (type === "Tabs") {
            if (!Array.isArray(c.tabs) || c.tabs.length === 0)
                return null
            for (let ti = 0; ti < c.tabs.length; ++ti) {
                const tab = c.tabs[ti]
                if (isPlainObject(tab) && hasMalformedPointer(tab.label))
                    return null
            }
        }
        if (type === "Text" && hasMalformedPointer(c.text))
            return null
        if (type === "Icon" && hasMalformedPointer(c.name))
            return null
        if (type === "Button") {
            if (hasMalformedPointer(c.label) || hasMalformedPointer(c.disabled) || hasMalformedPointer(c.icon))
                return null
            if (!validateActionSpec(c.action, opts, dm, [null]))
                return null
        } else if (c.action !== undefined && c.action !== null) {
            if (!validateActionSpec(c.action, opts, dm, [null]))
                return null
        }
        if (type === "TextField") {
            if (c.obscured === true || hasMalformedPointer(c.value) || hasMalformedPointer(c.label) || hasMalformedPointer(c.placeholder))
                return null
            const valPtr = extractPointer(c.value)
            const lblPtr = extractPointer(c.label)
            const phPtr = extractPointer(c.placeholder)
            const lblSig = lblPtr || (typeof c.label === "string" ? c.label : (isPlainObject(c.label) ? JSON.stringify(c.label) : ""))
            const phSig = phPtr || (typeof c.placeholder === "string" ? c.placeholder : (isPlainObject(c.placeholder) ? JSON.stringify(c.placeholder) : ""))
            const sig = [c.id, lblSig, phSig, c.inputType, c.variant, valPtr, typeof c.value === "string" ? c.value : ""].join(" ")
            const inType = String(c.inputType ?? c.variant ?? "").trim().toLowerCase()
            if (_SECRET_FIELD_RE.test(_stripCtrl(sig)) || inType === "password" || inType === "obscured" || inType === "secret")
                return null
        }
        if (type === "Image") {
            const rawImg = c.url !== undefined ? c.url : c.source
            if (hasMalformedPointer(rawImg))
                return null
            const imgPtr = extractPointer(rawImg)
            if (imgPtr) {
                const boundVal = getByPath(dm, imgPtr, null)
                if (boundVal !== undefined && boundVal !== null && !isLocalImageSource(String(boundVal)))
                    return null
            } else if (!isLocalImageSource(String(rawImg ?? ""))) {
                return null
            }
        }
        map[c.id] = c
    }
    if (!map["root"])
        return null

    function _checkNodeInScopes(c, scopes, emptyTemplateScope) {
        const effScopes = (Array.isArray(scopes) && scopes.length > 0) ? scopes : [null]
        if (c.component === "Image") {
            const rawImg = c.url !== undefined ? c.url : c.source
            const imgPtr = extractPointer(rawImg)
            if (imgPtr) {
                for (let s = 0; s < effScopes.length; ++s) {
                    const boundVal = getByPath(dm, imgPtr, effScopes[s])
                    if (boundVal !== undefined && boundVal !== null && !isLocalImageSource(String(boundVal)))
                        return false
                }
            }
        } else if (c.component === "TextField") {
            const lblPtr = extractPointer(c.label)
            const phPtr = extractPointer(c.placeholder)
            for (let s = 0; s < effScopes.length; ++s) {
                if (lblPtr) {
                    const rv = getByPath(dm, lblPtr, effScopes[s])
                    if (rv !== undefined && rv !== null && _SECRET_FIELD_RE.test(String(rv)))
                        return false
                }
                if (phPtr) {
                    const rv = getByPath(dm, phPtr, effScopes[s])
                    if (rv !== undefined && rv !== null && _SECRET_FIELD_RE.test(String(rv)))
                        return false
                }
            }
        }
        if (c.action !== undefined && c.action !== null) {
            const scopedOpts = Object.assign({}, opts || ({}), {
                _checkPointerArgs: true,
                _emptyTemplateScope: Boolean(emptyTemplateScope)
            })
            if (!validateActionSpec(c.action, scopedOpts, dm, effScopes))
                return false
        }
        return true
    }

    function walk(cid, stack, depth, scopes, emptyTemplateScope) {
        if (depth > MAX_DEPTH || stack[cid] || !_hasOwn(map, cid))
            return false
        stack[cid] = true
        const c = map[cid]
        if (!_checkNodeInScopes(c, scopes, emptyTemplateScope)) {
            delete stack[cid]
            return false
        }
        const nextEntries = []
        if (typeof c.child === "string" && c.child)
            nextEntries.push({ id: c.child, scopes: scopes, emptyTpl: emptyTemplateScope })
        if (Array.isArray(c.children)) {
            for (let i = 0; i < c.children.length; ++i)
                if (typeof c.children[i] === "string") nextEntries.push({ id: c.children[i], scopes: scopes, emptyTpl: emptyTemplateScope })
        } else if (isPlainObject(c.children) && typeof c.children.componentId === "string") {
            const tplPtr = extractPointer(c.children)
            const childScopes = []
            let sawExplicitEmptyArr = false
            const curScopes = (Array.isArray(scopes) && scopes.length > 0) ? scopes : [null]
            for (let s = 0; s < curScopes.length; ++s) {
                const arr = getByPath(dm, tplPtr, curScopes[s])
                if (Array.isArray(arr)) {
                    if (arr.length === 0)
                        sawExplicitEmptyArr = true
                    for (let k = 0; k < Math.min(arr.length, 32); ++k)
                        childScopes.push(arr[k])
                }
            }
            nextEntries.push({
                id: c.children.componentId,
                scopes: childScopes.length > 0 ? childScopes : scopes,
                emptyTpl: Boolean(emptyTemplateScope || (childScopes.length === 0 && sawExplicitEmptyArr))
            })
        }
        if (c.component === "Tabs" && Array.isArray(c.tabs)) {
            for (let i = 0; i < c.tabs.length; ++i)
                if (typeof c.tabs[i]?.child === "string") nextEntries.push({ id: c.tabs[i].child, scopes: scopes, emptyTpl: emptyTemplateScope })
        }
        for (let i = 0; i < nextEntries.length; ++i) {
            if (!walk(nextEntries[i].id, stack, depth + 1, nextEntries[i].scopes, nextEntries[i].emptyTpl))
                return false
        }
        delete stack[cid]
        return true
    }
    if (!walk("root", Object.create(null), 0, [null], false))
        return null

    const clean = deepClone(raw)
    clean.createSurface.sendDataModel = false
    clean.createSurface.dataModel = dm
    if (opts && opts.untrustedGenerative) {
        const prevMeta = isPlainObject(clean.createSurface.surfaceMeta) ? clean.createSurface.surfaceMeta : {}
        const safeMeta = { kind: "generative", sourceKind: "generative" }
        if (typeof prevMeta.interactive === "boolean")
            safeMeta.interactive = prevMeta.interactive
        if (opts.tainted)
            safeMeta.tainted = true
        clean.createSurface.surfaceMeta = safeMeta
    }
    // Normalize legacy Text heading variants (h1..h5) and strip secret keys from event.context
    for (let i = 0; i < clean.createSurface.components.length; ++i) {
        const comp = clean.createSurface.components[i]
        if (!isPlainObject(comp))
            continue
        if (comp.component === "Text") {
            const v = String(comp.variant ?? "body").trim().toLowerCase()
            if (/^h[1-5]$/.test(v)) {
                const lvl = Math.min(3, Number(v.slice(1)) || 2)
                if (typeof comp.text === "string" && !/^\s*#/.test(comp.text))
                    comp.text = "#".repeat(lvl) + " " + comp.text
                comp.variant = "body"
            } else if (v !== "body" && v !== "caption") {
                comp.variant = "body"
            }
        }
        if (isPlainObject(comp.action) && isPlainObject(comp.action.event) && isPlainObject(comp.action.event.context)) {
            const safeCtx = {}
            const ckeys = Object.keys(comp.action.event.context).slice(0, 16)
            for (let k = 0; k < ckeys.length; ++k) {
                const ck = String(ckeys[k] ?? "").trim().slice(0, 48)
                if (!_isSafeObjectKey(ck) || _SECRET_FIELD_RE.test(ck))
                    continue
                safeCtx[ck] = comp.action.event.context[ckeys[k]]
            }
            comp.action.event.context = safeCtx
        }
    }
    return clean
}

function stripA2uiStreamTag(rawText) {
    const src = String(rawText ?? "")
    if (src.indexOf("<a2ui") === -1 && src.indexOf("<A2UI") === -1)
        return src
    return src
        .replace(/<a2ui\b[^>]*>[\s\S]*?(?:<\/a2ui\s*>|$)/gi, "")
        .replace(/<a2ui\b[^>]*$/i, "")
        .trim()
}

function extractA2uiFromText(rawText, opts) {
    const src = String(rawText ?? "")
    const m = /<a2ui\s*>\s*([\s\S]*?)\s*<\/a2ui\s*>/i.exec(src)
    if (!m)
        return { cleanText: src, uiSurface: null }
    const cleanText = src.replace(/<a2ui\s*>[\s\S]*?<\/a2ui\s*>/gi, "").trim()
    let inner = String(m[1] ?? "").trim().replace(/^```(?:json)?\s*|\s*```$/gi, "").trim()
    let parsed = null
    try {
        parsed = JSON.parse(inner)
    } catch (e) {
        parsed = null
    }
    const effectiveOpts = {
        untrustedGenerative: !(opts && opts.untrustedGenerative === false),
        tainted: Boolean(opts && opts.tainted)
    }
    const valid = parsed ? validateSurface(parsed, effectiveOpts) : null
    return { cleanText: cleanText, uiSurface: valid }
}

function escapeHtml(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
}

function formatMarkdownText(rawText, accentHex, codeBgHex) {
    const lines = String(rawText ?? "").replace(/\r\n?/g, "\n").split("\n")
    const out = []
    const cbg = String(codeBgHex || "rgba(255,255,255,0.08)")
    for (let i = 0; i < lines.length; ++i) {
        let ln = lines[i]
        let isHeading = false
        let hSize = ""
        const hm = /^(#{1,3})\s+(.+)$/.exec(ln.trim())
        if (hm) {
            isHeading = true
            ln = hm[2].trim()
            hSize = hm[1].length === 1 ? "font-size:1.08em;" : (hm[1].length === 2 ? "font-size:1.04em;" : "")
        }
        let esc = escapeHtml(ln)
        // Extract inline code spans into placeholders first so * / ** inside code are never turned into italics/bold
        const codeSpans = []
        esc = esc.replace(/`([^`]+)`/g, (_m, codeBody) => {
            const token = "\u0001CODE" + codeSpans.length + "\u0002"
            codeSpans.push("<span style='background-color:" + cbg + ";font-family:monospace;'>&nbsp;" + codeBody + "&nbsp;</span>")
            return token
        })
        esc = esc.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        esc = esc.replace(/\*([^*]+)\*/g, "<i>$1</i>")
        for (let k = 0; k < codeSpans.length; ++k)
            esc = esc.replace("\u0001CODE" + k + "\u0002", codeSpans[k])
        if (isHeading)
            out.push("<span style='font-weight:600;" + hSize + "'>" + esc + "</span>")
        else
            out.push(esc)
    }
    return out.join("<br/>")
}
