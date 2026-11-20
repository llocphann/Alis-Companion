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

// One question offers one choice row. Only explicit chat requests input focus.
Item {
    id: root
    objectName: embeddedMode ? "aquaCompanionEmbeddedCloud" : "aquaTalkCloud"
    property Item actor: null
    property real outputWidth: 420
    property real outputHeight: 680
    property bool allowed: false
    property bool sidebarOpen: false
    property bool embeddedMode: false
    readonly property alias inputField: message
    readonly property bool hasSidebarInputFocus: chatSidebar.visible && chatSidebar.hasActiveInputFocus
    property bool wantsKeyboardFocus: false
    property bool swarmOpen: false
    readonly property AquaSwarmPane activeSwarmPane: (root.visible && swarmPaneLoader.active) ? (swarmPaneLoader.item as AquaSwarmPane) : null
    readonly property bool hasSwarmInputFocus: Boolean(activeSwarmPane && activeSwarmPane.visible && activeSwarmPane.hasActiveInputFocus)
    readonly property bool hasActiveTextInputFocus: message.activeFocus || editorTextArea.activeFocus || clarificationCustomField.activeFocus || chatWeightField.activeFocus || root.hasSidebarInputFocus || root.hasSwarmInputFocus || root.anySelectableTextFocused || root.voiceStageActive
    readonly property bool keyboardFocusActive: root.visible && (root.wantsKeyboardFocus || root.hasActiveTextInputFocus)
    readonly property bool cloudHovered: root.visible && talkCloudHover.hovered
    readonly property AquaPocketPane activePocketPane: (root.visible && pocketPaneLoader.active) ? (pocketPaneLoader.item as AquaPocketPane) : null
    readonly property Item pocketPaneItem: (activePocketPane && activePocketPane.visible) ? activePocketPane : null
    readonly property bool pocketPaneHovered: Boolean(activePocketPane && activePocketPane.visible && activePocketPane.hovered)
    readonly property bool popupOpen: root.visible && !root.embeddedMode && (root.panelMode || root.editing || root.swarmOpen || root.pocketOpen || AquaMind.conversationOpen || AquaMind.contextOpen || root.voiceStageActive || AquaMind.text.length > 0)
    PointHandler {
        id: talkCloudFocusTap
        acceptedButtons: Qt.AllButtons
        onActiveChanged: {
            if (active && root.visible && !root.embeddedMode) {
                if (!root.editing && AquaMind.source === "mascot") {
                    AquaMind.dismissMascotRemark();
                    return;
                }
                root.wantsKeyboardFocus = true;
                Qt.callLater(() => {
                    if (root.visible && !root.embeddedMode && !root.hasActiveTextInputFocus)
                        root.focusPrimaryInput();
                });
            }
        }
    }
    HoverHandler {
        id: talkCloudHover
        blocking: false
    }
    onCloudHoveredChanged: if (!root.editing && AquaMind.source === "mascot")
        AquaMind.pauseMascotExpiry(cloudHovered)
    onHasSidebarInputFocusChanged: if (hasSidebarInputFocus)
        root.wantsKeyboardFocus = true
    onHasSwarmInputFocusChanged: if (hasSwarmInputFocus)
        root.wantsKeyboardFocus = true
    function focusPrimaryInput(): void {
        root.wantsKeyboardFocus = true;
        if (root.hasSwarmInputFocus && root.activeSwarmPane) {
            root.activeSwarmPane.focusComposer();
        } else if (root.editing && !root.voiceStageActive && message.visible && message.enabled) {
            message.forceActiveFocus();
        } else {
            root.forceActiveFocus();
        }
    }
    function requestKeyboardFocus(): void {
        root.wantsKeyboardFocus = true;
        root.focusPrimaryInput();
    }
    function releaseKeyboardFocus(): void {
        root.wantsKeyboardFocus = false;
        if (message.activeFocus)
            message.focus = false;
        if (editorTextArea.activeFocus)
            editorTextArea.focus = false;
        if (clarificationCustomField.activeFocus)
            clarificationCustomField.focus = false;
        if (chatWeightField.activeFocus)
            chatWeightField.focus = false;
        if (root.activeFocus)
            root.focus = false;
    }
    property string chatCheckInField: ""
    readonly property string activeChatCheckInField: {
        if (!root.editing || root.profileStage !== 0 || root.editorMode || root.voiceStageActive)
            return "";
        if (root.chatCheckInField === "mood" || root.chatCheckInField === "energy" || root.chatCheckInField === "weight")
            return root.chatCheckInField;
        return "";
    }
    readonly property var moodOptions: [
        { value: "terrible", label: Translation.tr("Awful") },
        { value: "bad", label: Translation.tr("Low") },
        { value: "okay", label: Translation.tr("Okay") },
        { value: "good", label: Translation.tr("Good") },
        { value: "great", label: Translation.tr("Great") }
    ]
    readonly property var energyOptions: [
        { value: "drained", label: Translation.tr("Drained") },
        { value: "low", label: Translation.tr("Low") },
        { value: "medium", label: Translation.tr("Mid") },
        { value: "high", label: Translation.tr("High") },
        { value: "peak", label: Translation.tr("Peak") }
    ]
    function formatCheckInLabel(field, value): string {
        const list = field === "energy" ? root.energyOptions : root.moodOptions;
        const match = list.find(item => item.value === String(value ?? "").toLowerCase());
        return match ? String(match.label) : String(value ?? "");
    }
    // 0 = closed, 1 = thinking effort, 2 = model picker.
    property int profileStage: 0
    property int lastProfileStage: 0
    property bool effortClosingHold: false
    // "primary", "action", or "thinking" inside model picker.
    property string modelPickerRole: "primary"
    Timer {
        id: effortCloseHoldTimer
        interval: 150
        repeat: false
        onTriggered: root.effortClosingHold = false
    }
    function triggerEffortSlideClose(): void {
        root.lastProfileStage = 1;
        if (AquaMind.liveModelAutoDetectAllowed) {
            root.effortClosingHold = true;
            effortCloseHoldTimer.restart();
        } else {
            root.effortClosingHold = false;
        }
        root.profileStage = 0;
        Qt.callLater(() => {
            if (root.editing)
                message.forceActiveFocus();
        });
    }
    onProfileStageChanged: {
        if (profileStage !== 0) {
            root.lastProfileStage = profileStage;
            root.effortClosingHold = false;
            effortCloseHoldTimer.stop();
            root.slashMenuDismissed = true;
        } else {
            root.modelPickerRole = "primary";
        }
    }
    property bool embeddedPocketOpen: false
    readonly property bool pocketOpen: (root.embeddedMode ? root.embeddedPocketOpen : AquaPocket.opened) && AquaPocket.permitted
    Connections {
        target: AquaPocket
        function onPermittedChanged(): void {
            if (!AquaPocket.permitted)
                root.embeddedPocketOpen = false;
        }
    }
    onPocketOpenChanged: {
        if (pocketOpen)
            root.swarmOpen = false;
        if (pocketOpen && !embeddedMode) {
            sidebarOpen = false;
            if (AquaMind.editorPopupOpen)
                AquaMind.closeEditorPopup();
        }
    }
    readonly property bool editing: (embeddedMode ? (parent?.objectName === "aquaCompanionChatView" ? Boolean(parent.hostSidebarOpen) : visible) : (visible && AquaMind.conversationOpen)) && !pocketOpen
    readonly property bool editorMode: root.editing && AquaMind.editorPopupOpen && AquaMind.editorFilePath.length > 0
    readonly property bool voiceStageActive: root.editing && (AquaMind.voiceModeOpen || AquaMind.voiceRecording || AquaMind.voiceTranscribing)
    readonly property real activeVoiceLevel: Math.max(0.0, Math.min(1.0, Number(AquaMind.voiceInputLevel) || 0.0))
    readonly property bool hasVoiceAudioInput: AquaMind.voiceRecording && !AquaMind.voiceTranscribing && root.activeVoiceLevel > 0.02
    readonly property color voiceAccentColor: AquaMind.voiceTranscribing ? (Appearance.colors.colTertiary ?? Appearance.colors.colPrimary) : Appearance.colors.colPrimary
    readonly property color voiceContainerColor: AquaMind.voiceTranscribing ? (Appearance.colors.colTertiaryContainer ?? Appearance.colors.colPrimaryContainer) : Appearance.colors.colPrimaryContainer
    readonly property color voiceOnAccentColor: AquaMind.voiceTranscribing ? (Appearance.colors.colOnTertiary ?? Appearance.colors.colOnPrimary) : Appearance.colors.colOnPrimary
    readonly property color voiceOnContainerColor: AquaMind.voiceTranscribing ? (Appearance.colors.colOnTertiaryContainer ?? Appearance.colors.colOnPrimaryContainer) : Appearance.colors.colOnPrimaryContainer
    readonly property color voiceLayer1Color: Appearance.colors.colLayer1.a < 0.85 ? (Appearance.colors.colLayer1Base ?? Appearance.colors.colLayer1) : Appearance.colors.colLayer1
    readonly property color voiceLayer2Color: Appearance.colors.colLayer2.a < 0.85 ? (Appearance.colors.colLayer2Base ?? Appearance.colors.colLayer2) : Appearance.colors.colLayer2
    property real voiceWavePhase: 0.0
    Timer {
        id: voiceWaveAnimTimer
        interval: 55
        repeat: true
        running: root.visible && root.voiceStageActive && (root.hasVoiceAudioInput || AquaMind.voiceTranscribing)
        onTriggered: root.voiceWavePhase = (root.voiceWavePhase + 0.24) % (Math.PI * 64)
    }
    function formatVoiceTimer(sec): string {
        const s = Math.max(0, Math.floor(Number(sec) || 0));
        const m = Math.floor(s / 60);
        const r = s % 60;
        return (m < 10 ? "0" + m : String(m)) + ":" + (r < 10 ? "0" + r : String(r));
    }
    function voiceBarHeight(index, total, phase, recording, transcribing, inputLevel = 0.0, bands = null): real {
        const count = Math.max(2, Number(total) || 32);
        const norm = index / Math.max(1, count - 1);
        const envelope = Math.sin(norm * Math.PI);
        if (transcribing) {
            const shimmer = 0.5 + 0.5 * Math.sin(phase * 3.0 - index * 0.35);
            return Math.round(4 + (3 + 11 * shimmer) * (0.35 + 0.65 * envelope));
        }
        const level = Math.max(0.0, Math.min(1.0, Number(inputLevel) || 0.0));
        if (!recording || level <= 0.02) {
            return 4;
        }
        if (Array.isArray(bands) && bands.length >= 8) {
            const distFromCenter = Math.abs(norm - 0.5) * 2.0;
            const bandPos = Math.max(0.0, Math.min(7.0, distFromCenter * 7.0));
            const lo = Math.floor(bandPos);
            const hi = Math.min(7, lo + 1);
            const frac = bandPos - lo;
            const bVal = (Number(bands[lo]) || 0.0) * (1.0 - frac) + (Number(bands[hi]) || 0.0) * frac;
            const ripple = 0.88 + 0.12 * Math.sin(phase * 1.8 + index * 0.65);
            const drive = Math.max(0.10, Math.min(1.0, (bVal * 0.78 + level * 0.32) * ripple));
            return Math.round(4 + 20 * drive * (0.28 + 0.72 * envelope));
        }
        const w1 = Math.sin(phase * 2.5 + index * 0.48);
        const w2 = Math.cos(phase * 1.7 - index * 0.73);
        const w3 = Math.sin(phase * 3.8 + index * 1.19);
        const energy = Math.max(0.12, Math.min(1.0, 0.54 + 0.26 * w1 + 0.16 * w2 + 0.12 * w3));
        return Math.round(4 + 19 * level * energy * (0.30 + 0.70 * envelope));
    }
    onVoiceStageActiveChanged: {
        if (voiceStageActive) {
            root.profileStage = 0;
            root.slashMenuDismissed = true;
            Qt.callLater(() => {
                if (root.voiceStageActive)
                    root.forceActiveFocus();
            });
        } else if (root.editing) {
            Qt.callLater(() => {
                if (root.editing && !root.voiceStageActive)
                    message.forceActiveFocus();
            });
        }
    }
    property real editorExpandProgress: root.editorMode ? 1.0 : 0.0
    Behavior on editorExpandProgress {
        enabled: root.visible
        NumberAnimation {
            duration: 240
            easing.type: Easing.OutCubic
        }
    }
    property real swarmExpandProgress: root.swarmOpen ? 1.0 : 0.0
    Behavior on swarmExpandProgress {
        enabled: root.visible
        NumberAnimation {
            duration: 240
            easing.type: Easing.OutCubic
        }
    }
    readonly property real editorScaleFactor: 1.0 + (0.5 * root.editorExpandProgress)
    readonly property bool panelMode: embeddedMode || editing || swarmOpen || AquaMind.contextOpen || pocketOpen
    readonly property real bodyTop: panelMode ? (editorMode ? 84 : 46) : 12
    readonly property bool controlsVisible: visible && (pocketOpen || swarmOpen || editing || AquaMind.contextOpen || AquaMind.checkInStage.length > 0)
    property real customSidebarWidth: AquaMind.customSidebarWidth > 0 ? AquaMind.customSidebarWidth : 144
    onCustomSidebarWidthChanged: if (AquaMind.customSidebarWidth !== customSidebarWidth)
        AquaMind.customSidebarWidth = customSidebarWidth
    onSidebarOpenChanged: {
        if (sidebarOpen) {
            customSidebarWidth = minSidebarWidth;
            AquaMind.customSidebarWidth = minSidebarWidth;
        }
    }
    readonly property real minSidebarWidth: 144
    readonly property real maxSidebarWidth: embeddedMode ? Math.max(180, Math.min(360, Math.round((width > 0 ? width : outputWidth) * 0.56))) : Math.max(196, Math.min(340, Math.round((outputWidth - 32) * 0.54)))
    readonly property real sidebarWidth: (sidebarOpen && editing) ? Math.max(minSidebarWidth, Math.min(maxSidebarWidth, customSidebarWidth)) : 0
    readonly property real sidebarOffset: sidebarWidth > 0 ? (sidebarWidth + 10) : 0
    function resizeSidebarTo(targetWidth): real {
        const clamped = Math.max(minSidebarWidth, Math.min(maxSidebarWidth, Math.round(Number(targetWidth) || minSidebarWidth)));
        customSidebarWidth = clamped;
        AquaMind.customSidebarWidth = clamped;
        return sidebarWidth;
    }
    property real customSwarmSidebarWidth: 320
    readonly property real minSwarmSidebarWidth: 264
    readonly property real maxSwarmSidebarWidth: embeddedMode
        ? Math.max(280, Math.min(440, Math.round((width > 0 ? width : outputWidth) * 0.52)))
        : Math.max(300, Math.min(460, Math.round((outputWidth - 32) * 0.52)))
    readonly property bool swarmOverlayInNarrowEmbedded: root.embeddedMode && root.width < 540
    readonly property real swarmSidebarTargetWidth: Math.max(minSwarmSidebarWidth, Math.min(maxSwarmSidebarWidth, customSwarmSidebarWidth))
    readonly property real swarmSidebarWidth: (root.swarmOpen || root.swarmExpandProgress > 0.001) && root.editing
        ? (swarmOverlayInNarrowEmbedded ? Math.max(220, root.width - 24) : Math.round(swarmSidebarTargetWidth * Math.max(0.01, root.swarmExpandProgress)))
        : 0
    readonly property real swarmRightOffset: (!swarmOverlayInNarrowEmbedded && root.editing && (root.swarmOpen || root.swarmExpandProgress > 0.001))
        ? (Math.round(swarmSidebarTargetWidth * root.swarmExpandProgress) + 10)
        : 0
    function resizeSwarmSidebarTo(targetWidth): real {
        const clamped = Math.max(minSwarmSidebarWidth, Math.min(maxSwarmSidebarWidth, Math.round(Number(targetWidth) || 320)));
        customSwarmSidebarWidth = clamped;
        return customSwarmSidebarWidth;
    }
    readonly property real edgeOpacity: AbyssStyle.contentOpacity
    readonly property real edgeHighlight: AbyssStyle.highlightStrength
    readonly property real edgeLift: AbyssStyle.blurRadius > 0 ? 0.024 : 0
    readonly property real edgeRadius: Math.max(12, Math.min(26, AbyssStyle.perimeterRadius))
    readonly property color cloudColor: Qt.rgba(Math.min(1, AbyssStyle.surfaceDeep.r + edgeLift), Math.min(1, AbyssStyle.surfaceDeep.g + edgeLift), Math.min(1, AbyssStyle.surfaceDeep.b + edgeLift), edgeOpacity)
    readonly property color cloudBorderColor: edgeHighlight > 0.01 ? Qt.alpha(AbyssStyle.specular, Math.max(0.08, 0.55 * edgeHighlight)) : Qt.alpha(AbyssStyle.textColor, 0.06)
    function edgeSurfaceColor(depthWeight): color {
        const k = Math.max(0, Math.min(1, depthWeight * 0.38 * edgeHighlight));
        return Qt.rgba(cloudColor.r + (AbyssStyle.surfaceRaised.r - cloudColor.r) * k, cloudColor.g + (AbyssStyle.surfaceRaised.g - cloudColor.g) * k, cloudColor.b + (AbyssStyle.surfaceRaised.b - cloudColor.b) * k, edgeOpacity);
    }
    property var userPromptHistory: []
    property int promptHistoryIndex: -1
    property string promptHistoryDraft: ""
    property int pendingJumpTurnIndex: -1
    property string pendingJumpThreadId: ""
    property bool stickToBottom: true

    function scrollByPage(direction: int): bool {
        const dir = Number(direction) || 0;
        if (dir === 0 || transcript.contentHeight <= transcript.height)
            return false;
        const step = Math.max(60, Math.round(transcript.height * 0.55));
        const minContentY = transcript.originY;
        const maxContentY = transcript.originY + Math.max(0, transcript.contentHeight - transcript.height);
        transcript.contentY = Math.max(minContentY, Math.min(maxContentY, transcript.contentY + (dir > 0 ? step : -step)));
        root.stickToBottom = transcript.atYEnd;
        return true;
    }

    // Slash Command Menu (/) & @Mention Context Autocomplete state & catalog
    property bool slashMenuDismissed: false
    property int slashSelectedIndex: 0
    readonly property var mentionCatalog: [
        {
            name: "selection",
            cmd: "@selection",
            usage: "@selection",
            category: "Context",
            icon: "select_all",
            desc: "Attach Wayland primary selection",
            needsArgs: true,
            isMention: true
        },
        {
            name: "clipboard",
            cmd: "@clipboard",
            usage: "@clipboard",
            category: "Context",
            icon: "content_paste",
            desc: "Attach clipboard text",
            needsArgs: true,
            isMention: true
        },
        {
            name: "screen",
            cmd: "@screen",
            usage: "@screen",
            category: "Context",
            icon: "screenshot_monitor",
            desc: "Attach active window context",
            needsArgs: true,
            isMention: true
        },
        {
            name: "pocket",
            cmd: "@pocket",
            usage: "@pocket",
            category: "Context",
            icon: "inventory_2",
            desc: "Attach Pocket items",
            needsArgs: true,
            isMention: true
        },
        {
            name: "git-diff",
            cmd: "@git-diff",
            usage: "@git-diff",
            category: "Code",
            icon: "difference",
            desc: "Attach git status & diff",
            needsArgs: true,
            isMention: true
        }
    ]
    readonly property var baseSlashCommands: [
        {
            name: "swarm",
            cmd: "/swarm",
            usage: "/swarm <goal>",
            category: "Swarm",
            icon: "hub",
            desc: "Delegate goal across Swarm workers",
            needsArgs: true
        },
        {
            name: "plan",
            cmd: "/plan",
            usage: "/plan <goal>",
            category: "Agent",
            icon: "architecture",
            desc: "Draft a structured task plan",
            needsArgs: true
        },
        {
            name: "clarify",
            cmd: "/clarify",
            usage: "/clarify <topic>",
            category: "Agent",
            icon: "psychology_alt",
            desc: "Ask 1–3 clarifying choices",
            needsArgs: false
        },
        {
            name: "learn",
            cmd: "/learn",
            usage: "/learn <path-or-workflow>",
            category: "Hermes",
            icon: "school",
            desc: "Synthesize a reusable skill",
            needsArgs: true
        },
        {
            name: "skills",
            cmd: "/skills",
            usage: "/skills",
            category: "Hermes",
            icon: "extension",
            desc: "Manage agent SKILL.md packages",
            needsArgs: false
        },
        {
            name: "rag",
            cmd: "/rag",
            usage: "/rag <query|list|add>",
            category: "Knowledge",
            icon: "library_books",
            desc: "Search or manage RAG sources",
            needsArgs: false
        },
        {
            name: "mcp",
            cmd: "/mcp",
            usage: "/mcp <tools|resources|call>",
            category: "Tools",
            icon: "hub",
            desc: "Inspect or invoke MCP tools",
            needsArgs: false
        },
        {
            name: "git",
            cmd: "/git",
            usage: "/git status",
            category: "Code",
            icon: "commit",
            desc: "Inspect git status and diff",
            needsArgs: false
        },
        {
            name: "system",
            cmd: "/system",
            usage: "/system",
            category: "System",
            icon: "monitor_heart",
            desc: "Check CPU, RAM, disk, battery",
            needsArgs: false
        },
        {
            name: "memory",
            cmd: "/memory",
            usage: "/memory",
            category: "Memory",
            icon: "psychology",
            desc: "View or save memories",
            needsArgs: false
        },
        {
            name: "permissions",
            cmd: "/permissions",
            usage: "/permissions",
            category: "Security",
            icon: "verified_user",
            desc: "Inspect agent permissions",
            needsArgs: false
        },
        {
            name: "clear",
            cmd: "/clear",
            usage: "/clear",
            category: "Chat",
            icon: "ink_eraser",
            desc: "Clear current chat thread",
            needsArgs: false
        }
    ]
    readonly property var slashCommandCatalog: {
        const list = root.baseSlashCommands.slice();
        const existing = {};
        for (let i = 0; i < list.length; ++i)
            existing[list[i].name] = true;
        const skills = Array.isArray(AquaMind.availableSkills) ? AquaMind.availableSkills : [];
        for (let j = 0; j < skills.length; ++j) {
            const sk = skills[j];
            if (!sk || sk.enabled === false)
                continue;
            const sname = String(sk.name ?? "").trim().toLowerCase();
            if (!sname || existing[sname])
                continue;
            existing[sname] = true;
            list.push({
                name: sname,
                cmd: "/" + sname,
                usage: "/" + sname + " <task>",
                category: sk.builtIn ? "Skill" : "Learned",
                icon: sk.builtIn ? "auto_awesome" : "psychology_alt",
                desc: String(sk.description ?? "Reusable agent skill"),
                needsArgs: false
            });
        }
        return list;
    }
    readonly property string slashRawInput: String(message.text ?? "")
    readonly property bool isSlashPrefix: root.editing && /^\/[^\s]*$/.test(root.slashRawInput)
    readonly property var mentionMatch: root.editing && !root.isSlashPrefix ? /(?:^|\s)@([a-zA-Z0-9_-]*)$/.exec(root.slashRawInput) : null
    readonly property bool isMentionPrefix: root.mentionMatch !== null
    readonly property string mentionQuery: root.isMentionPrefix ? String(root.mentionMatch[1] ?? "").toLowerCase() : ""
    readonly property string slashQuery: root.isSlashPrefix ? root.slashRawInput.slice(1).toLowerCase() : (root.isMentionPrefix ? root.mentionQuery : "")
    readonly property var filteredSlashCommands: {
        if (root.isMentionPrefix) {
            const mq = root.mentionQuery;
            if (!mq)
                return root.mentionCatalog;
            return root.mentionCatalog.filter(item => item.name.indexOf(mq) >= 0 || item.desc.toLowerCase().indexOf(mq) >= 0);
        }
        if (!root.isSlashPrefix)
            return [];
        const q = root.slashQuery;
        if (!q)
            return root.slashCommandCatalog;
        const matched = root.slashCommandCatalog.filter(item => item.name.indexOf(q) >= 0 || item.category.toLowerCase().indexOf(q) >= 0 || item.desc.toLowerCase().indexOf(q) >= 0);
        matched.sort((a, b) => {
            const aStarts = a.name.indexOf(q) === 0 ? 0 : (a.name.indexOf(q) > 0 ? 1 : 2);
            const bStarts = b.name.indexOf(q) === 0 ? 0 : (b.name.indexOf(q) > 0 ? 1 : 2);
            return aStarts - bStarts;
        });
        return matched;
    }
    readonly property bool slashMenuOpen: (root.isSlashPrefix || root.isMentionPrefix) && !root.slashMenuDismissed && root.filteredSlashCommands.length > 0
    function selectSlashCommand(item, executeIfReady = true): bool {
        if (!item)
            return false;
        root.profileStage = 0;
        if (item.isMention) {
            message.text = String(message.text ?? "").replace(/@[a-zA-Z0-9_-]*$/, item.cmd + " ");
            root.slashMenuDismissed = true;
            if (item.name === "selection") {
                AquaMind.attachPrimarySelection();
            }
            Qt.callLater(() => {
                if (root.editing) {
                    message.forceActiveFocus();
                    message.cursorPosition = message.text.length;
                }
            });
            return true;
        }
        if (item.needsArgs || !executeIfReady) {
            message.text = item.cmd + " ";
            root.slashMenuDismissed = true;
            Qt.callLater(() => {
                if (root.editing) {
                    message.forceActiveFocus();
                    message.cursorPosition = message.text.length;
                }
            });
            return true;
        }
        message.text = item.cmd;
        root.slashMenuDismissed = true;
        root.submit();
        return true;
    }

    // Antigravity-Style Chat Turn Dot Navigators state & helpers
    property int hoveredTurnIndex: -1
    property int forcedHoveredTurnIndex: -1
    property int activeTurnIndex: -1
    readonly property var chatTurns: {
        const hist = Array.isArray(AquaMind.history) ? AquaMind.history : [];
        const turns = [];
        let currentTurn = null;
        for (let i = 0; i < hist.length; ++i) {
            const msg = hist[i];
            if (!msg)
                continue;
            const role = String(msg.role ?? "assistant");
            const rawContent = role === "user" ? String(msg.content ?? "").replace(/\s+/g, " ").trim() : root.stripMarkdownToPlain(root.compactBubbleSource(String(msg.content ?? "")));
            const created = Number(msg.created_ms ?? msg.createdMs ?? 0);
            if (role === "user") {
                if (currentTurn)
                    turns.push(currentTurn);
                currentTurn = {
                    turnNumber: turns.length + 1,
                    historyIndex: i,
                    promptText: rawContent || "(empty prompt)",
                    replyText: "",
                    createdMs: created
                };
            } else {
                if (!currentTurn) {
                    currentTurn = {
                        turnNumber: turns.length + 1,
                        historyIndex: i,
                        promptText: "Companion",
                        replyText: rawContent,
                        createdMs: created
                    };
                } else {
                    currentTurn.replyText = currentTurn.replyText ? (currentTurn.replyText + " " + rawContent).trim() : rawContent;
                    if (!currentTurn.createdMs && created > 0)
                        currentTurn.createdMs = created;
                }
            }
        }
        if (currentTurn)
            turns.push(currentTurn);
        return turns;
    }
    readonly property int effectiveHoveredTurnIndex: root.forcedHoveredTurnIndex >= 0 ? root.forcedHoveredTurnIndex : root.hoveredTurnIndex
    onEffectiveHoveredTurnIndexChanged: {
        if (root.effectiveHoveredTurnIndex >= 0)
            root.scrollDotNavigatorToTurn(root.effectiveHoveredTurnIndex);
    }
    readonly property var effectiveHoveredTurn: (root.effectiveHoveredTurnIndex >= 0 && root.effectiveHoveredTurnIndex < root.chatTurns.length) ? root.chatTurns[root.effectiveHoveredTurnIndex] : null
    function scrollDotNavigatorToTurn(turnIndex): void {
        const idx = Number(turnIndex);
        if (!Number.isInteger(idx) || idx < 0 || idx >= root.chatTurns.length)
            return;
        if (dotNavigatorFlickable.contentHeight <= dotNavigatorFlickable.height) {
            dotNavigatorFlickable.contentY = 0;
            return;
        }
        const step = dotNavigatorRail.dotItemHeight + dotNavigatorRail.dotSpacing;
        const itemTop = 4 + idx * step;
        const itemBottom = itemTop + dotNavigatorRail.dotItemHeight;
        const maxContentY = Math.max(0, dotNavigatorFlickable.contentHeight - dotNavigatorFlickable.height);
        if (itemTop < dotNavigatorFlickable.contentY + 6) {
            dotNavigatorFlickable.contentY = Math.max(0, itemTop - 6);
        } else if (itemBottom > dotNavigatorFlickable.contentY + dotNavigatorFlickable.height - 6) {
            dotNavigatorFlickable.contentY = Math.min(maxContentY, itemBottom - dotNavigatorFlickable.height + 6);
        }
    }
    function jumpToChatTurn(turnIndex): bool {
        const idx = Number(turnIndex);
        if (!Number.isInteger(idx) || idx < 0)
            return false;
        if (idx < root.chatTurns.length) {
            const turn = root.chatTurns[idx];
            if (!turn)
                return false;
            root.activeTurnIndex = idx;
            root.scrollDotNavigatorToTurn(idx);
            const histIdx = Number(turn.historyIndex ?? 0);
            if (histIdx >= 0 && histIdx < transcript.count) {
                transcript.positionViewAtIndex(histIdx, ListView.Beginning);
                return true;
            }
            return false;
        }
        if (idx < transcript.count) {
            let matchedTurnIdx = -1;
            for (let i = 0; i < root.chatTurns.length; ++i) {
                if (Number(root.chatTurns[i]?.historyIndex ?? 0) <= idx)
                    matchedTurnIdx = i;
            }
            if (matchedTurnIdx >= 0) {
                root.activeTurnIndex = matchedTurnIdx;
                root.scrollDotNavigatorToTurn(matchedTurnIdx);
            }
            transcript.positionViewAtIndex(idx, ListView.Beginning);
            return true;
        }
        return false;
    }
    property int _scrollSettlePass: 0
    property bool _snappingToBottom: false
    property real _lastSnapContentY: 0
    function _snapTranscriptToBottom(): void {
        if (transcript.count <= 0)
            return;
        root._snappingToBottom = true;
        try {
            transcript.positionViewAtIndex(transcript.count - 1, ListView.End);
            transcript.positionViewAtEnd();
            if (transcript.contentHeight > transcript.height) {
                const targetY = transcript.originY + transcript.contentHeight - transcript.height;
                if (Math.abs(transcript.contentY - targetY) > 1)
                    transcript.contentY = targetY;
            }
            root._lastSnapContentY = transcript.contentY;
            if (root.chatTurns.length > 0) {
                root.scrollDotNavigatorToTurn(root.chatTurns.length - 1);
            }
        } finally {
            root._snappingToBottom = false;
        }
    }
    Timer {
        id: scrollToBottomSettleTimer
        interval: 25
        repeat: false
        onTriggered: {
            if (!root.editing || root.editorMode || transcript.count <= 0)
                return;
            if (root.pendingJumpTurnIndex >= 0 || !root.stickToBottom)
                return;
            transcript.readyForOlder = false;
            root._snapTranscriptToBottom();
            if (root._scrollSettlePass < 1) {
                root._scrollSettlePass += 1;
                scrollToBottomSettleTimer.interval = 45;
                scrollToBottomSettleTimer.restart();
            } else {
                transcript.readyForOlder = true;
            }
        }
    }
    function scrollToLatestMessage(): void {
        if (transcript.count <= 0 || root.editorMode)
            return;
        if (root.pendingJumpTurnIndex >= 0 && (!root.pendingJumpThreadId || root.pendingJumpThreadId === AquaMind.activeThreadId)) {
            const targetIdx = root.pendingJumpTurnIndex;
            root.pendingJumpTurnIndex = -1;
            root.pendingJumpThreadId = "";
            root.stickToBottom = false;
            scrollToBottomSettleTimer.stop();
            if (!root.jumpToChatTurn(targetIdx) && targetIdx < transcript.count) {
                transcript.positionViewAtIndex(targetIdx, ListView.Beginning);
            }
            transcript.readyForOlder = true;
            return;
        }
        root.stickToBottom = true;
        transcript.readyForOlder = false;
        root.activeTurnIndex = -1;
        root._snapTranscriptToBottom();
        Qt.callLater(() => {
            if (transcript.count > 0 && root.pendingJumpTurnIndex < 0 && root.stickToBottom) {
                root._snapTranscriptToBottom();
            }
            if (!scrollToBottomSettleTimer.running)
                transcript.readyForOlder = true;
        });
        root._scrollSettlePass = 0;
        scrollToBottomSettleTimer.interval = 25;
        scrollToBottomSettleTimer.restart();
    }
    function formatTurnTimestamp(ms): string {
        const ts = Number(ms || 0);
        const d = ts > 0 ? new Date(ts) : new Date();
        let h = d.getHours();
        const m = d.getMinutes();
        const ampm = h >= 12 ? "PM" : "AM";
        h = h % 12;
        if (h === 0)
            h = 12;
        return String(h) + ":" + (m < 10 ? "0" + String(m) : String(m)) + " " + ampm;
    }
    function formatBytes(byteCount): string {
        const b = Math.max(0, Number(byteCount) || 0);
        if (b < 1024)
            return String(b) + " B";
        return (b / 1024).toFixed(1) + " KB";
    }
    function stripMarkdownToPlain(raw): string {
        let s = String(raw ?? "");
        if (!s)
            return "";
        s = s.replace(/```[\s\S]*?```/g, m => m.replace(/```[a-zA-Z0-9_-]*\n?/g, "").replace(/```/g, ""));
        s = s.replace(/\*\*\*([^*]+)\*\*\*/g, "$1");
        s = s.replace(/\*\*([^*]+)\*\*/g, "$1");
        s = s.replace(/\*([^*\n]+)\*/g, "$1");
        s = s.replace(/`([^`\n]+)`/g, "$1");
        s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1");
        s = s.replace(/(^|\n)\s*#{1,4}\s+/g, "$1");
        return s.replace(/\s+/g, " ").trim();
    }
    function compactBubbleSource(raw): string {
        let text = String(raw ?? "");
        if (!text)
            return "";
        if (/^\*\*🖥\s*[^\n*]*$/.test(text.trim())) {
            return "*(Previous reply was interrupted.)*";
        }
        // Normalize legacy verbose screen observation header & noisy OCR lines for compact chatbox
        text = text.replace(/^Current window observation(\s*\([^)]*\))?\s*\[source:\s*`([^`]+)`,\s*window:\s*`([^`]+)`(?:,\s*at:\s*`[^`]+`)?\]:/m, (_, changed, src, win) => `**🖥 ${win}** · \`${src}\`${changed ? " *" + changed.trim() + "*" : ""}`);
        text = text.replace(/^Quan sát màn hình hiện tại(\s*\([^)]*\))?\s*\[nguồn:\s*`([^`]+)`,\s*cửa sổ:\s*`([^`]+)`(?:,\s*lúc:\s*`[^`]+`)?\]:/m, (_, changed, src, win) => `**🖥 ${win}** · \`${src}\`${changed ? " *" + changed.trim() + "*" : ""}`);
        text = text.replace(/\*\*MCP Eyes & Hands are active:\*\*[\s\S]*$/m, "💡 **MCP Eyes & Hands:** `/web` search · `/fetch` read URL · `/browser` automate web · open apps / run commands.");
        text = text.replace(/\*\*Mắt \(Eyes\) & Tay \(Hands\) MCP đang hoạt động:\*\*[\s\S]*$/m, "💡 **Mắt & Tay MCP:** `/web` tìm kiếm · `/fetch` đọc trang · `/browser` tự động hoá web · mở ứng dụng / chạy lệnh.");
        if (text.startsWith("**🖥 ") && !/`[^`]*(?:\+ai|vision)`/.test(text.split("\n")[0] ?? "")) {
            const rawLines = text.split("\n");
            const outLines = [];
            let bulletCount = 0;
            for (let i = 0; i < rawLines.length; ++i) {
                const ln = rawLines[i];
                if (/^\s*-\s+/.test(ln)) {
                    let body = ln.replace(/^\s*-\s+/, "").trim();
                    const tokens = body.split(/\s+/).filter(Boolean);
                    if (tokens.length >= 4) {
                        let noisy = 0;
                        for (let t = 0; t < tokens.length; ++t) {
                            if (tokens[t].length <= 2 || !/[a-zA-ZÀ-ỹ]/.test(tokens[t]))
                                noisy++;
                        }
                        if ((noisy / tokens.length) >= 0.56)
                            continue;
                    }
                    while (tokens.length >= 2 && (tokens[0].length === 1 || !/[a-zA-Z0-9À-ỹ]/.test(tokens[0]))) {
                        tokens.shift();
                    }
                    body = tokens.join(" ").replace(/^[-•|>_~\s]+/, "").trim();
                    if (body.length < 3)
                        continue;
                    if (body.length > 140)
                        body = body.slice(0, 137).trim() + "…";
                    bulletCount++;
                    if (bulletCount <= 6)
                        outLines.push("- " + body);
                } else {
                    outLines.push(ln);
                }
            }
            text = outLines.join("\n");
        }
        return text.replace(/\n{3,}/g, "\n\n").trim();
    }
    function containsMarkdownSyntax(raw): bool {
        const s = String(raw ?? "");
        if (!s)
            return false;
        return s.indexOf("**") !== -1 || s.indexOf("`") !== -1 || /(^|\n)\s*[-*•]\s+\S/.test(s) || /(^|\n)\s*\d+\.\s+\S/.test(s) || /(^|\n)\s*#{1,4}\s+\S/.test(s) || /\*[^*\n]+\*/.test(s) || /\[[^\]]+\]\([^)]+\)/.test(s);
    }
    function formatBubbleMarkdown(raw): string {
        let s = String(raw ?? "");
        if (!s)
            return "";
        const codeBlocks = [];
        s = s.replace(/```(?:[a-zA-Z0-9_-]+)?\n?([\s\S]*?)```/g, (_, code) => {
            const idx = codeBlocks.length;
            const esc = String(code ?? "").trim().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br/>");
            codeBlocks.push(`<span style="font-family:monospace;">${esc}</span>`);
            return `\u0000CODEBLOCK_${idx}\u0000`;
        });
        const inlineCodes = [];
        s = s.replace(/`([^`\n]+)`/g, (_, code) => {
            const idx = inlineCodes.length;
            const esc = String(code ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
            inlineCodes.push(`<span style="font-family:monospace;">${esc}</span>`);
            return `\u0000INLINECODE_${idx}\u0000`;
        });
        s = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "<a href=\"$2\" style=\"color:#7aa2f7;text-decoration:underline;\">$1</a>");
        s = s.replace(/\*\*\*([^*\n]+)\*\*\*/g, "<b><i>$1</i></b>");
        s = s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
        s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, "$1<i>$2</i>");

        const lines = s.split("\n");
        const renderedLines = [];
        for (let i = 0; i < lines.length; ++i) {
            let ln = lines[i];
            if (/^\s*#{1,4}\s+/.test(ln)) {
                ln = "<b>" + ln.replace(/^\s*#{1,4}\s+/, "") + "</b>";
            } else if (/^\s*[-*•]\s+/.test(ln)) {
                ln = "• " + ln.replace(/^\s*[-*•]\s+/, "");
            } else if (/^\s*(\d+)\.\s+/.test(ln)) {
                ln = ln.replace(/^\s*(\d+)\.\s+/, "<b>$1.</b> ");
            }
            renderedLines.push(ln);
        }
        let out = renderedLines.join("<br/>");
        out = out.replace(/\u0000INLINECODE_(\d+)\u0000/g, (_, i) => inlineCodes[Number(i)] ?? "");
        out = out.replace(/\u0000CODEBLOCK_(\d+)\u0000/g, (_, i) => codeBlocks[Number(i)] ?? "");
        return out;
    }

    property string lastCopiedText: ""
    property int lastCopiedBubbleIndex: -1
    property int activeSelectionBubbleIndex: -1
    property int activePressBubbleCount: 0
    property int focusedSelectableCount: 0
    property bool thinkingPreviewExpanded: true
    property int thinkingAnimTick: 0
    readonly property bool bubblePointerPressed: activePressBubbleCount > 0
    readonly property bool anySelectableTextFocused: focusedSelectableCount > 0
    TextEdit {
        id: clipboardHelper
        visible: false
    }
    Timer {
        id: copyFeedbackTimer
        interval: 1500
        repeat: false
        onTriggered: root.lastCopiedBubbleIndex = -1
    }
    Timer {
        id: thinkingPulseTimer
        interval: 260
        repeat: true
        running: root.visible && root.editing && !root.editorMode && AquaMind.isWorking
        onTriggered: root.thinkingAnimTick = (root.thinkingAnimTick + 1) % 3
    }
    function findChildByObjectName(item, targetName) {
        if (!item)
            return null;
        if (item.objectName === targetName)
            return item;
        if (item.contentItem && item.contentItem !== item) {
            const cMatch = findChildByObjectName(item.contentItem, targetName);
            if (cMatch)
                return cMatch;
        }
        const kids = item.children ?? [];
        for (let i = 0; i < kids.length; ++i) {
            const match = findChildByObjectName(kids[i], targetName);
            if (match)
                return match;
        }
        const dataKids = item.data ?? [];
        for (let i = 0; i < dataKids.length; ++i) {
            const match = findChildByObjectName(dataKids[i], targetName);
            if (match)
                return match;
        }
        return null;
    }
    function clearOtherSelections(exceptIndex = -999): void {
        const keep = Number(exceptIndex);
        if (keep !== -3) {
            if (String(streamingPreviewTextItem.selectedText ?? "").length > 0)
                streamingPreviewTextItem.deselect();
            streamingPreviewTextItem.cachedSelectedText = "";
        }
        for (let i = 0; i < transcript.count; ++i) {
            if (i === keep)
                continue;
            const textItem = root.findChildByObjectName(transcript, "aquaChatBubbleText-" + i);
            if (textItem) {
                if (String(textItem.selectedText ?? "").length > 0)
                    textItem.deselect();
                if ("cachedSelectedText" in textItem)
                    textItem.cachedSelectedText = "";
            }
        }
    }
    function resetSelectionState(): void {
        copyFeedbackTimer.stop();
        root.clearOtherSelections(-999);
        root.activeSelectionBubbleIndex = -1;
        root.lastCopiedBubbleIndex = -1;
        root.activePressBubbleCount = 0;
    }
    function copyTextToClipboard(textValue, bubbleIdx = -1): bool {
        const val = String(textValue ?? "");
        if (!val)
            return false;
        root.lastCopiedText = val;
        root.lastCopiedBubbleIndex = Number(bubbleIdx);
        copyFeedbackTimer.restart();
        clipboardHelper.text = val;
        clipboardHelper.selectAll();
        clipboardHelper.copy();
        try {
            if (typeof AquaMind !== "undefined" && AquaMind && typeof AquaMind.copyToClipboard === "function")
                AquaMind.copyToClipboard(val);
            else if (typeof Quickshell !== "undefined" && Quickshell && "clipboardText" in Quickshell)
                Quickshell.clipboardText = val;
        } catch (e) {}
        return true;
    }
    function selectBubbleText(index, startPos = 0, endPos = -1): string {
        const idx = Number(index);
        if (idx >= 0 && idx < transcript.count)
            transcript.positionViewAtIndex(idx, ListView.Contain);
        const textItem = root.findChildByObjectName(transcript, "aquaChatBubbleText-" + idx);
        if (!textItem)
            return "";
        root.clearOtherSelections(idx);
        if (Number(endPos) < 0)
            textItem.selectAll();
        else
            textItem.select(Number(startPos), Number(endPos));
        const sel = String(textItem.selectedText ?? "");
        if ("cachedSelectedText" in textItem)
            textItem.cachedSelectedText = sel;
        root.activeSelectionBubbleIndex = sel.length > 0 ? idx : -1;
        return sel;
    }
    function copyBubbleText(index, directTextItem = null): string {
        const idx = Number(index);
        if (!directTextItem && idx >= 0 && idx < transcript.count)
            transcript.positionViewAtIndex(idx, ListView.Contain);
        const textItem = directTextItem || root.findChildByObjectName(transcript, "aquaChatBubbleText-" + idx);
        const fallbackContent = (idx >= 0 && idx < AquaMind.history.length) ? String(AquaMind.history[idx]?.content ?? "") : "";
        const liveSel = textItem ? String(textItem.selectedText ?? "") : "";
        const cachedSel = (textItem && textItem.cachedSelectedText !== undefined) ? String(textItem.cachedSelectedText ?? "") : "";
        const activeSel = liveSel.length > 0 ? liveSel : cachedSel;
        const chosen = activeSel.length > 0 ? activeSel : (textItem && textItem.rawContent !== undefined ? String(textItem.rawContent) : (textItem ? String(textItem.text ?? "") : fallbackContent));
        if (!root.copyTextToClipboard(chosen, idx))
            return "";
        if (textItem && "cachedSelectedText" in textItem && liveSel.length === 0) {
            textItem.cachedSelectedText = "";
            if (root.activeSelectionBubbleIndex === idx)
                root.activeSelectionBubbleIndex = -1;
        }
        return chosen;
    }
    function copyActiveSelection(): bool {
        const streamSel = String(streamingPreviewTextItem.selectedText || streamingPreviewTextItem.cachedSelectedText || "");
        if (root.activeSelectionBubbleIndex === -3 && streamingPreviewTextItem.visible && streamSel.length > 0)
            return root.copyTextToClipboard(streamSel, -3);
        if (root.activeSelectionBubbleIndex >= 0 && root.activeSelectionBubbleIndex < transcript.count) {
            const activeItem = root.findChildByObjectName(transcript, "aquaChatBubbleText-" + root.activeSelectionBubbleIndex);
            const activeSel = activeItem ? String(activeItem.selectedText || activeItem.cachedSelectedText || "") : "";
            if (activeSel.length > 0)
                return root.copyTextToClipboard(activeSel, root.activeSelectionBubbleIndex);
        }
        if (streamingPreviewTextItem.visible && streamSel.length > 0)
            return root.copyTextToClipboard(streamSel, -3);
        for (let i = 0; i < transcript.count; ++i) {
            const textItem = root.findChildByObjectName(transcript, "aquaChatBubbleText-" + i);
            const itemSel = textItem ? String(textItem.selectedText || textItem.cachedSelectedText || "") : "";
            if (itemSel.length > 0)
                return root.copyTextToClipboard(itemSel, i);
        }
        return false;
    }

    readonly property bool backgroundThinkingBubble: false

    readonly property real compactPanelWidth: (sidebarOpen && editing) ? Math.min(Math.max(574, outputWidth - 24), Math.max(420, 380 + sidebarOffset)) : Math.min(380, Math.max(240, outputWidth - 32))
    readonly property real expandedSwarmWidth: Math.min(Math.max(420, outputWidth - 24), Math.round(compactPanelWidth + swarmSidebarTargetWidth + 10))
    readonly property real baseCloudWidth: embeddedMode ? (parent ? parent.width : outputWidth) : panelMode ? Math.round(compactPanelWidth + (expandedSwarmWidth - compactPanelWidth) * swarmExpandProgress) : Math.min(380, Math.max(220, outputWidth - 32))
    width: embeddedMode ? baseCloudWidth : Math.round(baseCloudWidth * editorScaleFactor)
    focus: visible && (!editing || (!message.activeFocus && !editorTextArea.activeFocus && !root.anySelectableTextFocused))
    function closePopup(): void {
        root.profileStage = 0;
        root.chatCheckInField = "";
        root.slashMenuDismissed = false;
        root.swarmOpen = false;
        if (root.embeddedMode) {
            if (root.pocketOpen)
                root.embeddedPocketOpen = false;
            return;
        }
        message.focus = false;
        if (root.pocketOpen)
            AquaPocket.close();
        AquaMind.dismiss();
    }
    function handleEscape(): bool {
        if (!root.visible || root.embeddedMode)
            return false;
        if (root.voiceStageActive) {
            AquaMind.cancelVoiceMode();
            return true;
        }
        if (root.slashMenuOpen) {
            root.slashMenuDismissed = true;
            return true;
        }
        if (clarificationCard.visible) {
            AquaMind.dismissClarification();
            return true;
        }
        if (root.editorMode) {
            AquaMind.closeEditorPopup();
            return true;
        }
        root.closePopup();
        return true;
    }
    Shortcut {
        sequences: ["Escape"]
        enabled: root.popupOpen && !Boolean(root.Window.window?.hasPerimeterEscapeRouter)
        context: Qt.WindowShortcut
        autoRepeat: false
        onActivated: root.handleEscape()
        onActivatedAmbiguously: root.handleEscape()
    }
    Keys.onPressed: event => {
        if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C && root.copyActiveSelection()) {
            event.accepted = true;
            return;
        }
        if (root.voiceStageActive && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
            event.accepted = true;
            if (AquaMind.voiceRecording)
                AquaMind.finishVoiceMode(AquaMind.voiceAutoSend);
            return;
        }
    }
    Keys.onEscapePressed: event => {
        if (root.handleEscape())
            event.accepted = true;
    }
    Connections {
        target: AquaMind
        function onContextOpenChanged(): void {
            if (AquaMind.contextOpen && !root.editing)
                Qt.callLater(() => root.forceActiveFocus());
        }
        function onHistoryChanged(): void {
            if (root.pendingJumpTurnIndex >= 0 && (!root.pendingJumpThreadId || root.pendingJumpThreadId === AquaMind.activeThreadId)) {
                Qt.callLater(() => {
                    if (root.pendingJumpTurnIndex >= 0 && (!root.pendingJumpThreadId || root.pendingJumpThreadId === AquaMind.activeThreadId)) {
                        const targetIdx = root.pendingJumpTurnIndex;
                        root.stickToBottom = false;
                        if (root.jumpToChatTurn(targetIdx)) {
                            root.pendingJumpTurnIndex = -1;
                            root.pendingJumpThreadId = "";
                        }
                    }
                });
                return;
            }
            if (root.editing && !AquaMind.historyLoadingOlder) {
                Qt.callLater(() => {
                    if (root.editing && !AquaMind.historyLoadingOlder)
                        root.scrollToLatestMessage();
                });
            }
        }
    }
    readonly property real nonEditingContentHeight: AquaMind.contextOpen ? content.implicitHeight : speechText.implicitHeight + (AquaMind.checkInStage.length > 0 ? content.spacing + 28 : 0)
    readonly property real compactPanelHeight: Math.min(440, Math.max(280, outputHeight - 48))
    readonly property real expandedSwarmHeight: Math.min(540, Math.max(340, outputHeight - 48))
    readonly property real baseCloudHeight: embeddedMode ? (parent ? parent.height : outputHeight) : (editing || pocketOpen || swarmOpen || swarmExpandProgress > 0.001) ? Math.round(compactPanelHeight + (expandedSwarmHeight - compactPanelHeight) * swarmExpandProgress) : nonEditingContentHeight + bodyTop + 12
    height: embeddedMode ? baseCloudHeight : Math.round(baseCloudHeight * editorScaleFactor)
    visible: embeddedMode || (allowed && actor && actor.visible && actor.inputReady && (pocketOpen || swarmOpen || AquaMind.text.length > 0 || AquaMind.conversationOpen || AquaMind.contextOpen))
    readonly property real actorVisualScale: (visible && actor) ? Math.max(.1, Number(actor.scale) || 1) : 1
    readonly property real actorVisualWidth: (visible && actor) ? actor.width * actorVisualScale : 0
    readonly property real actorVisualHeight: (visible && actor) ? actor.height * actorVisualScale : 0
    readonly property real actorVisualLeft: (visible && actor) ? actor.x + (actor.width - actorVisualWidth) / 2 : 0
    readonly property real actorVisualTop: (visible && actor) ? actor.y + (actor.height - actorVisualHeight) / 2 : 0
    readonly property real actorVisualBottom: actorVisualTop + actorVisualHeight
    readonly property real anchorGap: Math.max(6, Math.min(10, actorVisualHeight * .08))
    x: embeddedMode ? 0 : (visible ? Math.max(12, Math.min(outputWidth - width - 12, actorVisualLeft + actorVisualWidth / 2 - width / 2)) : 0)
    y: embeddedMode ? 0 : (visible ? (actorVisualTop - height - anchorGap >= 12 ? actorVisualTop - height - anchorGap : Math.min(outputHeight - height - 12, actorVisualBottom + anchorGap)) : 0)
    z: 240
    function containsScenePoint(point): bool {
        const local = root.mapFromItem(null, point.x, point.y);
        return visible && local.x >= 0 && local.x <= width && local.y >= 0 && local.y <= height;
    }

    // Outer Abyss shadow + glow matching AbyssField.frag outside pass
    Rectangle {
        visible: !root.embeddedMode
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
        radius: root.embeddedMode ? 14 : root.edgeRadius
        border.width: 1
        border.color: root.cloudBorderColor
        color: root.cloudColor
        gradient: Gradient {
            orientation: Gradient.Vertical
            GradientStop {
                position: 0.0
                color: root.edgeSurfaceColor(1.0)
            }
            GradientStop {
                position: 1.0
                color: root.edgeSurfaceColor(0.25)
            }
        }
        Rectangle {
            visible: root.panelMode
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: root.edgeRadius * 0.7
            anchors.rightMargin: root.edgeRadius * 0.7
            height: 1.5
            radius: 1
            color: Qt.alpha(AbyssStyle.accent, 0.45)
        }
    }

    // Unified Mochi-Style Header & Mode Switcher (Abyss Panel Style)
    RowLayout {
        id: unifiedHeader
        objectName: "aquaUnifiedHeader"
        visible: root.panelMode
        x: 12
        y: 10
        width: parent.width - 24
        height: 32
        spacing: 6

        RippleButton {
            id: sidebarToggleBtn
            objectName: "aquaChatSidebarToggle"
            Layout.preferredWidth: 30
            Layout.preferredHeight: 30
            implicitWidth: 30
            implicitHeight: 30
            buttonRadius: 15
            toggled: root.sidebarOpen
            colBackground: Qt.alpha(AbyssStyle.textColor, 0.05)
            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.24)
            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.32)
            onClicked: {
                if (root.pocketOpen) {
                    if (root.embeddedMode)
                        root.embeddedPocketOpen = false;
                    else
                        AquaMind.openChat();
                }
                root.sidebarOpen = !root.sidebarOpen;
                if (root.sidebarOpen && AquaMind.threads.length === 0)
                    AquaMind.refreshThreads();
            }
            StyledToolTip {
                extraVisibleCondition: sidebarToggleBtn.buttonHovered
                text: Translation.tr("Chats & Projects Sidebar")
            }
            contentItem: MaterialSymbol {
                anchors.centerIn: parent
                text: root.sidebarOpen ? "left_panel_close" : "left_panel_open"
                iconSize: 16
                color: sidebarToggleBtn.toggled ? AbyssStyle.accent : AbyssStyle.textColor
            }
        }

        Rectangle {
            objectName: "aquaUnifiedModeTabs"
            Layout.fillWidth: true
            Layout.preferredHeight: 30
            implicitHeight: 30
            radius: 15
            color: Qt.alpha(AbyssStyle.textColor, 0.05)
            border.width: 1
            border.color: Qt.alpha(AbyssStyle.textColor, 0.09)
            RowLayout {
                anchors.fill: parent
                anchors.margins: 3
                spacing: 3
                RippleButton {
                    id: chatTabBtn
                    objectName: "aquaUnifiedChatTab"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    implicitHeight: 24
                    buttonRadius: 12
                    toggled: root.editing
                    colBackground: "transparent"
                    colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.12)
                    colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                    colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                    onClicked: {
                        if (root.embeddedMode)
                            root.embeddedPocketOpen = false;
                        else
                            AquaMind.openChat();
                    }
                    StyledToolTip {
                        extraVisibleCondition: chatTabBtn.buttonHovered
                        text: AquaMind.activeProject ? (String(AquaMind.activeProject.name) + " · " + AquaMind.activeThreadTitle) : (AquaMind.activeThreadTitle !== "New Chat" ? AquaMind.activeThreadTitle : Translation.tr("Chat"))
                    }
                    contentItem: Item {
                        clip: true
                        Row {
                            anchors.centerIn: parent
                            spacing: 5
                            MaterialSymbol {
                                anchors.verticalCenter: parent.verticalCenter
                                text: AquaMind.activeProject ? "folder_special" : "auto_awesome"
                                iconSize: 15
                                color: chatTabBtn.toggled ? AbyssStyle.accent : AbyssStyle.textColorMuted
                            }
                            StyledText {
                                anchors.verticalCenter: parent.verticalCenter
                                text: Translation.tr("Chat")
                                wrapMode: Text.NoWrap
                                elide: Text.ElideRight
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                font.weight: chatTabBtn.toggled ? Font.DemiBold : Font.Normal
                                color: chatTabBtn.toggled ? AbyssStyle.textColor : AbyssStyle.textColorMuted
                            }
                        }
                    }
                }
                RippleButton {
                    id: pocketTabBtn
                    objectName: "aquaUnifiedPocketTab"
                    visible: AquaPocket.permitted
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    implicitHeight: 24
                    buttonRadius: 12
                    toggled: root.pocketOpen
                    colBackground: "transparent"
                    colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.12)
                    colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                    colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                    onClicked: {
                        root.swarmOpen = false;
                        root.openPocket();
                    }
                    contentItem: Item {
                        Row {
                            anchors.centerIn: parent
                            spacing: 5
                            MaterialSymbol {
                                anchors.verticalCenter: parent.verticalCenter
                                text: "inventory_2"
                                iconSize: 15
                                color: pocketTabBtn.toggled ? AbyssStyle.accent : AbyssStyle.textColorMuted
                            }
                            StyledText {
                                anchors.verticalCenter: parent.verticalCenter
                                text: AquaPocket.items.length > 0 ? Translation.tr("Pocket") + " (" + AquaPocket.items.length + ")" : Translation.tr("Pocket")
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                font.weight: pocketTabBtn.toggled ? Font.DemiBold : Font.Normal
                                color: pocketTabBtn.toggled ? AbyssStyle.textColor : AbyssStyle.textColorMuted
                            }
                        }
                    }
                }
            }
        }

        RippleButton {
            id: stopTtsQuickBtn
            objectName: "aquaStopTtsButton"
            visible: AquaMind.ttsSpeaking
            Layout.preferredWidth: 30
            Layout.preferredHeight: 30
            implicitWidth: 30
            implicitHeight: 30
            buttonRadius: 15
            colBackground: Qt.alpha(Appearance.colors.colError, 0.16)
            colBackgroundHover: Qt.alpha(Appearance.colors.colError, 0.28)
            onClicked: AquaMind.stopVoiceTts()
            StyledToolTip {
                extraVisibleCondition: stopTtsQuickBtn.buttonHovered
                text: Translation.tr("Stop TTS")
            }
            contentItem: MaterialSymbol {
                anchors.centerIn: parent
                text: "stop_circle"
                iconSize: 15
                color: Appearance.colors.colError
            }
        }

        RippleButton {
            id: pinChatQuickBtn
            objectName: "aquaPinChatButton"
            visible: !root.embeddedMode
            Layout.preferredWidth: 28
            Layout.preferredHeight: 30
            implicitWidth: 28
            implicitHeight: 30
            buttonRadius: 14
            toggled: AquaMind.conversationPinned
            colBackground: Qt.alpha(AbyssStyle.textColor, 0.05)
            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.16)
            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.24)
            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.32)
            onClicked: AquaMind.toggleConversationPinned()
            StyledToolTip {
                extraVisibleCondition: pinChatQuickBtn.buttonHovered
                text: AquaMind.conversationPinned ? Translation.tr("Unpin chat window") : Translation.tr("Pin chat window")
            }
            contentItem: MaterialSymbol {
                anchors.centerIn: parent
                text: "keep"
                iconSize: 14
                color: pinChatQuickBtn.toggled ? AbyssStyle.accent : AbyssStyle.textColorMuted
            }
        }

        RippleButton {
            id: swarmSidebarToggleBtn
            objectName: "aquaSwarmSidebarToggle"
            Layout.preferredWidth: 30
            Layout.preferredHeight: 30
            implicitWidth: 30
            implicitHeight: 30
            buttonRadius: 15
            toggled: root.swarmOpen
            colBackground: Qt.alpha(AbyssStyle.textColor, 0.05)
            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.16)
            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.24)
            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.32)
            onClicked: {
                if (root.swarmOpen) {
                    root.swarmOpen = false;
                } else {
                    root.openSwarm();
                }
            }
            StyledToolTip {
                extraVisibleCondition: swarmSidebarToggleBtn.buttonHovered
                text: Translation.tr("Swarm Right Sidebar (Board · Members · Graph)")
            }
            contentItem: Item {
                MaterialSymbol {
                    anchors.centerIn: parent
                    text: root.swarmOpen ? "right_panel_close" : "right_panel_open"
                    iconSize: 16
                    color: swarmSidebarToggleBtn.toggled || AquaMind.swarmBusy ? AbyssStyle.accent : AbyssStyle.textColor
                }
                Rectangle {
                    visible: AquaMind.swarmBusy || AquaMind.swarmMembers.length > 1
                    width: 6
                    height: 6
                    radius: 3
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.topMargin: 5
                    anchors.rightMargin: 4
                    color: AquaMind.swarmBusy ? AbyssStyle.accent : Qt.rgba(0.32, 0.84, 0.58, 1.0)
                }
            }
        }
    }

    component CheckInRow: Item {
        id: choice
        required property string field
        required property var options
        required property string selectedValue
        width: content.width
        height: 28
        implicitWidth: content.width
        implicitHeight: 28
        Repeater {
            model: choice.options
            DialogButton {
                required property var modelData
                required property int index
                objectName: "aqua" + choice.field + "-" + modelData.value
                x: index * ((choice.width + 2) / 5)
                y: 0
                width: (choice.width - 8) / 5
                height: 28
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

    AquaChatSidebar {
        id: chatSidebar
        visible: root.sidebarOpen && root.editing && !root.pocketOpen
        x: 12
        y: root.bodyTop
        width: root.sidebarWidth
        height: Math.max(0, root.height - root.bodyTop - 12)
        mind: AquaMind
        onThreadTurnSelected: (threadId, matchedTurnIndex) => {
            const targetThread = String(threadId ?? "").trim();
            root.pendingJumpThreadId = targetThread;
            root.pendingJumpTurnIndex = Number(matchedTurnIndex);
            if (!targetThread || targetThread === AquaMind.activeThreadId) {
                Qt.callLater(() => {
                    if (root.pendingJumpTurnIndex >= 0 && (!root.pendingJumpThreadId || root.pendingJumpThreadId === AquaMind.activeThreadId)) {
                        const targetIdx = root.pendingJumpTurnIndex;
                        if (root.jumpToChatTurn(targetIdx)) {
                            root.pendingJumpTurnIndex = -1;
                            root.pendingJumpThreadId = "";
                        }
                    }
                });
            }
        }
    }

    // Draggable edge resize handle for Sidebar (Codex Desktop style)
    Item {
        id: sidebarResizeHandle
        objectName: "aquaSidebarResizeHandle"
        visible: chatSidebar.visible
        x: chatSidebar.x + chatSidebar.width
        y: chatSidebar.y
        width: 10
        height: chatSidebar.height
        z: 15
        readonly property bool dragging: sidebarResizeMouse.pressed
        readonly property bool hovered: sidebarResizeMouse.containsMouse

        Rectangle {
            anchors.centerIn: parent
            width: parent.dragging || parent.hovered ? 3 : 2
            height: parent.dragging ? Math.max(36, parent.height * 0.55) : (parent.hovered ? Math.max(28, parent.height * 0.35) : 24)
            radius: 1.5
            color: parent.dragging ? AbyssStyle.accent : (parent.hovered ? Qt.alpha(AbyssStyle.accent, 0.72) : Qt.alpha(AbyssStyle.textColor, 0.16))
        }

        MouseArea {
            id: sidebarResizeMouse
            objectName: "aquaSidebarResizeMouse"
            anchors.fill: parent
            hoverEnabled: true
            preventStealing: true
            cursorShape: Qt.SplitHCursor
            property real pressStartSceneX: 0
            property real pressStartWidth: 186
            onPressed: mouse => {
                const gp = sidebarResizeMouse.mapToItem(null, mouse.x, mouse.y);
                pressStartSceneX = gp.x;
                pressStartWidth = root.sidebarWidth;
            }
            onPositionChanged: mouse => {
                if (!pressed)
                    return;
                const gp = sidebarResizeMouse.mapToItem(null, mouse.x, mouse.y);
                const delta = gp.x - pressStartSceneX;
                root.resizeSidebarTo(pressStartWidth + delta);
            }
            onDoubleClicked: root.resizeSidebarTo(186)
        }
    }

    Item {
        id: content
        visible: !root.pocketOpen && !(root.swarmOverlayInNarrowEmbedded && root.swarmOpen)
        x: 12 + root.sidebarOffset
        y: root.bodyTop
        width: Math.max(180, parent.width - 24 - root.sidebarOffset - root.swarmRightOffset)
        height: root.editing ? root.height - root.bodyTop - 12 : root.nonEditingContentHeight
        property real spacing: 7
        readonly property real contextRowsImplicitHeight: (todayContextHeader.visible ? todayContextHeader.height + spacing : 0) + (speechText.visible ? speechText.implicitHeight : 0) + (todayContextRows.visible ? spacing + todayContextRows.implicitHeight : 0) + (controlsColumn.visible ? spacing + controlsColumn.targetHeight : 0)
        implicitHeight: contextRowsImplicitHeight
        RowLayout {
            id: todayContextHeader
            visible: AquaMind.contextOpen && !root.editing
            x: 0
            y: 0
            width: content.width
            height: 20
            implicitHeight: 20
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
            objectName: "aquaSpeechText"
            readonly property string displaySpeech: root.compactBubbleSource(String(AquaMind.text ?? ""))
            readonly property bool useRichSpeech: root.containsMarkdownSyntax(displaySpeech)
            x: 0
            y: todayContextHeader.visible ? (todayContextHeader.height + content.spacing) : 0
            width: content.width
            Layout.fillWidth: true
            visible: !root.editing
            text: useRichSpeech ? root.formatBubbleMarkdown(displaySpeech) : displaySpeech
            textFormat: useRichSpeech ? Text.RichText : Text.PlainText
            wrapMode: Text.WordWrap
            font.pixelSize: Appearance.font.pixelSize.small
            Accessible.description: AquaMind.source === "ai" ? "AI reply" : "Companion message"
            onLinkActivated: link => {
                if (link && AquaMind.liveModelAutoDetectAllowed)
                    AquaMind.openExternalUrl(link);
            }
        }
        ColumnLayout {
            id: todayContextRows
            visible: AquaMind.contextOpen && !root.editing && AquaMind.checkInComplete
            x: 0
            y: (todayContextHeader.visible ? todayContextHeader.height + content.spacing : 0) + (speechText.visible ? speechText.implicitHeight + content.spacing : 0)
            width: content.width
            Layout.fillWidth: true
            StyledText {
                Layout.fillWidth: true
                text: Translation.tr("Mood") + ": " + AquaMind.userMood + "  ·  " + Translation.tr("Energy") + ": " + AquaMind.userEnergy + (AquaMind.userWeight.length > 0 ? ("  ·  " + Translation.tr("Weight") + ": " + AquaMind.userWeight + " kg") : "")
                wrapMode: Text.WordWrap
                color: Appearance.colors.colSubtext
            }
            Repeater {
                model: AquaMind.contextOpen ? AquaMind.reminderRows(DateTime.clock.date).slice(0, 6) : []
                StyledText {
                    required property var modelData
                    Layout.fillWidth: true
                    text: String(Math.floor(modelData.start / 60)).padStart(2, "0") + ":" + String(modelData.start % 60).padStart(2, "0") + " · " + modelData.title
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.pixelSize: Appearance.font.pixelSize.small
                }
            }
        }
        Rectangle {
            id: chatCheckInCard
            objectName: "aquaChatCheckInCard"
            visible: root.editing && !root.editorMode && !root.voiceStageActive
            x: 0
            y: 0
            width: content.width
            height: visible ? implicitHeight : 0
            implicitHeight: chatCheckInCol.implicitHeight + 10
            radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
            color: Qt.alpha(AbyssStyle.textColor, 0.05)
            border.width: 1
            border.color: Qt.alpha(AbyssStyle.textColor, 0.08)
            ColumnLayout {
                id: chatCheckInCol
                anchors {
                    left: parent.left
                    right: parent.right
                    top: parent.top
                    margins: 5
                }
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Flow {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        spacing: 4
                        RippleButton {
                            id: chatMoodBtn
                            objectName: "aquaChatMoodButton"
                            height: 22
                            implicitHeight: 22
                            implicitWidth: chatMoodRow.implicitWidth + 14
                            width: Math.min(parent.width, implicitWidth)
                            clip: true
                            buttonRadius: 11
                            toggled: root.activeChatCheckInField === "mood"
                            colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                            onClicked: {
                                root.profileStage = 0;
                                root.chatCheckInField = (root.activeChatCheckInField === "mood" ? "" : "mood");
                            }
                            contentItem: Item {
                                Row {
                                    id: chatMoodRow
                                    anchors.centerIn: parent
                                    spacing: 4
                                    MaterialSymbol {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "mood"
                                        iconSize: 13
                                        color: chatMoodBtn.toggled || AquaMind.userMood ? AbyssStyle.accent : AbyssStyle.textColorMuted
                                    }
                                    StyledText {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: Translation.tr("Mood") + ": " + (AquaMind.userMood ? root.formatCheckInLabel("mood", AquaMind.userMood) : "—")
                                        font.pixelSize: Appearance.font.pixelSize.smallest
                                        font.weight: chatMoodBtn.toggled ? Font.DemiBold : Font.Normal
                                        color: chatMoodBtn.toggled ? AbyssStyle.textColor : Appearance.colors.colSubtext
                                    }
                                }
                            }
                        }
                        RippleButton {
                            id: chatEnergyBtn
                            objectName: "aquaChatEnergyButton"
                            height: 22
                            implicitHeight: 22
                            implicitWidth: chatEnergyRow.implicitWidth + 14
                            width: Math.min(parent.width, implicitWidth)
                            clip: true
                            buttonRadius: 11
                            toggled: root.activeChatCheckInField === "energy"
                            colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                            onClicked: {
                                root.profileStage = 0;
                                root.chatCheckInField = (root.activeChatCheckInField === "energy" ? "" : "energy");
                            }
                            contentItem: Item {
                                Row {
                                    id: chatEnergyRow
                                    anchors.centerIn: parent
                                    spacing: 4
                                    MaterialSymbol {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "bolt"
                                        iconSize: 13
                                        color: chatEnergyBtn.toggled || AquaMind.userEnergy ? AbyssStyle.accent : AbyssStyle.textColorMuted
                                    }
                                    StyledText {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: Translation.tr("Energy") + ": " + (AquaMind.userEnergy ? root.formatCheckInLabel("energy", AquaMind.userEnergy) : "—")
                                        font.pixelSize: Appearance.font.pixelSize.smallest
                                        font.weight: chatEnergyBtn.toggled ? Font.DemiBold : Font.Normal
                                        color: chatEnergyBtn.toggled ? AbyssStyle.textColor : Appearance.colors.colSubtext
                                    }
                                }
                            }
                        }
                        RippleButton {
                            id: chatWeightBtn
                            objectName: "aquaChatWeightButton"
                            height: 22
                            implicitHeight: 22
                            implicitWidth: chatWeightRow.implicitWidth + 14
                            width: Math.min(parent.width, implicitWidth)
                            clip: true
                            buttonRadius: 11
                            toggled: root.activeChatCheckInField === "weight"
                            colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                            onClicked: {
                                root.profileStage = 0;
                                if (root.activeChatCheckInField === "weight") {
                                    root.chatCheckInField = "";
                                } else {
                                    root.chatCheckInField = "weight";
                                    chatWeightField.text = AquaMind.userWeight;
                                    Qt.callLater(() => {
                                        if (chatWeightField.visible) {
                                            chatWeightField.forceActiveFocus();
                                            chatWeightField.selectAll();
                                        }
                                    });
                                }
                            }
                            contentItem: Item {
                                Row {
                                    id: chatWeightRow
                                    anchors.centerIn: parent
                                    spacing: 4
                                    MaterialSymbol {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "monitor_weight"
                                        iconSize: 13
                                        color: chatWeightBtn.toggled || AquaMind.userWeight.length > 0 ? AbyssStyle.accent : AbyssStyle.textColorMuted
                                    }
                                    StyledText {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: AquaMind.userWeight.length > 0 ? (AquaMind.userWeight + " kg") : Translation.tr("Weight")
                                        font.pixelSize: Appearance.font.pixelSize.smallest
                                        font.weight: chatWeightBtn.toggled ? Font.DemiBold : Font.Normal
                                        color: chatWeightBtn.toggled ? AbyssStyle.textColor : Appearance.colors.colSubtext
                                    }
                                }
                            }
                        }
                    }
                    RippleButton {
                        id: chatOpenJournalBtn
                        objectName: "aquaChatOpenJournal"
                        visible: AquaMind.journal.journalPath.length > 0 || (typeof AlisIntergration !== "undefined" && Boolean(AlisIntergration?.nexus?.vaultConfigured))
                        Layout.preferredWidth: 22
                        Layout.preferredHeight: 22
                        Layout.alignment: Qt.AlignTop
                        implicitWidth: 22
                        implicitHeight: 22
                        buttonRadius: 11
                        colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.16)
                        onClicked: AquaMind.openJournal()
                        StyledToolTip {
                            extraVisibleCondition: chatOpenJournalBtn.buttonHovered
                            text: Translation.tr("Open journal")
                        }
                        contentItem: Item {
                            MaterialSymbol {
                                anchors.centerIn: parent
                                text: "open_in_new"
                                iconSize: 13
                                color: AbyssStyle.accent
                            }
                        }
                    }
                }
                RowLayout {
                    objectName: "aquaChatCheckInChoices"
                    visible: root.activeChatCheckInField === "mood" || root.activeChatCheckInField === "energy"
                    Layout.fillWidth: true
                    spacing: 4
                    Repeater {
                        model: root.activeChatCheckInField === "energy" ? root.energyOptions : root.moodOptions
                        RippleButton {
                            id: chatChoiceChip
                            required property var modelData
                            objectName: "aquaChatChoice-" + root.activeChatCheckInField + "-" + String(modelData.value)
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            Layout.preferredHeight: 24
                            implicitHeight: 24
                            buttonRadius: 10
                            enabled: !AquaMind.busy
                            toggled: (root.activeChatCheckInField === "energy" ? AquaMind.userEnergy : AquaMind.userMood) === modelData.value
                            colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.24)
                            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.32)
                            onClicked: {
                                const targetField = root.activeChatCheckInField;
                                if (AquaMind.setChatCheckInChoice(targetField, modelData.value)) {
                                    if (targetField === "mood" && !AquaMind.userEnergy)
                                        root.chatCheckInField = "energy";
                                    else
                                        root.chatCheckInField = "";
                                }
                            }
                            contentItem: Item {
                                StyledText {
                                    anchors.centerIn: parent
                                    text: chatChoiceChip.modelData.label
                                    horizontalAlignment: Text.AlignHCenter
                                    font.pixelSize: Appearance.font.pixelSize.smallest
                                    font.weight: chatChoiceChip.toggled ? Font.DemiBold : Font.Normal
                                    color: chatChoiceChip.toggled ? AbyssStyle.textColor : Appearance.colors.colSubtext
                                }
                            }
                        }
                    }
                }
                RowLayout {
                    objectName: "aquaChatWeightRow"
                    visible: root.activeChatCheckInField === "weight"
                    Layout.fillWidth: true
                    spacing: 6
                    TextField {
                        id: chatWeightField
                        objectName: "aquaChatWeightInput"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        implicitHeight: 26
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        color: AbyssStyle.textColor
                        placeholderText: Translation.tr("Weight (kg)")
                        placeholderTextColor: AbyssStyle.textColorMuted
                        inputMethodHints: Qt.ImhFormattedNumbersOnly
                        leftPadding: 8
                        rightPadding: 8
                        background: Rectangle {
                            radius: 9
                            color: Qt.alpha(AbyssStyle.textColor, 0.06)
                            border.width: 1
                            border.color: chatWeightField.activeFocus ? Qt.alpha(AbyssStyle.accent, 0.6) : Qt.alpha(AbyssStyle.textColor, 0.10)
                        }
                        onActiveFocusChanged: if (activeFocus)
                            root.wantsKeyboardFocus = true
                        Keys.onEscapePressed: event => {
                            root.chatCheckInField = "";
                            event.accepted = true;
                        }
                        onAccepted: {
                            if (AquaMind.setChatWeight(chatWeightField.text))
                                root.chatCheckInField = "";
                        }
                    }
                    RippleButton {
                        id: chatWeightSaveBtn
                        objectName: "aquaChatWeightSave"
                        Layout.preferredHeight: 26
                        Layout.preferredWidth: chatWeightSaveLabel.implicitWidth + 16
                        implicitHeight: 26
                        implicitWidth: Layout.preferredWidth
                        buttonRadius: 9
                        enabled: !AquaMind.busy && AquaMind.normalizeWeightInput(chatWeightField.text).length > 0
                        colBackground: Qt.alpha(AbyssStyle.accent, 0.22)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.34)
                        onClicked: {
                            if (AquaMind.setChatWeight(chatWeightField.text))
                                root.chatCheckInField = "";
                        }
                        contentItem: Item {
                            StyledText {
                                id: chatWeightSaveLabel
                                anchors.centerIn: parent
                                text: Translation.tr("Save")
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                font.weight: Font.DemiBold
                                color: AbyssStyle.textColor
                            }
                        }
                    }
                }
            }
        }
        // Keep empty-state flex spacer active across all profileStage values so controlsColumn stays bottom-aligned
        Item {
            id: emptyStateSpacer
            visible: root.editing && !root.editorMode && AquaMind.history.length === 0
            x: 0
            y: chatCheckInCard.visible ? (chatCheckInCard.height + content.spacing) : 0
            width: content.width
            height: Math.max(0, content.height - y - (thinkingCard.visible ? thinkingCard.height + content.spacing : 0) - (controlsColumn.visible ? controlsColumn.height + content.spacing : 0))
            Layout.fillWidth: true
            Layout.fillHeight: true
            StyledText {
                visible: root.profileStage === 0 && !root.slashMenuOpen && AquaMind.historyLoaded && !AquaMind.isWorking
                anchors.centerIn: parent
                width: Math.min(parent.width, 260)
                horizontalAlignment: Text.AlignHCenter
                text: "Splish! Ask anything, or / for commands."
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.small
            }
        }
        Item {
            id: transcriptArea
            visible: root.editing && !root.editorMode && AquaMind.history.length > 0
            onVisibleChanged: {
                if (visible)
                    root.scrollToLatestMessage();
            }
            x: 0
            y: chatCheckInCard.visible ? (chatCheckInCard.height + content.spacing) : 0
            width: content.width
            height: Math.max(80, content.height - y - (thinkingCard.visible ? thinkingCard.height + content.spacing : 0) - (controlsColumn.visible ? controlsColumn.height + content.spacing : 0))
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: transcript
                objectName: "aquaChatHistory"
                x: 0
                y: 0
                width: Math.max(80, parent.width - (dotNavigatorRail.visible ? dotNavigatorRail.width + 4 : 0))
                height: parent.height
                clip: true
                spacing: 7
                interactive: !root.bubblePointerPressed
                model: AquaMind.history
                property bool readyForOlder: false
                onCountChanged: {
                    if (transcript.count === 0)
                        root.resetSelectionState();
                    if (AquaMind.historyLoadingOlder)
                        return;
                    root.activeTurnIndex = -1;
                    root.stickToBottom = true;
                    readyForOlder = false;
                    Qt.callLater(() => {
                        if (!root.editing)
                            return;
                        root.scrollToLatestMessage();
                    });
                }
                onHeightChanged: {
                    if (root.editing && root.stickToBottom && !AquaMind.historyLoadingOlder && root.pendingJumpTurnIndex < 0 && !transcript.moving && !transcript.dragging) {
                        root._snapTranscriptToBottom();
                    }
                }
                onWidthChanged: {
                    if (root.editing && root.stickToBottom && !AquaMind.historyLoadingOlder && root.pendingJumpTurnIndex < 0 && !transcript.moving && !transcript.dragging) {
                        root._snapTranscriptToBottom();
                    }
                }
                onMovementEnded: {
                    root.stickToBottom = transcript.atYEnd;
                }
                onContentYChanged: {
                    if (!root._snappingToBottom && (transcript.moving || transcript.dragging || contentY < root._lastSnapContentY - 4)) {
                        root.stickToBottom = transcript.atYEnd;
                        if (!root.stickToBottom)
                            scrollToBottomSettleTimer.stop();
                    }
                    if (readyForOlder && !scrollToBottomSettleTimer.running && contentY <= transcript.originY + 12 && AquaMind.historyHasMore && !AquaMind.busy)
                        AquaMind.loadHistory(true);
                }
                delegate: Item {
                    id: bubbleRow
                    required property var modelData
                    required property int index
                    property bool activityExpanded: false
                    readonly property var bubbleActivitySteps: {
                        const raw = modelData?.activitySteps;
                        const out = [];
                        if (raw && typeof raw.length === "number") {
                            for (let i = 0; i < raw.length; ++i) {
                                const ln = String(raw[i] ?? "").trim();
                                if (ln)
                                    out.push(ln);
                            }
                        }
                        return out;
                    }
                    readonly property bool hasActivitySteps: !fromUser && bubbleActivitySteps.length > 0
                    readonly property int workedSec: Math.max(1, Number(modelData?.workedSeconds ?? 1))
                    readonly property bool fromVoiceReply: !fromUser && modelData?.fromVoiceReply === true
                    readonly property bool voiceTranscriptPending: !fromUser && modelData?.voiceTranscriptPending === true
                    readonly property bool voiceTranscribed: !fromUser && modelData?.voiceTranscribed === true
                    readonly property string workedLabel: {
                        if (voiceTranscriptPending)
                            return "🔊 Speaking response · Transcribing text... " + (activityExpanded ? "▾" : "›");
                        const baseWorked = workedSec >= 60 ? ("Worked for " + Math.floor(workedSec / 60) + "m " + (workedSec % 60 > 0 ? ((workedSec % 60) + "s ") : "")) : ("Worked for " + workedSec + "s ");
                        return baseWorked + (fromVoiceReply ? "· 🔊 Voice → Text " : "") + (activityExpanded ? "▾" : "›");
                    }
                    readonly property real activityOffset: activityLogBox.visible ? (activityLogBox.height + 3) : 0
                    readonly property bool fromUser: modelData?.role === "user"
                    readonly property string firstCodeLang: {
                        if (fromUser)
                            return "";
                        const m = /```([a-zA-Z0-9_-]+)?\n([\s\S]*?)```/.exec(String(modelData?.content ?? ""));
                        return m ? String(m[1] ?? "txt").trim().toLowerCase() : "txt";
                    }
                    readonly property string firstCodeBlock: {
                        if (fromUser)
                            return "";
                        const m = /```(?:[a-zA-Z0-9_-]+)?\n([\s\S]*?)```/.exec(String(modelData?.content ?? ""));
                        return m ? String(m[1] ?? "").trim() : "";
                    }
                    readonly property bool hasCodeBlock: !fromUser && firstCodeBlock.length > 0
                    readonly property string receiptId: !fromUser ? String(modelData?.receiptId ?? "") : ""
                    readonly property int addedLines: !fromUser ? Math.max(0, Number(modelData?.addedLines ?? 0)) : 0
                    readonly property int removedLines: !fromUser ? Math.max(0, Number(modelData?.removedLines ?? 0)) : 0
                    readonly property bool rolledBack: modelData?.rolledBack === true
                    readonly property bool hasFileDiff: !fromUser && (receiptId.length > 0 || addedLines > 0 || removedLines > 0)
                    readonly property bool hasFailedNotice: fromUser && modelData?.failed === true
                    readonly property string failedNoticeText: hasFailedNotice ? String(modelData?.errorText || AquaMind.errorMessage || "Request timed out or failed.") : ""
                    readonly property real metaChipsOffset: (hasCodeBlock || hasFileDiff || hasFailedNotice) ? 23 : 0
                    width: transcript.width
                    height: activityOffset + bubble.height + metaChipsOffset + 2
                    readonly property real navReserve: dotNavigatorRail.visible ? (dotNavigatorRail.width + 4) : 0
                    readonly property real copyReserve: 26
                    readonly property real availableWidth: Math.max(140, width - navReserve)
                    readonly property real maxBubbleWidth: Math.max(120, Math.min(availableWidth - copyReserve, Math.floor(availableWidth * (fromUser ? 0.85 : 0.92))))
                    readonly property real hPad: 11
                    readonly property real vPad: 7
                    readonly property real maxTextWidth: Math.max(60, maxBubbleWidth - hPad * 2)
                    HoverHandler {
                        id: rowHover
                        blocking: false
                    }
                    Column {
                        id: activityLogBox
                        objectName: "aquaChatBubbleActivity-" + bubbleRow.index
                        visible: bubbleRow.hasActivitySteps
                        x: 2
                        y: 0
                        width: bubbleRow.maxBubbleWidth
                        spacing: 2
                        Item {
                            width: Math.min(parent.width, activityToggleLabel.implicitWidth + 6)
                            height: 16
                            StyledText {
                                id: activityToggleLabel
                                objectName: "aquaChatBubbleWorkedLabel-" + bubbleRow.index
                                anchors.verticalCenter: parent.verticalCenter
                                text: bubbleRow.workedLabel
                                font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                color: activityToggleArea.containsMouse ? AbyssStyle.textColor : Appearance.colors.colSubtext
                            }
                            MouseArea {
                                id: activityToggleArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: bubbleRow.activityExpanded = !bubbleRow.activityExpanded
                            }
                        }
                        Column {
                            id: activityStepsCol
                            visible: bubbleRow.activityExpanded
                            x: 10
                            width: Math.max(40, activityLogBox.width - 12)
                            spacing: 2
                            Repeater {
                                model: bubbleRow.bubbleActivitySteps
                                delegate: StyledText {
                                    required property var modelData
                                    width: activityStepsCol.width
                                    text: String(modelData ?? "")
                                    elide: Text.ElideRight
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    color: Appearance.colors.colSubtext
                                }
                            }
                        }
                    }
                    Rectangle {
                        id: bubble
                        objectName: "aquaChatBubble-" + bubbleRow.index
                        y: bubbleRow.activityOffset
                        readonly property bool isVoiceSpeakingFirst: bubbleRow.voiceTranscriptPending
                        readonly property bool isVoiceTranscribedAfter: bubbleRow.voiceTranscribed
                        readonly property var bubbleImages: {
                            const raw = bubbleRow.modelData?.images;
                            const out = [];
                            if (raw && typeof raw.length === "number") {
                                for (let i = 0; i < raw.length; ++i) {
                                    if (raw[i])
                                        out.push(String(raw[i]));
                                }
                            }
                            if (out.length === 0 && !bubbleRow.fromUser) {
                                return AquaMind.extractReplyImages(String(bubbleRow.modelData?.content ?? ""));
                            }
                            return out;
                        }
                        readonly property bool hasBubbleImages: bubbleImages.length > 0
                        readonly property real thumbW: bubbleRow.fromUser ? 46 : Math.min(216, Math.max(120, bubbleRow.maxTextWidth))
                        readonly property real thumbH: bubbleRow.fromUser ? 46 : 122
                        readonly property real imagesRowHeight: hasBubbleImages ? (thumbH + 6) : 0
                        readonly property real imagesRowWidth: hasBubbleImages ? Math.min(bubbleRow.maxTextWidth, bubbleImages.length * (thumbW + 6) - 6) : 0
                        readonly property real measuredTextWidth: messageText.contentWidth > 0 ? Math.ceil(messageText.contentWidth) + 2 : Math.min(Math.ceil(messageText.implicitWidth) + 2, bubbleRow.maxTextWidth)
                        readonly property real measuredTextHeight: messageText.contentHeight > 0 ? Math.ceil(messageText.contentHeight) : Math.ceil(messageText.implicitHeight)
                        readonly property real unclampedWidth: Math.max(32, Math.min(bubbleRow.maxBubbleWidth, Math.max(measuredTextWidth, imagesRowWidth) + bubbleRow.hPad * 2))
                        readonly property real actionClusterOuterReserve: bubbleRow.fromUser ? 86 : 138
                        readonly property bool fitsActionClusterOutside: (unclampedWidth + actionClusterOuterReserve) <= bubbleRow.availableWidth
                        readonly property real minInnerClusterWidth: bubbleRow.fromUser ? 92 : 142
                        readonly property real actionFooterHeight: fitsActionClusterOutside ? 0 : 22
                        width: fitsActionClusterOutside ? unclampedWidth : Math.min(bubbleRow.maxBubbleWidth, Math.max(unclampedWidth, minInnerClusterWidth))
                        height: Math.max(28, measuredTextHeight + imagesRowHeight + bubbleRow.vPad * 2 + actionFooterHeight)
                        x: bubbleRow.fromUser ? Math.max(0, bubbleRow.availableWidth - width) : 0
                        radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
                        color: bubbleRow.fromUser ? Qt.alpha(AbyssStyle.accent, .14) : Qt.alpha(AbyssStyle.textColor, .06)
                        border.width: 1
                        border.color: String(messageText.selectedText ?? "").length > 0 ? Qt.alpha(AbyssStyle.accent, .48) : (bubbleRow.fromUser ? Qt.alpha(AbyssStyle.accent, .22) : Qt.alpha(AbyssStyle.textColor, .08))
                        opacity: bubbleRow.modelData?.failed === true ? .58 : 1
                        HoverHandler {
                            id: bubbleHover
                            blocking: false
                            cursorShape: messageText.hoveredLink ? Qt.PointingHandCursor : Qt.IBeamCursor
                        }
                        PointHandler {
                            id: bubblePressHandler
                            acceptedButtons: Qt.LeftButton
                            onActiveChanged: {
                                root.activePressBubbleCount = Math.max(0, root.activePressBubbleCount + (active ? 1 : -1));
                            }
                            Component.onDestruction: {
                                if (active)
                                    root.activePressBubbleCount = Math.max(0, root.activePressBubbleCount - 1);
                            }
                        }
                        Row {
                            id: bubbleImagesRow
                            objectName: "aquaBubbleImages-" + bubbleRow.index
                            visible: bubble.hasBubbleImages
                            x: bubbleRow.hPad
                            y: bubbleRow.vPad
                            height: bubble.thumbH
                            spacing: 6
                            Repeater {
                                model: bubble.bubbleImages
                                delegate: Rectangle {
                                    id: bubbleThumbCard
                                    required property var modelData
                                    required property int index
                                    objectName: "aquaBubbleImageThumb-" + bubbleRow.index + "-" + index
                                    readonly property string imageUrl: String(modelData ?? "").startsWith("/") ? ("file://" + String(modelData)) : String(modelData ?? "")
                                    width: bubble.thumbW
                                    height: bubble.thumbH
                                    radius: bubbleRow.fromUser ? 8 : 10
                                    clip: true
                                    color: Qt.alpha(AbyssStyle.textColor, 0.08)
                                    border.width: 1
                                    border.color: Qt.alpha(AbyssStyle.accent, 0.28)
                                    Image {
                                        anchors.fill: parent
                                        anchors.margins: 1
                                        fillMode: Image.PreserveAspectCrop
                                        asynchronous: false
                                        sourceSize: Qt.size(Math.max(1, Math.round(bubble.thumbW * 2)), Math.max(1, Math.round(bubble.thumbH * 2)))
                                        source: bubbleThumbCard.imageUrl
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (bubbleThumbCard.imageUrl && AquaMind.liveModelAutoDetectAllowed)
                                                Qt.openUrlExternally(bubbleThumbCard.imageUrl);
                                        }
                                    }
                                }
                            }
                        }
                        Rectangle {
                            id: voiceSpeakingBanner
                            objectName: "aquaVoiceSpeakingBanner-" + bubbleRow.index
                            visible: bubble.isVoiceSpeakingFirst
                            x: Math.max(4, bubbleRow.hPad - 4)
                            y: Math.max(2, bubbleRow.vPad + bubble.imagesRowHeight - 2)
                            width: Math.min(bubble.width - 8, Math.max(16, messageText.width + 8))
                            height: Math.max(18, messageText.height + 4)
                            radius: 8
                            color: Qt.alpha(AbyssStyle.accent, 0.14)
                            border.width: 1
                            border.color: Qt.alpha(AbyssStyle.accent, 0.36)
                        }
                        TextEdit {
                            id: messageText
                            objectName: "aquaChatBubbleText-" + bubbleRow.index
                            property string cachedSelectedText: ""
                            readonly property string rawContent: String(bubbleRow.modelData?.content ?? "")
                            readonly property string displaySource: bubbleRow.fromUser ? rawContent : root.compactBubbleSource(rawContent)
                            readonly property bool useRichMarkdown: root.containsMarkdownSyntax(displaySource)
                            x: bubbleRow.hPad
                            y: bubbleRow.vPad + bubble.imagesRowHeight
                            width: Math.min(Math.max(1, Math.ceil(implicitWidth) + 2), bubbleRow.maxTextWidth)
                            text: useRichMarkdown ? root.formatBubbleMarkdown(displaySource) : displaySource
                            textFormat: useRichMarkdown ? TextEdit.RichText : TextEdit.PlainText
                            wrapMode: TextEdit.Wrap
                            readOnly: true
                            selectByMouse: true
                            selectByKeyboard: true
                            persistentSelection: true
                            leftPadding: 0
                            rightPadding: 0
                            topPadding: 0
                            bottomPadding: 0
                            renderType: TextEdit.QtRendering
                            color: Appearance?.colors.colOnLayer0 ?? AbyssStyle.textColor
                            selectionColor: Qt.alpha(AbyssStyle.accent, 0.38)
                            selectedTextColor: AbyssStyle.textColor
                            font.hintingPreference: Font.PreferDefaultHinting
                            font.family: Appearance.font.family.main ?? "Roboto Flex"
                            font.pixelSize: Appearance.font.pixelSize.small
                            font.variableAxes: Appearance.font.variableAxes.main
                            onLinkActivated: link => {
                                if (link && AquaMind.liveModelAutoDetectAllowed)
                                    AquaMind.openExternalUrl(link);
                            }
                            onSelectedTextChanged: {
                                const curSel = String(selectedText ?? "");
                                if (curSel.length > 0) {
                                    cachedSelectedText = curSel;
                                    root.activeSelectionBubbleIndex = bubbleRow.index;
                                    root.clearOtherSelections(bubbleRow.index);
                                } else {
                                    if (bubblePressHandler.active && !bubbleCopyBtn.buttonHovered && !bubbleCopyBtn.down)
                                        cachedSelectedText = "";
                                    if (root.activeSelectionBubbleIndex === bubbleRow.index && cachedSelectedText.length === 0)
                                        root.activeSelectionBubbleIndex = -1;
                                }
                            }
                            onActiveFocusChanged: {
                                root.focusedSelectableCount = Math.max(0, root.focusedSelectableCount + (activeFocus ? 1 : -1));
                            }
                            Component.onDestruction: {
                                if (activeFocus)
                                    root.focusedSelectableCount = Math.max(0, root.focusedSelectableCount - 1);
                                if (root.activeSelectionBubbleIndex === bubbleRow.index)
                                    root.activeSelectionBubbleIndex = -1;
                            }
                            Keys.onPressed: event => {
                                if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C) {
                                    root.copyBubbleText(bubbleRow.index, messageText);
                                    event.accepted = true;
                                } else if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_A) {
                                    messageText.selectAll();
                                    event.accepted = true;
                                }
                            }
                        }
                    }
                    RippleButton {
                        id: bubbleCopyBtn
                        objectName: "aquaChatBubbleCopy-" + bubbleRow.index
                        focusPolicy: Qt.NoFocus
                        readonly property bool hasSelection: String(messageText.selectedText ?? "").length > 0 || String(messageText.cachedSelectedText ?? "").length > 0
                        readonly property bool justCopied: root.lastCopiedBubbleIndex === bubbleRow.index
                        readonly property bool fitsOutside: bubble.fitsActionClusterOutside
                        visible: bubbleHover.hovered || rowHover.hovered || buttonHovered || bubbleHoverActionsRow.visible || hasSelection || justCopied
                        width: (hasSelection || justCopied) ? 54 : 22
                        height: 20
                        implicitWidth: width
                        implicitHeight: 20
                        x: bubbleRow.fromUser ? (fitsOutside ? (bubble.x - width - 4) : (bubble.x + 6)) : (fitsOutside ? (bubble.x + bubble.width + 4) : Math.max(bubble.x + 6, bubbleHoverActionsRow.x - width - 4))
                        y: fitsOutside ? Math.max(2, bubble.y + bubble.height - height - 2) : Math.max(2, bubble.y + bubble.height - height - 5)
                        z: 15
                        buttonRadius: 10
                        colBackground: justCopied ? Qt.alpha(AbyssStyle.accent, 0.24) : Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.92)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
                        onClicked: root.copyBubbleText(bubbleRow.index, messageText)
                        StyledToolTip {
                            extraVisibleCondition: bubbleCopyBtn.buttonHovered
                            text: bubbleCopyBtn.justCopied ? "Copied!" : (bubbleCopyBtn.hasSelection ? "Copy selected text" : "Copy message")
                        }
                        contentItem: Item {
                            Row {
                                anchors.centerIn: parent
                                spacing: 3
                                MaterialSymbol {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: bubbleCopyBtn.justCopied ? "check" : "content_copy"
                                    iconSize: 12
                                    color: (bubbleCopyBtn.justCopied || bubbleCopyBtn.hasSelection) ? AbyssStyle.accent : AbyssStyle.textColor
                                }
                                StyledText {
                                    visible: bubbleCopyBtn.hasSelection || bubbleCopyBtn.justCopied
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: bubbleCopyBtn.justCopied ? "Copied" : "Copy"
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                    font.weight: Font.DemiBold
                                    color: AbyssStyle.accent
                                }
                            }
                        }
                    }

                    // Per-Message Hover Action Bar (Edit on user turns; Regenerate, TTS & Pocket on assistant turns)
                    Row {
                        id: bubbleHoverActionsRow
                        objectName: "aquaChatBubbleActions-" + bubbleRow.index
                        readonly property int clusterWidth: bubbleRow.fromUser ? 22 : 72
                        visible: bubbleHover.hovered || rowHover.hovered || bubbleCopyBtn.buttonHovered || bubbleEditBtn.buttonHovered || bubbleRegenBtn.buttonHovered || bubbleTtsBtn.buttonHovered || bubblePocketBtn.buttonHovered
                        spacing: 3
                        height: 20
                        x: bubbleRow.fromUser ? (bubbleCopyBtn.fitsOutside ? Math.max(2, bubbleCopyBtn.x - clusterWidth - 4) : (bubbleCopyBtn.x + bubbleCopyBtn.width + 4)) : (bubbleCopyBtn.fitsOutside ? Math.min(bubbleRow.availableWidth - clusterWidth - 2, bubbleCopyBtn.x + bubbleCopyBtn.width + 4) : (bubble.x + bubble.width - clusterWidth - 6))
                        y: bubbleCopyBtn.y
                        z: 15

                        RippleButton {
                            id: bubbleEditBtn
                            objectName: "aquaChatBubbleEdit-" + bubbleRow.index
                            visible: bubbleRow.fromUser
                            width: 22
                            height: 20
                            implicitWidth: 22
                            implicitHeight: 20
                            buttonRadius: 10
                            colBackground: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.92)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
                            onClicked: {
                                message.text = messageText.rawContent;
                                Qt.callLater(() => {
                                    if (root.editing) {
                                        message.forceActiveFocus();
                                        message.cursorPosition = message.text.length;
                                    }
                                });
                            }
                            StyledToolTip {
                                extraVisibleCondition: bubbleEditBtn.buttonHovered
                                text: "Edit prompt in composer"
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: "edit"
                                iconSize: 12
                                color: AbyssStyle.textColor
                            }
                        }

                        RippleButton {
                            id: bubbleRegenBtn
                            objectName: "aquaChatBubbleRegen-" + bubbleRow.index
                            visible: !bubbleRow.fromUser
                            width: 22
                            height: 20
                            implicitWidth: 22
                            implicitHeight: 20
                            enabled: !AquaMind.busy
                            buttonRadius: 10
                            colBackground: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.92)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
                            onClicked: {
                                const hist = Array.isArray(AquaMind.history) ? AquaMind.history : [];
                                for (let i = bubbleRow.index - 1; i >= 0; --i) {
                                    if (hist[i]?.role === "user" && String(hist[i]?.content ?? "").trim().length > 0) {
                                        AquaMind.sendMessage(String(hist[i].content));
                                        break;
                                    }
                                }
                            }
                            StyledToolTip {
                                extraVisibleCondition: bubbleRegenBtn.buttonHovered
                                text: "Regenerate reply"
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: "refresh"
                                iconSize: 12
                                color: AbyssStyle.textColor
                            }
                        }

                        RippleButton {
                            id: bubbleTtsBtn
                            objectName: "aquaChatBubbleTts-" + bubbleRow.index
                            visible: !bubbleRow.fromUser
                            width: 22
                            height: 20
                            implicitWidth: 22
                            implicitHeight: 20
                            buttonRadius: 10
                            colBackground: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.92)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
                            onClicked: {
                                if (AquaMind.ttsSpeaking)
                                    AquaMind.stopVoiceTts();
                                else
                                    AquaMind.speakText(root.stripMarkdownToPlain(messageText.rawContent));
                            }
                            StyledToolTip {
                                extraVisibleCondition: bubbleTtsBtn.buttonHovered
                                text: AquaMind.ttsSpeaking ? "Stop reading aloud" : "Read aloud (TTS)"
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: AquaMind.ttsSpeaking ? "stop_circle" : "volume_up"
                                iconSize: 12
                                color: AquaMind.ttsSpeaking ? Appearance.colors.colError : AbyssStyle.textColor
                            }
                        }

                        RippleButton {
                            id: bubblePocketBtn
                            objectName: "aquaChatBubblePocket-" + bubbleRow.index
                            visible: !bubbleRow.fromUser
                            width: 22
                            height: 20
                            implicitWidth: 22
                            implicitHeight: 20
                            buttonRadius: 10
                            colBackground: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.92)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
                            onClicked: {
                                const targetOrBody = String(bubbleRow.modelData?.targetPath || messageText.rawContent || "").trim();
                                if (targetOrBody.length > 0) {
                                    AquaMind.savePocketItem(targetOrBody, "turn_" + String(bubbleRow.index + 1));
                                }
                            }
                            StyledToolTip {
                                extraVisibleCondition: bubblePocketBtn.buttonHovered
                                text: "Save to Pocket"
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: "bookmark_add"
                                iconSize: 12
                                color: AbyssStyle.textColor
                            }
                        }
                    }

                    // Code Block Action Chips & File Diff / Undo Pill
                    Row {
                        id: bubbleMetaChipsRow
                        objectName: "aquaBubbleMetaChips-" + bubbleRow.index
                        visible: bubbleRow.hasCodeBlock || bubbleRow.hasFileDiff
                        x: bubble.x + 2
                        y: bubble.y + bubble.height + 3
                        height: 20
                        spacing: 5

                        RippleButton {
                            id: copyCodeChip
                            objectName: "aquaBubbleCopyCode-" + bubbleRow.index
                            visible: bubbleRow.hasCodeBlock
                            width: copyCodeRow.implicitWidth + 12
                            height: 20
                            implicitWidth: width
                            implicitHeight: 20
                            buttonRadius: 10
                            colBackground: Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.20)
                            onClicked: root.copyTextToClipboard(bubbleRow.firstCodeBlock, bubbleRow.index)
                            contentItem: Item {
                                Row {
                                    id: copyCodeRow
                                    anchors.centerIn: parent
                                    spacing: 3
                                    MaterialSymbol {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "content_copy"
                                        iconSize: 11
                                        color: AbyssStyle.accent
                                    }
                                    StyledText {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "Copy Code"
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        color: AbyssStyle.textColor
                                    }
                                }
                            }
                        }

                        RippleButton {
                            id: openCodeEditorChip
                            objectName: "aquaBubbleOpenCodeEditor-" + bubbleRow.index
                            visible: bubbleRow.hasCodeBlock
                            width: openCodeEditorRow.implicitWidth + 12
                            height: 20
                            implicitWidth: width
                            implicitHeight: 20
                            buttonRadius: 10
                            colBackground: Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.20)
                            onClicked: AquaMind.openEditorPopup({
                                title: "Code Snippet",
                                content: bubbleRow.firstCodeBlock,
                                ext: bubbleRow.firstCodeLang || "txt",
                                filePath: String(bubbleRow.modelData?.targetPath ?? "").trim()
                            })
                            contentItem: Item {
                                Row {
                                    id: openCodeEditorRow
                                    anchors.centerIn: parent
                                    spacing: 3
                                    MaterialSymbol {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "open_in_new"
                                        iconSize: 11
                                        color: AbyssStyle.accent
                                    }
                                    StyledText {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "Open in Editor"
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        color: AbyssStyle.textColor
                                    }
                                }
                            }
                        }

                        RippleButton {
                            id: diffUndoPill
                            objectName: "aquaBubbleDiffUndoPill-" + bubbleRow.index
                            visible: bubbleRow.hasFileDiff
                            enabled: !bubbleRow.rolledBack && bubbleRow.receiptId.length > 0 && !AquaMind.busy && !AquaMind.fileRollbackBusy
                            width: diffUndoRow.implicitWidth + 12
                            height: 20
                            implicitWidth: width
                            implicitHeight: 20
                            buttonRadius: 10
                            colBackground: bubbleRow.rolledBack ? Qt.alpha(AbyssStyle.textColor, 0.06) : Qt.alpha(AbyssStyle.accent, 0.14)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.26)
                            onClicked: {
                                if (bubbleRow.receiptId.length > 0 && !bubbleRow.rolledBack) {
                                    AquaMind.rollbackFileReceipt(bubbleRow.receiptId);
                                }
                            }
                            contentItem: Item {
                                Row {
                                    id: diffUndoRow
                                    anchors.centerIn: parent
                                    spacing: 4
                                    MaterialSymbol {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: bubbleRow.rolledBack ? "check" : "undo"
                                        iconSize: 11
                                        color: bubbleRow.rolledBack ? Appearance.colors.colSubtext : AbyssStyle.accent
                                    }
                                    StyledText {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: bubbleRow.rolledBack ? "Rolled back" : ("+" + String(bubbleRow.addedLines) + " / -" + String(bubbleRow.removedLines) + " · Undo")
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        font.weight: Font.DemiBold
                                        color: bubbleRow.rolledBack ? Appearance.colors.colSubtext : AbyssStyle.accent
                                    }
                                }
                            }
                        }
                    }

                    RippleButton {
                        id: failedRetryPill
                        objectName: "aquaBubbleFailedRetry-" + bubbleRow.index
                        visible: bubbleRow.hasFailedNotice
                        enabled: !AquaMind.busy && !AquaMind.isWorking
                        width: Math.min(bubbleRow.maxBubbleWidth, failedRetryRow.implicitWidth + 14)
                        height: 20
                        implicitWidth: width
                        implicitHeight: 20
                        x: Math.max(0, bubble.x + bubble.width - width)
                        y: bubble.y + bubble.height + 3
                        buttonRadius: 10
                        colBackground: Qt.alpha(Appearance.colors.colError, 0.14)
                        colBackgroundHover: Qt.alpha(Appearance.colors.colError, 0.26)
                        onClicked: {
                            const retryText = String(bubbleRow.modelData?.content ?? "").trim();
                            if (retryText.length > 0) {
                                AquaMind.sendMessage(retryText);
                            }
                        }
                        contentItem: Item {
                            Row {
                                id: failedRetryRow
                                anchors.centerIn: parent
                                spacing: 4
                                MaterialSymbol {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "refresh"
                                    iconSize: 11
                                    color: Appearance.colors.colError
                                }
                                StyledText {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: Math.min(implicitWidth, bubbleRow.maxBubbleWidth - 32)
                                    elide: Text.ElideRight
                                    text: bubbleRow.failedNoticeText + " · Retry"
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                    font.weight: Font.DemiBold
                                    color: Appearance.colors.colError
                                }
                            }
                        }
                    }
                }
                Connections {
                    target: AquaMind
                    function onHistoryPrepended(count): void {
                        transcript.positionViewAtIndex(count, ListView.Beginning);
                        transcript.readyForOlder = true;
                    }
                }
            }

            // Floating Scroll-to-Bottom Button
            RippleButton {
                id: scrollToBottomBtn
                objectName: "aquaScrollToBottomButton"
                visible: !transcript.atYEnd && transcript.contentHeight > transcript.height + 24
                width: 26
                height: 26
                implicitWidth: 26
                implicitHeight: 26
                x: Math.max(4, transcript.width - width - 8)
                y: Math.max(4, transcriptArea.height - height - 6)
                z: 45
                buttonRadius: 13
                colBackground: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.94)
                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                onClicked: root.scrollToLatestMessage()
                StyledToolTip {
                    extraVisibleCondition: scrollToBottomBtn.buttonHovered
                    text: "Scroll to bottom"
                }
                contentItem: MaterialSymbol {
                    anchors.centerIn: parent
                    text: "south"
                    iconSize: 15
                    color: AbyssStyle.accent
                }
            }

            // Antigravity-Style Chat Turn Dot Navigator Rail
            Item {
                id: dotNavigatorRail
                objectName: "aquaChatDotNavigator"
                visible: root.editing && !root.editorMode && root.chatTurns.length > 0
                readonly property int totalTurns: root.chatTurns.length
                readonly property real dotItemHeight: totalTurns > 28 ? 6 : (totalTurns > 16 ? 8 : 12)
                readonly property real dotSpacing: totalTurns > 28 ? 2 : (totalTurns > 16 ? 3 : 4)
                width: 14
                height: Math.min(parent.height, Math.max(24, dotNavigatorColumn.implicitHeight + 8))
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                clip: true
                z: 40

                Flickable {
                    id: dotNavigatorFlickable
                    anchors.fill: parent
                    contentWidth: width
                    contentHeight: dotNavigatorColumn.implicitHeight + 8
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    interactive: contentHeight > height

                    Column {
                        id: dotNavigatorColumn
                        x: 0
                        y: dotNavigatorFlickable.contentHeight <= dotNavigatorFlickable.height ? Math.round((dotNavigatorFlickable.height - implicitHeight) / 2) : 4
                        width: parent.width
                        spacing: dotNavigatorRail.dotSpacing
                        Repeater {
                            model: root.chatTurns
                            Item {
                                id: dotItem
                                required property var modelData
                                required property int index
                                objectName: "aquaChatDot-" + index
                                width: 14
                                height: dotNavigatorRail.dotItemHeight
                                readonly property bool dotHovered: dotMouse.containsMouse || root.forcedHoveredTurnIndex === index
                                readonly property bool dotActive: root.activeTurnIndex === index || (root.activeTurnIndex < 0 && index === root.chatTurns.length - 1)
                                readonly property bool compactTick: dotNavigatorRail.totalTurns > 16 && !dotHovered && !dotActive && index < (root.chatTurns.length - 6)
                                Rectangle {
                                    anchors.centerIn: parent
                                    width: dotItem.dotHovered ? 8 : (dotItem.dotActive ? 7 : (dotItem.compactTick ? 6 : 5))
                                    height: dotItem.compactTick ? 2 : width
                                    radius: height / 2
                                    color: dotItem.dotHovered ? AbyssStyle.textColor : (dotItem.dotActive ? AbyssStyle.accent : Qt.alpha(AbyssStyle.textColor, dotItem.compactTick ? 0.24 : 0.34))
                                    border.width: dotItem.dotHovered ? 1 : 0
                                    border.color: Qt.alpha(AbyssStyle.accent, 0.65)
                                }
                                MouseArea {
                                    id: dotMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onEntered: root.hoveredTurnIndex = dotItem.index
                                    onExited: {
                                        if (root.hoveredTurnIndex === dotItem.index)
                                            root.hoveredTurnIndex = -1;
                                    }
                                    onClicked: root.jumpToChatTurn(dotItem.index)
                                }
                            }
                        }
                    }
                }
            }

            // Antigravity-Style Floating Turn Preview Card on Dot Hover
            Rectangle {
                id: dotPreviewCard
                objectName: "aquaChatDotPreviewCard"
                visible: root.effectiveHoveredTurn !== null && dotNavigatorRail.visible
                width: Math.min(244, Math.max(168, transcriptArea.width - 24))
                height: dotPreviewCol.implicitHeight + 18
                x: Math.max(4, dotNavigatorRail.x - width - 6)
                y: {
                    const idx = Math.max(0, root.effectiveHoveredTurnIndex);
                    const total = Math.max(1, root.chatTurns.length);
                    const ratio = total > 1 ? (idx / (total - 1)) : 0.5;
                    const targetY = dotNavigatorRail.y + ratio * Math.max(0, dotNavigatorRail.height - height);
                    return Math.max(2, Math.min(Math.max(2, transcriptArea.height - height - 2), targetY));
                }
                z: 95
                radius: 10
                color: Qt.rgba(Math.max(0.06, AbyssStyle.surfaceDeep.r * 0.92), Math.max(0.06, AbyssStyle.surfaceDeep.g * 0.92), Math.max(0.08, AbyssStyle.surfaceDeep.b * 0.92), 0.97)
                border.width: 1
                border.color: Qt.alpha(AbyssStyle.textColor, 0.16)

                ColumnLayout {
                    id: dotPreviewCol
                    x: 10
                    y: 9
                    width: parent.width - 20
                    spacing: 5
                    StyledText {
                        objectName: "aquaChatDotPreviewPrompt"
                        Layout.fillWidth: true
                        text: String(root.effectiveHoveredTurn?.promptText ?? "")
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        font.pixelSize: Appearance.font.pixelSize.small
                        font.weight: Font.DemiBold
                        color: AbyssStyle.textColor
                    }
                    StyledText {
                        objectName: "aquaChatDotPreviewReply"
                        visible: String(root.effectiveHoveredTurn?.replyText ?? "").length > 0
                        Layout.fillWidth: true
                        text: String(root.effectiveHoveredTurn?.replyText ?? "")
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        maximumLineCount: 3
                        elide: Text.ElideRight
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        color: Appearance.colors.colSubtext
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        StyledText {
                            objectName: "aquaChatDotPreviewCounter"
                            text: String(root.effectiveHoveredTurn?.turnNumber ?? 1) + " of " + String(root.chatTurns.length)
                            font.pixelSize: Appearance.font.pixelSize.smallest - 1
                            color: Qt.alpha(AbyssStyle.textColor, 0.55)
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        StyledText {
                            objectName: "aquaChatDotPreviewTime"
                            text: root.formatTurnTimestamp(root.effectiveHoveredTurn?.createdMs ?? 0)
                            font.pixelSize: Appearance.font.pixelSize.smallest - 1
                            color: Qt.alpha(AbyssStyle.textColor, 0.55)
                        }
                    }
                }
            }
        }

        // Modern Desktop AI Thinking & Live Drafting Status Card (ChatGPT / Claude / Cursor / Antigravity style)
        Rectangle {
            id: thinkingCard
            objectName: "aquaThinkingCard"
            visible: root.editing && !root.editorMode && AquaMind.isWorking
            x: 0
            y: Math.max(0, content.height - (controlsColumn.visible ? controlsColumn.height + content.spacing : 0) - height)
            width: content.width
            height: implicitHeight
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            implicitHeight: 12 + 20 + (streamingPreviewBox.visible ? (5 + streamingPreviewBox.implicitHeight) : 0)
            radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
            color: Qt.alpha(AbyssStyle.accent, 0.08)
            border.width: 1
            border.color: Qt.alpha(AbyssStyle.accent, 0.28)
            clip: true
            onVisibleChanged: {
                if (visible && transcript.count > 0)
                    Qt.callLater(() => {
                        if (root.editing)
                            transcript.positionViewAtEnd();
                    });
            }

            ColumnLayout {
                id: thinkingCardCol
                x: 9
                y: 6
                width: Math.max(0, parent.width - 18)
                spacing: 5

                RowLayout {
                    x: 0
                    y: 0
                    width: parent.width
                    height: 20
                    Layout.fillWidth: true
                    spacing: 6

                    Item {
                        id: thinkingPulseBox
                        objectName: "aquaThinkingPulse"
                        Layout.preferredWidth: 34
                        Layout.preferredHeight: 18
                        implicitWidth: 34
                        implicitHeight: 18
                        Row {
                            anchors.centerIn: parent
                            spacing: 3
                            MaterialSymbol {
                                anchors.verticalCenter: parent.verticalCenter
                                text: AquaMind.workPhase === "tool" ? "terminal" : (AquaMind.workPhase === "drafting" ? "edit_note" : (AquaMind.workPhase === "deep" ? "psychology_alt" : "psychology"))
                                iconSize: 14
                                color: AbyssStyle.accent
                            }
                            Repeater {
                                model: 3
                                Rectangle {
                                    required property int index
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 4
                                    height: 4
                                    radius: 2
                                    color: AbyssStyle.accent
                                    opacity: root.thinkingAnimTick === index ? 1.0 : 0.32
                                    scale: root.thinkingAnimTick === index ? 1.2 : 0.88
                                    Behavior on opacity {
                                        NumberAnimation {
                                            duration: 160
                                        }
                                    }
                                    Behavior on scale {
                                        NumberAnimation {
                                            duration: 160
                                        }
                                    }
                                }
                            }
                        }
                    }

                    StyledText {
                        id: thinkingPhaseLabel
                        objectName: "aquaThinkingLabel"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 88
                        clip: true
                        text: AquaMind.workStatusTitle
                        elide: Text.ElideRight
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        font.weight: Font.DemiBold
                        color: AbyssStyle.textColor
                    }

                    Rectangle {
                        id: thinkingRouteBadge
                        objectName: "aquaThinkingRouteBadge"
                        visible: AquaMind.activeRoutedRoleLabel.length > 0
                        Layout.preferredWidth: implicitWidth
                        Layout.maximumWidth: 108
                        Layout.preferredHeight: 18
                        implicitWidth: Math.min(108, thinkingRouteBadgeRow.implicitWidth + 10)
                        implicitHeight: 18
                        radius: 9
                        clip: true
                        color: AquaMind.activeRoutedRole === "thinking" ? Qt.alpha(AbyssStyle.accent, 0.20) : Qt.alpha(AbyssStyle.textColor, 0.10)
                        border.width: 1
                        border.color: AquaMind.activeRoutedRole === "thinking" ? Qt.alpha(AbyssStyle.accent, 0.42) : Qt.alpha(AbyssStyle.textColor, 0.18)
                        HoverHandler {
                            id: thinkingRouteHover
                        }
                        StyledToolTip {
                            extraVisibleCondition: thinkingRouteHover.hovered && (AquaMind.activeRoutedReason.length > 0 || AquaMind.activeRoutedModelLabel.length > 0)
                            text: AquaMind.activeRoutedReason + (AquaMind.activeRoutedModelLabel.length > 0 ? (" · " + AquaMind.activeRoutedModelLabel) : "") + (AquaMind.activeRoutedDirective.length > 0 ? (" · " + AquaMind.activeRoutedDirective) : "")
                        }
                        Row {
                            id: thinkingRouteBadgeRow
                            anchors.centerIn: parent
                            spacing: 3
                            MaterialSymbol {
                                anchors.verticalCenter: parent.verticalCenter
                                text: AquaMind.activeRoutedRoleLabel === "Domain RAG" ? "menu_book" : (AquaMind.activeRoutedRole === "thinking" ? "psychology" : (AquaMind.activeRoutedRoleLabel === "Fast Chat" ? "chat_bubble" : "bolt"))
                                iconSize: 11
                                color: AquaMind.activeRoutedRole === "thinking" ? AbyssStyle.accent : AbyssStyle.textColor
                            }
                            StyledText {
                                id: thinkingRouteBadgeText
                                objectName: "aquaThinkingRouteText"
                                anchors.verticalCenter: parent.verticalCenter
                                width: Math.min(implicitWidth, 84)
                                elide: Text.ElideRight
                                text: AquaMind.activeRoutedRoleLabel
                                font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                font.weight: Font.DemiBold
                                color: AquaMind.activeRoutedRole === "thinking" ? AbyssStyle.accent : AbyssStyle.textColor
                            }
                        }
                    }

                    StyledText {
                        id: thinkingElapsedText
                        objectName: "aquaThinkingTimer"
                        visible: AquaMind.workElapsedLabel.length > 0
                        text: AquaMind.workElapsedLabel
                        font.pixelSize: Appearance.font.pixelSize.smallest - 1
                        color: Appearance.colors.colSubtext
                    }

                    RippleButton {
                        id: thinkingPreviewToggleBtn
                        objectName: "aquaThinkingTogglePreview"
                        visible: AquaMind.workStatusDetail.length > 0
                        Layout.preferredWidth: 20
                        Layout.preferredHeight: 20
                        implicitWidth: 20
                        implicitHeight: 20
                        buttonRadius: 10
                        colBackground: "transparent"
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                        onClicked: AquaMind.toggleWorkActivityExpanded()
                        StyledToolTip {
                            extraVisibleCondition: thinkingPreviewToggleBtn.buttonHovered
                            text: AquaMind.workActivityExpanded ? "Show current step only" : "View activity log"
                        }
                        contentItem: MaterialSymbol {
                            anchors.centerIn: parent
                            text: AquaMind.workActivityExpanded ? "expand_less" : "expand_more"
                            iconSize: 14
                            color: Appearance.colors.colSubtext
                        }
                    }

                    RippleButton {
                        id: thinkingStopBtn
                        objectName: "aquaThinkingStopButton"
                        Layout.preferredHeight: 20
                        Layout.preferredWidth: 48
                        implicitHeight: 20
                        implicitWidth: 48
                        buttonRadius: 10
                        colBackground: Qt.alpha(Appearance.colors.colError, 0.16)
                        colBackgroundHover: Qt.alpha(Appearance.colors.colError, 0.28)
                        onClicked: AquaMind.cancel()
                        StyledToolTip {
                            extraVisibleCondition: thinkingStopBtn.buttonHovered
                            text: "Stop generation"
                        }
                        contentItem: Item {
                            Row {
                                anchors.centerIn: parent
                                spacing: 2
                                MaterialSymbol {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "stop"
                                    iconSize: 12
                                    color: Appearance.colors.colError
                                }
                                StyledText {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "Stop"
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                    font.weight: Font.DemiBold
                                    color: Appearance.colors.colError
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    id: streamingPreviewBox
                    objectName: "aquaStreamingPreview"
                    visible: root.thinkingPreviewExpanded && AquaMind.workStatusDetail.length > 0
                    x: 0
                    y: 25
                    width: parent.width
                    height: implicitHeight
                    Layout.fillWidth: true
                    Layout.preferredHeight: AquaMind.workPhase === "drafting" ? Math.min(72, streamingPreviewTextItem.implicitHeight + 10) : (AquaMind.workActivityExpanded ? Math.min(108, streamingPreviewTextItem.implicitHeight + 10) : Math.min(24, streamingPreviewTextItem.implicitHeight + 10))
                    implicitHeight: Layout.preferredHeight
                    radius: 8
                    color: Qt.alpha(AbyssStyle.surfaceDeep, 0.45)
                    border.width: 1
                    border.color: Qt.alpha(AbyssStyle.textColor, 0.08)
                    clip: true

                    HoverHandler {
                        blocking: false
                        cursorShape: AquaMind.workPhase === "drafting" ? Qt.IBeamCursor : Qt.PointingHandCursor
                    }

                    TapHandler {
                        acceptedButtons: Qt.LeftButton
                        onTapped: {
                            if (AquaMind.workPhase !== "drafting" && String(streamingPreviewTextItem.selectedText ?? "").length === 0) {
                                AquaMind.toggleWorkActivityExpanded();
                            }
                        }
                    }

                    TextEdit {
                        id: streamingPreviewTextItem
                        objectName: "aquaStreamingPreviewText"
                        property string cachedSelectedText: ""
                        x: 7
                        y: (AquaMind.workActivityExpanded || String(streamingPreviewTextItem.selectedText ?? "").length > 0 || streamingPreviewPressHandler.active) ? 5 : Math.min(5, streamingPreviewBox.height - streamingPreviewTextItem.implicitHeight - 5)
                        width: Math.max(40, parent.width - 14)
                        text: AquaMind.workStatusDetail
                        textFormat: TextEdit.PlainText
                        wrapMode: (AquaMind.workPhase === "drafting" || AquaMind.workActivityExpanded) ? TextEdit.Wrap : TextEdit.NoWrap
                        readOnly: true
                        selectByMouse: true
                        selectByKeyboard: true
                        persistentSelection: true
                        leftPadding: 0
                        rightPadding: 0
                        topPadding: 0
                        bottomPadding: 0
                        renderType: TextEdit.QtRendering
                        color: AquaMind.workPhase === "drafting" ? AbyssStyle.textColor : Appearance.colors.colSubtext
                        selectionColor: Qt.alpha(AbyssStyle.accent, 0.38)
                        selectedTextColor: AbyssStyle.textColor
                        font.hintingPreference: Font.PreferDefaultHinting
                        font.family: Appearance.font.family.main ?? "Roboto Flex"
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        font.variableAxes: Appearance.font.variableAxes.main
                        PointHandler {
                            id: streamingPreviewPressHandler
                            acceptedButtons: Qt.LeftButton
                        }
                        onSelectedTextChanged: {
                            const curSel = String(selectedText ?? "");
                            if (curSel.length > 0) {
                                cachedSelectedText = curSel;
                                root.activeSelectionBubbleIndex = -3;
                                root.clearOtherSelections(-3);
                            } else if (root.activeSelectionBubbleIndex === -3 && cachedSelectedText.length === 0) {
                                root.activeSelectionBubbleIndex = -1;
                            }
                        }
                        onActiveFocusChanged: {
                            root.focusedSelectableCount = Math.max(0, root.focusedSelectableCount + (activeFocus ? 1 : -1));
                        }
                        Keys.onPressed: event => {
                            if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C && (streamingPreviewTextItem.selectedText.length > 0 || streamingPreviewTextItem.cachedSelectedText.length > 0)) {
                                root.copyTextToClipboard(streamingPreviewTextItem.selectedText || streamingPreviewTextItem.cachedSelectedText, -3);
                                event.accepted = true;
                            } else if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_A) {
                                streamingPreviewTextItem.selectAll();
                                event.accepted = true;
                            }
                        }
                    }
                }
            }
        }

        // Two-Tier Small File Editor Popup (expands Chatbox by +50% with sliding animation)
        Rectangle {
            id: editorPopupContainer
            objectName: "aquaEditorPopup"
            visible: root.editorMode
            x: 0
            y: 0
            width: content.width
            height: Math.max(120, content.height - (controlsColumn.visible ? controlsColumn.height + content.spacing : 0))
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
            color: Qt.alpha(AbyssStyle.surfaceDeep, 0.72)
            border.width: 1
            border.color: AquaMind.editorDirty ? Qt.alpha(AbyssStyle.accent, 0.42) : Qt.alpha(AbyssStyle.textColor, 0.12)
            clip: true

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 6

                RowLayout {
                    id: editorToolbar
                    Layout.fillWidth: true
                    spacing: 6
                    MaterialSymbol {
                        text: "code"
                        iconSize: 16
                        color: AbyssStyle.accent
                    }
                    StyledText {
                        id: editorTitleLabel
                        objectName: "aquaEditorFileName"
                        text: AquaMind.editorFileName + (AquaMind.editorDirty ? " *" : "")
                        font.pixelSize: Appearance.font.pixelSize.small
                        font.weight: Font.DemiBold
                        color: AbyssStyle.textColor
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                        Layout.minimumWidth: 40
                    }
                    StyledText {
                        objectName: "aquaEditorMetaLabel"
                        text: String(AquaMind.editorLineCount) + "L · " + root.formatBytes(AquaMind.editorByteSize) + (AquaMind.editorStatusMessage ? (" · " + AquaMind.editorStatusMessage) : "")
                        font.pixelSize: Appearance.font.pixelSize.smallest - 1
                        color: AquaMind.editorStatusMessage === "Saved" ? AbyssStyle.accent : Appearance.colors.colSubtext
                    }
                    RippleButton {
                        id: editorOpenExtBtn
                        objectName: "aquaEditorOpenExternalButton"
                        Layout.preferredHeight: 24
                        Layout.preferredWidth: 68
                        implicitHeight: 24
                        implicitWidth: 68
                        buttonRadius: 10
                        colBackground: Qt.alpha(AbyssStyle.textColor, 0.07)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.16)
                        onClicked: AquaMind.openFileInEditor(AquaMind.editorFilePath, true)
                        StyledToolTip {
                            extraVisibleCondition: editorOpenExtBtn.buttonHovered
                            text: "Open in system default editor"
                        }
                        contentItem: Item {
                            implicitWidth: editorOpenExtRow.implicitWidth
                            implicitHeight: editorOpenExtRow.implicitHeight
                            Row {
                                id: editorOpenExtRow
                                anchors.centerIn: parent
                                spacing: 3
                                MaterialSymbol {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "open_in_new"
                                    iconSize: 12
                                    color: AbyssStyle.textColor
                                }
                                StyledText {
                                    anchors.verticalCenter: parent.verticalCenter
                                    verticalAlignment: Text.AlignVCenter
                                    text: "Editor"
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    color: AbyssStyle.textColor
                                }
                            }
                        }
                    }
                    RippleButton {
                        id: editorSaveBtn
                        objectName: "aquaEditorSaveButton"
                        Layout.preferredHeight: 24
                        Layout.preferredWidth: 56
                        implicitHeight: 24
                        implicitWidth: 56
                        buttonRadius: 10
                        enabled: !AquaMind.editorSaving
                        colBackground: AquaMind.editorDirty ? Qt.alpha(AbyssStyle.accent, 0.24) : Qt.alpha(AbyssStyle.textColor, 0.08)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.34)
                        onClicked: AquaMind.saveEditorPopup()
                        contentItem: StyledText {
                            anchors.centerIn: parent
                            text: AquaMind.editorSaving ? "..." : "Save"
                            font.pixelSize: Appearance.font.pixelSize.smallest
                            font.weight: Font.DemiBold
                            color: AquaMind.editorDirty ? AbyssStyle.accent : AbyssStyle.textColor
                        }
                    }
                    RippleButton {
                        id: editorCloseBtn
                        objectName: "aquaEditorCloseButton"
                        Layout.preferredHeight: 24
                        Layout.preferredWidth: 24
                        implicitHeight: 24
                        implicitWidth: 24
                        buttonRadius: 12
                        colBackground: Qt.alpha(AbyssStyle.textColor, 0.07)
                        colBackgroundHover: Qt.alpha(AbyssStyle.textColor, 0.16)
                        onClicked: AquaMind.closeEditorPopup()
                        StyledToolTip {
                            extraVisibleCondition: editorCloseBtn.buttonHovered
                            text: "Back to chat"
                        }
                        contentItem: MaterialSymbol {
                            anchors.centerIn: parent
                            text: "close"
                            iconSize: 14
                            color: AbyssStyle.textColor
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: 8
                    color: Qt.alpha(AbyssStyle.textColor, 0.04)
                    border.width: 1
                    border.color: editorTextArea.activeFocus ? Qt.alpha(AbyssStyle.accent, 0.32) : Qt.alpha(AbyssStyle.textColor, 0.08)
                    clip: true

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 4
                        spacing: 4

                        // Line-number gutter
                        Rectangle {
                            Layout.preferredWidth: 28
                            Layout.fillHeight: true
                            radius: 5
                            color: Qt.alpha(AbyssStyle.textColor, 0.03)
                            clip: true
                            StyledText {
                                x: 4
                                y: 6 - editorFlickable.contentY
                                width: parent.width - 8
                                horizontalAlignment: Text.AlignRight
                                font.family: "monospace"
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Qt.alpha(AbyssStyle.textColor, 0.38)
                                text: {
                                    const n = Math.max(1, Math.min(300, AquaMind.editorLineCount));
                                    const lines = [];
                                    for (let i = 1; i <= n; ++i)
                                        lines.push(String(i));
                                    return lines.join("\n");
                                }
                            }
                        }

                        Flickable {
                            id: editorFlickable
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            contentWidth: width
                            contentHeight: Math.max(height, editorTextArea.implicitHeight + 12)
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds

                            TextArea {
                                id: editorTextArea
                                objectName: "aquaEditorTextArea"
                                width: editorFlickable.width
                                height: Math.max(editorFlickable.height, implicitHeight)
                                text: AquaMind.editorContent
                                wrapMode: TextEdit.Wrap
                                selectByMouse: true
                                color: AbyssStyle.textColor
                                selectionColor: Qt.alpha(AbyssStyle.accent, 0.32)
                                selectedTextColor: AbyssStyle.textColor
                                font.family: "monospace"
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                leftPadding: 6
                                rightPadding: 6
                                topPadding: 6
                                bottomPadding: 6
                                background: null
                                onActiveFocusChanged: if (activeFocus)
                                    root.wantsKeyboardFocus = true
                                onTextChanged: {
                                    if (text !== AquaMind.editorContent)
                                        AquaMind.updateEditorContent(text);
                                }
                                Keys.onPressed: event => {
                                    if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_S) {
                                        event.accepted = true;
                                        AquaMind.saveEditorPopup();
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        Item {
            id: controlsColumn
            visible: root.controlsVisible
            x: 0
            y: root.editing ? Math.max(0, content.height - height) : ((todayContextHeader.visible ? todayContextHeader.height + content.spacing : 0) + (speechText.visible ? speechText.implicitHeight + content.spacing : 0) + (todayContextRows.visible ? todayContextRows.implicitHeight + content.spacing : 0))
            width: content.width
            readonly property real topPanelsHeight: (profileBox.visible ? profileBox.implicitHeight + spacing : 0) + (clarificationCard.visible ? clarificationCard.implicitHeight + spacing : 0) + (slashCommandPopup.visible ? slashCommandPopup.implicitHeight + spacing : 0)
            readonly property real targetHeight: root.editing ? composer.implicitHeight + topPanelsHeight : 28
            height: targetHeight
            implicitHeight: targetHeight
            Layout.fillWidth: true
            Layout.preferredHeight: targetHeight
            property real spacing: 5
            CheckInRow {
                visible: !root.editing && AquaMind.checkInStage === "mood"
                field: "mood"
                selectedValue: AquaMind.userMood
                options: [
                    {
                        value: "terrible",
                        label: Translation.tr("Awful")
                    },
                    {
                        value: "bad",
                        label: Translation.tr("Low")
                    },
                    {
                        value: "okay",
                        label: Translation.tr("Okay")
                    },
                    {
                        value: "good",
                        label: Translation.tr("Good")
                    },
                    {
                        value: "great",
                        label: Translation.tr("Great")
                    }
                ]
            }
            CheckInRow {
                visible: !root.editing && AquaMind.checkInStage === "energy"
                field: "energy"
                selectedValue: AquaMind.userEnergy
                options: [
                    {
                        value: "drained",
                        label: Translation.tr("Drained")
                    },
                    {
                        value: "low",
                        label: Translation.tr("Low")
                    },
                    {
                        value: "medium",
                        label: Translation.tr("Mid")
                    },
                    {
                        value: "high",
                        label: Translation.tr("High")
                    },
                    {
                        value: "peak",
                        label: Translation.tr("Peak")
                    }
                ]
            }
            Rectangle {
                id: profileBox
                objectName: "aquaProfilePanel"
                readonly property bool logicallyOpen: root.editing && ((root.profileStage === 1 && AquaMind.thinkingSupported) || root.profileStage === 2 || AquaMind.pendingDeleteStage > 0 || root.effortClosingHold)
                property real profileSlideProgress: logicallyOpen ? 1.0 : 0.0
                Behavior on profileSlideProgress {
                    enabled: root.visible && AquaMind.liveModelAutoDetectAllowed
                    NumberAnimation {
                        duration: 185
                        easing.type: Easing.OutCubic
                    }
                }
                visible: root.editing && (logicallyOpen || profileSlideProgress > 0.01)
                clip: true
                opacity: profileSlideProgress
                x: 0
                y: 0
                width: content.width
                height: implicitHeight
                Layout.fillWidth: true
                Layout.preferredHeight: implicitHeight
                implicitHeight: Math.round((profilePanel.syncImplicitHeight + 16) * profileSlideProgress)
                radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
                color: Qt.alpha(AbyssStyle.textColor, .05)
                border.width: 1
                border.color: AquaMind.pendingDeleteStage > 0 ? Qt.alpha(Appearance.colors.colError, 0.45) : Qt.alpha(AbyssStyle.textColor, .08)
                Item {
                    id: profilePanel
                    x: 8
                    y: Math.round(8 - (1.0 - profileBox.profileSlideProgress) * 14)
                    width: parent.width - 16
                    readonly property real subPanelOffset: deleteConfirmBar.visible ? deleteConfirmBar.height + 5 : 0
                    readonly property real syncImplicitHeight: subPanelOffset + (effortPanel.visible ? effortPanel.implicitHeight : 0) + (modelPickerPanel.visible ? modelPickerPanel.implicitHeight : 0)
                    height: syncImplicitHeight
                    RowLayout {
                        id: deleteConfirmBar
                        objectName: "aquaDeleteConfirmBar"
                        visible: AquaMind.pendingDeleteStage > 0
                        width: parent.width
                        height: 26
                        spacing: 6
                        MaterialSymbol {
                            text: AquaMind.pendingDeleteStage === 2 ? "delete_forever" : "warning"
                            iconSize: 17
                            color: Appearance.colors.colError
                        }
                        StyledText {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: (AquaMind.pendingDeleteStage === 2 ? "Confirm 2/2 (Final): " : "Confirm 1/2: ") + AquaMind.pendingDeletePath.split("/").pop()
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
                    Item {
                        id: effortPanel
                        objectName: "aquaEffortPanel"
                        visible: (root.profileStage === 1 || (root.profileStage === 0 && root.lastProfileStage === 1 && (root.effortClosingHold || profileBox.profileSlideProgress > 0.01))) && AquaMind.thinkingSupported
                        y: profilePanel.subPanelOffset
                        width: parent.width
                        height: implicitHeight
                        implicitHeight: 24 + effortMenu.implicitHeight
                        RowLayout {
                            width: parent.width
                            height: 20
                            spacing: 6
                            StyledText {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: "Thinking effort"
                                verticalAlignment: Text.AlignVCenter
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colSubtext
                            }
                            RippleButton {
                                id: autoEffortBtn
                                objectName: "aquaEffortOption-auto"
                                readonly property bool isSelected: AquaMind.effectiveThinkingEffort === "auto"
                                implicitWidth: 44
                                implicitHeight: 20
                                width: 44
                                height: 20
                                toggled: isSelected
                                enabled: !AquaMind.busy
                                buttonRadius: 10
                                colBackground: Qt.alpha(AbyssStyle.textColor, 0.08)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.16)
                                colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                                colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.28)
                                onClicked: {
                                    AquaMind.setThinkingEffort("auto");
                                    root.triggerEffortSlideClose();
                                }
                                contentItem: Item {
                                    StyledText {
                                        anchors.centerIn: parent
                                        text: "Auto"
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                        font.weight: autoEffortBtn.isSelected ? Font.DemiBold : Font.Normal
                                        color: autoEffortBtn.isSelected ? AbyssStyle.accent : Appearance.colors.colSubtext
                                    }
                                }
                            }
                            StyledText {
                                text: AquaMind.thinkingEffortLabel
                                verticalAlignment: Text.AlignVCenter
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                font.weight: Font.DemiBold
                                color: Appearance.colors.colPrimary
                            }
                        }
                        Item {
                            id: effortMenu
                            objectName: "aquaThinkingEffort"
                            y: 24
                            width: parent.width
                            height: implicitHeight
                            implicitHeight: 36
                            readonly property bool autoActive: AquaMind.effectiveThinkingEffort === "auto"
                            opacity: autoActive ? 0.55 : 1.0
                            property bool draggingSlider: false
                            readonly property int stepCount: Math.max(1, effortRepeater.count)
                            readonly property real stepSlotWidth: width / stepCount
                            readonly property int activeStepIndex: {
                                const levels = AquaMind.thinkingLevels ?? [];
                                const cur = AquaMind.effectiveThinkingEffort;
                                for (let i = 0; i < levels.length; ++i) {
                                    const val = String(levels[i]?.value ?? "");
                                    if (val === cur || (cur === "off" && val === "instant"))
                                        return i;
                                }
                                return 0;
                            }
                            property real animatedStepIndex: activeStepIndex
                            Behavior on animatedStepIndex {
                                enabled: root.visible && AquaMind.liveModelAutoDetectAllowed
                                NumberAnimation {
                                    duration: 145
                                    easing.type: Easing.OutCubic
                                }
                            }
                            function stepIndexAtX(localX): int {
                                const count = Math.max(1, (AquaMind.thinkingLevels ?? []).length);
                                if (count <= 1 || width <= 0)
                                    return 0;
                                const pad = stepSlotWidth * 0.5;
                                const trackW = Math.max(1, width - stepSlotWidth);
                                const ratio = Math.max(0, Math.min(1, (localX - pad) / trackW));
                                return Math.max(0, Math.min(count - 1, Math.round(ratio * (count - 1))));
                            }
                            function applyEffortAtX(localX, closeAfter = false): bool {
                                const levels = AquaMind.thinkingLevels ?? [];
                                if (levels.length === 0 || AquaMind.busy)
                                    return false;
                                const idx = stepIndexAtX(localX);
                                const item = levels[idx];
                                if (item && item.value !== undefined) {
                                    AquaMind.setThinkingEffort(item.value);
                                }
                                if (closeAfter) {
                                    root.triggerEffortSlideClose();
                                }
                                return true;
                            }
                            Rectangle {
                                id: effortSliderTrackBg
                                objectName: "aquaThinkingEffortSliderTrack"
                                x: Math.round(effortMenu.stepSlotWidth * 0.5)
                                y: 9
                                width: Math.max(0, effortMenu.width - effortMenu.stepSlotWidth)
                                height: 4
                                radius: 2
                                color: Qt.alpha(AbyssStyle.textColor, 0.14)
                            }
                            Rectangle {
                                id: effortSliderTrackFill
                                x: effortSliderTrackBg.x
                                y: effortSliderTrackBg.y
                                width: effortMenu.autoActive ? 0 : (effortMenu.stepCount > 1 ? Math.round(effortSliderTrackBg.width * (effortMenu.animatedStepIndex / (effortMenu.stepCount - 1))) : 0)
                                height: 4
                                radius: 2
                                color: AbyssStyle.accent
                            }
                            Rectangle {
                                id: effortSliderThumb
                                objectName: "aquaThinkingEffortThumb"
                                visible: !effortMenu.autoActive
                                z: 5
                                width: 14
                                height: 14
                                radius: 7
                                x: Math.round((effortMenu.animatedStepIndex + 0.5) * effortMenu.stepSlotWidth - width / 2)
                                y: 4
                                color: AbyssStyle.accent
                                border.width: 2
                                border.color: Appearance.colors.colOnPrimary
                            }
                            Repeater {
                                id: effortRepeater
                                model: AquaMind.thinkingLevels
                                RippleButton {
                                    id: effortOption
                                    required property var modelData
                                    required property int index
                                    objectName: "aquaEffortOption-" + String(modelData.value)
                                    readonly property string shortStepLabel: modelData.value === "instant" || modelData.value === "off" ? "Instant" : modelData.value === "low" ? "Low" : modelData.value === "medium" ? "Med" : modelData.value === "high" ? "High" : modelData.value === "xhigh" ? "XHigh" : "Max"
                                    x: Math.round(index * effortMenu.stepSlotWidth)
                                    y: 0
                                    width: Math.max(28, Math.round(effortMenu.stepSlotWidth))
                                    height: 36
                                    implicitWidth: 32
                                    implicitHeight: 36
                                    buttonText: modelData.label
                                    toggled: AquaMind.effectiveThinkingEffort === modelData.value || (AquaMind.effectiveThinkingEffort === "off" && modelData.value === "instant")
                                    enabled: !AquaMind.busy
                                    buttonRadius: 8
                                    colBackground: "transparent"
                                    colBackgroundHover: Qt.alpha(AbyssStyle.accent, .10)
                                    colBackgroundToggled: "transparent"
                                    colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, .14)
                                    downAction: event => {
                                        effortMenu.draggingSlider = false;
                                    }
                                    moveAction: event => {
                                        if (effortOption.down) {
                                            effortMenu.draggingSlider = true;
                                            effortOption.suppressClickOnRelease = true;
                                            effortMenu.applyEffortAtX(effortOption.x + event.x, false);
                                        }
                                    }
                                    releaseAction: () => {
                                        if (effortMenu.draggingSlider) {
                                            effortMenu.draggingSlider = false;
                                            root.triggerEffortSlideClose();
                                        }
                                    }
                                    onClicked: {
                                        AquaMind.setThinkingEffort(modelData.value);
                                        root.triggerEffortSlideClose();
                                    }
                                    contentItem: Item {
                                        Rectangle {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            y: 6
                                            width: 10
                                            height: 10
                                            radius: 5
                                            visible: !effortOption.toggled
                                            color: (!effortMenu.autoActive && effortOption.index <= effortMenu.activeStepIndex) ? Qt.alpha(AbyssStyle.accent, 0.72) : Appearance.colors.colLayer2
                                            border.width: 1
                                            border.color: Qt.alpha(AbyssStyle.textColor, 0.28)
                                        }
                                        StyledText {
                                            objectName: "aquaEffortLabel-" + String(effortOption.modelData.value)
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            y: 20
                                            height: 14
                                            text: effortOption.shortStepLabel
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                            font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                            font.weight: effortOption.toggled ? Font.DemiBold : Font.Normal
                                            color: effortOption.toggled ? AbyssStyle.accent : Appearance.colors.colSubtext
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Item {
                        id: modelPickerPanel
                        objectName: "aquaModelPicker"
                        visible: root.profileStage === 2 || (root.profileStage === 0 && root.lastProfileStage === 2 && !root.effortClosingHold && profileBox.profileSlideProgress > 0.01)
                        y: profilePanel.subPanelOffset
                        width: parent.width
                        height: implicitHeight
                        readonly property bool isCustomRoleMode: root.modelPickerRole === "action" || root.modelPickerRole === "thinking"
                        readonly property real optionsTopY: isCustomRoleMode ? 48 : 24
                        implicitHeight: optionsTopY + Math.max(0, modelRepeater.count * 34 + Math.max(0, modelRepeater.count - 1) * 4)

                        // Hidden compatibility anchors for tests
                        Item {
                            id: modelRouteStatusText
                            objectName: "aquaModelRouteStatus"
                            visible: false
                            property string text: AquaMind.routingSummaryLabel
                        }
                        Item {
                            id: autoRouteToggleBtn
                            objectName: "aquaAutoRouteToggle"
                            visible: false
                            property bool toggled: AquaMind.autoModelRoutingEnabled
                        }
                        Item {
                            id: mtpOptimizeToggleBtn
                            objectName: "aquaMtpOptimizeToggle"
                            visible: false
                            property bool toggled: AquaMind.mtpOptimizeEnabled
                        }

                        // Row 1: [Auto] [Custom] + [Reload & detect models] on the same line
                        Item {
                            id: modeHeaderRow
                            x: 0
                            y: 0
                            width: parent.width
                            height: 20
                            readonly property real refreshBtnW: 22
                            readonly property real modeTabW: Math.max(40, (width - refreshBtnW - 8) / 2)

                            RippleButton {
                                id: pickerModeAutoBtn
                                objectName: "aquaPickerRolePrimary"
                                x: 0
                                y: 0
                                width: modeHeaderRow.modeTabW
                                height: 20
                                implicitWidth: width
                                implicitHeight: 20
                                toggled: !modelPickerPanel.isCustomRoleMode
                                buttonRadius: 8
                                colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                                colBackgroundToggled: Appearance.colors.colPrimaryContainer
                                colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                                onClicked: root.modelPickerRole = "primary"
                                contentItem: Item {
                                    StyledText {
                                        anchors.fill: parent
                                        leftPadding: 4
                                        rightPadding: 4
                                        text: "Auto"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        font.weight: pickerModeAutoBtn.toggled ? Font.DemiBold : Font.Normal
                                        color: pickerModeAutoBtn.toggled ? Appearance.colors.colOnPrimaryContainer : Appearance.colors.colSubtext
                                    }
                                }
                            }

                            RippleButton {
                                id: pickerModeCustomBtn
                                objectName: "aquaPickerRoleCustom"
                                x: modeHeaderRow.modeTabW + 4
                                y: 0
                                width: modeHeaderRow.modeTabW
                                height: 20
                                implicitWidth: width
                                implicitHeight: 20
                                toggled: modelPickerPanel.isCustomRoleMode
                                buttonRadius: 8
                                colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                                colBackgroundToggled: Appearance.colors.colPrimaryContainer
                                colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                                onClicked: {
                                    if (!modelPickerPanel.isCustomRoleMode)
                                        root.modelPickerRole = "action";
                                }
                                contentItem: Item {
                                    StyledText {
                                        anchors.fill: parent
                                        leftPadding: 4
                                        rightPadding: 4
                                        text: "Custom"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        font.weight: pickerModeCustomBtn.toggled ? Font.DemiBold : Font.Normal
                                        color: pickerModeCustomBtn.toggled ? Appearance.colors.colOnPrimaryContainer : Appearance.colors.colSubtext
                                    }
                                }
                            }

                            RippleButton {
                                id: pickerRefreshBtn
                                objectName: "aquaPickerRefreshModelsButton"
                                x: Math.max(0, modeHeaderRow.width - width)
                                y: 0
                                width: modeHeaderRow.refreshBtnW
                                height: 20
                                implicitHeight: 20
                                implicitWidth: modeHeaderRow.refreshBtnW
                                enabled: !AquaMind.refreshingModels
                                buttonRadius: 8
                                colBackground: Qt.alpha(AbyssStyle.textColor, 0.08)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.16)
                                onClicked: AquaMind.refreshAvailableModels(true)
                                StyledToolTip {
                                    extraVisibleCondition: pickerRefreshBtn.buttonHovered
                                    text: "Auto-detect & refresh available models"
                                }
                                contentItem: Item {
                                    MaterialSymbol {
                                        anchors.centerIn: parent
                                        text: "sync"
                                        iconSize: 13
                                        color: AquaMind.refreshingModels ? Appearance.colors.colSubtext : AbyssStyle.accent
                                    }
                                }
                            }
                        }

                        // Row 2: [UI Action] [Deep Thinking] (only shown when Custom is selected)
                        Item {
                            id: customRoleSubRow
                            visible: modelPickerPanel.isCustomRoleMode
                            x: 0
                            y: 24
                            width: parent.width
                            height: 20
                            readonly property real subTabW: Math.max(40, (width - 4) / 2)

                            RippleButton {
                                id: pickerRoleActionBtn
                                objectName: "aquaPickerRoleAction"
                                x: 0
                                y: 0
                                width: customRoleSubRow.subTabW
                                height: 20
                                implicitWidth: width
                                implicitHeight: 20
                                toggled: root.modelPickerRole === "action"
                                buttonRadius: 8
                                colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                                colBackgroundToggled: Appearance.colors.colPrimaryContainer
                                colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                                onClicked: root.modelPickerRole = "action"
                                contentItem: Item {
                                    StyledText {
                                        anchors.fill: parent
                                        leftPadding: 4
                                        rightPadding: 4
                                        text: "UI Action"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        font.weight: pickerRoleActionBtn.toggled ? Font.DemiBold : Font.Normal
                                        color: pickerRoleActionBtn.toggled ? Appearance.colors.colOnPrimaryContainer : Appearance.colors.colSubtext
                                    }
                                }
                            }

                            RippleButton {
                                id: pickerRoleThinkingBtn
                                objectName: "aquaPickerRoleThinking"
                                x: customRoleSubRow.subTabW + 4
                                y: 0
                                width: customRoleSubRow.subTabW
                                height: 20
                                implicitWidth: width
                                implicitHeight: 20
                                toggled: root.modelPickerRole === "thinking"
                                buttonRadius: 8
                                colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                                colBackgroundToggled: Appearance.colors.colPrimaryContainer
                                colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                                onClicked: root.modelPickerRole = "thinking"
                                contentItem: Item {
                                    StyledText {
                                        anchors.fill: parent
                                        leftPadding: 4
                                        rightPadding: 4
                                        text: "Deep Thinking"
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        font.weight: pickerRoleThinkingBtn.toggled ? Font.DemiBold : Font.Normal
                                        color: pickerRoleThinkingBtn.toggled ? Appearance.colors.colOnPrimaryContainer : Appearance.colors.colSubtext
                                    }
                                }
                            }
                        }

                        Repeater {
                            id: modelRepeater
                            model: root.modelPickerRole === "action" ? AquaMind.actionSelectableModels : (root.modelPickerRole === "thinking" ? AquaMind.thinkingSelectableModels : AquaMind.selectableModels)
                            RippleButton {
                                id: modelOption
                                required property var modelData
                                required property int index
                                objectName: "aquaModelOption-" + String(modelData.name)
                                x: 0
                                y: modelPickerPanel.optionsTopY + index * 38
                                width: modelPickerPanel.width
                                height: 34
                                implicitHeight: 34
                                buttonText: modelData.label ?? modelData.name
                                toggled: root.modelPickerRole === "action" ? (AquaMind.configuredActionModel === modelData.name) : (root.modelPickerRole === "thinking" ? (AquaMind.configuredThinkingModel === modelData.name) : (AquaMind.model === modelData.name))
                                enabled: !AquaMind.busy
                                buttonRadius: 12
                                colBackground: "transparent"
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, .10)
                                colBackgroundToggled: Appearance.colors.colPrimaryContainer
                                colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                                onClicked: {
                                    if (root.modelPickerRole === "action") {
                                        AquaMind.selectActionModel(modelData);
                                    } else if (root.modelPickerRole === "thinking") {
                                        AquaMind.selectThinkingModel(modelData);
                                    } else {
                                        AquaMind.selectModel(modelData);
                                        root.profileStage = 0;
                                        Qt.callLater(() => {
                                            if (root.editing)
                                                message.forceActiveFocus();
                                        });
                                    }
                                }
                                contentItem: Item {
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 12
                                        anchors.rightMargin: 12
                                        spacing: 8
                                        StyledText {
                                            objectName: "aquaModelLabel-" + String(modelOption.modelData.name)
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            Layout.alignment: Qt.AlignVCenter
                                            text: modelOption.buttonText
                                            verticalAlignment: Text.AlignVCenter
                                            elide: Text.ElideRight
                                            font.pixelSize: Appearance.font.pixelSize.small
                                            color: modelOption.toggled ? Appearance.colors.colOnPrimaryContainer : Appearance.colors.colOnLayer1
                                        }
                                        MaterialSymbol {
                                            visible: modelOption.toggled
                                            Layout.alignment: Qt.AlignVCenter
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
            }

            // Interactive Clarification Card (Codex / Antigravity / Claude Desktop style: 1-3 Ideas + Custom Input)
            Rectangle {
                id: clarificationCard
                objectName: "aquaClarificationCard"
                visible: root.editing && !root.editorMode && AquaMind.activeClarification !== null && Array.isArray(AquaMind.activeClarification?.options) && AquaMind.activeClarification.options.length > 0
                x: 0
                y: profileBox.visible ? profileBox.height + controlsColumn.spacing : 0
                width: content.width
                height: implicitHeight
                Layout.fillWidth: true
                Layout.preferredHeight: implicitHeight
                implicitHeight: clarificationInnerCol.implicitHeight + 16
                radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
                color: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.96)
                border.width: 1
                border.color: Qt.alpha(AbyssStyle.accent, 0.36)
                clip: true
                z: 25

                ColumnLayout {
                    id: clarificationInnerCol
                    x: 8
                    y: 8
                    width: Math.max(0, parent.width - 16)
                    spacing: 5

                    // Header: Question + Skip/Dismiss button
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Rectangle {
                            Layout.preferredWidth: 20
                            Layout.preferredHeight: 20
                            Layout.alignment: Qt.AlignTop
                            implicitWidth: 20
                            implicitHeight: 20
                            radius: 6
                            color: Qt.alpha(AbyssStyle.accent, 0.16)
                            MaterialSymbol {
                                anchors.centerIn: parent
                                text: "psychology_alt"
                                iconSize: 14
                                color: AbyssStyle.accent
                            }
                        }

                        StyledText {
                            id: clarificationQuestionText
                            objectName: "aquaClarificationQuestion"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: String(AquaMind.activeClarification?.question ?? "")
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                            font.pixelSize: Appearance.font.pixelSize.smallest
                            font.weight: Font.DemiBold
                            color: AbyssStyle.textColor
                        }

                        RippleButton {
                            id: clarificationDismissBtn
                            objectName: "aquaClarificationDismiss"
                            Layout.preferredWidth: 22
                            Layout.preferredHeight: 22
                            Layout.alignment: Qt.AlignTop
                            implicitWidth: 22
                            implicitHeight: 22
                            buttonRadius: 11
                            colBackground: Qt.alpha(AbyssStyle.textColor, 0.07)
                            colBackgroundHover: Qt.alpha(AbyssStyle.textColor, 0.15)
                            onClicked: AquaMind.dismissClarification()
                            StyledToolTip {
                                extraVisibleCondition: clarificationDismissBtn.buttonHovered
                                text: "Dismiss clarification"
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: "close"
                                iconSize: 13
                                color: Appearance.colors.colSubtext
                            }
                        }
                    }

                    // 1-3 Selectable Idea Options
                    ColumnLayout {
                        id: clarificationOptionsColumn
                        objectName: "aquaClarificationOptions"
                        Layout.fillWidth: true
                        spacing: 4

                        Repeater {
                            id: clarificationOptionsRepeater
                            model: Array.isArray(AquaMind.activeClarification?.options) ? AquaMind.activeClarification.options.slice(0, 3) : []

                            RippleButton {
                                id: clarOptBtn
                                required property var modelData
                                required property int index
                                objectName: "aquaClarificationOption-" + String(index)
                                Layout.fillWidth: true
                                readonly property bool hasDesc: String(modelData?.description ?? "").trim().length > 0
                                readonly property bool isRecommended: Boolean(modelData?.recommended)
                                Layout.preferredHeight: hasDesc ? 38 : 26
                                implicitHeight: hasDesc ? 38 : 26
                                height: implicitHeight
                                enabled: !AquaMind.busy
                                buttonRadius: 8
                                colBackground: isRecommended ? Qt.alpha(AbyssStyle.accent, 0.12) : Qt.alpha(AbyssStyle.textColor, 0.05)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
                                onClicked: AquaMind.selectClarificationOption(index)

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 8
                                    color: "transparent"
                                    border.width: 1
                                    border.color: clarOptBtn.buttonHovered || clarOptBtn.isRecommended ? Qt.alpha(AbyssStyle.accent, clarOptBtn.buttonHovered ? 0.5 : 0.3) : Qt.alpha(AbyssStyle.textColor, 0.09)
                                }

                                contentItem: RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 7
                                    anchors.rightMargin: 7
                                    spacing: 6

                                    Rectangle {
                                        Layout.preferredWidth: 18
                                        Layout.preferredHeight: 18
                                        implicitWidth: 18
                                        implicitHeight: 18
                                        radius: 5
                                        color: clarOptBtn.isRecommended ? AbyssStyle.accent : Qt.alpha(AbyssStyle.textColor, 0.12)
                                        StyledText {
                                            anchors.centerIn: parent
                                            text: String(clarOptBtn.index + 1)
                                            font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                            font.weight: Font.Bold
                                            color: clarOptBtn.isRecommended ? Appearance.colors.colOnPrimary : AbyssStyle.textColor
                                        }
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        spacing: 1

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 5

                                            StyledText {
                                                objectName: "aquaClarificationOptionLabel-" + String(clarOptBtn.index)
                                                Layout.fillWidth: true
                                                Layout.minimumWidth: 0
                                                text: String(clarOptBtn.modelData?.label ?? "")
                                                textFormat: Text.PlainText
                                                elide: Text.ElideRight
                                                font.pixelSize: Appearance.font.pixelSize.smallest
                                                font.weight: Font.DemiBold
                                                color: AbyssStyle.textColor
                                            }

                                            Rectangle {
                                                objectName: "aquaClarificationRecommended-" + String(clarOptBtn.index)
                                                visible: clarOptBtn.isRecommended
                                                Layout.preferredWidth: recLabel.implicitWidth + 8
                                                Layout.preferredHeight: 15
                                                implicitWidth: recLabel.implicitWidth + 8
                                                implicitHeight: 15
                                                radius: 7
                                                color: Qt.alpha(AbyssStyle.accent, 0.18)
                                                StyledText {
                                                    id: recLabel
                                                    anchors.centerIn: parent
                                                    text: "Recommended"
                                                    font.pixelSize: Appearance.font.pixelSize.smallest - 3
                                                    font.weight: Font.DemiBold
                                                    color: AbyssStyle.accent
                                                }
                                            }
                                        }

                                        StyledText {
                                            visible: clarOptBtn.hasDesc
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            text: String(clarOptBtn.modelData?.description ?? "")
                                            textFormat: Text.PlainText
                                            elide: Text.ElideRight
                                            font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                            color: Appearance.colors.colSubtext
                                        }
                                    }

                                    MaterialSymbol {
                                        text: "north_east"
                                        iconSize: 13
                                        color: clarOptBtn.buttonHovered ? AbyssStyle.accent : Appearance.colors.colSubtext
                                    }
                                }
                            }
                        }
                    }

                    // Custom Idea Input Box (1 custom chat input for user's own idea)
                    RowLayout {
                        id: clarificationCustomRow
                        objectName: "aquaClarificationCustomRow"
                        visible: AquaMind.activeClarification?.allowCustom !== false
                        Layout.fillWidth: true
                        Layout.preferredHeight: 28
                        spacing: 5

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            Layout.preferredHeight: 26
                            implicitHeight: 26
                            radius: 8
                            color: Qt.alpha(AbyssStyle.textColor, 0.05)
                            border.width: 1
                            border.color: clarificationCustomField.activeFocus ? Qt.alpha(AbyssStyle.accent, 0.48) : Qt.alpha(AbyssStyle.textColor, 0.10)

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 7
                                anchors.rightMargin: 6
                                spacing: 5

                                MaterialSymbol {
                                    text: "edit_note"
                                    iconSize: 15
                                    color: clarificationCustomField.activeFocus ? AbyssStyle.accent : Appearance.colors.colSubtext
                                }

                                MaterialTextField {
                                    id: clarificationCustomField
                                    objectName: "aquaClarificationCustomInput"
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    Layout.preferredHeight: 24
                                    implicitHeight: 24
                                    maximumLength: 600
                                    enableSettingsSearch: false
                                    placeholderText: String(AquaMind.activeClarification?.customPlaceholder ?? "Type your own idea or custom instructions...")
                                    font.pixelSize: Appearance.font.pixelSize.smallest
                                    background: Rectangle {
                                        color: "transparent"
                                        border.width: 0
                                    }
                                    onActiveFocusChanged: if (activeFocus)
                                        root.wantsKeyboardFocus = true
                                    onAccepted: {
                                        const val = text.trim();
                                        if (val.length > 0 && !AquaMind.busy) {
                                            text = "";
                                            AquaMind.submitClarificationCustom(val);
                                        }
                                    }
                                }
                            }
                        }

                        RippleButton {
                            id: clarificationCustomSubmitBtn
                            objectName: "aquaClarificationCustomSubmit"
                            Layout.preferredWidth: 26
                            Layout.preferredHeight: 26
                            implicitWidth: 26
                            implicitHeight: 26
                            buttonRadius: 13
                            enabled: clarificationCustomField.text.trim().length > 0 && !AquaMind.busy
                            colBackground: enabled ? AbyssStyle.accent : Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.lighter(AbyssStyle.accent, 1.08)
                            onClicked: {
                                const val = clarificationCustomField.text.trim();
                                if (val.length > 0) {
                                    clarificationCustomField.text = "";
                                    AquaMind.submitClarificationCustom(val);
                                }
                            }
                            StyledToolTip {
                                extraVisibleCondition: clarificationCustomSubmitBtn.buttonHovered
                                text: "Send custom idea"
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: "arrow_upward"
                                iconSize: 15
                                color: clarificationCustomSubmitBtn.enabled ? Appearance.colors.colOnPrimary : Appearance.colors.colSubtext
                            }
                        }
                    }
                }
            }

            // Interactive Slash Command Menu (Antigravity / Claude Desktop / ChatGPT style)
            Rectangle {
                id: slashCommandPopup
                objectName: "aquaSlashCommandMenu"
                visible: root.slashMenuOpen
                x: 0
                y: (profileBox.visible ? profileBox.height + controlsColumn.spacing : 0) + (clarificationCard.visible ? clarificationCard.height + controlsColumn.spacing : 0)
                width: content.width
                height: implicitHeight
                readonly property real maxSlashPopupHeight: Math.max(96, content.height - (thinkingCard.visible ? thinkingCard.implicitHeight + content.spacing : 0) - (profileBox.visible ? profileBox.implicitHeight + controlsColumn.spacing : 0) - (clarificationCard.visible ? clarificationCard.implicitHeight + controlsColumn.spacing : 0) - composer.implicitHeight - controlsColumn.spacing - 8)
                implicitHeight: Math.min(182, maxSlashPopupHeight, 26 + Math.min(5, Math.max(1, root.filteredSlashCommands.length)) * 30 + 8)
                radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
                color: Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.96)
                border.width: 1
                border.color: Qt.alpha(AbyssStyle.accent, 0.34)
                clip: true
                z: 30

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 6
                    spacing: 4

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 16
                        spacing: 5
                        MaterialSymbol {
                            text: "terminal"
                            iconSize: 13
                            color: AbyssStyle.accent
                        }
                        StyledText {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: root.slashQuery.length > 0 ? ("Matching /" + root.slashQuery + " (" + root.filteredSlashCommands.length + ")") : ("Slash Commands & Learned Skills (" + root.filteredSlashCommands.length + ")")
                            font.pixelSize: Appearance.font.pixelSize.smallest - 1
                            font.weight: Font.DemiBold
                            color: Appearance.colors.colSubtext
                            elide: Text.ElideRight
                        }
                        StyledText {
                            text: "↑↓ · Tab · Enter"
                            font.pixelSize: Appearance.font.pixelSize.smallest - 2
                            color: Appearance.colors.colSubtext
                        }
                    }

                    ListView {
                        id: slashMenuList
                        objectName: "aquaSlashCommandList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 2
                        model: root.filteredSlashCommands
                        boundsBehavior: Flickable.StopAtBounds

                        delegate: Rectangle {
                            id: slashItemRow
                            required property var modelData
                            required property int index
                            objectName: "aquaSlashCmd-" + String(modelData.name)
                            width: slashMenuList.width
                            height: 28
                            radius: 7
                            readonly property bool isCurrent: index === root.slashSelectedIndex
                            color: isCurrent ? Qt.alpha(AbyssStyle.accent, 0.22) : (slashItemMouse.containsMouse ? Qt.alpha(AbyssStyle.accent, 0.12) : "transparent")
                            border.width: isCurrent ? 1 : 0
                            border.color: isCurrent ? Qt.alpha(AbyssStyle.accent, 0.42) : "transparent"

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 7
                                anchors.rightMargin: 7
                                spacing: 6

                                MaterialSymbol {
                                    text: String(slashItemRow.modelData.icon ?? "terminal")
                                    iconSize: 15
                                    color: slashItemRow.isCurrent ? AbyssStyle.accent : Appearance.colors.colPrimary
                                }

                                StyledText {
                                    text: String(slashItemRow.modelData.cmd)
                                    font.pixelSize: Appearance.font.pixelSize.smallest
                                    font.weight: Font.DemiBold
                                    color: AbyssStyle.textColor
                                }

                                StyledText {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    text: String(slashItemRow.modelData.desc ?? "")
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    color: Appearance.colors.colSubtext
                                    elide: Text.ElideRight
                                }

                                Rectangle {
                                    Layout.preferredWidth: implicitWidth
                                    Layout.preferredHeight: 16
                                    implicitWidth: catBadgeText.implicitWidth + 8
                                    implicitHeight: 16
                                    radius: 8
                                    color: Qt.alpha(AbyssStyle.accent, 0.12)
                                    StyledText {
                                        id: catBadgeText
                                        anchors.centerIn: parent
                                        text: String(slashItemRow.modelData.category ?? "Agent")
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        color: AbyssStyle.accent
                                    }
                                }
                            }

                            MouseArea {
                                id: slashItemMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onEntered: root.slashSelectedIndex = slashItemRow.index
                                onClicked: root.selectSlashCommand(slashItemRow.modelData, true)
                            }
                        }
                    }
                }
            }

            Rectangle {
                id: composer
                objectName: "aquaChatComposer"
                visible: root.editing
                x: 0
                y: (profileBox.visible ? profileBox.height + controlsColumn.spacing : 0) + (clarificationCard.visible ? clarificationCard.height + controlsColumn.spacing : 0) + (slashCommandPopup.visible ? slashCommandPopup.height + controlsColumn.spacing : 0)
                width: content.width
                height: implicitHeight
                Layout.fillWidth: true
                Layout.preferredHeight: implicitHeight
                implicitHeight: composerContent.height + 10
                radius: Math.max(12, Math.min(18, root.edgeRadius - 4))
                color: root.voiceStageActive ? ColorUtils.transparentize(ColorUtils.mix(root.voiceLayer1Color, root.voiceContainerColor, 0.78), 0.08) : Qt.alpha(AbyssStyle.textColor, .05)
                border.width: 1
                border.color: chatImageDropArea.containsDrag ? AbyssStyle.accent : (root.voiceStageActive ? ColorUtils.transparentize(root.voiceAccentColor, 0.45) : (root.slashMenuOpen ? Qt.alpha(AbyssStyle.accent, 0.35) : Qt.alpha(AbyssStyle.textColor, .09)))
                Behavior on color {
                    ColorAnimation {
                        duration: 160
                        easing.type: Easing.OutCubic
                    }
                }
                Behavior on border.color {
                    ColorAnimation {
                        duration: 160
                        easing.type: Easing.OutCubic
                    }
                }
                DropArea {
                    id: chatImageDropArea
                    objectName: "aquaChatDropArea"
                    anchors.fill: parent
                    onDropped: drop => {
                        let handled = false;
                        if (drop.hasUrls && Array.isArray(drop.urls)) {
                            for (let i = 0; i < drop.urls.length; ++i) {
                                const u = String(drop.urls[i]);
                                if (AquaMind.attachImage(u) || AquaMind.attachFile(u))
                                    handled = true;
                            }
                        }
                        if (!handled && drop.hasText && drop.text) {
                            const lines = String(drop.text).split(/\r?\n/);
                            for (let j = 0; j < lines.length; ++j) {
                                if (AquaMind.attachImage(lines[j]) || AquaMind.attachFile(lines[j]))
                                    handled = true;
                            }
                        }
                        if (handled)
                            drop.accept(Qt.CopyAction);
                    }
                }
                Item {
                    id: composerContent
                    x: 6
                    y: 4
                    width: Math.max(0, parent.width - 12)
                    readonly property real imgPreviewOffset: (!root.voiceStageActive && attachedImagesStrip.visible) ? (attachedImagesStrip.height + 4) : 0
                    readonly property real filePreviewOffset: (!root.voiceStageActive && attachedFilesStrip.visible) ? (attachedFilesStrip.height + 4) : 0
                    readonly property real previewOffset: imgPreviewOffset + filePreviewOffset
                    readonly property real standardTextInputStageHeight: 40 + 2 + composerRow.height
                    height: root.voiceStageActive ? voiceStage.height : (previewOffset + message.implicitHeight + 2 + composerRow.height)
                    implicitHeight: height
                    Item {
                        id: attachedImagesStrip
                        objectName: "aquaChatImagePreviewRow"
                        visible: !root.voiceStageActive && Array.isArray(AquaMind.attachedImages) && AquaMind.attachedImages.length > 0
                        x: 0
                        y: 0
                        width: parent.width
                        height: visible ? 34 : 0
                        Row {
                            anchors.fill: parent
                            spacing: 6
                            Repeater {
                                model: AquaMind.attachedImages
                                delegate: Rectangle {
                                    id: imgChip
                                    required property var modelData
                                    required property int index
                                    objectName: "aquaAttachedImageCard-" + index
                                    width: Math.min(126, Math.max(76, imgNameText.implicitWidth + 54))
                                    height: 32
                                    radius: 8
                                    color: Qt.alpha(AbyssStyle.accent, 0.12)
                                    border.width: 1
                                    border.color: Qt.alpha(AbyssStyle.accent, 0.28)
                                    Rectangle {
                                        x: 4
                                        y: 4
                                        width: 24
                                        height: 24
                                        radius: 5
                                        clip: true
                                        color: Qt.alpha(AbyssStyle.textColor, 0.08)
                                        Image {
                                            anchors.fill: parent
                                            fillMode: Image.PreserveAspectCrop
                                            asynchronous: false
                                            sourceSize: Qt.size(48, 48)
                                            source: String(imgChip.modelData?.url ?? "")
                                        }
                                    }
                                    StyledText {
                                        id: imgNameText
                                        objectName: "aquaAttachedImageName-" + imgChip.index
                                        x: 32
                                        y: 0
                                        width: Math.max(20, imgChip.width - 54)
                                        height: parent.height
                                        text: String(imgChip.modelData?.name ?? "image.png")
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideMiddle
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                        color: AbyssStyle.textColor
                                    }
                                    RippleButton {
                                        id: removeImgBtn
                                        objectName: "aquaAttachedImageRemove-" + imgChip.index
                                        x: imgChip.width - 20
                                        y: 7
                                        width: 18
                                        height: 18
                                        implicitWidth: 18
                                        implicitHeight: 18
                                        buttonRadius: 9
                                        colBackground: Qt.alpha(AbyssStyle.textColor, 0.10)
                                        colBackgroundHover: Qt.alpha(Appearance.colors.colError, 0.24)
                                        onClicked: AquaMind.removeAttachedImage(imgChip.index)
                                        contentItem: Item {
                                            MaterialSymbol {
                                                anchors.centerIn: parent
                                                text: "close"
                                                iconSize: 12
                                                color: AbyssStyle.textColor
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Item {
                        id: attachedFilesStrip
                        objectName: "aquaChatFilePreviewRow"
                        visible: !root.voiceStageActive && Array.isArray(AquaMind.attachedFiles) && AquaMind.attachedFiles.length > 0
                        x: 0
                        y: composerContent.imgPreviewOffset
                        width: parent.width
                        height: visible ? 28 : 0
                        Row {
                            anchors.fill: parent
                            spacing: 6
                            Repeater {
                                model: AquaMind.attachedFiles
                                delegate: Rectangle {
                                    id: fileChip
                                    required property var modelData
                                    required property int index
                                    objectName: "aquaAttachedFileCard-" + index
                                    width: Math.min(136, Math.max(76, fileNameText.implicitWidth + 46))
                                    height: 26
                                    radius: 7
                                    color: Qt.alpha(AbyssStyle.accent, 0.12)
                                    border.width: 1
                                    border.color: Qt.alpha(AbyssStyle.accent, 0.28)
                                    MaterialSymbol {
                                        x: 6
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "description"
                                        iconSize: 14
                                        color: AbyssStyle.accent
                                    }
                                    StyledText {
                                        id: fileNameText
                                        objectName: "aquaAttachedFileName-" + fileChip.index
                                        x: 24
                                        y: 0
                                        width: Math.max(20, fileChip.width - 44)
                                        height: parent.height
                                        text: String(fileChip.modelData?.name ?? "file.txt")
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideMiddle
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                        color: AbyssStyle.textColor
                                    }
                                    RippleButton {
                                        id: removeFileBtn
                                        objectName: "aquaAttachedFileRemove-" + fileChip.index
                                        x: fileChip.width - 20
                                        y: 4
                                        width: 18
                                        height: 18
                                        implicitWidth: 18
                                        implicitHeight: 18
                                        buttonRadius: 9
                                        colBackground: Qt.alpha(AbyssStyle.textColor, 0.10)
                                        colBackgroundHover: Qt.alpha(Appearance.colors.colError, 0.24)
                                        onClicked: AquaMind.removeAttachedFile(fileChip.index)
                                        contentItem: Item {
                                            MaterialSymbol {
                                                anchors.centerIn: parent
                                                text: "close"
                                                iconSize: 12
                                                color: AbyssStyle.textColor
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    MaterialTextField {
                        id: message
                        objectName: "aquaChatInput"
                        visible: !root.voiceStageActive
                        x: 0
                        y: composerContent.previewOffset
                        width: parent.width
                        height: implicitHeight
                        maximumLength: 1200
                        wrapMode: TextInput.Wrap
                        enableSettingsSearch: false
                        echoMode: AquaMind.pendingSudoPassword ? TextInput.Password : TextInput.Normal
                        placeholderText: AquaMind.pendingSudoPassword ? "Enter sudo password (or type 'cancel')..." : AquaMind.voiceErrorNotice
                        Accessible.name: "Message"
                        background: Rectangle {
                            color: "transparent"
                            border.width: 0
                        }
                        onActiveFocusChanged: if (activeFocus)
                            root.wantsKeyboardFocus = true
                        TapHandler {
                            onPressedChanged: if (pressed) {
                                root.wantsKeyboardFocus = true;
                                if (!message.activeFocus)
                                    Qt.callLater(() => {
                                        if (root.editing && !root.voiceStageActive && message.visible && message.enabled)
                                            message.forceActiveFocus();
                                    });
                            }
                        }
                        onTextChanged: {
                            if (text.trim().length === 0 && AquaMind.pendingVoiceInputTurn) {
                                AquaMind.pendingVoiceInputTurn = false;
                            }
                            if (text.length > 0 && AquaMind.voiceErrorNotice.length > 0) {
                                AquaMind.voiceErrorNotice = "";
                            }
                            if (text.startsWith("/")) {
                                if (!/^\/[^\s]*\s/.test(text)) {
                                    root.slashMenuDismissed = false;
                                }
                                root.slashSelectedIndex = 0;
                            } else {
                                root.slashMenuDismissed = false;
                                root.slashSelectedIndex = 0;
                            }
                        }
                        onAccepted: {
                            if (root.slashMenuOpen && root.filteredSlashCommands.length > 0) {
                                const idx = Math.max(0, Math.min(root.filteredSlashCommands.length - 1, root.slashSelectedIndex));
                                const chosen = root.filteredSlashCommands[idx];
                                if (chosen && (chosen.needsArgs || root.slashRawInput.trim().toLowerCase() !== chosen.cmd)) {
                                    root.selectSlashCommand(chosen, !chosen.needsArgs);
                                    return;
                                }
                            }
                            root.submit();
                        }
                        Keys.onDownPressed: event => {
                            if (root.slashMenuOpen && root.filteredSlashCommands.length > 0) {
                                event.accepted = true;
                                root.slashSelectedIndex = (root.slashSelectedIndex + 1) % root.filteredSlashCommands.length;
                                slashMenuList.positionViewAtIndex(root.slashSelectedIndex, ListView.Contain);
                                return;
                            }
                            if (root.promptHistoryIndex >= 0 && root.userPromptHistory.length > 0 && (!message.text.includes("\n") || message.cursorPosition >= message.text.length)) {
                                event.accepted = true;
                                if (root.promptHistoryIndex < root.userPromptHistory.length - 1) {
                                    root.promptHistoryIndex += 1;
                                    message.text = String(root.userPromptHistory[root.promptHistoryIndex] ?? "");
                                } else {
                                    root.promptHistoryIndex = -1;
                                    message.text = root.promptHistoryDraft;
                                }
                                message.cursorPosition = message.text.length;
                            }
                        }
                        Keys.onUpPressed: event => {
                            if (root.slashMenuOpen && root.filteredSlashCommands.length > 0) {
                                event.accepted = true;
                                root.slashSelectedIndex = (root.slashSelectedIndex - 1 + root.filteredSlashCommands.length) % root.filteredSlashCommands.length;
                                slashMenuList.positionViewAtIndex(root.slashSelectedIndex, ListView.Contain);
                                return;
                            }
                            if (root.userPromptHistory.length > 0 && (!message.text.includes("\n") || message.cursorPosition === 0 || root.promptHistoryIndex >= 0)) {
                                event.accepted = true;
                                if (root.promptHistoryIndex < 0) {
                                    root.promptHistoryDraft = message.text;
                                    root.promptHistoryIndex = root.userPromptHistory.length - 1;
                                } else if (root.promptHistoryIndex > 0) {
                                    root.promptHistoryIndex -= 1;
                                }
                                message.text = String(root.userPromptHistory[root.promptHistoryIndex] ?? "");
                                message.cursorPosition = message.text.length;
                            }
                        }
                        Keys.onTabPressed: event => {
                            if (root.slashMenuOpen && root.filteredSlashCommands.length > 0) {
                                event.accepted = true;
                                const idx = Math.max(0, Math.min(root.filteredSlashCommands.length - 1, root.slashSelectedIndex));
                                root.selectSlashCommand(root.filteredSlashCommands[idx], false);
                            }
                        }
                        Keys.onPressed: event => {
                            if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C && String(message.selectedText ?? "").length === 0 && root.copyActiveSelection()) {
                                event.accepted = true;
                                return;
                            }
                            if (event.key === Qt.Key_PageUp) {
                                if (root.scrollByPage(-1))
                                    event.accepted = true;
                                return;
                            }
                            if (event.key === Qt.Key_PageDown) {
                                if (root.scrollByPage(1))
                                    event.accepted = true;
                                return;
                            }
                            if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ShiftModifier)) {
                                const pos = Math.max(0, message.cursorPosition);
                                message.text = message.text.slice(0, pos) + "\n" + message.text.slice(pos);
                                message.cursorPosition = pos + 1;
                                event.accepted = true;
                                return;
                            }
                            if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_V) {
                                let clipVal = "";
                                try {
                                    if (typeof Quickshell !== "undefined" && Quickshell && "clipboardText" in Quickshell)
                                        clipVal = String(Quickshell.clipboardText ?? "").trim();
                                } catch (e) {}
                                const lines = clipVal.split(/\r?\n/).map(l => l.trim().replace(/^['"`]+|['"`]+$/g, "").trim()).filter(l => l.length > 0 && !l.startsWith("#"));
                                if (lines.length > 0 && lines.length <= 4 && lines.every(l => /^(?:\/|~\/|file:\/\/).+\.(?:png|jpe?g|webp|gif|bmp|svg|pdf|docx|xlsx|csv|md|txt|json|py|js|ts|qml|rs)$/i.test(l))) {
                                    let attachedAny = false;
                                    for (const ln of lines) {
                                        if (AquaMind.attachFile(ln))
                                            attachedAny = true;
                                    }
                                    if (attachedAny) {
                                        event.accepted = true;
                                        return;
                                    }
                                }
                            }
                            if (clarificationCard.visible && message.text.length === 0 && !(event.modifiers & (Qt.ControlModifier | Qt.AltModifier))) {
                                const opts = Array.isArray(AquaMind.activeClarification?.options) ? AquaMind.activeClarification.options : [];
                                const numIdx = event.key - Qt.Key_1;
                                if (numIdx >= 0 && numIdx < Math.min(9, opts.length)) {
                                    AquaMind.selectClarificationOption(numIdx);
                                    event.accepted = true;
                                    return;
                                }
                            }
                        }
                        Keys.onEscapePressed: event => {
                            if (root.handleEscape())
                                event.accepted = true;
                        }
                    }
                    Item {
                        id: composerRow
                        objectName: "aquaChatComposerRow"
                        visible: !root.voiceStageActive
                        x: 0
                        y: message.y + message.height + 2
                        width: parent.width
                        height: 32
                        property real spacing: 6
                        RippleButton {
                            id: attachImageBtn
                            objectName: "aquaChatAttachImage"
                            x: 0
                            y: Math.round((composerRow.height - height) / 2)
                            width: 30
                            height: 30
                            implicitWidth: 30
                            implicitHeight: 30
                            enabled: !AquaMind.busy
                            buttonRadius: 15
                            colBackground: (AquaMind.attachedImages.length > 0 || AquaMind.attachedFiles.length > 0) ? Qt.alpha(AbyssStyle.accent, 0.20) : Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                            onClicked: AquaMind.pickAndAttachFile()
                            StyledToolTip {
                                extraVisibleCondition: attachImageBtn.buttonHovered
                                text: "Attach image or file (PNG, JPG, PDF, DOCX, XLSX, CSV, Code, Text)"
                            }
                            contentItem: Item {
                                MaterialSymbol {
                                    anchors.centerIn: parent
                                    text: "attach_file"
                                    iconSize: 16
                                    color: (AquaMind.attachedImages.length > 0 || AquaMind.attachedFiles.length > 0) ? AbyssStyle.accent : Appearance.colors.colPrimary
                                }
                            }
                        }
                        RippleButton {
                            id: modelSelectorBtn
                            objectName: "aquaModelSelector"
                            x: attachImageBtn.width + composerRow.spacing
                            y: Math.round((composerRow.height - height) / 2)
                            width: 30
                            height: 30
                            implicitWidth: 30
                            implicitHeight: 30
                            enabled: !AquaMind.busy && AquaMind.selectableModels.length > 0
                            toggled: root.profileStage === 2
                            buttonRadius: 15
                            colBackground: root.profileStage === 2 ? Qt.alpha(AbyssStyle.accent, 0.22) : Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                            onClicked: {
                                if (root.profileStage === 2) {
                                    root.profileStage = 0;
                                    Qt.callLater(() => {
                                        if (root.editing)
                                            message.forceActiveFocus();
                                    });
                                } else {
                                    if (AquaMind.liveModelAutoDetectAllowed)
                                        AquaMind.refreshAvailableModels(false);
                                    root.profileStage = 2;
                                }
                            }
                            Keys.onEscapePressed: event => {
                                event.accepted = true;
                                root.closePopup();
                            }
                            StyledToolTip {
                                objectName: "aquaModelSelectorToolTip"
                                extraVisibleCondition: modelSelectorBtn.buttonHovered && root.profileStage !== 2
                                text: AquaMind.compactModelTooltipLabel
                            }
                            contentItem: Item {
                                MaterialSymbol {
                                    anchors.centerIn: parent
                                    text: "smart_toy"
                                    iconSize: 16
                                    color: root.profileStage === 2 ? AbyssStyle.accent : Appearance.colors.colPrimary
                                }
                            }
                        }
                        RippleButton {
                            id: effortSelectorBtn
                            objectName: "aquaModelEffort"
                            visible: AquaMind.thinkingSupported
                            TextMetrics {
                                id: effortTextMetrics
                                font: effortLabel.font
                                text: AquaMind.thinkingEffortShortLabel
                            }
                            readonly property real targetWidth: Math.max(54, Math.min(82, Math.ceil(effortTextMetrics.advanceWidth) + 32))
                            x: modelSelectorBtn.x + modelSelectorBtn.width + composerRow.spacing
                            y: Math.round((composerRow.height - height) / 2)
                            width: targetWidth
                            height: 30
                            implicitWidth: targetWidth
                            implicitHeight: 30
                            enabled: !AquaMind.busy && AquaMind.thinkingSupported
                            toggled: root.profileStage === 1
                            buttonRadius: 15
                            colBackground: root.profileStage === 1 ? Qt.alpha(AbyssStyle.accent, 0.22) : Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                            onClicked: {
                                if (root.profileStage === 1) {
                                    root.profileStage = 0;
                                    Qt.callLater(() => {
                                        if (root.editing)
                                            message.forceActiveFocus();
                                    });
                                } else {
                                    root.profileStage = 1;
                                }
                            }
                            Keys.onEscapePressed: event => {
                                event.accepted = true;
                                root.closePopup();
                            }
                            contentItem: Item {
                                Row {
                                    id: effortBtnRow
                                    objectName: "aquaModelEffortRow"
                                    anchors.centerIn: parent
                                    spacing: 4
                                    MaterialSymbol {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: AquaMind.effectiveThinkingEffort === "instant" || AquaMind.effectiveThinkingEffort === "off" ? "bolt" : (AquaMind.effectiveThinkingEffort === "auto" || AquaMind.effectiveThinkingEffort === "xhigh" || AquaMind.effectiveThinkingEffort === "max") ? "auto_awesome" : "psychology"
                                        iconSize: 15
                                        color: root.profileStage === 1 ? AbyssStyle.accent : Appearance.colors.colPrimary
                                    }
                                    StyledText {
                                        id: effortLabel
                                        objectName: "aquaModelEffortLabel"
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: AquaMind.thinkingEffortShortLabel
                                        verticalAlignment: Text.AlignVCenter
                                        font.pixelSize: Appearance.font.pixelSize.smallest
                                        font.weight: Font.Medium
                                        color: root.profileStage === 1 ? AbyssStyle.accent : Appearance.colors.colOnLayer1
                                    }
                                }
                            }
                        }
                        RippleButton {
                            id: voiceBtn
                            objectName: "aquaChatVoiceButton"
                            x: Math.max(0, sendBtn.x - width - 4)
                            y: Math.round((composerRow.height - height) / 2)
                            width: 28
                            height: 30
                            implicitWidth: 28
                            implicitHeight: 30
                            enabled: !AquaMind.busy
                            toggled: root.voiceStageActive
                            buttonRadius: 14
                            colBackground: root.voiceStageActive ? ColorUtils.transparentize(root.voiceContainerColor, 0.15) : Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                            colBackgroundToggled: ColorUtils.transparentize(root.voiceContainerColor, 0.15)
                            colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                            onClicked: AquaMind.openVoiceMode(null, !root.embeddedMode)
                            Item {
                                objectName: "aquaVoiceMicButton"
                                anchors.fill: parent
                                visible: voiceBtn.visible
                                function clicked(): void {
                                    voiceBtn.clicked();
                                }
                            }
                            StyledToolTip {
                                extraVisibleCondition: voiceBtn.buttonHovered
                                text: "Voice"
                            }
                            contentItem: Item {
                                MaterialSymbol {
                                    anchors.centerIn: parent
                                    text: root.voiceStageActive ? "graphic_eq" : "mic"
                                    iconSize: 16
                                    color: root.voiceStageActive ? root.voiceAccentColor : Appearance.colors.colPrimary
                                }
                            }
                        }
                        RippleButton {
                            id: sendBtn
                            objectName: "aquaChatSend"
                            x: Math.max(0, composerRow.width - width)
                            y: Math.round((composerRow.height - height) / 2)
                            width: 30
                            height: 30
                            implicitWidth: 30
                            implicitHeight: 30
                            enabled: (!AquaMind.busy || AquaMind.canInterruptWithPrompt(message.text)) && (message.text.trim().length > 0 || AquaMind.attachedImages.length > 0 || AquaMind.attachedFiles.length > 0)
                            buttonRadius: 15
                            onClicked: root.submit()
                            contentItem: Item {
                                MaterialSymbol {
                                    anchors.centerIn: parent
                                    text: "arrow_upward"
                                    iconSize: 18
                                    color: Appearance.colors.colPrimary
                                }
                            }
                        }
                    }

                    // -----------------------------------------------------------------
                    // VOICE INPUT STAGE (replaces text input & toolbar row)
                    // -----------------------------------------------------------------
                    Item {
                        id: voiceStage
                        objectName: "aquaVoiceStage"
                        visible: root.voiceStageActive
                        x: 0
                        y: 0
                        width: parent.width
                        height: visible ? composerContent.standardTextInputStageHeight : 0
                        implicitHeight: height
                        clip: true
                        readonly property color voiceAccentColor: root.voiceAccentColor
                        readonly property color voiceContainerColor: root.voiceContainerColor
                        readonly property color voiceOnAccentColor: root.voiceOnAccentColor
                        readonly property color voiceOnContainerColor: root.voiceOnContainerColor

                        // Ambient radial/linear glow backdrop inside the voice stage
                        Rectangle {
                            id: voiceStageBackdrop
                            objectName: "aquaVoiceStageBackdrop"
                            anchors.fill: parent
                            radius: Math.max(10, composer.radius - 4)
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop {
                                    position: 0.0
                                    color: ColorUtils.transparentize(root.voiceContainerColor, 0.55)
                                }
                                GradientStop {
                                    position: 0.52
                                    color: ColorUtils.transparentize(root.voiceLayer1Color, 0.25)
                                }
                                GradientStop {
                                    position: 1.0
                                    color: ColorUtils.transparentize(Appearance.colors.colSecondaryContainer, 0.65)
                                }
                            }
                        }

                        // Top Header Row: Status Badge + Live Elapsed Timer
                        Item {
                            id: voiceStageHeader
                            x: 4
                            y: 1
                            width: Math.max(0, parent.width - 8)
                            height: 17

                            Rectangle {
                                id: statusBadgePill
                                objectName: "aquaVoiceStatusPill"
                                x: 0
                                y: 0
                                height: 17
                                width: Math.max(44, Math.min(parent.width - timerBadgePill.width - 6, statusBadgeRow.implicitWidth + 12))
                                radius: 8.5
                                clip: true
                                color: ColorUtils.transparentize(root.voiceContainerColor, 0.35)
                                border.width: 1
                                border.color: ColorUtils.transparentize(root.voiceAccentColor, 0.55)

                                Row {
                                    id: statusBadgeRow
                                    x: 6
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 5

                                    Rectangle {
                                        id: statusPulseDot
                                        objectName: "aquaVoiceStatusDot"
                                        width: 6
                                        height: 6
                                        radius: 3
                                        anchors.verticalCenter: parent.verticalCenter
                                        color: root.voiceAccentColor
                                        opacity: (root.hasVoiceAudioInput || AquaMind.voiceTranscribing) ? (0.55 + 0.45 * Math.abs(Math.sin(root.voiceWavePhase * 1.4))) : 0.78
                                    }

                                    StyledText {
                                        id: voiceStatusLabel
                                        objectName: "aquaVoiceStatusLabel"
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: Math.max(24, Math.min(180, voiceStageHeader.width - timerBadgePill.width - 30, implicitWidth))
                                        text: AquaMind.voiceTranscribing ? "Transcribing..." : "Listening"
                                        elide: Text.ElideRight
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                        font.weight: Font.DemiBold
                                        color: root.voiceOnContainerColor
                                    }
                                }
                            }

                            Rectangle {
                                id: timerBadgePill
                                objectName: "aquaVoiceTimerPill"
                                x: Math.max(0, parent.width - width)
                                y: 0
                                height: 17
                                width: timerBadgeRow.implicitWidth + 12
                                radius: 8.5
                                color: ColorUtils.transparentize(root.voiceLayer2Color, 0.15)
                                border.width: 1
                                border.color: ColorUtils.transparentize(Appearance.colors.colOutlineVariant, 0.45)

                                Row {
                                    id: timerBadgeRow
                                    anchors.centerIn: parent
                                    spacing: 3

                                    MaterialSymbol {
                                        id: timerBadgeIcon
                                        objectName: "aquaVoiceTimerIcon"
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: AquaMind.voiceTranscribing ? "auto_awesome" : "graphic_eq"
                                        iconSize: 11
                                        color: root.voiceAccentColor
                                    }

                                    StyledText {
                                        id: voiceTimerLabel
                                        objectName: "aquaVoiceTimerLabel"
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: root.formatVoiceTimer(AquaMind.voiceElapsedSeconds)
                                        font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                        font.family: Appearance.font.family.monospace
                                        font.weight: Font.DemiBold
                                        color: Appearance.colors.colOnLayer2
                                    }
                                }
                            }
                        }

                        // Center Visualizer Stage: Pulsing Voice Core Orb + 32-Bar Frequency Equalizer
                        Item {
                            id: voiceVisualizerStage
                            x: 4
                            y: 20
                            width: Math.max(0, parent.width - 8)
                            height: 26

                            Item {
                                id: voiceCoreOrb
                                objectName: "aquaVoiceCoreOrb"
                                x: 2
                                anchors.verticalCenter: parent.verticalCenter
                                width: 26
                                height: 26

                                // Outer sonar ring
                                Rectangle {
                                    id: orbOuterSonar
                                    objectName: "aquaVoiceOrbOuterSonar"
                                    anchors.centerIn: parent
                                    readonly property real pulseScale: (root.hasVoiceAudioInput || AquaMind.voiceTranscribing) ? (0.82 + 0.18 * (0.5 + 0.5 * Math.sin(root.voiceWavePhase * 1.35))) : 0.88
                                    width: Math.round(26 * pulseScale)
                                    height: width
                                    radius: width / 2
                                    color: ColorUtils.transparentize(root.voiceContainerColor, 0.55)
                                    border.width: 1
                                    border.color: ColorUtils.transparentize(root.voiceAccentColor, 0.58)
                                }

                                // Mid harmonic ring
                                Rectangle {
                                    id: orbMidRing
                                    objectName: "aquaVoiceOrbMidRing"
                                    anchors.centerIn: parent
                                    readonly property real midScale: (root.hasVoiceAudioInput || AquaMind.voiceTranscribing) ? (0.72 + 0.14 * (0.5 + 0.5 * Math.cos(root.voiceWavePhase * 2.1))) : 0.76
                                    width: Math.round(22 * midScale)
                                    height: width
                                    radius: width / 2
                                    color: ColorUtils.transparentize(root.voiceContainerColor, 0.30)
                                    border.width: 1
                                    border.color: ColorUtils.transparentize(root.voiceAccentColor, 0.35)
                                }

                                // Inner core orb
                                Rectangle {
                                    id: orbCoreCircle
                                    objectName: "aquaVoiceOrbCoreCircle"
                                    anchors.centerIn: parent
                                    width: 16
                                    height: 16
                                    radius: 8
                                    readonly property color coreTopColor: root.voiceAccentColor
                                    readonly property color coreBottomColor: ColorUtils.mix(root.voiceAccentColor, root.voiceContainerColor, 0.65)
                                    gradient: Gradient {
                                        GradientStop {
                                            position: 0.0
                                            color: orbCoreCircle.coreTopColor
                                        }
                                        GradientStop {
                                            position: 1.0
                                            color: orbCoreCircle.coreBottomColor
                                        }
                                    }

                                    MaterialSymbol {
                                        id: orbCoreIcon
                                        objectName: "aquaVoiceOrbCoreIcon"
                                        anchors.centerIn: parent
                                        text: AquaMind.voiceTranscribing ? "auto_awesome" : "mic"
                                        iconSize: 11
                                        color: root.voiceOnAccentColor
                                    }
                                }
                            }

                            Item {
                                id: voiceWaveform
                                objectName: "aquaVoiceWaveform"
                                x: voiceCoreOrb.x + voiceCoreOrb.width + 8
                                y: 1
                                width: Math.max(80, parent.width - x - 2)
                                height: 24
                                clip: true
                                property int barCount: 32

                                // Subtle center axis hairline
                                Rectangle {
                                    x: 0
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: parent.width
                                    height: 1
                                    color: ColorUtils.transparentize(Appearance.colors.colOutlineVariant, 0.55)
                                }

                                Row {
                                    id: waveBarRow
                                    anchors.centerIn: parent
                                    spacing: Math.max(1.5, Math.min(4, (voiceWaveform.width - voiceWaveform.barCount * 2.5) / Math.max(1, voiceWaveform.barCount - 1)))

                                    Repeater {
                                        model: voiceWaveform.barCount
                                        delegate: Rectangle {
                                            id: waveBar
                                            required property int index
                                            objectName: "aquaVoiceWaveBar-" + index
                                            readonly property real barFraction: index / Math.max(1, voiceWaveform.barCount - 1)
                                            readonly property real targetBarHeight: root.voiceBarHeight(index, voiceWaveform.barCount, root.voiceWavePhase, AquaMind.voiceRecording, AquaMind.voiceTranscribing, root.activeVoiceLevel, AquaMind.voiceInputHasBands ? AquaMind.voiceInputBands : null)
                                            readonly property color topStopColor: ColorUtils.mix(root.voiceAccentColor, root.voiceOnContainerColor, 0.72 - 0.35 * waveBar.barFraction)
                                            readonly property color bottomStopColor: ColorUtils.transparentize(ColorUtils.mix(root.voiceAccentColor, root.voiceContainerColor, 0.55), 0.18)
                                            width: Math.max(2.0, Math.min(5.0, (voiceWaveform.width - (voiceWaveform.barCount - 1) * waveBarRow.spacing) / voiceWaveform.barCount))
                                            height: targetBarHeight
                                            Behavior on height {
                                                enabled: root.visible
                                                NumberAnimation {
                                                    duration: 24
                                                    easing.type: Easing.OutQuad
                                                }
                                            }
                                            anchors.verticalCenter: parent.verticalCenter
                                            radius: width / 2
                                            gradient: Gradient {
                                                GradientStop {
                                                    position: 0.0
                                                    color: waveBar.topStopColor
                                                }
                                                GradientStop {
                                                    position: 1.0
                                                    color: waveBar.bottomStopColor
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // Bottom Action Deck: Cancel · Auto-Send Toggle · Retry · Finish/Send CTA
                        Item {
                            id: voiceActionDeck
                            objectName: "aquaVoiceActionDeck"
                            x: 4
                            y: 48
                            width: Math.max(0, parent.width - 8)
                            height: 24
                            readonly property bool compactDeck: width < 316
                            readonly property bool ultraCompactDeck: width < 260
                            readonly property real btnSpacing: compactDeck ? 4 : 6

                            RippleButton {
                                id: voiceCancelBtn
                                objectName: "aquaVoiceCancelButton"
                                x: 0
                                y: 0
                                height: 24
                                width: voiceActionDeck.ultraCompactDeck ? 24 : (cancelBtnRow.implicitWidth + (voiceActionDeck.compactDeck ? 10 : 14))
                                implicitWidth: width
                                implicitHeight: 24
                                focusPolicy: Qt.NoFocus
                                Keys.forwardTo: [root]
                                buttonRadius: 12
                                colBackground: ColorUtils.transparentize(Appearance.colors.colErrorContainer ?? root.voiceLayer2Color, 0.45)
                                colBackgroundHover: ColorUtils.transparentize(Appearance.colors.colErrorContainer ?? root.voiceLayer2Color, 0.25)
                                onClicked: AquaMind.cancelVoiceMode()
                                StyledToolTip {
                                    extraVisibleCondition: voiceCancelBtn.buttonHovered
                                    text: "Discard voice capture and return to text input (Esc)"
                                }
                                contentItem: Item {
                                    Row {
                                        id: cancelBtnRow
                                        anchors.centerIn: parent
                                        spacing: 3
                                        MaterialSymbol {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: "close"
                                            iconSize: 13
                                            color: Appearance.colors.colError
                                        }
                                        StyledText {
                                            visible: !voiceActionDeck.ultraCompactDeck
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: "Cancel"
                                            font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                            font.weight: Font.Medium
                                            color: Appearance.colors.colOnErrorContainer ?? Appearance.colors.colError
                                        }
                                    }
                                }
                            }

                            RippleButton {
                                id: voiceAutoSendBtn
                                objectName: "aquaVoiceAutoSendToggle"
                                x: voiceCancelBtn.x + voiceCancelBtn.width + voiceActionDeck.btnSpacing
                                y: 0
                                height: 24
                                width: autoSendBtnRow.implicitWidth + (voiceActionDeck.compactDeck ? 10 : 14)
                                implicitWidth: width
                                implicitHeight: 24
                                focusPolicy: Qt.NoFocus
                                Keys.forwardTo: [root]
                                toggled: AquaMind.voiceAutoSend
                                buttonRadius: 12
                                colBackground: AquaMind.voiceAutoSend ? ColorUtils.transparentize(Appearance.colors.colPrimaryContainer, 0.15) : root.voiceLayer2Color
                                colBackgroundHover: AquaMind.voiceAutoSend ? Appearance.colors.colPrimaryContainerHover : Appearance.colors.colLayer2Hover
                                colBackgroundToggled: ColorUtils.transparentize(Appearance.colors.colPrimaryContainer, 0.15)
                                colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                                onClicked: {
                                    AquaMind.setVoiceAutoSend(!AquaMind.voiceAutoSend);
                                    if (root.voiceStageActive)
                                        root.forceActiveFocus();
                                }
                                StyledToolTip {
                                    extraVisibleCondition: voiceAutoSendBtn.buttonHovered
                                    text: AquaMind.voiceAutoSend ? "Mode: Auto-send prompt after transcription (click to insert into composer instead)" : "Mode: Insert transcript into text input for review before sending"
                                }
                                contentItem: Item {
                                    Row {
                                        id: autoSendBtnRow
                                        anchors.centerIn: parent
                                        spacing: 3
                                        MaterialSymbol {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: AquaMind.voiceAutoSend ? "bolt" : "edit_note"
                                            iconSize: 13
                                            color: AquaMind.voiceAutoSend ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
                                        }
                                        StyledText {
                                            id: voiceAutoSendLabel
                                            objectName: "aquaVoiceAutoSendLabel"
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: voiceActionDeck.ultraCompactDeck ? (AquaMind.voiceAutoSend ? "Auto" : "Input") : (AquaMind.voiceAutoSend ? "Auto-send" : "To input")
                                            font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                            font.weight: Font.Medium
                                            color: AquaMind.voiceAutoSend ? Appearance.colors.colOnPrimaryContainer : Appearance.colors.colSubtext
                                        }
                                    }
                                }
                            }

                            RippleButton {
                                id: voiceRetryBtn
                                objectName: "aquaVoiceRetryButton"
                                x: voiceAutoSendBtn.x + voiceAutoSendBtn.width + voiceActionDeck.btnSpacing
                                y: 0
                                width: 24
                                height: 24
                                implicitWidth: 24
                                implicitHeight: 24
                                focusPolicy: Qt.NoFocus
                                Keys.forwardTo: [root]
                                enabled: !AquaMind.voiceTranscribing
                                opacity: enabled ? 1.0 : 0.55
                                buttonRadius: 12
                                colBackground: root.voiceLayer2Color
                                colBackgroundHover: Appearance.colors.colLayer2Hover
                                buttonColor: enabled ? (buttonHovered ? colBackgroundHover : colBackground) : ColorUtils.transparentize(colBackground, 0.25)
                                onClicked: {
                                    AquaMind.restartVoiceMode(!root.embeddedMode);
                                    if (root.voiceStageActive)
                                        root.forceActiveFocus();
                                }
                                StyledToolTip {
                                    extraVisibleCondition: voiceRetryBtn.buttonHovered
                                    text: "Restart voice capture"
                                }
                                contentItem: Item {
                                    MaterialSymbol {
                                        anchors.centerIn: parent
                                        text: "refresh"
                                        iconSize: 14
                                        color: Appearance.colors.colOnLayer2
                                    }
                                }
                            }

                            RippleButton {
                                id: voiceDoneBtn
                                objectName: "aquaVoiceDoneButton"
                                readonly property real minLeftX: voiceRetryBtn.x + voiceRetryBtn.width + voiceActionDeck.btnSpacing
                                readonly property real availableRightWidth: Math.max(44, parent.width - minLeftX)
                                x: Math.max(minLeftX, parent.width - width)
                                y: 0
                                height: 24
                                width: Math.min(availableRightWidth, doneBtnRow.implicitWidth + (voiceActionDeck.compactDeck ? 10 : 16))
                                implicitWidth: width
                                implicitHeight: 24
                                focusPolicy: Qt.NoFocus
                                Keys.forwardTo: [root]
                                enabled: !AquaMind.voiceTranscribing
                                opacity: enabled ? 1.0 : 0.85
                                buttonRadius: 12
                                colBackground: AquaMind.voiceTranscribing ? ColorUtils.transparentize(root.voiceContainerColor, 0.25) : root.voiceAccentColor
                                colBackgroundHover: AquaMind.voiceTranscribing ? root.voiceContainerColor : Appearance.colors.colPrimaryHover
                                buttonColor: enabled ? (buttonHovered ? colBackgroundHover : colBackground) : colBackground
                                onClicked: {
                                    AquaMind.finishVoiceMode(AquaMind.voiceAutoSend);
                                    if (root.voiceStageActive)
                                        root.forceActiveFocus();
                                }
                                StyledToolTip {
                                    extraVisibleCondition: voiceDoneBtn.buttonHovered
                                    text: AquaMind.voiceAutoSend ? "Finish recording, transcribe, and send immediately (Enter)" : "Finish recording and insert transcript into composer (Enter)"
                                }
                                contentItem: Item {
                                    clip: true
                                    Row {
                                        id: doneBtnRow
                                        anchors.centerIn: parent
                                        spacing: voiceActionDeck.compactDeck ? 3 : 4
                                        MaterialSymbol {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: AquaMind.voiceTranscribing ? "hourglass_top" : (AquaMind.voiceAutoSend ? "arrow_upward" : "check")
                                            iconSize: 13
                                            color: AquaMind.voiceTranscribing ? root.voiceOnContainerColor : root.voiceOnAccentColor
                                        }
                                        StyledText {
                                            id: voiceDoneLabel
                                            objectName: "aquaVoiceDoneLabel"
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: AquaMind.voiceTranscribing ? (voiceActionDeck.ultraCompactDeck ? "..." : "Working...") : (voiceActionDeck.ultraCompactDeck ? (AquaMind.voiceAutoSend ? "Send" : "Done") : (AquaMind.voiceAutoSend ? "Finish & Send" : "Transcribe"))
                                            font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                            font.weight: Font.Bold
                                            color: AquaMind.voiceTranscribing ? root.voiceOnContainerColor : root.voiceOnAccentColor
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    // Draggable edge resize handle for Chatbox Right Swarm Sidebar
    Item {
        id: swarmRightResizeHandle
        objectName: "aquaSwarmSidebarResizeHandle"
        visible: swarmPaneLoader.visible && !root.swarmOverlayInNarrowEmbedded
        x: swarmPaneLoader.x - 10
        y: swarmPaneLoader.y
        width: 10
        height: swarmPaneLoader.height
        z: 15
        readonly property bool dragging: swarmResizeMouse.pressed
        readonly property bool hovered: swarmResizeMouse.containsMouse

        Rectangle {
            anchors.centerIn: parent
            width: parent.dragging || parent.hovered ? 3 : 2
            height: parent.dragging ? Math.max(36, parent.height * 0.55) : (parent.hovered ? Math.max(28, parent.height * 0.35) : 24)
            radius: 1.5
            color: parent.dragging ? AbyssStyle.accent : (parent.hovered ? Qt.alpha(AbyssStyle.accent, 0.72) : Qt.alpha(AbyssStyle.textColor, 0.16))
        }

        MouseArea {
            id: swarmResizeMouse
            objectName: "aquaSwarmSidebarResizeMouse"
            anchors.fill: parent
            hoverEnabled: true
            preventStealing: true
            cursorShape: Qt.SplitHCursor
            property real pressStartSceneX: 0
            property real pressStartWidth: 320
            onPressed: mouse => {
                const gp = swarmResizeMouse.mapToItem(null, mouse.x, mouse.y);
                pressStartSceneX = gp.x;
                pressStartWidth = root.swarmSidebarTargetWidth;
            }
            onPositionChanged: mouse => {
                if (!pressed)
                    return;
                const gp = swarmResizeMouse.mapToItem(null, mouse.x, mouse.y);
                const delta = pressStartSceneX - gp.x;
                root.resizeSwarmSidebarTo(pressStartWidth + delta);
            }
            onDoubleClicked: root.resizeSwarmSidebarTo(320)
        }
    }
    Loader {
        id: swarmPaneLoader
        objectName: "aquaSwarmRightSidebar"
        x: root.swarmOverlayInNarrowEmbedded ? 12 : (parent.width - 12 - root.swarmSidebarTargetWidth)
        y: root.bodyTop
        width: root.swarmOverlayInNarrowEmbedded ? (parent.width - 24) : root.swarmSidebarTargetWidth
        height: Math.max(0, parent.height - root.bodyTop - 12)
        clip: true
        active: root.editing && (root.swarmOpen || root.swarmExpandProgress > 0.001)
        visible: active
        sourceComponent: Component {
            AquaSwarmPane {
                actor: root.actor
                embeddedMode: root.embeddedMode
                onCloseRequested: root.swarmOpen = false
                onPrefillChatRequested: text => {
                    message.text = text;
                    Qt.callLater(() => {
                        message.forceActiveFocus();
                        message.cursorPosition = message.text.length;
                    });
                }
            }
        }
    }
    Loader {
        id: pocketPaneLoader
        x: 12
        y: root.bodyTop
        width: parent.width - 24
        height: Math.max(0, parent.height - root.bodyTop - 12)
        active: root.pocketOpen
        sourceComponent: Component {
            AquaPocketPane {
                actor: root.actor
                embeddedMode: root.embeddedMode
                onCloseRequested: root.embeddedPocketOpen = false
            }
        }
    }
    function openSwarm(): void {
        if (root.pocketOpen) {
            if (root.embeddedMode)
                root.embeddedPocketOpen = false;
            else
                AquaPocket.close();
        }
        if (!root.embeddedMode && !AquaMind.conversationOpen)
            AquaMind.openChat();
        if (AquaMind.editorPopupOpen)
            AquaMind.closeEditorPopup();
        root.swarmOpen = true;
        root.wantsKeyboardFocus = true;
        Qt.callLater(() => {
            if (root.editing && !root.voiceStageActive && message.visible && message.enabled)
                message.forceActiveFocus();
        });
    }
    function openPocket(): void {
        if (AquaMind.busy || !AquaPocket.permitted)
            return;
        root.swarmOpen = false;
        if (root.embeddedMode) {
            root.embeddedPocketOpen = true;
            AquaPocket.refresh();
            root.sidebarOpen = false;
            if (AquaMind.editorPopupOpen)
                AquaMind.closeEditorPopup();
            return;
        }
        if (AquaPocket.show()) {
            root.sidebarOpen = false;
            if (AquaMind.editorPopupOpen)
                AquaMind.closeEditorPopup();
            AquaMind.dismiss();
        }
    }
    Connections {
        target: AquaMind
        function onSwarmOpenRequested(): void {
            root.openSwarm();
        }
        function onConversationOpenChanged(): void {
            if (AquaMind.conversationOpen) {
                AquaPocket.close();
                Qt.callLater(() => root.scrollToLatestMessage());
            } else if (!root.embeddedMode) {
                root.swarmOpen = false;
            }
        }
        function onSidebarChatOpenChanged(): void {
            if (AquaMind.sidebarChatOpen && root.editing) {
                Qt.callLater(() => root.scrollToLatestMessage());
            }
        }
        function onContextOpenChanged(): void {
            if (AquaMind.contextOpen)
                AquaPocket.close();
        }
        function onActiveThreadIdChanged(): void {
            root.resetSelectionState();
            Qt.callLater(() => root.scrollToLatestMessage());
        }
        function onHistoryLoadedChanged(): void {
            if (AquaMind.historyLoaded && root.editing) {
                Qt.callLater(() => root.scrollToLatestMessage());
            }
        }
        function onCustomSidebarWidthChanged(): void {
            if (AquaMind.customSidebarWidth > 0 && root.customSidebarWidth !== AquaMind.customSidebarWidth) {
                root.customSidebarWidth = AquaMind.customSidebarWidth;
            }
        }
        function onVoiceTranscriptReady(transcript: string, autoSent: bool): void {
            if (!root.editing)
                return;
            if (!autoSent && String(transcript ?? "").trim().length > 0) {
                const existing = String(message.text ?? "").trim();
                message.text = existing.length > 0 ? (existing + " " + String(transcript).trim()) : String(transcript).trim();
                Qt.callLater(() => {
                    if (root.editing && !root.voiceStageActive) {
                        message.forceActiveFocus();
                        message.cursorPosition = message.text.length;
                    }
                });
            }
        }
    }
    function submit(): void {
        const raw = String(message.text ?? "").trim();
        root.slashMenuDismissed = true;
        root.promptHistoryIndex = -1;
        root.promptHistoryDraft = "";
        if (raw.length > 0 && !AquaMind.pendingSudoPassword) {
            const hist = Array.isArray(root.userPromptHistory) ? root.userPromptHistory.slice() : [];
            if (hist.length === 0 || hist[hist.length - 1] !== raw) {
                hist.push(raw);
                if (hist.length > 40)
                    hist.shift();
                root.userPromptHistory = hist;
            }
        }
        if (/^\/(?:clear|clean|cls)(?:\s+.*)?$/i.test(raw)) {
            AquaMind.clearConversation();
            message.text = "";
            return;
        }
        if (AquaMind.sendMessage(message.text))
            message.text = "";
    }
    onVisibleChanged: {
        if (visible && editing) {
            root.scrollToLatestMessage();
        }
    }
    onEditingChanged: {
        if (editing) {
            root.wantsKeyboardFocus = true;
            root.scrollToLatestMessage();
            Qt.callLater(() => {
                if (root.editing && root.wantsKeyboardFocus) {
                    root.scrollToLatestMessage();
                    if (!root.voiceStageActive)
                        message.forceActiveFocus();
                }
            });
        } else {
            root.wantsKeyboardFocus = false;
            root.profileStage = 0;
            root.chatCheckInField = "";
            root.slashMenuDismissed = false;
            root.resetSelectionState();
            if (!AquaMind.conversationOpen && !AquaMind.sidebarChatOpen && (AquaMind.voiceModeOpen || AquaMind.voiceRecording || AquaMind.voiceTranscribing))
                AquaMind.cancelVoiceMode();
        }
    }
    onControlsVisibleChanged: if (!controlsVisible) {
        root.wantsKeyboardFocus = false;
        root.profileStage = 0;
        root.slashMenuDismissed = false;
        message.focus = false;
    }
}
