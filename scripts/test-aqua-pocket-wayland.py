#!/usr/bin/env python3
"""External Qt client -> actual actor DropArea over a private Wayland seat.

Uses only a nested compositor created by this process and synthetic files.
No host input, model, real vault, package activation or privileged devices.
"""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from native_test_session import private_wayland

ROOT=Path(__file__).resolve().parents[1]
fixture=runpy.run_path(str(ROOT/'scripts/aqua-manual-visual-matrix.py'))
FILES=ROOT/'scripts/aqua-fixtures/pocket'
QML='''import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.services
import qs.modules.common
import "optional/alis-companion/services"
import "optional/alis-companion/modules/abyss/companion"
PanelWindow {
 id:root;anchors {top:true;bottom:true;left:true;right:true}
 color:"transparent";exclusionMode:ExclusionMode.Ignore
 WlrLayershell.layer:WlrLayer.Overlay
 WlrLayershell.keyboardFocus:WlrKeyboardFocus.None
 mask:Region {item:actor}
 AbyssCompanion {
  id:actor;x:root.width*.7;y:root.height*.6
  motionEnabled:false;effectsEnabled:false;renderQuality:"performance";reveal:1
 }
 AquaPocketDrop {id:drop;parent:actor;actor:actor;allowed:true}
 Connections {
  target:drop
  function onEntered(event):void {console.log("POCKET_ENTER",event.hasUrls,event.urls.length,event.supportedActions,event.proposedAction,event.accepted)}
  function onDropped(event):void {console.log("POCKET_DROP",event.hasUrls,event.urls.length,event.action,event.accepted,JSON.stringify(Array.from(event.urls,value=>String(value))))}
 }
 IpcHandler {
  target:"pocketProof"
  function consent(value:bool):void {Config.setNestedValue("abyss.companion.pocketEnabled",value)}
  function refresh():void {AquaPocket.show()}
  function layout(character:string,edge:string):void {
   actor.character=character;actor.edge=edge
   actor.x=edge==="left"?12:edge==="right"?root.width-actor.width-12:root.width*.7
   actor.y=edge==="top"?12:edge==="bottom"?root.height-actor.height-12:root.height*.6
  }
  function state():string {return JSON.stringify({ready:Config.ready && AlisCompanion.available && actor.inputReady,
   permitted:AquaPocket.permitted,busy:AquaPocket.busy,notice:AquaPocket.notice,
   items:AquaPocket.items,containsDrag:drop.containsDrag,
   count:AquaPocket.items.length,width:root.width,height:root.height,
   x:actor.x+actor.width/2,y:actor.y+actor.height/2,enabled:drop.enabled})}
 }
 Timer {interval:100;running:Config.ready;onTriggered: {
  AquaPocket.hostAvailable=true;Config.setNestedValue("abyss.companion.pocketEnabled",true)
 }}
}'''

def stop(process):
    if process and process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)

def until(callback, seconds=6):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        result=callback()
        if result:return result
        time.sleep(.08)
    raise RuntimeError('private Wayland proof deadline exceeded')

def main():
    if not os.environ.get('WAYLAND_DISPLAY'):
        print('SKIP: private Wayland parent unavailable');return 77
    if not importlib.util.find_spec('PySide6'):
        print('SKIP: installed PySide6 drag source unavailable');return 77
    with tempfile.TemporaryDirectory(prefix='companion-pocket-wayland-') as temporary:
        private=Path(temporary);shell,xdg=fixture['staged'](private)
        (shell/'shell.qml').write_text(QML)
        original=private/'selected.txt';original.write_bytes(b'original private content\n')
        protocol=FILES/'wlr-virtual-pointer-unstable-v1.xml'
        for kind,name in [('client-header','pointer-protocol.h'),('private-code','pointer-protocol.c')]:
            subprocess.run(['wayland-scanner',kind,str(protocol),str(private/name)],check=True,capture_output=True)
        flags=subprocess.check_output(['pkg-config','--cflags','--libs','wayland-client'],text=True).split()
        subprocess.run(['cc','-O2','-I'+str(private),str(FILES/'pointer.c'),str(private/'pointer-protocol.c'),
                        '-o',str(private/'pointer')]+flags,check=True,capture_output=True)
        source_command=[sys.executable,str(FILES/'source.py')]
        if os.environ.get('POCKET_CPP_SOURCE'):
            qtflags=subprocess.check_output(['pkg-config','--cflags','--libs','Qt6Widgets'],text=True).split()
            subprocess.run(['c++','-O2','-fPIC',str(FILES/'source.cpp'),'-o',str(private/'source')]+qtflags,check=True,capture_output=True)
            source_command=[str(private/'source')]
        compositor=private/'compositor';compositor.mkdir()
        processes=[]
        try:
            with private_wayland(compositor) as nested:
                if not nested:
                    print('SKIP: private Wayland compositor unavailable');return 77
                assert nested['WAYLAND_DISPLAY']!=os.environ['WAYLAND_DISPLAY']
                env=fixture['private_env'](xdg,private/'unused.json')
                env.update(WAYLAND_DISPLAY=nested['WAYLAND_DISPLAY'],NIRI_SOCKET=nested['NIRI_SOCKET'],
                    XDG_RUNTIME_DIR=os.environ['XDG_RUNTIME_DIR'],QT_QPA_PLATFORM='wayland',
                    QT_QUICK_BACKEND='software',QT_QUICK_CONTROLS_STYLE='Basic',QT_QPA_PLATFORMTHEME='generic',
                    QT_NO_XDG_DESKTOP_PORTAL='1',INIR_GGUF_ROOTS='[]',
                    POCKET_PARENT_WAYLAND=os.environ['WAYLAND_DISPLAY'],POCKET_PRIVATE_WAYLAND=nested['WAYLAND_DISPLAY'],
                    POCKET_SOURCE_URLS=json.dumps([original.as_uri()]))
                targetlog=private/'target.log';sourcelog=private/'source.log'
                # Portal transfer keys belong to a session bus. Both clients
                # share this private bus, as real desktop peers do; different
                # buses yield an opaque key which the product correctly denies.
                bus=subprocess.Popen(['dbus-daemon','--session','--nofork','--print-address=1'],env=env,
                    stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True)
                processes.append(bus)
                if not select.select([bus.stdout],[],[],3)[0]:raise RuntimeError('private bus did not initialize')
                env['DBUS_SESSION_BUS_ADDRESS']=bus.stdout.readline().strip()
                if os.environ.get('POCKET_WAYLAND_DEBUG'):env['WAYLAND_DEBUG']='client'
                with targetlog.open('w') as log:
                    target=subprocess.Popen(['qs','-p',str(shell)],env=env,cwd=ROOT,
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=True);processes.append(target)
                def state():
                    run=subprocess.run(['qs','ipc','-p',str(shell),'call','pocketProof','state'],env=env,
                        capture_output=True,text=True,timeout=2)
                    try:return json.loads(run.stdout) if run.returncode==0 else {}
                    except ValueError:return {}
                geometry=until(lambda:(lambda s:s if s.get('ready') and s.get('enabled') else None)(state()),10)
                with sourcelog.open('w') as log:
                    source=subprocess.Popen(source_command,env=env,cwd=ROOT,
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=True);processes.append(source)
                until(lambda:'POCKET_SOURCE_READY' in sourcelog.read_text())
                pointerenv=env.copy();pointerenv.pop('WAYLAND_DEBUG',None)
                pointer=subprocess.Popen([str(private/'pointer')],env=pointerenv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,text=True,start_new_session=True);processes.append(pointer)
                if not select.select([pointer.stdout],[],[],4)[0]:raise RuntimeError('private pointer did not initialize')
                line=pointer.stdout.readline().strip()
                if line!='POCKET_POINTER_READY':raise RuntimeError('private compositor has no virtual pointer')
                def send(command):
                    pointer.stdin.write(command+'\n');pointer.stdin.flush()
                    if command=='q':return
                    if not select.select([pointer.stdout],[],[],4)[0]:
                        raise RuntimeError('private pointer command was not applied')
                    assert pointer.stdout.readline().strip()=='POCKET_POINTER_APPLIED'
                    time.sleep(.07)
                def drag(release=True,leave=False):
                    started=sourcelog.read_text().count('POCKET_DRAG_STARTED')
                    send('m 1000 2500');send('b 1');send('m 1250 2500')
                    until(lambda:sourcelog.read_text().count('POCKET_DRAG_STARTED')==started+1)
                    for step in range(1,13):
                        x=.125+step/12*(geometry['x']/geometry['width']-.125)
                        y=.25+step/12*(geometry['y']/geometry['height']-.25)
                        send('m %d %d'%(x*10000,y*10000))
                    if leave:send('m 4500 5000')
                    if release:send('b 0')
                drag()
                result=until(lambda:next((json.loads(line) for line in sourcelog.read_text().splitlines()
                    if line.startswith('{"result"')),None))
                assert result['result']=='CopyAction',(result,geometry,state())
                after=until(lambda:(lambda s:s if s.get('count')==1 and not s.get('busy') else None)(state()))
                assert original.read_bytes()==b'original private content\n'
                subprocess.run(['qs','ipc','-p',str(shell),'call','pocketProof','consent','false'],env=env,
                    check=True,capture_output=True,text=True,timeout=2)
                until(lambda:state().get('enabled') is False)
                drag()
                def results():
                    return [json.loads(line) for line in sourcelog.read_text().splitlines() if line.startswith('{"result"')]
                disabled=until(lambda:results() if len(results())==2 else None)
                assert disabled[1]['result']=='IgnoreAction',disabled
                assert original.read_bytes()==b'original private content\n'
                assert state()['count']==0
                # Upgrade a sanitized v1 store without changing existing IDs,
                # timestamps or source. Restart the actual service before use.
                stop(target)
                legacy=private/'legacy.txt';legacy.write_bytes(b'legacy original\n')
                database=xdg/'state/alis/companion/pocket.sqlite3'
                with sqlite3.connect(database) as db:
                    db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?)',
                        (27,str(legacy),legacy.name,16,41,43,'picker'))
                    saved=db.execute('SELECT * FROM items ORDER BY id').fetchall()
                with targetlog.open('a') as log:
                    target=subprocess.Popen(['qs','-p',str(shell)],env=env,cwd=ROOT,
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=True);processes.append(target)
                until(lambda:state().get('enabled'),10)
                def ipc(*arguments):
                    subprocess.run(['qs','ipc','-p',str(shell),'call','pocketProof',*arguments],env=env,
                        check=True,capture_output=True,text=True,timeout=2)
                ipc('refresh');until(lambda:state().get('count')==2 and not state().get('busy'))
                def expect(action,previous):
                    rows=until(lambda:results() if len(results())==previous+1 else None)
                    assert rows[-1]['result']==action,(rows[-1],state())
                for character in ['aqua','octo']:
                    for edge in ['top','bottom','left','right']:
                        ipc('layout',character,edge);time.sleep(.1);geometry=state()
                        previous=len(results());drag();expect('CopyAction',previous)
                        until(lambda:state().get('count')==2 and not state().get('busy'))
                        with sqlite3.connect(database) as db:
                            assert db.execute('SELECT * FROM items ORDER BY id').fetchall()==saved
                previous=len(results());drag(release=False)
                until(lambda:state().get('containsDrag'))
                send('m 4500 5000');send('b 0');expect('IgnoreAction',previous)
                assert state()['count']==2
                previous=len(results());drag(release=False)
                until(lambda:state().get('containsDrag'))
                ipc('consent','false');until(lambda:state().get('enabled') is False)
                send('b 0');expect('IgnoreAction',previous);assert state()['count']==0
                # A Move-only source must never receive an acknowledgement
                # that could authorize deleting its original file.
                stop(source);ipc('consent','true');ipc('refresh')
                until(lambda:state().get('count')==2 and not state().get('busy'))
                previous=len(results());ready=sourcelog.read_text().count('POCKET_SOURCE_READY')
                moveenv=dict(env,POCKET_SOURCE_ACTIONS='move-only')
                with sourcelog.open('a') as log:
                    source=subprocess.Popen(source_command,env=moveenv,cwd=ROOT,
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=True);processes.append(source)
                until(lambda:sourcelog.read_text().count('POCKET_SOURCE_READY')==ready+1)
                drag();expect('IgnoreAction',previous)
                assert state()['count']==2
                with sqlite3.connect(database) as db:
                    assert db.execute('SELECT * FROM items ORDER BY id').fetchall()==saved
                assert legacy.read_bytes()==b'legacy original\n'
                assert original.read_bytes()==b'original private content\n'
                assert not any(word in targetlog.read_text() for word in ['ReferenceError:','TypeError:','Binding loop','Failed to load configuration'])
                send('q')
                print('COMPANION_POCKET_WAYLAND_PASS externalQtClient actualSeat CopyAction disabledIgnore originalIntact noHostInput cast2 edge4 legacyRestart dedupe leave revoke moveOnlyDenied')
                return 0
        except Exception:
            for file in [private/'target.log',private/'source.log']:
                if file.exists():print(file.read_text()[-6500:])
            raise
        finally:
            for process in reversed(processes):stop(process)
            retained=os.environ.get('POCKET_WAYLAND_RETAIN')
            if retained:
                destination=Path(retained);destination.mkdir(mode=0o700,parents=True,exist_ok=False)
                for name in ['target.log','source.log']:
                    if (private/name).exists():shutil.copyfile(private/name,destination/name)

if __name__=='__main__':raise SystemExit(main())
