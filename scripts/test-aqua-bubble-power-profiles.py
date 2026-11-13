#!/usr/bin/env python3
"""Offscreen runtime verification for Companion power-profile bubble modes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
ALIS_ROOT = Path(os.environ.get("ALIS_ROOT") or (SCRIPT_ROOT.parent / "Alis")).resolve()
COMPANION_DIR = (SCRIPT_ROOT / "modules/abyss/companion").resolve()

QML = '''
import QtQuick
import Quickshell
import qs.modules.common
import qs.modules.abyss.looks
import "COMPANION_BODY" as Companion

ShellRoot {
    id: root
    property int phaseStep: 0
    property int bootTicks: 0
    property int waitTicks: 0
    property real savedOrbitPhase: -1
    property string savedPositions: ""

    function fail(msg) {
        console.error("ALIS_COMPANION_BUBBLE_POWER_PROFILES_FAIL:" + msg)
        Qt.exit(1)
    }
    function require(cond, msg) {
        if (!cond) fail(msg)
    }
    function find(item, name) {
        if (!item) return null
        if (item.objectName === name) return item
        for (const child of item.children ?? []) {
            const match = root.find(child, name)
            if (match) return match
        }
        return null
    }
    function snapshotPositions(droplets) {
        const list = []
        for (let i = 0; i < droplets.count; ++i) {
            const d = droplets.itemAt(i)
            list.push([Math.round(d.x * 1000), Math.round(d.y * 1000), d.z])
        }
        return JSON.stringify(list)
    }

    Window {
        visible: true
        width: 320
        height: 260
        color: "#061320"

        Companion.AbyssCompanion {
            id: actor
            x: 100
            y: 80
            reveal: 1
            motionEnabled: true
            effectsEnabled: false
            bubblesEnabled: true
            renderQuality: "quality"
            powerProfile: "power-saver"
            nearbyInteraction: true
            pointerFresh: true
            quietSettled: false
        }
    }

    Timer {
        interval: 60
        running: true
        repeat: true
        onTriggered: {
            if (!Config.ready || actor.presentation < 0.999) {
                root.require(++root.bootTicks < 100, "boot/presentation timeout")
                return
            }
            const body = root.find(actor, "aquaLiquidBody")
            const droplets = root.find(body, "aquaExternalDroplets")
            root.require(body !== null && droplets !== null, "missing body/droplets")

            if (root.phaseStep === 0) {
                // 1. Power Saving: static 2D bubbles, visible even with effectsEnabled=false, never rotating.
                root.require(droplets.count === 6, "power-saver count expected 6, got " + droplets.count)
                root.require(body.bubble2DMode === true, "expected bubble2DMode in power-saver")
                for (let i = 0; i < droplets.count; ++i) {
                    const item = droplets.itemAt(i)
                    const d2d = root.find(item, "aquaDroplet2D")
                    const d3d = root.find(item, "aquaDroplet3D")
                    root.require(item.z === 1, "2D bubble z must be 1")
                    root.require(d2d !== null && d2d.visible === true, "aquaDroplet2D must be visible")
                    root.require(d3d === null || d3d.visible === false, "aquaDroplet3D must be hidden in 2D")
                }
                root.savedOrbitPhase = body.orbitPhase
                root.savedPositions = root.snapshotPositions(droplets)
                root.waitTicks = 0
                root.phaseStep = 1
            } else if (root.phaseStep === 1) {
                if (++root.waitTicks < 4) return
                root.require(body.orbitPhase === root.savedOrbitPhase, "power-saver must not advance orbitPhase")
                root.require(root.snapshotPositions(droplets) === root.savedPositions, "power-saver must stay stationary")

                // 2. Switch to Balanced (Normal) & idle: 3D mode, paused when no nearby interaction.
                actor.nearbyInteraction = false
                actor.pointerFresh = false
                actor.quietSettled = true
                actor.powerProfile = "balanced"
                root.phaseStep = 2
            } else if (root.phaseStep === 2) {
                root.require(body.bubble2DMode === false, "balanced must exit bubble2DMode")
                for (let i = 0; i < droplets.count; ++i) {
                    const item = droplets.itemAt(i)
                    const d2d = root.find(item, "aquaDroplet2D")
                    const d3d = root.find(item, "aquaDroplet3D")
                    const fb = root.find(item, "aquaDropletFallback")
                    root.require(d2d === null || d2d.visible === false, "aquaDroplet2D must hide in 3D mode")
                    root.require((d3d !== null && d3d.visible) || (fb !== null && fb.active), "3D or fallback must be active")
                }
                // Wait for viewYaw Behavior (600ms) from clearing pointerFresh to settle before snapshotting positions.
                root.waitTicks = 0
                root.phaseStep = 25
            } else if (root.phaseStep === 25) {
                if (++root.waitTicks < 12) return
                root.savedOrbitPhase = body.orbitPhase
                root.savedPositions = root.snapshotPositions(droplets)
                root.waitTicks = 0
                root.phaseStep = 3
            } else if (root.phaseStep === 3) {
                if (++root.waitTicks < 4) return
                root.require(body.orbitPhase === root.savedOrbitPhase, "balanced idle must pause orbit")
                root.require(root.snapshotPositions(droplets) === root.savedPositions, "balanced idle positions must not move")

                // 3a. Balanced + nearby interaction -> rotates!
                actor.nearbyInteraction = true
                root.savedOrbitPhase = body.orbitPhase
                root.savedPositions = root.snapshotPositions(droplets)
                root.waitTicks = 0
                root.phaseStep = 4
            } else if (root.phaseStep === 4) {
                if (++root.waitTicks < 5) return
                root.require(body.orbitPhase !== root.savedOrbitPhase, "balanced nearbyInteraction must advance orbit")
                root.require(root.snapshotPositions(droplets) !== root.savedPositions, "balanced nearbyInteraction must move droplets")
                let front = 0, back = 0
                for (let i = 0; i < droplets.count; ++i) {
                    if (droplets.itemAt(i).z > 0) front++; else back++
                }
                root.require(front > 0 && back > 0, "3D orbit must have front and back depth ordering")

                // 3b. Balanced interaction ends -> pauses in place cleanly.
                actor.nearbyInteraction = false
                root.phaseStep = 5
            } else if (root.phaseStep === 5) {
                root.savedOrbitPhase = body.orbitPhase
                root.require(root.savedOrbitPhase > 0, "orbitPhase should retain non-zero paused phase")
                root.waitTicks = 0
                root.phaseStep = 6
            } else if (root.phaseStep === 6) {
                if (++root.waitTicks < 4) return
                root.require(body.orbitPhase === root.savedOrbitPhase, "balanced must stay paused after interaction ends")

                // 4. Performance: always rotates even when idle and quietSettled=true.
                actor.powerProfile = "performance"
                root.savedOrbitPhase = body.orbitPhase
                root.savedPositions = root.snapshotPositions(droplets)
                root.waitTicks = 0
                root.phaseStep = 7
            } else if (root.phaseStep === 7) {
                if (++root.waitTicks < 5) return
                root.require(body.orbitPhase !== root.savedOrbitPhase, "performance must rotate while idle")
                root.require(root.snapshotPositions(droplets) !== root.savedPositions, "performance idle must move droplets")

                // 5. bubblesEnabled=false clears bubbles cleanly.
                actor.bubblesEnabled = false
                root.phaseStep = 8
            } else if (root.phaseStep === 8) {
                root.require(droplets.count === 0, "bubblesEnabled=false must yield 0 droplets")
                console.log("ALIS_COMPANION_BUBBLE_POWER_PROFILES_PASS")
                Qt.quit()
            }
        }
    }
}
'''

with tempfile.TemporaryDirectory(prefix="alis-companion-bubble-profiles-") as temporary:
    private = Path(temporary)
    shell = private / "shell"
    shell.mkdir()
    for name in ("modules", "services", "scripts", "defaults", "translations", "assets", "sdata", "qmldir", "optional"):
        if (ALIS_ROOT / name).exists():
            (shell / name).symlink_to(ALIS_ROOT / name)
    for source in ALIS_ROOT.glob("*.qml"):
        if source.name != "shell.qml":
            (shell / source.name).symlink_to(source)
    (shell / "shell.qml").write_text(QML.replace("COMPANION_BODY", COMPANION_DIR.as_uri()))

    config = private / "config/alis"
    config.mkdir(parents=True)
    options = json.loads((ALIS_ROOT / "defaults/config.json").read_text())
    options["abyss"]["companion"]["enabled"] = False
    options["abyss"]["companionMind"]["aiEnabled"] = False
    options["abyss"]["companionMind"]["obsidianEnabled"] = False
    (config / "config.json").write_text(json.dumps(options))

    env = dict(os.environ)
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "QML_IMPORT_PATH", "QML2_IMPORT_PATH", "QS_CONFIG_NAME", "QS_CONFIG_PATH"):
        env.pop(key, None)
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        folder = private / name.lower()
        folder.mkdir(exist_ok=True)
        env["XDG_" + name + "_HOME"] = str(folder)
    env.update(
        QT_QPA_PLATFORM="offscreen",
        QT_NO_XDG_DESKTOP_PORTAL="1",
        QT_QPA_PLATFORMTHEME="generic",
        QT_LOGGING_RULES="qml.debug=true",
        NIRI_SOCKET=str(private / "unavailable-niri.sock"),
        ALIS_GGUF_ROOTS="[]",
    )
    log = private / "run.log"
    with log.open("w") as stream:
        process = subprocess.Popen(
            ["dbus-run-session", "--", "qs", "--path", str(shell / "shell.qml"), "--no-color"],
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=25)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
    text = log.read_text()
    assert code == 0 and "ALIS_COMPANION_BUBBLE_POWER_PROFILES_PASS" in text, text[-10000:]
    for error in ("ReferenceError:", "TypeError:", "Binding loop", "Unable to assign"):
        assert error not in text, text[-10000:]
print("ALIS_COMPANION_BUBBLE_POWER_PROFILES_PASS")
