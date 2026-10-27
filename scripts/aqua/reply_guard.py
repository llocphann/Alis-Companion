"""Bounded public-output verification before Companion local chat persistence.

The QML shared-model route uses services/AquaReplyGuard.js. Keep protocol
sentinel fixtures aligned. No model calls, local data access or extra inference.
"""
import re
import unicodedata

_SENTINEL = re.compile(
    r"</?\s*(?:think(?:ing)?|analysis|tool(?:_call|_result)?|function_call|system|assistant|developer)\b"
    r"|<[|｜]|\[\s*/?\s*INST\s*\]|<<\s*/?\s*SYS\s*>>"
    r"|</?\s*(?:start_of_turn|end_of_turn|bos|eos)\s*>"
    r"|\[\[\s*Function\s*:", re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MIXED_METADATA = re.compile(
    r"""\{\s*["']?(?:text|expression|tool_calls|function_call)["']?\s*:"""
    r"""|\bexpression\s*:\s*["']?[a-z]+["']?\s*[}.,]?\s*$""", re.IGNORECASE)
_ACTION_CLAIM = re.compile(
    r"\bI(?:['’](?:ve|ll|m))?(?:\s+(?:have|already|just|will|can|decide\s+to|decided\s+to|am\s+going\s+to|going\s+to)){0,3}\s+"
    r"(?:change[ds]?|delet(?:e[ds]?|ing)|rearrang(?:e[ds]?|ing)|install(?:ed)?|open(?:ed)?|save[ds]?|schedul(?:e[ds]?|ing)|send|sent|writ(?:e|ten)|wrote|updat(?:e[ds]?|ing)|run|ran|execut(?:e[ds]?|ing))\s+"
    r"(?:(?:your|the|a|an|this|that|my|these|those)\s+)?"
    r"(?:desktop|wallpaper|files?|folders?|icons?|settings?|apps?|programs?|scripts?|commands?|reminders?|appointments?|messages?|memory|memories)\b", re.IGNORECASE)
EXPRESSIONS = frozenset(("idle", "happy", "excited", "thinking", "working",
                         "surprised", "sleepy", "sad", "alert"))


class UnsafeReply(ValueError):
    pass


PRIVACY_NOTICE = ("Nothing was deleted. Alis can store chats locally; use Clear conversation "
                  "to clear them or memory controls to forget saved memories. "
                  "Obsidian copies and backups may remain.")
_EN_DELETION = re.compile(
    r"^(?:(?:please|can you|could you|would you|will you|i want you to|i would like you to)\s+){0,2}"
    r"(forget|delete|erase|clear|remove|wipe)\s+(.+)$")
_EN_HISTORY = re.compile(
    r"^(?:(?:all(?: of)?|my|our|your|the|this|these|those|previous|old|entire|saved|local|current|past|earlier)\s+){0,5}"
    r"(?:chats?|conversations?|history|messages?|memories|memory|(?:what|everything|anything) (?:i|we) (?:said|told|wrote|shared|asked))\b")
_VI_DELETION = re.compile(
    r"^(?:(?:hay|vui long|lam on|ban co the|ban hay|xin)\s+){0,2}(quen|xoa)\s+(.+)$")
_VI_HISTORY = re.compile(
    r"^(?:(?:tat ca|het|cac|nhung|toan bo|cu|da luu|truoc do)\s+){0,5}"
    r"(?:doan chat|cuoc tro chuyen|tin nhan|lich su|ky uc|bo nho|thong tin da luu|nhung gi (?:toi|chung ta) (?:da )?(?:noi|viet|chia se))\b")


def privacy_reply(prompt):
    """Bounded explicit EN/VN deletion intents; never execute a deletion."""
    if not isinstance(prompt, str) or len(prompt) > 1200:
        return None
    normalized = unicodedata.normalize("NFD", prompt.strip().lower())
    normalized = re.sub(r"[\u0300-\u036f]", "", normalized).replace("đ", "d")
    normalized = re.sub(r"\s+", " ", normalized)
    en = _EN_DELETION.match(normalized)
    vi = _VI_DELETION.match(normalized)
    requested = (en and (_EN_HISTORY.match(en[2]) or
        (en[1] == "forget" and re.match(r"^(?:everything|anything|all|me)\b", en[2])))) or (
        vi and (_VI_HISTORY.match(vi[2]) or
        (vi[1] == "xoa" and vi[2].startswith("bo ") and _VI_HISTORY.match(vi[2][3:])) or
        (vi[1] == "quen" and re.match(r"^(?:tat ca|het|moi thu|toi)\b", vi[2]))))
    return {"text": PRIVACY_NOTICE, "expression": "idle"} if requested else None


def chat_reply(value, prompt):
    guarded = public_reply(value)
    return privacy_reply(prompt) or guarded


def public_text(value):
    if not isinstance(value, str):
        raise UnsafeReply("invalid_text")
    # Inspect all normalized bytes before truncation can hide an envelope.
    candidate = re.sub(r"\r?\n?__ALIS_HTTP_STATUS__:\d+\s*$", "", _CONTROL.sub("", value))
    if _SENTINEL.search(candidate):
        raise UnsafeReply("internal_protocol")
    if _MIXED_METADATA.search(candidate):
        raise UnsafeReply("malformed_structured_output")
    if _ACTION_CLAIM.search(candidate):
        raise UnsafeReply("unverified_action_claim")
    text = candidate.strip()[:420]
    if not text:
        raise UnsafeReply("empty_text")
    return text


def public_reply(value):
    """Validate a parsed model envelope without treating tool fields as prose."""
    if not isinstance(value, dict) or value.keys() - {"text", "expression"}:
        raise UnsafeReply("invalid_response_object")
    text = public_text(value.get("text"))
    expression = value.get("expression", "idle")
    if not isinstance(expression, str) or expression not in EXPRESSIONS:
        expression = "idle"
    return {"text": text, "expression": expression}
