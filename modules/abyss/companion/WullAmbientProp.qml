import QtQuick
import QtQuick.Shapes

Item {
    id: root
    objectName: "wullAmbientProp"
    required property var body
    property string propKind: ""
    property color accentColor: "#6be5ff"
    property color specularColor: "#d9fbff"
    property real shimmer: 0
    property real lift: 0
    width: 76
    height: 92
    visible: propKind === "book" || propKind === "coffee" || propKind === "thought"

    readonly property var bookAnchor: body ? body.project(0, -13, 18) : ({ x: 0, y: -13, z: 18, scale: 1 })
    readonly property var coffeeAnchor: body ? body.project(14, -11, 17) : ({ x: 14, y: -11, z: 17, scale: 1 })
    readonly property var thoughtAnchor: body ? body.project(23, 24, 14) : ({ x: 23, y: 24, z: 14, scale: 1 })

    Item {
        id: bookProp
        objectName: "wullAmbientBook"
        visible: root.propKind === "book"
        width: 28
        height: 18
        x: 38 + root.bookAnchor.x - width * 0.5
        y: 46.14 - root.bookAnchor.y - height * 0.5 + root.lift + (root.shimmer - 0.5) * 1.4
        scale: Math.max(0.82, Math.min(1.16, root.bookAnchor.scale))
        rotation: (root.shimmer - 0.5) * 4
        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0.9
                strokeColor: root.specularColor
                fillColor: Qt.darker(root.accentColor, 1.45)
                startX: 2; startY: 4
                PathQuad { x: 14; y: 6; controlX: 8; controlY: 2.5 }
                PathQuad { x: 26; y: 4; controlX: 20; controlY: 2.5 }
                PathLine { x: 25; y: 15 }
                PathQuad { x: 14; y: 16.5; controlX: 20; controlY: 14 }
                PathQuad { x: 3; y: 15; controlX: 8; controlY: 14 }
                PathLine { x: 2; y: 4 }
            }
            ShapePath {
                strokeWidth: 0.65
                strokeColor: Qt.darker(root.accentColor, 1.25)
                fillColor: Qt.alpha(root.specularColor, 0.92)
                startX: 3.5; startY: 3
                PathQuad { x: 14; y: 5.2; controlX: 8.5; controlY: 1.8 }
                PathQuad { x: 24.5; y: 3; controlX: 19.5; controlY: 1.8 }
                PathLine { x: 23.8; y: 13.4 }
                PathQuad { x: 14; y: 15.2; controlX: 19.2; controlY: 12.6 }
                PathQuad { x: 4.2; y: 13.4; controlX: 8.8; controlY: 12.6 }
                PathLine { x: 3.5; y: 3 }
            }
            ShapePath {
                strokeWidth: 0.8
                strokeColor: root.accentColor
                fillColor: "transparent"
                startX: 14; startY: 5.2
                PathLine { x: 14; y: 15.2 }
            }
        }
    }

    Item {
        id: coffeeProp
        objectName: "wullAmbientCoffee"
        visible: root.propKind === "coffee"
        width: 20
        height: 22
        x: 38 + root.coffeeAnchor.x - width * 0.5
        y: 46.14 - root.coffeeAnchor.y - height * 0.5 + root.lift + (0.5 - root.shimmer) * 1.2
        scale: Math.max(0.82, Math.min(1.16, root.coffeeAnchor.scale))
        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0.85
                strokeColor: root.specularColor
                fillColor: Qt.lighter(root.accentColor, 1.18)
                startX: 4; startY: 8
                PathLine { x: 14; y: 8 }
                PathQuad { x: 13; y: 18; controlX: 14; controlY: 16 }
                PathQuad { x: 5; y: 18; controlX: 9; controlY: 19.2 }
                PathQuad { x: 4; y: 8; controlX: 4; controlY: 16 }
            }
            ShapePath {
                strokeWidth: 1.1
                strokeColor: root.specularColor
                fillColor: "transparent"
                startX: 14; startY: 9.8
                PathCubic { x: 13.4; y: 15.4; control1X: 18; control1Y: 9.8; control2X: 18; control2Y: 15.4 }
            }
            ShapePath {
                strokeWidth: 0.75
                strokeColor: Qt.alpha(root.specularColor, 0.78)
                fillColor: "transparent"
                startX: 7; startY: 6.2
                PathQuad { x: 7.6; y: 2.2; controlX: 5.4 + root.shimmer * 2.2; controlY: 4.2 }
                PathMove { x: 11; y: 6.4 }
                PathQuad { x: 11.4; y: 2.6; controlX: 12.6 - root.shimmer * 2.2; controlY: 4.4 }
            }
        }
    }

    Item {
        id: thoughtProp
        objectName: "wullAmbientThought"
        visible: root.propKind === "thought"
        width: 24
        height: 22
        x: 38 + root.thoughtAnchor.x - width * 0.5
        y: 46.14 - root.thoughtAnchor.y - height * 0.5 + root.lift + (root.shimmer - 0.5) * 1.6
        scale: Math.max(0.82, Math.min(1.16, root.thoughtAnchor.scale))
        Rectangle {
            x: 2; y: 16; width: 3.5; height: 3.5; radius: 1.75
            color: Qt.alpha(root.specularColor, 0.72)
            border.width: 0.5; border.color: root.accentColor
        }
        Rectangle {
            x: 6; y: 12; width: 5; height: 5; radius: 2.5
            color: Qt.alpha(root.specularColor, 0.82)
            border.width: 0.6; border.color: root.accentColor
        }
        Rectangle {
            x: 9; y: 2; width: 13; height: 10; radius: 5
            color: Qt.alpha(root.specularColor, 0.9)
            border.width: 0.75; border.color: root.accentColor
        }
    }
}
