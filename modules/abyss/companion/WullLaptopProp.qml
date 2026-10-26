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
        ? body.project(0, -25 + laptopLift, 21 - laptopSlide * 0.45)
        : ({ x: 0, y: -25, z: 21, scale: 1 })

    Item {
        id: laptopRoot
        objectName: "wullLaptopRoot"
        width: 50
        height: 34
        x: 38 + root.deckAnchor.x - width * 0.5
        y: 46.5 - root.deckAnchor.y - height * 0.5 + root.lift
        scale: Math.max(0.88, Math.min(1.2, root.deckAnchor.scale ?? 1.05))

        // Drop shadow beneath the 3/4 laptop
        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0
                fillColor: "#66020812"
                startX: 6; startY: 24.5
                PathLine { x: 25; y: 12.5 }
                PathLine { x: 46; y: 18.5 }
                PathLine { x: 27; y: 31.0 }
                PathLine { x: 6; y: 24.5 }
            }
        }

        // 1. BACK LAYER: Keyboard deck extending UP-AND-RIGHT toward Companion's body & hands
        Item {
            id: laptopDeck
            objectName: "wullLaptopDeck"
            x: 8
            y: 7
            width: 39
            height: 23

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                // 3D base thickness along the right/bottom edges
                ShapePath {
                    strokeWidth: 0.75
                    strokeColor: "#0e1f2f"
                    fillColor: "#13283c"
                    startX: 20.0; startY: 20.8
                    PathLine { x: 37.5; y: 9.8 }
                    PathLine { x: 37.5; y: 12.2 }
                    PathLine { x: 20.0; y: 23.0 }
                    PathLine { x: 0.8; y: 17.2 }
                    PathLine { x: 0.8; y: 15.0 }
                    PathLine { x: 20.0; y: 20.8 }
                }
                // Top aluminum deck plane extending back toward Companion (upper-right)
                ShapePath {
                    strokeWidth: 1.0
                    strokeColor: "#d8f8ff"
                    fillGradient: LinearGradient {
                        x1: 34; y1: 4; x2: 8; y2: 20
                        GradientStop { position: 0; color: "#315f82" }
                        GradientStop { position: 1; color: "#1b374e" }
                    }
                    startX: 0.8; startY: 15.0
                    PathLine { x: 18.5; y: 3.8 }
                    PathLine { x: 37.5; y: 9.8 }
                    PathLine { x: 20.0; y: 20.8 }
                    PathLine { x: 0.8; y: 15.0 }
                }
                // Trackpad on the upper-right palmrest (closest to Companion's belly!)
                ShapePath {
                    strokeWidth: 0.6
                    strokeColor: "#b8f2ff"
                    fillColor: Qt.alpha("#9ef6ff", 0.32 + root.typingPulse * 0.28)
                    startX: 21.8; startY: 6.2
                    PathLine { x: 28.2; y: 8.2 }
                    PathLine { x: 24.4; y: 10.7 }
                    PathLine { x: 18.0; y: 8.7 }
                    PathLine { x: 21.8; y: 6.2 }
                }
                // Recessed dark keyboard well between Companion's palmrest and front hinge
                ShapePath {
                    strokeWidth: 0.65
                    strokeColor: Qt.alpha(root.specularColor, 0.75)
                    fillColor: "#091521"
                    startX: 5.5; startY: 14.5
                    PathLine { x: 16.2; y: 7.6 }
                    PathLine { x: 31.8; y: 12.5 }
                    PathLine { x: 21.0; y: 19.3 }
                    PathLine { x: 5.5; y: 14.5 }
                }
            }

            // Key rows angled along the deck where Companion's hands tap
            Repeater {
                model: 4
                Rectangle {
                    required property int index
                    width: 3.4
                    height: 1.8
                    radius: 0.5
                    x: 13.5 + index * 3.5
                    y: 9.8 + index * 1.05
                    rotation: 17
                    color: (index % 2 === 0 && root.typingPulse > 0.15) ? "#ffffff" : "#82f0ff"
                    opacity: 0.82 + root.typingPulse * 0.18
                }
            }
            Repeater {
                model: 4
                Rectangle {
                    required property int index
                    width: 3.4
                    height: 1.8
                    radius: 0.5
                    x: 10.2 + index * 3.5
                    y: 12.2 + index * 1.05
                    rotation: 17
                    color: (index % 2 === 1 && root.typingPulse > 0.15) ? "#ffffff" : "#5ed2f2"
                    opacity: 0.76 + root.typingPulse * 0.22
                }
            }
        }

        // 2. MIDDLE LAYER: Cyan light cast backward/upward from the hidden inner screen onto Companion
        Shape {
            visible: root.hingeFactor > 0.35
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0
                fillGradient: LinearGradient {
                    x1: 18; y1: 16; x2: 34; y2: 2
                    GradientStop { position: 0; color: Qt.rgba(0.42, 0.94, 1.0, 0.42 + root.screenPulse * 0.18) }
                    GradientStop { position: 1; color: "transparent" }
                }
                startX: 6; startY: 9
                PathLine { x: 28; y: 1 }
                PathLine { x: 42; y: 11 }
                PathLine { x: 27; y: 24 }
                PathLine { x: 6; y: 9 }
            }
        }

        // 3. FRONT LAYER: Hinge barrel along the front-bottom-left edge (closest to viewer)
        Shape {
            id: laptopHingeBar
            objectName: "wullLaptopHinge"
            x: 6
            y: 20.5
            width: 24
            height: 8.5
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0.75
                strokeColor: "#d9fbff"
                fillColor: "#14283a"
                startX: 1.5; startY: 1.2
                PathLine { x: 21.5; y: 6.8 }
                PathLine { x: 20.5; y: 8.2 }
                PathLine { x: 0.6; y: 2.6 }
                PathLine { x: 1.5; y: 1.2 }
            }
        }

        // 4. FOREGROUND LAYER: Back of the Screen Lid (occludes front of keyboard; screen faces Companion!)
        Item {
            id: laptopLid
            objectName: "wullLaptopLid"
            width: 28
            height: 7.0 + root.hingeFactor * 15.5
            x: 4
            y: 28.5 - height

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer

                // Inner glowing cyan screen bezel sliver on the back/top-right rim facing Companion
                ShapePath {
                    strokeWidth: 0.85
                    strokeColor: "#a8f8ff"
                    fillColor: Qt.rgba(0.32, 0.90, 1.0, 0.92 + root.screenPulse * 0.08)
                    startX: 2.8; startY: 0.4
                    PathLine { x: 24.8; y: 6.2 }
                    PathLine { x: 26.8; y: laptopLid.height - 1.2 }
                    PathLine { x: 24.4; y: laptopLid.height - 0.2 }
                    PathLine { x: 22.6; y: 7.4 }
                    PathLine { x: 1.0; y: 1.6 }
                    PathLine { x: 2.8; y: 0.4 }
                }

                // Matte metallic BACK cover of the lid facing the viewer in the foreground
                ShapePath {
                    strokeWidth: 1.05
                    strokeColor: "#b8e6f5"
                    fillGradient: LinearGradient {
                        x1: 0; y1: 0; x2: 24; y2: laptopLid.height
                        GradientStop { position: 0; color: "#22435e" }
                        GradientStop { position: 0.55; color: "#152c40" }
                        GradientStop { position: 1; color: "#0d1d2c" }
                    }
                    startX: 0.8; startY: 1.6
                    PathLine { x: 22.6; y: 7.4 }
                    PathLine { x: 24.4; y: laptopLid.height - 0.2 }
                    PathLine { x: 2.8; y: Math.max(2.6, laptopLid.height - 6.0) }
                    PathLine { x: 0.8; y: 1.6 }
                }

                // Subtle diagonal brushed-metal sheen on the back of the lid
                ShapePath {
                    strokeWidth: 0
                    fillColor: Qt.alpha(root.specularColor, 0.12)
                    startX: 3.5; startY: 2.4
                    PathLine { x: 10.5; y: 4.2 }
                    PathLine { x: 8.2; y: Math.max(4.0, laptopLid.height - 4.4) }
                    PathLine { x: 2.8; y: Math.max(2.6, laptopLid.height - 6.0) }
                    PathLine { x: 3.5; y: 2.4 }
                }
            }

            // Glowing Companion logo on the center of the back cover facing the viewer
            Rectangle {
                id: lidBackEmblem
                objectName: "wullLaptopEmblem"
                visible: root.hingeFactor > 0.35
                width: 4.4
                height: 3.6
                radius: 2.0
                rotation: 15
                x: 10.8
                y: laptopLid.height * 0.46
                color: "#ecfdff"
                border.width: 0.7
                border.color: root.accentColor
                opacity: 0.78 + root.screenPulse * 0.22
            }
        }
    }
}
