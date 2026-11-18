import QtQuick
import QtQuick.Shapes
import QtQuick.Window
import qs.modules.abyss.looks
import qs.modules.common
import qs.modules.common.functions
import "AquaExpressions.js" as Expressions
import "AquaPreferences.js" as Preferences
import "CompanionMotion.js" as Motion
import "AquaPose.js" as Pose
import "OctoRig.js" as OctoRig

Item {
    id: root
    objectName: "aquaLiquidBody"
    property string character: "aqua"
    property bool tentacleGrip: false
    property real gripProgress: 0
    readonly property bool octopus: character==="octo"
    readonly property var curves: Motion.forCharacter(character)
    readonly property var headCenter: octopus ? project(0,OctoRig.geometry.headOffset,0) : ({x:0,y:0,z:0,scale:1})
    readonly property real headSize: OctoRig.geometry.headSize
    property string expression: "idle"
    property color accentColor: AbyssStyle.accent
    property real hoverAmount: hovered ? 1 : 0
    readonly property color liquidAccent: ColorUtils.colorWithLightness(accentColor,
        Math.min(.82,accentColor.hslLightness+hoverAmount*.035+tapPulse*.018))
    readonly property color reflectionColor: ColorUtils.colorWithLightness(accentColor, 0.85+hoverAmount*.04)
    property real energy: 0.45
    property real gazeX: 0
    property real gazeY: 0
    property bool motionEnabled: AbyssStyle.motionEnabled && visible
    property bool quietSettled: false
    property real motionScale: 1
    property bool effectsEnabled: Appearance.effectsEnabled && renderQuality !== "performance"
    property string powerProfile: AbyssRenderPolicy.powerProfile
    property bool bubblesEnabled: root.effectsEnabled
    property bool nearbyInteraction: false
    readonly property bool bubble2DMode: root.powerProfile === "power-saver"
    property bool floorReflectionEnabled: false
    property string renderQuality: "quality"
    property real translucency: 0.16
    readonly property int qualityLevel: Preferences.renderTier(renderQuality)
    readonly property bool detailedEffects: effectsEnabled && qualityLevel > 0
    property real squash: 0
    property real bob: 0
    property real sway: 0
    property real shimmer: 0
    property real orbitPhase: 0
    readonly property int orbitStep: Math.floor((((orbitPhase % 1) + 1) % 1) * 720)
    readonly property real orbitAngle: orbitStep * (Math.PI * 2 / 720)
    readonly property real orbitBaseCos: Math.cos(orbitAngle)
    readonly property real orbitBaseSin: Math.sin(orbitAngle)
    property real stateSquash: 0
    property real stateStretch: 0
    property real stateLean: 0
    property real stateTip: 0
    property real orientationAngle: 0
    property real viewYaw: -15
    property real viewPitch: 0
    property real rollingAngle: 0 // Legacy input retained but no longer controls body pose.
    property var coworkPose: null
    property bool coworkPropVisible: false
    readonly property real modelRoll:gait.roll+(coworkPose?.roll ?? 0)
    readonly property real modelYaw: viewYaw+gait.yaw+(coworkPose?.yaw ?? 0)
    readonly property real modelPitch: viewPitch+gait.pitch+(coworkPose?.pitch ?? (ambientProp==="book" ? 5 : 0))
    // These three angles feed five ShaderEffect instances (torso + four
    // limbs). Convert each shared pose angle once per pose revision instead of
    // repeating identical degree-to-radian work in every consumer.
    readonly property real modelYawRadians: modelYaw * Math.PI / 180
    readonly property real modelPitchRadians: modelPitch * Math.PI / 180
    readonly property real modelRollRadians: modelRoll * Math.PI / 180
    // Shader uniforms below are identical across repeated limb/droplet
    // delegates. Construct each shared QVector4D once per input revision.
    readonly property vector4d materialRenderingUniform: Qt.vector4d(qualityLevel, translucency, 0, 0)
    // Small hands/feet/orbital droplets cover very few pixels but otherwise
    // pay the same tiered liquid ray cost as the 76 px torso. Tier 0 keeps the
    // same implicit surface/material shader while dropping high-tier secondary
    // traces and studio-light detail that is sub-pixel at these sizes.
    readonly property vector4d smallMaterialRenderingUniform: Qt.vector4d(0, translucency, 0, 0)
    readonly property vector4d zeroPoseUniform: Qt.vector4d(0, 0, 0, 0)
    readonly property vector4d limbMotionUniform: (octopus || tentacleGrip) ? Qt.vector4d(shimmer, 0, pulse, effectsEnabled ? 1 : 0) : zeroPoseUniform
    readonly property vector4d limbOpticsUniform: (octopus || tentacleGrip) ? Qt.vector4d(modelYawRadians, 3, 0, 0) : zeroPoseUniform
    readonly property vector4d handPoseUniform: zeroPoseUniform
    readonly property vector4d footPoseUniform: zeroPoseUniform
    readonly property vector4d limbPoseUniform: zeroPoseUniform
    readonly property vector4d dropletMotionUniform: Qt.vector4d(0.35, 0, Math.round(pulse * 20) / 20, 0)
    readonly property vector4d dropletOpticsUniform: Qt.vector4d(0, 1, 0, 0)
    readonly property var poseRotation: Pose.rotation(modelYaw,modelPitch,modelRoll)
    readonly property real poseScaleX: gait.scaleX
    readonly property real poseScaleY: gait.scaleY
    readonly property real poseDepthScale: 1 / (poseScaleX * poseScaleY)
    function project(x,y,z): var {return Pose.project(poseRotation,x,y,z,poseScaleX,poseScaleY)}
    function projectX(x: real, y: real, z: real): real {
        const r = poseRotation
        return r[0] * (x * poseScaleX) + r[1] * (y * poseScaleY) + r[2] * (z * poseDepthScale)
    }
    function projectY(x: real, y: real, z: real): real {
        const r = poseRotation
        return r[3] * (x * poseScaleX) + r[4] * (y * poseScaleY) + r[5] * (z * poseDepthScale)
    }
    function projectZ(x: real, y: real, z: real): real {
        const r = poseRotation
        return r[6] * (x * poseScaleX) + r[7] * (y * poseScaleY) + r[8] * (z * poseDepthScale)
    }
    property bool walking: false
    property bool flying: false
    property bool grounded: true
    property bool dragging: false
    property bool traveling: false
    property string ambientProp: ""
    property string motionAction: ""
    property real motionProgress: -1
    property real walkingDirection: 1
    property real ripple: 0
    property real eyeOpen: 1
    property real mouthCurve: 0.12
    property real pulse: 0
    property real reveal: 1
    property real reactionPhase: 1
    readonly property real reactionLift: curves.sample("hop", "lift", reactionPhase) * 0.6
    property real reactionRipple: 0
    property real shine: 0
    property real tapPhase: 1
    property real tapPulse: 0
    property real tapX: 38
    property real tapY: 48
    property bool pocketDragActive: false
    property real swallowPhase: 1
    property real chompPhase: 0
    property int swallowCount: 1
    property real swallowStartX: 38
    property real swallowStartY: 8
    readonly property var expressionProfile: Expressions.profile(expression)
    property real expressionSquash: expressionProfile.squash
    readonly property real motionAmount: motionEnabled ? AbyssStyle.motionIntensity * motionScale : 0
    readonly property bool hovered: hoverHandler.hovered
    // Qt reports handler positions in this item's already rotated coordinates.
    readonly property real hoverGazeX: Math.max(-1,Math.min(1,(hoverHandler.point.position.x-width/2)/(width/2)))
    readonly property real hoverGazeY: Math.max(-1,Math.min(1,(hoverHandler.point.position.y-height/2)/(height/2)))
    // QSB reflection reuse may leave status Uncompiled after drawing. A real
    // presented frame and a supported API are the authoritative readiness.
    property bool framePresented: false
    readonly property bool softwareFallback: GraphicsInfo.api === GraphicsInfo.Software
    readonly property bool materialReady: framePresented && GraphicsInfo.api !== GraphicsInfo.Software
        && GraphicsInfo.api !== GraphicsInfo.Null && material.status !== ShaderEffect.Error
    Window.onWindowChanged: root.framePresented = false
    Connections {
        target: root.Window.window
        enabled: !root.framePresented
        function onFrameSwapped(): void { root.framePresented = true }
    }
    signal chatRequested()
    signal pocketRequested()
    signal pressed()
    signal settingsRequested()
    function reactToTap(px,py): void {
        pressed()
        if (!motionEnabled) return
        tapX=Math.max(8,Math.min(width-8,px)); tapY=Math.max(12,Math.min(height-12,py))
        squashBurst.restart(); tapBurst.restart()
        reactionBounce.restart()
    }
    function triggerPocketSwallow(count = 1, startX = 38, startY = 8): void {
        swallowCount = Math.max(1, Math.min(4, Number(count) || 1))
        swallowStartX = Math.max(-640, Math.min(width + 640, Number.isFinite(startX) ? startX : 38))
        swallowStartY = Math.max(-640, Math.min(height + 640, Number.isFinite(startY) ? startY : 8))
        if (!motionEnabled) {
            swallowPhase = 1
            chompPhase = 0
            return
        }
        swallowBurst.restart()
        squashBurst.restart()
        reactionBounce.restart()
        if (effectsEnabled) shineBurst.restart()
    }

    AquaMotion {
        id: gait
        character: root.character
        walking: root.walking
        flying: root.flying
        action: root.motionAction
        progress: root.motionProgress
        direction: Math.abs(root.walkingDirection)>.05 ? root.walkingDirection : 1
        motionEnabled: root.motionEnabled && root.visible
    }
    AquaMotion {
        id: gripMotion
        character: "octo"
        action: root.tentacleGrip ? "pull" : ""
        progress: root.gripProgress
        motionEnabled: root.tentacleGrip && root.motionEnabled && root.visible
    }

    // Preserve placement and native input bounds. The body itself is square.
    implicitWidth: 76
    implicitHeight: 92
    transformOrigin: Item.Bottom
    scale: 1 + squash * 0.035 + hoverAmount * 0.015
    rotation: orientationAngle + ((gait.active ? 0 : sway * 2.2) + stateLean * 5.0 + stateTip * 2.4 + expressionProfile.tilt * 4) * motionAmount
    transform: [
        Scale {
            origin.x: root.width * 0.5; origin.y: root.octopus ? 74.5 : 77.5
            xScale: 1 + (root.stateSquash + root.expressionSquash) * 0.05 - root.stateStretch * 0.025 + root.squash * 0.06
            yScale: 1 - (root.stateSquash + root.expressionSquash) * 0.035 + root.stateStretch * 0.06 - root.squash * 0.05
        },
        Translate {
            y: (gait.active || root.grounded ? 0 : root.bob) * root.motionAmount + (1 - root.reveal) * 6 * root.motionAmount
        }
    ]
    Item {
        id: contact
        visible: root.floorReflectionEnabled && root.grounded
        width: root.qualityLevel > 1 ? 102 : 94
        height: root.qualityLevel > 1 ? 18 : root.qualityLevel > 0 ? 15 : 13
        x: (root.width - width) * 0.5
        y: 78.3 - height * 0.5
        z: -1
        Shape {
            anchors.fill: parent
            antialiasing: true
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeWidth: 0
                fillGradient: RadialGradient {
                    centerX: contact.width * 0.5; centerY: contact.height * 0.5; centerRadius: contact.width * 0.5
                    focalX: centerX; focalY: centerY
                    GradientStop { position: 0; color: Qt.alpha(root.liquidAccent, 0.18) }
                    GradientStop { position: 1; color: "transparent" }
                }
                startX: 0; startY: contact.height * 0.5
                PathArc { x: contact.width; y: contact.height * 0.5; radiusX: contact.width * 0.5; radiusY: contact.height * 0.5 }
                PathArc { x: 0; y: contact.height * 0.5; radiusX: contact.width * 0.5; radiusY: contact.height * 0.5 }
            }
        }
        Repeater {
            model: (contact.visible && (root.softwareFallback || material.status === ShaderEffect.Error || !root.detailedEffects)) ? 3 : 0
            Shape {
                id: rippleRing
                required property int index
                visible: root.softwareFallback || material.status === ShaderEffect.Error || !root.detailedEffects
                anchors.centerIn: parent
                width: contact.width * (0.55 + index * 0.18)
                height: contact.height * (0.50 + index * 0.24)
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                scale: 1 + Math.max(root.ripple, root.reactionRipple) * (0.03 + index * 0.015) + root.shimmer * 0.02
                ShapePath {
                    fillColor: "transparent"; strokeWidth: 0.65
                    strokeColor: Qt.alpha(root.liquidAccent, 0.78 - rippleRing.index * 0.20)
                    startX: 0; startY: rippleRing.height / 2
                    PathArc { x: rippleRing.width; y: rippleRing.height / 2; radiusX: rippleRing.width / 2; radiusY: rippleRing.height / 2 }
                    PathArc { x: 0; y: rippleRing.height / 2; radiusX: rippleRing.width / 2; radiusY: rippleRing.height / 2 }
                }
            }
        }
        ShaderEffect {
            id: contactShader
            anchors.fill: parent
            visible: contact.visible && root.detailedEffects && !root.softwareFallback && material.status !== ShaderEffect.Error
            property var surfaceSource: reflectionSource
            property color accent: root.liquidAccent
            property color specular: root.reflectionColor
            property vector4d motion: visible ? Qt.vector4d(root.shimmer, Math.max(root.ripple, root.reactionRipple), contact.width / material.width, 0) : root.zeroPoseUniform
            property vector4d rendering: Qt.vector4d(root.qualityLevel, 0, 0, 0)
            property vector4d feet: visible ? Qt.vector4d(
                0.5, 0.5,
                Math.max(0, 1 - Math.abs(gait.lift) / 10),
                Math.max(0, 1 - Math.abs(gait.lift) / 10)) : root.zeroPoseUniform
            fragmentShader: Qt.resolvedUrl("WaterDropletContact.frag.qsb")
        }
    }
    ShaderEffectSource {
        id: reflectionSource
        objectName: "aquaFloorReflectionSource"
        sourceItem: root.floorReflectionEnabled && root.grounded && root.detailedEffects && !root.softwareFallback && material.status !== ShaderEffect.Error ? reflectionLayer : null
        sourceRect: Qt.rect(0, 7, 76, 82)
        // The reflection is finally compressed into a 13-18 px-tall puddle
        // and sampled with a three-tap softening kernel. A 2x capture at tier 2
        // quadruples offscreen pixels without preserving visible extra detail.
        textureSize: Qt.size(76, 82)
        smooth: true
        live: root.floorReflectionEnabled && root.visible && root._windowVisible && root.grounded && root.detailedEffects && (!root.idleLoopPaused || gait.active)
        visible: false
    }
    // Reflect only our body, bubbles and face; never desktop content.
    Item {
        id: reflectionLayer
        width: root.width; height: root.height
        // Aqua is a pure limbless water droplet; Octo uses its tentacle rig below.
        Repeater {
            objectName: "aquaWaterLimbs"
            model: 0
            Item {}
        }
        Loader {
            active: root.octopus
            sourceComponent: OctoTentacles {
                body: root
                motion: gait
            }
        }
        Loader {
            active: root.tentacleGrip && !root.octopus
            z: -.5
            sourceComponent: OctoTentacles {
                body: root;motion: gripMotion;gripping: true;tentacleIndices: [1,2]
                objectName: "octoGripBack"
                opacity: Math.min(1,gripMotion.sample("gripRise")*4)
            }
        }
        Loader {
            active: root.tentacleGrip && !root.octopus
            z: .5
            sourceComponent: OctoTentacles {
                body: root;motion: gripMotion;gripping: true;tentacleIndices: [0,3]
                objectName: "octoGripFront"
                opacity: Math.min(1,gripMotion.sample("gripRise")*4)
            }
        }
        Item {
            id: torso
            width: root.width; height: root.height
            y: gait.lift + (root.reactionLift + (gait.active || !root.grounded ? 0 : root.bob * 0.35)) * root.motionAmount
            // Rounded vector fallback for software/error; volume optics require a GPU.
            Loader {
                active: root.softwareFallback || material.status === ShaderEffect.Error
                anchors.fill: parent
                sourceComponent: Item {
                    Rectangle {
                        visible: root.octopus
                        x: material.x; y: material.y; width: material.width; height: material.height
                        radius: width / 2
                        gradient: Gradient {
                            GradientStop { position: 0; color: Qt.darker(root.liquidAccent, 1.7) }
                            GradientStop { position: .6; color: root.liquidAccent }
                            GradientStop { position: 1; color: root.reflectionColor }
                        }
                    }
                    Shape {
                        x: 0; y: 7; width: 76; height: 76
                        visible: !root.octopus
                        opacity: 1 - Math.max(0, Math.min(0.35, root.translucency)) * 0.55
                        antialiasing: true
                        preferredRendererType: Shape.CurveRenderer
                        ShapePath {
                            strokeWidth: 1; strokeColor: root.reflectionColor
                            fillGradient: LinearGradient {
                                x1: 38; y1: 0; x2: 38; y2: 76
                                GradientStop { position: 0; color: Qt.darker(root.liquidAccent, 2.2) }
                                GradientStop { position: 0.55; color: root.liquidAccent }
                                GradientStop { position: 1; color: Qt.lighter(root.liquidAccent, 1.65) }
                            }
                            startX: 38; startY: 4.2
                            PathCubic { x: 15; y: 28.5; control1X: 32; control1Y: 14; control2X: 23; control2Y: 21 }
                            PathCubic { x: 5.1; y: 47.3; control1X: 8; control1Y: 34; control2X: 5.1; control2Y: 41 }
                            PathCubic { x: 38; y: 69.9; control1X: 5.1; control1Y: 62; control2X: 16; control2Y: 69.9 }
                            PathCubic { x: 70.9; y: 47.3; control1X: 60; control1Y: 69.9; control2X: 71; control2Y: 62 }
                            PathCubic { x: 61; y: 28.5; control1X: 70.9; control1Y: 41; control2X: 68; control2Y: 34 }
                            PathCubic { x: 38; y: 4.2; control1X: 53; control1Y: 21; control2X: 44; control2Y: 14 }
                        }
                    }
                }
            }
            ShaderEffect {
                id: material
                objectName:"aquaVolumeMaterial"
                x: root.octopus ? 38+root.headCenter.x-root.headSize/2 : 0
                y: root.octopus ? 46.14-root.headCenter.y-root.headSize/2 : 7
                width: root.octopus ? root.headSize : 76; height: width
                visible: GraphicsInfo.api !== GraphicsInfo.Software
                property color accent: root.liquidAccent
                property color specular: root.reflectionColor
                property vector4d motion: Qt.vector4d(root.shimmer, root.stateTip + root.sway * 0.25,
                    Math.max(root.pulse,root.tapPulse*.65) + root.hoverAmount*.16, root.effectsEnabled ? 1 : 0)
                property vector4d optics: Qt.vector4d(root.modelYawRadians, root.octopus ? 4 : 0, 0, 0)
                property vector4d rendering: root.materialRenderingUniform
                property vector4d pose:Qt.vector4d(root.modelPitchRadians,root.modelRollRadians,root.poseScaleX-1,root.poseScaleY-1)
                fragmentShader: Qt.resolvedUrl("WaterDropletMaterial.frag.qsb")
            }
            // Decorative droplets fit the enclosing host; the body hitbox stays 76x92.
            Repeater {
                objectName: "aquaExternalDroplets"
                model: !root.bubblesEnabled ? 0 : root.qualityLevel > 1 ? 8 : root.qualityLevel > 0 ? 6 : 3
                Item {
                    id: floatingDroplet
                    required property int index
                    readonly property real phaseCos: Expressions.bubbleCos[index]
                    readonly property real phaseSin: Expressions.bubbleSin[index]
                    readonly property real orbitCos: root.bubble2DMode ? phaseCos : (root.orbitBaseCos * phaseCos - root.orbitBaseSin * phaseSin)
                    readonly property real orbitSin: root.bubble2DMode ? phaseSin : (root.orbitBaseSin * phaseCos + root.orbitBaseCos * phaseSin)
                    readonly property real localX: 47 * orbitCos
                    readonly property real localY: 4.14 - 37 * orbitSin
                    readonly property real localZ: root.bubble2DMode ? 0 : 25 * orbitSin
                    readonly property real depth: root.bubble2DMode ? 1 : root.projectZ(localX, localY, localZ)
                    x: (root.bubble2DMode ? (38 + localX * 0.92) : (38 + root.projectX(localX, localY, localZ))) - width / 2
                    y: (root.bubble2DMode ? (46.14 - localY * 0.92) : (46.14 - root.projectY(localX, localY, localZ))) - height / 2
                    z: root.bubble2DMode ? 1 : (depth < 0 ? -1 : 2)
                    opacity: root.bubble2DMode ? 0.9 : (depth < 0 ? 0.78 : 1)
                    width: Expressions.dropletSize[index]; height: width
                    Rectangle {
                        objectName: "aquaDroplet2D"
                        anchors.fill: parent
                        visible: root.bubble2DMode
                        radius: width * 0.5
                        border.width: width >= 8 ? 0.85 : 0.6
                        border.color: Qt.alpha(root.reflectionColor, 0.78)
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: Qt.alpha(root.reflectionColor, 0.88) }
                            GradientStop { position: 0.42; color: Qt.alpha(root.liquidAccent, 0.42) }
                            GradientStop { position: 1.0; color: Qt.alpha(root.liquidAccent, 0.82) }
                        }
                        Rectangle {
                            visible: parent.width >= 6
                            width: Math.max(1.6, parent.width * 0.26)
                            height: width
                            x: parent.width * 0.22
                            y: parent.height * 0.18
                            radius: width * 0.5
                            color: Qt.alpha("#ffffff", 0.72)
                        }
                    }
                    ShaderEffect {
                        id: droplet3d
                        objectName: "aquaDroplet3D"
                        anchors.fill: parent
                        visible: !root.bubble2DMode && !root.softwareFallback && status !== ShaderEffect.Error && material.status !== ShaderEffect.Error
                        property color accent: root.liquidAccent
                        property color specular: root.reflectionColor
                        property vector4d motion: root.dropletMotionUniform
                        property vector4d optics: root.dropletOpticsUniform
                        property vector4d rendering: root.smallMaterialRenderingUniform
                        property vector4d pose: root.zeroPoseUniform
                        fragmentShader: Qt.resolvedUrl("WaterDropletMaterial.frag.qsb")
                    }
                    Loader {
                        objectName: "aquaDropletFallback"
                        anchors.fill: parent
                        active: !root.bubble2DMode && (root.softwareFallback || droplet3d.status === ShaderEffect.Error || material.status === ShaderEffect.Error)
                        sourceComponent: Shape {
                            antialiasing: true
                            preferredRendererType: Shape.CurveRenderer
                            ShapePath {
                                strokeWidth: 0.4; strokeColor: root.reflectionColor
                                fillGradient: LinearGradient {
                                    x1: 0; y1: 0; x2: 0; y2: floatingDroplet.height
                                    GradientStop { position: 0; color: root.reflectionColor }
                                    GradientStop { position: 0.4; color: Qt.alpha(root.liquidAccent, 0.18) }
                                    GradientStop { position: 1; color: root.liquidAccent }
                                }
                                startX: 0; startY: floatingDroplet.height / 2
                                PathArc { x: floatingDroplet.width; y: floatingDroplet.height / 2; radiusX: floatingDroplet.width / 2; radiusY: floatingDroplet.height / 2 }
                                PathArc { x: 0; y: floatingDroplet.height / 2; radiusX: floatingDroplet.width / 2; radiusY: floatingDroplet.height / 2 }
                            }
                        }
                    }
                }
            }
            Item {
                id: faceOverlay
                anchors.fill: parent
                transformOrigin: Item.Center
                opacity:Math.max(0,Math.min(1,root.poseRotation[8]*5))
                transform:Matrix4x4 {
                    matrix: {
                        const m=Pose.faceMatrix(root.poseRotation,root.poseScaleX,root.poseScaleY,38,46.14,root.octopus ? 24 : 26)
                        return Qt.matrix4x4(m[0],m[1],m[2],m[3],m[4],m[5],m[6],m[7],m[8],m[9],m[10],m[11],m[12],m[13],m[14],m[15])
                    }
                }
                WaterDropletFace {
                    width: parent.width; height: parent.height
                    y: root.octopus ? -7 : 0
                    scale: root.octopus ? .9 : 1
                    transformOrigin: Item.TopLeft
                    transform: Translate {x:root.octopus ? 3.8 : 0;y:root.octopus ? 5.4 : 0}
                    expression: root.expression
                    cheeksVisible: !root.octopus
                    viewYaw: root.modelYaw
                    accent: root.liquidAccent
                    eyeOpen: root.coworkPose?.eyeOpen !== undefined ? root.eyeOpen * root.coworkPose.eyeOpen : root.eyeOpen
                    mouthCurve: root.mouthCurve
                    // Pointer feedback is interpolated locally, never streamed over IPC.
                    // The actor projects observed coordinates into this face's
                    // orientation, including sideways and upper water rims.
                    gazeX: root.gazeX
                    gazeY: root.gazeY
                    microX: root.motionEnabled && !root.traveling ? root.sway*.16 : 0
                    microY: root.motionEnabled && !root.traveling ? root.bob*.055 : 0
                    pulse: root.pulse
                    feedingOpen: root.pocketDragActive || root.swallowPhase < 0.88
                    chompPhase: root.chompPhase
                    motionEnabled: root.motionEnabled
                    qualityLevel: root.qualityLevel
                }
            }
        }
    }
    Repeater {
        objectName: "aquaPocketSwallowItems"
        model: root.swallowPhase < 0.88 ? root.swallowCount : 0
        Item {
            id: swallowItem
            required property int index
            readonly property real progress: Math.max(0, Math.min(1, (root.swallowPhase - index * 0.07) / 0.76))
            readonly property real flightT: Math.min(1, progress / 0.72)
            readonly property real bellyT: Math.max(0, (progress - 0.72) / 0.28)
            readonly property real mouthX: 38 + (root.octopus ? 3.8 : 0)
            readonly property real mouthY: 60.5 + (root.octopus ? -1.6 : 0) + gait.lift + root.reactionLift * root.motionAmount
            readonly property real bellyX: 38 + (root.octopus ? 2.0 : 0)
            readonly property real bellyY: 74 + (root.octopus ? -2.0 : 0) + gait.lift
            readonly property real originX: root.swallowStartX + (index - (root.swallowCount - 1) * 0.5) * 9
            readonly property real originY: root.swallowStartY - index * 5
            readonly property real distToMouth: Math.hypot(mouthX - originX, mouthY - originY)
            readonly property real arcLift: Math.sin(flightT * Math.PI) * Math.min(28, 10 + distToMouth * 0.1)
            width: 13
            height: 16
            z: 5
            x: (progress < 0.72
                ? originX + (mouthX - originX) * flightT
                : mouthX + (bellyX - mouthX) * bellyT) - width * 0.5
            y: (progress < 0.72
                ? originY + (mouthY - originY) * (flightT * (0.4 + 0.6 * flightT)) - arcLift
                : mouthY + (bellyY - mouthY) * bellyT) - height * 0.5
            scale: progress < 0.72
                ? (1 - flightT * 0.45) * (root.swallowPhase >= index * 0.07 ? 1 : 0)
                : Math.max(0.08, 0.55 * (1 - bellyT * 0.82))
            rotation: (index % 2 === 0 ? -18 : 18) * (1 - flightT) + progress * 28
            opacity: progress < 0.78 ? (root.swallowPhase >= index * 0.07 ? 1 : 0) : Math.max(0, (1 - progress) / 0.22)
            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: 0.9
                    strokeColor: root.accentColor
                    fillColor: "#f4fcff"
                    startX: 1; startY: 1
                    PathLine { x: 8.5; y: 1 }
                    PathLine { x: 12; y: 4.5 }
                    PathLine { x: 12; y: 15 }
                    PathLine { x: 1; y: 15 }
                    PathLine { x: 1; y: 1 }
                }
                ShapePath {
                    strokeWidth: 0.7
                    strokeColor: root.accentColor
                    fillColor: "#bfefff"
                    startX: 8.5; startY: 1
                    PathLine { x: 8.5; y: 4.5 }
                    PathLine { x: 12; y: 4.5 }
                }
            }
            Rectangle {
                x: 3; y: 6.5; width: 6.5; height: 1.2; radius: 0.6
                color: "#3d7a99"
            }
            Rectangle {
                x: 3; y: 9.2; width: 5.0; height: 1.2; radius: 0.6
                color: "#3d7a99"
            }
        }
    }
    Item {
        id: workingOrbit
        x: 0; y: 34; width: 76; height: 14
        visible: root.expressionProfile.orbit && root.effectsEnabled
        rotation: visible ? -12 + root.shimmer * 24 : -12
        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: "transparent"; strokeWidth: 0.6
                strokeColor: Qt.alpha(root.liquidAccent, 0.65)
                startX: 0; startY: workingOrbit.height / 2
                PathArc { x: workingOrbit.width; y: workingOrbit.height / 2; radiusX: workingOrbit.width / 2; radiusY: workingOrbit.height / 2 }
                PathArc { x: 0; y: workingOrbit.height / 2; radiusX: workingOrbit.width / 2; radiusY: workingOrbit.height / 2 }
            }
        }
        Rectangle {
            x: workingOrbit.visible ? workingOrbit.width / 2 + (workingOrbit.width / 2 - 3) * Math.cos(root.shimmer * Math.PI * 2) - 1.5 : 0
            y: workingOrbit.visible ? workingOrbit.height / 2 + workingOrbit.height / 2 * Math.sin(root.shimmer * Math.PI * 2) - 1.5 : 0
            width: 3; height: 3; radius: 1.5; color: "#d7fbff"
        }
    }
    Loader {
        id: ambientPropLoader
        objectName: "aquaAmbientPropLoader"
        active: root.ambientProp !== "" && !root.coworkPropVisible && !root.walking && !root.flying && !root.dragging && !root.traveling
        z: 3
        sourceComponent: AquaAmbientProp {
            body: root
            propKind: root.ambientProp
            accentColor: root.liquidAccent
            specularColor: root.reflectionColor
            shimmer: root.shimmer
            lift: gait.lift + root.reactionLift * root.motionAmount
        }
    }
    Loader {
        id: laptopPropLoader
        objectName: "aquaLaptopPropLoader"
        active: root.coworkPropVisible && root.coworkPose !== null && !root.walking && !root.flying && !root.dragging && !root.traveling
        z: 3
        sourceComponent: AquaLaptopProp {
            body: root
            pose: root.coworkPose
            accentColor: root.liquidAccent
            specularColor: root.reflectionColor
            lift: gait.lift + root.reactionLift * root.motionAmount
        }
    }
    HoverHandler { id: hoverHandler; enabled: root.enabled }
    Repeater {
        model: root.effectsEnabled && root.tapPhase<1 ? 2 : 0
        Shape {
            id: tapRing
            required property int index
            objectName: "aquaTapRipple"+index
            width: 36+index*12; height: width
            x: root.tapX-width/2; y: root.tapY-height/2
            scale: .35+root.tapPhase*(1.1+index*.2)
            opacity: (1-root.tapPhase)*(.6-index*.15)
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: "transparent"; strokeColor: root.reflectionColor; strokeWidth: .8
                startX: 0; startY: tapRing.height/2
                PathArc { x: tapRing.width; y: tapRing.height/2; radiusX: tapRing.width/2; radiusY: tapRing.height/2 }
                PathArc { x: 0; y: tapRing.height/2; radiusX: tapRing.width/2; radiusY: tapRing.height/2 }
            }
        }
    }
    Repeater {
        model: root.effectsEnabled && root.shine > 0 ? 5 : 0
        Shape {
            required property int index
            x: Expressions.shineX[index]
            y: Expressions.shineY[index]
            width: 5; height: 5; opacity: root.shine
            ShapePath {
                fillColor: "#fff2b6"; strokeWidth: 0
                startX: 2.5; startY: 0
                PathLine { x: 3.2; y: 1.8 }
                PathLine { x: 5; y: 2.5 }
                PathLine { x: 3.2; y: 3.2 }
                PathLine { x: 2.5; y: 5 }
                PathLine { x: 1.8; y: 3.2 }
                PathLine { x: 0; y: 2.5 }
                PathLine { x: 1.8; y: 1.8 }
                PathLine { x: 2.5; y: 0 }
            }
        }
    }
    TapHandler {
        id: primaryTap
        enabled: root.enabled && !root.dragging
        acceptedButtons: Qt.LeftButton
        gesturePolicy: TapHandler.DragThreshold
        onTapped: root.reactToTap(point.position.x,point.position.y)
        onDoubleTapped: root.chatRequested()
    }
    TapHandler {
        enabled: root.enabled
        acceptedButtons: Qt.MiddleButton
        onTapped: root.pocketRequested()
    }
    TapHandler {
        enabled: root.enabled
        acceptedButtons: Qt.RightButton
        onTapped: root.settingsRequested()
    }
    onMotionEnabledChanged: {
        if (!motionEnabled) {
            squashBurst.stop(); reactionBounce.stop(); shineBurst.stop(); tapBurst.stop(); swallowBurst.stop()
            squash = 0; bob = 0; sway = 0; orbitPhase = 0; reactionPhase = 1; reactionRipple = 0; shine = 0; tapPhase = 1; tapPulse = 0; swallowPhase = 1; chompPhase = 0
        }
    }
    onExpressionChanged: {
        if (motionEnabled && ["happy", "excited", "surprised", "alert"].includes(expression)) {
            reactionBounce.restart()
            if (["happy", "excited"].includes(expression) && effectsEnabled) shineBurst.restart()
        }
    }
    Behavior on accentColor { enabled: root.motionEnabled; ColorAnimation { duration: 480 } }
    Behavior on hoverAmount { enabled: root.motionEnabled; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    Behavior on viewYaw { enabled: root.motionEnabled; NumberAnimation { duration: root.pocketDragActive ? 140 : 600; easing.type: Easing.InOutCubic } }
    Behavior on viewPitch { enabled: root.motionEnabled; NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    Behavior on expressionSquash { enabled: root.motionEnabled; SpringAnimation { spring: 3; damping: 0.42 } }
    Behavior on stateSquash { enabled: root.motionEnabled; SpringAnimation { spring: 3.2; damping: 0.34 } }
    Behavior on stateStretch { enabled: root.motionEnabled; SpringAnimation { spring: 2.8; damping: 0.36 } }
    Behavior on stateLean { enabled: root.motionEnabled; SpringAnimation { spring: 2.5; damping: 0.42 } }
    Behavior on stateTip { enabled: root.motionEnabled; SpringAnimation { spring: 2.4; damping: 0.44 } }
    Behavior on eyeOpen { enabled: root.motionEnabled; NumberAnimation { duration: 110; easing.type: Easing.OutQuad } }
    Behavior on mouthCurve { enabled: root.motionEnabled; NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
    Behavior on pulse { enabled: root.motionEnabled; NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
    SequentialAnimation {
        id: squashBurst
        NumberAnimation { target: root; property: "squash"; to: 1; duration: 85; easing.type: Easing.OutQuad }
        NumberAnimation { target: root; property: "squash"; to: -0.35; duration: 150; easing.type: Easing.OutBack }
        NumberAnimation { target: root; property: "squash"; to: 0; duration: 210; easing.type: Easing.OutCubic }
    }
    ParallelAnimation {
        id: tapBurst
        NumberAnimation { target: root; property: "tapPhase"; from: 0; to: 1; duration: 650; easing.type: Easing.OutCubic }
        NumberAnimation { target: root; property: "tapPulse"; from: 1; to: 0; duration: 500; easing.type: Easing.OutCubic }
    }
    ParallelAnimation {
        id: reactionBounce
        NumberAnimation { target: root; property: "reactionPhase"; from: 0; to: 1; duration: curves.clips.hop.duration }
        NumberAnimation { target: root; property: "reactionRipple"; from: 1; to: 0; duration: curves.clips.hop.duration; easing.type: Easing.OutCubic }
    }
    SequentialAnimation {
        id: shineBurst
        NumberAnimation { target: root; property: "shine"; to: 1; duration: 160 }
        NumberAnimation { target: root; property: "shine"; to: 0; duration: 850 }
    }
    ParallelAnimation {
        id: swallowBurst
        NumberAnimation { target: root; property: "swallowPhase"; from: 0; to: 1; duration: 680; easing.type: Easing.InOutQuad }
        SequentialAnimation {
            PauseAnimation { duration: 240 }
            NumberAnimation { target: root; property: "chompPhase"; from: 0; to: 1.5; duration: 420; easing.type: Easing.InOutSine }
            PropertyAction { target: root; property: "chompPhase"; value: 0 }
        }
    }
    readonly property bool _windowVisible: root.Window.window ? root.Window.window.visible : true
    readonly property bool burstActive: squashBurst.running || tapBurst.running || reactionBounce.running || shineBurst.running || swallowBurst.running
    readonly property bool idleLoopPaused: !root._windowVisible || root.motionAmount <= 0.001 || (root.quietSettled && !root.burstActive && !root.hovered && !root.dragging && !root.walking && !root.flying && !root.traveling && !root.pocketDragActive)
    readonly property bool effectiveNearbyInteraction: root._windowVisible && (root.nearbyInteraction || root.hovered || root.dragging || root.walking || root.flying || root.traveling || root.pocketDragActive || root.burstActive)
    SequentialAnimation on bob {
        running: root.motionEnabled && root.visible && !gait.active; paused: root.idleLoopPaused && running; loops: Animation.Infinite
        NumberAnimation { to: -1.2 - root.energy * 2.4; duration: 1570; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0.6 + root.energy * 1.2; duration: 2110; easing.type: Easing.InOutSine }
    }
    SequentialAnimation on sway {
        running: root.motionEnabled && root.visible && !gait.active; paused: root.idleLoopPaused && running; loops: Animation.Infinite
        NumberAnimation { to: 0.45 + root.energy; duration: 2390; easing.type: Easing.InOutSine }
        NumberAnimation { to: -0.3 - root.energy * 0.7; duration: 3170; easing.type: Easing.InOutSine }
    }
    SequentialAnimation on shimmer {
        running: root.motionEnabled && root.visible && root.effectsEnabled; paused: root.idleLoopPaused && running; loops: Animation.Infinite
        NumberAnimation { to: 1; duration: 2800; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0; duration: 4300; easing.type: Easing.InOutSine }
    }
    NumberAnimation on orbitPhase {
        running: root.motionEnabled && root.visible && root.bubblesEnabled && !root.bubble2DMode
        paused: running && (!root._windowVisible || root.motionAmount <= 0.001 || (root.powerProfile !== "performance" && !root.effectiveNearbyInteraction))
        from: 0; to: 1; duration: curves.clips.orbit.duration
        loops: Animation.Infinite
    }
}
