pragma ComponentBehavior: Bound
import QtQuick
import qs.modules.common
import qs.modules.abyss.looks
import "A2uiDataModel.js" as A2ui

// Top-level A2UI v1.0 Surface host mounted inside Companion assistant bubbles.
Item {
    id: root
    objectName: surfaceId ? ("a2uiSurface-" + surfaceId) : "a2uiSurface"
    property var surface: null
    property bool compactMode: width < 320
    property color colBoxSubtle: Qt.rgba(0.12, 0.14, 0.18, 0.90)
    property color colBoxLayer: Qt.rgba(0.15, 0.17, 0.22, 0.92)
    property color colBoxElevated: Qt.rgba(0.19, 0.21, 0.27, 0.94)
    property color colBoxHover: Qt.rgba(0.23, 0.26, 0.32, 0.95)

    signal serverEvent(string surfaceId, string eventName, var context)
    signal functionCallRequested(string callName, var args)
    signal textInputFocusChanged(bool focused)

    readonly property var createSurface: (surface && typeof surface === "object" && surface.version === "1.0" && surface.createSurface && typeof surface.createSurface === "object")
        ? surface.createSurface
        : null
    readonly property string surfaceId: createSurface ? String(createSurface.surfaceId ?? "") : ""
    readonly property var componentMap: createSurface ? A2ui.buildComponentMap(createSurface.components) : ({})
    readonly property bool validSurface: createSurface !== null && surfaceId.length > 0 && componentMap["root"] !== undefined

    property var localDataModel: ({})
    property string _boundSurfaceId: ""
    property string _lastModelSig: ""

    function _syncDataModel(): void {
        if (!createSurface) {
            _boundSurfaceId = "";
            _lastModelSig = "";
            localDataModel = ({});
            return;
        }
        const incoming = createSurface.dataModel ?? ({});
        let sig = "";
        try {
            sig = JSON.stringify(incoming);
        } catch (e) {
            sig = "";
        }
        if (surfaceId !== _boundSurfaceId || sig !== _lastModelSig) {
            _boundSurfaceId = surfaceId;
            _lastModelSig = sig;
            localDataModel = A2ui.deepClone(incoming);
        }
    }

    function applyDataPatches(patches): void {
        if (!Array.isArray(patches) || patches.length === 0)
            return;
        const nextModel = A2ui.applyPatches(localDataModel, patches);
        localDataModel = nextModel;
        try {
            _lastModelSig = JSON.stringify(nextModel);
        } catch (e) {
            _lastModelSig = "";
        }
    }

    onSurfaceChanged: _syncDataModel()
    Component.onCompleted: _syncDataModel()

    visible: validSurface
    implicitWidth: validSurface ? Math.max(80, width > 0 ? width : rootNode.implicitWidth) : 0
    implicitHeight: validSurface ? rootNode.implicitHeight : 0
    height: implicitHeight

    // Absorb pointer hover cursor locally so AquaTalkCloud's TextEdit IBeamCursor never bleeds over A2UI controls.
    HoverHandler {
        blocking: false
        cursorShape: Qt.ArrowCursor
    }

    A2uiNode {
        id: rootNode
        width: root.width > 0 ? root.width : 280
        componentId: root.validSurface ? "root" : ""
        componentMap: root.componentMap
        dataModel: root.localDataModel
        depth: 0
        compactMode: root.compactMode
        maxContentWidth: root.width > 0 ? root.width : 280
        colBoxSubtle: root.colBoxSubtle
        colBoxLayer: root.colBoxLayer
        colBoxElevated: root.colBoxElevated
        colBoxHover: root.colBoxHover
        onServerEvent: (ev, ctx) => {
            if (root.surfaceId)
                root.serverEvent(root.surfaceId, ev, ctx);
        }
        onFunctionCallRequested: (fn, args) => root.functionCallRequested(fn, args)
        onLocalDataChanged: (p, v) => {
            root.localDataModel = A2ui.setByPath(root.localDataModel, p, v);
        }
        onTextInputFocusChanged: f => root.textInputFocusChanged(f)
    }
}
