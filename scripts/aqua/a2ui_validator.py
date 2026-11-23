#!/usr/bin/env python3
"""Stateless A2UI v1.0 validator, sanitizer, and JSON-Pointer dataModel helper.

Safe for prewarming in `companion_daemon.py` (zero global mutable state).
Enforces:
- Single-message A2UI v1.0 `createSurface` with `"version": "1.0"` and `sendDataModel: false`
- Strict component allowlist (10 Basic Catalog + 4 Phase 1.5 safe components)
- Explicit rejection of Video, AudioPlayer, DateTimeInput, Modal, and password/sudo fields
- Strict `@call` allowlists (`openUrl` http/https only, `clientAction` SAFE_INSTANT_CLIENT_ACTIONS only)
- Local-only `Image` paths (no remote network fetch)
- Tree depth <= 6, component count <= 40, serialized payload <= 6144 bytes (6 KB)
"""
from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

import alis_local_bridge

A2UI_VERSION = "1.0"
DEFAULT_CATALOG_ID = "alis.companion.basic.v1"
MAX_SURFACE_BYTES = 6144
MAX_COMPONENTS = 40
MAX_TREE_DEPTH = 6
MAX_POINTER_DEPTH = 12
MAX_STRING_LEN = 2000

BASIC_CATALOG_COMPONENTS = frozenset({
    "Column",
    "Row",
    "Card",
    "Divider",
    "Text",
    "Icon",
    "Button",
    "List",
    "ChoicePicker",
    "CheckBox",
})

PHASE15_SAFE_COMPONENTS = frozenset({
    "TextField",
    "Slider",
    "Tabs",
    "Image",
})

ALLOWED_COMPONENTS = BASIC_CATALOG_COMPONENTS | PHASE15_SAFE_COMPONENTS

FORBIDDEN_COMPONENTS = frozenset({
    "Video",
    "AudioPlayer",
    "DateTimeInput",
    "Modal",
})

ALLOWED_TEXT_VARIANTS = frozenset({"body", "caption"})

ALLOWED_FUNCTION_CALLS = frozenset({
    "openUrl",
    "clientAction",
    "copyText",
    "openFile",
})

PRIVILEGED_FUNCTION_CALLS = frozenset({
    "openFile",
})

ALLOWED_SERVER_EVENTS = frozenset({
    "confirm_action",
    "send_prompt",
    "followup_prompt",
    "choice_submit",
    "checklist_toggle",
    "plan_toggle",
    "rollback_receipt",
    "run_tests",
    "open_editor",
    "save_pocket",
    "form_submit",
    "slider_change",
})

PRIVILEGED_SERVER_EVENTS = frozenset({
    "confirm_action",
    "rollback_receipt",
    "run_tests",
    "open_editor",
})

TAINTED_LOCAL_UI_EVENTS = frozenset({
    "choice_submit",
    "checklist_toggle",
    "plan_toggle",
    "form_submit",
    "slider_change",
})

_COMP_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ICON_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_HTTP_URL_RE = re.compile(r"^https?://[^\s\x00-\x1f\x7f]{3,1020}$", re.IGNORECASE)
_SECRET_FIELD_RE = re.compile(
    r"(?:password|passwd|sudo|secret|api[_-]?key|credential|passphrase|pin_code|mật[\s_-]?khẩu|mat[\s_-]?khau)",
    re.IGNORECASE,
)
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_A2UI_BLOCK_RE = re.compile(r"<a2ui\s*>\s*([\s\S]*?)\s*</a2ui\s*>", re.IGNORECASE)


def _clean_str(val: Any, limit: int = MAX_STRING_LEN) -> str:
    s = _CTRL_CHARS_RE.sub("", str(val if val is not None else ""))
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s[:limit]


def _to_snake_icon(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return "info"
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"[-\s]+", "_", s).lower()
    s = re.sub(r"[^a-z0-9_]", "", s)[:48]
    if _ICON_NAME_RE.match(s):
        return s
    return "info"


_FORBIDDEN_POINTER_SEGMENTS = frozenset({"__proto__", "constructor", "prototype"})


def _parse_finite_float(val: Any, default: float) -> tuple[float, bool]:
    if val is None:
        return default, True
    if isinstance(val, bool):
        return default, False
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default, False
    if not math.isfinite(num):
        return default, False
    return num, True


def _parse_finite_int(val: Any, lo: int, hi: int) -> int | None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    num = float(val)
    if not math.isfinite(num):
        return None
    return max(lo, min(hi, int(num)))


def _is_valid_pointer(ptr: Any) -> bool:
    if not isinstance(ptr, str):
        return False
    if not ptr.startswith("/") or ptr == "/" or len(ptr) > 160:
        return False
    if "\r" in ptr or "\n" in ptr or _CTRL_CHARS_RE.search(ptr):
        return False
    parts = ptr[1:].split("/")
    if not parts or len(parts) > MAX_POINTER_DEPTH:
        return False
    for part in parts:
        if not part:
            return False
        tok = part.replace("~1", "/").replace("~0", "~")
        if tok in _FORBIDDEN_POINTER_SEGMENTS:
            return False
    return True


def _has_malformed_pointer(val: Any) -> bool:
    if isinstance(val, dict):
        has_at = "@path" in val
        has_plain = "path" in val
        if not has_at and not has_plain:
            return False
        if has_at and not _is_valid_pointer(val.get("@path")):
            return True
        if has_plain and not _is_valid_pointer(val.get("path")):
            return True
        if has_at and has_plain and val.get("@path") != val.get("path"):
            return True
        return False
    if isinstance(val, str) and val.startswith("@path:"):
        return not _is_valid_pointer(val[6:].strip())
    return False


def _extract_pointer(val: Any) -> str:
    """Extract a validated JSON Pointer from `{"@path": "/..."}`, `{"path": "/..."}`, or `"@path:/..."`."""
    if _has_malformed_pointer(val):
        return ""
    if isinstance(val, dict):
        if "@path" in val:
            return str(val["@path"]) if _is_valid_pointer(val.get("@path")) else ""
        if "path" in val:
            return str(val["path"]) if _is_valid_pointer(val.get("path")) else ""
    if isinstance(val, str) and val.startswith("@path:"):
        p = val[6:].strip()
        if _is_valid_pointer(p):
            return p
    return ""


def _normalize_binding(val: Any, *, max_str: int = MAX_STRING_LEN) -> Any:
    """Normalize A2UI v1.0 dynamic value (literal, {path}, or '@path:/...')."""
    ptr = _extract_pointer(val)
    if ptr:
        return {"path": ptr}
    if isinstance(val, str):
        return _clean_str(val, max_str)
    if isinstance(val, dict):
        if "literalString" in val:
            return _clean_str(val.get("literalString"), max_str)
        if "literalNumber" in val and isinstance(val.get("literalNumber"), (int, float)) and not isinstance(val.get("literalNumber"), bool):
            num_val = float(val["literalNumber"])
            if math.isfinite(num_val):
                return val["literalNumber"]
        if "literalBoolean" in val and isinstance(val.get("literalBoolean"), bool):
            return val["literalBoolean"]
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        if math.isfinite(float(val)):
            return val
        return 0
    return _clean_str(val, max_str)


def _sanitize_json_value(val: Any, depth: int = 0) -> Any:
    if depth > 6:
        return None
    if val is None or isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val if math.isfinite(float(val)) else None
    if isinstance(val, str):
        return _clean_str(val, 1200)
    if isinstance(val, list):
        return [_sanitize_json_value(item, depth + 1) for item in val[:32]]
    if isinstance(val, dict):
        out: dict[str, Any] = {}
        for k, v in list(val.items())[:32]:
            ks = _clean_str(k, 64).strip()
            if not ks or ks in _FORBIDDEN_POINTER_SEGMENTS or _SECRET_FIELD_RE.search(ks):
                continue
            out[ks] = _sanitize_json_value(v, depth + 1)
        return out
    return _clean_str(val, 240)


def _is_safe_file_path(fpath: str) -> bool:
    s = _clean_str(fpath, 1024).strip()
    if not s or "\n" in s or "\x00" in s:
        return False
    return not s.lower().startswith(("http://", "https://", "javascript:", "data:", "ftp://", "smb://"))


def _validate_function_call(
    fc: Any,
    *,
    untrusted_generative: bool = False,
    tainted: bool = False,
) -> tuple[dict | None, str]:
    if not isinstance(fc, dict):
        return None, "invalid_function_call"
    has_at = "@call" in fc
    has_plain = "call" in fc
    if has_at and not isinstance(fc.get("@call"), str):
        return None, "invalid_function_call"
    if has_plain and not isinstance(fc.get("call"), str):
        return None, "invalid_function_call"
    if has_at and has_plain and fc["@call"].strip() != fc["call"].strip():
        return None, "conflicting_function_call"
    raw_call = fc.get("@call") if has_at else fc.get("call")
    call_name = str(raw_call or "").strip()
    if call_name not in ALLOWED_FUNCTION_CALLS:
        return None, f"disallowed_function_call:{call_name}"
    if tainted:
        return None, f"tainted_function_call_forbidden:{call_name}"
    if untrusted_generative and call_name in PRIVILEGED_FUNCTION_CALLS:
        return None, f"privileged_function_call_forbidden:{call_name}"
    raw_args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
    for _ak, _av in raw_args.items():
        if _has_malformed_pointer(_av):
            return None, "malformed_action_pointer"

    if call_name == "openUrl":
        raw_url = raw_args.get("url")
        url_ptr = _extract_pointer(raw_url)
        if url_ptr:
            return {"call": "openUrl", "args": {"url": {"path": url_ptr}}}, ""
        url = str(raw_url or "").strip()
        if not _HTTP_URL_RE.match(url):
            return None, "disallowed_url_scheme"
        return {"call": "openUrl", "args": {"url": url}}, ""

    if call_name == "clientAction":
        raw_act = raw_args.get("action")
        act_ptr = _extract_pointer(raw_act)
        if act_ptr:
            args_out: dict[str, Any] = {"action": {"path": act_ptr}}
        else:
            act = str(raw_act or "").strip().lower()
            if act not in alis_local_bridge.SAFE_INSTANT_CLIENT_ACTIONS:
                return None, f"disallowed_client_action:{act}"
            args_out = {"action": act}
        if raw_args.get("target") is not None:
            t_ptr = _extract_pointer(raw_args.get("target"))
            args_out["target"] = {"path": t_ptr} if t_ptr else _clean_str(raw_args.get("target"), 240).strip()
        if raw_args.get("value") is not None:
            v_ptr = _extract_pointer(raw_args.get("value"))
            args_out["value"] = {"path": v_ptr} if v_ptr else _clean_str(raw_args.get("value"), 240).strip()
        return {"call": "clientAction", "args": args_out}, ""

    if call_name == "copyText":
        raw_txt = raw_args.get("text")
        txt_ptr = _extract_pointer(raw_txt)
        if txt_ptr:
            return {"call": "copyText", "args": {"text": {"path": txt_ptr}}}, ""
        txt = _clean_str(raw_txt, 4000)
        if not txt.strip():
            return None, "empty_copy_text"
        return {"call": "copyText", "args": {"text": txt}}, ""

    if call_name == "openFile":
        raw_fp = raw_args.get("path")
        fp_ptr = _extract_pointer(raw_fp)
        if fp_ptr:
            return {"call": "openFile", "args": {"path": {"path": fp_ptr}}}, ""
        fpath = _clean_str(raw_fp, 1024).strip()
        if not _is_safe_file_path(fpath):
            return None, "invalid_file_path"
        return {"call": "openFile", "args": {"path": fpath}}, ""

    return None, "unknown_function_call"


def _validate_action(
    raw_action: Any,
    *,
    untrusted_generative: bool = False,
    tainted: bool = False,
) -> tuple[dict | None, str]:
    if not isinstance(raw_action, dict):
        return None, "invalid_action"
    has_fc = ("functionCall" in raw_action or "@call" in raw_action or "call" in raw_action)
    has_ev = ("event" in raw_action)
    if has_fc and has_ev:
        return None, "ambiguous_action_target"
    if has_fc:
        if "functionCall" in raw_action:
            if "@call" in raw_action or "call" in raw_action:
                return None, "conflicting_function_call"
            fc_raw = raw_action.get("functionCall")
        elif "@call" in raw_action:
            if "call" in raw_action and raw_action.get("@call") != raw_action.get("call"):
                return None, "conflicting_function_call"
            fc_raw = raw_action if isinstance(raw_action.get("@call"), str) else raw_action.get("@call")
        else:
            fc_raw = raw_action
        fc, err = _validate_function_call(
            fc_raw,
            untrusted_generative=untrusted_generative,
            tainted=tainted,
        )
        if err or not fc:
            return None, err or "invalid_function_call"
        return {"functionCall": fc}, ""
    if has_ev and isinstance(raw_action.get("event"), dict):
        ev = raw_action["event"]
        ev_name = str(ev.get("name") or "").strip()
        if ev_name not in ALLOWED_SERVER_EVENTS:
            return None, f"disallowed_event:{ev_name}"
        if untrusted_generative and ev_name in PRIVILEGED_SERVER_EVENTS:
            return None, f"privileged_event_forbidden:{ev_name}"
        raw_ctx = ev.get("context") if isinstance(ev.get("context"), dict) else {}
        if tainted and ev_name not in TAINTED_LOCAL_UI_EVENTS:
            return None, f"tainted_event_forbidden:{ev_name}"
        clean_ctx: dict[str, Any] = {}
        for k, v in list(raw_ctx.items())[:16]:
            if _has_malformed_pointer(v):
                return None, "malformed_event_pointer"
            ks = _clean_str(k, 48).strip()
            if tainted and ks.lower() == "prompt":
                return None, "tainted_prompt_context_forbidden"
            if not ks or ks in _FORBIDDEN_POINTER_SEGMENTS or _SECRET_FIELD_RE.search(ks):
                continue
            v_ptr = _extract_pointer(v)
            if v_ptr:
                clean_ctx[ks] = {"path": v_ptr}
            else:
                clean_ctx[ks] = _sanitize_json_value(v, depth=1)
        return {"event": {"name": ev_name, "context": clean_ctx}}, ""
    return None, "missing_action_target"


def _is_local_image_source(src: str) -> bool:
    s = str(src or "").strip()
    if not s or "\n" in s or "\x00" in s:
        return False
    low = s.lower()
    if low.startswith(("http://", "https://", "data:", "javascript:", "ftp://", "smb://")):
        return False
    if low.startswith("file:///"):
        return True
    return s.startswith("/")


def validate_and_sanitize_surface(
    raw_surface: Any,
    *,
    max_bytes: int = MAX_SURFACE_BYTES,
    untrusted_generative: bool = False,
    tainted: bool = False,
) -> tuple[dict | None, str]:
    """Validate and normalize an A2UI v1.0 single-message `createSurface` envelope."""
    if not isinstance(raw_surface, dict):
        return None, "surface_not_object"

    # Support either full envelope {"version": "1.0", "createSurface": {...}} or inner createSurface dict
    if "createSurface" in raw_surface and isinstance(raw_surface.get("createSurface"), dict):
        ver = str(raw_surface.get("version") or "").strip()
        if ver != A2UI_VERSION:
            return None, f"unsupported_version:{ver}"
        cs = raw_surface["createSurface"]
    else:
        return None, "missing_createSurface"

    surface_id = _clean_str(cs.get("surfaceId"), 80).strip()
    if not surface_id or not _COMP_ID_RE.match(surface_id):
        return None, "invalid_surface_id"

    catalog_id = _clean_str(cs.get("catalogId") or DEFAULT_CATALOG_ID, 80).strip() or DEFAULT_CATALOG_ID
    raw_comps = cs.get("components")
    if not isinstance(raw_comps, list) or not raw_comps:
        return None, "empty_components"
    if len(raw_comps) > MAX_COMPONENTS:
        return None, f"too_many_components:{len(raw_comps)}"

    sanitized_comps: list[dict[str, Any]] = []
    comp_map: dict[str, dict[str, Any]] = {}

    for item in raw_comps:
        if not isinstance(item, dict):
            return None, "component_not_object"
        cid = str(item.get("id") or "").strip()
        if not _COMP_ID_RE.match(cid) or cid in _FORBIDDEN_POINTER_SEGMENTS:
            return None, f"invalid_component_id:{cid}"
        if cid in comp_map:
            return None, f"duplicate_component_id:{cid}"

        ctype = str(item.get("component") or "").strip()
        if ctype in FORBIDDEN_COMPONENTS or ctype not in ALLOWED_COMPONENTS:
            return None, f"disallowed_component:{ctype}"

        node: dict[str, Any] = {"id": cid, "component": ctype}
        if "weight" in item and isinstance(item.get("weight"), (int, float)) and not isinstance(item.get("weight"), bool):
            w_val, w_ok = _parse_finite_float(item["weight"], 1.0)
            if w_ok:
                node["weight"] = max(0.0, min(12.0, w_val))
        if "visible" in item:
            if isinstance(item["visible"], bool):
                node["visible"] = item["visible"]
            else:
                if _has_malformed_pointer(item["visible"]):
                    return None, f"malformed_visible_pointer:{cid}"
                vis_ptr = _extract_pointer(item["visible"])
                if vis_ptr:
                    node["visible"] = {"path": vis_ptr}

        if ctype in ("Column", "Row"):
            children = item.get("children")
            if isinstance(children, list):
                c_ids = [str(x).strip() for x in children[:24] if _COMP_ID_RE.match(str(x or "").strip())]
                node["children"] = c_ids
            elif isinstance(children, dict):
                tpl_cid = str(children.get("componentId") or "").strip()
                tpl_path = _extract_pointer(children)
                if not _COMP_ID_RE.match(tpl_cid) or not tpl_path:
                    return None, f"invalid_child_template:{cid}"
                node["children"] = {"componentId": tpl_cid, "path": tpl_path}
            else:
                node["children"] = []
            if item.get("align") in ("start", "center", "end", "stretch"):
                node["align"] = item["align"]
            if item.get("justify") in ("start", "center", "end", "spaceBetween"):
                node["justify"] = item["justify"]
            gap_val = _parse_finite_int(item.get("gap"), 0, 24)
            if gap_val is not None:
                node["gap"] = gap_val
            if isinstance(item.get("wrap"), bool):
                node["wrap"] = item["wrap"]

        elif ctype == "Card":
            child_id = str(item.get("child") or "").strip()
            if not _COMP_ID_RE.match(child_id):
                return None, f"invalid_card_child:{cid}"
            node["child"] = child_id
            if item.get("tone") in ("subtle", "layer", "elevated", "accent", "warning", "error", "success"):
                node["tone"] = item["tone"]

        elif ctype == "Divider":
            if item.get("axis") in ("horizontal", "vertical"):
                node["axis"] = item["axis"]

        elif ctype == "Text":
            raw_var = str(item.get("variant") or "body").strip().lower()
            raw_txt = item.get("text")
            if _has_malformed_pointer(raw_txt):
                return None, f"malformed_text_pointer:{cid}"
            if raw_var in ("h1", "h2", "h3", "h4", "h5"):
                # Coerce legacy heading variants cleanly to A2UI v1.0 "body" with Markdown heading
                level = int(raw_var[1])
                prefix = "#" * min(3, level) + " "
                if isinstance(raw_txt, str) and not raw_txt.lstrip().startswith("#"):
                    raw_txt = prefix + raw_txt
                raw_var = "body"
            elif raw_var not in ALLOWED_TEXT_VARIANTS:
                raw_var = "body"
            node["variant"] = raw_var
            node["text"] = _normalize_binding(raw_txt, max_str=1600)
            if item.get("tone") in ("default", "muted", "accent", "success", "warning", "error"):
                node["tone"] = item["tone"]
            if item.get("align") in ("left", "center", "right"):
                node["align"] = item["align"]
            if isinstance(item.get("maxLines"), int) and not isinstance(item.get("maxLines"), bool) and 1 <= item["maxLines"] <= 20:
                node["maxLines"] = item["maxLines"]

        elif ctype == "Icon":
            raw_icon = item.get("name")
            if _has_malformed_pointer(raw_icon):
                return None, f"malformed_icon_pointer:{cid}"
            icon_ptr = _extract_pointer(raw_icon)
            if icon_ptr:
                node["name"] = {"path": icon_ptr}
            else:
                node["name"] = _to_snake_icon(raw_icon)
            size_val = _parse_finite_int(item.get("size"), 10, 32)
            if size_val is not None:
                node["size"] = size_val
            if item.get("tone") in ("default", "muted", "accent", "success", "warning", "error"):
                node["tone"] = item["tone"]

        elif ctype == "Button":
            child_id = str(item.get("child") or "").strip()
            if child_id:
                if not _COMP_ID_RE.match(child_id):
                    return None, f"invalid_button_child:{cid}"
                node["child"] = child_id
            else:
                if _has_malformed_pointer(item.get("label")):
                    return None, f"malformed_button_label_pointer:{cid}"
                lbl_ptr = _extract_pointer(item.get("label"))
                if lbl_ptr:
                    node["label"] = {"path": lbl_ptr}
                else:
                    lbl = _clean_str(item.get("label"), 80).strip()
                    if not lbl:
                        return None, f"missing_button_child_or_label:{cid}"
                    node["label"] = lbl
                if item.get("icon"):
                    if _has_malformed_pointer(item.get("icon")):
                        return None, f"malformed_button_icon_pointer:{cid}"
                    ico_ptr = _extract_pointer(item.get("icon"))
                    node["icon"] = {"path": ico_ptr} if ico_ptr else _to_snake_icon(item.get("icon"))
            act, err = _validate_action(
                item.get("action"),
                untrusted_generative=untrusted_generative,
                tainted=tainted,
            )
            if err or not act:
                return None, f"button_action:{err}"
            node["action"] = act
            if item.get("variant") in ("primary", "secondary", "ghost", "danger"):
                node["variant"] = item["variant"]
            if "disabled" in item:
                if isinstance(item["disabled"], bool):
                    node["disabled"] = item["disabled"]
                else:
                    if _has_malformed_pointer(item["disabled"]):
                        return None, f"malformed_disabled_pointer:{cid}"
                    dis_ptr = _extract_pointer(item["disabled"])
                    if dis_ptr:
                        node["disabled"] = {"path": dis_ptr}

        elif ctype == "List":
            children = item.get("children")
            if isinstance(children, list):
                c_ids = [str(x).strip() for x in children[:24] if _COMP_ID_RE.match(str(x or "").strip())]
                node["children"] = c_ids
            elif isinstance(children, dict):
                tpl_cid = str(children.get("componentId") or "").strip()
                tpl_path = _extract_pointer(children)
                if not _COMP_ID_RE.match(tpl_cid) or not tpl_path:
                    return None, f"invalid_list_template:{cid}"
                node["children"] = {"componentId": tpl_cid, "path": tpl_path}
            else:
                return None, f"invalid_list_children:{cid}"
            if item.get("direction") in ("vertical", "horizontal"):
                node["direction"] = item["direction"]
            list_gap = _parse_finite_int(item.get("gap"), 0, 20)
            if list_gap is not None:
                node["gap"] = list_gap

        elif ctype == "ChoicePicker":
            raw_opts = item.get("options")
            if not isinstance(raw_opts, list) or not raw_opts:
                return None, f"empty_choice_options:{cid}"
            opts_out: list[dict[str, Any]] = []
            for o in raw_opts[:8]:
                if isinstance(o, dict):
                    raw_olbl = o.get("label") if o.get("label") is not None else o.get("value")
                    if _has_malformed_pointer(raw_olbl):
                        return None, f"malformed_choice_option_pointer:{cid}"
                    olbl_ptr = _extract_pointer(raw_olbl)
                    lbl: Any = {"path": olbl_ptr} if olbl_ptr else _clean_str(raw_olbl, 80).strip()
                    val = _clean_str(o.get("value") if o.get("value") is not None else (olbl_ptr or lbl), 80).strip()
                    if lbl and val:
                        opts_out.append({"label": lbl, "value": val})
                elif isinstance(o, str) and o.strip():
                    s = _clean_str(o, 80).strip()
                    opts_out.append({"label": s, "value": s})
            if not opts_out:
                return None, f"empty_choice_options:{cid}"
            node["options"] = opts_out
            if _has_malformed_pointer(item.get("value")) or _has_malformed_pointer(item.get("label")):
                return None, f"malformed_choice_pointer:{cid}"
            ch_ptr = _extract_pointer(item.get("value"))
            if ch_ptr:
                node["value"] = {"path": ch_ptr}
            elif isinstance(item.get("value"), str):
                node["value"] = _normalize_binding(item["value"], max_str=160)
            else:
                node["value"] = {"path": f"/choices/{cid}"}
            if item.get("label"):
                lbl_ptr = _extract_pointer(item.get("label"))
                node["label"] = {"path": lbl_ptr} if lbl_ptr else _clean_str(item.get("label"), 120).strip()
            node["multiSelect"] = bool(item.get("multiSelect", False))
            if "action" in item and item.get("action") is not None:
                act, err = _validate_action(
                    item.get("action"),
                    untrusted_generative=untrusted_generative,
                    tainted=tainted,
                )
                if err or not act:
                    return None, f"choice_action:{err}"
                node["action"] = act

        elif ctype == "CheckBox":
            if _has_malformed_pointer(item.get("label")) or _has_malformed_pointer(item.get("value")):
                return None, f"malformed_checkbox_pointer:{cid}"
            node["label"] = _normalize_binding(item.get("label") or "", max_str=220)
            chk_ptr = _extract_pointer(item.get("value"))
            if chk_ptr:
                node["value"] = {"path": chk_ptr}
            elif isinstance(item.get("value"), bool):
                node["value"] = item["value"]
            else:
                node["value"] = {"path": f"/checks/{cid}"}
            if "action" in item and item.get("action") is not None:
                act, err = _validate_action(
                    item.get("action"),
                    untrusted_generative=untrusted_generative,
                    tainted=tainted,
                )
                if err or not act:
                    return None, f"checkbox_action:{err}"
                node["action"] = act

        elif ctype == "TextField":
            if (
                item.get("obscured") is True
                or _has_malformed_pointer(item.get("value"))
                or _has_malformed_pointer(item.get("label"))
                or _has_malformed_pointer(item.get("placeholder"))
            ):
                return None, "password_field_forbidden_in_a2ui"
            lbl_ptr = _extract_pointer(item.get("label"))
            ph_ptr = _extract_pointer(item.get("placeholder"))
            lbl_val: Any = {"path": lbl_ptr} if lbl_ptr else _clean_str(item.get("label") or "", 120).strip()
            ph_val: Any = {"path": ph_ptr} if ph_ptr else _clean_str(item.get("placeholder") or "", 120).strip()
            input_type = str(item.get("inputType") or item.get("variant") or "text").strip().lower()
            val_path = _extract_pointer(item.get("value"))
            raw_sig = _CTRL_CHARS_RE.sub(
                "",
                f"{cid} {lbl_ptr or lbl_val} {ph_ptr or ph_val} {val_path} {item.get('value') if isinstance(item.get('value'), str) else ''}",
            )
            if input_type in ("password", "obscured", "secret") or _SECRET_FIELD_RE.search(raw_sig):
                return None, "password_field_forbidden_in_a2ui"
            node["label"] = lbl_val
            node["placeholder"] = ph_val
            node["value"] = {"path": val_path} if val_path else {"path": f"/inputs/{cid}"}
            if isinstance(item.get("maxLength"), int) and not isinstance(item.get("maxLength"), bool):
                node["maxLength"] = max(1, min(400, item["maxLength"]))

        elif ctype == "Slider":
            if _has_malformed_pointer(item.get("label")) or _has_malformed_pointer(item.get("value")):
                return None, f"malformed_slider_pointer:{cid}"
            sl_lbl_ptr = _extract_pointer(item.get("label"))
            node["label"] = {"path": sl_lbl_ptr} if sl_lbl_ptr else _clean_str(item.get("label") or "", 120).strip()
            lo, lo_ok = _parse_finite_float(item.get("min", 0), 0.0)
            hi, hi_ok = _parse_finite_float(item.get("max", 100), 100.0)
            step, step_ok = _parse_finite_float(item.get("step", 1), 1.0)
            if not (lo_ok and hi_ok and step_ok):
                return None, f"invalid_slider_bounds:{cid}"
            if hi <= lo:
                hi = lo + 1.0
            if step <= 0:
                step = 1.0
            node["min"] = lo
            node["max"] = hi
            node["step"] = step
            sl_ptr = _extract_pointer(item.get("value"))
            if sl_ptr:
                node["value"] = {"path": sl_ptr}
            else:
                node["value"] = {"path": f"/sliders/{cid}"}
            if "action" in item and item.get("action") is not None:
                act, err = _validate_action(
                    item.get("action"),
                    untrusted_generative=untrusted_generative,
                    tainted=tainted,
                )
                if err or not act:
                    return None, f"slider_action:{err}"
                node["action"] = act

        elif ctype == "Tabs":
            raw_tabs = item.get("tabs")
            if not isinstance(raw_tabs, list) or not raw_tabs:
                return None, f"empty_tabs:{cid}"
            tabs_out: list[dict[str, Any]] = []
            for t in raw_tabs[:6]:
                if not isinstance(t, dict):
                    continue
                raw_tlbl = t.get("label")
                if _has_malformed_pointer(raw_tlbl):
                    return None, f"malformed_tab_label_pointer:{cid}"
                tlbl_ptr = _extract_pointer(raw_tlbl)
                tlbl: Any = {"path": tlbl_ptr} if tlbl_ptr else _clean_str(raw_tlbl, 48).strip()
                tchild = str(t.get("child") or "").strip()
                if tlbl and _COMP_ID_RE.match(tchild):
                    tabs_out.append({"label": tlbl, "child": tchild})
            if not tabs_out:
                return None, f"empty_tabs:{cid}"
            node["tabs"] = tabs_out

        elif ctype == "Image":
            raw_url = item.get("url") if "url" in item else item.get("source")
            if _has_malformed_pointer(raw_url):
                return None, "remote_or_invalid_image_forbidden"
            img_ptr = _extract_pointer(raw_url)
            if img_ptr:
                node["url"] = {"path": img_ptr}
            else:
                url_str = _clean_str(raw_url, 1024).strip()
                if not _is_local_image_source(url_str):
                    return None, "remote_or_invalid_image_forbidden"
                node["url"] = url_str
            if item.get("alt"):
                node["alt"] = _clean_str(item.get("alt"), 120).strip()
            mh_val = _parse_finite_int(item.get("maxHeight"), 48, 220)
            if mh_val is not None:
                node["maxHeight"] = mh_val

        sanitized_comps.append(node)
        comp_map[cid] = node

    if "root" not in comp_map:
        return None, "missing_root_component"

    # Sanitize dataModel before scoped tree verification
    raw_dm = cs.get("dataModel")
    clean_dm = _sanitize_json_value(raw_dm if isinstance(raw_dm, dict) else {}, depth=0)
    if not isinstance(clean_dm, dict):
        clean_dm = {}

    def _lookup_in_scope(ptr: str, scope: Any) -> Any:
        if isinstance(scope, (dict, list)):
            sv = get_pointer_value(scope, ptr)
            if sv is not None:
                return sv
        return get_pointer_value(clean_dm, ptr)

    def _check_scoped_node(comp: dict[str, Any], scopes: list[Any], empty_template: bool = False) -> str:
        eff_scopes = scopes if scopes else [None]
        ctype_n = comp.get("component")
        if ctype_n == "Image" and isinstance(comp.get("url"), dict):
            img_p = comp["url"]["path"]
            for sc in eff_scopes:
                bval = _lookup_in_scope(img_p, sc)
                if bval is not None and not _is_local_image_source(str(bval)):
                    return "remote_or_invalid_bound_image"
        elif ctype_n == "TextField":
            for fld in ("label", "placeholder"):
                fval = comp.get(fld)
                if isinstance(fval, dict) and isinstance(fval.get("path"), str):
                    for sc in eff_scopes:
                        rv = _lookup_in_scope(fval["path"], sc)
                        if rv is not None and _SECRET_FIELD_RE.search(_CTRL_CHARS_RE.sub("", str(rv))):
                            return "password_field_forbidden_in_a2ui"
        act = comp.get("action")
        if isinstance(act, dict) and isinstance(act.get("functionCall"), dict):
            fc = act["functionCall"]
            cname = str(fc.get("call") or "")
            fargs = fc.get("args") if isinstance(fc.get("args"), dict) else {}
            if cname == "openUrl" and isinstance(fargs.get("url"), dict):
                vals = [_lookup_in_scope(fargs["url"]["path"], sc) for sc in eff_scopes]
                vals = [v for v in vals if v is not None]
                if (not vals and not empty_template) or not all(_HTTP_URL_RE.match(str(v).strip()) for v in vals):
                    return "disallowed_url_scheme"
            elif cname == "clientAction" and isinstance(fargs.get("action"), dict):
                vals = [_lookup_in_scope(fargs["action"]["path"], sc) for sc in eff_scopes]
                vals = [v for v in vals if v is not None]
                if (not vals and not empty_template) or not all(str(v).strip().lower() in alis_local_bridge.SAFE_INSTANT_CLIENT_ACTIONS for v in vals):
                    return "disallowed_client_action"
            elif cname == "openFile" and isinstance(fargs.get("path"), dict):
                vals = [_lookup_in_scope(fargs["path"]["path"], sc) for sc in eff_scopes]
                vals = [v for v in vals if v is not None]
                if (not vals and not empty_template) or not all(_is_safe_file_path(str(v)) for v in vals):
                    return "invalid_file_path"
        return ""

    # Verify child references, acyclicity, max tree depth <= MAX_TREE_DEPTH, and scoped security
    def _walk(cid: str, stack: set[str], depth: int, scopes: list[Any], empty_template: bool = False) -> str:
        if depth > MAX_TREE_DEPTH:
            return f"max_depth_exceeded:{cid}"
        if cid in stack:
            return f"cycle_detected:{cid}"
        comp = comp_map.get(cid)
        if comp is None:
            return f"dangling_child:{cid}"
        sc_err = _check_scoped_node(comp, scopes, empty_template=empty_template)
        if sc_err:
            return sc_err
        stack.add(cid)
        next_entries: list[tuple[str, list[Any], bool]] = []
        if "child" in comp and isinstance(comp["child"], str):
            next_entries.append((comp["child"], scopes, empty_template))
        ch = comp.get("children")
        if isinstance(ch, list):
            for c_id in ch:
                if isinstance(c_id, str):
                    next_entries.append((c_id, scopes, empty_template))
        elif isinstance(ch, dict) and isinstance(ch.get("componentId"), str):
            tpl_ptr = str(ch.get("path") or "")
            child_scopes: list[Any] = []
            saw_explicit_empty_arr = False
            for sc in (scopes if scopes else [None]):
                arr = _lookup_in_scope(tpl_ptr, sc)
                if isinstance(arr, list):
                    if len(arr) == 0:
                        saw_explicit_empty_arr = True
                    child_scopes.extend(arr[:32])
            next_entries.append((
                ch["componentId"],
                child_scopes if child_scopes else scopes,
                bool(empty_template or (not child_scopes and saw_explicit_empty_arr)),
            ))
        if comp.get("component") == "Tabs":
            for t in comp.get("tabs") or []:
                if isinstance(t, dict) and isinstance(t.get("child"), str):
                    next_entries.append((t["child"], scopes, empty_template))
        for child_id, child_sc, child_empty in next_entries:
            err = _walk(child_id, stack, depth + 1, child_sc, empty_template=child_empty)
            if err:
                return err
        stack.remove(cid)
        return ""

    tree_err = _walk("root", set(), 0, [None], empty_template=False)
    if tree_err:
        return None, tree_err

    # Also verify root-level bound images on any non-template/unreached components
    for comp in sanitized_comps:
        if comp["component"] == "Image" and isinstance(comp.get("url"), dict):
            bound_val = get_pointer_value(clean_dm, comp["url"]["path"])
            if bound_val is not None and not _is_local_image_source(str(bound_val)):
                return None, "remote_or_invalid_bound_image"

    raw_meta = cs.get("surfaceMeta") if isinstance(cs.get("surfaceMeta"), dict) else {}
    clean_meta: dict[str, Any] = {}
    if untrusted_generative:
        clean_meta["kind"] = "generative"
        clean_meta["sourceKind"] = "generative"
        if isinstance(raw_meta.get("interactive"), bool):
            clean_meta["interactive"] = raw_meta["interactive"]
        if tainted:
            clean_meta["tainted"] = True
    elif raw_meta:
        for mk in ("kind", "confirmId", "receiptId", "sourceKind"):
            if raw_meta.get(mk):
                clean_meta[mk] = _clean_str(raw_meta[mk], 80).strip()
        for bk in ("interactive", "tainted", "dedupeMetaChips", "dedupeUndoChip", "dedupeCodeChips", "dedupeImagesRow"):
            if isinstance(raw_meta.get(bk), bool):
                clean_meta[bk] = raw_meta[bk]

    envelope: dict[str, Any] = {
        "version": A2UI_VERSION,
        "createSurface": {
            "surfaceId": surface_id,
            "catalogId": catalog_id,
            "sendDataModel": False,
            "components": sanitized_comps,
            "dataModel": clean_dm,
        },
    }
    if clean_meta:
        envelope["createSurface"]["surfaceMeta"] = clean_meta

    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        return None, f"surface_too_large:{len(encoded)}"

    return envelope, ""


def _decode_pointer_tokens(path: str) -> list[str]:
    if not path or path == "/":
        return []
    raw = path[1:] if path.startswith("/") else path
    return [tok.replace("~1", "/").replace("~0", "~") for tok in raw.split("/") if tok != ""]


def _decode_pointer_tokens_strict(path: str) -> tuple[list[str], bool]:
    if not isinstance(path, str) or not path:
        return [], False
    if path == "/":
        return [], True
    if not path.startswith("/") or len(path) > 160 or "\r" in path or "\n" in path or _CTRL_CHARS_RE.search(path):
        return [], False
    parts = path[1:].split("/")
    if not parts or len(parts) > MAX_POINTER_DEPTH:
        return [], False
    tokens: list[str] = []
    for part in parts:
        if not part:
            return [], False
        tok = part.replace("~1", "/").replace("~0", "~")
        if tok in _FORBIDDEN_POINTER_SEGMENTS or _SECRET_FIELD_RE.search(_CTRL_CHARS_RE.sub("", tok)):
            return [], False
        tokens.append(tok)
    return tokens, True


def get_pointer_value(data_model: Any, path: str) -> Any:
    if not path or path == "/":
        return data_model
    if not _is_valid_pointer(path):
        return None
    cur = data_model
    for tok in _decode_pointer_tokens(path):
        if isinstance(cur, dict) and tok in cur:
            cur = cur[tok]
        elif isinstance(cur, list) and tok.isdigit():
            idx = int(tok)
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        else:
            return None
    return cur


def apply_update_data_model(surface: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Apply an A2UI v1.0 `updateDataModel` patch (`value is None` deletes the key)."""
    out = copy.deepcopy(surface)
    cs = out.get("createSurface")
    if not isinstance(cs, dict):
        return out
    dm = cs.get("dataModel")
    if not isinstance(dm, dict):
        dm = {}
        cs["dataModel"] = dm

    tokens, valid = _decode_pointer_tokens_strict(path)
    if not valid:
        return out
    if not tokens:
        if value is None:
            cs["dataModel"] = {}
        elif isinstance(value, dict):
            cs["dataModel"] = _sanitize_json_value(value, depth=0) or {}
        return out

    parent: Any = dm
    for tok in tokens[:-1]:
        if isinstance(parent, dict):
            nxt = parent.get(tok)
            if not isinstance(nxt, (dict, list)):
                nxt = {}
                parent[tok] = nxt
            parent = nxt
        elif isinstance(parent, list) and tok.isdigit():
            idx = int(tok)
            if 0 <= idx < len(parent):
                parent = parent[idx]
            else:
                return out
        else:
            return out

    leaf = tokens[-1]
    if isinstance(parent, dict):
        if value is None:
            parent.pop(leaf, None)
        else:
            parent[leaf] = _sanitize_json_value(value, depth=1)
    elif isinstance(parent, list) and leaf.isdigit():
        idx = int(leaf)
        if 0 <= idx < len(parent):
            if value is None:
                parent.pop(idx)
            else:
                parent[idx] = _sanitize_json_value(value, depth=1)
    return out


def lock_confirmation_surface(
    surface: dict[str, Any],
    *,
    resolution: str = "locked",
    new_surface_id: str = "",
    outcome_summary: str = "",
) -> dict[str, Any]:
    """Return a copy with any confirmation actions locked/disabled (for resolution or duplicate_thread)."""
    out = copy.deepcopy(surface)
    cs = out.get("createSurface")
    if not isinstance(cs, dict):
        return out
    if new_surface_id and _COMP_ID_RE.match(new_surface_id):
        cs["surfaceId"] = new_surface_id
    meta = cs.get("surfaceMeta")
    is_confirm = isinstance(meta, dict) and (meta.get("kind") == "confirmation" or bool(meta.get("confirmId")))
    comps = cs.get("components") if isinstance(cs.get("components"), list) else []
    has_confirm_btn = any(
        isinstance(c, dict)
        and c.get("component") == "Button"
        and isinstance((c.get("action") or {}).get("event"), dict)
        and (c["action"]["event"].get("name") == "confirm_action")
        for c in comps
    )
    if is_confirm or has_confirm_btn:
        out = apply_update_data_model(out, "/resolved", True)
        out = apply_update_data_model(out, "/resolution", str(resolution or "locked"))
        status_label = {
            "confirmed": "Confirmed",
            "cancelled": "Cancelled",
            "expired": "Expired",
            "failed": "Failed",
            "locked": "Locked (archived copy)",
        }.get(str(resolution or "locked"), "Resolved")
        out = apply_update_data_model(out, "/statusLabel", status_label)
        summary_clean = _clean_str(outcome_summary, 280).strip()
        if summary_clean:
            out = apply_update_data_model(out, "/outcomeSummary", summary_clean)
            # Upgrade legacy confirmation cards lacking an outcome_txt component so the persisted summary renders on restart.
            out_comps = out["createSurface"].get("components")
            if isinstance(out_comps, list) and len(out_comps) < MAX_COMPONENTS:
                has_outcome_node = any(
                    isinstance(c, dict)
                    and (c.get("id") == "outcome_txt" or _extract_pointer(c.get("text")) == "/outcomeSummary")
                    for c in out_comps
                )
                if not has_outcome_node:
                    body_col = next(
                        (
                            c
                            for c in out_comps
                            if isinstance(c, dict)
                            and c.get("id") == "body_col"
                            and c.get("component") == "Column"
                            and isinstance(c.get("children"), list)
                        ),
                        None,
                    )
                    if body_col is not None:
                        out_comps.append(
                            {
                                "id": "outcome_txt",
                                "component": "Text",
                                "variant": "caption",
                                "tone": "default",
                                "text": {"path": "/outcomeSummary"},
                                "visible": {"path": "/outcomeSummary"},
                                "maxLines": 3,
                            }
                        )
                        ch_list = body_col["children"]
                        if "actions_row" in ch_list:
                            ch_list.insert(ch_list.index("actions_row"), "outcome_txt")
                        elif "act_row" in ch_list:
                            ch_list.insert(ch_list.index("act_row"), "outcome_txt")
                        elif "status_txt" in ch_list:
                            ch_list.insert(ch_list.index("status_txt") + 1, "outcome_txt")
                        else:
                            ch_list.append("outcome_txt")
        for c in out["createSurface"].get("components", []):
            if (
                isinstance(c, dict)
                and c.get("component") == "Button"
                and isinstance((c.get("action") or {}).get("event"), dict)
                and c["action"]["event"].get("name") == "confirm_action"
            ):
                c["disabled"] = True
        if isinstance(out["createSurface"].get("surfaceMeta"), dict):
            out["createSurface"]["surfaceMeta"]["interactive"] = False
    return out


def extract_a2ui_from_text(
    raw_text: str,
    *,
    untrusted_generative: bool = True,
    tainted: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    """Strip `<a2ui>...</a2ui>` from model text and validate the embedded surface if present."""
    src = str(raw_text or "")
    match = _A2UI_BLOCK_RE.search(src)
    if not match:
        return src, None
    clean_text = _A2UI_BLOCK_RE.sub("", src).strip()
    block = match.group(1).strip()
    block = re.sub(r"^```(?:json)?\s*|\s*```$", "", block, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(block)
    except Exception:
        return clean_text, None
    try:
        validated, _err = validate_and_sanitize_surface(
            parsed,
            untrusted_generative=untrusted_generative,
            tainted=tainted,
        )
    except Exception:
        return clean_text, None
    return clean_text, validated
