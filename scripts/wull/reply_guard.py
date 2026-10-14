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
