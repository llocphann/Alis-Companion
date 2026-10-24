import QtQuick
import Quickshell
import qs
import qs.services
import qs.modules.common
import qs.modules.abyss.looks
import "services"
import "modules/abyss/companion"
import "modules/abyss/companion/WullPreferences.js" as WullPreferences
import "modules/abyss/companion/WullContextProtocol.js" as ContextProtocol
import "modules/abyss/companion/WullBehaviorDirector.js" as Director

Item {
    id: root
    required property var host
    readonly property var outputHosts: host.outputs
    readonly property var mind: WullMind
    readonly property var bridge: companionBridge
    property var contextState:ContextProtocol.newState()
    property var contextConnection:null
    property bool contextConnected:false
    property var contextIntent:({mode:"idle",reason:"disabled",expression:"idle",epoch:0})
    readonly property var contextFeed:host?.contextApiVersion===1 ? host.contextSource : null
    function disconnectContext(): void {
        contextSettle.stop()
        ContextProtocol.configure(contextState,false,"",Date.now())
        contextConnection=null;contextConnected=false;evaluateContext()
    }
    function connectContext(): void {
        if(!contextFeed || host.session!==root){disconnectContext();return}
        if(contextConnected && contextConnection===contextFeed && contextState.enabled)return
        const source=contextFeed,binding=source.bind(root)
        if(!binding || binding.version!==1
                || !ContextProtocol.configure(contextState,true,binding.generation,Date.now()).accepted){disconnectContext();return}
        contextConnection=source;contextConnected=true
        if(!source.activate(root,binding.generation))disconnectContext()
    }
    function evaluateContext(): void {
        contextIntent=ContextProtocol.observe(contextState,{
            enabled:companionEnabled,visible:companionSessionVisible,
            optedIn:contextConnected,locked:GlobalStates.screenLocked,
            fullscreen:GameMode.hasFullscreenOnOutput(companionTargetOutput),
            gameMode:Appearance.gameModeMinimal,chatOpen:WullMind.conversationOpen,
            modalOpen:WullMind.hostBlocked,dnd:Notifications.notificationPolicyActive,
            quiet:!AbyssStyle.motionEnabled || companionPreferences?.animationsEnabled!==true,
            idle:WullMind.hostIdle},Date.now())
        if(!contextState.enabled)contextConnected=false
    }
    onContextFeedChanged: {disconnectContext();contextConnect.restart()}
    Connections {
        target:root.contextFeed
        ignoreUnknownSignals:true
        function onFrame(packet): void {
            const trusted=root.contextConnected && root.contextConnection===root.contextFeed
                && root.host.session===root
            const accepted=ContextProtocol.accept(root.contextState,packet,trusted,Date.now())
            if(accepted.accepted){root.evaluateContext();contextSettle.restart()}
            else if(!root.contextState.enabled){
                root.disconnectContext()
                if(root.contextFeed && root.host.session===root)contextReconnect.restart()
            }
        }
        function onRevoked(): void {root.disconnectContext()}
    }
    // Re-evaluate the Director's existing focus-settle deadline; this is not a
    // Focus session clock and never loads a model or starts a second actor.
    Timer {id:contextSettle;interval:750;onTriggered:root.evaluateContext()}
    Timer {id:contextConnect;interval:1;onTriggered:root.connectContext()}
    Timer {id:contextReconnect;interval:1000;onTriggered:root.connectContext()}
    Connections {
        target:root.host
        ignoreUnknownSignals:true
        function onSessionChanged(): void {contextConnect.restart()}
    }
    readonly property var companionOptions: Config.options?.abyss?.companion
    readonly property var companionPreferences: WullPreferences.normalize(companionOptions)
    readonly property string selectedCompanion: companionPreferences.character
    readonly property bool alternatingCompanions: companionPreferences.alternateCompanions
    property string companionCharacter: selectedCompanion
    Binding {target:WullMind;property:"character";value:root.companionCharacter}
    function resetCompanionCast(): void {
        companionCharacter=selectedCompanion
        for (const window of root.outputHosts) window.resetCompanionCast()
    }
    onSelectedCompanionChanged: Qt.callLater(root.resetCompanionCast)
    onAlternatingCompanionsChanged: if (!alternatingCompanions) Qt.callLater(root.resetCompanionCast)
    readonly property bool companionEnabled: Config.ready && companionPreferences.enabled
    Binding {target:WullPocket;property:"hostAvailable";value:root.companionSessionVisible && root.companionInteractive}
    Binding {target:WullMind;property:"hostVisible";value:root.companionSessionVisible && companionBridge.ready && companionBridge.visibility==="present"}
    Binding {target:WullMind;property:"hostIdle";value:companionBridge.activity==="idle"}
    Binding {
        target:WullMind;property:"hostBlocked"
        value:!root.companionSessionVisible || !root.companionPreferences.animationsEnabled
            || !AbyssStyle.motionEnabled || Notifications.notificationPolicyActive
            || GameMode.hasFullscreenOnOutput(root.companionTargetOutput)
            || root.outputHosts.find(window=>window.outputName===root.companionTargetOutput)?.extension?.ambientBlocked !== false
    }
    Connections {
        target:WullMind
        function onReactionRequested(expression): void {
            root.presentAiExpression(expression)
        }
    }
    function visualContext(): var {
        const local=root.outputHosts.find(window=>window.outputName===root.companionTargetOutput)?.extension?.directorContext
        if(!local)return null
        return Object.assign({},local,{
            enabled:root.companionEnabled && local.enabled===true,
            visible:root.companionSessionVisible && companionBridge.visibility==="present",
            locked:GlobalStates.screenLocked,gameMode:Appearance.gameModeMinimal,
            fullscreen:GameMode.hasFullscreenOnOutput(root.companionTargetOutput),
            dnd:Notifications.notificationPolicyActive,chatOpen:WullMind.conversationOpen,
            ambientOptIn:root.companionPreferences.ambientEnabled
                && (root.companionPreferences.ambientFocusEnabled || root.companionPreferences.ambientBatteryEnabled
                    || root.companionPreferences.ambientMediaEnabled),idle:WullMind.hostIdle,
            motionEnabled:root.companionPreferences.animationsEnabled && AbyssStyle.motionEnabled})
    }
    function presentAiExpression(expression): bool {
        const proposal=Director.proposeAiExpression(contextState.director,visualContext(),{expression:expression},Date.now())
        return proposal.accepted && proposal.epoch===contextState.director.epoch
            && companionBridge.sendIntent(proposal.expression,proposal.intensity,proposal.ttlMs)
    }
    function presentAmbientExpression(expression): bool {
        const proposal=Director.proposeAmbientExpression(contextState.director,visualContext(),{expression:expression},Date.now())
        return proposal.accepted && proposal.epoch===contextState.director.epoch
            && companionBridge.sendIntent(proposal.expression,proposal.intensity,proposal.ttlMs)
    }
    WullAmbientController {
        id:ambientController
        objectName:"companionAmbientController"
        permitted:root.companionEnabled && root.companionPreferences.ambientEnabled
            && (root.companionPreferences.ambientFocusEnabled || root.companionPreferences.ambientBatteryEnabled
                || root.companionPreferences.ambientMediaEnabled)
            && root.companionSessionVisible && !Notifications.notificationPolicyActive
            && !GameMode.hasFullscreenOnOutput(root.companionTargetOutput)
            && root.companionPreferences.animationsEnabled && AbyssStyle.motionEnabled
        consentKey:[root.companionPreferences.ambientFocusEnabled,
            root.companionPreferences.ambientBatteryEnabled,root.companionPreferences.ambientMediaEnabled].join(":")
        eligible:Director.proposeAmbientExpression(root.contextState.director,
            root.visualContext(),{expression:"idle"},Date.now()).accepted
        focusPhase:permitted && root.companionPreferences.ambientFocusEnabled
            ? !TimerService.pomodoroRunning ? "off" : TimerService.pomodoroPaused ? "paused"
                : TimerService.pomodoroBreak ? "break" : "focus" : "unavailable"
        powerBand:permitted && root.companionPreferences.ambientBatteryEnabled
            ? !Battery.available ? "unavailable" : !Battery.onBattery ? "charging"
                : Battery.isLow ? "low" : "normal" : "unavailable"
        playback:permitted && root.companionPreferences.ambientMediaEnabled
            ? !MprisController.activePlayer ? "unavailable"
                : MprisController.activePlayer.isPlaying ? "playing" : "paused" : "unavailable"
        onReactionRequested:expression=>root.presentAmbientExpression(expression)
    }
    readonly property string companionTargetOutput: GlobalStates.resolveOutputName(
        "", Config.options?.bar?.screenList ?? [])
    readonly property string companionEdge: (Config.options?.bar?.vertical ? (Config.options?.bar?.bottom ? "right" : "left") : (Config.options?.bar?.bottom ? "bottom" : "top"))
    readonly property real companionScale: companionPreferences.size
    readonly property bool companionInteractive: companionPreferences.interactive
    readonly property bool companionSessionVisible: companionEnabled
        && companionTargetOutput.length > 0
        && !GlobalStates.screenLocked
        && !Appearance.gameModeMinimal
        && (!GameMode.hasFullscreenOnOutput(companionTargetOutput)
            || (!companionPreferences.hideInFullscreen
                && (Config.options?.abyss?.perimeter?.visibleInFullscreen ?? false)))

    function syncCompanionVisibility(): void {
        if (root.companionSessionVisible)
            companionBridge.show()
        else
            companionBridge.hide()
    }

    onCompanionSessionVisibleChanged: root.syncCompanionVisibility()

    CompanionBridge {
        id: companionBridge
        // Guard the development binary override as well as the dispatcher.
        // An inherited INIR_COMPANIOND must not bypass default-off.
        binaryPath: root.companionEnabled ? (Quickshell.env("INIR_COMPANIOND") ?? "") : ""
        useNativeDispatcher: root.companionEnabled
        personality: root.companionPreferences.personality
        appearanceFrequency: root.companionPreferences.appearanceFrequency
    }
function chat(): void {
    if (!root.companionSessionVisible || !root.companionInteractive || !WullMind.talkEnabled) return
    if (WullMind.conversationOpen) {WullMind.cancel();WullMind.dismiss();return}
    companionBridge.show()
    companionBridge.sendEvent("hover",true)
    for (const window of root.outputHosts)
        if(window.outputName===root.companionTargetOutput)window.requestCompanionChat()
}
function status(): string {
    const outputs=[]
    for (let i=0;i<root.outputHosts.length;i++)
        outputs.push(root.outputHosts[i].companionStatus())
    return JSON.stringify({enabled:root.companionEnabled,
        context:{connected:root.contextConnected,mode:root.contextIntent.mode,
            reason:root.contextIntent.reason,epoch:root.contextIntent.epoch},
        sessionVisible:root.companionSessionVisible,
        targetOutput:root.companionTargetOutput,
        backend:{ready:companionBridge.ready,
            requestedVisible:companionBridge.requestedVisible,
            visibility:companionBridge.visibility,
            sequence:companionBridge.inboundSeq,
            restartAttempts:companionBridge.restartAttempts},outputs:outputs})
}

    Component.onCompleted: {root.syncCompanionVisibility();contextConnect.start()}
    Component.onDestruction: {
        WullPocket.hostAvailable=false
        WullPocket.revoke()
        root.disconnectContext()
        WullMind.hostVisible=false
        WullMind.hostIdle=false
        WullMind.hostBlocked=true
        WullMind.cancel()
        WullMind.dismiss()
        companionBridge.hide()
    }
}
