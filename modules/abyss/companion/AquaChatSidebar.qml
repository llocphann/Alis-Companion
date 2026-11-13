pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.widgets
import qs.modules.abyss.looks

Rectangle {
    id: sidebar
    objectName: "aquaChatSidebar"
    property var mind: AquaMind
    property bool compact: false
    readonly property bool isNarrow: sidebar.width < 176
    readonly property bool isVeryNarrow: sidebar.width < 156
    readonly property bool isWide: sidebar.width >= 230
    signal threadSelected(string threadId)
    signal threadTurnSelected(string threadId, int matchedTurnIndex)

    radius: 12
    color: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.94)
    border.width: 1
    border.color: Qt.rgba(Appearance.colors.colOutlineVariant.r, Appearance.colors.colOutlineVariant.g, Appearance.colors.colOutlineVariant.b, 0.42)
    clip: true

    // Inline editor state: "", "new_project", "rename_thread", "rename_project", "move_thread"
    property string editorMode: ""
    property string editorTargetId: ""
    property string editorText: ""
    property string editorWorkspace: ""
    property string draggingThreadId: ""
    property string draggingProjectId: ""
    property string searchQuery: ""
    readonly property bool searchActive: sidebar.searchQuery.trim().length > 0
    readonly property bool hasActiveInputFocus: sidebarSearchInput.activeFocus
        || inlineNameInput.activeFocus
        || inlineWorkspaceInput.activeFocus

    Timer {
        id: searchDebounce
        interval: 120
        repeat: false
        onTriggered: {
            if (sidebar.mind && typeof sidebar.mind.searchThreads === "function") {
                sidebar.mind.searchThreads(sidebar.searchQuery)
            }
        }
    }

    function threadMatchesFilter(t): bool {
        if (!t) return false
        const q = sidebar.searchQuery.trim().toLowerCase()
        if (!q) return true
        const title = String(t.title ?? "").toLowerCase()
        const preview = String(t.preview ?? t.lastMessagePreview ?? "").toLowerCase()
        if (title.indexOf(q) >= 0 || preview.indexOf(q) >= 0) return true
        const sq = String(sidebar.mind?.searchedQuery ?? "")
        const sIds = sidebar.mind?.searchedThreadIds
        if (sq === q && Array.isArray(sIds) && sIds.indexOf(String(t.id ?? "")) >= 0) {
            return true
        }
        return false
    }

    readonly property var pinnedProjects: (sidebar.mind?.projects ?? []).filter(p => p && p.pinned === true)
    readonly property var pinnedThreads: (sidebar.mind?.threads ?? []).filter(t => t && t.pinned === true && sidebar.threadMatchesFilter(t))
    readonly property var allProjects: sidebar.mind?.projects ?? []
    readonly property var unassignedThreads: (sidebar.mind?.threads ?? []).filter(t => t && !String(t.projectId ?? "").trim() && sidebar.threadMatchesFilter(t))

    function openNewProjectEditor(): void {
        editorMode = "new_project"
        editorTargetId = ""
        editorText = ""
        editorWorkspace = ""
        inlineNameInput.text = ""
        inlineWorkspaceInput.text = ""
        Qt.callLater(() => inlineNameInput.forceActiveFocus())
    }

    function openRenameThreadEditor(threadObj): void {
        if (!threadObj) return
        editorMode = "rename_thread"
        editorTargetId = String(threadObj.id ?? "")
        editorText = String(threadObj.title ?? "")
        inlineNameInput.text = editorText
        Qt.callLater(() => {
            inlineNameInput.forceActiveFocus()
            inlineNameInput.selectAll()
        })
    }

    function openRenameProjectEditor(projObj): void {
        if (!projObj) return
        editorMode = "rename_project"
        editorTargetId = String(projObj.id ?? "")
        editorText = String(projObj.name ?? "")
        editorWorkspace = String(projObj.workspacePath ?? "")
        inlineNameInput.text = editorText
        inlineWorkspaceInput.text = editorWorkspace
        Qt.callLater(() => {
            inlineNameInput.forceActiveFocus()
            inlineNameInput.selectAll()
        })
    }

    function openMoveThreadPicker(threadObj): void {
        if (!threadObj) return
        if (editorMode === "move_thread" && editorTargetId === String(threadObj.id ?? "")) {
            editorMode = ""
            editorTargetId = ""
            return
        }
        editorMode = "move_thread"
        editorTargetId = String(threadObj.id ?? "")
        editorText = String(threadObj.title ?? "")
    }

    function submitInlineEditor(): void {
        const nameVal = inlineNameInput.text.trim()
        const wsVal = inlineWorkspaceInput.text.trim()
        if (editorMode === "new_project" && nameVal.length > 0) {
            sidebar.mind.createProject(nameVal, wsVal, "")
        } else if (editorMode === "rename_thread" && editorTargetId.length > 0 && nameVal.length > 0) {
            sidebar.mind.renameThread(editorTargetId, nameVal)
        } else if (editorMode === "rename_project" && editorTargetId.length > 0 && nameVal.length > 0) {
            sidebar.mind.renameProject(editorTargetId, nameVal, wsVal)
        }
        editorMode = ""
        editorTargetId = ""
    }

    function cancelInlineEditor(): void {
        editorMode = ""
        editorTargetId = ""
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: sidebar.isVeryNarrow ? 6 : 8
        spacing: 6

        // Top action buttons: + New Chat & + New Project
        RowLayout {
            Layout.fillWidth: true
            spacing: 5

            Rectangle {
                id: newChatBtn
                objectName: "aquaSidebarNewChat"
                Layout.fillWidth: true
                Layout.minimumWidth: sidebar.isNarrow ? 62 : 80
                implicitHeight: 28
                radius: 8
                color: newChatArea.containsMouse
                    ? Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.24)
                    : Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.14)
                border.width: 1
                border.color: Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.45)

                RowLayout {
                    anchors.centerIn: parent
                    spacing: 4
                    MaterialSymbol {
                        text: "add_comment"
                        iconSize: 14
                        color: Appearance.colors.colPrimary
                    }
                    Text {
                        text: sidebar.isVeryNarrow ? "New" : "New Chat"
                        color: Appearance.colors.colPrimary
                        font.family: Appearance.font.family.main
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                    }
                }
                MouseArea {
                    id: newChatArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        const curProjId = String(sidebar.mind?.activeThread?.projectId ?? "")
                        sidebar.mind.createThread("", curProjId)
                    }
                }
            }

            Rectangle {
                id: newProjBtn
                objectName: "aquaSidebarNewProject"
                Layout.preferredWidth: sidebar.isVeryNarrow ? 30 : (sidebar.isNarrow ? 66 : 88)
                implicitWidth: Layout.preferredWidth
                implicitHeight: 28
                radius: 8
                color: newProjArea.containsMouse
                    ? Qt.rgba(Appearance.colors.colSurfaceContainerHighest.r, Appearance.colors.colSurfaceContainerHighest.g, Appearance.colors.colSurfaceContainerHighest.b, 0.92)
                    : Qt.rgba(Appearance.colors.colSurfaceContainerHigh.r, Appearance.colors.colSurfaceContainerHigh.g, Appearance.colors.colSurfaceContainerHigh.b, 0.68)
                border.width: 1
                border.color: Qt.rgba(Appearance.colors.colOutlineVariant.r, Appearance.colors.colOutlineVariant.g, Appearance.colors.colOutlineVariant.b, 0.42)

                RowLayout {
                    anchors.centerIn: parent
                    spacing: 4
                    MaterialSymbol {
                        text: "create_new_folder"
                        iconSize: 14
                        color: Appearance.colors.colOnLayer1
                    }
                    Text {
                        visible: !sidebar.isVeryNarrow
                        text: "Project"
                        color: Appearance.colors.colOnLayer1
                        font.family: Appearance.font.family.main
                        font.pixelSize: 11
                        font.weight: Font.Medium
                    }
                }
                MouseArea {
                    id: newProjArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: sidebar.openNewProjectEditor()
                }
            }
        }

        // Quick Search / Filter Chats Bar
        Rectangle {
            id: sidebarSearchBox
            objectName: "aquaSidebarSearchBox"
            Layout.fillWidth: true
            implicitHeight: 24
            radius: 6
            color: Qt.rgba(Appearance.colors.colSurfaceContainerHigh.r, Appearance.colors.colSurfaceContainerHigh.g, Appearance.colors.colSurfaceContainerHigh.b, 0.55)
            border.width: sidebarSearchInput.activeFocus ? 1 : 0
            border.color: Appearance.colors.colPrimary

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 6
                anchors.rightMargin: 6
                spacing: 4

                MaterialSymbol {
                    text: "search"
                    iconSize: 13
                    color: Appearance.colors.colSubtext
                }

                TextInput {
                    id: sidebarSearchInput
                    objectName: "aquaSidebarSearchInput"
                    Layout.fillWidth: true
                    verticalAlignment: TextInput.AlignVCenter
                    color: Appearance.colors.colOnLayer1
                    selectionColor: Appearance.colors.colPrimary
                    font.family: Appearance.font.family.main
                    font.pixelSize: 10
                    clip: true
                    onTextChanged: {
                        sidebar.searchQuery = text
                        if (!text.trim()) {
                            searchDebounce.stop()
                            if (sidebar.mind && typeof sidebar.mind.searchThreads === "function") {
                                sidebar.mind.searchThreads("")
                            }
                        } else {
                            searchDebounce.restart()
                        }
                    }
                    Keys.onEscapePressed: {
                        text = ""
                        focus = false
                    }

                    Text {
                        anchors.fill: parent
                        verticalAlignment: Text.AlignVCenter
                        visible: !sidebarSearchInput.text
                        text: "Filter chats..."
                        color: Appearance.colors.colSubtext
                        font: sidebarSearchInput.font
                    }
                }

                Rectangle {
                    id: sidebarSearchClear
                    objectName: "aquaSidebarSearchClear"
                    visible: sidebarSearchInput.text.length > 0
                    implicitWidth: 16
                    implicitHeight: 16
                    radius: 8
                    color: clearSearchHover.containsMouse
                        ? Qt.rgba(Appearance.colors.colOutlineVariant.r, Appearance.colors.colOutlineVariant.g, Appearance.colors.colOutlineVariant.b, 0.45)
                        : "transparent"

                    MaterialSymbol {
                        anchors.centerIn: parent
                        text: "close"
                        iconSize: 11
                        color: Appearance.colors.colSubtext
                    }

                    MouseArea {
                        id: clearSearchHover
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            sidebarSearchInput.text = ""
                            sidebarSearchInput.forceActiveFocus()
                        }
                    }
                }
            }
        }

        // Inline Editor Box (New Project / Rename Thread / Rename Project)
        Rectangle {
            id: inlineEditorBox
            objectName: "aquaSidebarInlineEditor"
            visible: sidebar.editorMode === "new_project" || sidebar.editorMode === "rename_thread" || sidebar.editorMode === "rename_project"
            Layout.fillWidth: true
            implicitHeight: editorCol.implicitHeight + 12
            radius: 8
            color: Qt.rgba(Appearance.colors.colSurfaceContainerHighest.r, Appearance.colors.colSurfaceContainerHighest.g, Appearance.colors.colSurfaceContainerHighest.b, 0.92)
            border.width: 1
            border.color: Appearance.colors.colPrimary

            ColumnLayout {
                id: editorCol
                anchors.fill: parent
                anchors.margins: 6
                spacing: 4

                Text {
                    text: sidebar.editorMode === "new_project" ? "New Project"
                        : sidebar.editorMode === "rename_project" ? "Edit Project" : "Rename Chat"
                    color: Appearance.colors.colPrimary
                    font.family: Appearance.font.family.main
                    font.pixelSize: 10
                    font.weight: Font.DemiBold
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 24
                    radius: 6
                    color: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.9)
                    border.width: 1
                    border.color: Qt.rgba(Appearance.colors.colOutlineVariant.r, Appearance.colors.colOutlineVariant.g, Appearance.colors.colOutlineVariant.b, 0.5)

                    TextInput {
                        id: inlineNameInput
                        objectName: "aquaSidebarInlineInput"
                        anchors.fill: parent
                        anchors.leftMargin: 6
                        anchors.rightMargin: 6
                        verticalAlignment: TextInput.AlignVCenter
                        color: Appearance.colors.colOnLayer1
                        selectionColor: Appearance.colors.colPrimary
                        font.family: Appearance.font.family.main
                        font.pixelSize: 11
                        clip: true
                        Keys.onReturnPressed: sidebar.submitInlineEditor()
                        Keys.onEnterPressed: sidebar.submitInlineEditor()
                        Keys.onEscapePressed: sidebar.cancelInlineEditor()

                        Text {
                            anchors.fill: parent
                            verticalAlignment: Text.AlignVCenter
                            visible: !inlineNameInput.text
                            text: sidebar.editorMode === "rename_thread" ? "Chat title..." : "Project name..."
                            color: Appearance.colors.colSubtext
                            font: inlineNameInput.font
                        }
                    }
                }

                Rectangle {
                    visible: sidebar.editorMode === "new_project" || sidebar.editorMode === "rename_project"
                    Layout.fillWidth: true
                    implicitHeight: 24
                    radius: 6
                    color: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.9)
                    border.width: 1
                    border.color: Qt.rgba(Appearance.colors.colOutlineVariant.r, Appearance.colors.colOutlineVariant.g, Appearance.colors.colOutlineVariant.b, 0.5)

                    TextInput {
                        id: inlineWorkspaceInput
                        objectName: "aquaSidebarWorkspaceInput"
                        anchors.fill: parent
                        anchors.leftMargin: 6
                        anchors.rightMargin: 6
                        verticalAlignment: TextInput.AlignVCenter
                        color: Appearance.colors.colOnLayer1
                        selectionColor: Appearance.colors.colPrimary
                        font.family: Appearance.font.family.main
                        font.pixelSize: 10
                        clip: true
                        Keys.onReturnPressed: sidebar.submitInlineEditor()
                        Keys.onEnterPressed: sidebar.submitInlineEditor()
                        Keys.onEscapePressed: sidebar.cancelInlineEditor()

                        Text {
                            anchors.fill: parent
                            verticalAlignment: Text.AlignVCenter
                            visible: !inlineWorkspaceInput.text
                            text: "Workspace path (optional, e.g. ~/Projects)"
                            color: Appearance.colors.colSubtext
                            font: inlineWorkspaceInput.font
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Item { Layout.fillWidth: true }
                    Rectangle {
                        objectName: "aquaSidebarInlineCancel"
                        implicitWidth: 48
                        implicitHeight: 20
                        radius: 5
                        color: Qt.rgba(Appearance.colors.colSurfaceContainerHigh.r, Appearance.colors.colSurfaceContainerHigh.g, Appearance.colors.colSurfaceContainerHigh.b, 0.8)
                        Text {
                            anchors.centerIn: parent
                            text: "Cancel"
                            color: Appearance.colors.colSubtext
                            font.family: Appearance.font.family.main
                            font.pixelSize: 10
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: sidebar.cancelInlineEditor()
                        }
                    }
                    Rectangle {
                        objectName: "aquaSidebarInlineSave"
                        implicitWidth: 48
                        implicitHeight: 20
                        radius: 5
                        color: Appearance.colors.colPrimary
                        Text {
                            anchors.centerIn: parent
                            text: "Save"
                            color: Appearance.colors.colOnPrimary
                            font.family: Appearance.font.family.main
                            font.pixelSize: 10
                            font.weight: Font.DemiBold
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: sidebar.submitInlineEditor()
                        }
                    }
                }
            }
        }

        // Move Thread / Reorder Picker Box
        Rectangle {
            id: moveThreadPicker
            objectName: "aquaSidebarMovePicker"
            visible: sidebar.editorMode === "move_thread" && sidebar.editorTargetId.length > 0
            Layout.fillWidth: true
            implicitHeight: moveCol.implicitHeight + 12
            radius: 8
            color: Qt.rgba(Appearance.colors.colSurfaceContainerHighest.r, Appearance.colors.colSurfaceContainerHighest.g, Appearance.colors.colSurfaceContainerHighest.b, 0.92)
            border.width: 1
            border.color: Appearance.colors.colPrimary

            ColumnLayout {
                id: moveCol
                anchors.fill: parent
                anchors.margins: 6
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: "Move / Reorder: " + sidebar.editorText
                        color: Appearance.colors.colPrimary
                        font.family: Appearance.font.family.main
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    Text {
                        text: "✕"
                        color: Appearance.colors.colSubtext
                        font.pixelSize: 10
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: sidebar.cancelInlineEditor()
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Rectangle {
                        objectName: "aquaMoveThreadUp"
                        Layout.fillWidth: true
                        implicitHeight: 20
                        radius: 5
                        color: Qt.rgba(Appearance.colors.colSurfaceContainerHigh.r, Appearance.colors.colSurfaceContainerHigh.g, Appearance.colors.colSurfaceContainerHigh.b, 0.8)
                        Text {
                            anchors.centerIn: parent
                            text: sidebar.isNarrow ? "↑ Top" : "↑ Move Top"
                            color: Appearance.colors.colOnLayer1
                            font.family: Appearance.font.family.main
                            font.pixelSize: 10
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                const tObj = (sidebar.mind?.threads ?? []).find(t => t && t.id === sidebar.editorTargetId)
                                sidebar.mind.moveThread(sidebar.editorTargetId, String(tObj?.projectId ?? ""), 0)
                                sidebar.cancelInlineEditor()
                            }
                        }
                    }
                    Rectangle {
                        objectName: "aquaMoveThreadDown"
                        Layout.fillWidth: true
                        implicitHeight: 20
                        radius: 5
                        color: Qt.rgba(Appearance.colors.colSurfaceContainerHigh.r, Appearance.colors.colSurfaceContainerHigh.g, Appearance.colors.colSurfaceContainerHigh.b, 0.8)
                        Text {
                            anchors.centerIn: parent
                            text: sidebar.isNarrow ? "↓ Down" : "↓ Move Down"
                            color: Appearance.colors.colOnLayer1
                            font.family: Appearance.font.family.main
                            font.pixelSize: 10
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                const tObj = (sidebar.mind?.threads ?? []).find(t => t && t.id === sidebar.editorTargetId)
                                const pid = String(tObj?.projectId ?? "")
                                const siblings = (sidebar.mind?.threads ?? []).filter(t => t && String(t.projectId ?? "") === pid)
                                const curIdx = siblings.findIndex(t => t && t.id === sidebar.editorTargetId)
                                const nextIdx = curIdx >= 0 ? Math.min(siblings.length - 1, curIdx + 1) : 1
                                sidebar.mind.moveThread(sidebar.editorTargetId, pid, nextIdx)
                                sidebar.cancelInlineEditor()
                            }
                        }
                    }
                    Rectangle {
                        objectName: "aquaMoveThreadUnassign"
                        Layout.fillWidth: true
                        implicitHeight: 20
                        radius: 5
                        color: Qt.rgba(Appearance.colors.colSurfaceContainerHigh.r, Appearance.colors.colSurfaceContainerHigh.g, Appearance.colors.colSurfaceContainerHigh.b, 0.8)
                        Text {
                            anchors.centerIn: parent
                            text: sidebar.isNarrow ? "Unset" : "No Project"
                            color: Appearance.colors.colOnLayer1
                            font.family: Appearance.font.family.main
                            font.pixelSize: 10
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                sidebar.mind.moveThread(sidebar.editorTargetId, "", 0)
                                sidebar.cancelInlineEditor()
                            }
                        }
                    }
                }

                Repeater {
                    model: sidebar.allProjects
                    delegate: Rectangle {
                        required property var modelData
                        objectName: "aquaMoveThreadToProject-" + String(modelData?.id ?? "")
                        Layout.fillWidth: true
                        implicitHeight: 22
                        radius: 5
                        color: moveProjArea.containsMouse
                            ? Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.22)
                            : Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.75)

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 6
                            anchors.rightMargin: 6
                            spacing: 4
                            MaterialSymbol {
                                text: "folder"
                                iconSize: 12
                                color: Appearance.colors.colPrimary
                            }
                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: "Move to " + String(modelData?.name ?? "")
                                color: Appearance.colors.colOnLayer1
                                font.family: Appearance.font.family.main
                                font.pixelSize: 10
                                elide: Text.ElideRight
                            }
                        }
                        MouseArea {
                            id: moveProjArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                sidebar.mind.moveThread(sidebar.editorTargetId, String(modelData?.id ?? ""), 0)
                                sidebar.cancelInlineEditor()
                            }
                        }
                    }
                }
            }
        }

        // Scrollable list of Pinned, Projects, and Recent Chats
        Flickable {
            id: listFlick
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: width
            contentHeight: listColumn.implicitHeight + 8
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            ColumnLayout {
                id: listColumn
                width: listFlick.width
                spacing: 8

                // 1. Pinned Section (Pinned Chats)
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    visible: sidebar.pinnedThreads.length > 0

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        MaterialSymbol {
                            text: "keep"
                            iconSize: 12
                            color: Appearance.colors.colPrimary
                        }
                        Text {
                            text: "PINNED"
                            color: Appearance.colors.colSubtext
                            font.family: Appearance.font.family.main
                            font.pixelSize: 9
                            font.weight: Font.Bold
                        }
                    }

                    Repeater {
                        model: sidebar.pinnedThreads
                        delegate: ChatRowDelegate {
                            required property var modelData
                            required property int index
                            threadData: modelData
                            rowIndex: index
                            sectionPrefix: "pinned"
                        }
                    }
                }

                // 2. Projects Section
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    visible: sidebar.allProjects.length > 0

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        MaterialSymbol {
                            text: "folder_special"
                            iconSize: 12
                            color: Appearance.colors.colPrimary
                        }
                        Text {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: "PROJECTS (" + sidebar.allProjects.length + ")"
                            color: Appearance.colors.colSubtext
                            font.family: Appearance.font.family.main
                            font.pixelSize: 9
                            font.weight: Font.Bold
                            elide: Text.ElideRight
                        }
                    }

                    Repeater {
                        model: sidebar.allProjects
                        delegate: ColumnLayout {
                            id: projectGroup
                            required property var modelData
                            required property int index
                            readonly property string projId: String(modelData?.id ?? "")
                            readonly property bool isCollapsed: modelData?.collapsed === true
                            readonly property var projectThreads: (sidebar.mind?.threads ?? []).filter(t => t && String(t.projectId ?? "") === projId && sidebar.threadMatchesFilter(t))
                            Layout.fillWidth: true
                            spacing: 2

                            Rectangle {
                                id: projectHeader
                                objectName: "aquaProjectHeader-" + projectGroup.projId
                                Layout.fillWidth: true
                                implicitHeight: 28
                                radius: 7
                                color: projectDropArea.containsDrag
                                    ? Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.28)
                                    : (projHover.hovered
                                        ? Qt.rgba(Appearance.colors.colSurfaceContainerHighest.r, Appearance.colors.colSurfaceContainerHighest.g, Appearance.colors.colSurfaceContainerHighest.b, 0.78)
                                        : Qt.rgba(Appearance.colors.colSurfaceContainerHigh.r, Appearance.colors.colSurfaceContainerHigh.g, Appearance.colors.colSurfaceContainerHigh.b, 0.48))
                                border.width: projectDropArea.containsDrag || modelData?.pinned ? 1 : 0
                                border.color: projectDropArea.containsDrag
                                    ? Appearance.colors.colPrimary
                                    : Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.35)

                                HoverHandler { id: projHover }

                                DropArea {
                                    id: projectDropArea
                                    anchors.fill: parent
                                    onDropped: drop => {
                                        if (sidebar.draggingThreadId.length > 0) {
                                            sidebar.mind.moveThread(sidebar.draggingThreadId, projectGroup.projId, 0)
                                            sidebar.draggingThreadId = ""
                                        } else if (sidebar.draggingProjectId.length > 0 && sidebar.draggingProjectId !== projectGroup.projId) {
                                            sidebar.mind.moveProject(sidebar.draggingProjectId, projectGroup.index)
                                            sidebar.draggingProjectId = ""
                                        }
                                    }
                                }

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 6
                                    anchors.rightMargin: 5
                                    spacing: 4

                                    MaterialSymbol {
                                        text: projectGroup.isCollapsed ? "folder" : "folder_open"
                                        iconSize: 14
                                        color: Appearance.colors.colPrimary
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        spacing: 0
                                        Text {
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            text: String(projectGroup.modelData?.name ?? "Project")
                                            color: Appearance.colors.colOnLayer1
                                            font.family: Appearance.font.family.main
                                            font.pixelSize: 11
                                            font.weight: Font.DemiBold
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            visible: String(projectGroup.modelData?.workspacePath ?? "").length > 0 && !sidebar.isVeryNarrow
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            text: sidebar.isWide
                                                ? String(projectGroup.modelData?.workspacePath ?? "")
                                                : (String(projectGroup.modelData?.workspacePath ?? "").split("/").filter(Boolean).pop() || String(projectGroup.modelData?.workspacePath ?? ""))
                                            color: Appearance.colors.colSubtext
                                            font.family: Appearance.font.family.main
                                            font.pixelSize: 9
                                            elide: Text.ElideMiddle
                                        }
                                    }

                                    // Project action icons: + chat in project, pin, move/reorder, rename, delete
                                    RowLayout {
                                        id: projectActionsRow
                                        spacing: sidebar.isNarrow ? 1 : 2
                                        Rectangle {
                                            objectName: "aquaProjectNewChat-" + projectGroup.projId
                                            implicitWidth: 18
                                            implicitHeight: 18
                                            radius: 4
                                            color: "transparent"
                                            MaterialSymbol {
                                                anchors.centerIn: parent
                                                text: "add"
                                                iconSize: 13
                                                color: Appearance.colors.colPrimary
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: sidebar.mind.createThread("", projectGroup.projId)
                                            }
                                        }
                                        Rectangle {
                                            objectName: "aquaProjectPin-" + projectGroup.projId
                                            visible: !sidebar.isNarrow || projHover.hovered || projectGroup.modelData?.pinned === true
                                            implicitWidth: 18
                                            implicitHeight: 18
                                            radius: 4
                                            color: "transparent"
                                            MaterialSymbol {
                                                anchors.centerIn: parent
                                                text: "keep"
                                                iconSize: 12
                                                color: projectGroup.modelData?.pinned ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: sidebar.mind.pinProject(projectGroup.projId)
                                            }
                                        }
                                        Rectangle {
                                            objectName: "aquaProjectMove-" + projectGroup.projId
                                            visible: !sidebar.isNarrow || projHover.hovered
                                            implicitWidth: 18
                                            implicitHeight: 18
                                            radius: 4
                                            color: "transparent"
                                            MaterialSymbol {
                                                anchors.centerIn: parent
                                                text: "swap_vert"
                                                iconSize: 12
                                                color: Appearance.colors.colSubtext
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: {
                                                    const nextIdx = projectGroup.index > 0
                                                        ? projectGroup.index - 1
                                                        : Math.min(Math.max(0, sidebar.allProjects.length - 1), projectGroup.index + 1)
                                                    sidebar.mind.moveProject(projectGroup.projId, nextIdx)
                                                }
                                            }
                                        }
                                        Rectangle {
                                            objectName: "aquaProjectRename-" + projectGroup.projId
                                            visible: !sidebar.isNarrow || projHover.hovered
                                            implicitWidth: 18
                                            implicitHeight: 18
                                            radius: 4
                                            color: "transparent"
                                            MaterialSymbol {
                                                anchors.centerIn: parent
                                                text: "edit"
                                                iconSize: 12
                                                color: Appearance.colors.colSubtext
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: sidebar.openRenameProjectEditor(projectGroup.modelData)
                                            }
                                        }
                                        Rectangle {
                                            objectName: "aquaProjectDelete-" + projectGroup.projId
                                            visible: !sidebar.isNarrow || projHover.hovered
                                            implicitWidth: 18
                                            implicitHeight: 18
                                            radius: 4
                                            color: "transparent"
                                            MaterialSymbol {
                                                anchors.centerIn: parent
                                                text: "delete"
                                                iconSize: 12
                                                color: Appearance.colors.colSubtext
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: sidebar.mind.deleteProject(projectGroup.projId, false)
                                            }
                                        }
                                    }
                                }

                                MouseArea {
                                    id: projectSelectDragArea
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: Math.max(24, parent.width - projectActionsRow.implicitWidth - 10)
                                    cursorShape: Qt.PointingHandCursor
                                    preventStealing: true
                                    drag.target: projectDragProxy
                                    onPressed: {
                                        sidebar.draggingProjectId = projectGroup.projId
                                    }
                                    onReleased: {
                                        if (projectDragProxy.Drag.active) {
                                            projectDragProxy.Drag.drop()
                                        }
                                        sidebar.draggingProjectId = ""
                                        projectDragProxy.x = 0
                                        projectDragProxy.y = 0
                                    }
                                    onClicked: sidebar.mind.toggleProjectCollapsed(projectGroup.projId)
                                }

                                Item {
                                    id: projectDragProxy
                                    width: projectHeader.width
                                    height: projectHeader.height
                                    Drag.active: projectSelectDragArea.drag.active
                                    Drag.hotSpot.x: width / 2
                                    Drag.hotSpot.y: height / 2
                                }
                            }

                            // Project's chat threads
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.leftMargin: sidebar.isNarrow ? 6 : 10
                                spacing: 2
                                visible: !projectGroup.isCollapsed || (sidebar.searchActive && projectGroup.projectThreads.length > 0)

                                Repeater {
                                    model: projectGroup.projectThreads
                                    delegate: ChatRowDelegate {
                                        required property var modelData
                                        required property int index
                                        threadData: modelData
                                        rowIndex: index
                                        sectionPrefix: "proj-" + projectGroup.projId
                                    }
                                }

                                Text {
                                    visible: projectGroup.projectThreads.length === 0 && !sidebar.searchActive
                                    Layout.leftMargin: 6
                                    text: "Drop chats here or click +"
                                    color: Appearance.colors.colSubtext
                                    font.family: Appearance.font.family.main
                                    font.pixelSize: 9
                                    font.italic: true
                                }
                            }
                        }
                    }
                }

                // 3. Recent / Unassigned Chats Section
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3

                    Rectangle {
                        id: unassignedDropBanner
                        objectName: "aquaRecentChatsDropZone"
                        Layout.fillWidth: true
                        implicitHeight: 20
                        radius: 5
                        color: unassignedDrop.containsDrag
                            ? Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.24)
                            : "transparent"

                        DropArea {
                            id: unassignedDrop
                            anchors.fill: parent
                            onDropped: drop => {
                                if (sidebar.draggingThreadId.length > 0) {
                                    sidebar.mind.moveThread(sidebar.draggingThreadId, "", 0)
                                    sidebar.draggingThreadId = ""
                                }
                            }
                        }

                        RowLayout {
                            anchors.fill: parent
                            spacing: 4
                            MaterialSymbol {
                                text: "chat_bubble"
                                iconSize: 12
                                color: Appearance.colors.colSubtext
                            }
                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: "CHATS (" + sidebar.unassignedThreads.length + ")"
                                color: Appearance.colors.colSubtext
                                font.family: Appearance.font.family.main
                                font.pixelSize: 9
                                font.weight: Font.Bold
                                elide: Text.ElideRight
                            }
                        }
                    }

                    Repeater {
                        model: sidebar.unassignedThreads
                        delegate: ChatRowDelegate {
                            required property var modelData
                            required property int index
                            threadData: modelData
                            rowIndex: index
                            sectionPrefix: "recent"
                        }
                    }
                }
            }
        }
    }

    // Reusable Chat Thread Row with Drag-and-Drop, Select, Pin, Move, Rename, and Delete
    component ChatRowDelegate: Rectangle {
        id: chatRow
        property var threadData: ({})
        property int rowIndex: 0
        property string sectionPrefix: "recent"
        readonly property string tid: String(threadData?.id ?? "")
        readonly property bool isActive: tid.length > 0 && tid === String(sidebar.mind?.activeThreadId ?? "")
        readonly property bool isPinned: threadData?.pinned === true
        readonly property var matchedInfo: {
            if (!sidebar.searchActive) return null
            const matches = sidebar.mind?.searchedThreadMatches
            if (Array.isArray(matches)) {
                for (let i = 0; i < matches.length; ++i) {
                    if (String(matches[i]?.id ?? "") === chatRow.tid) {
                        return matches[i]
                    }
                }
            }
            return chatRow.threadData
        }
        readonly property string matchedSnippet: sidebar.searchActive
            ? String(matchedInfo?.matchedSnippet ?? threadData?.matchedSnippet ?? "").trim()
            : ""
        readonly property int matchedTurnIndex: {
            if (!sidebar.searchActive) return -1
            const raw = matchedInfo?.matchedTurnIndex ?? threadData?.matchedTurnIndex
            const num = Number(raw)
            return (raw !== undefined && raw !== null && Number.isFinite(num) && num >= 0) ? Math.floor(num) : -1
        }
        objectName: sectionPrefix === "recent" ? ("aquaThreadRow-" + tid) : ("aquaThreadRow-" + sectionPrefix + "-" + tid)

        Layout.fillWidth: true
        implicitHeight: chatRow.matchedSnippet.length > 0 ? 40 : 28
        radius: 7
        color: rowDropArea.containsDrag
            ? Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.30)
            : isActive
                ? Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.20)
                : (rowHover.hovered
                    ? Qt.rgba(Appearance.colors.colSurfaceContainerHighest.r, Appearance.colors.colSurfaceContainerHighest.g, Appearance.colors.colSurfaceContainerHighest.b, 0.72)
                    : "transparent")
        border.width: isActive || rowDropArea.containsDrag ? 1 : 0
        border.color: isActive || rowDropArea.containsDrag
            ? Qt.rgba(Appearance.colors.colPrimary.r, Appearance.colors.colPrimary.g, Appearance.colors.colPrimary.b, 0.55)
            : "transparent"

        HoverHandler { id: rowHover }

        DropArea {
            id: rowDropArea
            anchors.fill: parent
            onDropped: drop => {
                if (sidebar.draggingThreadId.length > 0 && sidebar.draggingThreadId !== chatRow.tid) {
                    sidebar.mind.moveThread(
                        sidebar.draggingThreadId,
                        String(chatRow.threadData?.projectId ?? ""),
                        chatRow.rowIndex
                    )
                    sidebar.draggingThreadId = ""
                }
            }
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: sidebar.isNarrow ? 4 : 6
            anchors.rightMargin: 4
            spacing: sidebar.isNarrow ? 3 : 4

            MaterialSymbol {
                text: chatRow.isPinned ? "keep" : "chat"
                iconSize: 13
                color: chatRow.isActive ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 1

                Text {
                    objectName: "aquaThreadTitle-" + chatRow.tid
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: String(chatRow.threadData?.title ?? "New Chat")
                    color: chatRow.isActive ? Appearance.colors.colPrimary : Appearance.colors.colOnLayer1
                    font.family: Appearance.font.family.main
                    font.pixelSize: 11
                    font.weight: chatRow.isActive ? Font.DemiBold : Font.Normal
                    elide: Text.ElideRight
                }

                Text {
                    objectName: "aquaThreadSnippet-" + chatRow.tid
                    visible: chatRow.matchedSnippet.length > 0
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: chatRow.matchedSnippet
                    color: Appearance.colors.colSubtext
                    font.family: Appearance.font.family.main
                    font.pixelSize: 10
                    elide: Text.ElideRight
                }
            }

            // Action buttons (Pin, Move/Project, Duplicate, Rename, Delete)
            RowLayout {
                id: threadActionsRow
                spacing: 1
                opacity: rowHover.hovered || chatRow.isActive ? 1.0 : 0.65

                Rectangle {
                    objectName: "aquaThreadPin-" + chatRow.tid
                    visible: !sidebar.isNarrow || rowHover.hovered || chatRow.isPinned
                    implicitWidth: 17
                    implicitHeight: 17
                    radius: 4
                    color: "transparent"
                    MaterialSymbol {
                        anchors.centerIn: parent
                        text: "keep"
                        iconSize: 11
                        color: chatRow.isPinned ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: sidebar.mind.pinThread(chatRow.tid)
                    }
                }

                Rectangle {
                    objectName: "aquaThreadMove-" + chatRow.tid
                    visible: !sidebar.isNarrow || rowHover.hovered
                    implicitWidth: 17
                    implicitHeight: 17
                    radius: 4
                    color: "transparent"
                    MaterialSymbol {
                        anchors.centerIn: parent
                        text: "drive_file_move"
                        iconSize: 11
                        color: Appearance.colors.colSubtext
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: sidebar.openMoveThreadPicker(chatRow.threadData)
                    }
                }

                Rectangle {
                    objectName: "aquaThreadDuplicate-" + chatRow.tid
                    visible: !sidebar.isVeryNarrow && (!sidebar.isNarrow || rowHover.hovered)
                    implicitWidth: 17
                    implicitHeight: 17
                    radius: 4
                    color: "transparent"
                    MaterialSymbol {
                        anchors.centerIn: parent
                        text: "content_copy"
                        iconSize: 11
                        color: Appearance.colors.colSubtext
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: sidebar.mind.duplicateThread(chatRow.tid)
                    }
                }

                Rectangle {
                    objectName: "aquaThreadRename-" + chatRow.tid
                    visible: !sidebar.isNarrow || rowHover.hovered
                    implicitWidth: 17
                    implicitHeight: 17
                    radius: 4
                    color: "transparent"
                    MaterialSymbol {
                        anchors.centerIn: parent
                        text: "edit"
                        iconSize: 11
                        color: Appearance.colors.colSubtext
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: sidebar.openRenameThreadEditor(chatRow.threadData)
                    }
                }

                Rectangle {
                    objectName: "aquaThreadDelete-" + chatRow.tid
                    visible: !sidebar.isNarrow || rowHover.hovered
                    implicitWidth: 17
                    implicitHeight: 17
                    radius: 4
                    color: "transparent"
                    MaterialSymbol {
                        anchors.centerIn: parent
                        text: "delete"
                        iconSize: 11
                        color: Appearance.colors.colSubtext
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: sidebar.mind.deleteThread(chatRow.tid)
                    }
                }
            }
        }

        // Main click + drag handle area on the title/icon region
        MouseArea {
            id: selectDragArea
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: Math.max(24, parent.width - threadActionsRow.implicitWidth - 10)
            cursorShape: Qt.PointingHandCursor
            preventStealing: true
            drag.target: dragProxy
            onPressed: {
                sidebar.draggingThreadId = chatRow.tid
            }
            onReleased: {
                if (dragProxy.Drag.active) {
                    dragProxy.Drag.drop()
                }
                sidebar.draggingThreadId = ""
                dragProxy.x = 0
                dragProxy.y = 0
            }
            onClicked: {
                sidebar.mind.selectThread(chatRow.tid)
                sidebar.threadSelected(chatRow.tid)
                if (sidebar.searchActive && chatRow.matchedTurnIndex >= 0) {
                    sidebar.threadTurnSelected(chatRow.tid, chatRow.matchedTurnIndex)
                }
            }
        }

        Item {
            id: dragProxy
            width: chatRow.width
            height: chatRow.height
            Drag.active: selectDragArea.drag.active
            Drag.hotSpot.x: width / 2
            Drag.hotSpot.y: height / 2
        }
    }
}
