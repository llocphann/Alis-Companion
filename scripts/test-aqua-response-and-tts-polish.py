#!/usr/bin/env python3
"""Silent tests for Cloud-AI response presentation, Arch/Niri/Alis grounding, and Speech-First TTS distillation."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

from PIL import Image

ROOT = Path(os.environ.get("ALIS_COMPANION_SOURCE", Path(__file__).resolve().parents[1]))
AQUA_DIR = ROOT / "scripts" / "aqua"
if str(AQUA_DIR) not in sys.path:
    sys.path.insert(0, str(AQUA_DIR))

import agent_runtime  # noqa: E402
import desktop_control  # noqa: E402


def _assert_tts_distillation() -> None:
    noisy_example = """**🖥 Alis - Alis - Antigravity** · `niri+vision` *(refreshed)*
  - The active window is titled "Alis - Alis - Antigravity (antigravity)" and appears to be a code editor with a dark theme.
  - On the left sidebar of the application, there are navigation tabs including “Antigravity”, “Workspace”, and project folders such as “Alis”.
  - A floating chat interface overlay is visible at the bottom-left, showing an ongoing conversation in Vietnamese and English.
  - The top bar shows time (“18:38:35”), date (“Friday, 20/11/26”), and temperature (“27°C”).
  - The overall desktop environment suggests a developer workspace with multiple tools running simultaneously.
📷 Screenshot captured (`screenshot-1795174715555.png`)."""

    clean = desktop_control._clean_text_for_tts(noisy_example, context_prompt="describe my screen")
    assert clean, "Expected non-empty distilled TTS speech"
    for forbidden in ("🖥", "niri+vision", "refreshed", "📷", "Screenshot captured", "screenshot-1795174715555.png"):
        assert forbidden not in clean, f"Forbidden noise token {forbidden!r} leaked into TTS text: {clean!r}"
    assert not clean.startswith(("-", "*")) and "\n-" not in clean, f"Expected Markdown bullet prefixes converted to prose: {clean!r}"
    assert len(clean) <= 520, f"Expected TTS text <= 520 chars, got {len(clean)}: {clean!r}"
    assert clean.endswith((".", "!", "?", "…")), f"Expected sentence-boundary ending without mid-word cut: {clean!r}"
    assert "active window" in clean.lower(), f"Expected meaningful content preserved: {clean!r}"

    # Intentional emoji question preserves icons cleanly
    icon_kept = desktop_control._clean_text_for_tts(
        "The emoji 📷 means camera or screenshot captured.",
        context_prompt="biểu tượng 📷 nghĩa là gì?",
    )
    assert "📷" in icon_kept, f"Expected icon kept when prompt specifically asks about icons: {icon_kept!r}"


def _assert_tts_language_detection(tmp_dir: Path) -> None:
    mixed_en = (
        "The active window in Alis shows a code editor and terminal running automated checks, "
        "with a compact chat overlay displaying a chào greeting from the session."
    )
    assert desktop_control._detect_tts_language(mixed_en, requested="auto") == "en", (
        f"Expected mixed mostly-English reply to stay 'en'"
    )
    assert desktop_control._detect_tts_language("Màn hình hiện đang mở trình soạn thảo mã Alis.", requested="auto") == "vi"
    assert desktop_control._detect_tts_language("Chào bạn!", requested="auto") == "vi"

    out_mix = tmp_dir / "mix_en.wav"
    res_mix = desktop_control.voice_tts(mixed_en, language="auto", out_path=str(out_mix))
    assert res_mix["neuralVoice"] == "en-US-AvaNeural", f"Expected en-US-AvaNeural, got {res_mix}"

    out_vi = tmp_dir / "vi_only.wav"
    res_vi = desktop_control.voice_tts("Thời tiết hiện tại ở khu vực của bạn khá mát mẻ.", language="auto", out_path=str(out_vi))
    assert res_vi["neuralVoice"] == "vi-VN-HoaiMyNeural", f"Expected vi-VN-HoaiMyNeural, got {res_vi}"


def _assert_unaccented_vi_detection() -> None:
    for p in ("chup man hinh mo ta giup toi", "thoi tiet hom nay the nao", "ban thay gi tren man hinh bay gio"):
        assert agent_runtime.detect_language(p) == "vi", f"Expected 'vi' for unaccented Vietnamese prompt: {p!r}"
    assert agent_runtime.detect_language("describe what is on my screen") == "en"


class _MockVlmHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, format, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        body = json.loads(raw)
        type(self).calls.append(body)
        is_voice = any("spoken sentences" in str(m.get("content") or "").lower() for m in body.get("messages") or [])
        reply_text = (
            "Your screen shows the Alis code editor on Niri with a clean test suite passing in the bottom panel."
            if is_voice
            else "You are working in the **Alis** code editor on Niri Wayland, with documentation checks passing.\n\n- **Left Sidebar**: Project files and Workspace navigation.\n- **Bottom Panel**: Automated verification results (`ALIS_MD_BROKEN_LINKS: 0`)."
        )
        payload = {"choices": [{"message": {"content": reply_text}}]}
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _assert_vlm_grounding_and_clean_surface(tmp_dir: Path) -> None:
    shot_path = tmp_dir / "screenshot-999111222.png"
    Image.new("RGB", (320, 180), color=(14, 24, 34)).save(shot_path)

    fixture_payload = {
        "windowId": "win-99",
        "appId": "antigravity",
        "title": "Alis - Alis - Antigravity",
        "screenshotPath": str(shot_path),
        "headings": ["ALIS_MD_BROKEN_LINKS : PASS"],
        "elements": [],
    }
    os.environ["XDG_STATE_HOME"] = str(tmp_dir / "state")
    os.environ["XDG_CONFIG_HOME"] = str(tmp_dir / "config")
    os.environ["ALIS_AQUA_SCREEN_FIXTURE"] = json.dumps(fixture_payload)
    os.environ["ALIS_AQUA_ALLOW_SCREEN_CAPTURE"] = "1"
    # Keep _MockVlmHandler testable when synthesizeWithLlm=True while clearing external fixture URLs
    os.environ.pop("AQUA_TEST_ENDPOINT", None)
    os.environ.pop("AI_FIXTURE_URL", None)

    server = HTTPServer(("127.0.0.1", 0), _MockVlmHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    ep = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    try:
        # 1. Text turn with live VLM response -> clean BLUF output without debug banner/footer
        alis_root = ROOT.parent / "Alis"
        _MockVlmHandler.calls = []
        txt_res = agent_runtime.execute_agent_turn(
            {
                "prompt": "what is on my screen?",
                "endpoint": ep,
                "model": "qwen3.5-vl-test",
                "synthesizeWithLlm": True,
                "allowScreenshot": True,
                "persistHistory": False,
            },
            companion_root=ROOT,
            host_root=alis_root,
        )
        assert txt_res["handled"] is True and txt_res["intent"] == "screen_observe"
        assert _MockVlmHandler.calls, "Expected live mock VLM call"
        usr_txt = ""
        for part in _MockVlmHandler.calls[-1]["messages"][1]["content"]:
            if isinstance(part, dict) and part.get("type") == "text":
                usr_txt = part.get("text", "")
        assert "Arch Linux" in usr_txt and "Niri" in usr_txt and "NEVER call it macOS" in usr_txt, (
            f"Expected host platform grounding in VLM user prompt: {usr_txt!r}"
        )
        reply = txt_res["text"]
        assert "**🖥" not in reply and "niri+vision" not in reply and "Screenshot captured" not in reply, (
            f"Expected clean Cloud-AI answer without raw telemetry banner/footer: {reply!r}"
        )
        assert txt_res.get("images") == [str(shot_path)], f"Expected attached screenshot preserved: {txt_res.get('images')}"

        # 2. Voice turn with live VLM response -> spoken-friendly prompt & clean sentence-bounded TTS output
        _MockVlmHandler.calls = []
        voice_res = agent_runtime.execute_agent_turn(
            {
                "prompt": "take a screenshot and tell me what is on my screen",
                "endpoint": ep,
                "model": "qwen3.5-vl-test",
                "synthesizeWithLlm": True,
                "allowScreenshot": True,
                "fromVoice": True,
                "persistHistory": False,
            },
            companion_root=ROOT,
            host_root=alis_root,
        )
        assert voice_res["handled"] is True and voice_res["voiceReply"]["spoken"] is True
        sys_txt = _MockVlmHandler.calls[-1]["messages"][0]["content"]
        assert "spoken sentences" in sys_txt.lower(), f"Expected voice-specific VLM system prompt: {sys_txt!r}"
        spoken = voice_res["voiceReply"]["spokenText"]
        assert "niri+vision" not in spoken and "screenshot-" not in spoken
        assert spoken.endswith(".") and voice_res["voiceReply"]["neuralVoice"] == "en-US-AvaNeural"
    finally:
        server.shutdown()
        server.server_close()


with tempfile.TemporaryDirectory(prefix="companion-response-tts-") as tmp:
    tmp_dir = Path(tmp)
    _assert_tts_distillation()
    _assert_tts_language_detection(tmp_dir)
    _assert_unaccented_vi_detection()
    _assert_vlm_grounding_and_clean_surface(tmp_dir)

print("COMPANION_RESPONSE_AND_TTS_POLISH_PASS")
