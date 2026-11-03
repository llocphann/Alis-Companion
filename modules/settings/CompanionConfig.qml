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
import "../abyss/companion/AquaPreferences.js" as Preferences

ContentPage {
    id: root
    settingsPageIndex: 37
    settingsPageName: Translation.tr("Companion")
    property string activeSection: "overview"
    readonly property bool isOverviewTab: activeSection !== "ai"
    onActiveSectionChanged: {
        if (activeSection === "ai") {
            Ai.ensureInitialized()
            if (AquaMind.liveModelAutoDetectAllowed)
                AquaMind.refreshAvailableModels(false)
            if (!Quickshell.env("AQUA_TEST_ENDPOINT") && !Quickshell.env("AI_FIXTURE_URL"))
                AquaMind.refreshAgentStatus()
        }
    }
    Component.onCompleted: {
        if (AquaMind.liveModelAutoDetectAllowed) {
            AquaMind.refreshAvailableModels(false)
            AquaMind.refreshAgentStatus()
        }
    }
    property string previewExpression: "idle"
    property string previewMotion: ""
    property string memoryDraft: ""
    property string memoryScope: "shared"
    property int memoryTtlDays: 7
    property double memoryEditingId: 0
    property bool confirmForgetMemories: false
    property bool modelAliasExpanded: false
    property string activeStorageTab: "skills"
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
            id: choiceCombo
            objectName: choice.objectName + "Control"
            Layout.preferredWidth: 224
            Layout.fillWidth: choice.columns === 1
            model: choice.options
            textRole: "displayName"
            settingsSearchLabel: choice.label
            settingsSearchDescription: choice.description
            currentIndex: Math.max(0, choice.options.findIndex(o => o.value === choice.currentValue))
            Binding {
                target: choiceCombo
                property: "currentIndex"
                value: Math.max(0, choice.options.findIndex(o => o.value === choice.currentValue))
            }
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
            id: inlineCombo
            objectName: inlineChoice.objectName ? inlineChoice.objectName + "Control" : ""
            Layout.fillWidth: true
            Layout.minimumWidth: Math.min(64, inlineChoice.comboMinWidth)
            Layout.preferredWidth: inlineChoice.comboMinWidth
            popupMinimumWidth: Math.max(132, inlineChoice.comboMinWidth)
            model: inlineChoice.options
            textRole: "displayName"
            settingsSearchLabel: inlineChoice.label
            settingsSearchDescription: inlineChoice.description
            currentIndex: Math.max(0, inlineChoice.options.findIndex(o => o.value === inlineChoice.currentValue))
            Binding {
                target: inlineCombo
                property: "currentIndex"
                value: Math.max(0, inlineChoice.options.findIndex(o => o.value === inlineChoice.currentValue))
            }
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
            id: compactCombo
            objectName: compactChoice.objectName ? compactChoice.objectName + "Control" : ""
            Layout.fillWidth: true
            model: compactChoice.options
            textRole: "displayName"
            settingsSearchLabel: compactChoice.label
            settingsSearchDescription: compactChoice.description
            currentIndex: Math.max(0, compactChoice.options.findIndex(o => o.value === compactChoice.currentValue))
            Binding {
                target: compactCombo
                property: "currentIndex"
                value: Math.max(0, compactChoice.options.findIndex(o => o.value === compactChoice.currentValue))
            }
            onActivated: index => compactChoice.selected(compactChoice.options[index].value)
        }
    }

    component PercentageRow: RowLayout {
        id: percentage
        property string iconName: "tune"
        property string label: ""
        property string description: ""
        property real value: 1
        property real minimum: 0
        property real maximum: 1
        signal moved(real value)
        Layout.fillWidth: true
        spacing: 8
        implicitHeight: Math.round(28 * Appearance.fontSizeScale)
        MaterialSymbol {
            text: percentage.iconName
            iconSize: 16
            color: Appearance.colors.colSubtext
            Layout.alignment: Qt.AlignVCenter
        }
        StyledText {
            Layout.preferredWidth: Math.max(implicitWidth, 108 * Appearance.fontSizeScale)
            Layout.minimumWidth: implicitWidth
            Layout.alignment: Qt.AlignVCenter
            text: percentage.label
            elide: Text.ElideRight
            color: Appearance.colors.colOnSurface
            font.pixelSize: Appearance.font.pixelSize.small
        }
        StyledSlider {
            id: percentageSlider
            objectName: percentage.objectName + "Control"
            Layout.fillWidth: true
            Layout.minimumWidth: 64 * Appearance.fontSizeScale
            Layout.alignment: Qt.AlignVCenter
            configuration: StyledSlider.Configuration.S
            from: percentage.minimum
            to: percentage.maximum
            stepSize: 0.01
            value: percentage.value
            tooltipContent: Math.round(value * 100) + "%" + (percentage.description.length > 0 ? " · " + percentage.description : "")
            settingsSearchLabel: percentage.label
            settingsSearchDescription: percentage.description
            onMoved: percentage.moved(value)
        }
        StyledText {
            Layout.preferredWidth: 42 * Appearance.fontSizeScale
            Layout.alignment: Qt.AlignVCenter
            horizontalAlignment: Text.AlignRight
            text: Math.round(percentage.value * 100) + "%"
            color: Appearance.colors.colPrimary
            font.pixelSize: Appearance.font.pixelSize.small
        }
    }

    onActiveCardSubTabChanged: if (activeCardSubTab.indexOf(Translation.tr("AI")) === 0) Ai.ensureInitialized()

    SettingsTaskNavigator {
        showIntro: false
        showTabBar: false
        currentValue: root.isOverviewTab ? "overview" : "ai"
        onSelected: value => {
            if (!root.embedded && value === "ai")
                return
            root.activeSection = value
        }
        options: [
            { displayName: Translation.tr("Overview"), icon: "water_drop", value: "overview" }
        ]
    }

    GridLayout {
        id: unifiedOverviewGrid
        objectName: "companionUnifiedOverviewGrid"
        Layout.fillWidth: true
        visible: true
        columns: root.isOverviewTab && width >= 620 ? 2 : 1
        uniformCellWidths: columns === 2
        columnSpacing: 12
        rowSpacing: 10

        SettingsCardSection {
            id: overviewCard
            property bool _preventBentoReparent: true
            Layout.preferredWidth: 1
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignTop
            settingsTaskSection: "overview"
            visible: root.isOverviewTab
            expanded: true
            title: Translation.tr("Overview")
            icon: "water_drop"

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: hero.implicitHeight + 24
                radius: SettingsMaterialPreset.groupRadius
                color: Appearance.colors.colLayer1
                border.width: 1
                border.color: Appearance.colors.colLayer0Border
                GridLayout {
                    id: hero
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 12
                    columns: root.availableRowWidth > 560 ? 2 : 1
                    columnSpacing: 18
                    rowSpacing: 8
                    Rectangle {
                        Layout.fillWidth: hero.columns === 1
                        Layout.fillHeight: true
                        Layout.preferredWidth: 220
                        Layout.preferredHeight: 198
                        Layout.minimumHeight: 192
                        radius: SettingsMaterialPreset.groupRadius
                        color: AbyssStyle.surface
                        border.width: 1
                        border.color: Qt.alpha(AbyssStyle.accent, 0.22)
                        WaterDropletBody {
                            id: preview
                            character: root.preferences.character
                            objectName: "companionPreview"
                            anchors.centerIn: parent
                            width: 76; height: 92
                            scale: Math.min(1.95, 1.68 * root.preferences.size)
                            transformOrigin: Item.Center
                            expression: root.previewExpression
                            motionAction: root.previewMotion
                            grounded: !["fly","jump","drag"].includes(root.previewMotion)
                            motionScale: Preferences.motionScale(root.preferences.personality)
                            motionEnabled: root.preferences.animationsEnabled && AbyssStyle.motionEnabled && visible
                            effectsEnabled: root.preferences.effectsEnabled && Appearance.effectsEnabled
                                && AbyssRenderPolicy.aquaQuality !== "performance"
                            renderQuality: AbyssRenderPolicy.aquaQuality
                            translucency: root.preferences.translucency
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            StyledText {
                                Layout.fillWidth: true
                                text: root.preferences.character === "octo" ? "Octo" : "Aqua"
                                font.pixelSize: 22
                                font.weight: Font.DemiBold
                            }
                            CompactIconButton {
                                objectName: "companionReset"
                                enabled: Config.ready
                                iconName: "restart_alt"
                                tooltipText: Translation.tr("Reset Companion settings")
                                onClicked: root.resetPreferences()
                            }
                        }
                        StyledText {
                            Layout.fillWidth: true
                            text: Translation.tr("A little liquid companion for your desktop.") + " " + Translation.tr("Color and reflections follow Abyss Panel Style automatically.")
                            font.pixelSize: Appearance.font.pixelSize.smaller
                            color: Appearance.colors.colSubtext
                            wrapMode: Text.WordWrap
                        }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: width > 360 ? 2 : 1
                            uniformCellWidths: columns > 1
                            columnSpacing: 12
                            rowSpacing: 8
                            CompactChoice {
                                objectName: "companionPreviewExpression"
                                label: Translation.tr("Preview expression")
                                options: root.expressionOptions
                                currentValue: root.previewExpression
                                onSelected: value => root.previewExpression = value
                            }
                            CompactChoice {
                                objectName: "companionPreviewMotion"
                                label: Translation.tr("Preview movement")
                                options: root.motionOptions
                                currentValue: root.previewMotion
                                onSelected: value => root.previewMotion = value
                            }
                        }
                    }
                }
            }

            SettingsGroup {
                enabled: Config.ready
                CompactChoice {
                    objectName: "companionCharacter"
                    label: Translation.tr("Companion")
                    options: [{displayName:"Aqua",value:"aqua"},{displayName:"Octo",value:"octo"}]
                    currentValue: root.preferences.character
                    onSelected: value => root.setPreference("character",value)
                }
                GridLayout {
                    objectName: "companionOverviewTogglesGrid"
                    Layout.fillWidth: true
                    columns: overviewCard.width > 420 ? 2 : 1
                    uniformCellWidths: columns > 1
                    columnSpacing: 10; rowSpacing: 8
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
                        text: Translation.tr("Respond to Click")
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

        ColumnLayout {
            id: rightOverviewColumn
            visible: root.isOverviewTab
            Layout.preferredWidth: 1
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignTop
            spacing: 10

            SettingsCardSection {
                id: renderingCard
                property bool _preventBentoReparent: true
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                settingsTaskSection: "rendering"
                visible: root.isOverviewTab
                expanded: true
                title: Translation.tr("Rendering")
                icon: "diamond"
                SettingsGroup {
                    enabled: Config.ready
                    RowLayout {
                        objectName: "companionRenderingTogglesGrid"
                        Layout.fillWidth: true
                        spacing: 8

                        ConfigSelectionArray {
                            id: companionQualitySelector
                            objectName: "companionQuality"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 248
                            enabled: !root.preferences.autoQuality
                            currentValue: root.preferences.autoQuality ? AbyssRenderPolicy.aquaQuality : root.preferences.renderQuality
                            options: root.qualityOptions
                            onSelected: value => root.setPreference("renderQuality", value)

                            Item {
                                objectName: "companionQualityControl"
                                visible: false
                                width: 0
                                height: 0
                                function activated(index) {
                                    if (index >= 0 && index < companionQualitySelector.options.length)
                                        companionQualitySelector.selected(companionQualitySelector.options[index].value)
                                }
                            }
                        }

                        SettingsSwitch {
                            Layout.fillWidth: false
                            buttonIcon: "speed"
                            text: "Auto quality"
                            autoToggle: false
                            checked: root.preferences.autoQuality
                            onToggledByUser: checked => root.setPreference("autoQuality", checked)
                            StyledToolTip { text: Translation.tr("Use Performance in Power Saver, Balanced in Balanced, and Quality in Performance power profile.") }
                        }

                        SettingsSwitch {
                            objectName: "companionEffects"
                            Layout.fillWidth: false
                            buttonIcon: "auto_fix_high"
                            text: "More details"
                            autoToggle: false
                            checked: root.preferences.effectsEnabled
                            onToggledByUser: checked => root.setPreference("effectsEnabled", checked)
                            StyledToolTip { text: Translation.tr("Add floating bubbles and sparkle around your companion. Respects the shell effects setting.") }
                        }
                    }

                    SettingsDivider {}

                    GridLayout {
                        objectName: "companionRenderingSlidersGrid"
                        Layout.fillWidth: true
                        columns: renderingCard.width > 420 ? 2 : 1
                        uniformCellWidths: columns > 1
                        columnSpacing: 14
                        rowSpacing: 6
                        PercentageRow {
                            objectName: "companionSize"
                            iconName: "open_in_full"
                            label: Translation.tr("Companion size")
                            description: Translation.tr("Keep your companion small or give it a little more room.")
                            value: root.preferences.size
                            minimum: 0.65; maximum: 1.5
                            onMoved: value => root.setPreference("size", value)
                        }
                        PercentageRow {
                            objectName: "companionTranslucency"
                            iconName: "opacity"
                            label: Translation.tr("Translucency")
                            description: Translation.tr("Let a little of the background show through the liquid. Eyes and bright reflections stay clear.")
                            value: root.preferences.translucency
                            minimum: 0
                            maximum: 0.35
                            onMoved: value => root.setPreference("translucency", value)
                        }
                    }
                }
            }

            SettingsCardSection {
                id: behaviorCard
                property bool _preventBentoReparent: true
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.alignment: Qt.AlignTop
                settingsTaskSection: "behavior"
                visible: root.isOverviewTab
                expanded: true
                title: Translation.tr("Behavior")
                icon: "sentiment_satisfied"
                SettingsGroup {
                    enabled: Config.ready
                    GridLayout {
                        objectName: "companionBehaviorChoicesGrid"
                        Layout.fillWidth: true
                        columns: behaviorCard.width > 360 ? 2 : 1
                        uniformCellWidths: columns > 1
                        columnSpacing: 14; rowSpacing: 8
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
                        id: companionBehaviorTogglesGrid
                        objectName: "companionBehaviorTogglesGrid"
                        Layout.fillWidth: true
                        columns: behaviorCard.width > 360 ? 2 : 1
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
                            objectName: "companionAmbientFocus"
                            buttonIcon: "timer"
                            text: Translation.tr("Follow Focus sessions")
                            tooltipText: Translation.tr("Uses the existing Focus timer; stays quiet during work.")
                            checked: root.preferences.ambientFocusEnabled
                            onToggledByUser: checked => root.setPreference("ambientFocusEnabled", checked)
                        }
                        CompactToggle {
                            objectName: "companionBehaviorTalkClouds"
                            buttonIcon: "chat_bubble"
                            text: Translation.tr("Talk clouds")
                            tooltipText: "Show speech bubbles and quick chat above your companion."
                            checked: AquaMind.talkEnabled
                            onToggledByUser: checked => Config.setNestedValue("abyss.companionMind.talkEnabled", checked)
                        }
                    }
                }
            }
        }

        SettingsCardSection {
            id: proactiveCard
            property bool _preventBentoReparent: true
            Layout.columnSpan: unifiedOverviewGrid.columns
            Layout.preferredWidth: 1
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            settingsTaskSection: "behavior"
            visible: root.isOverviewTab
            expanded: true
            title: "Proactive & Reminders"
            icon: "notifications_active"
            SettingsGroup {
                enabled: Config.ready

                // ROW 1: Proactive Check-ins + Frequency (Left) | Quiet Hours + Start/End (Right)
                GridLayout {
                    objectName:"aquaQuietHoursGrid"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    columns:root.availableRowWidth>520 ? 2 : 1
                    uniformCellWidths:columns > 1
                    columnSpacing:14;rowSpacing:6

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:8
                        CompactToggle {
                            objectName:"aquaEnableProactive"
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:80
                            buttonIcon:"notifications_active"
                            text:Translation.tr("Allow proactive check-ins and reminders")
                            tooltipText:Translation.tr("Off by default. Pauses during Focus, fullscreen, games, dialogs and quiet hours. Repeatedly ignored offers pause until you interact or the next day.")
                            checked:AquaMind.options.proactiveEnabled === true
                            enabled:AquaMind.options.proactiveEnabled !== undefined
                            onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.proactiveEnabled",checked)
                        }
                        InlineChoice {
                            objectName:"aquaProactiveFrequency"
                            Layout.fillWidth:false
                            label:""
                            comboMinWidth:136
                            description:Translation.tr("Check-ins and reminders")
                            options:[
                                {displayName:Translation.tr("Rarely while idle"),value:"rare"},
                                {displayName:Translation.tr("Occasionally while idle"),value:"occasional"},
                                {displayName:Translation.tr("Regularly while idle"),value:"regular"},
                                {displayName:Translation.tr("Often while idle"),value:"often"},
                                {displayName:Translation.tr("Only when I chat"),value:"manual"}
                            ]
                            currentValue:AquaMind.proactive
                            onSelected:value=>Config.setNestedValue("abyss.companionMind.proactive",value)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:8
                        CompactToggle {
                            objectName:"aquaQuietHoursToggle"
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:76
                            buttonIcon:"bedtime"
                            text:Translation.tr("Quiet hours")
                            tooltipText:"Pause automatic check-ins and reminders during your quiet hours."
                            checked:AquaMind.options.proactiveQuietEnabled !== false
                            onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.proactiveQuietEnabled",checked)
                        }
                        InlineChoice {
                            objectName:"aquaQuietHoursStart"
                            Layout.fillWidth:false
                            label:"Start"
                            comboMinWidth:78
                            options:[{displayName:"20:00",value:1200},{displayName:"22:00",value:1320},{displayName:"00:00",value:0}]
                            currentValue:AquaMind.options.proactiveQuietStart ?? 1320
                            onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveQuietStart",value)
                        }
                        InlineChoice {
                            objectName:"aquaQuietHoursEnd"
                            Layout.fillWidth:false
                            label:"End"
                            comboMinWidth:78
                            options:[{displayName:"06:00",value:360},{displayName:"08:00",value:480},{displayName:"10:00",value:600}]
                            currentValue:AquaMind.options.proactiveQuietEnd ?? 480
                            onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveQuietEnd",value)
                        }
                    }
                }

                // ROW 2: Reminder Sources (3 Columns)
                GridLayout {
                    objectName:"aquaReminderSourcesGrid"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    columns:root.availableRowWidth>520 ? 3 : 1
                    uniformCellWidths:columns > 1
                    columnSpacing:14;rowSpacing:6

                    CompactToggle {
                        objectName:"aquaPlannerReminders"
                        Layout.preferredWidth:1
                        Layout.minimumWidth:0
                        buttonIcon:"event_upcoming"
                        text:"Read Weekly Planner events"
                        tooltipText:"Use scheduled events from your Alis Weekly Planner for proactive reminders."
                        checked:AquaMind.plannerRemindersEnabled
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.calendarRemindersEnabled",checked)
                        Item { objectName:"aquaCalendarReminders"; visible:false }
                    }

                    CompactToggle {
                        objectName:"aquaTodoReminders"
                        Layout.preferredWidth:1
                        Layout.minimumWidth:0
                        buttonIcon:"checklist"
                        text:Translation.tr("Read Todo items for reminders")
                        tooltipText:"Use timed tasks from your Alis Todo list for proactive reminders."
                        checked:AquaMind.todoRemindersEnabled
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.todoRemindersEnabled",checked)
                    }

                    CompactToggle {
                        objectName:"aquaReminderNotifications"
                        Layout.preferredWidth:1
                        Layout.minimumWidth:0
                        buttonIcon:"notifications"
                        text:Translation.tr("Show reminder notifications")
                        tooltipText:Translation.tr("Also send consented reminders to the notification center. Off by default.")
                        checked:AquaMind.reminderNotificationsEnabled
                        onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.reminderNotificationsEnabled",checked)
                    }
                }

                // ROW 3: Offer Quotas (Left) | Personalize Dialogue Inline (Right)
                GridLayout {
                    objectName:"aquaProactiveQuotaGrid"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    columns:root.availableRowWidth>520 ? 2 : 1
                    uniformCellWidths:columns > 1
                    columnSpacing:14;rowSpacing:6

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:10
                        InlineChoice {
                            objectName:"aquaProactiveDailyCap"
                            Layout.preferredWidth:1
                            label:Translation.tr("Daily offer limit")
                            comboMinWidth:64
                            options:[{displayName:"2",value:2},{displayName:"4",value:4},{displayName:"6",value:6},{displayName:"10",value:10}]
                            currentValue:AquaMind.options.proactiveDailyCap ?? 6
                            onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveDailyCap",value)
                        }
                        InlineChoice {
                            objectName:"aquaProactiveEventCap"
                            Layout.preferredWidth:1
                            label:Translation.tr("Limit for each kind of offer")
                            comboMinWidth:60
                            options:[{displayName:"1",value:1},{displayName:"2",value:2},{displayName:"3",value:3}]
                            currentValue:AquaMind.options.proactiveEventCap ?? 3
                            onSelected:value=>Config.setNestedValue("abyss.companionMind.proactiveEventCap",value)
                        }
                    }

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:8
                        CompactToggle {
                            objectName:"aquaDialogueConsent"
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:80
                            buttonIcon:"mood"
                            text:Translation.tr("Personalize local dialogue")
                            tooltipText:Translation.tr("Use your chosen tone and today's explicit check-in for local replies. Familiarity is your choice. No relationship score, penalties for absence or pressure to return.")
                            checked:AquaMind.options.bondEnabled===true
                            enabled:AquaMind.options.bondEnabled!==undefined
                            onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.bondEnabled",checked)
                        }
                        InlineChoice {
                            objectName:"aquaDialogueTone"
                            Layout.fillWidth:false
                            label:""
                            description:Translation.tr("Dialogue tone")
                            comboMinWidth:84
                            options:[{displayName:Translation.tr("Gentle"),value:"gentle"},
                                {displayName:Translation.tr("Witty"),value:"witty"},{displayName:Translation.tr("Concise"),value:"concise"}]
                            currentValue:AquaMind.options.dialogueTone ?? "gentle"
                            onSelected:value=>Config.setNestedValue("abyss.companionMind.dialogueTone",value)
                        }
                        InlineChoice {
                            objectName:"aquaDialogueFamiliarity"
                            Layout.fillWidth:false
                            label:""
                            description:Translation.tr("Familiarity")
                            comboMinWidth:96
                            options:[{displayName:Translation.tr("Neutral"),value:"neutral"},
                                {displayName:Translation.tr("Familiar coworker"),value:"familiar"}]
                            currentValue:AquaMind.options.familiarity ?? "neutral"
                            onSelected:value=>Config.setNestedValue("abyss.companionMind.familiarity",value)
                        }
                        CompactIconButton {
                            objectName:"aquaResetDialogue"
                            iconName:"restart_alt"
                            tooltipText:Translation.tr("Reset dialogue preferences")
                            onClicked:AquaMind.resetDialogue()
                        }
                    }
                }
            }
        }

        SettingsCardSection {
            id: aiCard
            property bool _preventBentoReparent: true
            Layout.columnSpan: unifiedOverviewGrid.columns
            Layout.preferredWidth: 1
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            settingsTaskSection:"ai"
            visible:!root.isOverviewTab
            expanded:true
            title:Translation.tr("AI")
            icon:"chat"
            SettingsGroup {
                objectName:"aquaAiSettingsGroup"

                // ROW 1: Talk Clouds + Allow Cloud Models + Model (Left) | Thinking Effort / Privacy Status + Clear (Right)
                GridLayout {
                    objectName:"aquaAiModelGrid"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    columns:root.availableRowWidth>520 ? 2 : 1
                    uniformCellWidths:columns > 1
                    columnSpacing:14;rowSpacing:6

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:8
                        CompactToggle {
                            objectName:"aquaTalkClouds"
                            Layout.fillWidth:false
                            Layout.minimumWidth:76
                            buttonIcon:"chat_bubble"
                            text:Translation.tr("Talk clouds")
                            tooltipText:"Show speech bubbles and quick chat above your companion."
                            checked:AquaMind.talkEnabled
                            onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.talkEnabled",checked)
                        }
                        CompactToggle {
                            objectName:"aquaAllowCloudModels"
                            Layout.fillWidth:false
                            Layout.minimumWidth:84
                            buttonIcon:"cloud"
                            text:Translation.tr("Allow cloud models")
                            tooltipText:"Keep off to restrict Companion strictly to verified local models."
                            checked:!AquaMind.localOnly
                            onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.localOnly",!checked)
                        }
                        StyledComboBox {
                            objectName:"aquaCompanionModelSelector"
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:80
                            popupMinimumWidth:320
                            model:AquaMind.selectableModels
                            textRole:"label"
                            currentIndex:Math.max(0,AquaMind.selectableModels.findIndex(item=>item.name===String(Config.options?.abyss?.companionMind?.model ?? "")))
                            displayText:currentIndex>=0 ? (AquaMind.selectableModels[currentIndex].label ?? AquaMind.selectableModels[currentIndex].name) : "Use the AI tab model"
                            onActivated:index=>{
                                const item=AquaMind.selectableModels[index]
                                if(item)AquaMind.selectModel(item)
                            }
                        }
                        CompactIconButton {
                            objectName:"aquaRefreshModelsButton"
                            iconName:"sync"
                            enabled:!AquaMind.refreshingModels
                            tooltipText:"Auto-detect & refresh available models"
                            onClicked:AquaMind.refreshAvailableModels(true)
                        }
                        CompactIconButton {
                            id:toggleModelAliasBtn
                            objectName:"aquaToggleModelAliasButton"
                            visible:AquaMind.model.length>0
                            iconName:"edit_note"
                            tooltipText:"Customize model display name"
                            colBackground:root.modelAliasExpanded
                                ? Qt.alpha(Appearance.colors.colPrimary,0.22)
                                : Appearance.colors.colLayer2
                            onClicked:{
                                root.modelAliasExpanded=!root.modelAliasExpanded
                                if(root.modelAliasExpanded) {
                                    Qt.callLater(()=>settingsModelAliasInput.forceActiveFocus())
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:8
                        StyledText {
                            visible:AquaMind.thinkingSupported
                            Layout.fillWidth:false
                            text:"Thinking effort"
                            color:Appearance.colors.colSubtext
                            font.pixelSize:Appearance.font.pixelSize.smaller
                        }
                        Rectangle {
                            id:settingsEffortSlider
                            objectName:"aquaCompanionThinkingEffort"
                            visible:AquaMind.thinkingSupported
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:108
                            implicitWidth:150
                            implicitHeight:34
                            radius:10
                            color:Appearance.colors.colLayer2
                            enabled:AquaMind.thinkingSupported && !AquaMind.busy
                            readonly property int stepCount:Math.max(1,settingsEffortRepeater.count)
                            readonly property real stepSlotWidth:Math.max(16,(width-12)/stepCount)
                            readonly property int activeStepIndex:Math.max(0,AquaMind.thinkingLevels.findIndex(level=>level.value===AquaMind.effectiveThinkingEffort || (level.value==="off" && AquaMind.effectiveThinkingEffort==="instant") || (level.value==="instant" && AquaMind.effectiveThinkingEffort==="off")))
                            property real animatedStepIndex:activeStepIndex
                            Behavior on animatedStepIndex {
                                enabled: root.visible && AquaMind.liveModelAutoDetectAllowed
                                NumberAnimation {
                                    duration: 140
                                    easing.type: Easing.OutCubic
                                }
                            }
                            function applyEffortAtX(localX): bool {
                                const levels = AquaMind.thinkingLevels ?? []
                                if (levels.length <= 1 || !enabled) return false
                                const pad = 6 + stepSlotWidth * 0.5
                                const trackW = Math.max(1, width - 12 - stepSlotWidth)
                                const ratio = Math.max(0, Math.min(1, (localX - pad) / trackW))
                                const idx = Math.max(0, Math.min(levels.length - 1, Math.round(ratio * (levels.length - 1))))
                                const item = levels[idx]
                                if (item && item.value !== undefined) {
                                    AquaMind.setThinkingEffort(item.value)
                                    return true
                                }
                                return false
                            }
                            Rectangle {
                                id:settingsEffortTrackBg
                                x:6+Math.round(settingsEffortSlider.stepSlotWidth*0.5)
                                y:9
                                width:Math.max(0,settingsEffortSlider.width-12-settingsEffortSlider.stepSlotWidth)
                                height:4
                                radius:2
                                color:Qt.alpha(Appearance.colors.colOnLayer1,0.14)
                            }
                            Rectangle {
                                x:settingsEffortTrackBg.x
                                y:settingsEffortTrackBg.y
                                width:settingsEffortSlider.stepCount>1
                                    ? Math.round(settingsEffortTrackBg.width*(settingsEffortSlider.animatedStepIndex/(settingsEffortSlider.stepCount-1)))
                                    : 0
                                height:4
                                radius:2
                                color:Appearance.colors.colPrimary
                            }
                            Rectangle {
                                id:settingsEffortThumb
                                objectName:"aquaSettingsEffortThumb"
                                z:5
                                width:12
                                height:12
                                radius:6
                                x:6+Math.round((settingsEffortSlider.animatedStepIndex+0.5)*settingsEffortSlider.stepSlotWidth-width/2)
                                y:5
                                color:Appearance.colors.colPrimary
                                border.width:2
                                border.color:Appearance.colors.colOnPrimary
                            }
                            Repeater {
                                id:settingsEffortRepeater
                                model:AquaMind.thinkingLevels
                                RippleButton {
                                    id:settingsEffortStep
                                    required property var modelData
                                    required property int index
                                    objectName:"aquaSettingsEffortOption-"+String(modelData.value)
                                    readonly property string shortLabel:modelData.value==="instant" || modelData.value==="off"
                                        ? "Inst"
                                        : modelData.value==="low" ? "Low"
                                        : modelData.value==="medium" ? "Med"
                                        : modelData.value==="high" ? "High"
                                        : modelData.value==="xhigh" ? "XHigh" : "Max"
                                    readonly property bool isSelected:index===settingsEffortSlider.activeStepIndex
                                    x:6+Math.round(index*settingsEffortSlider.stepSlotWidth)
                                    y:1
                                    width:Math.max(16,Math.round(settingsEffortSlider.stepSlotWidth))
                                    height:32
                                    implicitWidth:24
                                    implicitHeight:32
                                    buttonRadius:7
                                    colBackground:"transparent"
                                    colBackgroundHover:Qt.alpha(Appearance.colors.colPrimary,0.10)
                                    moveAction: event => {
                                        if (settingsEffortStep.down) {
                                            settingsEffortStep.suppressClickOnRelease = true
                                            settingsEffortSlider.applyEffortAtX(settingsEffortStep.x + event.x)
                                        }
                                    }
                                    onClicked:AquaMind.setThinkingEffort(modelData.value)
                                    StyledToolTip {
                                        extraVisibleCondition:settingsEffortStep.buttonHovered
                                        text:"Thinking effort: "+String(settingsEffortStep.modelData.label)
                                    }
                                    contentItem:Item {
                                        Rectangle {
                                            anchors.horizontalCenter:parent.horizontalCenter
                                            y:6
                                            width:8
                                            height:8
                                            radius:4
                                            visible:!settingsEffortStep.isSelected
                                            color:settingsEffortStep.index<=settingsEffortSlider.activeStepIndex
                                                ? Qt.alpha(Appearance.colors.colPrimary,0.68)
                                                : Appearance.colors.colLayer3
                                            border.width:1
                                            border.color:Qt.alpha(Appearance.colors.colOnLayer1,0.28)
                                        }
                                        StyledText {
                                            anchors.horizontalCenter:parent.horizontalCenter
                                            y:18
                                            height:12
                                            text:settingsEffortStep.shortLabel
                                            horizontalAlignment:Text.AlignHCenter
                                            verticalAlignment:Text.AlignVCenter
                                            font.pixelSize:Appearance.font.pixelSize.smallest-3
                                            font.weight:settingsEffortStep.isSelected ? Font.DemiBold : Font.Normal
                                            color:settingsEffortStep.isSelected
                                                ? Appearance.colors.colPrimary
                                                : Appearance.colors.colSubtext
                                        }
                                    }
                                }
                            }
                        }
                        Rectangle {
                            visible:!AquaMind.thinkingSupported
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:60
                            implicitWidth:120
                            implicitHeight:34;radius:10
                            color:Appearance.colors.colLayer2
                            RowLayout {
                                id:privacyRow
                                anchors.fill:parent
                                anchors.leftMargin:10
                                anchors.rightMargin:10
                                spacing:6
                                MaterialSymbol {
                                    text:AquaMind.localOnly ? "verified_user" : "cloud_done"
                                    iconSize:17;color:Appearance.colors.colPrimary
                                }
                                StyledText {
                                    objectName:"aquaAiPrivacyStatus"
                                    Layout.fillWidth:true
                                    Layout.minimumWidth:0
                                    elide:Text.ElideRight
                                    text:(AquaMind.localOnly ? "Local only" : "Cloud models allowed") + " · " + Translation.tr("Providers and connections are shared with AI settings.")
                                    font.pixelSize:Appearance.font.pixelSize.smaller
                                    color:Appearance.colors.colSubtext
                                }
                            }
                            MouseArea {
                                id:privacyHover;anchors.fill:parent;hoverEnabled:true;acceptedButtons:Qt.NoButton
                            }
                            StyledToolTip {
                                text:(AquaMind.localOnly ? "Local only" : "Cloud models allowed") + " · " + Translation.tr("Providers and connections are shared with AI settings.")
                                extraVisibleCondition:privacyHover.containsMouse
                            }
                        }
                        CompactIconButton {
                            objectName:"aquaClearConversation"
                            iconName:"delete_sweep"
                            tooltipText:Translation.tr("Clear conversation")
                            onClicked:AquaMind.clearConversation()
                        }
                    }
                }

                // ROW 1A: Custom Model Display Name (Alias) for long model identifiers (toggled by icon button)
                RowLayout {
                    id:settingsModelAliasRow
                    objectName:"aquaSettingsModelAliasRow"
                    visible:root.modelAliasExpanded && AquaMind.model.length>0
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    spacing:8
                    readonly property string currentSavedAlias:(AquaMind.modelAliases, AquaMind.modelAliasFor(AquaMind.model))
                    Connections {
                        target:AquaMind
                        function onModelChanged():void {
                            settingsModelAliasInput.text=AquaMind.modelAliasFor(AquaMind.model)
                        }
                        function onModelAliasesChanged():void {
                            if(!settingsModelAliasInput.activeFocus) {
                                settingsModelAliasInput.text=AquaMind.modelAliasFor(AquaMind.model)
                            }
                        }
                    }
                    MaterialSymbol {
                        text:"edit_note"
                        iconSize:17
                        color:Appearance.colors.colPrimary
                    }
                    StyledText {
                        text:"Model display name"
                        color:Appearance.colors.colSubtext
                        font.pixelSize:Appearance.font.pixelSize.smaller
                    }
                    Rectangle {
                        Layout.fillWidth:true
                        Layout.preferredWidth:1
                        Layout.minimumWidth:80
                        implicitWidth:120
                        implicitHeight:34
                        radius:10
                        color:Appearance.colors.colLayer1
                        border.width:1
                        border.color:Qt.alpha(Appearance.colors.colPrimary,settingsModelAliasInput.activeFocus ? .8 : .3)
                        StyledTextInput {
                            id:settingsModelAliasInput
                            objectName:"aquaSettingsModelAliasInput"
                            property string placeholderText:AquaMind.rawModelDisplayName(AquaMind.model,"Custom display name...",true)
                            anchors.fill:parent
                            anchors.leftMargin:10
                            anchors.rightMargin:10
                            verticalAlignment:Text.AlignVCenter
                            clip:true
                            selectByMouse:true
                            activeFocusOnTab:true
                            maximumLength:80
                            text:settingsModelAliasRow.currentSavedAlias
                            onAccepted:{
                                AquaMind.setModelAlias(AquaMind.model,text)
                                root.modelAliasExpanded=false
                            }
                        }
                        StyledText {
                            anchors.fill:settingsModelAliasInput
                            verticalAlignment:Text.AlignVCenter
                            visible:settingsModelAliasInput.text.length===0
                            elide:Text.ElideRight
                            text:settingsModelAliasInput.placeholderText
                            color:Appearance.colors.colSubtext
                            font.pixelSize:Appearance.font.pixelSize.smaller
                        }
                    }
                    RippleButton {
                        id:settingsModelAliasSaveBtn
                        objectName:"aquaSettingsModelAliasSave"
                        Layout.fillWidth:false
                        Layout.preferredWidth:54
                        Layout.preferredHeight:32
                        implicitWidth:54
                        implicitHeight:32
                        buttonRadius:9
                        colBackground:Appearance.colors.colPrimaryContainer
                        colBackgroundHover:Appearance.colors.colPrimaryContainerHover
                        onClicked:{
                            AquaMind.setModelAlias(AquaMind.model,settingsModelAliasInput.text)
                            root.modelAliasExpanded=false
                        }
                        contentItem:Item {
                            StyledText {
                                anchors.fill:parent
                                text:"Save"
                                horizontalAlignment:Text.AlignHCenter
                                verticalAlignment:Text.AlignVCenter
                                font.pixelSize:Appearance.font.pixelSize.smaller
                                font.weight:Font.DemiBold
                                color:Appearance.colors.colOnPrimaryContainer
                            }
                        }
                    }
                    CompactIconButton {
                        id:settingsModelAliasResetBtn
                        objectName:"aquaSettingsModelAliasReset"
                        visible:settingsModelAliasRow.currentSavedAlias.length>0 || settingsModelAliasInput.text.trim().length>0
                        iconName:"restart_alt"
                        tooltipText:"Reset to original model name"
                        onClicked:{
                            settingsModelAliasInput.text=""
                            AquaMind.setModelAlias(AquaMind.model,"")
                        }
                    }
                }

                // ROW 1B: Dual-Model Auto-Routing (UI Action vs Deep Thinking) & MTP Auto-Optimization
                GridLayout {
                    objectName:"aquaDualModelRoutingGrid"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    columns:root.availableRowWidth>520 ? 2 : 1
                    uniformCellWidths:columns > 1
                    columnSpacing:14;rowSpacing:6

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:8
                        CompactToggle {
                            objectName:"aquaAutoModelRoutingToggle"
                            Layout.fillWidth:false
                            Layout.minimumWidth:76
                            buttonIcon:"alt_route"
                            text:"Auto-route"
                            tooltipText:"Automatically route desktop/UI commands to your fast Action model (or {REASON:imedium}) and deep reasoning prompts to your Deep Thinking model (or {REASON:xhigh} / {REASON:einstein})."
                            checked:AquaMind.autoModelRoutingEnabled
                            onToggledByUser:checked=>AquaMind.setAutoModelRoutingEnabled(checked)
                        }
                        CompactToggle {
                            objectName:"aquaMtpOptimizeToggle"
                            Layout.fillWidth:false
                            Layout.minimumWidth:76
                            buttonIcon:"speed"
                            text:"MTP Auto-Optimize"
                            tooltipText:"Automatically set repetition_penalty = 1.0 and temperature <= 0.8 when running Multi-Token Prediction (MTP) GGUF models."
                            checked:AquaMind.mtpOptimizeEnabled
                            onToggledByUser:checked=>AquaMind.setMtpOptimizeEnabled(checked)
                        }
                        Rectangle {
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:60
                            implicitWidth:100
                            implicitHeight:34;radius:10
                            color:Appearance.colors.colLayer2
                            RowLayout {
                                anchors.fill:parent
                                anchors.leftMargin:10;anchors.rightMargin:10
                                spacing:6
                                MaterialSymbol {
                                    text:AquaMind.dualModelActive ? "hub" : (AquaMind.currentModelIsPlusIq ? "psychology" : "smart_toy")
                                    iconSize:16;color:Appearance.colors.colPrimary
                                }
                                StyledText {
                                    objectName:"aquaDualModelRouteStatus"
                                    Layout.fillWidth:true
                                    Layout.minimumWidth:0
                                    elide:Text.ElideRight
                                    text:AquaMind.routingSummaryLabel + " · " + AquaMind.mtpStatusLabel
                                    font.pixelSize:Appearance.font.pixelSize.smaller
                                    color:Appearance.colors.colSubtext
                                }
                            }
                            MouseArea {
                                id:routeStatusHover;anchors.fill:parent;hoverEnabled:true;acceptedButtons:Qt.NoButton
                            }
                            StyledToolTip {
                                text:AquaMind.routingSummaryLabel + " · " + AquaMind.mtpStatusLabel
                                extraVisibleCondition:routeStatusHover.containsMouse
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:8
                        StyledComboBox {
                            objectName:"aquaActionModelSelector"
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:80
                            popupMinimumWidth:300
                            enabled:AquaMind.autoModelRoutingEnabled && !AquaMind.busy
                            model:AquaMind.actionSelectableModels
                            textRole:"label"
                            currentIndex:Math.max(0,AquaMind.actionSelectableModels.findIndex(item=>item.name===AquaMind.configuredActionModel))
                            displayText:"UI Action: " + (AquaMind.configuredActionModel.length>0 ? AquaMind.actionModelLabel : ("Auto (" + AquaMind.actionModelLabel + ")"))
                            onActivated:index=>{
                                const item=AquaMind.actionSelectableModels[index]
                                if(item)AquaMind.selectActionModel(item)
                            }
                        }
                        StyledComboBox {
                            objectName:"aquaThinkingModelSelector"
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:80
                            popupMinimumWidth:300
                            enabled:AquaMind.autoModelRoutingEnabled && !AquaMind.busy
                            model:AquaMind.thinkingSelectableModels
                            textRole:"label"
                            currentIndex:Math.max(0,AquaMind.thinkingSelectableModels.findIndex(item=>item.name===AquaMind.configuredThinkingModel))
                            displayText:"Deep Thinking: " + (AquaMind.configuredThinkingModel.length>0 ? AquaMind.thinkingModelLabel : ("Auto (" + AquaMind.thinkingModelLabel + ")"))
                            onActivated:index=>{
                                const item=AquaMind.thinkingSelectableModels[index]
                                if(item)AquaMind.selectThinkingModel(item)
                            }
                        }
                        StyledComboBox {
                            objectName:"aquaLlmIdleUnloadSelector"
                            Layout.fillWidth:false
                            Layout.preferredWidth:138
                            Layout.minimumWidth:116
                            popupMinimumWidth:160
                            model:AquaMind.llmIdleUnloadOptions
                            textRole:"displayName"
                            settingsSearchLabel:"Auto-unload idle LLM"
                            settingsSearchDescription:"Automatically unload local LLM models from memory after idle timeout"
                            currentIndex:Math.max(0,AquaMind.llmIdleUnloadOptions.findIndex(item=>Number(item.value)===AquaMind.llmIdleUnloadMinutes))
                            displayText:"Unload: " + (AquaMind.llmIdleUnloadMinutes > 0 ? (AquaMind.llmIdleUnloadMinutes + "m") : "Never")
                            onActivated:index=>{
                                const item=AquaMind.llmIdleUnloadOptions[index]
                                if(item)AquaMind.setLlmIdleUnloadMinutes(item.value)
                            }
                        }
                    }
                }

                // ROW 5: Unified Full Desktop & App Access (merged Desktop Agent) + Share Alis AI Activity
                GridLayout {
                    objectName:"aquaDialogueGrid"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    columns:root.availableRowWidth>520 ? 2 : 1
                    uniformCellWidths:columns > 1
                    columnSpacing:14;rowSpacing:6

                    CompactToggle {
                        objectName: "companionContext"
                        Layout.preferredWidth:1
                        Layout.minimumWidth:0
                        buttonIcon: "admin_panel_settings"
                        text: Translation.tr("Full Desktop & App Access")
                        tooltipText: Translation.tr("Allow Companion full access to Apps, Desktop & User data, on-screen info, desktop edits, and scripts.")
                        checked: root.preferences.contextEnabled && AquaMind.agentToolsEnabled
                        onToggledByUser: checked => {
                            root.setPreference("contextEnabled", checked)
                            root.setPreference("contextFocusEnabled", checked)
                            AquaMind.agentToolsEnabled = checked
                            AquaMind.agentAppContextEnabled = checked
                        }
                        Item {
                            objectName: "aquaAgentToolsToggle"
                            visible: false
                            property bool checked: root.preferences.contextEnabled && AquaMind.agentToolsEnabled
                        }
                        Item {
                            objectName: "aquaContextAccessGrid"
                            visible: false
                        }
                    }
                    CompactToggle {
                        objectName: "companionContextAi"
                        Layout.preferredWidth:1
                        Layout.minimumWidth:0
                        buttonIcon: "psychology"
                        text: Translation.tr("Share Alis AI activity")
                        tooltipText: Translation.tr("Only working, waiting for approval and finished. No prompts, replies, tools or permission grants.")
                        enabled: root.preferences.contextEnabled
                        checked: root.preferences.contextAiEnabled
                        onToggledByUser: checked => root.setPreference("contextAiEnabled", checked)
                    }
                }

                // ROW 5B: Storage & Capabilities Sub-Tabs (Skills [Built-in + Learned merged], MCP, RAG / State)
                RowLayout {
                    objectName:"aquaStorageFoldersRow"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    spacing:8

                    RippleButton {
                        id:storageTabSkillsBtn
                        objectName:"aquaStorageTabSkills"
                        Layout.preferredHeight:32
                        Layout.preferredWidth:storageTabSkillsRow.implicitWidth+24
                        implicitHeight:32
                        implicitWidth:Layout.preferredWidth
                        buttonRadius:10
                        colBackground:root.activeStorageTab==="skills"
                            ? Appearance.colors.colPrimary
                            : Appearance.colors.colLayer2
                        colBackgroundHover:root.activeStorageTab==="skills"
                            ? Appearance.colors.colPrimaryHover
                            : Appearance.colors.colLayer2Hover
                        onClicked:{
                            root.activeStorageTab="skills"
                            if(AquaMind.liveModelAutoDetectAllowed)
                                AquaMind.refreshAgentStatus()
                        }
                        StyledToolTip {
                            extraVisibleCondition:storageTabSkillsBtn.hovered
                            text:"View merged Built-in & Learned Skills (" + String(Array.isArray(AquaMind.availableSkills) ? AquaMind.availableSkills.length : 0) + ")"
                        }
                        contentItem:Item {
                            implicitWidth:storageTabSkillsRow.implicitWidth
                            implicitHeight:storageTabSkillsRow.implicitHeight
                            Row {
                                id:storageTabSkillsRow
                                anchors.centerIn:parent
                                spacing:6
                                MaterialSymbol {
                                    anchors.verticalCenter:parent.verticalCenter
                                    text:"extension"
                                    iconSize:16
                                    color:root.activeStorageTab==="skills"
                                        ? Appearance.colors.colOnPrimary
                                        : Appearance.colors.colPrimary
                                }
                                StyledText {
                                    anchors.verticalCenter:parent.verticalCenter
                                    verticalAlignment:Text.AlignVCenter
                                    text:"Skills (" + String(Array.isArray(AquaMind.availableSkills) ? AquaMind.availableSkills.length : 0) + ")"
                                    font.pixelSize:Appearance.font.pixelSize.smaller
                                    font.weight:root.activeStorageTab==="skills" ? Font.DemiBold : Font.Normal
                                    color:root.activeStorageTab==="skills"
                                        ? Appearance.colors.colOnPrimary
                                        : Appearance.colors.colOnLayer2
                                }
                            }
                        }
                    }

                    RippleButton {
                        id:storageTabMcpBtn
                        objectName:"aquaStorageTabMcp"
                        readonly property int mcpUpdateCount: Array.isArray(AquaMind.mcpServers)
                            ? AquaMind.mcpServers.filter(s => s && s.hasUpdate === true).length
                            : 0
                        Layout.preferredHeight:32
                        Layout.preferredWidth:storageTabMcpRow.implicitWidth+24
                        implicitHeight:32
                        implicitWidth:Layout.preferredWidth
                        buttonRadius:10
                        colBackground:root.activeStorageTab==="mcp"
                            ? Appearance.colors.colPrimary
                            : Appearance.colors.colLayer2
                        colBackgroundHover:root.activeStorageTab==="mcp"
                            ? Appearance.colors.colPrimaryHover
                            : Appearance.colors.colLayer2Hover
                        onClicked:{
                            root.activeStorageTab="mcp"
                            if(AquaMind.liveModelAutoDetectAllowed)
                                AquaMind.refreshAgentStatus()
                        }
                        StyledToolTip {
                            extraVisibleCondition:storageTabMcpBtn.hovered
                            text:"View Eyes & Hands MCP Servers (" + String(Array.isArray(AquaMind.mcpServers) ? AquaMind.mcpServers.length : 0) + ")"
                        }
                        contentItem:Item {
                            implicitWidth:storageTabMcpRow.implicitWidth
                            implicitHeight:storageTabMcpRow.implicitHeight
                            Row {
                                id:storageTabMcpRow
                                anchors.centerIn:parent
                                spacing:6
                                MaterialSymbol {
                                    anchors.verticalCenter:parent.verticalCenter
                                    text:"hub"
                                    iconSize:16
                                    color:root.activeStorageTab==="mcp"
                                        ? Appearance.colors.colOnPrimary
                                        : Appearance.colors.colPrimary
                                }
                                StyledText {
                                    anchors.verticalCenter:parent.verticalCenter
                                    verticalAlignment:Text.AlignVCenter
                                    text:"MCP (" + String(Array.isArray(AquaMind.mcpServers) ? AquaMind.mcpServers.length : 0) + ")"
                                    font.pixelSize:Appearance.font.pixelSize.smaller
                                    font.weight:root.activeStorageTab==="mcp" ? Font.DemiBold : Font.Normal
                                    color:root.activeStorageTab==="mcp"
                                        ? Appearance.colors.colOnPrimary
                                        : Appearance.colors.colOnLayer2
                                }
                                Rectangle {
                                    visible:storageTabMcpBtn.mcpUpdateCount > 0
                                    anchors.verticalCenter:parent.verticalCenter
                                    implicitHeight:16
                                    implicitWidth:mcpTabBadgeText.implicitWidth+8
                                    radius:8
                                    color:root.activeStorageTab==="mcp"
                                        ? Qt.alpha(Appearance.colors.colOnPrimary,0.22)
                                        : Qt.alpha(Appearance.colors.colPrimary,0.22)
                                    StyledText {
                                        id:mcpTabBadgeText
                                        anchors.centerIn:parent
                                        text:String(storageTabMcpBtn.mcpUpdateCount) + " new"
                                        font.pixelSize:Appearance.font.pixelSize.smallest - 1
                                        font.weight:Font.DemiBold
                                        color:root.activeStorageTab==="mcp"
                                            ? Appearance.colors.colOnPrimary
                                            : Appearance.colors.colPrimary
                                    }
                                }
                            }
                        }
                    }

                    RippleButton {
                        id:storageTabRagBtn
                        objectName:"aquaStorageTabRag"
                        Layout.preferredHeight:32
                        Layout.preferredWidth:storageTabRagRow.implicitWidth+24
                        implicitHeight:32
                        implicitWidth:Layout.preferredWidth
                        buttonRadius:10
                        colBackground:root.activeStorageTab==="rag"
                            ? Appearance.colors.colPrimary
                            : Appearance.colors.colLayer2
                        colBackgroundHover:root.activeStorageTab==="rag"
                            ? Appearance.colors.colPrimaryHover
                            : Appearance.colors.colLayer2Hover
                        onClicked:root.activeStorageTab="rag"
                        StyledToolTip {
                            extraVisibleCondition:storageTabRagBtn.hovered
                            text:"View RAG & State storage paths"
                        }
                        contentItem:Item {
                            implicitWidth:storageTabRagRow.implicitWidth
                            implicitHeight:storageTabRagRow.implicitHeight
                            Row {
                                id:storageTabRagRow
                                anchors.centerIn:parent
                                spacing:6
                                MaterialSymbol {
                                    anchors.verticalCenter:parent.verticalCenter
                                    text:"library_books"
                                    iconSize:16
                                    color:root.activeStorageTab==="rag"
                                        ? Appearance.colors.colOnPrimary
                                        : Appearance.colors.colPrimary
                                }
                                StyledText {
                                    anchors.verticalCenter:parent.verticalCenter
                                    verticalAlignment:Text.AlignVCenter
                                    text:"RAG / State"
                                    font.pixelSize:Appearance.font.pixelSize.smaller
                                    font.weight:root.activeStorageTab==="rag" ? Font.DemiBold : Font.Normal
                                    color:root.activeStorageTab==="rag"
                                        ? Appearance.colors.colOnPrimary
                                        : Appearance.colors.colOnLayer2
                                }
                            }
                        }
                    }

                    Item { Layout.fillWidth:true }

                    RippleButton {
                        id:openSkillsDirBtn
                        objectName:"aquaOpenSkillsDirButton"
                        visible:root.activeStorageTab==="skills"
                        Layout.preferredHeight:28
                        Layout.preferredWidth:openSkillsDirRow.implicitWidth+16
                        implicitHeight:28
                        implicitWidth:Layout.preferredWidth
                        buttonRadius:9
                        colBackground:Appearance.colors.colLayer2
                        colBackgroundHover:Appearance.colors.colLayer2Hover
                        onClicked:AquaMind.openStorageFolder("skills")
                        StyledToolTip {
                            extraVisibleCondition:openSkillsDirBtn.hovered
                            text:"Open Skills folder (" + String(AquaMind.storagePaths?.skillsDir ?? "~/.local/state/alis-companion/skills") + ")"
                        }
                        contentItem:Item {
                            implicitWidth:openSkillsDirRow.implicitWidth
                            implicitHeight:openSkillsDirRow.implicitHeight
                            Row {
                                id:openSkillsDirRow
                                anchors.centerIn:parent
                                spacing:4
                                MaterialSymbol {
                                    anchors.verticalCenter:parent.verticalCenter
                                    text:"folder_open"
                                    iconSize:14
                                    color:Appearance.colors.colPrimary
                                }
                                StyledText {
                                    anchors.verticalCenter:parent.verticalCenter
                                    verticalAlignment:Text.AlignVCenter
                                    text:"Open Skills Folder"
                                    font.pixelSize:Appearance.font.pixelSize.smallest
                                }
                            }
                        }
                    }

                    RippleButton {
                        id:checkMcpUpdatesBtn
                        objectName:"aquaCheckMcpUpdatesButton"
                        visible:root.activeStorageTab==="mcp"
                        Layout.preferredHeight:28
                        Layout.preferredWidth:checkMcpUpdatesRow.implicitWidth+16
                        implicitHeight:28
                        implicitWidth:Layout.preferredWidth
                        buttonRadius:9
                        colBackground:Appearance.colors.colLayer2
                        colBackgroundHover:Appearance.colors.colLayer2Hover
                        onClicked:AquaMind.checkMcpUpdates()
                        StyledToolTip {
                            extraVisibleCondition:checkMcpUpdatesBtn.hovered
                            text:"Check for new MCP server versions"
                        }
                        contentItem:Item {
                            implicitWidth:checkMcpUpdatesRow.implicitWidth
                            implicitHeight:checkMcpUpdatesRow.implicitHeight
                            Row {
                                id:checkMcpUpdatesRow
                                anchors.centerIn:parent
                                spacing:4
                                MaterialSymbol {
                                    anchors.verticalCenter:parent.verticalCenter
                                    text:"system_update_alt"
                                    iconSize:14
                                    color:Appearance.colors.colPrimary
                                }
                                StyledText {
                                    anchors.verticalCenter:parent.verticalCenter
                                    verticalAlignment:Text.AlignVCenter
                                    text:"Check Updates"
                                    font.pixelSize:Appearance.font.pixelSize.smallest
                                }
                            }
                        }
                    }

                    RippleButton {
                        id:openMcpDirBtn
                        objectName:"aquaOpenMcpDirButton"
                        visible:root.activeStorageTab==="mcp"
                        Layout.preferredHeight:28
                        Layout.preferredWidth:openMcpRow.implicitWidth+16
                        implicitHeight:28
                        implicitWidth:Layout.preferredWidth
                        buttonRadius:9
                        colBackground:Appearance.colors.colLayer2
                        colBackgroundHover:Appearance.colors.colLayer2Hover
                        onClicked:AquaMind.openStorageFolder("mcp")
                        StyledToolTip {
                            extraVisibleCondition:openMcpDirBtn.hovered
                            text:"Open MCP config folder (" + String(AquaMind.storagePaths?.mcpConfigDir ?? "~/.config/alis-companion") + ")"
                        }
                        contentItem:Item {
                            implicitWidth:openMcpRow.implicitWidth
                            implicitHeight:openMcpRow.implicitHeight
                            Row {
                                id:openMcpRow
                                anchors.centerIn:parent
                                spacing:4
                                MaterialSymbol {
                                    anchors.verticalCenter:parent.verticalCenter
                                    text:"folder_open"
                                    iconSize:14
                                    color:Appearance.colors.colPrimary
                                }
                                StyledText {
                                    anchors.verticalCenter:parent.verticalCenter
                                    verticalAlignment:Text.AlignVCenter
                                    text:"Open MCP Folder"
                                    font.pixelSize:Appearance.font.pixelSize.smallest
                                }
                            }
                        }
                    }

                    RippleButton {
                        id:openRagDirBtn
                        objectName:"aquaOpenRagDirButton"
                        visible:root.activeStorageTab==="rag"
                        Layout.preferredHeight:28
                        Layout.preferredWidth:openRagRow.implicitWidth+16
                        implicitHeight:28
                        implicitWidth:Layout.preferredWidth
                        buttonRadius:9
                        colBackground:Appearance.colors.colLayer2
                        colBackgroundHover:Appearance.colors.colLayer2Hover
                        onClicked:AquaMind.openStorageFolder("rag")
                        StyledToolTip {
                            extraVisibleCondition:openRagDirBtn.hovered
                            text:"Open RAG & State storage folder (" + String(AquaMind.storagePaths?.ragStateDir ?? "~/.local/state/alis-companion") + ")"
                        }
                        contentItem:Item {
                            implicitWidth:openRagRow.implicitWidth
                            implicitHeight:openRagRow.implicitHeight
                            Row {
                                id:openRagRow
                                anchors.centerIn:parent
                                spacing:4
                                MaterialSymbol {
                                    anchors.verticalCenter:parent.verticalCenter
                                    text:"folder_open"
                                    iconSize:14
                                    color:Appearance.colors.colPrimary
                                }
                                StyledText {
                                    anchors.verticalCenter:parent.verticalCenter
                                    verticalAlignment:Text.AlignVCenter
                                    text:"Open RAG / State Folder"
                                    font.pixelSize:Appearance.font.pixelSize.smallest
                                }
                            }
                        }
                    }

                    CompactIconButton {
                        objectName:"aquaRefreshSkillsButton"
                        iconName:"refresh"
                        tooltipText:"Refresh Skills, MCP servers, and storage directories"
                        onClicked:AquaMind.refreshAgentStatus()
                    }
                }

                // ROW 5C-1: Companion Skills Catalog (Built-in + Learned merged)
                Rectangle {
                    id:skillsListSection
                    objectName:"aquaSkillsListSection"
                    visible:root.activeStorageTab==="skills"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    implicitHeight:skillsListColumn.implicitHeight+20
                    radius:12
                    color:Appearance.colors.colLayer1
                    border.width:1
                    border.color:Qt.alpha(Appearance.colors.colPrimary,0.18)

                    ColumnLayout {
                        id:skillsListColumn
                        anchors.left:parent.left
                        anchors.right:parent.right
                        anchors.top:parent.top
                        anchors.margins:10
                        spacing:8

                        RowLayout {
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:0
                            spacing:8
                            MaterialSymbol {
                                text:"extension"
                                iconSize:18
                                color:Appearance.colors.colPrimary
                            }
                            StyledText {
                                objectName:"aquaSkillsSectionHeader"
                                text:"Companion Skills (" + String(Array.isArray(AquaMind.availableSkills) ? AquaMind.availableSkills.length : 0) + ")"
                                font.weight:Font.DemiBold
                            }
                            Item { Layout.fillWidth:true; Layout.minimumWidth:0 }
                            CompactToggle {
                                objectName:"aquaAgentSkillsToggle"
                                Layout.fillWidth:false
                                Layout.minimumWidth:80
                                buttonIcon:"extension"
                                text:"Agent Skills"
                                tooltipText:"Discover and execute modular SKILL.md workflows and scripts."
                                enabled:AquaMind.agentToolsEnabled
                                checked:AquaMind.agentSkillsEnabled
                                onToggledByUser:checked=>AquaMind.agentSkillsEnabled=checked
                            }
                        }

                        Flickable {
                            id:skillsListFlickable
                            objectName:"aquaSkillsListFlickable"
                            readonly property real rowUnitHeight:skillsCardsRepeater.count>0 && skillsCardsRepeater.itemAt(0)
                                ? skillsCardsRepeater.itemAt(0).implicitHeight
                                : 74
                            readonly property real maxThreeRowsHeight:rowUnitHeight*3 + skillsCardsGrid.rowSpacing*2
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:0
                            implicitHeight:Math.min(skillsCardsGrid.implicitHeight,maxThreeRowsHeight)
                            Layout.preferredHeight:implicitHeight
                            Layout.maximumHeight:maxThreeRowsHeight
                            contentWidth:width
                            contentHeight:skillsCardsGrid.implicitHeight
                            clip:true
                            boundsBehavior:Flickable.StopAtBounds
                            interactive:contentHeight>height+1

                            WheelHandler {
                                enabled:skillsListFlickable.contentHeight>skillsListFlickable.height+1
                                acceptedDevices:PointerDevice.Mouse | PointerDevice.TouchPad
                                onWheel:event=>{
                                    const maxY=Math.max(0,skillsListFlickable.contentHeight-skillsListFlickable.height)
                                    skillsListFlickable.contentY=Math.max(0,Math.min(maxY,skillsListFlickable.contentY-event.angleDelta.y*0.5))
                                    event.accepted=true
                                }
                            }

                            GridLayout {
                                id:skillsCardsGrid
                                objectName:"aquaSkillsCardsGrid"
                                width:skillsListFlickable.width
                                columns:root.availableRowWidth>560 ? 2 : 1
                                uniformCellWidths:columns > 1
                                columnSpacing:10
                                rowSpacing:8

                                Repeater {
                                    id:skillsCardsRepeater
                                    model:Array.isArray(AquaMind.availableSkills) ? AquaMind.availableSkills : []
                                    Rectangle {
                                        id:skillCard
                                        required property var modelData
                                        required property int index
                                        readonly property string skillName:String(modelData?.name ?? "")
                                        readonly property bool skillEnabled:modelData?.enabled !== false
                                        readonly property bool isBuiltIn:modelData?.builtIn === true || modelData?.source === "builtin"
                                        objectName:"aquaSkillCard-" + skillName
                                        Layout.fillWidth:true
                                        Layout.preferredWidth:1
                                        Layout.minimumWidth:0
                                        implicitHeight:skillCardCol.implicitHeight+16
                                        radius:10
                                        color:Appearance.colors.colLayer2
                                        border.width:1
                                        border.color:skillEnabled
                                            ? Qt.alpha(Appearance.colors.colPrimary,0.24)
                                            : Qt.alpha(Appearance.colors.colSubtext,0.15)
                                        opacity:skillEnabled ? 1.0 : 0.68

                                        ColumnLayout {
                                            id:skillCardCol
                                            anchors.left:parent.left
                                            anchors.right:parent.right
                                            anchors.top:parent.top
                                            anchors.margins:8
                                            spacing:4

                                            RowLayout {
                                                Layout.fillWidth:true
                                                Layout.preferredWidth:1
                                                Layout.minimumWidth:0
                                                spacing:6
                                                MaterialSymbol {
                                                    text:skillCard.isBuiltIn ? "auto_awesome" : "psychology_alt"
                                                    iconSize:16
                                                    color:skillCard.skillEnabled ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
                                                }
                                                StyledText {
                                                    objectName:"aquaSkillTitle-" + skillCard.skillName
                                                    text:skillCard.skillName
                                                    font.weight:Font.DemiBold
                                                }
                                                Rectangle {
                                                    implicitHeight:18
                                                    implicitWidth:badgeText.implicitWidth+10
                                                    radius:9
                                                    color:Qt.alpha(Appearance.colors.colPrimary,skillCard.isBuiltIn ? 0.14 : 0.22)
                                                    StyledText {
                                                        id:badgeText
                                                        anchors.centerIn:parent
                                                        text:(skillCard.isBuiltIn ? "Built-in" : "Learned") + " · " + String(skillCard.modelData?.category ?? "general")
                                                        font.pixelSize:Appearance.font.pixelSize.smallest - 1
                                                        color:Appearance.colors.colPrimary
                                                    }
                                                }
                                                Item { Layout.fillWidth:true; Layout.minimumWidth:0 }
                                                CompactIconButton {
                                                    objectName:"aquaSkillOpen-" + skillCard.skillName
                                                    implicitWidth:28
                                                    implicitHeight:28
                                                    buttonRadius:14
                                                    iconName:"folder_open"
                                                    tooltipText:"Open folder for " + skillCard.skillName
                                                    onClicked:AquaMind.openStorageFolder(
                                                        skillCard.isBuiltIn ? "builtin_skills" : "skills",
                                                        String(skillCard.modelData?.folder ?? "")
                                                    )
                                                }
                                                CompactIconToggle {
                                                    objectName:"aquaSkillToggle-" + skillCard.skillName
                                                    implicitWidth:28
                                                    implicitHeight:28
                                                    buttonRadius:14
                                                    text:skillCard.skillName
                                                    buttonIcon:skillCard.skillEnabled ? "check_circle" : "radio_button_unchecked"
                                                    checked:skillCard.skillEnabled
                                                    tooltipText:skillCard.skillEnabled ? ("Disable " + skillCard.skillName) : ("Enable " + skillCard.skillName)
                                                    onToggledByUser:nextChecked=>AquaMind.setSkillEnabled(skillCard.skillName,nextChecked)
                                                }
                                            }

                                            StyledText {
                                                objectName:"aquaSkillDesc-" + skillCard.skillName
                                                Layout.fillWidth:true
                                                Layout.preferredWidth:1
                                                Layout.minimumWidth:0
                                                text:String(skillCard.modelData?.description ?? "")
                                                maximumLineCount:1
                                                elide:Text.ElideRight
                                                font.pixelSize:Appearance.font.pixelSize.smaller
                                                color:Appearance.colors.colSubtext
                                            }

                                            StyledText {
                                                Layout.fillWidth:true
                                                Layout.preferredWidth:1
                                                Layout.minimumWidth:0
                                                elide:Text.ElideRight
                                                text:"Trigger: /" + skillCard.skillName
                                                    + " · v" + String(skillCard.modelData?.version ?? "1.0.0")
                                                    + (Number(skillCard.modelData?.usageCount ?? 0) > 0
                                                        ? (" · Used " + String(skillCard.modelData.usageCount) + "x")
                                                        : "")
                                                font.pixelSize:Appearance.font.pixelSize.smallest - 1
                                                color:Appearance.colors.colSubtext
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // ROW 5C-2: MCP Servers Catalog & Update Management (Eyes & Hands)
                Rectangle {
                    id:mcpListSection
                    objectName:"aquaMcpListSection"
                    visible:root.activeStorageTab==="mcp"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    implicitHeight:mcpListColumn.implicitHeight+20
                    radius:12
                    color:Appearance.colors.colLayer1
                    border.width:1
                    border.color:Qt.alpha(Appearance.colors.colPrimary,0.18)

                    ColumnLayout {
                        id:mcpListColumn
                        anchors.left:parent.left
                        anchors.right:parent.right
                        anchors.top:parent.top
                        anchors.margins:10
                        spacing:8

                        RowLayout {
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:0
                            spacing:8
                            MaterialSymbol {
                                text:"hub"
                                iconSize:18
                                color:Appearance.colors.colPrimary
                            }
                            StyledText {
                                objectName:"aquaMcpSectionHeader"
                                text:"MCP Servers (" + String(Array.isArray(AquaMind.mcpServers) ? AquaMind.mcpServers.length : 0) + ")"
                                font.weight:Font.DemiBold
                            }
                            Item { Layout.fillWidth:true; Layout.minimumWidth:0 }
                            CompactToggle {
                                objectName:"aquaAgentMcpToggle"
                                Layout.fillWidth:false
                                Layout.minimumWidth:68
                                buttonIcon:"hub"
                                text:"MCP"
                                tooltipText:"Connect to Eyes & Hands MCP servers (computer-use-linux, playwright-mcp, chrome-devtools-mcp, desktop-commander, companion-system)."
                                enabled:AquaMind.agentToolsEnabled
                                checked:AquaMind.agentMcpEnabled
                                onToggledByUser:checked=>AquaMind.agentMcpEnabled=checked
                            }
                        }

                        Flickable {
                            id:mcpListFlickable
                            objectName:"aquaMcpListFlickable"
                            readonly property real rowUnitHeight:mcpCardsRepeater.count>0 && mcpCardsRepeater.itemAt(0)
                                ? mcpCardsRepeater.itemAt(0).implicitHeight
                                : 74
                            readonly property real maxThreeRowsHeight:rowUnitHeight*3 + mcpCardsGrid.rowSpacing*2
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:0
                            implicitHeight:Math.min(mcpCardsGrid.implicitHeight,maxThreeRowsHeight)
                            Layout.preferredHeight:implicitHeight
                            Layout.maximumHeight:maxThreeRowsHeight
                            contentWidth:width
                            contentHeight:mcpCardsGrid.implicitHeight
                            clip:true
                            boundsBehavior:Flickable.StopAtBounds
                            interactive:contentHeight>height+1

                            WheelHandler {
                                enabled:mcpListFlickable.contentHeight>mcpListFlickable.height+1
                                acceptedDevices:PointerDevice.Mouse | PointerDevice.TouchPad
                                onWheel:event=>{
                                    const maxY=Math.max(0,mcpListFlickable.contentHeight-mcpListFlickable.height)
                                    mcpListFlickable.contentY=Math.max(0,Math.min(maxY,mcpListFlickable.contentY-event.angleDelta.y*0.5))
                                    event.accepted=true
                                }
                            }

                            GridLayout {
                                id:mcpCardsGrid
                                objectName:"aquaMcpCardsGrid"
                                width:mcpListFlickable.width
                                columns:root.availableRowWidth>560 ? 2 : 1
                                uniformCellWidths:columns > 1
                                columnSpacing:10
                                rowSpacing:8

                                Repeater {
                                    id:mcpCardsRepeater
                                    model:Array.isArray(AquaMind.mcpServers) ? AquaMind.mcpServers : []
                                    Rectangle {
                                        id:mcpCard
                                        required property var modelData
                                        required property int index
                                        readonly property string serverName:String(modelData?.name ?? "")
                                        readonly property bool serverEnabled:modelData?.enabled !== false
                                        readonly property bool hasUpdate:modelData?.hasUpdate === true
                                        readonly property string currentVer:String(modelData?.version ?? "1.0.0")
                                        readonly property string latestVer:String(modelData?.latestVersion ?? currentVer)
                                        readonly property string roleLabel:String(modelData?.role ?? "MCP")
                                        readonly property string catName:String(modelData?.category ?? "custom")
                                        objectName:"aquaMcpCard-" + serverName
                                        Layout.fillWidth:true
                                        Layout.preferredWidth:1
                                        Layout.minimumWidth:0
                                        implicitHeight:mcpCardCol.implicitHeight+16
                                        radius:10
                                        color:Appearance.colors.colLayer2
                                        border.width:1
                                        border.color:hasUpdate
                                            ? Qt.alpha(Appearance.colors.colPrimary,0.45)
                                            : (serverEnabled
                                                ? Qt.alpha(Appearance.colors.colPrimary,0.24)
                                                : Qt.alpha(Appearance.colors.colSubtext,0.15))
                                        opacity:serverEnabled ? 1.0 : 0.68

                                        ColumnLayout {
                                            id:mcpCardCol
                                            anchors.left:parent.left
                                            anchors.right:parent.right
                                            anchors.top:parent.top
                                            anchors.margins:8
                                            spacing:4

                                            RowLayout {
                                                Layout.fillWidth:true
                                                Layout.preferredWidth:1
                                                Layout.minimumWidth:0
                                                spacing:6
                                                MaterialSymbol {
                                                    text:mcpCard.catName === "eyes"
                                                        ? "travel_explore"
                                                        : (mcpCard.catName === "eyes-hands"
                                                            ? "visibility"
                                                            : (mcpCard.catName === "hands" ? "terminal" : "hub"))
                                                    iconSize:16
                                                    color:mcpCard.serverEnabled ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
                                                }
                                                StyledText {
                                                    objectName:"aquaMcpTitle-" + mcpCard.serverName
                                                    text:mcpCard.serverName
                                                    font.weight:Font.DemiBold
                                                }
                                                Rectangle {
                                                    implicitHeight:18
                                                    implicitWidth:mcpRoleBadgeText.implicitWidth+10
                                                    radius:9
                                                    color:Qt.alpha(Appearance.colors.colPrimary,0.14)
                                                    StyledText {
                                                        id:mcpRoleBadgeText
                                                        anchors.centerIn:parent
                                                        text:mcpCard.roleLabel
                                                        font.pixelSize:Appearance.font.pixelSize.smallest - 1
                                                        color:Appearance.colors.colPrimary
                                                    }
                                                }
                                                Rectangle {
                                                    objectName:"aquaMcpVersionBadge-" + mcpCard.serverName
                                                    implicitHeight:18
                                                    implicitWidth:mcpVerBadgeText.implicitWidth+10
                                                    radius:9
                                                    color:mcpCard.hasUpdate
                                                        ? Qt.alpha(Appearance.colors.colPrimary,0.26)
                                                        : Qt.alpha(Appearance.colors.colSubtext,0.14)
                                                    StyledText {
                                                        id:mcpVerBadgeText
                                                        objectName:"aquaMcpVersionText-" + mcpCard.serverName
                                                        anchors.centerIn:parent
                                                        text:mcpCard.hasUpdate
                                                            ? ("v" + mcpCard.currentVer + " → v" + mcpCard.latestVer)
                                                            : ("v" + mcpCard.currentVer)
                                                        font.pixelSize:Appearance.font.pixelSize.smallest - 1
                                                        font.weight:mcpCard.hasUpdate ? Font.DemiBold : Font.Normal
                                                        color:mcpCard.hasUpdate
                                                            ? Appearance.colors.colPrimary
                                                            : Appearance.colors.colSubtext
                                                    }
                                                }
                                                Item { Layout.fillWidth:true; Layout.minimumWidth:0 }
                                                RippleButton {
                                                    id:mcpUpdateBtn
                                                    objectName:"aquaMcpUpdate-" + mcpCard.serverName
                                                    visible:mcpCard.hasUpdate
                                                    Layout.preferredHeight:24
                                                    Layout.preferredWidth:mcpUpdateBtnRow.implicitWidth+14
                                                    implicitHeight:24
                                                    implicitWidth:Layout.preferredWidth
                                                    buttonRadius:12
                                                    colBackground:Appearance.colors.colPrimary
                                                    colBackgroundHover:Appearance.colors.colPrimaryHover
                                                    onClicked:AquaMind.updateMcpServer(mcpCard.serverName)
                                                    StyledToolTip {
                                                        extraVisibleCondition:mcpUpdateBtn.hovered
                                                        text:"Update " + mcpCard.serverName + " to v" + mcpCard.latestVer
                                                    }
                                                    contentItem:Item {
                                                        implicitWidth:mcpUpdateBtnRow.implicitWidth
                                                        implicitHeight:mcpUpdateBtnRow.implicitHeight
                                                        Row {
                                                            id:mcpUpdateBtnRow
                                                            anchors.centerIn:parent
                                                            spacing:3
                                                            MaterialSymbol {
                                                                anchors.verticalCenter:parent.verticalCenter
                                                                text:"upgrade"
                                                                iconSize:13
                                                                color:Appearance.colors.colOnPrimary
                                                            }
                                                            StyledText {
                                                                anchors.verticalCenter:parent.verticalCenter
                                                                verticalAlignment:Text.AlignVCenter
                                                                text:"Update"
                                                                font.pixelSize:Appearance.font.pixelSize.smallest - 1
                                                                font.weight:Font.DemiBold
                                                                color:Appearance.colors.colOnPrimary
                                                            }
                                                        }
                                                    }
                                                }
                                                CompactIconButton {
                                                    objectName:"aquaMcpOpen-" + mcpCard.serverName
                                                    implicitWidth:28
                                                    implicitHeight:28
                                                    buttonRadius:14
                                                    iconName:"folder_open"
                                                    tooltipText:"Open folder for " + mcpCard.serverName
                                                    onClicked:AquaMind.openStorageFolder(
                                                        "mcp",
                                                        String(mcpCard.modelData?.folder ?? "")
                                                    )
                                                }
                                                CompactIconToggle {
                                                    objectName:"aquaMcpToggle-" + mcpCard.serverName
                                                    implicitWidth:28
                                                    implicitHeight:28
                                                    buttonRadius:14
                                                    text:mcpCard.serverName
                                                    buttonIcon:mcpCard.serverEnabled ? "check_circle" : "radio_button_unchecked"
                                                    checked:mcpCard.serverEnabled
                                                    tooltipText:mcpCard.serverEnabled ? ("Disable " + mcpCard.serverName) : ("Enable " + mcpCard.serverName)
                                                    onToggledByUser:nextChecked=>AquaMind.setMcpServerEnabled(mcpCard.serverName,nextChecked)
                                                }
                                            }

                                            StyledText {
                                                objectName:"aquaMcpDesc-" + mcpCard.serverName
                                                Layout.fillWidth:true
                                                Layout.preferredWidth:1
                                                Layout.minimumWidth:0
                                                text:String(mcpCard.modelData?.description ?? "")
                                                maximumLineCount:1
                                                elide:Text.ElideRight
                                                font.pixelSize:Appearance.font.pixelSize.smaller
                                                color:Appearance.colors.colSubtext
                                            }

                                            StyledText {
                                                Layout.fillWidth:true
                                                Layout.preferredWidth:1
                                                Layout.minimumWidth:0
                                                elide:Text.ElideRight
                                                text:"Package: " + String(mcpCard.modelData?.package ?? mcpCard.serverName)
                                                    + " · Transport: " + String(mcpCard.modelData?.transport ?? "stdio")
                                                    + (mcpCard.hasUpdate ? (" · New version v" + mcpCard.latestVer + " available") : " · Up to date")
                                                font.pixelSize:Appearance.font.pixelSize.smallest - 1
                                                color:mcpCard.hasUpdate ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // ROW 5C-3: RAG & State Storage Section
                Rectangle {
                    id:ragListSection
                    objectName:"aquaRagListSection"
                    visible:root.activeStorageTab==="rag"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    implicitHeight:ragListColumn.implicitHeight+20
                    radius:12
                    color:Appearance.colors.colLayer1
                    border.width:1
                    border.color:Qt.alpha(Appearance.colors.colPrimary,0.18)

                    readonly property var ragRowsData: {
                        const rows = [
                            {
                                icon: "folder",
                                label: "State Directory",
                                value: String(AquaMind.storagePaths?.stateDir ?? "~/.local/state/alis-companion")
                            },
                            {
                                icon: "extension",
                                label: "Skills Directory",
                                value: String(AquaMind.storagePaths?.skillsDir ?? "~/.local/state/alis-companion/skills")
                            },
                            {
                                icon: "hub",
                                label: "MCP Config",
                                value: String(AquaMind.storagePaths?.mcpConfigDir ?? "~/.config/alis-companion")
                            }
                        ]
                        const sources = Array.isArray(AquaMind.ragSources) ? AquaMind.ragSources : []
                        for (let i = 0; i < sources.length; ++i) {
                            const s = sources[i]
                            rows.push({
                                icon: "library_books",
                                label: String(s?.name ?? ("RAG Source #" + (i + 1))),
                                value: String(s?.path ?? s ?? "")
                            })
                        }
                        return rows
                    }

                    ColumnLayout {
                        id:ragListColumn
                        anchors.left:parent.left
                        anchors.right:parent.right
                        anchors.top:parent.top
                        anchors.margins:10
                        spacing:8

                        RowLayout {
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:0
                            spacing:8
                            MaterialSymbol {
                                text:"library_books"
                                iconSize:18
                                color:Appearance.colors.colPrimary
                            }
                            StyledText {
                                objectName:"aquaRagSectionHeader"
                                text:"RAG & State Storage"
                                font.weight:Font.DemiBold
                            }
                            Item { Layout.fillWidth:true; Layout.minimumWidth:0 }
                            CompactToggle {
                                objectName:"aquaAgentDocRagToggle"
                                Layout.fillWidth:false
                                Layout.minimumWidth:80
                                buttonIcon:"library_books"
                                text:"Docs RAG"
                                tooltipText:"Retrieve grounded snippets with citations across Companion docs, AquaPocket files, and user RAG folders."
                                enabled:AquaMind.agentToolsEnabled
                                checked:AquaMind.agentDocRagEnabled
                                onToggledByUser:checked=>{
                                    AquaMind.agentDocRagEnabled=checked
                                    if(checked)AquaMind.evaluateToolRouting("How does Companion architecture work?",true)
                                }
                            }
                        }

                        Flickable {
                            id:ragListFlickable
                            objectName:"aquaRagListFlickable"
                            readonly property real rowUnitHeight:30
                            readonly property real maxThreeRowsHeight:rowUnitHeight*3 + ragRowsColumn.spacing*2
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:0
                            implicitHeight:Math.min(ragRowsColumn.implicitHeight,maxThreeRowsHeight)
                            Layout.preferredHeight:implicitHeight
                            Layout.maximumHeight:maxThreeRowsHeight
                            contentWidth:width
                            contentHeight:ragRowsColumn.implicitHeight
                            clip:true
                            boundsBehavior:Flickable.StopAtBounds
                            interactive:contentHeight>height+1

                            WheelHandler {
                                enabled:ragListFlickable.contentHeight>ragListFlickable.height+1
                                acceptedDevices:PointerDevice.Mouse | PointerDevice.TouchPad
                                onWheel:event=>{
                                    const maxY=Math.max(0,ragListFlickable.contentHeight-ragListFlickable.height)
                                    ragListFlickable.contentY=Math.max(0,Math.min(maxY,ragListFlickable.contentY-event.angleDelta.y*0.5))
                                    event.accepted=true
                                }
                            }

                            ColumnLayout {
                                id:ragRowsColumn
                                width:ragListFlickable.width
                                spacing:6

                                Repeater {
                                    model:ragListSection.ragRowsData
                                    Rectangle {
                                        required property var modelData
                                        required property int index
                                        objectName:"aquaRagRow-" + index
                                        Layout.fillWidth:true
                                        Layout.preferredWidth:1
                                        Layout.minimumWidth:0
                                        implicitHeight:30
                                        radius:8
                                        color:Appearance.colors.colLayer2
                                        RowLayout {
                                            anchors.fill:parent
                                            anchors.leftMargin:10
                                            anchors.rightMargin:10
                                            spacing:8
                                            MaterialSymbol {
                                                text:String(modelData.icon ?? "folder")
                                                iconSize:15
                                                color:Appearance.colors.colPrimary
                                            }
                                            StyledText {
                                                text:String(modelData.label ?? "") + ":"
                                                font.pixelSize:Appearance.font.pixelSize.smaller
                                                font.weight:Font.DemiBold
                                            }
                                            StyledText {
                                                Layout.fillWidth:true
                                                Layout.minimumWidth:0
                                                elide:Text.ElideRight
                                                text:String(modelData.value ?? "")
                                                font.pixelSize:Appearance.font.pixelSize.smaller
                                                color:Appearance.colors.colSubtext
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // ROW 6: Explicit Local Memories (2 Columns)
                GridLayout {
                    objectName:"aquaAiPermissionsGrid"
                    Layout.fillWidth:true
                    Layout.preferredWidth:1
                    Layout.minimumWidth:0
                    columns:root.availableRowWidth>520 ? 2 : 1
                    uniformCellWidths:columns > 1
                    columnSpacing:14;rowSpacing:6

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:8
                        CompactToggle {
                            objectName:"aquaEnableMemory"
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:90
                            buttonIcon:"bookmark"
                            text:Translation.tr("Use saved memories with local models")
                            tooltipText:Translation.tr("Only entries you save here are remembered. No automatic extraction from chat. Turning this off stops use and keeps your records available for review or deletion.")
                            checked:AquaMind.memoryEnabled
                            enabled:AquaMind.options.memoryEnabled !== undefined && !AquaMind.busy
                            onToggledByUser:checked=>Config.setNestedValue("abyss.companionMind.memoryEnabled",checked)
                        }
                        InlineChoice {
                            objectName:"aquaMemoryScope"
                            Layout.fillWidth:false
                            label:""
                            description:Translation.tr("Remember for")
                            comboMinWidth:76
                            options:[{displayName:"Aqua",value:"aqua"},{displayName:"Octo",value:"octo"},{displayName:Translation.tr("Both"),value:"shared"}]
                            currentValue:root.memoryScope
                            onSelected:value=>root.memoryScope=value
                        }
                        InlineChoice {
                            objectName:"aquaMemoryTtl"
                            Layout.fillWidth:false
                            label:""
                            description:Translation.tr("Keep memory")
                            comboMinWidth:90
                            options:[{displayName:Translation.tr("7 days"),value:7},{displayName:Translation.tr("30 days"),value:30},{displayName:Translation.tr("Until I forget it"),value:0}]
                            currentValue:root.memoryTtlDays
                            onSelected:value=>root.memoryTtlDays=value
                        }
                    }

                    RowLayout {
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;Layout.alignment:Qt.AlignVCenter;spacing:8
                        Rectangle {
                            Layout.fillWidth:true
                            Layout.preferredWidth:1
                            Layout.minimumWidth:80
                            implicitHeight:34;radius:10
                            color:Appearance.colors.colLayer1
                            border.width:1;border.color:Qt.alpha(Appearance.colors.colPrimary,memoryInput.activeFocus ? .8 : .3)
                            StyledTextInput {
                                id:memoryInput
                                objectName:"aquaMemoryDraft"
                                anchors.fill:parent;anchors.leftMargin:10;anchors.rightMargin:10
                                verticalAlignment:Text.AlignVCenter
                                enabled:AquaMind.memoryEnabled && !AquaMind.busy
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
                            objectName:"aquaSaveMemory"
                            iconName:root.memoryEditingId ? "save" : "add"
                            tooltipText:root.memoryEditingId ? Translation.tr("Save memory changes") : Translation.tr("Save memory")
                            enabled:AquaMind.memoryEnabled && !AquaMind.busy && root.memoryDraft.trim().length>0
                            onClicked:if(AquaMind.saveMemory(root.memoryDraft,root.memoryScope,root.memoryTtlDays,root.memoryEditingId)) {
                                root.memoryDraft="";root.memoryEditingId=0
                            }
                        }
                        CompactIconButton {
                            objectName:"aquaReviewMemory"
                            iconName:"refresh"
                            tooltipText:Translation.tr("Review saved memories")
                            enabled:!AquaMind.busy
                            onClicked:AquaMind.refreshMemory()
                        }
                        CompactIconButton {
                            objectName:"aquaForgetAllMemory"
                            iconName:root.confirmForgetMemories ? "warning" : "delete_sweep"
                            tooltipText:root.confirmForgetMemories ? Translation.tr("Confirm forgetting all saved memories") : Translation.tr("Forget all saved memories")
                            enabled:!AquaMind.busy && AquaMind.memories.length>0
                            onClicked:{
                                if(root.confirmForgetMemories){AquaMind.forgetAllMemories();root.confirmForgetMemories=false}
                                else root.confirmForgetMemories=true
                            }
                        }
                    }
                }

                Repeater {
                    model:AquaMind.toolReceipts
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;spacing:8
                        StyledText {
                            Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;elide:Text.ElideRight;textFormat:Text.PlainText
                            font.pixelSize:Appearance.font.pixelSize.smaller;color:Appearance.colors.colSubtext
                            text:modelData.field+": "+modelData.beforeValue+" → "+modelData.afterValue+(modelData.rolledBack ? " (rolled back)" : "")
                        }
                        CompactIconButton {
                            iconName:"undo"
                            tooltipText:"Rollback preference update"
                            enabled:!AquaMind.busy && !modelData.rolledBack
                            onClicked:AquaMind.rollbackToolReceipt(String(modelData.receiptId))
                        }
                    }
                }
                StyledText {
                    visible:AquaMind.memoryNotice.length>0
                    Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;wrapMode:Text.WordWrap;textFormat:Text.PlainText
                    text:AquaMind.memoryNotice
                    font.pixelSize:Appearance.font.pixelSize.smaller
                    color:Appearance.colors.colSubtext
                }
                Repeater {
                    model:AquaMind.memories
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;spacing:8
                        StyledText {
                            Layout.fillWidth:true;Layout.preferredWidth:1;Layout.minimumWidth:0;elide:Text.ElideRight;textFormat:Text.PlainText
                            text:modelData.scope+" · "+modelData.text
                        }
                        StyledText {
                            Layout.minimumWidth:0
                            textFormat:Text.PlainText;color:Appearance.colors.colSubtext
                            font.pixelSize:Appearance.font.pixelSize.smaller
                            text:modelData.expires_ms===0 ? Translation.tr("Until forgotten")
                                : (modelData.expires_ms<=Date.now() ? Translation.tr("Expired") : Translation.tr("Expires"))
                                    +" · "+Qt.formatDateTime(new Date(modelData.expires_ms),"yyyy-MM-dd")
                        }
                        CompactIconButton {
                            iconName:"edit"
                            tooltipText:Translation.tr("Edit")
                            enabled:AquaMind.memoryEnabled && !AquaMind.busy
                            onClicked:{root.memoryDraft=modelData.text;root.memoryScope=modelData.scope;root.memoryEditingId=modelData.id}
                        }
                        CompactIconButton {
                            iconName:"delete"
                            tooltipText:Translation.tr("Forget")
                            enabled:!AquaMind.busy
                            onClicked:AquaMind.forgetMemory(modelData.id)
                        }
                    }
                }
            }
        }
    }

}
