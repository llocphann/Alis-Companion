import QtQuick
import QtQuick.Shapes

Item {
    id: root
    objectName: "wullLaptopProp"
    required property var body
    property var pose: null
    property color accentColor: "#6be5ff"
    property color specularColor: "#d9fbff"
    property real lift: 0
    width: 76
    height: 92

    readonly property real laptopHinge: pose?.laptopHinge ?? 0
    readonly property real laptopSlide: pose?.laptopSlide ?? 0
    readonly property real laptopLift: pose?.laptopLift ?? 0
    readonly property real screenPulse: pose?.screenPulse ?? 0.55
    readonly property real hingeFactor: Math.max(0, Math.min(1.03, laptopHinge / 106))
    readonly property var deckAnchor: body
        ? body.project(0, -24 + laptopLift, 20 - laptopSlide * 0.45)
        : ({ x: 0, y: -24, z: 20, scale: 1 })

    Item {
        id: laptopRoot
        objectName: "wullLaptopRoot"
        width: 38
        height: 28
        x: 38 + root.deckAnchor.x - width * 0.5
        y: 46.14 - root.deckAnchor.y - height * 0.5 + root.lift
        scale: Math.max(0.84, Math.min(1.18, root.deckAnchor.scale))

        Item {
            id: laptopLid
            objectName: "wullLaptopLid"
            width: 30
            height: 4 + root.hingeFactor * 14
            x: 4
            y: 15 - height

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: 0.9
                    strokeColor: root.specularColor
                    fillColor: Qt.darker(root.accentColor, 1.85)
                    startX: 2; startY: 0
                    PathLine { x: 28; y: 0 }
                    PathLine { x: 29.5; y: laptopLid.height }
                    PathLine { x: 0.5; y: laptopLid.height }
                    PathLine { x: 2; y: 0 }
                }
                ShapePath {
                    strokeWidth: 0.5
                    strokeColor: Qt.alpha(root.specularColor, 0.85)
                    fillColor: Qt.alpha(root.accentColor, 0.28 + root.screenPulse * 0.46)
                    startX: 3.5; startY: Math.min(laptopLid.height * 0.45, 1.8)
                    PathLine { x: 26.5; y: Math.min(laptopLid.height * 0.45, 1.8) }
                    PathLine { x: 27.6; y: Math.max(laptopLid.height - 1.5, 2) }
                    PathLine { x: 2.4; y: Math.max(laptopLid.height - 1.5, 2) }
                    PathLine { x: 3.5; y: Math.min(laptopLid.height * 0.45, 1.8) }
                }
            }
            Rectangle {
                objectName: "wullLaptopEmblem"
                visible: root.hingeFactor > 0.35
                width: 4.5
                height: 4.5
                radius: 2.25
                anchors.centerIn: parent
                color: Qt.alpha(root.specularColor, 0.45 + root.screenPulse * 0.5)
            }
        }

        Rectangle {
            id: laptopHingeBar
            objectName: "wullLaptopHinge"
            x: 5
            y: 14.2
            width: 28
            height: 2
            radius: 1
            color: Qt.darker(root.accentColor, 1.55)
            border.width: 0.5
            border.color: root.specularColor
        }

        Item {
            id: laptopDeck
            objectName: "wullLaptopDeck"
            x: 1
            y: 15.2
            width: 36
            height: 11

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: 0.9
                    strokeColor: root.specularColor
                    fillColor: Qt.darker(root.accentColor, 1.45)
                    startX: 3.5; startY: 0.5
                    PathLine { x: 32.5; y: 0.5 }
                    PathLine { x: 35.5; y: 8.5 }
                    PathLine { x: 0.5; y: 8.5 }
                    PathLine { x: 3.5; y: 0.5 }
                }
                ShapePath {
                    strokeWidth: 0.55
                    strokeColor: Qt.alpha(root.specularColor, 0.72)
                    fillColor: Qt.darker(root.accentColor, 2.05)
                    startX: 5.5; startY: 1.8
                    PathLine { x: 30.5; y: 1.8 }
                    PathLine { x: 32.2; y: 5.8 }
                    PathLine { x: 3.8; y: 5.8 }
                    PathLine { x: 5.5; y: 1.8 }
                }
                ShapePath {
                    strokeWidth: 0.5
                    strokeColor: Qt.alpha(root.specularColor, 0.65)
                    fillColor: Qt.alpha(root.specularColor, 0.25)
                    startX: 14.5; startY: 6.3
                    PathLine { x: 21.5; y: 6.3 }
                    PathLine { x: 22.0; y: 8.0 }
                    PathLine { x: 14.0; y: 8.0 }
                    PathLine { x: 14.5; y: 6.3 }
                }
            }
        }
    }
}
