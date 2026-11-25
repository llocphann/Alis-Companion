#!/usr/bin/env python3
"""Silent offscreen & isolated regression suite for:
1. Embedded AquaTalkCloud button alignment (starter chips, header buttons, voice button, AquaChatSidebar buttons).
2. Chat Left Sidebar + Swarm Right Sidebar mutual exclusion on narrow/medium widths (< 680px),
   clean side-by-side coexistence on wide widths (>= 680px), and keeping shared Chatbox composer
   visible & non-overlapping when Swarm covers the chat body in narrow mode.
3. Installed-layout `daemon_call.py` / `companion_daemon.py` `--host-root` / `ALIS_ROOT` propagation
   (prevents `ModuleNotFoundError: gguf_runtime` -> `daemon_eof`).
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("ALIS_COMPANION_SOURCE", Path(__file__).resolve().parents[1])).resolve()
DEFAULT_HOST = Path(
    os.environ.get("ALIS_COMPANION_TEST_HOST")
    or os.environ.get("ALIS_ROOT")
    or str(ROOT.parent / "Alis")
).resolve()


def _find_by_object_name_js() -> str:
    return """
    function findObj(item, name) {
        if (!item) return null;
        if (item.objectName === name) return item;
        const kids = item.data ?? item.children ?? [];
        for (let i = 0; i < kids.length; ++i) {
            const m = findObj(kids[i], name);
            if (m) return m;
        }
        return null;
    }
    function assert(cond, msg) {
        if (!cond) throw new Error(msg);
    }
    function approxEq(a, b, tol, msg) {
        if (Math.abs(a - b) > tol)
            throw new Error(msg + " (got " + a + " vs " + b + ", tol=" + tol + ")");
    }
    """


class EmbeddedSidebarSwarmAndDaemonTests(unittest.TestCase):
    def test_01_embedded_layout_coexistence_and_alignment_offscreen(self) -> None:
        qs = shutil.which("qs") or shutil.which("quickshell")
        self.assertTrue(qs, "Quickshell (qs) is required")
        self.assertTrue((DEFAULT_HOST / "shell.qml").is_file(), f"Missing Alis host at {DEFAULT_HOST}")

        spec = importlib.util.spec_from_file_location("val_mod", ROOT / "scripts/validate.py")
        val_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(val_mod)

        with tempfile.TemporaryDirectory(prefix="aqua-embedded-ui-") as tmp:
            tmp_path = Path(tmp)
            if os.environ.get("ALIS_COMPANION_TEST_HOST"):
                staged = DEFAULT_HOST
            else:
                staged = val_mod.stage(DEFAULT_HOST, tmp_path / "host")

            shell_qml = staged / "test_embedded_chat.qml"

            shell_qml.write_text(
                f"""import QtQuick
import Quickshell
import "services"
import "optional/alis-companion/services"
import "modules/common"
import "optional/alis-companion/modules/abyss/companion"

ShellRoot {{
    id: root
    {_find_by_object_name_js()}

    Window {{
        id: win
        visible: true
        width: 430
        height: 680

        Item {{
            id: hostContainer
            anchors.fill: parent
            property bool panelVisible: true

            AquaCompanionChatView {{
                id: chatView
                anchors.fill: parent
                visible: true
            }}
        }}
    }}

    property int step: 0

    Timer {{
        id: driver
        interval: 80
        repeat: true
        running: true
        onTriggered: {{
            try {{
                const cloud = chatView.cloud;
                if (!cloud) return;

                if (root.step === 0) {{
                    if (!Config.ready) return;
                    AquaMind.history = [];
                    AquaMind.historyLoaded = true;
                    root.step = 1;
                    return;
                }}

                if (root.step === 1) {{
                    const spacer = findObj(cloud, "aquaStarterChipsRow")?.parent?.parent;
                    const chips = findObj(cloud, "aquaStarterChipsRow");
                    assert(spacer && chips && chips.visible, "Starter chips must be visible at 430px empty state");
                    const p = chips.mapToItem(spacer, 0, 0);
                    const leftMargin = p.x;
                    const rightMargin = spacer.width - (p.x + chips.width);
                    approxEq(leftMargin, rightMargin, 3.0, "Starter chips must be horizontally centered at 430px");

                    for (const idName of ["aquaStarterChip-summarize-screen", "aquaStarterChip-system-status", "aquaStarterChip-remind-15m", "aquaStarterChip-search-notes"]) {{
                        const btn = findObj(cloud, idName);
                        assert(btn && btn.visible, "Missing chip " + idName);
                        const cp = btn.mapToItem(chips, 0, 0);
                        assert(cp.x >= -1 && cp.x + btn.width <= chips.width + 1, idName + " clipped horizontally");
                    }}

                    const voiceBtn = findObj(cloud, "aquaChatVoiceButton");
                    const sendBtn = findObj(cloud, "aquaChatSend");
                    assert(voiceBtn && sendBtn, "Composer buttons missing");
                    approxEq(voiceBtn.width, 30, 0.5, "Voice button width must match round buttons (30px)");
                    approxEq(voiceBtn.height, 30, 0.5, "Voice button height must be 30px");
                    approxEq(sendBtn.width, 30, 0.5, "Send button width must be 30px");

                    // Now open Left Chat Sidebar at 430px
                    findObj(cloud, "aquaChatSidebarToggle").clicked();
                    root.step = 2;
                    return;
                }}

                if (root.step === 2) {{
                    const sidebar = findObj(cloud, "aquaChatSidebar");
                    assert(cloud.sidebarOpen === true && sidebar && sidebar.visible, "Chat Left Sidebar failed to open at 430px");
                    const newChatBtn = findObj(cloud, "aquaSidebarNewChat");
                    const newProjBtn = findObj(cloud, "aquaSidebarNewProject");
                    assert(newChatBtn && newProjBtn, "Sidebar buttons missing");
                    assert(newChatBtn.height === 28 && newProjBtn.height === 28, "Sidebar button heights mismatch");

                    // Open Swarm while Left Chat Sidebar is open at 430px -> must auto-close Left Sidebar!
                    cloud.openSwarm();
                    root.step = 3;
                    return;
                }}

                if (root.step === 3) {{
                    const sidebar = findObj(cloud, "aquaChatSidebar");
                    const swarm = findObj(cloud, "aquaSwarmRightSidebar");
                    const composer = findObj(cloud, "aquaChatComposer");
                    assert(cloud.swarmOpen === true, "Swarm should be open at step 3");
                    assert(cloud.sidebarOpen === false, "Left Chat Sidebar must close when Swarm opens at 430px");
                    assert(!sidebar || !sidebar.visible, "Chat Left Sidebar must not remain visible behind Swarm at 430px");
                    assert(swarm && swarm.visible, "Swarm Right Sidebar must be visible at 430px");
                    assert(composer && composer.visible, "Shared Chatbox composer must stay visible when Swarm is open at 430px");
                    const swarmBottom = swarm.mapToItem(cloud, 0, swarm.height).y;
                    const composerTop = composer.mapToItem(cloud, 0, 0).y;
                    assert(swarmBottom <= composerTop + 1.0,
                        "Swarm Right Sidebar (" + swarmBottom + ") overlapped shared composer (" + composerTop + ")");
                    assert(!findObj(cloud, "aquaStarterChipsRow")?.visible, "Empty state chips should hide when Swarm covers chat body");

                    // Click Chat Left Sidebar toggle while Swarm is open at 430px -> must close Swarm cleanly!
                    findObj(cloud, "aquaChatSidebarToggle").clicked();
                    root.step = 4;
                    return;
                }}

                if (root.step === 4) {{
                    const sidebar = findObj(cloud, "aquaChatSidebar");
                    const swarm = findObj(cloud, "aquaSwarmRightSidebar");
                    assert(cloud.sidebarOpen === true && sidebar && sidebar.visible, "Left Sidebar did not open");
                    assert(cloud.swarmOpen === false, "Swarm must close when opening Left Sidebar at 430px");
                    assert(!swarm || !swarm.visible, "Swarm must not stay visible behind Left Sidebar");

                    // Also check medium width 560px (between 540 and 680): still mutually exclusive!
                    win.width = 560;
                    root.step = 5;
                    return;
                }}

                if (root.step === 5) {{
                    cloud.openSwarm();
                    root.step = 6;
                    return;
                }}

                if (root.step === 6) {{
                    assert(cloud.swarmOpen === true && cloud.sidebarOpen === false,
                        "Medium width 560px must keep Swarm and Left Sidebar mutually exclusive");

                    // Wide width 760px: both sidebars may coexist side-by-side without overlapping content!
                    win.width = 760;
                    root.step = 7;
                    return;
                }}

                if (root.step === 7) {{
                    findObj(cloud, "aquaChatSidebarToggle").clicked();
                    root.step = 8;
                    return;
                }}

                if (root.step === 8) {{
                    const sidebar = findObj(cloud, "aquaChatSidebar");
                    const swarm = findObj(cloud, "aquaSwarmRightSidebar");
                    const checkIn = findObj(cloud, "aquaChatCheckInCard");
                    assert(cloud.canShowBothSidebarsSideBySide === true, "Expected side-by-side mode at 760px");
                    assert(cloud.sidebarOpen && sidebar && sidebar.visible, "Left sidebar should remain open at 760px");
                    assert(cloud.swarmOpen && swarm && swarm.visible, "Swarm sidebar should remain open at 760px");
                    assert(checkIn && checkIn.visible, "Center chat column should be visible at 760px");
                    const sbRight = sidebar.x + sidebar.width;
                    const bodyLeft = checkIn.mapToItem(cloud, 0, 0).x;
                    const bodyRight = bodyLeft + checkIn.width;
                    const swarmLeft = swarm.x;
                    assert(sbRight < bodyLeft, "Left sidebar (" + sbRight + ") overlaps center body (" + bodyLeft + ")");
                    assert(bodyRight <= swarmLeft + 1, "Center body (" + bodyRight + ") overlaps Swarm sidebar (" + swarmLeft + ")");
                    assert(checkIn.width >= 180, "Center body too narrow: " + checkIn.width);

                    // Min width 320px: check starter chips still don't overflow
                    cloud.swarmOpen = false;
                    cloud.sidebarOpen = false;
                    win.width = 320;
                    root.step = 9;
                    return;
                }}

                if (root.step === 9) {{
                    if (cloud.swarmExpandProgress > 0.001) return;
                    const chips = findObj(cloud, "aquaStarterChipsRow");
                    const spacer = chips?.parent?.parent;
                    assert(chips && spacer && chips.visible, "Starter chips missing at 320px");
                    const p = chips.mapToItem(spacer, 0, 0);
                    approxEq(p.x, spacer.width - (p.x + chips.width), 3.0, "Starter chips must stay centered at 320px");
                    for (const idName of ["aquaStarterChip-summarize-screen", "aquaStarterChip-system-status", "aquaStarterChip-remind-15m", "aquaStarterChip-search-notes"]) {{
                        const btn = findObj(cloud, idName);
                        assert(btn && btn.visible, "Missing chip at 320px: " + idName);
                        const cp = btn.mapToItem(chips, 0, 0);
                        assert(cp.x >= -1 && cp.x + btn.width <= chips.width + 1, idName + " clipped at 320px");
                    }}
                    console.log("EMBEDDED_SIDEBAR_SWARM_UI_PASS");
                    driver.stop();
                    Qt.quit();
                }}
            }} catch (err) {{
                console.error("EMBEDDED_SIDEBAR_SWARM_UI_FAIL: " + err);
                driver.stop();
                Qt.quit();
            }}
        }}
    }}
}}
""",
                encoding="utf-8",
            )

            native_spec = importlib.util.spec_from_file_location("native_session", staged / "scripts/native_test_session.py")
            native_session = importlib.util.module_from_spec(native_spec)
            native_spec.loader.exec_module(native_session)

            wayland_dir = tmp_path / "wayland"
            wayland_dir.mkdir(parents=True, exist_ok=True)
            with native_session.private_wayland(wayland_dir) as wl_env:
                self.assertTrue(wl_env, "private_wayland virtual compositor unavailable")
                env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "NIRI_SOCKET", "QS_CONFIG_NAME", "QS_CONFIG_PATH", "QS_MANIFEST")}
                env.update(wl_env)
                env.update({
                    "HOME": str(tmp_path / "home"),
                    "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
                    "XDG_STATE_HOME": str(tmp_path / "state"),
                    "XDG_CACHE_HOME": str(tmp_path / "cache"),
                    "XDG_DATA_HOME": str(tmp_path / "data"),
                    "QT_QUICK_BACKEND": "software",
                    "QT_QUICK_CONTROLS_STYLE": "Basic",
                    "QT_NO_XDG_DESKTOP_PORTAL": "1",
                    "ALIS_TEST_ISOLATED": "1",
                    "AQUA_TEST_VAULT": str(tmp_path / "vault"),
                })
                for d in ("home", "cfg", "state", "cache", "data", "vault"):
                    (tmp_path / d).mkdir(parents=True, exist_ok=True)

                proc = subprocess.Popen(
                    ["dbus-run-session", "--", qs, "--path", str(shell_qml)],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                try:
                    out, _ = proc.communicate(timeout=45)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    out, _ = proc.communicate()
                    self.fail(f"QML harness timed out:\n{out}")

            self.assertIn("EMBEDDED_SIDEBAR_SWARM_UI_PASS", out, f"QML test failed:\n{out}")
            self.assertNotIn("EMBEDDED_SIDEBAR_SWARM_UI_FAIL", out, out)
            self.assertNotIn("Binding loop", out, out)

    def test_02_installed_layout_daemon_call_no_daemon_eof(self) -> None:
        self.assertTrue((DEFAULT_HOST / "scripts/ai/gguf_runtime.py").is_file(), f"Missing host scripts/ai at {DEFAULT_HOST}")
        with tempfile.TemporaryDirectory(prefix="aqua-installed-daemon-") as tmp:
            tmp_path = Path(tmp)
            # Simulate `~/.local/share/alis-companion/releases/<sha>/scripts/aqua` with NO sibling Alis folder.
            rel_aqua = tmp_path / "share/alis-companion/releases/deadbeef/scripts/aqua"
            shutil.copytree(ROOT / "scripts/aqua", rel_aqua, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            rt_dir = tmp_path / "run"
            rt_dir.mkdir(parents=True, exist_ok=True)
            sock_path = rt_dir / f"alis-companion-ai-{os.getuid()}.sock"

            env = {k: v for k, v in os.environ.items() if k not in ("ALIS_ROOT", "ALIS_COMPANION_NO_DAEMON")}
            env.update({
                "HOME": str(tmp_path / "home"),
                "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
                "XDG_STATE_HOME": str(tmp_path / "state"),
                "XDG_CACHE_HOME": str(tmp_path / "cache"),
                "XDG_RUNTIME_DIR": str(rt_dir),
                "ALIS_COMPANION_DAEMON_SOCK": str(sock_path),
            })
            for d in ("home", "cfg", "state", "cache"):
                (tmp_path / d).mkdir(parents=True, exist_ok=True)

            try:
                proc = subprocess.run(
                    [sys.executable, str(rel_aqua / "daemon_call.py"), "--host-root", str(DEFAULT_HOST), "--use-daemon"],
                    input=json.dumps({"action": "chat_threads", "subaction": "list"}) + "\n",
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}")
                data = json.loads(proc.stdout.strip())
                self.assertTrue(data.get("ok"), f"daemon_call failed (expected ok=True, zero daemon_eof): {data}")
            finally:
                # Shutdown spawned daemon so tempdir cleans up cleanly
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import sys; sys.path.insert(0, sys.argv[1]); "
                            "import companion_daemon as cd; cd.shutdown_daemon()"
                        ),
                        str(rel_aqua),
                    ],
                    env=env,
                    capture_output=True,
                    timeout=5,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
