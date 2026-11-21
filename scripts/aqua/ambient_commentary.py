#!/usr/bin/env python3
"""Ephemeral, low-overhead LLM ambient mascot commentary for Alis-Companion.

Generates brief, context-aware desktop remarks for the small Mascot talk bubble
WITHOUT writing to chat history, SQLite databases, or TTS playback.

Resource & Privacy Guarantees:
  1. Never cold-loads an unloaded local model (probes /api/v0/models or /api/ps first).
  2. Tier 1 (default): single non-thinking micro-LLM text call (`max_tokens <= 60`)
     using sanitized Niri active window, Pomodoro, media, weather, and mood cues.
  3. Tier 2 (opt-in `visualPeekEnabled`, default OFF): only fires when Tier 1 title
     is opaque and the warm model supports vision; resizes an ephemeral capture
     to <= 480px JPEG in `/tmp`, sends 1 call, and unlinks in `finally:`.
  4. Never fires on sensitive apps/titles (password managers, incognito, banking,
     OTP/login, secrets, lockscreens, fullscreen games/video) or low battery.
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.parse
import urllib.request

ALLOWED_EXPRESSIONS = ("calm", "happy", "curious", "sleepy", "excited", "working")
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


def _fold_ascii(text: str) -> str:
    norm = unicodedata.normalize("NFD", str(text or ""))
    stripped = "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D").lower().strip()


def _is_vlm_name(model_id: str, model_type: str = "") -> bool:
    if str(model_type or "").strip().lower() == "vlm":
        return True
    low = str(model_id or "").lower()
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
            "qwen2-vl",
            "qwen2.5-vl",
            "llava",
            "gemma-3",
            "gemma3",
            "pixtral",
            "minicpm",
            "internvl",
        )
    )


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


def probe_warm_local_model(
    endpoint: str,
    preferred_model: str = "",
    *,
    provider_hint: str = "",
    allow_cold_load: bool = False,
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
    if host in ("127.0.0.1", "localhost") and (port == 1234 or hint == "lmstudio") and not allow_cold_load:
        try:
            v0_url = f"{parsed.scheme or 'http'}://{parsed.netloc}/api/v0/models"
            with _urlopen_local(v0_url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            items = data.get("data") if isinstance(data, dict) else []
            loaded = [
                it
                for it in (items if isinstance(items, list) else [])
                if isinstance(it, dict)
                and str(it.get("state") or "").lower() == "loaded"
                and str(it.get("id") or "").strip()
                and not _is_embedding_model(str(it.get("id") or ""), str(it.get("type") or ""))
            ]
            if not loaded:
                return False, "", False
            chosen = next((it for it in loaded if str(it.get("id") or "").strip() == pref), loaded[0])
            cid = str(chosen.get("id") or "").strip()
            return True, cid, _is_vlm_name(cid, str(chosen.get("type") or ""))
        except Exception:
            return False, "", False

    # Ollama warm process check
    if host in ("127.0.0.1", "localhost") and port == 11434 and not allow_cold_load:
        try:
            ps_url = f"{parsed.scheme or 'http'}://{parsed.netloc}/api/ps"
            with _urlopen_local(ps_url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            models = [m for m in (data.get("models") or []) if isinstance(m, dict) and str(m.get("name") or "").strip()]
            if not models:
                return False, "", False
            chosen = next((m for m in models if str(m.get("name") or "").strip() == pref), models[0])
            cid = str(chosen.get("name") or "").strip()
            return True, cid, _is_vlm_name(cid)
        except Exception:
            return False, "", False

    if "/v1/" in ep and ep.startswith(("http://", "https://")):
        return True, pref or "default", _is_vlm_name(pref)
    return False, "", False


def _capture_ephemeral_peek_jpeg() -> Path | None:
    """Capture an ephemeral <=480px JPEG strictly in /tmp (never saved in screenshots/)."""
    fd, tmp_name = tempfile.mkstemp(prefix="alis-companion-peek-", suffix=".jpg")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        from PIL import Image

        fix_raw = os.environ.get("ALIS_AQUA_SCREEN_FIXTURE", "").strip()
        if fix_raw:
            try:
                fix_obj = json.loads(fix_raw)
                cand = str(fix_obj.get("screenshotPath") or fix_obj.get("imagePath") or "").strip()
                if cand and Path(cand).is_file():
                    with Image.open(cand) as im:
                        rgb = im.convert("RGB")
                        rgb.thumbnail((480, 480))
                        rgb.save(tmp_path, format="JPEG", quality=55)
                    return tmp_path
            except Exception:
                pass

        grim_bin = shutil.which("grim")
        if grim_bin and os.environ.get("WAYLAND_DISPLAY"):
            raw_fd, raw_name = tempfile.mkstemp(prefix="alis-companion-raw-", suffix=".png")
            os.close(raw_fd)
            raw_path = Path(raw_name)
            try:
                subprocess.run(
                    [grim_bin, str(raw_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                    check=False,
                )
                if raw_path.is_file() and raw_path.stat().st_size > 256:
                    with Image.open(raw_path) as im:
                        rgb = im.convert("RGB")
                        rgb.thumbnail((480, 480))
                        rgb.save(tmp_path, format="JPEG", quality=55)
                    return tmp_path
            finally:
                raw_path.unlink(missing_ok=True)
    except Exception:
        pass
    tmp_path.unlink(missing_ok=True)
    return None


def _parse_remark_response(raw_text: str) -> tuple[str, str]:
    txt = str(raw_text or "").strip()
    txt = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", txt, flags=re.IGNORECASE).strip()
    if not txt:
        return "", "happy"
    expr = "happy"
    remark = ""
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.I | re.S).strip()
    try:
        obj = json.loads(fenced)
        if isinstance(obj, dict):
            remark = str(obj.get("remark") or obj.get("text") or obj.get("message") or "").strip()
            cand_expr = str(obj.get("expression") or "").strip().lower()
            if cand_expr in ALLOWED_EXPRESSIONS:
                expr = cand_expr
    except Exception:
        m = re.search(r"\{[\s\S]*\}", fenced)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    remark = str(obj.get("remark") or obj.get("text") or "").strip()
                    cand_expr = str(obj.get("expression") or "").strip().lower()
                    if cand_expr in ALLOWED_EXPRESSIONS:
                        expr = cand_expr
            except Exception:
                pass
    if not remark:
        if fenced.lstrip().startswith("{"):
            m_partial = re.search(r'"(?:remark|text|message)"\s*:\s*"([^"\n]+)"', fenced)
            if m_partial:
                remark = m_partial.group(1).strip()
            else:
                return "", expr
        else:
            remark = fenced
    remark = re.sub(r"[#*_`>|]+", " ", remark)
    remark = re.sub(r"https?://\S+", "", remark)
    remark = " ".join(remark.split()).strip(" \"'")
    if len(remark) > 155:
        cut = remark[:155].rsplit(" ", 1)[0].rstrip(" ,;:-")
        remark = (cut + "...") if cut else remark[:152] + "..."
    return remark, expr


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
                    text_only = "\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
                    user_msgs.append({"role": m.get("role") or "user", "content": text_only})
                else:
                    user_msgs.append({"role": m.get("role") or "user", "content": str(content or "")})
        anth_body = {
            "model": model_name or body.get("model") or "",
            "max_tokens": min(80, int(body.get("max_tokens") or 60)),
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
        user_txt = ""
        for m in msgs:
            if not isinstance(m, dict):
                continue
            if m.get("role") == "system":
                sys_txt = str(m.get("content") or "")
            else:
                content = m.get("content")
                if isinstance(content, list):
                    user_txt = "\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
                else:
                    user_txt = str(content or "")
        gem_body: dict = {
            "contents": [{"role": "user", "parts": [{"text": user_txt}]}],
            "generationConfig": {"maxOutputTokens": min(80, int(body.get("max_tokens") or 60)), "temperature": 0.72},
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
) -> tuple[str, str]:
    """Zero-model-load ephemeral contextual remark when no LLM is currently warm."""
    app_raw = str(clean_ctx.get("focusedApp") or "").strip()
    title_raw = str(clean_ctx.get("focusedTitle") or "").strip()
    dwell = int(clean_ctx.get("dwellMinutes") or 0)
    music = str(clean_ctx.get("music") or "").strip()
    pomodoro = str(clean_ctx.get("pomodoro") or "").strip()
    weather = str(clean_ctx.get("weather") or "").strip()

    app_l = app_raw.lower()
    if app_l in ("org.quickshell", "quickshell", "qs") and re.search(r"\balis\s+nexus\b", title_raw, re.I):
        app_raw = "Alis Nexus"
        app_l = "alis-nexus"
    elif app_l in ("org.quickshell", "quickshell", "qs") and re.search(r"\balis\s+settings\b", title_raw, re.I):
        app_raw = "Alis Settings"
        app_l = "alis-settings"
    title_clean = re.sub(r"\s*[-—–|·]\s*(?:Visual Studio Code|Code|Obsidian|Firefox|Zen Browser|Chromium|Google Chrome|Brave|Alis Nexus)\s*$", "", title_raw, flags=re.I).strip()
    if title_clean.lower() in ("alis nexus", "alis settings"):
        title_clean = ""
    short_title = title_clean[:38].rstrip(" -—–|·") if title_clean else ""
    recent_set = {str(r).strip().lower() for r in (recent or []) if str(r).strip()}

    candidates: list[tuple[str, str]] = []
    if pomodoro:
        candidates.append((f"{pomodoro} — staying right here with you, keep the momentum steady.", "working"))
    if any(tok in app_l for tok in ("code", "zed", "nvim", "vim", "antigravity", "cursor", "windsurf", "kate", "emacs", "neovide")):
        if short_title and not is_opaque_title(app_raw, title_raw):
            candidates.append((f"Deep in {short_title} — nice focus, take a breather when you need one.", "working"))
            candidates.append((f"Working through {short_title}? Looking steady from over here.", "curious"))
        if dwell >= 20:
            candidates.append((f"{dwell} minutes straight in the editor — remember to blink and stretch.", "calm"))
        candidates.append(("Quiet coding flow — I'll keep watch on the edge while you build.", "working"))
    elif any(tok in app_l for tok in ("foot", "kitty", "alacritty", "wezterm", "ghostty", "konsole", "terminal")):
        if short_title and not is_opaque_title(app_raw, title_raw):
            candidates.append((f"Terminal busy with {short_title} — hope everything builds cleanly.", "curious"))
        candidates.append(("Command-line focus mode — smooth sailing in the terminal.", "working"))
    elif any(tok in app_l for tok in ("obsidian", "alis-nexus", "nexus", "logseq", "notion")):
        if short_title and not is_opaque_title(app_raw, title_raw):
            candidates.append((f"Organizing notes in {short_title} — love seeing the vault grow.", "happy"))
        candidates.append(("Quiet note-taking session — connecting ideas at your own pace.", "calm"))
    elif any(tok in app_l for tok in ("firefox", "zen", "chromium", "chrome", "brave", "vivaldi")):
        if short_title and not is_opaque_title(app_raw, title_raw):
            candidates.append((f"Reading up on {short_title} — anything interesting catching your eye?", "curious"))
        if dwell >= 15:
            candidates.append((f"Browsing for {dwell} minutes — don't lose track of your main groove.", "calm"))
        candidates.append(("Cruising through tabs — let me know if you want a quick summary.", "curious"))
    elif app_raw:
        pretty_app = re.sub(r"^(?:org|com|io|dev|net)\.(?:[^.]+\.)?", "", app_raw, flags=re.I).replace("-", " ").replace("_", " ").strip().title()
        if short_title and not is_opaque_title(app_raw, title_raw):
            candidates.append((f"Focused on {short_title} in {pretty_app} — keeping you company.", "happy"))
        else:
            candidates.append((f"Hanging out while you work in {pretty_app} — steady pace!", "happy"))

    if music:
        short_music = music[:36].rstrip(" -—–|·")
        candidates.append((f"Nice vibe with {short_music} playing while you work.", "happy"))
    if weather:
        candidates.append((f"It's {weather} outside — cozy time to stay focused indoors.", "calm"))
    if dwell >= 35:
        candidates.append((f"You've been at this for {dwell} minutes — quick water break soon?", "calm"))

    if not candidates:
        candidates.append(("Right here on the screen edge whenever you need a hand.", "happy"))

    for remark, expr in candidates:
        if remark.lower() not in recent_set:
            if personality == "energetic" and expr == "calm":
                expr = "happy"
            return remark, expr
    return candidates[0]


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
    }


def generate_mascot_commentary(payload: dict) -> dict:
    """Generate one ephemeral contextual remark for the desktop mascot bubble."""
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
    recent = [
        " ".join(str(r).split())[:120]
        for r in (req.get("recentComments") or [])
        if str(r or "").strip()
    ][-5:]

    def _fallback_or_skip(reason: str) -> dict:
        if not allow_fallback:
            return _skipped(reason)
        fb_remark, fb_expr = _build_contextual_fallback_remark(
            clean_ctx, personality=personality, recent=recent
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
            timeout=0.7,
        )
    else:
        warm_ok = bool(endpoint and (preferred_model or api_format == "gemini"))
        warm_model = preferred_model or "default"
        is_vlm = _is_vlm_name(warm_model)

    if not warm_ok or not warm_model:
        return _fallback_or_skip("no_warm_model")

    char_name = "Octo" if str(req.get("character") or "").lower() == "octo" else "Aqua"
    lang_raw = str(req.get("language") or "auto").lower()
    lang = "vi" if lang_raw.startswith("vi") else ("en" if lang_raw.startswith("en") else "auto")

    lang_rule = (
        "Write in natural, warm Vietnamese."
        if lang == "vi"
        else ("Write in natural, concise English matching user context." if lang == "en" else "Match the user's primary language (English or Vietnamese).")
    )
    tone_hint = (
        "soft and unhurried"
        if personality == "calm"
        else ("playful and upbeat" if personality == "energetic" else "warm, observant, and friendly")
    )
    sys_msg = (
        f"You are {char_name}, a tiny translucent desktop companion mascot sitting quietly at the screen edge. "
        f"Look briefly at the user's ambient desktop state and say ONE short, {tone_hint} check-in or companion remark "
        f"(10-22 words max). {lang_rule} "
        "Do NOT answer like a formal chatbot or list commands. Do NOT include URLs, Markdown, file paths, or repeat recent remarks. "
        'Reply ONLY as compact JSON: {"remark": "...", "expression": "calm|happy|curious|sleepy|excited|working"}. {REASON:none}'
    )

    recent_block = f"\nAvoid repeating: {json.dumps(recent, ensure_ascii=False)}" if recent else ""
    ctx_json = json.dumps(clean_ctx, ensure_ascii=False)

    visual_opt_in = bool(req.get("visualPeekEnabled"))
    want_visual = bool(visual_opt_in and is_vlm and is_opaque_title(app_id, title))
    peek_img: Path | None = None
    tier = "text"

    try:
        user_content: str | list[dict]
        if want_visual:
            peek_img = _capture_ephemeral_peek_jpeg()
        if peek_img is not None and peek_img.is_file():
            tier = "visual_peek"
            b64 = base64.b64encode(peek_img.read_bytes()).decode("ascii")
            user_content = [
                {
                    "type": "text",
                    "text": f"{{REASON:none}}\nAmbient context: {ctx_json}{recent_block}\nBriefly react as a desktop mascot to what you notice cleanly.",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
            ]
        else:
            user_content = f"{{REASON:none}}\nAmbient desktop context: {ctx_json}{recent_block}"

        body: dict = {
            "model": warm_model,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.72,
            "max_tokens": 60,
            "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if "mtp" in warm_model.lower():
            body["repetition_penalty"] = 1.0
            body["repeat_penalty"] = 1.0

        raw_out = _post_micro_completion(
            endpoint,
            body,
            timeout=8.5,
            api_key=api_key,
            api_format=api_format,
            model_name=warm_model,
        )
        remark, expr = _parse_remark_response(raw_out)
        if not remark:
            return _fallback_or_skip("empty_llm_output")
        return {
            "ok": True,
            "skipped": False,
            "skipReason": "",
            "remark": remark,
            "expression": expr,
            "tier": tier,
            "ephemeral": True,
            "model": warm_model,
        }
    except Exception as exc:
        return _fallback_or_skip(f"error:{type(exc).__name__}")
    finally:
        if peek_img is not None:
            try:
                peek_img.unlink(missing_ok=True)
            except OSError:
                pass
