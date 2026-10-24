pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.services
import "../../services"
import qs.modules.common
import qs.modules.common.widgets
import "../abyss/companion"
import qs.modules.abyss.looks
import "../abyss/companion/WullPreferences.js" as Preferences

ContentPage {
    id: root
    settingsPageIndex: 37
    settingsPageName: Translation.tr("Companion")
    property string activeSection: "overview"
    onActiveSectionChanged: if (activeSection === "ai") Ai.ensureInitialized()
    property string previewExpression: "idle"
    property string previewMotion: ""
    property string memoryDraft: ""
    property string memoryScope: "shared"
    property int memoryTtlDays: 7
    property double memoryEditingId: 0
    property bool confirmForgetMemories: false
    readonly property var preferences: Preferences.normalize(Config.options?.abyss?.companion)
    readonly property var personalityOptions: [
        { displayName: Translation.tr("Calm"), value: "calm" },
        { displayName: Translation.tr("Balanced"), value: "balanced" },
        { displayName: Translation.tr("Energetic"), value: "energetic" }
    ]
    readonly property var frequencyOptions: [
        { displayName: Translation.tr("Always visible"), value: "always" },
        { displayName: Translation.tr("Every minute"), value: "frequent" },
        { displayName: Translation.tr("Every 3 minutes"), value: "occasional" },
        { displayName: Translation.tr("Every 10 minutes"), value: "rare" }
    ]
    readonly property var qualityOptions: [
        { displayName: Translation.tr("Performance"), value: "performance" },
        { displayName: Translation.tr("Balanced"), value: "quality" },
        { displayName: Translation.tr("Quality"), value: "detailed" }
    ]
    readonly property var expressionOptions: [
        { displayName: Translation.tr("Idle"), value: "idle" },
        { displayName: Translation.tr("Happy"), value: "happy" },
        { displayName: Translation.tr("Excited"), value: "excited" },
        { displayName: Translation.tr("Thinking"), value: "thinking" },
        { displayName: Translation.tr("Working"), value: "working" },
        { displayName: Translation.tr("Surprised"), value: "surprised" },
        { displayName: Translation.tr("Sleepy"), value: "sleepy" },
        { displayName: Translation.tr("Sad"), value: "sad" },
        { displayName: Translation.tr("Alert"), value: "alert" }
    ]
    readonly property var motionOptions: [
        { displayName: Translation.tr("Rest"), value: "" },
        { displayName: Translation.tr("Walk"), value: "walk" },
        { displayName: Translation.tr("Run"), value: "run" },
        { displayName: Translation.tr("Fly"), value: "fly" },
        { displayName: Translation.tr("Jump"), value: "jump" },
        { displayName: Translation.tr("Dragged"), value: "drag" },
        { displayName: Translation.tr("Reach"), value: "reach" },
        { displayName: Translation.tr("Inspect"), value: "inspect" },
        { displayName: Translation.tr("Wave"), value: "wave" }
    ]

    function setPreference(key, value): void {
        Config.setNestedValue("abyss.companion." + key, value)
    }

    function resetPreferences(): void {
        const values = {}
        const defaults = Preferences.defaults()
        for (const key of Object.keys(defaults))
            values["abyss.companion." + key] = defaults[key]
        Config.setNestedValues(values)
    }

    component ChoiceRow: GridLayout {
        id: choice
        property string label: ""
        property string description: ""
        property var options: []
        property string currentValue: ""
        signal selected(string value)
        Layout.fillWidth: true
        columns: width > 540 ? 2 : 1
        columnSpacing: 24
        rowSpacing: 8
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 3
            StyledText { Layout.fillWidth: true; text: choice.label; wrapMode: Text.WordWrap }
            StyledText {
                Layout.fillWidth: true
                visible: text.length > 0
                text: choice.description
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.smaller
                wrapMode: Text.WordWrap
            }
        }
        StyledComboBox {
            objectName: choice.objectName + "Control"
            Layout.preferredWidth: 224
            Layout.fillWidth: choice.columns === 1
            model: choice.options
            textRole: "displayName"
            settingsSearchLabel: choice.label
            settingsSearchDescription: choice.description
            currentIndex: Math.max(0, choice.options.findIndex(o => o.value === choice.currentValue))
            onActivated: index => choice.selected(choice.options[index].value)
        }
    }

    component PercentageRow: ColumnLayout {
        id: percentage
        property string label: ""
        property string description: ""
        property real value: 1
        property real minimum: 0
        property real maximum: 1
        signal moved(real value)
        Layout.fillWidth: true
        spacing: 4
        RowLayout {
            Layout.fillWidth: true
            StyledText { Layout.fillWidth: true; text: percentage.label }
            StyledText { text: Math.round(percentage.value * 100) + "%"; color: Appearance.colors.colPrimary }
        }
        StyledText {
            Layout.fillWidth: true
            text: percentage.description
            color: Appearance.colors.colSubtext
            font.pixelSize: Appearance.font.pixelSize.smaller
            wrapMode: Text.WordWrap
        }
        StyledSlider {
            objectName: percentage.objectName + "Control"
            from: percentage.minimum
            to: percentage.maximum
            stepSize: 0.01
            value: percentage.value
            tooltipContent: Math.round(value * 100) + "%"
            settingsSearchLabel: percentage.label
            settingsSearchDescription: percentage.description
            onMoved: percentage.moved(value)
        }
    }

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: hero.implicitHeight + 32
        radius: SettingsMaterialPreset.cardRadius
        color: AbyssStyle.surface
        border.width: 1
        border.color: Qt.alpha(AbyssStyle.accent, 0.18)
        GridLayout {
            id: hero
            anchors.fill: parent
            anchors.margins: 16
            columns: width > 600 ? 2 : 1
            columnSpacing: 24
            rowSpacing: 8
            Item {
                Layout.fillWidth: true
                Layout.preferredWidth: 260
                Layout.preferredHeight: 232
                WaterDropletBody {
                    id: preview
                    character: root.preferences.character
                    objectName: "companionPreview"
                    anchors.centerIn: parent
                    width: 76; height: 92
                    scale: Math.min(2, 1.8 * root.preferences.size)
                    transformOrigin: Item.Center
                    expression: root.previewExpression
                    motionAction: root.previewMotion
                    grounded: !["fly","jump","drag"].includes(root.previewMotion)
                    motionScale: Preferences.motionScale(root.preferences.personality)
                    motionEnabled: root.preferences.animationsEnabled && AbyssStyle.motionEnabled && visible
                    effectsEnabled: root.preferences.effectsEnabled && Appearance.effectsEnabled
                        && AbyssStyle.quality !== "performance"
                    renderQuality: AbyssRenderPolicy.wullQuality
                    translucency: root.preferences.translucency
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 12
                StyledText { text: root.preferences.character === "octo" ? "Octo" : "Aqua"; font.pixelSize: 26; font.weight: Font.DemiBold }
                StyledText {
                    Layout.fillWidth: true
                    text: Translation.tr("A little liquid companion for your desktop.")
                    wrapMode: Text.WordWrap
                }
                StyledText {
                    Layout.fillWidth: true
                    text: Translation.tr("Color and reflections follow Abyss Panel Style automatically.")
                    font.pixelSize: Appearance.font.pixelSize.smaller
                    color: Appearance.colors.colSubtext
                    wrapMode: Text.WordWrap
                }
                ChoiceRow {
                    objectName: "companionPreviewExpression"
                    label: Translation.tr("Preview expression")
                    options: root.expressionOptions
                    currentValue: root.previewExpression
                    onSelected: value => root.previewExpression = value
                }
                ChoiceRow {
                    objectName: "companionPreviewMotion"
                    label: Translation.tr("Preview movement")
                    options: root.motionOptions
                    currentValue: root.previewMotion
                    onSelected: value => root.previewMotion = value
                }
            }
        }
    }

    SettingsTaskNavigator {
        showIntro: false
        currentValue: root.activeSection
        onSelected: value => root.activeSection = value
        options: [
            { displayName: Translation.tr("Overview"), icon: "water_drop", value: "overview" },
            { displayName: Translation.tr("Behavior"), icon: "sentiment_satisfied", value: "behavior" },
            { displayName: Translation.tr("Rendering"), icon: "diamond", value: "rendering" },
            { displayName: Translation.tr("AI"), icon: "chat", value: "ai" }
        ]
    }

    SettingsCardSection {
        settingsTaskSection: "overview"
        visible: root.activeSection === "overview"
        title: Translation.tr("Overview")
        icon: "water_drop"
        SettingsGroup {
            enabled: Config.ready
            ChoiceRow {
                objectName: "companionCharacter"
                label: Translation.tr("Companion")
                options: [{displayName:"Aqua",value:"aqua"},{displayName:"Octo",value:"octo"}]
                currentValue: root.preferences.character
                onSelected: value => root.setPreference("character",value)
            }
            SettingsSwitch {
                objectName: "companionAlternate"
                text: Translation.tr("Take turns on the desktop")
                autoToggle: false
                checked: root.preferences.alternateCompanions
                onToggledByUser: checked => root.setPreference("alternateCompanions",checked)
            }
            SettingsSwitch {
                objectName: "companionEnabled"
                text: Translation.tr("Enable Companion")
                description: Translation.tr("Let your companion join the desktop. Right-click to open these settings.")
                autoToggle: false
                checked: root.preferences.enabled
                onToggledByUser: checked => root.setPreference("enabled", checked)
            }
            SettingsSwitch {
                objectName: "companionInteractive"
                text: Translation.tr("Respond to pointer interactions")
                description: Translation.tr("Highlight on hover, react to clicks, and drag your companion around the screen.")
                autoToggle: false
                checked: root.preferences.interactive
                onToggledByUser: checked => root.setPreference("interactive", checked)
            }
            SettingsSwitch {
                objectName: "companionFullscreen"
                text: Translation.tr("Always hide in fullscreen")
                description: Translation.tr("Otherwise, follow the screen edge fullscreen setting.")
                autoToggle: false
                checked: root.preferences.hideInFullscreen
                onToggledByUser: checked => root.setPreference("hideInFullscreen", checked)
            }
        }
    }

    SettingsCardSection {
        settingsTaskSection: "behavior"
        visible: root.activeSection === "behavior"
        title: Translation.tr("Behavior")
        icon: "sentiment_satisfied"
        SettingsGroup {
            enabled: Config.ready
            ChoiceRow {
                objectName: "companionPersonality"
                label: Translation.tr("Personality")
                description: Translation.tr("Calm moves gently. Balanced feels natural. Energetic reacts with bigger movements and more frequent expressions.")
                options: root.personalityOptions
                currentValue: root.preferences.personality
                onSelected: value => root.setPreference("personality", value)
            }
            ChoiceRow {
                objectName: "companionFrequency"
                label: Translation.tr("Appearance frequency")
                description: Translation.tr("Scheduled visits last 20 seconds. Your companion stays while you interact or a task is running.")
                options: root.frequencyOptions
                currentValue: root.preferences.appearanceFrequency
                onSelected: value => root.setPreference("appearanceFrequency", value)
            }
            SettingsSwitch {
                objectName: "companionExploreFeatures"
                text: Translation.tr("Explore Abyss features")
                description: Translation.tr("Occasionally open and explore panels, then close them. Your companion leaves them open when you start using them.")
                autoToggle: false
                checked: root.preferences.exploreFeatures
                onToggledByUser: checked => root.setPreference("exploreFeatures", checked)
            }
            SettingsSwitch {
                objectName: "companionMotion"
                text: Translation.tr("Companion animations")
                description: Translation.tr("Walk on surfaces, float through open space, and react naturally. Respects the shell motion setting.")
                autoToggle: false
                checked: root.preferences.animationsEnabled
                onToggledByUser: checked => root.setPreference("animationsEnabled", checked)
            }
            SettingsSwitch {
                objectName:"companionContext"
                text:Translation.tr("Share coarse activity with Companion")
                description:Translation.tr("Off by default. Shares only the categories and states you choose below. Stops while locked, fullscreen or Do Not Disturb.")
                autoToggle:false;checked:root.preferences.contextEnabled
                onToggledByUser:checked=>root.setPreference("contextEnabled",checked)
            }
            SettingsSwitch {
                objectName:"companionPocket"
                text:Translation.tr("Enable Pocket")
                description:Translation.tr("Save local file names, paths, sizes and dates when you choose or drop files. File contents are never read or sent to AI. Removing a reference keeps the original file.")
                autoToggle:false;checked:root.preferences.pocketEnabled
                onToggledByUser:checked=>root.setPreference("pocketEnabled",checked)
            }
            SettingsSwitch {
                objectName:"companionContextFocus"
                text:Translation.tr("Share the focused app category")
                description:Translation.tr("Only terminal, editor or other. Window titles, files and app content stay private.")
                enabled:root.preferences.contextEnabled
                autoToggle:false;checked:root.preferences.contextFocusEnabled
                onToggledByUser:checked=>root.setPreference("contextFocusEnabled",checked)
            }
            SettingsSwitch {
                objectName:"companionContextAi"
                text:Translation.tr("Share Alis AI activity")
                description:Translation.tr("Only working, waiting for approval and finished. No prompts, replies, tools or permission grants.")
                enabled:root.preferences.contextEnabled
                autoToggle:false;checked:root.preferences.contextAiEnabled
                onToggledByUser:checked=>root.setPreference("contextAiEnabled",checked)
            }
            SettingsSwitch {
                objectName:"companionAmbient"
                text:Translation.tr("Quiet ambient reactions")
                description:Translation.tr("Small expressions, at most once every two minutes.")
                autoToggle:false;checked:root.preferences.ambientEnabled
                onToggledByUser:checked=>root.setPreference("ambientEnabled",checked)
            }
            SettingsSwitch {
                objectName:"companionAmbientFocus"
                text:Translation.tr("Follow Focus sessions")
                description:Translation.tr("Uses the existing Focus timer; stays quiet during work.")
                enabled:root.preferences.ambientEnabled
                autoToggle:false;checked:root.preferences.ambientFocusEnabled
                onToggledByUser:checked=>root.setPreference("ambientFocusEnabled",checked)
            }
            SettingsSwitch {
                objectName:"companionAmbientBattery"
                text:Translation.tr("React to battery state")
                description:Translation.tr("Only low battery or charging; no device details.")
                enabled:root.preferences.ambientEnabled
                autoToggle:false;checked:root.preferences.ambientBatteryEnabled
                onToggledByUser:checked=>root.setPreference("ambientBatteryEnabled",checked)
            }
            SettingsSwitch {
                objectName:"companionAmbientMedia"
                text:Translation.tr("React to playback state")
                description:Translation.tr("Only playing or paused; no titles, artists or media control.")
                enabled:root.preferences.ambientEnabled
                autoToggle:false;checked:root.preferences.ambientMediaEnabled
                onToggledByUser:checked=>root.setPreference("ambientMediaEnabled",checked)
            }
        }
    }

    SettingsCardSection {
        settingsTaskSection: "rendering"
        visible: root.activeSection === "rendering"
        title: Translation.tr("Rendering")
        icon: "diamond"
        SettingsGroup {
            enabled: Config.ready
            PercentageRow {
                objectName: "companionSize"
                label: Translation.tr("Companion size")
                description: Translation.tr("Keep your companion small or give it a little more room.")
                value: root.preferences.size
                minimum: 0.65; maximum: 1.5
                onMoved: value => root.setPreference("size", value)
            }
            ChoiceRow {
                objectName: "companionQuality"
                label: Translation.tr("Rendering quality")
                description: Translation.tr("Performance uses simpler lighting. Balanced adds liquid detail. Quality adds deeper reflections.")
                enabled: !root.preferences.autoQuality
                options: root.qualityOptions
                currentValue: root.preferences.renderQuality
                onSelected: value => root.setPreference("renderQuality", value)
            }
            SettingsSwitch {
                text: Translation.tr("Follow power profile")
                description: Translation.tr("Use Performance in Power Saver and Balanced in other power profiles.")
                autoToggle: false
                checked: root.preferences.autoQuality
                onToggledByUser: checked => root.setPreference("autoQuality",checked)
            }
            StyledText {
                text: Translation.tr("Current render quality")+": "+AbyssRenderPolicy.wullQualityLabel
                color: Appearance.colors.colSubtext
            }
            PercentageRow {
                objectName: "companionTranslucency"
                label: Translation.tr("Translucency")
                description: Translation.tr("Let a little of the background show through the liquid. Eyes and bright reflections stay clear.")
                value: root.preferences.translucency
                minimum: 0
                maximum: 0.35
                onMoved: value => root.setPreference("translucency", value)
            }
            SettingsSwitch {
                objectName: "companionEffects"
                text: Translation.tr("Bubbles and floor reflections")
                description: Translation.tr("Add floating bubbles, sparkle and the liquid reflection beneath your companion. Performance uses a simpler floor effect. Respects the shell effects setting.")
                autoToggle: false
                checked: root.preferences.effectsEnabled
                onToggledByUser: checked => root.setPreference("effectsEnabled", checked)
            }
        }
    }

    DialogButton {
        objectName: "companionReset"
        Layout.alignment: Qt.AlignRight
        enabled: Config.ready
        buttonText: Translation.tr("Reset Companion settings")
        onClicked: root.resetPreferences()
    }
    SettingsCardSection {
        settingsTaskSection:"ai"
        visible:root.activeSection==="ai"
        title:Translation.tr("AI")
        icon:"chat"
        SettingsGroup {
            SettingsSwitch {
                text:Translation.tr("Talk clouds")
                autoToggle:false;checked:WullMind.talkEnabled
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.talkEnabled",checked)
            }
            SettingsSwitch {
                objectName:"wullAllowCloudModels"
                text:Translation.tr("Allow cloud models")
                autoToggle:false;checked:!WullMind.localOnly
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.localOnly",!checked)
            }
            StyledText {
                Layout.fillWidth:true
                text:WullMind.localOnly ? "Local only" : "Cloud models allowed"
                color:Appearance.colors.colSubtext
            }
            SettingsSwitch {
                objectName:"wullEnableProactive"
                text:Translation.tr("Allow proactive check-ins and reminders")
                description:Translation.tr("Off by default. Pauses during Focus, fullscreen, games, dialogs and quiet hours. Repeatedly ignored offers pause until you interact or the next day.")
                autoToggle:false;checked:WullMind.options.proactiveEnabled === true
                enabled:WullMind.options.proactiveEnabled !== undefined
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.proactiveEnabled",checked)
            }
            ChoiceRow {
                objectName:"wullProactiveFrequency"
                label:Translation.tr("Check-ins and reminders")
                options:[
                    {displayName:Translation.tr("Rarely while idle"),value:"rare"},
                    {displayName:Translation.tr("Occasionally while idle"),value:"occasional"},
                    {displayName:Translation.tr("Regularly while idle"),value:"regular"},
                    {displayName:Translation.tr("Often while idle"),value:"often"},
                    {displayName:Translation.tr("Only when I chat"),value:"manual"}
                ]
                currentValue:WullMind.proactive
                onSelected:value=>Config.setNestedValue("abyss.companionMind.proactive",value)
            }
            ChoiceRow {
                label:Translation.tr("Daily offer limit")
                options:[{displayName:"2",value:2},{displayName:"4",value:4},{displayName:"6",value:6},{displayName:"10",value:10}]
                currentValue:WullMind.options.proactiveDailyCap ?? 6
                onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveDailyCap",value)
            }
            ChoiceRow {
                label:Translation.tr("Limit for each kind of offer")
                options:[{displayName:"1",value:1},{displayName:"2",value:2},{displayName:"3",value:3}]
                currentValue:WullMind.options.proactiveEventCap ?? 3
                onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveEventCap",value)
            }
            SettingsSwitch {
                text:Translation.tr("Quiet hours")
                autoToggle:false;checked:WullMind.options.proactiveQuietEnabled !== false
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.proactiveQuietEnabled",checked)
            }
            ChoiceRow {
                label:Translation.tr("Quiet hours start")
                options:[{displayName:"20:00",value:1200},{displayName:"22:00",value:1320},{displayName:"00:00",value:0}]
                currentValue:WullMind.options.proactiveQuietStart ?? 1320
                onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveQuietStart",value)
            }
            ChoiceRow {
                label:Translation.tr("Quiet hours end")
                options:[{displayName:"06:00",value:360},{displayName:"08:00",value:480},{displayName:"10:00",value:600}]
                currentValue:WullMind.options.proactiveQuietEnd ?? 480
                onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveQuietEnd",value)
            }
            SettingsSwitch {
                objectName:"wullTodoReminders"
                text:Translation.tr("Read Todo items for reminders")
                autoToggle:false;checked:WullMind.todoRemindersEnabled
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.todoRemindersEnabled",checked)
            }
            SettingsSwitch {
                objectName:"wullCalendarReminders"
                text:Translation.tr("Read calendar events for reminders")
                autoToggle:false;checked:WullMind.calendarRemindersEnabled
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.calendarRemindersEnabled",checked)
            }
            SettingsSwitch {
                objectName:"wullJournalReminders"
                text:Translation.tr("Read today's journal for reminders")
                autoToggle:false;checked:WullMind.journalRemindersEnabled
                enabled:WullMind.obsidianEnabled
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.journalRemindersEnabled",checked)
            }
            SettingsSwitch {
                objectName:"wullReminderNotifications"
                text:Translation.tr("Show reminder notifications")
                description:Translation.tr("Also send consented reminders to the notification center. Off by default.")
                autoToggle:false;checked:WullMind.reminderNotificationsEnabled
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.reminderNotificationsEnabled",checked)
            }
            StyledText {
                Layout.fillWidth:true;wrapMode:Text.WordWrap
                text:Translation.tr("Providers and connections are shared with AI settings.")
                color:Appearance.colors.colSubtext
            }
            SettingsSwitch {
                objectName:"wullDialogueConsent"
                text:Translation.tr("Personalize local dialogue")
                description:Translation.tr("Use your chosen tone and today's explicit check-in for local replies. Familiarity is your choice. No relationship score, penalties for absence or pressure to return.")
                autoToggle:false;checked:WullMind.options.bondEnabled===true
                enabled:WullMind.options.bondEnabled!==undefined
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.bondEnabled",checked)
            }
            ChoiceRow {
                label:Translation.tr("Dialogue tone")
                options:[{displayName:Translation.tr("Gentle"),value:"gentle"},
                    {displayName:Translation.tr("Witty"),value:"witty"},{displayName:Translation.tr("Concise"),value:"concise"}]
                currentValue:WullMind.options.dialogueTone ?? "gentle"
                onSelected:value=>Config.setNestedValue("abyss.companionMind.dialogueTone",value)
            }
            ChoiceRow {
                label:Translation.tr("Familiarity")
                options:[{displayName:Translation.tr("Neutral"),value:"neutral"},
                    {displayName:Translation.tr("Familiar coworker"),value:"familiar"}]
                currentValue:WullMind.options.familiarity ?? "neutral"
                onSelected:value=>Config.setNestedValue("abyss.companionMind.familiarity",value)
            }
            DialogButton {
                objectName:"wullResetDialogue"
                buttonText:Translation.tr("Reset dialogue preferences")
                onClicked:WullMind.resetDialogue()
            }
            RowLayout {
                DialogButton {buttonText:Translation.tr("Open chat");enabled:root.preferences.enabled;onClicked:WullMind.openChat()}
                DialogButton {buttonText:Translation.tr("Clear conversation");onClicked:WullMind.clearConversation()}
            }
            StyledText {
                Layout.fillWidth:true
                text:"Model"
                color:Appearance.colors.colSubtext
            }
            StyledComboBox {
                objectName:"wullCompanionModelSelector"
                Layout.fillWidth:true
                model:WullMind.selectableModels
                textRole:"label"
                currentIndex:Math.max(0,WullMind.selectableModels.findIndex(item=>item.name===String(Config.options?.abyss?.companionMind?.model ?? "")))
                displayText:currentIndex>=0 ? (WullMind.selectableModels[currentIndex].label ?? WullMind.selectableModels[currentIndex].name) : "Use the AI tab model"
                onActivated:index=>{
                    const item=WullMind.selectableModels[index]
                    if(item)WullMind.selectModel(item)
                }
            }
            RowLayout {
                Layout.fillWidth:true
                StyledText {Layout.fillWidth:true;text:"Thinking effort";color:Appearance.colors.colSubtext}
                StyledText {text:WullMind.thinkingEffortLabel;color:Appearance.colors.colPrimary}
            }
            StyledSlider {
                objectName:"wullCompanionThinkingEffort"
                Layout.fillWidth:true
                from:0;to:3;stepSize:1
                value:Math.max(0,WullMind.thinkingLevels.findIndex(level=>level.value===WullMind.effectiveThinkingEffort))
                enabled:WullMind.thinkingSupported && !WullMind.busy
                tooltipContent:WullMind.thinkingEffortLabel
                onMoved:{
                    const levels=["off","low","medium","high"]
                    WullMind.setThinkingEffort(levels[Math.max(0,Math.min(3,Math.round(value)))])
                }
            }
            StyledText {
                visible:WullMind.model.length>0 && !WullMind.thinkingSupported
                Layout.fillWidth:true;wrapMode:Text.WordWrap
                text:Translation.tr("This model uses its provider default. Choose a supported reasoning model to set effort.")
                color:Appearance.colors.colSubtext
            }
            SettingsSwitch {
                text:Translation.tr("Connect Obsidian")
                autoToggle:false;checked:WullMind.obsidianEnabled
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.obsidianEnabled",checked)
            }
            StyledText {Layout.fillWidth:true;wrapMode:Text.WordWrap
                text:Translation.tr("Obsidian vault")+": "+(Todo.sharedVaultPath || Translation.tr("Not configured"))}
            RowLayout {
                DialogButton {buttonText:Translation.tr("Read today's context");enabled:WullMind.obsidianEnabled && !WullMind.busy;onClicked:WullMind.refreshJournal()}
                DialogButton {buttonText:Translation.tr("Open journal");enabled:!!WullMind.journal.journalPath;onClicked:WullMind.openJournal()}
            }
            SettingsSwitch {
                objectName:"wullEnableMemory"
                text:Translation.tr("Use saved memories with local models")
                description:Translation.tr("Only entries you save here are remembered. No automatic extraction from chat. Turning this off stops use and keeps your records available for review or deletion.")
                autoToggle:false;checked:WullMind.memoryEnabled
                enabled:WullMind.options.memoryEnabled !== undefined && !WullMind.busy
                onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.memoryEnabled",checked)
            }
            Rectangle {
                Layout.fillWidth:true
                implicitHeight:48;radius:10
                color:Appearance.colors.colLayer1
                border.width:1;border.color:Qt.alpha(Appearance.colors.colPrimary,memoryInput.activeFocus ? .8 : .3)
                StyledTextInput {
                    id:memoryInput
                    objectName:"wullMemoryDraft"
                    anchors.fill:parent;anchors.margins:12
                    enabled:WullMind.memoryEnabled && !WullMind.busy
                    clip:true;selectByMouse:true;activeFocusOnTab:true
                    Accessible.name:Translation.tr("An explicit preference to remember")
                    maximumLength:320;text:root.memoryDraft
                    onTextEdited:root.memoryDraft=text
                }
                StyledText {
                    anchors.fill:memoryInput;verticalAlignment:Text.AlignVCenter
                    visible:!memoryInput.text;elide:Text.ElideRight
                    text:Translation.tr("An explicit preference to remember")
                    color:Appearance.colors.colSubtext
                }
            }
            ChoiceRow {
                label:Translation.tr("Remember for")
                options:[{displayName:"Aqua",value:"aqua"},{displayName:"Octo",value:"octo"},{displayName:Translation.tr("Both"),value:"shared"}]
                currentValue:root.memoryScope
                onSelected:value=>root.memoryScope=value
            }
            ChoiceRow {
                label:Translation.tr("Keep memory")
                options:[{displayName:Translation.tr("7 days"),value:7},{displayName:Translation.tr("30 days"),value:30},{displayName:Translation.tr("Until I forget it"),value:0}]
                currentValue:root.memoryTtlDays
                onSelected:value=>root.memoryTtlDays=value
            }
            RowLayout {
                DialogButton {
                    objectName:"wullSaveMemory"
                    buttonText:root.memoryEditingId ? Translation.tr("Save memory changes") : Translation.tr("Save memory")
                    enabled:WullMind.memoryEnabled && !WullMind.busy && root.memoryDraft.trim().length>0
                    onClicked:if(WullMind.saveMemory(root.memoryDraft,root.memoryScope,root.memoryTtlDays,root.memoryEditingId)) {
                        root.memoryDraft="";root.memoryEditingId=0
                    }
                }
                DialogButton {
                    objectName:"wullReviewMemory"
                    buttonText:Translation.tr("Review saved memories");enabled:!WullMind.busy
                    onClicked:WullMind.refreshMemory()
                }
            }
            StyledText {
                Layout.fillWidth:true;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                text:WullMind.memoryNotice || Translation.tr("Up to 128 entries. Expiry stops use. Forgetting removes logical records; backups, chat and Obsidian copies may remain.")
                color:Appearance.colors.colSubtext
            }
            Repeater {
                model:WullMind.memories
                ColumnLayout {
                    required property var modelData
                    Layout.fillWidth:true
                    StyledText {
                        Layout.fillWidth:true;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                        text:modelData.scope+" · "+modelData.text
                    }
                    StyledText {
                        Layout.fillWidth:true;textFormat:Text.PlainText;color:Appearance.colors.colSubtext
                        text:modelData.expires_ms===0 ? Translation.tr("Until forgotten")
                            : (modelData.expires_ms<=Date.now() ? Translation.tr("Expired") : Translation.tr("Expires"))
                                +" · "+Qt.formatDateTime(new Date(modelData.expires_ms),"yyyy-MM-dd")
                    }
                    RowLayout {
                        DialogButton {
                            buttonText:Translation.tr("Edit");enabled:WullMind.memoryEnabled && !WullMind.busy
                            onClicked:{root.memoryDraft=modelData.text;root.memoryScope=modelData.scope;root.memoryEditingId=modelData.id}
                        }
                        DialogButton {
                            buttonText:Translation.tr("Forget");enabled:!WullMind.busy
                            onClicked:WullMind.forgetMemory(modelData.id)
                        }
                    }
                }
            }
            DialogButton {
                objectName:"wullForgetAllMemory"
                buttonText:root.confirmForgetMemories ? Translation.tr("Confirm forgetting all saved memories") : Translation.tr("Forget all saved memories")
                enabled:!WullMind.busy && WullMind.memories.length>0
                onClicked:{
                    if(root.confirmForgetMemories){WullMind.forgetAllMemories();root.confirmForgetMemories=false}
                    else root.confirmForgetMemories=true
                }
            }
        }
    }

}
