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

    readonly property color deckEdgeStrokeColor: Qt.darker(root.accentColor, 2.8)
    readonly property color deckEdgeFillColor: Qt.darker(root.accentColor, 2.35)
    readonly property color deckTopRimColor: Qt.lighter(root.specularColor, 1.05)
    readonly property color deckGradStartColor: Qt.darker(root.accentColor, 1.45)
    readonly property color deckGradEndColor: Qt.darker(root.accentColor, 1.95)
    readonly property color trackpadStrokeColor: Qt.lighter(root.accentColor, 1.35)
    readonly property color trackpadFillColor: Qt.alpha(root.accentColor, 0.34 + root.typingPulse * 0.28)
    readonly property color keyWellFillColor: Qt.darker(root.accentColor, 3.1)
    readonly property color keyCapPrimaryColor: Qt.lighter(root.accentColor, 1.22)
    readonly property color keyCapSecondaryColor: root.accentColor
    readonly property color screenGlowColor: Qt.alpha(root.accentColor, 0.44 + root.screenPulse * 0.18)
    readonly property color hingeStrokeColor: Qt.darker(root.accentColor, 2.6)
    readonly property color hingeFillColor: Qt.darker(root.accentColor, 1.65)
    readonly property color lidRimStrokeColor: Qt.darker(root.accentColor, 2.15)
    readonly property color lidRimFillColor: Qt.darker(root.accentColor, 1.35)
    readonly property color lidBackStrokeColor: Qt.darker(root.accentColor, 1.9)
    readonly property color lidBackGrad0: Qt.lighter(root.specularColor, 1.02)
    readonly property color lidBackGrad1: Qt.lighter(root.accentColor, 1.18)
    readonly property color lidBackGrad2: Qt.darker(root.accentColor, 1.15)
    readonly property color lidCreaseColor: Qt.darker(root.accentColor, 1.55)
    readonly property color emblemStrokeColor: Qt.darker(root.accentColor, 2.0)
    readonly property color emblemFillColor: root.specularColor

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
                    strokeColor: root.deckEdgeStrokeColor
                    fillColor: root.deckEdgeFillColor
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
                    strokeColor: root.deckTopRimColor
                    fillGradient: LinearGradient {
                        x1: 34; y1: 4; x2: 8; y2: 20
                        GradientStop { position: 0; color: root.deckGradStartColor }
                        GradientStop { position: 1; color: root.deckGradEndColor }
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
                    strokeColor: root.trackpadStrokeColor
                    fillColor: root.trackpadFillColor
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
                    fillColor: root.keyWellFillColor
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
                    color: (index % 2 === 0 && root.typingPulse > 0.15) ? root.specularColor : root.keyCapPrimaryColor
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
                    color: (index % 2 === 1 && root.typingPulse > 0.15) ? root.specularColor : root.keyCapSecondaryColor
                    opacity: 0.76 + root.typingPulse * 0.22
                }
            }
        }

        // 2. MIDDLE LAYER: Theme light cast backward/upward from the hidden inner screen onto Companion
        Shape {
            visible: root.hingeFactor > 0.35
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0
                fillGradient: LinearGradient {
                    x1: 18; y1: 16; x2: 34; y2: 2
                    GradientStop { position: 0; color: root.screenGlowColor }
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
            x: 5
            y: 20.0
            width: 25
            height: 8.5
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0.85
                strokeColor: root.hingeStrokeColor
                fillColor: root.hingeFillColor
                startX: 1.2; startY: 1.0
                PathLine { x: 22.0; y: 6.8 }
                PathLine { x: 20.8; y: 8.3 }
                PathLine { x: 0.4; y: 2.5 }
                PathLine { x: 1.2; y: 1.0 }
            }
        }

        // 4. FOREGROUND LAYER: Theme-Tinted Metallic BACK Cover of the Screen Lid
        //    (Faces the viewer in the foreground, occluding the front of the keyboard;
        //     the screen is on the opposite side facing Companion!)
        Item {
            id: laptopLid
            objectName: "wullLaptopLid"
            width: 30
            height: 7.5 + root.hingeFactor * 15.5
            x: 3.5
            y: 28.5 - height

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer

                // 3D lid thickness edge along the top & right rim (showing the lid is angled away toward Companion)
                ShapePath {
                    strokeWidth: 0.75
                    strokeColor: root.lidRimStrokeColor
                    fillColor: root.lidRimFillColor
                    startX: 3.0; startY: 0.5
                    PathLine { x: 25.6; y: 6.0 }
                    PathLine { x: 26.2; y: laptopLid.height - 0.4 }
                    PathLine { x: 24.0; y: laptopLid.height }
                    PathLine { x: 23.4; y: 7.2 }
                    PathLine { x: 1.0; y: 1.7 }
                    PathLine { x: 3.0; y: 0.5 }
                }

                // Theme-tinted metallic BACK shell of the lid facing the viewer
                ShapePath {
                    strokeWidth: 1.1
                    strokeColor: root.lidBackStrokeColor
                    fillGradient: LinearGradient {
                        x1: 2; y1: 1; x2: 24; y2: laptopLid.height
                        GradientStop { position: 0.0; color: root.lidBackGrad0 }
                        GradientStop { position: 0.45; color: root.lidBackGrad1 }
                        GradientStop { position: 1.0; color: root.lidBackGrad2 }
                    }
                    startX: 1.0; startY: 1.7
                    PathLine { x: 23.4; y: 7.2 }
                    PathLine { x: 24.0; y: laptopLid.height }
                    PathLine { x: 2.2; y: Math.max(3.0, laptopLid.height - 5.6) }
                    PathLine { x: 1.0; y: 1.7 }
                }

                // Metallic diagonal specular reflection across the aluminum lid back
                ShapePath {
                    strokeWidth: 0
                    fillColor: Qt.alpha(root.specularColor, 0.38)
                    startX: 4.2; startY: 2.5
                    PathLine { x: 11.2; y: 4.2 }
                    PathLine { x: 8.0; y: Math.max(4.2, laptopLid.height - 4.2) }
                    PathLine { x: 2.8; y: Math.max(3.2, laptopLid.height - 5.4) }
                    PathLine { x: 4.2; y: 2.5 }
                }

                // Bottom-back hinge crease line on the exterior shell
                ShapePath {
                    strokeWidth: 0.8
                    strokeColor: root.lidCreaseColor
                    fillColor: "transparent"
                    startX: 2.6; startY: Math.max(2.8, laptopLid.height - 6.8)
                    PathLine { x: 23.6; y: Math.max(7.6, laptopLid.height - 1.4) }
                }

                // Glowing Abyss droplet emblem on the center of the metallic back cover
                ShapePath {
                    strokeWidth: 0.85
                    strokeColor: root.emblemStrokeColor
                    fillColor: root.emblemFillColor
                    startX: 12.8; startY: laptopLid.height * 0.34
                    PathQuad { x: 15.6; y: laptopLid.height * 0.56; controlX: 16.2; controlY: laptopLid.height * 0.44 }
                    PathQuad { x: 12.6; y: laptopLid.height * 0.66; controlX: 14.8; controlY: laptopLid.height * 0.68 }
                    PathQuad { x: 9.8; y: laptopLid.height * 0.50; controlX: 10.2; controlY: laptopLid.height * 0.62 }
                    PathQuad { x: 12.8; y: laptopLid.height * 0.34; controlX: 10.0; controlY: laptopLid.height * 0.40 }
                }
            }

            Rectangle {
                id: lidBackEmblem
                objectName: "wullLaptopEmblem"
                visible: false
                width: 4; height: 4
            }
        }
    }
}
