pragma ComponentBehavior: Bound
import QtQuick

// Bounded runtime-loaded slot for recursive A2UI v1.0 child nodes (zero Qt.createQmlObject, zero QML compile-time recursion).
Item {
    id: slot
    property string componentId: ""
    property var componentMap: ({})
    property var dataModel: ({})
    property var scopeData: null
    property string scopePath: ""
    property int depth: 0
    property bool compactMode: false
    property real maxContentWidth: 280
    property color colBoxSubtle: Qt.rgba(0.12, 0.14, 0.18, 0.90)
    property color colBoxLayer: Qt.rgba(0.15, 0.17, 0.22, 0.92)
    property color colBoxElevated: Qt.rgba(0.19, 0.21, 0.27, 0.94)
    property color colBoxHover: Qt.rgba(0.23, 0.26, 0.32, 0.95)
    property color colTextPrimary: "#e8eefc"
    property color colTextMuted: "#9aa6c0"
    property color colAccent: "#7db8ff"
    property color colPrimaryBg: "#7db8ff"
    property color colOnPrimary: "#0b1220"
    property color colErrorBg: "#e05a5a"
    property color colOnError: "#ffffff"
    property color colToggledBg: Qt.rgba(0.49, 0.72, 1.0, 0.26)

    signal serverEvent(string eventName, var context)
    signal functionCallRequested(string callName, var args)
    signal localDataChanged(string path, var value)
    signal textInputFocusChanged(bool focused)

    readonly property bool canMount: depth <= 6 && componentId.length > 0 && componentMap && componentMap[componentId] !== undefined
    visible: canMount && (!childLoader.item || Boolean(childLoader.item.nodeVisible))
    implicitWidth: visible && childLoader.item ? childLoader.item.implicitWidth : 0
    implicitHeight: visible && childLoader.item ? childLoader.item.implicitHeight : 0

    Loader {
        id: childLoader
        width: slot.width > 0 ? slot.width : implicitWidth
        active: slot.canMount
        source: active ? Qt.resolvedUrl("A2uiNode.qml") : ""
        onLoaded: {
            if (!item)
                return;
            item.dataModel = Qt.binding(() => slot.dataModel);
            item.scopeData = Qt.binding(() => slot.scopeData);
            item.scopePath = Qt.binding(() => slot.scopePath);
            item.depth = Qt.binding(() => slot.depth);
            item.compactMode = Qt.binding(() => slot.compactMode);
            item.maxContentWidth = Qt.binding(() => slot.maxContentWidth);
            item.colBoxSubtle = Qt.binding(() => slot.colBoxSubtle);
            item.colBoxLayer = Qt.binding(() => slot.colBoxLayer);
            item.colBoxElevated = Qt.binding(() => slot.colBoxElevated);
            item.colBoxHover = Qt.binding(() => slot.colBoxHover);
            item.colTextPrimary = Qt.binding(() => slot.colTextPrimary);
            item.colTextMuted = Qt.binding(() => slot.colTextMuted);
            item.colAccent = Qt.binding(() => slot.colAccent);
            item.colPrimaryBg = Qt.binding(() => slot.colPrimaryBg);
            item.colOnPrimary = Qt.binding(() => slot.colOnPrimary);
            item.colErrorBg = Qt.binding(() => slot.colErrorBg);
            item.colOnError = Qt.binding(() => slot.colOnError);
            item.colToggledBg = Qt.binding(() => slot.colToggledBg);
            item.componentMap = Qt.binding(() => slot.componentMap);
            item.componentId = Qt.binding(() => slot.componentId);
        }
    }

    Connections {
        target: childLoader.item
        ignoreUnknownSignals: true
        function onServerEvent(ev, ctx): void {
            slot.serverEvent(ev, ctx);
        }
        function onFunctionCallRequested(fn, args): void {
            slot.functionCallRequested(fn, args);
        }
        function onLocalDataChanged(p, v): void {
            slot.localDataChanged(p, v);
        }
        function onTextInputFocusChanged(f): void {
            slot.textInputFocusChanged(f);
        }
    }
}
