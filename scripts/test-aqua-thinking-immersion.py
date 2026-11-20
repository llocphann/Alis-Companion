#!/usr/bin/env python3
"""Silent offscreen verification for Companion immersive composable thinking animation."""
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
import "COMPANION_BODY" as Companion

ShellRoot {
    id: root
    property int charIndex: 0
    readonly property var castList: ["aqua", "octo"]
    property int phaseStep: 0
    property int bootTicks: 0
    property int waitTicks: 0
    property real savedThinkingPhase: -1

    function fail(msg) {
        console.error("AQUA_THINKING_IMMERSION_FAIL:" + msg)
        Qt.exit(1)
    }
    function require(cond, msg) {
        if (!cond) fail(msg)
    }
    function find(item, name) {
        if (!item) return null
        if (item.objectName === name) return item
        const kids = item.data ?? item.children ?? []
        for (let i = 0; i < kids.length; ++i) {
            const match = root.find(kids[i], name)
            if (match) return match
        }
        return null
    }

    Window {
        visible: true
        width: 320
        height: 260
        color: "#061320"

        Companion.AbyssCompanion {
            id: actor
            character: root.castList[root.charIndex]
            x: 110
            y: 90
            reveal: 1
            motionEnabled: true
            effectsEnabled: true
            bubblesEnabled: true
            renderQuality: "quality"
            powerProfile: "balanced"
            nearbyInteraction: false
            quietSettled: true
        }
    }

    Timer {
        interval: 45
        running: true
        repeat: true
        onTriggered: {
            if (!Config.ready || actor.presentation < 0.999) {
                root.require(++root.bootTicks < 100, "boot/presentation timeout")
                return
            }
            const body = root.find(actor, "aquaLiquidBody")
            const auraLoader = root.find(body, "aquaThinkingAuraLoader")
            const ambientLoader = root.find(body, "aquaAmbientPropLoader")
            const laptopLoader = root.find(body, "aquaLaptopPropLoader")
            const mark = root.find(body, "aquaExpressionMark")
            const glint = root.find(body, "aquaEyeThinkingGlint")
            root.require(body !== null && auraLoader !== null && ambientLoader !== null && laptopLoader !== null && mark !== null && glint !== null,
                "missing companion internal nodes")

            const tag = "[" + actor.character + "] "

            if (root.phaseStep === 0) {
                // 1. Idle state: zero aura nodes instantiated & eye glint hidden
                root.require(auraLoader.active === false && auraLoader.item === null, tag + "aura must be inactive while idle")
                root.require(body.thinkingPhase === 0, tag + "thinkingPhase must be 0 when idle")
                root.require(glint.visible === false, tag + "eye thinking glint must be hidden while idle")

                // 2. Enable thinking (no prop)
                actor.thinking = true
                actor.thinkingPhase = "thinking"
                actor.activity = "thinking"
                actor.expression = "thinking"
                root.waitTicks = 0
                root.phaseStep = 1
            } else if (root.phaseStep === 1) {
                root.require(auraLoader.active === true && auraLoader.item !== null, tag + "aura did not activate for thinking")
                for (const objName of [
                    "aquaThinkingOuterRing",
                    "aquaThinkingInnerRing",
                    "aquaThinkingHeroStar",
                    "aquaThinkingTwinStar",
                    "aquaThinkingPulseWave"
                ]) {
                    const node = root.find(auraLoader.item, objName)
                    root.require(node !== null && node.visible === true, tag + "missing/invisible " + objName)
                }
                root.require(glint.visible === true, tag + "eye thinking glint did not appear during thinking")
                root.require(actor.motionAction === "inspect", tag + "expected inspect motion when thinking without laptop")
                root.require(mark.visible === false, tag + "static '?' expression mark should hide during active thinking")
                root.savedThinkingPhase = body.thinkingPhase
                root.phaseStep = 2
            } else if (root.phaseStep === 2) {
                if (++root.waitTicks < 3) return
                root.require(body.thinkingPhase !== root.savedThinkingPhase, tag + "thinkingPhase animation did not advance")
                root.require(body.thinkingFloatY < 0, tag + "companion body did not float up while thinking")

                // 3. Stage accent checks: tool -> drafting
                actor.thinkingPhase = "tool"
                root.phaseStep = 3
            } else if (root.phaseStep === 3) {
                root.require(auraLoader.item !== null && auraLoader.item.orbitCycles === 2, tag + "tool stage did not double orbitCycles")
                actor.thinkingPhase = "drafting"
                root.phaseStep = 4
            } else if (root.phaseStep === 4) {
                root.require(auraLoader.item !== null && auraLoader.item.isDrafting === true, tag + "drafting stage did not activate")

                // 4. Compose thinking + reading book prop
                actor.thinkingPhase = "thinking"
                actor.ambientProp = "book"
                root.phaseStep = 5
            } else if (root.phaseStep === 5) {
                root.require(ambientLoader.active === true && ambientLoader.item !== null, tag + "book prop did not stay active during thinking")
                root.require(auraLoader.active === true && auraLoader.item !== null, tag + "aura must compose alongside book prop")
                root.require(body.propDampsInspect === true, tag + "propDampsInspect should calm inspect motion while reading")
                const scan = root.find(ambientLoader.item, "aquaBookThinkingScan")
                root.require(scan !== null && scan.visible === true, tag + "book page-scan shimmer missing while thinking")

                // 5. Compose thinking + laptop prop (yields inspect to prevent double pitch)
                actor.ambientProp = ""
                actor.coworkPose = ({
                    laptopHinge: 106, screenPulse: 0.55,
                    laptopTap0: 0, laptopTap1: 0,
                    laptopLift: 0, laptopSlide: 0,
                    pitch: 6, roll: 0, yaw: 0
                })
                actor.coworkPropVisible = true
                root.phaseStep = 6
            } else if (root.phaseStep === 6) {
                root.require(laptopLoader.active === true && laptopLoader.item !== null, tag + "laptop prop did not activate during thinking")
                root.require(auraLoader.active === true && auraLoader.item !== null, tag + "aura must compose alongside laptop prop")
                root.require(actor.motionAction === "", tag + "inspect clip should yield cleanly to coworkPose laptop")
                root.require(laptopLoader.item.effectiveScreenPulse >= 0.48, tag + "laptop screen glow should animate during thinking")
                const stream = root.find(laptopLoader.item, "aquaLaptopThinkingStream")
                root.require(stream !== null && stream.visible === true, tag + "laptop thinking stream missing while thinking")

                // 6. Disable effects -> zero aura overhead
                actor.coworkPropVisible = false
                actor.coworkPose = null
                actor.effectsEnabled = false
                root.phaseStep = 7
            } else if (root.phaseStep === 7) {
                root.require(auraLoader.active === false, tag + "aura must deactivate when effectsEnabled=false")

                // 7. Stop thinking -> return to idle cleanly
                actor.effectsEnabled = true
                actor.thinking = false
                actor.thinkingPhase = ""
                actor.activity = "idle"
                actor.expression = "idle"
                root.phaseStep = 8
            } else if (root.phaseStep === 8) {
                root.require(auraLoader.active === false && auraLoader.item === null, tag + "aura did not unload after thinking ended")
                root.require(body.thinkingPhase === 0, tag + "thinkingPhase must reset to 0 after thinking")
                root.require(glint.visible === false, tag + "eye thinking glint must hide after thinking ended")

                if (root.charIndex + 1 < root.castList.length) {
                    root.charIndex++
                    root.phaseStep = 0
                } else {
                    console.log("AQUA_THINKING_IMMERSION_PASS")
                    Qt.quit()
                }
            }
        }
    }
}
'''

with tempfile.TemporaryDirectory(prefix="alis-companion-thinking-immersion-") as temporary:
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
    assert code == 0 and "AQUA_THINKING_IMMERSION_PASS" in text, text[-10000:]
    for error in ("ReferenceError:", "TypeError:", "Binding loop", "Unable to assign"):
        assert error not in text, text[-10000:]
print("AQUA_THINKING_IMMERSION_PASS")
