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
        || PolkitService.active || GlobalStates.regionSelectorOpen
        || GlobalStates.annotationEditorOpen || GlobalStates.widgetEditMode
        || GlobalStates.shellLayoutEditMode || host.overviewDragging
    readonly property bool companionPermission: AquaHostPolicy.hostActive(
        session.companionSessionVisible,companionBridge.ready,
        session.companionTargetOutput,host.outputName,host.presented,field.ready)
        && !root.companionOccluded
        && liquid.records.length<=field.capacity
    readonly property var companionScene: root._companionSceneState
    readonly property bool companionHostActive: root.companionPermission && companionPresence.qualified
    property var _companionSceneState: null
    property bool _sceneRebuildQueued: false
    property bool _sceneDirty: false
    property bool _barSurfacePending: false
    property var _settledModuleSurfaces: []
    property var _settledDetachedModules: []
    property var _settledBarModules: []
    readonly property bool _dockExcludedForScene: (host?.desktopHasApps ?? false)
        && (Config.options?.dock?.enable ?? true)
        && (Config.options?.enabledPanels ?? []).includes("abyssDock")
        && (!Array.isArray(Config.options?.dock?.screenList)
            || Config.options.dock.screenList.length === 0
            || Config.options.dock.screenList.includes(host.outputName))
    readonly property var _dockParticipant: liquid?.participants?.dock ?? null
    readonly property var _dockRestingForScene: root._dockExcludedForScene
        ? (root._dockParticipant?.restingRecord ?? null)
        : null
    onCompanionPermissionChanged: {
        if (!root.companionPermission) {
            barMotionSettleTimer.stop()
            root._barSurfacePending = false
            root._sceneRebuildQueued = false
            root._sceneDirty = false
            root._companionSceneState = null
        } else {
            root._scheduleCompanionSceneRebuild(true)
        }
    }
    on_DockExcludedForSceneChanged: root._scheduleCompanionSceneRebuild(false)
    on_DockRestingForSceneChanged: root._scheduleCompanionSceneRebuild(false)
    function _snapshotBarGeometry(): void {
        const barVisible = Boolean(bar && bar.visible)
        root._settledModuleSurfaces = barVisible
            ? (bar.deformations ?? []).filter(d=>d && d.surface && d.surface.width>0 && d.surface.height>0).map(d=>({
                edge:d.edge,
                surface:{x:d.surface.x,y:d.surface.y,width:d.surface.width,height:d.surface.height},
                radius:Number.isFinite(Number(d.radius)) ? Number(d.radius) : AbyssStyle.neckRadius,
                depth:Number(d.depth) || 0,
                along:Number(d.along) || 0,
                span:Number(d.span) || 0,
                cornerWrapArm:d.cornerWrapArm===true,
                kind:String(d.kind ?? "")}))
            : []
        root._settledDetachedModules = barVisible
            ? (bar.layoutRecords ?? []).filter(r=>r && r.detached===true && r.content && r.content.width>0 && r.content.height>0).map(r=>({
                edge:r.edge,
                rect:{x:r.content.x-6,y:r.content.y-6,width:r.content.width+12,height:r.content.height+12},
                radius:AbyssStyle.neckRadius}))
            : []
        root._settledBarModules = barVisible
            ? (bar.layoutRecords ?? []).map(record=>({
                edge:record.edge,along:record.along,span:record.span,detached:record.detached===true}))
            : []
    }
    function _buildCompanionScene(): var {
        if (!root.companionPermission)
            return null
        return AquaScene.fromParticipants({
            width:host.width,height:host.height,
            hostWidth:112*session.companionScale,hostHeight:98*session.companionScale,
            scale:session.companionScale,insets:host.nativeInsets,rimRadius:AbyssStyle.neckRadius,
            perimeterRadius:AbyssStyle.perimeterRadius,
            connectionDepth:AbyssStyle.connectionDepth,
            moduleSurfaces:root._settledModuleSurfaces,
            detachedModules:root._settledDetachedModules,
            excludeKeys:root._dockExcludedForScene ? ["dock"] : []},
            liquid.participants,root._settledBarModules)
    }
    function _scheduleCompanionSceneRebuild(barGeometryChanged: bool): void {
        if (!root.companionPermission) {
            barMotionSettleTimer.stop()
            root._barSurfacePending = false
            root._sceneRebuildQueued = false
            root._sceneDirty = false
            root._companionSceneState = null
            return
        }
        if (barGeometryChanged) {
            root._barSurfacePending = true
            if (bar?.motionActive && root._companionSceneState !== null) {
                barMotionSettleTimer.restart()
                return
            }
        } else {
            root._sceneDirty = true
        }
        if (root._sceneRebuildQueued)
            return
        root._sceneRebuildQueued = true
        Qt.callLater(root._flushCompanionSceneRebuild)
    }
    function _flushCompanionSceneRebuild(): void {
        root._sceneRebuildQueued = false
        if (!root.companionPermission) {
            root._sceneDirty = false
            root._companionSceneState = null
            return
        }
        if (root._companionSceneState === null || (root._barSurfacePending && !bar?.motionActive)) {
            root._snapshotBarGeometry()
            root._barSurfacePending = false
            root._sceneDirty = true
        } else if (root._barSurfacePending && bar?.motionActive) {
            barMotionSettleTimer.restart()
        }
        if (!root._sceneDirty)
            return
        root._sceneDirty = false
        root._companionSceneState = root._buildCompanionScene()
    }
    Timer {
        id: barMotionSettleTimer
        interval: 80
        repeat: false
        onTriggered: {
            if (!root.companionPermission)
                return
            if (bar?.motionActive) {
                if (root._barSurfacePending)
                    restart()
                return
            }
            if (root._barSurfacePending || root._companionSceneState === null) {
                root._sceneDirty = true
                root._flushCompanionSceneRebuild()
            }
        }
    }
    Connections {
        target: (host instanceof QtObject) ? host : null
        ignoreUnknownSignals: true
        function onWidthChanged(): void { root._scheduleCompanionSceneRebuild(false) }
        function onHeightChanged(): void { root._scheduleCompanionSceneRebuild(false) }
        function onNativeInsetsChanged(): void { root._scheduleCompanionSceneRebuild(false) }
        function onOutputNameChanged(): void { root._scheduleCompanionSceneRebuild(false) }
    }
    Connections {
        target: (session instanceof QtObject) ? session : null
        ignoreUnknownSignals: true
        function onCompanionScaleChanged(): void { root._scheduleCompanionSceneRebuild(false) }
    }
    Connections {
        target: AbyssStyle
        ignoreUnknownSignals: true
        function onNeckRadiusChanged(): void { root._scheduleCompanionSceneRebuild(false) }
        function onPerimeterRadiusChanged(): void { root._scheduleCompanionSceneRebuild(false) }
        function onConnectionDepthChanged(): void { root._scheduleCompanionSceneRebuild(false) }
    }
    Connections {
        target: (bar instanceof QtObject) ? bar : null
        ignoreUnknownSignals: true
        function onVisibleChanged(): void { root._scheduleCompanionSceneRebuild(true) }
        function onDeformationsChanged(): void { root._scheduleCompanionSceneRebuild(true) }
        function onLayoutRecordsChanged(): void { root._scheduleCompanionSceneRebuild(true) }
        function onMotionActiveChanged(): void {
            if (root._barSurfacePending && !bar?.motionActive)
                barMotionSettleTimer.restart()
        }
    }
    Connections {
        target: (liquid instanceof QtObject) ? liquid : null
        ignoreUnknownSignals: true
        function onParticipantsChanged(): void { root._scheduleCompanionSceneRebuild(false) }
        function onRecordsChanged(): void { root._scheduleCompanionSceneRebuild(false) }
        function onSurfacesSettledChanged(): void { root._scheduleCompanionSceneRebuild(false) }
    }
    readonly property bool ambientBlocked: !root.companionHostActive || root.companionOccluded
        || companionPresence.dragging || companionPresence.traveling || companionPresence.portalActive
        || companionCuriosity.busy || talkCloud.controlsVisible
    readonly property bool companionPerformanceHeld: session.companionTargetOutput===host.outputName
        && (session.cowork?.active===true || (session.companionAmbientProp ?? "").length>0 || AquaPocket.opened || AquaMind.isWorking)
    onCompanionPerformanceHeldChanged: if (companionPerformanceHeld) Qt.callLater(() => {
        if (root.companionPerformanceHeld && (AquaPocket.opened || (companionPresence.hiddenUntil<=Date.now() && companionPresence.requestedReveal>0)))
            root.ensureCompanionPresent()
    })
    readonly property var directorContext: ({enabled:root.companionHostActive,
        dragging:companionPresence.dragging,manualTravel:companionPresence.traveling,
        portalTravel:companionPresence.portalActive,
        grounded:companionPresence.grounded && !companion.flying && !companion.moving && !companion.relocating && companion.presentation>0.99,
        controlsVisible:talkCloud.controlsVisible,
        modalOpen:root.companionOccluded || companionCuriosity.busy || AquaPocket.opened || AquaPocket.busy})
    onDirectorContextChanged: if (session && session.companionTargetOutput===host.outputName) {
        Qt.callLater(session.evaluateContext)
    }
    property bool pendingChatOpen: false
    function ensureCompanionPresent(snapHome = false): void {
        if(!root.companionPermission) return
        companionTurns.cancel()
        companionCuriosity.interrupt()
        companionPresence.hiddenUntil=0
        if(!companionPresence.visitActive || companionPresence.retreating) {
            if(!companionPresence.resumeFromOcclusion())
                companionPresence.appear()
        }
        else if(snapHome)companionPresence.returnToHome(true)
        else if(companionPresence.traveling || companionPresence.portalActive)companionPresence.pause(false)
        companionPresence.peekOnly=false;companionPresence.peekIntro=false;companionPresence.renderedReveal=1
        if(companion.presentation<.99 && !companion.leaving)companion.present(1)
    }
    function requestCompanionChat(): void {
        if(!root.companionPermission) return
        companionTurns.cancel()
        companionCuriosity.interrupt()
        companionPresence.hiddenUntil=0
        if(!companionPresence.visitActive || companionPresence.retreating) {
            root.pendingChatOpen=false
            companionPresence.appear()
            companionPresence.peekOnly=false;companionPresence.peekIntro=false;companionPresence.renderedReveal=1
            if(companion.presentation<.99 && !companion.leaving)companion.present(1)
            AquaMind.openChat()
            return
        }
        if(companionPresence.isNearHome()) {
            root.pendingChatOpen=false
            root.ensureCompanionPresent(false)
            AquaMind.openChat()
            return
        }
        if(talkCloud.controlsVisible || AquaMind.conversationOpen)
            AquaMind.dismiss()
        root.pendingChatOpen=true
        if(!companionPresence.fastReturnNearHome()) {
            root.pendingChatOpen=false
            root.ensureCompanionPresent(false)
            AquaMind.openChat()
        }
    }
    function requestCompanionPocket(): void {
        if(!root.companionPermission || !AquaPocket.permitted) return
        root.pendingChatOpen=false
        root.ensureCompanionPresent(false)
        AquaMind.dismiss()
        AquaPocket.show()
    }
    function resetCompanionCast(): void {
        companionTurns.cancel()
        companionPresence.clearOcclusionState()
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
            scene:{valid:AquaScene.valid(scene),width:scene?.width ?? 0,height:scene?.height ?? 0,
                hostWidth:scene?.hostWidth ?? 0,hostHeight:scene?.hostHeight ?? 0,insets:scene?.insets ?? null,
                records:scene?.records?.slice(0,256),blockers:scene?.blockers?.slice(0,128),
                surfaces:scene?.surfaces?.slice(0,40)},
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
        && companion.interactive && (companion.hovered || companion.dragging || pocketDrop.containsDrag || talkCloud.controlsVisible || AquaMind.conversationOpen || AquaMind.isWorking || root.companionPerformanceHeld)
    onCompanionHoverHeldChanged: if (session.companionTargetOutput===host.outputName && companionBridge.ready)
        companionBridge.sendEvent("hover",root.companionHoverHeld)
    AquaPresence {
        id: companionPresence
        scene: root.companionScene
        actor: companion
        permitted: root.companionPermission
        occluded: root.companionOccluded
        homeEdge: session.companionHomeEdge
        homeAlong: session.companionHomeAlong
        requestedReveal: companionBridge.visibility==="present" ? 1
            : companionBridge.visibility==="peeking" ? .46 : 0
        motionEnabled: session.companionPreferences.animationsEnabled && AbyssStyle.motionEnabled
        interactionHeld: companionBridge.activity!=="idle" || pocketDrop.containsDrag || AquaPocket.busy || talkCloud.controlsVisible || AquaMind.conversationOpen || AquaMind.isWorking || root.companionPerformanceHeld
        pointerFresh: root.companionPointerFresh
        pointerX: root.companionPointerX
        pointerY: root.companionPointerY
        pointerReactionsEnabled: session.companionInteractive && !talkCloud.controlsVisible
            && !AquaMind.conversationOpen
        personality: session.companionPreferences.personality
        travelId: companionBridge.travelId
        travelFraction: companionBridge.travelTarget
        onStopRequested: companion.stopTravel()
        onSettled: if (root.pendingChatOpen) {
            root.pendingChatOpen=false
            if (root.companionPermission) {
                root.ensureCompanionPresent(false)
                AquaMind.openChat()
            }
        }
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
        primaryCharacter:session.selectedCompanion
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
                    talkCloud.releaseKeyboardFocus()
                    if (AquaPocket.opened && !GlobalStates.settingsNativeDialogOpen)
                        AquaPocket.close()
                    if (AquaMind.contextOpen)
                        AquaMind.dismiss()
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
    readonly property bool companionQuietSettled: session.companionMotionSettled
        && !companion.pointerFresh && !companion.hovered && !companion.dragging
        && !companionPresence.dragging && !companionPresence.traveling && !companionPresence.portalActive
        && !companion.moving && !companion.walking && !companion.rolling && !companion.flying && !companion.relocating
        && !companion.leaving && !companion.gesturing && companion.presentation >= 0.99
        && !companionTurns.active && !companionCuriosity.busy
        && !talkCloud.controlsVisible
        && !AquaMind.conversationOpen && !AquaMind.isWorking && !AquaPocket.opened && !pocketDrop.containsDrag
        && (!session.cowork?.active || session.cowork?.phase === "loop")
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
            && AbyssRenderPolicy.aquaQuality!=="performance"
        powerProfile: AbyssRenderPolicy.powerProfile
        bubblesEnabled: session.companionPreferences.effectsEnabled && Appearance.effectsEnabled
        nearbyInteraction: companion.pointerFresh || companion.hovered || companion.dragging
            || pocketDrop.containsDrag || talkCloud.controlsVisible || root.talkCloudHovered
            || root.pocketPaneHovered || AquaMind.conversationOpen || AquaMind.contextOpen
            || AquaPocket.opened || companionCuriosity.busy
        quietSettled: root.companionQuietSettled
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
        readonly property real pointerDx: root.companionPointerFresh ? root.companionPointerX-(x+width/2) : 0
        readonly property real pointerDy: root.companionPointerFresh ? root.companionPointerY-(y+height/2) : 0
        readonly property real pointerDist: root.companionPointerFresh ? Math.hypot(pointerDx,pointerDy) : Infinity
        pointerFresh: root.companionPointerFresh && pointerDist<companionPresence.pointerNoticeRadius
        pointerX: pointerFresh ? pointerDx/Math.max(55*scale,pointerDist) : 0
        pointerY: pointerFresh ? pointerDy/Math.max(55*scale,pointerDist) : 0
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
        thinking: AquaMind.isWorking
        thinkingPhase: AquaMind.workPhase
        expression: AquaMind.isWorking ? "thinking" : companionBridge.expression
        mood: companionBridge.mood
        activity: AquaMind.isWorking ? "thinking" : companionBridge.activity
        ambientProp: !root.ambientBlocked && session.companionTargetOutput===host.outputName ? session.companionAmbientProp : ""
        coworkPose: !root.ambientBlocked && session.companionTargetOutput===host.outputName ? session.companionCoworkPose : null
        coworkPropVisible: !root.ambientBlocked && session.companionTargetOutput===host.outputName && session.companionCoworkPropVisible
        targetX: companionPresence.targetX+(session.companionScale-1)*implicitWidth/2
        targetY: companionPresence.targetY+(session.companionScale-1)*implicitHeight/2
        onTravelCompleted: companionPresence.arrived()
        onDragStarted: {root.pendingChatOpen=false;companionPresence.beginDrag()}
        onDragPositionRequested: (px,py)=>companionPresence.dragTo(
            px-(session.companionScale-1)*implicitWidth/2,
            py-(session.companionScale-1)*implicitHeight/2)
        onDragEnded: companionPresence.endDrag()
        onActivated: {
            companionWater.tap()
            companionBridge.sendEvent("click")
            companionCuriosity.interrupt()
            if (!talkCloud.controlsVisible)
                root.requestCompanionChat()
            else
                root.ensureCompanionPresent(false)
        }
        onChatRequested: {
            companionCuriosity.interrupt()
            root.requestCompanionChat()
        }
        onPocketRequested: if (AquaPocket.permitted) {
            companionCuriosity.interrupt()
            root.requestCompanionPocket()
        }
        onSettingsRequested: GlobalStates.openSettingsSection(37,"Overview")
    }
    Timer {
        id: initialChatFocusPulse
        interval: 120
        repeat: false
    }
    Timer {
        id: postOpenFocusGuard
        interval: 420
        repeat: false
    }
    Connections {
        target: root.Window.window
        ignoreUnknownSignals: true
        function onActiveChanged(): void {
            if (root.Window.window && !root.Window.window.active && !initialChatFocusPulse.running && !postOpenFocusGuard.running)
                talkCloud.releaseKeyboardFocus()
        }
    }
    Connections {
        target: typeof ToplevelManager !== "undefined" ? ToplevelManager : null
        ignoreUnknownSignals: true
        function onActiveToplevelChanged(): void {
            if (ToplevelManager.activeToplevel?.activated && !initialChatFocusPulse.running && !postOpenFocusGuard.running)
                talkCloud.releaseKeyboardFocus()
        }
    }
    Connections {
        target: typeof NiriService !== "undefined" ? NiriService : null
        ignoreUnknownSignals: true
        function onActiveWindowChanged(): void {
            if (NiriService.activeWindow && !initialChatFocusPulse.running && !postOpenFocusGuard.running)
                talkCloud.releaseKeyboardFocus()
        }
    }
    Connections {
        target: AquaMind
        function onConversationOpenChanged(): void {
            if (AquaMind.conversationOpen && root.companionPermission) {
                root.ensureCompanionPresent(false)
                talkCloud.wantsKeyboardFocus=true
                initialChatFocusPulse.restart()
                postOpenFocusGuard.restart()
            } else {
                initialChatFocusPulse.stop()
                postOpenFocusGuard.stop()
                talkCloud.releaseKeyboardFocus()
            }
        }
        function onContextOpenChanged(): void {
            if (AquaMind.contextOpen && root.companionPermission)
                root.ensureCompanionPresent(false)
        }
    }
    readonly property bool companionSurfacePreempted: host.editorOpen
        || liquid.activeDialog || PolkitService.active || GlobalStates.regionSelectorOpen
        || GlobalStates.annotationEditorOpen || GlobalStates.widgetEditMode
        || GlobalStates.shellLayoutEditMode || host.overviewDragging
    onCompanionSurfacePreemptedChanged: if (companionSurfacePreempted) {
        root.pendingChatOpen=false
        talkCloud.releaseKeyboardFocus()
        if (AquaPocket.opened && !GlobalStates.settingsNativeDialogOpen)
            AquaPocket.close()
        if (AquaMind.conversationOpen || AquaMind.contextOpen || AquaMind.checkInStage.length>0 || AquaMind.text.length>0)
            AquaMind.dismiss()
    }
    AquaTalkCloud {
        parent:adapter.overlayParent
        id:talkCloud;actor:companion
        outputWidth:host.width;outputHeight:host.height
        allowed:root.companionHostActive && session.companionInteractive
        onControlsVisibleChanged: if (controlsVisible) {
            companionCuriosity.interrupt()
        }
    }
    AquaPocketDrop {
        id:pocketDrop;parent:companion;actor:companion
        outputWidth:host.width;outputHeight:host.height
        allowed:root.companionHostActive && session.companionInteractive && !AquaMind.busy
    }
    Item {
        id: agentCursorOverlay
        objectName: "aquaAgentCursor"
        parent: adapter.overlayParent
        z: 999
        enabled: false
        width: 1
        height: 1
        x: AquaMind.agentCursorX
        y: AquaMind.agentCursorY
        visible: opacity > 0.01
        opacity: (AquaMind.agentCursorVisible && !root.companionOccluded) ? 1.0 : 0.0
        readonly property color cursorAccent: (session?.companionCharacter ?? companion?.character) === "octo" ? "#fb7185" : "#38bdf8"
        readonly property int pulseSeq: AquaMind.agentCursorPulseSeq
        onPulseSeqChanged: {
            cursorRippleAnim.restart();
        }
        onVisibleChanged: if (visible)
            cursorPointerCanvas.requestPaint()
        onCursorAccentChanged: cursorPointerCanvas.requestPaint()
        Behavior on opacity {
            NumberAnimation {
                duration: 180
            }
        }
        Behavior on x {
            NumberAnimation {
                duration: 260
                easing.type: Easing.OutCubic
            }
        }
        Behavior on y {
            NumberAnimation {
                duration: 260
                easing.type: Easing.OutCubic
            }
        }
        Rectangle {
            id: cursorRippleRing
            objectName: "aquaAgentCursorRipple"
            width: 36
            height: 36
            x: -width / 2
            y: -height / 2
            radius: width / 2
            color: "transparent"
            border.width: 2.2
            border.color: agentCursorOverlay.cursorAccent
            scale: 0.35
            opacity: 0.0
            ParallelAnimation {
                id: cursorRippleAnim
                NumberAnimation {
                    target: cursorRippleRing
                    property: "scale"
                    from: 0.3
                    to: 1.55
                    duration: 460
                    easing.type: Easing.OutCubic
                }
                NumberAnimation {
                    target: cursorRippleRing
                    property: "opacity"
                    from: 0.92
                    to: 0.0
                    duration: 460
                    easing.type: Easing.OutQuad
                }
            }
        }
        Canvas {
            id: cursorPointerCanvas
            objectName: "aquaAgentCursorPointer"
            x: -2
            y: -2
            width: 28
            height: 28
            onPaint: {
                var ctx = getContext("2d");
                ctx.reset();
                ctx.beginPath();
                ctx.moveTo(2, 2);
                ctx.lineTo(2, 21);
                ctx.lineTo(7.5, 16.2);
                ctx.lineTo(11.8, 24.5);
                ctx.lineTo(15.2, 22.8);
                ctx.lineTo(10.8, 14.5);
                ctx.lineTo(18.5, 14.5);
                ctx.closePath();
                ctx.fillStyle = String(agentCursorOverlay.cursorAccent);
                ctx.fill();
                ctx.lineWidth = 1.8;
                ctx.strokeStyle = "#ffffff";
                ctx.stroke();
            }
        }
        Rectangle {
            id: cursorBadgePill
            objectName: "aquaAgentCursorBadge"
            x: 18
            y: 14
            height: 22
            width: Math.min(210, cursorBadgeText.implicitWidth + 16)
            radius: 11
            color: "#dd0f172a"
            border.width: 1
            border.color: agentCursorOverlay.cursorAccent
            Text {
                id: cursorBadgeText
                objectName: "aquaAgentCursorBadgeText"
                anchors.centerIn: parent
                color: "#f8fafc"
                font.pixelSize: 11
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                text: {
                    const refTag = String(AquaMind.agentCursorRef || "").trim();
                    const lbl = String(AquaMind.agentCursorLabel || "").trim();
                    const act = String(AquaMind.agentCursorAction || "click").trim();
                    const verb = (act === "fill" || act === "set_text" || act === "type")
                        ? "Typing"
                        : (act === "focus_element" ? "Focusing" : (act === "move" ? "Moving" : "Clicking"));
                    if (refTag && lbl)
                        return "✦ " + refTag + " · " + lbl;
                    if (refTag)
                        return "✦ " + refTag + " · " + verb;
                    if (lbl && lbl !== act)
                        return "✦ " + lbl;
                    return "✦ " + verb + "…";
                }
            }
        }
    }
    Loader {
        id: editorMannequinBody
        parent: host.edgeEditor?.companionMannequinItem ?? null
        readonly property bool topEdge: host.edgeEditor?.companionDraftEdge === "top"
        readonly property real availableHeight: Math.max(24, (parent?.height ?? 98) - 24)
        readonly property real availableWidth: Math.max(24, (parent?.width ?? 112) - 12)
        readonly property real dropletScale: Math.min(
            session.companionScale * 0.88,
            availableHeight / 72,
            availableWidth / 68)
        anchors.centerIn: parent
        anchors.verticalCenterOffset: (topEdge ? -9 : 9) + 3.3 * dropletScale
        z: 1
        active: host.editorOpen && !!host.edgeEditor?.companionMannequinItem && host.edgeEditor.companionMannequinItem.visible
        sourceComponent: Component {
            WaterDropletBody {
                character: session.companionCharacter
                width: 76; height: 92
                transformOrigin: Item.Center
                scale: editorMannequinBody.dropletScale
                motionEnabled: false
                quietSettled: true
                effectsEnabled: false
                bubblesEnabled: false
                renderQuality: "performance"
                translucency: session.companionPreferences.translucency
                grounded: true
                opacity: 0.92
            }
        }
    }
    Binding {
        target: host.edgeEditor?.companionMannequinItem ?? null
        property: "customModelPresent"
        value: editorMannequinBody.active && !!editorMannequinBody.item
        when: !!host.edgeEditor?.companionMannequinItem
    }
    property bool _rhiPipelinesWarm: false
    Loader {
        id: rhiPipelineWarmer
        x: 2; y: 2; width: 4; height: 4
        z: -100
        enabled: false
        active: host.presented && field.ready && session.companionSessionVisible
            && session.companionTargetOutput === host.outputName && !root._rhiPipelinesWarm
        sourceComponent: Component {
            Item {
                id: prewarmContainer
                width: 4; height: 4
                clip: true
                opacity: 0.004
                property int _warmFrames: 0
                WaterDropletBody {
                    character: session.companionCharacter
                    width: 76; height: 92
                    scale: 0.05
                    transformOrigin: Item.TopLeft
                    motionEnabled: false
                    quietSettled: true
                    effectsEnabled: Appearance.effectsEnabled && AbyssRenderPolicy.aquaQuality !== "performance"
                    bubblesEnabled: false
                    renderQuality: AbyssRenderPolicy.aquaQuality
                    translucency: session.companionPreferences.translucency
                    grounded: true
                }
                FrameAnimation {
                    running: true
                    onTriggered: {
                        prewarmContainer._warmFrames += 1
                        if (prewarmContainer._warmFrames >= 2)
                            root._rhiPipelinesWarm = true
                    }
                }
            }
        }
    }
    readonly property bool companionAcceptsInput: AquaHostPolicy.acceptsInput(
        root.companionHostActive,
        companion.interactive,
        companion.visible && companion.inputReady && !companionPresence.portalActive)
    readonly property bool companionPresentingTransition:
        (companion.presentation > 0.001 && companion.presentation < 0.999)
        || companion.relocating || companion.leaving
    readonly property bool companionMotionQuantize: companion.moving
        || companionPresence.traveling || companionPresence.portalActive
        || root.companionPresentingTransition
    readonly property int companionMaskStep: companionMotionQuantize ? 16 : 1
    readonly property int companionMaskPad: companionMotionQuantize ? 16 : 0
    readonly property real companionCenterX: companion.x + companion.width * 0.5
    readonly property real companionCenterY: companion.y + companion.height * 0.5
    readonly property real companionHalfW: companion.width * companion.scale * 0.5
    readonly property real companionHalfH: companion.height * companion.scale * 0.5
    readonly property int companionMaskX: companionAcceptsInput
        ? Math.max(0, Math.floor((companionCenterX - companionHalfW - companionMaskPad) / companionMaskStep) * companionMaskStep) : 0
    readonly property int companionMaskY: companionAcceptsInput
        ? Math.max(0, Math.floor((companionCenterY - companionHalfH - companionMaskPad) / companionMaskStep) * companionMaskStep) : 0
    readonly property int companionMaskRight: companionAcceptsInput
        ? Math.min(host.width, Math.ceil((companionCenterX + companionHalfW + companionMaskPad) / companionMaskStep) * companionMaskStep) : 0
    readonly property int companionMaskBottom: companionAcceptsInput
        ? Math.min(host.height, Math.ceil((companionCenterY + companionHalfH + companionMaskPad) / companionMaskStep) * companionMaskStep) : 0
    readonly property Region companionInputMask: Region {
        x: root.companionMaskX
        y: root.companionMaskY
        width: Math.max(0, root.companionMaskRight - root.companionMaskX)
        height: Math.max(0, root.companionMaskBottom - root.companionMaskY)
    }
    readonly property bool companionRimInputActive: host.presented && field.ready
        && root.companionAcceptsInput
    readonly property real companionRimThickness: Math.min(AbyssStyle.perimeterThickness,host.width/2,host.height/2)
    readonly property bool pocketSensorActive: root.companionRimInputActive && pocketDrop.enabled && pocketDrop.expandedZone
    readonly property bool pocketZoneNeeded: root.pocketSensorActive || pocketDrop.containsDrag
    readonly property int pocketZoneX: root.pocketZoneNeeded ? Math.max(0, Math.round(companion.x + companion.width * 0.5 - pocketDrop.dragRadius)) : 0
    readonly property int pocketZoneY: root.pocketZoneNeeded ? Math.max(0, Math.round(companion.y + companion.height * 0.5 - pocketDrop.dragRadius)) : 0
    readonly property int pocketZoneW: root.pocketZoneNeeded ? Math.max(0, Math.min(host.width - pocketZoneX, Math.round(companion.x + companion.width * 0.5 + pocketDrop.dragRadius) - pocketZoneX)) : 0
    readonly property int pocketZoneH: root.pocketZoneNeeded ? Math.max(0, Math.min(host.height - pocketZoneY, Math.round(companion.y + companion.height * 0.5 + pocketDrop.dragRadius) - pocketZoneY)) : 0
    readonly property int pocketMidRadius: Math.round(pocketDrop.dragRadius * 0.55)
    readonly property int pocketMidX: root.pocketSensorActive ? Math.max(0, Math.round(companion.x + companion.width * 0.5 - pocketMidRadius)) : 0
    readonly property int pocketMidY: root.pocketSensorActive ? Math.max(0, Math.round(companion.y + companion.height * 0.5 - pocketMidRadius)) : 0
    readonly property int pocketMidW: root.pocketSensorActive ? Math.max(0, Math.min(host.width - pocketMidX, Math.round(companion.x + companion.width * 0.5 + pocketMidRadius) - pocketMidX)) : 0
    readonly property int pocketMidH: root.pocketSensorActive ? Math.max(0, Math.min(host.height - pocketMidY, Math.round(companion.y + companion.height * 0.5 + pocketMidRadius) - pocketMidY)) : 0
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
        Region { x:0; y:0; width:(root.companionRimInputActive && (AquaMind.contextOpen || (AquaPocket.opened && !GlobalStates.settingsNativeDialogOpen))) ? host.width : 0; height:(root.companionRimInputActive && (AquaPocket.opened && !GlobalStates.settingsNativeDialogOpen || AquaMind.contextOpen)) ? host.height : 0 }
    }

    readonly property bool popupOpen: talkCloud.popupOpen
    function handleEscape(): bool {
        return talkCloud.handleEscape()
    }
    readonly property bool editing: ((talkCloud.editing || talkCloud.swarmOpen) && talkCloud.keyboardFocusActive) || (talkCloud.visible && (AquaMind.contextOpen || talkCloud.pocketOpen))
    readonly property bool motionBusy: companionPresence.portalActive || companionPresence.traveling || companion.moving || companionTurns.active || root.companionPresentingTransition || companion.gesturing
    readonly property bool keyboardFocusOnDemand: !initialChatFocusPulse.running
        && (root.popupOpen || (((talkCloud.editing || talkCloud.swarmOpen) && talkCloud.keyboardFocusActive) || (talkCloud.visible && (AquaMind.contextOpen || talkCloud.pocketOpen))))
    readonly property bool curiosityOwned: companionCuriosity.owned
    readonly property bool utilitiesVisitActive: companionCuriosity.owned
        && companionCuriosity.feature?.kind === "utilities"
        && root.ownsCompanionFeature(companionCuriosity.feature)
    readonly property bool dockHeld: companionBridge.ready && companion.visible
        && !(host?.desktopHasApps ?? false)
        && (companionPresence.placement.key === "dock"
            || companionPresence.placement.support?.key === "dock"
            || (companionPresence.traveling && companionPresence.destination.key === "dock"))
    readonly property var waterLink: companionWater
    readonly property var actor: companion
    readonly property var talkCloudItem: talkCloud
    readonly property Item pocketPaneItem: talkCloud.visible ? talkCloud.pocketPaneItem : null
    readonly property bool talkCloudHovered: Boolean(talkCloud.visible && talkCloud.cloudHovered)
    readonly property bool pocketPaneHovered: Boolean(pocketPaneItem && pocketPaneItem.visible && talkCloud.pocketPaneHovered)
    readonly property int talkMaskStep: companionMotionQuantize ? 16 : 1
    readonly property int talkMaskPad: companionMotionQuantize ? 12 : 0
    readonly property int talkMaskX: talkCloud.visible
        ? Math.max(0, Math.floor((talkCloud.x - talkMaskPad) / talkMaskStep) * talkMaskStep) : 0
    readonly property int talkMaskY: talkCloud.visible
        ? Math.max(0, Math.floor((talkCloud.y - talkMaskPad) / talkMaskStep) * talkMaskStep) : 0
    readonly property int talkMaskRight: talkCloud.visible
        ? Math.min(host.width, Math.ceil((talkCloud.x + talkCloud.width + talkMaskPad) / talkMaskStep) * talkMaskStep) : 0
    readonly property int talkMaskBottom: talkCloud.visible
        ? Math.min(host.height, Math.ceil((talkCloud.y + talkCloud.height + talkMaskPad) / talkMaskStep) * talkMaskStep) : 0
    readonly property var inputRegions: [companionInputMask,companionRimInputMask,talkRegion,aiRegion]
    Region {
        id: talkRegion
        x: root.talkMaskX
        y: root.talkMaskY
        width: Math.max(0, root.talkMaskRight - root.talkMaskX)
        height: Math.max(0, root.talkMaskBottom - root.talkMaskY)
    }
    Region { id:aiRegion;item:emptyInput }
    function yieldToUser(): void {companionCuriosity.yieldToUser()}
    Component.onCompleted: {
        if (session && session.companionTargetOutput===host.outputName) Qt.callLater(session.evaluateContext)
        root._scheduleCompanionSceneRebuild(true)
    }
    Component.onDestruction: {
        companionCuriosity.interrupt()
        // Return QObject ownership before Loader destroys this output.
        companionPointer.enabled = false
        companionPointer.parent = root
    }
}
