#!/usr/bin/env python3
"""Owned native mesh/material/Timeline proof; no actual Companion or AI hooks.

--bundle is the output of companion-export-cowork-native.py. With no bundle,
test the bounded converter offline. Native captures use a new owned compositor,
fixed sampled poses and one Loader3D actor. They are not G0/G1 or input proof.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cowork_native", ROOT / "scripts/companion-cowork-native.py")
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


def surface(asset, primitive, omit_normal=False):
    """Compare exact Float32 corner data, retaining triangle winding and count.

    Smooth normals can merge glTF vertices. Index/vertex counts alone therefore
    cannot establish an unchanged surface or UV seam.
    """
    attributes = {k: asset.read(v) for k, v in primitive['attributes'].items()
                  if not (omit_normal and k == 'NORMAL')}
    targets = [{k: asset.read(v) for k, v in target.items()}
               for target in primitive.get('targets', [])]
    count = len(attributes['POSITION'])
    assert all(len(v) == count for v in attributes.values())
    assert all(len(v) == count for target in targets for v in target.values())
    indexes = [row[0] for row in asset.read(primitive['indices'])]
    assert primitive.get('mode', 4) == 4 and len(indexes) % 3 == 0
    assert all(isinstance(i, int) and 0 <= i < count for i in indexes)
    def corner(index):
        return (tuple((k, tuple(v[index])) for k, v in sorted(attributes.items())),
                tuple(tuple((k, tuple(v[index])) for k, v in sorted(target.items()))
                      for target in targets))
    triangles = Counter()
    for i in range(0, len(indexes), 3):
        a, b, c = (corner(index) for index in indexes[i:i+3])
        triangles[min((a, b, c), (b, c, a), (c, a, b))] += 1
    return triangles


def animation_data(asset):
    animations = asset.g['animations']
    assert len(animations) == 1
    animation = animations[0]
    channels = {}
    for channel in animation['channels']:
        target = channel['target']
        key = (asset.g['nodes'][target['node']]['name'], target['path'])
        assert key not in channels
        sampler = animation['samplers'][channel['sampler']]
        channels[key] = (sampler.get('interpolation', 'LINEAR'),
                         asset.read(sampler['input']), asset.read(sampler['output']))
    return channels


def compare_eye_normals(reference, bundle, output):
    """Offline authoring audit only: no compositor, model or live package."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    receipts = [json.loads((folder/'export.json').read_text()) for folder in (reference, bundle)]
    before, after = receipts
    assert not before.get('smoothEyeNormals') and after.get('smoothEyeNormals') is True
    for key in ('schema', 'stagedOnly', 'blender', 'bakedHz', 'lossless',
                'smoothBubbleNormals', 'conceptFace'):
        assert before[key] == after[key], 'unrelated export option changed: '+key
    assert set(before['characters']) == set(after['characters']) == {'aqua', 'octo'}
    rows = []
    for character in ('aqua', 'octo'):
        old, new = before['characters'][character], after['characters'][character]
        assert old['blendSha256'] == new['blendSha256']
        assert set(old['clips']) == set(new['clips']) and len(new['clips']) == 8
        for clip in sorted(new['clips']):
            assets = [converter.Asset(folder/character/(clip+'.gltf')) for folder in (reference, bundle)]
            for asset, receipt in zip(assets, (old['clips'][clip], new['clips'][clip])):
                assert hashlib.sha256(asset.path.read_bytes()).hexdigest() == receipt['gltfSha256']
                assert hashlib.sha256(asset.raw).hexdigest() == receipt['binSha256']
            a, b = assets
            assert old['clips'][clip]['duration'] == new['clips'][clip]['duration']
            assert a.g['materials'] == b.g['materials'], 'materials changed'
            assert a.g['scenes'] == b.g['scenes'] and a.g['scene'] == b.g['scene']
            assert [{k:v for k,v in n.items() if k != 'mesh'} for n in a.g['nodes']] == [
                    {k:v for k,v in n.items() if k != 'mesh'} for n in b.g['nodes']], 'parent or transform changed'
            assert animation_data(a) == animation_data(b), 'authored animation changed'
            eyes = []
            for left, right in zip(a.g['nodes'], b.g['nodes']):
                assert ('mesh' in left) == ('mesh' in right)
                if 'mesh' not in left:
                    continue
                lm, rm = a.g['meshes'][left['mesh']], b.g['meshes'][right['mesh']]
                assert {k:v for k,v in lm.items() if k != 'primitives'} == {
                        k:v for k,v in rm.items() if k != 'primitives'}
                assert len(lm['primitives']) == len(rm['primitives']) == 1
                lp, rp = lm['primitives'][0], rm['primitives'][0]
                assert {k:v for k,v in lp.items() if k not in ('attributes','indices','targets')} == {
                        k:v for k,v in rp.items() if k not in ('attributes','indices','targets')}
                eye = left['name'].startswith('Glossy eye')
                assert surface(a, lp, eye) == surface(b, rp, eye), 'surface, normals, morph or UV changed: '+left['name']
                if eye:
                    assert len(left.get('children', [])) == 4 and not lp.get('targets') and not rp.get('targets')
                    assert surface(a, lp) != surface(b, rp), 'eye normals were not changed'
                    minima = []
                    vertices = []
                    for asset, primitive in ((a, lp), (b, rp)):
                        positions = asset.read(primitive['attributes']['POSITION'])
                        normals = asset.read(primitive['attributes']['NORMAL'])
                        vertices.append(len(positions))
                        minima.append(min(sum(p*n for p,n in zip(position, normal)) /
                            (math.hypot(*position)*math.hypot(*normal))
                            for position, normal in zip(positions, normals)))
                    assert minima[1] > .999 and minima[1] > minima[0], 'outer eye normals remain faceted'
                    eyes.append({'name':left['name'], 'vertices':vertices, 'minRadialNormalCos':minima})
            assert len(eyes) == 2
            rows.append({'character':character, 'clip':clip, 'eyes':eyes,
                         'unchangedChannels':len(animation_data(a))})
    result = {'scope':'Offline exact surface/UV/material/parent/keyframe audit; not native pixels, G0/G1, shipping or lossless rendering',
              'referenceExportSha256':hashlib.sha256((reference/'export.json').read_bytes()).hexdigest(),
              'candidateExportSha256':hashlib.sha256((bundle/'export.json').read_bytes()).hexdigest(),
              'pairedClips':len(rows), 'proofs':rows}
    (output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print('COMPANION_EYE_NORMAL_AUDIT_PASS '+str(len(rows))+' pairedClips exactSurface UV materials parents Float32Keys')


def record_native_process(output, result, cases, deadline, contact):
    """Retain the actual process outcome before native qualification can fail."""
    missing=[case['name'] for case in cases
             if not (output/'frames'/(case['name']+'.png')).is_file()]
    receipt={'nativeExitCode':result.returncode,'deadlineSeconds':deadline,
             'scheduledFrames':len(cases),'capturedFrames':len(cases)-len(missing),
             'missingFrames':missing,'contact':contact,
             'completionMarker':'COMPANION_NATIVE_CAPTURE_DONE' in result.stdout,
             'scope':'Process inventory only; not geometry, pixels or resource qualification'}
    (output/'process.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def offline(folder):
    source = folder / "fixture.gltf"
    binary = source.with_suffix(".bin")
    binary.write_bytes(struct.pack("<9f", 0,0,0, 1,0,0, 0,1,0))
    value = {"asset":{"version":"2.0"},"buffers":[{"uri":binary.name,"byteLength":36}],
             "bufferViews":[{"buffer":0,"byteLength":36}],
             "accessors":[{"bufferView":0,"componentType":5126,"type":"VEC3","count":3}]}
    source.write_text(json.dumps(value))
    asset = converter.Asset(source)
    assert asset.read(0) == [[0,0,0],[1,0,0],[0,1,0]]
    # Different indexing after normal smoothing must retain the same oriented
    # triangle corners. Reject changed geometry, reversed winding or normals.
    from types import SimpleNamespace
    data = {0:[[1,0,0],[0,1,0],[0,0,1]], 1:[[1,0,0],[0,1,0],[0,0,1]],
            2:[[0,0],[1,0],[0,1]], 3:[[0],[1],[2]], 4:[[1],[2],[0]], 5:[[0],[2],[1]],
            6:[[.5,.5,.5]]*3, 7:[[1,0,0],[0,1,0],[0,0,2]],
            8:[[0,0],[.5,0],[0,1]], 9:[[0],[1],[2],[0],[1],[2]]}
    fake = SimpleNamespace(read=lambda index:data[index])
    triangle = {'indices':3,'attributes':{'POSITION':0,'NORMAL':1,'TEXCOORD_0':2}}
    rotated = dict(triangle, indices=4)
    assert surface(fake, triangle) == surface(fake, rotated)
    assert surface(fake, triangle) != surface(fake, dict(triangle, indices=5))
    smooth = dict(triangle, attributes=dict(triangle['attributes'], NORMAL=6))
    moved = dict(triangle, attributes=dict(triangle['attributes'], POSITION=7))
    assert surface(fake, triangle) != surface(fake, smooth)
    assert surface(fake, triangle, True) == surface(fake, smooth, True)
    assert surface(fake, triangle, True) != surface(fake, moved, True)
    changed_uv = dict(triangle, attributes=dict(triangle['attributes'], TEXCOORD_0=8))
    assert surface(fake, triangle, True) != surface(fake, changed_uv, True)
    assert surface(fake, triangle) != surface(fake, dict(triangle, indices=9))
    morphed = dict(triangle, targets=[{'POSITION':0}])
    assert surface(fake, morphed) != surface(fake, dict(triangle, targets=[{'POSITION':7}]))
    fake.g = {'nodes':[{'name':'Glossy eye'}], 'animations':[{
        'channels':[{'sampler':0,'target':{'node':0,'path':'scale'}}],
        'samplers':[{'input':3,'output':1,'interpolation':'LINEAR'}]}]}
    original_keys = animation_data(fake)
    fake.g['animations'][0]['samplers'][0]['output'] = 6
    assert animation_data(fake) != original_keys
    fake.g['animations'][0]['samplers'][0].update(output=1, interpolation='STEP')
    assert animation_data(fake) != original_keys
    uv_value = json.loads(json.dumps(value))
    uv_value['accessors'][0].update(type='VEC2', count=3)
    source.write_text(json.dumps(uv_value))
    assert converter.Asset(source).read(0) == [[0,0],[0,1],[0,0]]
    for index in (-1,True,2):
        try: asset.read(index)
        except ValueError: pass
        else: raise AssertionError("invalid accessor index accepted")
    for mutation in ({"count":200001},{"count":True},{"byteOffset":32},{"byteOffset":-1}):
        broken = json.loads(json.dumps(value))
        broken["accessors"][0].update(mutation)
        source.write_text(json.dumps(broken))
        try: converter.Asset(source).read(0)
        except ValueError: pass
        else: raise AssertionError("invalid accessor accepted")
    for uri in ("../fixture.bin", "https://example.invalid/weights.bin", "file:///fixture.bin"):
        broken = json.loads(json.dumps(value))
        broken["buffers"][0]["uri"] = uri
        source.write_text(json.dumps(broken))
        try: converter.Asset(source)
        except ValueError: pass
        else: raise AssertionError("external buffer accepted")
    for invalid in (float("nan"),float("inf"),True):
        try: converter.number(invalid)
        except ValueError: pass
        else: raise AssertionError("invalid keyframe accepted")
    # Synthetic process results exercise failed/partial/successful inventory;
    # they never launch a compositor or count as a native capture qualification.
    cases=[{'name':'first'},{'name':'second'}]
    for index,(code,marker,count) in enumerate([(124,'',1),(0,'',1),
                                               (0,'COMPANION_NATIVE_CAPTURE_DONE',2)]):
        output=folder/('process-'+str(index));(output/'frames').mkdir(parents=True)
        for case in cases[:count]:(output/'frames'/(case['name']+'.png')).touch()
        result=subprocess.CompletedProcess([],code,marker)
        receipt=record_native_process(output,result,cases,81,True)
        assert json.loads((output/'process.json').read_text())==receipt
        assert receipt['nativeExitCode']==code and receipt['deadlineSeconds']==81
        assert receipt['scheduledFrames']==2 and receipt['capturedFrames']==count
        assert receipt['missingFrames']==[case['name'] for case in cases[count:]]
        assert receipt['completionMarker']==bool(marker) and receipt['contact'] is True
    print("COMPANION_NATIVE_CONVERTER_PASS boundedBuffers finiteValues nativeResultReceipt exactNormalsAudit")


QML = '''import QtQuick
import QtQuick.Window
import QtQuick3D
import QtQuick3D.Helpers
import Quickshell
OWNER_IMPORTS
Window {
    id: root
    width: 420; height: 420; visible: true; color: "#061119"
    property var cases: CASES
    property int index: 0
    property var current: cases[index]
    property var proofs: []
    readonly property var actor: actorLoader.item
    OWNER_PROPERTIES
    function check(ok, why) { if(!ok) throw new Error(why) }
    function configure() {
        if(current.performance) {
            if(actor?.contactSurface) actor.contactSurface.enabled=false
            configurePerformance();return
        }
        if(current.hidden) {settle.restart();return}
        if(!actor) return
        actor.clip=current.clip;actor.phase=current.phase
        actor.opticsProfile=current.optics??"studio"
        actor.theme=current.theme;actor.propVisible=current.prop
        if(actor.contactSurface) {
            actor.contactSurface.enabled=current.contact??false
            actor.contactSurface.phase=current.ripple??.12
            actor.contactSurface.reflectionsEnabled=current.reflections??false
            actor.contactSurface.eyePosition=contactCamera.scenePosition
            actor.castIntoContact=current.captureSubjects??true
            Qt.callLater(()=>actor?.contactSurface?.capture())
        }
        settle.restart()
    }
    function vector(p) {return [p.x,p.y,p.z]}
    function inspect() {
        if(current.hidden) {
            check(!actor,"disabled loader retained a model")
            if(current.performance)check(owner.view.phase===current.expectedPhase&&!owner.view.renderProp,"hidden policy retained a prop")
            proofs.push({name:current.name,character:current.character,hidden:true,actorCount:0})
            return
        }
        check(actor && actor.visible,"missing single actor")
        if(current.performance) inspectPerformance()
        const prop=actor.named("Laptop root"),hinge=actor.named("Laptop hinge")
        check(prop.visible===current.prop,"prop lifecycle mismatch")
        const proof={name:current.name,character:current.character,clip:current.clip,phase:current.phase,
            rim:current.rim,theme:String(actor.theme),propVisible:prop.visible,
            optics:actor.opticsProfile,themeRgb:[actor.theme.r,actor.theme.g,actor.theme.b],
            eyes:actor.objects.filter(n=>n.objectName.startsWith("Glossy eye")).map(n=>({
                name:n.objectName,position:vector(n.position),scale:vector(n.scale),
                children:actor.objects.filter(c=>c.parent===n).map(c=>({name:c.objectName,
                    position:vector(c.position),scale:vector(c.scale)}))})),
            cores:actor.coreVolumes.map(c=>({visible:c.visible,scale:vector(c.scale),vertices:c.geometry.positions.length})),
            materials:actor.materials.map(m=>({name:m.objectName,color:String(m.baseColor),rgb:[m.baseColor.r,m.baseColor.g,m.baseColor.b],
                alpha:m.baseColor.a,transmission:m.transmissionFactor,thickness:m.thicknessFactor,
                emission:vector(m.emissiveFactor)})),
            hinge:vector(hinge.eulerRotation),lid:vector(actor.named("Laptop lid").scenePosition)}
        if(current.performance) proof.owner=ownerProof
        if(actor.contactSurface) {
            const water=actor.contactSurface,m=water.waterMaterial
            proof.contact={enabled:water.enabled,position:vector(water.scenePosition),
                normal:vector(water.mapDirectionToScene(Qt.vector3d(0,1,0))),
                rgb:[m.baseColor.r,m.baseColor.g,m.baseColor.b],metalness:m.metalness,roughness:m.roughness,
                reflections:water.reflectionsEnabled,probeVisible:water.probe.visible,
                floorReceives:water.floor.receivesReflections,floorCasts:water.floor.castsReflections,
                floorVertices:water.floor.geometry.positions.length,
                floorAlpha:water.floor.geometry.colors.map(c=>c.w),
                subjectCasters:actor.contactCasters.concat(actor.coreVolumes).map(m=>({casts:m.castsReflections,receives:m.receivesReflections})),
                probeBox:vector(water.probe.boxSize),probeQuality:water.probe.quality,shadows:keyLight.castsShadow,
                eye:vector(water.eyePosition),mirrorEye:vector(water.probe.scenePosition),
                ripples:water.ripples.map(r=>({progress:r.progress,radius:r.radius,opacity:r.opacity,scale:vector(r.scale)}))}
        }
        if(current.character==="aqua") {
            proof.left=vector(actor.named("Left water arm").scenePosition)
            proof.right=vector(actor.named("Right water arm").scenePosition)
        } else {
            const arm=actor.named("Tentacle 1")
            proof.weights=arm.morphWeights
            proof.tip=vector(arm.tapTip)
            proof.cup=vector(actor.named("Suction cup 1.4").scenePosition)
        }
        proofs.push(proof)
    }
    onCurrentChanged: configure()
    Item {
        id: canvas;width:420;height:420;anchors.centerIn:parent
        Rectangle {anchors.fill:parent;color:"#061119"}
        View3D {
            anchors.fill:parent
            environment:SceneEnvironment {
                backgroundMode: SceneEnvironment.Transparent
                lightProbe: Texture {source: STUDIO}
                probeExposure: 1
                tonemapMode: SceneEnvironment.TonemapModeLinear
                antialiasingMode: SceneEnvironment.MSAA
                antialiasingQuality: SceneEnvironment.High
            }
            camera:root.current.contact?contactCamera:camera
            PerspectiveCamera {
                id:contactCamera;position:Qt.vector3d(50,76,220);fieldOfView:37
                Component.onCompleted:lookAt(Qt.vector3d(0,36,0))
            }
            OrthographicCamera {
                id:camera;position:Qt.vector3d(50,76,220)
                horizontalMagnification: 2.9;verticalMagnification:2.9
                Component.onCompleted:lookAt(Qt.vector3d(0,36,0))
            }
            DirectionalLight {
                id:keyLight
                eulerRotation:Qt.vector3d(-30,-35,0);brightness:root.current.optics==="abyss" ? 1.35 : 1.8;color:"#e6f7ff"
                castsShadow:(root.current.contact??false)&&!root.current.hidden
                shadowFactor:35;shadowFilter:5;shadowBias:.02
                shadowMapQuality:Light.ShadowMapQualityLow
            }
            DirectionalLight {eulerRotation:Qt.vector3d(-20,110,0);brightness:root.current.optics==="abyss" ? .3 : 1.3;color:"#84c5ff"}
            DirectionalLight {eulerRotation:Qt.vector3d(30,180,0);brightness:root.current.optics==="abyss" ? 1.4 : 1.6;color:"#c8eaff"}
            Node {
                position:Qt.vector3d(0,36,0)
                eulerRotation.z:root.current.rim
                Loader3D {
                    id:actorLoader;y:-36
                    active: !root.current.hidden
                    source: root.current.character==="aqua" ? "AquaCowork.qml" : "OctoCowork.qml"
                    onLoaded:root.configure()
                    onStatusChanged:if(status===Loader3D.Error) {
                        console.log("COMPANION_NATIVE_FAIL actor import");Qt.callLater(Qt.quit)
                    }
                }
            }
        }
    }
    Timer {
        id:settle;interval:root.current.video&&!root.current.firstFrame?35:350;repeat:false
        onTriggered: {
            try {
                root.inspect()
                const saved=root.current.name
                check(canvas.grabToImage(result=>{
                    if(!result.saveToFile(OUT+"/"+saved+".png")) {
                        console.log("COMPANION_NATIVE_FAIL save "+saved);Qt.quit();return
                    }
                    if(root.index+1===root.cases.length) {
                        console.log("COMPANION_NATIVE_PROOFS "+JSON.stringify(root.proofs))
                        console.log("COMPANION_NATIVE_CAPTURE_DONE")
                        Qt.quit()
                    } else {root.index++}
                }),"grab scheduling failed")
            } catch(error) {console.log("COMPANION_NATIVE_FAIL "+error);Qt.quit()}
        }
    }
}
'''


OWNER_QML = '''property var owner: null
property int ownerIndex: -1
property var ownerProof: null
function configurePerformance() {
    if(ownerIndex!==index) {
        if(current.reset||!owner||owner.character!==current.character) {
            owner={character:current.character,d:Director.newState(),
                s:Performance.newState(current.character,CURVES[current.character]),
                host:{enabled:true,visible:true,optedIn:true,coworkEnabled:true,motionEnabled:true,
                    grounded:true,character:current.character},nativeFrom:null,view:null,oldEpoch:-1}
        }
        const before=Performance.view(owner.s,current.at)
        let saved=null
        if(actor&&actor.character===current.character) {
            actor.blendProgress=owner.s.blendFrom?Math.min(1,(current.at-owner.s.started)/Performance.BLEND_MS):1
            actor.clip=before.active?before.clip:"laptop_close"
            actor.phase=before.active?before.progress:1
            saved=actor.snapshot()
        }
        const started=owner.s.started,clip=owner.s.clip
        if(current.event) check(Director.acceptAgent(owner.d,{word:current.event,token:"0123456789abcdef"},current.at).accepted,"fixture event rejected")
        if(current.focus!==undefined) Director.setFocus(owner.d,current.focus,current.at)
        Object.assign(owner.host,current.patch??{})
        if(current.cue) check(Performance.cue(owner.s,owner.d,current.cue,current.at).accepted,"fixture cue rejected")
        let staleRejected=null
        if(current.stale) staleRejected=!Performance.complete(owner.s,owner.d,owner.oldEpoch,current.at).accepted
        owner.view=Performance.update(owner.s,owner.d,owner.host,current.at)
        if(current.storeEpoch) owner.oldEpoch=owner.view.epoch
        const changed=owner.s.blendFrom&&(owner.s.started!==started||owner.s.clip!==clip)
        const reversed=before.phase==="intro"&&owner.view.phase==="outro"&&before.clip===owner.view.clip
        owner.boundary=(changed||reversed)?saved:null
        if(changed) owner.nativeFrom=saved
        if(!owner.s.blendFrom) owner.nativeFrom=null
        ownerProof={phase:owner.view.phase,clip:owner.view.clip,epoch:owner.view.epoch,
            active:owner.view.active,renderProp:owner.view.renderProp,progress:owner.view.progress,
            staleRejected:staleRejected,continuityMax:null,continuityKind:changed?"blend":reversed?"reversal":null,blendProgress:1}
        ownerIndex=index
    }
    if(current.hidden) {settle.restart();return}
    if(!actor||actor.character!==current.character)return
    actor.blendFrom=owner.nativeFrom
    actor.blendProgress=owner.s.blendFrom?Math.min(1,(current.at-owner.s.started)/Performance.BLEND_MS):1
    actor.clip=owner.view.active?owner.view.clip:"laptop_close"
    actor.phase=owner.view.active?owner.view.progress:1
    actor.propVisible=owner.view.renderProp;actor.theme=current.theme
    actor.opticsProfile=current.optics??"studio"
    ownerProof.blendProgress=actor.blendProgress
    settle.restart()
}
function inspectPerformance() {
    check(actor.character===current.character,"wrong single actor after cast")
    check(owner.view.phase===current.expectedPhase,"wrong presentation phase "+current.name)
    check(owner.view.clip===current.expectedClip,"wrong presentation clip "+current.name)
    check(owner.view.renderProp===current.prop,"wrong prop release "+current.name)
    if(current.stale)check(ownerProof.staleRejected,"late completion restored a prop")
    if(owner.boundary) {
        let error=0
        const after=actor.snapshot()
        for(let i=0;i<after.length;i++) for(const field of ["position","scale","weights"])
            for(let j=0;j<after[i][field].length;j++)error=Math.max(error,Math.abs(after[i][field][j]-owner.boundary[i][field][j]))
        for(let i=0;i<after.length;i++) {
            const a=after[i].rotation,b=owner.boundary[i].rotation
            const dot=a.reduce((v,x,j)=>v+x*b[j],0)
            error=Math.max(error,Math.abs(1-Math.abs(dot)))
        }
        ownerProof.continuityMax=error
    }
    if(ownerProof.continuityMax!==null)check(ownerProof.continuityMax<.0001,"native pose jumped on clip change")
}'''


def performance_cases():
    """Fixed synthetic input; timestamps are caller-owned, not desktop time."""
    schedule=[
        (100000,"intro",dict(reset=True,event="working",storeEpoch=True),"intro","laptop_open",False),
        (100700,"opening",dict(focus="terminal"),"intro","laptop_open",True),
        (101450,"agent",{},"loop","laptop_agent_loop",True),
        (102050,"typing-switch",dict(event="ended"),"loop","laptop_typing_loop",True),
        (102120,"typing-blend",{},"loop","laptop_typing_loop",True),
        (102190,"typing",{},"loop","laptop_typing_loop",True),
        (102191,"agent-switch",dict(event="working"),"loop","laptop_agent_loop",True),
        (102331,"agent-again",{},"loop","laptop_agent_loop",True),
        (102400,"waiting",dict(event="needs_input"),"loop","laptop_agent_loop",True),
        (147401,"thinking-switch",{},"loop","laptop_thinking_loop",True),
        (147471,"thinking-blend",{},"loop","laptop_thinking_loop",True),
        (147541,"thinking",{},"loop","laptop_thinking_loop",True),
        (147542,"alert-switch",dict(cue="alert"),"cue","laptop_alert",True),
        (148142,"alert",{},"cue","laptop_alert",True),
        (148742,"alert-end",{},"loop","laptop_thinking_loop",True),
        (148750,"drag",dict(patch={"dragging":True}),"none","",False),
        (152000,"late-callback",dict(stale=True),"none","",False),
        (152010,"released",dict(patch={"dragging":False}),"intro","laptop_open",False),
        (152020,"context-end",dict(event="ended",focus="none"),"intro","laptop_open",False),
        (152800,"grace",{},"intro","laptop_open",True),
        (153020,"reverse",{},"outro","laptop_open",True),
        (153400,"closing",{},"outro","laptop_open",True),
        (154030,"closed",{},"none","",False),
        (154040,"off",dict(hidden=True,patch={"enabled":False}),"none","",False),
    ]
    return [dict(name=character+"-owner-"+name,character=character,performance=True,at=at,
                 expectedPhase=phase,expectedClip=clip,clip=clip or "laptop_close",phase=0,
                 rim=0,theme="#36d3f3",prop=visible,**extra)
            for character in ("aqua","octo") for at,name,extra,phase,clip,visible in schedule]


def native(bundle, output, host, video=False, performance=False, optics=False, contact=False):
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    receipt = converter.convert(bundle,output/"qt",contact)
    # Inspect exported normals rather than trusting the authoring option label.
    normal_proofs={}
    export_receipt=json.loads((bundle/"export.json").read_text())
    if optics and export_receipt.get('smoothBubbleNormals'):
        for character in ('aqua','octo'):
            asset=converter.Asset(bundle/character/'laptop_typing_loop.gltf')
            values=[]
            for node in asset.g['nodes']:
                if not node['name'].startswith('Orbital bubble '):continue
                mesh=asset.g['meshes'][node['mesh']]['primitives'][0]
                positions=asset.read(mesh['attributes']['POSITION']);normals=asset.read(mesh['attributes']['NORMAL'])
                values.append(min(sum(p*n for p,n in zip(point,normal))/math.hypot(*point)/math.hypot(*normal)
                    for point,normal in zip(positions,normals)))
            assert len(values)==8 and min(values)>.995,'orbital bubble shading is still faceted'
            normal_proofs[character]=values
    session_spec = importlib.util.spec_from_file_location("native_session",host/"scripts/native_test_session.py")
    session = importlib.util.module_from_spec(session_spec)
    session_spec.loader.exec_module(session)
    cases=[]
    clips=json.loads((bundle/"export.json").read_text())["characters"]["aqua"]["clips"]
    for character in ("aqua","octo"):
        for clip in clips:
            cases.append(dict(name=character+"-"+clip,character=character,clip=clip,phase=.5,rim=0,theme="#36d3f3",prop=True))
        for phase in (0,.12,.38,1):
            cases.append(dict(name=character+"-typing-"+str(phase),character=character,
                clip="laptop_typing_loop",phase=phase,rim=0,theme="#36d3f3",prop=True))
        for rim in (90,180,270):
            cases.append(dict(name=character+"-rim-"+str(rim),character=character,
                clip="laptop_typing_loop",phase=.12,rim=rim,theme="#36d3f3",prop=True))
        for theme,label in (("#ffb967","amber"),("#ae84ff","purple"),("#24dfba","green")):
            cases.append(dict(name=character+"-"+label,character=character,
                clip="laptop_typing_loop",phase=.12,rim=0,theme=theme,prop=True))
        cases.append(dict(name=character+"-closed",character=character,
            clip="laptop_close",phase=1,rim=0,theme="#36d3f3",prop=False))
        cases.append(dict(name=character+"-off",character=character,hidden=True,
            clip="laptop_close",phase=1,rim=0,theme="#36d3f3",prop=False))
    if performance:cases.extend(performance_cases())
    if optics:
        for character in ("aqua","octo"):
            for theme,label in (("#36d3f3","blue"),("#ffb967","amber"),("#ae84ff","purple"),("#24dfba","green")):
                cases.append(dict(name=character+"-optics-"+label,character=character,clip="laptop_typing_loop",
                    phase=.12,rim=0,theme=theme,prop=True,optics="abyss"))
        if export_receipt.get('conceptFace'):
            for character in ('aqua','octo'):
                cases.append(dict(name=character+'-face-blink',character=character,clip='laptop_thinking_loop',
                    phase=.32,rim=0,theme='#36d3f3',prop=True,optics='abyss'))
    if contact:
        for character in ('aqua','octo'):
            base=dict(character=character,clip='laptop_close',phase=1,rim=0,theme='#36d3f3',prop=False,optics='abyss',contact=True,ripple=.12)
            cases.append(dict(base,name=character+'-contact-plain',reflections=False))
            cases.append(dict(base,name=character+'-contact-reflected',reflections=True))
            cases.append(dict(base,name=character+'-contact-empty-probe',reflections=True,captureSubjects=False))
            cases.append(dict(base,name=character+'-contact-wave-late',reflections=True,ripple=.72))
            for rim in (90,180,270):cases.append(dict(base,name=character+'-contact-rim-'+str(rim),reflections=True,rim=rim))
            for theme,label in (('#ffb967','amber'),('#ae84ff','purple'),('#24dfba','green')):
                cases.append(dict(base,name=character+'-contact-'+label,reflections=True,theme=theme))
            cases.append(dict(base,name=character+'-contact-off',reflections=True,hidden=True))
    static_cases=list(cases)
    if video:
        if not shutil.which("ffmpeg"):
            raise SystemExit("ffmpeg is required for the optional review movie")
        for character in ("aqua","octo"):
            frame=0
            for clip,seconds in (("laptop_open",1.45),("laptop_typing_loop",1.8),
                    ("laptop_thinking_loop",1.6),("laptop_agent_loop",1.2),
                    ("laptop_pause",.6),("laptop_success",1.1),
                    ("laptop_alert",1.2),("laptop_close",1.2)):
                duration=clips[clip]["duration"]/1000
                for j in range(round(seconds*24)+1):
                    phase=min(j/24/duration,1)
                    visible=clip!="laptop_open" or phase>=.13
                    visible=visible and (clip!="laptop_close" or phase<.93)
                    cases.append(dict(name=character+"-video-"+format(frame,"04d"),
                        character=character,clip=clip,phase=phase,rim=0,theme="#36d3f3",prop=visible,
                        video=True,firstFrame=frame==0))
                    frame+=1
    frames=output/"frames";frames.mkdir()
    qml=QML.replace("CASES",json.dumps(cases)).replace("STUDIO",json.dumps(str(bundle/"studio.hdr")))
    qml=qml.replace("OUT",json.dumps(str(frames)))
    imports='import "assets/cowork/CoworkPerformance.js" as Performance\nimport "modules/abyss/companion/AquaBehaviorDirector.js" as Director'
    # Copy only dormant libraries and authored curves, never host/user inputs.
    if performance:
        for name in ("assets/cowork/CoworkPerformance.js","modules/abyss/companion/AquaBehaviorDirector.js"):
            destination=output/"qt"/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/name,destination)
        curves={c:json.loads((ROOT/"assets/cowork"/(c.title()+"LaptopMotion.json")).read_text()) for c in ("aqua","octo")}
        owner_qml=OWNER_QML.replace("CURVES",json.dumps(curves,separators=(",",":")))
    else:owner_qml=""
    qml=qml.replace("OWNER_IMPORTS",imports if performance else "").replace("OWNER_PROPERTIES",owner_qml)
    (output/"qt/shell.qml").write_text(qml)
    compositor=output/"compositor";compositor.mkdir()
    with session.private_wayland(compositor) as env:
        if env is None:
            raise SystemExit("SKIP: Native laptop proof requires owned Niri/Wayland")
        env.update(QT_QUICK_BACKEND="rhi",QSG_RHI_BACKEND="opengl",QT_QUICK_CONTROLS_STYLE="Basic",
                   QT_QPA_PLATFORMTHEME="generic",QT_NO_XDG_DESKTOP_PORTAL="1")
        deadline=max(55,math.ceil(len(cases)*.55+15))
        result=session.run_qs(output/"qt",env,timeout=deadline)
    log=result.stdout
    (output/'native.log').write_text(log)
    record_native_process(output,result,cases,deadline,contact)
    if result.returncode or "COMPANION_NATIVE_CAPTURE_DONE" not in log or any(bad in log for bad in
        ("COMPANION_NATIVE_FAIL","ReferenceError:","TypeError:","Unable to assign","Binding loop")):
        raise SystemExit("Native laptop fixture failed: "+log[-7000:])
    proofs=json.loads(next(line.split("COMPANION_NATIVE_PROOFS ",1)[1] for line in log.splitlines()
                          if "COMPANION_NATIVE_PROOFS " in line))
    (output/'geometry-proofs.json').write_text(json.dumps(proofs,indent=2)+'\n')
    assert len(proofs)==len(cases)
    for proof in proofs:
        if proof.get("hidden"):
            assert proof["actorCount"]==0
            continue
        assert len(proof['cores'])==1 and proof['cores'][0]['vertices']>500
        assert proof['cores'][0]['visible']==(proof['optics']=='abyss')
        assert max(abs(a-b) for a,b in zip(proof['cores'][0]['scale'],(.9,.74,.84)))<.0001
        if contact and not proof['name'].startswith(proof['character']+'-contact-'):
            assert not proof['contact']['enabled'],'water receiver escaped its opt-in fixture'
        for material in proof["materials"]:
            if any(word in material["name"].lower() for word in ("liquid","theme accent","cyan iris","opaque glossy tentacles")):
                factor=.12 if "cyan iris" in material["name"].lower() else .9 if "liquid" in material['name'].lower() else .65 if "opaque glossy tentacles" in material['name'].lower() else 1
                if proof['optics']=='abyss':
                    assert max(abs(a-b*factor) for a,b in zip(material['rgb'],proof['themeRgb']))<.0001,"material lost theme hue"
                else:assert material["color"]==proof["theme"],"theme did not recolor all original volumes"
            if "opaque glossy tentacles" in material["name"].lower():
                assert material["alpha"]==1 and material["transmission"]==0,"Octo limb depth/opacity mismatch"
            if proof['optics']=='abyss' and 'liquid' in material['name'].lower():
                assert abs(material['transmission']-.3)<.0001 and material['thickness']==8
            if material['name']=='Staged inner core':
                assert max(abs(a-b*.8) for a,b in zip(material['rgb'],proof['themeRgb']))<.0001
                assert max(abs(a-b*.8) for a,b in zip(material['emission'],proof['themeRgb']))<.0001
    for character in ("aqua","octo"):
        if contact:
            reflected=next(p for p in proofs if p['name']==character+'-contact-reflected')['contact']
            late=next(p for p in proofs if p['name']==character+'-contact-wave-late')['contact']
            assert reflected['enabled'] and reflected['reflections'] and reflected['probeVisible'] and reflected['shadows']
            assert reflected['probeQuality']==2 and reflected['floorReceives'] and not reflected['floorCasts']
            assert len(reflected['subjectCasters'])>50 and all(m['casts'] and not m['receives'] for m in reflected['subjectCasters'])
            empty=next(p for p in proofs if p['name']==character+'-contact-empty-probe')['contact']
            assert empty['reflections'] and all(not m['casts'] and not m['receives'] for m in empty['subjectCasters'])
            assert reflected['floorVertices']==775 and len(reflected['floorAlpha'])==775
            assert reflected['floorAlpha'][0]==1 and reflected['floorAlpha'][-1]==0
            assert all(0<=a<=1 for a in reflected['floorAlpha'])
            assert all(b<=a for a,b in zip(reflected['floorAlpha'],reflected['floorAlpha'][1:]))
            assert len(reflected['ripples'])==3 and reflected['ripples'][0]['radius']!=late['ripples'][0]['radius']
            for p in proofs:
                if not p['name'].startswith(character+'-contact-') or p.get('hidden'):continue
                water=p['contact'];angle=math.radians(p['rim'])
                assert math.dist(water['normal'],(-math.sin(angle),math.cos(angle),0))<.0001
                delta=[a-b for a,b in zip(water['eye'],water['position'])]
                distance=sum(a*b for a,b in zip(delta,water['normal']))
                mirror=[a-2*distance*n for a,n in zip(water['eye'],water['normal'])]
                assert math.dist(water['mirrorEye'],mirror)<.0001,'camera was not reflected across supporting plane'
                assert math.dist(water['probeBox'],[140,4,115] if p['rim'] in (0,180) else [4,140,115])<.0001
                assert max(abs(a-b*.10) for a,b in zip(water['rgb'],p['themeRgb']))<.0001
                assert abs(water['metalness']-.15)<.0001 and abs(water['roughness']-.08)<.0001
        if optics and export_receipt.get('conceptFace'):
            opened=next(p for p in proofs if p['name']==character+'-optics-blue')['eyes']
            blink=next(p for p in proofs if p['name']==character+'-face-blink')['eyes']
            assert len(opened)==len(blink)==2
            for eye,closed in zip(opened,blink):
                assert closed['name']==eye['name'] and closed['scale'][1]<eye['scale'][1]*.2,'larger eyes lost authored blink'
                assert eye['scale'][0]>6,'eye volume was not enlarged'
                assert abs(eye['position'][1]-(-1 if character=='aqua' else 4))<.0001,'eye volume was not raised'
                children=eye['children'];assert len(children)==4,'eye layers lost shared parent'
                pupil=next(c for c in children if c['name'].endswith(' pupil'))
                assert math.dist(pupil['scale'],(.58,.64,.23))<.0001
                assert abs(pupil['position'][1]-.17)<.0001
        paired=[p for p in proofs if p["name"].startswith(character+"-typing-")]
        zero,tap,other,end=paired
        if character=="aqua":
            distance=lambda a,b:math.dist(a,b)
            assert distance(zero["left"],tap["left"])>2.5 and distance(zero["right"],tap["right"])<.05
            assert distance(zero["right"],other["right"])>2.5 and distance(zero["left"],other["left"])<.05
            assert distance(zero["left"],end["left"])<.01
        else:
            assert tap["weights"]!=zero["weights"] and math.dist(tap["tip"],zero["tip"])>2.5
            assert math.dist(tap["cup"],zero["cup"])>1.5
            assert math.dist(zero["cup"],end["cup"])<.01
        assert not next(p for p in proofs if p["name"]==character+"-closed")["propVisible"]
        if performance:
            paired=[p for p in proofs if p["name"].startswith(character+"-owner-")]
            assert len(paired)==24 and paired[-1]['hidden'] and paired[-1]['actorCount']==0
            continuity=[p['owner']['continuityMax'] for p in paired if p.get('owner',{}).get('continuityMax') is not None]
            assert len(continuity)>=5 and max(continuity)<.0001
            assert any(p.get('owner',{}).get('continuityKind')=='reversal' for p in paired)
            assert any(p.get('owner',{}).get('staleRejected') is True for p in paired)
            assert any(0<p.get('owner',{}).get('blendProgress',1)<1 for p in paired)
    for case in cases:
        data=(frames/(case["name"]+".png")).read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n") and struct.unpack(">II",data[16:24])==(420,420)
        assert len(data)>(256 if case.get("hidden") else 1500)
    # File existence/size alone cannot prove Qt painted a real model. Inspect
    # only the fixed matrix, never choose/retry movie frames by their pixels.
    from PIL import Image
    painted = {}
    for case in static_cases:
        with Image.open(frames/(case["name"]+".png")) as image:
            pixels=image.convert("RGBA").tobytes()
        background=bytes((6,17,25,255))
        count=sum(pixels[i:i+4]!=background for i in range(0,len(pixels),4))
        assert count==0 if case.get("hidden") else count>3000,"empty or retained native model paint"
        painted[case["name"]]=count
    contact_pixels={}
    if contact:
        # A live probe flag alone is insufficient: remove only the 3D casters
        # and require an actual reflected contribution in the fixed water ROI.
        # This lower bound proves visible paint, not G0 parity or visual quality.
        for character in ('aqua','octo'):
            roi=(65,325,355,370)
            with Image.open(frames/(character+'-contact-reflected.png')) as image:
                reflected=image.convert('RGB').crop(roi).tobytes()
            with Image.open(frames/(character+'-contact-empty-probe.png')) as image:
                empty=image.convert('RGB').crop(roi).tobytes()
            differences=[max(abs(reflected[i+c]-empty[i+c]) for c in range(3))
                         for i in range(0,len(reflected),3)]
            contribution=sum(d>=8 for d in differences)
            assert contribution>200,'native water did not reflect the 3D subject'
            contact_pixels[character]={'roi':roi,'subjectPixelsAtLeast8':contribution,
                                       'maxChannelDifference':max(differences)}
    movies=[]
    if video:
        for character in ("aqua","octo"):
            path=output/(character+"-cowork.mp4")
            subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-framerate","24",
                "-i",str(frames/(character+"-video-%04d.png")),"-c:v","libx264","-crf","18",
                "-pix_fmt","yuv420p","-threads","2","-movflags","+faststart",str(path)],
                check=True,timeout=30)
            assert path.stat().st_size>5000
            movies.append(str(path))
    receipt.update(scope="Original staged mesh/material/Timeline only; not production, G0/G1 or input acceptance",
                   frames=len(cases),staticFrames=len(static_cases),paintedPixels=painted,
                   movies=movies,proofs=proofs,performanceFixture=performance,opticsFixture=optics,
                   bubbleNormalProof=normal_proofs,conceptFace=export_receipt.get('conceptFace',False),
                   contactFixture=contact,contactPaintProof=contact_pixels,exitCode=result.returncode)
    (output/"result.json").write_text(json.dumps(receipt,indent=2)+"\n")
    print("COMPANION_NATIVE_QML_PASS "+str(len(cases))+" fixedFrames oneLoader pairedClips fourRims fourThemes propYield")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle",type=Path)
    parser.add_argument("--reference-bundle",type=Path,help="offline-only exact comparison for an unsaved --smooth-eyes export")
    parser.add_argument("--output",type=Path)
    parser.add_argument("--video",action="store_true",help="also retain two short original 3D review movies")
    parser.add_argument("--performance",action="store_true",help="also prove synthetic native controller interruptions and blends")
    parser.add_argument("--optics",action="store_true",help="also compare eight themed refractive Abyss staging poses")
    parser.add_argument("--contact",action="store_true",help="also compare native water/reflection/ripple/rim poses; requires Qt 6.12")
    parser.add_argument("--hadalis-root",type=Path,default=Path(os.environ.get("HADALIS_ROOT",ROOT.parent/"Hadalis")))
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="alis-companion-cowork-converter-") as temporary:
        offline(Path(temporary))
    if bool(args.bundle)!=bool(args.output):parser.error("--bundle and --output must be supplied together")
    if args.video and not args.bundle:parser.error("--video requires the owned --bundle and --output")
    if args.performance and not args.bundle:parser.error("--performance requires the owned --bundle and --output")
    if args.optics and not args.bundle:parser.error("--optics requires the owned --bundle and --output")
    if args.contact and not args.bundle:parser.error("--contact requires the owned --bundle and --output")
    if args.reference_bundle:
        if not args.bundle or args.video or args.performance or args.optics or args.contact:
            parser.error("--reference-bundle requires --bundle/--output and excludes native capture options")
        compare_eye_normals(args.reference_bundle.resolve(), args.bundle.resolve(), args.output.resolve())
    elif args.bundle:native(args.bundle.resolve(),args.output.resolve(),args.hadalis_root.resolve(),args.video,args.performance,args.optics,args.contact)
