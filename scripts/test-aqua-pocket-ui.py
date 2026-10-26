#!/usr/bin/env python3
"""Owned Wayland UI/helper proof. Picker callback is controlled fixture input.

Actual external-client Wayland drag/drop is a separate proof; no model, real
vault, live Companion or host input is used by this test.
"""
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
fixture=runpy.run_path(str(ROOT/'scripts/aqua-manual-visual-matrix.py'))
QML='''import QtQuick
import QtTest
import Quickshell
import qs
import qs.services
import qs.modules.common
import "optional/alis-companion/services"
import "optional/alis-companion/modules/abyss/companion"
FloatingWindow {
 id:root;visible:true;implicitWidth:800;implicitHeight:600;color:"#081419"
 function named(item,name) {
  if(item.objectName===name)return item
  for(const child of item.data ?? item.children ?? []) {const match=named(child,name);if(match)return match}
  return null
 }
 AbyssCompanion {id:actor;x:350;y:490;motionEnabled:false;effectsEnabled:false;renderQuality:"performance";reveal:1}
 AquaPocketDrop {id:drop;parent:actor;actor:actor;allowed:true}
 AquaTalkCloud {id:cloud;actor:actor;outputWidth:root.width;outputHeight:root.height;allowed:true}
 TestCase {
  id:test;when:false;optional:true
  function runChecks() {
   try {
    tryCompare(AlisCompanion,"available",true,3000)
    Ai._initialized=true;Ai.models=({});Ai.modelList=[];Ai.currentModelId=""
    AquaPocket.hostAvailable=true
    verify(Config.options.abyss.companion.pocketEnabled===true)
    Config.setNestedValue("abyss.companion.pocketEnabled",false)
    tryCompare(AquaPocket,"permitted",false,3000)
    verify(!AquaPocket.permitted && !AquaPocket.show() && !drop.enabled)
    verify(!AquaPocket.addFiles([Quickshell.env("POCKET_FILE_ONE")],"drop"))
    Config.setNestedValue("abyss.companion.pocketEnabled",true)
    tryCompare(AquaPocket,"permitted",true,3000)
    verify(AquaPocket.show());tryCompare(AquaPocket,"busy",false,3000)
    tryVerify(()=>cloud.visible && actor.inputReady && drop.enabled,3000)
    verify(!cloud.editing && cloud.controlsVisible && AquaPocket.items.length===0)
    verify(!drop.accepts({hasUrls:true,supportedActions:Qt.MoveAction,urls:["file:///a"]}))
    verify(drop.accepts({hasUrls:true,supportedActions:Qt.CopyAction|Qt.MoveAction,urls:["file:///a"]}))
    verify(!drop.accepts({hasUrls:false,supportedActions:Qt.CopyAction,urls:[]}))
    const choose=root.named(cloud,"aquaPocketChoose")
    verify(choose && choose.visible)
    mouseClick(choose)
    tryCompare(GlobalStates,"settingsNativeDialogOpen",true,3000)
    const picker=root.named(cloud,"aquaPocketFileDialog")
    verify(picker && picker.visible)
    // Controlled selection signal exercises the production acceptance handler.
    // This is not represented as a human/native file-selection acceptance.
    picker.selectedFile=Quickshell.env("POCKET_FILE_ONE")
    picker.accepted();picker.close()
    tryCompare(AquaPocket,"busy",false,3000)
    tryCompare(GlobalStates,"settingsNativeDialogOpen",false,3000)
    compare(AquaPocket.items.length,1)
    const oldId=AquaPocket.items[0].id,oldCreated=AquaPocket.items[0].created_ms
    AquaPocket.close();verify(AquaPocket.show());tryCompare(AquaPocket,"busy",false,3000)
    compare(AquaPocket.items[0].id,oldId);compare(AquaPocket.items[0].created_ms,oldCreated)
    for(const character of ["aqua","octo"])for(const edge of ["top","bottom","left","right"]) {
     actor.character=character;actor.edge=edge
     actor.x=edge==="left"?12:edge==="right"?root.width-actor.width-12:350
     actor.y=edge==="top"?12:edge==="bottom"?root.height-actor.height-12:240
     wait(50)
     verify(cloud.visible && cloud.controlsVisible && !cloud.editing && !actor.rolling)
     verify(cloud.x>=0 && cloud.y>=0 && cloud.x+cloud.width<=root.width && cloud.y+cloud.height<=root.height)
    }
    actor.x=350;actor.y=490
    verify(AquaPocket.addFiles([Quickshell.env("POCKET_FILE_ONE"),Quickshell.env("POCKET_FILE_TWO")],"drop"))
    tryCompare(AquaPocket,"busy",false,3000);compare(AquaPocket.items.length,2)
    compare(AquaPocket.items.find(row=>row.id===oldId).created_ms,oldCreated)
    wait(80)
    const remove=root.named(cloud,"aquaPocketRemove-"+oldId)
    verify(remove && remove.visible);mouseClick(remove)
    tryCompare(AquaPocket,"busy",false,3000);compare(AquaPocket.items.length,1)
    const clear=root.named(cloud,"aquaPocketClear")
    mouseClick(clear);compare(AquaPocket.items.length,1)
    mouseClick(clear);tryCompare(AquaPocket,"busy",false,3000);compare(AquaPocket.items.length,0)
    verify(AquaPocket.addFiles([Quickshell.env("POCKET_FILE_TWO")],"drop"))
    Config.setNestedValue("abyss.companion.pocketEnabled",false)
    tryVerify(()=>!AquaPocket.permitted && !AquaPocket.busy && !AquaPocket.opened && AquaPocket.items.length===0,3000)
    tryCompare(AquaPocket,"draining",false,3000)
    verify(!drop.enabled)
    wait(150);compare(AquaPocket.items.length,0)
    // Existing chat/context ownership still preempts the Pocket view.
    Config.setNestedValue("abyss.companion.pocketEnabled",true)
    verify(AquaPocket.show());tryCompare(AquaPocket,"busy",false,3000)
    AquaMind.conversationOpen=true;wait(40)
    verify(!AquaPocket.opened && cloud.editing)
    AquaMind.dismiss()
    AquaPocket.hostAvailable=false
    console.log("COMPANION_POCKET_UI_PASS cast2 edge4 defaultOff pickerCallback typedHelper stableIds clearConfirmation revoke noModel")
   } catch(e) {console.error("COMPANION_POCKET_UI_FAIL",String(e))}
   shutdown.start()
  }
 }
 Timer {interval:200;running:Config.ready;onTriggered:test.runChecks()}
 Timer {id:shutdown;interval:100;onTriggered:Qt.quit()}
}'''

if not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP: Pocket UI requires the validator-owned Wayland session')
    raise SystemExit(77)
with tempfile.TemporaryDirectory(prefix='companion-pocket-ui-') as temporary:
    private=Path(temporary);shell,xdg=fixture['staged'](private)
    # Sanitized legacy config lacks the new field and retains unrelated edits.
    config=xdg/'config/alis/config.json'
    old=json.loads(config.read_text())
    old['abyss']['companion'].pop('pocketEnabled',None)
    old['pocket_fixture_unrelated']={'text':'preserved owner edit','version':7}
    config.write_text(json.dumps(old))
    files=[private/'chosen <plain>.txt',private/'second.md']
    for n,file in enumerate(files):file.write_bytes(('private %d\n'%n).encode())
    protected=[xdg/'state/legacy-history.sqlite3',xdg/'state/legacy-memory.sqlite3',private/'vault.md']
    for file in protected:file.write_bytes(b'private unrelated bytes\n')
    (shell/'shell.qml').write_text(QML)
    env=fixture['private_env'](xdg,private/'unused.json')
    env.update(QT_QPA_PLATFORM='wayland',WAYLAND_DISPLAY=os.environ['WAYLAND_DISPLAY'],
               XDG_RUNTIME_DIR=os.environ['XDG_RUNTIME_DIR'],QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic',QT_QPA_PLATFORMTHEME='generic',QT_NO_XDG_DESKTOP_PORTAL='1',
               ALIS_GGUF_ROOTS='[]',ALIS_AQUA_HISTORY_DB=str(protected[0]),ALIS_AQUA_MEMORY_DB=str(protected[1]),
               POCKET_FILE_ONE=files[0].as_uri(),POCKET_FILE_TWO=files[1].as_uri())
    with (private/'run.log').open('w') as log:
        process=subprocess.Popen(['dbus-run-session','--','qs','--path',str(shell/'shell.qml')],
            cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:code=process.wait(timeout=32)
        except subprocess.TimeoutExpired:code=124
        finally:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=3)
    output=(private/'run.log').read_text()
    bad=('COMPANION_POCKET_UI_FAIL','ReferenceError:','TypeError:','SyntaxError:',
         'Unable to assign','Binding loop','Failed to load configuration','FAIL!')
    if code or 'COMPANION_POCKET_UI_PASS' not in output or any(value in output for value in bad):
        print(output[-12000:]);raise SystemExit(1)
    for n,file in enumerate(files):assert file.read_bytes()==('private %d\n'%n).encode()
    for file in protected:assert file.read_bytes()==b'private unrelated bytes\n'
    after=json.loads(config.read_text())
    assert after['pocket_fixture_unrelated']==old['pocket_fixture_unrelated']
    print('COMPANION_POCKET_UI_PASS cast2 edge4 defaultOff pickerCallback typedHelper stableIds clearConfirmation revoke noModel originalsAndLegacyPreserved')
