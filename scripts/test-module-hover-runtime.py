#!/usr/bin/env python3
"""Bounded real-package Qt hover comparison. Does not assert wl_pointer delivery."""
import hashlib,importlib.util,json,os,subprocess,sys,tempfile
from pathlib import Path
PACKAGE=Path(os.environ.get('HADANION_SOURCE',str(Path(__file__).resolve().parents[1]))).resolve()
ROOT=Path(os.environ.get('HADANION_TEST_HOST',str(PACKAGE.parent/'Alis'))).resolve()
sys.path.insert(0,str(ROOT/'scripts'))
source_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=PACKAGE,text=True).strip()
status=subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=PACKAGE,text=True)
identity=source_sha+('-working' if status else '')
spec=importlib.util.spec_from_file_location('private_installer',PACKAGE/'scripts/install.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
from native_test_session import private_wayland,run_qs
if not (PACKAGE/'native/target/release/inir-companiond').is_file():
 raise SystemExit('Build the pinned Companion CPU actor backend first; no AI backend required')
with tempfile.TemporaryDirectory(prefix='alis-companion-hover-') as name:
 folder=Path(name)
 for entry in ['modules','services','GlobalStates.qml','qmldir','assets','scripts','defaults','translations']:(folder/entry).symlink_to(ROOT/entry)
 (folder/'shell.qml').write_text(r'''
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import Quickshell.Wayland
import qs
import qs.services
import qs.modules.common
import qs.modules.abyss
import qs.modules.abyss.looks
import "modules/abyss/looks/AbyssGeometry.js" as Geometry
ShellRoot {
 id:root
 property int frames:0
 property int checks:0
 Component.onCompleted:Quickshell.watchFiles=false
 AbyssPerimeter {id:perimeter}
 TestCase {
  id:test;when:false;optional:true
  SignalSpy {id:clicks;signalName:"clicked"}
  function check(ok,message){root.checks++;if(!ok)throw new Error(message)}
  function find(item,p){if(p(item))return item;for(const child of item.children??[]){const result=find(child,p);if(result)return result}return null}
  function render(field){const before=root.frames;check(field.grabToImage(()=>root.frames++,Qt.size(320,200)),"field render refused");tryVerify(()=>root.frames>before,2500);check(root.frames>before,"field render incomplete")}
  // Input dispatch needs real polished hit geometry, not an offscreen bitmap
  // after every move. Keep actual field draws at startup/state/popup entry;
  // per-move grabs can block an occluded compositor beyond visit deadlines.
  function observe(field,ms){wait(ms);check(waitForPolish(field.parent,2500),"input scene polish incomplete");wait(12)}
  function outside(host,field){
   const bar=find(host.contentItem,item=>typeof item.itemForId==="function" && item.layoutRecords!==undefined)
   const rectangles=[...bar.layoutRecords.map(r=>r.content),...bar.liquidController.records.map(r=>r.surface)].filter(Boolean)
   const candidates=[[.5,.5],[.9,.1],[.1,.9],[.9,.9],[.1,.1],[.5,.1],[.5,.9]]
   const point=candidates.map(p=>[host.width*p[0],host.height*p[1]]).find(p=>rectangles.every(r=>p[0]<r.x-3 || p[0]>r.x+r.width+3 || p[1]<r.y-3 || p[1]>r.y+r.height+3))
   check(point!==undefined,"no genuinely outside point available")
   mouseMove(host.contentItem,point[0],point[1]);observe(field,450)
   return point
  }
  function bridgePoint(body,bar,host){
   const horizontal=["top","bottom"].includes(body.edge),ins=host.nativeInsets
   const content=body.record.content,surface=body.rawPresentationRecord.surface
   const cross=body.edge==="left"?ins.left+6:body.edge==="right"?host.width-ins.right-6:body.edge==="top"?ins.top+6:host.height-ins.bottom-6
   const center=horizontal?content.x+content.width/2:content.y+content.height/2
   const minimum=Math.max(0,Math.ceil(horizontal?surface.x:surface.y))
   const maximum=Math.min((horizontal?host.width:host.height)-1,Math.floor((horizontal?surface.x+surface.width:surface.y+surface.height)-1))
   for(let delta=0;delta<=maximum-minimum;delta++)for(const tangent of [Math.floor(center-delta),Math.ceil(center+delta)]){
    if(tangent<minimum || tangent>maximum)continue
    const x=horizontal?tangent:cross,y=horizontal?cross:tangent
    if(Geometry.rectContains(content,x,y) || bar.layoutRecords.some(r=>Geometry.rectContains(r.content,x,y)))continue
    const local=body.hoverParent.mapFromItem(host.contentItem,x,y)
    if(body.hoverParent.contains(local) && Geometry.distance(x,y,host.width,host.height,ins,AbyssStyle.perimeterRadius,[body.rawPresentationRecord],AbyssStyle.connectionDepth,AbyssStyle.neckRadius)<0)return [x,y]
   }
   return null
  }
  function runChecks(){try{
   tryCompare(Config,"ready",true,6000)
   check(Config.options.abyss.companion.exploreFeatures===false
      && Config.options.abyss.companion.renderQuality==="performance"
      && Config.options.abyss.companionMind.talkEnabled===false,
      "private canonical configuration was not loaded")
   GlobalStates.shellEntryReady=true;GlobalStates.deferredPanelsReady=true;GlobalStates.barOpen=true
   tryVerify(()=>perimeter.outputHosts.length>0,6000)
   const host=perimeter.outputHosts[0]
   const field=find(host.contentItem,item=>typeof item.packed==="function" && item.wallpaperReady!==undefined)
   check(field!==null,"production field missing");render(field)
   tryVerify(()=>host.nativeFieldReady,6000)
   const bar=find(host.contentItem,item=>typeof item.itemForId==="function" && item.layoutRecords!==undefined)
   check(bar!==null,"production AbyssBar missing")
   tryCompare(Hadanion,"available",true,6000)
   check(Hadanion.available,"private exact package unavailable: "+Hadanion.diagnostic)
   check(Hadanion.sourceSha===Quickshell.env("ALIS_COMPANION_TEST_SHA"),"wrong private package identity")
   Weather.data={temp:"25°",wCode:"113",description:"Overcast",sunrise:"06:00",sunset:"18:00",hourly:Array.from({length:8},(_,i)=>({label:String(i*3).padStart(2,"0")+":00",temp:"25°",code:"113"}))}
   const edges=(Quickshell.env("HADANION_HOVER_EDGES")??"top,bottom,left,right").split(",")
   for(const edge of edges){
    Config.setNestedValues({"bar.vertical":["left","right"].includes(edge),"bar.bottom":["bottom","right"].includes(edge),"abyss.companion.edge":edge,
     "abyss.modules.configured":true,"abyss.modules.outputLayouts":[],"abyss.modules.placements":[{id:"clock",kind:"clock",edge:edge,position:.2},{id:"weather",kind:"weather",edge:edge,position:.45},{id:"media",kind:"media",edge:edge,position:.7}]})
    observe(field,300)
    for(const state of ["disabled","present","absent"]){
     console.info("COMPANION_PHASE_START",edge,state)
     outside(host,field)
     Config.setNestedValue("abyss.companion.enabled",state!=="disabled")
     if(state==="disabled"){
      tryVerify(()=>Hadanion.session===null,6000)
      check(findChild(host.contentItem,"wullRimPointerObserver")===null,"disabled package retained a full-output pointer observer")
      check(find(host.contentItem,item=>typeof item.stopTravel==="function" && typeof item.carryTo==="function")===null,"disabled package retained an actor")
     }
     else{
      tryVerify(()=>Hadanion.session?.bridge?.ready===true,6000)
      check(Hadanion.session?.bridge?.ready,"Companion CPU actor backend did not handshake")
      if(state==="present"){
       Hadanion.session.bridge.show()
       tryVerify(()=>Hadanion.outputs[0]?.extension?.actor?.inputReady===true,7000)
       check(Hadanion.outputs[0]?.extension?.actor?.inputReady,"actor did not become present")
      }else{
       Hadanion.session.bridge.hide()
       tryVerify(()=>Hadanion.outputs[0]?.extension?.actor?.visible===false,7000)
       check(Hadanion.outputs[0]?.extension?.actor?.visible===false,"actor did not become absent")
      }
     }
     render(field)
     console.info("COMPANION_HOVER_STATE",edge,state,JSON.stringify({layer:host.WlrLayershell.layer,focus:host.WlrLayershell.keyboardFocus,package:Hadanion.sourceSha,status:state==="disabled"?null:Hadanion.outputs[0].extension.status()}))
     for(const kind of ["clock","weather","media"]){
      outside(host,field)
      const module=bar.itemForId(kind)
      check(module && module.width>0 && module.height>0,"module unavailable "+kind+" "+edge)
      const popup=module.companionPopup
      check(popup && popup._anchorReady,"mature popup source unavailable "+kind+" "+edge)
      mouseMove(module,module.width/2,module.height/2);observe(field,450)
      render(field)
      console.info("COMPANION_HOVER_ENTER",edge,state,kind,JSON.stringify({module:module.hovered,anchor:popup._anchorHover.hovered,requested:popup.requestedVisible,content:popup._contentHovered,lease:!!popup.companionLease,slots:bar.liquidController.activePopups.length}))
      check(popup.requestedVisible,kind+" hover did not open with "+state+" on "+edge)
      if(state!=="disabled"){
       const observer=findChild(host.contentItem,"wullRimPointerObserver")
       check(observer && observer.parent===Hadanion.outputs[0].overlayParent && observer.blocking===false,"observer left existing ancestor")
       check(observer.hovered,"restored module hover lost Companion pointer tracking")
       const point=module.mapToItem(null,module.width/2,module.height/2)
       check(Math.abs(observer.point.scenePosition.x-point.x)<1 && Math.abs(observer.point.scenePosition.y-point.y)<1,"Companion pointer coordinates changed")
      }
      const body=bar.liquidController._popupHost(bar.liquidController._popupSlot(popup))
      check(body && body.contentParent.width>0,"popup host missing")
      console.info("COMPANION_BRIDGE_START",edge,state,kind,Date.now(),JSON.stringify({surface:body.rawPresentationRecord.surface,content:body.record.content,input:body.inputBounds}))
      const bridge=bridgePoint(body,bar,host)
      console.info("COMPANION_BRIDGE_FOUND",edge,state,kind,Date.now(),JSON.stringify(bridge))
      check(bridge!==null,"no painted connector outside body/source regions "+kind+" "+edge)
      mouseMove(host.contentItem,bridge[0],bridge[1]);observe(field,180)
      check(popup.requestedVisible,kind+" connector transfer closed with "+state+" on "+edge)
      mouseMove(body.contentParent,body.contentParent.width/2,body.contentParent.height/2);observe(field,350)
      check(popup.requestedVisible,kind+" content transfer closed with "+state+" on "+edge)
      mouseMove(host.contentItem,bridge[0],bridge[1]);observe(field,180)
      check(popup.requestedVisible,"reverse connector transfer lost popup")
      mouseMove(module,module.width/2,module.height/2);observe(field,60)
      check(popup.requestedVisible,"reverse hover transfer lost popup")
      if((popup.hoverTarget.acceptedButtons & Qt.LeftButton)!==0){
       clicks.target=popup.hoverTarget;clicks.clear();mouseClick(popup.hoverTarget,popup.hoverTarget.width/2,popup.hoverTarget.height/2)
       check(clicks.count===1,"click did not reach actual anchor "+kind)
      }else console.info("COMPANION_CLICK_NOT_APPLICABLE",kind,"actual hover source accepts no LeftButton")
      console.info("COMPANION_EXIT_START",edge,state,kind,Date.now())
      const exit=outside(host,field)
      // Vertical Media deliberately pins its mature popup on LeftButton.
      // Preserve that click contract and exercise its actual outside catcher.
      if(popup.alternativeVisibleCondition && popup.closeOnOutsideClick){
       check(popup.requestedVisible,"click-pinned popup closed on pointer exit")
       const catcher=popup._clickOutsideBackdropObject
       check(catcher?.visible,"click-pinned popup has no native outside catcher")
       // Its catcher is a separate Top window, below the finite Overlay
       // mask. A Qt test move into the field cannot route that native click.
       mouseClick(catcher.contentItem,exit[0],exit[1]);observe(field,180)
      }
      console.info("COMPANION_EXIT_END",edge,state,kind,Date.now())
      check(!popup.requestedVisible,"popup did not exit "+kind+" "+state+" "+edge+" "+JSON.stringify({anchor:popup._anchorHover.hovered,content:popup._contentHovered,module:popup.moduleHoverActive,alternative:popup.alternativeVisibleCondition,lease:!!popup.companionLease}))
     }
    }
   }
   console.info("COMPANION_HOVER_PASS",root.checks,root.frames)
  }catch(e){console.error("COMPANION_HOVER_FAIL",e.message,e.stack,JSON.stringify({session:Hadanion.session?.status(),outputs:Hadanion.outputs.map(output=>output.extension?.status())}))}Qt.quit()}
 }
 Timer {interval:100;running:true;onTriggered:test.runChecks()}
}
''')
 with private_wayland(folder) as env:
  if env is None:raise SystemExit('SKIP: private Niri unavailable')
  # Only an isolated installation. Never mutate the owner release or defaults.
  release=folder/'data/hadanion/releases'/identity;release.parent.mkdir(parents=True)
  installer.assemble(PACKAGE,release,PACKAGE/'native/target/release/inir-companiond',identity)
  (release.parent.parent/'current').symlink_to(Path('releases')/identity)
  assert (release/'HadalisOutput.qml').read_bytes()==(PACKAGE/'HadalisOutput.qml').read_bytes(), 'private package readback mismatch'
  runtime=folder/'runtime';runtime.mkdir(mode=0o700)
  env.update(WAYLAND_DISPLAY=str(Path(env.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))/env['WAYLAND_DISPLAY']),XDG_RUNTIME_DIR=str(runtime),QSG_RHI_BACKEND='opengl',ALIS_COMPANION_TEST_SHA=identity)
  cfg=folder/'config/alis/config.json';cfg.parent.mkdir(parents=True,exist_ok=True)
  data=json.loads((ROOT/'defaults/config.json').read_text());data['panelFamily']='abyss';data['enabledPanels']=['abyssPerimeter','abyssBar','abyssPopup']
  data['abyss']['companion'].update(enabled=False,along=.9,exploreFeatures=False,animationsEnabled=True,soundEnabled=False,alternateCompanions=False,renderQuality='performance')
  data['abyss']['companionMind'].update(talkEnabled=False,aiEnabled=False,obsidianEnabled=False,proactive='manual',model='',referenceVault='')
  data['abyss']['waves']['enabled']=False;data['abyss']['quality']='performance';data['bar']['weather']['enable']=False
  cfg.write_text(json.dumps(data))
  # Budget the complete input matrix, including unfocused native field draws;
  # keep the 2.5-second frame deadline and all pointer assertions unchanged.
  result=run_qs(folder,env,timeout=400)
  output=os.environ.get('HADANION_HOVER_LOG')
  if output:Path(output).write_text(result.stdout+'\nprocessExit='+str(result.returncode)+'\n')
  print('Host source:',os.environ.get('HADANION_HOVER_HOST_SHA','direct host checkout'));print('Package identity:',identity);print('Payload sha256:',hashlib.sha256((PACKAGE/'HadalisOutput.qml').read_bytes()).hexdigest());print(result.stdout);print('Process exit:',result.returncode)
  if result.returncode or 'COMPANION_HOVER_PASS' not in result.stdout or any(x in result.stdout for x in ['COMPANION_HOVER_FAIL','TypeError:','ReferenceError:','Binding loop','Failed to load configuration']):raise SystemExit(1)
