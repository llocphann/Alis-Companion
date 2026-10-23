import QtQuick
import Quickshell
import qs
import qs.services
import qs.modules.common
import qs.modules.abyss.looks
import "services"
import "modules/abyss/companion"
import "modules/abyss/companion/WullPreferences.js" as WullPreferences

Item {
    id: root
    required property var host
    readonly property var outputHosts: host.outputs
    readonly property var mind: WullMind
    readonly property var bridge: companionBridge
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
            if(root.companionSessionVisible && companionBridge.visibility==="present")
                companionBridge.sendIntent(expression,.5,3000)
        }
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
        sessionVisible:root.companionSessionVisible,
        targetOutput:root.companionTargetOutput,
        backend:{ready:companionBridge.ready,
            requestedVisible:companionBridge.requestedVisible,
            visibility:companionBridge.visibility,
            sequence:companionBridge.inboundSeq,
            restartAttempts:companionBridge.restartAttempts},outputs:outputs})
}

    Component.onCompleted: root.syncCompanionVisibility()
    Component.onDestruction: {
        WullMind.hostVisible=false
        WullMind.hostIdle=false
        WullMind.hostBlocked=true
        WullMind.cancel()
        WullMind.dismiss()
        companionBridge.hide()
    }
}
