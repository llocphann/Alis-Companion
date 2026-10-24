import QtQuick
import QtTest
import Quickshell
import qs.optional.hadanion.modules.abyss.companion
import "../../../modules/abyss/companion/WullScene.js" as Scene
import "../../../modules/abyss/companion/WullMotionData.js" as Curves
Window {
    id: root
    visible:true;width:1200;height:820;color:"#051521"
    property bool animate:false
    property bool allowed:true
    property bool watchPointer:false
    property real wantedReveal:1
    property var popup:null
    readonly property var scene:Scene.fromParticipants({width:width,height:height,hostWidth:112,hostHeight:98,scale:1,
        insets:{top:20,right:20,bottom:20,left:20}},popup ? {popup:{surfaceSettled:false,geometry:{edge:"top",surface:popup}}} : {},[])
    function named(item,name) {
        if(item.objectName===name)return item
        for(const child of item.data ?? item.children ?? []){const match=named(child,name);if(match)return match}
        return null
    }
    function place(point):void {animate=false;presence.hiddenUntil=0;presence.appear(point);input.wait(30);animate=true;input.wait(30)}
    HoverHandler {
        parent:root.contentItem;target:null;blocking:false
        onPointChanged: if (hovered) {
            presence.pointerX=point.scenePosition.x;presence.pointerY=point.scenePosition.y;presence.pointerFresh=true
        }
        onHoveredChanged:if(!hovered)presence.pointerFresh=false
    }
    WullPresence {
        id:presence;scene:root.scene;actor:actor;permitted:root.allowed;requestedReveal:root.wantedReveal;motionEnabled:root.animate
        pointerReactionsEnabled:root.watchPointer
        onStopRequested:actor.stopTravel()
        onResetRequested:(x,y,edge)=>actor.resetTo(x,y,edge)
    }
    AbyssCompanion {
        id:actor;width:112;height:98;managedPlacement:true;upright:true;connectedWater:true;effectsEnabled:false
        character:Quickshell.env("COMPANION_TEST_CHARACTER")==="octo" ? "octo" : "aqua"
        reveal:presence.renderedReveal;edge:presence.emergenceEdge;emergenceEdge:presence.emergenceEdge
        standingAngle:presence.standingAngle;surfaceSupported:presence.grounded;motionEnabled:root.animate
        appearClip:presence.appearClip;hideClip:presence.hideClip
        travelEnabled:presence.traveling;travelMode:presence.mode;travelDuration:presence.duration
        travelArc:presence.arc;travelNormalX:presence.normalX;travelNormalY:presence.normalY
        targetX:presence.targetX;targetY:presence.targetY
        travelDirection:presence.directionX/Math.max(1,Math.hypot(presence.directionX,presence.directionY))
        travelDirectionY:presence.directionY/Math.max(1,Math.hypot(presence.directionX,presence.directionY))
        onTravelCompleted:presence.arrived()
        dragEnabled:true
        onDragStarted:presence.beginDrag()
        onDragPositionRequested:(x,y)=>presence.dragTo(x,y)
        onDragEnded:presence.endDrag()
    }
    TestCase {
        id:input;when:false;optional:true
        function check(value,message):void {if(!value)throw new Error(message)}
        function checkPresentations(body,gait):void {
            const entries=["emerge","launch","stuckLaunch","stuckJump","riseJump"]
            const exits=["dive","diveJump",actor.character==="octo" ? "sink" : "pulled"]
            actor.presentationManaged=true
            for(const edge of ["top","bottom","left","right"]) {
                root.place(Scene.edgePoint(root.scene,edge,.5))
                actor.adoptTo(actor.x,actor.y,edge,1)
                // These are the production presentation and body bindings;
                // only their existing finite progress clock is sampled here.
                for(const leaving of [false,true])for(const clip of leaving ? exits : entries) {
                    if(leaving)actor.hideClip=clip
                    else actor.appearClip=clip
                    actor.leaving=leaving
                    for(const phase of [.02,.12,.18,.3,.4,.55,.65,.77,.86,.94,.98]) {
                        actor.presentation=leaving ? 1-phase : phase
                        wait(1)
                        const label=actor.character+"/"+edge+"/"+clip+"@"+phase
                        check(actor.motionAction===clip && gait.clip===clip,"wrong composite caller "+label)
                        check(Math.abs(body.modelPitch)<=30 && Math.abs(body.modelRoll)<=20,"tipping body "+label)
                        check(body.poseScaleX>=.6 && body.poseScaleY>=.6,"collapsed body "+label)
                        check(Math.abs(actor.emergenceNormal-actor.curves.sample(clip,"normal",phase))<.00001,
                            "detached launch/sink clock "+label)
                        check(!actor.inputReady,"partial transition retained input "+label)
                        check(body.orientationAngle===({top:180,bottom:0,left:90,right:-90})[edge],
                            "transition changed supporting edge "+label)
                    }
                    actor.presentation=leaving ? 0 : 1
                    wait(1)
                    check(leaving ? !actor.visible && !actor.inputReady : actor.inputReady,
                        "transition endpoint did not release input/visibility "+clip)
                    // Reverse an interrupted transition through the real
                    // presentation selector, without creating another actor.
                    actor.leaving=!leaving;actor.presentation=.47;wait(1)
                    check(Math.abs(body.modelPitch)<=30 && Math.abs(body.modelRoll)<=20,
                        "interrupted blend revived tipping pose "+clip)
                    actor.leaving=leaving
                }
            }
            actor.appearClip=Qt.binding(()=>presence.appearClip)
            actor.hideClip=Qt.binding(()=>presence.hideClip)
            actor.presentationManaged=false
            root.place(Scene.edgePoint(root.scene,"bottom",.5))
        }
        function checkRetiredRoll(body,gait):void {
            for(const edge of ["top","bottom","left","right"]) {
                const point=Scene.edgePoint(root.scene,edge,.5)
                root.place(point)
                const expected=({top:180,bottom:0,left:90,right:-90})[edge]
                // A legacy writable input must not bypass the shared pose.
                body.rollingAngle=720;wait(1)
                check(Math.abs(body.modelRoll)<=14,"legacy body input revived a full turn")
                body.rollingAngle=0
                check(presence.moveTo(Scene.edgePoint(root.scene,edge,.6),false,"roll"),"legacy route lost translation")
                wait(90)
                check(presence.mode==="run" && actor.walking && !actor.rolling && !actor.flying,"legacy route was not a stable run")
                check(Math.abs(body.modelRoll)<=3 && body.orientationAngle===expected,"legacy route rotated off its support")
                // Direct old actor bindings receive the same migration.
                actor.travelMode="roll";wait(50)
                check(actor.effectiveTravelMode==="run" && gait.clip==="run" && !actor.rolling,"direct actor binding revived roll")
                presence.pause(true);actor.travelMode=Qt.binding(()=>presence.mode)
                check(!actor.moving && body.orientationAngle===expected,"interrupted run lost its support frame")
                root.place(point)
                presence.travelId=1;presence.travelFraction=.9;presence.randomState=2000
                presence.explore();wait(60)
                check(presence.traveling && presence.mode==="run" && !actor.rolling,"automatic former-roll branch still rolls")
                presence.pause(true)
            }
            root.place(Scene.edgePoint(root.scene,"bottom",.5))
        }
        function runChecks():void {
            try {
                const body=root.named(actor,"wullLiquidBody"),gait=root.named(actor,"wullLocomotion")
                root.place(Scene.edgePoint(root.scene,"bottom",.5))
                check(actor.inputReady,"retired-action fixture is not fully presented")
                for(const action of ["fall","faceplant","buttplant","fallVanish","ice","roll"]) {
                    check(!actor.perform(action) && actor.curves.clips[action]===undefined,"retired gesture accepted: "+action)
                    actor.pairedAction=action;wait(10)
                    check(actor.motionAction==="" && gait.clip!==action,"paired action revived retired clip: "+action)
                }
                actor.pairedAction=""
                actor.appearClip="faceplant";actor.hideClip="fallVanish";wait(10)
                check(actor.activeAppearClip==="emerge" && actor.activeHideClip==="dive","legacy transition failed safe fallback")
                actor.appearClip=Qt.binding(()=>presence.appearClip);actor.hideClip=Qt.binding(()=>presence.hideClip)
                checkPresentations(body,gait)
                actor.appearClip="roll";actor.hideClip="roll";wait(1)
                check(actor.activeAppearClip==="emerge" && actor.activeHideClip==="dive","legacy roll transition revived a turn")
                actor.appearClip=Qt.binding(()=>presence.appearClip);actor.hideClip=Qt.binding(()=>presence.hideClip)
                checkRetiredRoll(body,gait)
                for(const edge of ["top","bottom","left","right"]) {
                    const point=Scene.edgePoint(root.scene,edge,.5);root.place(point)
                    check(presence.grounded,"rim not grounded: "+edge)
                    check(body.orientationAngle===({top:180,bottom:0,left:90,right:-90})[edge],"wrong standing orientation: "+edge)
                    const angle=body.orientationAngle*Math.PI/180
                    const face=root.named(body,"wullFace"),center=face.mapToItem(root.contentItem,38,46),right=face.mapToItem(root.contentItem,58,46)
                    check((right.x-center.x)*Math.cos(angle)+(right.y-center.y)*Math.sin(angle)>8,"face did not turn with body: "+edge)
                    actor.pointerFresh=true;actor.pointerX=.7*Math.cos(angle);actor.pointerY=.7*Math.sin(angle);wait(20)
                    check(actor.attention.x>.6 && Math.abs(actor.attention.y)<.01,"pointer not projected into rotated face: "+edge)
                    actor.pointerFresh=false
                    const along=Scene.edgePoint(root.scene,edge,.6)
                    check(presence.moveTo(along,false),"oriented walk rejected")
                    wait(50);check(actor.walking && presence.mode==="walk","side walk used flight")
                    check(Math.abs(presence.duration-Math.round(Scene.distance(point,along)/16*1000))<=1,"walk not slowed to 16")
                    presence.pause(true)
                }
                root.place(Scene.edgePoint(root.scene,"top",.5))
                root.popup={x:400,y:20,width:360,height:120};wait(140)
                check(actor.visible && actor.presentation>.99 && presence.placement.key==="popup","popup hid Wull instead of carrying it")
                check(Math.abs(actor.y-142)<.2 && body.orientationAngle===180,"growing popup attachment wrong")
                check(actor.gesture==="balance","popup did not prompt balance")
                root.popup={x:400,y:20,width:360,height:200};wait(100)
                check(Math.abs(actor.y-222)<.2 && actor.presentation>.99,"popup growth replayed appearance")
                actor.stopGesture();presence.randomState=2000;presence.gestureFinished("balance");wait(80)
                check(!presence.traveling && presence.placement.key==="popup" && !actor.flying,"balance still triggered a fall")
                root.popup=null;wait(80)
                check(presence.supportRecovery && presence.mode==="fly" && actor.flying && !presence.portalActive,"lost support did not use flight recovery")
                presence.pause();wait(20)
                check(presence.traveling,"ordinary pause interrupted support recovery")
                tryCompare(presence,"traveling",false,presence.duration+700)
                check(!presence.supportRecovery && presence.grounded && presence.placement.support.key==="bottom","flight recovery did not land")
                root.place(Scene.annotate(root.scene,{x:550,y:300},"top","drop","drop"))
                presence.beginDrag();check(presence.dragging,"gentle-release fixture did not begin a drag")
                presence.endDrag();wait(40)
                check(presence.supportRecovery && presence.mode==="fly" && actor.flying,"gentle release still fell or remained unsupported")
                tryCompare(presence,"traveling",false,presence.duration+700)
                check(presence.grounded && actor.inputReady,"gentle release did not settle on live support")
                root.place(Scene.annotate(root.scene,{x:550,y:300},"top","drop","drop"))
                check(presence.recoverSupport(),"motion-cancel fixture could not start recovery")
                wait(40);root.animate=false;wait(40)
                check(!presence.traveling && !presence.supportRecovery && !actor.moving,"reduced motion retained recovery travel")
                root.place(Scene.annotate(root.scene,{x:550,y:300},"top","drop","drop"))
                check(presence.recoverSupport(),"disable fixture could not start recovery")
                wait(40);root.allowed=false;wait(40)
                check(!presence.traveling && !presence.supportRecovery && !actor.moving && !actor.visible,"disable retained recovery or actor")
                root.allowed=true
                root.popup=null;root.place(Scene.edgePoint(root.scene,"bottom",.5))
                const notice=root.named(presence,"wullPointerNoticeDeadline"),noticePoint=presence.position()
                presence.pointerFresh=false;presence.annoyance=0;presence.nextPointerNotice=0;presence.randomState=15
                root.watchPointer=true
                mouseMove(root.contentItem,noticePoint.x+scene.hostWidth/2+350,noticePoint.y+scene.hostHeight/2)
                wait(30)
                check(presence.pointerNearby && notice.running,"350px passive pointer did not arm notice")
                tryCompare(actor,"gesture","wave",1500)
                check(actor.reactionExpression==="happy" && presence.annoyance===0,"passive pointer needed a click or accumulated annoyance")
                const cooldown=presence.nextPointerNotice
                actor.stopGesture();presence.offerPointerNotice();wait(40)
                check(!notice.running && presence.nextPointerNotice===cooldown,"passive notice repeated during cooldown")
                presence.pointerFresh=false;presence.nextPointerNotice=0;presence.randomState=15;wait(20)
                presence.pointerFresh=true;wait(20);check(notice.running,"second pointer approach did not arm")
                mouseMove(root.contentItem,20,200);wait(30)
                check(!presence.pointerNearby && !notice.running,"leaving proximity retained notice deadline")
                presence.pointerFresh=false;presence.interactionHeld=true
                mouseMove(root.contentItem,noticePoint.x+scene.hostWidth/2+350,noticePoint.y+scene.hostHeight/2);wait(30)
                check(!notice.running,"chat/working hold armed a reaction")
                presence.interactionHeld=false;wait(20);check(notice.running,"idle pointer was not reconsidered "+JSON.stringify({
                    nearby:presence.pointerNearby,fresh:presence.pointerFresh,held:presence.interactionHeld,directed:presence.directed,
                    gesture:actor.gesture,presentation:actor.presentation,traveling:presence.traveling,next:presence.nextPointerNotice,
                    pointer:[presence.pointerX,presence.pointerY],position:presence.position()}))
                root.watchPointer=false;wait(20);check(!notice.running,"disabled pointer reactions retained a deadline")
                actor.stopGesture();presence.annoyance=0
                presence.nearbyClick(noticePoint.x+scene.hostWidth/2+461,noticePoint.y+scene.hostHeight/2)
                check(presence.annoyance===0,"distant click disturbed Wull")
                presence.randomState=0
                presence.nearbyClick(noticePoint.x+scene.hostWidth/2+350,noticePoint.y+scene.hostHeight/2)
                check(presence.annoyance===1 && actor.gesture==="startle","350px nearby click was ignored")
                actor.stopGesture();presence.pendingReaction="";presence.annoyance=0
                const point=presence.position();presence.randomState=0;presence.nearbyClick(point.x+120,point.y+45)
                check(actor.gesture==="startle" && actor.reactionExpression==="surprised","near click did not startle")
                wait(1000);check(presence.traveling && presence.mode==="run","startled Wull did not evade")
                check(Math.abs(presence.duration-3125)<=1,"run is not former walk speed")
                presence.pause(true);actor.stopGesture()
                presence.annoyance=4;presence.lastDisturbance=Date.now();presence.randomState=1000
                presence.nearbyClick(actor.x+100,actor.y+45)
                check(actor.reactionExpression==="angry" && (presence.traveling || presence.hiddenUntil>Date.now()),"repeated disturbance lacked anger/avoidance")
                root.place(Scene.annotate(root.scene,{x:550,y:300},"top","drop","drop"))
                mousePress(body,38,49,Qt.LeftButton)
                for(const x of [680,540,690,530,680,540,690,530]) {
                    presence.dragSampleTime=Date.now()-50;mouseMove(root.contentItem,x+55,345,30,Qt.LeftButton);wait(50)
                }
                check(presence.shakeReversals>=4 && actor.reactionExpression==="angry","shaking did not annoy Wull "+JSON.stringify({dragging:presence.dragging,reversals:presence.shakeReversals,expression:actor.reactionExpression,x:actor.x,y:actor.y,velocity:presence.dragVelocityX}))
                mouseRelease(root.contentItem,595,345,Qt.LeftButton);wait(30);check(presence.hiddenUntil>Date.now(),"shaken Wull did not take a break")
                root.animate=false;presence.hideImmediately();presence.hiddenUntil=0
                root.animate=true;presence.appear(Scene.edgePoint(root.scene,"bottom",.5));presence.peekOnly=true
                wait(520)
                const deadline=root.named(presence,"wullPeekDeadline");deadline.interval=20;deadline.restart()
                wait(1500)
                check(!presence.visitActive && presence.renderedReveal===0 && presence.hiddenUntil>Date.now(),"peek-only always emerged")
                root.popup={x:400,y:20,width:360,height:80};wait(100)
                check(!presence.visitActive && presence.renderedReveal===0,"popup scene changes bypassed the peek-only break")
                root.popup=null;root.place(Scene.edgePoint(root.scene,"bottom",.5))
                presence.hiddenUntil=Date.now()+10000;presence.randomState=2000;presence.retreat();wait(120)
                check(actor.gesture!=="ice" && ["dive","diveJump",...(actor.character==="octo" ? ["sink"] : [])].includes(presence.hideClip),"retreat selected a retired or wrong-cast transition")
                tryCompare(actor,"presentation",0,actor.curves.clips[actor.activeHideClip].duration+700)
                check(!presence.visitActive && !presence.traveling && presence.renderedReveal===0,"retreat retained a retry or live actor")
                // These raw authoring assertions retain archival asset coverage;
                // runtime rejection above proves they are no longer selectable.
                for(const clip of ["riseJump","launch","stuckJump","stuckLaunch","faceplant","buttplant","diveJump","sink","fallVanish"]) {
                    check(Curves.clips[clip] && Curves.clips[clip].tracks.normal,"missing authored transition "+clip)
                    check(Curves.clips[clip].tracks.eyeOpen.some(k=>k[1]===0),"transition lacks closed-eye pose "+clip)
                }
                root.animate=false;presence.hideImmediately();presence.hiddenUntil=0
                root.wantedReveal=1;root.animate=true;presence.appear(Scene.edgePoint(root.scene,"bottom",.5))
                presence.peekOnly=false;presence.peekIntro=false;presence.renderedReveal=1
                wait(120);root.wantedReveal=0;wait(150)
                check(presence.visitActive && !presence.retreating,"native hide cut the entrance short")
                tryCompare(actor,"presentation",1,4500)
                check(presence.fullyPresentSince>0 && root.named(presence,"wullFullVisitDeadline").running,"full visit minimum was not armed")
                wait(120);check(actor.visible && !presence.retreating,"newly appeared actor immediately vanished")
                presence.fullyPresentSince=Date.now()-presence.minimumFullVisit-10;presence.synchronize()
                wait(80);check(presence.retreating,"minimum visit never released the hide")
                check(Curves.clips.sink.duration>=5000,"quicksand remains too fast")
                check(Curves.sample("launch","normal",.30)<-1 && Curves.sample("riseJump","normal",.36)<-.8,"entrance arc remained ordinary")
                check(Math.abs(Curves.sample("buttplant","pitch",.60))>60 && Curves.sample("buttplant","foot0Z",.60)>14,"backside landing has no spatial seated pose")
                check(Curves.sample("buttplant","scaleY",.60)>.95,"backside fall flattened Wull")
                root.allowed=false;wait(40)
                check(!actor.visible && !gait.active && !root.named(presence,"wullPeekDeadline").running && !notice.running
                    && !root.named(presence,"wullFullVisitDeadline").running,"policy hide retained clocks")
                console.log("WULL_ALIVE=PASS "+actor.character+" compositeEntryExitPoses fourEdgeTransitionClocks interruptedPresentation inputEndpoints fourRimOrientation rotatedFaceGaze slowerWalk formerWalkRun popupCarry balanceNoFall supportRecovery gentleRelease recoveryCancel recoveryDisable retiredActions legacyFallback widerClick passiveNotice noticeCooldown leaveCancel chatHold startleEvade annoyance shakeBreak peekOnly finiteRetreat archivalCurves")
            } catch(e) {console.error("WULL_ALIVE=FAIL "+e)}
            shutdown.start()
        }
    }
    Timer {interval:100;running:true;onTriggered:input.runChecks()}
    Timer {id:shutdown;interval:150;onTriggered:Qt.quit()}
}
