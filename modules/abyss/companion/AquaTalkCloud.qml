pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.services
import "../../../services"
import qs.modules.common
import qs.modules.common.widgets
import qs.modules.abyss.looks

// One question offers one choice row. Only explicit chat requests input focus.
Item {
    id: root
    property Item actor: null
    property real outputWidth: 420
    property real outputHeight: 680
    property bool allowed: false
    property bool sidebarOpen: false
    property bool embeddedMode: false
    readonly property alias inputField: message
    readonly property bool hasSidebarInputFocus: chatSidebar.visible && chatSidebar.hasActiveInputFocus
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
        root.lastProfileStage = 1
        if (AquaMind.liveModelAutoDetectAllowed) {
            root.effortClosingHold = true
            effortCloseHoldTimer.restart()
        } else {
            root.effortClosingHold = false
        }
        root.profileStage = 0
        Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
    }
    onProfileStageChanged: {
        if (profileStage !== 0) {
            root.lastProfileStage = profileStage
            root.effortClosingHold = false
            effortCloseHoldTimer.stop()
            root.slashMenuDismissed = true
            root.chatCheckInField = "none"
        } else {
            root.modelPickerRole = "primary"
        }
    }
    readonly property bool pocketOpen: AquaPocket.opened && AquaPocket.permitted
    readonly property bool editing: visible && (embeddedMode || AquaMind.conversationOpen) && !pocketOpen
    readonly property bool editorMode: root.editing && AquaMind.editorPopupOpen && AquaMind.editorFilePath.length > 0
    property real editorExpandProgress: root.editorMode ? 1.0 : 0.0
    Behavior on editorExpandProgress {
        enabled: root.visible
        NumberAnimation {
            duration: 240
            easing.type: Easing.OutCubic
        }
    }
    readonly property real editorScaleFactor: 1.0 + (0.5 * root.editorExpandProgress)
    readonly property bool panelMode: embeddedMode || editing || AquaMind.contextOpen || pocketOpen
    readonly property real bodyTop: panelMode ? ((editing && !editorMode) ? 46 : 84) : 12
    readonly property bool controlsVisible: visible && (pocketOpen || editing || AquaMind.contextOpen || AquaMind.checkInStage.length>0)
    property real customSidebarWidth: AquaMind.customSidebarWidth > 0 ? AquaMind.customSidebarWidth : 186
    onCustomSidebarWidthChanged: if (AquaMind.customSidebarWidth !== customSidebarWidth) AquaMind.customSidebarWidth = customSidebarWidth
    readonly property real minSidebarWidth: 144
    readonly property real maxSidebarWidth: embeddedMode
        ? Math.max(180, Math.min(360, Math.round((width > 0 ? width : outputWidth) * 0.56)))
        : Math.max(196, Math.min(340, Math.round((outputWidth - 32) * 0.54)))
    readonly property real sidebarWidth: (sidebarOpen && editing)
        ? Math.max(minSidebarWidth, Math.min(maxSidebarWidth, customSidebarWidth))
        : 0
    readonly property real sidebarOffset: sidebarWidth > 0 ? (sidebarWidth + 10) : 0
    function resizeSidebarTo(targetWidth): real {
        const clamped = Math.max(minSidebarWidth, Math.min(maxSidebarWidth, Math.round(Number(targetWidth) || 186)))
        customSidebarWidth = clamped
        AquaMind.customSidebarWidth = clamped
        return sidebarWidth
    }
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
    function formatPomodoroClock(sec): string {
        const s = Math.max(0, Math.floor(Number(sec) || 0))
        const m = Math.floor(s / 60)
        const r = s % 60
        return (m < 10 ? "0" + m : String(m)) + ":" + (r < 10 ? "0" + r : String(r))
    }
    property string chatCheckInField: ""
    property bool forceTodayBarHover: false
    readonly property string activeChatCheckInField: {
        if (!editing || profileStage !== 0 || slashMenuOpen) return ""
        if (chatCheckInField === "none") return ""
        if (chatCheckInField === "mood" || chatCheckInField === "energy") return chatCheckInField
        if (!AquaMind.checkInComplete) return AquaMind.userMood ? "energy" : "mood"
        return ""
    }
    readonly property bool pomodoroActivelyRunning: Boolean(TimerService.pomodoroRunning && !TimerService.pomodoroPaused)
    readonly property bool pomodoroIsPaused: Boolean(TimerService.pomodoroRunning && TimerService.pomodoroPaused)
    function formatCheckInDisplayValue(val): string {
        const v = String(val ?? "").trim()
        if (!v) return "—"
        return v.toLowerCase() === "medium" ? "mid" : v
    }
    readonly property bool todayBarHovered: forceTodayBarHover
        || chatTodayHover.hovered
        || chatMoodBtn.buttonHovered
        || chatEnergyBtn.buttonHovered
    readonly property bool todayBarExpanded: root.profileStage === 0
        && !root.slashMenuOpen
        && (!AquaMind.checkInComplete
            || root.activeChatCheckInField !== ""
            || root.todayBarHovered)
    readonly property var moodOptions: [
        {value: "terrible", label: Translation.tr("Awful")},
        {value: "bad", label: Translation.tr("Low")},
        {value: "okay", label: Translation.tr("Okay")},
        {value: "good", label: Translation.tr("Good")},
        {value: "great", label: Translation.tr("Great")}
    ]
    readonly property var energyOptions: [
        {value: "drained", label: Translation.tr("Drained")},
        {value: "low", label: Translation.tr("Low")},
        {value: "medium", label: Translation.tr("Mid")},
        {value: "high", label: Translation.tr("High")},
        {value: "peak", label: Translation.tr("Peak")}
    ]
    function choiceLabel(field, value): string {
        const list = field === "energy" ? root.energyOptions : root.moodOptions
        const match = list.find(item => item.value === String(value ?? "").toLowerCase())
        return match ? String(match.label) : String(value ?? "")
    }

    // Slash Command Menu (/) state & catalog (Antigravity / Claude / ChatGPT / Hermes style)
    property bool slashMenuDismissed: false
    property int slashSelectedIndex: 0
    readonly property var baseSlashCommands: [
        { name: "learn", cmd: "/learn", usage: "/learn <path-or-workflow>", category: "Hermes", icon: "school", desc: "Learn & synthesize a reusable skill from a project, session, or workflow", needsArgs: true },
        { name: "workflow", cmd: "/workflow", usage: "/workflow", category: "Hermes", icon: "account_tree", desc: "Inspect or adapt learned workflows & self-reflections", needsArgs: false },
        { name: "skills", cmd: "/skills", usage: "/skills", category: "Hermes", icon: "extension", desc: "List, inspect, patch, or manage agent SKILL.md packages", needsArgs: false },
        { name: "plan", cmd: "/plan", usage: "/plan <goal>", category: "Agent", icon: "architecture", desc: "Create a structured plan for a task, project, or schedule", needsArgs: true },
        { name: "clarify", cmd: "/clarify", usage: "/clarify <topic>", category: "Agent", icon: "psychology_alt", desc: "Ask clarifying questions with 1-3 idea choices & custom input", needsArgs: false },
        { name: "grill-me", cmd: "/grill-me", usage: "/grill-me <goal>", category: "Agent", icon: "quiz", desc: "Interactive interview with 1-3 idea choices to align on a plan", needsArgs: false },
        { name: "rag", cmd: "/rag", usage: "/rag <query|list|add>", category: "Knowledge", icon: "library_books", desc: "Search or manage RAG knowledge base & citations", needsArgs: false },
        { name: "mcp", cmd: "/mcp", usage: "/mcp <tools|resources|call>", category: "Tools", icon: "hub", desc: "Inspect or invoke Model Context Protocol servers & tools", needsArgs: false },
        { name: "git", cmd: "/git", usage: "/git status", category: "Code", icon: "commit", desc: "Inspect git status, branch, diff, or recent commits", needsArgs: false },
        { name: "system", cmd: "/system", usage: "/system", category: "System", icon: "monitor_heart", desc: "Check CPU, RAM, disk, battery, and temperatures", needsArgs: false },
        { name: "processes", cmd: "/processes", usage: "/processes", category: "System", icon: "memory", desc: "List top running processes by CPU and memory", needsArgs: false },
        { name: "memory", cmd: "/memory", usage: "/memory", category: "Memory", icon: "psychology", desc: "View or save persistent Companion memories", needsArgs: false },
        { name: "rules", cmd: "/rules", usage: "/rules", category: "Automation", icon: "rule", desc: "List or create proactive event/time automation rules", needsArgs: false },
        { name: "permissions", cmd: "/permissions", usage: "/permissions", category: "Security", icon: "verified_user", desc: "Inspect or revoke desktop agent permissions", needsArgs: false },
        { name: "clear", cmd: "/clear", usage: "/clear", category: "Chat", icon: "ink_eraser", desc: "Clear messages in the current chat thread", needsArgs: false }
    ]
    readonly property var slashCommandCatalog: {
        const list = root.baseSlashCommands.slice()
        const existing = {}
        for (let i = 0; i < list.length; ++i) existing[list[i].name] = true
        const skills = Array.isArray(AquaMind.availableSkills) ? AquaMind.availableSkills : []
        for (let j = 0; j < skills.length; ++j) {
            const sk = skills[j]
            if (!sk || sk.enabled === false) continue
            const sname = String(sk.name ?? "").trim().toLowerCase()
            if (!sname || existing[sname]) continue
            existing[sname] = true
            list.push({
                name: sname,
                cmd: "/" + sname,
                usage: "/" + sname + " <task>",
                category: sk.builtIn ? "Skill" : "Learned",
                icon: sk.builtIn ? "auto_awesome" : "psychology_alt",
                desc: String(sk.description ?? "Reusable agent skill"),
                needsArgs: false
            })
        }
        return list
    }
    readonly property string slashRawInput: String(message.text ?? "")
    readonly property bool isSlashPrefix: root.editing && /^\/[^\s]*$/.test(root.slashRawInput)
    readonly property string slashQuery: root.isSlashPrefix ? root.slashRawInput.slice(1).toLowerCase() : ""
    readonly property var filteredSlashCommands: {
        if (!root.isSlashPrefix) return []
        const q = root.slashQuery
        if (!q) return root.slashCommandCatalog
        const matched = root.slashCommandCatalog.filter(item =>
            item.name.indexOf(q) >= 0
            || item.category.toLowerCase().indexOf(q) >= 0
            || item.desc.toLowerCase().indexOf(q) >= 0
        )
        matched.sort((a, b) => {
            const aStarts = a.name.indexOf(q) === 0 ? 0 : (a.name.indexOf(q) > 0 ? 1 : 2)
            const bStarts = b.name.indexOf(q) === 0 ? 0 : (b.name.indexOf(q) > 0 ? 1 : 2)
            return aStarts - bStarts
        })
        return matched
    }
    readonly property bool slashMenuOpen: root.isSlashPrefix && !root.slashMenuDismissed && root.filteredSlashCommands.length > 0
    function selectSlashCommand(item, executeIfReady = true): bool {
        if (!item) return false
        root.profileStage = 0
        if (item.needsArgs || !executeIfReady) {
            message.text = item.cmd + " "
            root.slashMenuDismissed = true
            Qt.callLater(() => {
                if (root.editing) {
                    message.forceActiveFocus()
                    message.cursorPosition = message.text.length
                }
            })
            return true
        }
        message.text = item.cmd
        root.slashMenuDismissed = true
        root.submit()
        return true
    }

    // Antigravity-Style Chat Turn Dot Navigators state & helpers
    property int hoveredTurnIndex: -1
    property int forcedHoveredTurnIndex: -1
    property int activeTurnIndex: -1
    readonly property var chatTurns: {
        const hist = Array.isArray(AquaMind.history) ? AquaMind.history : []
        const turns = []
        let currentTurn = null
        for (let i = 0; i < hist.length; ++i) {
            const msg = hist[i]
            if (!msg) continue
            const role = String(msg.role ?? "assistant")
            const rawContent = role === "user"
                ? String(msg.content ?? "").replace(/\s+/g, " ").trim()
                : root.stripMarkdownToPlain(root.compactBubbleSource(String(msg.content ?? "")))
            const created = Number(msg.created_ms ?? msg.createdMs ?? 0)
            if (role === "user") {
                if (currentTurn) turns.push(currentTurn)
                currentTurn = {
                    turnNumber: turns.length + 1,
                    historyIndex: i,
                    promptText: rawContent || "(empty prompt)",
                    replyText: "",
                    createdMs: created
                }
            } else {
                if (!currentTurn) {
                    currentTurn = {
                        turnNumber: turns.length + 1,
                        historyIndex: i,
                        promptText: "Companion",
                        replyText: rawContent,
                        createdMs: created
                    }
                } else {
                    currentTurn.replyText = currentTurn.replyText
                        ? (currentTurn.replyText + " " + rawContent).trim()
                        : rawContent
                    if (!currentTurn.createdMs && created > 0) currentTurn.createdMs = created
                }
            }
        }
        if (currentTurn) turns.push(currentTurn)
        return turns
    }
    readonly property int effectiveHoveredTurnIndex: root.forcedHoveredTurnIndex >= 0
        ? root.forcedHoveredTurnIndex
        : root.hoveredTurnIndex
    onEffectiveHoveredTurnIndexChanged: {
        if (root.effectiveHoveredTurnIndex >= 0)
            root.scrollDotNavigatorToTurn(root.effectiveHoveredTurnIndex)
    }
    readonly property var effectiveHoveredTurn: (root.effectiveHoveredTurnIndex >= 0
            && root.effectiveHoveredTurnIndex < root.chatTurns.length)
        ? root.chatTurns[root.effectiveHoveredTurnIndex]
        : null
    function scrollDotNavigatorToTurn(turnIndex): void {
        const idx = Number(turnIndex)
        if (!Number.isInteger(idx) || idx < 0 || idx >= root.chatTurns.length) return
        if (dotNavigatorFlickable.contentHeight <= dotNavigatorFlickable.height) {
            dotNavigatorFlickable.contentY = 0
            return
        }
        const step = dotNavigatorRail.dotItemHeight + dotNavigatorRail.dotSpacing
        const itemTop = 4 + idx * step
        const itemBottom = itemTop + dotNavigatorRail.dotItemHeight
        const maxContentY = Math.max(0, dotNavigatorFlickable.contentHeight - dotNavigatorFlickable.height)
        if (itemTop < dotNavigatorFlickable.contentY + 6) {
            dotNavigatorFlickable.contentY = Math.max(0, itemTop - 6)
        } else if (itemBottom > dotNavigatorFlickable.contentY + dotNavigatorFlickable.height - 6) {
            dotNavigatorFlickable.contentY = Math.min(maxContentY, itemBottom - dotNavigatorFlickable.height + 6)
        }
    }
    function jumpToChatTurn(turnIndex): bool {
        const idx = Number(turnIndex)
        if (!Number.isInteger(idx) || idx < 0 || idx >= root.chatTurns.length) return false
        const turn = root.chatTurns[idx]
        if (!turn) return false
        root.activeTurnIndex = idx
        root.scrollDotNavigatorToTurn(idx)
        const histIdx = Number(turn.historyIndex ?? 0)
        if (histIdx >= 0 && histIdx < transcript.count) {
            transcript.positionViewAtIndex(histIdx, ListView.Beginning)
            return true
        }
        return false
    }
    function formatTurnTimestamp(ms): string {
        const ts = Number(ms || 0)
        const d = ts > 0 ? new Date(ts) : new Date()
        let h = d.getHours()
        const m = d.getMinutes()
        const ampm = h >= 12 ? "PM" : "AM"
        h = h % 12
        if (h === 0) h = 12
        return String(h) + ":" + (m < 10 ? "0" + String(m) : String(m)) + " " + ampm
    }
    function formatBytes(byteCount): string {
        const b = Math.max(0, Number(byteCount) || 0)
        if (b < 1024) return String(b) + " B"
        return (b / 1024).toFixed(1) + " KB"
    }
    function stripMarkdownToPlain(raw): string {
        let s = String(raw ?? "")
        if (!s) return ""
        s = s.replace(/```[\s\S]*?```/g, m => m.replace(/```[a-zA-Z0-9_-]*\n?/g, "").replace(/```/g, ""))
        s = s.replace(/\*\*\*([^*]+)\*\*\*/g, "$1")
        s = s.replace(/\*\*([^*]+)\*\*/g, "$1")
        s = s.replace(/\*([^*\n]+)\*/g, "$1")
        s = s.replace(/`([^`\n]+)`/g, "$1")
        s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1")
        s = s.replace(/(^|\n)\s*#{1,4}\s+/g, "$1")
        return s.replace(/\s+/g, " ").trim()
    }
    function compactBubbleSource(raw): string {
        let text = String(raw ?? "")
        if (!text) return ""
        // Normalize legacy verbose screen observation header & noisy OCR lines for compact chatbox
        text = text.replace(
            /^Current window observation(\s*\([^)]*\))?\s*\[source:\s*`([^`]+)`,\s*window:\s*`([^`]+)`(?:,\s*at:\s*`[^`]+`)?\]:/m,
            (_, changed, src, win) => `**🖥 ${win}** · \`${src}\`${changed ? " *" + changed.trim() + "*" : ""}`
        )
        text = text.replace(
            /^Quan sát màn hình hiện tại(\s*\([^)]*\))?\s*\[nguồn:\s*`([^`]+)`,\s*cửa sổ:\s*`([^`]+)`(?:,\s*lúc:\s*`[^`]+`)?\]:/m,
            (_, changed, src, win) => `**🖥 ${win}** · \`${src}\`${changed ? " *" + changed.trim() + "*" : ""}`
        )
        text = text.replace(
            /\*\*MCP Eyes & Hands are active:\*\*[\s\S]*$/m,
            "💡 **MCP Eyes & Hands:** `/web` search · `/fetch` read URL · `/browser` automate web · open apps / run commands."
        )
        text = text.replace(
            /\*\*Mắt \(Eyes\) & Tay \(Hands\) MCP đang hoạt động:\*\*[\s\S]*$/m,
            "💡 **Mắt & Tay MCP:** `/web` tìm kiếm · `/fetch` đọc trang · `/browser` tự động hoá web · mở ứng dụng / chạy lệnh."
        )
        if (text.startsWith("**🖥 ") && !/`[^`]*(?:\+ai|vision)`/.test(text.split("\n")[0] ?? "")) {
            const rawLines = text.split("\n")
            const outLines = []
            let bulletCount = 0
            for (let i = 0; i < rawLines.length; ++i) {
                const ln = rawLines[i]
                if (/^\s*-\s+/.test(ln)) {
                    let body = ln.replace(/^\s*-\s+/, "").trim()
                    const tokens = body.split(/\s+/).filter(Boolean)
                    if (tokens.length >= 4) {
                        let noisy = 0
                        for (let t = 0; t < tokens.length; ++t) {
                            if (tokens[t].length <= 2 || !/[a-zA-ZÀ-ỹ]/.test(tokens[t])) noisy++
                        }
                        if ((noisy / tokens.length) >= 0.56) continue
                    }
                    while (tokens.length >= 2 && (tokens[0].length === 1 || !/[a-zA-Z0-9À-ỹ]/.test(tokens[0]))) {
                        tokens.shift()
                    }
                    body = tokens.join(" ").replace(/^[-•|>_~\s]+/, "").trim()
                    if (body.length < 3) continue
                    if (body.length > 140) body = body.slice(0, 137).trim() + "…"
                    bulletCount++
                    if (bulletCount <= 6) outLines.push("- " + body)
                } else {
                    outLines.push(ln)
                }
            }
            text = outLines.join("\n")
        }
        return text.replace(/\n{3,}/g, "\n\n").trim()
    }
    function containsMarkdownSyntax(raw): bool {
        const s = String(raw ?? "")
        if (!s) return false
        return s.indexOf("**") !== -1
            || s.indexOf("`") !== -1
            || /(^|\n)\s*[-*•]\s+\S/.test(s)
            || /(^|\n)\s*\d+\.\s+\S/.test(s)
            || /(^|\n)\s*#{1,4}\s+\S/.test(s)
            || /\*[^*\n]+\*/.test(s)
            || /\[[^\]]+\]\([^)]+\)/.test(s)
    }
    function formatBubbleMarkdown(raw): string {
        let s = String(raw ?? "")
        if (!s) return ""
        const codeBlocks = []
        s = s.replace(/```(?:[a-zA-Z0-9_-]+)?\n?([\s\S]*?)```/g, (_, code) => {
            const idx = codeBlocks.length
            const esc = String(code ?? "").trim()
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/\n/g, "<br/>")
            codeBlocks.push(`<span style="font-family:monospace;">${esc}</span>`)
            return `\u0000CODEBLOCK_${idx}\u0000`
        })
        const inlineCodes = []
        s = s.replace(/`([^`\n]+)`/g, (_, code) => {
            const idx = inlineCodes.length
            const esc = String(code ?? "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
            inlineCodes.push(`<span style="font-family:monospace;">${esc}</span>`)
            return `\u0000INLINECODE_${idx}\u0000`
        })
        s = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "<a href=\"$2\" style=\"color:#7aa2f7;text-decoration:underline;\">$1</a>")
        s = s.replace(/\*\*\*([^*\n]+)\*\*\*/g, "<b><i>$1</i></b>")
        s = s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
        s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, "$1<i>$2</i>")

        const lines = s.split("\n")
        const renderedLines = []
        for (let i = 0; i < lines.length; ++i) {
            let ln = lines[i]
            if (/^\s*#{1,4}\s+/.test(ln)) {
                ln = "<b>" + ln.replace(/^\s*#{1,4}\s+/, "") + "</b>"
            } else if (/^\s*[-*•]\s+/.test(ln)) {
                ln = "• " + ln.replace(/^\s*[-*•]\s+/, "")
            } else if (/^\s*(\d+)\.\s+/.test(ln)) {
                ln = ln.replace(/^\s*(\d+)\.\s+/, "<b>$1.</b> ")
            }
            renderedLines.push(ln)
        }
        let out = renderedLines.join("<br/>")
        out = out.replace(/\u0000INLINECODE_(\d+)\u0000/g, (_, i) => inlineCodes[Number(i)] ?? "")
        out = out.replace(/\u0000CODEBLOCK_(\d+)\u0000/g, (_, i) => codeBlocks[Number(i)] ?? "")
        return out
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
        running: root.visible && AquaMind.isWorking
        onTriggered: root.thinkingAnimTick = (root.thinkingAnimTick + 1) % 3
    }
    function findChildByObjectName(item, targetName) {
        if (!item) return null
        if (item.objectName === targetName) return item
        const kids = item.data ?? item.children ?? []
        for (let i = 0; i < kids.length; ++i) {
            const match = findChildByObjectName(kids[i], targetName)
            if (match) return match
        }
        return null
    }
    function clearOtherSelections(exceptIndex = -999): void {
        const keep = Number(exceptIndex)
        if (keep !== -3 && String(streamingPreviewTextItem.selectedText ?? "").length > 0)
            streamingPreviewTextItem.deselect()
        for (let i = 0; i < transcript.count; ++i) {
            if (i === keep) continue
            const textItem = root.findChildByObjectName(transcript, "aquaChatBubbleText-" + i)
            if (textItem && String(textItem.selectedText ?? "").length > 0)
                textItem.deselect()
        }
    }
    function resetSelectionState(): void {
        copyFeedbackTimer.stop()
        root.clearOtherSelections(-999)
        root.activeSelectionBubbleIndex = -1
        root.lastCopiedBubbleIndex = -1
        root.activePressBubbleCount = 0
    }
    function copyTextToClipboard(textValue, bubbleIdx = -1): bool {
        const val = String(textValue ?? "")
        if (!val) return false
        root.lastCopiedText = val
        root.lastCopiedBubbleIndex = Number(bubbleIdx)
        copyFeedbackTimer.restart()
        clipboardHelper.text = val
        clipboardHelper.selectAll()
        clipboardHelper.copy()
        try {
            if (typeof Quickshell !== "undefined" && Quickshell && "clipboardText" in Quickshell)
                Quickshell.clipboardText = val
        } catch (e) {}
        return true
    }
    function selectBubbleText(index, startPos = 0, endPos = -1): string {
        const idx = Number(index)
        if (idx >= 0 && idx < transcript.count)
            transcript.positionViewAtIndex(idx, ListView.Contain)
        const textItem = root.findChildByObjectName(transcript, "aquaChatBubbleText-" + idx)
        if (!textItem) return ""
        root.clearOtherSelections(idx)
        if (Number(endPos) < 0) textItem.selectAll()
        else textItem.select(Number(startPos), Number(endPos))
        const sel = String(textItem.selectedText ?? "")
        root.activeSelectionBubbleIndex = sel.length > 0 ? idx : -1
        return sel
    }
    function copyBubbleText(index): string {
        const idx = Number(index)
        if (idx >= 0 && idx < transcript.count)
            transcript.positionViewAtIndex(idx, ListView.Contain)
        const textItem = root.findChildByObjectName(transcript, "aquaChatBubbleText-" + idx)
        const fallbackContent = (idx >= 0 && idx < AquaMind.history.length)
            ? String(AquaMind.history[idx]?.content ?? "")
            : ""
        const chosen = (textItem && String(textItem.selectedText ?? "").length > 0)
            ? String(textItem.selectedText)
            : (textItem && textItem.rawContent !== undefined
                ? String(textItem.rawContent)
                : (textItem ? String(textItem.text ?? "") : fallbackContent))
        if (!root.copyTextToClipboard(chosen, idx)) return ""
        return chosen
    }
    function copyActiveSelection(): bool {
        if (root.activeSelectionBubbleIndex === -3 && streamingPreviewTextItem.visible && String(streamingPreviewTextItem.selectedText ?? "").length > 0)
            return root.copyTextToClipboard(streamingPreviewTextItem.selectedText, -3)
        if (root.activeSelectionBubbleIndex >= 0 && root.activeSelectionBubbleIndex < transcript.count) {
            const activeItem = root.findChildByObjectName(transcript, "aquaChatBubbleText-" + root.activeSelectionBubbleIndex)
            if (activeItem && String(activeItem.selectedText ?? "").length > 0)
                return root.copyTextToClipboard(activeItem.selectedText, root.activeSelectionBubbleIndex)
        }
        if (streamingPreviewTextItem.visible && String(streamingPreviewTextItem.selectedText ?? "").length > 0)
            return root.copyTextToClipboard(streamingPreviewTextItem.selectedText, -3)
        for (let i = 0; i < transcript.count; ++i) {
            const textItem = root.findChildByObjectName(transcript, "aquaChatBubbleText-" + i)
            if (textItem && String(textItem.selectedText ?? "").length > 0)
                return root.copyTextToClipboard(textItem.selectedText, i)
        }
        return false
    }

    readonly property bool backgroundThinkingBubble: !embeddedMode
        && !panelMode
        && AquaMind.isWorking
        && AquaMind.text.length === 0
        && AquaMind.checkInStage.length === 0
    onBackgroundThinkingBubbleChanged: {
        if (actor) {
            if (backgroundThinkingBubble) {
                actor.expression = "thinking"
                actor.activity = "thinking"
            } else if (actor.activity === "thinking" && !AquaMind.isWorking) {
                actor.expression = "idle"
                actor.activity = "idle"
            }
        }
    }

    readonly property real baseCloudWidth: embeddedMode
        ? (parent ? parent.width : outputWidth)
        : backgroundThinkingBubble
            ? 58
            : (sidebarOpen && editing)
                ? Math.min(Math.max(574, outputWidth - 24), Math.max(420, 380 + sidebarOffset))
                : panelMode
                    ? Math.min(380, Math.max(240, outputWidth - 32))
                    : Math.min(380, Math.max(220, outputWidth - 32))
    width: embeddedMode
        ? baseCloudWidth
        : Math.round(baseCloudWidth * editorScaleFactor)
    focus: visible && !backgroundThinkingBubble && (!editing || (!message.activeFocus && !editorTextArea.activeFocus && !root.anySelectableTextFocused))
    function closePopup(): void {
        root.profileStage = 0
        root.chatCheckInField = ""
        root.slashMenuDismissed = false
        if (root.embeddedMode) {
            if (root.pocketOpen) AquaPocket.close()
            return
        }
        message.focus = false
        if (root.pocketOpen) AquaPocket.close()
        AquaMind.dismiss()
    }
    Shortcut {
        sequence: "Escape"
        enabled: !root.embeddedMode && root.visible && !root.hasSidebarInputFocus && (root.panelMode || AquaMind.text.length > 0)
        context: Qt.WindowShortcut
        onActivated: {
            if (root.slashMenuOpen) {
                root.slashMenuDismissed = true
                return
            }
            if (root.editorMode) {
                AquaMind.closeEditorPopup()
                return
            }
            root.closePopup()
        }
    }
    Keys.onPressed: event => {
        if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C && root.copyActiveSelection()) {
            event.accepted = true
        }
    }
    Keys.onEscapePressed: event => {
        event.accepted = true
        if (root.slashMenuOpen) {
            root.slashMenuDismissed = true
            return
        }
        if (root.editorMode) {
            AquaMind.closeEditorPopup()
            return
        }
        root.closePopup()
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
    readonly property real baseCloudHeight: embeddedMode
        ? (parent ? parent.height : outputHeight)
        : backgroundThinkingBubble
            ? 32
            : pocketOpen ? Math.min(370, Math.max(210, bodyTop + 66 + Math.max(1, AquaPocket.items.length) * 46))
            : editing ? Math.min(440, Math.max(280, outputHeight - 48)) : nonEditingContentHeight + bodyTop + 12
    height: embeddedMode
        ? baseCloudHeight
        : Math.round(baseCloudHeight * editorScaleFactor)
    visible: embeddedMode || (allowed && actor && actor.visible && actor.inputReady && (pocketOpen || AquaMind.text.length > 0 || AquaMind.conversationOpen || AquaMind.contextOpen || AquaMind.isWorking))
    readonly property real actorVisualScale: (visible && actor) ? Math.max(.1, Number(actor.scale) || 1) : 1
    readonly property real actorVisualWidth: (visible && actor) ? actor.width * actorVisualScale : 0
    readonly property real actorVisualHeight: (visible && actor) ? actor.height * actorVisualScale : 0
    readonly property real actorVisualLeft: (visible && actor) ? actor.x + (actor.width - actorVisualWidth) / 2 : 0
    readonly property real actorVisualTop: (visible && actor) ? actor.y + (actor.height - actorVisualHeight) / 2 : 0
    readonly property real actorVisualRight: actorVisualLeft + actorVisualWidth
    readonly property real actorVisualBottom: actorVisualTop + actorVisualHeight
    readonly property real anchorGap: Math.max(6, Math.min(10, actorVisualHeight * .08))
    x: embeddedMode ? 0 : (visible ? Math.max(12, Math.min(outputWidth - width - 12,
        actorVisualLeft + actorVisualWidth / 2 - width / 2)) : 0)
    y: embeddedMode ? 0 : (visible ? (actorVisualTop - height - anchorGap >= 12
        ? actorVisualTop - height - anchorGap
        : Math.min(outputHeight - height - 12, actorVisualBottom + anchorGap)) : 0)
    z: 240
    function containsScenePoint(point): bool {
        const local = root.mapFromItem(null, point.x, point.y)
        return visible && local.x >= 0 && local.x <= width && local.y >= 0 && local.y <= height
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
            GradientStop { position: 0.0; color: root.edgeSurfaceColor(1.0) }
            GradientStop { position: 1.0; color: root.edgeSurfaceColor(0.25) }
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

    Item {
        id: backgroundThinkingDotsItem
        objectName: "aquaBackgroundThinkingDotsBubble"
        visible: root.backgroundThinkingBubble
        anchors.fill: parent
        Row {
            objectName: "aquaBackgroundThinkingDotsRow"
            anchors.centerIn: parent
            spacing: 5
            Repeater {
                model: 3
                Rectangle {
                    required property int index
                    width: 6
                    height: 6
                    radius: 3
                    color: AbyssStyle.accent
                    opacity: root.thinkingAnimTick === index ? 1.0 : 0.34
                    scale: root.thinkingAnimTick === index ? 1.18 : 0.92
                    Behavior on opacity { NumberAnimation { duration: 180 } }
                    Behavior on scale { NumberAnimation { duration: 180 } }
                }
            }
        }
        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: AquaMind.openChat()
        }
    }

    // Unified Mochi-Style Header & Mode Switcher (Abyss Panel Style)
    RowLayout {
        id: unifiedHeader
        objectName: "aquaUnifiedHeader"
        visible: root.panelMode
        x: 12; y: 10; width: parent.width - 24; height: 32
        spacing: 6

        RippleButton {
            id: sidebarToggleBtn
            objectName: "aquaChatSidebarToggle"
            Layout.preferredWidth: 30; Layout.preferredHeight: 30
            implicitWidth: 30; implicitHeight: 30
            buttonRadius: 15
            toggled: root.sidebarOpen
            colBackground: Qt.alpha(AbyssStyle.textColor, 0.05)
            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.24)
            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.32)
            onClicked: {
                if (root.pocketOpen) AquaMind.openChat()
                root.sidebarOpen = !root.sidebarOpen
                if (root.sidebarOpen && AquaMind.threads.length === 0)
                    AquaMind.refreshThreads()
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
                        if (root.embeddedMode) AquaPocket.close()
                        else AquaMind.openChat()
                    }
                    contentItem: Item {
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
                                width: Math.min(implicitWidth, Math.max(48, chatTabBtn.width - 34))
                                elide: Text.ElideRight
                                text: AquaMind.activeProject
                                    ? (String(AquaMind.activeProject.name) + " · " + AquaMind.activeThreadTitle)
                                    : (AquaMind.activeThreadTitle !== "New Chat" ? AquaMind.activeThreadTitle : Translation.tr("Chat"))
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
                    onClicked: root.openPocket()
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
                                text: AquaPocket.items.length > 0
                                    ? Translation.tr("Pocket") + " (" + AquaPocket.items.length + ")"
                                    : Translation.tr("Pocket")
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
            id: newChatQuickBtn
            objectName: "aquaNewChatButton"
            Layout.preferredWidth: 30; Layout.preferredHeight: 30
            implicitWidth: 30; implicitHeight: 30
            buttonRadius: 15
            colBackground: Qt.alpha(AbyssStyle.accent, 0.14)
            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.28)
            onClicked: {
                if (root.pocketOpen) AquaPocket.close()
                if (!root.embeddedMode && !AquaMind.conversationOpen) AquaMind.openChat()
                AquaMind.createThread("", String(AquaMind.activeThread?.projectId ?? ""))
            }
            StyledToolTip {
                extraVisibleCondition: newChatQuickBtn.buttonHovered
                text: Translation.tr("New Chat")
            }
            contentItem: MaterialSymbol {
                anchors.centerIn: parent
                text: "add_comment"
                iconSize: 15
                color: AbyssStyle.accent
            }
        }
    }

    // Standalone Pomodoro Bar when in Pocket or non-editing panel mode (in Chat mode it merges into chatTodayBar Col 1)
    Rectangle {
        id: pocketPomodoroBar
        visible: root.panelMode && (!root.editing || root.editorMode)
        x: 12; y: 46; width: parent.width - 24; height: 30
        radius: 11
        color: TimerService.pomodoroRunning
            ? Qt.alpha(AbyssStyle.accent, 0.12)
            : Qt.alpha(AbyssStyle.textColor, 0.04)
        border.width: 1
        border.color: TimerService.pomodoroRunning
            ? Qt.alpha(AbyssStyle.accent, 0.28)
            : Qt.alpha(AbyssStyle.textColor, 0.08)
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 9
            anchors.rightMargin: 6
            spacing: 6
            MaterialSymbol {
                text: root.pomodoroActivelyRunning ? "timelapse" : (root.pomodoroIsPaused ? "pause_circle" : "timer")
                iconSize: 15
                color: TimerService.pomodoroRunning ? AbyssStyle.accent : AbyssStyle.textColorMuted
            }
            StyledText {
                text: TimerService.pomodoroBreak ? Translation.tr("Break") : Translation.tr("Focus")
                font.pixelSize: Appearance.font.pixelSize.smallest
                font.weight: Font.DemiBold
                color: TimerService.pomodoroRunning ? AbyssStyle.accent : AbyssStyle.textColor
            }
            StyledText {
                text: root.formatPomodoroClock(TimerService.pomodoroSecondsLeft)
                font.pixelSize: Appearance.font.pixelSize.small
                font.weight: Font.DemiBold
                color: AbyssStyle.textColor
            }
            Item { Layout.fillWidth: true }
            RippleButton {
                Layout.preferredHeight: 22
                Layout.preferredWidth: pocketPomoLabel.implicitWidth + 16
                implicitHeight: 22
                implicitWidth: Layout.preferredWidth
                buttonRadius: 11
                colBackground: root.pomodoroActivelyRunning
                    ? Qt.alpha(AbyssStyle.accent, 0.24)
                    : Qt.alpha(AbyssStyle.accent, 0.14)
                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.34)
                onClicked: TimerService.togglePomodoro()
                contentItem: StyledText {
                    id: pocketPomoLabel
                    anchors.centerIn: parent
                    text: root.pomodoroActivelyRunning
                        ? Translation.tr("Pause")
                        : (root.pomodoroIsPaused ? Translation.tr("Resume") : Translation.tr("Start"))
                    font.pixelSize: Appearance.font.pixelSize.smallest
                    font.weight: Font.DemiBold
                    color: AbyssStyle.accent
                }
            }
            Item {
                id: pocketPomoResetSlide
                readonly property bool showReset: root.pomodoroIsPaused
                property real revealProgress: showReset ? 1.0 : 0.0
                Behavior on revealProgress {
                    NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                }
                visible: revealProgress > 0.01
                Layout.preferredWidth: Math.round(20 * revealProgress)
                Layout.preferredHeight: 20
                Layout.alignment: Qt.AlignVCenter
                implicitWidth: Layout.preferredWidth
                implicitHeight: 20
                clip: true
                RippleButton {
                    x: Math.round((1.0 - pocketPomoResetSlide.revealProgress) * 20)
                    y: 0
                    width: 20
                    height: 20
                    implicitWidth: 20
                    implicitHeight: 20
                    buttonRadius: 10
                    opacity: pocketPomoResetSlide.revealProgress
                    enabled: pocketPomoResetSlide.showReset
                    colBackground: Qt.alpha(AbyssStyle.textColor, 0.08)
                    colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.18)
                    onClicked: TimerService.resetPomodoro()
                    contentItem: MaterialSymbol {
                        anchors.centerIn: parent
                        text: "replay"
                        iconSize: 12
                        color: AbyssStyle.textColor
                    }
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
            height: parent.dragging
                ? Math.max(36, parent.height * 0.55)
                : (parent.hovered ? Math.max(28, parent.height * 0.35) : 24)
            radius: 1.5
            color: parent.dragging
                ? AbyssStyle.accent
                : (parent.hovered
                    ? Qt.alpha(AbyssStyle.accent, 0.72)
                    : Qt.alpha(AbyssStyle.textColor, 0.16))
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
                const gp = sidebarResizeMouse.mapToItem(null, mouse.x, mouse.y)
                pressStartSceneX = gp.x
                pressStartWidth = root.sidebarWidth
            }
            onPositionChanged: mouse => {
                if (!pressed) return
                const gp = sidebarResizeMouse.mapToItem(null, mouse.x, mouse.y)
                const delta = gp.x - pressStartSceneX
                root.resizeSidebarTo(pressStartWidth + delta)
            }
            onDoubleClicked: root.resizeSidebarTo(186)
        }
    }

    Item {
        id: content
        visible:!root.pocketOpen
        x: 12 + root.sidebarOffset; y: root.bodyTop; width: parent.width - 24 - root.sidebarOffset
        height: root.editing ? root.height - root.bodyTop - 12 : root.nonEditingContentHeight
        property real spacing: 7
        readonly property real contextRowsImplicitHeight: (todayContextHeader.visible ? todayContextHeader.height + spacing : 0)
            + (speechText.visible ? speechText.implicitHeight : 0)
            + (todayContextRows.visible ? spacing + todayContextRows.implicitHeight : 0)
            + (controlsColumn.visible ? spacing + controlsColumn.targetHeight : 0)
        implicitHeight: contextRowsImplicitHeight
        // Merged 1-Row, 2-Column Top Bar: Column 1 = Focus (Pomodoro), Column 2 = Today Mood/Energy (expands downward on hover/check-in)
        Rectangle {
            id: chatTodayBar
            objectName: "aquaChatTodayBar"
            visible: root.editing && !root.editorMode
            clip: true
            x: 0
            y: 0
            width: content.width
            height: implicitHeight
            implicitWidth: content.width
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            readonly property real colGap: 6
            readonly property real colWidth: Math.max(80, Math.floor((width - colGap) / 2))
            readonly property real collapsedHeight: 30
            readonly property real expandedTargetHeight: 34
                + (root.profileStage === 0 ? (5 + chatTodayPhrase.implicitHeight) : 0)
                + (chatCheckInChoicesRow.visible ? 29 : 0)
            readonly property real targetHeight: root.todayBarExpanded ? expandedTargetHeight : collapsedHeight
            readonly property real expandProgress: Math.max(0, Math.min(1,
                (implicitHeight - collapsedHeight) / Math.max(1, expandedTargetHeight - collapsedHeight)))
            implicitHeight: targetHeight
            onVisibleChanged: {
                if (!visible) {
                    chatTodayHeightAnim.stop()
                    implicitHeight = Qt.binding(() => chatTodayBar.targetHeight)
                }
            }
            Behavior on implicitHeight {
                enabled: root.visible && root.editing && AquaMind.checkInComplete
                NumberAnimation {
                    id: chatTodayHeightAnim
                    duration: 190
                    easing.type: Easing.OutCubic
                }
            }
            radius: 11
            color: root.todayBarExpanded ? Qt.alpha(AbyssStyle.textColor, 0.04) : "transparent"
            border.width: root.todayBarExpanded ? 1 : 0
            border.color: root.activeChatCheckInField !== ""
                ? Qt.alpha(AbyssStyle.accent, 0.26)
                : (root.todayBarHovered ? Qt.alpha(AbyssStyle.accent, 0.18) : Qt.alpha(AbyssStyle.textColor, 0.08))

            // Column 1 (Left): Focus / Pomodoro Bar
            Rectangle {
                id: unifiedPomodoroBar
                objectName: "aquaUnifiedPomodoroBar"
                visible: root.panelMode
                x: 0
                y: 0
                width: chatTodayBar.colWidth
                height: 30
                radius: 11
                clip: true
                color: TimerService.pomodoroRunning
                    ? Qt.alpha(AbyssStyle.accent, 0.12)
                    : Qt.alpha(AbyssStyle.textColor, 0.04)
                border.width: 1
                border.color: TimerService.pomodoroRunning
                    ? Qt.alpha(AbyssStyle.accent, 0.28)
                    : Qt.alpha(AbyssStyle.textColor, 0.08)
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 7
                    anchors.rightMargin: 6
                    spacing: 3
                    MaterialSymbol {
                        Layout.alignment: Qt.AlignVCenter
                        text: root.pomodoroActivelyRunning ? "timelapse" : (root.pomodoroIsPaused ? "pause_circle" : "timer")
                        iconSize: 13
                        color: TimerService.pomodoroRunning ? AbyssStyle.accent : AbyssStyle.textColorMuted
                    }
                    StyledText {
                        visible: unifiedPomodoroBar.width >= (pomoResetSlideWrapper.visible ? 196 : 146)
                        Layout.alignment: Qt.AlignVCenter
                        text: TimerService.pomodoroBreak ? Translation.tr("Break") : Translation.tr("Focus")
                        verticalAlignment: Text.AlignVCenter
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        font.weight: Font.DemiBold
                        color: TimerService.pomodoroRunning ? AbyssStyle.accent : AbyssStyle.textColor
                    }
                    StyledText {
                        Layout.alignment: Qt.AlignVCenter
                        text: root.formatPomodoroClock(TimerService.pomodoroSecondsLeft)
                        verticalAlignment: Text.AlignVCenter
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        font.weight: Font.DemiBold
                        color: AbyssStyle.textColor
                    }
                    Item { Layout.fillWidth: true }
                    RippleButton {
                        id: pomoToggleBtn
                        objectName: "aquaUnifiedPomodoroToggle"
                        Layout.preferredHeight: 22
                        Layout.preferredWidth: pomoToggleRow.implicitWidth + (root.pomodoroIsPaused ? 10 : 12)
                        Layout.alignment: Qt.AlignVCenter
                        implicitHeight: 22
                        implicitWidth: Layout.preferredWidth
                        buttonRadius: 11
                        colBackground: root.pomodoroActivelyRunning
                            ? Qt.alpha(AbyssStyle.accent, 0.24)
                            : Qt.alpha(AbyssStyle.accent, 0.14)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.34)
                        onClicked: TimerService.togglePomodoro()
                        contentItem: Item {
                            Row {
                                id: pomoToggleRow
                                anchors.centerIn: parent
                                spacing: 2
                                MaterialSymbol {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: root.pomodoroActivelyRunning ? "pause" : "play_arrow"
                                    iconSize: 13
                                    color: AbyssStyle.accent
                                }
                                StyledText {
                                    objectName: "aquaUnifiedPomodoroToggleLabel"
                                    visible: unifiedPomodoroBar.width >= 122
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: root.pomodoroActivelyRunning
                                        ? Translation.tr("Pause")
                                        : (root.pomodoroIsPaused ? Translation.tr("Resume") : Translation.tr("Start"))
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    font.weight: Font.DemiBold
                                    color: AbyssStyle.accent
                                }
                            }
                        }
                    }
                    Item {
                        id: pomoResetSlideWrapper
                        readonly property bool showReset: root.pomodoroIsPaused
                        property real revealProgress: showReset ? 1.0 : 0.0
                        Behavior on revealProgress {
                            NumberAnimation {
                                duration: 180
                                easing.type: Easing.OutCubic
                            }
                        }
                        visible: revealProgress > 0.01
                        Layout.preferredWidth: Math.round(20 * revealProgress)
                        Layout.preferredHeight: 22
                        Layout.alignment: Qt.AlignVCenter
                        implicitWidth: Layout.preferredWidth
                        implicitHeight: 22
                        clip: true

                        RippleButton {
                            id: pomoResetBtn
                            objectName: "aquaUnifiedPomodoroReset"
                            x: Math.round((1.0 - pomoResetSlideWrapper.revealProgress) * 20)
                            y: 1
                            width: 20
                            height: 20
                            implicitWidth: 20
                            implicitHeight: 20
                            buttonRadius: 10
                            opacity: pomoResetSlideWrapper.revealProgress
                            enabled: pomoResetSlideWrapper.showReset
                            colBackground: Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.18)
                            onClicked: TimerService.resetPomodoro()
                            StyledToolTip {
                                extraVisibleCondition: pomoResetBtn.buttonHovered
                                text: Translation.tr("Reset timer")
                            }
                            contentItem: MaterialSymbol {
                                anchors.centerIn: parent
                                text: "replay"
                                iconSize: 12
                                color: AbyssStyle.textColor
                            }
                        }
                    }
                }
            }

            // Column 2 (Right): Today Mood & Energy Bar
            Rectangle {
                id: chatTodayRightCol
                objectName: "aquaChatTodayRightCol"
                x: unifiedPomodoroBar.width + chatTodayBar.colGap
                y: 0
                width: Math.max(80, chatTodayBar.width - x)
                height: 30
                radius: 11
                color: Qt.alpha(AbyssStyle.textColor, 0.04)
                border.width: 1
                border.color: root.activeChatCheckInField !== ""
                    ? Qt.alpha(AbyssStyle.accent, 0.26)
                    : (root.todayBarHovered ? Qt.alpha(AbyssStyle.accent, 0.18) : Qt.alpha(AbyssStyle.textColor, 0.08))
                HoverHandler { id: chatTodayHover; blocking: false }

                RowLayout {
                    id: chatTodayHeaderRow
                    anchors.fill: parent
                    anchors.leftMargin: 6
                    anchors.rightMargin: 4
                    spacing: 3
                    MaterialSymbol {
                        visible: !root.todayBarExpanded || (chatMoodBtn.implicitWidth + chatEnergyBtn.implicitWidth <= chatTodayRightCol.width - 28)
                        Layout.alignment: Qt.AlignVCenter
                        text: "menu_book"
                        iconSize: 14
                        color: AbyssStyle.accent
                    }
                    StyledText {
                        visible: chatTodayRightCol.width >= (root.todayBarExpanded || !AquaMind.checkInComplete ? 215 : 158)
                        Layout.alignment: Qt.AlignVCenter
                        text: Translation.tr("Today")
                        verticalAlignment: Text.AlignVCenter
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        font.weight: Font.DemiBold
                        color: AbyssStyle.textColor
                    }
                    Item { Layout.fillWidth: true }
                    RippleButton {
                        id: chatMoodBtn
                        objectName: "aquaChatMoodButton"
                        Layout.preferredHeight: 22
                        Layout.preferredWidth: chatMoodRow.implicitWidth + 8
                        Layout.alignment: Qt.AlignVCenter
                        implicitHeight: 22
                        implicitWidth: Layout.preferredWidth
                        buttonRadius: 11
                        toggled: root.activeChatCheckInField === "mood"
                        colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                        colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                        colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                        onClicked: {
                            root.profileStage = 0
                            root.chatCheckInField = (root.activeChatCheckInField === "mood" ? "none" : "mood")
                        }
                        contentItem: Item {
                            Row {
                                id: chatMoodRow
                                anchors.centerIn: parent
                                spacing: 3
                                MaterialSymbol {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "mood"
                                    iconSize: 12
                                    color: chatMoodBtn.toggled ? AbyssStyle.accent : AbyssStyle.textColorMuted
                                }
                                StyledText {
                                    objectName: "aquaChatMoodLabel"
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: (root.todayBarExpanded || !AquaMind.checkInComplete)
                                        ? (Translation.tr("Mood") + ": " + root.formatCheckInDisplayValue(AquaMind.userMood))
                                        : (AquaMind.userMood ? root.formatCheckInDisplayValue(AquaMind.userMood) : "")
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    font.weight: chatMoodBtn.toggled ? Font.DemiBold : Font.Normal
                                    color: chatMoodBtn.toggled ? AbyssStyle.textColor : Appearance.colors.colSubtext
                                }
                            }
                        }
                    }
                    RippleButton {
                        id: chatEnergyBtn
                        objectName: "aquaChatEnergyButton"
                        Layout.preferredHeight: 22
                        Layout.preferredWidth: chatEnergyRow.implicitWidth + 8
                        Layout.alignment: Qt.AlignVCenter
                        implicitHeight: 22
                        implicitWidth: Layout.preferredWidth
                        buttonRadius: 11
                        toggled: root.activeChatCheckInField === "energy"
                        colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                        colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                        colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                        onClicked: {
                            root.profileStage = 0
                            root.chatCheckInField = (root.activeChatCheckInField === "energy" ? "none" : "energy")
                        }
                        contentItem: Item {
                            Row {
                                id: chatEnergyRow
                                anchors.centerIn: parent
                                spacing: 3
                                MaterialSymbol {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "bolt"
                                    iconSize: 12
                                    color: chatEnergyBtn.toggled ? AbyssStyle.accent : AbyssStyle.textColorMuted
                                }
                                StyledText {
                                    objectName: "aquaChatEnergyLabel"
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: (root.todayBarExpanded || !AquaMind.checkInComplete)
                                        ? (Translation.tr("Energy") + ": " + root.formatCheckInDisplayValue(AquaMind.userEnergy))
                                        : (AquaMind.userEnergy ? root.formatCheckInDisplayValue(AquaMind.userEnergy) : "")
                                    font.pixelSize: Appearance.font.pixelSize.smallest - 1
                                    font.weight: chatEnergyBtn.toggled ? Font.DemiBold : Font.Normal
                                    color: chatEnergyBtn.toggled ? AbyssStyle.textColor : Appearance.colors.colSubtext
                                }
                            }
                        }
                    }
                }
            }
            StyledText {
                id: chatTodayPhrase
                objectName: "aquaChatTodayPhrase"
                x: 9
                y: 34 - (1.0 - chatTodayBar.expandProgress) * 4
                width: Math.max(0, parent.width - 17)
                visible: root.profileStage === 0 && (root.todayBarExpanded || chatTodayBar.expandProgress > 0.02)
                opacity: chatTodayBar.expandProgress
                text: !root.visible ? ""
                    : root.activeChatCheckInField === "mood"
                    ? "Tiny check-in! How are you feeling today?"
                    : root.activeChatCheckInField === "energy"
                    ? "And how's your energy? Tiny spark or full splash?"
                    : AquaMind.contextualPhrase()
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                font.pixelSize: Appearance.font.pixelSize.smallest
                color: Appearance.colors.colSubtext
            }
            RowLayout {
                id: chatCheckInChoicesRow
                objectName: "aquaChatCheckInChoices"
                x: 9
                y: root.profileStage === 0
                    ? (34 + chatTodayPhrase.implicitHeight + 5)
                    : 34
                width: Math.max(0, parent.width - 17)
                height: 24
                visible: root.activeChatCheckInField === "mood" || root.activeChatCheckInField === "energy"
                spacing: 4
                Repeater {
                    id: chatChoiceRepeater
                    model: root.activeChatCheckInField === "energy" ? root.energyOptions : root.moodOptions
                    RippleButton {
                        id: chatChoiceChip
                        required property var modelData
                        required property int index
                        objectName: "aquaChatChoice-" + root.activeChatCheckInField + "-" + String(modelData.value)
                        readonly property real chipCount: Math.max(1, chatChoiceRepeater.count)
                        readonly property real chipWidth: Math.max(24, (chatCheckInChoicesRow.width - (chipCount - 1) * chatCheckInChoicesRow.spacing) / chipCount)
                        x: index * (chipWidth + chatCheckInChoicesRow.spacing)
                        y: 0
                        width: chipWidth
                        height: 24
                        Layout.fillWidth: true
                        Layout.preferredHeight: 24
                        implicitWidth: chipWidth
                        implicitHeight: 24
                        buttonRadius: 10
                        enabled: !AquaMind.busy
                        toggled: (root.activeChatCheckInField === "energy" ? AquaMind.userEnergy : AquaMind.userMood) === modelData.value
                        colBackground: Qt.alpha(AbyssStyle.textColor, 0.06)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.14)
                        colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.24)
                        colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.32)
                        onClicked: {
                            const targetField = root.activeChatCheckInField
                            if (AquaMind.setChatCheckInChoice(targetField, modelData.value)) {
                                if (targetField === "mood" && !AquaMind.userEnergy)
                                    root.chatCheckInField = "energy"
                                else
                                    root.chatCheckInField = "none"
                            }
                        }
                        contentItem: StyledText {
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
            visible: !root.editing && !root.backgroundThinkingBubble
            text: useRichSpeech ? root.formatBubbleMarkdown(displaySpeech) : displaySpeech
            textFormat: useRichSpeech ? Text.RichText : Text.PlainText
            wrapMode: Text.WordWrap
            font.pixelSize: Appearance.font.pixelSize.small
            Accessible.description: AquaMind.source === "ai" ? "AI reply" : "Companion message"
        }
        ColumnLayout {
            id: todayContextRows
            visible:AquaMind.contextOpen && !root.editing && AquaMind.checkInComplete
            x: 0
            y: (todayContextHeader.visible ? todayContextHeader.height + content.spacing : 0)
                + (speechText.visible ? speechText.implicitHeight + content.spacing : 0)
            width: content.width
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
        }
        // Keep empty-state flex spacer active across all profileStage values so controlsColumn stays bottom-aligned
        Item {
            id: emptyStateSpacer
            visible: root.editing && !root.editorMode && AquaMind.history.length === 0
            x: 0
            y: chatTodayBar.visible ? (chatTodayBar.height + content.spacing) : 0
            width: content.width
            height: Math.max(0, content.height - y
                - (thinkingCard.visible ? thinkingCard.height + content.spacing : 0)
                - (controlsColumn.visible ? controlsColumn.height + content.spacing : 0))
            Layout.fillWidth: true
            Layout.fillHeight: true
            StyledText {
                visible: root.profileStage === 0 && !root.slashMenuOpen && AquaMind.historyLoaded && !AquaMind.isWorking
                anchors.centerIn: parent
                width: Math.min(parent.width, 260)
                horizontalAlignment: Text.AlignHCenter
                text: "Splish! What's on your mind?\nType / for agent commands & skills."
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.small
            }
        }
        Item {
            id: transcriptArea
            visible: root.editing && !root.editorMode && AquaMind.history.length > 0
            x: 0
            y: chatTodayBar.visible ? (chatTodayBar.height + content.spacing) : 0
            width: content.width
            height: Math.max(80, content.height - y
                - (thinkingCard.visible ? thinkingCard.height + content.spacing : 0)
                - (controlsColumn.visible ? controlsColumn.height + content.spacing : 0))
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
                        root.resetSelectionState()
                    if (AquaMind.historyLoadingOlder) return
                    root.activeTurnIndex = -1
                    readyForOlder=false
                    Qt.callLater(() => {
                        if (!root.editing) return
                        transcript.positionViewAtEnd()
                        if (root.chatTurns.length > 0)
                            root.scrollDotNavigatorToTurn(root.chatTurns.length - 1)
                        transcript.readyForOlder=true
                    })
                }
                onContentYChanged: {
                    if (readyForOlder && contentY<=12 && AquaMind.historyHasMore && !AquaMind.busy)
                        AquaMind.loadHistory(true)
                }
                delegate: Item {
                    id: bubbleRow
                    required property var modelData
                    required property int index
                    property bool activityExpanded: false
                    readonly property var bubbleActivitySteps: {
                        const raw = modelData?.activitySteps
                        const out = []
                        if (raw && typeof raw.length === "number") {
                            for (let i = 0; i < raw.length; ++i) {
                                const ln = String(raw[i] ?? "").trim()
                                if (ln) out.push(ln)
                            }
                        }
                        return out
                    }
                    readonly property bool hasActivitySteps: !fromUser && bubbleActivitySteps.length > 0
                    readonly property int workedSec: Math.max(1, Number(modelData?.workedSeconds ?? 1))
                    readonly property string workedLabel: workedSec >= 60
                        ? ("Worked for " + Math.floor(workedSec / 60) + "m " + (workedSec % 60 > 0 ? ((workedSec % 60) + "s ") : "") + (activityExpanded ? "▾" : "›"))
                        : ("Worked for " + workedSec + "s " + (activityExpanded ? "▾" : "›"))
                    readonly property real activityOffset: activityLogBox.visible ? (activityLogBox.height + 3) : 0
                    width: transcript.width
                    height: activityOffset + bubble.height + 2
                    readonly property bool fromUser: modelData?.role === "user"
                    readonly property real navReserve: dotNavigatorRail.visible ? (dotNavigatorRail.width + 4) : 0
                    readonly property real copyReserve: 26
                    readonly property real availableWidth: Math.max(140, width - navReserve)
                    readonly property real maxBubbleWidth: Math.max(120, Math.min(
                        availableWidth - copyReserve,
                        Math.floor(availableWidth * (fromUser ? 0.85 : 0.92))
                    ))
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
                        readonly property var bubbleImages: {
                            const raw = bubbleRow.modelData?.images
                            const out = []
                            if (raw && typeof raw.length === "number") {
                                for (let i = 0; i < raw.length; ++i) {
                                    if (raw[i]) out.push(String(raw[i]))
                                }
                            }
                            if (out.length === 0 && !bubbleRow.fromUser) {
                                return AquaMind.extractReplyImages(String(bubbleRow.modelData?.content ?? ""))
                            }
                            return out
                        }
                        readonly property bool hasBubbleImages: bubbleImages.length > 0
                        readonly property real thumbW: bubbleRow.fromUser
                            ? 46
                            : Math.min(216, Math.max(120, bubbleRow.maxTextWidth))
                        readonly property real thumbH: bubbleRow.fromUser ? 46 : 122
                        readonly property real imagesRowHeight: hasBubbleImages ? (thumbH + 6) : 0
                        readonly property real imagesRowWidth: hasBubbleImages
                            ? Math.min(bubbleRow.maxTextWidth, bubbleImages.length * (thumbW + 6) - 6)
                            : 0
                        readonly property real measuredTextWidth: messageText.contentWidth > 0
                            ? Math.ceil(messageText.contentWidth) + 2
                            : Math.min(Math.ceil(messageText.implicitWidth) + 2, bubbleRow.maxTextWidth)
                        readonly property real measuredTextHeight: messageText.contentHeight > 0
                            ? Math.ceil(messageText.contentHeight)
                            : Math.ceil(messageText.implicitHeight)
                        width: Math.max(32, Math.min(bubbleRow.maxBubbleWidth, Math.max(measuredTextWidth, imagesRowWidth) + bubbleRow.hPad * 2))
                        height: Math.max(28, measuredTextHeight + imagesRowHeight + bubbleRow.vPad * 2)
                        x: bubbleRow.fromUser ? Math.max(0, bubbleRow.availableWidth - width) : 0
                        radius: Math.max(10, Math.min(14, root.edgeRadius - 6))
                        color: bubbleRow.fromUser ? Qt.alpha(AbyssStyle.accent, .14) : Qt.alpha(AbyssStyle.textColor, .06)
                        border.width: 1
                        border.color: String(messageText.selectedText ?? "").length > 0
                            ? Qt.alpha(AbyssStyle.accent, .48)
                            : (bubbleRow.fromUser ? Qt.alpha(AbyssStyle.accent, .22) : Qt.alpha(AbyssStyle.textColor, .08))
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
                                root.activePressBubbleCount = Math.max(0, root.activePressBubbleCount + (active ? 1 : -1))
                            }
                            Component.onDestruction: {
                                if (active) root.activePressBubbleCount = Math.max(0, root.activePressBubbleCount - 1)
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
                                    readonly property string imageUrl: String(modelData ?? "").startsWith("/")
                                        ? ("file://" + String(modelData))
                                        : String(modelData ?? "")
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
                                        source: bubbleThumbCard.imageUrl
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (bubbleThumbCard.imageUrl && AquaMind.liveModelAutoDetectAllowed)
                                                Qt.openUrlExternally(bubbleThumbCard.imageUrl)
                                        }
                                    }
                                }
                            }
                        }
                        TextEdit {
                            id: messageText
                            objectName: "aquaChatBubbleText-" + bubbleRow.index
                            readonly property string rawContent: String(bubbleRow.modelData?.content ?? "")
                            readonly property string displaySource: bubbleRow.fromUser
                                ? rawContent
                                : root.compactBubbleSource(rawContent)
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
                                    Qt.openUrlExternally(link)
                            }
                            onSelectedTextChanged: {
                                if (String(selectedText ?? "").length > 0) {
                                    root.activeSelectionBubbleIndex = bubbleRow.index
                                    root.clearOtherSelections(bubbleRow.index)
                                } else if (root.activeSelectionBubbleIndex === bubbleRow.index) {
                                    root.activeSelectionBubbleIndex = -1
                                }
                            }
                            onActiveFocusChanged: {
                                root.focusedSelectableCount = Math.max(0, root.focusedSelectableCount + (activeFocus ? 1 : -1))
                            }
                            Component.onDestruction: {
                                if (activeFocus) root.focusedSelectableCount = Math.max(0, root.focusedSelectableCount - 1)
                                if (root.activeSelectionBubbleIndex === bubbleRow.index) root.activeSelectionBubbleIndex = -1
                            }
                            Keys.onPressed: event => {
                                if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C) {
                                    root.copyBubbleText(bubbleRow.index)
                                    event.accepted = true
                                } else if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_A) {
                                    messageText.selectAll()
                                    event.accepted = true
                                }
                            }
                        }
                    }
                    RippleButton {
                        id: bubbleCopyBtn
                        objectName: "aquaChatBubbleCopy-" + bubbleRow.index
                        readonly property bool hasSelection: String(messageText.selectedText ?? "").length > 0
                        readonly property bool justCopied: root.lastCopiedBubbleIndex === bubbleRow.index
                        readonly property bool fitsOutside: bubbleRow.fromUser
                            ? (bubble.x >= width + 6)
                            : (bubble.x + bubble.width + width + 4 <= bubbleRow.availableWidth)
                        visible: bubbleHover.hovered || rowHover.hovered || buttonHovered || hasSelection || justCopied
                        width: (hasSelection || justCopied) ? 54 : 22
                        height: 20
                        implicitWidth: width
                        implicitHeight: 20
                        x: bubbleRow.fromUser
                            ? (fitsOutside ? (bubble.x - width - 4) : (bubble.x + 6))
                            : (fitsOutside ? (bubble.x + bubble.width + 4) : (bubble.x + bubble.width - width - 6))
                        y: fitsOutside
                            ? Math.max(2, bubble.y + bubble.height - height - 2)
                            : Math.max(2, bubble.y + bubble.height - height - 5)
                        z: 15
                        buttonRadius: 10
                        colBackground: justCopied
                            ? Qt.alpha(AbyssStyle.accent, 0.24)
                            : Qt.rgba(Appearance.colors.colLayer1.r, Appearance.colors.colLayer1.g, Appearance.colors.colLayer1.b, 0.92)
                        colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
                        onClicked: root.copyBubbleText(bubbleRow.index)
                        StyledToolTip {
                            extraVisibleCondition: bubbleCopyBtn.buttonHovered
                            text: bubbleCopyBtn.justCopied
                                ? "Copied!"
                                : (bubbleCopyBtn.hasSelection ? "Copy selected text" : "Copy message")
                        }
                        contentItem: Item {
                            Row {
                                anchors.centerIn: parent
                                spacing: 3
                                MaterialSymbol {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: bubbleCopyBtn.justCopied ? "check" : "content_copy"
                                    iconSize: 12
                                    color: (bubbleCopyBtn.justCopied || bubbleCopyBtn.hasSelection)
                                        ? AbyssStyle.accent
                                        : AbyssStyle.textColor
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
                }
                Connections {
                    target: AquaMind
                    function onHistoryPrepended(count): void {
                        transcript.positionViewAtIndex(count,ListView.Beginning)
                        transcript.readyForOlder=true
                    }
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
                        y: dotNavigatorFlickable.contentHeight <= dotNavigatorFlickable.height
                            ? Math.round((dotNavigatorFlickable.height - implicitHeight) / 2)
                            : 4
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
                                readonly property bool dotActive: root.activeTurnIndex === index
                                    || (root.activeTurnIndex < 0 && index === root.chatTurns.length - 1)
                                readonly property bool compactTick: dotNavigatorRail.totalTurns > 16
                                    && !dotHovered
                                    && !dotActive
                                    && index < (root.chatTurns.length - 6)
                                Rectangle {
                                    anchors.centerIn: parent
                                    width: dotItem.dotHovered ? 8 : (dotItem.dotActive ? 7 : (dotItem.compactTick ? 6 : 5))
                                    height: dotItem.compactTick ? 2 : width
                                    radius: height / 2
                                    color: dotItem.dotHovered
                                        ? AbyssStyle.textColor
                                        : (dotItem.dotActive
                                            ? AbyssStyle.accent
                                            : Qt.alpha(AbyssStyle.textColor, dotItem.compactTick ? 0.24 : 0.34))
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
                                            root.hoveredTurnIndex = -1
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
                    const idx = Math.max(0, root.effectiveHoveredTurnIndex)
                    const total = Math.max(1, root.chatTurns.length)
                    const ratio = total > 1 ? (idx / (total - 1)) : 0.5
                    const targetY = dotNavigatorRail.y + ratio * Math.max(0, dotNavigatorRail.height - height)
                    return Math.max(2, Math.min(Math.max(2, transcriptArea.height - height - 2), targetY))
                }
                z: 95
                radius: 10
                color: Qt.rgba(
                    Math.max(0.06, AbyssStyle.surfaceDeep.r * 0.92),
                    Math.max(0.06, AbyssStyle.surfaceDeep.g * 0.92),
                    Math.max(0.08, AbyssStyle.surfaceDeep.b * 0.92),
                    0.97
                )
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
                        Item { Layout.fillWidth: true }
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
                    Qt.callLater(() => { if (root.editing) transcript.positionViewAtEnd() })
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
                                text: AquaMind.workPhase === "tool"
                                    ? "terminal"
                                    : (AquaMind.workPhase === "drafting" ? "edit_note" : "psychology")
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
                                    Behavior on opacity { NumberAnimation { duration: 160 } }
                                    Behavior on scale { NumberAnimation { duration: 160 } }
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
                        Layout.maximumWidth: 88
                        Layout.preferredHeight: 18
                        implicitWidth: Math.min(88, thinkingRouteBadgeRow.implicitWidth + 10)
                        implicitHeight: 18
                        radius: 9
                        clip: true
                        color: AquaMind.activeRoutedRole === "thinking"
                            ? Qt.alpha(AbyssStyle.accent, 0.20)
                            : Qt.alpha(AbyssStyle.textColor, 0.10)
                        border.width: 1
                        border.color: AquaMind.activeRoutedRole === "thinking"
                            ? Qt.alpha(AbyssStyle.accent, 0.42)
                            : Qt.alpha(AbyssStyle.textColor, 0.18)
                        HoverHandler {
                            id: thinkingRouteHover
                        }
                        StyledToolTip {
                            extraVisibleCondition: thinkingRouteHover.hovered && (AquaMind.activeRoutedReason.length > 0 || AquaMind.activeRoutedModelLabel.length > 0)
                            text: AquaMind.activeRoutedReason
                                + (AquaMind.activeRoutedModelLabel.length > 0 ? (" · " + AquaMind.activeRoutedModelLabel) : "")
                                + (AquaMind.activeRoutedDirective.length > 0 ? (" · " + AquaMind.activeRoutedDirective) : "")
                        }
                        Row {
                            id: thinkingRouteBadgeRow
                            anchors.centerIn: parent
                            spacing: 3
                            MaterialSymbol {
                                anchors.verticalCenter: parent.verticalCenter
                                text: AquaMind.activeRoutedRole === "thinking" ? "psychology" : "bolt"
                                iconSize: 11
                                color: AquaMind.activeRoutedRole === "thinking"
                                    ? AbyssStyle.accent
                                    : AbyssStyle.textColor
                            }
                            StyledText {
                                id: thinkingRouteBadgeText
                                objectName: "aquaThinkingRouteText"
                                anchors.verticalCenter: parent.verticalCenter
                                width: Math.min(implicitWidth, 66)
                                elide: Text.ElideRight
                                text: AquaMind.activeRoutedRoleLabel
                                font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                font.weight: Font.DemiBold
                                color: AquaMind.activeRoutedRole === "thinking"
                                    ? AbyssStyle.accent
                                    : AbyssStyle.textColor
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
                    Layout.preferredHeight: AquaMind.workPhase === "drafting"
                        ? Math.min(72, streamingPreviewTextItem.implicitHeight + 10)
                        : (AquaMind.workActivityExpanded
                            ? Math.min(108, streamingPreviewTextItem.implicitHeight + 10)
                            : Math.min(24, streamingPreviewTextItem.implicitHeight + 10))
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
                                AquaMind.toggleWorkActivityExpanded()
                            }
                        }
                    }

                    TextEdit {
                        id: streamingPreviewTextItem
                        objectName: "aquaStreamingPreviewText"
                        x: 7
                        y: (AquaMind.workActivityExpanded || String(streamingPreviewTextItem.selectedText ?? "").length > 0 || streamingPreviewPressHandler.active)
                            ? 5
                            : Math.min(5, streamingPreviewBox.height - streamingPreviewTextItem.implicitHeight - 5)
                        width: Math.max(40, parent.width - 14)
                        text: AquaMind.workStatusDetail
                        textFormat: TextEdit.PlainText
                        wrapMode: (AquaMind.workPhase === "drafting" || AquaMind.workActivityExpanded)
                            ? TextEdit.Wrap
                            : TextEdit.NoWrap
                        readOnly: true
                        selectByMouse: true
                        selectByKeyboard: true
                        persistentSelection: true
                        leftPadding: 0
                        rightPadding: 0
                        topPadding: 0
                        bottomPadding: 0
                        renderType: TextEdit.QtRendering
                        color: AquaMind.workPhase === "drafting"
                            ? AbyssStyle.textColor
                            : Appearance.colors.colSubtext
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
                            if (String(selectedText ?? "").length > 0) {
                                root.activeSelectionBubbleIndex = -3
                                root.clearOtherSelections(-3)
                            } else if (root.activeSelectionBubbleIndex === -3) {
                                root.activeSelectionBubbleIndex = -1
                            }
                        }
                        onActiveFocusChanged: {
                            root.focusedSelectableCount = Math.max(0, root.focusedSelectableCount + (activeFocus ? 1 : -1))
                        }
                        Keys.onPressed: event => {
                            if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C && streamingPreviewTextItem.selectedText.length > 0) {
                                root.copyTextToClipboard(streamingPreviewTextItem.selectedText, -3)
                                event.accepted = true
                            } else if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_A) {
                                streamingPreviewTextItem.selectAll()
                                event.accepted = true
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
            border.color: AquaMind.editorDirty
                ? Qt.alpha(AbyssStyle.accent, 0.42)
                : Qt.alpha(AbyssStyle.textColor, 0.12)
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
                        text: String(AquaMind.editorLineCount) + "L · " + root.formatBytes(AquaMind.editorByteSize)
                            + (AquaMind.editorStatusMessage ? (" · " + AquaMind.editorStatusMessage) : "")
                        font.pixelSize: Appearance.font.pixelSize.smallest - 1
                        color: AquaMind.editorStatusMessage === "Saved"
                            ? AbyssStyle.accent
                            : Appearance.colors.colSubtext
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
                        colBackground: AquaMind.editorDirty
                            ? Qt.alpha(AbyssStyle.accent, 0.24)
                            : Qt.alpha(AbyssStyle.textColor, 0.08)
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
                    border.color: editorTextArea.activeFocus
                        ? Qt.alpha(AbyssStyle.accent, 0.32)
                        : Qt.alpha(AbyssStyle.textColor, 0.08)
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
                                    const n = Math.max(1, Math.min(300, AquaMind.editorLineCount))
                                    const lines = []
                                    for (let i = 1; i <= n; ++i) lines.push(String(i))
                                    return lines.join("\n")
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
                                onTextChanged: {
                                    if (text !== AquaMind.editorContent)
                                        AquaMind.updateEditorContent(text)
                                }
                                Keys.onPressed: event => {
                                    if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_S) {
                                        event.accepted = true
                                        AquaMind.saveEditorPopup()
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
            y: root.editing
                ? Math.max(0, content.height - height)
                : ((todayContextHeader.visible ? todayContextHeader.height + content.spacing : 0)
                    + (speechText.visible ? speechText.implicitHeight + content.spacing : 0)
                    + (todayContextRows.visible ? todayContextRows.implicitHeight + content.spacing : 0))
            width: content.width
            readonly property real topPanelsHeight: (profileBox.visible ? profileBox.implicitHeight + spacing : 0)
                + (clarificationCard.visible ? clarificationCard.implicitHeight + spacing : 0)
                + (slashCommandPopup.visible ? slashCommandPopup.implicitHeight + spacing : 0)
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
                    {value: "terrible", label: Translation.tr("Awful")},
                    {value: "bad", label: Translation.tr("Low")},
                    {value: "okay", label: Translation.tr("Okay")},
                    {value: "good", label: Translation.tr("Good")},
                    {value: "great", label: Translation.tr("Great")}
                ]
            }
            CheckInRow {
                visible: !root.editing && AquaMind.checkInStage === "energy"
                field: "energy"
                selectedValue: AquaMind.userEnergy
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
                readonly property bool logicallyOpen: root.editing && (
                    (root.profileStage === 1 && AquaMind.thinkingSupported)
                    || root.profileStage === 2
                    || AquaMind.pendingDeleteStage > 0
                    || root.effortClosingHold
                )
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
                border.color: AquaMind.pendingDeleteStage > 0
                    ? Qt.alpha(Appearance.colors.colError, 0.45)
                    : Qt.alpha(AbyssStyle.textColor, .08)
                Item {
                    id: profilePanel
                    x: 8
                    y: Math.round(8 - (1.0 - profileBox.profileSlideProgress) * 14)
                    width: parent.width - 16
                    readonly property real subPanelOffset: deleteConfirmBar.visible ? deleteConfirmBar.height + 5 : 0
                    readonly property real syncImplicitHeight: subPanelOffset
                        + (effortPanel.visible ? effortPanel.implicitHeight : 0)
                        + (modelPickerPanel.visible ? modelPickerPanel.implicitHeight : 0)
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
                    Item {
                        id: effortPanel
                        objectName: "aquaEffortPanel"
                        visible: (root.profileStage === 1
                            || (root.profileStage === 0 && root.lastProfileStage === 1 && (root.effortClosingHold || profileBox.profileSlideProgress > 0.01)))
                            && AquaMind.thinkingSupported
                        y: profilePanel.subPanelOffset
                        width: parent.width
                        height: implicitHeight
                        implicitHeight: 20 + effortMenu.implicitHeight
                        RowLayout {
                            width: parent.width
                            height: 16
                            StyledText {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: "Thinking effort"
                                verticalAlignment: Text.AlignVCenter
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                color: Appearance.colors.colSubtext
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
                            y: 20
                            width: parent.width
                            height: implicitHeight
                            implicitHeight: 36
                            property bool draggingSlider: false
                            readonly property int stepCount: Math.max(1, effortRepeater.count)
                            readonly property real stepSlotWidth: width / stepCount
                            readonly property int activeStepIndex: {
                                const levels = AquaMind.thinkingLevels ?? []
                                const cur = AquaMind.effectiveThinkingEffort
                                for (let i = 0; i < levels.length; ++i) {
                                    const val = String(levels[i]?.value ?? "")
                                    if (val === cur || (cur === "off" && val === "instant")) return i
                                }
                                return 0
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
                                const count = Math.max(1, (AquaMind.thinkingLevels ?? []).length)
                                if (count <= 1 || width <= 0) return 0
                                const pad = stepSlotWidth * 0.5
                                const trackW = Math.max(1, width - stepSlotWidth)
                                const ratio = Math.max(0, Math.min(1, (localX - pad) / trackW))
                                return Math.max(0, Math.min(count - 1, Math.round(ratio * (count - 1))))
                            }
                            function applyEffortAtX(localX, closeAfter = false): bool {
                                const levels = AquaMind.thinkingLevels ?? []
                                if (levels.length === 0 || AquaMind.busy) return false
                                const idx = stepIndexAtX(localX)
                                const item = levels[idx]
                                if (item && item.value !== undefined) {
                                    AquaMind.setThinkingEffort(item.value)
                                }
                                if (closeAfter) {
                                    root.triggerEffortSlideClose()
                                }
                                return true
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
                                width: effortMenu.stepCount > 1
                                    ? Math.round(effortSliderTrackBg.width * (effortMenu.animatedStepIndex / (effortMenu.stepCount - 1)))
                                    : 0
                                height: 4
                                radius: 2
                                color: AbyssStyle.accent
                            }
                            Rectangle {
                                id: effortSliderThumb
                                objectName: "aquaThinkingEffortThumb"
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
                                    readonly property string shortStepLabel: modelData.value === "instant" || modelData.value === "off"
                                        ? "Instant"
                                        : modelData.value === "low"
                                        ? "Low"
                                        : modelData.value === "medium"
                                        ? "Med"
                                        : modelData.value === "high"
                                        ? "High"
                                        : modelData.value === "xhigh"
                                        ? "XHigh"
                                        : "Max"
                                    x: Math.round(index * effortMenu.stepSlotWidth)
                                    y: 0
                                    width: Math.max(28, Math.round(effortMenu.stepSlotWidth))
                                    height: 36
                                    implicitWidth: 32
                                    implicitHeight: 36
                                    buttonText: modelData.label
                                    toggled: AquaMind.effectiveThinkingEffort === modelData.value
                                        || (AquaMind.effectiveThinkingEffort === "off" && modelData.value === "instant")
                                    enabled: !AquaMind.busy
                                    buttonRadius: 8
                                    colBackground: "transparent"
                                    colBackgroundHover: Qt.alpha(AbyssStyle.accent, .10)
                                    colBackgroundToggled: "transparent"
                                    colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, .14)
                                    downAction: event => {
                                        effortMenu.draggingSlider = false
                                    }
                                    moveAction: event => {
                                        if (effortOption.down) {
                                            effortMenu.draggingSlider = true
                                            effortOption.suppressClickOnRelease = true
                                            effortMenu.applyEffortAtX(effortOption.x + event.x, false)
                                        }
                                    }
                                    releaseAction: () => {
                                        if (effortMenu.draggingSlider) {
                                            effortMenu.draggingSlider = false
                                            root.triggerEffortSlideClose()
                                        }
                                    }
                                    onClicked: {
                                        AquaMind.setThinkingEffort(modelData.value)
                                        root.triggerEffortSlideClose()
                                    }
                                    contentItem: Item {
                                        Rectangle {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            y: 6
                                            width: 10
                                            height: 10
                                            radius: 5
                                            visible: !effortOption.toggled
                                            color: effortOption.index <= effortMenu.activeStepIndex
                                                ? Qt.alpha(AbyssStyle.accent, 0.72)
                                                : Appearance.colors.colLayer2
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
                                            color: effortOption.toggled
                                                ? AbyssStyle.accent
                                                : Appearance.colors.colSubtext
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Item {
                        id: modelPickerPanel
                        objectName: "aquaModelPicker"
                        visible: root.profileStage === 2
                            || (root.profileStage === 0 && root.lastProfileStage === 2 && !root.effortClosingHold && profileBox.profileSlideProgress > 0.01)
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
                                        color: pickerModeAutoBtn.toggled
                                            ? Appearance.colors.colOnPrimaryContainer
                                            : Appearance.colors.colSubtext
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
                                        root.modelPickerRole = "action"
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
                                        color: pickerModeCustomBtn.toggled
                                            ? Appearance.colors.colOnPrimaryContainer
                                            : Appearance.colors.colSubtext
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
                                        color: pickerRoleActionBtn.toggled
                                            ? Appearance.colors.colOnPrimaryContainer
                                            : Appearance.colors.colSubtext
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
                                        color: pickerRoleThinkingBtn.toggled
                                            ? Appearance.colors.colOnPrimaryContainer
                                            : Appearance.colors.colSubtext
                                    }
                                }
                            }
                        }

                        Repeater {
                            id: modelRepeater
                            model: root.modelPickerRole === "action"
                                ? AquaMind.actionSelectableModels
                                : (root.modelPickerRole === "thinking"
                                    ? AquaMind.thinkingSelectableModels
                                    : AquaMind.selectableModels)
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
                                toggled: root.modelPickerRole === "action"
                                    ? (AquaMind.configuredActionModel === modelData.name)
                                    : (root.modelPickerRole === "thinking"
                                        ? (AquaMind.configuredThinkingModel === modelData.name)
                                        : (AquaMind.model === modelData.name))
                                enabled: !AquaMind.busy
                                buttonRadius: 12
                                colBackground: "transparent"
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, .10)
                                colBackgroundToggled: Appearance.colors.colPrimaryContainer
                                colBackgroundToggledHover: Appearance.colors.colPrimaryContainerHover
                                onClicked: {
                                    if (root.modelPickerRole === "action") {
                                        AquaMind.selectActionModel(modelData)
                                    } else if (root.modelPickerRole === "thinking") {
                                        AquaMind.selectThinkingModel(modelData)
                                    } else {
                                        AquaMind.selectModel(modelData)
                                        root.profileStage = 0
                                        Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
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
                                            color: modelOption.toggled
                                                ? Appearance.colors.colOnPrimaryContainer
                                                : Appearance.colors.colOnLayer1
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
                visible: root.editing && !root.editorMode
                    && AquaMind.activeClarification !== null
                    && Array.isArray(AquaMind.activeClarification?.options)
                    && AquaMind.activeClarification.options.length > 0
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
                            model: Array.isArray(AquaMind.activeClarification?.options)
                                ? AquaMind.activeClarification.options.slice(0, 3)
                                : []

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
                                colBackground: isRecommended
                                    ? Qt.alpha(AbyssStyle.accent, 0.12)
                                    : Qt.alpha(AbyssStyle.textColor, 0.05)
                                colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.22)
                                onClicked: AquaMind.selectClarificationOption(index)

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 8
                                    color: "transparent"
                                    border.width: 1
                                    border.color: clarOptBtn.buttonHovered || clarOptBtn.isRecommended
                                        ? Qt.alpha(AbyssStyle.accent, clarOptBtn.buttonHovered ? 0.5 : 0.3)
                                        : Qt.alpha(AbyssStyle.textColor, 0.09)
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
                                        color: clarOptBtn.isRecommended
                                            ? AbyssStyle.accent
                                            : Qt.alpha(AbyssStyle.textColor, 0.12)
                                        StyledText {
                                            anchors.centerIn: parent
                                            text: String(clarOptBtn.index + 1)
                                            font.pixelSize: Appearance.font.pixelSize.smallest - 2
                                            font.weight: Font.Bold
                                            color: clarOptBtn.isRecommended
                                                ? Appearance.colors.colOnPrimary
                                                : AbyssStyle.textColor
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
                            border.color: clarificationCustomField.activeFocus
                                ? Qt.alpha(AbyssStyle.accent, 0.48)
                                : Qt.alpha(AbyssStyle.textColor, 0.10)

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 7
                                anchors.rightMargin: 6
                                spacing: 5

                                MaterialSymbol {
                                    text: "edit_note"
                                    iconSize: 15
                                    color: clarificationCustomField.activeFocus
                                        ? AbyssStyle.accent
                                        : Appearance.colors.colSubtext
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
                                    background: Rectangle { color: "transparent"; border.width: 0 }
                                    onAccepted: {
                                        const val = text.trim()
                                        if (val.length > 0 && !AquaMind.busy) {
                                            text = ""
                                            AquaMind.submitClarificationCustom(val)
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
                            colBackground: enabled
                                ? AbyssStyle.accent
                                : Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.lighter(AbyssStyle.accent, 1.08)
                            onClicked: {
                                const val = clarificationCustomField.text.trim()
                                if (val.length > 0) {
                                    clarificationCustomField.text = ""
                                    AquaMind.submitClarificationCustom(val)
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
                                color: clarificationCustomSubmitBtn.enabled
                                    ? Appearance.colors.colOnPrimary
                                    : Appearance.colors.colSubtext
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
                y: (profileBox.visible ? profileBox.height + controlsColumn.spacing : 0)
                    + (clarificationCard.visible ? clarificationCard.height + controlsColumn.spacing : 0)
                width: content.width
                height: implicitHeight
                readonly property real maxSlashPopupHeight: Math.max(96,
                    content.height
                    - (chatTodayBar.visible ? chatTodayBar.implicitHeight + content.spacing : 0)
                    - (thinkingCard.visible ? thinkingCard.implicitHeight + content.spacing : 0)
                    - (profileBox.visible ? profileBox.implicitHeight + controlsColumn.spacing : 0)
                    - (clarificationCard.visible ? clarificationCard.implicitHeight + controlsColumn.spacing : 0)
                    - composer.implicitHeight
                    - controlsColumn.spacing - 8)
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
                            text: root.slashQuery.length > 0
                                ? ("Matching /" + root.slashQuery + " (" + root.filteredSlashCommands.length + ")")
                                : ("Slash Commands & Learned Skills (" + root.filteredSlashCommands.length + ")")
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
                            color: isCurrent
                                ? Qt.alpha(AbyssStyle.accent, 0.22)
                                : (slashItemMouse.containsMouse ? Qt.alpha(AbyssStyle.accent, 0.12) : "transparent")
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
                y: (profileBox.visible ? profileBox.height + controlsColumn.spacing : 0)
                    + (clarificationCard.visible ? clarificationCard.height + controlsColumn.spacing : 0)
                    + (slashCommandPopup.visible ? slashCommandPopup.height + controlsColumn.spacing : 0)
                width: content.width
                height: implicitHeight
                Layout.fillWidth: true
                Layout.preferredHeight: implicitHeight
                implicitHeight: composerContent.height + 10
                radius: Math.max(12, Math.min(18, root.edgeRadius - 4))
                color: Qt.alpha(AbyssStyle.textColor, .05)
                border.width: 1
                border.color: chatImageDropArea.containsDrag
                    ? AbyssStyle.accent
                    : (root.slashMenuOpen
                        ? Qt.alpha(AbyssStyle.accent, 0.35)
                        : Qt.alpha(AbyssStyle.textColor, .09))
                DropArea {
                    id: chatImageDropArea
                    objectName: "aquaChatDropArea"
                    anchors.fill: parent
                    onDropped: drop => {
                        let handled = false
                        if (drop.hasUrls && Array.isArray(drop.urls)) {
                            for (let i = 0; i < drop.urls.length; ++i) {
                                if (AquaMind.attachImage(String(drop.urls[i]))) handled = true
                            }
                        }
                        if (!handled && drop.hasText && drop.text) {
                            const lines = String(drop.text).split(/\r?\n/)
                            for (let j = 0; j < lines.length; ++j) {
                                if (AquaMind.attachImage(lines[j])) handled = true
                            }
                        }
                        if (handled) drop.accept(Qt.CopyAction)
                    }
                }
                Item {
                    id: composerContent
                    x: 6
                    y: 4
                    width: Math.max(0, parent.width - 12)
                    readonly property real previewOffset: attachedImagesStrip.visible ? (attachedImagesStrip.height + 4) : 0
                    height: previewOffset + message.implicitHeight + 2 + composerRow.height
                    implicitHeight: height
                    Item {
                        id: attachedImagesStrip
                        objectName: "aquaChatImagePreviewRow"
                        visible: Array.isArray(AquaMind.attachedImages) && AquaMind.attachedImages.length > 0
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
                    MaterialTextField {
                        id: message
                        objectName: "aquaChatInput"
                        x: 0
                        y: composerContent.previewOffset
                        width: parent.width
                        height: implicitHeight
                        maximumLength: 1200
                        enableSettingsSearch: false
                        echoMode: AquaMind.pendingSudoPassword ? TextInput.Password : TextInput.Normal
                        placeholderText: AquaMind.pendingSudoPassword ? "Enter sudo password (or type 'cancel')..." : ""
                        Accessible.name: "Message"
                        background: Rectangle { color: "transparent"; border.width: 0 }
                        onTextChanged: {
                            if (text.startsWith("/")) {
                                if (!/^\/[^\s]*\s/.test(text)) {
                                    root.slashMenuDismissed = false
                                }
                                root.slashSelectedIndex = 0
                            } else {
                                root.slashMenuDismissed = false
                                root.slashSelectedIndex = 0
                            }
                        }
                        onAccepted: {
                            if (root.slashMenuOpen && root.filteredSlashCommands.length > 0) {
                                const idx = Math.max(0, Math.min(root.filteredSlashCommands.length - 1, root.slashSelectedIndex))
                                const chosen = root.filteredSlashCommands[idx]
                                if (chosen && (chosen.needsArgs || root.slashRawInput.trim().toLowerCase() !== chosen.cmd)) {
                                    root.selectSlashCommand(chosen, !chosen.needsArgs)
                                    return
                                }
                            }
                            root.submit()
                        }
                        Keys.onDownPressed: event => {
                            if (root.slashMenuOpen && root.filteredSlashCommands.length > 0) {
                                event.accepted = true
                                root.slashSelectedIndex = (root.slashSelectedIndex + 1) % root.filteredSlashCommands.length
                                slashMenuList.positionViewAtIndex(root.slashSelectedIndex, ListView.Contain)
                            }
                        }
                        Keys.onUpPressed: event => {
                            if (root.slashMenuOpen && root.filteredSlashCommands.length > 0) {
                                event.accepted = true
                                root.slashSelectedIndex = (root.slashSelectedIndex - 1 + root.filteredSlashCommands.length) % root.filteredSlashCommands.length
                                slashMenuList.positionViewAtIndex(root.slashSelectedIndex, ListView.Contain)
                            }
                        }
                        Keys.onTabPressed: event => {
                            if (root.slashMenuOpen && root.filteredSlashCommands.length > 0) {
                                event.accepted = true
                                const idx = Math.max(0, Math.min(root.filteredSlashCommands.length - 1, root.slashSelectedIndex))
                                root.selectSlashCommand(root.filteredSlashCommands[idx], false)
                            }
                        }
                        Keys.onPressed: event => {
                            if ((event.modifiers & Qt.ControlModifier) && event.key === Qt.Key_C && String(message.selectedText ?? "").length === 0 && root.copyActiveSelection()) {
                                event.accepted = true
                            }
                        }
                        Keys.onEscapePressed: event => {
                            event.accepted = true
                            if (root.slashMenuOpen) {
                                root.slashMenuDismissed = true
                                return
                            }
                            root.closePopup()
                        }
                    }
                    Item {
                        id: composerRow
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
                            colBackground: AquaMind.attachedImages.length > 0
                                ? Qt.alpha(AbyssStyle.accent, 0.20)
                                : Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                            onClicked: AquaMind.pickAndAttachImage()
                            StyledToolTip {
                                extraVisibleCondition: attachImageBtn.buttonHovered
                                text: "Attach image (PNG, JPG, WebP, GIF)"
                            }
                            contentItem: Item {
                                MaterialSymbol {
                                    anchors.centerIn: parent
                                    text: "add_photo_alternate"
                                    iconSize: 16
                                    color: AquaMind.attachedImages.length > 0
                                        ? AbyssStyle.accent
                                        : Appearance.colors.colPrimary
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
                            colBackground: root.profileStage === 2
                                ? Qt.alpha(AbyssStyle.accent, 0.22)
                                : Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                            onClicked: {
                                if (root.profileStage === 2) {
                                    root.profileStage = 0
                                    Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
                                } else {
                                    if (AquaMind.liveModelAutoDetectAllowed)
                                        AquaMind.refreshAvailableModels(false)
                                    root.profileStage = 2
                                }
                            }
                            Keys.onEscapePressed: event => {
                                event.accepted = true
                                root.closePopup()
                            }
                            StyledToolTip {
                                extraVisibleCondition: modelSelectorBtn.buttonHovered && root.profileStage !== 2
                                text: AquaMind.currentModelLabel + " · " + AquaMind.routingSummaryLabel + " · " + AquaMind.mtpStatusLabel
                            }
                            contentItem: Item {
                                MaterialSymbol {
                                    anchors.centerIn: parent
                                    text: "smart_toy"
                                    iconSize: 16
                                    color: root.profileStage === 2
                                        ? AbyssStyle.accent
                                        : Appearance.colors.colPrimary
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
                            readonly property real targetWidth: Math.max(68, Math.min(118, Math.ceil(effortTextMetrics.advanceWidth) + 46))
                            x: modelSelectorBtn.x + modelSelectorBtn.width + composerRow.spacing
                            y: Math.round((composerRow.height - height) / 2)
                            width: targetWidth
                            height: 30
                            implicitWidth: targetWidth
                            implicitHeight: 30
                            enabled: !AquaMind.busy && AquaMind.thinkingSupported
                            toggled: root.profileStage === 1
                            buttonRadius: 15
                            colBackground: root.profileStage === 1
                                ? Qt.alpha(AbyssStyle.accent, 0.22)
                                : Qt.alpha(AbyssStyle.textColor, 0.08)
                            colBackgroundHover: Qt.alpha(AbyssStyle.accent, 0.24)
                            colBackgroundToggled: Qt.alpha(AbyssStyle.accent, 0.22)
                            colBackgroundToggledHover: Qt.alpha(AbyssStyle.accent, 0.30)
                            onClicked: {
                                if (root.profileStage === 1) {
                                    root.profileStage = 0
                                    Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
                                } else {
                                    root.profileStage = 1
                                }
                            }
                            Keys.onEscapePressed: event => {
                                event.accepted = true
                                root.closePopup()
                            }
                            contentItem: Item {
                                Row {
                                    id: effortBtnRow
                                    anchors.centerIn: parent
                                    spacing: 4
                                    MaterialSymbol {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: AquaMind.effectiveThinkingEffort === "instant" || AquaMind.effectiveThinkingEffort === "off"
                                            ? "bolt"
                                            : AquaMind.effectiveThinkingEffort === "xhigh" || AquaMind.effectiveThinkingEffort === "max"
                                            ? "auto_awesome"
                                            : "psychology"
                                        iconSize: 15
                                        color: root.profileStage === 1
                                            ? AbyssStyle.accent
                                            : Appearance.colors.colPrimary
                                    }
                                    StyledText {
                                        id: effortLabel
                                        objectName: "aquaModelEffortLabel"
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: AquaMind.thinkingEffortShortLabel
                                        verticalAlignment: Text.AlignVCenter
                                        font.pixelSize: Appearance.font.pixelSize.smallest
                                        font.weight: Font.Medium
                                        color: root.profileStage === 1
                                            ? AbyssStyle.accent
                                            : Appearance.colors.colOnLayer1
                                    }
                                    MaterialSymbol {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: root.profileStage === 1 ? "expand_more" : "expand_less"
                                        iconSize: 15
                                        color: Appearance.colors.colSubtext
                                    }
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
                            enabled: (!AquaMind.busy || AquaMind.canInterruptWithPrompt(message.text))
                                && (message.text.trim().length > 0 || AquaMind.attachedImages.length > 0)
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
                }
            }
        }
    }
    Loader {
        x: 12; y: root.bodyTop
        width: parent.width - 24
        height: Math.max(0, parent.height - root.bodyTop - 12)
        active: root.pocketOpen
        sourceComponent: Component { AquaPocketPane { actor: root.actor } }
    }
    function openPocket(): void {
        if (!AquaMind.busy && AquaPocket.show()) AquaMind.dismiss()
    }
    Connections {
        target:AquaMind
        function onConversationOpenChanged():void {if(AquaMind.conversationOpen)AquaPocket.close()}
        function onContextOpenChanged():void {if(AquaMind.contextOpen)AquaPocket.close()}
        function onActiveThreadIdChanged():void {root.resetSelectionState()}
    }
    function submit(): void {
        const raw = String(message.text ?? "").trim()
        root.slashMenuDismissed = true
        if (/^\/(?:clear|clean|cls)(?:\s+.*)?$/i.test(raw)) {
            AquaMind.clearConversation()
            message.text = ""
            return
        }
        if (AquaMind.sendMessage(message.text)) message.text=""
    }
    onEditingChanged: {
        if (editing) Qt.callLater(() => { if (root.editing) message.forceActiveFocus() })
        else {
            root.profileStage = 0
            root.slashMenuDismissed = false
            root.resetSelectionState()
        }
    }
    onControlsVisibleChanged: if (!controlsVisible) {
        root.profileStage = 0
        root.slashMenuDismissed = false
        message.focus = false
    }
}
