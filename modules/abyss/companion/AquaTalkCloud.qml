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
    readonly property bool pocketOpen: AquaPocket.opened && AquaPocket.permitted
    readonly property bool editing: visible && AquaMind.conversationOpen && !pocketOpen
    readonly property bool controlsVisible: visible && (pocketOpen || editing || AquaMind.contextOpen || AquaMind.checkInStage.length>0)
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
    width: (pocketOpen || (AquaMind.contextOpen && !editing))
        ? Math.min(272, Math.max(200, outputWidth - 32))
        : Math.min(380, Math.max(220, outputWidth - 32))
    focus: visible && !editing && (AquaMind.contextOpen || pocketOpen)
    Keys.onEscapePressed: {
        if (AquaMind.contextOpen) AquaMind.dismiss()
        else if (pocketOpen) AquaPocket.close()
    }
    Connections {
        target: AquaMind
        function onContextOpenChanged(): void {
            if (AquaMind.contextOpen && !root.editing)
                Qt.callLater(() => root.forceActiveFocus())
        }
    }
    readonly property real nonEditingContentHeight: AquaMind.contextOpen
        ? content.implicitHeight
        : speechText.implicitHeight + (AquaMind.checkInStage.length > 0 ? content.spacing + 28 : 0)
    height: pocketOpen ? Math.min(310, Math.max(138, 66 + Math.max(1, AquaPocket.items.length) * 46))
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
    visible: allowed && actor.visible && actor.inputReady && (pocketOpen || AquaMind.text.length > 0 || AquaMind.conversationOpen || AquaMind.contextOpen)
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
        objectName: "aquaCloudBackground"
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
                    objectName: "aqua" + choice.field + "-" + modelData.value
                    x: index * ((choice.width + 2) / 5)
                    width: (choice.width - 8) / 5
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    implicitHeight: 28
                    padding: 3
                    enabled: !AquaMind.busy
                    buttonText: modelData.label
                    toggled: choice.selectedValue === modelData.value
                    colBackgroundToggled: Qt.alpha(AbyssStyle.accent, .2)
                    colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, .3)
                    onClicked: AquaMind.setCheckInChoice(choice.field, modelData.value)
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
            visible: AquaMind.contextOpen && !root.editing
            Layout.fillWidth: true
            spacing: 6
            MaterialSymbol {
                text: "menu_book"
                iconSize: 16
                color: AbyssStyle.accent
            }
            StyledText {
                Layout.fillWidth: true
                text: Translation.tr("Today")
                font.weight: Font.DemiBold
            }
        }
        StyledText {
            id: speechText
            width: content.width
            Layout.fillWidth: true
            visible: !root.editing
            text: AquaMind.text
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            font.pixelSize: Appearance.font.pixelSize.small
            Accessible.description: AquaMind.source === "ai" ? "AI reply" : "Companion message"
        }
        ColumnLayout {
            visible:AquaMind.contextOpen && !root.editing && AquaMind.checkInComplete
            Layout.fillWidth:true
            StyledText {
                Layout.fillWidth:true
                text:Translation.tr("Mood")+": "+AquaMind.userMood+"  ·  "+Translation.tr("Energy")+": "+AquaMind.userEnergy
                wrapMode:Text.WordWrap; color:Appearance.colors.colSubtext
            }
            Repeater {
                model: AquaMind.contextOpen ? AquaMind.reminderRows(DateTime.clock.date).slice(0,6) : []
                StyledText {
                    required property var modelData
                    Layout.fillWidth:true
                    text:String(Math.floor(modelData.start/60)).padStart(2,"0")+":"+String(modelData.start%60).padStart(2,"0")+" · "+modelData.title
                    textFormat:Text.PlainText
                    elide:Text.ElideRight; font.pixelSize:Appearance.font.pixelSize.small
                }
            }
            DialogButton {
                visible:AquaMind.journal.journalPath.length>0
                buttonText:Translation.tr("Open journal"); onClicked:AquaMind.openJournal()
            }
        }
        Item {
            visible: root.editing && AquaMind.historyLoaded && AquaMind.history.length===0
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
            objectName: "aquaChatHistory"
            visible: root.editing && AquaMind.history.length>0
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 7
            model: AquaMind.history
            property bool readyForOlder: false
            onCountChanged: {
                if (AquaMind.historyLoadingOlder) return
                readyForOlder=false
                Qt.callLater(() => {
                    if (!root.editing) return
                    transcript.positionViewAtEnd()
                    transcript.readyForOlder=true
                })
            }
            onContentYChanged: {
                if (readyForOlder && contentY<=12 && AquaMind.historyHasMore && !AquaMind.busy)
                    AquaMind.loadHistory(true)
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
                target: AquaMind
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
                visible: AquaMind.checkInStage === "mood"
                field: "mood"
                selectedValue: AquaMind.userMood || String(AquaMind.journal.mood ?? "").toLowerCase()
                options: [
                    {value: "terrible", label: Translation.tr("Awful")},
                    {value: "bad", label: Translation.tr("Low")},
                    {value: "okay", label: Translation.tr("Okay")},
                    {value: "good", label: Translation.tr("Good")},
                    {value: "great", label: Translation.tr("Great")}
                ]
            }
            CheckInRow {
                visible: AquaMind.checkInStage === "energy"
                field: "energy"
                selectedValue: AquaMind.userEnergy || String(AquaMind.journal.energy ?? "").toLowerCase()
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
                objectName: "aquaProfilePanel"
                visible: root.editing && (root.profileStage > 0 || AquaMind.pendingDeleteStage > 0)
                width: content.width
                height: implicitHeight
                Layout.fillWidth: true
                implicitHeight: profilePanel.implicitHeight + 16
                radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
                color: Qt.alpha(AbyssStyle.textColor, .05)
                border.width: 1
                border.color: AquaMind.pendingDeleteStage > 0
                    ? Qt.alpha(Appearance.colors.colError, 0.45)
                    : Qt.alpha(AbyssStyle.textColor, .08)
                ColumnLayout {
                    id: profilePanel
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 5
                    RowLayout {
                        objectName: "aquaDeleteConfirmBar"
                        visible: AquaMind.pendingDeleteStage > 0
                        Layout.fillWidth: true
                        spacing: 6
                        MaterialSymbol {
                            text: AquaMind.pendingDeleteStage === 2 ? "delete_forever" : "warning"
                            iconSize: 17
                            color: Appearance.colors.colError
                        }
                        StyledText {
                            Layout.fillWidth: true
                            text: (AquaMind.pendingDeleteStage === 2 ? "Confirm 2/2 (Final): " : "Confirm 1/2: ")
                                + AquaMind.pendingDeletePath.split("/").pop()
                            elide: Text.ElideMiddle
                            font.pixelSize: Appearance.font.pixelSize.smallest
                            font.weight: Font.DemiBold
                            color: Appearance.colors.colError
                        }
                        RippleButton {
                            id: confirmDelBtn
                            objectName: "aquaAgentConfirmDelete"
                            Layout.preferredWidth: AquaMind.pendingDeleteStage === 2 ? 78 : 68
                            Layout.preferredHeight: 26
                            implicitWidth: Layout.preferredWidth
                            implicitHeight: 26
                            buttonRadius: 13
                            colBackground: Qt.alpha(Appearance.colors.colError, 0.22)
                            colBackgroundHover: Qt.alpha(Appearance.colors.colError, 0.35)
                            onClicked: AquaMind.confirmAgentDelete()
                            contentItem: StyledText {
                                anchors.centerIn: parent
                                text: AquaMind.pendingDeleteStage === 2 ? "Delete 2/2" : "Confirm 1/2"
                                horizontalAlignment: Text.AlignHCenter
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                font.weight: Font.DemiBold
                                color: Appearance.colors.colError
                            }
                        }
                        RippleButton {
                            id: cancelDelBtn
                            objectName: "aquaAgentCancelDelete"
                            Layout.preferredWidth: 26
                            Layout.preferredHeight: 26
                            implicitWidth: 26
                            implicitHeight: 26
                            buttonRadius: 13
                            colBackground: Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.textColor, 0.16)
                            onClicked: AquaMind.cancelAgentDelete()
                            StyledToolTip {
                                extraVisibleCondition: cancelDelBtn.buttonHovered
                                text: "Cancel delete"
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: "close"
                                iconSize: 15
                                color: AbyssStyle.textColor
                            }
                        }
                    }
                    ColumnLayout {
                        objectName: "aquaAgentPanel"
                        visible: root.profileStage === 3
                        Layout.fillWidth: true
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Rectangle {
                                objectName: "aquaFullAccessBadge"
                                implicitWidth: fullAccessBadgeText.implicitWidth + 12
                                implicitHeight: 20
                                radius: 10
                                color: Qt.alpha(AbyssStyle.accent, 0.16)
                                border.width: 1
                                border.color: Qt.alpha(AbyssStyle.accent, 0.36)
                                StyledText {
                                    id: fullAccessBadgeText
                                    anchors.centerIn: parent
                                    text: "Full Access"
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    font.weight: Font.DemiBold
                                    color: AbyssStyle.accent
                                }
                            }
                            StyledText {
                                Layout.fillWidth: true
                                text: AquaMind.agentStatusSummary
                                elide: Text.ElideRight
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colSubtext
                            }
                            RippleButton {
                                id: scanAppBtn
                                objectName: "aquaAgentScanScreen"
                                Layout.preferredWidth: 26
                                Layout.preferredHeight: 26
                                implicitWidth: 26
                                implicitHeight: 26
                                buttonRadius: 13
                                colBackground: Qt.alpha(AbyssStyle.accent, 0.14)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.26)
                                onClicked: AquaMind.runAgentFileOp("access", "", "", "", "write", 0, "access screen")
                                StyledToolTip {
                                    extraVisibleCondition: scanAppBtn.buttonHovered
                                    text: "Inspect active app, Desktop & Screen OCR"
                                }
                                contentItem: MaterialSymbol {
                                    anchors.centerIn: parent
                                    text: "document_scanner"
                                    iconSize: 15
                                    color: AbyssStyle.accent
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 5
                            Repeater {
                                model: [
                                    { op: "access", icon: "folder_open", tip: "Access / Read Apps, Desktop or file" },
                                    { op: "edit", icon: "edit_note", tip: "Edit / Write file on Desktop" },
                                    { op: "run", icon: "terminal", tip: "Run script or command" },
                                    { op: "copy", icon: "content_copy", tip: "Copy file or folder" },
                                    { op: "cut", icon: "content_cut", tip: "Cut / Move file or folder" },
                                    { op: "delete", icon: "delete_forever", tip: "Delete (requires 2x confirmation)" }
                                ]
                                RippleButton {
                                    id: agentOpChip
                                    required property var modelData
                                    objectName: "aquaAgentOp-" + modelData.op
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 28
                                    implicitHeight: 28
                                    buttonRadius: 10
                                    colBackground: modelData.op === "delete"
                                        ? Qt.alpha(Appearance.colors.colError, 0.12)
                                        : Qt.alpha(AbyssStyle.accent, 0.10)
                                    colBackgroundHover: modelData.op === "delete"
                                        ? Qt.alpha(Appearance.colors.colError, 0.24)
                                        : Qt.alpha(AbyssStyle.accent, 0.22)
                                    onClicked: {
                                        const pocketItem = AquaPocket.permitted && AquaPocket.items.length > 0 ? AquaPocket.items[0] : null
                                        const sampleName = pocketItem ? String(pocketItem.name) : "~/Desktop/note.txt"
                                        if (modelData.op === "access" && pocketItem) {
                                            AquaMind.runAgentFileOp("access", pocketItem.path, "", "", "write", 0, "access " + pocketItem.name)
                                        } else if (modelData.op === "delete" && pocketItem) {
                                            AquaMind.runAgentFileOp("delete", pocketItem.path, "", "", "write", 0, "delete " + pocketItem.name)
                                        } else {
                                            if (modelData.op === "access") message.text = "access " + (pocketItem ? sampleName : "~/Desktop")
                                            else if (modelData.op === "edit") message.text = "edit " + sampleName + ": "
                                            else if (modelData.op === "run") message.text = "run " + (pocketItem ? sampleName : "~/Desktop/script.sh")
                                            else if (modelData.op === "copy") message.text = "copy " + sampleName + " -> ~/Desktop/copy.txt"
                                            else if (modelData.op === "cut") message.text = "cut " + sampleName + " -> ~/Documents/"
                                            else if (modelData.op === "delete") message.text = "delete " + sampleName
                                            Qt.callLater(() => {
                                                message.forceActiveFocus()
                                                message.cursorPosition = message.text.length
                                            })
                                        }
                                    }
                                    StyledToolTip {
                                        extraVisibleCondition: agentOpChip.buttonHovered
                                        text: agentOpChip.modelData.tip
                                    }
                                    contentItem: MaterialSymbol {
                                        anchors.centerIn: parent
                                        text: agentOpChip.modelData.icon
                                        iconSize: 16
                                        color: agentOpChip.modelData.op === "delete" ? Appearance.colors.colError : AbyssStyle.accent
                                    }
                                }
                            }
                        }
                    }
                    ColumnLayout {
                        objectName: "aquaEffortPanel"
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
                                text: AquaMind.thinkingEffortLabel
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colPrimary
                            }
                        }
                        StyledSlider {
                            objectName: "aquaThinkingEffort"
                            Layout.fillWidth: true
                            enableSettingsSearch: false
                            from: 0
                            to: Math.max(1,AquaMind.thinkingLevels.length-1)
                            stepSize: 1
                            value: Math.max(0, AquaMind.thinkingLevels.findIndex(level => level.value === AquaMind.effectiveThinkingEffort))
                            enabled: AquaMind.thinkingSupported && !AquaMind.busy
                            tooltipContent: AquaMind.thinkingEffortLabel
                            onMoved: {
                                const levels = AquaMind.thinkingLevels
                                AquaMind.setThinkingEffort(levels[Math.max(0, Math.min(levels.length-1, Math.round(value)))].value)
                            }
                        }
                        StyledText {
                            visible: !AquaMind.thinkingSupported
                            Layout.fillWidth: true
                            text: "This model uses its provider default. Tap the effort control again to choose another model."
                            wrapMode: Text.WordWrap
                            font.pixelSize: Appearance.font.pixelSize.smallest
                            color: Appearance.colors.colSubtext
                        }
                    }
                    ColumnLayout {
                        objectName: "aquaModelPicker"
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
                                text: AquaMind.currentModelLabel
                                elide: Text.ElideRight
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colPrimary
                            }
                        }
                        Repeater {
                            model: AquaMind.selectableModels
                            RippleButton {
                                id: modelOption
                                required property var modelData
                                objectName: "aquaModelOption-" + String(modelData.name)
                                Layout.fillWidth: true
                                implicitHeight: 36
                                buttonText: modelData.label ?? modelData.name
                                toggled: AquaMind.model === modelData.name
                                enabled: !AquaMind.busy
                                buttonRadius: 12
                                colBackground: "transparent"
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, .10)
                                colBackgroundToggled: Appearance.colors.colPrimaryContainer
                                colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                                onClicked: {
                                    AquaMind.selectModel(modelData)
                                    root.profileStage = 0
                                    Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
                                }
                                contentItem: RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 10
                                    spacing: 8
                                    StyledText {
                                        objectName: "aquaModelLabel-" + String(modelOption.modelData.name)
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
                objectName: "aquaChatComposer"
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
                        objectName: "aquaChatInput"
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
                                AquaMind.cancel()
                                AquaMind.dismiss()
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
                            objectName: "aquaQuickAgent"
                            x: 0
                            y: 3
                            Layout.preferredWidth: 30
                            Layout.preferredHeight: 30
                            implicitWidth: 30
                            implicitHeight: 30
                            enabled: !AquaMind.busy
                            buttonRadius: 15
                            colBackground: AquaMind.agentToolsEnabled
                                ? Qt.alpha(AbyssStyle.accent, 0.22)
                                : Qt.alpha(AbyssStyle.accent, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.32)
                            onClicked: {
                                if (!AquaMind.agentToolsEnabled) AquaMind.agentToolsEnabled = true
                                AquaMind.captureActiveAppContext()
                                root.profileStage = (root.profileStage === 3 ? 0 : 3)
                            }
                            StyledToolTip {
                                extraVisibleCondition: agentQuickBtn.buttonHovered
                                text: "Desktop Agent: " + AquaMind.agentStatusSummary
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: AquaMind.lastActiveAppContext?.version === "screenshot_ocr" ? "document_scanner" : "screen_search_desktop"
                                iconSize: 17
                                color: root.profileStage === 3 || AquaMind.agentToolsEnabled ? AbyssStyle.accent : AbyssStyle.textColorMuted
                            }
                        }
                        RippleButton {
                            objectName: "aquaModelEffort"
                            x: 34
                            y: 3
                            Layout.preferredWidth: Math.max(68, Math.min(root.width - 90, effortLabel.implicitWidth + 60))
                            Layout.preferredHeight: 30
                            implicitWidth: Layout.preferredWidth
                            implicitHeight: 30
                            enabled: !AquaMind.busy && AquaMind.selectableModels.length > 0
                            buttonRadius: 15
                            onClicked: {
                                if (root.profileStage === 0 || root.profileStage === 3)
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
                                    text: AquaMind.thinkingSupported ? "psychology" : "bolt"
                                    iconSize: 16
                                    color: Appearance.colors.colPrimary
                                }
                                StyledText {
                                    id: effortLabel
                                    text: AquaMind.thinkingEffortShortLabel
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
                            objectName: "aquaChatSend"
                            x: parent.width - width
                            y: 0
                            Layout.preferredWidth: 36
                            Layout.preferredHeight: 36
                            implicitWidth: 36
                            implicitHeight: 36
                            enabled: !AquaMind.busy && message.text.trim().length>0
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
        sourceComponent:Component {AquaPocketPane {actor:root.actor}}
    }
    function openPocket(): void {
        if (!AquaMind.busy && AquaPocket.show()) AquaMind.dismiss()
    }
    Connections {
        target:AquaMind
        function onConversationOpenChanged():void {if(AquaMind.conversationOpen)AquaPocket.close()}
        function onContextOpenChanged():void {if(AquaMind.contextOpen)AquaPocket.close()}
    }
    function submit(): void {
        if (AquaMind.sendMessage(message.text)) message.text=""
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
