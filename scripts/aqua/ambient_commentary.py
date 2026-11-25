#!/usr/bin/env python3
"""Ephemeral, low-overhead LLM ambient mascot commentary for Alis-Companion.

Generates brief, context-aware, non-repetitive desktop remarks for the small
Mascot talk bubble WITHOUT writing to chat history, SQLite databases, or TTS.

Resource, Diversity & Privacy Guarantees:
  1. Never cold-loads an unloaded local model (probes /api/v0/models or /api/ps first)
     and prefers the smallest already-warm micro-LLM / micro-VLM when multiple models
     are loaded.
  2. Tier 1 (default): single non-thinking micro-LLM text call (`max_tokens <= 60`)
     using sanitized Niri active window, Pomodoro, media, weather, time, and mood cues.
  3. Tier 2 (opt-in `visualPeekEnabled`, default OFF): when enabled and a warm VLM is
     available, captures an ephemeral <=480px JPEG in `/tmp`, checks a 64-bit perceptual
     dHash delta against `lastVisualHash` so unchanged screens skip redundant vision
     tokens, anchors on a concrete `scene` cue, and unlinks the temp file in `finally:`.
  4. Diversity & anti-repetition engine: rotates 8 creative persona angles (never
     repeating the last 3), bans recent opening stems, checks word + char 4-gram
     similarity against recent remarks, performs at most 1 novelty retry if a tiny model
     repeats itself, and provides rich bilingual (VI + EN) randomized fallbacks.
  5. Never fires on sensitive apps/titles (password managers, incognito, banking,
     OTP/login, secrets, lockscreens, fullscreen games/video) or low battery / power-saver.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_EXPRESSIONS = ("calm", "happy", "curious", "sleepy", "excited", "working")
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_SYS_RNG = random.SystemRandom()

# 8 rotating persona angles to prevent tiny LLMs from collapsing into one repetitive template.
CREATIVE_ANGLES: tuple[tuple[str, str, str], ...] = (
    ("visual_detail", "curious", "Focus on ONE concrete detail of what the user is working on or viewing right now."),
    ("playful_aside", "happy", "Make a light, playful, slightly witty remark tied to the current app or activity."),
    ("curious_question", "curious", "Ask ONE brief, warm, low-pressure question about what caught their attention."),
    ("ambience_vibe", "calm", "Blend the workspace mood with time of day, weather, or playing music naturally."),
    ("gentle_care", "calm", "Weave in a subtle, non-preachy micro-care nudge (eyes, water, posture, breather)."),
    ("momentum_cheer", "excited", "Offer a short, genuine burst of encouragement for their current focus or streak."),
    ("quiet_company", "calm", "Express warm, unobtrusive presence from the screen edge while they stay in flow."),
    ("liquid_metaphor", "happy", "Use a subtle, fresh water/ripple/tide image connected naturally to their task."),
)


def _urlopen_local(req_or_url, timeout: float = 5.0):
    target_url = req_or_url.full_url if hasattr(req_or_url, "full_url") else str(req_or_url or "")
    host = (urllib.parse.urlparse(target_url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return _NO_PROXY_OPENER.open(req_or_url, timeout=timeout)
    return urllib.request.urlopen(req_or_url, timeout=timeout)


_SENSITIVE_KEYWORDS = (
    "bitwarden",
    "keepass",
    "1password",
    "lastpass",
    "dashlane",
    "enpass",
    "seahorse",
    "polkit",
    "pinentry",
    "keyring",
    "incognito",
    "inprivate",
    "private browsing",
    "private window",
    "an danh",
    "internet banking",
    "ngan hang",
    "vietcombank",
    "techcombank",
    "tpbank",
    "mbbank",
    "vpbank",
    "momo",
    "zalopay",
    "paypal",
    "stripe",
    "authenticator",
    "passkey",
    "2fa",
    "totp",
    "one-time password",
    "sign in",
    "log in",
    "hyprlock",
    "swaylock",
    "gtklock",
    "id_rsa",
    "id_ed25519",
    ".env",
    "credentials",
    "secret",
)

_SENSITIVE_WORD_RE = re.compile(
    r"\b(?:otp|login|password|passwd|pin|token|banking|wallet)\b"
    r"|\b(?:password|key|secret|credential|bitwarden|keepass|1password)\s+vault\b",
    re.IGNORECASE,
)

_MEDIA_GAME_APPS = frozenset({
    "mpv",
    "vlc",
    "steam_app_0",
    "gamescope",
    "lutris",
    "heroic",
    "retroarch",
})

_OPAQUE_GENERIC_TITLES = frozenset({
    "",
    "new tab",
    "tab moi",
    "untitled",
    "start page",
    "home",
    "firefox",
    "zen",
    "zen browser",
    "chromium",
    "google chrome",
    "brave",
    "blender",
    "gimp",
    "inkscape",
    "krita",
    "code",
    "foot",
    "kitty",
    "alacritty",
    "wezterm",
})

_MODEL_SIZE_RE = re.compile(r"(?:^|[^0-9a-z])(\d+(?:\.\d+)?)\s*b(?:[^a-z0-9]|$)", re.IGNORECASE)


_VIETNAMESE_TEXT_RE = re.compile(
    r"[ăâđêôơưằắẳẵặầấẩẫậềếểễệờớởỡợừứửữựảãạẻẽẹỉĩịỏõọủũụỳỷỹỵ]",
    re.IGNORECASE,
)


def _contains_vietnamese_text(text: str) -> bool:
    """Detect Vietnamese-specific diacritic letters while tolerating English loanwords like café/naïve."""
    s = unicodedata.normalize("NFC", str(text or ""))
    return bool(_VIETNAMESE_TEXT_RE.search(s))


def _fold_ascii(text: str) -> str:
    norm = unicodedata.normalize("NFD", str(text or ""))
    stripped = "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D").lower().strip()


def _estimate_model_billions(model_id: str) -> float:
    low = str(model_id or "").strip().lower()
    m = _MODEL_SIZE_RE.search(low)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    if any(tok in low for tok in ("moondream", "smolvlm", "fastvlm", "florence", "tiny", "nano", "micro")):
        return 1.5
    if any(tok in low for tok in ("mini", "small", "flash", "haiku")):
        return 3.5
    return 99.0


def _is_vlm_name(model_id: str, model_type: str = "") -> bool:
    if str(model_type or "").strip().lower() == "vlm":
        return True
    low = str(model_id or "").lower()
    if any(tok in low for tok in ("gemma-3-1b", "gemma3-1b", "gemma-3:1b", "gemma3:1b", "gemma-3-270m", "gemma3-270m")):
        return False
    return any(
        tok in low
        for tok in (
            "-vl",
            "vl-",
            "vision",
            "qwen3.5",
            "qwen3_5",
            "qwen3.6",
            "qwen3_6",
            "qwen3-vl",
            "qwen2-vl",
            "qwen2.5-vl",
            "llava",
            "gemma-3",
            "gemma3",
            "pixtral",
            "minicpm",
            "internvl",
            "smolvlm",
            "moondream",
            "fastvlm",
            "florence",
            "paligemma",
            "phi-3.5-vision",
            "phi-4-multimodal",
        )
    )


def _uses_reason_none_directive(model_id: str) -> bool:
    low = str(model_id or "").lower()
    return any(tok in low for tok in ("qwen", "deepseek", "think", "reason", "qwq", "-r1"))


def _is_embedding_model(model_id: str, model_type: str = "") -> bool:
    t_low = str(model_type or "").strip().lower()
    m_low = str(model_id or "").strip().lower()
    return t_low in ("embeddings", "embedding") or "embed" in m_low


def is_sensitive_context(app_id: str, title: str, fullscreen: bool = False) -> bool:
    """Return True if the active window should suppress ambient mascot inspection."""
    if fullscreen:
        return True
    app_f = _fold_ascii(app_id)
    title_f = _fold_ascii(title)
    combined = f"{app_f} {title_f}".strip()
    if app_f in _MEDIA_GAME_APPS or app_f.startswith("steam_app_"):
        return True
    for kw in _SENSITIVE_KEYWORDS:
        if kw in combined:
            return True
    if _SENSITIVE_WORD_RE.search(combined):
        return True
    return False


def is_opaque_title(app_id: str, title: str) -> bool:
    """Return True if app_id/title carries too little semantic context for text-only Tier 1."""
    app_f = _fold_ascii(re.sub(r"\.(?:desktop|exe|bin)$", "", str(app_id or ""), flags=re.I))
    title_f = _fold_ascii(re.sub(r"[—–\-|·]+", " ", str(title or "")))
    if not title_f or title_f in _OPAQUE_GENERIC_TITLES:
        return True
    if title_f == app_f:
        return True
    return len(title_f) <= 3


def sanitize_desktop_context(ctx: dict | None) -> dict:
    """Strip sensitive tokens and bound field sizes before sending context to an LLM."""
    raw = ctx if isinstance(ctx, dict) else {}

    def _clean(val: object, limit: int = 80) -> str:
        s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(val or "")).strip()
        s = re.sub(r"\b[A-Za-z0-9_\-]{28,}\b", "[id]", s)
        s = " ".join(s.split())
        return s[:limit]

    dwell_raw = raw.get("dwellMinutes", raw.get("dwellMin", 0))
    try:
        dwell_min = max(0, min(720, int(round(float(dwell_raw or 0)))))
    except (TypeError, ValueError):
        dwell_min = 0

    out = {
        "focusedApp": _clean(raw.get("focusedApp"), 48),
        "focusedTitle": _clean(raw.get("focusedTitle"), 84),
        "dwellMinutes": dwell_min,
        "music": _clean(raw.get("music"), 60),
        "pomodoro": _clean(raw.get("pomodoro"), 44),
        "weather": _clean(raw.get("weather"), 60),
        "battery": _clean(raw.get("battery"), 32),
        "timeOfDay": _clean(raw.get("timeOfDay") or raw.get("timeText"), 24),
        "mood": _clean(raw.get("mood"), 20),
        "energy": _clean(raw.get("energy"), 20),
    }
    return {k: v for k, v in out.items() if v or (k == "dwellMinutes" and v > 0)}


def _normalize_chat_endpoint(endpoint: str) -> str:
    ep = str(endpoint or "").strip().rstrip("/")
    if not ep:
        return ""
    if ep.endswith("/chat/completions") or ep.endswith("/completions"):
        return ep
    if ep.endswith("/v1"):
        return ep + "/chat/completions"
    if "/v1/" in ep:
        return ep
    return ep + "/v1/chat/completions"


def _select_best_warm_candidate(
    candidates: list[tuple[str, bool]],
    preferred_model: str = "",
    *,
    prefer_vision: bool = False,
) -> tuple[str, bool]:
    """Pick the smallest/fastest already-warm candidate (preferring tiny VLMs when visual peek is enabled)."""
    if not candidates:
        return "", False
    if len(candidates) == 1:
        return candidates[0]

    pool = candidates
    if prefer_vision:
        vlms = [c for c in candidates if c[1]]
        if vlms:
            pool = vlms

    # Prefer a warm micro-model (<= 4.5B) over a large chat/thinking model when multiple models are loaded.
    ranked = sorted(
        pool,
        key=lambda item: (
            0 if _estimate_model_billions(item[0]) <= 4.5 else 1,
            _estimate_model_billions(item[0]),
            0 if (preferred_model and item[0] == preferred_model) else 1,
        ),
    )
    return ranked[0]


def probe_warm_local_model(
    endpoint: str,
    preferred_model: str = "",
    *,
    provider_hint: str = "",
    allow_cold_load: bool = False,
    prefer_vision: bool = False,
    timeout: float = 0.6,
) -> tuple[bool, str, bool]:
    """Check whether an already-warm model is ready without triggering cold multi-GB loads."""
    ep = _normalize_chat_endpoint(endpoint)
    pref = re.sub(r"^(?:lmstudio-|ollama-|gguf:)", "", str(preferred_model or "").strip())
    if not ep:
        return False, "", False
    parsed = urllib.parse.urlparse(ep)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    hint = str(provider_hint or "").strip().lower()

    # LM Studio native model-state check (prevents triggering cold weight load while idle)
    if host in ("127.0.0.1", "localhost", "::1") and (port == 1234 or hint == "lmstudio") and not allow_cold_load:
        try:
            v0_url = f"{parsed.scheme or 'http'}://{parsed.netloc}/api/v0/models"
            with _urlopen_local(v0_url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            items = data.get("data") if isinstance(data, dict) else []
            loaded = [
                (str(it.get("id") or "").strip(), _is_vlm_name(str(it.get("id") or ""), str(it.get("type") or "")))
                for it in (items if isinstance(items, list) else [])
                if isinstance(it, dict)
                and str(it.get("state") or "").lower() == "loaded"
                and str(it.get("id") or "").strip()
                and not _is_embedding_model(str(it.get("id") or ""), str(it.get("type") or ""))
            ]
            if not loaded:
                return False, "", False
            cid, is_vlm = _select_best_warm_candidate(loaded, pref, prefer_vision=prefer_vision)
            return True, cid, is_vlm
        except Exception:
            return False, "", False

    # Ollama warm process check
    if host in ("127.0.0.1", "localhost", "::1") and (port == 11434 or hint == "ollama") and not allow_cold_load:
        try:
            ps_url = f"{parsed.scheme or 'http'}://{parsed.netloc}/api/ps"
            with _urlopen_local(ps_url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            models = [
                (str(m.get("name") or "").strip(), _is_vlm_name(str(m.get("name") or "")))
                for m in (data.get("models") or [])
                if isinstance(m, dict)
                and str(m.get("name") or "").strip()
                and not _is_embedding_model(str(m.get("name") or ""))
            ]
            if not models:
                return False, "", False
            cid, is_vlm = _select_best_warm_candidate(models, pref, prefer_vision=prefer_vision)
            return True, cid, is_vlm
        except Exception:
            return False, "", False

    if "/v1/" in ep and ep.startswith(("http://", "https://")):
        return True, pref or "default", _is_vlm_name(pref)
    return False, "", False


def _compute_image_dhash(img_or_path) -> str:
    """Compute a fast 64-bit perceptual difference hash (dHash) in memory (zero PIL required)."""
    try:
        if isinstance(img_or_path, (str, os.PathLike)):
            import native_host_bridge

            return native_host_bridge.compute_dhash_from_image_file(img_or_path)
        if isinstance(img_or_path, (bytes, bytearray)):
            import native_host_bridge

            raw = bytes(img_or_path)
            if raw[:2] in (b"P2", b"P3", b"P5", b"P6"):
                return native_host_bridge.compute_dhash_from_ppm_bytes(raw)
            return native_host_bridge.compute_dhash_from_png_bytes(raw)
        # Backward compatibility if a PIL.Image instance is ever passed directly
        gray = img_or_path.convert("L").resize((9, 8))
        px = list(gray.getdata())
        bits = 0
        for row in range(8):
            row_start = row * 9
            for col in range(8):
                bits = (bits << 1) | (1 if px[row_start + col] > px[row_start + col + 1] else 0)
        return f"{bits:016x}"
    except Exception:
        return ""


def _dhash_distance(hash_a: str, hash_b: str) -> int:
    a = str(hash_a or "").strip()
    b = str(hash_b or "").strip()
    if len(a) != 16 or len(b) != 16:
        return 64
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except ValueError:
        return 64


def _capture_ephemeral_peek_jpeg_with_hash() -> tuple[Path | None, str]:
    """Capture an ephemeral <=480px JPEG strictly in /tmp + 64-bit dHash (0 PIL, native Rust toolchain)."""
    fd, tmp_name = tempfile.mkstemp(prefix="alis-companion-peek-", suffix=".jpg")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        import native_host_bridge

        fix_raw = os.environ.get("ALIS_AQUA_SCREEN_FIXTURE", "").strip()
        if fix_raw:
            try:
                fix_obj = json.loads(fix_raw)
                cand = str(fix_obj.get("screenshotPath") or fix_obj.get("imagePath") or "").strip()
                if cand and Path(cand).is_file():
                    vhash = native_host_bridge.compute_dhash_from_image_file(cand)
                    ok_rs, _ = native_host_bridge.image_resize(cand, "480x480", tmp_path, quality=55)
                    if not ok_rs:
                        # Pure-stdlib fallback when neither alis-theme nor PIL is present
                        tmp_path.write_bytes(Path(cand).read_bytes())
                    return tmp_path, vhash
            except Exception:
                pass

        if os.environ.get("WAYLAND_DISPLAY"):
            raw_fd, raw_name = tempfile.mkstemp(prefix="alis-companion-raw-", suffix=".png")
            os.close(raw_fd)
            raw_path = Path(raw_name)
            try:
                ok_cap, _cap_backend = native_host_bridge.screencopy(raw_path, fmt="png", timeout=3.0)
                if ok_cap and raw_path.is_file() and raw_path.stat().st_size > 128:
                    vhash = native_host_bridge.compute_dhash_from_image_file(raw_path)
                    ok_rs, _ = native_host_bridge.image_resize(raw_path, "480x480", tmp_path, quality=55)
                    if ok_rs and tmp_path.is_file() and tmp_path.stat().st_size > 0:
                        return tmp_path, vhash
            finally:
                raw_path.unlink(missing_ok=True)
    except Exception:
        pass
    tmp_path.unlink(missing_ok=True)
    return None, ""


def _capture_ephemeral_peek_jpeg() -> Path | None:
    path, _vhash = _capture_ephemeral_peek_jpeg_with_hash()
    return path


def _tokenize_norm(text: str) -> list[str]:
    folded = _fold_ascii(text)
    return re.findall(r"[a-z0-9]+", folded)


def _char_ngrams(text: str, n: int = 4) -> set[str]:
    compact = re.sub(r"[^a-z0-9 ]+", "", _fold_ascii(text))
    compact = " ".join(compact.split())
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def remark_similarity(a: str, b: str) -> float:
    """Compute combined token + char-4-gram similarity in [0.0, 1.0]."""
    ta = set(_tokenize_norm(a))
    tb = set(_tokenize_norm(b))
    if not ta or not tb:
        return 0.0
    tok_jaccard = len(ta & tb) / max(1, len(ta | tb))
    ga = _char_ngrams(a, 4)
    gb = _char_ngrams(b, 4)
    gram_jaccard = (len(ga & gb) / max(1, len(ga | gb))) if (ga and gb) else 0.0
    return max(tok_jaccard, gram_jaccard)


def is_too_similar(candidate: str, recent: list[str] | None) -> bool:
    """Return True if `candidate` duplicates or closely echoes any recent mascot remark."""
    if not candidate or not recent:
        return False
    cand_toks = _tokenize_norm(candidate)
    if not cand_toks:
        return True
    cand_norm = " ".join(cand_toks)
    cand_prefix = tuple(cand_toks[:2]) if len(cand_toks) >= 4 else ()

    for idx, prev in enumerate(reversed(recent)):
        prev_toks = _tokenize_norm(prev)
        if not prev_toks:
            continue
        if cand_norm == " ".join(prev_toks):
            return True
        if remark_similarity(candidate, prev) >= 0.48:
            return True
        # Ban repeating the exact same 2-word opener within the 5 most recent comments
        if idx < 5 and cand_prefix and len(prev_toks) >= 4 and cand_prefix == tuple(prev_toks[:2]):
            return True
    return False


def _extract_banned_openings(recent: list[str]) -> list[str]:
    seen: list[str] = []
    for item in reversed(recent[-8:]):
        words = re.findall(r"[^\s,.;:!?—–-]+", str(item or ""))
        if len(words) >= 2:
            opener = " ".join(words[:2]).strip()
            if opener and opener.lower() not in {x.lower() for x in seen}:
                seen.append(opener)
                if len(seen) >= 5:
                    break
    return seen


def _choose_creative_angle(
    clean_ctx: dict,
    *,
    personality: str = "balanced",
    recent_angles: list[str] | None = None,
    visual_active: bool = False,
    exclude_angles: set[str] | None = None,
) -> tuple[str, str, str]:
    recent_set = {str(a).strip().lower() for a in (recent_angles or [])[-3:] if str(a).strip()}
    if exclude_angles:
        recent_set |= {a.lower() for a in exclude_angles}

    eligible = [a for a in CREATIVE_ANGLES if a[0] not in recent_set]
    if not eligible:
        eligible = list(CREATIVE_ANGLES)

    dwell = int(clean_ctx.get("dwellMinutes") or 0)
    has_ambience = bool(clean_ctx.get("music") or clean_ctx.get("weather") or clean_ctx.get("timeOfDay"))
    has_focus = bool(clean_ctx.get("pomodoro") or dwell >= 10)

    weights: list[float] = []
    for name, _expr, _desc in eligible:
        w = 1.0
        if name == "visual_detail" and visual_active:
            w += 2.2
        elif name == "ambience_vibe" and has_ambience:
            w += 1.4
        elif name == "gentle_care" and dwell >= 20:
            w += 1.6
        elif name == "momentum_cheer" and has_focus:
            w += 1.2
        if personality == "calm" and name in ("quiet_company", "ambience_vibe", "gentle_care"):
            w += 0.9
        elif personality == "energetic" and name in ("playful_aside", "momentum_cheer", "curious_question", "liquid_metaphor"):
            w += 0.9
        weights.append(w)

    return _SYS_RNG.choices(eligible, weights=weights, k=1)[0]


def _parse_remark_response_with_scene(raw_text: str) -> tuple[str, str, str]:
    txt = str(raw_text or "").strip()
    txt = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", txt, flags=re.IGNORECASE).strip()
    if not txt:
        return "", "happy", ""
    expr = "happy"
    remark = ""
    scene_cue = ""
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.I | re.S).strip()
    obj = None
    try:
        parsed = json.loads(fenced)
        if isinstance(parsed, dict):
            obj = parsed
    except Exception:
        m = re.search(r"\{[\s\S]*\}", fenced)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    obj = parsed
            except Exception:
                pass
    if isinstance(obj, dict):
        remark = str(obj.get("remark") or obj.get("text") or obj.get("message") or "").strip()
        scene_cue = str(obj.get("scene") or obj.get("visualCue") or "").strip()[:80]
        cand_expr = str(obj.get("expression") or "").strip().lower()
        if cand_expr in ALLOWED_EXPRESSIONS:
            expr = cand_expr
    if not remark:
        if fenced.lstrip().startswith("{"):
            # Only recover a complete quoted remark string; reject unclosed/malformed fragments.
            m_partial = re.search(r'"(?:remark|text|message)"\s*:\s*"([^"\n]+)"', fenced)
            if not scene_cue:
                m_sc = re.search(r'"(?:scene|visualCue)"\s*:\s*"([^"\n]{2,80})"', fenced)
                if m_sc:
                    scene_cue = m_sc.group(1).strip()[:80]
            if m_partial:
                remark = m_partial.group(1).strip()
            else:
                return "", expr, scene_cue
        else:
            remark = fenced
    remark = re.sub(r"\{REASON\s*:\s*[^}]+\}", "", remark, flags=re.IGNORECASE)
    remark = re.sub(r"[#*_`>|]+", " ", remark)
    remark = re.sub(r"https?://\S+", "", remark)
    remark = " ".join(remark.split()).strip(" \"'")
    if len(remark) > 155:
        cut = remark[:155].rsplit(" ", 1)[0].rstrip(" ,;:-")
        remark = (cut + "...") if cut else remark[:152] + "..."
    return remark, expr, scene_cue


def _parse_remark_response(raw_text: str) -> tuple[str, str]:
    """Backwards-compatible 2-tuple parser `(remark, expression)`."""
    remark, expr, _scene = _parse_remark_response_with_scene(raw_text)
    return remark, expr


def _extract_data_uri_b64(url: str) -> tuple[str, str]:
    m = re.match(r"^data:([^;]+);base64,(.+)$", str(url or "").strip(), flags=re.I | re.S)
    if not m:
        return "", ""
    return m.group(1).strip(), m.group(2).strip()


def _post_micro_completion(
    endpoint: str,
    body: dict,
    timeout: float = 8.0,
    *,
    api_key: str = "",
    api_format: str = "openai",
    model_name: str = "",
) -> str:
    fmt = str(api_format or "openai").strip().lower()
    key = str(api_key or "").strip()
    headers = {"Content-Type": "application/json"}

    if fmt == "anthropic" or "anthropic.com" in endpoint.lower():
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        msgs = body.get("messages") or []
        sys_txt = ""
        user_msgs = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            if m.get("role") == "system":
                sys_txt = str(m.get("content") or "")
            else:
                content = m.get("content")
                if isinstance(content, list):
                    anth_blocks = []
                    for p in content:
                        if not isinstance(p, dict):
                            continue
                        if p.get("type") == "text" and p.get("text"):
                            anth_blocks.append({"type": "text", "text": str(p["text"])})
                        elif p.get("type") == "image_url":
                            img_url = str((p.get("image_url") or {}).get("url") or "")
                            mime, b64 = _extract_data_uri_b64(img_url)
                            if mime and b64:
                                anth_blocks.append({
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": mime, "data": b64},
                                })
                    user_msgs.append({"role": m.get("role") or "user", "content": anth_blocks or ""})
                else:
                    user_msgs.append({"role": m.get("role") or "user", "content": str(content or "")})
        anth_body = {
            "model": model_name or body.get("model") or "",
            "max_tokens": min(110, int(body.get("max_tokens") or 60)),
            "temperature": float(body.get("temperature") or 0.85),
            "system": sys_txt,
            "messages": user_msgs,
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(anth_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with _urlopen_local(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        blocks = data.get("content") or []
        return "\n".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text").strip()

    if fmt == "gemini" or "generativelanguage.googleapis.com" in endpoint.lower():
        url = endpoint
        if key:
            headers["x-goog-api-key"] = key
        msgs = body.get("messages") or []
        sys_txt = ""
        gem_parts = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            if m.get("role") == "system":
                sys_txt = str(m.get("content") or "")
            else:
                content = m.get("content")
                if isinstance(content, list):
                    for p in content:
                        if not isinstance(p, dict):
                            continue
                        if p.get("type") == "text" and p.get("text"):
                            gem_parts.append({"text": str(p["text"])})
                        elif p.get("type") == "image_url":
                            img_url = str((p.get("image_url") or {}).get("url") or "")
                            mime, b64 = _extract_data_uri_b64(img_url)
                            if mime and b64:
                                gem_parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                elif content:
                    gem_parts.append({"text": str(content)})
        gem_body: dict = {
            "contents": [{"role": "user", "parts": gem_parts or [{"text": ""}]}],
            "generationConfig": {
                "maxOutputTokens": min(110, int(body.get("max_tokens") or 60)),
                "temperature": float(body.get("temperature") or 0.85),
                "topP": 0.92,
            },
        }
        if sys_txt:
            gem_body["systemInstruction"] = {"parts": [{"text": sys_txt}]}
        req = urllib.request.Request(
            url,
            data=json.dumps(gem_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with _urlopen_local(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        cands = data.get("candidates") or []
        if cands and isinstance(cands[0], dict):
            parts = ((cands[0].get("content") or {}).get("parts") or [])
            return "\n".join(str(p.get("text") or "") for p in parts if isinstance(p, dict)).strip()
        return ""

    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with _urlopen_local(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            parts = [str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            return "\n".join(parts).strip()
        return str(content or "").strip()
    return ""


def _build_contextual_fallback_remark(
    clean_ctx: dict,
    *,
    personality: str = "balanced",
    recent: list[str] | None = None,
    language: str = "en",
) -> tuple[str, str]:
    """Zero-model-load, anti-repetitive, randomized contextual remark (English by default)."""
    app_raw = str(clean_ctx.get("focusedApp") or "").strip()
    title_raw = str(clean_ctx.get("focusedTitle") or "").strip()
    dwell = int(clean_ctx.get("dwellMinutes") or 0)
    music = str(clean_ctx.get("music") or "").strip()
    pomodoro = str(clean_ctx.get("pomodoro") or "").strip()
    weather = str(clean_ctx.get("weather") or "").strip()
    time_of_day = str(clean_ctx.get("timeOfDay") or "").strip()

    use_vi = str(language or "en").strip().lower().startswith("vi")

    app_l = app_raw.lower()
    if app_l in ("org.quickshell", "quickshell", "qs") and re.search(r"\balis\s+nexus\b", title_raw, re.I):
        app_raw = "Alis Nexus"
        app_l = "alis-nexus"
    elif app_l in ("org.quickshell", "quickshell", "qs") and re.search(r"\balis\s+settings\b", title_raw, re.I):
        app_raw = "Alis Settings"
        app_l = "alis-settings"
    title_clean = re.sub(
        r"\s*[-—–|·]\s*(?:Visual Studio Code|Code|Obsidian|Firefox|Zen Browser|Chromium|Google Chrome|Brave|Alis Nexus)\s*$",
        "",
        title_raw,
        flags=re.I,
    ).strip()
    if title_clean.lower() in ("alis nexus", "alis settings"):
        title_clean = ""
    short_title = title_clean[:38].rstrip(" -—–|·") if title_clean else ""
    informative = bool(short_title and not is_opaque_title(app_raw, title_raw))
    pretty_app = re.sub(r"^(?:org|com|io|dev|net)\.(?:[^.]+\.)?", "", app_raw, flags=re.I).replace("-", " ").replace("_", " ").strip().title()

    candidates: list[tuple[str, str]] = []
    if use_vi:
        if pomodoro:
            candidates.extend([
                (f"{pomodoro} — giữ nhịp nhẹ nhàng nhé, mình ngồi ngay mép màn hình cổ vũ bạn.", "working"),
                (f"Đang trong phiên {pomodoro}, làm từng chút một thôi là tới đích liền.", "happy"),
            ])
        if any(tok in app_l for tok in ("code", "zed", "nvim", "vim", "antigravity", "cursor", "windsurf", "kate", "emacs", "neovide")):
            if informative:
                candidates.extend([
                    (f"Đang gỡ nút thắt ở {short_title} à? Nhìn tập trung cực kỳ luôn.", "curious"),
                    (f"Lướt cùng bạn trong {short_title} — code chạy êm ru nhé!", "working"),
                    (f"{short_title} có vẻ thú vị đấy, nhớ chớp mắt và thả lỏng vai một chút nha.", "calm"),
                ])
            candidates.extend([
                ("Nhịp gõ phím đều tay ghê — mình trôi nhẹ bên cạnh chờ bạn build xong.", "working"),
                ("Không gian code yên tĩnh quá, chúc mọi hàm hôm nay chạy mượt ngay lần đầu.", "happy"),
            ])
        elif any(tok in app_l for tok in ("foot", "kitty", "alacritty", "wezterm", "ghostty", "konsole", "terminal")):
            if informative:
                candidates.extend([
                    (f"Terminal đang xoay với {short_title} — hy vọng toàn dòng xanh mát mắt!", "curious"),
                    (f"Theo dõi {short_title} cùng bạn, cần mình giải thích log thì gọi nhé.", "working"),
                ])
            candidates.extend([
                ("Chế độ dòng lệnh tốc độ cao — gió yên biển lặng nhé!", "working"),
                ("Nhìn terminal chạy trông chuyên nghiệp thật, cứ bình tĩnh từng lệnh một.", "happy"),
            ])
        elif any(tok in app_l for tok in ("obsidian", "alis-nexus", "nexus", "logseq", "notion")):
            if informative:
                candidates.extend([
                    (f"Đang chăm chút ghi chú {short_title} — kho tri thức ngày càng gọn đẹp.", "happy"),
                    (f"Ý tưởng trong {short_title} đang kết nối dần rồi kìa!", "curious"),
                ])
            candidates.extend([
                ("Giờ viết ghi chú thật yên bình — dòng suy nghĩ trôi êm như mặt nước.", "calm"),
                ("Sắp xếp lại ý tưởng giúp đầu óc nhẹ hơn hẳn nhỉ?", "happy"),
            ])
        elif any(tok in app_l for tok in ("firefox", "zen", "chromium", "chrome", "brave", "vivaldi")):
            if informative:
                candidates.extend([
                    (f"Đang khám phá {short_title} — có chi tiết nào hay ho không kể mình nghe với!", "curious"),
                    (f"Đọc {short_title} xong cần tóm tắt nhanh thì cứ bấm gọi mình nha.", "happy"),
                ])
            candidates.extend([
                ("Lướt web tìm cảm hứng à? Coi chừng bị cuốn vào quá nhiều tab nhé.", "curious"),
                ("Mình ngồi đây canh mép màn hình trong lúc bạn tìm tài liệu.", "calm"),
            ])
        elif pretty_app:
            if informative:
                candidates.append((f"Đang chăm chú với {short_title} trên {pretty_app} — nhịp làm việc ổn áp lắm.", "happy"))
            candidates.extend([
                (f"Ghé ngang {pretty_app} một chút — mình ở ngay đây đồng hành cùng bạn.", "happy"),
                (f"Làm việc trên {pretty_app} trông khá thư thái đấy chứ!", "calm"),
            ])
        if music:
            short_music = music[:34].rstrip(" -—–|·")
            candidates.extend([
                (f"Giai điệu {short_music} hợp không khí làm việc lúc này ghê.", "happy"),
                (f"Nghe {short_music} nhẹ nhàng thế này giúp tập trung hơn hẳn.", "calm"),
            ])
        if weather:
            candidates.append((f"Ngoài trời {weather} — ngồi trong nhà làm việc thế này thật ấm cúng.", "calm"))
        if time_of_day:
            candidates.append((f"Đã {time_of_day} rồi — nhịp làm việc hôm nay của bạn vẫn ổn áp chứ?", "curious"))
        mood_v = str(clean_ctx.get("mood") or "").lower()
        energy_v = str(clean_ctx.get("energy") or "").lower()
        if mood_v in ("bad", "terrible") or energy_v in ("drained", "low"):
            candidates.append(("Hôm nay năng lượng hơi thấp, làm chậm lại một nhịp cũng chẳng sao đâu nhé.", "calm"))
        elif mood_v in ("great", "good") and energy_v in ("high", "peak"):
            candidates.append(("Năng lượng đang lên cao quá — tranh thủ bứt tốc đoạn khó nhất nào!", "excited"))
        if dwell >= 20:
            candidates.extend([
                (f"Đã tập trung liên tục {dwell} phút rồi đó — nhấp ngụm nước rồi chiến tiếp nhé.", "calm"),
                (f"{dwell} phút không rời mắt khỏi màn hình rồi, duỗi tay vài giây cho khỏe nào.", "happy"),
            ])
        if not candidates:
            candidates.append(("Mình đang trôi nhẹ ở mép màn hình, cần gì cứ gọi mình nhé!", "happy"))
    else:
        if pomodoro:
            candidates.extend([
                (f"{pomodoro} — keeping quiet company at the edge while you ride the groove.", "working"),
                (f"Steady focus on {pomodoro}; small ripples add up fast.", "happy"),
            ])
        if any(tok in app_l for tok in ("code", "zed", "nvim", "vim", "antigravity", "cursor", "windsurf", "kate", "emacs", "neovide")):
            if informative:
                candidates.extend([
                    (f"Tinkering inside {short_title} — hope the logic snaps neatly into place.", "working"),
                    (f"How is {short_title} treating you today? Looking locked-in from over here.", "curious"),
                    (f"Spotted you deep in {short_title} — drop your shoulders and breathe easy.", "calm"),
                ])
            candidates.extend([
                ("Smooth coding cadence — I'll keep watch quietly from the edge.", "working"),
                ("Clean editor flow today; wishing you zero sneaky bugs.", "happy"),
            ])
        elif any(tok in app_l for tok in ("foot", "kitty", "alacritty", "wezterm", "ghostty", "konsole", "terminal")):
            if informative:
                candidates.extend([
                    (f"Terminal humming along on {short_title} — fingers crossed for green output.", "curious"),
                    (f"Keeping an eye on {short_title} with you; holler if any log looks weird.", "working"),
                ])
            candidates.extend([
                ("Command-line cruise control — smooth sailing in the shell.", "working"),
                ("Love the crisp terminal vibe; steady keystrokes from here.", "happy"),
            ])
        elif any(tok in app_l for tok in ("obsidian", "alis-nexus", "nexus", "logseq", "notion")):
            if informative:
                candidates.extend([
                    (f"Shaping ideas in {short_title} — fun watching the vault connect.", "happy"),
                    (f"New thoughts landing in {short_title}? Quietly cheering from the corner.", "curious"),
                ])
            candidates.extend([
                ("Calm note-taking tide — organizing thoughts at your own rhythm.", "calm"),
                ("A tidy knowledge graph always feels refreshing.", "happy"),
            ])
        elif any(tok in app_l for tok in ("firefox", "zen", "chromium", "chrome", "brave", "vivaldi")):
            if informative:
                candidates.extend([
                    (f"Checking out {short_title} — anything surprising catch your eye?", "curious"),
                    (f"Reading through {short_title}? Tap me anytime if you want a quick recap.", "happy"),
                ])
            candidates.extend([
                ("Surfing tabs — careful not to drift into a 30-tab rabbit hole!", "curious"),
                ("Drifting alongside your browsing session whenever you need a hand.", "calm"),
            ])
        elif pretty_app:
            if informative:
                candidates.append((f"Focused on {short_title} in {pretty_app} — nice steady rhythm.", "happy"))
            candidates.extend([
                (f"Hanging out at the edge while you work in {pretty_app}.", "happy"),
                (f"Steady pace in {pretty_app} — let me know if you want a quick assist.", "calm"),
            ])
        if music:
            short_music = music[:34].rstrip(" -—–|·")
            candidates.extend([
                (f"Great soundtrack with {short_music} floating in the background.", "happy"),
                (f"{short_music} sets a cozy rhythm for getting things done.", "calm"),
            ])
        if weather:
            candidates.append((f"{weather} outside — prime weather for a cozy desk session.", "calm"))
        if time_of_day:
            candidates.append((f"Checking in around {time_of_day} — smooth pace so far?", "curious"))
        mood_v = str(clean_ctx.get("mood") or "").lower()
        energy_v = str(clean_ctx.get("energy") or "").lower()
        if mood_v in ("bad", "terrible") or energy_v in ("drained", "low"):
            candidates.append(("Low-battery day — zero pressure, even tiny steps count right now.", "calm"))
        elif mood_v in ("great", "good") and energy_v in ("high", "peak"):
            candidates.append(("Energy looks sharp today — great wave to knock out the tricky bit!", "excited"))
        if dwell >= 20:
            candidates.extend([
                (f"{dwell} minutes straight here — quick sip of water or blink break soon?", "calm"),
                (f"You've held focus for {dwell} minutes — nice streak, stretch your wrists a bit.", "happy"),
            ])
        if not candidates:
            candidates.append(("Floating right here at the screen edge whenever you need a hand.", "happy"))

    # Shuffle with cryptographic RNG so fallback never repeats the same order
    shuffled = list(candidates)
    _SYS_RNG.shuffle(shuffled)
    recent_list = list(recent or [])
    for remark, expr in shuffled:
        if not is_too_similar(remark, recent_list):
            if personality == "energetic" and expr == "calm":
                expr = "happy"
            return remark, expr

    # If all candidates had partial overlap, pick the LEAST similar one (never loop candidates[0] blindly!)
    best_pair = min(
        shuffled,
        key=lambda pair: max((remark_similarity(pair[0], r) for r in recent_list), default=0.0),
    )
    return best_pair


def _skipped(reason: str) -> dict:
    return {
        "ok": True,
        "skipped": True,
        "skipReason": reason,
        "remark": "",
        "expression": "idle",
        "tier": "none",
        "ephemeral": True,
        "model": "",
        "angle": "",
        "visualHash": "",
    }


def generate_mascot_commentary(payload: dict) -> dict:
    """Generate one ephemeral, diverse, context-grounded remark for the desktop mascot bubble."""
    req = payload if isinstance(payload, dict) else {}
    power_profile = str(req.get("powerProfile") or "").strip().lower()
    if power_profile == "power-saver":
        return _skipped("power_saver")

    try:
        batt_pct = float(req.get("batteryPercent") if req.get("batteryPercent") is not None else 100.0)
    except (TypeError, ValueError):
        batt_pct = 100.0
    if batt_pct < 20.0 and req.get("batteryCharging") is False:
        return _skipped("low_battery")

    raw_ctx = req.get("desktopContext") if isinstance(req.get("desktopContext"), dict) else {}
    fullscreen = bool(req.get("fullscreen") or raw_ctx.get("fullscreen"))
    app_id = str(raw_ctx.get("focusedApp") or "")
    title = str(raw_ctx.get("focusedTitle") or "")
    if is_sensitive_context(app_id, title, fullscreen=fullscreen):
        return _skipped("sensitive_context")

    clean_ctx = sanitize_desktop_context(raw_ctx)
    if not clean_ctx.get("focusedApp") and not clean_ctx.get("focusedTitle") and not clean_ctx.get("music") and not clean_ctx.get("pomodoro"):
        return _skipped("empty_context")

    endpoint_raw = str(req.get("actionEndpoint") or req.get("endpoint") or "").strip()
    api_format = str(req.get("apiFormat") or "openai").strip().lower()
    api_key = str(req.get("apiKey") or "").strip()
    allow_fallback = bool(req.get("allowContextualFallback"))
    personality = str(req.get("personality") or "balanced").lower()
    lang_raw = str(req.get("language") or "en").strip().lower()
    if lang_raw.startswith("vi"):
        lang = "vi"
    elif lang_raw == "auto":
        sample_lang = str(req.get("lastUserPrompt") or "")
        has_vi = bool(sample_lang and _contains_vietnamese_text(sample_lang))
        env_vi = str(os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower().startswith("vi")
        lang = "vi" if (has_vi or env_vi) else "en"
    else:
        lang = "en"
    recent = [
        " ".join(str(r).split())[:120]
        for r in (req.get("recentComments") or [])
        if str(r or "").strip()
    ][-14:]
    recent_angles = [
        str(a).strip().lower()
        for a in (req.get("recentAngles") or [])
        if str(a or "").strip()
    ][-6:]
    last_visual_hash = str(req.get("lastVisualHash") or "").strip()
    last_scene_cue = " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", str(req.get("lastSceneCue") or "")).split())[:80]
    visual_opt_in = bool(req.get("visualPeekEnabled"))

    def _fallback_or_skip(reason: str) -> dict:
        if not allow_fallback:
            return _skipped(reason)
        fb_remark, fb_expr = _build_contextual_fallback_remark(
            clean_ctx, personality=personality, recent=recent, language=lang
        )
        if not fb_remark:
            return _skipped(reason)
        return {
            "ok": True,
            "skipped": False,
            "skipReason": "",
            "remark": fb_remark,
            "expression": fb_expr,
            "tier": "contextual",
            "ephemeral": True,
            "model": "contextual-local",
            "angle": "contextual_fallback",
            "visualHash": last_visual_hash,
        }

    if api_format in ("gemini", "anthropic") and endpoint_raw.startswith(("http://", "https://")):
        endpoint = endpoint_raw
    else:
        endpoint = _normalize_chat_endpoint(endpoint_raw)
    preferred_model = str(req.get("actionModel") or req.get("model") or "").strip()
    if not endpoint:
        return _fallback_or_skip("no_endpoint")

    parsed_ep = urllib.parse.urlparse(endpoint)
    is_local_host = (parsed_ep.hostname or "").lower() in ("127.0.0.1", "localhost", "::1")
    if bool(req.get("localOnly", True)) and not is_local_host:
        return _fallback_or_skip("local_only_blocked")

    allow_cold = bool(req.get("allowColdLoad"))
    if is_local_host:
        warm_ok, warm_model, is_vlm = probe_warm_local_model(
            endpoint,
            preferred_model=preferred_model,
            provider_hint=str(req.get("providerHint") or ""),
            allow_cold_load=allow_cold,
            prefer_vision=visual_opt_in,
            timeout=0.7,
        )
    else:
        warm_ok = bool(endpoint and (preferred_model or api_format == "gemini"))
        warm_model = preferred_model or "default"
        is_vlm = _is_vlm_name(warm_model)

    if not warm_ok or not warm_model:
        return _fallback_or_skip("no_warm_model")

    char_name = "Octo" if str(req.get("character") or "").lower() == "octo" else "Aqua"
    lang_rule = (
        "Write in natural, warm Vietnamese."
        if lang == "vi"
        else "Write strictly in natural, concise English ONLY (never Vietnamese or any other language, even if window titles, music, or screen text are non-English)."
    )
    tone_hint = (
        "soft and unhurried"
        if personality == "calm"
        else ("playful and upbeat" if personality == "energetic" else "warm, observant, and friendly")
    )

    peek_img: Path | None = None
    captured_vhash = ""
    tier = "text"
    use_visual = False
    is_opaque = is_opaque_title(app_id, title)

    try:
        # When opt-in visualPeekEnabled is ON and a warm VLM is ready, capture ephemeral <=480px JPEG
        # and run vision if the screen visually changed (dHash distance >= 6), or on an opaque title without a cached scene cue.
        if visual_opt_in and is_vlm:
            peek_img, captured_vhash = _capture_ephemeral_peek_jpeg_with_hash()
            if peek_img is not None and peek_img.is_file():
                vis_changed = (not last_visual_hash) or (_dhash_distance(captured_vhash, last_visual_hash) >= 6)
                if vis_changed or (is_opaque and not last_scene_cue):
                    use_visual = True
                    tier = "visual_peek"

        b64_img = base64.b64encode(peek_img.read_bytes()).decode("ascii") if (use_visual and peek_img and peek_img.is_file()) else ""
        # On unchanged static screens with visual peek enabled, reuse the ephemeral in-memory scene cue without spending vision tokens.
        prompt_ctx = dict(clean_ctx)
        if not use_visual and visual_opt_in and last_scene_cue:
            prompt_ctx["visualScene"] = last_scene_cue
        banned_openings = _extract_banned_openings(recent)
        recent_block = f"\nAvoid echoing any of these recent remarks: {json.dumps(recent[-8:], ensure_ascii=False)}" if recent else ""
        opener_block = f"\nDo NOT start with: {json.dumps(banned_openings, ensure_ascii=False)}" if banned_openings else ""
        is_mtp = "mtp" in warm_model.lower()
        reason_tag = " {REASON:none}" if _uses_reason_none_directive(warm_model) else ""
        reason_prefix = "{REASON:none}\n" if reason_tag else ""

        def _invoke_once(
            angle_tuple: tuple[str, str, str],
            extra_novelty: bool = False,
            force_text_only: bool = False,
            scene_override: str = "",
            force_english: bool = False,
        ) -> tuple[str, str, str]:
            angle_name, _angle_expr, angle_instruction = angle_tuple
            novelty_nudge = (" Invent a completely fresh phrasing and topic hook." if extra_novelty else "") + (
                " Output English ONLY (no Vietnamese words)." if force_english else ""
            )
            attach_img = bool(use_visual and b64_img and not force_text_only)
            eff_ctx = dict(prompt_ctx)
            if not attach_img and scene_override and not eff_ctx.get("visualScene"):
                eff_ctx["visualScene"] = scene_override
            eff_ctx_json = json.dumps(eff_ctx, ensure_ascii=False)
            if attach_img:
                schema_hint = '{"scene": "3-6 word visual detail", "remark": "...", "expression": "calm|happy|curious|sleepy|excited|working"}'
                max_tok = 90
            else:
                schema_hint = '{"remark": "...", "expression": "calm|happy|curious|sleepy|excited|working"}'
                max_tok = 60

            sys_msg = (
                f"You are {char_name}, a tiny translucent desktop companion mascot sitting quietly at the screen edge. "
                f"Creative angle for THIS remark ({angle_name}): {angle_instruction}{novelty_nudge} "
                f"Say ONE short, spontaneous, {tone_hint} remark (9-22 words max). {lang_rule} "
                "Be genuinely varied and specific; avoid generic clichés like 'Deep in...', 'Focused on...', 'Keep up the good work'. "
                "Do NOT answer like a formal chatbot or list commands. Do NOT include URLs, Markdown, or file paths. "
                f"Reply ONLY as compact JSON: {schema_hint}.{reason_tag}"
            )

            if attach_img:
                user_content: str | list[dict] = [
                    {
                        "type": "text",
                        "text": (
                            f"{reason_prefix}Ambient context: {eff_ctx_json}{recent_block}{opener_block}\n"
                            "Look at the screenshot, note one specific visual detail in `scene`, and react naturally as a desktop mascot."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                    },
                ]
            else:
                user_content = f"{reason_prefix}Ambient desktop context: {eff_ctx_json}{recent_block}{opener_block}"

            body: dict = {
                "model": warm_model,
                "messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.72 if is_mtp else (0.95 if extra_novelty else 0.86),
                "top_p": 0.92,
                "max_tokens": max_tok,
                "reasoning_effort": "none",
                "chat_template_kwargs": {"enable_thinking": False},
            }
            if is_mtp:
                body["repetition_penalty"] = 1.0
                body["repeat_penalty"] = 1.0
            else:
                body["presence_penalty"] = 0.55

            raw_out = _post_micro_completion(
                endpoint,
                body,
                timeout=8.5,
                api_key=api_key,
                api_format=api_format,
                model_name=warm_model,
            )
            return _parse_remark_response_with_scene(raw_out)

        chosen_angle = _choose_creative_angle(
            clean_ctx,
            personality=personality,
            recent_angles=recent_angles,
            visual_active=use_visual,
        )
        try:
            remark, expr, scene_cue = _invoke_once(chosen_angle, extra_novelty=False)
        except urllib.error.HTTPError as http_exc:
            if use_visual and http_exc.code in (400, 415, 422):
                # Warm model rejected multimodal image payload: gracefully downgrade to Tier 1 text-only.
                use_visual = False
                tier = "text"
                remark, expr, scene_cue = _invoke_once(
                    chosen_angle,
                    extra_novelty=False,
                    force_text_only=True,
                    scene_override=last_scene_cue,
                )
            else:
                raise

        # Anti-repetition & language guard: if a tiny model echoes a recent remark OR drifts into
        # Vietnamese under English commentary mode, run at most 1 fast retry with a different angle.
        # Reuse the already-extracted scene_cue in text-only mode to avoid spending a second VLM vision pass.
        lang_violation = bool(remark and lang == "en" and _contains_vietnamese_text(remark))
        dup_violation = bool(remark and is_too_similar(remark, recent))
        if dup_violation or lang_violation:
            alt_angle = _choose_creative_angle(
                clean_ctx,
                personality=personality,
                recent_angles=recent_angles,
                visual_active=use_visual,
                exclude_angles={chosen_angle[0]},
            )
            reuse_text_retry = (not use_visual) or bool(scene_cue)
            retry_remark, retry_expr, retry_scene = _invoke_once(
                alt_angle,
                extra_novelty=True,
                force_text_only=reuse_text_retry,
                scene_override=scene_cue or last_scene_cue,
                force_english=lang_violation,
            )
            retry_lang_ok = bool(retry_remark) and not (lang == "en" and _contains_vietnamese_text(retry_remark))
            if retry_lang_ok and not is_too_similar(retry_remark, recent):
                chosen_angle = alt_angle
                remark, expr = retry_remark, retry_expr
                scene_cue = retry_scene or scene_cue
            else:
                reason = "non_english_llm_output" if lang_violation else "duplicate_llm_output"
                return _fallback_or_skip(reason) if allow_fallback else _skipped(reason)

        if not remark:
            return _fallback_or_skip("empty_llm_output")
        # Keep the last VLM peek baseline hash unchanged when vision was skipped so slow sub-threshold drift accumulates.
        out_visual_hash = (captured_vhash if (use_visual or not last_visual_hash) else last_visual_hash) or captured_vhash
        out_res = {
            "ok": True,
            "skipped": False,
            "skipReason": "",
            "remark": remark,
            "expression": expr,
            "tier": tier,
            "ephemeral": True,
            "model": warm_model,
            "angle": chosen_angle[0],
            "visualHash": out_visual_hash,
        }
        final_scene_cue = scene_cue or (last_scene_cue if (visual_opt_in and not use_visual) else "")
        if final_scene_cue:
            out_res["sceneCue"] = final_scene_cue
        return out_res
    except Exception as exc:
        return _fallback_or_skip(f"error:{type(exc).__name__}")
    finally:
        if peek_img is not None:
            try:
                peek_img.unlink(missing_ok=True)
            except OSError:
                pass
