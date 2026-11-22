#!/usr/bin/env python3
"""Route-aware context compaction and non-waking `next-step` context inbox.

Implements DeepSeek Harness architecture upgrades #2 and #3 for Alis-Companion:
1. Route-aware context window & input budget (`(W - reserved_output) * 0.85`),
   conservative UTF-8 + tool-call token estimation, and structured session
   compaction preserving:
   - byte-identical static `system` prompt prefix (for Prompt Prefix Cache),
   - recent ~60% of `input_budget`,
   - indivisible `assistant(tool_calls)` <-> `tool(tool_call_id)` atomic groups
     (never emitting orphaned `tool` messages or unmatched `tool_calls`).
2. Session-scoped non-waking `context_inbox` (`enqueue_context_update`,
   `drain_context_updates`, `drain_and_inject_context`) that deduplicates
   ambient state updates (`mood_checkin`, `active_window`, `nexus_note`, etc.)
   without waking the LLM or mutating the top-of-prompt system message.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import spill_store

COMPACTED_HEADER = "[Compacted Session Context]"
DEFAULT_CONTEXT_WINDOW = 32_768
DEFAULT_RESERVED_OUTPUT = 1_024
MIN_INPUT_BUDGET = 2_048
RECENT_BUDGET_RATIO = 0.60
INPUT_SAFETY_RATIO = 0.85
CONTEXT_PROBE_TTL_SEC = 60.0

_CONTEXT_PROBE_LOCK = threading.Lock()
_CONTEXT_PROBE_CACHE: dict[tuple[str, str], tuple[float, int]] = {}

_LOCAL_PROVIDER_IDS = frozenset((
    "ollama",
    "lmstudio",
    "lm-studio",
    "llamacpp",
    "llama.cpp",
    "gguf",
    "local",
))


def clear_context_probe_cache() -> None:
    """Clear cached live endpoint context window probes."""
    with _CONTEXT_PROBE_LOCK:
        _CONTEXT_PROBE_CACHE.clear()


def _loopback_origin(endpoint: str) -> str:
    raw = str(endpoint or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return ""
    host = (parsed.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def probe_live_context_window(endpoint: str, model: str = "", timeout_sec: float = 1.2) -> int | None:
    """Query loopback LM Studio (`/api/v0/models`) or Ollama (`/api/show`) for live context window."""
    origin = _loopback_origin(endpoint)
    if not origin:
        return None
    if os.environ.get("ALIS_TEST_CONTEXT_PROBE") != "1" and (
        os.environ.get("ALIS_TEST_ISOLATED")
        or os.environ.get("AQUA_TEST_ENDPOINT")
        or os.environ.get("AI_FIXTURE_URL")
    ):
        return None

    mdl_key = str(model or "").strip().lower()
    cache_key = (origin, mdl_key)
    now = time.monotonic()
    with _CONTEXT_PROBE_LOCK:
        cached = _CONTEXT_PROBE_CACHE.get(cache_key)
        if cached and (now - cached[0]) < CONTEXT_PROBE_TTL_SEC:
            return cached[1]

    detected: int | None = None
    _local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # 1. LM Studio native REST endpoint (/api/v0/models)
    try:
        req = urllib.request.Request(f"{origin}/api/v0/models", headers={"Accept": "application/json"})
        with _local_opener.open(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        entries = payload.get("data") if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])
        matched = None
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip().lower()
            if mdl_key and (item_id == mdl_key or mdl_key in item_id):
                matched = item
                break
            if matched is None and str(item.get("state") or "").lower() == "loaded":
                matched = item
        if matched is None and isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], dict):
            matched = entries[0]
        if isinstance(matched, dict):
            for k in ("loaded_context_length", "context_length", "max_context_length"):
                val = matched.get(k)
                if isinstance(val, (int, float)) and int(val) >= MIN_INPUT_BUDGET:
                    detected = int(val)
                    break
    except Exception:
        detected = None

    # 2. Ollama native model metadata (/api/show)
    if detected is None and mdl_key:
        try:
            body = json.dumps({"name": str(model).strip()}).encode("utf-8")
            req = urllib.request.Request(
                f"{origin}/api/show",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _local_opener.open(req, timeout=timeout_sec) as resp:
                show_data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(show_data, dict):
                mi = show_data.get("model_info") if isinstance(show_data.get("model_info"), dict) else {}
                for k, v in mi.items():
                    if str(k).endswith(".context_length") and isinstance(v, (int, float)) and int(v) >= MIN_INPUT_BUDGET:
                        detected = int(v)
                        break
                if detected is None:
                    params_txt = str(show_data.get("parameters") or "")
                    m_ctx = re.search(r"\bnum_ctx\s+(\d{4,7})\b", params_txt)
                    if m_ctx and int(m_ctx.group(1)) >= MIN_INPUT_BUDGET:
                        detected = int(m_ctx.group(1))
        except Exception:
            detected = None

    if detected is not None and detected >= MIN_INPUT_BUDGET:
        with _CONTEXT_PROBE_LOCK:
            _CONTEXT_PROBE_CACHE[cache_key] = (now, detected)
        return detected
    return None


def resolve_route_context_window(
    provider: str = "",
    model: str = "",
    endpoint: str = "",
    explicit_context_window: int | None = None,
) -> int:
    """Resolve authoritative context window `W` for a `(provider, model)` route."""
    if explicit_context_window is not None:
        try:
            explicit_val = int(explicit_context_window)
            if explicit_val >= MIN_INPUT_BUDGET:
                return explicit_val
        except (TypeError, ValueError):
            pass

    prov = str(provider or "").strip().lower()
    mdl = str(model or "").strip().lower()
    ep = str(endpoint or "").strip().lower()

    is_local = (
        prov in _LOCAL_PROVIDER_IDS
        or mdl.startswith(("ollama-", "lmstudio-", "gguf:"))
        or any(h in ep for h in ("127.0.0.1", "localhost", "[::1]", ":11434", ":1234"))
    )

    if is_local:
        if _loopback_origin(endpoint):
            probed = probe_live_context_window(endpoint, model)
            if probed is not None and probed >= MIN_INPUT_BUDGET:
                return probed
        # Local llama.cpp / raw GGUF / older small local models default 16k;
        # modern local Ollama / LM Studio / Qwen / Llama-3 / Gemma / DeepSeek-distill default 32k.
        if (
            prov in ("llamacpp", "llama.cpp", "gguf")
            or mdl.startswith("gguf:")
            or any(k in mdl for k in ("llama-2", "llama2", "tinyllama", "phi-2", "mistral-7b-v0.1"))
        ):
            if any(k in mdl for k in ("qwen2.5", "qwen3", "llama-3.1", "llama-3.2", "llama-3.3", "32k")):
                return 32_768
            return 16_384
        if "16k" in mdl:
            return 16_384
        return 32_768

    # Google Gemini routes -> 1M
    if "gemini" in prov or "google" in prov or "gemini" in mdl:
        return 1_000_000

    # Anthropic Claude routes -> 200k
    if "anthropic" in prov or "claude" in mdl:
        return 200_000

    # DeepSeek cloud routes -> 64k–128k
    if "deepseek" in prov or "deepseek" in mdl:
        if "deepseek-v2" in mdl or "64k" in mdl:
            return 65_536
        return 128_000

    # OpenAI / Codex GPT-4o / GPT-4.1 / GPT-5 / o-series -> 128k–200k
    if (
        "openai" in prov
        or "codex" in prov
        or any(
            mdl.startswith(p) or f"/{p}" in mdl or f"-{p}" in mdl
            for p in ("gpt-5", "codex", "o1", "o3", "o4")
        )
    ):
        if any(k in mdl for k in ("gpt-5", "codex", "o1", "o3", "o4")):
            return 200_000
        return 128_000

    if any(k in mdl for k in ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "chatgpt-4o")):
        return 128_000

    return DEFAULT_CONTEXT_WINDOW


def compute_input_budget(context_window: int, reserved_output: int = DEFAULT_RESERVED_OUTPUT) -> int:
    """Compute conservative input token budget `max(2048, int((W - reserved_output) * 0.85))`."""
    try:
        w = int(context_window)
    except (TypeError, ValueError):
        w = DEFAULT_CONTEXT_WINDOW
    try:
        res_out = max(0, int(reserved_output or 0))
    except (TypeError, ValueError):
        res_out = DEFAULT_RESERVED_OUTPUT
    return max(MIN_INPUT_BUDGET, int((w - res_out) * INPUT_SAFETY_RATIO))


def _extract_content_text_and_vision_tokens(content) -> tuple[str, int]:
    """Extract text for token estimation without counting raw base64 image URIs as text tokens."""
    if content is None:
        return "", 0
    if isinstance(content, str):
        return content, 0
    if isinstance(content, list):
        texts: list[str] = []
        vision_tokens = 0
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict):
                ptype = str(part.get("type") or "")
                if ptype in ("image_url", "input_image", "image"):
                    vision_tokens += 768
                elif "text" in part and part.get("text") is not None:
                    texts.append(str(part["text"]))
                else:
                    texts.append(json.dumps(part, ensure_ascii=False))
        return "\n".join(texts), vision_tokens
    return json.dumps(content, ensure_ascii=False), 0


def _string_tokens(text: str) -> int:
    if not text:
        return 0
    utf8_len = len(text.encode("utf-8", errors="replace"))
    return max(utf8_len // 3, len(text) // 4)


def estimate_message_tokens(msg: dict) -> int:
    """Conservatively estimate tokens for a single chat message including tool call payloads."""
    if not isinstance(msg, dict):
        return 12
    text, vision_tokens = _extract_content_text_and_vision_tokens(msg.get("content"))
    reasoning = str(msg.get("reasoning_content") or msg.get("reasoning") or "")
    combined = f"{text}\n{reasoning}" if reasoning else text
    tokens = _string_tokens(combined) + vision_tokens + 12

    tool_calls = msg.get("tool_calls")
    if tool_calls:
        try:
            tc_json = json.dumps(tool_calls, ensure_ascii=False)
        except Exception:
            tc_json = str(tool_calls)
        tokens += _string_tokens(tc_json) + 8

    tc_id = str(msg.get("tool_call_id") or "")
    t_name = str(msg.get("name") or "")
    if tc_id or t_name:
        tokens += _string_tokens(f"{tc_id}:{t_name}") + 4
    return tokens


def estimate_messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_message_tokens(m) for m in (messages or []) if isinstance(m, dict))


def estimate_tools_tokens(tools: list[dict] | None) -> int:
    """Conservatively estimate prompt token overhead consumed by serialized tool schemas."""
    if not tools or not isinstance(tools, (list, tuple)):
        return 0
    try:
        serialized = json.dumps(list(tools), ensure_ascii=False, separators=(",", ":"))
    except Exception:
        serialized = str(tools)
    return _string_tokens(serialized) + (4 * len(tools))


def partition_atomic_message_groups(messages: list[dict]) -> tuple[list[dict], list[list[dict]]]:
    """Partition `messages` into `(leading_system_prefix, atomic_turn_groups)`.

    Invariants enforced:
    1. Leading `system` messages stay untouched at the front (`leading_system_prefix`).
    2. An `assistant` message with `tool_calls` and ALL its corresponding `tool`
       messages (`tool_call_id`) are bound into a single indivisible atomic group.
    3. Any missing `tool` response for a declared `tool_call_id` is synthesized as
       an interrupted placeholder inside the group so providers never reject an
       incomplete `assistant.tool_calls` batch.
    4. Any orphaned `role == "tool"` message lacking a preceding matching
       `assistant.tool_calls` is converted into a plain context note (`role: "user"`)
       so OpenAI/Anthropic/DeepSeek never reject the payload with HTTP 400.
    """
    valid_msgs = [m for m in (messages or []) if isinstance(m, dict)]
    system_prefix: list[dict] = []
    idx = 0
    while idx < len(valid_msgs) and valid_msgs[idx].get("role") == "system":
        # Preserve exact dict reference/content for static prefix stability
        system_prefix.append(valid_msgs[idx])
        idx += 1

    groups: list[list[dict]] = []
    n = len(valid_msgs)
    while idx < n:
        cur = valid_msgs[idx]
        role = str(cur.get("role") or "")
        tcs = cur.get("tool_calls") if role == "assistant" else None
        if role == "assistant" and isinstance(tcs, list) and len(tcs) > 0:
            expected_ids: list[str] = []
            expected_names: dict[str, str] = {}
            normalized_tcs: list[dict] = []
            for tc_pos, tc in enumerate(tcs):
                if not isinstance(tc, dict):
                    continue
                tc_id = str(tc.get("id") or tc.get("toolCallId") or f"call_{idx}_{tc_pos + 1}")
                fn_obj = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                fn_name = str(fn_obj.get("name") or tc.get("name") or "tool")
                expected_ids.append(tc_id)
                expected_names[tc_id] = fn_name
                if "id" in tc and "function" in tc:
                    normalized_tcs.append(tc)
                else:
                    raw_args = fn_obj.get("arguments") if "arguments" in fn_obj else tc.get("args", {})
                    arg_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args or {}, ensure_ascii=False)
                    normalized_tcs.append({
                        "id": tc_id,
                        "type": "function",
                        "function": {"name": fn_name, "arguments": arg_str},
                    })

            if normalized_tcs is not tcs and normalized_tcs != tcs:
                assistant_copy = dict(cur)
                assistant_copy["tool_calls"] = normalized_tcs
            else:
                assistant_copy = cur

            j = idx + 1
            by_id: dict[str, list[dict]] = {}
            extra_tools: list[dict] = []
            expected_set = set(expected_ids)
            while j < n and str(valid_msgs[j].get("role") or "") == "tool":
                t_msg = valid_msgs[j]
                tid = str(t_msg.get("tool_call_id") or "")
                if tid in expected_set:
                    by_id.setdefault(tid, []).append(t_msg)
                elif not tid and len(by_id) < len(expected_ids):
                    inferred_id = expected_ids[len(by_id)]
                    repaired_tool = dict(t_msg)
                    repaired_tool["tool_call_id"] = inferred_id
                    by_id.setdefault(inferred_id, []).append(repaired_tool)
                else:
                    extra_tools.append(t_msg)
                j += 1

            ordered_tools: list[dict] = []
            for exp_id in expected_ids:
                matched_list = by_id.get(exp_id)
                if matched_list:
                    ordered_tools.extend(matched_list)
                else:
                    stub: dict = {
                        "role": "tool",
                        "tool_call_id": exp_id,
                        "content": "[tool result unavailable — stream/turn interrupted]",
                    }
                    if expected_names.get(exp_id):
                        stub["name"] = expected_names[exp_id]
                    ordered_tools.append(stub)

            # Convert any stray unmatched tool messages in the contiguous run into safe notes
            group: list[dict] = [assistant_copy, *ordered_tools]
            groups.append(group)
            for stray in extra_tools:
                stray_txt, _ = _extract_content_text_and_vision_tokens(stray.get("content"))
                if stray_txt.strip():
                    groups.append([{
                        "role": "user",
                        "content": f"[Recovered tool observation]\n{stray_txt.strip()[:1200]}",
                    }])
            idx = j
            continue

        if role == "tool":
            # Orphaned tool message with no preceding assistant.tool_calls -> convert to prevent HTTP 400
            orphan_txt, _ = _extract_content_text_and_vision_tokens(cur.get("content"))
            if orphan_txt.strip():
                groups.append([{
                    "role": "user",
                    "content": f"[Recovered tool observation]\n{orphan_txt.strip()[:1200]}",
                }])
            idx += 1
            continue

        groups.append([cur])
        idx += 1

    return system_prefix, groups


def _one_line_excerpt(text: str, limit: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(24, limit - 1)].rstrip() + "…"


def _summarize_evicted_groups(evicted_groups: list[list[dict]], max_tokens: int) -> dict | None:
    """Build a structured `[Compacted Session Context]` message for evicted older groups."""
    if not evicted_groups:
        return None
    max_chars = max(320, min(14_000, int(max_tokens) * 3))

    carried_prior: list[str] = []
    bullets: list[str] = []
    artifacts: list[str] = []
    seen_artifacts: set[str] = set()

    def _collect_refs(raw_text: str) -> None:
        for rcpt in re.findall(r"\brcpt_[0-9a-f]{8,24}\b", raw_text):
            if rcpt not in seen_artifacts:
                seen_artifacts.add(rcpt)
                artifacts.append(f"receipt:{rcpt}")
        for sp in re.findall(r'spill_id="([^"\s]{1,80})"', raw_text):
            tag = f"spill:{sp}"
            if tag not in seen_artifacts:
                seen_artifacts.add(tag)
                artifacts.append(tag)
        for pmatch in re.findall(r"(?:/home/[^\s`'\"<>():]{3,120}|~/[^\s`'\"<>():]{2,100})", raw_text):
            clean_p = pmatch.rstrip(".,;:")
            if clean_p not in seen_artifacts and len(artifacts) < 16:
                seen_artifacts.add(clean_p)
                artifacts.append(clean_p)

    for grp in evicted_groups:
        if len(grp) > 1 and grp[0].get("role") == "assistant" and grp[0].get("tool_calls"):
            a_msg = grp[0]
            a_txt, _ = _extract_content_text_and_vision_tokens(a_msg.get("content"))
            if a_txt.strip():
                _collect_refs(a_txt)
            tc_summaries: list[str] = []
            tcs = a_msg.get("tool_calls") or []
            tool_by_id = {str(tm.get("tool_call_id") or ""): tm for tm in grp[1:] if isinstance(tm, dict)}
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                tname = str(fn.get("name") or tc.get("name") or "tool")
                tid = str(tc.get("id") or "")
                t_out_msg = tool_by_id.get(tid)
                t_out_txt, _ = _extract_content_text_and_vision_tokens((t_out_msg or {}).get("content"))
                _collect_refs(t_out_txt)
                first_ln = _one_line_excerpt(t_out_txt.splitlines()[0] if t_out_txt.splitlines() else t_out_txt, 140)
                tc_summaries.append(f"`{tname}` -> {first_ln}" if first_ln else f"`{tname}`")
            thought_part = f"{_one_line_excerpt(a_txt, 120)} | " if a_txt.strip() else ""
            bullets.append(f"- [Tool Step] {thought_part}" + "; ".join(tc_summaries[:4]))
            continue

        for msg in grp:
            role = str(msg.get("role") or "user")
            txt, _ = _extract_content_text_and_vision_tokens(msg.get("content"))
            raw_s = txt.strip()
            if not raw_s:
                continue
            _collect_refs(raw_s)
            if raw_s.startswith(COMPACTED_HEADER):
                for ln in raw_s.splitlines()[1:]:
                    ln_s = ln.strip()
                    if ln_s.startswith("- ") and ln_s not in carried_prior:
                        carried_prior.append(ln_s)
                continue
            label = "User" if role == "user" else ("Assistant" if role == "assistant" else role.capitalize())
            bullets.append(f"- [{label}] {_one_line_excerpt(raw_s, 200)}")

    header_lines = [
        f"{COMPACTED_HEADER}",
        f"Summary of {len(evicted_groups)} earlier turn group(s) compacted to fit route context budget:",
    ]
    if artifacts:
        header_lines.append("- [Key Refs] " + ", ".join(artifacts[:12]))

    combined_bullets = carried_prior[-8:] + bullets
    # Keep newest milestones if total bullets exceed max_chars
    header_str = "\n".join(header_lines)
    budget_for_bullets = max(120, max_chars - len(header_str) - 4)
    kept_rev: list[str] = []
    used = 0
    for b in reversed(combined_bullets):
        add = len(b) + (1 if kept_rev else 0)
        if used + add > budget_for_bullets:
            if not kept_rev:
                kept_rev.append(b[:budget_for_bullets])
            break
        kept_rev.append(b)
        used += add
    kept_rev.reverse()
    omitted_bullets = max(0, len(combined_bullets) - len(kept_rev))
    if omitted_bullets > 0:
        kept_rev.insert(0, f"- [... {omitted_bullets} earlier summary item(s) condensed ...]")

    content = header_str + ("\n" + "\n".join(kept_rev) if kept_rev else "")
    return {"role": "user", "content": content}


def validate_tool_pair_integrity(messages: list[dict]) -> tuple[bool, str]:
    """Verify no `role == 'tool'` message is orphaned and every `assistant.tool_calls` has matching `tool` replies."""
    idx = 0
    n = len(messages)
    while idx < n:
        msg = messages[idx]
        role = str(msg.get("role") or "")
        if role == "tool":
            return False, f"Orphaned tool message at index {idx} (tool_call_id={msg.get('tool_call_id')!r})"
        tcs = msg.get("tool_calls") if role == "assistant" else None
        if role == "assistant" and isinstance(tcs, list) and len(tcs) > 0:
            expected = [
                str(tc.get("id") or tc.get("toolCallId") or "")
                for tc in tcs
                if isinstance(tc, dict)
            ]
            if any(not x for x in expected):
                return False, f"Assistant tool_calls missing id at index {idx}"
            j = idx + 1
            seen: list[str] = []
            while j < n and str(messages[j].get("role") or "") == "tool":
                tid = str(messages[j].get("tool_call_id") or "")
                if tid not in expected:
                    return False, f"Unmatched tool_call_id={tid!r} at index {j}"
                seen.append(tid)
                j += 1
            for exp_id in expected:
                if exp_id not in seen:
                    return False, f"Missing tool result for tool_call_id={exp_id!r} from assistant at index {idx}"
            idx = j
            continue
        idx += 1
    return True, ""


def compact_messages_for_route(
    messages: list[dict],
    provider: str = "",
    model: str = "",
    endpoint: str = "",
    reserved_output: int = DEFAULT_RESERVED_OUTPUT,
    context_window: int | None = None,
    tools: list[dict] | None = None,
    llm_summarize_fn: Any = None,
) -> tuple[list[dict], dict]:
    """Compact `messages` when estimated tokens exceed the `(provider, model)` route `input_budget`.

    Guarantees:
    - Static leading `system` messages are preserved byte-for-byte.
    - Tool-schema token overhead (`tools`) is deducted from the route's input budget.
    - Recent turns fitting within ~60% of `input_budget` are kept intact.
    - `assistant(tool_calls)` <-> `tool(tool_call_id)` groups are NEVER split.
    """
    w = resolve_route_context_window(
        provider=provider,
        model=model,
        endpoint=endpoint,
        explicit_context_window=context_window,
    )
    tool_schema_tokens = estimate_tools_tokens(tools)
    base_budget = compute_input_budget(w, reserved_output=reserved_output)
    input_budget = max(512, base_budget - tool_schema_tokens)
    system_prefix, groups = partition_atomic_message_groups(messages)
    repaired_flat = system_prefix + [m for g in groups for m in g]
    tokens_before = estimate_messages_tokens(repaired_flat)

    if tokens_before <= input_budget or len(groups) <= 1:
        return repaired_flat, {
            "compacted": False,
            "provider": str(provider or ""),
            "model": str(model or ""),
            "contextWindow": w,
            "reservedOutput": int(reserved_output or 0),
            "toolSchemaTokens": tool_schema_tokens,
            "inputBudget": input_budget,
            "estimatedTokensBefore": tokens_before,
            "estimatedTokensAfter": tokens_before,
            "evictedGroupCount": 0,
            "retainedGroupCount": len(groups),
        }

    sys_tokens = estimate_messages_tokens(system_prefix)
    available_after_sys = max(256, input_budget - sys_tokens)
    # Target ~60% of input_budget for recent verbatim turns while leaving room for summary
    summary_reserve = min(640, max(160, available_after_sys // 5))
    recent_target_budget = min(
        max(256, int(input_budget * RECENT_BUDGET_RATIO)),
        max(256, available_after_sys - summary_reserve),
    )

    group_tokens = [estimate_messages_tokens(g) for g in groups]
    retained_rev: list[list[dict]] = []
    retained_tokens = 0
    split_idx = len(groups)

    for g_idx in range(len(groups) - 1, -1, -1):
        gtok = group_tokens[g_idx]
        if not retained_rev:
            # Always keep at least the latest atomic group so current user prompt or active tool batch survives
            retained_rev.append(groups[g_idx])
            retained_tokens += gtok
            split_idx = g_idx
            continue
        if retained_tokens + gtok <= recent_target_budget:
            retained_rev.append(groups[g_idx])
            retained_tokens += gtok
            split_idx = g_idx
        else:
            break

    retained_rev.reverse()
    evicted_groups = groups[:split_idx]
    if not evicted_groups:
        return repaired_flat, {
            "compacted": False,
            "provider": str(provider or ""),
            "model": str(model or ""),
            "contextWindow": w,
            "reservedOutput": int(reserved_output or 0),
            "toolSchemaTokens": tool_schema_tokens,
            "inputBudget": input_budget,
            "estimatedTokensBefore": tokens_before,
            "estimatedTokensAfter": tokens_before,
            "evictedGroupCount": 0,
            "retainedGroupCount": len(groups),
        }

    remaining_for_summary = max(160, input_budget - sys_tokens - retained_tokens)
    summary_msg = None
    if callable(llm_summarize_fn):
        try:
            llm_summary = str(llm_summarize_fn(evicted_groups, remaining_for_summary) or "").strip()
            if llm_summary:
                summary_msg = {
                    "role": "user",
                    "content": f"{COMPACTED_HEADER}\n{llm_summary[:max(160, remaining_for_summary * 3)]}",
                }
        except Exception:
            summary_msg = None
    if summary_msg is None:
        summary_msg = _summarize_evicted_groups(evicted_groups, max_tokens=remaining_for_summary)

    compacted_list: list[dict] = list(system_prefix)
    if summary_msg is not None:
        compacted_list.append(summary_msg)
    for grp in retained_rev:
        compacted_list.extend(grp)

    tokens_after = estimate_messages_tokens(compacted_list)
    return compacted_list, {
        "compacted": True,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "contextWindow": w,
        "reservedOutput": int(reserved_output or 0),
        "toolSchemaTokens": tool_schema_tokens,
        "inputBudget": input_budget,
        "estimatedTokensBefore": tokens_before,
        "estimatedTokensAfter": tokens_after,
        "evictedGroupCount": len(evicted_groups),
        "retainedGroupCount": len(retained_rev),
    }


# ---------------------------------------------------------------------------
# Upgrade #3: Non-Waking Session `context_inbox` (Prompt Prefix Cache safe)
# ---------------------------------------------------------------------------


def context_inbox_dir(create: bool = False) -> Path:
    explicit = os.environ.get("ALIS_AQUA_CONTEXT_INBOX_DIR", "").strip()
    folder = Path(explicit).expanduser() if explicit else (spill_store.companion_state_dir() / "context_inbox")
    if create:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        st = folder.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise RuntimeError(f"Unsafe context_inbox directory: {folder}")
        if st.st_uid == os.getuid():
            os.chmod(folder, 0o700)
    return folder


def _inbox_paths(session_id: str) -> tuple[Path, Path]:
    safe_sid = spill_store.sanitize_id(session_id or "chat_default", fallback="chat_default")
    folder = context_inbox_dir(create=True)
    return folder / f"{safe_sid}.json", folder / f".{safe_sid}.lock"


def _normalize_context_value(content) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, (dict, list)):
        text = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(content if content is not None else "").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text[:2400]


def _read_inbox_unlocked(data_path: Path) -> list[dict]:
    if not data_path.exists():
        return []
    st = data_path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return []
    try:
        raw = json.loads(data_path.read_text(encoding="utf-8"))
        items = raw.get("items") if isinstance(raw, dict) else raw
        if isinstance(items, list):
            return [it for it in items if isinstance(it, dict)]
    except Exception:
        return []
    return []


def _write_inbox_unlocked(data_path: Path, items: list[dict]) -> None:
    if not items:
        try:
            data_path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    payload = json.dumps({"version": 1, "items": items}, ensure_ascii=False).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".ctx_tmp_", suffix=".tmp", dir=str(data_path.parent))
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp_path, data_path)
        os.chmod(data_path, 0o600)
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def enqueue_context_update(
    session_id: str,
    kind: str,
    content,
    dedupe_key: str | None = None,
    now_ms: int | None = None,
) -> dict:
    """Queue a non-waking ambient/background context update in the session inbox.

    Deduplicates in-place by `dedupe_key` (defaulting to `kind`) so repeated
    ambient signals (`mood_checkin`, `active_window`, `nexus_note`, etc.) never
    accumulate stale duplicates and never wake the LLM or mutate system prompts.
    """
    clean_kind = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(kind or "context").strip())[:64] or "context"
    eff_key = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(dedupe_key or clean_kind).strip())[:80] or clean_kind
    norm_val = _normalize_context_value(content)
    ts_ms = int(now_ms if isinstance(now_ms, int) and now_ms > 0 else time.time() * 1000)

    data_path, lock_path = _inbox_paths(session_id)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        items = _read_inbox_unlocked(data_path)
        if not norm_val:
            items = [it for it in items if str(it.get("dedupeKey") or it.get("kind") or "") != eff_key]
            _write_inbox_unlocked(data_path, items)
            return {"queued": False, "removed": True, "dedupeKey": eff_key, "pendingCount": len(items)}

        entry = {
            "kind": clean_kind,
            "dedupeKey": eff_key,
            "content": norm_val,
            "updatedAtMs": ts_ms,
        }
        replaced = False
        next_items: list[dict] = []
        for it in items:
            if str(it.get("dedupeKey") or it.get("kind") or "") == eff_key:
                next_items.append(entry)
                replaced = True
            else:
                next_items.append(it)
        if not replaced:
            next_items.append(entry)
        # Bound inbox to 32 distinct keys
        next_items = next_items[-32:]
        _write_inbox_unlocked(data_path, next_items)
        return {
            "queued": True,
            "deduplicated": replaced,
            "dedupeKey": eff_key,
            "kind": clean_kind,
            "pendingCount": len(next_items),
        }
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def peek_context_updates(session_id: str) -> list[dict]:
    """Inspect pending non-waking context updates without draining them."""
    folder = context_inbox_dir(create=False)
    safe_sid = spill_store.sanitize_id(session_id or "chat_default", fallback="chat_default")
    data_path = folder / f"{safe_sid}.json"
    if not data_path.exists():
        return []
    _, lock_path = _inbox_paths(session_id)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_SH)
        return _read_inbox_unlocked(data_path)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def format_context_updates_block(items: list[dict]) -> str:
    """Render pending inbox items into a single compact `<context_update>...</context_update>` block."""
    lines: list[str] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        k = str(it.get("kind") or it.get("dedupeKey") or "context").strip()
        val = str(it.get("content") or "").strip()
        if not val:
            continue
        lines.append(f"- [{k}] {val}")
    if not lines:
        return ""
    return "<context_update>\n" + "\n".join(lines) + "\n</context_update>"


def drain_context_updates(session_id: str) -> str:
    """Atomically drain all queued updates for `session_id` into a `<context_update>` block."""
    folder = context_inbox_dir(create=False)
    safe_sid = spill_store.sanitize_id(session_id or "chat_default", fallback="chat_default")
    data_path = folder / f"{safe_sid}.json"
    if not data_path.exists():
        return ""
    _, lock_path = _inbox_paths(session_id)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        items = _read_inbox_unlocked(data_path)
        _write_inbox_unlocked(data_path, [])
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
    return format_context_updates_block(items)


def clear_context_updates(session_id: str) -> int:
    """Discard pending context updates for `session_id` and return removed count."""
    folder = context_inbox_dir(create=False)
    safe_sid = spill_store.sanitize_id(session_id or "chat_default", fallback="chat_default")
    data_path = folder / f"{safe_sid}.json"
    if not data_path.exists():
        return 0
    _, lock_path = _inbox_paths(session_id)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        items = _read_inbox_unlocked(data_path)
        _write_inbox_unlocked(data_path, [])
        return len(items)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def inject_context_update_message(messages: list[dict], context_block: str) -> list[dict]:
    """Inject a drained `<context_update>` block at a valid pre-step boundary.

    - NEVER modifies `messages[0]` or any `system` prompt (preserving Prompt Prefix Cache).
    - NEVER splits an `assistant(tool_calls)` message from its `tool` responses.
    - Inserts right before the trailing `user` message (for a new user turn) or
      appends after the completed `tool` result batch (for the next agent loop step).
    """
    block = str(context_block or "").strip()
    if not block:
        return list(messages or [])
    out = list(messages or [])
    ctx_msg = {"role": "user", "content": block}
    if not out:
        return [ctx_msg]
    # Avoid duplicate injection if the exact block is already present at the tail
    for tail_msg in out[-2:]:
        if isinstance(tail_msg, dict) and tail_msg.get("content") == block:
            return out
    if isinstance(out[-1], dict) and out[-1].get("role") == "user":
        insert_at = len(out) - 1
        out.insert(insert_at, ctx_msg)
        return out
    # If the last message is an assistant with tool_calls (no tool results yet), do NOT insert after it;
    # insert right before that assistant message instead. Otherwise append at end (e.g. after tool results).
    if isinstance(out[-1], dict) and out[-1].get("role") == "assistant" and out[-1].get("tool_calls"):
        out.insert(len(out) - 1, ctx_msg)
        return out
    out.append(ctx_msg)
    return out


def drain_and_inject_context(messages: list[dict], session_id: str) -> tuple[list[dict], str]:
    """Drain `session_id` inbox once and inject `<context_update>` if non-empty."""
    drained = drain_context_updates(session_id)
    if not drained:
        return list(messages or []), ""
    return inject_context_update_message(messages, drained), drained
