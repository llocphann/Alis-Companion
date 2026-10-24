"""Bounded public-output verification before Companion local chat persistence.

The QML shared-model route uses services/WullReplyGuard.js. Keep protocol
sentinel fixtures aligned. No model calls, local data access or extra inference.
"""
import re

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


def public_text(value):
    if not isinstance(value, str):
        raise UnsafeReply("invalid_text")
    # Inspect all normalized bytes before truncation can hide an envelope.
    candidate = _CONTROL.sub("", value)
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
