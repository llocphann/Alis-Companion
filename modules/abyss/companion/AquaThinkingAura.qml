import QtQuick
import "AquaExpressions.js" as Expressions

Item {
    id: root
    objectName: "aquaThinkingAura"

    required property var body
    property color accentColor: "#6be5ff"
    property color specularColor: "#d9fbff"
    property real thinkingPhase: 0
    property real thinkingWave: 0
    property real thinkingBlend: 1
    property string thinkingStage: ""
    property int qualityLevel: 1
    property bool octopus: false
    property real floatOffset: 0

    width: 76
    height: 92

    readonly property bool isTool: thinkingStage === "tool"
    readonly property bool isDeep: thinkingStage === "deep"
    readonly property bool isDrafting: thinkingStage === "drafting"
    readonly property real orbitSpeedFactor: body ? body.orbitSpeedFactor : 1.25
    readonly property color trailTint: isTool
        ? "#68f5ff"
        : (isDeep ? "#b89cff" : (isDrafting ? "#ffe394" : Qt.lighter(accentColor, 1.38)))
    readonly property color trailCore: isTool
        ? "#e0ffff"
        : (isDeep ? "#f4eeff" : (isDrafting ? "#fff8d6" : "#f5fdff"))
    readonly property int trailBeadsTotal: body && body.bubblesEnabled ? Math.min(3, body.bubbleCount) * 2 : 0

    // Bubble-coupled micro comet trails replacing cluttered overhead rings.
    Item {
        id: bubbleTrails
        objectName: "aquaThinkingBubbleTrails"
        x: 0
        y: root.floatOffset
        width: root.width
        height: root.height
        opacity: Math.max(0, Math.min(1, root.thinkingBlend))

        Repeater {
            model: root.trailBeadsTotal
            Rectangle {
                id: trailBead
                required property int index
                readonly property int bubbleIdx: Math.floor(index / 2)
                readonly property int step: (index % 2) + 1
                readonly property real lagRad: step * (root.isTool ? 0.14 : (root.isDeep ? 0.13 : 0.11))
                readonly property var pt: root.body ? root.body.bubbleOrbitPoint(bubbleIdx, lagRad) : ({ x: 38, y: 46, depth: 0, wavePulse: 0.5 })
                readonly property real leadSize: Expressions.dropletSize[bubbleIdx] ?? 11
                width: Math.max(2.0, leadSize * (step === 1 ? 0.34 : 0.20))
                height: width
                radius: width * 0.5
                x: pt.x - width * 0.5
                y: pt.y - height * 0.5
                z: pt.depth < 0 ? -1 : 1
                color: step === 1 ? root.trailCore : root.trailTint
                border.width: step === 1 ? 0.7 : 0
                border.color: Qt.alpha(root.trailTint, 0.82)
                opacity: (pt.depth < 0 ? 0.24 : (step === 1 ? 0.68 : 0.36)) * (0.65 + 0.35 * pt.wavePulse)

                Rectangle {
                    visible: trailBead.step === 1
                    anchors.centerIn: parent
                    width: Math.max(1.2, parent.width * 0.45)
                    height: width
                    radius: width * 0.5
                    color: "#ffffff"
                    opacity: 0.85
                }
            }
        }
    }
}
