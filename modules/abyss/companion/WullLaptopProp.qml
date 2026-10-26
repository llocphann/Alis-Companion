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
        ? body.project(-2, -25 + laptopLift, 21 - laptopSlide * 0.45)
        : ({ x: -2, y: -25, z: 21, scale: 1 })

    Item {
        id: laptopRoot
        objectName: "wullLaptopRoot"
        width: 48
        height: 34
        x: 38 + root.deckAnchor.x - width * 0.5
        y: 46.5 - root.deckAnchor.y - height * 0.5 + root.lift
        scale: Math.max(0.88, Math.min(1.2, root.deckAnchor.scale ?? 1.05))

        // Soft drop shadow beneath the 3/4 angled laptop
        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0
                fillColor: "#66020812"
                startX: 6; startY: 23.5
                PathLine { x: 29; y: 18.5 }
                PathLine { x: 45; y: 26.5 }
                PathLine { x: 21; y: 31.5 }
                PathLine { x: 6; y: 23.5 }
            }
        }

        // 1. Keyboard deck (inner/right 3/4 plane facing Wull's hands & body)
        Item {
            id: laptopDeck
            objectName: "wullLaptopDeck"
            x: 6
            y: 13.5
            width: 40
            height: 18

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                // 3D chassis thickness along bottom-right edges
                ShapePath {
                    strokeWidth: 0.8
                    strokeColor: "#102233"
                    fillColor: "#14293d"
                    startX: 1.5; startY: 7.2
                    PathLine { x: 16.5; y: 14.8 }
                    PathLine { x: 38.2; y: 9.4 }
                    PathLine { x: 38.2; y: 11.8 }
                    PathLine { x: 16.5; y: 17.2 }
                    PathLine { x: 1.5; y: 9.5 }
                    PathLine { x: 1.5; y: 7.2 }
                }
                // Top aluminum deck surface angled toward Wull
                ShapePath {
                    strokeWidth: 1.05
                    strokeColor: "#eefcff"
                    fillGradient: LinearGradient {
                        x1: 4; y1: 2; x2: 36; y2: 15
                        GradientStop { position: 0; color: "#2d5878" }
                        GradientStop { position: 1; color: "#1c3950" }
                    }
                    startX: 1.5; startY: 7.2
                    PathLine { x: 23.2; y: 1.8 }
                    PathLine { x: 38.2; y: 9.4 }
                    PathLine { x: 16.5; y: 14.8 }
                    PathLine { x: 1.5; y: 7.2 }
                }
                // Recessed dark keyboard well (3/4 isometric)
                ShapePath {
                    strokeWidth: 0.65
                    strokeColor: Qt.alpha(root.specularColor, 0.78)
                    fillColor: "#0a1622"
                    startX: 5.2; startY: 7.2
                    PathLine { x: 22.8; y: 2.9 }
                    PathLine { x: 31.2; y: 7.2 }
                    PathLine { x: 13.6; y: 11.6 }
                    PathLine { x: 5.2; y: 7.2 }
                }
                // Angled trackpad on the inner side closest to Wull
                ShapePath {
                    strokeWidth: 0.6
                    strokeColor: "#bfefff"
                    fillColor: Qt.alpha("#9ef6ff", 0.34 + root.typingPulse * 0.32)
                    startX: 19.5; startY: 11.2
                    PathLine { x: 26.8; y: 9.4 }
                    PathLine { x: 30.2; y: 11.1 }
                    PathLine { x: 22.9; y: 12.9 }
                    PathLine { x: 19.5; y: 11.2 }
                }
            }
            // Isometric key rows angled along the 3/4 deck plane
            Repeater {
                model: 5
                Rectangle {
                    required property int index
                    width: 3.4
                    height: 1.8
                    radius: 0.5
                    x: 8.5 + index * 3.4
                    y: 7.6 - index * 0.82
                    rotation: -14
                    color: (index % 2 === 0 && root.typingPulse > 0.15) ? "#ffffff" : "#7eeeff"
                    opacity: 0.78 + root.typingPulse * 0.22
                }
            }
            Repeater {
                model: 5
                Rectangle {
                    required property int index
                    width: 3.4
                    height: 1.8
                    radius: 0.5
                    x: 12.2 + index * 3.4
                    y: 9.4 - index * 0.82
                    rotation: -14
                    color: (index % 2 === 1 && root.typingPulse > 0.15) ? "#ffffff" : "#62d6f5"
                    opacity: 0.72 + root.typingPulse * 0.25
                }
            }
        }

        // Cyan light cast upward from the inner screen onto Wull's face
        Shape {
            visible: root.hingeFactor > 0.35
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0
                fillGradient: LinearGradient {
                    x1: 20; y1: 6; x2: 36; y2: 0
                    GradientStop { position: 0; color: Qt.rgba(0.45, 0.94, 1.0, 0.34 + root.screenPulse * 0.16) }
                    GradientStop { position: 1; color: "transparent" }
                }
                startX: 8; startY: 6
                PathLine { x: 30; y: 1 }
                PathLine { x: 42; y: 9 }
                PathLine { x: 26; y: 15 }
                PathLine { x: 8; y: 6 }
            }
        }

        // 2. Diagonal hinge barrel connecting the outer lid and inner deck
        Shape {
            id: laptopHingeBar
            objectName: "wullLaptopHinge"
            x: 6
            y: 14.5
            width: 24
            height: 7.5
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0.75
                strokeColor: "#eefcff"
                fillColor: "#172d40"
                startX: 1.2; startY: 5.8
                PathLine { x: 21.8; y: 0.6 }
                PathLine { x: 22.8; y: 2.3 }
                PathLine { x: 2.2; y: 7.4 }
                PathLine { x: 1.2; y: 5.8 }
            }
        }

        // 3. Open Lid in 3/4 view (back cover faces viewer; glowing inner screen faces Wull)
        Item {
            id: laptopLid
            objectName: "wullLaptopLid"
            width: 31
            height: 6.0 + root.hingeFactor * 16.0
            x: 3
            y: 21.2 - height

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                // Inner glowing screen edge & 3/4 rim facing Wull (visible on top/right edge)
                ShapePath {
                    strokeWidth: 0.85
                    strokeColor: "#9ef6ff"
                    fillColor: Qt.rgba(0.16, 0.58, 0.82, 0.92 + root.screenPulse * 0.08)
                    startX: 2.4; startY: 4.8
                    PathLine { x: 24.8; y: 0 }
                    PathLine { x: 27.6; y: 1.2 }
                    PathLine { x: 29.8; y: Math.max(3.2, laptopLid.height - 4.4) }
                    PathLine { x: 26.8; y: Math.max(4.0, laptopLid.height - 3.6) }
                    PathLine { x: 2.4; y: 4.8 }
                }
                // Outer metallic lid shell facing the viewer at a 3/4 diagonal angle
                ShapePath {
                    strokeWidth: 1.15
                    strokeColor: "#eefcff"
                    fillGradient: LinearGradient {
                        x1: 0; y1: 0; x2: 28; y2: laptopLid.height
                        GradientStop { position: 0; color: "#274c6b" }
                        GradientStop { position: 0.55; color: "#183349" }
                        GradientStop { position: 1; color: "#0f2233" }
                    }
                    startX: 0.8; startY: 5.6
                    PathLine { x: 23.8; y: 0.6 }
                    PathLine { x: 26.8; y: Math.max(4.2, laptopLid.height - 3.6) }
                    PathLine { x: 3.8; y: laptopLid.height }
                    PathLine { x: 0.8; y: 5.6 }
                }
                // Subtle diagonal specular sheen across the back of the lid
                ShapePath {
                    strokeWidth: 0
                    fillColor: Qt.alpha(root.specularColor, 0.16 + root.screenPulse * 0.08)
                    startX: 4.0; startY: 5.2
                    PathLine { x: 13.5; y: 3.1 }
                    PathLine { x: 10.2; y: Math.max(5.0, laptopLid.height - 1.6) }
                    PathLine { x: 3.8; y: laptopLid.height }
                    PathLine { x: 4.0; y: 5.2 }
                }
            }

            // Glowing inner-screen activity indicators peeking along the 3/4 right edge facing Wull
            Column {
                visible: root.hingeFactor > 0.45
                x: 24.2
                y: 3.2
                spacing: 1.8
                Rectangle {
                    width: 2.6 + root.typingPulse * 1.4
                    height: 1.3
                    radius: 0.65
                    rotation: -12
                    color: "#9ef6ff"
                }
                Rectangle {
                    width: 3.2 - root.typingPulse * 0.8
                    height: 1.3
                    radius: 0.65
                    rotation: -12
                    color: "#ffd584"
                }
                Rectangle {
                    width: 2.4 + root.screenPulse * 1.4
                    height: 1.3
                    radius: 0.65
                    rotation: -12
                    color: "#7ef7c8"
                }
            }

            // Glowing Companion emblem on the back of the laptop lid (3/4 perspective oval)
            Rectangle {
                objectName: "wullLaptopEmblem"
                visible: root.hingeFactor > 0.35
                width: 6.4
                height: 5.4
                radius: 2.7
                x: 11.2
                y: laptopLid.height * 0.5 - 1.5
                rotation: -13
                color: Qt.alpha("#ffffff", 0.75 + root.screenPulse * 0.25)
                border.width: 0.9
                border.color: root.accentColor
            }
        }
    }
}
