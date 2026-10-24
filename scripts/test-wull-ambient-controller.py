#!/usr/bin/env python3
"""Owned Qt event/lifecycle proof; synthetic coarse states, no system services."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

ROOT=Path(os.environ.get('HADANION_SOURCE',Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='companion-ambient-') as temporary:
    private=Path(temporary)
    for name in ('services/WullAmbientController.qml','modules/abyss/companion/WullAmbientPolicy.js'):
        destination=private/name;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/name,destination)
    (private/'shell.qml').write_text('''
import QtQuick
import QtTest
import Quickshell
import "services"
ShellRoot {
 id:root
 property double stamp:1000000
 property var reactions:[]
 WullAmbientController {id:ambient;clock:()=>root.stamp;onReactionRequested:expression=>root.reactions.push(expression)}
 TestCase {
  id:test;when:false;optional:true
  function check(value,label):void {if(!value)throw new Error(label)}
  function run():void {
   try {
    ambient.focusPhase="off";ambient.powerBand="normal";ambient.playback="paused";wait(300)
    check(reactions.length===0 && ambient.reason==="disabled","default-off reaction")
    ambient.eligible=true;ambient.permitted=true;wait(300)
    check(reactions.length===0 && ambient.reason==="initial_snapshot","initial state became a reaction")
    root.stamp+=1000;ambient.focusPhase="focus";wait(300)
    check(reactions.join() ==="working","existing Focus state did not propose working")
    root.stamp+=150000;ambient.powerBand="low";ambient.playback="playing";wait(300)
    check(reactions.length===1 && ambient.reason==="quiet_focus","work interrupted by ambient event")
    ambient.focusPhase="break";wait(300)
    check(reactions.join() ==="working,happy","existing break transition not observed")
    root.stamp+=150000;ambient.eligible=false;ambient.focusPhase="paused";ambient.powerBand="charging";wait(300)
    ambient.eligible=true;wait(300)
    check(reactions.length===2,"suppressed transition replayed on unlock")
    ambient.powerBand="low";wait(300)
    check(reactions.join() ==="working,happy,alert","coarse battery event missing")
    root.stamp+=150000;ambient.powerBand="charging";ambient.permitted=false;wait(300)
    check(reactions.length===3 && ambient.reason==="disabled","revocation retained queued reaction")
    ambient.permitted=true;wait(300)
    check(reactions.length===3,"re-enable replayed initial state")
    ambient.consentKey="changed";ambient.playback="paused";wait(300)
    check(reactions.length===3,"source consent change emitted a cue")
    root.stamp--;ambient.playback="playing";wait(300)
    check(reactions.length===3 && ambient.reason==="invalid_clock","backwards clock emitted reaction")
    ambient.permitted=false
    console.log("COMPANION_AMBIENT_QML_PASS defaultOff existingFocus quietWork suppressedNoReplay boundedQueue cancelRevoke sourceConsent clock noSystemObservation noInference")
   }catch(e){console.error("COMPANION_AMBIENT_QML_FAIL",e.message)}
   shutdown.start()
  }
 }
 Timer {interval:1;running:true;onTriggered:test.run()}
 Timer {id:shutdown;interval:1;onTriggered:Qt.quit()}
}
''')
    env=os.environ.copy()
    for key in ('DISPLAY','WAYLAND_DISPLAY','NIRI_SOCKET','QS_CONFIG_NAME','QS_CONFIG_PATH',
                'QS_MANIFEST','DBUS_SESSION_BUS_ADDRESS','DBUS_SYSTEM_BUS_ADDRESS'):
        env.pop(key,None)
    for kind in ('config','data','state','cache','runtime'):
        folder=private/kind;folder.mkdir(mode=0o700)
        env['XDG_'+('RUNTIME_DIR' if kind=='runtime' else kind.upper()+'_HOME')]=str(folder)
    env.update(QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QT_NO_XDG_DESKTOP_PORTAL='1')
    process=subprocess.Popen(['dbus-run-session','--','qs','-p',str(private),'--no-color'],env=env,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
    try:output,_=process.communicate(timeout=12)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGTERM)
        try:output,_=process.communicate(timeout=3)
        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);output,_=process.communicate(timeout=3)
        raise SystemExit('Owned ambient fixture timed out: '+output[-4000:])
    if process.returncode or 'COMPANION_AMBIENT_QML_PASS' not in output or any(word in output for word in
            ('COMPANION_AMBIENT_QML_FAIL','TypeError:','ReferenceError:','Binding loop','Unable to assign','SyntaxError:')):
        raise SystemExit(output[-6000:])
print('COMPANION_AMBIENT_QML_PASS defaultOff existingFocus quietWork suppressedNoReplay boundedQueue cancelRevoke sourceConsent clock noSystemObservation noInference')
