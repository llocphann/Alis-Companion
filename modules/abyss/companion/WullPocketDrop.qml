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
        const urls = Array.from(drop.urls, value => String(value))
        if (accepts(drop) && WullPocket.addFiles(urls, "drop")) {
            drop.accept(Qt.CopyAction)
            if (typeof actor.swallowPocketFiles === "function")
                actor.swallowPocketFiles(urls.length, drop.x - (actor.width - 76) * 0.5, drop.y - (actor.height - 92) * 0.5)
        }
    }
    Binding {
        target: root.actor
        property: "pocketDragActive"
        value: root.containsDrag
        when: !!root.actor && root.actor.hasOwnProperty("pocketDragActive")
    }
    Connections {
        target: WullPocket
        function onFilesSwallowed(count): void {
            if (root.actor && typeof root.actor.swallowPocketFiles === "function" && root.actor.swallowPhase >= 0.99)
                root.actor.swallowPocketFiles(count, 38, 6)
        }
    }
}
