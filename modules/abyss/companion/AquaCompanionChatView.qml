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

    function syncSidebarState(): void {
        AquaMind.sidebarChatOpen = root.visible
        if (root.visible)
            AquaMind.ensureWorkspaceLoaded()
    }

    onVisibleChanged: root.syncSidebarState()
    Component.onCompleted: root.syncSidebarState()
    Component.onDestruction: {
        AquaMind.sidebarChatOpen = false
    }

    function handleInput(event): void {
        if (embeddedCloud.hasSidebarInputFocus || embeddedCloud.editorMode)
            return
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
