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

    readonly property var bookAnchor: body ? body.project(0, -17, 20) : ({ x: 0, y: -17, z: 20, scale: 1 })
    readonly property var coffeeAnchor: body ? body.project(14, -14, 19) : ({ x: 14, y: -14, z: 19, scale: 1 })
    readonly property var thoughtAnchor: body ? body.project(23, 24, 14) : ({ x: 23, y: 24, z: 14, scale: 1 })

    Item {
        id: bookProp
        objectName: "wullAmbientBook"
        visible: root.propKind === "book"
        width: 34
        height: 22
        x: 38 + root.bookAnchor.x - width * 0.5
        y: 46.14 - root.bookAnchor.y - height * 0.5 + root.lift + (root.shimmer - 0.5) * 1.8
        scale: Math.max(0.88, Math.min(1.2, root.bookAnchor.scale ?? 1.05))
        rotation: (root.shimmer - 0.5) * 4.5
        Rectangle {
            x: 3
            y: 16
            width: 28
            height: 5
            radius: 2.5
            color: "#55020812"
        }
        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 1.1
                strokeColor: "#ffd88a"
                fillColor: "#8f3b2f"
                startX: 1.5; startY: 4.5
                PathQuad { x: 17; y: 7.2; controlX: 9; controlY: 2.8 }
                PathQuad { x: 32.5; y: 4.5; controlX: 25; controlY: 2.8 }
                PathLine { x: 31.2; y: 18.5 }
                PathQuad { x: 17; y: 20.5; controlX: 24.5; controlY: 17.5 }
                PathQuad { x: 2.8; y: 18.5; controlX: 9.5; controlY: 17.5 }
                PathLine { x: 1.5; y: 4.5 }
            }
            ShapePath {
                strokeWidth: 0.75
                strokeColor: "#d9b478"
                fillColor: "#fff9ec"
                startX: 3.2; startY: 3.2
                PathQuad { x: 17; y: 6.2; controlX: 10; controlY: 1.6 }
                PathQuad { x: 30.8; y: 3.2; controlX: 24; controlY: 1.6 }
                PathLine { x: 29.8; y: 16.6 }
                PathQuad { x: 17; y: 18.8; controlX: 23.6; controlY: 15.6 }
                PathQuad { x: 4.2; y: 16.6; controlX: 10.4; controlY: 15.6 }
                PathLine { x: 3.2; y: 3.2 }
            }
            ShapePath {
                strokeWidth: 1.0
                strokeColor: "#b87d4b"
                fillColor: "transparent"
                startX: 17; startY: 6.0
                PathLine { x: 17; y: 18.8 }
            }
            ShapePath {
                strokeWidth: 0.7
                strokeColor: "#355570"
                fillColor: "transparent"
                startX: 6.2; startY: 6.8
                PathLine { x: 14.4; y: 7.8 }
                PathMove { x: 6.0; y: 9.6 }
                PathLine { x: 14.2; y: 10.6 }
                PathMove { x: 5.8; y: 12.4 }
                PathLine { x: 13.6; y: 13.3 }
                PathMove { x: 19.6; y: 7.8 }
                PathLine { x: 27.8; y: 6.8 }
                PathMove { x: 19.8; y: 10.6 }
                PathLine { x: 28.0; y: 9.6 }
                PathMove { x: 20.4; y: 13.3 }
                PathLine { x: 28.2; y: 12.4 }
            }
        }
        Rectangle {
            x: 16.2
            y: 17.5
            width: 1.8
            height: 3.5
            radius: 0.6
            color: "#ff5c6c"
        }
    }

    Item {
        id: coffeeProp
        objectName: "wullAmbientCoffee"
        visible: root.propKind === "coffee"
        width: 24
        height: 26
        x: 38 + root.coffeeAnchor.x - width * 0.5
        y: 46.14 - root.coffeeAnchor.y - height * 0.5 + root.lift + (0.5 - root.shimmer) * 1.4
        scale: Math.max(0.88, Math.min(1.2, root.coffeeAnchor.scale ?? 1.05))
        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 1.0
                strokeColor: "#eefcff"
                fillColor: "#fff6ea"
                startX: 4.5; startY: 9.5
                PathLine { x: 16.5; y: 9.5 }
                PathQuad { x: 15.2; y: 21.5; controlX: 16.5; controlY: 19 }
                PathQuad { x: 5.8; y: 21.5; controlX: 10.5; controlY: 22.8 }
                PathQuad { x: 4.5; y: 9.5; controlX: 4.5; controlY: 19 }
            }
            ShapePath {
                strokeWidth: 0.7
                strokeColor: "#d9b478"
                fillColor: "#4a2713"
                startX: 5.0; startY: 9.8
                PathQuad { x: 16.0; y: 9.8; controlX: 10.5; controlY: 8.2 }
                PathQuad { x: 5.0; y: 9.8; controlX: 10.5; controlY: 11.4 }
            }
            ShapePath {
                strokeWidth: 1.4
                strokeColor: "#fff6ea"
                fillColor: "transparent"
                startX: 16.2; startY: 11.5
                PathCubic { x: 15.5; y: 18.2; control1X: 21.2; control1Y: 11.5; control2X: 21.2; control2Y: 18.2 }
            }
            ShapePath {
                strokeWidth: 0.95
                strokeColor: Qt.alpha("#ffffff", 0.85)
                fillColor: "transparent"
                startX: 8; startY: 7.4
                PathQuad { x: 8.6; y: 2.2; controlX: 6.0 + root.shimmer * 2.8; controlY: 4.8 }
                PathMove { x: 12.8; y: 7.6 }
                PathQuad { x: 13.4; y: 2.6; controlX: 15.0 - root.shimmer * 2.8; controlY: 5.0 }
            }
        }
    }

    Item {
        id: thoughtProp
        objectName: "wullAmbientThought"
        visible: root.propKind === "thought"
        width: 28
        height: 24
        x: 38 + root.thoughtAnchor.x - width * 0.5
        y: 46.14 - root.thoughtAnchor.y - height * 0.5 + root.lift + (root.shimmer - 0.5) * 1.8
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
                        width: 2.2; height: 2.2; radius: 1.1
                        color: "#1e4460"
                    }
                }
            }
        }
    }
}
