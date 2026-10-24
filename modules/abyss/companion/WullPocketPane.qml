pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Dialogs
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.widgets
import qs.modules.settings

ColumnLayout {
    id:root
    spacing:8
    property bool confirmClear:false
    RowLayout {
        Layout.fillWidth:true
        StyledText {Layout.fillWidth:true;text:Translation.tr("Pocket");font.weight:Font.DemiBold}
        DialogButton {objectName:"wullPocketClose";buttonText:Translation.tr("Close");onClicked:WullPocket.close()}
    }
    StyledText {
        Layout.fillWidth:true
        text:Translation.tr("Keep up to 32 local file references. Choose eight at a time, or drop files onto your companion. File contents stay private.")
        wrapMode:Text.WordWrap;font.pixelSize:Appearance.font.pixelSize.smaller
    }
    RowLayout {
        Layout.fillWidth:true
        DialogButton {
            objectName:"wullPocketChoose";buttonText:Translation.tr("Choose files")
            enabled:WullPocket.canSelect
            onClicked: {root.confirmClear=false;picker.open()}
        }
        StyledText {Layout.fillWidth:true;text:WullPocket.items.length+" / 32";horizontalAlignment:Text.AlignRight}
        DialogButton {
            objectName:"wullPocketClear";buttonText:root.confirmClear ? Translation.tr("Confirm clear") : Translation.tr("Clear")
            enabled:WullPocket.canSelect && WullPocket.items.length>0
            onClicked: {if(root.confirmClear){WullPocket.clear(true);root.confirmClear=false}else root.confirmClear=true}
        }
    }
    StyledText {
        visible:root.confirmClear;Layout.fillWidth:true
        text:Translation.tr("Clear saved references? Original files will stay where they are.")
        wrapMode:Text.WordWrap
    }
    ListView {
        id:list;objectName:"wullPocketList";Layout.fillWidth:true;Layout.fillHeight:true
        clip:true;spacing:6;model:WullPocket.items
        delegate:RowLayout {
            id:row
            required property var modelData
            width:list.width;spacing:6
            ColumnLayout {
                Layout.fillWidth:true;Layout.minimumWidth:0;spacing:2
                StyledText {Layout.fillWidth:true;text:row.modelData.name;textFormat:Text.PlainText;elide:Text.ElideMiddle}
                StyledText {
                    Layout.fillWidth:true;text:row.modelData.path;textFormat:Text.PlainText;elide:Text.ElideLeft
                    font.pixelSize:Appearance.font.pixelSize.smallest;color:Appearance.colors.colSubtext
                }
                StyledText {
                    text:row.modelData.size+" bytes";font.pixelSize:Appearance.font.pixelSize.smallest
                    color:Appearance.colors.colSubtext
                }
            }
            DialogButton {
                objectName:"wullPocketRemove-"+row.modelData.id;buttonText:Translation.tr("Remove")
                enabled:WullPocket.canSelect
                onClicked: {root.confirmClear=false;WullPocket.remove(row.modelData.id)}
            }
        }
        StyledText {
            anchors.centerIn:parent;width:parent.width
            visible:!WullPocket.busy && WullPocket.items.length===0
            text:Translation.tr("Pocket is empty.");horizontalAlignment:Text.AlignHCenter;wrapMode:Text.WordWrap
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
