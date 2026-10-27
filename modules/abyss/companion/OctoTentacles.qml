import QtQuick
import QtQuick.Shapes
import "OctoRig.js" as Rig

// Editable Blender curves, projected in the head's 3D pose. Each
// trimmed quad traces a tapered volume; there are no sprite frames or clocks.
Item {
    id: root
    required property var body
    required property var motion
    property bool gripping: false
    property var tentacleIndices: [0,1,2,3]
    readonly property vector4d tentacleOpticsUniform: Qt.vector4d(0, 3, 0, 0)
    width: body.width; height: body.height
    Repeater {
        model: root.tentacleIndices
        Item {
            id: arm
            required property int modelData
            readonly property int index: modelData
            objectName: "octoTentacle"+index
            readonly property real coworkReach: index < 2 ? (root.body.coworkPose ? (root.body.coworkPose["laptopReach" + index] ?? 0) * (index === 0 ? 12 : -12) : root.body.ambientProp === "book" ? (index === 0 ? 9.5 : -9.5) : root.body.ambientProp === "coffee" && index === 1 ? -9.5 : 0) : 0
            readonly property real coworkTap: index < 2 ? (root.body.coworkPose ? (root.body.coworkPose["laptopTap" + index] ?? 0) * -2.2 : root.body.ambientProp === "book" ? (root.body.shimmer - 0.5) * -1.8 : 0) : 0
            readonly property var curve: root.gripping
                ? Rig.gripControls(index,root.motion.sample("gripRise"),root.motion.sample("gripWrap"))
                : Rig.controls(index,root.motion.curl(index),root.motion.sample("tentacle"+index+"Lift")+coworkTap,root.motion.sample("tentacle"+index+"Reach")*root.motion.direction+coworkReach)
            readonly property var points: curve.map(p=>root.body.project(p.x,p.y,p.z))
            readonly property var bounds: {
                const radius=(Rig.geometry.rootRadius+1)*Math.max(root.body.poseScaleX,root.body.poseScaleY,1/(root.body.poseScaleX*root.body.poseScaleY))
                const p0=points[0],p1=points[1],p2=points[2],p3=points[3]
                return {left:Math.min(p0.x,p1.x,p2.x,p3.x)-radius,right:Math.max(p0.x,p1.x,p2.x,p3.x)+radius,
                    bottom:Math.min(p0.y,p1.y,p2.y,p3.y)-radius,top:Math.max(p0.y,p1.y,p2.y,p3.y)+radius}
            }
            x:38+bounds.left; y:46.14-bounds.top+root.motion.lift+root.body.reactionLift*root.body.motionAmount
            width:bounds.right-bounds.left; height:bounds.top-bounds.bottom
            z:(points[0].z+points[1].z+points[2].z+points[3].z)*0.25
            readonly property real radialScale: Math.sqrt(root.body.poseScaleX*root.body.poseScaleY)
            function control(i): vector4d {const p=points[i];return Qt.vector4d(p.x/35.34884,p.y/35.34884,p.z/35.34884,curve[i].r*radialScale/35.34884)}
            ShaderEffect {
                id: material
                objectName:"octoTentacleMaterial"+arm.index
                anchors.fill: parent
                visible: !root.body.softwareFallback
                property color accent: root.body.liquidAccent
                property color specular: root.body.reflectionColor
                property vector4d motion: root.body.limbMotionUniform
                property vector4d optics: root.tentacleOpticsUniform
                property vector4d rendering: root.body.smallMaterialRenderingUniform
                property vector4d pose: root.body.zeroPoseUniform
                property vector4d curve0: arm.control(0)
                property vector4d curve1: arm.control(1)
                property vector4d curve2: arm.control(2)
                property vector4d curve3: arm.control(3)
                property vector4d bounds: Qt.vector4d(arm.bounds.left/35.34884,arm.bounds.bottom/35.34884,arm.width/35.34884,arm.height/35.34884)
                fragmentShader: Qt.resolvedUrl("OctoTentacle.frag.qsb")
            }
            Shape {
                id: fallbackShape
                visible:root.body.softwareFallback || material.status===ShaderEffect.Error
                anchors.fill:parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    fillColor:"transparent";strokeColor:root.body.liquidAccent;strokeWidth:7
                    capStyle:ShapePath.RoundCap
                    startX:fallbackShape.visible ? arm.points[0].x-arm.bounds.left : 0;startY:fallbackShape.visible ? arm.bounds.top-arm.points[0].y : 0
                    PathCubic {
                        x:fallbackShape.visible ? arm.points[3].x-arm.bounds.left : 0;y:fallbackShape.visible ? arm.bounds.top-arm.points[3].y : 0
                        control1X:fallbackShape.visible ? arm.points[1].x-arm.bounds.left : 0;control1Y:fallbackShape.visible ? arm.bounds.top-arm.points[1].y : 0
                        control2X:fallbackShape.visible ? arm.points[2].x-arm.bounds.left : 0;control2Y:fallbackShape.visible ? arm.bounds.top-arm.points[2].y : 0
                    }
                }
            }
        }
    }
}
