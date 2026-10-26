pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Dialogs
import Quickshell
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.widgets
import qs.modules.settings

ColumnLayout {
    id:root
    spacing:8
    property bool confirmClear:false
    focus:true
    Component.onCompleted: Qt.callLater(() => { if (root.visible) root.forceActiveFocus() })
    Keys.onEscapePressed: WullPocket.close()
    DropArea {
        parent:root
        anchors.fill:parent
        enabled:WullPocket.canSelect
        function accepts(event): bool {
            return enabled && event.hasUrls && (event.supportedActions & Qt.CopyAction)!==0
                && event.urls.length>0 && event.urls.length<=8
        }
        onEntered:drag=> {drag.accepted=false;if(accepts(drag))drag.accept(Qt.CopyAction)}
        onPositionChanged:drag=> {drag.accepted=false;if(accepts(drag))drag.accept(Qt.CopyAction)}
        onDropped:drop=> {
            drop.accepted=false
            if (accepts(drop) && WullPocket.addFiles(Array.from(drop.urls,value=>String(value)),"drop"))
                drop.accept(Qt.CopyAction)
        }
    }
    RowLayout {
        Layout.fillWidth:true
        spacing:6
        RippleButton {
            id:chooseBtn
            objectName:"wullPocketChoose"
            Layout.preferredWidth:30;Layout.preferredHeight:30
            implicitWidth:30;implicitHeight:30
            buttonRadius:15
            enabled:WullPocket.canSelect
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
        StyledText {
            Layout.fillWidth:true
            horizontalAlignment:Text.AlignHCenter
            text:Translation.tr("Pocket")+" · "+WullPocket.items.length+"/32"
            font.weight:Font.DemiBold
        }
        RippleButton {
            id:clearBtn
            objectName:"wullPocketClear"
            Layout.preferredWidth:30;Layout.preferredHeight:30
            implicitWidth:30;implicitHeight:30
            buttonRadius:15
            enabled:WullPocket.canSelect && WullPocket.items.length>0
            Accessible.name:root.confirmClear ? Translation.tr("Confirm clear") : Translation.tr("Clear")
            onClicked: {if(root.confirmClear){WullPocket.clear(true);root.confirmClear=false}else root.confirmClear=true}
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
    StyledText {
        visible:root.confirmClear;Layout.fillWidth:true
        text:Translation.tr("Clear saved references? Original files will stay where they are.")
        wrapMode:Text.WordWrap
        font.pixelSize:Appearance.font.pixelSize.smallest
        color:Appearance.colors.colSubtext
    }
    ListView {
        id:list;objectName:"wullPocketList";Layout.fillWidth:true;Layout.fillHeight:true
        clip:true;spacing:4;model:WullPocket.items
        delegate:RowLayout {
            id:row
            required property var modelData
            width:list.width;spacing:6
            ColumnLayout {
                Layout.fillWidth:true;Layout.minimumWidth:0;spacing:1
                StyledText {Layout.fillWidth:true;text:row.modelData.name;textFormat:Text.PlainText;elide:Text.ElideMiddle;font.pixelSize:Appearance.font.pixelSize.small}
                StyledText {
                    Layout.fillWidth:true;text:row.modelData.path;textFormat:Text.PlainText;elide:Text.ElideLeft
                    font.pixelSize:Appearance.font.pixelSize.smallest;color:Appearance.colors.colSubtext
                }
            }
            RippleButton {
                id:openItemBtn
                Layout.preferredWidth:28;Layout.preferredHeight:28
                implicitWidth:28;implicitHeight:28
                buttonRadius:14
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
                objectName:"wullPocketRemove-"+row.modelData.id
                Layout.preferredWidth:28;Layout.preferredHeight:28
                implicitWidth:28;implicitHeight:28
                buttonRadius:14
                enabled:WullPocket.canSelect
                Accessible.name:Translation.tr("Remove")
                onClicked: {root.confirmClear=false;WullPocket.remove(row.modelData.id)}
                StyledToolTip {
                    extraVisibleCondition:removeItemBtn.buttonHovered
                    text:Translation.tr("Remove")
                }
                contentItem:MaterialSymbol {
                    anchors.centerIn:parent
                    text:"close"
                    iconSize:16
                    color:Appearance.colors.colSubtext
                }
            }
        }
        StyledText {
            anchors.centerIn:parent;width:parent.width
            visible:!WullPocket.busy && WullPocket.items.length===0
            text:Translation.tr("Pocket is empty.");horizontalAlignment:Text.AlignHCenter;wrapMode:Text.WordWrap
            color:Appearance.colors.colSubtext
        }
    }
    StyledText {
        Layout.fillWidth:true
        visible:WullPocket.busy || WullPocket.notice.length>0
        text:WullPocket.busy ? Translation.tr("Updating references…") : Translation.tr(WullPocket.notice)
        wrapMode:Text.WordWrap;font.pixelSize:Appearance.font.pixelSize.smaller
        Accessible.role:Accessible.StaticText
    }
    FileDialog {
        id:picker;objectName:"wullPocketFileDialog"
        title:Translation.tr("Choose files for Pocket")
        fileMode:FileDialog.OpenFiles
        options:FileDialog.ReadOnly | FileDialog.DontResolveSymlinks
        onAccepted:if(WullPocket.permitted && WullPocket.opened)
            WullPocket.addFiles(Array.from(selectedFiles,value=>String(value)),"picker")
    }
    SettingsNativeDialogGuard {dialog:picker;dialogKey:"companion-pocket-picker"}
    Connections {
        target:WullPocket
        function onPermittedChanged():void {if(!WullPocket.permitted)picker.close()}
        function onOpenedChanged():void {if(!WullPocket.opened)picker.close()}
    }
}
