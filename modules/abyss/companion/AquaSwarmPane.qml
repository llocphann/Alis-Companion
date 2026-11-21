pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.functions
import qs.modules.common.widgets
import qs.modules.abyss.looks

Item {
    id: root
    objectName: "aquaSwarmPane"
    property Item actor: null
    property bool embeddedMode: false
    property string activeSubTab: "board"
    property string selectedNodeId: "coordinator"
    property string newMemberRole: "researcher"
    signal closeRequested()
    signal prefillChatRequested(string text)

    readonly property bool hovered: root.visible && swarmHover.hovered
    readonly property color colTonalBase: ColorUtils.applyAlpha(
        Qt.color(Appearance.colors.colLayer1Base).valid
            ? Appearance.colors.colLayer1Base
            : Appearance.colors.colLayer0Base,
        1.0
    )
    readonly property real tonalDir: Qt.color(root.colTonalBase).hslLightness > 0.86 ? -1 : 1
    readonly property color colBoxSubtle: ColorUtils.applyAlpha(
        ColorUtils.tonalStep(root.colTonalBase, root.tonalDir * 0.018, 1.04),
        0.90
    )
    readonly property color colBoxLayer: ColorUtils.applyAlpha(
        ColorUtils.tonalStep(root.colTonalBase, root.tonalDir * 0.026, 1.05),
        0.92
    )
    readonly property color colBoxElevated: ColorUtils.applyAlpha(
        ColorUtils.tonalStep(root.colTonalBase, root.tonalDir * 0.046, 1.08),
        0.94
    )
    readonly property bool hasActiveInputFocus: false
    readonly property string swarmStatus: String(AquaMind.swarmState?.status ?? "idle")
    readonly property string swarmTitle: String(AquaMind.swarmState?.title ?? "Companion Swarm")
    readonly property string swarmGoal: String(AquaMind.swarmState?.goal ?? "")
    readonly property var boardItems: AquaMind.swarmBoard
    readonly property var memberItems: AquaMind.swarmMembers
    readonly property var graphNodes: Array.isArray(AquaMind.swarmGraph?.nodes) ? AquaMind.swarmGraph.nodes : []
    readonly property var graphEdges: Array.isArray(AquaMind.swarmGraph?.edges) ? AquaMind.swarmGraph.edges : []

    readonly property var selectedNodeObj: {
        const list = root.graphNodes;
        for (let i = 0; i < list.length; ++i) {
            if (String(list[i]?.id ?? "") === root.selectedNodeId)
                return list[i];
        }
        return list.length > 0 ? list[0] : null;
    }

    readonly property var roleOptions: [
        { key: "researcher", label: "Researcher", icon: "travel_explore" },
        { key: "planner", label: "Planner", icon: "architecture" },
        { key: "coder", label: "Coder", icon: "code" },
        { key: "executor", label: "Executor", icon: "terminal" },
        { key: "verifier", label: "Verifier", icon: "verified" }
    ]

    function statusColor(st): color {
        const s = String(st ?? "waiting");
        if (s === "working" || s === "running")
            return AbyssStyle.accent;
        if (s === "done" || s === "completed")
            return Qt.rgba(0.32, 0.84, 0.58, 1.0);
        if (s === "stopped")
            return Appearance.colors.colError;
        return AbyssStyle.textColorMuted;
    }

    function nodeGeometry(nodeItem, stageW, stageH): var {
        const maxLayer = root.graphNodes.reduce((acc, n) => Math.max(acc, Number(n?.layer ?? 0)), 0);
        const totalCols = Math.max(1, maxLayer + 1);
        const padX = 14;
        const padY = 14;
        const nodeW = Math.max(88, Math.min(132, Math.floor((stageW - padX * 2 - (totalCols - 1) * 18) / totalCols)));
        const nodeH = 48;
        const lyr = Math.max(0, Number(nodeItem?.layer ?? 0));
        const slot = Math.max(0, Number(nodeItem?.slot ?? 0));
        const count = Math.max(1, Number(nodeItem?.layerCount ?? 1));
        const usableW = Math.max(nodeW, stageW - padX * 2);
        const xPos = totalCols === 1
            ? Math.round((stageW - nodeW) * 0.5)
            : Math.round(padX + (usableW - nodeW) * (lyr / Math.max(1, totalCols - 1)));
        const usableH = Math.max(nodeH, stageH - padY * 2);
        const stepY = usableH / count;
        const yPos = Math.round(padY + stepY * (slot + 0.5) - nodeH * 0.5);
        return {
            x: xPos,
            y: Math.max(8, Math.min(Math.max(8, stageH - nodeH - 8), yPos)),
            w: nodeW,
            h: nodeH
        };
    }

    function focusComposer(): void {
        root.forceActiveFocus();
    }

    function submitNewMember(): void {
        AquaMind.addSwarmMember(root.newMemberRole, "", false);
    }

    function mentionMemberOnBoard(memberId): void {
        const mid = String(memberId ?? "").trim();
        if (!mid)
            return;
        root.activeSubTab = "board";
        root.prefillChatRequested("/swarm @" + mid + " ");
    }

    Component.onCompleted: {
        AquaMind.refreshSwarmState();
    }

    onGraphNodesChanged: {
        dagCanvas.requestPaint();
    }
    onGraphEdgesChanged: {
        dagCanvas.requestPaint();
    }

    HoverHandler {
        id: swarmHover
        blocking: false
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 7

        // Top Mission Header + Sub-tabs (Board | Members | Graph) — responsive for Chatbox Right Sidebar
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: headerInnerCol.implicitHeight + 12
            implicitHeight: headerInnerCol.implicitHeight + 12
            radius: 12
            color: root.colBoxSubtle
            border.width: 1
            border.color: Qt.alpha(AbyssStyle.textColor, 0.08)

            ColumnLayout {
                id: headerInnerCol
                anchors.fill: parent
                anchors.margins: 6
                spacing: 5

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Rectangle {
                        Layout.preferredHeight: 20
                        Layout.preferredWidth: statusRow.implicitWidth + 10
                        radius: 10
                        color: Qt.alpha(root.statusColor(root.swarmStatus), 0.16)
                        border.width: 1
                        border.color: Qt.alpha(root.statusColor(root.swarmStatus), 0.36)
                        Row {
                            id: statusRow
                            anchors.centerIn: parent
                            spacing: 4
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 6
                                height: 6
                                radius: 3
                                color: root.statusColor(root.swarmStatus)
                            }
                            StyledText {
                                anchors.verticalCenter: parent.verticalCenter
                                text: root.swarmStatus.toUpperCase()
                                font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                font.weight: Font.Bold
                                color: root.statusColor(root.swarmStatus)
                            }
                        }
                    }

                    StyledText {
                        Layout.fillWidth: true
                        text: root.swarmGoal.length > 0 ? root.swarmTitle : "Swarm"
                        elide: Text.ElideRight
                        font.pixelSize: Appearance.font.pixelSize.smaller
                        font.weight: Font.DemiBold
                        color: AbyssStyle.textColor
                    }

                    RippleButton {
                        id: stopSwarmBtn
                        objectName: "aquaSwarmStopButton"
                        visible: AquaMind.swarmBusy
                        Layout.preferredWidth: 24
                        Layout.preferredHeight: 24
                        implicitWidth: 24
                        implicitHeight: 24
                        leftPadding: 0
                        rightPadding: 0
                        topPadding: 0
                        bottomPadding: 0
                        buttonRadius: 12
                        colBackground: Qt.alpha(Appearance.colors.colError, 0.16)
                        colBackgroundHover: Qt.alpha(Appearance.colors.colError, 0.28)
                        onClicked: AquaMind.stopSwarm()
                        StyledToolTip {
                            extraVisibleCondition: stopSwarmBtn.buttonHovered
                            text: "Stop /swarm"
                        }
                        contentItem: Item {
                            MaterialSymbol {
                                anchors.centerIn: parent
                                text: "stop_circle"
                                iconSize: 14
                                color: Appearance.colors.colError
                            }
                        }
                    }

                    RippleButton {
                        id: clearSwarmBtn
                        objectName: "aquaSwarmClearButton"
                        visible: !AquaMind.swarmBusy && (root.boardItems.length > 0 || root.memberItems.length > 1)
                        Layout.preferredWidth: 24
                        Layout.preferredHeight: 24
                        implicitWidth: 24
                        implicitHeight: 24
                        leftPadding: 0
                        rightPadding: 0
                        topPadding: 0
                        bottomPadding: 0
                        buttonRadius: 12
                        colBackground: root.colBoxLayer
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.16)
                        onClicked: {
                            root.selectedNodeId = "coordinator";
                            AquaMind.clearSwarm();
                        }
                        StyledToolTip {
                            extraVisibleCondition: clearSwarmBtn.buttonHovered
                            text: "Reset board & workers"
                        }
                        contentItem: Item {
                            MaterialSymbol {
                                anchors.centerIn: parent
                                text: "restart_alt"
                                iconSize: 14
                                color: AbyssStyle.textColorMuted
                            }
                        }
                    }
                }

                // Sub-tabs: Board | Members | Graph
                Rectangle {
                    objectName: "aquaSwarmSubTabs"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 26
                    implicitHeight: 26
                    radius: 13
                    color: root.colBoxLayer
                    border.width: 1
                    border.color: Qt.alpha(AbyssStyle.textColor, 0.08)

                    RowLayout {
                        id: subTabRow
                        anchors.fill: parent
                        anchors.margins: 2
                        spacing: 2

                        Repeater {
                            model: [
                                { key: "board", label: "Board", icon: "forum", count: root.boardItems.length, obj: "aquaSwarmBoardTab" },
                                { key: "members", label: "Members", icon: "groups", count: root.memberItems.length, obj: "aquaSwarmMembersTab" },
                                { key: "graph", label: "Graph", icon: "account_tree", count: root.graphEdges.length, obj: "aquaSwarmGraphTab" }
                            ]
                            RippleButton {
                                id: subTabBtn
                                required property var modelData
                                objectName: modelData.obj
                                readonly property bool active: root.activeSubTab === modelData.key
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                implicitHeight: 22
                                implicitWidth: subTabInner.implicitWidth + 10
                                leftPadding: 0
                                rightPadding: 0
                                topPadding: 0
                                bottomPadding: 0
                                buttonRadius: 11
                                toggled: active
                                colBackground: "transparent"
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.12)
                                colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.24)
                                colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.32)
                                onClicked: {
                                    root.activeSubTab = modelData.key;
                                    if (modelData.key === "graph")
                                        dagCanvas.requestPaint();
                                }
                                contentItem: Item {
                                    clip: true
                                    Row {
                                        id: subTabInner
                                        anchors.centerIn: parent
                                        spacing: 3
                                        MaterialSymbol {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: subTabBtn.modelData.icon
                                            iconSize: 13
                                            color: subTabBtn.active ? AbyssStyle.accent : AbyssStyle.textColorMuted
                                        }
                                        StyledText {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: subTabBtn.modelData.count > 0 ? (subTabBtn.modelData.label + " " + subTabBtn.modelData.count) : subTabBtn.modelData.label
                                            font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                            font.weight: subTabBtn.active ? Font.DemiBold : Font.Normal
                                            color: subTabBtn.active ? AbyssStyle.textColor : AbyssStyle.textColorMuted
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // =========================================================
        // TAB 1: BOARD (Shared chronological message board)
        // =========================================================
        ColumnLayout {
            visible: root.activeSubTab === "board"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 6

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                // Empty Board Starter View
                ColumnLayout {
                    anchors.centerIn: parent
                    width: Math.min(parent.width - 24, 420)
                    visible: root.boardItems.length === 0
                    spacing: 8

                    MaterialSymbol {
                        Layout.alignment: Qt.AlignHCenter
                        text: "hub"
                        iconSize: 24
                        color: AbyssStyle.accent
                    }
                    StyledText {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: "Swarm Board"
                        font.pixelSize: Appearance.font.pixelSize.small
                        font.weight: Font.DemiBold
                        color: AbyssStyle.textColor
                    }
                    StyledText {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        text: "/swarm <goal> in chat, or pick a preset:"
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        color: Appearance.colors.colSubtext
                    }
                    Row {
                        Layout.alignment: Qt.AlignHCenter
                        spacing: 6
                        Repeater {
                            model: [
                                "Audit shell health",
                                "Inspect git diff & tests"
                            ]
                            RippleButton {
                                required property string modelData
                                implicitHeight: 24
                                implicitWidth: starterText.implicitWidth + 24
                                width: implicitWidth
                                height: 24
                                leftPadding: 0
                                rightPadding: 0
                                topPadding: 0
                                bottomPadding: 0
                                buttonRadius: 12
                                colBackground: Qt.alpha(AbyssStyle.accent, 0.12)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
                                onClicked: AquaMind.startSwarm(modelData)
                                contentItem: Item {
                                    StyledText {
                                        id: starterText
                                        anchors.centerIn: parent
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                        text: modelData
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                        color: AbyssStyle.accent
                                    }
                                }
                            }
                        }
                    }
                }

                ListView {
                    id: boardListView
                    objectName: "aquaSwarmBoardList"
                    anchors.fill: parent
                    visible: root.boardItems.length > 0
                    clip: true
                    spacing: 6
                    boundsBehavior: Flickable.StopAtBounds
                    model: root.boardItems
                    onCountChanged: Qt.callLater(() => boardListView.positionViewAtEnd())

                    delegate: Rectangle {
                        id: postCard
                        required property var modelData
                        required property int index
                        objectName: "aquaSwarmBoardPost-" + String(modelData?.seq ?? index)
                        width: boardListView.width
                        implicitHeight: postCol.implicitHeight + 14
                        height: implicitHeight
                        radius: 10
                        readonly property string postKind: String(modelData?.kind ?? "finding")
                        readonly property bool isSynthesis: postKind === "synthesis"
                        readonly property bool isUser: postKind === "user"
                        color: isSynthesis
                            ? Qt.alpha(AbyssStyle.accent, 0.11)
                            : (isUser ? root.colBoxElevated : root.colBoxSubtle)
                        border.width: 1
                        border.color: isSynthesis ? Qt.alpha(AbyssStyle.accent, 0.32) : Qt.alpha(AbyssStyle.textColor, 0.08)

                        ColumnLayout {
                            id: postCol
                            x: 9
                            y: 7
                            width: parent.width - 18
                            spacing: 4

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                StyledText {
                                    text: "#" + String(postCard.modelData?.seq ?? (postCard.index + 1))
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    font.weight: Font.Bold
                                    color: AbyssStyle.accent
                                }

                                StyledText {
                                    text: "@" + String(postCard.modelData?.sender ?? "coordinator")
                                    font.pixelSize: Appearance.font.pixelSize.smallest
                                    font.weight: Font.DemiBold
                                    color: AbyssStyle.textColor
                                }

                                StyledText {
                                    text: "· " + String(postCard.modelData?.senderTitle ?? "")
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    color: Appearance.colors.colSubtext
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Rectangle {
                                    implicitHeight: 16
                                    implicitWidth: kindLabel.implicitWidth + 10
                                    radius: 8
                                    color: Qt.alpha(AbyssStyle.accent, 0.14)
                                    StyledText {
                                        id: kindLabel
                                        anchors.centerIn: parent
                                        text: postCard.postKind.toUpperCase()
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        font.weight: Font.DemiBold
                                        color: AbyssStyle.accent
                                    }
                                }
                            }

                            StyledText {
                                Layout.fillWidth: true
                                text: String(postCard.modelData?.text ?? "")
                                textFormat: Text.PlainText
                                wrapMode: Text.WordWrap
                                font.pixelSize: Appearance.font.pixelSize.smaller
                                color: AbyssStyle.textColor
                            }
                        }
                    }
                }
            }
        }

        // =========================================================
        // TAB 2: MEMBERS (Roster, Status, Isolation & Add Worker)
        // =========================================================
        ColumnLayout {
            visible: root.activeSubTab === "members"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 6

            ListView {
                id: membersListView
                objectName: "aquaSwarmMembersList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                boundsBehavior: Flickable.StopAtBounds
                model: root.memberItems

                delegate: Rectangle {
                    id: memberCard
                    required property var modelData
                    required property int index
                    readonly property string memberId: String(modelData?.id ?? "")
                    readonly property string memberStatus: String(modelData?.status ?? "waiting")
                    readonly property bool isCoord: memberId === "coordinator"
                    objectName: "aquaSwarmMemberCard-" + memberId
                    width: membersListView.width
                    implicitHeight: memberCol.implicitHeight + 16
                    height: implicitHeight
                    radius: 11
                    color: memberStatus === "working" ? Qt.alpha(AbyssStyle.accent, 0.10) : root.colBoxSubtle
                    border.width: 1
                    border.color: memberStatus === "working" ? Qt.alpha(AbyssStyle.accent, 0.38) : Qt.alpha(AbyssStyle.textColor, 0.08)

                    ColumnLayout {
                        id: memberCol
                        x: 10
                        y: 8
                        width: parent.width - 20
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            MaterialSymbol {
                                text: String(memberCard.modelData?.icon ?? "smart_toy")
                                iconSize: 15
                                color: root.statusColor(memberCard.memberStatus)
                            }

                            StyledText {
                                text: "@" + memberCard.memberId
                                font.pixelSize: Appearance.font.pixelSize.smaller
                                font.weight: Font.Bold
                                color: AbyssStyle.textColor
                            }

                            StyledText {
                                text: String(memberCard.modelData?.title ?? "")
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colSubtext
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }

                            Rectangle {
                                implicitHeight: 18
                                implicitWidth: roLabel.implicitWidth + 8
                                radius: 9
                                color: root.colBoxElevated
                                StyledText {
                                    id: roLabel
                                    anchors.centerIn: parent
                                    text: memberCard.modelData?.readOnly !== false ? "RO" : "RW"
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                    font.weight: Font.DemiBold
                                    color: Appearance.colors.colSubtext
                                }
                            }

                            Rectangle {
                                implicitHeight: 18
                                implicitWidth: mStatusText.implicitWidth + 10
                                radius: 9
                                color: Qt.alpha(root.statusColor(memberCard.memberStatus), 0.16)
                                StyledText {
                                    id: mStatusText
                                    anchors.centerIn: parent
                                    text: memberCard.memberStatus.toUpperCase()
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                    font.weight: Font.Bold
                                    color: root.statusColor(memberCard.memberStatus)
                                }
                            }

                            RippleButton {
                                visible: !memberCard.isCoord
                                implicitWidth: 22
                                implicitHeight: 22
                                width: 22
                                height: 22
                                leftPadding: 0
                                rightPadding: 0
                                topPadding: 0
                                bottomPadding: 0
                                buttonRadius: 11
                                colBackground: Qt.alpha(AbyssStyle.accent, 0.12)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                                onClicked: root.mentionMemberOnBoard(memberCard.memberId)
                                contentItem: Item {
                                    MaterialSymbol {
                                        anchors.centerIn: parent
                                        text: "alternate_email"
                                        iconSize: 13
                                        color: AbyssStyle.accent
                                    }
                                }
                            }

                            RippleButton {
                                objectName: "aquaSwarmRemoveMember-" + memberCard.memberId
                                visible: !memberCard.isCoord && memberCard.memberStatus !== "working"
                                implicitWidth: 22
                                implicitHeight: 22
                                width: 22
                                height: 22
                                leftPadding: 0
                                rightPadding: 0
                                topPadding: 0
                                bottomPadding: 0
                                buttonRadius: 11
                                colBackground: root.colBoxLayer
                                colBackgroundHover: Qt.alpha(Appearance.colors.colError, 0.20)
                                onClicked: AquaMind.removeSwarmMember(memberCard.memberId)
                                contentItem: Item {
                                    MaterialSymbol {
                                        anchors.centerIn: parent
                                        text: "close"
                                        iconSize: 13
                                        color: AbyssStyle.textColorMuted
                                    }
                                }
                            }
                        }

                        StyledText {
                            Layout.fillWidth: true
                            visible: String(memberCard.modelData?.task ?? "").length > 0
                            text: "Brief: " + String(memberCard.modelData?.task ?? "")
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                            font.pixelSize: Appearance.font.pixelSize.smallest
                            color: Appearance.colors.colSubtext
                        }

                        StyledText {
                            Layout.fillWidth: true
                            visible: String(memberCard.modelData?.summary ?? "").length > 0
                            text: String(memberCard.modelData?.summary ?? "")
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                            font.pixelSize: Appearance.font.pixelSize.smallest
                            color: AbyssStyle.textColor
                        }
                    }
                }
            }

            // Add Specialist Worker Row
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: addWorkerCol.implicitHeight + 14
                radius: 12
                color: root.colBoxSubtle
                border.width: 1
                border.color: Qt.alpha(AbyssStyle.textColor, 0.09)

                ColumnLayout {
                    id: addWorkerCol
                    x: 8
                    y: 7
                    width: parent.width - 16
                    spacing: 5

                    Flow {
                        Layout.fillWidth: true
                        spacing: 4
                        Repeater {
                            model: root.roleOptions
                            RippleButton {
                                id: roleChip
                                required property var modelData
                                objectName: "aquaSwarmRoleChip-" + modelData.key
                                readonly property bool chosen: root.newMemberRole === modelData.key
                                implicitHeight: 22
                                implicitWidth: roleChipRow.implicitWidth + 14
                                width: implicitWidth
                                height: 22
                                leftPadding: 0
                                rightPadding: 0
                                topPadding: 0
                                bottomPadding: 0
                                buttonRadius: 11
                                toggled: chosen
                                colBackground: root.colBoxLayer
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                                colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.24)
                                colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.32)
                                onClicked: root.newMemberRole = modelData.key
                                contentItem: Item {
                                    Row {
                                        id: roleChipRow
                                        anchors.centerIn: parent
                                        spacing: 3
                                        MaterialSymbol {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: roleChip.modelData.icon
                                            iconSize: 12
                                            color: roleChip.chosen ? AbyssStyle.accent : AbyssStyle.textColorMuted
                                        }
                                        StyledText {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: roleChip.modelData.label
                                            font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                            font.weight: roleChip.chosen ? Font.DemiBold : Font.Normal
                                            color: roleChip.chosen ? AbyssStyle.textColor : AbyssStyle.textColorMuted
                                        }
                                    }
                                }
                            }
                        }
                    }

                    RippleButton {
                        objectName: "aquaSwarmAddMemberButton"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 28
                        implicitHeight: 28
                        implicitWidth: addBtnRow.implicitWidth + 16
                        leftPadding: 0
                        rightPadding: 0
                        topPadding: 0
                        bottomPadding: 0
                        buttonRadius: 9
                        enabled: !AquaMind.swarmBusy
                        colBackground: Qt.alpha(AbyssStyle.accent, 0.18)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.30)
                        onClicked: root.submitNewMember()
                        contentItem: Item {
                            Row {
                                id: addBtnRow
                                anchors.centerIn: parent
                                spacing: 4
                                MaterialSymbol {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "person_add"
                                    iconSize: 13
                                    color: AbyssStyle.accent
                                }
                                StyledText {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "Add " + root.newMemberRole.charAt(0).toUpperCase() + root.newMemberRole.slice(1) + " Worker"
                                    font.pixelSize: Appearance.font.pixelSize.smallest
                                    font.weight: Font.DemiBold
                                    color: AbyssStyle.accent
                                }
                            }
                        }
                    }
                }
            }
        }

        // =========================================================
        // TAB 3: GRAPH (Interactive Layered DAG & Node Inspector)
        // =========================================================
        ColumnLayout {
            visible: root.activeSubTab === "graph"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 6

            Rectangle {
                id: graphStage
                objectName: "aquaSwarmGraphStage"
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 12
                clip: true
                color: root.colBoxSubtle
                border.width: 1
                border.color: Qt.alpha(AbyssStyle.textColor, 0.08)

                onWidthChanged: dagCanvas.requestPaint()
                onHeightChanged: dagCanvas.requestPaint()

                Canvas {
                    id: dagCanvas
                    objectName: "aquaSwarmGraphCanvas"
                    anchors.fill: parent
                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.clearRect(0, 0, width, height);
                        if (width <= 10 || height <= 10)
                            return;

                        const nodeMap = {};
                        const nodes = root.graphNodes;
                        for (let i = 0; i < nodes.length; ++i) {
                            const n = nodes[i];
                            if (n && n.id)
                                nodeMap[String(n.id)] = root.nodeGeometry(n, width, height);
                        }

                        const edges = root.graphEdges;
                        for (let j = 0; j < edges.length; ++j) {
                            const edge = edges[j];
                            const src = nodeMap[String(edge?.from ?? "")];
                            const dst = nodeMap[String(edge?.to ?? "")];
                            if (!src || !dst)
                                continue;

                            const isBackReport = String(edge?.kind ?? "") === "reports";
                            const startX = isBackReport ? (src.x + src.w * 0.5) : (src.x + src.w);
                            const startY = isBackReport ? (src.y + src.h) : (src.y + src.h * 0.5);
                            const endX = isBackReport ? (dst.x + dst.w * 0.5) : dst.x;
                            const endY = isBackReport ? (dst.y + dst.h) : (dst.y + dst.h * 0.5);

                            ctx.save();
                            ctx.beginPath();
                            if (edge.active) {
                                ctx.strokeStyle = Qt.alpha(AbyssStyle.accent, 0.92);
                                ctx.lineWidth = 2.2;
                            } else if (edge.completed) {
                                ctx.strokeStyle = Qt.rgba(0.32, 0.84, 0.58, 0.72);
                                ctx.lineWidth = 1.8;
                            } else {
                                ctx.strokeStyle = Qt.alpha(AbyssStyle.textColor, 0.24);
                                ctx.lineWidth = 1.4;
                            }

                            if (isBackReport) {
                                const arcY = Math.min(height - 6, Math.max(startY, endY) + 26);
                                ctx.moveTo(startX, startY);
                                ctx.bezierCurveTo(startX, arcY, endX, arcY, endX, endY);
                            } else {
                                const midX = (startX + endX) * 0.5;
                                ctx.moveTo(startX, startY);
                                ctx.bezierCurveTo(midX, startY, midX, endY, endX, endY);
                            }
                            ctx.stroke();

                            // Arrowhead at destination
                            ctx.beginPath();
                            ctx.fillStyle = ctx.strokeStyle;
                            if (isBackReport) {
                                ctx.moveTo(endX, endY);
                                ctx.lineTo(endX - 4, endY + 6);
                                ctx.lineTo(endX + 4, endY + 6);
                            } else {
                                ctx.moveTo(endX, endY);
                                ctx.lineTo(endX - 6, endY - 4);
                                ctx.lineTo(endX - 6, endY + 4);
                            }
                            ctx.closePath();
                            ctx.fill();
                            ctx.restore();
                        }
                    }
                }

                Repeater {
                    model: root.graphNodes
                    Rectangle {
                        id: dagNodeCard
                        required property var modelData
                        required property int index
                        readonly property string nodeId: String(modelData?.id ?? "")
                        readonly property string nodeStatus: String(modelData?.status ?? "waiting")
                        readonly property bool isSelected: root.selectedNodeId === nodeId
                        readonly property var geom: root.nodeGeometry(modelData, graphStage.width, graphStage.height)
                        objectName: "aquaSwarmGraphNode-" + nodeId
                        x: geom.x
                        y: geom.y
                        width: geom.w
                        height: geom.h
                        radius: 11
                        color: isSelected
                            ? Qt.alpha(AbyssStyle.accent, 0.20)
                            : Qt.rgba(AbyssStyle.surfaceDeep.r, AbyssStyle.surfaceDeep.g, AbyssStyle.surfaceDeep.b, 0.92)
                        border.width: isSelected || nodeStatus === "working" ? 1.5 : 1
                        border.color: isSelected
                            ? AbyssStyle.accent
                            : (nodeStatus === "working" ? Qt.alpha(AbyssStyle.accent, 0.65) : Qt.alpha(AbyssStyle.textColor, 0.16))

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 6
                            spacing: 2

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                MaterialSymbol {
                                    text: String(dagNodeCard.modelData?.icon ?? "smart_toy")
                                    iconSize: 13
                                    color: root.statusColor(dagNodeCard.nodeStatus)
                                }
                                StyledText {
                                    Layout.fillWidth: true
                                    text: String(dagNodeCard.modelData?.label ?? "")
                                    elide: Text.ElideRight
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    font.weight: Font.Bold
                                    color: AbyssStyle.textColor
                                }
                                Rectangle {
                                    width: 6
                                    height: 6
                                    radius: 3
                                    color: root.statusColor(dagNodeCard.nodeStatus)
                                }
                            }

                            StyledText {
                                Layout.fillWidth: true
                                text: String(dagNodeCard.modelData?.title ?? "")
                                elide: Text.ElideRight
                                font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                color: Appearance.colors.colSubtext
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.selectedNodeId = dagNodeCard.nodeId
                        }
                    }
                }
            }

            // Selected Node Inspector Strip
            Rectangle {
                objectName: "aquaSwarmGraphInspector"
                Layout.fillWidth: true
                visible: root.selectedNodeObj !== null
                implicitHeight: inspectorCol.implicitHeight + 14
                radius: 11
                color: root.colBoxSubtle
                border.width: 1
                border.color: Qt.alpha(AbyssStyle.textColor, 0.09)

                ColumnLayout {
                    id: inspectorCol
                    x: 10
                    y: 7
                    width: parent.width - 20
                    spacing: 3

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        StyledText {
                            text: "@" + String(root.selectedNodeObj?.id ?? "coordinator")
                            font.pixelSize: Appearance.font.pixelSize.smaller
                            font.weight: Font.Bold
                            color: AbyssStyle.accent
                        }
                        StyledText {
                            Layout.fillWidth: true
                            text: String(root.selectedNodeObj?.title ?? "") + " · " + (root.selectedNodeObj?.readOnly !== false ? "Read-Only" : "Read-Write")
                            elide: Text.ElideRight
                            font.pixelSize: Appearance.font.pixelSize.smallest
                            color: Appearance.colors.colSubtext
                        }
                        RippleButton {
                            visible: String(root.selectedNodeObj?.id ?? "") !== "coordinator"
                            implicitHeight: 22
                            implicitWidth: msgWorkerLabel.implicitWidth + 16
                            width: implicitWidth
                            height: 22
                            leftPadding: 0
                            rightPadding: 0
                            topPadding: 0
                            bottomPadding: 0
                            buttonRadius: 11
                            colBackground: Qt.alpha(AbyssStyle.accent, 0.16)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.26)
                            onClicked: root.mentionMemberOnBoard(String(root.selectedNodeObj?.id ?? ""))
                            contentItem: Item {
                                StyledText {
                                    id: msgWorkerLabel
                                    anchors.centerIn: parent
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                    text: "@Message"
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    font.weight: Font.DemiBold
                                    color: AbyssStyle.accent
                                }
                            }
                        }
                    }

                    StyledText {
                        Layout.fillWidth: true
                        text: String(root.selectedNodeObj?.summary || root.selectedNodeObj?.task || "/swarm ready.")
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        color: AbyssStyle.textColor
                    }
                }
            }
        }
    }
}
