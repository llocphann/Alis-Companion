import QtQuick
import "../../../services"

DropArea {
    id:root
    required property Item actor
    property bool allowed:false
    property real dragLocalX:0
    property real dragLocalY:0
    anchors.fill:parent
    anchors.margins:-36
    // Keep this target alive while the accepted request starts its helper.
    // Helper startup must not change the target's input ownership mid-event.
    enabled:allowed && WullPocket.permitted && actor.visible && actor.inputReady && !actor.dragging
    // Never acknowledge MoveAction: a file manager could then delete its
    // source. This target receives references, never the original contents.
    function accepts(event): bool {
        return enabled && WullPocket.canSelect && event.hasUrls && (event.supportedActions & Qt.CopyAction)!==0
            && event.urls.length>0 && event.urls.length<=8
    }
    function updateDragPose(drag): void {
        const cx = Math.max(1, root.width * 0.5)
        const cy = Math.max(1, root.height * 0.5)
        root.dragLocalX = Math.max(-1, Math.min(1, (drag.x - cx) / (cx * 0.72)))
        root.dragLocalY = Math.max(-1, Math.min(1, (drag.y - cy) / (cy * 0.72)))
    }
    onEntered:drag=> {
        drag.accepted=false
        if(accepts(drag)) {
            drag.accept(Qt.CopyAction)
            updateDragPose(drag)
        }
    }
    onPositionChanged:drag=> {
        drag.accepted=false
        if(accepts(drag)) {
            drag.accept(Qt.CopyAction)
            updateDragPose(drag)
        }
    }
    onExited: {
        root.dragLocalX = 0
        root.dragLocalY = 0
    }
    onDropped:drop=> {
        drop.accepted=false
        const urls = Array.from(drop.urls, value => String(value))
        if (accepts(drop) && WullPocket.addFiles(urls, "drop")) {
            drop.accept(Qt.CopyAction)
            if (typeof actor.swallowPocketFiles === "function")
                actor.swallowPocketFiles(urls.length, drop.x + root.x - (actor.width - 76) * 0.5, drop.y + root.y - (actor.height - 92) * 0.5)
        }
    }
    Binding {
        target: root.actor
        property: "pocketDragActive"
        value: root.containsDrag
        when: !!root.actor && root.actor.hasOwnProperty("pocketDragActive")
    }
    Binding {
        target: root.actor
        property: "pocketDragX"
        value: root.dragLocalX
        when: !!root.actor && root.actor.hasOwnProperty("pocketDragX")
    }
    Binding {
        target: root.actor
        property: "pocketDragY"
        value: root.dragLocalY
        when: !!root.actor && root.actor.hasOwnProperty("pocketDragY")
    }
    Connections {
        target: WullPocket
        function onFilesSwallowed(count): void {
            if (root.actor && typeof root.actor.swallowPocketFiles === "function" && root.actor.swallowPhase >= 0.99)
                root.actor.swallowPocketFiles(count, 38, 6)
        }
    }
}
