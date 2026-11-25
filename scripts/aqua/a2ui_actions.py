#!/usr/bin/env python3
"""Trusted A2UI v1.0 UI event router extracted from `agent_runtime.py`.

Handles confirmation cards, scheduled-job cancellations, plan/checklist/form
state patches, receipt rollbacks, Pocket saves, and follow-up prompts while
enforcing A2UI surface taint and generative-surface privilege boundaries.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any


def handle_a2ui_action(
    payload: dict,
    companion_root: Path,
    host_root: Path,
) -> dict:
    """Handle trusted UI events emitted by the Companion A2UI v1.0 QML renderer.

    Never trusts raw `dataModel` snapshots (`sendDataModel: false`): only accepts
    allowlisted event names and explicitly bound event `context` keys.
    """
    import agent_runtime as _rt

    req = payload if isinstance(payload, dict) else {}
    tctx = _rt._tctx()
    prev_live_gui = bool(getattr(tctx, "live_gui_control", False))
    prev_nexus_vault = str(getattr(tctx, "current_turn_nexus_vault", "") or "")
    if "liveGuiControlEnabled" in req:
        tctx.live_gui_control = bool(req.get("liveGuiControlEnabled") is True)
    if req.get("nexusVaultPath") is not None:
        tctx.current_turn_nexus_vault = str(req.get("nexusVaultPath") or "").strip()
    try:
        return _handle_a2ui_action_impl(req, companion_root, host_root, _rt)
    finally:
        tctx.live_gui_control = prev_live_gui
        tctx.current_turn_nexus_vault = prev_nexus_vault


def _handle_a2ui_action_impl(
    req: dict,
    companion_root: Path,
    host_root: Path,
    _rt: Any,
) -> dict:
    now_ms = int(req.get("nowMs") or int(time.time() * 1000))
    surface_id = str(req.get("surfaceId") or "").strip()[:80]
    event_name = str(req.get("eventName") or "").strip()
    ctx_in = req.get("context") if isinstance(req.get("context"), dict) else {}
    raw_lang = str(req.get("language") or "en").strip().lower()

    if event_name not in _rt.a2ui_validator.ALLOWED_SERVER_EVENTS:
        return {
            "ok": False,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "error": f"disallowed_event:{event_name}",
        }

    existing_surface_row = _rt.history_store.get_surface_by_id(surface_id) if surface_id else None
    existing_cs = (
        (existing_surface_row[1].get("createSurface") or {})
        if existing_surface_row and isinstance(existing_surface_row[1], dict)
        else {}
    )
    existing_meta = existing_cs.get("surfaceMeta") if isinstance(existing_cs.get("surfaceMeta"), dict) else {}
    surf_is_tainted = bool(existing_meta.get("tainted") is True)
    surf_is_generative = bool(
        existing_meta.get("kind") == "generative"
        or existing_meta.get("sourceKind") == "generative"
        or surface_id.startswith("surf_gen")
    )

    if raw_lang.startswith("vi"):
        lang = "vi"
    elif raw_lang == "auto" and existing_cs:
        sample_str = json.dumps(existing_cs.get("dataModel") or {}, ensure_ascii=False)
        lang = "vi" if re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", sample_str, re.I) else "en"
    else:
        lang = "en"

    # Server-side security & CaMeL taint boundary enforcement (defense-in-depth)
    if surf_is_generative and event_name in _rt.a2ui_validator.PRIVILEGED_SERVER_EVENTS:
        return {
            "ok": False,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "error": f"privileged_event_forbidden:{event_name}",
        }
    if surf_is_tainted and event_name not in _rt.a2ui_validator.TAINTED_LOCAL_UI_EVENTS:
        return {
            "ok": False,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "error": "tainted_surface_action_forbidden",
        }

    def _outcome_summary(raw_msg: str) -> str:
        return _rt.a2ui_validator._clean_str(raw_msg, 280).strip()

    # 1. Confirmation card actions (strictly matched against non-empty confirmId, trusted source="a2ui_confirm")
    if event_name == "confirm_action":
        confirm_id = str(ctx_in.get("confirmId") or req.get("confirmId") or "").strip()
        decision = "confirm" if str(ctx_in.get("decision") or "").strip().lower() == "confirm" else "cancel"
        session_ctx = _rt.load_session_context()

        def _persist_confirm_outcome(res_state: str, cid_to_lock: str, summary: str) -> str:
            clean_sum = _outcome_summary(summary)
            if surface_id:
                _rt.history_store.patch_surface_by_id(
                    surface_id,
                    lock_resolution=res_state,
                    outcome_summary=clean_sum,
                )
            if cid_to_lock:
                _rt.history_store.lock_confirm_surfaces(
                    cid_to_lock,
                    resolution=res_state,
                    outcome_summary=clean_sum,
                )
            return clean_sum

        def _failed_confirm_response(cid_to_lock: str, exc: Exception) -> dict:
            err_txt = str(exc)[:160]
            err_sum = _persist_confirm_outcome("failed", cid_to_lock, f"Error: {err_txt}")
            return {
                "ok": False,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "resolved": True,
                "resolution": "failed",
                "requiresConfirmation": False,
                "confirmId": "",
                "error": err_txt,
                "text": err_sum,
                "expression": "sad",
                "dataPatch": [
                    {"path": "/resolved", "value": True},
                    {"path": "/resolution", "value": "failed"},
                    {"path": "/statusLabel", "value": "Failed"},
                    {"path": "/outcomeSummary", "value": err_sum},
                ],
                "clientActions": [],
            }

        # Security: A2UI confirm_action MUST provide a non-empty confirmId matching the active pending request.
        if not confirm_id:
            missing_msg = _outcome_summary(
                "Thiếu mã xác nhận hợp lệ; thao tác không được thực hiện."
                if lang == "vi"
                else "Missing valid confirmation ID; no action was executed."
            )
            return {
                "ok": True,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "stale": True,
                "resolved": True,
                "resolution": "expired",
                "requiresConfirmation": False,
                "confirmId": "",
                "text": missing_msg,
                "dataPatch": [
                    {"path": "/resolved", "value": True},
                    {"path": "/resolution", "value": "expired"},
                    {"path": "/statusLabel", "value": "Expired"},
                    {"path": "/outcomeSummary", "value": missing_msg},
                ],
                "clientActions": [],
            }

        # Sudo operations can NEVER be confirmed from A2UI (only cancelled)
        if session_ctx.get("pendingSudoOp"):
            sudo_obj = session_ctx.get("pendingSudoOp") or {}
            sudo_cid = str(sudo_obj.get("confirmId") or session_ctx.get("pendingConfirmId") or "")
            if sudo_cid and confirm_id == sudo_cid:
                if decision == "confirm":
                    return {
                        "ok": True,
                        "op": "a2ui_action",
                        "eventName": event_name,
                        "surfaceId": surface_id,
                        "stale": False,
                        "requiresConfirmation": True,
                        "requiresSudoPassword": True,
                        "confirmId": sudo_cid,
                        "text": (
                            "Vui lòng nhập mật khẩu `sudo` trực tiếp vào ô chat."
                            if lang == "vi"
                            else "Please enter your `sudo` password directly in the chat composer."
                        ),
                        "dataPatch": [],
                        "clientActions": [],
                    }
                session_ctx["pendingSudoOp"] = None
                session_ctx["pendingConfirmId"] = ""
                _rt.save_session_context(session_ctx, now_ms=now_ms)
                _rt.resolve_pending_confirmation(confirm_id=sudo_cid, decision="cancel", source="a2ui_confirm", now_ms=now_ms)
                msg = "Đã huỷ thao tác hệ thống." if lang == "vi" else "Cancelled system path operation."
                summary = _persist_confirm_outcome("cancelled", sudo_cid, msg)
                return {
                    "ok": True,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "resolved": True,
                    "resolution": "cancelled",
                    "requiresConfirmation": False,
                    "requiresSudoPassword": False,
                    "confirmId": "",
                    "text": msg,
                    "spokenText": msg,
                    "expression": "idle",
                    "dataPatch": [
                        {"path": "/resolved", "value": True},
                        {"path": "/resolution", "value": "cancelled"},
                        {"path": "/statusLabel", "value": "Cancelled"},
                        {"path": "/outcomeSummary", "value": summary},
                    ],
                    "clientActions": [],
                }

        # Pending desktop/system power confirmation (session_power, bluetooth_disconnect, wifi_toggle off)
        if isinstance(session_ctx.get("pendingDesktopControl"), dict):
            pdc = dict(session_ctx.get("pendingDesktopControl") or {})
            pdc_cid = str(pdc.get("confirmId") or session_ctx.get("pendingConfirmId") or "")
            if pdc_cid and confirm_id == pdc_cid:
                session_ctx["pendingDesktopControl"] = None
                session_ctx["pendingConfirmId"] = ""
                _rt.save_session_context(session_ctx, now_ms=now_ms)
                res_state = "confirmed" if decision == "confirm" else "cancelled"
                if decision == "confirm":
                    p_act = str(pdc.get("action") or "")
                    p_tgt = str(pdc.get("target") or "")
                    p_val = str(pdc.get("value") or "")
                    try:
                        d_out = _rt.desktop_control.manage_desktop(action=p_act, target=p_tgt, value=p_val)
                    except Exception as exc:
                        return _failed_confirm_response(pdc_cid, exc)
                    if d_out.get("ok") is False or d_out.get("status") == "failed":
                        res_state = "failed"
                    msg = str(d_out.get("summary") or f"Executed {p_act}.")
                else:
                    msg = "Đã huỷ thao tác hệ thống." if lang == "vi" else "Cancelled pending system control action."
                summary = _persist_confirm_outcome(res_state, pdc_cid, msg)
                status_lbl = "Confirmed" if res_state == "confirmed" else ("Failed" if res_state == "failed" else "Cancelled")
                return {
                    "ok": res_state != "failed",
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "resolved": True,
                    "resolution": res_state,
                    "requiresConfirmation": False,
                    "confirmId": "",
                    "text": msg,
                    "spokenText": msg,
                    "expression": "happy" if res_state == "confirmed" else ("sad" if res_state == "failed" else "idle"),
                    "dataPatch": [
                        {"path": "/resolved", "value": True},
                        {"path": "/resolution", "value": res_state},
                        {"path": "/statusLabel", "value": status_lbl},
                        {"path": "/outcomeSummary", "value": summary},
                    ],
                    "clientActions": [],
                }

        # Pending client action (todo_add, quicknote_add, rag_note_save)
        if isinstance(session_ctx.get("pendingClientAction"), dict):
            pca = dict(session_ctx.get("pendingClientAction") or {})
            pca_cid = str(pca.get("confirmId") or session_ctx.get("pendingConfirmId") or "")
            if pca_cid and confirm_id == pca_cid:
                session_ctx["pendingClientAction"] = None
                session_ctx["pendingConfirmId"] = ""
                _rt.save_session_context(session_ctx, now_ms=now_ms)
                res_state = "confirmed" if decision == "confirm" else "cancelled"
                emitted_actions: list[dict] = []
                if decision == "confirm":
                    p_act = str(pca.get("action") or "")
                    p_tgt = str(pca.get("target") or "")
                    p_val = str(pca.get("value") or "")
                    if p_act == "rag_note_save":
                        try:
                            saved_note = _rt.nexus_rag.save_rag_note(
                                p_tgt or "Companion Note",
                                p_val or p_tgt,
                                folder=str(pca.get("folder") or "Knowledge"),
                                vault_path=str(pca.get("vaultPath") or _rt._tctx().current_turn_nexus_vault),
                            )
                        except Exception as exc:
                            return _failed_confirm_response(pca_cid, exc)
                        msg = (
                            f"Đã lưu ghi chú RAG `{saved_note['title']}` vào Nexus (`{saved_note['relPath']}`)."
                            if lang == "vi"
                            else f"Saved RAG note `{saved_note['title']}` to Nexus (`{saved_note['relPath']}`)."
                        )
                    else:
                        emitted_actions.append({"action": p_act, "target": p_tgt, "value": p_val})
                        msg = (
                            f"Đã xác nhận thực hiện `{p_act}` (`{p_tgt or p_val}`)."
                            if lang == "vi"
                            else f"Confirmed `{p_act}` (`{p_tgt or p_val}`)."
                        )
                else:
                    msg = "Đã huỷ thao tác đang chờ." if lang == "vi" else "Cancelled pending action."
                summary = _persist_confirm_outcome(res_state, pca_cid, msg)
                return {
                    "ok": True,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "resolved": True,
                    "resolution": res_state,
                    "requiresConfirmation": False,
                    "confirmId": "",
                    "text": msg,
                    "spokenText": msg,
                    "expression": "happy" if decision == "confirm" else "idle",
                    "dataPatch": [
                        {"path": "/resolved", "value": True},
                        {"path": "/resolution", "value": res_state},
                        {"path": "/statusLabel", "value": "Confirmed" if decision == "confirm" else "Cancelled"},
                        {"path": "/outcomeSummary", "value": summary},
                    ],
                    "clientActions": emitted_actions,
                }

        # Pending memory forget
        if isinstance(session_ctx.get("pendingMemoryForget"), dict):
            mem_target = dict(session_ctx.get("pendingMemoryForget") or {})
            mem_cid = str(mem_target.get("confirmId") or session_ctx.get("pendingConfirmId") or "")
            if mem_cid and confirm_id == mem_cid:
                session_ctx["pendingMemoryForget"] = None
                session_ctx["pendingConfirmId"] = ""
                _rt.save_session_context(session_ctx, now_ms=now_ms)
                res_state = "confirmed" if decision == "confirm" else "cancelled"
                if decision == "confirm":
                    try:
                        if mem_target.get("all"):
                            cnt = _rt.memory_store.forget_all()
                            msg = f"Đã xoá {cnt} ghi nhớ." if lang == "vi" else f"Removed {cnt} memory item(s)."
                        else:
                            _rt.memory_store.forget_one(int(mem_target["id"]))
                            msg = f"Đã xoá ghi nhớ #{mem_target['id']}." if lang == "vi" else f"Forgot memory #{mem_target['id']}."
                    except Exception as exc:
                        return _failed_confirm_response(mem_cid, exc)
                else:
                    msg = "Đã huỷ yêu cầu xoá ghi nhớ." if lang == "vi" else "Cancelled memory deletion."
                summary = _persist_confirm_outcome(res_state, mem_cid, msg)
                return {
                    "ok": True,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "resolved": True,
                    "resolution": res_state,
                    "requiresConfirmation": False,
                    "confirmId": "",
                    "text": msg,
                    "spokenText": msg,
                    "expression": "happy" if decision == "confirm" else "idle",
                    "dataPatch": [
                        {"path": "/resolved", "value": True},
                        {"path": "/resolution", "value": res_state},
                        {"path": "/statusLabel", "value": "Confirmed" if decision == "confirm" else "Cancelled"},
                        {"path": "/outcomeSummary", "value": summary},
                    ],
                    "clientActions": [],
                }

        # Pending file delete
        if session_ctx.get("pendingDeletePath"):
            del_path = str(session_ctx.get("pendingDeletePath") or "")
            del_cid = str(session_ctx.get("pendingDeleteConfirmId") or session_ctx.get("pendingConfirmId") or "")
            if del_cid and confirm_id == del_cid:
                session_ctx["pendingDeletePath"] = ""
                session_ctx["pendingDeleteConfirmId"] = ""
                session_ctx["pendingConfirmId"] = ""
                _rt.save_session_context(session_ctx, now_ms=now_ms)
                res_state = "confirmed" if decision == "confirm" else "cancelled"
                try:
                    if decision == "confirm":
                        _rt.agent_file_op({"op": "delete", "targetPath": del_path, "confirmStep": 0, "nowMs": now_ms})
                        _rt.agent_file_op({"op": "delete", "targetPath": del_path, "confirmStep": 1, "nowMs": now_ms + 1})
                        deleted = _rt.agent_file_op({"op": "delete", "targetPath": del_path, "confirmStep": 2, "nowMs": now_ms + 2})
                        msg = (
                            f"Đã xoá `{Path(del_path).name}` ({deleted['summary']})."
                            if lang == "vi"
                            else f"Deleted `{Path(del_path).name}` ({deleted['summary']})."
                        )
                    else:
                        _rt.agent_file_op({"op": "cancel_delete", "targetPath": del_path, "nowMs": now_ms})
                        msg = f"Đã huỷ xoá `{Path(del_path).name}`." if lang == "vi" else f"Cancelled deleting `{Path(del_path).name}`."
                except Exception as exc:
                    return _failed_confirm_response(del_cid, exc)
                summary = _persist_confirm_outcome(res_state, del_cid, msg)
                return {
                    "ok": True,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "resolved": True,
                    "resolution": res_state,
                    "requiresConfirmation": False,
                    "confirmId": "",
                    "text": msg,
                    "spokenText": msg,
                    "expression": "happy" if decision == "confirm" else "idle",
                    "dataPatch": [
                        {"path": "/resolved", "value": True},
                        {"path": "/resolution", "value": res_state},
                        {"path": "/statusLabel", "value": "Confirmed" if decision == "confirm" else "Cancelled"},
                        {"path": "/outcomeSummary", "value": summary},
                    ],
                    "clientActions": [],
                }

        # Pending permission_store confirmation (grant_directory, tainted_tool_escalation)
        resolved_conf = _rt.resolve_pending_confirmation(
            confirm_id=confirm_id,
            decision="confirm" if decision == "confirm" else "cancel",
            source="a2ui_confirm",
            now_ms=now_ms,
        )
        if resolved_conf:
            res_state = "confirmed" if decision == "confirm" else "cancelled"
            resolved_cid = str(resolved_conf.get("confirmId") or confirm_id)
            session_ctx["pendingConfirmId"] = ""
            _rt.save_session_context(session_ctx, now_ms=now_ms)
            follow_prompt = ""
            if decision == "confirm":
                p_data = resolved_conf.get("payload") or {}
                if resolved_conf.get("actionKind") == "grant_directory":
                    try:
                        _rt.grant_permission(
                            "directory",
                            target=resolved_conf["targetPath"],
                            levels=p_data.get("levels") or ["read", "write", "execute"],
                            ttl_ms=int(p_data.get("ttlMs") or 3600_000),
                            source="a2ui_confirm",
                            now_ms=now_ms,
                        )
                    except Exception as exc:
                        return _failed_confirm_response(resolved_cid, exc)
                    follow_prompt = str(p_data.get("originalPrompt") or "")
                    msg = (
                        f"Đã xác nhận và cấp quyền cho `{resolved_conf['targetPath']}`."
                        if lang == "vi"
                        else f"Confirmed and granted permission for `{resolved_conf['targetPath']}`."
                    )
                elif resolved_conf.get("actionKind") == "tainted_tool_escalation":
                    tool_lbl = str(p_data.get("tool") or resolved_conf.get("targetPath") or "").strip()
                    tool_args = p_data.get("args") if isinstance(p_data.get("args"), dict) else {}
                    tool_out = ""
                    if tool_lbl:
                        try:
                            tool_out = _rt._execute_llm_tool_call(
                                {"name": tool_lbl, "args": tool_args},
                                session_ctx,
                                companion_root or Path(__file__).resolve().parents[2],
                                now_ms,
                            )
                        except Exception as exc:
                            return _failed_confirm_response(resolved_cid, exc)
                    if _rt._is_tool_failure_output(tool_out):
                        res_state = "failed"
                        msg = (
                            f"Đã xác nhận `{tool_lbl or 'tool'}`, nhưng thao tác không thực thi được:\n{tool_out[:800]}"
                            if lang == "vi"
                            else f"Confirmed `{tool_lbl or 'tool'}`, but execution did not succeed:\n{tool_out[:800]}"
                        )
                        summary = _persist_confirm_outcome("failed", resolved_cid, msg)
                        return {
                            "ok": False,
                            "op": "a2ui_action",
                            "eventName": event_name,
                            "surfaceId": surface_id,
                            "resolved": True,
                            "resolution": "failed",
                            "requiresConfirmation": False,
                            "confirmId": "",
                            "error": tool_out[:200],
                            "text": msg,
                            "spokenText": msg,
                            "expression": "sad",
                            "dataPatch": [
                                {"path": "/resolved", "value": True},
                                {"path": "/resolution", "value": "failed"},
                                {"path": "/statusLabel", "value": "Failed"},
                                {"path": "/outcomeSummary", "value": summary},
                            ],
                            "clientActions": [],
                        }
                    if _rt._is_gui_tool_call(tool_lbl, tool_args) and bool(getattr(_rt._tctx(), "live_gui_control", False)):
                        session_ctx["guiApprovedUntilMs"] = int(now_ms) + 300_000
                        _rt.save_session_context(session_ctx, now_ms=now_ms)
                    msg = (
                        f"Đã xác nhận và thực thi công cụ `{tool_lbl or 'tool'}`."
                        + (f"\n{tool_out[:800]}" if tool_out else "")
                        if lang == "vi"
                        else f"Confirmed and executed `{tool_lbl or 'tool'}`."
                        + (f"\n{tool_out[:800]}" if tool_out else "")
                    )
                else:
                    msg = (
                        f"Đã xác nhận thao tác cho `{resolved_conf.get('targetPath', '')}`."
                        if lang == "vi"
                        else f"Confirmed action for `{resolved_conf.get('targetPath', '')}`."
                    )
            else:
                msg = "Đã từ chối và huỷ thao tác đang chờ." if lang == "vi" else "Denied and cancelled pending action."
            summary = _persist_confirm_outcome(res_state, resolved_cid, msg)
            return {
                "ok": True,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "resolved": True,
                "resolution": res_state,
                "requiresConfirmation": False,
                "confirmId": "",
                "followUpPrompt": follow_prompt,
                "text": msg,
                "spokenText": msg,
                "expression": "happy" if decision == "confirm" else "idle",
                "dataPatch": [
                    {"path": "/resolved", "value": True},
                    {"path": "/resolution", "value": res_state},
                    {"path": "/statusLabel", "value": "Confirmed" if decision == "confirm" else "Cancelled"},
                    {"path": "/outcomeSummary", "value": summary},
                ],
                "clientActions": [],
            }

        # Stale or mismatched confirmation ID -> idempotently lock unresolved card without clobbering already-resolved outcome
        stale_msg = _outcome_summary(
            "Yêu cầu xác nhận này đã hết hạn hoặc đã xử lý trước đó."
            if lang == "vi"
            else "This confirmation request has already been resolved or expired."
        )
        existing_dm = existing_cs.get("dataModel") if isinstance(existing_cs.get("dataModel"), dict) else {}
        if existing_dm.get("resolved") is True:
            prev_res = str(existing_dm.get("resolution") or "expired")
            prev_lbl = str(existing_dm.get("statusLabel") or "Resolved")
            prev_out = str(existing_dm.get("outcomeSummary") or "")
            stale_patches: list[dict[str, Any]] = [
                {"path": "/resolved", "value": True},
                {"path": "/resolution", "value": prev_res},
                {"path": "/statusLabel", "value": prev_lbl},
            ]
            if prev_out:
                stale_patches.append({"path": "/outcomeSummary", "value": prev_out})
            stale_res_out = prev_res
        else:
            if surface_id:
                _rt.history_store.patch_surface_by_id(surface_id, lock_resolution="expired", outcome_summary=stale_msg)
            stale_patches = [
                {"path": "/resolved", "value": True},
                {"path": "/resolution", "value": "expired"},
                {"path": "/statusLabel", "value": "Expired"},
                {"path": "/outcomeSummary", "value": stale_msg},
            ]
            stale_res_out = "expired"
        return {
            "ok": True,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "stale": True,
            "resolved": True,
            "resolution": stale_res_out,
            "requiresConfirmation": False,
            "confirmId": "",
            "text": stale_msg,
            "dataPatch": stale_patches,
            "clientActions": [],
        }

    # 2. Plan checklist toggle / generic checkbox / choice / slider / form state persistence
    if event_name in ("plan_toggle", "checklist_toggle", "choice_submit", "slider_change", "form_submit"):
        patches: list[tuple[str, Any]] = []
        if event_name == "plan_toggle" and "index" in ctx_in:
            try:
                idx = int(ctx_in["index"])
                done_val = bool(ctx_in.get("done", False))
                patches.append((f"/steps/{idx}/done", done_val))
            except (TypeError, ValueError):
                pass
        else:
            if "path" in ctx_in:
                ptr = str(ctx_in.get("path") or "").strip()
                if _rt.a2ui_validator._is_valid_pointer(ptr):
                    patches.append((ptr, ctx_in.get("value")))
            if event_name == "form_submit":
                raw_fields = ctx_in.get("values") if isinstance(ctx_in.get("values"), dict) else (
                    ctx_in.get("fields") if isinstance(ctx_in.get("fields"), dict) else None
                )
                if isinstance(raw_fields, dict):
                    for fk, fv in list(raw_fields.items())[:24]:
                        fk_s = str(fk or "").strip()
                        if not fk_s:
                            continue
                        f_ptr = fk_s if fk_s.startswith("/") else f"/inputs/{fk_s}"
                        if _rt.a2ui_validator._is_valid_pointer(f_ptr):
                            patches.append((f_ptr, fv))
                for ck, cv in list(ctx_in.items())[:24]:
                    if ck in ("path", "value", "prompt", "values", "fields"):
                        continue
                    ck_s = str(ck or "").strip()
                    if not ck_s:
                        continue
                    c_ptr = ck_s if ck_s.startswith("/") else f"/inputs/{ck_s}"
                    if _rt.a2ui_validator._is_valid_pointer(c_ptr):
                        patches.append((c_ptr, cv))
        if surface_id and patches:
            patched_surf = _rt.history_store.patch_surface_by_id(surface_id, patches)
            if patched_surf is None:
                return {
                    "ok": False,
                    "op": "a2ui_action",
                    "eventName": event_name,
                    "surfaceId": surface_id,
                    "error": "surface_patch_failed",
                    "text": (
                        "Không thể lưu thay đổi thẻ (vượt giới hạn 6 KB hoặc thẻ không tồn tại)."
                        if lang == "vi"
                        else "Could not persist card update (exceeded 6 KB limit or surface not found)."
                    ),
                    "dataPatch": [],
                    "clientActions": [],
                }
        follow_p = "" if surf_is_tainted else str(ctx_in.get("prompt") or "").strip()[:400]
        return {
            "ok": True,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "followUpPrompt": follow_p,
            "dataPatch": [{"path": p, "value": v} for p, v in patches],
            "clientActions": [],
        }

    # 3. Rollback receipt button on Receipt Card
    if event_name == "rollback_receipt":
        rcpt_id = str(ctx_in.get("receiptId") or "").strip()
        if not rcpt_id:
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "missing_receipt_id"}
        try:
            rb = _rt.agent_file_op({"op": "rollback", "receiptId": rcpt_id, "nowMs": now_ms})
            if surface_id:
                _rt.history_store.patch_surface_by_id(surface_id, [("/rolledBack", True)])
            msg = str(rb.get("summary") or ("Đã hoàn tác thay đổi." if lang == "vi" else "Rolled back file edit."))
            return {
                "ok": True,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "receiptId": rcpt_id,
                "rolledBack": True,
                "text": msg,
                "spokenText": msg,
                "expression": "happy",
                "dataPatch": [{"path": "/rolledBack", "value": True}],
                "clientActions": [],
            }
        except Exception as exc:
            return {
                "ok": False,
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "error": str(exc)[:160],
            }

    # 3b. Open file in user's preferred editor (non-generative trusted cards only; guarded above)
    if event_name == "open_editor":
        raw_fp = str(ctx_in.get("path") or ctx_in.get("file") or "").strip()
        if not raw_fp or not _rt.a2ui_validator._is_safe_file_path(raw_fp):
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "invalid_file_path"}
        if _rt.is_symlink_raw(raw_fp) or _rt.is_sensitive_path(raw_fp):
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "forbidden_file_path"}
        target_p = Path(raw_fp).expanduser().resolve(strict=False)
        if _rt.is_forbidden_path(target_p) or _rt.is_sensitive_path(target_p):
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "forbidden_file_path"}
        try:
            opened = _rt.agent_file_op({
                "op": "open_editor",
                "targetPath": str(target_p),
                "nowMs": now_ms,
                "forceExternal": bool(ctx_in.get("forceExternal", False)),
            })
            msg = str(opened.get("summary") or (f"Đã mở `{target_p.name}`." if lang == "vi" else f"Opened `{target_p.name}`."))
            resp: dict[str, Any] = {
                "ok": opened.get("status") != "failed",
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "targetPath": str(opened.get("targetPath") or target_p),
                "text": msg,
                "dataPatch": [],
                "clientActions": [],
            }
            for k in ("editorMode", "editorPopup", "editorCommand"):
                if k in opened:
                    resp[k] = opened[k]
            return resp
        except Exception as exc:
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": str(exc)[:160]}

    # 3c. Save path or snippet to Companion Pocket
    if event_name == "save_pocket":
        import pocket as pocket_store

        raw_path = str(ctx_in.get("path") or ctx_in.get("file") or "").strip()
        raw_text = str(ctx_in.get("text") or ctx_in.get("content") or "").strip()
        raw_label = str(ctx_in.get("label") or ctx_in.get("title") or "").strip()[:120]
        try:
            if raw_path:
                if _rt.is_symlink_raw(raw_path) or _rt.is_sensitive_path(raw_path):
                    return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "invalid_pocket_path"}
                target_p = Path(raw_path).expanduser().resolve(strict=False)
                if _rt.is_forbidden_path(target_p) or _rt.is_sensitive_path(target_p) or not target_p.is_file():
                    return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "invalid_pocket_path"}
                perm = _rt.check_permission("file_read", target_path=str(target_p), source="a2ui_save_pocket", now_ms=now_ms)
                if not perm.get("allowed"):
                    return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "permission_denied"}
                pk_res = pocket_store.dispatch({
                    "version": 1,
                    "consent": True,
                    "action": "add",
                    "urls": [target_p.as_uri()],
                    "source": "drop",
                })
            elif raw_text:
                notes_dir = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "alis-companion" / "pocket_clips"
                notes_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_label or "clip").strip("_")[:40] or "clip"
                clip_file = (notes_dir / f"{slug}_{now_ms}.md").resolve(strict=False)
                clip_file.write_text(raw_text[:8000], encoding="utf-8")
                os.chmod(clip_file, 0o600)
                pk_res = pocket_store.dispatch({
                    "version": 1,
                    "consent": True,
                    "action": "add",
                    "urls": [clip_file.as_uri()],
                    "source": "drop",
                })
            else:
                return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "missing_pocket_payload"}
            if surface_id:
                _rt.history_store.patch_surface_by_id(surface_id, [("/savedToPocket", True)])
            msg = "Đã lưu vào Pocket." if lang == "vi" else "Saved to Pocket."
            return {
                "ok": bool(pk_res.get("ok", True)),
                "op": "a2ui_action",
                "eventName": event_name,
                "surfaceId": surface_id,
                "text": msg,
                "dataPatch": [{"path": "/savedToPocket", "value": True}],
                "clientActions": [],
            }
        except Exception as exc:
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": str(exc)[:160]}

    # 3d. Cancel a scheduled reminder / recurring job / process watch from Schedule A2UI Card
    if event_name == "cancel_schedule":
        job_id = str(ctx_in.get("jobId") or req.get("jobId") or "").strip()
        if not job_id:
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": "missing_job_id"}
        try:
            cancelled_job = _rt.scheduler_store.cancel_job(job_id)
        except _rt.SchedulerError as exc:
            return {"ok": False, "op": "a2ui_action", "eventName": event_name, "surfaceId": surface_id, "error": str(exc)[:160]}
        safe_key = re.sub(r"[^A-Za-z0-9_]", "_", job_id)
        rem_active = len(_rt.scheduler_store.list_jobs())
        status_lbl = f"{rem_active} " + ("tác vụ đang hoạt động" if lang == "vi" else "active job(s)") if rem_active else ("Không có lịch hẹn nào đang chạy" if lang == "vi" else "No active scheduled jobs")
        patches = [
            {"path": f"/cancelled_{safe_key}", "value": True},
            {"path": "/statusLabel", "value": status_lbl},
        ]
        if surface_id:
            _rt.history_store.patch_surface_by_id(surface_id, [(p["path"], p["value"]) for p in patches])
        msg = (
            f"Đã huỷ lịch hẹn `{cancelled_job['id']}` ({cancelled_job['title']})."
            if lang == "vi"
            else f"Cancelled schedule `{cancelled_job['id']}` ({cancelled_job['title']})."
        )
        return {
            "ok": True,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "job": cancelled_job,
            "text": msg,
            "dataPatch": patches,
            "clientActions": [],
        }

    # 4. Safe follow-up prompt dispatch (already enforced against generative run_tests & tainted surfaces above)
    if event_name in ("send_prompt", "followup_prompt", "run_tests"):
        follow_p = str(ctx_in.get("prompt") or ("/tests" if event_name == "run_tests" else "")).strip()[:500]
        return {
            "ok": True,
            "op": "a2ui_action",
            "eventName": event_name,
            "surfaceId": surface_id,
            "followUpPrompt": follow_p,
            "dataPatch": [],
            "clientActions": [],
        }

    return {
        "ok": True,
        "op": "a2ui_action",
        "eventName": event_name,
        "surfaceId": surface_id,
        "dataPatch": [],
        "clientActions": [],
    }
