"""Self-learning trajectory store and ShareGPT/ChatML JSONL fine-tuning dataset exporter for Alis-Companion."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time

import history_store
import memory_store
import run_store
import skill_store


_LEAKED_FIXTURE_PATTERNS = (
    "/tmp/alis-companion-",
    "empty-audit-fixture.example.org",
    "example.org",
)


def _is_leaked_test_fixture(text: str) -> bool:
    s = str(text or "")
    return any(pat in s for pat in _LEAKED_FIXTURE_PATTERNS)


def _state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg) if xdg else (Path.home() / ".local" / "state")
    folder = (base / "alis-companion").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def _learning_file() -> Path:
    return _state_dir() / "learning_store.json"


def _load_learning_store() -> dict:
    p = _learning_file()
    if not p.is_file():
        return {"trajectories": [], "preferences": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "trajectories": data.get("trajectories") if isinstance(data.get("trajectories"), list) else [],
                "preferences": data.get("preferences") if isinstance(data.get("preferences"), dict) else {},
            }
    except (OSError, ValueError):
        pass
    return {"trajectories": [], "preferences": {}}


def _save_learning_store(store: dict) -> None:
    p = _learning_file()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    p.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def record_trajectory(
    user_prompt: str,
    assistant_reply: str,
    tool_calls: list[dict] | None = None,
    *,
    success: bool = True,
    rating: int = 1,
    preferences: dict | None = None,
) -> dict:
    """Record a successful user/assistant/tool trajectory for future local LoRA fine-tuning."""
    store = _load_learning_store()
    entry = {
        "userPrompt": str(user_prompt or "").strip(),
        "assistantReply": str(assistant_reply or "").strip(),
        "toolCalls": list(tool_calls or []),
        "success": bool(success),
        "rating": int(rating),
        "timestamp": int(time.time() * 1000),
    }
    store["trajectories"] = (store.get("trajectories", []) + [entry])[-500:]
    if isinstance(preferences, dict) and preferences:
        merged_prefs = dict(store.get("preferences") or {})
        merged_prefs.update(preferences)
        store["preferences"] = merged_prefs
    _save_learning_store(store)
    return entry


def _collect_history_trajectories() -> list[dict]:
    """Extract chronological user->assistant turn pairs from SQLite chat history."""
    pairs: list[dict] = []
    try:
        db_path = history_store.path()
        if not db_path.is_file():
            return pairs
        with sqlite3.connect(str(db_path), timeout=3.0) as conn:
            rows = conn.execute(
                """SELECT tm.thread_id, m.role, m.content
                   FROM thread_messages tm
                   JOIN messages m ON m.id = tm.message_id
                   ORDER BY tm.thread_id ASC, m.created_ms ASC, m.id ASC"""
            ).fetchall()
        pending_user = ""
        last_tid = ""
        for tid, role, content in rows:
            tid_s = str(tid or "")
            role_s = str(role or "")
            content_s = str(content or "").strip()
            if tid_s != last_tid:
                pending_user = ""
                last_tid = tid_s
            if role_s == "user" and content_s:
                pending_user = content_s
            elif role_s == "assistant" and content_s and pending_user:
                if not _is_leaked_test_fixture(pending_user) and not _is_leaked_test_fixture(content_s):
                    pairs.append(
                        {
                            "userPrompt": pending_user,
                            "assistantReply": content_s,
                            "toolCalls": [],
                            "success": True,
                            "rating": 1,
                        }
                    )
                pending_user = ""
    except Exception:
        pass
    return pairs


def _collect_run_store_trajectories(
    history_pairs: list[dict] | None = None,
    reflection_reply_by_prompt: dict[str, str] | None = None,
) -> list[dict]:
    """Reconstruct multi-turn tool trajectories from completed agent_runs + agent_steps."""
    trajectories: list[dict] = []
    history_reply_by_prompt: dict[str, str] = {}
    for hp in history_pairs or []:
        if isinstance(hp, dict) and hp.get("userPrompt") and hp.get("assistantReply"):
            history_reply_by_prompt.setdefault(str(hp["userPrompt"]).strip(), str(hp["assistantReply"]).strip())

    try:
        db_path = run_store._db_path()
        if not db_path.is_file():
            return trajectories
        with sqlite3.connect(str(db_path), timeout=3.0) as conn:
            conn.row_factory = sqlite3.Row
            run_rows = conn.execute(
                """SELECT run_id, status, goal, checkpoint_json
                   FROM agent_runs
                   WHERE status = 'completed'
                   ORDER BY started_ms ASC"""
            ).fetchall()
            for r in run_rows:
                run_id = str(r["run_id"] or "")
                goal = str(r["goal"] or "").strip()
                if not goal or _is_leaked_test_fixture(goal):
                    continue
                try:
                    cp = json.loads(r["checkpoint_json"] or "{}")
                    if not isinstance(cp, dict):
                        cp = {}
                except Exception:
                    cp = {}

                step_rows = conn.execute(
                    """SELECT seq, tool_name, target_path, status, detail_json
                       FROM agent_steps
                       WHERE run_id = ?
                       ORDER BY seq ASC""",
                    (run_id,),
                ).fetchall()

                leaked_step = False
                tool_calls: list[dict] = []
                last_step_summary = ""
                for s in step_rows:
                    t_path = str(s["target_path"] or "").strip()
                    raw_detail = str(s["detail_json"] or "")
                    if _is_leaked_test_fixture(t_path) or _is_leaked_test_fixture(raw_detail):
                        leaked_step = True
                        break
                    if str(s["status"] or "") != "completed":
                        continue
                    try:
                        detail = json.loads(raw_detail) if raw_detail else {}
                        if not isinstance(detail, dict):
                            detail = {"output": str(detail)[:600]}
                    except Exception:
                        detail = {"output": raw_detail[:600]}

                    t_name = str(s["tool_name"] or "tool").strip()
                    for sum_key in ("summary", "visionSummary", "output", "message", "title"):
                        cand = str(detail.get(sum_key) or "").strip()
                        if cand:
                            last_step_summary = cand[:1200]
                            break

                    # Internal synthesis step is not an external tool call
                    if t_name == "llm_synthesize":
                        continue

                    args: dict = {}
                    if t_path:
                        if t_name in ("web_search", "memory_search"):
                            args["query"] = t_path
                        elif t_name in ("fetch_url", "browser_open"):
                            args["url"] = t_path
                        else:
                            args["targetPath"] = t_path
                    for k in ("op", "action", "query", "url", "command", "value", "kind"):
                        if detail.get(k) is not None and detail.get(k) != "":
                            args[k] = detail[k]

                    obs: dict = {}
                    for ok_key in (
                        "status",
                        "summary",
                        "visionSummary",
                        "output",
                        "count",
                        "targetPath",
                        "title",
                        "appId",
                        "headings",
                        "op",
                        "action",
                        "source",
                        "addedLines",
                        "removedLines",
                        "receiptId",
                    ):
                        val = detail.get(ok_key)
                        if val is not None and val != "":
                            obs[ok_key] = str(val)[:600] if isinstance(val, str) else val
                    if not obs and detail:
                        obs = {k: (str(v)[:400] if isinstance(v, str) else v) for k, v in list(detail.items())[:8]}

                    if not last_step_summary and t_name:
                        if t_name == "screen_observe":
                            app_id = str(detail.get("appId") or "desktop").strip()
                            win_title = str(detail.get("title") or "active window").strip()
                            headings = detail.get("headings") if isinstance(detail.get("headings"), list) else []
                            h_preview = "; ".join(str(h).strip() for h in headings[:3] if str(h).strip())
                            last_step_summary = f"Observed {app_id} ({win_title})" + (f": {h_preview}" if h_preview else ".")
                        else:
                            last_step_summary = f"Completed {t_name}" + (f" on {t_path}." if t_path else ".")

                    tool_calls.append(
                        {
                            "name": t_name,
                            "arguments": args,
                            "observation": obs,
                        }
                    )

                if leaked_step:
                    continue

                final_reply = (
                    str(cp.get("finalReply") or "").strip()
                    or history_reply_by_prompt.get(goal, "")
                    or (reflection_reply_by_prompt or {}).get(goal, "")
                    or last_step_summary
                )
                if not final_reply or _is_leaked_test_fixture(final_reply):
                    continue

                trajectories.append(
                    {
                        "userPrompt": goal,
                        "assistantReply": final_reply,
                        "toolCalls": tool_calls,
                        "success": True,
                        "rating": 1,
                    }
                )
    except Exception:
        pass
    return trajectories


def export_finetune_dataset(
    output_path: str | Path | None = None,
    format: str = "sharegpt",
) -> dict:
    """Export successful user/assistant/tool trajectories and learned preferences into a ShareGPT or ChatML JSONL file."""
    store = _load_learning_store()
    trajectories = list(store.get("trajectories") or [])
    prefs = dict(store.get("preferences") or {})

    # Enrich preferences from explicit memory store if available
    try:
        mem_rows = memory_store.list_all()
        mem_items = [str(m.get("text") or "").strip() for m in (mem_rows or []) if isinstance(m, dict) and m.get("text")]
        if mem_items:
            prefs.setdefault("memories", mem_items[:20])
    except Exception:
        pass

    # Extract Hermes self-learning reflections (mapping prompt -> summary for run recovery)
    reflection_reply_by_prompt: dict[str, str] = {}
    refl_trajectories: list[dict] = []
    try:
        for refl in skill_store._load_reflections():
            if not isinstance(refl, dict) or refl.get("status") == "failed":
                continue
            p_txt = str(refl.get("prompt") or "").strip()
            s_txt = str(refl.get("summary") or "").strip()
            l_txt = str(refl.get("lesson") or "").strip()
            reply_txt = s_txt or (
                l_txt if (l_txt and not l_txt.startswith("Completed ") and " ->  ->" not in l_txt) else ""
            )
            if p_txt and reply_txt and not _is_leaked_test_fixture(p_txt) and not _is_leaked_test_fixture(reply_txt):
                reflection_reply_by_prompt[p_txt] = reply_txt
                ops = [str(op).strip() for op in (refl.get("ops") or []) if str(op).strip() and str(op).strip() != "llm_synthesize"]
                refl_trajectories.append(
                    {
                        "userPrompt": p_txt,
                        "assistantReply": reply_txt,
                        "toolCalls": (
                            [{"skill": str(refl.get("skillName") or "")}]
                            if refl.get("skillName")
                            else [{"name": op, "arguments": {}} for op in ops]
                        ),
                        "success": True,
                        "rating": int(refl.get("positiveCount") or 1),
                    }
                )
    except Exception:
        pass

    # Reconstruct multi-turn tool trajectories from run_store and chat history
    history_pairs = _collect_history_trajectories()
    trajectories.extend(_collect_run_store_trajectories(history_pairs, reflection_reply_by_prompt))
    trajectories.extend(refl_trajectories)
    trajectories.extend(history_pairs)

    out_file = (
        Path(output_path).expanduser().resolve(strict=False)
        if output_path
        else (_state_dir() / "exports" / "companion_finetune_chatml.jsonl")
    )
    out_file.parent.mkdir(parents=True, exist_ok=True)

    sys_prompt = "You are Alis-Companion (Aqua/Octo), a native Linux Wayland desktop AI assistant."
    if prefs:
        sys_prompt += "\nUser Preferences: " + json.dumps(prefs, ensure_ascii=False)

    fmt = str(format or "sharegpt").strip().lower()
    seen_keys: set[tuple[str, str]] = set()
    count = 0
    with out_file.open("w", encoding="utf-8") as f:
        for t in trajectories:
            if not isinstance(t, dict):
                continue
            if not t.get("success", True) or int(t.get("rating", 1)) < 0:
                continue
            user_prompt = str(t.get("userPrompt") or t.get("prompt") or "").strip()
            assistant_reply = str(t.get("assistantReply") or t.get("reply") or "").strip()
            if not user_prompt or not assistant_reply:
                continue
            if _is_leaked_test_fixture(user_prompt) or _is_leaked_test_fixture(assistant_reply):
                continue
            dedup_key = (user_prompt, assistant_reply)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            tool_calls = t.get("toolCalls") if isinstance(t.get("toolCalls"), list) else []

            if fmt == "sharegpt":
                convs = [
                    {"from": "system", "value": sys_prompt},
                    {"from": "human", "value": user_prompt},
                ]
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        tc_payload = {
                            "name": str(tc.get("name") or tc.get("tool") or tc.get("skill") or "tool"),
                            "arguments": (
                                tc.get("arguments")
                                if isinstance(tc.get("arguments"), dict)
                                else {k: v for k, v in tc.items() if k not in ("name", "arguments", "observation")}
                            ),
                        }
                        convs.append({"from": "function_call", "value": json.dumps(tc_payload, ensure_ascii=False)})
                        if "observation" in tc and tc["observation"] is not None:
                            obs_val = (
                                tc["observation"]
                                if isinstance(tc["observation"], str)
                                else json.dumps(tc["observation"], ensure_ascii=False)
                            )
                            convs.append({"from": "observation", "value": obs_val})
                    else:
                        convs.append({"from": "function_call", "value": json.dumps(tc, ensure_ascii=False)})
                convs.append({"from": "gpt", "value": assistant_reply})
                record = {"conversations": convs}
            else:
                msgs = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                for idx, tc in enumerate(tool_calls):
                    if isinstance(tc, dict):
                        tc_name = str(tc.get("name") or tc.get("tool") or tc.get("skill") or "tool")
                        tc_args = (
                            tc.get("arguments")
                            if isinstance(tc.get("arguments"), dict)
                            else {k: v for k, v in tc.items() if k not in ("name", "arguments", "observation")}
                        )
                        call_id = f"call_{idx + 1}"
                        msgs.append(
                            {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": call_id,
                                        "type": "function",
                                        "function": {
                                            "name": tc_name,
                                            "arguments": json.dumps(tc_args, ensure_ascii=False),
                                        },
                                    }
                                ],
                            }
                        )
                        if "observation" in tc and tc["observation"] is not None:
                            obs_val = (
                                tc["observation"]
                                if isinstance(tc["observation"], str)
                                else json.dumps(tc["observation"], ensure_ascii=False)
                            )
                            msgs.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call_id,
                                    "name": tc_name,
                                    "content": obs_val,
                                }
                            )
                msgs.append({"role": "assistant", "content": assistant_reply})
                record = {"messages": msgs}

            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1

    return {
        "ok": True,
        "op": "export_finetune",
        "path": str(out_file),
        "outputPath": str(out_file),
        "count": count,
        "format": fmt,
    }
