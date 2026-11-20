#!/usr/bin/env python3
"""Canonical message prompt assembler with static system prefix & untrusted data isolation.

Guarantees:
  1. `messages[0]` is strictly static trusted instructions (persona, user system prompt,
     trusted rules, project instructions) — byte-identical across turns for KV cache reuse.
  2. Volatility (`<environment_delta>`) and retrieved / external tool content
     (`<untrusted_data source="...">`) live ONLY in the final user turn, never in `messages[0]`.
  3. Adjacent `user` messages are coalesced cleanly so strict chat templates never choke
     on consecutive user turns.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _escape_tag_boundaries(text: str, tag_name: str) -> str:
    raw = str(text or "")
    return re.sub(rf"</?\s*{re.escape(tag_name)}\b[^>]*>", f"[{tag_name}]", raw, flags=re.IGNORECASE)


def wrap_env_delta(env_delta: str | dict | None) -> str:
    if isinstance(env_delta, dict):
        body = json.dumps(env_delta, ensure_ascii=False, separators=(",", ":"))
    else:
        body = str(env_delta or "").strip()
    if not body:
        return ""
    safe = _escape_tag_boundaries(body, "environment_delta")
    return f"<environment_delta>\n{safe[:2400]}\n</environment_delta>"


def wrap_untrusted_blocks(blocks: list[tuple[str, str]] | None) -> str:
    if not blocks:
        return ""
    rendered: list[str] = []
    for source, text in blocks:
        body = str(text or "").strip()
        if not body:
            continue
        src_clean = re.sub(r'["<>\r\n]+', " ", str(source or "external")).strip()[:64] or "external"
        safe = _escape_tag_boundaries(body, "untrusted_data")
        rendered.append(f'<untrusted_data source="{src_clean}">\n{safe[:4800]}\n</untrusted_data>')
    return "\n\n".join(rendered)


def _merge_content(a: Any, b: Any) -> Any:
    if isinstance(a, str) and isinstance(b, str):
        return f"{a.rstrip()}\n\n{b.lstrip()}" if a.strip() and b.strip() else (a or b)
    parts: list[dict] = []
    for item in (a, b):
        if isinstance(item, list):
            parts.extend(p for p in item if isinstance(p, dict))
        elif isinstance(item, str) and item.strip():
            parts.append({"type": "text", "text": item})
    return parts


def coalesce_user_messages(messages: list[dict]) -> list[dict]:
    """Merge adjacent `user` turns outside leading `system` messages."""
    out: list[dict] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role == "user" and out and out[-1].get("role") == "user":
            merged = dict(out[-1])
            merged["content"] = _merge_content(out[-1].get("content"), msg.get("content"))
            out[-1] = merged
        else:
            out.append(dict(msg))
    return out


def build_static_system_prompt(
    *,
    system_persona: str,
    trusted_system_prompt: str = "",
    trusted_rules: str = "",
    active_project: dict | None = None,
    max_chars: int = 4000,
) -> str:
    sections: list[str] = []
    persona = str(system_persona or "").strip()
    if persona:
        sections.append(persona)
    custom = str(trusted_system_prompt or "").strip()
    if custom and custom not in persona:
        sections.append(custom)
    rules = str(trusted_rules or "").strip()
    if rules and rules not in persona and rules not in custom:
        sections.append(f"## Active Rules\n{rules[:1200]}")
    if isinstance(active_project, dict) and active_project.get("name"):
        pname = str(active_project.get("name") or "").strip()
        pws = str(active_project.get("workspacePath") or "").strip()
        pinst = str(active_project.get("instructions") or "").strip()
        proj_line = f"## Active Project: {pname}" + (f" (`{pws}`)" if pws else "")
        if pinst:
            proj_line += f"\n{pinst[:800]}"
        sections.append(proj_line)
    return "\n\n".join(sections)[:max_chars].strip()


def assemble_messages(
    *,
    system_persona: str,
    trusted_system_prompt: str = "",
    trusted_rules: str = "",
    active_project: dict | None = None,
    history: list[dict] | None = None,
    user_prompt: str = "",
    env_delta: str | dict | None = "",
    untrusted_blocks: list[tuple[str, str]] | None = None,
    directive_prefix: str = "",
    system_max_chars: int = 4000,
) -> list[dict]:
    """Assemble `[system_prefix, ...history, final_user_turn]` cleanly and KV-cache safely."""
    sys_text = build_static_system_prompt(
        system_persona=system_persona,
        trusted_system_prompt=trusted_system_prompt,
        trusted_rules=trusted_rules,
        active_project=active_project,
        max_chars=system_max_chars,
    )
    msgs: list[dict] = [{"role": "system", "content": sys_text}]

    tail: list[dict] = []
    for hm in (history or []):
        if isinstance(hm, dict) and hm.get("role") in ("user", "assistant", "tool"):
            tail.append(dict(hm))

    user_parts: list[str] = []
    prefix = str(directive_prefix or "").strip()
    prompt_body = str(user_prompt or "").strip()
    if prefix and prefix not in prompt_body:
        user_parts.append(f"{prefix}\n{prompt_body}" if prompt_body else prefix)
    elif prompt_body:
        user_parts.append(prompt_body)

    env_block = wrap_env_delta(env_delta)
    if env_block:
        user_parts.append(env_block)

    untrusted_md = wrap_untrusted_blocks(untrusted_blocks)
    if untrusted_md:
        user_parts.append(untrusted_md)

    final_user_content = "\n\n".join(p for p in user_parts if p).strip()
    if final_user_content:
        tail.append({"role": "user", "content": final_user_content})

    return msgs + coalesce_user_messages(tail)
