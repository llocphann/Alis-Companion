import QtQuick
import QtQuick.Shapes
import qs.modules.abyss.looks
import "AquaTravel.js" as Travel

// Two finite water openings inside the existing output surface. No idle clock,
// capture texture, input handler or additional native window is needed.
Item {
    id:root
    property var presence:null
    visible:presence?.portalActive ?? false
    readonly property real progress:presence?.portalProgress ?? 0
    readonly property real opening:Travel.opening(progress)
    Repeater {
        model:2
        delegate:Item {
            id:mouth
            required property int index
            objectName:index===0 ? "aquaPortalSource" : "aquaPortalDestination"
            readonly property var point:index===0 ? root.presence?.portalSource : root.presence?.portalDestination
            readonly property real unit:root.presence?.sceneScale ?? 1
            readonly property real hostW:root.presence?.sceneHostWidth ?? 112
            readonly property real hostH:root.presence?.sceneHostHeight ?? 98
            visible:root.visible && !!point
            width:106*unit;height:144*unit
            x:(point?.x ?? 0)+(hostW-width)/2
            y:(point?.y ?? 0)+(hostH-height)/2
            opacity:root.opening
            scale:Math.max(.01,root.opening)
            rotation:point?.edge==="left" || point?.edge==="right" ? 90 : 0
            Shape {
                anchors.fill:parent
                preferredRendererType:Shape.CurveRenderer
                ShapePath {
                    strokeColor:AbyssStyle.accent;strokeWidth:3*mouth.unit
                    fillColor:Qt.rgba(AbyssStyle.accent.r*.12,AbyssStyle.accent.g*.12,AbyssStyle.accent.b*.12,.92)
                    PathAngleArc {centerX:mouth.width/2;centerY:mouth.height/2;radiusX:mouth.width*.42;radiusY:mouth.height*.46;startAngle:0;sweepAngle:360}
                }
            }
            Repeater {
                model:4
                delegate:Item {
                    id:curlHost
                    required property int index
                    readonly property real rx:mouth.width*(.38-index*.055)
                    readonly property real ry:mouth.height*(.42-index*.055)
                    readonly property real baseR:Math.max(1,Math.sqrt(rx*ry))
                    readonly property real pad:4*mouth.unit
                    readonly property real boxSize:(baseR+pad)*2
                    width:boxSize;height:boxSize
                    anchors.centerIn:parent
                    opacity:.72-index*.12
                    transform:Scale {
                        origin.x:curlHost.boxSize/2
                        origin.y:curlHost.boxSize/2
                        xScale:curlHost.rx/curlHost.baseR
                        yScale:curlHost.ry/curlHost.baseR
                    }
                    Shape {
                        id:curl
                        anchors.fill:parent
                        preferredRendererType:Shape.CurveRenderer
                        transformOrigin:Item.Center
                        rotation:root.progress*720+curlHost.index*93+mouth.index*180
                        ShapePath {
                            strokeColor:AbyssStyle.accent;strokeWidth:(1.8-curlHost.index*.2)*mouth.unit
                            fillColor:"transparent";capStyle:ShapePath.RoundCap
                            PathAngleArc {
                                centerX:curlHost.boxSize/2;centerY:curlHost.boxSize/2
                                radiusX:curlHost.baseR;radiusY:curlHost.baseR
                                startAngle:0;sweepAngle:235
                            }
                        }
                    }
                }
            }
        }
    }
}
