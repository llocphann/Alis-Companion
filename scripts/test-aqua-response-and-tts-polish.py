#!/usr/bin/env python3
"""Silent tests for single fixed multilingual voice, streaming TTS chunking, dual-channel speech, and compound multi-intent routing."""
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


def _assert_tts_distillation_and_unit_naturalization() -> None:
    noisy_example = """**🖥 Alis - Alis - Antigravity** · `niri+vision` *(refreshed)*
  - The active window is titled "Alis - Alis - Antigravity (antigravity)" and appears to be a code editor with a dark theme.
  - On the left sidebar of the application, there are navigation tabs including “Antigravity”, “Workspace”, and project folders such as “Alis”.
  - A floating chat interface overlay is visible at the bottom-left, showing an ongoing conversation in Vietnamese and English.
  - The top bar shows time (“18:38:35”), date (“Friday, 20/11/26”), and temperature (“27°C”).
  - The overall desktop environment suggests a developer workspace with multiple tools running simultaneously.
📷 Screenshot captured (`screenshot-1795174715555.png`)."""

    clean = desktop_control._clean_text_for_tts(noisy_example, context_prompt="describe my screen")
    assert clean, "Expected non-empty distilled TTS speech"
    for forbidden in ("🖥", "niri+vision", "refreshed", "📷", "Screenshot captured", "screenshot-1795174715555.png", "°C"):
        assert forbidden not in clean, f"Forbidden noise token {forbidden!r} leaked into TTS text: {clean!r}"
    assert not clean.startswith(("-", "*")) and "\n-" not in clean, f"Expected Markdown bullet prefixes converted to prose: {clean!r}"
    assert len(clean) <= 620, f"Expected TTS text <= 620 chars, got {len(clean)}: {clean!r}"
    assert clean.endswith((".", "!", "?", "…")), f"Expected sentence-boundary ending without mid-word cut: {clean!r}"
    assert "active window" in clean.lower(), f"Expected meaningful content preserved: {clean!r}"

    # Unit & air-quality naturalization (never read raw EAQI / μg/m³ / (SE) / °C / [x])
    raw_weather_dump = (
        "Current weather in **Cần Thơ, Vietnam**: **27°C** (feels like 31°C) — Light rain.\n"
        "- Range 25°C–33°C · Humidity 80% · Wind 9 km/h (SE) · Precip 0.1 mm · UV 0\n"
        "- Air quality: **EAQI 45** (Moderate) · PM2.5: 24.2 μg/m³"
    )
    spoken_weather = desktop_control._clean_text_for_tts(raw_weather_dump)
    for bad in ("EAQI", "μg/m³", "(SE)", "°C", "km/h"):
        assert bad not in spoken_weather, f"Raw unit/acronym {bad!r} leaked into spoken text: {spoken_weather!r}"
    assert "27 degrees" in spoken_weather and "kilometers per hour" in spoken_weather and "moderate" in spoken_weather.lower(), spoken_weather

    # Intentional emoji question preserves icons cleanly
    icon_kept = desktop_control._clean_text_for_tts(
        "The emoji 📷 means camera or screenshot captured.",
        context_prompt="biểu tượng 📷 nghĩa là gì?",
    )
    assert "📷" in icon_kept, f"Expected icon kept when prompt specifically asks about icons: {icon_kept!r}"

    # Sentence chunking splits smoothly without losing any clause
    chunks = desktop_control._split_spoken_sentences(
        "You have the Alis code editor open on Niri. Current weather in Cần Thơ is 27 degrees with light rain. Humidity is 80 percent and wind is 9 kilometers per hour.",
        max_chunk_chars=100,
    )
    assert len(chunks) >= 2 and all(len(c) <= 120 for c in chunks), f"Unexpected sentence chunks: {chunks}"


def _assert_single_fixed_multilingual_voice(tmp_dir: Path) -> None:
    expected = "en-US-AvaMultilingualNeural"
    samples = [
        ("mixed_en", "Current weather in Cần Thơ, Vietnam is 27 degrees, with light rain.", "auto"),
        ("pure_vi", "Thời tiết Cần Thơ hôm nay trời có mưa nhẹ và độ ẩm cao.", "vi"),
        ("short_vi", "Chào bạn!", "vi"),
        ("explicit_male_ignored", "Hello there, how can I help today?", "en-US-AndrewMultilingualNeural"),
        ("legacy_hoaimy_ignored", "Xin chào buổi tối!", "vi-VN-HoaiMyNeural"),
        ("espeak_ignored", "Testing single voice guarantee.", "espeak-ng"),
    ]
    for name, txt, req_v in samples:
        out_wav = tmp_dir / f"{name}.wav"
        res = desktop_control.voice_tts(txt, voice=req_v, language=req_v, out_path=str(out_wav))
        assert res["ok"] is True and res["spoken"] is True, res
        assert res["voice"] == expected and res["neuralVoice"] == expected, (
            f"Voice drifted on {name}: got voice={res['voice']!r}, neuralVoice={res['neuralVoice']!r}"
        )
        assert res["ttsBackend"] == "fixture-edge-tts", res

    status = desktop_control.voice_status()
    assert status["canonicalVoice"] == expected, status


def _assert_unaccented_vi_and_intent_guards() -> None:
    for p in ("chup man hinh mo ta giup toi", "thoi tiet hom nay the nao", "ban thay gi tren man hinh bay gio"):
        assert agent_runtime.detect_language(p) == "vi", f"Expected 'vi' for unaccented Vietnamese prompt: {p!r}"
    assert agent_runtime.detect_language("describe what is on my screen") == "en"

    # Single-topic screen_time must NOT trigger screen_observe or compound_task
    st_intent = agent_runtime.classify_intent("How much screen time did I use today?", {})
    assert st_intent["kind"] == "alis_local_info" and st_intent["topic"] == "screentime", st_intent

    # Exact user compound prompt must resolve to compound_task preserving order: screen_observe -> weather
    comp = agent_runtime.classify_intent(
        "Take a screenshot and tell me what it's like and then tell me what's the weather today.",
        {},
    )
    assert comp["kind"] == "compound_task", comp
    sub_kinds = [f"{s['kind']}:{s.get('topic', '')}".rstrip(":") for s in comp["subtasks"]]
    assert sub_kinds == ["screen_observe", "alis_local_info:weather"], f"Expected ordered screen+weather, got {comp}"

    # Vietnamese compound prompt
    comp_vi = agent_runtime.classify_intent("Chụp màn hình mô tả giúp tôi rồi cho biết thời tiết hôm nay thế nào?", {})
    assert comp_vi["kind"] == "compound_task", comp_vi
    assert [f"{s['kind']}:{s.get('topic', '')}".rstrip(":") for s in comp_vi["subtasks"]] == [
        "screen_observe",
        "alis_local_info:weather",
    ], comp_vi

    # Explicit Nexus-scoped search/open must stay single-intent, never double-trigger system/journal
    nx_thinkfan = agent_runtime.classify_intent("tìm thinkfan trong nexus", {})
    assert nx_thinkfan["kind"] == "nexus_knowledge" and nx_thinkfan.get("subkind") == "search", nx_thinkfan

    nx_open_daily = agent_runtime.classify_intent("mở nhật ký hôm nay trong nexus", {})
    assert nx_open_daily["kind"] == "alis_service_control" and nx_open_daily.get("action") == "nexus_open_daily", nx_open_daily

    # Explicitly compounded Nexus search + weather must keep ordered subtasks
    nx_weather = agent_runtime.classify_intent("Tìm thinkfan trong nexus rồi cho biết thời tiết hôm nay", {})
    assert nx_weather["kind"] == "compound_task", nx_weather
    assert [f"{s['kind']}:{s.get('topic') or s.get('subkind') or ''}".rstrip(":") for s in nx_weather["subtasks"]] == [
        "nexus_knowledge:search",
        "alis_local_info:weather",
    ], nx_weather


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


def _assert_vlm_grounding_and_compound_voice(tmp_dir: Path) -> None:
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
    live_snap = {
        "weather": {
            "enabled": True,
            "visibleCity": "Cần Thơ, Vietnam",
            "temp": "27°C",
            "tempFeelsLike": "31°C",
            "description": "Light rain",
            "humidity": "80%",
            "wind": "9 km/h",
            "windDir": "SE",
            "precip": "0.1 mm",
            "uv": "0",
            "tempMin": "25°C",
            "tempMax": "33°C",
            "airQuality": {"available": True, "scale": "EAQI", "aqi": 45, "label": "Moderate", "pm25": "24.2 μg/m³"},
        },
        "todos": {
            "backend": "internal",
            "openCount": 1,
            "doneCount": 2,
            "items": [{"done": False, "content": "Review Companion TTS"}],
        },
    }
    os.environ["XDG_STATE_HOME"] = str(tmp_dir / "state")
    os.environ["XDG_CONFIG_HOME"] = str(tmp_dir / "config")
    os.environ["ALIS_AQUA_SCREEN_FIXTURE"] = json.dumps(fixture_payload)
    os.environ["ALIS_AQUA_ALLOW_SCREEN_CAPTURE"] = "1"
    os.environ.pop("AQUA_TEST_ENDPOINT", None)
    os.environ.pop("AI_FIXTURE_URL", None)

    server = HTTPServer(("127.0.0.1", 0), _MockVlmHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    ep = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    alis_root = ROOT.parent / "Alis"
    try:
        # 1. Single text turn with live VLM response -> clean BLUF output without debug banner/footer
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

        # 2. Compound exact user prompt via Voice: screenshot + describe + weather
        _MockVlmHandler.calls = []
        comp_res = agent_runtime.execute_agent_turn(
            {
                "prompt": "Take a screenshot and tell me what it's like and then tell me what's the weather today.",
                "endpoint": ep,
                "model": "qwen3.5-vl-test",
                "synthesizeWithLlm": True,
                "allowScreenshot": True,
                "fromVoice": True,
                "alisLocalSnapshot": live_snap,
                "persistHistory": False,
            },
            companion_root=ROOT,
            host_root=alis_root,
        )
        assert comp_res["handled"] is True and comp_res["intent"] == "compound_task", comp_res
        assert comp_res["subtaskIntents"] == ["screen_observe", "alis_local_info:weather"], comp_res
        assert comp_res.get("images") == [str(shot_path)], f"Expected captured screenshot in compound reply: {comp_res}"
        assert "Alis code editor" in comp_res["text"] and "Cần Thơ" in comp_res["text"] and "27°C" in comp_res["text"], comp_res["text"]
        vr = comp_res["voiceReply"]
        assert vr["spoken"] is True and vr["neuralVoice"] == "en-US-AvaMultilingualNeural", vr
        assert vr.get("streamingChunks", 0) >= 2, f"Expected sentence-chunked streaming >= 2 chunks: {vr}"
        spoken = vr["spokenText"]
        assert "Alis code editor" in spoken and "Cần Thơ" in spoken and "27 degrees" in spoken, spoken
        for banned in ("EAQI", "μg/m³", "(SE)", "niri+vision", "screenshot-", "°C"):
            assert banned not in spoken, f"Leaked raw token {banned!r} in compound voiceReply: {spoken!r}"

        # 3. Compound multi-topic local query (weather + todos) without screenshot
        combo_local = agent_runtime.execute_agent_turn(
            {
                "prompt": "What's the weather today and what are my todos?",
                "fromVoice": True,
                "alisLocalSnapshot": live_snap,
                "persistHistory": False,
            },
            companion_root=ROOT,
            host_root=alis_root,
        )
        assert combo_local["handled"] is True and combo_local["intent"] == "compound_task", combo_local
        assert combo_local["subtaskIntents"] == ["alis_local_info:weather", "alis_local_info:todos"], combo_local
        assert "Cần Thơ" in combo_local["voiceReply"]["spokenText"] and "Review Companion TTS" in combo_local["voiceReply"]["spokenText"]
    finally:
        server.shutdown()
        server.server_close()


def _assert_tts_stream_player_cleanup(tmp_dir: Path) -> None:
    assert hasattr(desktop_control, "sys") and bool(desktop_control.sys.executable), "desktop_control missing sys import"
    import subprocess
    chunk_wav = tmp_dir / "stream_chunk0.wav"
    chunk_wav.write_bytes(desktop_control._EMPTY_WAV_HEADER)
    manifest_file = tmp_dir / "stream_manifest.json"
    manifest_file.write_text(
        json.dumps(
            {
                "firstAudioPath": str(chunk_wav),
                "firstMp3Path": "",
                "remainingChunks": [],
                "voice": "en-US-AvaMultilingualNeural",
                "rate": "+4%",
                "chunkDir": str(tmp_dir),
                "playBin": "/usr/bin/true",
                "ffmpegBin": "",
                "ffplayBin": "",
                "edgeCli": "",
            }
        ),
        encoding="utf-8",
    )
    stream_res = subprocess.run(
        [desktop_control.sys.executable, str(AQUA_DIR / "tts_stream_player.py"), str(manifest_file)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert stream_res.returncode == 0, f"tts_stream_player failed: rc={stream_res.returncode} err={stream_res.stderr}"
    assert not manifest_file.exists() and not chunk_wav.exists(), "tts_stream_player must unlink temp manifest & wav"


with tempfile.TemporaryDirectory(prefix="alis-companion-tts-") as tmp:
    tmp_dir = Path(tmp)
    os.environ["ALIS_TEST_ISOLATED"] = "1"
    os.environ["XDG_STATE_HOME"] = str(tmp_dir / "state")
    os.environ["XDG_CONFIG_HOME"] = str(tmp_dir / "config")
    _assert_tts_distillation_and_unit_naturalization()
    _assert_single_fixed_multilingual_voice(tmp_dir)
    _assert_unaccented_vi_and_intent_guards()
    _assert_vlm_grounding_and_compound_voice(tmp_dir)
    _assert_tts_stream_player_cleanup(tmp_dir)

print("COMPANION_RESPONSE_AND_TTS_POLISH_PASS")
