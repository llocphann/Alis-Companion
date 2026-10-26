import QtQuick
import Quickshell
import Quickshell.Wayland
import qs
import qs.services
import qs.modules.common
import qs.modules.abyss.looks
import "services"
import "modules/abyss/companion"
import "modules/abyss/companion/AquaHostPolicy.js" as AquaHostPolicy
import "modules/abyss/companion/AquaScene.js" as AquaScene
import "modules/abyss/companion/AquaPreferences.js" as AquaPreferences

Item {
    id: root
    required property var adapter
    readonly property var session: adapter.session
    readonly property var companionBridge: session.bridge
    readonly property var host: adapter.host
    readonly property var liquid: adapter.hostLiquid
    readonly property var field: adapter.hostField
    readonly property var bar: adapter.hostBar
    readonly property var leftPanel: adapter.hostLeftPanel
    readonly property var rightPanel: adapter.hostRightPanel
    readonly property var corners: adapter.hostCorners
    readonly property var utility: adapter.hostUtility
    readonly property var barHover: adapter.hostBarHover
    readonly property var revealHover: adapter.hostRevealHover
    Item { id:emptyInput;width:0;height:0 }
    // Ordinary Abyss surfaces are Aqua's habitat. Actual modal/security
    // owners still preempt it; moving bodies remain scene obstacles.
    readonly property bool companionOccluded: host.editorOpen
        || liquid.activeDialog || GlobalStates.settingsNativeDialogOpen
        || PolkitService.active || GlobalStates.regionSelectorOpen || host.overviewDragging
    readonly property var companionScene: AquaScene.fromParticipants({
        width:host.width,height:host.height,
        hostWidth:112*session.companionScale,hostHeight:98*session.companionScale,
        scale:session.companionScale,insets:host.nativeInsets,rimRadius:AbyssStyle.neckRadius},
        liquid.participants,bar.visible ? bar.layoutRecords.map(record=>({
            edge:record.edge,along:record.along,span:record.span})) : [])
    readonly property bool companionPermission: AquaHostPolicy.hostActive(
        session.companionSessionVisible,companionBridge.ready,
        session.companionTargetOutput,host.outputName,host.presented,field.ready)
        && !root.companionOccluded
        && liquid.records.length<=field.capacity
    readonly property bool companionHostActive: root.companionPermission && companionPresence.qualified
    readonly property bool ambientBlocked: !root.companionHostActive || root.companionOccluded
        || companionPresence.dragging || companionPresence.traveling || companionPresence.portalActive
        || companionCuriosity.busy || talkCloud.controlsVisible
    readonly property bool companionPerformanceHeld: session.companionTargetOutput===host.outputName
        && (session.cowork?.active===true || (session.companionAmbientProp ?? "").length>0 || AquaPocket.opened)
    onCompanionPerformanceHeldChanged: if (companionPerformanceHeld) Qt.callLater(() => { if (root.companionPerformanceHeld) root.ensureCompanionPresent() })
    readonly property var directorContext: ({enabled:root.companionHostActive,
        dragging:companionPresence.dragging,manualTravel:companionPresence.traveling,
        portalTravel:companionPresence.portalActive,
        grounded:companionPresence.grounded && !companion.flying && !companion.moving && !companion.relocating && companion.presentation>0.99,
        controlsVisible:talkCloud.controlsVisible,
        modalOpen:root.companionOccluded || companionCuriosity.busy || AquaPocket.opened || AquaPocket.busy})
    onDirectorContextChanged: if (session && session.companionTargetOutput===host.outputName) {
        session.evaluateContext()
        Qt.callLater(session.evaluateContext)
    }
    function ensureCompanionPresent(): void {
        if(!root.companionPermission) return
        companionTurns.cancel()
        companionCuriosity.interrupt()
        companionPresence.hiddenUntil=0
        if(!companionPresence.visitActive || companionPresence.retreating)companionPresence.appear()
        companionPresence.peekOnly=false;companionPresence.peekIntro=false;companionPresence.renderedReveal=1
        if(companion.presentation<.99 && !companion.leaving)companion.present(1)
    }
    function requestCompanionChat(): void {
        if(!root.companionPermission) return
        root.ensureCompanionPresent()
        AquaMind.openChat()
    }
    function requestCompanionPocket(): void {
        if(!root.companionPermission || !AquaPocket.permitted) return
        root.ensureCompanionPresent()
        AquaMind.dismiss()
        AquaPocket.show()
    }
    function resetCompanionCast(): void {
        companionTurns.cancel()
        companionPresence.hideImmediately()
        Qt.callLater(companionPresence.synchronize)
    }
    function status() {
        const scene=root.companionScene
        return {output:outputName,presented:host.presented,
            occluded:root.companionOccluded,permission:root.companionPermission,
            active:root.companionHostActive,
            field:{ready:field.ready,framePresented:field.framePresented,
                diagnostic:String(field.diagnostic).slice(0,512),
                records:liquid.records.length,capacity:field.capacity},
            scene:{valid:AquaScene.valid(scene),width:scene.width,height:scene.height,
                hostWidth:scene.hostWidth,hostHeight:scene.hostHeight,insets:scene.insets,
                records:scene.records?.slice(0,256),blockers:scene.blockers?.slice(0,128),
                surfaces:scene.surfaces?.slice(0,40)},
            presence:{qualified:companionPresence.qualified,
                portalActive:companionPresence.portalActive,portalProgress:companionPresence.portalProgress,
                visitActive:companionPresence.visitActive,
                traveling:companionPresence.traveling,mode:companionPresence.mode,
                dragging:companionPresence.dragging,arc:companionPresence.arc,
                requestedReveal:companionPresence.requestedReveal,
                renderedReveal:companionPresence.renderedReveal,
                placement:companionPresence.placement},
            curiosity:{enabled:session.companionPreferences.exploreFeatures,
                stage:companionCuriosity.stage,owned:companionCuriosity.owned,
                feature:companionCuriosity.feature?.kind ?? ""},
            turns:{enabled:session.alternatingCompanions,active:companionTurns.active,paired:companionTurns.paired,
                incoming:companionTurns.active ? companionTurns.incoming : ""},
            actor:{character:companion.character,visible:companion.visible,inputReady:companion.inputReady,
                moving:companion.moving,walking:companion.walking,rolling:companion.rolling,flying:companion.flying,
                presentation:companion.presentation,opacity:companion.opacity,ambientProp:companion.ambientProp,
                coworkPropVisible:companion.coworkPropVisible,
                x:companion.x,y:companion.y}}
    }
    readonly property bool companionHoverHeld: root.companionHostActive
        && companion.interactive && (companion.hovered || cloudActions.hovered || cloudActions.visible || companion.dragging || pocketDrop.containsDrag || talkCloud.controlsVisible || AquaMind.conversationOpen || root.companionPerformanceHeld)
    onCompanionHoverHeldChanged: if (session.companionTargetOutput===host.outputName && companionBridge.ready)
        companionBridge.sendEvent("hover",root.companionHoverHeld)
    AquaPresence {
        id: companionPresence
        scene: root.companionScene
        actor: companion
        permitted: root.companionPermission
        requestedReveal: companionBridge.visibility==="present" ? 1
            : companionBridge.visibility==="peeking" ? .46 : 0
        motionEnabled: session.companionPreferences.animationsEnabled && AbyssStyle.motionEnabled
        interactionHeld: companionBridge.activity!=="idle" || pocketDrop.containsDrag || AquaPocket.busy || talkCloud.controlsVisible || AquaMind.conversationOpen || root.companionPerformanceHeld
        pointerFresh: root.companionPointerFresh
        pointerX: root.companionPointerX
        pointerY: root.companionPointerY
        pointerReactionsEnabled: session.companionInteractive && !talkCloud.controlsVisible
            && !AquaMind.conversationOpen
        personality: session.companionPreferences.personality
        travelId: companionBridge.travelId
        travelFraction: companionBridge.travelTarget
        onStopRequested: companion.stopTravel()
        onResetRequested: (px,py,sourceEdge)=>companion.resetTo(
            px+(session.companionScale-1)*companion.implicitWidth/2,
            py+(session.companionScale-1)*companion.implicitHeight/2,sourceEdge)
    }
    AquaAbyssLink {
        id: companionWater
        waveFunctions: adapter.waveFunctions
        presence: companionPresence
        actor: companion
        controller: liquid
        allowed: root.companionHostActive
    }
    CompanionTurns {
        id:companionTurns
        presence:companionPresence;actor:companion
        allowed:root.companionHostActive && companionBridge.ready
        alternating:session.alternatingCompanions
        interactionHeld:companionPresence.interactionHeld || companionCuriosity.busy
        onStarting:companionCuriosity.interrupt()
        onCharacterChosen:character=>session.companionCharacter=character
    }
    CompanionChallenger {parent:adapter.overlayParent;turns:companionTurns;actor:companion;z:24}
    function companionFeaturesIdle(): bool {
        return !companionTurns.active && root.companionPermission && !root.companionOccluded
            && !liquid.popupsOpen && !GlobalStates.abyssPopupKind
            && !GlobalStates.sidebarLeftOpen && !GlobalStates.sidebarRightOpen
            && !GlobalStates.settingsOverlayOpen && !GlobalStates.overviewOpen
            && !GlobalStates.clipboardOpen && !GlobalStates.dashboardOpen
            && !GlobalStates.controlPanelOpen && !GlobalStates.notificationCenterOpen
            && !GlobalStates.widgetEditMode && !utility.open && !host.dockHovered
            && !barHover.hovered && !revealHover.hovered
    }
    readonly property var companionFeatures: {
        const result=[]
        if (!session.companionEnabled || !session.companionPreferences.exploreFeatures) return result
        const panels=Config.options?.enabledPanels ?? []
        const gestures={clock:"inspect",resources:"inspect",battery:"inspect",
            weather:"inspect",media:"wave",utilButtons:"press"}
        if (bar.visible && panels.includes("abyssPopup")) {
            for (const record of bar.layoutRecords) {
                const module=bar.itemForId(record.id)
                const kind=module?.kind
                const mature=module?.companionPopup ?? null
                if (record.span>0 && gestures[kind] && (mature || kind==="utilButtons")) result.push({
                    kind:kind==="utilButtons" ? "utilities" : kind,
                    popup:mature,key:kind==="utilButtons" ? "popup" : "",edge:record.edge,
                    along:record.along+record.span/2,openGesture:"press",gesture:gestures[kind]})
            }
        }
        const sidebarOutput=GlobalStates.resolveOutputName(host.outputName,Config.options?.sidebar?.screenList ?? [])
        for (const side of ["left","right"]) {
            const key=side+"Panel", body=side==="left" ? leftPanel : rightPanel
            if (panels.includes(side==="left" ? "abyssSidebarLeft" : "abyssSidebarRight")
                    && sidebarOutput===host.outputName && !body.open)
                result.push({kind:key,key:key,edge:body.edge,along:body.along+body.span/2,
                    openGesture:"reach",gesture:side==="left" ? "inspect" : "press"})
        }
        for (const [kind,available,mature,along] of [
                ["quickNotes",corners.notesAvailable,corners.notesPopup,host.nativeInsets.left+80],
                ["notificationCenter",corners.centerAvailable,corners.centerPopup,host.width-host.nativeInsets.right-80]]) {
            if (available && !mature.presentationActive)
                result.push({kind:kind,popup:mature,key:"",edge:"bottom",along:along,
                    openGesture:"reach",gesture:kind==="quickNotes" ? "inspect" : "wave"})
        }
        return result
    }
    function companionSurfaceKey(feature): string {
        if (!feature?.popup) return feature?.key ?? ""
        const slot=liquid._popupSlot(feature.popup)
        return slot>=0 ? "styledPopup"+slot : ""
    }
    function openCompanionFeature(feature): bool {
        if (!companionFeaturesIdle() || !feature) return false
        if (["clock","resources","battery","weather","media","quickNotes","notificationCenter"].includes(feature.kind)) {
            // Borrow the module/corner's mature StyledPopup. Its hover
            // path and Aqua share one slot, one content and one host.
            return !!feature.popup && feature.popup.acquireCompanion(host)
        }
        if (feature.kind==="leftPanel") GlobalStates.openSidebarLeft(host.outputName,false)
        else if (feature.kind==="rightPanel") GlobalStates.openSidebarRight(host.outputName,false)
        else if (feature.kind==="utilities") {
            GlobalStates.abyssPopupTargetOutput=host.outputName
            GlobalStates.abyssPopupEdge=feature.edge
            GlobalStates.abyssPopupAlong=feature.along
            GlobalStates.abyssPopupKind="utilities"
        } else return false
        return ownsCompanionFeature(feature)
    }
    function ownsCompanionFeature(feature): bool {
        if (!feature) return false
        if (feature.popup) return feature.popup.companionLease===host
        if (feature.kind==="leftPanel") return GlobalStates.sidebarLeftOpen
            && GlobalStates.sidebarLeftTargetOutput===host.outputName
        if (feature.kind==="rightPanel") return GlobalStates.sidebarRightOpen
            && GlobalStates.sidebarRightTargetOutput===host.outputName
        return feature.kind==="utilities" && GlobalStates.abyssPopupTargetOutput===host.outputName
            && GlobalStates.abyssPopupKind==="utilities"
    }
    function closeCompanionFeature(feature): void {
        if (!ownsCompanionFeature(feature)) return
        if (feature.popup) feature.popup.releaseCompanion(host)
        else if (feature.kind==="leftPanel") GlobalStates.closeSidebarLeft()
        else if (feature.kind==="rightPanel") GlobalStates.closeSidebarRight()
        else if (feature.kind==="utilities") host.closeGenericPopup("utilities")
    }
    function releaseCompanionFeature(feature): void {
        if (feature?.popup) feature.popup.releaseCompanion(host)
        else if (ownsCompanionFeature(feature)) {
            // A sidebar visited by Aqua becomes an ordinary transient
            // hover surface after a real user hand-off, not a sticky IPC open.
            if (feature.kind==="leftPanel") GlobalStates.sidebarLeftTransient=true
            else if (feature.kind==="rightPanel") GlobalStates.sidebarRightTransient=true
        }
    }
    readonly property string companionFeatureState: [GlobalStates.sidebarLeftOpen,
        GlobalStates.sidebarLeftTargetOutput,GlobalStates.sidebarRightOpen,GlobalStates.sidebarRightTargetOutput,
        GlobalStates.abyssPopupKind,GlobalStates.abyssPopupTargetOutput].join("|")
    onCompanionFeatureStateChanged: if (companionCuriosity) companionCuriosity.checkOwnership()
    Connections {
        target: companionCuriosity.feature?.popup ?? null
        function onCompanionLeaseChanged(): void {companionCuriosity.checkOwnership()}
    }
    AquaCuriosity {
        id: companionCuriosity
        presence: companionPresence
        actor: companion
        adapter: root
        features: root.companionFeatures
        allowed: root.companionHostActive && session.companionPreferences.exploreFeatures
            && session.companionPreferences.animationsEnabled && AbyssStyle.motionEnabled && !root.companionPerformanceHeld
        idle: companionBridge.activity==="idle" && companionBridge.visibility==="present"
        eventId: companionBridge.travelId
    }
    property real companionPointerX: 0
    property real companionPointerY: 0
    property bool companionPointerFresh: false
    Item {
        anchors.fill: parent
        HoverHandler {
            id: companionPointer
            objectName: "aquaRimPointerObserver"
            // Observe from the existing host ancestor. A later full-output
            // sibling steals Qt hover from the bar's MouseAreas even when
            // this handler is non-blocking. Keep the finite native mask.
            parent: adapter.overlayParent
            target: null
            blocking: false
            enabled: root.companionHostActive && (session.companionInteractive || companionCuriosity.busy)
            onPointChanged: if (hovered) {
                root.companionPointerX=point.scenePosition.x
                root.companionPointerY=point.scenePosition.y
                root.companionPointerFresh=true
                companionPointerExpiry.restart()
                companionCuriosity.pointerMoved(root.companionPointerX,root.companionPointerY)
            }
            onHoveredChanged: if (!hovered) companionPointerExpiry.restart()
        }
        PointHandler {
            enabled: root.companionHostActive && session.companionInteractive
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            onActiveChanged: if (active) {
                if (companionCuriosity.containsPoint(point.scenePosition.x,point.scenePosition.y))
                    companionCuriosity.yieldToUser()
                else companionCuriosity.interrupt()
                if (!talkCloud.containsScenePoint(point.scenePosition)) {
                    if (AquaPocket.opened && !GlobalStates.settingsNativeDialogOpen)
                        AquaPocket.close()
                    companionPresence.nearbyClick(point.scenePosition.x,point.scenePosition.y)
                }
            }
        }
    }
    Timer {
        id: companionPointerExpiry
        interval: 1400; repeat: false
        onTriggered: root.companionPointerFresh=false
    }
    AquaPortals {parent:adapter.overlayParent;presence:companionPresence;z:23}
    Loader {
        id:waterImmersion
        parent:adapter.overlayParent
        active:root.companionHostActive && field.ready && companion.visible && companion.leaving
            && ["sink","pulled"].includes(companion.hideClip) && companion.presentation<.999
        z:24
        x:companionPresence.position().x-48*session.companionScale
        y:companionPresence.position().y-100*session.companionScale
        width:208*session.companionScale;height:298*session.companionScale
        sourceComponent:field.immersionComponent
        Binding {target:waterImmersion.item;property:"bodyItem";value:companion;when:waterImmersion.active && !!waterImmersion.item}
        Binding {target:waterImmersion.item;property:"renderRect";value:Qt.vector4d(waterImmersion.x,waterImmersion.y,waterImmersion.width,waterImmersion.height);when:waterImmersion.active && !!waterImmersion.item}
        Binding {target:waterImmersion.item;property:"renderScale";value:host.modelData?.devicePixelRatio ?? 1;when:waterImmersion.active && !!waterImmersion.item}
    }
    AbyssCompanion {
        id: companion
        character:session.companionCharacter
        z: 24
        opacity: companionBridge.ready ? 1 : 0
        edge: companionPresence.emergenceEdge
        scale: session.companionScale
        interactive: session.companionInteractive && root.companionHostActive && !companionPresence.retreating
        // Drag is allowed, but release never leaves Aqua parked in open
        // space. A fast release becomes a bounded throw to a verified
        // grounded destination; a slow release settles back to support.
        dragEnabled: interactive
        motionEnabled: session.companionPreferences.animationsEnabled && AbyssStyle.motionEnabled
        effectsEnabled: session.companionPreferences.effectsEnabled && Appearance.effectsEnabled
            && AbyssStyle.quality!=="performance"
        motionScale: AquaPreferences.motionScale(session.companionPreferences.personality)
        renderQuality: AbyssRenderPolicy.aquaQuality
        translucency: session.companionPreferences.translucency
        travelEnabled: companionPresence.traveling && !companionPresence.portalActive
        portalReveal:companionPresence.portalReveal
        travelMode: companionPresence.mode
        surfaceSupported: companionPresence.grounded
        travelDuration: companionPresence.duration
        travelArc: companionPresence.arc
        travelDirection: companionPresence.directionX/Math.max(1,Math.hypot(companionPresence.directionX,companionPresence.directionY))
        travelDirectionY: companionPresence.directionY/Math.max(1,Math.hypot(companionPresence.directionX,companionPresence.directionY))
        managedPlacement: true
        emergenceEdge: companionPresence.emergenceEdge
        upright: true
        standingAngle: companionPresence.standingAngle
        appearClip: companionPresence.appearClip
        appearanceOffsetX: companionPresence.appearanceOffsetX
        appearanceOffsetY: companionPresence.appearanceOffsetY
        hideClip: companionPresence.hideClip
        travelNormalX: companionPresence.normalX
        travelNormalY: companionPresence.normalY
        connectedWater: true
        curvedImmersion:waterImmersion.active && !!waterImmersion.item && waterImmersion.item.status!==ShaderEffect.Error
        reveal: companionPresence.renderedReveal
        pointerFresh: root.companionPointerFresh
            && Math.hypot(root.companionPointerX-(x+width/2),root.companionPointerY-(y+height/2))<companionPresence.pointerNoticeRadius
        pointerX: (root.companionPointerX-(x+width/2))/Math.max(55*scale,Math.hypot(root.companionPointerX-(x+width/2),root.companionPointerY-(y+height/2)))
        pointerY: (root.companionPointerY-(y+height/2))/Math.max(55*scale,Math.hypot(root.companionPointerX-(x+width/2),root.companionPointerY-(y+height/2)))
        gazeX: companionBridge.gazeX
        gazeY: companionBridge.gazeY
        energy: companionBridge.energy
        bodySquash: companionBridge.squash
        bodyStretch: companionBridge.stretch
        bodyLean: companionBridge.lean
        bodyTip: companionBridge.tip
        ripple: companionBridge.ripple
        eyeOpen: companionBridge.eyeOpen
        mouthCurve: companionBridge.mouthCurve
        pulse: companionBridge.pulse
        expression: companionBridge.expression
        mood: companionBridge.mood
        activity: companionBridge.activity
        ambientProp: !root.ambientBlocked && session.companionTargetOutput===host.outputName ? session.companionAmbientProp : ""
        coworkPose: !root.ambientBlocked && session.companionTargetOutput===host.outputName ? session.companionCoworkPose : null
        coworkPropVisible: !root.ambientBlocked && session.companionTargetOutput===host.outputName && session.companionCoworkPropVisible
        targetX: companionPresence.targetX+(session.companionScale-1)*implicitWidth/2
        targetY: companionPresence.targetY+(session.companionScale-1)*implicitHeight/2
        onTravelCompleted: companionPresence.arrived()
        onDragStarted: companionPresence.beginDrag()
        onDragPositionRequested: (px,py)=>companionPresence.dragTo(
            px-(session.companionScale-1)*implicitWidth/2,
            py-(session.companionScale-1)*implicitHeight/2)
        onDragEnded: companionPresence.endDrag()
        onActivated: {companionWater.tap();companionBridge.sendEvent("click")}
        onChatRequested: {companionCuriosity.interrupt();AquaMind.openChat()}
        onPocketRequested: if (AquaPocket.permitted) {companionCuriosity.interrupt();AquaMind.dismiss();AquaPocket.show()}
        onSettingsRequested: GlobalStates.openSettingsSection(37,"Overview")
    }
    AquaTalkCloud {
        parent:adapter.overlayParent
        id:talkCloud;actor:companion
        outputWidth:host.width;outputHeight:host.height
        allowed:root.companionHostActive && session.companionInteractive
        onControlsVisibleChanged: if (controlsVisible) companionCuriosity.interrupt()
    }
    AquaPocketDrop {
        id:pocketDrop;parent:companion;actor:companion
        outputWidth:host.width;outputHeight:host.height
        allowed:root.companionHostActive && session.companionInteractive && !AquaMind.busy
    }
    AquaCloudActions {
        parent:adapter.overlayParent
        id:cloudActions;actor:companion
        outputWidth:host.width;outputHeight:host.height
        allowed:root.companionHostActive && session.companionInteractive
    }
    readonly property Region companionInputMask: Region { item: AquaHostPolicy.acceptsInput(root.companionHostActive, companion.interactive, companion.visible && companion.inputReady) ? companion : emptyInput }
    readonly property bool companionRimInputActive: host.presented && field.ready
        && AquaHostPolicy.acceptsInput(root.companionHostActive,companion.interactive,companion.visible && companion.inputReady)
    readonly property real companionRimThickness: Math.min(AbyssStyle.perimeterThickness,host.width/2,host.height/2)
    readonly property bool pocketSensorActive: root.companionRimInputActive && pocketDrop.enabled && pocketDrop.expandedZone
    readonly property int pocketZoneX: Math.max(0, Math.round(companion.x + companion.width * 0.5 - pocketDrop.dragRadius))
    readonly property int pocketZoneY: Math.max(0, Math.round(companion.y + companion.height * 0.5 - pocketDrop.dragRadius))
    readonly property int pocketZoneW: Math.max(0, Math.min(host.width - pocketZoneX, Math.round(companion.x + companion.width * 0.5 + pocketDrop.dragRadius) - pocketZoneX))
    readonly property int pocketZoneH: Math.max(0, Math.min(host.height - pocketZoneY, Math.round(companion.y + companion.height * 0.5 + pocketDrop.dragRadius) - pocketZoneY))
    readonly property int pocketMidRadius: Math.round(pocketDrop.dragRadius * 0.55)
    readonly property int pocketMidX: Math.max(0, Math.round(companion.x + companion.width * 0.5 - pocketMidRadius))
    readonly property int pocketMidY: Math.max(0, Math.round(companion.y + companion.height * 0.5 - pocketMidRadius))
    readonly property int pocketMidW: Math.max(0, Math.min(host.width - pocketMidX, Math.round(companion.x + companion.width * 0.5 + pocketMidRadius) - pocketMidX))
    readonly property int pocketMidH: Math.max(0, Math.min(host.height - pocketMidY, Math.round(companion.y + companion.height * 0.5 + pocketMidRadius) - pocketMidY))
    // Observe Aqua's surrounding water on all four painted rims. The
    // interior desktop retains its existing pass-through/input owners.
    readonly property Region companionRimInputMask: Region {
        Region { x:0; y:0; width:root.companionRimInputActive ? host.width : 0; height:root.companionRimThickness }
        Region { x:0; y:host.height-root.companionRimThickness; width:root.companionRimInputActive ? host.width : 0; height:root.companionRimThickness }
        Region { x:0; y:0; width:root.companionRimInputActive ? root.companionRimThickness : 0; height:host.height }
        Region { x:host.width-root.companionRimThickness; y:0; width:root.companionRimInputActive ? root.companionRimThickness : 0; height:host.height }
        Region { x:root.pocketZoneX; y:root.pocketZoneY; width:pocketDrop.containsDrag ? root.pocketZoneW : 0; height:pocketDrop.containsDrag ? root.pocketZoneH : 0 }
        Region { x:root.pocketZoneX; y:root.pocketZoneY; width:root.pocketSensorActive ? root.pocketZoneW : 0; height:2 }
        Region { x:root.pocketZoneX; y:root.pocketZoneY+root.pocketZoneH-2; width:root.pocketSensorActive ? root.pocketZoneW : 0; height:2 }
        Region { x:root.pocketZoneX; y:root.pocketZoneY; width:2; height:root.pocketSensorActive ? root.pocketZoneH : 0 }
        Region { x:root.pocketZoneX+root.pocketZoneW-2; y:root.pocketZoneY; width:2; height:root.pocketSensorActive ? root.pocketZoneH : 0 }
        Region { x:root.pocketMidX; y:root.pocketMidY; width:root.pocketSensorActive ? root.pocketMidW : 0; height:2 }
        Region { x:root.pocketMidX; y:root.pocketMidY+root.pocketMidH-2; width:root.pocketSensorActive ? root.pocketMidW : 0; height:2 }
        Region { x:root.pocketMidX; y:root.pocketMidY; width:2; height:root.pocketSensorActive ? root.pocketMidH : 0 }
        Region { x:root.pocketMidX+root.pocketMidW-2; y:root.pocketMidY; width:2; height:root.pocketSensorActive ? root.pocketMidH : 0 }
    }

    readonly property bool editing: talkCloud.editing
    readonly property bool curiosityOwned: companionCuriosity.owned
    readonly property bool utilitiesVisitActive: companionCuriosity.owned
        && companionCuriosity.feature?.kind === "utilities"
        && root.ownsCompanionFeature(companionCuriosity.feature)
    readonly property bool dockHeld: companionBridge.ready && companion.visible
        && (companionPresence.placement.key === "dock"
            || companionPresence.placement.support?.key === "dock"
            || (companionPresence.traveling && companionPresence.destination.key === "dock"))
    readonly property var waterLink: companionWater
    readonly property var actor: companion
    readonly property var inputRegions: [companionInputMask,companionRimInputMask,talkRegion,obsidianRegion,aiRegion]
    Region { id:talkRegion;item:talkCloud.visible ? talkCloud : emptyInput }
    Region { id:obsidianRegion;item:cloudActions.visible ? cloudActions.obsidianTarget : emptyInput }
    Region {
        id:aiRegion
        item:cloudActions.visible ? cloudActions.aiTarget : emptyInput
        Region { item:cloudActions.visible && cloudActions.pocketTarget?.visible ? cloudActions.pocketTarget : emptyInput }
        Region { item:cloudActions.visible && cloudActions.pomodoroTarget?.visible ? cloudActions.pomodoroTarget : emptyInput }
    }
    function yieldToUser(): void {companionCuriosity.yieldToUser()}
    Component.onDestruction: {
        companionCuriosity.interrupt()
        // Return QObject ownership before Loader destroys this output.
        companionPointer.enabled = false
        companionPointer.parent = root
    }
}
