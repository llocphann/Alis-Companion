pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Shapes
import Quickshell
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.widgets
import qs.modules.abyss.looks
import "AquaCloudOrbit.js" as Orbit

Item {
    id: root
    required property Item actor
    required property real outputWidth
    required property real outputHeight
    property bool allowed: false
    property bool revealed: false
    property bool pomodoroExpanded: false
    readonly property bool hovered: obsidian.hovered || ai.hovered || (pocket.visible && pocket.hovered) || (pomodoro.visible && pomodoro.hovered)
    readonly property Item obsidianTarget: obsidian
    readonly property Item aiTarget: ai
    readonly property Item pocketTarget: pocket
    readonly property Item pomodoroTarget: pomodoro
    readonly property real edgeOpacity: AbyssStyle.contentOpacity
    readonly property real edgeHighlight: AbyssStyle.highlightStrength
    readonly property real edgeLift: AbyssStyle.blurRadius > 0 ? 0.024 : 0
    readonly property color edgeSurfaceBase: Qt.rgba(
        Math.min(1, AbyssStyle.surfaceDeep.r + edgeLift),
        Math.min(1, AbyssStyle.surfaceDeep.g + edgeLift),
        Math.min(1, AbyssStyle.surfaceDeep.b + edgeLift),
        edgeOpacity)
    function edgeSurfaceColor(depthWeight): color {
        const k = Math.max(0, Math.min(1, depthWeight * 0.38 * edgeHighlight))
        return Qt.rgba(
            edgeSurfaceBase.r + (AbyssStyle.surfaceRaised.r - edgeSurfaceBase.r) * k,
            edgeSurfaceBase.g + (AbyssStyle.surfaceRaised.g - edgeSurfaceBase.g) * k,
            edgeSurfaceBase.b + (AbyssStyle.surfaceRaised.b - edgeSurfaceBase.b) * k,
            edgeOpacity)
    }
    function edgeBorderColor(isHovered, isActive): color {
        if (isActive || isHovered)
            return Qt.alpha(AbyssStyle.accent, isActive ? 0.38 : 0.28)
        if (edgeHighlight > 0.01)
            return Qt.alpha(AbyssStyle.specular, Math.max(0.08, 0.55 * edgeHighlight))
        return Qt.alpha(AbyssStyle.textColor, 0.06)
    }
    readonly property string pomodoroTimeText: {
        const secs = Math.max(0, Number(TimerService.pomodoroSecondsLeft ?? TimerService.focusTime ?? 1500))
        return String(Math.floor(secs / 60)).padStart(2, "0") + ":" + String(secs % 60).padStart(2, "0")
    }
    readonly property bool eligible: allowed && actor.visible && actor.inputReady && revealed
        && !AquaMind.conversationOpen && !AquaMind.contextOpen && !AquaPocket.opened
    readonly property var orbit: eligible ? Orbit.layout(outputWidth,outputHeight,{
        x:actor.x,y:actor.y,width:actor.width,height:actor.height,scale:actor.scale,
        sideAlignment:actor.sideAlignment ?? 0,floorAlignment:actor.floorAlignment ?? 0,
        edge:actor.edge ?? "bottom"}) : {available:false,nodes:[],pocketNode:null,pomodoroNode:null}
    width: outputWidth; height: outputHeight
    z: 241
    visible: eligible && orbit.available
    onVisibleChanged: if (!visible) pomodoroExpanded = false
    onAllowedChanged: if(!allowed) { revealed=false; pomodoroExpanded=false }
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
        // Screen-Edge-matched container (child 0)
        Rectangle {
            id: cloudSurface
            objectName: cloud.kind==="obsidian" ? "aquaObsidianCloudShape" : cloud.kind==="pocket" ? "aquaPocketCloudShape" : "aquaAiCloudShape"
            anchors.fill: parent
            radius: Math.min(height / 2, Math.max(10, AbyssStyle.perimeterRadius * 0.72))
            color: root.edgeSurfaceBase
            border.width: 1
            border.color: root.edgeBorderColor(cloud.hovered, false)
            gradient: Gradient {
                orientation: Gradient.Vertical
                GradientStop { position: 0.0; color: root.edgeSurfaceColor(1.0) }
                GradientStop { position: 1.0; color: root.edgeSurfaceColor(0.25) }
            }
            // Subtle hover tint matching AbyssButton inside Screen Edges
            Rectangle {
                anchors.fill: parent
                radius: parent.radius
                color: cloud.hovered ? Qt.alpha(AbyssStyle.accent, 0.08) : "transparent"
            }
            // Outer Abyss shadow + glow matching AbyssField.frag outside pass
            Rectangle {
                z: -1
                anchors.fill: parent
                anchors.margins: -2
                radius: parent.radius + 2
                color: AbyssStyle.glow
                border.width: 2
                border.color: AbyssStyle.shadow
            }
        }
        RippleButton {
            id:button
            objectName: cloud.kind==="obsidian" ? "aquaObsidianAction" : cloud.kind==="pocket" ? "aquaPocketAction" : "aquaAiAction"
            anchors.fill:parent;buttonRadius:cloudSurface.radius
            colBackground:"transparent";colBackgroundHover:"transparent";colRipple:AbyssStyle.accent
            Accessible.name: cloud.kind==="obsidian" ? "Daily check-in and schedule"
                : cloud.kind==="pocket" ? "Pocket (" + AquaPocket.items.length + "/32)" : "AI chat"
            onClicked: {
                if (cloud.kind === "obsidian") AquaMind.openContext()
                else if (cloud.kind === "pocket") {
                    if (!AquaPocket.permitted) Config.setNestedValue("abyss.companion.pocketEnabled", true)
                    AquaMind.dismiss()
                    Qt.callLater(() => AquaPocket.show())
                } else AquaMind.openChat()
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
                        AquaObsidianIcon {objectName:"aquaObsidianGlyph";color:ai?.paintInk ?? AbyssStyle.accent}
                    }
                    Component {
                        id:aiGlyph
                        MaterialSymbol {objectName:"aquaAiGlyph";iconSize:cloud.width*.42;color:AbyssStyle.accent;text:"auto_awesome"}
                    }
                    Component {
                        id:pocketGlyph
                        Item {
                            objectName: "aquaPocketGlyph"
                            property color color: ai?.paintInk ?? AbyssStyle.accent
                            MaterialSymbol {
                                anchors.centerIn: parent
                                iconSize: cloud.width * 0.42
                                color: parent.color
                                text: "inventory_2"
                            }
                            Rectangle {
                                visible: AquaPocket.items.length > 0
                                width: 13; height: 13; radius: 6.5
                                anchors.right: parent.right; anchors.rightMargin: -4
                                anchors.top: parent.top; anchors.topMargin: -4
                                color: AbyssStyle.accent
                                border.width: 1
                                border.color: AbyssStyle.surfaceDeep
                                StyledText {
                                    anchors.centerIn: parent
                                    text: String(AquaPocket.items.length)
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

    // Screen-Edge-matched Pomodoro Icon Bubble (click to expand into Start/Pause + Timer + Reset capsule)
    Item {
        id: pomodoro
        objectName: "aquaPomodoroBubble"
        readonly property var placement: root.orbit.pomodoroNode
        readonly property bool hovered: pomHover.hovered || pomCollapsedBtn.buttonHovered || pomToggleBtn.buttonHovered || pomResetBtn.buttonHovered
        visible: !!placement && (placement.width ?? 0) > 0
        z: root.pomodoroExpanded ? 5 : 1
        x: root.pomodoroExpanded ? (placement?.expandedX ?? placement?.x ?? 0) : (placement?.x ?? 0)
        y: root.pomodoroExpanded ? (placement?.expandedY ?? placement?.y ?? 0) : (placement?.y ?? 0)
        width: root.pomodoroExpanded ? (placement?.expandedWidth ?? 116) : (placement?.width ?? 38)
        height: root.pomodoroExpanded ? (placement?.expandedHeight ?? 36) : (placement?.height ?? 33.25)
        HoverHandler { id: pomHover; enabled: root.visible; blocking: false; cursorShape: Qt.PointingHandCursor }

        // Main Screen-Edge-matched capsule
        Rectangle {
            id: pomodoroCapsule
            objectName: "aquaPomodoroCloudShape"
            anchors.fill: parent
            radius: Math.min(height / 2, Math.max(10, AbyssStyle.perimeterRadius * 0.72))
            color: root.edgeSurfaceBase
            border.width: 1
            border.color: root.edgeBorderColor(pomodoro.hovered, TimerService.pomodoroRunning)
            gradient: Gradient {
                orientation: Gradient.Vertical
                GradientStop { position: 0.0; color: root.edgeSurfaceColor(1.0) }
                GradientStop { position: 1.0; color: root.edgeSurfaceColor(0.25) }
            }
            Rectangle {
                anchors.fill: parent
                radius: parent.radius
                color: pomodoro.hovered ? Qt.alpha(AbyssStyle.accent, 0.08) : "transparent"
            }
            Rectangle {
                z: -1
                anchors.fill: parent
                anchors.margins: -2
                radius: parent.radius + 2
                color: AbyssStyle.glow
                border.width: 2
                border.color: AbyssStyle.shadow
            }
        }

        // Collapsed Icon Bubble state: click to expand
        RippleButton {
            id: pomCollapsedBtn
            objectName: "aquaPomodoroExpand"
            visible: !root.pomodoroExpanded
            anchors.fill: parent
            buttonRadius: pomodoroCapsule.radius
            colBackground: "transparent"
            colBackgroundHover: "transparent"
            colRipple: AbyssStyle.accent
            Accessible.name: "Pomodoro (" + root.pomodoroTimeText + ")"
            onClicked: root.pomodoroExpanded = true
            contentItem: Item {
                Item {
                    anchors.centerIn: parent
                    width: pomodoro.width * 0.42
                    height: width
                    MaterialSymbol {
                        objectName: "aquaPomodoroGlyph"
                        anchors.centerIn: parent
                        iconSize: pomodoro.width * 0.42
                        color: ai?.paintInk ?? AbyssStyle.accent
                        text: TimerService.pomodoroRunning && !TimerService.pomodoroPaused ? "timelapse" : "timer"
                    }
                    Rectangle {
                        visible: TimerService.pomodoroRunning
                        width: 8; height: 8; radius: 4
                        anchors.right: parent.right; anchors.rightMargin: -3
                        anchors.top: parent.top; anchors.topMargin: -3
                        color: AbyssStyle.accent
                        border.width: 1
                        border.color: AbyssStyle.surfaceDeep
                    }
                }
            }
        }

        // Expanded Pomodoro controls state: Start/Pause + readable Timer + Reset
        Row {
            visible: root.pomodoroExpanded
            anchors.centerIn: parent
            spacing: 5
            RippleButton {
                id: pomToggleBtn
                objectName: "aquaPomodoroAction"
                width: 26; height: 26
                implicitWidth: 26; implicitHeight: 26
                buttonRadius: 13
                colBackground: TimerService.pomodoroRunning && !TimerService.pomodoroPaused
                    ? Qt.alpha(AbyssStyle.accent, 0.22)
                    : Qt.alpha(AbyssStyle.accent, 0.08)
                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.30)
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
                objectName: "aquaPomodoroTimerText"
                anchors.verticalCenter: parent.verticalCenter
                text: root.pomodoroTimeText
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: TimerService.pomodoroRunning ? AbyssStyle.accent : AbyssStyle.textColor
            }
            RippleButton {
                id: pomResetBtn
                objectName: "aquaPomodoroReset"
                width: 24; height: 24
                implicitWidth: 24; implicitHeight: 24
                buttonRadius: 12
                colBackground: "transparent"
                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
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
