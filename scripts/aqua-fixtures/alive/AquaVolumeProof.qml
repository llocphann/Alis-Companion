import QtQuick
import QtTest
import Quickshell
import qs.optional.alis_companion.modules.abyss.companion

Window {
    id:root;visible:true;width:360;height:300;color:"#051521"
    property int step:0
    property var states:[{action:"",phase:0},{action:"jump",phase:.4},{action:"fly",phase:.5},{action:"land",phase:.7}]
    function named(item,name) {
        if(item.objectName===name)return item
        for(const child of item.data ?? item.children ?? []){const match=named(child,name);if(match)return match}
        return null
    }
    WaterDropletBody {
        id:body;x:142;y:104;motionEnabled:true;effectsEnabled:false;renderQuality:"quality";translucency:.16
        motionAction:root.states[root.step].action;motionProgress:root.states[root.step].phase
    }
    TestCase {id:input;when:false;optional:true}
    function save():void {
        const material=named(body,"aquaVolumeMaterial"),gait=named(body,"aquaLocomotion"),face=named(body,"aquaFace")
        if(!body.materialReady || !Number.isFinite(body.modelPitch)
                || (root.step>0 && (gait.clip!==root.states[root.step].action || gait.scaleY<.95))){console.error("AQUA_VOLUME=FAIL pose");Qt.quit();return}
        const p=face.mapToItem(body,38,54),r=body.project(0,46.14-54,26)
        if(Math.abs(p.x-38-r.x)>.1 || Math.abs(p.y-46.14+r.y-gait.lift)>.1){console.error("AQUA_VOLUME=FAIL face "+JSON.stringify({actual:p,projected:r,pitch:body.modelPitch}));Qt.quit();return}
        material.grabToImage(result=>{
            if(!result.saveToFile(Quickshell.env("AQUA_VOLUME_OUTPUT")+"/pose-"+root.step+".png")){console.error("AQUA_VOLUME=FAIL save");Qt.quit();return}
            if(root.step===root.states.length-1){console.log("AQUA_VOLUME=PASS realMaterial threeAxisPose projectedFace retainedVolume");shutdown.start()}
            else {root.step++;delay.start()}
        },Qt.size(304,304))
    }
    Timer {interval:800;running:true;onTriggered:root.save()}
    Timer {id:delay;interval:300;onTriggered:root.save()}
    Timer {id:shutdown;interval:100;onTriggered:Qt.quit()}
}
