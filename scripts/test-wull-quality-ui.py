#!/usr/bin/env python3
"""Owned headless settings -> host policy -> material-uniform binding proof."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "optional/hadanion/modules/settings/CompanionConfig.qml"
catalog = json.loads((ROOT / "translations/en_US.json").read_text())
for label in re.findall(r'Translation\.tr\("([^"\n]*)"\)', PAGE.read_text()):
    assert catalog.get(label) == label, "Companion English label missing: " + label

QML = '''
import QtQuick
import Quickshell
import qs.modules.common
import qs.modules.abyss.looks
import "OPTIONAL_SETTINGS" as Settings
import "OPTIONAL_BODY" as Companion
ShellRoot {
    id: root
    property int step: 0
    property int ticks: 0
    property int settling: 0
    function require(ok, label) {
        if (!ok) { console.error("HADANION_QUALITY_FAIL:" + label); Qt.exit(1) }
    }
    function find(item, name) {
        if (item.objectName === name) return item
        for (const child of item.children ?? []) {
            const found = root.find(child, name)
            if (found) return found
        }
        return null
    }
    Settings.CompanionConfig {
        id: page
        width: 600; height: 800
        activeSection: "rendering"
    }
    Companion.WaterDropletBody {
        id: material
        motionEnabled: false
        renderQuality: AbyssRenderPolicy.wullQuality
    }
    Timer {
        interval: 100; running: true; repeat: true
        onTriggered: {
            if (!Config.ready) {
                root.require(++root.ticks < 100, "config deadline")
                return
            }
            const row = root.find(page, "companionQuality")
            root.require(row !== null, "quality control")
            if (root.step === 0) {
                Config.options.abyss.autoQuality = false
                Config.options.abyss.quality = "balanced"
                Config.options.abyss.companion.autoQuality = false
                const choices = Array.from(row.options)
                root.require(choices.length === 3, "three choices")
                root.require(choices.map(v => v.displayName).join("|") === "Performance|Balanced|Quality", "English names")
                root.require(choices.map(v => v.value).join("|") === "performance|quality|detailed", "compatibility values")
                root.require(Config.options.abyss.companion.enabled === false, "actor remains off")
                row.selected("performance")
            } else if (root.step === 1) {
                root.require(material.qualityLevel === 0 && AbyssRenderPolicy.wullQualityLabel === "Performance", "performance binding")
                row.selected("quality")
            } else if (root.step === 2) {
                root.require(material.qualityLevel === 1 && AbyssRenderPolicy.wullQualityLabel === "Balanced", "legacy cost preserved")
                row.selected("detailed")
            } else if (root.step === 3) {
                root.require(Config.options.abyss.companion.renderQuality === "detailed", "typed selection")
                root.require(material.qualityLevel === 2 && AbyssRenderPolicy.wullQualityLabel === "Quality", "detail binding")
                const shader = root.find(material, "wullVolumeMaterial")
                root.require(shader !== null && shader.rendering.x === 2, "shader receives tier two")
                Config.options.abyss.quality = "performance"
            } else if (root.step === 4) {
                root.require(material.qualityLevel === 0 && AbyssRenderPolicy.wullQualityLabel === "Performance", "shell ceiling")
                root.require(Config.options.abyss.companion.renderQuality === "detailed", "ceiling preserves choice")
                Config.options.abyss.quality = "balanced"
            } else if (root.step === 5) {
                root.require(material.qualityLevel === 2, "manual detail restored")
                Config.options.abyss.companion.autoQuality = true
            } else if (root.step === 6) {
                const expected = AbyssRenderPolicy.powerProfile === "power-saver" ? 0 : 1
                root.require(material.qualityLevel === expected, "automatic profile cost preserved")
                root.require(row.enabled === false, "automatic control")
                Config.options.abyss.companion.autoQuality = false
            } else if (root.step === 7) {
                root.require(material.qualityLevel === 2 && row.enabled, "manual mode restored")
            } else if (root.settling++ >= 6) {
                root.require(Config.options.abyss.companion.enabled === false, "no activation")
                console.log("HADANION_QUALITY_UI_BINDINGS_PASS")
                Qt.quit()
            }
            ++root.step
        }
    }
}
'''

with tempfile.TemporaryDirectory(prefix="hadanion-quality-ui-") as temporary:
    private = Path(temporary)
    shell = private / "shell"
    shell.mkdir()
    for name in ("modules", "services", "scripts", "defaults", "translations", "assets", "sdata", "qmldir", "optional"):
        (shell / name).symlink_to(ROOT / name)
    for source in ROOT.glob("*.qml"):
        if source.name != "shell.qml":
            (shell / source.name).symlink_to(source)
    qml = QML.replace("OPTIONAL_SETTINGS", (ROOT / "optional/hadanion/modules/settings").as_uri())
    qml = qml.replace("OPTIONAL_BODY", (ROOT / "optional/hadanion/modules/abyss/companion").as_uri())
    (shell / "shell.qml").write_text(qml)
    config = private / "config/alis"
    config.mkdir(parents=True)
    options = json.loads((ROOT / "defaults/config.json").read_text())
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
    env.update(QT_QPA_PLATFORM="offscreen", QT_NO_XDG_DESKTOP_PORTAL="1",
               QT_QPA_PLATFORMTHEME="generic", QT_LOGGING_RULES="qml.debug=true",
               NIRI_SOCKET=str(private / "unavailable-niri.sock"), INIR_GGUF_ROOTS="[]")
    log = private / "run.log"
    with log.open("w") as stream:
        process = subprocess.Popen(["dbus-run-session", "--", "qs", "--path", str(shell / "shell.qml"), "--no-color"],
                                   env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=20)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
    text = log.read_text()
    assert code == 0 and "HADANION_QUALITY_UI_BINDINGS_PASS" in text, text[-10000:]
    for error in ("ReferenceError:", "TypeError:", "Binding loop", "Failed to load configuration"):
        assert error not in text, text[-10000:]
    persisted = json.loads((config / "config.json").read_text())["abyss"]["companion"]
    assert persisted["renderQuality"] == "detailed" and persisted["autoQuality"] is False
    assert persisted["enabled"] is False
print("HADANION_QUALITY_UI_ENGLISH_PERSISTENCE_AND_CEILING_PASS")
