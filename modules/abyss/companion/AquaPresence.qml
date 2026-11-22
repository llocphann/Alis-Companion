import QtQuick
import "AquaScene.js" as Scene
import "AquaTravel.js" as Travel
import "CompanionMotion.js" as Motion

// One controller per existing output window; only the permitted output acts.
// Native visit/wander events plus one sustained-surface deadline drive choices.
Item {
    id: root
    property var scene: null
    property var actor: null
    property bool handoffActive: false
    property bool permitted: false
    property bool occluded: false
    property real requestedReveal: 0
    property bool motionEnabled: true
    property bool interactionHeld: false
    property string personality: "balanced"
    property bool directed: false
    property string homeEdge: ""
    property real homeAlong: 0.72
    property double travelId: 0
    property real travelFraction: 0.5
    property double randomState: Date.now() % 4294967296
    property var placement: ({qualified:false})
    property var destination: ({qualified:false})
    property var waypoints: []
    property int waypoint: 0
    property real targetX: 0
    property real targetY: 0
    property real renderedReveal: 0
    property bool visitActive: false
    property bool traveling: false
    property bool portalActive: false
    property real portalProgress: 0
    property bool portalHopped: false
    property var portalOrigin: null
    property var portalSource: null
    property var portalDestination: null
    readonly property real portalReveal: portalActive ? Travel.reveal(portalProgress) : 1
    readonly property bool portalMoving: portalActive && Travel.portalMoving(portalProgress)
    property bool dragging: false
    property bool retreating: false
    property bool releasedFlight: false
    property bool supportRecovery: false
    property bool returningForChat: false
    readonly property real sceneScale: scene?.scale ?? 1
    readonly property real sceneHostWidth: scene?.hostWidth ?? 112
    readonly property real sceneHostHeight: scene?.hostHeight ?? 98
    readonly property real nearHomeRadius: 240*sceneScale
    readonly property bool surfaceBound: true
    property bool peekIntro: false
    property bool peekOnly: false
    property bool suspendedByOcclusion: false
    property bool sawOcclusionWhileHidden: false
    property var occludedPlacement: null
    property bool occludedPeekOnly: false
    property string occludedHomeEdge: ""
    property real occludedHomeAlong: 0.72
    property double fullyPresentSince: 0
    readonly property int minimumFullVisit: 8000
    property double hiddenUntil: 0
    property int annoyance: 0
    property double lastDisturbance: 0
    property double lastBalance: 0
    property bool pointerFresh: false
    property bool pointerReactionsEnabled: true
    property real pointerX: 0
    property real pointerY: 0
    property double nextPointerNotice: 0
    readonly property real pointerNoticeRadius: 420*sceneScale
    readonly property real nearbyClickRadius: 460*sceneScale
    readonly property bool pointerNearby: pointerFresh && pointerReactionsEnabled
        && permitted && visitActive && motionEnabled && !interactionHeld
        && !handoffActive && !dragging && !retreating && !traveling && !portalActive && !directed
        && actor && !actor.gesturing && actor.reactionExpression!=="angry"
        && actor.presentation>.99 && requestedReveal>.99
        && Math.hypot(pointerX-actor.x+(actor.scale-1)*actor.width/2-sceneHostWidth/2,
            pointerY-actor.y+(actor.scale-1)*actor.height/2-sceneHostHeight/2)<=pointerNoticeRadius
    property real dragVelocityX: 0
    property real dragVelocityY: 0
    property double dragSampleTime: 0
    property var dragSamples: []
    property real throwSpeed: 0
    property real throwArc: 0
    readonly property real throwThreshold: .8*sceneScale
    property int shakeReversals: 0
    property string pendingReaction: ""
    property real avoidX: 0
    property real avoidY: 0
    property string appearClip: "emerge"
    property real appearanceOffsetX: 0
    property real appearanceOffsetY: 0
    property string hideClip: "dive"
    readonly property real peekReveal: .64
    readonly property bool peeking: renderedReveal>0 && renderedReveal<.99
    property string mode: "fly"
    property int duration: 1000
    property real arc: 0
    property real normalX: 0
    property real normalY: -1
    // Airborne motion retains the takeoff support frame. `grounded` describes
    // current contact and becomes false during Jump/Fly; it must not rotate
    // the actor back to the bottom Edge before the destination is adopted.
    property real activeSlopeAngle: NaN
    readonly property bool _slopeAngleValid: Number.isFinite(activeSlopeAngle) || Number.isFinite(placement.support?.angle)
    property real _smoothedSlopeAngle: Number.isFinite(activeSlopeAngle)
        ? activeSlopeAngle
        : (Number.isFinite(placement.support?.angle) ? placement.support.angle : 0)
    Behavior on _smoothedSlopeAngle {
        enabled: root.motionEnabled && root.traveling && ["walk","run"].includes(root.mode) && root._slopeAngleValid
        RotationAnimation {
            direction: RotationAnimation.Shortest
            duration: 120
            easing.type: Easing.OutCubic
        }
    }
    readonly property real standingAngle: placement.grounded
        ? (root._slopeAngleValid
            ? root._smoothedSlopeAngle
            : (emergenceEdge==="top" ? 180 : emergenceEdge==="left" ? 90 : emergenceEdge==="right" ? -90 : 0))
        : 0
    property real directionX: 1
    property real directionY: 0
    property bool surfaceDue: false
    property bool initialized: false
    readonly property bool qualified: Scene.valid(scene) && placement.qualified
    readonly property bool ballistic: traveling && mode==="jump"
    readonly property bool grounded: !dragging && (portalActive || !traveling || ["walk","run"].includes(Motion.travelMode(mode))) && !!placement.grounded
    readonly property string emergenceEdge: placement.support?.edge ?? placement.edge ?? "top"
    readonly property bool canExplore: permitted && visitActive && requestedReveal>.99
        && motionEnabled && !interactionHeld && !handoffActive && !dragging && !retreating && !portalActive && Date.now()>=hiddenUntil
        && actor && !actor.hovered && actor.presentation>.99
    readonly property string surfaceKey: (scene?.surfaces ?? []).map(s=>s.key).join("|")
    signal resetRequested(real x, real y, string edge)
    signal stopRequested()
    signal settled()
    signal waterInteraction(var contact, string action)
    signal handoffRequested()
    function disturb(action, selected): void {
        const contact=selected?.contact ?? null
        if (permitted && motionEnabled && contact) waterInteraction(contact,action)
    }

    function random(): real {
        randomState = (randomState*1664525+1013904223) % 4294967296
        return randomState/4294967296
    }
    function setReaction(expression, milliseconds = 3000): void {
        if (!actor) return
        actor.reactionExpression=expression
        reactionDeadline.interval=milliseconds;reactionDeadline.restart()
    }
    function appearanceLanding(source): var {
        const support=Scene.supportAt(scene,source,source.support?.edge ?? source.edge)
        if (!support) return source
        const horizontal=support.axis==="x", extent=horizontal ? scene.hostWidth : scene.hostHeight
        const coordinate=horizontal ? source.x : source.y
        const roomBefore=coordinate-support.from
        const roomAfter=support.to-extent-coordinate
        const preferred=roomAfter>=roomBefore ? 1 : -1
        const n=Scene.normal(support.edge)
        const arcHeight=1.12*Math.max(scene.hostWidth,scene.hostHeight)
        for (const distance of [150,115,82].map(value=>value*scene.scale)) {
            for (const sign of [preferred,-preferred]) {
                const along=Scene.clamp(coordinate+sign*distance,support.from,support.to-extent)
                if (Math.abs(along-coordinate)<48*scene.scale) continue
                const point=horizontal ? {x:along,y:source.y} : {x:source.x,y:along}
                const candidate=Scene.annotate(scene,point,support.edge,source.kind,source.key)
                if (!candidate.grounded || candidate.support?.key!==support.key
                        || candidate.support?.edge!==support.edge) continue
                if (Scene.clearArc(scene,source,candidate,arcHeight,n.nx,n.ny))
                    return candidate
            }
        }
        return source
    }
    function chooseAppearance(selected, anchorToSelected = false): var {
        appearClip="emerge";appearanceOffsetX=0;appearanceOffsetY=0
        if (!motionEnabled) return selected
        const neighbor=appearanceLanding(selected)
        if (!neighbor?.qualified || Scene.distance(selected,neighbor)<1) return selected
        const roll=random()
        appearClip=roll<.25 ? "launch" : roll<.45 ? "stuckLaunch" : roll<.65 ? "stuckJump" : "riseJump"
        if (anchorToSelected) {
            appearanceOffsetX=neighbor.x-selected.x
            appearanceOffsetY=neighbor.y-selected.y
            return selected
        }
        appearanceOffsetX=selected.x-neighbor.x
        appearanceOffsetY=selected.y-neighbor.y
        return neighbor
    }
    function homePlacement(): var {
        if (!Scene.valid(scene) || !homeEdge) return {qualified:false}
        return Scene.homePoint(scene,homeEdge,homeAlong)
    }
    function isAtHome(point = position()): bool {
        const home=homePlacement()
        if (!home.qualified) return true
        return placement.kind===home.kind
            && placement.key===home.key
            && emergenceEdge===home.edge
            && Scene.distance(point,home)<=Math.max(8,12*(scene?.scale ?? 1))
    }
    function isNearHome(point = position()): bool {
        const home=homePlacement()
        if (!home.qualified) return true
        return placement.kind===home.kind
            && placement.key===home.key
            && emergenceEdge===home.edge
            && Scene.distance(point,home)<=nearHomeRadius
    }
    function nearHomeTarget(from = position()): var {
        const home=homePlacement()
        if (!home.qualified || !Scene.valid(scene)) return home
        const support=Scene.supportAt(scene,home,home.edge)
        if (!support) return home
        const scale=scene?.scale ?? 1
        const horizontal=support.axis==="x", extent=horizontal ? scene.hostWidth : scene.hostHeight
        const homeCoord=horizontal ? home.x : home.y
        const fromCoord=horizontal ? from.x : from.y
        const preferredDir=fromCoord>=homeCoord ? 1 : -1
        const jitter=0.75+0.45*random()
        for (const baseOffset of [110,75,45]) {
            const dist=Math.min(nearHomeRadius*0.85,baseOffset*scale*jitter)
            for (const sign of [preferredDir,-preferredDir]) {
                const along=Scene.clamp(homeCoord+sign*dist,support.from,support.to-extent)
                const point=horizontal ? {x:along,y:home.y} : {x:home.x,y:along}
                const candidate=Scene.annotate(scene,point,support.edge,home.kind,home.key)
                if (candidate.qualified && candidate.grounded && Scene.clearAt(scene,candidate)
                        && Scene.distance(candidate,home)<=nearHomeRadius)
                    return candidate
            }
        }
        return home
    }
    function fastReturnNearHome(): bool {
        if (!permitted || !Scene.valid(scene) || !homeEdge) return false
        homeReturnDeadline.stop()
        if (returningForChat && traveling) return true
        if (!motionEnabled || !visitActive || retreating || !actor) {
            returnToHome(true)
            return false
        }
        peekDeadline.stop();peekIntro=false;peekOnly=false
        if (requestedReveal>0) renderedReveal=1
        if (actor.presentation<.99) {
            const cur=position()
            actor.adoptTo(
                cur.x+(actor.scale-1)*actor.width/2,
                cur.y+(actor.scale-1)*actor.height/2,
                emergenceEdge,
                1
            )
        }
        if (isNearHome()) {
            if (traveling || portalActive) pause(false)
            return false
        }
        const here=Object.assign(position(),{edge:emergenceEdge})
        const target=nearHomeTarget(here)
        if (!target.qualified || !Scene.clearAt(scene,target)) {
            returnToHome(true)
            return false
        }
        const route=Scene.path(scene,here,target)
        if (!route.qualified) {
            returnToHome(true)
            return false
        }
        disturb("depart",Scene.annotate(scene,here,placement.edge,placement.kind,placement.key))
        clearMotion()
        returningForChat=true
        destination=target
        if (route.mode==="walk") {
            const roll=random()
            const canJump=Scene.clearArc(scene,here,target,42*scene.scale,placement.support?.nx ?? 0,placement.support?.ny ?? -1)
            mode=canJump && roll<.45 ? "jump" : roll<.82 ? "run" : "fly"
            supportRecovery=mode==="fly"
        } else {
            mode="fly"
            supportRecovery=true
        }
        retreating=false;waypoints=route.points;waypoint=0
        traveling=true
        advance()
        return traveling
    }
    function scheduleHomeReturn(): void {
        homeReturnDeadline.stop()
    }
    function returnToHome(immediate = false): bool {
        if (!permitted || occluded || !Scene.valid(scene) || !homeEdge || requestedReveal<=0 || Date.now()<hiddenUntil) return false
        const home=homePlacement()
        if (!home.qualified || !Scene.clearAt(scene,home)) return false
        homeReturnDeadline.stop()
        if (!immediate && (dragging || interactionHeld || (actor && actor.hovered))) {
            homeReturnDeadline.restart()
            return false
        }
        if (isAtHome() && !traveling && !portalActive && !dragging && visitActive && !retreating) {
            peekOnly=false;peekIntro=false
            if (requestedReveal>0) renderedReveal=1
            return true
        }
        if (immediate || !motionEnabled || !visitActive || retreating || !actor || actor.presentation<.99) {
            clearMotion()
            peekDeadline.stop();peekIntro=false;peekOnly=false
            appearClip="emerge";appearanceOffsetX=0;appearanceOffsetY=0
            placement=home;destination=home
            targetX=home.x;targetY=home.y
            retreating=false;visitActive=true
            renderedReveal=requestedReveal>0 ? 1 : requestedReveal
            if (actor) {
                actor.adoptTo(
                    targetX+(actor.scale-1)*actor.width/2,
                    targetY+(actor.scale-1)*actor.height/2,
                    home.edge,
                    renderedReveal
                )
            }
            disturb("land",home)
            return true
        }
        if (moveTo(home,false)) return true
        clearMotion()
        placement=home;destination=home
        targetX=home.x;targetY=home.y
        if (actor) {
            actor.adoptTo(
                targetX+(actor.scale-1)*actor.width/2,
                targetY+(actor.scale-1)*actor.height/2,
                home.edge,
                renderedReveal
            )
        }
        return true
    }
    function position() {
        return (actor && (traveling || dragging || portalActive))
            ? {x:actor.x-(actor.scale-1)*actor.width/2, y:actor.y-(actor.scale-1)*actor.height/2}
            : {x:targetX,y:targetY}
    }
    function clearMotion(): void {
        if (portalActive) {
            portalTween.stop();portalActive=false
            if (mode==="portal") mode="walk"
            const fallback=!portalHopped && portalOrigin?.qualified && Scene.clearAt(scene,portalOrigin)
                ? portalOrigin
                : portalHopped && destination?.qualified && Scene.clearAt(scene,destination)
                    ? destination : null
            if (fallback) {
                placement=fallback;destination=fallback;targetX=fallback.x;targetY=fallback.y
                if (actor) actor.adoptTo(targetX+(actor.scale-1)*actor.width/2,targetY+(actor.scale-1)*actor.height/2,emergenceEdge,renderedReveal)
            } else {
                const here=position();targetX=here.x;targetY=here.y
            }
        }
        stopRequested()
        activeSlopeAngle=NaN
        traveling=false; releasedFlight=false; supportRecovery=false; returningForChat=false; throwSpeed=0; throwArc=0; waypoints=[]; waypoint=0
    }
    function clearOcclusionState(): void {
        suspendedByOcclusion=false
        sawOcclusionWhileHidden=false
        occludedPlacement=null
        occludedPeekOnly=false
        occludedHomeEdge=""
        occludedHomeAlong=0.72
    }
    function captureOcclusionState(): void {
        if (suspendedByOcclusion || !visitActive || retreating || requestedReveal<=0 || Date.now()<hiddenUntil)
            return
        let snap=placement
        if (portalActive || traveling || !placement?.grounded) {
            const current=position()
            const support=Scene.supportAt(scene,current,placement?.support?.edge ?? placement?.edge)
            if (support) {
                const isEdge=Scene.edges.includes(support.key)
                const candidate=Scene.annotate(scene,current,support.edge,isEdge ? "edge" : "surface",support.key)
                if (candidate.qualified && candidate.grounded && Scene.clearAt(scene,candidate))
                    snap=candidate
            } else if (destination?.qualified && destination?.grounded && Scene.clearAt(scene,destination)) {
                snap=destination
            } else if (!placement?.qualified || !placement?.grounded) {
                const home=homePlacement()
                if (home.qualified) snap=home
            }
        }
        if (!snap?.qualified) return
        suspendedByOcclusion=true
        if (occluded) sawOcclusionWhileHidden=true
        occludedPlacement=Object.assign({},snap,{support:snap.support ? Object.assign({},snap.support) : null})
        occludedPeekOnly=peekOnly && requestedReveal<.99
        occludedHomeEdge=homeEdge
        occludedHomeAlong=homeAlong
    }
    function resumeFromOcclusion(): bool {
        if (!permitted || occluded || !Scene.valid(scene)) return false
        if (!suspendedByOcclusion || !sawOcclusionWhileHidden || requestedReveal<=0 || Date.now()<hiddenUntil) {
            clearOcclusionState()
            return false
        }
        const homeChanged=homeEdge!==occludedHomeEdge || Math.abs(homeAlong-occludedHomeAlong)>0.0001
        let target=null
        if (!homeChanged && occludedPlacement?.qualified) {
            const ride=Scene.ride(scene,{x:occludedPlacement.x,y:occludedPlacement.y},occludedPlacement)
            if (ride.qualified && ride.grounded && Scene.clearAt(scene,ride)) {
                target=ride
            } else if (Scene.clearAt(scene,occludedPlacement)) {
                const support=Scene.supportAt(scene,occludedPlacement,occludedPlacement.support?.edge ?? occludedPlacement.edge)
                if (support) {
                    const isEdge=Scene.edges.includes(support.key)
                    const candidate=Scene.annotate(scene,occludedPlacement,support.edge,isEdge ? "edge" : "surface",support.key)
                    if (candidate.qualified && candidate.grounded && Scene.clearAt(scene,candidate))
                        target=candidate
                }
            }
        }
        if (!target) {
            const home=homePlacement()
            if (home.qualified && Scene.clearAt(scene,home))
                target=home
            else {
                const fallback=Scene.appearance(scene,random(),random(),random())
                if (fallback.qualified && Scene.clearAt(scene,fallback))
                    target=fallback
            }
        }
        const wasPeekOnly=occludedPeekOnly && requestedReveal<.99
        clearOcclusionState()
        if (!target || !target.qualified || !Scene.clearAt(scene,target))
            return false
        clearMotion()
        recoveryDeadline.stop()
        fullVisitDeadline.stop()
        peekDeadline.stop()
        peekIntro=false
        peekOnly=wasPeekOnly
        appearClip="emerge"
        appearanceOffsetX=0;appearanceOffsetY=0
        placement=target;destination=target
        targetX=target.x;targetY=target.y
        retreating=false;visitActive=true
        renderedReveal=wasPeekOnly ? peekReveal : requestedReveal
        fullyPresentSince=(!wasPeekOnly && renderedReveal>.99) ? Date.now() : 0
        if (actor) {
            actor.adoptTo(
                targetX+(actor.scale-1)*actor.width/2,
                targetY+(actor.scale-1)*actor.height/2,
                target.edge,
                renderedReveal
            )
        }
        scheduleSurface()
        scheduleHomeReturn()
        return true
    }
    function hideImmediately(): void {
        clearMotion()
        appearanceOffsetX=0;appearanceOffsetY=0
        peekDeadline.stop(); peekIntro=false
        surfaceDeadline.stop(); surfaceDue=false
        homeReturnDeadline.stop()
        recoveryDeadline.stop();reactionDeadline.stop()
        fullVisitDeadline.stop();fullyPresentSince=0
        if (actor) {actor.reactionExpression="";actor.stopGesture()}
        dragging=false; retreating=false; visitActive=false; renderedReveal=0
        resetRequested(targetX,targetY,emergenceEdge)
    }
    function appear(selected): void {
        clearOcclusionState()
        const useHome=!selected && homeEdge.length>0
        if (!selected) {
            const home=homePlacement()
            selected=home.qualified && Scene.clearAt(scene,home)
                ? home : Scene.appearance(scene,random(),random(),random())
        }
        if (!selected.qualified || !Scene.clearAt(scene,selected)) {hideImmediately();return}
        clearMotion()
        recoveryDeadline.stop()
        fullVisitDeadline.stop();fullyPresentSince=0
        const source=selected
        const landing=chooseAppearance(source,useHome)
        placement=landing; destination=landing
        targetX=landing.x; targetY=landing.y
        retreating=false; visitActive=true
        resetRequested(targetX,targetY,source.edge)
        peekDeadline.stop()
        // Jump-style entrances are a complete water-to-ground arc. Peeking
        // remains available only to the simple emerge animation so a partial
        // reveal cannot freeze halfway between the source and landing point.
        peekIntro=appearClip==="emerge" && motionEnabled && requestedReveal>.99
        peekOnly=peekIntro && random()<.28
        peekDeadline.interval=550+Math.floor(random()*450)
        renderedReveal=peekIntro || requestedReveal<.99 ? peekReveal : requestedReveal
        disturb(peekIntro || requestedReveal<.99 ? "peek" : "emerge",source)
        scheduleSurface()
        scheduleHomeReturn()
    }
    function synchronize(): void {
        if (!initialized) return
        if (!permitted || occluded || !Scene.valid(scene)) {hideImmediately();return}
        if (handoffActive) return
        if (hiddenUntil>Date.now()) {
            clearOcclusionState()
            if(!recoveryDeadline.running){recoveryDeadline.interval=Math.round(hiddenUntil-Date.now());recoveryDeadline.restart()}
            if (visitActive && !retreating) retreat()
            return
        }
        if (requestedReveal<=0) {
            clearOcclusionState()
            // Native visit deadlines may expire during a long entrance or a
            // late surface reveal. Finish the entrance and grant a real visit.
            // Policy hides and deliberate peek-only withdrawals remain immediate.
            if (visitActive && !retreating && !peekOnly && !hiddenUntil && actor) {
                if (peekIntro) {peekIntro=false;peekDeadline.stop();renderedReveal=1}
                if (actor.presentation<.99 || !fullyPresentSince) return
                const remaining=minimumFullVisit-(Date.now()-fullyPresentSince)
                if (remaining>0) {fullVisitDeadline.interval=Math.ceil(remaining);fullVisitDeadline.restart();return}
            }
            if (visitActive && !retreating && !dragging) retreat()
            return
        }
        fullVisitDeadline.stop()
        if (!visitActive || retreating) {
            if (!resumeFromOcclusion())
                appear()
            return
        }
        if (requestedReveal<.99) {
            peekDeadline.stop(); peekIntro=false; renderedReveal=peekReveal
        } else if (!peekIntro) renderedReveal=requestedReveal
        if (requestedReveal<.99) pause()
    }
    function pause(force = false): void {
        if (portalActive) {
            if (force || interactionHeld || !motionEnabled || !permitted || requestedReveal<.99) {
                clearMotion()
                if (visitActive && !dragging && (!placement.grounded || !Scene.clearAt(scene,position())
                        || !Scene.supportAt(scene,position(),placement.support?.edge ?? placement.edge)))
                    recoverSupport()
            }
            return
        }
        if (!traveling || retreating || (!force && (returningForChat || ballistic || !grounded || directed || releasedFlight || supportRecovery || !Scene.supportAt(scene,position())))) return
        const here=position(), support=Scene.supportAt(scene,here)
        targetX=here.x; targetY=here.y
        clearMotion()
        if (!support && visitActive && !dragging) {
            recoverSupport(here)
            return
        }
        const isEdge=!!support && Scene.edges.includes(support.key)
        placement=Scene.annotate(scene,here,support?.edge ?? placement.edge,support ? (isEdge ? "edge" : "surface") : placement.kind,support ? support.key : placement.key)
    }
    function beginTurn(clip): void {
        handoffActive=true
        clearMotion();peekDeadline.stop();surfaceDeadline.stop();fullVisitDeadline.stop()
        peekIntro=false;peekOnly=false;fullyPresentSince=0
        hideClip=clip
        setReaction("panicked",5600)
        disturb(clip==="sink" ? "sink" : clip==="pulled" ? "pulled" : "dive",placement)
        renderedReveal=0
    }
    function finishTurn(selected): void {
        if (!actor) return
        const revealed=permitted && requestedReveal>0
        placement=selected;destination=selected;targetX=selected.x;targetY=selected.y
        hideClip="dive";appearClip="emerge";appearanceOffsetX=0;appearanceOffsetY=0
        retreating=false;visitActive=revealed;renderedReveal=0
        actor.reactionExpression=""
        actor.adoptTo(targetX+(actor.scale-1)*actor.width/2,targetY+(actor.scale-1)*actor.height/2,emergenceEdge,renderedReveal)
        handoffActive=false
        fullyPresentSince=0
        if (revealed) {disturb("emerge",placement);renderedReveal=requestedReveal;scheduleSurface()}
    }
    function moveTo(selected, exit, preferredMode = ""): bool {
        preferredMode=Motion.travelMode(preferredMode)
        if (Motion.retired(preferredMode)) return false
        if (handoffActive || portalActive || !permitted || !actor || actor.presentation<.99 || dragging || (peekIntro && !exit)) return false
        const here=Object.assign(position(),{edge:emergenceEdge})
        // Portal openings are travel-only: never replace an appearance, peek,
        // withdrawal or an explicitly requested physical movement.
        if (!exit && !retreating && visitActive && !peekIntro && !peekOnly
                && renderedReveal > .99 && requestedReveal > .99 && placement.grounded
                && motionEnabled && preferredMode === ""
                && Travel.usePortal(here,selected,scene?.scale)
                && startPortal(here,selected,false)) return true
        const route=Scene.path(scene,here,selected)
        if (!route.qualified) return false
        disturb("depart",Scene.annotate(scene,here,placement.edge,placement.kind,placement.key))
        clearMotion()
        destination=selected; mode=exit ? "fly" : route.mode
        supportRecovery=!exit && preferredMode==="fly"
        if (supportRecovery) mode="fly"
        else if (!exit && route.mode==="walk" && ["run","jump"].includes(preferredMode)) {
            if (preferredMode!=="jump" || Scene.clearArc(scene,here,selected,42*scene.scale,placement.support?.nx ?? 0,placement.support?.ny ?? -1)) mode=preferredMode
        }
        retreating=!!exit; waypoints=route.points; waypoint=0
        traveling=true
        advance()
        return true
    }
    function portalAnchor(point, preferredDirection, distances, edgeHint, kindHint, keyHint): var {
        const support=Scene.supportAt(scene,point,edgeHint)
        if (!support) return null
        const horizontal=support.axis==="x", extent=horizontal ? scene.hostWidth : scene.hostHeight
        const start=horizontal ? point.x : point.y
        const scale=scene?.scale ?? 1
        const signs=preferredDirection>=0 ? [1,-1] : [-1,1]
        for (const rawDistance of distances) {
            const distance=rawDistance*scale
            for (const sign of signs) {
                const coord=Scene.clamp(start+sign*distance,support.from,support.to-extent)
                if (Math.abs(coord-start)<36*scale) continue
                const next=horizontal ? {x:coord,y:point.y} : {x:point.x,y:coord}
                if (!Scene.clearSegment(scene,point,next)) continue
                const candidate=Scene.annotate(scene,next,support.edge,kindHint,keyHint)
                if (candidate.qualified && candidate.grounded
                        && candidate.support?.key===support.key
                        && candidate.support?.edge===support.edge)
                    return candidate
            }
        }
        return null
    }
    function portalSourceValid(): bool {
        if (!Scene.valid(scene) || !portalOrigin?.qualified || !portalSource?.qualified) return false
        if (!Scene.clearAt(scene,portalOrigin) || !Scene.clearAt(scene,portalSource)
                || !Scene.clearSegment(scene,portalOrigin,portalSource)) return false
        const support=Scene.supportAt(scene,portalSource,portalSource.support?.edge ?? portalSource.edge)
        return !!support && support.key===portalSource.support?.key && support.edge===portalSource.support?.edge
    }
    function portalDestinationValid(): bool {
        if (!Scene.valid(scene) || !portalDestination?.qualified || !destination?.qualified) return false
        if (!Scene.clearAt(scene,portalDestination) || !Scene.clearAt(scene,destination)
                || !Scene.clearSegment(scene,portalDestination,destination)) return false
        const exitSupport=Scene.supportAt(scene,portalDestination,portalDestination.support?.edge ?? portalDestination.edge)
        const landSupport=Scene.supportAt(scene,destination,destination.support?.edge ?? destination.edge)
        return !!exitSupport && !!landSupport
            && exitSupport.key===portalDestination.support?.key && exitSupport.edge===portalDestination.support?.edge
            && landSupport.key===destination.support?.key && landSupport.edge===destination.support?.edge
    }
    function startPortal(here, selected, exit): bool {
        if (!Scene.valid(scene) || !Scene.clearAt(scene,here)) return false
        const origin=Scene.annotate(scene,here,emergenceEdge,placement.kind,placement.key)
        if (!origin.qualified || !origin.grounded) return false
        const landing=Scene.annotate(scene,selected,selected.edge,selected.kind,selected.key)
        if (!landing.qualified || !landing.grounded || !Scene.clearAt(scene,landing)) return false
        const scale=scene?.scale ?? 1
        const sourceDir=(origin.support?.axis==="x" ? landing.x-origin.x : landing.y-origin.y)>=0 ? 1 : -1
        const sourceOpen=portalAnchor(origin,sourceDir,[88,72,56,42],origin.edge,origin.kind,origin.key)
        if (!sourceOpen || Scene.distance(sourceOpen,landing)<120*scale) return false
        const exitDir=(landing.support?.axis==="x" ? origin.x-landing.x : origin.y-landing.y)>=0 ? 1 : -1
        const destOpen=portalAnchor(landing,exitDir,[64,48,36],landing.edge,landing.kind,landing.key) ?? landing
        clearMotion()
        portalOrigin=origin
        portalSource=sourceOpen
        portalDestination=destOpen;destination=landing
        directionX=portalSource.x-portalOrigin.x
        directionY=portalSource.y-portalOrigin.y
        portalHopped=false;portalProgress=0;retreating=!!exit;mode="portal"
        disturb("depart",portalSource)
        portalActive=true;traveling=true;portalTween.restart()
        return true
    }
    function portalStep(): void {
        if (!portalActive) return
        if (!permitted || !motionEnabled) {clearMotion();reconcileScene();return}
        if (portalProgress<.5) {
            if (!portalSourceValid() || !portalDestinationValid()) {clearMotion();reconcileScene();return}
            const t=Travel.approachProgress(portalProgress)
            const x=portalOrigin.x+(portalSource.x-portalOrigin.x)*t
            const y=portalOrigin.y+(portalSource.y-portalOrigin.y)*t
            directionX=portalSource.x-portalOrigin.x
            directionY=portalSource.y-portalOrigin.y
            targetX=x;targetY=y
            if (actor) actor.carryTo(x+(actor.scale-1)*actor.width/2,y+(actor.scale-1)*actor.height/2)
            return
        }
        if (!portalDestinationValid()) {clearMotion();reconcileScene();return}
        if (!portalHopped) {
            portalHopped=true
            placement=portalDestination;targetX=portalDestination.x;targetY=portalDestination.y
            if (actor) actor.adoptTo(targetX+(actor.scale-1)*actor.width/2,targetY+(actor.scale-1)*actor.height/2,emergenceEdge,renderedReveal)
        }
        const t=Travel.exitProgress(portalProgress)
        const x=portalDestination.x+(destination.x-portalDestination.x)*t
        const y=portalDestination.y+(destination.y-portalDestination.y)*t
        if (Math.hypot(destination.x-portalDestination.x,destination.y-portalDestination.y)>.1) {
            directionX=destination.x-portalDestination.x
            directionY=destination.y-portalDestination.y
        }
        targetX=x;targetY=y
        if (actor) actor.carryTo(x+(actor.scale-1)*actor.width/2,y+(actor.scale-1)*actor.height/2)
        if (t>=.999) placement=destination
    }
    onPortalProgressChanged:portalStep()
    function advance(): void {
        if (!traveling || !permitted || !Scene.valid(scene)) return
        if (waypoint>=waypoints.length) {
            const exiting=retreating
            activeSlopeAngle=NaN
            placement=destination; targetX=placement.x; targetY=placement.y
            traveling=false; releasedFlight=false; supportRecovery=false; returningForChat=false; waypoints=[]
            if (exiting) {
                handoffRequested()
                if (handoffActive) return
                const exitRoll=random()
                hideClip=actor.character==="octo" && exitRoll<.40 ? "sink" : "diveJump"
                const n=Scene.normal(placement.edge)
                if(hideClip==="diveJump" && !Scene.clearArc(scene,placement,placement,.38*Math.max(scene.hostWidth,scene.hostHeight),n.nx,n.ny)) hideClip="dive"
                if (hideClip==="sink") setReaction("panicked",5600)
                disturb(hideClip==="sink" ? "sink" : "dive",placement)
                visitActive=false; renderedReveal=0; surfaceDeadline.stop()
            } else {
                disturb("land",placement)
                if (placement.grounded && ["jump","fly"].includes(mode) && motionEnabled)
                    actor.perform("land")
                scheduleHomeReturn()
                settled()
            }
            return
        }
        const here=position(), next=waypoints[waypoint++]
        const contourWaypoint = (mode==="walk" || mode==="run") && Number.isFinite(next.angle)
        if (!Scene.clearSegment(scene,here,next) && !(contourWaypoint && Scene.clearAt(scene,next))) {hideImmediately();return}
        activeSlopeAngle = contourWaypoint ? next.angle : NaN
        directionX=next.x-here.x; directionY=next.y-here.y
        const speed=releasedFlight
            ? Math.max(260*scene.scale,Math.min(1450*scene.scale,throwSpeed*780))
            : returningForChat ? 420*scene.scale
            : (retreating || supportRecovery ? 300 : mode==="walk" ? 16 : mode==="run" ? 32 : 105)*scene.scale
        normalX=mode==="jump" ? placement.support?.nx ?? 0 : 0
        normalY=mode==="jump" ? placement.support?.ny ?? -1 : -1
        arc=releasedFlight ? throwArc : mode==="jump" ? 42*scene.scale : mode==="fly" && !retreating && !supportRecovery
            && Scene.clearArc(scene,here,next,12*scene.scale) ? 12*scene.scale : 0
        duration=releasedFlight ? Math.max(200,Math.min(900,Math.round(Scene.distance(here,next)/speed*1000)))
            : returningForChat ? Math.max(240,Math.min(680,Math.round(Scene.distance(here,next)/speed*1000)))
            : mode==="jump" ? 950
            : Math.max((contourWaypoint && waypoints.length>1) ? 45 : 180,Math.round(Scene.distance(here,next)/speed*1000))
        targetX=next.x; targetY=next.y
        if (Math.abs(here.x-next.x)<.1 && Math.abs(here.y-next.y)<.1) Qt.callLater(root.arrived)
    }
    function arrived(): void {
        if (!traveling || portalActive || dragging || !actor || actor.presentation<.99) return
        const here=position()
        if (Math.abs(here.x-targetX)>.2 || Math.abs(here.y-targetY)>.2) return
        advance()
    }
    function retreat(): void {
        peekDeadline.stop(); peekIntro=false
        if (!motionEnabled || !actor) {hideImmediately();return}
        if (actor.presentation<.99) {
            // A peek is already at its water opening: simply retract there.
            disturb("dive",placement)
            clearMotion();visitActive=false;renderedReveal=0;surfaceDeadline.stop();return
        }
        const water=Scene.nearestWater(scene,position())
        if (!water.qualified || !moveTo(water.placement,true)) hideImmediately()
    }
    function explore(): void {
        if (!canExplore || traveling || directed || travelId===0) return
        const here=position(), support=Scene.supportAt(scene,here)
        if (!support) {
            const floor=Scene.landingBelow(scene,here)
            if (floor.qualified) moveTo(floor,false,"fly")
            else {
                const water=Scene.nearestWater(scene,here)
                if (water.qualified) moveTo(water.placement,false)
            }
            return
        }
        const delta=(travelFraction-.5)*240*scene.scale
        let selected={qualified:false}
        if (scene.hasContour && Number.isFinite(support.contourIndex) && support.contourIndex>=0) {
            selected=Scene.contourStep(scene,support.contourIndex,delta)
            if (!selected.qualified || !selected.grounded || !Scene.clearAt(scene,selected) || Scene.distance(here,selected)<=10*scene.scale)
                selected=Scene.contourStep(scene,support.contourIndex,-delta)
        } else {
            const horizontal=support.axis==="x",span=horizontal ? scene.hostWidth : scene.hostHeight
            const along=Scene.clamp((horizontal ? here.x : here.y)+delta,
                support.from,support.to-span)
            const isEdge=Scene.edges.includes(support.key)
            selected=Scene.annotate(scene,horizontal ? {x:along,y:here.y} : {x:here.x,y:along},
                support.edge,isEdge ? "edge" : "surface",support.key)
            if (isEdge && (!selected.grounded || !Scene.clearAt(scene,selected) || !Scene.clearSegment(scene,here,selected) || Scene.distance(here,selected)<=10*scene.scale)) {
                const revAlong=Scene.clamp((horizontal ? here.x : here.y)-delta,support.from,support.to-span)
                const rev=Scene.annotate(scene,horizontal ? {x:revAlong,y:here.y} : {x:here.x,y:revAlong},support.edge,"edge",support.key)
                if (rev.qualified && rev.grounded && Scene.clearAt(scene,rev) && Scene.clearSegment(scene,here,rev) && Scene.distance(here,rev)>10*scene.scale)
                    selected=rev
                else if (!selected.grounded || !Scene.clearAt(scene,selected))
                    selected=Scene.edgePoint(scene,support.edge,(along+span/2)/(horizontal ? scene.width : scene.height))
            }
        }
        if (selected.qualified && selected.grounded && Scene.clearAt(scene,selected) && Scene.distance(here,selected)>10*scene.scale) {
            const gait=random(), length=Scene.distance(here,selected)
            const movement=length>=35*scene.scale && gait<.18 ? "run"
                : length>=35*scene.scale && length<=180*scene.scale && gait<(actor?.character!=="octo" ? .68 : .35) ? "jump"
                : personality!=="calm" && gait<(personality==="energetic" ? .82 : .55) ? "run" : "walk"
            moveTo(selected,false,movement)
        }
    }
    function scheduleSurface(): void {
        surfaceDeadline.stop(); surfaceDue=false
        if (permitted && visitActive && requestedReveal>.99 && surfaceKey)
            surfaceDeadline.start()
    }
    function offerSurface(): void {
        if (!surfaceDue || !canExplore || traveling || directed) return
        surfaceDue=false
        const candidates=Scene.surfacePoints(scene)
        // One 62% offer after a stable surface has remained open. Hover/task
        // holds defer the offer until released, without a polling timer.
        if (candidates.length && random()<.62)
            moveTo(candidates[Math.min(candidates.length-1,Math.floor(random()*candidates.length))],false)
    }
    function recoverSupport(from = position()): bool {
        if (!Scene.valid(scene) || !Scene.clearAt(scene,from)) {hideImmediately();return false}
        const support=Scene.supportAt(scene,from,placement.support?.edge ?? placement.edge)
        if (support) {
            const isEdge=Scene.edges.includes(support.key)
            const expectedKind=isEdge ? "edge" : "surface"
            if (!traveling && !portalActive && !releasedFlight
                    && placement?.qualified && placement?.grounded
                    && placement.kind===expectedKind && placement.key===support.key
                    && placement.edge===support.edge
                    && Math.abs((placement.x ?? 0)-from.x)<.05
                    && Math.abs((placement.y ?? 0)-from.y)<.05
                    && Math.abs((placement.hostWidth ?? 0)-scene.hostWidth)<.05
                    && Math.abs((placement.hostHeight ?? 0)-scene.hostHeight)<.05
                    && placement.support?.key===support.key
                    && placement.support?.edge===support.edge
                    && Math.abs((placement.support?.coordinate ?? 0)-(support.coordinate ?? 0))<.05
                    && Math.abs((placement.support?.from ?? 0)-(support.from ?? 0))<.05
                    && Math.abs((placement.support?.to ?? 0)-(support.to ?? 0))<.05) {
                return true
            }
            clearMotion()
            const safe=Scene.annotate(scene,from,support.edge,expectedKind,support.key)
            placement=safe;destination=safe;targetX=safe.x;targetY=safe.y
            scheduleSurface()
            scheduleHomeReturn()
            return true
        }
        const floor=Scene.landingBelow(scene,from)
        if (floor.qualified && moveTo(floor,false,"fly")) return true
        const water=Scene.nearestWater(scene,from)
        if (water.qualified && moveTo(water.placement,false)) return true
        hideImmediately()
        return false
    }
    function reconcileScene(): void {
        if (!permitted || occluded || !Scene.valid(scene)) {hideImmediately();return}
        if (handoffActive) return
        if (portalActive) {
            if ((!portalHopped && !portalSourceValid()) || !portalDestinationValid() || !Scene.clearAt(scene,position())) {
                clearMotion();recoverSupport()
            }
            return
        }
        if (!visitActive && requestedReveal>0 && !retreating) {
            if (Date.now()>=hiddenUntil && !resumeFromOcclusion())
                appear()
            return
        }
        if (!visitActive || dragging) return
        let from=position()
        const sizeChanged=Number.isFinite(placement?.hostWidth)
            && (Math.abs(placement.hostWidth-scene.hostWidth)>.1 || Math.abs(placement.hostHeight-scene.hostHeight)>.1)
        const supportLost=!!placement?.support?.key && !Scene.edges.includes(placement.support.key)
            && !scene.surfaces.some(s=>s.key===placement.support.key)
        const ride=(grounded || sizeChanged || supportLost || !Scene.clearAt(scene,from))
            ? Scene.ride(scene,from,placement) : ({qualified:false})
        if (ride.qualified && (sizeChanged || Scene.distance(from,ride)>.25 || placement.support?.key!==ride.support?.key)) {
            const oldKey=placement.support?.key,delta=Scene.distance(from,ride)
            const closedToEdge=!Scene.edges.includes(oldKey) && Scene.edges.includes(ride.key)
            if (delta>.25) {directionX=ride.x-from.x;directionY=ride.y-from.y}
            clearMotion();placement=ride;destination=ride;targetX=ride.x;targetY=ride.y
            actor.carryTo(ride.x+(actor.scale-1)*actor.width/2,ride.y+(actor.scale-1)*actor.height/2)
            if (!sizeChanged && !closedToEdge && (oldKey!==ride.key || delta>6*scene.scale)) balance()
            return
        }
        if (!Scene.clearAt(scene,from)) {
            const edgeHorizontal=emergenceEdge==="top" || emergenceEdge==="bottom"
            const edgeFraction=(edgeHorizontal ? from.x+scene.hostWidth/2 : from.y+scene.hostHeight/2)
                / (edgeHorizontal ? scene.width : scene.height)
            let edgeSafe=scene.hasContour ? Scene.nearestClearContourPoint(scene,from,emergenceEdge) : ({qualified:false})
            if (!edgeSafe.qualified) edgeSafe=Scene.edgePoint(scene,emergenceEdge,edgeFraction)
            if (edgeSafe.qualified && Scene.clearAt(scene,edgeSafe)) {
                if (Scene.distance(from,edgeSafe)>.25) {directionX=edgeSafe.x-from.x;directionY=edgeSafe.y-from.y}
                clearMotion();placement=edgeSafe;destination=edgeSafe;targetX=edgeSafe.x;targetY=edgeSafe.y
                actor.carryTo(edgeSafe.x+(actor.scale-1)*actor.width/2,edgeSafe.y+(actor.scale-1)*actor.height/2);balance();return
            }
            // A crowded rim may no longer fit. Recover through a clear nearby
            // opening rather than hiding the actor behind a growing body.
            const points=Scene.surfacePoints(scene)
            points.sort((a,b)=>Scene.distance(from,a)-Scene.distance(from,b))
            if (points.length) {
                const safe=points[0];clearMotion();placement=safe;destination=safe;targetX=safe.x;targetY=safe.y
                actor.carryTo(safe.x+(actor.scale-1)*actor.width/2,safe.y+(actor.scale-1)*actor.height/2);balance();return
            }
            hideImmediately();return
        }
        if (traveling) {
            // A destination copied from the previous scene is not proof that
            // its support still exists. If the module/rim vanished, abort that
            // stale route before Aqua can arrive at an airborne coordinate.
            if (destination?.grounded === true
                    && !Scene.supportAt(scene,destination,destination.support?.edge ?? destination.edge)) {
                recoverSupport(from)
                return
            }
            for (let i=Math.max(0,waypoint-1);i<waypoints.length;i++) {
                if (!Scene.clearSegment(scene,from,waypoints[i])
                        || (arc>0 && !Scene.clearArc(scene,from,waypoints[i],arc,normalX,normalY))) {
                    recoverSupport(from)
                    return
                }
                from=waypoints[i]
            }
        } else {
            // A popup/module can disappear while Aqua is standing on its rim.
            // Coordinates can remain collision-free even though the support is
            // gone, so recovery must prove a live support rather than merely
            // re-annotating the old position.
            recoverSupport(from)
        }
    }
    function beginDrag(): void {
        if (!permitted || !actor || !actor.inputReady || retreating) return
        pause(true);dragging=true;surfaceDeadline.stop()
        const start=position()
        shakeReversals=0;dragVelocityX=0;dragVelocityY=0;dragSampleTime=Date.now()
        dragSamples=[{x:start.x,y:start.y,t:dragSampleTime}]
    }
    function dragTo(x,y): void {
        if (!dragging || !permitted) return
        const previous=position(), point=Scene.drop(scene,x,y,previous)
        if (!point.qualified) return
        const now=Date.now(),dt=Math.max(8,now-dragSampleTime)
        const stepVx=(point.x-previous.x)/dt,stepVy=(point.y-previous.y)/dt
        if (Math.hypot(stepVx,stepVy)>.65*scene.scale && Math.hypot(dragVelocityX,dragVelocityY)>.65*scene.scale
                && stepVx*dragVelocityX+stepVy*dragVelocityY<0) {
            shakeReversals++;if (shakeReversals>=4) setReaction("angry",6000)
        }
        if (dragSamples.length>0 && dragSampleTime<dragSamples[dragSamples.length-1].t)
            dragSamples[dragSamples.length-1].t=dragSampleTime
        const samples=dragSamples.filter(s=>now-s.t<=95)
        if (!samples.length && dragSamples.length) samples.push(dragSamples[dragSamples.length-1])
        let vx=stepVx,vy=stepVy
        if (samples.length) {
            let ref=samples[0]
            for (let i=samples.length-1;i>=0;i--) {
                if (now-samples[i].t>=16) {ref=samples[i];break}
            }
            const winDt=Math.max(8,now-ref.t)
            const winVx=(point.x-ref.x)/winDt,winVy=(point.y-ref.y)/winDt
            if (Math.hypot(winVx,winVy)>=Math.hypot(stepVx,stepVy)*.85 || now-dragSampleTime<8) {
                vx=winVx;vy=winVy
            }
        }
        samples.push({x:point.x,y:point.y,t:now})
        dragSamples=samples
        dragVelocityX=vx;dragVelocityY=vy;dragSampleTime=now
        directionX=point.x-previous.x; directionY=point.y-previous.y
        placement=point; destination=point; targetX=point.x; targetY=point.y
    }
    function throwCandidates(here, vx, vy): var {
        const speed=Math.hypot(vx,vy),scale=scene?.scale ?? 1
        if (!Scene.valid(scene) || speed<throwThreshold) return []
        const normSpeed=speed/scale
        const flightMs=Math.max(180,Math.min(1100,140+normSpeed*280))
        const minX=Math.max(2,scene.insets.left)
        const maxX=Math.min(scene.width-scene.hostWidth-2,scene.width-scene.insets.right-scene.hostWidth)
        const minY=Math.max(2,scene.insets.top)
        const maxY=Math.min(scene.height-scene.hostHeight-2,scene.height-scene.insets.bottom-scene.hostHeight)
        const tx=vx>.001 ? (maxX-here.x)/vx : vx<-.001 ? (minX-here.x)/vx : Infinity
        const ty=vy>.001 ? (maxY-here.y)/vy : vy<-.001 ? (minY-here.y)/vy : Infinity
        const tWall=Math.max(0,Math.min(tx,ty))
        const reachesWall=Number.isFinite(tWall) && tWall<=flightMs*1.15
        const tDirect=reachesWall && tWall>1 ? tWall : flightMs
        const predicted={
            x:Scene.clamp(here.x+vx*tDirect,2,scene.width-scene.hostWidth-2),
            y:Scene.clamp(here.y+vy*tDirect,2,scene.height-scene.hostHeight-2)
        }
        const ballisticFloorX=Scene.clamp(here.x+vx*flightMs*1.35,2,scene.width-scene.hostWidth-2)
        const candidates=Scene.surfacePoints(scene).slice()
        const centerX=predicted.x+scene.hostWidth/2,centerY=predicted.y+scene.hostHeight/2
        for (const edge of ["top","right","bottom","left"]) {
            const fraction=(edge==="top" || edge==="bottom")
                ? centerX/scene.width : centerY/scene.height
            const point=Scene.edgePoint(scene,edge,fraction)
            if (point.qualified) candidates.push(point)
        }
        if (Number.isFinite(tWall) && tWall>1) {
            const wallX=Scene.clamp(here.x+vx*tWall,2,scene.width-scene.hostWidth-2)+scene.hostWidth/2
            const wallY=Scene.clamp(here.y+vy*tWall,2,scene.height-scene.hostHeight-2)+scene.hostHeight/2
            for (const edge of ["top","right","bottom","left"]) {
                const fraction=(edge==="top" || edge==="bottom")
                    ? wallX/scene.width : wallY/scene.height
                const point=Scene.edgePoint(scene,edge,fraction)
                if (point.qualified) candidates.push(point)
            }
        }
        const floorHop=Scene.edgePoint(scene,"bottom",(ballisticFloorX+scene.hostWidth/2)/scene.width)
        if (floorHop.qualified) candidates.push(floorHop)
        const scored=[]
        for (const candidate of candidates) {
            if (!candidate?.qualified || !candidate.grounded) continue
            const dx=candidate.x-here.x,dy=candidate.y-here.y,distance=Math.hypot(dx,dy)
            if (distance<40*scale || !Scene.clearSegment(scene,here,candidate)) continue
            const alignment=(dx*vx+dy*vy)/(Math.max(.001,distance)*speed)
            if (alignment<-.12) continue
            let predictionError=Math.hypot(candidate.x-predicted.x,candidate.y-predicted.y)
            if (!reachesWall && Math.abs(vx)>=.22*speed && candidate.edge==="bottom") {
                const floorError=Math.hypot(candidate.x-ballisticFloorX,candidate.y-maxY)
                if (floorError<predictionError) predictionError=floorError
            }
            scored.push({candidate:candidate,score:predictionError+(1-alignment)*150*scale})
        }
        scored.sort((a,b)=>a.score-b.score)
        return scored.map(item=>item.candidate)
    }
    function throwTo(candidate, speed): bool {
        const here=position(),scale=scene?.scale ?? 1
        if (!candidate?.qualified || !candidate.grounded || !Scene.clearSegment(scene,here,candidate)) return false
        let desiredArc=Math.max(24*scale,Math.min(160*scale,18*scale+(speed/scale)*52+Math.abs(candidate.x-here.x)*.12))
        while (desiredArc>8*scale && !Scene.clearArc(scene,here,candidate,desiredArc,0,-1))
            desiredArc*=.62
        if (desiredArc<=8*scale || !Scene.clearArc(scene,here,candidate,desiredArc,0,-1))
            desiredArc=0
        disturb("depart",Scene.annotate(scene,here,placement.edge,placement.kind,placement.key))
        clearMotion()
        destination=candidate;mode="fly";retreating=false
        waypoints=[candidate];waypoint=0
        throwSpeed=speed;throwArc=desiredArc;releasedFlight=true;traveling=true
        setReaction("surprised",Math.max(900,Math.min(1800,Math.round(700+speed*450))))
        advance()
        return true
    }
    function tryThrow(vx, vy): bool {
        if (!motionEnabled || requestedReveal<=0) return false
        const speed=Math.hypot(vx,vy)
        if (speed<throwThreshold) return false
        const here=position()
        const candidates=throwCandidates(here,vx,vy)
        for (const candidate of candidates)
            if (throwTo(candidate,speed)) return true
        return false
    }
    function endDrag(): void {
        if (!dragging) return
        const stale=Date.now()-dragSampleTime>140
        const releaseVx=stale ? 0 : dragVelocityX,releaseVy=stale ? 0 : dragVelocityY
        dragSamples=[]
        dragging=false
        if (shakeReversals>=4) {annoyance=5;setReaction("angry",6000);hideForAWhile();return}
        if (!permitted || !Scene.valid(scene)) {hideImmediately();return}
        if (requestedReveal<=0) {retreat();return}
        if (tryThrow(releaseVx,releaseVy)) return
        reconcileScene()
        if (traveling) return
        if (motionEnabled && !placement.grounded) {
            const here=position(), floor=Scene.landingBelow(scene,here)
            releasedFlight=false
            if (floor.qualified && moveTo(floor,false,"fly")) return
            const water=Scene.nearestWater(scene,here)
            if (water.qualified && moveTo(water.placement,false)) return
            hideImmediately()
            return
        }
        disturb("land",placement)
        if (placement.grounded && motionEnabled) actor.perform("land")
        scheduleSurface()
        scheduleHomeReturn()
    }
    function hideForAWhile(): void {
        hiddenUntil=Date.now()+20000+Math.floor(random()*15000)
        recoveryDeadline.interval=Math.round(hiddenUntil-Date.now());recoveryDeadline.restart()
        retreat()
    }
    function evade(): void {
        if (!permitted || dragging || !motionEnabled || actor.presentation<.99) return
        let here=position(),dx=here.x+scene.hostWidth/2-avoidX,dy=here.y+scene.hostHeight/2-avoidY
        if (Math.hypot(dx,dy)<1) {dx=random()<.5 ? -1 : 1;dy=-.4}
        const length=Math.hypot(dx,dy),support=Scene.supportAt(scene,here,emergenceEdge)
        let next
        if (support) {
            if (scene.hasContour && Number.isFinite(support.contourIndex) && support.contourIndex>=0 && scene.contour?.length>=2) {
                const cLen=scene.contour.length, cIdx=support.contourIndex, nextIdx=(cIdx+1)%cLen
                const tx=scene.contour[nextIdx].bx-scene.contour[cIdx].bx
                const ty=scene.contour[nextIdx].by-scene.contour[cIdx].by
                const dirSign=(tx*dx+ty*dy)>=0 ? 1 : -1
                const stepLen=dirSign*100*scene.scale
                next=Scene.contourStep(scene,cIdx,stepLen)
                if (!next.qualified || !next.grounded || !Scene.clearAt(scene,next) || Scene.distance(here,next)<=10*scene.scale)
                    next=Scene.contourStep(scene,cIdx,-stepLen)
            } else {
                const horizontal=support.axis==="x",axis=horizontal ? dx : dy,extent=horizontal ? scene.hostWidth : scene.hostHeight
                const step=(axis>=0 ? 1 : -1)*100*scene.scale
                const along=Scene.clamp((horizontal ? here.x : here.y)+step,support.from,support.to-extent)
                const isEdge=Scene.edges.includes(support.key)
                next=Scene.annotate(scene,horizontal ? {x:along,y:here.y} : {x:here.x,y:along},support.edge,isEdge ? "edge" : "surface",support.key)
                if (isEdge && (!next.grounded || !Scene.clearAt(scene,next) || !Scene.clearSegment(scene,here,next) || Scene.distance(here,next)<=10*scene.scale)) {
                    const revAlong=Scene.clamp((horizontal ? here.x : here.y)-step,support.from,support.to-extent)
                    const rev=Scene.annotate(scene,horizontal ? {x:revAlong,y:here.y} : {x:here.x,y:revAlong},support.edge,"edge",support.key)
                    if (rev.qualified && rev.grounded && Scene.clearAt(scene,rev) && Scene.clearSegment(scene,here,rev) && Scene.distance(here,rev)>10*scene.scale)
                        next=rev
                    else if (!next.grounded || !Scene.clearAt(scene,next))
                        next=Scene.edgePoint(scene,support.edge,(along+extent/2)/(horizontal ? scene.width : scene.height))
                }
            }
        } else {
            const floor=Scene.landingBelow(scene,here)
            if (floor.qualified) next=floor
            else {
                const water=Scene.nearestWater(scene,here)
                next=water.qualified ? water.placement : null
            }
        }
        releasedFlight=false
        if (next?.qualified && next.grounded && Scene.clearAt(scene,next)) moveTo(next,false,support ? "run" : "")
    }
    function nearbyClick(x,y): void {
        if (!permitted || !visitActive || dragging || !motionEnabled || actor.presentation<.2) return
        const here=position()
        if (Math.hypot(x-here.x-scene.hostWidth/2,y-here.y-scene.hostHeight/2)>nearbyClickRadius) return
        const now=Date.now();annoyance=now-lastDisturbance<4500 ? annoyance+1 : 1;lastDisturbance=now
        avoidX=x;avoidY=y;pause(true)
        if (annoyance>=5) {
            setReaction("angry",7000)
            if (random()<.55) hideForAWhile();else evade()
        } else if (random()<.5) {
            setReaction("surprised",2400);pendingReaction="evade";actor.perform("startle")
        } else {setReaction("happy",2500);pendingReaction="";actor.perform("delight")}
    }
    function offerPointerNotice(): void {
        pointerNotice.stop()
        if (!pointerNearby || Date.now()<nextPointerNotice) return
        pointerNotice.interval=650+Math.floor(random()*500)
        pointerNotice.start()
    }
    function noticePointer(): void {
        if (!pointerNearby || Date.now()<nextPointerNotice) return
        const now=Date.now(), chance=personality==="energetic" ? .6 : personality==="calm" ? .25 : .4
        nextPointerNotice=now+12000+Math.floor(random()*13000)
        // Passive presence is not a disturbance. Only explicit clicks and
        // shaking accumulate annoyance; movement/working keep their attention.
        if (random()>chance) return
        const response=random()
        if (response<.22) {
            avoidX=pointerX;avoidY=pointerY;pendingReaction="evade"
            setReaction("surprised",2400);actor.perform("startle")
        } else if (response<.65) {setReaction("happy",2400);actor.perform("wave")}
        else {setReaction("thinking",2200);actor.perform("inspect")}
    }
    function balance(): void {
        if (!motionEnabled || dragging || !visitActive || Date.now()-lastBalance<5500 || !grounded) return
        lastBalance=Date.now()
        if (actor.perform("balance")) setReaction("surprised",1800)
    }
    function gestureFinished(action): void {
        if (action==="startle" && pendingReaction==="evade") {pendingReaction="";evade()}
    }
    onPermittedChanged: {
        if (!permitted) {
            captureOcclusionState()
            hideImmediately()
        } else if (initialized && !occluded) {
            synchronize()
        }
    }
    onOccludedChanged: {
        if (occluded) {
            captureOcclusionState()
            if (suspendedByOcclusion) sawOcclusionWhileHidden=true
            if (visitActive || retreating || portalActive || traveling)
                hideImmediately()
        } else if (initialized && permitted) {
            synchronize()
        }
    }
    onHomeEdgeChanged: if (initialized && permitted && !occluded && visitActive && !retreating && requestedReveal>0 && Date.now()>=hiddenUntil && homeEdge.length>0) Qt.callLater(() => root.returnToHome(true))
    onHomeAlongChanged: if (initialized && permitted && !occluded && visitActive && !retreating && requestedReveal>0 && Date.now()>=hiddenUntil && homeEdge.length>0) Qt.callLater(() => root.returnToHome(true))
    onPointerNearbyChanged: root.offerPointerNotice()
    onRequestedRevealChanged: Qt.callLater(root.synchronize)
    onSceneChanged: Qt.callLater(root.reconcileScene)
    onSurfaceKeyChanged: root.scheduleSurface()
    onTravelIdChanged: Qt.callLater(root.explore)
    onInteractionHeldChanged: if (interactionHeld) Qt.callLater(root.pause)
    onCanExploreChanged: {
        if (!canExplore && !retreating) Qt.callLater(root.pause)
        else Qt.callLater(root.offerSurface)
    }
    onMotionEnabledChanged: if (!motionEnabled) {
        peekDeadline.stop();peekIntro=false
        if (requestedReveal>.99 && visitActive) renderedReveal=1
        if (retreating) hideImmediately()
        else pause(true)
    }
    Component.onCompleted: {initialized=true;Qt.callLater(root.synchronize)}
    Connections {
        target: root.actor
        function onGestureCompleted(action): void {root.gestureFinished(action)}
        function onPresentationChanged(): void {
            if (root.visitActive && !root.peekIntro && !root.peekOnly && root.renderedReveal>.99
                    && root.actor.presentation>.99 && !root.fullyPresentSince) {
                root.fullyPresentSince=Date.now()
                Qt.callLater(root.synchronize)
            }
        }
    }
    Timer {id:fullVisitDeadline;objectName:"aquaFullVisitDeadline";repeat:false;onTriggered:root.synchronize()}
    NumberAnimation {
        id:portalTween;target:root;property:"portalProgress";from:0;to:1;duration:1800
        onFinished:{
            root.portalStep()
            if (!root.portalActive) return
            root.portalActive=false;root.mode="walk";root.traveling=true;root.waypoints=[];root.waypoint=0
            root.advance()
        }
    }
    Timer {id:reactionDeadline;interval:3000;onTriggered:if(root.actor) root.actor.reactionExpression=""}
    Timer {
        id: pointerNotice
        objectName: "aquaPointerNoticeDeadline"
        repeat: false
        onTriggered: root.noticePointer()
    }
    Timer {id:recoveryDeadline;interval:20000;onTriggered:{
        root.hiddenUntil=0
        if(root.permitted && !root.occluded && root.requestedReveal>0 && !root.visitActive) {
            if(!root.resumeFromOcclusion())
                root.appear()
        }
    }}
    Connections {
        target: root.actor
        enabled: root.peekIntro && !peekDeadline.running
        function onPresentationChanged(): void {
            if (root.actor && Math.abs(root.actor.presentation-root.peekReveal)<.005) peekDeadline.start()
        }
    }
    Timer {
        id: peekDeadline
        objectName: "aquaPeekDeadline"
        repeat: false
        onTriggered: {
            if (root.permitted && root.visitActive && root.requestedReveal>.99) {
                root.peekIntro=false
                if (root.peekOnly) {
                    root.disturb("dive",root.placement);root.visitActive=false;root.renderedReveal=0
                    root.hiddenUntil=Date.now()+2200;recoveryDeadline.interval=2200;recoveryDeadline.restart()
                } else {root.renderedReveal=1;root.disturb("emerge",root.placement)}
            }
        }
    }
    Timer {
        id: surfaceDeadline
        objectName: "aquaSurfaceDeadline"
        repeat: false
        interval: 12000
        onTriggered: {root.surfaceDue=true;root.offerSurface()}
    }
    Timer {
        id: homeReturnDeadline
        objectName: "aquaHomeReturnDeadline"
        repeat: false
        interval: 14000
        onTriggered: root.returnToHome(false)
    }
}
