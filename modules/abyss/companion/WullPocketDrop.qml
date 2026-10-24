import QtQuick
import "../../../services"

DropArea {
    id:root
    required property Item actor
    property bool allowed:false
    anchors.fill:parent
    // Keep this target alive while the accepted request starts its helper.
    // Helper startup must not change the target's input ownership mid-event.
    enabled:allowed && WullPocket.permitted && actor.visible && actor.inputReady && !actor.dragging
    // Never acknowledge MoveAction: a file manager could then delete its
    // source. This target receives references, never the original contents.
    function accepts(event): bool {
        return enabled && WullPocket.canSelect && event.hasUrls && (event.supportedActions & Qt.CopyAction)!==0
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
