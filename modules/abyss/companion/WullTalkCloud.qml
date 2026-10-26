pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.widgets
import qs.modules.abyss.looks

// One question offers one choice row. Only explicit chat requests input focus.
Item {
    id: root
    required property Item actor
    required property real outputWidth
    required property real outputHeight
    property bool allowed: false
    // 0 = closed, 1 = thinking effort, 2 = model picker.
    property int profileStage: 0
    readonly property bool pocketOpen: WullPocket.opened && WullPocket.permitted
    readonly property bool editing: visible && WullMind.conversationOpen && !pocketOpen
    readonly property bool controlsVisible: visible && (pocketOpen || editing || WullMind.contextOpen || WullMind.checkInStage.length>0)
    readonly property real edgeOpacity: AbyssStyle.contentOpacity
    readonly property real edgeHighlight: AbyssStyle.highlightStrength
    readonly property real edgeLift: AbyssStyle.blurRadius > 0 ? 0.024 : 0
    readonly property real edgeRadius: Math.max(12, Math.min(26, AbyssStyle.perimeterRadius))
    readonly property color cloudColor: Qt.rgba(
        Math.min(1, AbyssStyle.surfaceDeep.r + edgeLift),
        Math.min(1, AbyssStyle.surfaceDeep.g + edgeLift),
        Math.min(1, AbyssStyle.surfaceDeep.b + edgeLift),
        edgeOpacity)
    readonly property color cloudBorderColor: edgeHighlight > 0.01
        ? Qt.alpha(AbyssStyle.specular, Math.max(0.08, 0.55 * edgeHighlight))
        : Qt.alpha(AbyssStyle.textColor, 0.06)
    function edgeSurfaceColor(depthWeight): color {
        const k = Math.max(0, Math.min(1, depthWeight * 0.38 * edgeHighlight))
        return Qt.rgba(
            cloudColor.r + (AbyssStyle.surfaceRaised.r - cloudColor.r) * k,
            cloudColor.g + (AbyssStyle.surfaceRaised.g - cloudColor.g) * k,
            cloudColor.b + (AbyssStyle.surfaceRaised.b - cloudColor.b) * k,
            edgeOpacity)
    }
    width: Math.min(380, Math.max(220, outputWidth - 32))
    readonly property real nonEditingContentHeight: WullMind.contextOpen
        ? content.implicitHeight
        : speechText.implicitHeight + (WullMind.checkInStage.length > 0 ? content.spacing + 28 : 0)
    height: pocketOpen ? Math.min(360,Math.max(220,outputHeight-48))
        : editing ? Math.min(430, Math.max(230, outputHeight - 48)) : nonEditingContentHeight + 24
    readonly property real actorVisualScale: Math.max(.1, Number(actor.scale) || 1)
    readonly property real actorVisualWidth: actor.width * actorVisualScale
    readonly property real actorVisualHeight: actor.height * actorVisualScale
    readonly property real actorVisualLeft: actor.x + (actor.width - actorVisualWidth) / 2
    readonly property real actorVisualTop: actor.y + (actor.height - actorVisualHeight) / 2
    readonly property real actorVisualRight: actorVisualLeft + actorVisualWidth
    readonly property real actorVisualBottom: actorVisualTop + actorVisualHeight
    readonly property real anchorGap: Math.max(6, Math.min(10, actorVisualHeight * .08))
    x: Math.max(12, Math.min(outputWidth - width - 12,
        actorVisualLeft + actorVisualWidth / 2 - width / 2))
    y: actorVisualTop - height - anchorGap >= 12
        ? actorVisualTop - height - anchorGap
        : Math.min(outputHeight - height - 12, actorVisualBottom + anchorGap)
    visible: allowed && actor.visible && actor.inputReady && (pocketOpen || WullMind.text.length > 0 || WullMind.conversationOpen || WullMind.contextOpen)
    z: 240
    function containsScenePoint(point): bool {
        const local = root.mapFromItem(null, point.x, point.y)
        return visible && local.x >= 0 && local.x <= width && local.y >= 0 && local.y <= height
    }

    // Outer Abyss shadow + glow matching AbyssField.frag outside pass
    Rectangle {
        anchors.fill: parent
        anchors.margins: -3
        radius: root.edgeRadius + 3
        color: AbyssStyle.glow
        border.width: 3
        border.color: AbyssStyle.shadow
    }

    // Screen-Edge-matched container
    Rectangle {
        objectName: "wullCloudBackground"
        anchors.fill: parent
        radius: root.edgeRadius
        border.width: 1
        border.color: root.cloudBorderColor
        color: root.cloudColor
        gradient: Gradient {
            orientation: Gradient.Vertical
            GradientStop { position: 0.0; color: root.edgeSurfaceColor(1.0) }
            GradientStop { position: 1.0; color: root.edgeSurfaceColor(0.25) }
        }
    }

    component CheckInRow: ColumnLayout {
        id: choice
        required property string field
        required property var options
        required property string selectedValue
        width: content.width
        height: 28
        Layout.fillWidth: true
        spacing: 3
        GridLayout {
            width: parent.width
            height: 28
            Layout.fillWidth: true
            columns: 5
            columnSpacing: 2
            Repeater {
                model: choice.options
                DialogButton {
                    required property var modelData
                    required property int index
                    objectName: "wull" + choice.field + "-" + modelData.value
                    x: index * ((choice.width + 2) / 5)
                    width: (choice.width - 8) / 5
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    implicitHeight: 28
                    padding: 3
                    enabled: !WullMind.busy
                    buttonText: modelData.label
                    toggled: choice.selectedValue === modelData.value
                    colBackgroundToggled: Qt.alpha(AbyssStyle.accent, .2)
                    colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, .3)
                    onClicked: WullMind.setCheckInChoice(choice.field, modelData.value)
                }
            }
        }
    }

    ColumnLayout {
        id: content
        visible:!root.pocketOpen
        x: 12; y: 12; width: parent.width - 24
        height: root.editing ? root.height - 24 : root.nonEditingContentHeight
        spacing: 7
        RowLayout {
            visible: WullMind.contextOpen && !root.editing
            Layout.fillWidth: true
            StyledText { Layout.fillWidth:true; text:Translation.tr("Today"); font.weight:Font.DemiBold }
            DialogButton { objectName:"wullContextClose"; buttonText:Translation.tr("Close"); onClicked:WullMind.dismiss() }
        }
        StyledText {
            id: speechText
            width: content.width
            Layout.fillWidth: true
            visible: !root.editing
            text: WullMind.text
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            font.pixelSize: Appearance.font.pixelSize.small
            Accessible.description: WullMind.source === "ai" ? "AI reply" : "Companion message"
        }
        ColumnLayout {
            visible:WullMind.contextOpen && !root.editing && WullMind.checkInComplete
            Layout.fillWidth:true
            StyledText {
                Layout.fillWidth:true
                text:Translation.tr("Mood")+": "+WullMind.userMood+"  ·  "+Translation.tr("Energy")+": "+WullMind.userEnergy
                wrapMode:Text.WordWrap; color:Appearance.colors.colSubtext
            }
            Repeater {
                model: WullMind.contextOpen ? WullMind.reminderRows(DateTime.clock.date).slice(0,6) : []
                StyledText {
                    required property var modelData
                    Layout.fillWidth:true
                    text:String(Math.floor(modelData.start/60)).padStart(2,"0")+":"+String(modelData.start%60).padStart(2,"0")+" · "+modelData.title
                    textFormat:Text.PlainText
                    elide:Text.ElideRight; font.pixelSize:Appearance.font.pixelSize.small
                }
            }
            DialogButton {
                visible:WullMind.journal.journalPath.length>0
                buttonText:Translation.tr("Open journal"); onClicked:WullMind.openJournal()
            }
        }
        Item {
            visible: root.editing && WullMind.historyLoaded && WullMind.history.length===0
            Layout.fillWidth: true
            Layout.fillHeight: true
            StyledText {
                anchors.centerIn: parent
                width: Math.min(parent.width, 260)
                horizontalAlignment: Text.AlignHCenter
                text: "Splish! What's on your mind?"
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.small
            }
        }
        ListView {
            id: transcript
            objectName: "wullChatHistory"
            visible: root.editing && WullMind.history.length>0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 7
            model: WullMind.history
            property bool readyForOlder: false
            onCountChanged: {
                if (WullMind.historyLoadingOlder) return
                readyForOlder=false
                Qt.callLater(() => {
                    if (!root.editing) return
                    transcript.positionViewAtEnd()
                    transcript.readyForOlder=true
                })
            }
            onContentYChanged: {
                if (readyForOlder && contentY<=12 && WullMind.historyHasMore && !WullMind.busy)
                    WullMind.loadHistory(true)
            }
            delegate: Item {
                required property var modelData
                width: transcript.width
                height: bubble.height + 2
                readonly property bool fromUser: modelData?.role === "user"
                Rectangle {
                    id: bubble
                    width: Math.min(parent.width*.88,Math.max(86,messageText.implicitWidth+20))
                    height: messageText.implicitHeight+14
                    x: parent.fromUser ? parent.width-width : 0
                    radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
                    color: parent.fromUser ? Qt.alpha(AbyssStyle.accent,.14) : Qt.alpha(AbyssStyle.textColor,.06)
                    border.width: 1
                    border.color: parent.fromUser ? Qt.alpha(AbyssStyle.accent,.22) : Qt.alpha(AbyssStyle.textColor,.08)
                    opacity: parent.modelData?.failed === true ? .58 : 1
                    StyledText {
                        id: messageText
                        x: 10; y: 7; width: parent.width-20
                        text: String(parent.parent.modelData?.content ?? "")
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        font.pixelSize: Appearance.font.pixelSize.small
                    }
                }
            }
            Connections {
                target: WullMind
                function onHistoryPrepended(count): void {
                    transcript.positionViewAtIndex(count,ListView.Beginning)
                    transcript.readyForOlder=true
                }
            }
        }
        ColumnLayout {
            id: controlsColumn
            visible: root.controlsVisible
            width: content.width
            height: root.editing ? composer.height + (profileBox.visible ? profileBox.height + spacing : 0) : 28
            y: root.editing ? content.height - height : speechText.implicitHeight + content.spacing
            Layout.fillWidth: true
            spacing: 5
            CheckInRow {
                visible: WullMind.checkInStage === "mood"
                field: "mood"
                selectedValue: WullMind.userMood || String(WullMind.journal.mood ?? "").toLowerCase()
                options: [
                    {value: "terrible", label: Translation.tr("Awful")},
                    {value: "bad", label: Translation.tr("Low")},
                    {value: "okay", label: Translation.tr("Okay")},
                    {value: "good", label: Translation.tr("Good")},
                    {value: "great", label: Translation.tr("Great")}
                ]
            }
            CheckInRow {
                visible: WullMind.checkInStage === "energy"
                field: "energy"
                selectedValue: WullMind.userEnergy || String(WullMind.journal.energy ?? "").toLowerCase()
                options: [
                    {value: "drained", label: Translation.tr("Drained")},
                    {value: "low", label: Translation.tr("Low")},
                    {value: "medium", label: Translation.tr("Mid")},
                    {value: "high", label: Translation.tr("High")},
                    {value: "peak", label: Translation.tr("Peak")}
                ]
            }
            Rectangle {
                id: profileBox
                objectName: "wullProfilePanel"
                visible: root.editing && root.profileStage > 0
                width: content.width
                height: implicitHeight
                Layout.fillWidth: true
                implicitHeight: profilePanel.implicitHeight + 16
                radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
                color: Qt.alpha(AbyssStyle.textColor, .05)
                border.width: 1
                border.color: Qt.alpha(AbyssStyle.textColor, .08)
                ColumnLayout {
                    id: profilePanel
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 5
                    ColumnLayout {
                        objectName: "wullEffortPanel"
                        visible: root.profileStage === 1
                        Layout.fillWidth: true
                        spacing: 5
                        RowLayout {
                            Layout.fillWidth: true
                            StyledText {
                                Layout.fillWidth: true
                                text: "Thinking effort"
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colSubtext
                            }
                            StyledText {
                                text: WullMind.thinkingEffortLabel
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colPrimary
                            }
                        }
                        StyledSlider {
                            objectName: "wullThinkingEffort"
                            Layout.fillWidth: true
                            enableSettingsSearch: false
                            from: 0
                            to: Math.max(1,WullMind.thinkingLevels.length-1)
                            stepSize: 1
                            value: Math.max(0, WullMind.thinkingLevels.findIndex(level => level.value === WullMind.effectiveThinkingEffort))
                            enabled: WullMind.thinkingSupported && !WullMind.busy
                            tooltipContent: WullMind.thinkingEffortLabel
                            onMoved: {
                                const levels = WullMind.thinkingLevels
                                WullMind.setThinkingEffort(levels[Math.max(0, Math.min(levels.length-1, Math.round(value)))].value)
                            }
                        }
                        StyledText {
                            visible: !WullMind.thinkingSupported
                            Layout.fillWidth: true
                            text: "This model uses its provider default. Tap the effort control again to choose another model."
                            wrapMode: Text.WordWrap
                            font.pixelSize: Appearance.font.pixelSize.smallest
                            color: Appearance.colors.colSubtext
                        }
                    }
                    ColumnLayout {
                        objectName: "wullModelPicker"
                        visible: root.profileStage === 2
                        Layout.fillWidth: true
                        spacing: 4
                        RowLayout {
                            Layout.fillWidth: true
                            StyledText {
                                Layout.fillWidth: true
                                text: "Model"
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colSubtext
                            }
                            StyledText {
                                text: WullMind.currentModelLabel
                                elide: Text.ElideRight
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colPrimary
                            }
                        }
                        Repeater {
                            model: WullMind.selectableModels
                            RippleButton {
                                id: modelOption
                                required property var modelData
                                objectName: "wullModelOption-" + String(modelData.name)
                                Layout.fillWidth: true
                                implicitHeight: 36
                                buttonText: modelData.label ?? modelData.name
                                toggled: WullMind.model === modelData.name
                                enabled: !WullMind.busy
                                buttonRadius: 12
                                colBackground: "transparent"
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, .10)
                                colBackgroundToggled: Appearance.colors.colPrimaryContainer
                                colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                                onClicked: {
                                    WullMind.selectModel(modelData)
                                    root.profileStage = 0
                                    Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
                                }
                                contentItem: RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 10
                                    spacing: 8
                                    StyledText {
                                        objectName: "wullModelLabel-" + String(modelOption.modelData.name)
                                        Layout.fillWidth: true
                                        text: modelOption.buttonText
                                        elide: Text.ElideRight
                                        font.pixelSize: Appearance.font.pixelSize.small
                                        color: modelOption.toggled
                                            ? Appearance.colors.colOnPrimaryContainer
                                            : Appearance.colors.colOnLayer1
                                    }
                                    MaterialSymbol {
                                        visible: modelOption.toggled
                                        text: "check"
                                        iconSize: 17
                                        color: Appearance.colors.colOnPrimaryContainer
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Rectangle {
                id: composer
                objectName: "wullChatComposer"
                visible: root.editing
                y: profileBox.visible ? profileBox.height + controlsColumn.spacing : 0
                width: content.width
                height: implicitHeight
                Layout.fillWidth: true
                implicitHeight: composerContent.height + 8
                radius: Math.max(12, Math.min(18, root.edgeRadius - 4))
                color: Qt.alpha(AbyssStyle.textColor, .05)
                border.width: 1
                border.color: Qt.alpha(AbyssStyle.textColor, .09)
                ColumnLayout {
                    id: composerContent
                    x: 4
                    y: 4
                    width: parent.width - 8
                    height: message.implicitHeight + 2 + 36
                    implicitHeight: height
                    spacing: 2
                    MaterialTextField {
                        id: message
                        objectName: "wullChatInput"
                        width: parent.width
                        height: implicitHeight
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        maximumLength: 1200
                        enableSettingsSearch: false
                        placeholderText: ""
                        Accessible.name: "Message"
                        background: Rectangle { color: "transparent"; border.width: 0 }
                        onAccepted: root.submit()
                        Keys.onEscapePressed: {
                            if (root.profileStage > 0) {
                                root.profileStage = 0
                                forceActiveFocus()
                            } else {
                                focus = false
                                WullMind.cancel()
                                WullMind.dismiss()
                            }
                        }
                    }
                    RowLayout {
                        y: message.height + 2
                        width: parent.width
                        height: 36
                        Layout.fillWidth: true
                        spacing: 6
                        RippleButton {
                            id: agentQuickBtn
                            objectName: "wullQuickAgent"
                            x: 0
                            y: 3
                            Layout.preferredWidth: 30
                            Layout.preferredHeight: 30
                            implicitWidth: 30
                            implicitHeight: 30
                            enabled: !WullMind.busy
                            buttonRadius: 15
                            colBackground: WullMind.agentToolsEnabled
                                ? Qt.alpha(AbyssStyle.accent, 0.22)
                                : Qt.alpha(AbyssStyle.accent, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.32)
                            onClicked: {
                                if (!WullMind.agentToolsEnabled) WullMind.agentToolsEnabled = true
                                WullMind.captureActiveAppContext()
                            }
                            StyledToolTip {
                                extraVisibleCondition: agentQuickBtn.buttonHovered
                                text: "Agent: " + WullMind.agentStatusSummary
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: WullMind.lastActiveAppContext?.version === "screenshot_ocr" ? "document_scanner" : "screen_search_desktop"
                                iconSize: 17
                                color: WullMind.agentToolsEnabled ? AbyssStyle.accent : AbyssStyle.textColorMuted
                            }
                        }
                        RippleButton {
                            objectName: "wullModelEffort"
                            x: 34
                            y: 3
                            Layout.preferredWidth: Math.max(68, Math.min(root.width - 90, effortLabel.implicitWidth + 60))
                            Layout.preferredHeight: 30
                            implicitWidth: Layout.preferredWidth
                            implicitHeight: 30
                            enabled: !WullMind.busy && WullMind.selectableModels.length > 0
                            buttonRadius: 15
                            onClicked: {
                                if (root.profileStage === 0)
                                    root.profileStage = 1
                                else if (root.profileStage === 1)
                                    root.profileStage = 2
                                else {
                                    root.profileStage = 0
                                    Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
                                }
                            }
                            Keys.onEscapePressed: {
                                root.profileStage = 0
                                Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
                            }
                            contentItem: RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 8
                                spacing: 5
                                MaterialSymbol {
                                    text: WullMind.thinkingSupported ? "psychology" : "bolt"
                                    iconSize: 16
                                    color: Appearance.colors.colPrimary
                                }
                                StyledText {
                                    id: effortLabel
                                    text: WullMind.thinkingEffortShortLabel
                                    elide: Text.ElideRight
                                    font.pixelSize: Appearance.font.pixelSize.smallest
                                }
                                MaterialSymbol {
                                    text: root.profileStage === 2 ? "swap_vert"
                                        : root.profileStage === 1 ? "expand_more" : "chevron_right"
                                    iconSize: 16
                                    color: Appearance.colors.colSubtext
                                }
                            }
                        }
                        Item { Layout.fillWidth: true }
                        RippleButton {
                            objectName: "wullChatSend"
                            x: parent.width - width
                            y: 0
                            Layout.preferredWidth: 36
                            Layout.preferredHeight: 36
                            implicitWidth: 36
                            implicitHeight: 36
                            enabled: !WullMind.busy && message.text.trim().length>0
                            buttonRadius: 18
                            onClicked: root.submit()
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: "arrow_upward"
                                iconSize: 20
                                color: Appearance.colors.colPrimary
                            }
                        }
                    }
                }
            }
        }
    }
    Loader {
        anchors.fill:parent;anchors.margins:12
        active:root.pocketOpen
        sourceComponent:Component {WullPocketPane {}}
    }
    function openPocket(): void {
        if (!WullMind.busy && WullPocket.show()) WullMind.dismiss()
    }
    Connections {
        target:WullMind
        function onConversationOpenChanged():void {if(WullMind.conversationOpen)WullPocket.close()}
        function onContextOpenChanged():void {if(WullMind.contextOpen)WullPocket.close()}
    }
    function submit(): void {
        if (WullMind.sendMessage(message.text)) message.text=""
    }
    onEditingChanged: {
        if (editing) Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
        else root.profileStage = 0
    }
    onControlsVisibleChanged: if (!controlsVisible) {
        root.profileStage = 0
        message.focus = false
    }
}
