import QtQuick
import Quickshell
import Quickshell.Wayland
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
    property bool readingFocusActive:false
    property bool quietIdle:false
    property string previewAmbientProp:""
    property bool previewLaptopActive:false
    function evaluateContext(): void {
        contextIntent=ContextProtocol.observe(contextState,{
            enabled:companionEnabled,visible:companionSessionVisible,
            optedIn:contextConnected,locked:GlobalStates.screenLocked,
            fullscreen:GameMode.hasFullscreenOnOutput(companionTargetOutput),
            gameMode:Appearance.gameModeMinimal,chatOpen:WullMind.conversationOpen,
            modalOpen:WullMind.hostBlocked,dnd:Notifications.notificationPolicyActive,
            quiet:!AbyssStyle.motionEnabled || companionPreferences?.animationsEnabled!==true,
            idle:false},Date.now())
        if(!contextState.enabled)contextConnected=false
        const focusCategory=contextState.director?.focus ?? "none"
        const hasAgent=Object.keys(contextState.director?.sessions ?? ({})).length>0
        if(companionSessionVisible && (focusCategory==="editor" || focusCategory==="terminal" || hasAgent || previewLaptopActive))
            companionBridge.show()
        if(contextConnected && contextState.enabled && focusCategory==="other" && !hasAgent && !previewLaptopActive) {
            if(!readingFocusActive && !readingFocusSettle.running)readingFocusSettle.restart()
        } else {
            readingFocusSettle.stop()
            readingFocusActive=false
        }
        if(coworkController) {
            coworkController.character=root.companionCharacter
            coworkController.directorState=contextState.director
            coworkController.hostContext=coworkHostContext()
            coworkController.step()
            if(coworkController.phase==="loop" && coworkController.hostContext
                    && Director.pollNeedsInput(contextState.director,coworkController.hostContext,Date.now()).eligible)
                coworkController.triggerCue("alert")
        }
        if(companionSessionVisible && root.previewAmbientProp.length===0
                && (focusCategory==="editor" || focusCategory==="terminal" || hasAgent || previewLaptopActive)
                && !coworkController.active && !contextSettle.running)
            contextSettle.restart()
    }
    onContextFeedChanged: {disconnectContext();contextConnect.restart()}
    Connections {
        target:root.contextFeed
        ignoreUnknownSignals:true
        function onFrame(packet): void {
            const trusted=root.contextConnected && root.contextConnection===root.contextFeed
                && root.host.session===root
            let normalizedPacket=packet
            if(trusted && typeof packet==="string" && packet.includes('"kind":"focus"') && packet.includes('"category":"other"')) {
                const activeAppId=String(ToplevelManager.activeToplevel?.appId ?? "").trim().toLowerCase()
                if(["antigravity","google-antigravity","cursor","windsurf","zed","obsidian"].includes(activeAppId)) {
                    try {
                        const parsed=JSON.parse(packet)
                        if(parsed?.kind==="focus" && parsed?.payload?.category==="other") {
                            parsed.payload.category="editor"
                            normalizedPacket=JSON.stringify(parsed)
                        }
                    } catch(_err) {}
                }
            }
            const accepted=ContextProtocol.accept(root.contextState,normalizedPacket,trusted,Date.now())
            if(accepted.accepted){
                root.evaluateContext()
                if(accepted.finishedLong)coworkController.triggerCue("success")
                contextSettle.restart()
            }
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
    Timer {id:readingFocusSettle;interval:450;onTriggered:{root.readingFocusActive=true;if(root.companionSessionVisible)companionBridge.show()}}
    Timer {id:quietIdleSettle;interval:8000;onTriggered:root.quietIdle=true}
    Timer {id:contextConnect;interval:1;onTriggered:root.connectContext()}
    Timer {id:contextReconnect;interval:1000;onTriggered:root.connectContext()}
    Connections {
        target:companionBridge
        function onReadyChanged(): void {root.evaluateContext()}
        function onVisibilityChanged(): void {root.evaluateContext()}
        function onActivityChanged(): void {
            if(companionBridge.activity==="idle" && !coworkController.active && !root.readingFocusActive)
                quietIdleSettle.restart()
            else {quietIdleSettle.stop();root.quietIdle=false}
        }
    }
    Connections {
        target:ToplevelManager
        function onActiveToplevelChanged(): void {
            if(root.previewAmbientProp.length>0 || root.previewLaptopActive) {
                root.previewAmbientProp=""
                root.previewLaptopActive=false
                Director.acceptAgent(root.contextState.director,{word:"ended",token:"fedcba9876543210"},Date.now())
            }
            if(!root.contextConnected || !root.contextState.enabled
                    || !root.companionPreferences.contextFocusEnabled) return
            const id=String(ToplevelManager.activeToplevel?.appId ?? "").trim().toLowerCase()
            const cat=!id ? "none"
                : ["foot","footclient","kitty","alacritty","wezterm","org.wezfurlong.wezterm",
                   "org.kde.konsole","org.gnome.terminal","com.mitchellh.ghostty"].includes(id) ? "terminal"
                : ["code","code-oss","vscodium","codium","dev.zed.zed","org.gnome.builder",
                   "org.kde.kate","emacs","neovide","antigravity","google-antigravity",
                   "cursor","windsurf","zed","obsidian"].includes(id) ? "editor" : "other"
            if(Director.setFocus(root.contextState.director,cat,Date.now()).accepted) {
                root.evaluateContext()
                contextSettle.restart()
            }
        }
    }
    Connections {
        target:WullMind
        function onConversationOpenChanged(): void {root.evaluateContext()}
        function onHostBlockedChanged(): void {root.evaluateContext()}
    }
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
                    || root.companionPreferences.ambientMediaEnabled),idle:WullMind.hostIdle || root.readingFocusActive || ambientController.activeProp.length>0,
            motionEnabled:root.companionPreferences.animationsEnabled && AbyssStyle.motionEnabled})
    }
    function coworkHostContext(): var {
        const base=visualContext()
        if(!base)return null
        const targetExt=root.outputHosts.find(window=>window.outputName===root.companionTargetOutput)?.extension
        return Object.assign({},base,{
            idle:false,
            optedIn:(root.contextConnected && root.contextState.enabled) || root.previewLaptopActive,
            coworkEnabled:Boolean(targetExt?.actor) && root.companionEnabled && root.previewAmbientProp.length===0
                && ((root.contextConnected && (root.companionPreferences.contextEnabled || root.companionPreferences.contextAiEnabled))
                    || root.previewLaptopActive),
            grounded:targetExt?.actor ? targetExt.actor.surfaceSupported!==false && !targetExt.actor.flying : false,
            character:root.companionCharacter,
            quiet:!base.motionEnabled
        })
    }
    readonly property var cowork: coworkController
    readonly property var companionCoworkPose: coworkController.pose
    readonly property bool companionCoworkPropVisible: coworkController.renderProp
    readonly property string companionCoworkClip: coworkController.clip
    readonly property string companionCoworkPhase: coworkController.phase
    WullCoworkController {
        id:coworkController
        objectName:"companionCoworkController"
        character:root.companionCharacter
        directorState:root.contextState.director
        hostContext:root.coworkHostContext()
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
    function presentAmbientPerformance(expression,gesture): void {
        if(!presentAmbientExpression(expression)) {
            ambientController.clearPerformance()
            return
        }
        if(!gesture)return
        const target=root.outputHosts.find(window=>window.outputName===root.companionTargetOutput)?.extension
        if(target && !target.ambientBlocked)target.actor?.perform(gesture)
    }
    readonly property string companionAmbientProp: root.previewAmbientProp.length>0 ? root.previewAmbientProp : ambientController.activeProp
    readonly property string companionAmbientActivity: root.previewAmbientProp.length>0 ? root.previewAmbientProp : ambientController.activeActivity
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
            root.visualContext(),{expression:"idle"},Date.now()).accepted && !coworkController.active
        focusPhase:permitted && root.companionPreferences.ambientFocusEnabled
            ? TimerService.pomodoroRunning
                ? (TimerService.pomodoroPaused ? "paused" : TimerService.pomodoroBreak ? "break" : "focus")
                : (root.readingFocusActive ? "focus" : "off")
            : "unavailable"
        powerBand:permitted && root.companionPreferences.ambientBatteryEnabled
            ? !Battery.available ? "unavailable" : !Battery.onBattery ? "charging"
                : Battery.isLow ? "low" : "normal" : "unavailable"
        playback:permitted && root.companionPreferences.ambientMediaEnabled
            ? !MprisController.activePlayer ? "unavailable"
                : MprisController.activePlayer.isPlaying ? "playing" : "paused" : "unavailable"
        idleState:permitted && root.companionPreferences.ambientFocusEnabled
            ? (WullMind.hostIdle && (root.quietIdle || (root.contextState.frame?.idle ?? false)) ? "idle" : "active") : "unavailable"
        resultCategory:permitted && root.companionPreferences.ambientFocusEnabled
            ? (["none","success","failure"].includes(root.contextState.frame?.result ?? "")
                ? root.contextState.frame.result : "none") : "unavailable"
        onPerformanceRequested:(expression,gesture)=>root.presentAmbientPerformance(expression,gesture)
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
function pocket(): void {
    if (!root.companionSessionVisible || !root.companionInteractive || !WullPocket.permitted) return
    if (WullPocket.opened) {WullPocket.close();return}
    companionBridge.show()
    companionBridge.sendEvent("hover",true)
    for (const window of root.outputHosts)
        if(window.outputName===root.companionTargetOutput && window.extension?.requestCompanionPocket)
            window.extension.requestCompanionPocket()
}
function previewProp(kind): void {
    if (!root.companionSessionVisible) return
    const mode=String(kind ?? "").trim().toLowerCase()
    companionBridge.show()
    companionBridge.sendEvent("hover",true)
    for (const window of root.outputHosts)
        if(window.outputName===root.companionTargetOutput && window.extension?.ensureCompanionPresent)
            window.extension.ensureCompanionPresent()
    if (["book","coffee","thought"].includes(mode)) {
        root.previewLaptopActive=false
        Director.acceptAgent(root.contextState.director,{word:"ended",token:"fedcba9876543210"},Date.now())
        root.previewAmbientProp=mode
        if (coworkController.active) coworkController.revoke()
        root.evaluateContext()
    } else if (mode==="laptop") {
        root.previewAmbientProp=""
        root.previewLaptopActive=true
        Director.acceptAgent(root.contextState.director,{word:"working",token:"fedcba9876543210"},Date.now())
        root.evaluateContext()
        contextSettle.restart()
    } else {
        root.previewAmbientProp=""
        root.previewLaptopActive=false
        Director.acceptAgent(root.contextState.director,{word:"ended",token:"fedcba9876543210"},Date.now())
        root.evaluateContext()
    }
}
function status(): string {
    const outputs=[]
    for (let i=0;i<root.outputHosts.length;i++)
        outputs.push(root.outputHosts[i].companionStatus())
    return JSON.stringify({enabled:root.companionEnabled,
        context:{connected:root.contextConnected,mode:root.contextIntent.mode,
            reason:root.contextIntent.reason,epoch:root.contextIntent.epoch,
            focus:root.contextState.director?.focus ?? "none"},
        pocket:{permitted:WullPocket.permitted,opened:WullPocket.opened,
            busy:WullPocket.busy,count:WullPocket.items.length},
        cowork:{active:coworkController.active,phase:coworkController.phase,
            clip:coworkController.clip,renderProp:coworkController.renderProp},
        ambient:{prop:root.companionAmbientProp,activity:root.companionAmbientActivity,
            focusPhase:ambientController.focusPhase},
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
