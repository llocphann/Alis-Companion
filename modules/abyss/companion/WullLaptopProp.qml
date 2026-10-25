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
    readonly property real typingPulse: Math.max(
        Math.abs((pose?.arm0Z ?? -14) + 14) / 3,
        Math.abs((pose?.arm1Z ?? -14) + 14) / 3,
        (pose?.laptopTap0 ?? 0) / 3,
        (pose?.laptopTap1 ?? 0) / 3
    )
    readonly property real hingeFactor: Math.max(0, Math.min(1.03, laptopHinge / 106))
    readonly property var deckAnchor: body
        ? body.project(0, -24 + laptopLift, 20 - laptopSlide * 0.45)
        : ({ x: 0, y: -24, z: 20, scale: 1 })

    Item {
        id: laptopRoot
        objectName: "wullLaptopRoot"
        width: 44
        height: 32
        x: 38 + root.deckAnchor.x - width * 0.5
        y: 45.5 - root.deckAnchor.y - height * 0.5 + root.lift
        scale: Math.max(0.88, Math.min(1.2, root.deckAnchor.scale ?? 1.05))

        Rectangle {
            x: 3
            y: 23.5
            width: 38
            height: 6
            radius: 3
            color: "#66020812"
        }

        Item {
            id: laptopLid
            objectName: "wullLaptopLid"
            width: 34
            height: 4.5 + root.hingeFactor * 15.5
            x: 5
            y: 17 - height

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: 1.15
                    strokeColor: "#eefcff"
                    fillColor: "#102132"
                    startX: 2.2; startY: 0
                    PathLine { x: 31.8; y: 0 }
                    PathLine { x: 33.4; y: laptopLid.height }
                    PathLine { x: 0.6; y: laptopLid.height }
                    PathLine { x: 2.2; y: 0 }
                }
                ShapePath {
                    strokeWidth: 0.7
                    strokeColor: Qt.alpha(root.specularColor, 0.92)
                    fillColor: Qt.rgba(0.12, 0.34, 0.50, 0.88 + root.screenPulse * 0.12)
                    startX: 3.8; startY: Math.min(laptopLid.height * 0.45, 2.0)
                    PathLine { x: 30.2; y: Math.min(laptopLid.height * 0.45, 2.0) }
                    PathLine { x: 31.4; y: Math.max(laptopLid.height - 1.6, 2.4) }
                    PathLine { x: 2.6; y: Math.max(laptopLid.height - 1.6, 2.4) }
                    PathLine { x: 3.8; y: Math.min(laptopLid.height * 0.45, 2.0) }
                }
            }
            Column {
                visible: root.hingeFactor > 0.45
                x: 6
                y: 4
                spacing: 1.6
                Rectangle {
                    width: 11 + root.typingPulse * 6
                    height: 1.4
                    radius: 0.7
                    color: "#9ef6ff"
                }
                Rectangle {
                    width: 16 - root.typingPulse * 4
                    height: 1.4
                    radius: 0.7
                    color: "#ffd584"
                }
                Rectangle {
                    width: 9 + root.screenPulse * 7
                    height: 1.4
                    radius: 0.7
                    color: "#7ef7c8"
                }
            }
            Rectangle {
                objectName: "wullLaptopEmblem"
                visible: root.hingeFactor > 0.35
                width: 5.2
                height: 5.2
                radius: 2.6
                anchors.centerIn: parent
                color: Qt.alpha("#ffffff", 0.68 + root.screenPulse * 0.3)
                border.width: 0.8
                border.color: root.accentColor
            }
        }

        Rectangle {
            id: laptopHingeBar
            objectName: "wullLaptopHinge"
            x: 6
            y: 16.2
            width: 32
            height: 2.2
            radius: 1.1
            color: "#1b3348"
            border.width: 0.65
            border.color: "#eefcff"
        }

        Item {
            id: laptopDeck
            objectName: "wullLaptopDeck"
            x: 1
            y: 17.2
            width: 42
            height: 12.5

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: 1.1
                    strokeColor: "#eefcff"
                    fillColor: "#244662"
                    startX: 4.0; startY: 0.5
                    PathLine { x: 38.0; y: 0.5 }
                    PathLine { x: 41.4; y: 9.6 }
                    PathLine { x: 0.6; y: 9.6 }
                    PathLine { x: 4.0; y: 0.5 }
                }
                ShapePath {
                    strokeWidth: 0.7
                    strokeColor: Qt.alpha(root.specularColor, 0.85)
                    fillColor: "#0c1a28"
                    startX: 6.2; startY: 1.8
                    PathLine { x: 35.8; y: 1.8 }
                    PathLine { x: 37.8; y: 6.6 }
                    PathLine { x: 4.2; y: 6.6 }
                    PathLine { x: 6.2; y: 1.8 }
                }
                ShapePath {
                    strokeWidth: 0.65
                    strokeColor: "#d9fbff"
                    fillColor: Qt.alpha("#9ef6ff", 0.38 + root.typingPulse * 0.35)
                    startX: 16.5; startY: 7.2
                    PathLine { x: 25.5; y: 7.2 }
                    PathLine { x: 26.2; y: 9.0 }
                    PathLine { x: 15.8; y: 9.0 }
                    PathLine { x: 16.5; y: 7.2 }
                }
            }
            Row {
                x: 8.5
                y: 3.1
                spacing: 2.0
                Repeater {
                    model: 6
                    Rectangle {
                        required property int index
                        width: 3.2
                        height: 1.8
                        radius: 0.5
                        color: (index % 2 === 0 && root.typingPulse > 0.15) ? "#ffffff" : "#7eeeff"
                        opacity: 0.75 + root.typingPulse * 0.25
                    }
                }
            }
        }
    }
}
