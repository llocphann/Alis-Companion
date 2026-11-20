import QtQuick
import QtQuick.Shapes
import "OctoRig.js" as OctoRig

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

    readonly property var crownAnchor: body
        ? body.project(0, octopus ? OctoRig.geometry.headOffset + 35 : 37, 6)
        : ({ x: 0, y: 37, z: 6, scale: 1 })
    readonly property real baseX: 38 + (crownAnchor.x ?? 0)
    readonly property real baseY: 46.14 - (crownAnchor.y ?? 37) + floatOffset
    readonly property real anchorScale: Math.max(0.75, Math.min(1.25, crownAnchor.scale ?? 1.0))

    readonly property bool isTool: thinkingStage === "tool"
    readonly property bool isDrafting: thinkingStage === "drafting"
    readonly property real orbitCycles: isTool ? 2.0 : 1.0

    readonly property color inkRim: Qt.hsla(
        Math.max(0, accentColor.hslHue),
        Math.min(0.85, accentColor.hslSaturation * 0.72),
        0.08,
        0.80
    )
    readonly property color ringPrimary: isTool
        ? "#58f6ff"
        : (isDrafting ? "#ffe08a" : Qt.lighter(accentColor, 1.32))
    readonly property color starFill: isTool
        ? "#d8ffff"
        : (isDrafting ? "#fff6d1" : "#fff3b8")
    readonly property color starStroke: isTool ? "#084858" : inkRim

    readonly property real outerScale: 0.96 + thinkingWave * 0.08
    readonly property real outerTiltDeg: -12 + Math.sin(thinkingPhase * Math.PI * 2) * 8
    readonly property real outerRx: 27 * outerScale
    readonly property real outerRy: 8 * outerScale
    readonly property real outerAngle: thinkingPhase * Math.PI * 2 * orbitCycles

    readonly property real innerScale: 1.02 - thinkingWave * 0.07
    readonly property real innerTiltDeg: 16 - Math.sin(thinkingPhase * Math.PI * 2) * 10
    readonly property real innerRx: 18 * innerScale
    readonly property real innerRy: 5 * innerScale
    readonly property real innerAngle: -thinkingPhase * Math.PI * 2 + Math.PI * 0.65

    function ellipsePoint(rx: real, ry: real, cy: real, angle: real, tiltDeg: real): var {
        const ex = rx * Math.cos(angle);
        const ey = ry * Math.sin(angle);
        const tr = tiltDeg * Math.PI / 180;
        const ct = Math.cos(tr);
        const st = Math.sin(tr);
        return {
            x: ex * ct - ey * st,
            y: cy + ex * st + ey * ct,
            depth: Math.sin(angle)
        };
    }

    Item {
        id: orreryRoot
        x: root.baseX
        y: root.baseY
        width: 0
        height: 0
        opacity: Math.max(0, Math.min(1, root.thinkingBlend))
        scale: root.anchorScale * (0.72 + 0.28 * root.thinkingBlend)

        // 1. Soft bioluminescent crown radial backglow
        Shape {
            id: crownBackglow
            x: -width * 0.5
            y: -4 - height * 0.5
            width: 68
            height: 40
            z: 0
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0
                fillGradient: RadialGradient {
                    centerX: crownBackglow.width * 0.5
                    centerY: crownBackglow.height * 0.5
                    centerRadius: crownBackglow.width * 0.5
                    focalX: centerX
                    focalY: centerY
                    GradientStop { position: 0.0; color: Qt.alpha(root.ringPrimary, 0.42 + root.thinkingWave * 0.24) }
                    GradientStop { position: 0.55; color: Qt.alpha(root.accentColor, 0.18 + root.thinkingWave * 0.14) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                startX: 0; startY: crownBackglow.height * 0.5
                PathArc { x: crownBackglow.width; y: crownBackglow.height * 0.5; radiusX: crownBackglow.width * 0.5; radiusY: crownBackglow.height * 0.5 }
                PathArc { x: 0; y: crownBackglow.height * 0.5; radiusX: crownBackglow.width * 0.5; radiusY: crownBackglow.height * 0.5 }
            }
        }

        // 2. Expanding concentric synapse/sonar wave
        Shape {
            id: thoughtPulseWave
            objectName: "aquaThinkingPulseWave"
            x: -width * 0.5
            y: -3 - height * 0.5
            width: 62
            height: 22
            z: 0
            scale: 0.48 + root.thinkingPhase * 0.96
            opacity: Math.sin(root.thinkingPhase * Math.PI) * 0.80
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: "transparent"
                strokeColor: Qt.alpha(root.inkRim, 0.38)
                strokeWidth: 2.2
                startX: 2; startY: thoughtPulseWave.height * 0.5
                PathArc { x: thoughtPulseWave.width - 2; y: thoughtPulseWave.height * 0.5; radiusX: thoughtPulseWave.width * 0.5 - 2; radiusY: thoughtPulseWave.height * 0.5 - 2 }
                PathArc { x: 2; y: thoughtPulseWave.height * 0.5; radiusX: thoughtPulseWave.width * 0.5 - 2; radiusY: thoughtPulseWave.height * 0.5 - 2 }
            }
            ShapePath {
                fillColor: "transparent"
                strokeColor: Qt.alpha(root.ringPrimary, 0.95)
                strokeWidth: 1.3
                startX: 2; startY: thoughtPulseWave.height * 0.5
                PathArc { x: thoughtPulseWave.width - 2; y: thoughtPulseWave.height * 0.5; radiusX: thoughtPulseWave.width * 0.5 - 2; radiusY: thoughtPulseWave.height * 0.5 - 2 }
                PathArc { x: 2; y: thoughtPulseWave.height * 0.5; radiusX: thoughtPulseWave.width * 0.5 - 2; radiusY: thoughtPulseWave.height * 0.5 - 2 }
            }
        }

        // 3. Primary precessing astrolabe ring (always visible while effects are on)
        Item {
            id: outerAstrolabe
            objectName: "aquaThinkingOuterRing"
            x: -width * 0.5
            y: -3 - height * 0.5
            width: 58
            height: 20
            z: 2
            rotation: root.outerTiltDeg
            scale: root.outerScale

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: "transparent"
                    strokeColor: Qt.alpha(root.inkRim, 0.46)
                    strokeWidth: 2.8
                    startX: 2; startY: outerAstrolabe.height * 0.5
                    PathArc { x: outerAstrolabe.width - 2; y: outerAstrolabe.height * 0.5; radiusX: outerAstrolabe.width * 0.5 - 2; radiusY: outerAstrolabe.height * 0.5 - 2 }
                    PathArc { x: 2; y: outerAstrolabe.height * 0.5; radiusX: outerAstrolabe.width * 0.5 - 2; radiusY: outerAstrolabe.height * 0.5 - 2 }
                }
                ShapePath {
                    fillColor: "transparent"
                    strokeColor: Qt.alpha(root.ringPrimary, 0.95)
                    strokeWidth: 1.6
                    startX: 2; startY: outerAstrolabe.height * 0.5
                    PathArc { x: outerAstrolabe.width - 2; y: outerAstrolabe.height * 0.5; radiusX: outerAstrolabe.width * 0.5 - 2; radiusY: outerAstrolabe.height * 0.5 - 2 }
                    PathArc { x: 2; y: outerAstrolabe.height * 0.5; radiusX: outerAstrolabe.width * 0.5 - 2; radiusY: outerAstrolabe.height * 0.5 - 2 }
                }
                ShapePath {
                    fillColor: "transparent"
                    strokeColor: Qt.alpha("#ffffff", 0.72)
                    strokeWidth: 0.65
                    startX: 2; startY: outerAstrolabe.height * 0.5
                    PathArc { x: outerAstrolabe.width - 2; y: outerAstrolabe.height * 0.5; radiusX: outerAstrolabe.width * 0.5 - 2; radiusY: outerAstrolabe.height * 0.5 - 2 }
                    PathArc { x: 2; y: outerAstrolabe.height * 0.5; radiusX: outerAstrolabe.width * 0.5 - 2; radiusY: outerAstrolabe.height * 0.5 - 2 }
                }
            }

            // Opposite-pole astrolabe diamond node ticks
            Repeater {
                model: 2
                Rectangle {
                    required property int index
                    width: 4.5
                    height: 4.5
                    radius: 0.8
                    rotation: 45
                    x: (index === 0 ? 2 : outerAstrolabe.width - 2) - width * 0.5
                    y: outerAstrolabe.height * 0.5 - height * 0.5
                    color: root.starFill
                    border.width: 0.85
                    border.color: root.starStroke
                }
            }
        }

        // 4. Inner counter-tilted astrolabe ring
        Item {
            id: innerAstrolabe
            objectName: "aquaThinkingInnerRing"
            visible: root.qualityLevel > 0
            x: -width * 0.5
            y: -5 - height * 0.5
            width: 40
            height: 14
            z: 2
            rotation: root.innerTiltDeg
            scale: root.innerScale

            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor: "transparent"
                    strokeColor: Qt.alpha(root.inkRim, 0.38)
                    strokeWidth: 2.2
                    startX: 2; startY: innerAstrolabe.height * 0.5
                    PathArc { x: innerAstrolabe.width - 2; y: innerAstrolabe.height * 0.5; radiusX: innerAstrolabe.width * 0.5 - 2; radiusY: innerAstrolabe.height * 0.5 - 2 }
                    PathArc { x: 2; y: innerAstrolabe.height * 0.5; radiusX: innerAstrolabe.width * 0.5 - 2; radiusY: innerAstrolabe.height * 0.5 - 2 }
                }
                ShapePath {
                    fillColor: "transparent"
                    strokeColor: Qt.alpha(root.starFill, 0.88)
                    strokeWidth: 1.25
                    startX: 2; startY: innerAstrolabe.height * 0.5
                    PathArc { x: innerAstrolabe.width - 2; y: innerAstrolabe.height * 0.5; radiusX: innerAstrolabe.width * 0.5 - 2; radiusY: innerAstrolabe.height * 0.5 - 2 }
                    PathArc { x: 2; y: innerAstrolabe.height * 0.5; radiusX: innerAstrolabe.width * 0.5 - 2; radiusY: innerAstrolabe.height * 0.5 - 2 }
                }
            }
        }

        // 5a. Comet-tail pearls trailing the primary celestial star
        Repeater {
            model: 3
            Rectangle {
                required property int index
                readonly property var pt: root.ellipsePoint(
                    root.outerRx, root.outerRy, -3,
                    root.outerAngle - (index + 1) * 0.24,
                    root.outerTiltDeg
                )
                readonly property real depthWeight: 0.72 + 0.28 * pt.depth
                width: 6.4 - index * 1.5
                height: width
                radius: width * 0.5
                x: pt.x - width * 0.5
                y: pt.y - height * 0.5
                z: pt.depth >= 0 ? 3 : 1
                opacity: (index === 0 ? 0.90 : index === 1 ? 0.64 : 0.38) * depthWeight
                color: root.starFill
                border.width: 0.9
                border.color: root.starStroke
            }
        }

        // 5b. Hero 4-pointed celestial star riding the outer astrolabe
        Item {
            id: heroStarWrap
            readonly property var pt: root.ellipsePoint(
                root.outerRx, root.outerRy, -3,
                root.outerAngle,
                root.outerTiltDeg
            )
            width: 14
            height: 14
            x: pt.x - width * 0.5
            y: pt.y - height * 0.5
            z: pt.depth >= 0 ? 4 : 1
            scale: 1.01 + pt.depth * 0.19
            rotation: root.thinkingPhase * 180

            Shape {
                id: heroStar
                objectName: "aquaThinkingHeroStar"
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: 1.05
                    strokeColor: root.starStroke
                    fillColor: root.starFill
                    startX: 7; startY: 1.2
                    PathLine { x: 8.6; y: 5.4 }
                    PathLine { x: 12.8; y: 7 }
                    PathLine { x: 8.6; y: 8.6 }
                    PathLine { x: 7; y: 12.8 }
                    PathLine { x: 5.4; y: 8.6 }
                    PathLine { x: 1.2; y: 7 }
                    PathLine { x: 5.4; y: 5.4 }
                    PathLine { x: 7; y: 1.2 }
                }
            }

            Rectangle {
                anchors.centerIn: parent
                width: 3.2
                height: 3.2
                radius: 1.6
                color: "#ffffff"
            }
        }

        // 5c. Counter-orbiting twin 4-pointed star on the inner ring
        Item {
            id: twinStarWrap
            visible: root.qualityLevel > 0
            readonly property var pt: root.ellipsePoint(
                root.innerRx, root.innerRy, -5,
                root.innerAngle,
                root.innerTiltDeg
            )
            width: 10.5
            height: 10.5
            x: pt.x - width * 0.5
            y: pt.y - height * 0.5
            z: pt.depth >= 0 ? 4 : 1
            scale: 0.96 + pt.depth * 0.16
            rotation: -root.thinkingPhase * 180

            Shape {
                id: twinStar
                objectName: "aquaThinkingTwinStar"
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: 0.9
                    strokeColor: root.starStroke
                    fillColor: root.ringPrimary
                    startX: 5.25; startY: 1.0
                    PathLine { x: 6.45; y: 4.05 }
                    PathLine { x: 9.5; y: 5.25 }
                    PathLine { x: 6.45; y: 6.45 }
                    PathLine { x: 5.25; y: 9.5 }
                    PathLine { x: 4.05; y: 6.45 }
                    PathLine { x: 1.0; y: 5.25 }
                    PathLine { x: 4.05; y: 4.05 }
                    PathLine { x: 5.25; y: 1.0 }
                }
            }
        }

        // 6. Rising crystalline idea sparks
        Repeater {
            model: 4
            Item {
                id: ideaSpark
                required property int index
                readonly property real p: (root.thinkingPhase + index * 0.25) % 1
                readonly property real fade: Math.sin(p * Math.PI)
                readonly property bool isStar: index % 2 === 0
                width: isStar ? 8.5 : 6.0
                height: width
                x: (index - 1.5) * 9.5 + Math.sin(p * Math.PI * 2 + index * 1.4) * 3.5 - width * 0.5
                y: -6 - p * 24 - height * 0.5
                z: 3
                opacity: fade * 0.95
                scale: 0.65 + fade * 0.45
                rotation: isStar ? p * 135 * (index === 0 ? 1 : -1) : 0

                Shape {
                    anchors.fill: parent
                    visible: ideaSpark.isStar
                    antialiasing: true
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeWidth: 0.85
                        strokeColor: root.inkRim
                        fillColor: root.starFill
                        startX: 4.25; startY: 0.8
                        PathLine { x: 5.2; y: 3.3 }
                        PathLine { x: 7.7; y: 4.25 }
                        PathLine { x: 5.2; y: 5.2 }
                        PathLine { x: 4.25; y: 7.7 }
                        PathLine { x: 3.3; y: 5.2 }
                        PathLine { x: 0.8; y: 4.25 }
                        PathLine { x: 3.3; y: 3.3 }
                        PathLine { x: 4.25; y: 0.8 }
                    }
                }

                Rectangle {
                    anchors.fill: parent
                    visible: !ideaSpark.isStar
                    radius: width * 0.5
                    color: root.ringPrimary
                    border.width: 0.85
                    border.color: root.starStroke
                    Rectangle {
                        width: 2.0
                        height: 2.0
                        radius: 1.0
                        x: parent.width * 0.24
                        y: parent.height * 0.20
                        color: "#ffffff"
                    }
                }
            }
        }
    }
}
