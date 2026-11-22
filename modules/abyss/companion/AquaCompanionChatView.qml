pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.widgets
import qs.modules.abyss.looks
import "."

Item {
    id: root
    objectName: "aquaCompanionChatView"

    readonly property alias inputField: embeddedCloud.inputField
    readonly property alias cloud: embeddedCloud
    readonly property var mind: AquaMind

    function _findHostSidebar(startItem): Item {
        let p = startItem
        while (p) {
            if (p.panelVisible !== undefined)
                return p
            p = p.parent ?? null
        }
        return null
    }
    readonly property Item hostSidebar: root._findHostSidebar(root.parent)
    readonly property bool hostSidebarOpen: root.hostSidebar ? Boolean(root.hostSidebar.panelVisible) : root.visible
    readonly property bool effectiveVisible: root.visible && root.hostSidebarOpen
    property bool _ownedSidebarOpen: false
    property bool _workspaceEnsured: false
    property bool _initialScrolled: false

    onHostSidebarOpenChanged: {
        if (!root.hostSidebarOpen)
            root._initialScrolled = false
    }

    function syncSidebarState(): void {
        if (root.effectiveVisible) {
            root._ownedSidebarOpen = true
            AquaMind.sidebarChatOpen = true
            if (!root._workspaceEnsured) {
                root._workspaceEnsured = true
                AquaMind.ensureWorkspaceLoaded()
            }
            if (!root._initialScrolled) {
                root._initialScrolled = true
                Qt.callLater(() => {
                    if (root.effectiveVisible && typeof embeddedCloud.scrollToLatestMessage === "function")
                        embeddedCloud.scrollToLatestMessage()
                })
            }
        } else if (root._ownedSidebarOpen) {
            root._ownedSidebarOpen = false
            AquaMind.sidebarChatOpen = false
            if (!AquaMind.conversationOpen && (AquaMind.voiceModeOpen || AquaMind.voiceRecording || AquaMind.voiceTranscribing))
                AquaMind.cancelVoiceMode()
        }
    }

    onEffectiveVisibleChanged: root.syncSidebarState()
    Component.onCompleted: root.syncSidebarState()
    Component.onDestruction: {
        if (root._ownedSidebarOpen) {
            root._ownedSidebarOpen = false
            AquaMind.sidebarChatOpen = false
            if (!AquaMind.conversationOpen && (AquaMind.voiceModeOpen || AquaMind.voiceRecording || AquaMind.voiceTranscribing))
                AquaMind.cancelVoiceMode()
        }
    }

    function scrollByPage(direction: int): bool {
        if (typeof embeddedCloud.scrollByPage === "function")
            return embeddedCloud.scrollByPage(direction)
        return false
    }

    function copyTextToClipboard(textValue, bubbleIdx = -1): bool {
        if (typeof embeddedCloud.copyTextToClipboard === "function")
            return embeddedCloud.copyTextToClipboard(textValue, bubbleIdx)
        return false
    }

    function copyBubbleText(index): string {
        if (typeof embeddedCloud.copyBubbleText === "function")
            return embeddedCloud.copyBubbleText(index)
        return ""
    }

    function copyActiveSelection(): bool {
        if (typeof embeddedCloud.copyActiveSelection === "function")
            return embeddedCloud.copyActiveSelection()
        return false
    }

    function handleInput(event): void {
        if (embeddedCloud.hasSidebarInputFocus || embeddedCloud.hasA2uiInputFocus || embeddedCloud.editorMode)
            return
        if (event.modifiers === Qt.NoModifier && (event.key === Qt.Key_PageUp || event.key === Qt.Key_PageDown)) {
            if (root.scrollByPage(event.key === Qt.Key_PageDown ? 1 : -1)) {
                event.accepted = true
                return
            }
        }
        if (embeddedCloud.voiceStageActive) {
            if (event.key === Qt.Key_Escape) {
                AquaMind.cancelVoiceMode()
                event.accepted = true
            } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                if (AquaMind.voiceRecording)
                    AquaMind.finishVoiceMode(AquaMind.voiceAutoSend)
                event.accepted = true
            }
            return
        }
        const field = root.inputField
        if (!field)
            return
        if (field.activeFocus) {
            if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C && String(field.selectedText ?? "").length === 0 && embeddedCloud.copyActiveSelection()) {
                event.accepted = true
            }
            return
        }
        if (event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) {
            if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C && embeddedCloud.copyActiveSelection()) {
                event.accepted = true
            }
            return
        }
        if (event.key === Qt.Key_Backspace) {
            field.forceActiveFocus()
            if (field.text.length > 0)
                field.text = field.text.slice(0, -1)
            event.accepted = true
        } else if (event.text && event.text.length > 0 && event.text.charCodeAt(0) >= 32 && event.key !== Qt.Key_Delete) {
            field.forceActiveFocus()
            field.insert(field.cursorPosition, event.text)
            event.accepted = true
        }
    }

    AquaTalkCloud {
        id: embeddedCloud
        objectName: "aquaEmbeddedTalkCloud"
        anchors.fill: parent
        embeddedMode: true
        allowed: true
        outputWidth: Math.max(320, root.width)
        outputHeight: Math.max(360, root.height)
    }
}
