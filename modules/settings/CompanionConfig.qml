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
    readonly property real availableRowWidth: Math.max(0, root.width - 2 * root._horizontalMargin - 48)
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

    component CompactToggle: SettingsSwitch {
        id: compactToggle
        property string tooltipText: ""
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        autoToggle: false
        activeFocusOnTab: true
        Accessible.description: description.length > 0 ? description : tooltipText
        StyledToolTip {
            text: compactToggle.tooltipText.length > 0 ? compactToggle.tooltipText : compactToggle.text
            extraVisibleCondition: compactToggle.hovered && text.length > 0
        }
    }

    component CompactIconButton: RippleButton {
        id: iconBtn
        property string iconName: ""
        property string tooltipText: ""
        implicitWidth: 34; implicitHeight: 34; buttonRadius: 17
        activeFocusOnTab: true
        colBackground: Appearance.colors.colLayer2
        colBackgroundHover: Appearance.colors.colLayer2Hover
        Accessible.name: tooltipText
        contentItem: MaterialSymbol {
            anchors.centerIn: parent
            text: iconBtn.iconName
            iconSize: 18
            color: iconBtn.enabled ? Appearance.colors.colOnLayer1 : Appearance.colors.colSubtext
        }
        StyledToolTip {
            text: iconBtn.tooltipText
            extraVisibleCondition: iconBtn.hovered && text.length > 0
        }
    }

    component CompactIconToggle: RippleButton {
        id: iconToggle
        property string buttonIcon: ""
        property string tooltipText: ""
        signal toggledByUser(bool checked)
        implicitWidth: 36; implicitHeight: 36; buttonRadius: 11
        activeFocusOnTab: true
        colBackground: iconToggle.checked ? Qt.alpha(Appearance.colors.colPrimary, 0.22) : Appearance.colors.colLayer2
        colBackgroundHover: iconToggle.checked ? Qt.alpha(Appearance.colors.colPrimary, 0.32) : Appearance.colors.colLayer2Hover
        Accessible.checkable: true
        Accessible.checked: iconToggle.checked
        Accessible.name: iconToggle.text
        Accessible.description: iconToggle.tooltipText
        onClicked: iconToggle.toggledByUser(!iconToggle.checked)
        Rectangle {
            anchors.fill: parent
            radius: iconToggle.buttonRadius
            color: "transparent"
            border.width: iconToggle.checked ? 1.4 : 1
            border.color: iconToggle.checked ? Appearance.colors.colPrimary : Qt.alpha(Appearance.colors.colSubtext, 0.22)
        }
        contentItem: MaterialSymbol {
            anchors.centerIn: parent
            text: iconToggle.buttonIcon
            iconSize: 18
            color: !iconToggle.enabled ? Qt.alpha(Appearance.colors.colSubtext, 0.4)
                : iconToggle.checked ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
        }
        StyledToolTip {
            text: iconToggle.text + " (" + (iconToggle.checked ? "On" : "Off") + ")"
                + (iconToggle.tooltipText.length > 0 ? " · " + iconToggle.tooltipText : "")
            extraVisibleCondition: iconToggle.hovered
        }
    }

    component InlineChoice: RowLayout {
        id: inlineChoice
        property string label: ""
        property string description: ""
        property var options: []
        property var currentValue: ""
        property int comboMinWidth: 96
        signal selected(var value)
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: 6
        StyledText {
            visible: inlineChoice.label.length > 0
            text: inlineChoice.label
            color: Appearance.colors.colSubtext
            font.pixelSize: Appearance.font.pixelSize.smaller
        }
        StyledComboBox {
            objectName: inlineChoice.objectName ? inlineChoice.objectName + "Control" : ""
            Layout.fillWidth: true
            Layout.minimumWidth: inlineChoice.comboMinWidth
            model: inlineChoice.options
            textRole: "displayName"
            settingsSearchLabel: inlineChoice.label
            settingsSearchDescription: inlineChoice.description
            currentIndex: Math.max(0, inlineChoice.options.findIndex(o => o.value === inlineChoice.currentValue))
            onActivated: index => inlineChoice.selected(inlineChoice.options[index].value)
        }
    }

    component CompactChoice: ColumnLayout {
        id: compactChoice
        property string label: ""
        property string description: ""
        property var options: []
        property var currentValue: ""
        signal selected(var value)
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: 4
        StyledText {
            Layout.fillWidth: true
            text: compactChoice.label
            color: Appearance.colors.colSubtext
            font.pixelSize: Appearance.font.pixelSize.smaller
            elide: Text.ElideRight
        }
        StyledComboBox {
            objectName: compactChoice.objectName ? compactChoice.objectName + "Control" : ""
            Layout.fillWidth: true
            model: compactChoice.options
            textRole: "displayName"
            settingsSearchLabel: compactChoice.label
            settingsSearchDescription: compactChoice.description
            currentIndex: Math.max(0, compactChoice.options.findIndex(o => o.value === compactChoice.currentValue))
            onActivated: index => compactChoice.selected(compactChoice.options[index].value)
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
            GridLayout {
                objectName: "companionOverviewTogglesGrid"
                Layout.fillWidth: true
                columns: root.availableRowWidth > 440 ? 2 : 1
                columnSpacing: 12; rowSpacing: 8
                CompactToggle {
                    objectName: "companionEnabled"
                    buttonIcon: "visibility"
                    text: Translation.tr("Enable Companion")
                    tooltipText: Translation.tr("Let your companion join the desktop. Right-click to open these settings.")
                    checked: root.preferences.enabled
                    onToggledByUser: checked => root.setPreference("enabled", checked)
                }
                CompactToggle {
                    objectName: "companionAlternate"
                    buttonIcon: "swap_horiz"
                    text: Translation.tr("Take turns on the desktop")
                    tooltipText: "Alternate between Aqua and Octo across desktop visits."
                    checked: root.preferences.alternateCompanions
                    onToggledByUser: checked => root.setPreference("alternateCompanions",checked)
                }
                CompactToggle {
                    objectName: "companionInteractive"
                    buttonIcon: "touch_app"
                    text: Translation.tr("Respond to pointer interactions")
                    tooltipText: Translation.tr("Highlight on hover, react to clicks, and drag your companion around the screen.")
                    checked: root.preferences.interactive
                    onToggledByUser: checked => root.setPreference("interactive", checked)
                }
                CompactToggle {
                    objectName: "companionFullscreen"
                    buttonIcon: "fullscreen_exit"
                    text: Translation.tr("Always hide in fullscreen")
                    tooltipText: Translation.tr("Otherwise, follow the screen edge fullscreen setting.")
                    checked: root.preferences.hideInFullscreen
                    onToggledByUser: checked => root.setPreference("hideInFullscreen", checked)
                }
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
            GridLayout {
                objectName: "companionBehaviorChoicesGrid"
                Layout.fillWidth: true
                columns: root.availableRowWidth > 480 ? 2 : 1
                uniformCellWidths: columns > 1
                columnSpacing: 16; rowSpacing: 8
                CompactChoice {
                    objectName: "companionPersonality"
                    label: Translation.tr("Personality")
                    description: Translation.tr("Calm moves gently. Balanced feels natural. Energetic reacts with bigger movements and more frequent expressions.")
                    options: root.personalityOptions
                    currentValue: root.preferences.personality
                    onSelected: value => root.setPreference("personality", value)
                }
                CompactChoice {
                    objectName: "companionFrequency"
                    label: Translation.tr("Appearance frequency")
                    description: Translation.tr("Scheduled visits last 20 seconds. Your companion stays while you interact or a task is running.")
                    options: root.frequencyOptions
                    currentValue: root.preferences.appearanceFrequency
                    onSelected: value => root.setPreference("appearanceFrequency", value)
                }
            }
            GridLayout {
                objectName: "companionBehaviorTogglesGrid"
                Layout.fillWidth: true
                columns: root.availableRowWidth > 560 ? 2 : 1
                uniformCellWidths: columns > 1
                columnSpacing: 12; rowSpacing: 8
                CompactToggle {
                    objectName: "companionMotion"
                    buttonIcon: "animation"
                    text: Translation.tr("Companion animations")
                    tooltipText: Translation.tr("Walk on surfaces, float through open space, and react naturally. Respects the shell motion setting.")
                    checked: root.preferences.animationsEnabled
                    onToggledByUser: checked => root.setPreference("animationsEnabled", checked)
                }
                CompactToggle {
                    objectName: "companionExploreFeatures"
                    buttonIcon: "explore"
                    text: Translation.tr("Explore Abyss features")
                    tooltipText: Translation.tr("Occasionally open and explore panels, then close them. Your companion leaves them open when you start using them.")
                    checked: root.preferences.exploreFeatures
                    onToggledByUser: checked => root.setPreference("exploreFeatures", checked)
                }
                CompactToggle {
                    objectName: "companionPocket"
                    buttonIcon: "inventory_2"
                    text: Translation.tr("Enable Pocket")
                    tooltipText: Translation.tr("Save local file names, paths, sizes and dates when you choose or drop files. File contents are never read or sent to AI. Removing a reference keeps the original file.")
                    checked: root.preferences.pocketEnabled
                    onToggledByUser: checked => root.setPreference("pocketEnabled", checked)
                }
                CompactToggle {
                    objectName: "companionContext"
                    buttonIcon: "sensors"
                    text: Translation.tr("Share coarse activity with Companion")
                    tooltipText: Translation.tr("Off by default. Shares only the categories and states you choose below. Stops while locked, fullscreen or Do Not Disturb.")
                    checked: root.preferences.contextEnabled
                    onToggledByUser: checked => root.setPreference("contextEnabled", checked)
                }
                CompactToggle {
                    objectName: "companionContextFocus"
                    buttonIcon: "terminal"
                    text: Translation.tr("Share the focused app category")
                    tooltipText: Translation.tr("Only terminal, editor or other. Window titles, files and app content stay private.")
                    enabled: root.preferences.contextEnabled
                    checked: root.preferences.contextFocusEnabled
                    onToggledByUser: checked => root.setPreference("contextFocusEnabled", checked)
                }
                CompactToggle {
                    objectName: "companionContextAi"
                    buttonIcon: "psychology"
                    text: Translation.tr("Share Alis AI activity")
                    tooltipText: Translation.tr("Only working, waiting for approval and finished. No prompts, replies, tools or permission grants.")
                    enabled: root.preferences.contextEnabled
                    checked: root.preferences.contextAiEnabled
                    onToggledByUser: checked => root.setPreference("contextAiEnabled", checked)
                }
                CompactToggle {
                    objectName: "companionAmbient"
                    buttonIcon: "auto_awesome"
                    text: Translation.tr("Quiet ambient reactions")
                    tooltipText: Translation.tr("Small expressions, at most once every two minutes.")
                    checked: root.preferences.ambientEnabled
                    onToggledByUser: checked => root.setPreference("ambientEnabled", checked)
                }
                CompactToggle {
                    objectName: "companionAmbientFocus"
                    buttonIcon: "timer"
                    text: Translation.tr("Follow Focus sessions")
                    tooltipText: Translation.tr("Uses the existing Focus timer; stays quiet during work.")
                    enabled: root.preferences.ambientEnabled
                    checked: root.preferences.ambientFocusEnabled
                    onToggledByUser: checked => root.setPreference("ambientFocusEnabled", checked)
                }
                CompactToggle {
                    objectName: "companionAmbientBattery"
                    buttonIcon: "battery_charging_full"
                    text: Translation.tr("React to battery state")
                    tooltipText: Translation.tr("Only low battery or charging; no device details.")
                    enabled: root.preferences.ambientEnabled
                    checked: root.preferences.ambientBatteryEnabled
                    onToggledByUser: checked => root.setPreference("ambientBatteryEnabled", checked)
                }
                CompactToggle {
                    objectName: "companionAmbientMedia"
                    buttonIcon: "music_note"
                    text: Translation.tr("React to playback state")
                    tooltipText: Translation.tr("Only playing or paused; no titles, artists or media control.")
                    enabled: root.preferences.ambientEnabled
                    checked: root.preferences.ambientMediaEnabled
                    onToggledByUser: checked => root.setPreference("ambientMediaEnabled", checked)
                }
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
            GridLayout {
                objectName: "companionRenderingSlidersGrid"
                Layout.fillWidth: true
                columns: root.availableRowWidth > 520 ? 2 : 1
                uniformCellWidths: columns > 1
                columnSpacing: 16; rowSpacing: 8
                PercentageRow {
                    objectName: "companionSize"
                    label: Translation.tr("Companion size")
                    description: Translation.tr("Keep your companion small or give it a little more room.")
                    value: root.preferences.size
                    minimum: 0.65; maximum: 1.5
                    onMoved: value => root.setPreference("size", value)
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
            }
            ChoiceRow {
                objectName: "companionQuality"
                label: Translation.tr("Rendering quality")
                description: Translation.tr("Current render quality") + ": " + AbyssRenderPolicy.wullQualityLabel
                enabled: !root.preferences.autoQuality
                options: root.qualityOptions
                currentValue: root.preferences.renderQuality
                onSelected: value => root.setPreference("renderQuality", value)
            }
            GridLayout {
                objectName: "companionRenderingTogglesGrid"
                Layout.fillWidth: true
                columns: root.availableRowWidth > 440 ? 2 : 1
                uniformCellWidths: columns > 1
                columnSpacing: 12; rowSpacing: 8
                CompactToggle {
                    buttonIcon: "bolt"
                    text: Translation.tr("Follow power profile")
                    tooltipText: Translation.tr("Use Performance in Power Saver and Balanced in other power profiles.")
                    checked: root.preferences.autoQuality
                    onToggledByUser: checked => root.setPreference("autoQuality",checked)
                }
                CompactToggle {
                    objectName: "companionEffects"
                    buttonIcon: "auto_fix_high"
                    text: Translation.tr("Bubbles and floor reflections")
                    tooltipText: Translation.tr("Add floating bubbles, sparkle and the liquid reflection beneath your companion. Performance uses a simpler floor effect. Respects the shell effects setting.")
                    checked: root.preferences.effectsEnabled
                    onToggledByUser: checked => root.setPreference("effectsEnabled", checked)
                }
            }
        }
    }

    CompactIconButton {
        objectName: "companionReset"
        Layout.alignment: Qt.AlignRight
        enabled: Config.ready
        iconName: "restart_alt"
        tooltipText: Translation.tr("Reset Companion settings")
        onClicked: root.resetPreferences()
    }
    SettingsCardSection {
        settingsTaskSection:"ai"
        visible:root.activeSection==="ai"
        title:Translation.tr("AI")
        icon:"chat"
        SettingsGroup {
            objectName:"wullAiSettingsGroup"

            // ROW 1 (2 Columns): Model & Permissions (Left) | Thinking Effort & Clear (Right)
            GridLayout {
                objectName:"wullAiModelGrid"
                Layout.fillWidth:true
                columns:root.availableRowWidth>520 ? 2 : 1
                uniformCellWidths:columns > 1
                columnSpacing:16;rowSpacing:8
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:8
                    CompactIconToggle {
                        objectName:"wullTalkClouds"
                        buttonIcon:"chat_bubble"
                        text:Translation.tr("Talk clouds")
                        tooltipText:"Show speech bubbles and quick chat above your companion."
                        checked:WullMind.talkEnabled
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.talkEnabled",checked)
                    }
                    CompactIconToggle {
                        objectName:"wullAllowCloudModels"
                        buttonIcon:"cloud"
                        text:Translation.tr("Allow cloud models")
                        tooltipText:"Keep off to restrict Companion strictly to verified local models."
                        checked:!WullMind.localOnly
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.localOnly",!checked)
                    }
                    StyledComboBox {
                        objectName:"wullCompanionModelSelector"
                        Layout.fillWidth:true
                        Layout.minimumWidth:120
                        model:WullMind.selectableModels
                        textRole:"label"
                        currentIndex:Math.max(0,WullMind.selectableModels.findIndex(item=>item.name===String(Config.options?.abyss?.companionMind?.model ?? "")))
                        displayText:currentIndex>=0 ? (WullMind.selectableModels[currentIndex].label ?? WullMind.selectableModels[currentIndex].name) : "Use the AI tab model"
                        onActivated:index=>{
                            const item=WullMind.selectableModels[index]
                            if(item)WullMind.selectModel(item)
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:10
                    ColumnLayout {
                        Layout.fillWidth:true;spacing:2
                        RowLayout {
                            Layout.fillWidth:true
                            StyledText {Layout.fillWidth:true;text:"Thinking effort";color:Appearance.colors.colSubtext;font.pixelSize:Appearance.font.pixelSize.smaller}
                            StyledText {text:WullMind.thinkingEffortLabel;color:Appearance.colors.colPrimary;font.pixelSize:Appearance.font.pixelSize.smaller}
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
                    }
                    Rectangle {
                        implicitWidth:36;implicitHeight:36;radius:11
                        color:Appearance.colors.colLayer2
                        MaterialSymbol {
                            anchors.centerIn:parent
                            text:WullMind.localOnly ? "verified_user" : "cloud_done"
                            iconSize:18;color:Appearance.colors.colPrimary
                        }
                        StyledText {
                            objectName:"wullAiPrivacyStatus"
                            visible:false
                            text:WullMind.localOnly ? "Local only" : "Cloud models allowed"
                        }
                        MouseArea {
                            id:privacyHover;anchors.fill:parent;hoverEnabled:true;acceptedButtons:Qt.NoButton
                        }
                        StyledToolTip {
                            text:(WullMind.localOnly ? "Local only" : "Cloud models allowed") + " · " + Translation.tr("Providers and connections are shared with AI settings.")
                            extraVisibleCondition:privacyHover.containsMouse
                        }
                    }
                    CompactIconButton {
                        objectName:"wullClearConversation"
                        iconName:"delete_sweep"
                        tooltipText:Translation.tr("Clear conversation")
                        onClicked:WullMind.clearConversation()
                    }
                }
            }

            // ROW 2 (2 Columns): Proactive Check-ins & Sources (Left) | Quiet Hours Inline Start/End (Right)
            GridLayout {
                objectName:"wullQuietHoursGrid"
                Layout.fillWidth:true
                columns:root.availableRowWidth>520 ? 2 : 1
                uniformCellWidths:columns > 1
                columnSpacing:16;rowSpacing:8
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:6
                    CompactIconToggle {
                        objectName:"wullEnableProactive"
                        buttonIcon:"notifications_active"
                        text:Translation.tr("Allow proactive check-ins and reminders")
                        tooltipText:Translation.tr("Off by default. Pauses during Focus, fullscreen, games, dialogs and quiet hours. Repeatedly ignored offers pause until you interact or the next day.")
                        checked:WullMind.options.proactiveEnabled === true
                        enabled:WullMind.options.proactiveEnabled !== undefined
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.proactiveEnabled",checked)
                    }
                    CompactIconToggle {
                        objectName:"wullPlannerReminders"
                        buttonIcon:"event_upcoming"
                        text:"Read Weekly Planner events"
                        tooltipText:"Use scheduled events from your Alis Weekly Planner for proactive reminders."
                        checked:WullMind.plannerRemindersEnabled
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.calendarRemindersEnabled",checked)
                        Item { objectName:"wullCalendarReminders"; visible:false }
                    }
                    CompactIconToggle {
                        objectName:"wullTodoReminders"
                        buttonIcon:"checklist"
                        text:Translation.tr("Read Todo items for reminders")
                        tooltipText:"Use timed tasks from your Alis Todo list for proactive reminders."
                        checked:WullMind.todoRemindersEnabled
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.todoRemindersEnabled",checked)
                    }
                    CompactIconToggle {
                        objectName:"wullReminderNotifications"
                        buttonIcon:"notifications"
                        text:Translation.tr("Show reminder notifications")
                        tooltipText:Translation.tr("Also send consented reminders to the notification center. Off by default.")
                        checked:WullMind.reminderNotificationsEnabled
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.reminderNotificationsEnabled",checked)
                    }
                    InlineChoice {
                        objectName:"wullProactiveFrequency"
                        label:""
                        comboMinWidth:110
                        description:Translation.tr("Check-ins and reminders")
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
                }
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:8
                    CompactIconToggle {
                        objectName:"wullQuietHoursToggle"
                        buttonIcon:"bedtime"
                        text:Translation.tr("Quiet hours")
                        tooltipText:"Pause automatic check-ins and reminders during your quiet hours."
                        checked:WullMind.options.proactiveQuietEnabled !== false
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.proactiveQuietEnabled",checked)
                    }
                    InlineChoice {
                        objectName:"wullQuietHoursStart"
                        label:"Start"
                        comboMinWidth:82
                        options:[{displayName:"20:00",value:1200},{displayName:"22:00",value:1320},{displayName:"00:00",value:0}]
                        currentValue:WullMind.options.proactiveQuietStart ?? 1320
                        onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveQuietStart",value)
                    }
                    InlineChoice {
                        objectName:"wullQuietHoursEnd"
                        label:"End"
                        comboMinWidth:82
                        options:[{displayName:"06:00",value:360},{displayName:"08:00",value:480},{displayName:"10:00",value:600}]
                        currentValue:WullMind.options.proactiveQuietEnd ?? 480
                        onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveQuietEnd",value)
                    }
                }
            }

            // ROW 3 (2 Columns): Offer Quotas (Left) | Personalize Dialogue Inline (Right)
            GridLayout {
                objectName:"wullProactiveQuotaGrid"
                Layout.fillWidth:true
                columns:root.availableRowWidth>520 ? 2 : 1
                uniformCellWidths:columns > 1
                columnSpacing:16;rowSpacing:8
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:10
                    InlineChoice {
                        objectName:"wullProactiveDailyCap"
                        label:Translation.tr("Daily offer limit")
                        comboMinWidth:68
                        options:[{displayName:"2",value:2},{displayName:"4",value:4},{displayName:"6",value:6},{displayName:"10",value:10}]
                        currentValue:WullMind.options.proactiveDailyCap ?? 6
                        onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveDailyCap",value)
                    }
                    InlineChoice {
                        objectName:"wullProactiveEventCap"
                        label:Translation.tr("Limit for each kind of offer")
                        comboMinWidth:64
                        options:[{displayName:"1",value:1},{displayName:"2",value:2},{displayName:"3",value:3}]
                        currentValue:WullMind.options.proactiveEventCap ?? 3
                        onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveEventCap",value)
                    }
                }
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:8
                    CompactIconToggle {
                        objectName:"wullDialogueConsent"
                        buttonIcon:"mood"
                        text:Translation.tr("Personalize local dialogue")
                        tooltipText:Translation.tr("Use your chosen tone and today's explicit check-in for local replies. Familiarity is your choice. No relationship score, penalties for absence or pressure to return.")
                        checked:WullMind.options.bondEnabled===true
                        enabled:WullMind.options.bondEnabled!==undefined
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.bondEnabled",checked)
                    }
                    InlineChoice {
                        objectName:"wullDialogueTone"
                        label:""
                        description:Translation.tr("Dialogue tone")
                        comboMinWidth:96
                        options:[{displayName:Translation.tr("Gentle"),value:"gentle"},
                            {displayName:Translation.tr("Witty"),value:"witty"},{displayName:Translation.tr("Concise"),value:"concise"}]
                        currentValue:WullMind.options.dialogueTone ?? "gentle"
                        onSelected:value=>Config.setNestedValue("abyss.companionMind.dialogueTone",value)
                    }
                    InlineChoice {
                        objectName:"wullDialogueFamiliarity"
                        label:""
                        description:Translation.tr("Familiarity")
                        comboMinWidth:110
                        options:[{displayName:Translation.tr("Neutral"),value:"neutral"},
                            {displayName:Translation.tr("Familiar coworker"),value:"familiar"}]
                        currentValue:WullMind.options.familiarity ?? "neutral"
                        onSelected:value=>Config.setNestedValue("abyss.companionMind.familiarity",value)
                    }
                    CompactIconButton {
                        objectName:"wullResetDialogue"
                        iconName:"restart_alt"
                        tooltipText:Translation.tr("Reset dialogue preferences")
                        onClicked:WullMind.resetDialogue()
                    }
                }
            }

            // ROW 4 (2 Columns): Active-App / Screen OCR Agent (Left) | Obsidian Journal Context (Right)
            GridLayout {
                objectName:"wullDialogueGrid"
                Layout.fillWidth:true
                columns:root.availableRowWidth>520 ? 2 : 1
                uniformCellWidths:columns > 1
                columnSpacing:16;rowSpacing:8
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:6
                    CompactIconToggle {
                        objectName:"wullAgentToolsToggle"
                        buttonIcon:"smart_toy"
                        text:"Enable Desktop Agent"
                        tooltipText:"Allow Companion to answer questions using your focused app context or screenshot OCR."
                        checked:WullMind.agentToolsEnabled
                        onToggledByUser:checked=>WullMind.agentToolsEnabled=checked
                    }
                    CompactIconToggle {
                        objectName:"wullAgentAppContextToggle"
                        buttonIcon:"desktop_windows"
                        text:"Access focused app"
                        tooltipText:"Read active window metadata and open text/code file from your focused application."
                        enabled:WullMind.agentToolsEnabled
                        checked:WullMind.agentAppContextEnabled
                        onToggledByUser:checked=>WullMind.agentAppContextEnabled=checked
                    }
                    CompactIconToggle {
                        objectName:"wullAgentScreenOcrToggle"
                        buttonIcon:"document_scanner"
                        text:"Auto screenshot & OCR fallback"
                        tooltipText:"When an app cannot be accessed directly, automatically take a screenshot and read on-screen text via OCR."
                        enabled:WullMind.agentToolsEnabled && WullMind.agentAppContextEnabled
                        checked:WullMind.agentScreenFallbackEnabled
                        onToggledByUser:checked=>WullMind.agentScreenFallbackEnabled=checked
                    }
                    CompactIconToggle {
                        objectName:"wullAgentDocRagToggle"
                        buttonIcon:"library_books"
                        text:"Local docs RAG"
                        tooltipText:"Retrieve local Companion documentation snippets when relevant."
                        enabled:WullMind.agentToolsEnabled
                        checked:WullMind.agentDocRagEnabled
                        onToggledByUser:checked=>{
                            WullMind.agentDocRagEnabled=checked
                            if(checked)WullMind.evaluateToolRouting("How does Companion architecture work?",true)
                        }
                    }
                    CompactIconButton {
                        objectName:"wullAgentCaptureApp"
                        iconName:"center_focus_strong"
                        tooltipText:"Capture active app / screen OCR now"
                        enabled:WullMind.agentToolsEnabled && !WullMind.busy
                        onClicked:WullMind.inspectToolMetadata("active_app")
                    }
                    CompactIconButton {
                        objectName:"wullAgentProposeTone"
                        iconName:"tune"
                        tooltipText:"Propose dialogue tone edit (requires confirmation)"
                        enabled:WullMind.agentToolsEnabled && !WullMind.busy
                        onClicked:{
                            const cur=String(WullMind.options.dialogueTone ?? "gentle")
                            const next=cur==="gentle" ? "witty" : "gentle"
                            WullMind.proposePreferenceEdit("dialogueTone",cur,next)
                        }
                    }
                    CompactIconButton {
                        objectName:"wullAgentConfirmProposal"
                        visible:WullMind.lastToolProposal!==null
                        iconName:"check"
                        tooltipText:"Confirm pending Agent proposal"
                        enabled:!WullMind.busy && WullMind.lastToolProposal!==null
                        onClicked:if(WullMind.lastToolProposal)WullMind.confirmToolProposal(String(WullMind.lastToolProposal.proposalId))
                    }
                    CompactIconButton {
                        objectName:"wullAgentCancelProposal"
                        visible:WullMind.lastToolProposal!==null
                        iconName:"close"
                        tooltipText:"Cancel pending Agent proposal"
                        enabled:!WullMind.busy && WullMind.lastToolProposal!==null
                        onClicked:if(WullMind.lastToolProposal)WullMind.cancelToolProposal(String(WullMind.lastToolProposal.proposalId))
                    }
                    StyledText {
                        Layout.fillWidth:true;elide:Text.ElideRight
                        text:WullMind.agentStatusSummary
                        font.pixelSize:Appearance.font.pixelSize.smaller
                        color:Appearance.colors.colSubtext
                    }
                }
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:8
                    CompactIconToggle {
                        objectName:"wullConnectObsidian"
                        buttonIcon:"menu_book"
                        text:Translation.tr("Connect Obsidian")
                        tooltipText:"Share read-only daily mood/energy check-in with your configured Obsidian journal."
                        checked:WullMind.obsidianEnabled
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.obsidianEnabled",checked)
                    }
                    StyledText {
                        Layout.fillWidth:true;elide:Text.ElideMiddle
                        text:Translation.tr("Obsidian vault")+": "+(Todo.sharedVaultPath || Translation.tr("Not configured"))
                        font.pixelSize:Appearance.font.pixelSize.smaller
                        color:Appearance.colors.colSubtext
                    }
                    CompactIconButton {
                        iconName:"refresh"
                        tooltipText:Translation.tr("Read today's context")
                        enabled:WullMind.obsidianEnabled && !WullMind.busy
                        onClicked:WullMind.refreshJournal()
                    }
                    CompactIconButton {
                        iconName:"open_in_new"
                        tooltipText:Translation.tr("Open journal")
                        enabled:!!WullMind.journal.journalPath
                        onClicked:WullMind.openJournal()
                    }
                }
            }

            // ROW 5 (2 Columns): Explicit Local Memories Policy (Left) | Memory Input & Actions (Right)
            GridLayout {
                objectName:"wullAiPermissionsGrid"
                Layout.fillWidth:true
                columns:root.availableRowWidth>520 ? 2 : 1
                uniformCellWidths:columns > 1
                columnSpacing:16;rowSpacing:8
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:8
                    CompactIconToggle {
                        objectName:"wullEnableMemory"
                        buttonIcon:"bookmark"
                        text:Translation.tr("Use saved memories with local models")
                        tooltipText:Translation.tr("Only entries you save here are remembered. No automatic extraction from chat. Turning this off stops use and keeps your records available for review or deletion.")
                        checked:WullMind.memoryEnabled
                        enabled:WullMind.options.memoryEnabled !== undefined && !WullMind.busy
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.memoryEnabled",checked)
                    }
                    InlineChoice {
                        objectName:"wullMemoryScope"
                        label:Translation.tr("Remember for")
                        comboMinWidth:84
                        options:[{displayName:"Aqua",value:"aqua"},{displayName:"Octo",value:"octo"},{displayName:Translation.tr("Both"),value:"shared"}]
                        currentValue:root.memoryScope
                        onSelected:value=>root.memoryScope=value
                    }
                    InlineChoice {
                        objectName:"wullMemoryTtl"
                        label:Translation.tr("Keep memory")
                        comboMinWidth:96
                        options:[{displayName:Translation.tr("7 days"),value:7},{displayName:Translation.tr("30 days"),value:30},{displayName:Translation.tr("Until I forget it"),value:0}]
                        currentValue:root.memoryTtlDays
                        onSelected:value=>root.memoryTtlDays=value
                    }
                }
                RowLayout {
                    Layout.fillWidth:true;Layout.alignment:Qt.AlignVCenter;spacing:8
                    Rectangle {
                        Layout.fillWidth:true
                        implicitHeight:36;radius:10
                        color:Appearance.colors.colLayer1
                        border.width:1;border.color:Qt.alpha(Appearance.colors.colPrimary,memoryInput.activeFocus ? .8 : .3)
                        StyledTextInput {
                            id:memoryInput
                            objectName:"wullMemoryDraft"
                            anchors.fill:parent;anchors.leftMargin:10;anchors.rightMargin:10
                            verticalAlignment:Text.AlignVCenter
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
                            font.pixelSize:Appearance.font.pixelSize.smaller
                        }
                    }
                    CompactIconButton {
                        objectName:"wullSaveMemory"
                        iconName:root.memoryEditingId ? "save" : "add"
                        tooltipText:root.memoryEditingId ? Translation.tr("Save memory changes") : Translation.tr("Save memory")
                        enabled:WullMind.memoryEnabled && !WullMind.busy && root.memoryDraft.trim().length>0
                        onClicked:if(WullMind.saveMemory(root.memoryDraft,root.memoryScope,root.memoryTtlDays,root.memoryEditingId)) {
                            root.memoryDraft="";root.memoryEditingId=0
                        }
                    }
                    CompactIconButton {
                        objectName:"wullReviewMemory"
                        iconName:"refresh"
                        tooltipText:Translation.tr("Review saved memories")
                        enabled:!WullMind.busy
                        onClicked:WullMind.refreshMemory()
                    }
                    CompactIconButton {
                        objectName:"wullForgetAllMemory"
                        iconName:root.confirmForgetMemories ? "warning" : "delete_sweep"
                        tooltipText:root.confirmForgetMemories ? Translation.tr("Confirm forgetting all saved memories") : Translation.tr("Forget all saved memories")
                        enabled:!WullMind.busy && WullMind.memories.length>0
                        onClicked:{
                            if(root.confirmForgetMemories){WullMind.forgetAllMemories();root.confirmForgetMemories=false}
                            else root.confirmForgetMemories=true
                        }
                    }
                }
            }

            GridLayout {
                objectName:"wullReminderSourcesGrid"
                visible:false
                columns:root.availableRowWidth>520 ? 2 : 1
            }

            Repeater {
                model:WullMind.toolReceipts
                RowLayout {
                    required property var modelData
                    Layout.fillWidth:true;spacing:8
                    StyledText {
                        Layout.fillWidth:true;elide:Text.ElideRight;textFormat:Text.PlainText
                        font.pixelSize:Appearance.font.pixelSize.smaller;color:Appearance.colors.colSubtext
                        text:modelData.field+": "+modelData.beforeValue+" → "+modelData.afterValue+(modelData.rolledBack ? " (rolled back)" : "")
                    }
                    CompactIconButton {
                        iconName:"undo"
                        tooltipText:"Rollback preference update"
                        enabled:!WullMind.busy && !modelData.rolledBack
                        onClicked:WullMind.rollbackToolReceipt(String(modelData.receiptId))
                    }
                }
            }
            StyledText {
                visible:WullMind.memoryNotice.length>0
                Layout.fillWidth:true;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                text:WullMind.memoryNotice
                font.pixelSize:Appearance.font.pixelSize.smaller
                color:Appearance.colors.colSubtext
            }
            Repeater {
                model:WullMind.memories
                RowLayout {
                    required property var modelData
                    Layout.fillWidth:true;spacing:8
                    StyledText {
                        Layout.fillWidth:true;elide:Text.ElideRight;textFormat:Text.PlainText
                        text:modelData.scope+" · "+modelData.text
                    }
                    StyledText {
                        textFormat:Text.PlainText;color:Appearance.colors.colSubtext
                        font.pixelSize:Appearance.font.pixelSize.smaller
                        text:modelData.expires_ms===0 ? Translation.tr("Until forgotten")
                            : (modelData.expires_ms<=Date.now() ? Translation.tr("Expired") : Translation.tr("Expires"))
                                +" · "+Qt.formatDateTime(new Date(modelData.expires_ms),"yyyy-MM-dd")
                    }
                    CompactIconButton {
                        iconName:"edit"
                        tooltipText:Translation.tr("Edit")
                        enabled:WullMind.memoryEnabled && !WullMind.busy
                        onClicked:{root.memoryDraft=modelData.text;root.memoryScope=modelData.scope;root.memoryEditingId=modelData.id}
                    }
                    CompactIconButton {
                        iconName:"delete"
                        tooltipText:Translation.tr("Forget")
                        enabled:!WullMind.busy
                        onClicked:WullMind.forgetMemory(modelData.id)
                    }
                }
            }
        }
    }

}
