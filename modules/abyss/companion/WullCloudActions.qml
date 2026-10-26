pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Shapes
import Quickshell
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.widgets
import qs.modules.abyss.looks
import "WullCloudOrbit.js" as Orbit

Item {
    id: root
    required property Item actor
    required property real outputWidth
    required property real outputHeight
    property bool allowed: false
    property bool revealed: false
    readonly property bool hovered: obsidian.hovered || ai.hovered || (pocket.visible && pocket.hovered) || (pomodoro.visible && pomodoro.hovered)
    readonly property Item obsidianTarget: obsidian
    readonly property Item aiTarget: ai
    readonly property Item pocketTarget: pocket
    readonly property Item pomodoroTarget: pomodoro
    readonly property string pomodoroTimeText: {
        const secs = Math.max(0, Number(TimerService.pomodoroSecondsLeft ?? TimerService.focusTime ?? 1500))
        return String(Math.floor(secs / 60)).padStart(2, "0") + ":" + String(secs % 60).padStart(2, "0")
    }
    readonly property bool eligible: allowed && actor.visible && actor.inputReady && revealed
        && !WullMind.conversationOpen && !WullMind.contextOpen && !WullPocket.opened
    readonly property var orbit: eligible ? Orbit.layout(outputWidth,outputHeight,{
        x:actor.x,y:actor.y,width:actor.width,height:actor.height,scale:actor.scale,
        sideAlignment:actor.sideAlignment ?? 0,floorAlignment:actor.floorAlignment ?? 0,
        edge:actor.edge ?? "bottom"}) : {available:false,nodes:[],pocketNode:null,pomodoroNode:null}
    width: outputWidth; height: outputHeight
    z: 241
    visible: eligible && orbit.available
    onAllowedChanged: if(!allowed)revealed=false
    onHoveredChanged: if(hovered)hide.stop();else hide.restart()
    Connections {
        target:root.actor
        function onHoveredChanged():void {
            if(root.actor.hovered) {root.revealed=true;hide.stop()}
            else hide.restart()
        }
    }
    Timer { id:hide; interval:450; onTriggered:if(!root.actor.hovered && !root.hovered)root.revealed=false }
    component Cloud: Item {
        id:cloud
        required property string kind
        required property int orbitIndex
        readonly property var placement: cloud.kind==="pocket" ? root.orbit.pocketNode : root.orbit.nodes[orbitIndex]
        readonly property bool hovered: nodeHover.hovered || button.buttonHovered
        readonly property color paintInk: glyphLoader.item?.color ?? AbyssStyle.accent
        visible: !!placement && (placement.width ?? 0) > 0
        x: placement?.x ?? 0; y: placement?.y ?? 0
        width: placement?.width ?? 38; height: placement?.height ?? 33.25
        HoverHandler { id:nodeHover;enabled:root.visible;blocking:false;cursorShape:Qt.PointingHandCursor }
        Shape {
            objectName: cloud.kind==="obsidian" ? "wullObsidianCloudShape" : cloud.kind==="pocket" ? "wullPocketCloudShape" : "wullAiCloudShape"
            anchors.centerIn:parent
            width:48; height:42; scale:cloud.width/48
            preferredRendererType: Shape.CurveRenderer
            antialiasing: true
            // Outer Abyss soft shadow + glow halo
            ShapePath {
                strokeWidth: 3.5
                strokeColor: Qt.alpha(AbyssStyle.shadow, cloud.hovered ? 0.55 : 0.35)
                fillColor: Qt.alpha(AbyssStyle.glow, cloud.hovered ? 0.28 : 0.12)
                startX: 24; startY: 3
                PathArc { x: 24; y: 39; radiusX: 19; radiusY: 18 }
                PathArc { x: 24; y: 3; radiusX: 19; radiusY: 18 }
            }
            // Abyss Panel Style liquid-glass squircle capsule with directional rim specular
            ShapePath {
                strokeWidth: cloud.hovered ? 1.45 : 1.15
                strokeColor: cloud.hovered ? AbyssStyle.specular : Qt.alpha(AbyssStyle.accent, 0.58)
                fillGradient: LinearGradient {
                    x1: 8; y1: 4; x2: 38; y2: 38
                    GradientStop { position: 0.0; color: Qt.alpha(AbyssStyle.surfaceRaised, cloud.hovered ? 0.98 : 0.94) }
                    GradientStop { position: 0.52; color: Qt.alpha(AbyssStyle.surfaceDeep, 0.94) }
                    GradientStop { position: 1.0; color: Qt.alpha(AbyssStyle.surfaceDeep, 0.98) }
                }
                startX: 24; startY: 3
                PathArc { x: 24; y: 39; radiusX: 19; radiusY: 18 }
                PathArc { x: 24; y: 3; radiusX: 19; radiusY: 18 }
            }
            // Top-left Abyss glass specular highlight band
            ShapePath {
                strokeWidth: 0.9
                strokeColor: Qt.alpha(AbyssStyle.specular, cloud.hovered ? 0.65 : 0.36)
                fillGradient: LinearGradient {
                    x1: 12; y1: 5; x2: 28; y2: 22
                    GradientStop { position: 0.0; color: Qt.alpha(AbyssStyle.specular, cloud.hovered ? 0.22 : 0.12) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                startX: 10; startY: 16
                PathArc { x: 37; y: 14; radiusX: 16; radiusY: 13 }
                PathCubic { x: 10; y: 16; control1X: 28; control1Y: 9; control2X: 17; control2Y: 10 }
            }
        }
        RippleButton {
            id:button
            objectName: cloud.kind==="obsidian" ? "wullObsidianAction" : cloud.kind==="pocket" ? "wullPocketAction" : "wullAiAction"
            anchors.fill:parent;buttonRadius:height/2
            colBackground:"transparent";colBackgroundHover:"transparent";colRipple:AbyssStyle.accent
            Accessible.name: cloud.kind==="obsidian" ? "Daily check-in and schedule"
                : cloud.kind==="pocket" ? "Pocket (" + WullPocket.items.length + "/32)" : "AI chat"
            onClicked: {
                if (cloud.kind === "obsidian") WullMind.openContext()
                else if (cloud.kind === "pocket") {
                    if (!WullPocket.permitted) Config.setNestedValue("abyss.companion.pocketEnabled", true)
                    WullMind.dismiss()
                    Qt.callLater(() => WullPocket.show())
                } else WullMind.openChat()
            }
            contentItem:Item {
                Loader {
                    id:glyphLoader
                    anchors.centerIn:parent
                    width:cloud.width*.42;height:width
                    sourceComponent: cloud.kind==="obsidian" ? obsidianGlyph : cloud.kind==="pocket" ? pocketGlyph : aiGlyph
                    Component {
                        id:obsidianGlyph
                        // Reuse AI's animated ink, so both glyphs follow the
                        // same transition without a second color animator.
                        WullObsidianIcon {objectName:"wullObsidianGlyph";color:ai?.paintInk ?? AbyssStyle.accent}
                    }
                    Component {
                        id:aiGlyph
                        MaterialSymbol {objectName:"wullAiGlyph";iconSize:cloud.width*.42;color:AbyssStyle.accent;text:"auto_awesome"}
                    }
                    Component {
                        id:pocketGlyph
                        Item {
                            objectName: "wullPocketGlyph"
                            property color color: ai?.paintInk ?? AbyssStyle.accent
                            MaterialSymbol {
                                anchors.centerIn: parent
                                iconSize: cloud.width * 0.42
                                color: parent.color
                                text: "inventory_2"
                            }
                            Rectangle {
                                visible: WullPocket.items.length > 0
                                width: 13; height: 13; radius: 6.5
                                anchors.right: parent.right; anchors.rightMargin: -4
                                anchors.top: parent.top; anchors.topMargin: -4
                                color: AbyssStyle.accent
                                border.width: 1
                                border.color: AbyssStyle.surfaceDeep
                                StyledText {
                                    anchors.centerIn: parent
                                    text: String(WullPocket.items.length)
                                    font.pixelSize: 8
                                    font.weight: Font.Bold
                                    color: AbyssStyle.surfaceDeep
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Cloud { id:obsidian;kind:"obsidian";orbitIndex:0 }
    Cloud { id:ai;kind:"ai";orbitIndex:1 }
    Cloud { id:pocket;kind:"pocket";orbitIndex:2 }

    // Abyss Panel Style Pomodoro Pill Bubble with Start/Pause, readable Timer, and Reset button
    Item {
        id: pomodoro
        objectName: "wullPomodoroBubble"
        readonly property var placement: root.orbit.pomodoroNode
        readonly property bool hovered: pomHover.hovered || pomToggleBtn.buttonHovered || pomResetBtn.buttonHovered
        visible: !!placement && (placement.width ?? 0) > 0
        x: placement?.x ?? 0
        y: placement?.y ?? 0
        width: placement?.width ?? 116
        height: placement?.height ?? 36
        HoverHandler { id: pomHover; enabled: root.visible; blocking: false }

        // Outer Abyss shadow
        Rectangle {
            anchors.fill: parent
            anchors.margins: -2
            radius: height / 2
            color: "transparent"
            border.width: 2.5
            border.color: Qt.alpha(AbyssStyle.shadow, pomodoro.hovered ? 0.52 : 0.32)
        }
        // Main Abyss Panel capsule
        Rectangle {
            id: pomodoroCapsule
            objectName: "wullPomodoroCloudShape"
            anchors.fill: parent
            radius: height / 2
            border.width: TimerService.pomodoroRunning ? 1.4 : (pomodoro.hovered ? 1.3 : 1.05)
            border.color: TimerService.pomodoroRunning || pomodoro.hovered
                ? AbyssStyle.specular
                : Qt.alpha(AbyssStyle.accent, 0.55)
            gradient: Gradient {
                orientation: Gradient.Vertical
                GradientStop { position: 0.0; color: Qt.alpha(AbyssStyle.surfaceRaised, pomodoro.hovered ? 0.98 : 0.94) }
                GradientStop { position: 0.55; color: Qt.alpha(AbyssStyle.surfaceDeep, 0.95) }
                GradientStop { position: 1.0; color: Qt.alpha(AbyssStyle.surfaceDeep, 0.98) }
            }
            // Top inner specular highlight strip
            Rectangle {
                anchors.top: parent.top
                anchors.topMargin: 1.5
                anchors.horizontalCenter: parent.horizontalCenter
                width: parent.width - 18
                height: parent.height * 0.36
                radius: height / 2
                gradient: Gradient {
                    orientation: Gradient.Vertical
                    GradientStop { position: 0.0; color: Qt.alpha(AbyssStyle.specular, pomodoro.hovered ? 0.20 : 0.11) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
            }
        }

        Row {
            anchors.centerIn: parent
            spacing: 5
            RippleButton {
                id: pomToggleBtn
                objectName: "wullPomodoroAction"
                width: 26; height: 26
                implicitWidth: 26; implicitHeight: 26
                buttonRadius: 13
                colBackground: TimerService.pomodoroRunning && !TimerService.pomodoroPaused
                    ? Qt.alpha(AbyssStyle.accent, 0.24)
                    : Qt.alpha(AbyssStyle.accent, 0.10)
                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.32)
                colRipple: AbyssStyle.accent
                Accessible.name: TimerService.pomodoroRunning && !TimerService.pomodoroPaused ? "Pause Pomodoro" : "Start Pomodoro"
                onClicked: TimerService.togglePomodoro()
                contentItem: MaterialSymbol {
                    anchors.centerIn: parent
                    iconSize: 16
                    color: AbyssStyle.accent
                    text: TimerService.pomodoroRunning && !TimerService.pomodoroPaused
                        ? "pause"
                        : (TimerService.pomodoroBreak ? "coffee" : "play_arrow")
                }
            }
            StyledText {
                id: pomTimeLabel
                objectName: "wullPomodoroTimerText"
                anchors.verticalCenter: parent.verticalCenter
                text: root.pomodoroTimeText
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: TimerService.pomodoroRunning ? AbyssStyle.specular : AbyssStyle.textColor
            }
            RippleButton {
                id: pomResetBtn
                objectName: "wullPomodoroReset"
                width: 24; height: 24
                implicitWidth: 24; implicitHeight: 24
                buttonRadius: 12
                colBackground: "transparent"
                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                colRipple: AbyssStyle.accent
                Accessible.name: "Reset Pomodoro"
                onClicked: TimerService.resetPomodoro()
                contentItem: MaterialSymbol {
                    anchors.centerIn: parent
                    iconSize: 15
                    color: pomResetBtn.buttonHovered ? AbyssStyle.accent : AbyssStyle.textColorMuted
                    text: "restart_alt"
                }
            }
        }
    }
}
