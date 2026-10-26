#!/usr/bin/env python3
"""Run the dormant controller with real Qt JS imports and synthetic context only."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

ROOT = Path(os.environ.get("ALIS_COMPANION_SOURCE", Path(__file__).resolve().parents[1]))
qs = shutil.which("qs") or shutil.which("quickshell")
if not qs:
    raise SystemExit("Quickshell is required for the staged controller import proof")
with tempfile.TemporaryDirectory(prefix="alis-companion-cowork-qml-") as temporary:
    private = Path(temporary)
    fixture = private / "shell.qml"
    for name in ("assets/cowork/CoworkPerformance.js",
                 "modules/abyss/companion/AquaBehaviorDirector.js",
                 "modules/abyss/companion/AquaCoworkPerformance.js",
                 "modules/abyss/companion/AquaLaptopProp.qml",
                 "services/AquaCoworkController.qml"):
        destination = private / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    curves = {character.lower(): json.loads((ROOT / "assets/cowork" / (character + "LaptopMotion.json")).read_text())
              for character in ("Aqua", "Octo")}
    code = '''import QtQuick
import Quickshell
import "CONTROLLER" as Performance
import "DIRECTOR" as Director
import "modules/abyss/companion"
import "services"
ShellRoot {
    id: root
    property var curves: CURVES
    property double stamp: 1000
    QtObject {
        id: fakeBody
        function project(x, y, z) { return ({ x: x, y: y, z: z, scale: 1 }) }
    }
    AquaCoworkController {
        id: runtimeController
        clock: () => root.stamp
    }
    AquaLaptopProp {
        id: laptopProp
        body: fakeBody
        pose: runtimeController.pose
    }
    function check(ok, reason) { if (!ok) throw new Error(reason) }
    function run() {
        try {
            for (const character of ["aqua", "octo"]) {
                const s=Performance.newState(character,curves[character])
                const d=Director.newState()
                const host={enabled:true,visible:true,optedIn:true,coworkEnabled:true,
                    grounded:true,motionEnabled:true,character:character}
                Director.acceptAgent(d,{word:"working",token:"0123456789abcdef"},1000)
                const first=Performance.update(s,d,host,1000)
                check(first.phase==="intro" && !first.renderProp,"wrong intro")
                check(Performance.complete(s,d,first.epoch,2450).accepted,"external completion rejected")
                const loop=Performance.update(s,d,host,2450)
                check(loop.phase==="loop" && loop.renderProp,"completion restarted intro")
                host.dragging=true
                const canceled=Performance.update(s,d,host,2460)
                check(!canceled.active && !canceled.renderProp,"drag did not yield")
                check(!Performance.complete(s,d,loop.epoch,5000).accepted,"stale callback accepted")

                root.stamp=10000
                const rd=Director.newState()
                const rhost={enabled:true,visible:true,optedIn:true,coworkEnabled:true,
                    grounded:true,motionEnabled:true,character:character}
                runtimeController.character=character
                runtimeController.directorState=rd
                runtimeController.hostContext=rhost
                Director.acceptAgent(rd,{word:"working",token:"0123456789abcdef"},root.stamp)
                runtimeController.step()
                check(runtimeController.phase==="intro" && runtimeController.clip==="laptop_open" && !runtimeController.renderProp,"runtime intro failed")
                root.stamp+=700
                runtimeController.step()
                check(runtimeController.phase==="intro" && runtimeController.renderProp && laptopProp.laptopHinge>0,"runtime opening prop failed")
                root.stamp+=750
                runtimeController.step()
                check(runtimeController.phase==="loop" && runtimeController.clip==="laptop_agent_loop" && runtimeController.renderProp,"runtime agent loop failed")
                check(runtimeController.triggerCue("alert") && runtimeController.phase==="cue" && runtimeController.clip==="laptop_alert","runtime cue failed")
                root.stamp+=1200
                runtimeController.step()
                check(runtimeController.phase==="loop" && runtimeController.clip==="laptop_agent_loop","runtime cue return failed")
                Director.acceptAgent(rd,{word:"ended",token:"0123456789abcdef"},root.stamp)
                runtimeController.step()
                root.stamp+=500
                runtimeController.step()
                check(runtimeController.phase==="loop" && runtimeController.clip==="laptop_pause" && runtimeController.renderProp,"short context loss did not settle in pause")
                root.stamp+=600
                runtimeController.step()
                check(runtimeController.phase==="outro" && runtimeController.clip==="laptop_close","outro close did not start after grace")
                root.stamp+=1200
                runtimeController.step()
                check(runtimeController.phase==="none" && !runtimeController.active && !runtimeController.renderProp,"outro did not finish cleanly")
            }
            console.log("COMPANION_COWORK_QML_PASS pairedImports externalCompletion immediateYield staleEpoch")
            Qt.quit()
        } catch (error) {
            console.log("COMPANION_COWORK_QML_FAIL "+error)
            Qt.quit()
        }
    }
    Timer { interval: 1; running: true; repeat: false; onTriggered: root.run() }
}
'''
    code = code.replace("CONTROLLER", "assets/cowork/CoworkPerformance.js")
    code = code.replace("DIRECTOR", "modules/abyss/companion/AquaBehaviorDirector.js")
    code = code.replace("CURVES", json.dumps(curves, separators=(",", ":")))
    fixture.write_text(code)
    env = os.environ.copy()
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("NIRI_SOCKET", None)
    for name in ("config", "cache", "data", "state", "runtime"):
        folder = private / name
        folder.mkdir(mode=0o700)
        env["XDG_" + ("RUNTIME_DIR" if name == "runtime" else name.upper()+"_HOME")] = str(folder)
    env.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic",
               QT_QPA_PLATFORMTHEME="generic", QT_NO_XDG_DESKTOP_PORTAL="1")
    process = subprocess.Popen(["dbus-run-session", "--", qs, "--path", str(fixture)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               env=env, cwd=private, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        output, _ = process.communicate(timeout=3)
        raise SystemExit("Controller QML fixture timed out: " + output[-3000:])
    if process.returncode or "COMPANION_COWORK_QML_PASS" not in output or any(marker in output for marker in
        ("COMPANION_COWORK_QML_FAIL", "ReferenceError:", "TypeError:", "Binding loop", "Unable to assign")):
        raise SystemExit("Controller QML fixture failed: " + output[-4000:])
    print("COMPANION_COWORK_QML_PASS pairedImports externalCompletion immediateYield staleEpoch")
