#!/usr/bin/env python3
"""Silent tests for Hybrid UI+Deep Thinking two-stage pipeline & ephemeral LLM Ambient Mascot commentary."""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading

try:
    from PIL import Image
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from aqua_png_stdlib import Image

ROOT = Path(os.environ.get("ALIS_COMPANION_SOURCE", Path(__file__).resolve().parents[1])).resolve()
ALIS_ROOT = Path(os.environ.get("ALIS_ROOT") or (ROOT.parent / "Alis")).resolve()
AQUA_DIR = ROOT / "scripts" / "aqua"
if str(AQUA_DIR) not in sys.path:
    sys.path.insert(0, str(AQUA_DIR))


class _MockHybridAndLmServer(BaseHTTPRequestHandler):
    model_state = "loaded"
    model_id = "qwen3.6-vl-ui"
    model_type = "vlm"
    chat_calls: list[dict] = []

    def do_GET(self):
        if self.path == "/api/v0/models":
            body = json.dumps(
                {
                    "data": [
                        {
                            "id": self.model_id,
                            "object": "model",
                            "type": self.model_type,
                            "state": self.model_state,
                        }
                    ]
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        req = json.loads(raw or "{}")
        _MockHybridAndLmServer.chat_calls.append(req)
        msgs = req.get("messages") or []
        sys_txt = str((msgs[0] or {}).get("content") or "") if msgs else ""
        mdl = str(req.get("model") or "")

        if "Pha 2" in sys_txt or "Stage 2 (Deep Thinking)" in sys_txt or "deep" in mdl:
            reply_txt = (
                "Root-cause analysis confirms mutex contention in worker shutdown triggers the frame drop shown in Stage 1."
            )
        elif "desktop companion mascot" in sys_txt:
            reply_txt = json.dumps(
                {"remark": "Still tuning Alis-Companion in Antigravity, looking steady.", "expression": "curious"}
            )
        else:
            reply_txt = "Active window shows Antigravity editor on Niri Wayland with build logs."

        if req.get("stream"):
            body = (
                "data: "
                + json.dumps({"choices": [{"delta": {"content": reply_txt}, "finish_reason": "stop"}]})
                + "\n\ndata: [DONE]\n\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = json.dumps({"choices": [{"message": {"content": reply_txt}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _test_hybrid_ui_and_deep_thinking(tmp_dir: Path, server_port: int) -> None:
    import agent_runtime
    import history_store
    import thinking_router

    ep = f"http://127.0.0.1:{server_port}/v1/chat/completions"
    shot_path = tmp_dir / "hybrid_shot.png"
    Image.new("RGB", (640, 400), (22, 34, 48)).save(shot_path)
    os.environ["ALIS_AQUA_SCREEN_FIXTURE"] = json.dumps(
        {
            "screenshotPath": str(shot_path),
            "windowId": 77,
            "appId": "antigravity",
            "title": "Alis - Antigravity",
            "headings": ["worker_pool.rs", "Deadlock trace"],
        }
    )
    os.environ["ALIS_AQUA_ALLOW_SCREEN_CAPTURE"] = "1"
    os.environ.pop("AQUA_TEST_ENDPOINT", None)
    os.environ.pop("AI_FIXTURE_URL", None)

    dual_opts = {
        "model": "qwen-ui-3b-vl",
        "actionModel": "qwen-ui-3b-vl",
        "actionEndpoint": ep,
        "thinkingModel": "qwen3.6-deep-thinking",
        "thinkingEndpoint": ep,
        "endpoint": ep,
        "autoModelRoutingEnabled": True,
        "thinkingEffort": "auto",
    }

    hybrid_prompt = "Take a screenshot of my window and analyze deeply step by step why this race condition happens"
    route = agent_runtime.classify_prompt_model_role(hybrid_prompt, dual_opts)
    assert route["role"] == "thinking" and route["language"] == "en", route
    assert route["category"] == "hybrid_ui_thinking", route
    assert route["hybridUiThinking"] is True, route
    assert route["hybridStages"]["uiEffort"] == "off", route["hybridStages"]
    assert route["hybridStages"]["thinkingEffort"] in ("medium", "high", "xhigh", "max"), route["hybridStages"]

    # Router Python + JS parity check on hybrid_ui_thinking category
    py_eff = thinking_router.route_thinking_effort(
        "Click Save and check", {}, {"category": "hybrid_ui_thinking"}, persist_state=False
    )["effort"]
    js_path = ROOT / "modules/abyss/companion/AquaThinkingRouter.js"
    js_code = (
        "const fs=require('node:fs'), vm=require('node:vm');"
        f"const src=fs.readFileSync({json.dumps(str(js_path))},'utf8').replace(/^\\.pragma library\\s*/u,'');"
        "const ctx=vm.createContext({}); vm.runInContext(src, ctx);"
        "console.log(JSON.stringify(ctx.resolveEffort('Click Save and check', false, {category:'hybrid_ui_thinking'})));"
    )
    js_eff = json.loads(subprocess.run(["node", "-e", js_code], capture_output=True, text=True, check=True).stdout)
    assert py_eff == js_eff == "medium", f"Hybrid min_floor parity mismatch: py={py_eff}, js={js_eff}"

    # Pure fast compound prompt must remain single-stage (no hybrid Stage 2)
    fast_route = agent_runtime.classify_prompt_model_role(
        "Take a screenshot and tell me what it's like and then tell me what's the weather today.", dual_opts
    )
    assert fast_route["hybridUiThinking"] is False and fast_route["role"] == "action", fast_route

    # Two-stage end-to-end execution + history persistence parity
    _MockHybridAndLmServer.chat_calls = []
    res = agent_runtime.execute_agent_turn(
        {
            **dual_opts,
            "prompt": hybrid_prompt,
            "allowScreenshot": True,
            "synthesizeWithLlm": True,
            "persistHistory": True,
            "threadId": "chat_hybrid_test",
        },
        companion_root=ROOT,
        host_root=ALIS_ROOT,
    )
    assert res["handled"] is True, res
    assert res["hybridPipeline"]["deepExecuted"] is True, res.get("hybridPipeline")
    assert res.get("images") == [str(shot_path)], res.get("images")
    assert "mutex contention" in res["text"], res["text"]
    assert len(_MockHybridAndLmServer.chat_calls) == 2, f"Expected Stage 1 UI + Stage 2 Deep calls, got {len(_MockHybridAndLmServer.chat_calls)}"
    stage1_req, stage2_req = _MockHybridAndLmServer.chat_calls
    assert stage1_req["model"] == "qwen-ui-3b-vl", stage1_req["model"]
    assert stage1_req.get("chat_template_kwargs", {}).get("enable_thinking") is False, stage1_req
    assert stage2_req["model"] == "qwen3.6-deep-thinking", stage2_req["model"]
    assert stage2_req.get("chat_template_kwargs", {}).get("enable_thinking") is True, stage2_req
    assert "Stage 2 (Deep Thinking)" in str((stage2_req.get("messages") or [{}])[0].get("content") or ""), stage2_req

    rows = history_store.load(thread_id="chat_hybrid_test")
    assert len(rows) == 2 and "mutex contention" in rows[-1]["content"], f"Expected Stage 2 reply persisted once: {rows}"
    print("[PASS] 1. Hybrid UI + Deep Thinking two-stage routing, execution & history parity")


def _test_ambient_mascot_commentary(tmp_dir: Path, server_port: int) -> None:
    import ambient_commentary

    ep = f"http://127.0.0.1:{server_port}/v1/chat/completions"

    # Privacy & environment guards
    for app, title in (
        ("Bitwarden", "Bitwarden Vault"),
        ("zen", "GitHub - Private Browsing"),
        ("chromium", "Vietcombank Internet Banking - Login"),
        ("foot", "nvim .env"),
    ):
        assert ambient_commentary.is_sensitive_context(app, title) is True, (app, title)
        blocked = ambient_commentary.generate_mascot_commentary(
            {
                "endpoint": ep,
                "model": "qwen3.6-vl-ui",
                "providerHint": "lmstudio",
                "desktopContext": {"focusedApp": app, "focusedTitle": title},
            }
        )
        assert blocked["skipped"] is True and blocked["skipReason"] == "sensitive_context", blocked

    ps_skip = ambient_commentary.generate_mascot_commentary(
        {"endpoint": ep, "powerProfile": "power-saver", "desktopContext": {"focusedApp": "foot", "focusedTitle": "nvim"}}
    )
    assert ps_skip["skipped"] is True and ps_skip["skipReason"] == "power_saver", ps_skip
    batt_skip = ambient_commentary.generate_mascot_commentary(
        {
            "endpoint": ep,
            "batteryPercent": 15,
            "batteryCharging": False,
            "desktopContext": {"focusedApp": "foot", "focusedTitle": "nvim"},
        }
    )
    assert batt_skip["skipped"] is True and batt_skip["skipReason"] == "low_battery", batt_skip

    # Cold LM Studio guard: must skip with 0 chat_calls when state == not-loaded
    _MockHybridAndLmServer.model_state = "not-loaded"
    _MockHybridAndLmServer.chat_calls = []
    cold_skip = ambient_commentary.generate_mascot_commentary(
        {
            "endpoint": ep,
            "model": "qwen3.6-vl-ui",
            "providerHint": "lmstudio",
            "desktopContext": {"focusedApp": "antigravity", "focusedTitle": "Alis - editor"},
        }
    )
    assert cold_skip["skipped"] is True and cold_skip["skipReason"] == "no_warm_model", cold_skip
    assert len(_MockHybridAndLmServer.chat_calls) == 0, "Cold model must never trigger /v1/chat/completions"

    # Tier 1: Text-only micro-LLM when warm model is loaded
    _MockHybridAndLmServer.model_state = "loaded"
    _MockHybridAndLmServer.chat_calls = []
    tier1 = ambient_commentary.generate_mascot_commentary(
        {
            "endpoint": ep,
            "model": "qwen3.6-vl-ui",
            "providerHint": "lmstudio",
            "language": "en",
            "desktopContext": {
                "focusedApp": "antigravity",
                "focusedTitle": "Alis-Companion - edit",
                "dwellMinutes": 14,
                "weather": "27°C Light rain",
            },
            "recentComments": ["Older remark"],
        }
    )
    assert tier1["skipped"] is False and tier1["tier"] == "text" and tier1["ephemeral"] is True, tier1
    assert "Antigravity" in tier1["remark"] and tier1["expression"] == "curious", tier1
    assert len(_MockHybridAndLmServer.chat_calls) == 1
    req1 = _MockHybridAndLmServer.chat_calls[0]
    assert int(req1.get("max_tokens") or 0) <= 60 and req1.get("chat_template_kwargs", {}).get("enable_thinking") is False, req1
    assert isinstance(req1["messages"][1]["content"], str), "Tier 1 must be text-only"

    # Tier 2: Opt-in ephemeral visual peek on opaque title (<= 480px JPEG deleted after call, 0 saved screenshots)
    big_shot = tmp_dir / "opaque_src.png"
    Image.new("RGB", (1920, 1080), (40, 60, 80)).save(big_shot)
    os.environ["ALIS_AQUA_SCREEN_FIXTURE"] = json.dumps({"screenshotPath": str(big_shot)})
    shots_dir = tmp_dir / "state" / "alis-companion" / "screenshots"
    before_shots = set(shots_dir.iterdir()) if shots_dir.exists() else set()
    _MockHybridAndLmServer.chat_calls = []
    tier2 = ambient_commentary.generate_mascot_commentary(
        {
            "endpoint": ep,
            "model": "qwen3.6-vl-ui",
            "providerHint": "lmstudio",
            "visualPeekEnabled": True,
            "desktopContext": {"focusedApp": "firefox", "focusedTitle": "Firefox"},
        }
    )
    assert tier2["skipped"] is False and tier2["tier"] == "visual_peek" and tier2["ephemeral"] is True, tier2
    req2 = _MockHybridAndLmServer.chat_calls[0]
    parts = req2["messages"][1]["content"]
    img_part = next(p for p in parts if isinstance(p, dict) and p.get("type") == "image_url")
    b64_data = img_part["image_url"]["url"].split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(b64_data))) as im:
        assert max(im.size) <= 480, f"Expected downscaled <=480px peek, got {im.size}"
    after_shots = set(shots_dir.iterdir()) if shots_dir.exists() else set()
    assert after_shots == before_shots, "Visual peek must never write permanent screenshots"
    leaked_peeks = list(Path(tempfile.gettempdir()).glob("alis-companion-peek-*.jpg"))
    assert not leaked_peeks, f"Temporary visual peek leaked in /tmp: {leaked_peeks}"

    # End-to-end local_mind.py dispatch + AquaHelperReplyGuard validation
    import history_store

    db_file = history_store.path()
    conn = sqlite3.connect(str(db_file))
    before_hist = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()

    lm_proc = subprocess.run(
        ["python3", str(AQUA_DIR / "local_mind.py"), "--host-root", str(ALIS_ROOT)],
        input=json.dumps(
            {
                "action": "ambient_commentary",
                "endpoint": ep,
                "model": "qwen3.6-vl-ui",
                "providerHint": "lmstudio",
                "desktopContext": {"focusedApp": "foot", "focusedTitle": "nvim README.md"},
            }
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    guard_js = ROOT / "services" / "AquaHelperReplyGuard.js"
    check_js = (
        "const fs=require('node:fs'), vm=require('node:vm');"
        f"const raw={json.dumps(lm_proc.stdout)};"
        "const cleaned=fs.readFileSync("
        + json.dumps(str(guard_js))
        + ",'utf8').replace(/^\\.pragma library\\s*/u,'').replace(/^\\.import\\s+.*$/gmu,'var PublicReply={};');"
        "const ctx=vm.createContext({}); vm.runInContext(cleaned, ctx);"
        "const p=ctx.parse('ambient_commentary', raw, 0);"
        "if(!p.ok || p.result.ephemeral!==true) process.exit(2);"
        "console.log('GUARD_OK');"
    )
    guard_out = subprocess.run(["node", "-e", check_js], capture_output=True, text=True, check=True)
    assert "GUARD_OK" in guard_out.stdout

    conn = sqlite3.connect(str(db_file))
    after_hist = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    assert after_hist == before_hist, "Ambient mascot commentary must never persist into chat_history.sqlite3"
    print("[PASS] 2. Ambient mascot commentary (privacy, warm probe, Tier 1 text, Tier 2 peek, guard & zero history)")


def _test_offscreen_qml_load(tmp_dir: Path) -> None:
    if str(ALIS_ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ALIS_ROOT / "scripts"))
    from native_test_session import private_wayland

    shell_dir = tmp_dir / "shell"
    shell_dir.mkdir(exist_ok=True)
    for name in ("modules", "services", "scripts", "defaults", "translations", "assets", "sdata", "qmldir", "optional"):
        if (ALIS_ROOT / name).exists() and not (shell_dir / name).exists():
            (shell_dir / name).symlink_to(ALIS_ROOT / name)
    for src in ALIS_ROOT.glob("*.qml"):
        if src.name != "shell.qml" and not (shell_dir / src.name).exists():
            (shell_dir / src.name).symlink_to(src)

    comp_link = shell_dir / "comp_root"
    if not comp_link.exists():
        comp_link.symlink_to(ROOT)

    qml = '''
import QtQuick
import Quickshell
import qs.modules.common
import "comp_root/services" as CompSvc
import "comp_root/modules/abyss/companion" as CompMod
import "comp_root/modules/settings" as CompCfg

ShellRoot {
    id: root
    property int ticks: 0
    function fail(msg) { console.error("HYBRID_MASCOT_QML_FAIL:" + msg); Qt.exit(1); }
    Window {
        visible: false
        width: 460
        height: 520
        CompMod.AbyssCompanion { id: actor; reveal: 1 }
        CompMod.AquaTalkCloud { id: cloud; actor: actor; allowed: true; outputWidth: 460; outputHeight: 520 }
        CompCfg.CompanionConfig { id: cfg; width: 460; height: 420 }
    }
    Timer {
        interval: 40; running: true; repeat: true
        onTriggered: {
            if (!Config.ready) {
                if (++root.ticks > 120) root.fail("Config ready timeout");
                return;
            }
            if (CompSvc.AquaMind.ambientVisualPeek !== false) root.fail("ambientVisualPeek must default false");
            const prevLen = CompSvc.AquaMind.history.length;
            const shown = CompSvc.AquaMind.showMascotRemark("Compiling cleanly!", "curious");
            if (!shown || CompSvc.AquaMind.source !== "mascot" || CompSvc.AquaMind.text !== "Compiling cleanly!")
                root.fail("showMascotRemark failed");
            if (CompSvc.AquaMind.history.length !== prevLen)
                root.fail("showMascotRemark polluted history");
            CompSvc.AquaMind.dismissMascotRemark();
            if (CompSvc.AquaMind.text !== "" || CompSvc.AquaMind.source !== "built-in")
                root.fail("dismissMascotRemark did not reset state");
            console.log("HYBRID_MASCOT_QML_PASS");
            Qt.quit();
        }
    }
}
'''
    (shell_dir / "shell.qml").write_text(qml, encoding="utf-8")

    (tmp_dir / "cfg_qml" / "alis").mkdir(parents=True, exist_ok=True)
    opts = json.loads((ALIS_ROOT / "defaults/config.json").read_text())
    opts["abyss"]["companion"]["enabled"] = False
    opts["abyss"]["companionMind"]["aiEnabled"] = False
    (tmp_dir / "cfg_qml" / "alis" / "config.json").write_text(json.dumps(opts))

    with private_wayland(tmp_dir / "wl", isolate_xdg=False) as wl_env:
        env = os.environ.copy()
        for k in ("WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "QT_QPA_PLATFORM"):
            if wl_env and k in wl_env:
                env[k] = wl_env[k]
        env["QT_NO_XDG_DESKTOP_PORTAL"] = "1"
        env["NIRI_SOCKET"] = str(tmp_dir / "no-niri.sock")
        env["XDG_CONFIG_HOME"] = str(tmp_dir / "cfg_qml")
        env["XDG_STATE_HOME"] = str(tmp_dir / "state_qml")
        env["ALIS_COMPANION_SOURCE"] = str(ROOT)
        env["ALIS_GGUF_ROOTS"] = "[]"

        res = subprocess.run(
            ["dbus-run-session", "--", "qs", "-p", str(shell_dir / "shell.qml"), "--no-color", "--no-duplicate"],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    combined = (res.stdout or "") + "\n" + (res.stderr or "")
    assert res.returncode == 0 and "HYBRID_MASCOT_QML_PASS" in combined, f"QML test failed:\n{combined}"
    for line in combined.splitlines():
        if "WARN" in line or "ERROR" in line or "ReferenceError" in line or "TypeError" in line or "Binding loop" in line:
            if any(ok in line for ok in ("qt.qpa", "atspi", "$HYPRLAND", "Wayland", "No configuration", "FileView", "quickshell.io.socket", "Quickshell was built against Qt", "got operation finished from dropped operation")):
                continue
            raise AssertionError(f"Unexpected QML warning/error:\n{line}\nFull:\n{combined}")
    print("[PASS] 3. Silent headless Wayland QML load & ephemeral mascot API (0 warnings)")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="alis-companion-hybrid-mascot-") as tmp:
        tmp_dir = Path(tmp)
        os.environ["ALIS_TEST_ISOLATED"] = "1"
        os.environ["XDG_STATE_HOME"] = str(tmp_dir / "state")
        os.environ["XDG_CONFIG_HOME"] = str(tmp_dir / "config")
        os.environ["XDG_RUNTIME_DIR"] = str(tmp_dir / "rt")
        Path(os.environ["XDG_RUNTIME_DIR"]).mkdir(parents=True, exist_ok=True)
        os.environ["ALIS_THINKING_ROUTER_DISABLE_ML"] = "1"
        os.environ["ALIS_THINKING_ROUTER_DIR"] = str(tmp_dir / "router")
        os.environ["ALIS_THINKING_ROUTER_SOCK"] = str(tmp_dir / "rt" / "router.sock")
        os.environ["ALIS_THINKING_ROUTER_STATE"] = str(tmp_dir / "state" / "last_auto_thinking.json")

        srv = HTTPServer(("127.0.0.1", 0), _MockHybridAndLmServer)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            _test_hybrid_ui_and_deep_thinking(tmp_dir, srv.server_port)
            _test_ambient_mascot_commentary(tmp_dir, srv.server_port)
        finally:
            srv.shutdown()
            srv.server_close()
        _test_offscreen_qml_load(tmp_dir)
    print("COMPANION_HYBRID_AND_MASCOT_PASS")


if __name__ == "__main__":
    main()
