import QtQuick
import QtQuick.Shapes

Item {
    id: root
    objectName: "aquaAmbientProp"
    required property var body
    property string propKind: ""
    property color accentColor: "#6be5ff"
    property color specularColor: "#d9fbff"
    property real shimmer: 0
    property real lift: 0
    property bool thinking: false
    property real thinkingPhase: 0
    readonly property real thinkingWave: thinking ? (0.5 - 0.5 * Math.cos(thinkingPhase * Math.PI * 2)) : 0
    width: 76
    height: 92
    visible: propKind === "book" || propKind === "coffee" || propKind === "thought"

    readonly property color bookCoverLight: Qt.lighter(root.accentColor, 1.15)
    readonly property color bookCoverMid: Qt.darker(root.accentColor, 1.25)
    readonly property color bookCoverDark: Qt.darker(root.accentColor, 1.55)
    readonly property color bookSpineFill: Qt.darker(root.accentColor, 1.95)
    readonly property color bookRimStroke: Qt.lighter(root.specularColor, 1.02)
    readonly property color bookPageLineColor: Qt.darker(root.accentColor, 1.65)
    readonly property color bookRibbonFill: Qt.lighter(root.accentColor, 1.32)
    readonly property color coffeeCupStroke: Qt.lighter(root.specularColor, 1.04)
    readonly property color coffeeCupFill: Qt.lighter(root.accentColor, 1.65)

    readonly property var bookAnchor: (body && propKind === "book") ? body.project(-2, -23, 21) : ({ x: -2, y: -23, z: 21, scale: 1 })
    readonly property var coffeeAnchor: (body && propKind === "coffee") ? body.project(14, -14, 19) : ({ x: 14, y: -14, z: 19, scale: 1 })
    readonly property var thoughtAnchor: (body && propKind === "thought") ? body.project(23, 24, 14) : ({ x: 23, y: 24, z: 14, scale: 1 })

    Item {
        id: bookProp
        objectName: "aquaAmbientBook"
        visible: root.propKind === "book"
        width: 38
        height: 25
        x: 38 + root.bookAnchor.x - width * 0.5
        y: visible ? 46.14 - root.bookAnchor.y - height * 0.5 + root.lift + (root.shimmer - 0.5) * (root.thinking ? 0.9 : 1.6) : 0
        scale: Math.max(0.88, Math.min(1.2, root.bookAnchor.scale ?? 1.05))
        rotation: visible ? -6 + (root.shimmer - 0.5) * (root.thinking ? 1.8 : 3.5) : 0

        // Soft drop shadow under the 3/4 angled open book
        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0
                fillColor: "#55020812"
                startX: 3; startY: 18
                PathLine { x: 19; y: 22.5 }
                PathLine { x: 35; y: 17.5 }
                PathLine { x: 20; y: 14 }
                PathLine { x: 3; y: 18 }
            }
        }

        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer

            // 1. Inner right page & fanned V-page block facing Aqua (top/right 3/4 view)
            ShapePath {
                strokeWidth: 0.85
                strokeColor: "#d9b478"
                fillColor: "#fff9ec"
                startX: 4.2; startY: 5.2
                PathQuad { x: 16.5; y: 6.8; controlX: 10.5; controlY: 2.2 }
                PathQuad { x: 33.8; y: 2.2; controlX: 25.0; controlY: 0.8 }
                PathLine { x: 35.2; y: 14.2 }
                PathQuad { x: 18.2; y: 18.5; controlX: 27.5; controlY: 14.5 }
                PathLine { x: 4.2; y: 5.2 }
            }

            // Subtle inner page text lines facing Aqua (visible on the 3/4 right wing)
            ShapePath {
                strokeWidth: 0.7
                strokeColor: root.bookPageLineColor
                fillColor: "transparent"
                startX: 20.5; startY: 6.4
                PathLine { x: 31.2; y: 4.5 }
                PathMove { x: 21.0; y: 8.9 }
                PathLine { x: 31.6; y: 7.0 }
                PathMove { x: 21.5; y: 11.4 }
                PathLine { x: 30.8; y: 9.7 }
            }

            // 2. Outer hardcover boards facing the viewer in 3/4 perspective (theme-matched to Companion)
            // Right-back cover board (angled away)
            ShapePath {
                strokeWidth: 1.05
                strokeColor: root.bookRimStroke
                fillColor: root.bookCoverDark
                startX: 16.5; startY: 7.4
                PathQuad { x: 31.8; y: 4.4; controlX: 24.2; controlY: 4.6 }
                PathLine { x: 33.2; y: 16.4 }
                PathQuad { x: 18.0; y: 20.4; controlX: 25.8; controlY: 17.6 }
                PathLine { x: 16.5; y: 7.4 }
            }

            // Left-front cover board (facing viewer at 3/4 slant, hiding the left page face)
            ShapePath {
                strokeWidth: 1.1
                strokeColor: root.bookRimStroke
                fillGradient: LinearGradient {
                    x1: 2; y1: 5; x2: 18; y2: 20
                    GradientStop { position: 0; color: root.bookCoverLight }
                    GradientStop { position: 1; color: root.bookCoverMid }
                }
                startX: 2.2; startY: 5.8
                PathQuad { x: 16.5; y: 7.4; controlX: 9.2; controlY: 4.8 }
                PathLine { x: 18.0; y: 20.4 }
                PathQuad { x: 3.6; y: 17.8; controlX: 10.5; controlY: 17.4 }
                PathLine { x: 2.2; y: 5.8 }
            }

            // 3. Central 3D book spine ridge facing outward toward viewer
            ShapePath {
                strokeWidth: 0.9
                strokeColor: root.bookRimStroke
                fillColor: root.bookSpineFill
                startX: 15.2; startY: 7.1
                PathLine { x: 17.8; y: 7.2 }
                PathLine { x: 19.3; y: 20.6 }
                PathLine { x: 16.7; y: 20.5 }
                PathLine { x: 15.2; y: 7.1 }
            }

            // Specular decorative emblem on the outer front-left cover facing the viewer
            ShapePath {
                strokeWidth: 0.85
                strokeColor: root.bookRimStroke
                fillColor: Qt.alpha(root.specularColor, root.thinking ? (0.68 + root.thinkingWave * 0.32) : 1.0)
                startX: 9.4; startY: 10.2
                PathLine { x: 11.8; y: 12.4 }
                PathLine { x: 9.6; y: 14.6 }
                PathLine { x: 7.2; y: 12.4 }
                PathLine { x: 9.4; y: 10.2 }
            }
        }

        // Focused page-scanning shimmer highlight & rising knowledge motes when reading while thinking
        Item {
            id: bookThinkingScan
            objectName: "aquaBookThinkingScan"
            visible: root.thinking && root.propKind === "book"
            x: 19.5
            y: 3.6
            width: 13.5
            height: 9.5
            rotation: -10
            Rectangle {
                width: 12.6
                height: 4.2
                radius: 2.1
                x: 0.3 + Math.sin(root.thinkingPhase * Math.PI * 2) * 0.8
                y: root.thinkingWave * 4.6
                color: Qt.alpha(root.accentColor, 0.38 + root.thinkingWave * 0.25)
            }
            Rectangle {
                width: 11.4
                height: 2.2
                radius: 1.1
                x: 0.9 + Math.sin(root.thinkingPhase * Math.PI * 2) * 0.8
                y: 1.0 + root.thinkingWave * 4.6
                color: Qt.alpha("#fff7cc", 0.78 + root.thinkingWave * 0.22)
                border.width: 0.5
                border.color: Qt.alpha(root.bookRimStroke, 0.45)
            }
            Repeater {
                model: 3
                Rectangle {
                    required property int index
                    readonly property real p: (root.thinkingPhase + index * 0.33) % 1
                    readonly property real fade: Math.sin(p * Math.PI)
                    width: index === 1 ? 4.4 : 3.6
                    height: width
                    radius: width * 0.5
                    x: 1.5 + index * 4.2 + Math.sin(p * Math.PI * 2 + index) * 1.4 - width * 0.5
                    y: -2 - p * 20 - height * 0.5
                    opacity: fade * 0.92
                    scale: 0.7 + fade * 0.35
                    color: "#fff7d1"
                    border.width: 0.7
                    border.color: root.bookCoverDark
                }
            }
        }

        // Hanging bookmark ribbon from the bottom of the spine
        Shape {
            x: 16.2
            y: 19.5
            width: 5
            height: 5
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0.5
                strokeColor: root.bookRimStroke
                fillColor: root.bookRibbonFill
                startX: 0.8; startY: 0
                PathLine { x: 2.8; y: 0.2 }
                PathLine { x: 3.8; y: 4.2 }
                PathLine { x: 2.1; y: 3.2 }
                PathLine { x: 0.6; y: 4.4 }
                PathLine { x: 0.8; y: 0 }
            }
        }
    }

    Item {
        id: coffeeProp
        objectName: "aquaAmbientCoffee"
        visible: root.propKind === "coffee"
        width: 24
        height: 26
        x: 38 + root.coffeeAnchor.x - width * 0.5
        y: visible ? 46.14 - root.coffeeAnchor.y - height * 0.5 + root.lift + (0.5 - root.shimmer) * 1.4 : 0
        scale: Math.max(0.88, Math.min(1.2, root.coffeeAnchor.scale ?? 1.05))
        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 1.0
                strokeColor: root.coffeeCupStroke
                fillColor: root.coffeeCupFill
                startX: 4.5; startY: 9.5
                PathLine { x: 16.5; y: 9.5 }
                PathQuad { x: 15.2; y: 21.5; controlX: 16.5; controlY: 19 }
                PathQuad { x: 5.8; y: 21.5; controlX: 10.5; controlY: 22.8 }
                PathQuad { x: 4.5; y: 9.5; controlX: 4.5; controlY: 19 }
            }
            ShapePath {
                strokeWidth: 0.7
                strokeColor: root.bookRimStroke
                fillColor: "#4a2713"
                startX: 5.0; startY: 9.8
                PathQuad { x: 16.0; y: 9.8; controlX: 10.5; controlY: 8.2 }
                PathQuad { x: 5.0; y: 9.8; controlX: 10.5; controlY: 11.4 }
            }
            ShapePath {
                strokeWidth: 1.4
                strokeColor: root.coffeeCupFill
                fillColor: "transparent"
                startX: 16.2; startY: 11.5
                PathCubic { x: 15.5; y: 18.2; control1X: 21.2; control1Y: 11.5; control2X: 21.2; control2Y: 18.2 }
            }
            ShapePath {
                strokeWidth: root.thinking ? 1.25 : 0.95
                strokeColor: root.thinking ? Qt.alpha("#fff8dc", 0.92) : Qt.alpha("#ffffff", 0.85)
                fillColor: "transparent"
                startX: 8; startY: 7.4
                PathQuad { x: 8.6; y: 2.2; controlX: coffeeProp.visible ? 6.0 + (root.thinking ? 1.4 + Math.sin(root.thinkingPhase * Math.PI * 2) * 1.6 : root.shimmer * 2.8) : 6.0; controlY: 4.8 }
                PathMove { x: 12.8; y: 7.6 }
                PathQuad { x: 13.4; y: 2.6; controlX: coffeeProp.visible ? 15.0 - (root.thinking ? 1.4 + Math.sin(root.thinkingPhase * Math.PI * 2) * 1.6 : root.shimmer * 2.8) : 15.0; controlY: 5.0 }
            }
        }
    }

    Item {
        id: thoughtProp
        objectName: "aquaAmbientThought"
        visible: root.propKind === "thought"
        width: 28
        height: 24
        x: 38 + root.thoughtAnchor.x - width * 0.5
        y: visible ? 46.14 - root.thoughtAnchor.y - height * 0.5 + root.lift + (root.shimmer - 0.5) * 1.8 : 0
        scale: Math.max(0.88, Math.min(1.2, root.thoughtAnchor.scale ?? 1.05))
        Rectangle {
            x: 2; y: 17; width: 4.2; height: 4.2; radius: 2.1
            color: "#f2fdff"
            border.width: 0.7; border.color: root.accentColor
        }
        Rectangle {
            x: 6.5; y: 12.5; width: 6; height: 6; radius: 3
            color: "#f7feff"
            border.width: 0.8; border.color: root.accentColor
        }
        Rectangle {
            x: 10; y: 1.5; width: 16; height: 11.5; radius: 5.75
            color: "#ffffff"
            border.width: 0.9; border.color: root.accentColor
            Row {
                anchors.centerIn: parent
                spacing: 2
                Repeater {
                    model: 3
                    Rectangle {
                        required property int index
                        width: 2.2; height: 2.2; radius: 1.1
                        color: "#1e4460"
                        opacity: root.thinking ? (0.4 + 0.6 * Math.max(0, Math.sin((root.thinkingPhase + index * 0.25) * Math.PI * 2))) : 1.0
                    }
                }
            }
        }
    }
}
