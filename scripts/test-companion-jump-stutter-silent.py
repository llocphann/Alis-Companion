#!/usr/bin/env python3
"""Silent, zero-focus Companion entrance jump-up stutter, popup auto-expansion & standalone /swarm benchmark.

Runs inside an invisible virtual Wayland session (`private_wayland`), never opening
a visible window or changing focus on the user's live desktop.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def _resolve_host_and_companion() -> tuple[Path, Path]:
    if (ROOT / "optional/alis-companion/AlisOutput.qml").is_file() and (ROOT / "scripts/native_test_session.py").is_file():
        return ROOT, ROOT / "optional/alis-companion"
    sibling_host = ROOT.parent / "Alis"
    if (sibling_host / "scripts/native_test_session.py").is_file():
        return sibling_host, ROOT
    raise SystemExit("Alis host repository required for silent Companion stutter benchmark")


def main() -> int:
    host_root, comp_root = _resolve_host_and_companion()
    if not shutil.which("qs"):
        print("SKIP: Quickshell binary unavailable")
        return 77

    sys.path.insert(0, str(host_root / "scripts"))
    sys.path.insert(0, str(comp_root / "scripts"))
    from native_test_session import private_wayland  # noqa: E402
    import aqua.swarm_store as swarm_store  # noqa: E402

    with tempfile.TemporaryDirectory(prefix="comp-jump-stutter-") as tmpdir:
        tmp = Path(tmpdir)
        if (ROOT / "optional/alis-companion/AlisOutput.qml").is_file():
            staged_host = ROOT
        else:
            val_spec = importlib.util.spec_from_file_location("comp_val", comp_root / "scripts/validate.py")
            val_mod = importlib.util.module_from_spec(val_spec)
            val_spec.loader.exec_module(val_mod)
            staged_host = val_mod.stage(host_root, tmp / "staged-host")

        fixture = runpy.run_path(str(staged_host / "scripts/aqua-manual-visual-matrix.py"))
        run_dir = tmp / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        shell, xdg = fixture["staged"](run_dir)

        # Seed standalone local /swarm state in isolated XDG_STATE_HOME
        old_xdg_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(xdg / "state")
        try:
            state = swarm_store.start_swarm_mission(
                "Jump Smoothness & UI Audit",
                [
                    {"role": "researcher", "task": "Measure entrance frame deltas"},
                    {"role": "coder", "task": "Wire RHI pipeline pre-warmer"},
                    {"role": "verifier", "task": "Confirm zero binding loops"},
                ],
                emit=False,
            )
            state = swarm_store.record_worker_completion(
                state,
                "researcher-1",
                {"summary": "0 stutter frames during jump-up entrance", "toolCount": 2, "elapsedMs": 95},
                emit=False,
            )
            state = swarm_store.mark_worker_working(state, "coder-1", emit=False)
        finally:
            if old_xdg_state is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = old_xdg_state

        qml = """import QtQuick
import Quickshell
import qs.services
import qs.modules.common
import "optional/alis-companion/services"
import "optional/alis-companion/modules/abyss/companion"

FloatingWindow {
    id: win
    visible: true
    color: "#0f151c"
    implicitWidth: 1280
    implicitHeight: 820

    property int step: 0
    property int ticks: 0
    property real lastMs: 0
    property var jumpDeltas: []
    property bool samplingJump: false
    property real compactW: 0
    property real compactH: 0
    property real swarmW: 0
    property real swarmH: 0
    property real restoredW: 0
    property string swarmSubTabCheck: ""

    FrameAnimation {
        running: true
        onTriggered: {
            const now = Date.now()
            if (win.lastMs > 0 && win.samplingJump) {
                win.jumpDeltas.push(now - win.lastMs)
            }
            win.lastMs = now
        }
    }

    AbyssCompanion {
        id: actor
        x: 560
        y: 620
        edge: "bottom"
        character: "aqua"
        motionEnabled: true
        effectsEnabled: true
        renderQuality: "balanced"
        reveal: 0.0
        Behavior on reveal { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        Behavior on y { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    }

    AquaTalkCloud {
        id: cloud
        actor: actor
        outputWidth: win.width
        outputHeight: win.height
        allowed: true
        visible: win.step >= 2
    }

    Timer {
        interval: 80
        running: true
        repeat: true
        onTriggered: {
            if (!Config.ready) return
            win.ticks++
            if (win.step === 0) {
                AquaMind.hostAvailable = true
                win.samplingJump = true
                actor.reveal = 1.0
                actor.y = 540
                win.step = 1
            } else if (win.step === 1 && win.ticks >= 6) {
                win.samplingJump = false
                AquaMind.conversationOpen = true
                cloud.swarmOpen = false
                win.step = 2
            } else if (win.step === 2 && win.ticks >= 11) {
                win.compactW = Math.round(cloud.baseCloudWidth)
                win.compactH = Math.round(cloud.baseCloudHeight)
                cloud.swarmOpen = true
                win.step = 3
            } else if (win.step === 3 && win.ticks >= 18) {
                win.swarmW = Math.round(cloud.baseCloudWidth)
                win.swarmH = Math.round(cloud.baseCloudHeight)
                if (!cloud.editing)
                    console.error("ReferenceError: Chatbox editing unexpectedly closed when Swarm Right Sidebar opened")
                if (cloud.activeSwarmPane) {
                    cloud.activeSwarmPane.activeSubTab = "members"
                    cloud.activeSwarmPane.activeSubTab = "graph"
                    win.swarmSubTabCheck = cloud.activeSwarmPane.activeSubTab
                }
                cloud.swarmOpen = false
                win.step = 4
            } else if (win.step === 4 && win.ticks >= 25) {
                win.restoredW = Math.round(cloud.baseCloudWidth)
                console.log("COMP_SILENT_BENCH=" + JSON.stringify({
                    compactW: win.compactW,
                    compactH: win.compactH,
                    swarmW: win.swarmW,
                    swarmH: win.swarmH,
                    restoredW: win.restoredW,
                    swarmSubTabCheck: win.swarmSubTabCheck,
                    jumpFrames: win.jumpDeltas.length,
                    jumpDeltas: win.jumpDeltas
                }))
                Qt.quit()
            } else if (win.ticks > 65) {
                console.error("COMP_SILENT_BENCH_TIMEOUT")
                Qt.quit()
            }
        }
    }
}
"""
        (shell / "shell.qml").write_text(qml, encoding="utf-8")

        with private_wayland(tmp / "wl", isolate_xdg=False) as wl_env:
            env = fixture["private_env"](xdg, tmp / "unused.json")
            for k in ("WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "QT_QPA_PLATFORM"):
                if wl_env and k in wl_env:
                    env[k] = wl_env[k]
            env.update(
                LC_ALL="C.UTF-8",
                QT_NO_XDG_DESKTOP_PORTAL="1",
                ALIS_GGUF_ROOTS="[]",
            )
            log_file = tmp / "comp-bench.log"
            with log_file.open("wb") as lf:
                proc = subprocess.Popen(
                    ["dbus-run-session", "--", "qs", "-p", str(shell), "--no-color"],
                    env=env,
                    cwd=staged_host,
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    proc.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(timeout=3)

        log_txt = log_file.read_text(errors="replace")
        for bad in ("ReferenceError:", "TypeError:", "Binding loop", "COMP_SILENT_BENCH_TIMEOUT"):
            assert bad not in log_txt, f"Companion silent benchmark error ({bad}):\n{log_txt[-4000:]}"

        m = re.search(r"COMP_SILENT_BENCH=(\{.*\})", log_txt)
        assert m, f"Missing COMP_SILENT_BENCH result:\n{log_txt[-4000:]}"
        res = json.loads(m.group(1))
        assert 360 <= res["compactW"] <= 400 and 410 <= res["compactH"] <= 460, f"Unexpected compact size: {res}"
        assert res["swarmW"] >= 620 and res["swarmH"] >= 500, f"Popup did not auto-expand on Swarm tab: {res}"
        assert 360 <= res["restoredW"] <= 400, f"Popup did not shrink back on Chat tab: {res}"
        assert res["swarmSubTabCheck"] == "graph", f"AquaSwarmPane tabs did not switch cleanly: {res}"
        print(
            f"COMPANION_JUMP_STUTTER_SILENT_PASS compact={res['compactW']}x{res['compactH']} "
            f"swarmExpanded={res['swarmW']}x{res['swarmH']} restored={res['restoredW']} "
            f"jumpFrames={res['jumpFrames']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
