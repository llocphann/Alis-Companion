pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Dialogs
import Quickshell
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.functions
import qs.modules.common.widgets
import qs.modules.abyss.looks
import qs.modules.settings

Item {
    id:root
    property Item actor: null
    property bool embeddedMode: false
    property bool confirmClear:false
    property string searchQuery: ""
    property var pinnedPaths: ({})
    property var undoClearPaths: []
    function isImagePath(p): bool {
        const s = String(p ?? "").toLowerCase()
        return /\.(png|jpe?g|webp|gif|bmp|svg)$/.test(s)
    }
    function isPinned(p): bool {
        return Boolean(root.pinnedPaths && root.pinnedPaths[String(p ?? "")])
    }
    function togglePin(p): void {
        const key = String(p ?? "")
        if (!key) return
        const next = Object.assign({}, root.pinnedPaths)
        if (next[key]) delete next[key]
        else next[key] = true
        root.pinnedPaths = next
    }
    readonly property var visibleItems: {
        const raw = Array.isArray(AquaPocket.items) ? AquaPocket.items : []
        const q = String(root.searchQuery ?? "").trim().toLowerCase()
        const filtered = q.length > 0
            ? raw.filter(it => String(it?.name ?? "").toLowerCase().includes(q) || String(it?.path ?? "").toLowerCase().includes(q))
            : raw.slice()
        filtered.sort((a, b) => {
            const pa = root.isPinned(a?.path) ? 1 : 0
            const pb = root.isPinned(b?.path) ? 1 : 0
            return pb - pa
        })
        return filtered
    }
    Timer {
        id: undoClearTimer
        interval: 8000
        repeat: false
        onTriggered: root.undoClearPaths = []
    }
    signal closeRequested()
    function closePane(): void {
        if (root.embeddedMode)
            root.closeRequested()
        else
            AquaPocket.close()
    }
    readonly property bool hovered: root.visible && pocketPaneHover.hovered
    readonly property color colTonalBase: ColorUtils.applyAlpha(
        Qt.color(Appearance.colors.colLayer1Base).valid
            ? Appearance.colors.colLayer1Base
            : Appearance.colors.colLayer0Base,
        1.0
    )
    readonly property real tonalDir: Qt.color(root.colTonalBase).hslLightness > 0.86 ? -1 : 1
    readonly property color colBoxLayer: ColorUtils.applyAlpha(
        ColorUtils.tonalStep(root.colTonalBase, root.tonalDir * 0.026, 1.05),
        0.92
    )
    readonly property color colBoxBorder: ColorUtils.applyAlpha(
        ColorUtils.tonalStep(root.colTonalBase, root.tonalDir * 0.22, 1.10),
        0.14
    )
    implicitHeight: layoutCol.implicitHeight
    focus:true
    Component.onCompleted: Qt.callLater(() => { if (root.visible) root.forceActiveFocus() })
    onVisibleChanged: if (!root.visible) picker.close()
    Keys.onEscapePressed: root.closePane()
    HoverHandler {
        id: pocketPaneHover
        blocking: false
    }
    DropArea {
        id: paneDrop
        anchors.fill:parent
        enabled:AquaPocket.canSelect
        function accepts(event): bool {
            return enabled && event.hasUrls && (event.supportedActions & Qt.CopyAction)!==0
                && event.urls.length>0 && event.urls.length<=8
        }
        function syncActorDrag(drag, active): void {
            if (!root.actor) return
            root.actor.pocketDragActive = active
            if (active && drag) {
                const cx = Math.max(1, paneDrop.width * 0.5)
                const cy = Math.max(1, paneDrop.height * 0.5)
                root.actor.pocketDragX = Math.max(-1, Math.min(1, (drag.x - cx) / cx))
                root.actor.pocketDragY = Math.max(-1, Math.min(1, (drag.y - cy) / cy))
            } else {
                root.actor.pocketDragX = 0
                root.actor.pocketDragY = 0
            }
        }
        onEntered:drag=> {
            drag.accepted=false
            if(accepts(drag)) {
                drag.accept(Qt.CopyAction)
                syncActorDrag(drag, true)
            }
        }
        onPositionChanged:drag=> {
            drag.accepted=false
            if(accepts(drag)) {
                drag.accept(Qt.CopyAction)
                syncActorDrag(drag, true)
            }
        }
        onExited: syncActorDrag(null, false)
        onDropped:drop=> {
            drop.accepted=false
            syncActorDrag(null, false)
            if (accepts(drop) && AquaPocket.addFiles(Array.from(drop.urls,value=>String(value)),"drop"))
                drop.accept(Qt.CopyAction)
        }
    }
    ColumnLayout {
        id: layoutCol
        anchors.fill: parent
        spacing: 8
        RowLayout {
            Layout.fillWidth:true
            Layout.alignment: Qt.AlignTop
            spacing:6
            RippleButton {
                id:chooseBtn
                objectName:"aquaPocketChoose"
                Layout.preferredWidth:30;Layout.preferredHeight:30
                implicitWidth:30;implicitHeight:30
                buttonRadius:15
                enabled:AquaPocket.canSelect
                Accessible.name:Translation.tr("Choose files")
                onClicked: {root.confirmClear=false;picker.open()}
                StyledToolTip {
                    extraVisibleCondition:chooseBtn.buttonHovered
                    text:Translation.tr("Choose files")
                }
                contentItem:MaterialSymbol {
                    anchors.centerIn:parent
                    text:"add"
                    iconSize:18
                    color:Appearance.colors.colPrimary
                }
            }
            Item {
                Layout.fillWidth:true
                Layout.preferredHeight:30
                Rectangle {
                    anchors.centerIn:parent
                    width:headerBadgeRow.implicitWidth + 18
                    height:26
                    radius:13
                    color:Qt.alpha(AbyssStyle.accent, 0.12)
                    border.width:1
                    border.color:Qt.alpha(AbyssStyle.accent, 0.24)
                    RowLayout {
                        id:headerBadgeRow
                        anchors.centerIn:parent
                        spacing:5
                        MaterialSymbol {
                            text:"inventory_2"
                            iconSize:16
                            color:AbyssStyle.accent
                        }
                        StyledText {
                            text:AquaPocket.items.length+"/32"
                            font.pixelSize:Appearance.font.pixelSize.smaller
                            font.weight:Font.DemiBold
                            color:AbyssStyle.textColor
                        }
                    }
                }
            }
            RippleButton {
                id:clearBtn
                objectName:"aquaPocketClear"
                Layout.preferredWidth:30;Layout.preferredHeight:30
                implicitWidth:30;implicitHeight:30
                buttonRadius:15
                enabled:AquaPocket.canSelect && AquaPocket.items.length>0
                Accessible.name:root.confirmClear ? Translation.tr("Confirm clear") : Translation.tr("Clear")
                onClicked: {
                    if (root.confirmClear) {
                        const saved = (AquaPocket.items ?? []).map(it => String(it?.path ?? "")).filter(Boolean).slice(0, 8)
                        if (saved.length > 0) {
                            root.undoClearPaths = saved
                            undoClearTimer.restart()
                        }
                        AquaPocket.clear(true)
                        root.confirmClear = false
                    } else {
                        root.confirmClear = true
                    }
                }
                StyledToolTip {
                    extraVisibleCondition:clearBtn.buttonHovered
                    text:root.confirmClear ? Translation.tr("Confirm clear") : Translation.tr("Clear")
                }
                contentItem:MaterialSymbol {
                    anchors.centerIn:parent
                    text:root.confirmClear ? "check" : "delete_sweep"
                    iconSize:18
                    color:root.confirmClear ? Appearance.colors.colError : Appearance.colors.colOnLayer1
                }
            }
        }
        Rectangle {
            visible: AquaPocket.items.length > 1
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            radius: 9
            color: root.colBoxLayer
            border.width: 1
            border.color: root.colBoxBorder
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 6
                spacing: 5
                MaterialSymbol {
                    text: "search"
                    iconSize: 14
                    color: Appearance.colors.colSubtext
                }
                TextInput {
                    id: pocketSearchInput
                    Layout.fillWidth: true
                    color: AbyssStyle.textColor
                    font.pixelSize: Appearance.font.pixelSize.smaller
                    selectByMouse: true
                    clip: true
                    onTextChanged: root.searchQuery = text
                    onPreeditTextChanged: root.searchQuery = preeditText ? (text.slice(0, cursorPosition) + preeditText + text.slice(cursorPosition)) : text
                    StyledText {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: pocketSearchInput.text.length === 0 && !pocketSearchInput.preeditText
                        text: Translation.tr("Filter files...")
                        font.pixelSize: Appearance.font.pixelSize.smaller
                        color: Appearance.colors.colSubtext
                    }
                }
            }
        }
        Rectangle {
            visible: root.undoClearPaths.length > 0 && AquaPocket.items.length === 0
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            radius: 9
            color: Qt.alpha(AbyssStyle.accent, 0.14)
            border.width: 1
            border.color: Qt.alpha(AbyssStyle.accent, 0.30)
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 6
                spacing: 6
                StyledText {
                    Layout.fillWidth: true
                    text: Translation.tr("Pocket cleared")
                    font.pixelSize: Appearance.font.pixelSize.smallest
                    color: AbyssStyle.textColor
                }
                RippleButton {
                    objectName: "aquaPocketUndoClear"
                    Layout.preferredHeight: 22
                    implicitWidth: undoLbl.implicitWidth + 14
                    buttonRadius: 11
                    focusPolicy: Qt.NoFocus
                    colBackground: Qt.alpha(AbyssStyle.accent, 0.24)
                    onClicked: {
                        const urls = root.undoClearPaths.map(p => p.startsWith("file://") ? p : ("file://" + p))
                        root.undoClearPaths = []
                        undoClearTimer.stop()
                        AquaPocket.addFiles(urls, "picker", false)
                    }
                    contentItem: Item {
                        StyledText {
                            id: undoLbl
                            anchors.centerIn: parent
                            horizontalAlignment: Text.AlignHCenter
                            text: Translation.tr("Undo")
                            font.pixelSize: Appearance.font.pixelSize.smallest
                            font.weight: Font.DemiBold
                            color: AbyssStyle.accent
                        }
                    }
                }
            }
        }
        ListView {
            id:list;objectName:"aquaPocketList";Layout.fillWidth:true;Layout.fillHeight:true
            clip:true;spacing:5;model:root.visibleItems
            delegate:Rectangle {
                id:row
                required property var modelData
                width:list.width
                height:40
                radius:10
                color:root.colBoxLayer
                border.width:1
                border.color:root.isPinned(row.modelData.path) ? Qt.alpha(AbyssStyle.accent, 0.38) : root.colBoxBorder
                RowLayout {
                    anchors.fill:parent
                    anchors.leftMargin:8
                    anchors.rightMargin:6
                    spacing:6
                    Item {
                        Layout.preferredWidth: 22
                        Layout.preferredHeight: 22
                        Rectangle {
                            anchors.fill: parent
                            radius: 5
                            clip: true
                            color: "transparent"
                            visible: root.isImagePath(row.modelData.path)
                            Image {
                                anchors.fill: parent
                                source: root.isImagePath(row.modelData.path) ? ("file://" + String(row.modelData.path)) : ""
                                asynchronous: true
                                cache: true
                                sourceSize.width: 44
                                sourceSize.height: 44
                                fillMode: Image.PreserveAspectCrop
                            }
                        }
                        MaterialSymbol {
                            anchors.centerIn: parent
                            visible: !root.isImagePath(row.modelData.path)
                            text: "description"
                            iconSize: 16
                            color: AbyssStyle.accent
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth:true;Layout.minimumWidth:0;spacing:0
                        StyledText {
                            Layout.fillWidth:true
                            text:row.modelData.name
                            textFormat:Text.PlainText
                            elide:Text.ElideMiddle
                            font.pixelSize:Appearance.font.pixelSize.small
                        }
                        StyledText {
                            Layout.fillWidth:true
                            text:row.modelData.path
                            textFormat:Text.PlainText
                            elide:Text.ElideLeft
                            font.pixelSize:Appearance.font.pixelSize.smallest
                            color:Appearance.colors.colSubtext
                        }
                    }
                    RippleButton {
                        id:pinItemBtn
                        Layout.preferredWidth:24;Layout.preferredHeight:24
                        implicitWidth:24;implicitHeight:24
                        buttonRadius:12
                        focusPolicy:Qt.NoFocus
                        Accessible.name:root.isPinned(row.modelData.path) ? Translation.tr("Unpin") : Translation.tr("Pin")
                        onClicked:root.togglePin(row.modelData.path)
                        StyledToolTip {
                            extraVisibleCondition:pinItemBtn.buttonHovered
                            text:root.isPinned(row.modelData.path) ? Translation.tr("Unpin") : Translation.tr("Pin")
                        }
                        contentItem:MaterialSymbol {
                            anchors.centerIn:parent
                            text:"keep"
                            fill:root.isPinned(row.modelData.path) ? 1 : 0
                            iconSize:14
                            color:root.isPinned(row.modelData.path) ? AbyssStyle.accent : Appearance.colors.colSubtext
                        }
                    }
                    RippleButton {
                        id:sendToChatBtn
                        objectName:"aquaPocketSendToChat-"+row.modelData.id
                        Layout.preferredWidth:26;Layout.preferredHeight:26
                        implicitWidth:26;implicitHeight:26
                        buttonRadius:13
                        enabled:!!row.modelData.path
                        Accessible.name:"Send to Chat"
                        onClicked: {
                            root.confirmClear=false
                            AquaMind.attachFile(row.modelData.path)
                            root.closePane()
                            if (!root.embeddedMode)
                                AquaMind.openChat()
                        }
                        StyledToolTip {
                            extraVisibleCondition:sendToChatBtn.buttonHovered
                            text:"Send to Chat"
                        }
                        contentItem:MaterialSymbol {
                            anchors.centerIn:parent
                            text:"chat"
                            iconSize:15
                            color:Appearance.colors.colPrimary
                        }
                    }
                    RippleButton {
                        id:openItemBtn
                        Layout.preferredWidth:26;Layout.preferredHeight:26
                        implicitWidth:26;implicitHeight:26
                        buttonRadius:13
                        enabled:!!row.modelData.path
                        Accessible.name:"Open"
                        onClicked:Quickshell.execDetached(["xdg-open",row.modelData.path])
                        StyledToolTip {
                            extraVisibleCondition:openItemBtn.buttonHovered
                            text:"Open"
                        }
                        contentItem:MaterialSymbol {
                            anchors.centerIn:parent
                            text:"open_in_new"
                            iconSize:15
                            color:Appearance.colors.colSubtext
                        }
                    }
                    RippleButton {
                        id:removeItemBtn
                        objectName:"aquaPocketRemove-"+row.modelData.id
                        Layout.preferredWidth:26;Layout.preferredHeight:26
                        implicitWidth:26;implicitHeight:26
                        buttonRadius:13
                        enabled:AquaPocket.canSelect
                        Accessible.name:Translation.tr("Remove")
                        onClicked: {root.confirmClear=false;AquaPocket.remove(row.modelData.id)}
                        StyledToolTip {
                            extraVisibleCondition:removeItemBtn.buttonHovered
                            text:Translation.tr("Remove")
                        }
                        contentItem:MaterialSymbol {
                            anchors.centerIn:parent
                            text:"close"
                            iconSize:15
                            color:Appearance.colors.colSubtext
                        }
                    }
                }
            }
            ColumnLayout {
                anchors.centerIn:parent
                visible:!AquaPocket.busy && AquaPocket.items.length===0
                spacing:4
                MaterialSymbol {
                    Layout.alignment:Qt.AlignHCenter
                    text:"place_item"
                    iconSize:26
                    color:Qt.alpha(AbyssStyle.textColorMuted, 0.7)
                }
            }
        }
    }
    FileDialog {
        id:picker;objectName:"aquaPocketFileDialog"
        title:Translation.tr("Choose files for Pocket")
        fileMode:FileDialog.OpenFiles
        options:FileDialog.ReadOnly | FileDialog.DontResolveSymlinks
        onAccepted:if(AquaPocket.permitted && (root.embeddedMode || AquaPocket.opened))
            AquaPocket.addFiles(Array.from(selectedFiles,value=>String(value)),"picker",!root.embeddedMode)
    }
    SettingsNativeDialogGuard {dialog:picker;dialogKey:"companion-pocket-picker"}
    Connections {
        target:AquaPocket
        function onPermittedChanged():void {if(!AquaPocket.permitted)picker.close()}
        function onOpenedChanged():void {if(!root.embeddedMode && !AquaPocket.opened)picker.close()}
    }
}
