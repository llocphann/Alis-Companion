#!/usr/bin/env python3
"""Real optional QML loader/native lifecycle using an owned host scene, no inference."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from native_test_session import private_wayland

ROOT = Path(os.environ.get("HADANION_TEST_HOST", Path(__file__).resolve().parents[1]))


def descendants(pid):
    result = set()
    pending = [pid]
    while pending:
        parent = pending.pop()
        try:
            children = [int(p) for p in Path(f"/proc/{parent}/task/{parent}/children").read_text().split()]
        except OSError:
            continue
        for child in children:
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


with tempfile.TemporaryDirectory(prefix="hadanion-host-") as temporary:
    private = Path(temporary)
    shell = private / "shell"
    shell.mkdir()
    for name in ("scripts", "defaults", "translations", "assets", "qmldir", "GlobalStates.qml"):
        (shell / name).symlink_to(ROOT / name)
    # The shipping core has no original Companion directory or singleton.
    # Discover the installed release outside the host tree, without test aliases.
    shutil.copytree(ROOT / "modules", shell / "modules",
                    ignore=lambda directory, names: ["companion"] if Path(directory) == ROOT / "modules/abyss" else [])
    shutil.copytree(ROOT / "services", shell / "services", ignore=shutil.ignore_patterns("WullMind.qml"))
    release = private / "data/hadanion/releases/fixture"
    release.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "optional/hadanion", release)
    (release.parent.parent / "current").symlink_to("releases/fixture")
    config = private / "config/alis"
    config.mkdir(parents=True)
    options = json.loads((ROOT / "defaults/config.json").read_text())
    options["panelFamily"] = "abyss"
    options["enabledPanels"] = []
    options["abyss"]["companion"]["enabled"] = False
    (config / "config.json").write_text(json.dumps(options))
    runtime = private / "runtime"
    runtime.mkdir(mode=0o700)
    (shell / "shell.qml").write_text('''
import QtQuick
import Quickshell
import qs.services
import qs.modules.common
import qs.modules.abyss
import qs.modules.settings
ShellRoot {
    id: root
    property int ticks: 0
    property var preferencesSnapshot: Config.options
    property int step: 0
    property string oldContextGeneration:""
    property var optionalHost: Hadanion
    property var stubHost: ({width:900,height:650,outputName:"fixture",presented:true,
        editorOpen:false,overviewDragging:false,dockHovered:false,
        nativeInsets:{top:16,bottom:16,left:16,right:16},closeGenericPopup:function(){}})
    property var liquid: ({participants:{},records:[],activeDialog:null,popupsOpen:false,
        _popupSlot:function(){return -1},impulse:function(){}})
    property var field: ({ready:true,framePresented:true,capacity:64,diagnostic:"",immersionComponent:null})
    property var bar: ({visible:false,layoutRecords:[]})
    property var sidebar: ({open:false,edge:"left",along:50,span:200})
    property var corners: ({notesAvailable:false,centerAvailable:false,
        notesPopup:{presentationActive:false},centerPopup:{presentationActive:false}})
    HadanionSurface {
        id: surface
        width:900;height:650
        host:root.stubHost;hostLiquid:root.liquid;hostField:root.field;hostBar:root.bar
        hostLeftPanel:root.sidebar;hostRightPanel:root.sidebar;hostCorners:root.corners
        hostUtility:({open:false});hostBarHover:({hovered:false});hostRevealHover:({hovered:false})
    }
    CompanionConfig {width:800;height:600}
    function fail(message) {console.log("HADANION_RUNTIME=FAIL "+message);Qt.quit()}
    Timer {
        interval:100;running:true;repeat:true
        onTriggered: {
            if (++root.ticks>120) {root.fail("timeout");return}
            if (!Config.ready || !Hadanion.available) return
            if (root.step===0) {
                if (Hadanion.packageRoot.indexOf("/data/hadanion/releases/")<0)
                    {root.fail("package loaded through a host-tree alias");return}
                if (Hadanion.session!==null || surface.extension!==null || surface.inputRegions.length)
                    {root.fail("disabled package owns runtime/input");return}
                Config.setNestedValue("abyss.companion.enabled",true);root.step=1
            } else if (root.step===1 && Hadanion.session?.bridge.ready && surface.extension) {
                if (Hadanion.outputs.length!==1 || !surface.extension.actor
                        || surface.inputRegions.length!==5)
                    {root.fail("output contract");return}
                console.log("HADANION_NATIVE_READY")
                if(Quickshell.env("HADANION_TEST_CONTEXT")==="1") {
                    if(Hadanion.contextApiVersion!==1 || Hadanion.contextSource!==null
                            || Hadanion.session.contextConnected){root.fail("context not supported or default-off");return}
                    Config.setNestedValues({"abyss.companion.contextEnabled":true,
                        "abyss.companion.contextAiEnabled":true})
                    root.step=10;return
                }
                Config.setNestedValue("abyss.companion.enabled",false);root.step=2
            } else if(root.step===10 && Hadanion.session?.contextConnected) {
                const feed=Hadanion.contextSource,session=Hadanion.session
                if(!feed || !feed.active || feed.bind(root)!==null){root.fail("foreign context peer accepted");return}
                feed.aiBusy=true;feed.synchronizeAgent()
                if(Object.keys(session.contextState.director.sessions).length!==1)
                    {root.fail("typed AI context was not received");return}
                // Supply only an owned synthetic host permission descriptor;
                // no extra actor/window, model, coordinates or Popup is used.
                const originalOutputs=Hadanion.outputs,bridgeSequence=session.bridge.outboundSeq
                const local={enabled:true,dragging:false,manualTravel:true,portalTravel:false,modalOpen:false}
                Hadanion.outputs=[{outputName:session.companionTargetOutput,extension:{directorContext:local}}]
                const blocked=session.presentAiExpression("happy")
                local.manualTravel=false
                const invalid=session.presentAiExpression("fly")
                const accepted=session.presentAiExpression("happy")
                const afterAi=session.bridge.outboundSeq
                const defaultAmbient=session.presentAmbientExpression("working")
                Config.setNestedValues({"abyss.companion.ambientEnabled":true,
                    "abyss.companion.ambientFocusEnabled":true})
                // Synthetic eligibility only, on this fixture's existing
                // native owner. No live Focus timer or system feed is started.
                session.mind.hostIdle=true
                local.manualTravel=true
                const blockedAmbient=session.presentAmbientExpression("working")
                local.manualTravel=false
                const acceptedAmbient=session.presentAmbientExpression("working")
                const afterAmbient=session.bridge.outboundSeq
                Config.setNestedValues({"abyss.companion.ambientEnabled":false,
                    "abyss.companion.ambientFocusEnabled":false})
                session.mind.hostIdle=Qt.binding(()=>session.bridge.activity==="idle")
                Hadanion.outputs=originalOutputs
                if(blocked || invalid || !accepted || afterAi!==bridgeSequence+1)
                    {root.fail("AI Director bypassed travel/vocabulary or lost its bounded native intent");return}
                if(defaultAmbient || blockedAmbient || !acceptedAmbient || afterAmbient!==afterAi+1)
                    {root.fail("ambient bypassed consent/travel or lost its bounded native intent");return}
                const seq=session.contextState.sequence,generation=feed.generation
                feed.frame(JSON.stringify({version:1,generation:generation,sequence:seq,
                    issuedAtMs:Date.now(),kind:"heartbeat",payload:({})}))
                feed.frame(JSON.stringify({version:1,generation:generation,sequence:seq+1,
                    issuedAtMs:Date.now(),kind:"heartbeat",payload:({prompt:"synthetic private sentinel"})}))
                if(session.contextState.sequence!==seq){root.fail("replay or unknown data consumed sequence");return}
                root.oldContextGeneration=generation
                Config.setNestedValue("abyss.companion.contextEnabled",false);root.step=11
            } else if(root.step===11 && !Hadanion.session?.contextConnected && Hadanion.contextSource===null) {
                if(Hadanion.session.contextState.enabled
                        || Object.keys(Hadanion.session.contextState.director.sessions).length)
                    {root.fail("revocation retained context");return}
                Config.setNestedValue("abyss.companion.contextEnabled",true);root.step=12
            } else if(root.step===12 && Hadanion.session?.contextConnected) {
                if(Hadanion.contextSource.generation===root.oldContextGeneration)
                    {root.fail("reconnect reused generation");return}
                root.oldContextGeneration=Hadanion.contextSource.generation
                Hadanion.session.contextState.lastReceivedMs=Date.now()-15001
                Hadanion.contextSource.send("heartbeat",({}))
                if(Hadanion.session.contextConnected){root.fail("stale context remained live");return}
                root.step=13
            } else if(root.step===13 && Hadanion.session?.contextConnected) {
                if(Hadanion.contextSource.generation===root.oldContextGeneration)
                    {root.fail("expired connection reused generation");return}
                Config.setNestedValues({"abyss.companion.contextEnabled":false,
                    "abyss.companion.contextAiEnabled":false,"abyss.companion.enabled":false})
                console.log("COMPANION_CONTEXT_RUNTIME_PASS exactPeer authenticatedObject typedFrames replay unknownReject revocation freshGeneration directorTravelPriority boundedNativeIntent noInference")
                root.step=2
            } else if (root.step===2 && !Hadanion.session && !surface.extension) {
                if (surface.inputRegions.length || surface.editing || surface.curiosityOwned || surface.waterLink)
                    {root.fail("disabled output retains interaction");return}
                Config.setNestedValue("abyss.companion.enabled",true);root.step=3
            } else if (root.step===3 && Hadanion.session?.bridge.ready && surface.extension) {
                console.log("HADANION_NATIVE_READY")
                Hadanion.refresh();root.step=4
            } else if (root.step===4 && Hadanion.session?.bridge.ready && surface.extension) {
                console.log("HADANION_NATIVE_READY")
                Config.setNestedValue("abyss.companion.enabled",false);root.step=5
            } else if (root.step===5 && !Hadanion.session && !surface.extension) {
                console.log("HADANION_RUNTIME=PASS")
                Qt.quit()
            }
        }
    }
}
''')
    with private_wayland(private) as environment:
        if environment is None:
            print("SKIP: actual Hadanion loader requires an isolated Wayland backend")
            raise SystemExit(77)
        environment.pop("INIR_COMPANIOND",None)
        environment.update(QT_QUICK_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic",
            INIR_GGUF_ROOTS="[]", QT_QPA_PLATFORMTHEME="generic", QT_NO_XDG_DESKTOP_PORTAL="1")
        log = private / "runtime.log"
        native_pids = set()
        peak = 0
        with log.open("w") as output:
            process = subprocess.Popen(["dbus-run-session", "--", "qs", "--path", str(shell / "shell.qml")],
                                       env=environment, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                end = time.monotonic() + 20
                while process.poll() is None and time.monotonic() < end:
                    live = set()
                    for pid in descendants(process.pid):
                        try:
                            command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0]
                        except OSError:
                            continue
                        if command.endswith((b"/alis-companiond", b"/inir-companiond")):
                            live.add(pid)
                    native_pids.update(live)
                    peak = max(peak, len(live))
                    time.sleep(.01)
                assert process.poll() is not None, "fixture timed out: " + log.read_text()[-12000:]
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
        text = log.read_text()
        errors = ("ReferenceError:", "TypeError:", "Unable to assign", "Binding loop", "Failed to load configuration", "is not a type")
        assert process.returncode == 0 and "HADANION_RUNTIME=PASS" in text and not any(e in text for e in errors), text[-16000:]
        if os.environ.get('HADANION_TEST_CONTEXT')=='1':
            assert 'COMPANION_CONTEXT_RUNTIME_PASS' in text,text[-16000:]
        assert peak == 1 and native_pids, f"one native owner expected: peak={peak}"
        assert all(not Path(f"/proc/{pid}").exists() for pid in native_pids), "a native process survived unload"

print("PASS: external installed package/session/output/settings without core aliases, default-off, enable/disable/refresh and exactly one reaped native owner")
if os.environ.get('HADANION_TEST_CONTEXT')=='1':
    print('COMPANION_CONTEXT_RUNTIME_PASS exactPeer typedFrames replay unknownReject revoke reconnect staleFreshGeneration directorTravelPriority boundedNativeIntent ambientDefaultOff ambientTravelPriority ambientBoundedNativeIntent noInference')
