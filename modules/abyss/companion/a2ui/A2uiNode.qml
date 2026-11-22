pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.modules.common
import qs.modules.common.widgets
import qs.modules.abyss.looks
import "A2uiDataModel.js" as A2ui

// Static Component + Loader recursive A2UI v1.0 node renderer (zero Qt.createQmlObject).
Item {
    id: root
    property string componentId: ""
    property var componentMap: ({})
    property var dataModel: ({})
    property var scopeData: null
    property string scopePath: ""
    property int depth: 0
    property bool compactMode: false
    property real maxContentWidth: 280
    property color colBoxSubtle: Qt.rgba(0.12, 0.14, 0.18, 0.90)
    property color colBoxLayer: Qt.rgba(0.15, 0.17, 0.22, 0.92)
    property color colBoxElevated: Qt.rgba(0.19, 0.21, 0.27, 0.94)
    property color colBoxHover: Qt.rgba(0.23, 0.26, 0.32, 0.95)
    property color colTextPrimary: Appearance?.colors?.colOnLayer0 ?? AbyssStyle.textColor
    property color colTextMuted: Appearance?.colors?.colSubtext ?? Qt.alpha(AbyssStyle.textColor, 0.72)
    property color colAccent: AbyssStyle.accent
    property color colPrimaryBg: Appearance?.colors?.colPrimary ?? AbyssStyle.accent
    property color colOnPrimary: Appearance?.colors?.colOnPrimary ?? "#0b1220"
    property color colErrorBg: Appearance?.colors?.colError ?? "#e05a5a"
    property color colOnError: Appearance?.colors?.colOnError ?? "#ffffff"
    property color colToggledBg: Appearance?.colors?.colBackgroundToggled ?? Qt.alpha(colPrimaryBg, 0.26)

    signal serverEvent(string eventName, var context)
    signal functionCallRequested(string callName, var args)
    signal localDataChanged(string path, var value)
    signal textInputFocusChanged(bool focused)

    readonly property var spec: (componentId && componentMap && componentMap[componentId]) ? componentMap[componentId] : null
    readonly property string compType: spec ? String(spec.component ?? "") : ""
    readonly property bool nodeVisible: {
        if (!spec || depth > 6)
            return false;
        if (spec.visible === undefined)
            return true;
        const visFallback = A2ui.extractPointer(spec.visible) ? false : true;
        return Boolean(A2ui.resolveValue(spec.visible, dataModel, scopeData, visFallback));
    }

    visible: nodeVisible
    implicitWidth: nodeVisible && nodeLoader.item ? nodeLoader.item.implicitWidth : 0
    implicitHeight: nodeVisible && nodeLoader.item ? nodeLoader.item.implicitHeight : 0

    function resolveToneColor(tone): color {
        switch (String(tone ?? "default")) {
        case "muted":
            return root.colTextMuted;
        case "accent":
            return root.colAccent;
        case "success":
            return Appearance?.colors?.colSuccess ?? "#58c98a";
        case "warning":
            return Appearance?.colors?.colWarning ?? "#e8b454";
        case "error":
            return root.colErrorBg;
        default:
            return root.colTextPrimary;
        }
    }

    function dispatchAction(actionObj, extraCtx = null): void {
        if (!A2ui.isPlainObject(actionObj))
            return;
        const fc = A2ui.extractFunctionCall(actionObj);
        if (fc) {
            const resolvedArgs = A2ui.resolveContext(fc.args ?? {}, root.dataModel, root.scopeData);
            if (A2ui.isPlainObject(extraCtx))
                Object.assign(resolvedArgs, extraCtx);
            root.functionCallRequested(String(fc.call), resolvedArgs);
            return;
        }
        const ev = actionObj.event;
        if (A2ui.isPlainObject(ev) && typeof ev.name === "string" && ev.name.length > 0) {
            const resolvedCtx = A2ui.resolveContext(ev.context ?? {}, root.dataModel, root.scopeData);
            if (A2ui.isPlainObject(extraCtx))
                Object.assign(resolvedCtx, extraCtx);
            root.serverEvent(String(ev.name), resolvedCtx);
        }
    }

    Loader {
        id: nodeLoader
        width: parent.width > 0 ? parent.width : implicitWidth
        active: root.nodeVisible && root.compType.length > 0
        sourceComponent: {
            switch (root.compType) {
            case "Card":
                return cardComponent;
            case "Column":
                return columnComponent;
            case "Row":
                return rowComponent;
            case "Divider":
                return dividerComponent;
            case "Text":
                return textComponent;
            case "Icon":
                return iconComponent;
            case "Button":
                return buttonComponent;
            case "List":
                return listComponent;
            case "ChoicePicker":
                return choicePickerComponent;
            case "CheckBox":
                return checkBoxComponent;
            case "TextField":
                return textFieldComponent;
            case "Slider":
                return sliderComponent;
            case "Tabs":
                return tabsComponent;
            case "Image":
                return imageComponent;
            default:
                return null;
            }
        }
    }

    Component {
        id: cardComponent
        Rectangle {
            id: cardRect
            readonly property string tone: String(root.spec?.tone ?? "subtle")
            readonly property real padH: root.compactMode ? 9 : 11
            readonly property real padV: root.compactMode ? 7 : 9
            width: Math.max(0, root.width > 0 ? root.width : root.maxContentWidth)
            implicitWidth: Math.min(root.maxContentWidth, Math.max(80, cardChild.implicitWidth + padH * 2))
            implicitHeight: Math.max(24, cardChild.implicitHeight + padV * 2)
            height: implicitHeight
            radius: 10
            color: tone === "elevated" ? root.colBoxElevated : (tone === "layer" ? root.colBoxLayer : root.colBoxSubtle)
            border.width: 1
            border.color: {
                if (tone === "error")
                    return Qt.alpha(root.colErrorBg, 0.45);
                if (tone === "warning")
                    return Qt.alpha(Appearance?.colors?.colWarning ?? "#e8b454", 0.42);
                if (tone === "success")
                    return Qt.alpha(Appearance?.colors?.colSuccess ?? "#58c98a", 0.40);
                if (tone === "accent")
                    return Qt.alpha(root.colAccent, 0.36);
                return Qt.alpha(root.colTextPrimary, 0.10);
            }
            A2uiChildSlot {
                id: cardChild
                x: cardRect.padH
                y: cardRect.padV
                width: Math.max(40, cardRect.width - cardRect.padH * 2)
                componentId: String(root.spec?.child ?? "")
                componentMap: root.componentMap
                dataModel: root.dataModel
                scopeData: root.scopeData
                scopePath: root.scopePath
                depth: root.depth + 1
                compactMode: root.compactMode
                maxContentWidth: Math.max(40, cardRect.width - cardRect.padH * 2)
                colBoxSubtle: root.colBoxSubtle
                colBoxLayer: root.colBoxLayer
                colBoxElevated: root.colBoxElevated
                colBoxHover: root.colBoxHover
                colTextPrimary: root.colTextPrimary
                colTextMuted: root.colTextMuted
                colAccent: root.colAccent
                colPrimaryBg: root.colPrimaryBg
                colOnPrimary: root.colOnPrimary
                colErrorBg: root.colErrorBg
                colOnError: root.colOnError
                colToggledBg: root.colToggledBg
                onServerEvent: (ev, ctx) => root.serverEvent(ev, ctx)
                onFunctionCallRequested: (fn, args) => root.functionCallRequested(fn, args)
                onLocalDataChanged: (p, v) => root.localDataChanged(p, v)
                onTextInputFocusChanged: f => root.textInputFocusChanged(f)
            }
        }
    }

    Component {
        id: columnComponent
        Column {
            id: colBox
            readonly property var rawChildren: root.spec?.children
            readonly property bool isTemplate: A2ui.isPlainObject(rawChildren) && typeof rawChildren.componentId === "string"
            readonly property string templatePath: isTemplate ? A2ui.extractPointer(rawChildren) : ""
            readonly property var templateItems: {
                if (!isTemplate || !templatePath)
                    return [];
                const arr = A2ui.getByPath(root.dataModel, templatePath, root.scopeData);
                return Array.isArray(arr) ? arr : [];
            }
            readonly property var staticIds: Array.isArray(rawChildren) ? rawChildren : []
            width: Math.max(0, root.width > 0 ? root.width : root.maxContentWidth)
            spacing: Math.max(0, Number(root.spec?.gap ?? 6))
            Repeater {
                model: colBox.isTemplate ? colBox.templateItems.length : colBox.staticIds.length
                delegate: A2uiChildSlot {
                    required property int index
                    width: colBox.width
                    componentId: colBox.isTemplate ? String(colBox.rawChildren.componentId) : String(colBox.staticIds[index] ?? "")
                    componentMap: root.componentMap
                    dataModel: root.dataModel
                    scopeData: colBox.isTemplate ? colBox.templateItems[index] : root.scopeData
                    scopePath: colBox.isTemplate ? (A2ui.resolveAbsolutePath(colBox.templatePath, root.scopePath) + "/" + index) : root.scopePath
                    depth: root.depth + 1
                    compactMode: root.compactMode
                    maxContentWidth: colBox.width
                    colBoxSubtle: root.colBoxSubtle
                    colBoxLayer: root.colBoxLayer
                    colBoxElevated: root.colBoxElevated
                    colBoxHover: root.colBoxHover
                    colTextPrimary: root.colTextPrimary
                    colTextMuted: root.colTextMuted
                    colAccent: root.colAccent
                    colPrimaryBg: root.colPrimaryBg
                    colOnPrimary: root.colOnPrimary
                    colErrorBg: root.colErrorBg
                    colOnError: root.colOnError
                    colToggledBg: root.colToggledBg
                    onServerEvent: (ev, ctx) => root.serverEvent(ev, ctx)
                    onFunctionCallRequested: (fn, args) => root.functionCallRequested(fn, args)
                    onLocalDataChanged: (p, v) => root.localDataChanged(p, v)
                    onTextInputFocusChanged: f => root.textInputFocusChanged(f)
                    onVisibleChanged: Qt.callLater(colBox.forceLayout)
                    onImplicitHeightChanged: Qt.callLater(colBox.forceLayout)
                }
            }
        }
    }

    Component {
        id: rowComponent
        Item {
            id: rowWrapHost
            readonly property var rawChildren: root.spec?.children
            readonly property bool isTemplate: A2ui.isPlainObject(rawChildren) && typeof rawChildren.componentId === "string"
            readonly property string templatePath: isTemplate ? A2ui.extractPointer(rawChildren) : ""
            readonly property var templateItems: {
                if (!isTemplate || !templatePath)
                    return [];
                const arr = A2ui.getByPath(root.dataModel, templatePath, root.scopeData);
                return Array.isArray(arr) ? arr : [];
            }
            readonly property var staticIds: Array.isArray(rawChildren) ? rawChildren : []
            readonly property int childCount: isTemplate ? templateItems.length : staticIds.length
            readonly property real rowGap: Math.max(0, Number(root.spec?.gap ?? 6))
            readonly property bool hasExplicitWidth: root.width > 0
            readonly property bool useFlow: hasExplicitWidth && (root.spec?.wrap === true || isTemplate || (root.compactMode && childCount > 2))
            width: hasExplicitWidth ? root.width : implicitWidth
            implicitWidth: useFlow ? flowRow.implicitWidth : linearRow.implicitWidth
            implicitHeight: useFlow ? flowRow.implicitHeight : linearRow.implicitHeight
            height: implicitHeight

            Flow {
                id: flowRow
                visible: rowWrapHost.useFlow
                width: rowWrapHost.width
                spacing: rowWrapHost.rowGap
                Repeater {
                    model: rowWrapHost.useFlow ? rowWrapHost.childCount : 0
                    delegate: A2uiChildSlot {
                        required property int index
                        componentId: rowWrapHost.isTemplate ? String(rowWrapHost.rawChildren.componentId) : String(rowWrapHost.staticIds[index] ?? "")
                        componentMap: root.componentMap
                        dataModel: root.dataModel
                        scopeData: rowWrapHost.isTemplate ? rowWrapHost.templateItems[index] : root.scopeData
                        scopePath: rowWrapHost.isTemplate ? (A2ui.resolveAbsolutePath(rowWrapHost.templatePath, root.scopePath) + "/" + index) : root.scopePath
                        depth: root.depth + 1
                        compactMode: root.compactMode
                        maxContentWidth: rowWrapHost.width
                        colBoxSubtle: root.colBoxSubtle
                        colBoxLayer: root.colBoxLayer
                        colBoxElevated: root.colBoxElevated
                        colBoxHover: root.colBoxHover
                        colTextPrimary: root.colTextPrimary
                        colTextMuted: root.colTextMuted
                        colAccent: root.colAccent
                        colPrimaryBg: root.colPrimaryBg
                        colOnPrimary: root.colOnPrimary
                        colErrorBg: root.colErrorBg
                        colOnError: root.colOnError
                        colToggledBg: root.colToggledBg
                        onServerEvent: (ev, ctx) => root.serverEvent(ev, ctx)
                        onFunctionCallRequested: (fn, args) => root.functionCallRequested(fn, args)
                        onLocalDataChanged: (p, v) => root.localDataChanged(p, v)
                        onTextInputFocusChanged: f => root.textInputFocusChanged(f)
                    }
                }
            }

            RowLayout {
                id: linearRow
                visible: !rowWrapHost.useFlow
                width: rowWrapHost.hasExplicitWidth ? rowWrapHost.width : implicitWidth
                spacing: rowWrapHost.rowGap
                Repeater {
                    model: !rowWrapHost.useFlow ? rowWrapHost.childCount : 0
                    delegate: A2uiChildSlot {
                        required property int index
                        readonly property string resolvedCid: rowWrapHost.isTemplate ? String(rowWrapHost.rawChildren.componentId) : String(rowWrapHost.staticIds[index] ?? "")
                        readonly property var childSpec: root.componentMap[resolvedCid]
                        readonly property bool isSpaceBetween: String(root.spec?.justify ?? "") === "spaceBetween"
                        readonly property bool fillItem: Number(childSpec?.weight ?? 0) > 0
                            || (rowWrapHost.hasExplicitWidth && childSpec?.component === "Text" && (!isSpaceBetween || index === rowWrapHost.childCount - 1))
                        Layout.fillWidth: fillItem
                        Layout.minimumWidth: 0
                        Layout.maximumWidth: rowWrapHost.hasExplicitWidth ? rowWrapHost.width : Number.POSITIVE_INFINITY
                        Layout.alignment: Qt.AlignVCenter
                        componentId: resolvedCid
                        componentMap: root.componentMap
                        dataModel: root.dataModel
                        scopeData: rowWrapHost.isTemplate ? rowWrapHost.templateItems[index] : root.scopeData
                        scopePath: rowWrapHost.isTemplate ? (A2ui.resolveAbsolutePath(rowWrapHost.templatePath, root.scopePath) + "/" + index) : root.scopePath
                        depth: root.depth + 1
                        compactMode: root.compactMode
                        maxContentWidth: rowWrapHost.hasExplicitWidth ? Math.max(48, rowWrapHost.width - (rowWrapHost.childCount > 1 ? 24 : 0)) : root.maxContentWidth
                        colBoxSubtle: root.colBoxSubtle
                        colBoxLayer: root.colBoxLayer
                        colBoxElevated: root.colBoxElevated
                        colBoxHover: root.colBoxHover
                        colTextPrimary: root.colTextPrimary
                        colTextMuted: root.colTextMuted
                        colAccent: root.colAccent
                        colPrimaryBg: root.colPrimaryBg
                        colOnPrimary: root.colOnPrimary
                        colErrorBg: root.colErrorBg
                        colOnError: root.colOnError
                        colToggledBg: root.colToggledBg
                        onServerEvent: (ev, ctx) => root.serverEvent(ev, ctx)
                        onFunctionCallRequested: (fn, args) => root.functionCallRequested(fn, args)
                        onLocalDataChanged: (p, v) => root.localDataChanged(p, v)
                        onTextInputFocusChanged: f => root.textInputFocusChanged(f)
                    }
                }
            }
        }
    }

    Component {
        id: dividerComponent
        Rectangle {
            readonly property bool isVertical: String(root.spec?.axis ?? "horizontal") === "vertical"
            width: isVertical ? 1 : Math.max(0, root.width > 0 ? root.width : root.maxContentWidth)
            height: isVertical ? 16 : 1
            implicitWidth: width
            implicitHeight: height
            color: Qt.alpha(root.colTextPrimary, 0.12)
        }
    }

    Component {
        id: textComponent
        Text {
            id: txtItem
            readonly property string resolvedText: String(A2ui.resolveValue(root.spec?.text, root.dataModel, root.scopeData, ""))
            readonly property bool isCaption: String(root.spec?.variant ?? "body") === "caption"
            readonly property bool hasMarkdown: /(?:^|\n)#{1,3}\s|\*\*|`[^`]+`|\*[^*]+\*/.test(resolvedText)
            width: root.width > 0 ? Math.min(root.width, root.maxContentWidth) : Math.min(implicitWidth, root.maxContentWidth)
            text: hasMarkdown ? A2ui.formatMarkdownText(resolvedText, String(root.colAccent), "rgba(255,255,255,0.08)") : resolvedText
            textFormat: hasMarkdown ? Text.RichText : Text.PlainText
            wrapMode: Text.WordWrap
            maximumLineCount: Number(root.spec?.maxLines ?? 0) > 0 ? Number(root.spec.maxLines) : 100
            elide: Number(root.spec?.maxLines ?? 0) > 0 ? Text.ElideRight : Text.ElideNone
            color: root.resolveToneColor(root.spec?.tone ?? (isCaption ? "muted" : "default"))
            horizontalAlignment: {
                const a = String(root.spec?.align ?? "left");
                return a === "center" ? Text.AlignHCenter : (a === "right" ? Text.AlignRight : Text.AlignLeft);
            }
            font.family: Appearance?.font?.family?.main ?? "Roboto Flex"
            font.pixelSize: isCaption ? (Appearance?.font?.pixelSize?.smallest ?? 11) : (Appearance?.font?.pixelSize?.small ?? 13)
            renderType: Text.QtRendering
        }
    }

    Component {
        id: iconComponent
        Item {
            readonly property string iconName: A2ui.safeIconName(A2ui.resolveValue(root.spec?.name, root.dataModel, root.scopeData, "info"))
            readonly property real sz: Math.max(10, Math.min(32, Number(root.spec?.size ?? 16)))
            implicitWidth: sz
            implicitHeight: sz
            width: sz
            height: sz
            MaterialSymbol {
                anchors.centerIn: parent
                text: parent.iconName
                iconSize: parent.sz
                textRenderType: Text.CurveRendering
                color: root.resolveToneColor(root.spec?.tone ?? "default")
            }
        }
    }

    Component {
        id: buttonComponent
        RippleButton {
            id: a2uiBtn
            readonly property string variant: String(root.spec?.variant ?? "secondary")
            readonly property bool isDisabled: Boolean(A2ui.resolveValue(root.spec?.disabled, root.dataModel, root.scopeData, false))
                || (root.spec?.action?.event?.name === "confirm_action" && Boolean(root.dataModel?.resolved))
            readonly property string childId: String(root.spec?.child ?? "")
            readonly property string fallbackLabel: String(A2ui.resolveValue(root.spec?.label, root.dataModel, root.scopeData, ""))
            readonly property string fallbackIcon: {
                const rawIco = A2ui.resolveValue(root.spec?.icon, root.dataModel, root.scopeData, "");
                return rawIco ? A2ui.safeIconName(rawIco) : "";
            }
            readonly property color fgColor: variant === "primary" ? root.colOnPrimary : (variant === "danger" ? root.colOnError : root.colTextPrimary)
            focusPolicy: Qt.NoFocus
            enabled: !isDisabled
            opacity: enabled ? 1.0 : 0.48
            buttonRadius: 8
            implicitWidth: Math.min(root.maxContentWidth, Math.max(44, btnCenterBox.implicitWidth + 18))
            implicitHeight: Math.max(26, btnCenterBox.implicitHeight + 8)
            width: implicitWidth
            height: implicitHeight
            colBackground: {
                if (variant === "primary")
                    return root.colPrimaryBg;
                if (variant === "danger")
                    return root.colErrorBg;
                if (variant === "ghost")
                    return "transparent";
                return root.colBoxElevated;
            }
            colBackgroundHover: {
                if (variant === "primary")
                    return Qt.lighter(root.colPrimaryBg, 1.08);
                if (variant === "danger")
                    return Qt.lighter(root.colErrorBg, 1.08);
                return root.colBoxHover;
            }
            onClicked: {
                if (enabled)
                    root.dispatchAction(root.spec?.action);
            }
            Rectangle {
                anchors.fill: parent
                radius: a2uiBtn.buttonRadius
                color: "transparent"
                border.width: a2uiBtn.variant === "ghost" || a2uiBtn.variant === "secondary" ? 1 : 0
                border.color: Qt.alpha(root.colTextPrimary, 0.14)
            }
            contentItem: Item {
                Item {
                    id: btnCenterBox
                    anchors.centerIn: parent
                    implicitWidth: a2uiBtn.childId.length > 0 ? btnChildNode.implicitWidth : fallbackRow.implicitWidth
                    implicitHeight: a2uiBtn.childId.length > 0 ? btnChildNode.implicitHeight : fallbackRow.implicitHeight
                    width: implicitWidth
                    height: implicitHeight
                    A2uiChildSlot {
                        id: btnChildNode
                        visible: a2uiBtn.childId.length > 0
                        anchors.centerIn: parent
                        componentId: a2uiBtn.childId
                        componentMap: root.componentMap
                        dataModel: root.dataModel
                        scopeData: root.scopeData
                        scopePath: root.scopePath
                        depth: root.depth + 1
                        compactMode: root.compactMode
                        maxContentWidth: Math.max(40, root.maxContentWidth - 20)
                        colBoxSubtle: root.colBoxSubtle
                        colBoxLayer: root.colBoxLayer
                        colBoxElevated: root.colBoxElevated
                        colBoxHover: root.colBoxHover
                        colTextPrimary: a2uiBtn.fgColor
                        colTextMuted: a2uiBtn.fgColor
                        colAccent: a2uiBtn.fgColor
                        colPrimaryBg: root.colPrimaryBg
                        colOnPrimary: root.colOnPrimary
                        colErrorBg: root.colErrorBg
                        colOnError: root.colOnError
                        colToggledBg: root.colToggledBg
                    }
                    Row {
                        id: fallbackRow
                        visible: a2uiBtn.childId.length === 0
                        anchors.centerIn: parent
                        spacing: 4
                        MaterialSymbol {
                            visible: a2uiBtn.fallbackIcon.length > 0
                            anchors.verticalCenter: parent.verticalCenter
                            text: a2uiBtn.fallbackIcon
                            iconSize: 13
                            textRenderType: Text.CurveRendering
                            color: a2uiBtn.fgColor
                        }
                        StyledText {
                            anchors.verticalCenter: parent.verticalCenter
                            horizontalAlignment: Text.AlignHCenter
                            text: a2uiBtn.fallbackLabel
                            font.pixelSize: Appearance?.font?.pixelSize?.smallest ?? 11
                            font.weight: Font.DemiBold
                            color: a2uiBtn.fgColor
                        }
                    }
                }
            }
        }
    }

    Component {
        id: listComponent
        Item {
            id: listHost
            readonly property var rawChildren: root.spec?.children
            readonly property bool isTemplate: A2ui.isPlainObject(rawChildren) && typeof rawChildren.componentId === "string"
            readonly property string templatePath: isTemplate ? A2ui.extractPointer(rawChildren) : ""
            readonly property bool isHorizontal: String(root.spec?.direction ?? "vertical") === "horizontal"
            readonly property var templateItems: {
                if (!isTemplate || !templatePath)
                    return [];
                const arr = A2ui.getByPath(root.dataModel, templatePath, root.scopeData);
                return Array.isArray(arr) ? arr : [];
            }
            readonly property var staticIds: Array.isArray(rawChildren) ? rawChildren : []
            readonly property int itemCount: isTemplate ? templateItems.length : staticIds.length
            readonly property real listGap: Math.max(0, Number(root.spec?.gap ?? 5))
            width: Math.max(0, root.width > 0 ? root.width : root.maxContentWidth)
            implicitWidth: width
            implicitHeight: isHorizontal ? horizList.implicitHeight : vertList.implicitHeight
            height: implicitHeight

            Column {
                id: vertList
                visible: !listHost.isHorizontal
                width: listHost.width
                spacing: listHost.listGap
                Repeater {
                    model: !listHost.isHorizontal ? listHost.itemCount : 0
                    delegate: A2uiChildSlot {
                        required property int index
                        width: vertList.width
                        componentId: listHost.isTemplate ? String(listHost.rawChildren.componentId) : String(listHost.staticIds[index] ?? "")
                        componentMap: root.componentMap
                        dataModel: root.dataModel
                        scopeData: listHost.isTemplate ? listHost.templateItems[index] : root.scopeData
                        scopePath: listHost.isTemplate ? (A2ui.resolveAbsolutePath(listHost.templatePath, root.scopePath) + "/" + index) : root.scopePath
                        depth: root.depth + 1
                        compactMode: root.compactMode
                        maxContentWidth: vertList.width
                        colBoxSubtle: root.colBoxSubtle
                        colBoxLayer: root.colBoxLayer
                        colBoxElevated: root.colBoxElevated
                        colBoxHover: root.colBoxHover
                        colTextPrimary: root.colTextPrimary
                        colTextMuted: root.colTextMuted
                        colAccent: root.colAccent
                        colPrimaryBg: root.colPrimaryBg
                        colOnPrimary: root.colOnPrimary
                        colErrorBg: root.colErrorBg
                        colOnError: root.colOnError
                        colToggledBg: root.colToggledBg
                        onServerEvent: (ev, ctx) => root.serverEvent(ev, ctx)
                        onFunctionCallRequested: (fn, args) => root.functionCallRequested(fn, args)
                        onLocalDataChanged: (p, v) => root.localDataChanged(p, v)
                        onTextInputFocusChanged: f => root.textInputFocusChanged(f)
                    }
                }
            }

            Flow {
                id: horizList
                visible: listHost.isHorizontal
                width: listHost.width
                spacing: listHost.listGap
                Repeater {
                    model: listHost.isHorizontal ? listHost.itemCount : 0
                    delegate: A2uiChildSlot {
                        required property int index
                        componentId: listHost.isTemplate ? String(listHost.rawChildren.componentId) : String(listHost.staticIds[index] ?? "")
                        componentMap: root.componentMap
                        dataModel: root.dataModel
                        scopeData: listHost.isTemplate ? listHost.templateItems[index] : root.scopeData
                        scopePath: listHost.isTemplate ? (A2ui.resolveAbsolutePath(listHost.templatePath, root.scopePath) + "/" + index) : root.scopePath
                        depth: root.depth + 1
                        compactMode: root.compactMode
                        maxContentWidth: listHost.width
                        colBoxSubtle: root.colBoxSubtle
                        colBoxLayer: root.colBoxLayer
                        colBoxElevated: root.colBoxElevated
                        colBoxHover: root.colBoxHover
                        colTextPrimary: root.colTextPrimary
                        colTextMuted: root.colTextMuted
                        colAccent: root.colAccent
                        colPrimaryBg: root.colPrimaryBg
                        colOnPrimary: root.colOnPrimary
                        colErrorBg: root.colErrorBg
                        colOnError: root.colOnError
                        colToggledBg: root.colToggledBg
                        onServerEvent: (ev, ctx) => root.serverEvent(ev, ctx)
                        onFunctionCallRequested: (fn, args) => root.functionCallRequested(fn, args)
                        onLocalDataChanged: (p, v) => root.localDataChanged(p, v)
                        onTextInputFocusChanged: f => root.textInputFocusChanged(f)
                    }
                }
            }
        }
    }

    Component {
        id: choicePickerComponent
        Column {
            id: choiceCol
            readonly property string boundPath: A2ui.resolveAbsolutePath(A2ui.extractPointer(root.spec?.value), root.scopePath)
            readonly property var currentVal: A2ui.resolveValue(root.spec?.value, root.dataModel, root.scopeData, "")
            readonly property bool multiSelect: Boolean(root.spec?.multiSelect)
            readonly property var options: Array.isArray(root.spec?.options) ? root.spec.options : []
            width: Math.max(0, root.width > 0 ? root.width : root.maxContentWidth)
            spacing: 5

            function isSelected(optVal): bool {
                if (multiSelect && Array.isArray(currentVal))
                    return currentVal.indexOf(optVal) !== -1;
                return String(currentVal ?? "") === String(optVal ?? "");
            }

            readonly property string choiceLabel: String(A2ui.resolveValue(root.spec?.label, root.dataModel, root.scopeData, ""))
            StyledText {
                visible: choiceCol.choiceLabel.length > 0
                width: choiceCol.width
                text: choiceCol.choiceLabel
                font.pixelSize: Appearance?.font?.pixelSize?.smallest ?? 11
                color: root.colTextMuted
                wrapMode: Text.WordWrap
            }

            Flow {
                width: choiceCol.width
                spacing: 5
                Repeater {
                    model: choiceCol.options
                    delegate: RippleButton {
                        id: chipBtn
                        required property var modelData
                        readonly property string optValue: String(A2ui.resolveValue(modelData?.value !== undefined ? modelData.value : modelData, root.dataModel, root.scopeData, ""))
                        readonly property string optLabel: String(A2ui.resolveValue(modelData?.label !== undefined ? modelData.label : optValue, root.dataModel, root.scopeData, optValue))
                        readonly property bool active: choiceCol.isSelected(optValue)
                        focusPolicy: Qt.NoFocus
                        buttonRadius: 12
                        implicitWidth: Math.min(choiceCol.width, Math.max(44, chipLbl.implicitWidth + 18))
                        implicitHeight: 24
                        width: implicitWidth
                        height: 24
                        toggled: active
                        colBackground: root.colBoxElevated
                        colBackgroundHover: root.colBoxHover
                        colBackgroundToggled: root.colPrimaryBg
                        colBackgroundToggledHover: Qt.lighter(root.colPrimaryBg, 1.08)
                        onClicked: {
                            let nextVal = optValue;
                            if (choiceCol.multiSelect) {
                                const arr = Array.isArray(choiceCol.currentVal) ? choiceCol.currentVal.slice() : [];
                                const idx = arr.indexOf(optValue);
                                if (idx >= 0)
                                    arr.splice(idx, 1);
                                else
                                    arr.push(optValue);
                                nextVal = arr;
                            }
                            if (choiceCol.boundPath)
                                root.localDataChanged(choiceCol.boundPath, nextVal);
                            if (root.spec?.action) {
                                const extra = { value: nextVal };
                                if (choiceCol.boundPath)
                                    extra.path = choiceCol.boundPath;
                                root.dispatchAction(root.spec.action, extra);
                            } else if (choiceCol.boundPath) {
                                root.serverEvent("choice_submit", { path: choiceCol.boundPath, value: nextVal });
                            }
                        }
                        contentItem: Item {
                            StyledText {
                                id: chipLbl
                                anchors.centerIn: parent
                                horizontalAlignment: Text.AlignHCenter
                                text: chipBtn.optLabel
                                font.pixelSize: Appearance?.font?.pixelSize?.smallest ?? 11
                                font.weight: chipBtn.active ? Font.DemiBold : Font.Normal
                                color: chipBtn.active ? root.colOnPrimary : root.colTextPrimary
                            }
                        }
                    }
                }
            }
        }
    }

    Component {
        id: checkBoxComponent
        RippleButton {
            id: chkBtn
            readonly property string boundPath: A2ui.resolveAbsolutePath(A2ui.extractPointer(root.spec?.value), root.scopePath)
            readonly property bool isChecked: Boolean(A2ui.resolveValue(root.spec?.value, root.dataModel, root.scopeData, false))
            readonly property string lblText: String(A2ui.resolveValue(root.spec?.label, root.dataModel, root.scopeData, ""))
            focusPolicy: Qt.NoFocus
            width: Math.max(0, root.width > 0 ? root.width : root.maxContentWidth)
            implicitWidth: width
            implicitHeight: Math.max(24, chkLbl.implicitHeight + 6)
            height: implicitHeight
            buttonRadius: 7
            colBackground: "transparent"
            colBackgroundHover: Qt.alpha(root.colTextPrimary, 0.06)
            onClicked: {
                const next = !isChecked;
                if (boundPath)
                    root.localDataChanged(boundPath, next);
                if (root.spec?.action) {
                    const extra = { done: next, value: next };
                    if (boundPath)
                        extra.path = boundPath;
                    root.dispatchAction(root.spec.action, extra);
                } else if (boundPath) {
                    root.serverEvent("checklist_toggle", { path: boundPath, value: next });
                }
            }
            contentItem: RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 4
                anchors.rightMargin: 4
                spacing: 7
                Rectangle {
                    Layout.alignment: Qt.AlignVCenter
                    implicitWidth: 16
                    implicitHeight: 16
                    radius: 4
                    color: chkBtn.isChecked ? root.colPrimaryBg : root.colBoxElevated
                    border.width: 1
                    border.color: chkBtn.isChecked ? root.colPrimaryBg : Qt.alpha(root.colTextPrimary, 0.25)
                    MaterialSymbol {
                        anchors.centerIn: parent
                        visible: chkBtn.isChecked
                        text: "check"
                        iconSize: 12
                        textRenderType: Text.CurveRendering
                        color: root.colOnPrimary
                    }
                }
                StyledText {
                    id: chkLbl
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.alignment: Qt.AlignVCenter
                    text: chkBtn.lblText
                    wrapMode: Text.WordWrap
                    font.pixelSize: Appearance?.font?.pixelSize?.small ?? 12
                    font.strikeout: chkBtn.isChecked
                    color: chkBtn.isChecked ? root.colTextMuted : root.colTextPrimary
                }
            }
        }
    }

    Component {
        id: textFieldComponent
        Column {
            id: tfCol
            readonly property string boundPath: A2ui.resolveAbsolutePath(A2ui.extractPointer(root.spec?.value), root.scopePath)
            readonly property string currentStr: String(A2ui.resolveValue(root.spec?.value, root.dataModel, root.scopeData, ""))
            readonly property string tfLabel: String(A2ui.resolveValue(root.spec?.label, root.dataModel, root.scopeData, ""))
            readonly property string tfPlaceholder: String(A2ui.resolveValue(root.spec?.placeholder, root.dataModel, root.scopeData, ""))
            width: Math.max(0, root.width > 0 ? root.width : root.maxContentWidth)
            spacing: 4
            StyledText {
                visible: tfCol.tfLabel.length > 0
                width: tfCol.width
                text: tfCol.tfLabel
                font.pixelSize: Appearance?.font?.pixelSize?.smallest ?? 11
                color: root.colTextMuted
            }
            Rectangle {
                width: tfCol.width
                height: 28
                radius: 8
                color: root.colBoxElevated
                border.width: 1
                border.color: tfInput.activeFocus ? root.colAccent : Qt.alpha(root.colTextPrimary, 0.14)
                TextInput {
                    id: tfInput
                    anchors.fill: parent
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    verticalAlignment: TextInput.AlignVCenter
                    clip: true
                    selectByMouse: true
                    echoMode: TextInput.Normal
                    maximumLength: Number(root.spec?.maxLength ?? 200)
                    color: root.colTextPrimary
                    selectionColor: Qt.alpha(root.colAccent, 0.38)
                    font.family: Appearance?.font?.family?.main ?? "Roboto Flex"
                    font.pixelSize: Appearance?.font?.pixelSize?.small ?? 12
                    Component.onCompleted: text = tfCol.currentStr
                    Binding {
                        target: tfInput
                        property: "text"
                        value: tfCol.currentStr
                        when: !tfInput.activeFocus
                    }
                    onTextEdited: {
                        if (tfCol.boundPath)
                            root.localDataChanged(tfCol.boundPath, text);
                    }
                    onAccepted: {
                        if (tfCol.boundPath)
                            root.localDataChanged(tfCol.boundPath, text);
                        if (root.spec?.action) {
                            const extra = { value: text };
                            if (tfCol.boundPath)
                                extra.path = tfCol.boundPath;
                            root.dispatchAction(root.spec.action, extra);
                        } else if (tfCol.boundPath) {
                            root.serverEvent("form_submit", { path: tfCol.boundPath, value: text });
                        }
                    }
                    onActiveFocusChanged: root.textInputFocusChanged(activeFocus)
                    Component.onDestruction: {
                        if (activeFocus)
                            root.textInputFocusChanged(false);
                    }
                    Keys.onEscapePressed: event => {
                        tfInput.focus = false;
                        event.accepted = true;
                    }
                }
                StyledText {
                    visible: tfInput.text.length === 0 && !tfInput.preeditText && tfCol.tfPlaceholder.length > 0
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    elide: Text.ElideRight
                    text: tfCol.tfPlaceholder
                    font.pixelSize: Appearance?.font?.pixelSize?.small ?? 12
                    color: root.colTextMuted
                }
            }
        }
    }

    Component {
        id: sliderComponent
        Column {
            id: slCol
            readonly property string boundPath: A2ui.resolveAbsolutePath(A2ui.extractPointer(root.spec?.value), root.scopePath)
            readonly property string slLabel: String(A2ui.resolveValue(root.spec?.label, root.dataModel, root.scopeData, ""))
            readonly property real minVal: Number(root.spec?.min ?? 0)
            readonly property real maxVal: Number(root.spec?.max ?? 100)
            readonly property real stepVal: Math.max(0.01, Number(root.spec?.step ?? 1))
            readonly property real curVal: Number(A2ui.resolveValue(root.spec?.value, root.dataModel, root.scopeData, minVal))
            width: Math.max(0, root.width > 0 ? root.width : root.maxContentWidth)
            spacing: 2
            RowLayout {
                width: slCol.width
                StyledText {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: slCol.slLabel
                    font.pixelSize: Appearance?.font?.pixelSize?.smallest ?? 11
                    color: root.colTextMuted
                }
                StyledText {
                    text: String(Math.round(slControl.value * 100) / 100)
                    font.pixelSize: Appearance?.font?.pixelSize?.smallest ?? 11
                    font.weight: Font.DemiBold
                    color: root.colTextPrimary
                }
            }
            Slider {
                id: slControl
                width: slCol.width
                implicitHeight: 22
                focusPolicy: Qt.NoFocus
                from: slCol.minVal
                to: slCol.maxVal
                stepSize: slCol.stepVal
                background: Rectangle {
                    x: slControl.leftPadding
                    y: slControl.topPadding + slControl.availableHeight / 2 - height / 2
                    implicitWidth: 160
                    implicitHeight: 5
                    width: slControl.availableWidth
                    height: 5
                    radius: 3
                    color: root.colBoxElevated
                    Rectangle {
                        width: Math.max(0, Math.min(parent.width, slControl.visualPosition * parent.width))
                        height: parent.height
                        radius: 3
                        color: root.colAccent
                    }
                }
                handle: Rectangle {
                    x: slControl.leftPadding + slControl.visualPosition * (slControl.availableWidth - width)
                    y: slControl.topPadding + slControl.availableHeight / 2 - height / 2
                    implicitWidth: 14
                    implicitHeight: 14
                    width: 14
                    height: 14
                    radius: 7
                    color: slControl.pressed ? Qt.lighter(root.colAccent, 1.15) : root.colAccent
                    border.width: 1.5
                    border.color: root.colBoxSubtle
                }
                Component.onCompleted: value = slCol.curVal
                Binding {
                    target: slControl
                    property: "value"
                    value: slCol.curVal
                    when: !slControl.pressed
                }
                onMoved: {
                    if (slCol.boundPath)
                        root.localDataChanged(slCol.boundPath, value);
                }
                onPressedChanged: {
                    if (!pressed) {
                        if (root.spec?.action) {
                            const extra = { value: value };
                            if (slCol.boundPath)
                                extra.path = slCol.boundPath;
                            root.dispatchAction(root.spec.action, extra);
                        } else if (slCol.boundPath) {
                            root.serverEvent("slider_change", { path: slCol.boundPath, value: value });
                        }
                    }
                }
            }
        }
    }

    Component {
        id: tabsComponent
        Column {
            id: tabsCol
            property int activeTab: 0
            readonly property var tabList: Array.isArray(root.spec?.tabs) ? root.spec.tabs : []
            readonly property int safeActiveTab: tabList.length > 0 ? Math.max(0, Math.min(activeTab, tabList.length - 1)) : 0
            width: Math.max(0, root.width > 0 ? root.width : root.maxContentWidth)
            spacing: 6
            Flow {
                width: tabsCol.width
                spacing: 4
                Repeater {
                    model: tabsCol.tabList
                    delegate: RippleButton {
                        id: tabBtn
                        required property var modelData
                        required property int index
                        readonly property bool selected: tabsCol.safeActiveTab === index
                        focusPolicy: Qt.NoFocus
                        buttonRadius: 8
                        implicitWidth: Math.max(42, tabLbl.implicitWidth + 14)
                        implicitHeight: 22
                        width: implicitWidth
                        height: 22
                        toggled: selected
                        colBackground: root.colBoxElevated
                        colBackgroundHover: root.colBoxHover
                        colBackgroundToggled: root.colPrimaryBg
                        colBackgroundToggledHover: Qt.lighter(root.colPrimaryBg, 1.08)
                        onClicked: tabsCol.activeTab = index
                        contentItem: Item {
                            StyledText {
                                id: tabLbl
                                anchors.centerIn: parent
                                horizontalAlignment: Text.AlignHCenter
                                text: String(A2ui.resolveValue(tabBtn.modelData?.label, root.dataModel, root.scopeData, ""))
                                font.pixelSize: Appearance?.font?.pixelSize?.smallest ?? 11
                                font.weight: tabBtn.selected ? Font.DemiBold : Font.Normal
                                color: tabBtn.selected ? root.colOnPrimary : root.colTextPrimary
                            }
                        }
                    }
                }
            }
            A2uiChildSlot {
                width: tabsCol.width
                componentId: (tabsCol.tabList.length > 0)
                    ? String(tabsCol.tabList[tabsCol.safeActiveTab]?.child ?? "")
                    : ""
                componentMap: root.componentMap
                dataModel: root.dataModel
                scopeData: root.scopeData
                scopePath: root.scopePath
                depth: root.depth + 1
                compactMode: root.compactMode
                maxContentWidth: tabsCol.width
                colBoxSubtle: root.colBoxSubtle
                colBoxLayer: root.colBoxLayer
                colBoxElevated: root.colBoxElevated
                colBoxHover: root.colBoxHover
                colTextPrimary: root.colTextPrimary
                colTextMuted: root.colTextMuted
                colAccent: root.colAccent
                colPrimaryBg: root.colPrimaryBg
                colOnPrimary: root.colOnPrimary
                colErrorBg: root.colErrorBg
                colOnError: root.colOnError
                colToggledBg: root.colToggledBg
                onServerEvent: (ev, ctx) => root.serverEvent(ev, ctx)
                onFunctionCallRequested: (fn, args) => root.functionCallRequested(fn, args)
                onLocalDataChanged: (p, v) => root.localDataChanged(p, v)
                onTextInputFocusChanged: f => root.textInputFocusChanged(f)
            }
        }
    }

    Component {
        id: imageComponent
        Rectangle {
            id: imgCard
            readonly property string rawSrc: String(A2ui.resolveValue(root.spec?.url !== undefined ? root.spec.url : root.spec?.source, root.dataModel, root.scopeData, ""))
            readonly property string safeLocalUrl: A2ui.toLocalImageUrl(rawSrc)
            readonly property real capH: Math.max(48, Math.min(220, Number(root.spec?.maxHeight ?? 140)))
            visible: safeLocalUrl.length > 0
            width: Math.min(root.width > 0 ? root.width : root.maxContentWidth, root.maxContentWidth)
            implicitWidth: width
            implicitHeight: visible ? capH : 0
            height: implicitHeight
            radius: 8
            clip: true
            color: root.colBoxElevated
            border.width: 1
            border.color: Qt.alpha(root.colTextPrimary, 0.12)
            Image {
                anchors.fill: parent
                anchors.margins: 1
                source: imgCard.safeLocalUrl
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                sourceSize: Qt.size(Math.max(1, Math.round(imgCard.width * 2)), Math.max(1, Math.round(imgCard.capH * 2)))
            }
        }
    }
}
