import QtCore
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window

ApplicationWindow {
    id: root
    // A compact panel; app.py docks it in the bottom-right corner above the taskbar.
    width: 400
    height: 600
    minimumWidth: 320
    minimumHeight: 420
    visible: true
    title: "Ano"
    property bool trayAvailable: false
    property bool shortcutAvailable: false
    property bool positionTracking: false
    property bool uiReady: false
    Component.onCompleted: {
        positionTracking = true
        // Do not create/show the secondary native window while engine.load is still constructing this one.
        // This also runs after app.py has docked the panel, so the remembered size wins over its default.
        Qt.callLater(function() {
            root.uiReady = true
            if (panelPrefs.width >= root.minimumWidth && panelPrefs.height >= root.minimumHeight)
                root.resizeAnchored(panelPrefs.width, panelPrefs.height)
            root.sizeTracking = true
        })
    }

    // Panel size: presets, free edge resizing, remembered between runs.
    readonly property var sizePresets: ({ compact: [320, 440], standard: [360, 540], large: [420, 680] })
    readonly property string sizePreset: {
        for (var name in sizePresets)
            if (Math.abs(sizePresets[name][0] - width) < 2 && Math.abs(sizePresets[name][1] - height) < 2) return name
        return "custom"
    }
    property bool sizeTracking: false
    readonly property bool narrow: width < 380  // compact header: tighter spacing, smaller face and buttons
    readonly property int headerButton: narrow ? 32 : 38
    Settings {
        id: panelPrefs
        // An explicit file in Ano's data folder (%LOCALAPPDATA%\Ano\Ano\ui.ini); no registry identifiers needed.
        location: StandardPaths.writableLocation(StandardPaths.AppLocalDataLocation) + "/ui.ini"
        category: "panel"
        property int width: 0
        property int height: 0
    }
    // Grow or shrink while keeping the bottom-right corner (where Ano docks) in place, within the screen.
    function resizeAnchored(w, h) {
        var available = Screen.desktopAvailableHeight > 0 ? Screen.desktopAvailableHeight - 24 : h
        w = Math.max(minimumWidth, Math.round(w))
        h = Math.max(minimumHeight, Math.min(Math.round(h), available))
        var right = x + width, bottom = y + height
        width = w
        height = h
        x = right - w
        y = Math.max(Screen.virtualY || 0, bottom - h)
    }
    function applySize(name) {
        var preset = sizePresets[name]
        if (preset) resizeAnchored(preset[0], preset[1])
    }
    onWidthChanged: { if (sizeTracking && visibility === Window.Windowed) saveSize.restart() }
    onHeightChanged: { if (sizeTracking && visibility === Window.Windowed) saveSize.restart() }
    Timer {
        id: saveSize
        interval: 400
        onTriggered: { panelPrefs.width = root.width; panelPrefs.height = root.height }
    }
    onXChanged: { if (positionTracking && visibility === Window.Windowed) savePanelPosition.restart() }
    onYChanged: { if (positionTracking && visibility === Window.Windowed) savePanelPosition.restart() }
    Timer {
        id: savePanelPosition
        interval: 350
        onTriggered: {
            if (root.visible && root.visibility === Window.Windowed) ano.savePanelPosition(root.x, root.y)
        }
    }
    flags: Qt.Window | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
           | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint

    // Follows Windows light/dark mode (writable so the UI test can render dark). The accent comes from the chosen colour.
    property bool dark: Application.styleHints.colorScheme === Qt.ColorScheme.Dark
    readonly property color bg: dark ? "#151c27" : "#ffffff"
    readonly property color surface: dark ? "#1e2836" : "#f2f5fa"
    readonly property color line: dark ? "#2c323b" : "#e0e4ea"
    readonly property color ink: dark ? "#edf0f5" : "#1b2028"
    readonly property color muted: dark ? "#b4bdcb" : "#4e5968"  // WCAG AA contrast on both panel backgrounds
    readonly property color accent: ano.theme === "nebula" ? (dark ? "#b9a4ff" : "#6d4ae0")
                                   : ano.theme === "solar" ? (dark ? "#f2c27f" : "#b06a00")
                                   : (dark ? "#93b9fa" : "#315fbb")
    readonly property color danger: "#e5484d"
    readonly property color warn: dark ? "#f0b45a" : "#9a5b00"
    readonly property color good: "#3fbf6f"
    readonly property string mood: ano.paused || activity.locked ? "sleep"
                                    : voice.recording ? "listen" : speech.speaking ? "talk"
                                    : ano.busy || voice.busy ? "think"
                                    : ano.needsClarification ? "confused" : "idle"

    color: "transparent"
    background: Item {}
    font.family: "Segoe UI Variable Text, Segoe UI"
    font.pixelSize: 14
    palette.window: bg
    palette.windowText: ink
    palette.base: surface
    palette.alternateBase: surface
    palette.text: ink
    palette.button: bg
    palette.buttonText: ink
    palette.mid: line
    palette.midlight: line
    palette.highlight: accent
    palette.highlightedText: dark ? "#111316" : "#ffffff"
    palette.placeholderText: muted
    palette.toolTipBase: surface
    palette.toolTipText: ink

    onClosing: function(close) {
        desktopControl.stop()
        reviews.cancel()
        controlPanels.closeVault()
        voice.cancel()
        ano.stopVoiceConversation()
        screenCapture.cancel()
        if (trayAvailable) { close.accepted = false; root.hide() }
    }
    onVisibleChanged: { if (!visible) { voice.cancel(); ano.stopVoiceConversation(); controlPanels.closeVault() } }
    onActiveChanged: { if (!active) voice.cancel(); else unread = 0 }
    property int unread: 0  // replies that arrived while you were elsewhere; shown on the floating icon
    property string liveText: ""  // the streaming reply so far (see the chat footer)

    component Field: Rectangle {
        color: root.surface
        radius: 8
        border.color: root.line
    }

    // Settings building blocks.
    component SettingsHeading: Label {
        Layout.fillWidth: true
        Layout.leftMargin: 18; Layout.rightMargin: 18; Layout.topMargin: 16; Layout.bottomMargin: 2
        font.pixelSize: 11; font.weight: Font.DemiBold; font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
        color: root.muted
    }
    // A selectable option: radio dot, title, one-line explanation.
    component ChoiceCard: Rectangle {
        id: choice
        property string title
        property string detail
        property bool selected: false
        property color dot: root.accent
        signal clicked()
        Layout.fillWidth: true
        Layout.leftMargin: 14; Layout.rightMargin: 14
        implicitHeight: choiceText.implicitHeight + 18
        radius: 10
        color: selected ? Qt.alpha(root.accent, root.dark ? 0.16 : 0.10) : choiceArea.containsMouse && enabled ? root.surface : "transparent"
        border.color: selected ? Qt.alpha(root.accent, 0.6) : root.line
        opacity: enabled ? 1 : 0.45
        Accessible.role: Accessible.RadioButton
        Accessible.name: title
        Accessible.checked: selected
        Accessible.onPressAction: choice.clicked()
        Rectangle {
            id: radio
            anchors.left: parent.left; anchors.leftMargin: 12
            anchors.top: parent.top; anchors.topMargin: 12
            width: 16; height: 16; radius: 8
            color: "transparent"
            border.width: 2
            border.color: choice.selected ? choice.dot : root.muted
            Rectangle { anchors.centerIn: parent; width: 8; height: 8; radius: 4; color: choice.dot; visible: choice.selected }
        }
        ColumnLayout {
            id: choiceText
            anchors.left: radio.right; anchors.right: parent.right; anchors.top: parent.top
            anchors.leftMargin: 10; anchors.rightMargin: 12; anchors.topMargin: 9
            spacing: 1
            Label { Layout.fillWidth: true; text: choice.title; color: root.ink; font.weight: Font.DemiBold; wrapMode: Text.Wrap }
            Label { Layout.fillWidth: true; text: choice.detail; color: root.muted; font.pixelSize: 12; wrapMode: Text.Wrap; visible: text.length > 0 }
        }
        MouseArea { id: choiceArea; anchors.fill: parent; hoverEnabled: true; enabled: choice.enabled; cursorShape: Qt.PointingHandCursor; onClicked: choice.clicked() }
    }
    // A labelled on/off row. The owner flips the real setting in onClicked; checked mirrors it.
    component SwitchRow: Item {
        id: switchRow
        property string title
        property string detail
        property bool checked: false
        signal clicked()
        Layout.fillWidth: true
        Layout.leftMargin: 18; Layout.rightMargin: 12
        implicitHeight: Math.max(rowText.implicitHeight, rowSwitch.implicitHeight) + 8
        opacity: enabled ? 1 : 0.45
        ColumnLayout {
            id: rowText
            anchors.left: parent.left; anchors.right: rowSwitch.left; anchors.verticalCenter: parent.verticalCenter
            anchors.rightMargin: 8
            spacing: 1
            Label { Layout.fillWidth: true; text: switchRow.title; color: root.ink; wrapMode: Text.Wrap }
            Label { Layout.fillWidth: true; text: switchRow.detail; color: root.muted; font.pixelSize: 12; wrapMode: Text.Wrap; visible: text.length > 0 }
        }
        Switch {
            id: rowSwitch
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            checked: switchRow.checked
            enabled: switchRow.enabled
            onClicked: { checked = Qt.binding(function() { return switchRow.checked }); switchRow.clicked() }
            Accessible.name: switchRow.title
        }
    }
    // A plain row that opens another panel.
    component LinkRow: Rectangle {
        id: linkRow
        property string title
        property string glyph
        signal clicked()
        Layout.fillWidth: true
        Layout.leftMargin: 14; Layout.rightMargin: 14
        implicitHeight: 40
        radius: 10
        color: linkArea.containsMouse ? root.surface : "transparent"
        Accessible.role: Accessible.Button
        Accessible.name: title
        Accessible.onPressAction: linkRow.clicked()
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
            spacing: 10
            Text { text: linkRow.glyph; font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: 15; color: root.muted }
            Label { Layout.fillWidth: true; text: linkRow.title; color: root.ink }
            Text { text: ""; font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: 11; color: root.muted }
        }
        MouseArea { id: linkArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: linkRow.clicked() }
    }
    // A colour choice.
    component Swatch: Rectangle {
        id: swatch
        property string label
        property bool selected: false
        signal clicked()
        width: 34; height: 34; radius: 17
        border.width: selected ? 3 : 1
        border.color: selected ? root.ink : root.line
        Accessible.role: Accessible.RadioButton
        Accessible.name: label
        Accessible.checked: selected
        Accessible.onPressAction: swatch.clicked()
        MouseArea { id: swatchArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: swatch.clicked() }
        ToolTip.visible: swatchArea.containsMouse
        ToolTip.text: label
    }

    // Menus: a floating rounded card that fades and scales in, with icon rows and soft highlights.
    component AnoMenu: Menu {
        id: menu
        padding: 6
        margins: 8
        implicitWidth: 256
        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 140; easing.type: Easing.OutCubic }
                NumberAnimation { property: "scale"; from: 0.95; to: 1; duration: 160; easing.type: Easing.OutCubic }
            }
        }
        // No exit animation: a fading menu would still swallow the next click (e.g. a header drag).
        background: Item {
            implicitWidth: 256
            // A soft layered shadow, then the card itself.
            Repeater {
                model: 3
                delegate: Rectangle {
                    required property int index
                    anchors.fill: parent
                    anchors.margins: -(index + 1) * 2
                    anchors.topMargin: -(index + 1) * 1
                    radius: 14 + (index + 1) * 2
                    color: "#000000"
                    opacity: root.dark ? 0.10 : 0.035
                }
            }
            Rectangle {
                anchors.fill: parent
                radius: 14
                color: root.dark ? "#1b2533" : "#ffffff"
                border.color: root.dark ? "#2f3b4c" : "#e3e7ee"
            }
        }
    }
    component AnoMenuItem: MenuItem {
        id: item
        property string glyph: ""
        property string hint: ""
        property bool danger: false
        property bool shown: true  // hide rows that cannot apply right now instead of greying them out
        visible: shown
        height: shown ? implicitHeight : 0
        implicitHeight: 38
        leftPadding: 10
        rightPadding: 10
        indicator: Item {}
        arrow: Item {}
        contentItem: RowLayout {
            spacing: 12
            opacity: item.enabled ? 1 : 0.4
            Text {
                Layout.preferredWidth: 18
                horizontalAlignment: Text.AlignHCenter
                text: item.glyph
                font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"
                font.pixelSize: 15
                color: item.danger ? root.danger : item.highlighted ? root.accent : root.muted
            }
            Label {
                Layout.fillWidth: true
                text: item.text
                elide: Text.ElideRight
                font.pixelSize: 13
                color: item.danger ? root.danger : root.ink
            }
            Label { visible: item.hint.length > 0; text: item.hint; font.pixelSize: 11; color: root.muted }
            Text {
                visible: item.checkable && item.checked
                text: ""
                font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"
                font.pixelSize: 13
                color: root.accent
            }
        }
        background: Rectangle {
            radius: 9
            color: !item.highlighted || !item.enabled ? "transparent"
                   : item.danger ? Qt.alpha(root.danger, 0.13) : Qt.alpha(root.accent, root.dark ? 0.16 : 0.10)
            Behavior on color { ColorAnimation { duration: 90 } }
        }
    }
    component AnoMenuSeparator: MenuSeparator {
        property bool shown: true
        visible: shown
        height: shown ? implicitHeight : 0
        topPadding: 5
        bottomPadding: 5
        contentItem: Rectangle { implicitHeight: 1; color: root.line; opacity: 0.8 }
    }

    // A small rounded status pill with a coloured dot; tap for options.
    component StatusChip: Rectangle {
        id: chip
        property alias text: chipLabel.text
        property color dot: root.muted
        property string tip: ""
        signal clicked()
        ToolTip.visible: tip.length > 0 && chipArea.containsMouse
        ToolTip.text: tip
        ToolTip.delay: 400
        implicitWidth: chipRow.implicitWidth + 18
        implicitHeight: 24
        radius: 12
        color: chipArea.containsMouse && chip.enabled ? root.surface : "transparent"
        border.color: root.line
        opacity: enabled ? 1 : 0.5
        Accessible.role: Accessible.Button
        Accessible.name: chip.text
        Accessible.onPressAction: chip.clicked()
        RowLayout {
            id: chipRow
            anchors.centerIn: parent
            spacing: 6
            Rectangle { Layout.preferredWidth: 7; Layout.preferredHeight: 7; radius: 3.5; color: chip.dot }
            Label { id: chipLabel; color: root.ink; font.pixelSize: 12 }
        }
        MouseArea {
            id: chipArea
            anchors.fill: parent
            hoverEnabled: true
            enabled: chip.enabled
            cursorShape: Qt.PointingHandCursor
            onClicked: chip.clicked()
        }
    }

    Repeater {
        model: 4
        delegate: Rectangle {
            required property int index
            readonly property real spread: (index + 1) * 1.4
            x: 10 - spread
            y: 12 + index * .6
            width: root.width - 20 + spread * 2
            height: root.height - 24 + spread
            radius: 22 + spread
            color: "#0c1626"
            opacity: root.dark ? .09 : .025
        }
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: 10
        radius: 22
        color: root.bg
        border.color: root.line
    }
    // Resize handles in the transparent 10 px rim around the panel (it is frameless, so Windows provides none).
    Repeater {
        model: [
            { edges: Qt.LeftEdge, x: 0, y: 24, w: 9, h: -48, cursor: Qt.SizeHorCursor },
            { edges: Qt.RightEdge, x: -9, y: 24, w: 9, h: -48, cursor: Qt.SizeHorCursor },
            { edges: Qt.TopEdge, x: 24, y: 0, w: -48, h: 9, cursor: Qt.SizeVerCursor },
            { edges: Qt.BottomEdge, x: 24, y: -9, w: -48, h: 9, cursor: Qt.SizeVerCursor },
            { edges: Qt.LeftEdge | Qt.TopEdge, x: 0, y: 0, w: 24, h: 24, cursor: Qt.SizeFDiagCursor },
            { edges: Qt.RightEdge | Qt.BottomEdge, x: -24, y: -24, w: 24, h: 24, cursor: Qt.SizeFDiagCursor },
            { edges: Qt.RightEdge | Qt.TopEdge, x: -24, y: 0, w: 24, h: 24, cursor: Qt.SizeBDiagCursor },
            { edges: Qt.LeftEdge | Qt.BottomEdge, x: 0, y: -24, w: 24, h: 24, cursor: Qt.SizeBDiagCursor }
        ]
        delegate: MouseArea {
            required property var modelData
            objectName: "resizeHandle"
            // Negative x/y/w/h count from the far side, so the handles follow the window size.
            x: modelData.x < 0 ? root.width + modelData.x : modelData.x
            y: modelData.y < 0 ? root.height + modelData.y : modelData.y
            width: modelData.w < 0 ? root.width + modelData.w : modelData.w
            height: modelData.h < 0 ? root.height + modelData.h : modelData.h
            // Corner squares reach slightly inside the rounded corner, where the panel has no controls.
            z: 10
            hoverEnabled: true
            cursorShape: modelData.cursor
            onPressed: root.startSystemResize(modelData.edges)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 20
        anchors.rightMargin: 20
        anchors.topMargin: 22
        anchors.bottomMargin: 20
        spacing: 0

        // Top bar: Ano's live face, name and mood, and one menu for everything else.
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 4
            Layout.bottomMargin: 8
            spacing: root.narrow ? 2 : 10
            Item {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.preferredHeight: 42
                clip: true
                RowLayout {
                    anchors.fill: parent
                    spacing: root.narrow ? 8 : 10
                    AnoFace {
                        id: headerFace
                        objectName: "headerFace"
                        Layout.preferredWidth: root.narrow ? 34 : 42
                        Layout.preferredHeight: root.narrow ? 34 : 42
                        mood: root.mood
                        animated: root.visible
                        interactive: false
                        soundEnabled: false
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        spacing: 0
                        Label { text: "Ano"; font.pixelSize: root.narrow ? 16 : 17; font.bold: true; color: root.ink }
                        Label {
                            objectName: "moodText"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            elide: Text.ElideRight
                            text: root.mood === "sleep" ? "paused" : root.mood === "listen" ? "listening…"
                                  : root.mood === "think" ? "thinking…" : root.mood === "talk" ? "talking…"
                                  : root.mood === "confused" ? "could you clarify?"
                                  : ano.connected ? "ready" : "offline · local only"
                            font.pixelSize: 11
                            color: root.mood === "listen" ? root.danger : root.accent
                        }
                    }
                }
                MouseArea {
                    objectName: "panelDragHandle"
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                    property bool nativeMove: false
                    property point pressGlobal
                    property point startPosition
                    onPressed: function(mouse) {
                        pressGlobal = mapToGlobal(mouse.x, mouse.y)
                        startPosition = Qt.point(root.x, root.y)
                        nativeMove = root.startSystemMove()
                    }
                    onPositionChanged: function(mouse) {
                        if (pressed && !nativeMove) {
                            var current = mapToGlobal(mouse.x, mouse.y)
                            root.x = startPosition.x + current.x - pressGlobal.x
                            root.y = startPosition.y + current.y - pressGlobal.y
                        }
                    }
                    onDoubleClicked: root.applySize(root.sizePreset === "compact" ? "standard" : "compact")
                    ToolTip.visible: containsMouse && !pressed
                    ToolTip.delay: 650
                    ToolTip.text: "Drag to move · double-click for compact size"
                }
            }
            IconButton {
                objectName: "openChats"
                Layout.preferredWidth: root.headerButton
                glyph: "\uE8BD"
                onClicked: chatsPopup.open()
                ToolTip.text: "Your chats"
                Accessible.name: "Show chats"
            }
            IconButton {
                objectName: "newChat"
                Layout.preferredWidth: root.headerButton
                glyph: "\uE710"
                enabled: !ano.busy || ano.queueEnabled
                onClicked: { search.text = ""; search.visible = false; ano.newChat() }
                ToolTip.text: "New chat"
                Accessible.name: "Start a new chat"
            }
            IconButton {
                glyph: "\uE71A"
                visible: speech.speaking
                onClicked: speech.stop()
                ToolTip.text: "Stop speaking"
                Accessible.name: "Stop spoken reply"
            }
            IconButton {
                glyph: "\uE769"
                visible: ano.voiceConversationActive
                onClicked: ano.interruptVoiceConversation()
                ToolTip.text: "Interrupt voice turn"
                Accessible.name: "Interrupt voice conversation"
            }
            IconButton {
                objectName: "openSettingsButton"
                Layout.preferredWidth: root.headerButton
                glyph: ""
                onClicked: settingsPanel.open()
                ToolTip.text: "Settings"
                Accessible.name: "Open settings"
            }
            IconButton {
                objectName: "openMenu"
                Layout.preferredWidth: root.headerButton
                glyph: "\uE712"
                onClicked: mainMenu.popup(this, 0, height)
                ToolTip.text: "More"
                Accessible.name: "Open menu"
            }
            IconButton {
                objectName: "collapseToIcon"
                glyph: "\uE8BB"
                Layout.preferredWidth: 28
                onClicked: { ano.setFloating(true); root.hide() }
                ToolTip.text: "Collapse to floating icon"
                Accessible.name: "Collapse Ano to floating icon"
            }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: root.line }

        Rectangle {
            Layout.fillWidth: true
            visible: desktopControl.enabled || savedLogins.hold
            implicitHeight: controlBanner.implicitHeight + 16
            color: root.surface
            RowLayout {
                id: controlBanner
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 8
                Label { Layout.fillWidth: true; text: savedLogins.hold ? "Private login · Screens and tools blocked" : desktopControl.status; wrapMode: Text.Wrap; color: root.ink; font.pixelSize: 11 }
                AnoButton {
                    text: "Continue task · full control"
                    visible: savedLogins.hold && ano.websiteTaskPending
                    enabled: !savedLogins.busy && !ano.busy && !ano.paused
                    ToolTip.visible: hovered
                    ToolTip.text: "After you finish signing in, clear private mode and continue the saved task with full computer control. Esc stops."
                    onClicked: ano.continueWebsiteTask()
                }
                AnoButton {
                    text: savedLogins.hold ? "Done with login" : "Stop control"
                    enabled: !savedLogins.busy
                    onClicked: { if (savedLogins.hold) savedLogins.done(); else desktopControl.stop() }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            visible: ano.paused
            implicitHeight: 40
            color: root.surface
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 8
                Label { Layout.fillWidth: true; text: "Ano is paused"; color: root.ink }
                AnoButton { text: "Resume"; onClicked: ano.togglePause() }
            }
        }

        TextField {
            id: search
            visible: false
            Layout.fillWidth: true
            Layout.margins: 12
            Layout.bottomMargin: 0
            placeholderText: "Search messages…"
            onTextChanged: ano.search(text)
            Keys.onEscapePressed: { text = ""; visible = false }
            Accessible.name: "Search conversation history"
            color: root.ink
            background: Field {}
        }

        ListView {
            id: chat
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 14
            topMargin: 14
            bottomMargin: 14
            leftMargin: 12
            rightMargin: 12
            model: ano.messages
            // Stay pinned to the newest message while replies lay out (Markdown sizes settle late),
            // unless the user has scrolled up to read something older.
            property bool stickToEnd: true
            onMovementEnded: stickToEnd = atYEnd
            onCountChanged: { stickToEnd = true; scrollToEnd.restart() }
            onContentHeightChanged: { if (stickToEnd) scrollToEnd.restart() }
            Timer { id: scrollToEnd; interval: 40; onTriggered: chat.positionViewAtEnd() }
            ScrollBar.vertical: ScrollBar {}
            ColumnLayout {
                anchors.centerIn: parent
                width: parent.width - 48
                visible: chat.count === 0
                spacing: 8
                AnoFace {
                    objectName: "welcomeFace"
                    Layout.alignment: Qt.AlignHCenter
                    visible: !search.text.length
                    Layout.preferredWidth: 72
                    Layout.preferredHeight: 72
                    mood: root.mood
                    animated: root.visible
                    interactive: false
                    soundEnabled: false
                }
                Label {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    text: search.text.length ? "No matching messages." : "How can I help?"
                    font.pixelSize: 23
                    font.weight: Font.DemiBold
                    color: root.ink
                }
                Label {
                    Layout.fillWidth: true
                    visible: !search.text.length
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    text: ano.connected ? "Research, plan, and get things done."
                                        : "Connect your local brain with python -m ano.configure local so I can answer."
                    color: root.muted
                }
                // One tap runs a free built-in command; "Research…" just starts the sentence for you.
                Flow {
                    objectName: "suggestions"
                    Layout.fillWidth: true
                    Layout.topMargin: 10
                    visible: !search.text.length
                    spacing: 8
                    Repeater {
                        model: [{ label: "What can you do?", command: "show commands" },
                                { label: "Check my laptop", command: "check laptop health" },
                                { label: "Research a topic…", command: "research " },
                                { label: "Codex watch status", command: "codex watch status" }]
                        delegate: Rectangle {
                            required property var modelData
                            width: suggestionText.implicitWidth + 26
                            height: 32
                            radius: 16
                            color: suggestionArea.containsMouse ? Qt.alpha(root.accent, 0.14) : root.surface
                            border.color: suggestionArea.containsMouse ? Qt.alpha(root.accent, 0.5) : root.line
                            opacity: enabled ? 1 : 0.5
                            enabled: !ano.paused && (!ano.busy || ano.queueEnabled)
                            Accessible.role: Accessible.Button
                            Accessible.name: modelData.label
                            Label { id: suggestionText; anchors.centerIn: parent; text: modelData.label; color: root.ink; font.pixelSize: 12 }
                            MouseArea {
                                id: suggestionArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    composer.text = modelData.command
                                    if (modelData.command.slice(-1) === " ") {
                                        composer.forceActiveFocus()
                                        composer.cursorPosition = composer.text.length
                                    } else {
                                        root.submit()
                                    }
                                }
                            }
                        }
                    }
                }
            }
            // Your messages: tinted bubbles on the right. Ano's replies: cards beside its mark, with the
            // actions it ran shown as chips and anything it could not finish shown as an amber card.
            delegate: Item {
                id: message
                required property var modelData
                required property int index
                readonly property bool mine: modelData.role === "user"
                readonly property var parts: mine ? ({ main: modelData.body, notes: [], blockers: [] }) : root.splitBody(modelData.body)
                readonly property bool blocked: !mine && root.isBlocked(parts.main)
                // Notes Ano posts on its own (monitors, reminders, scheduled typing, Codex watch) are events, not replies.
                // A notification a monitor caught is content (and may carry Approve/Dismiss), so it gets a full card.
                readonly property string eventGlyph: mine || /^Monitor #\d+: notification from/.test(modelData.body) ? "" : /^Monitor #\d+/.test(modelData.body) ? ""
                                                    : /^Reminder:/.test(modelData.body) ? ""
                                                    : /^Scheduled action #\d+/.test(modelData.body) ? ""
                                                    : /^(Codex hit its usage limit|Pressed Resume goal|Codex is running again|Could not press Resume goal)/.test(modelData.body) ? "" : ""
                readonly property bool systemEvent: eventGlyph.length > 0
                // The same event posted again (e.g. the Codex watch re-announcing after a restart) is shown once.
                readonly property bool repeatEvent: systemEvent && index > 0 && ano.messages[index - 1] !== undefined
                                                    && ano.messages[index - 1].body === modelData.body
                visible: !repeatEvent
                // A "Today" / "Yesterday" / date heading above the first message of each day.
                readonly property string day: root.dayKey(modelData.created_at)
                readonly property bool newDay: day.length > 0 && (index === 0 || ano.messages[index - 1] === undefined
                                                                  || root.dayKey(ano.messages[index - 1].created_at) !== day)
                readonly property real dayHeight: newDay ? 34 : 0
                width: chat.width - chat.leftMargin - chat.rightMargin
                implicitHeight: repeatEvent ? 0 : dayHeight + (mine ? userBubble.height : systemEvent ? eventPill.height + 4 : reply.implicitHeight)

                RowLayout {
                    objectName: "daySeparator"
                    visible: message.newDay
                    width: parent.width
                    height: 24
                    spacing: 10
                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: root.line }
                    Label { text: root.dayName(message.modelData.created_at); font.pixelSize: 11; font.weight: Font.DemiBold; color: root.muted }
                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: root.line }
                }

                Rectangle {
                    id: eventPill
                    objectName: "eventPill"
                    visible: message.systemEvent
                    y: message.dayHeight
                    anchors.horizontalCenter: parent.horizontalCenter
                    // Something Ano noticed for you (a monitor firing, a reminder) stands out; routine notes stay quiet.
                    readonly property bool alert: /^(Monitor #|Reminder:)/.test(message.modelData.body)
                    width: Math.min(eventRow.implicitWidth + 28, parent.width * 0.92)
                    height: visible ? eventRow.implicitHeight + (alert ? 20 : 14) : 0
                    radius: height > 40 ? 14 : height / 2
                    color: Qt.alpha(alert ? root.warn : root.accent, root.dark ? (alert ? 0.16 : 0.10) : (alert ? 0.12 : 0.07))
                    border.color: Qt.alpha(alert ? root.warn : root.accent, alert ? 0.55 : 0.28)
                    Rectangle {
                        visible: eventPill.alert
                        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                        anchors.margins: 6
                        width: 3
                        radius: 2
                        color: root.warn
                    }
                    RowLayout {
                        id: eventRow
                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 14; anchors.rightMargin: 14
                        spacing: 8
                        Text { text: message.eventGlyph; font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: eventPill.alert ? 15 : 13; color: eventPill.alert ? root.warn : root.accent; Layout.alignment: Qt.AlignTop; Layout.topMargin: 2 }
                        Label {
                            Layout.fillWidth: true
                            Layout.maximumWidth: message.width * 0.92 - 90
                            text: message.systemEvent ? message.parts.main.replace(/\n+/g, " · ") + (message.parts.notes.length ? " · " + message.parts.notes.join(" · ") : "") : ""
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            font.pixelSize: eventPill.alert ? 13 : 12
                            font.weight: eventPill.alert ? Font.DemiBold : Font.Normal
                            color: root.ink
                        }
                        Label { text: root.clock(message.modelData.created_at); font.pixelSize: 11; color: root.muted; Layout.alignment: Qt.AlignTop; Layout.topMargin: 2 }
                    }
                }

                Rectangle {
                    id: userBubble
                    visible: message.mine
                    y: message.dayHeight
                    anchors.right: parent.right
                    // +6 slack: the TextArea needs a little more room than the measured text, or it wraps early.
                    width: Math.min(userMeasure.implicitWidth + 36, parent.width * 0.82)
                    height: visible ? userText.implicitHeight + 20 : 0
                    radius: 14
                    color: Qt.alpha(root.accent, root.dark ? 0.20 : 0.12)
                    border.color: Qt.alpha(root.accent, 0.32)
                    Text { id: userMeasure; visible: false; text: message.modelData.body; font: userText.font; textFormat: Text.PlainText }
                    TextArea {
                        id: userText
                        objectName: "userMessageText"
                        anchors.fill: parent
                        anchors.topMargin: 10; anchors.bottomMargin: 10; anchors.leftMargin: 15; anchors.rightMargin: 15
                        padding: 0
                        background: Item {}
                        text: message.mine ? message.modelData.body : ""
                        textFormat: TextEdit.PlainText
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.Wrap
                        color: root.ink
                        selectionColor: root.accent
                        selectedTextColor: root.palette.highlightedText
                    }
                }

                RowLayout {
                    id: reply
                    visible: !message.mine && !message.systemEvent
                    y: message.dayHeight
                    width: parent.width
                    spacing: 8
                    Image {
                        Layout.alignment: Qt.AlignTop
                        Layout.topMargin: 6
                        Layout.preferredWidth: 22
                        Layout.preferredHeight: 22
                        source: "../assets/ano-mark.png"
                        sourceSize: Qt.size(44, 44)
                        fillMode: Image.PreserveAspectFit
                        smooth: true
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        spacing: 2
                        Rectangle {
                            id: card
                            Layout.fillWidth: true
                            implicitHeight: cardColumn.implicitHeight + 22
                            radius: 12
                            color: message.blocked ? Qt.alpha(root.warn, root.dark ? 0.12 : 0.08) : root.surface
                            border.color: message.blocked ? Qt.alpha(root.warn, 0.5) : root.line
                            ColumnLayout {
                                id: cardColumn
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                anchors.topMargin: 11; anchors.leftMargin: 14; anchors.rightMargin: 14
                                spacing: 8
                                RowLayout {
                                    visible: message.blocked
                                    spacing: 6
                                    Text { text: "\uE7BA"; font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: 13; color: root.warn }
                                    Label { text: "Couldn't finish"; font.pixelSize: 12; font.weight: Font.DemiBold; color: root.warn }
                                }
                                // Long replies start folded to a readable height, with "Show more".
                                Item {
                                    id: segmentsBox
                                    property bool expanded: false
                                    readonly property bool long: segmentsColumn.implicitHeight > 440
                                    Layout.fillWidth: true
                                    implicitHeight: long && !expanded ? 320 : segmentsColumn.implicitHeight
                                    clip: true
                                    ColumnLayout {
                                        id: segmentsColumn
                                        width: parent.width
                                        spacing: 8
                                Repeater {
                                    model: message.mine ? [] : root.segments(message.blocked ? root.blockedBody(message.parts) : message.parts.main)
                                    delegate: Item {
                                        id: segment
                                        required property var modelData
                                        Layout.fillWidth: true
                                        implicitHeight: modelData.kind === "code" ? codeBox.implicitHeight
                                                        : modelData.kind === "table" ? tableBox.implicitHeight : bodyText.implicitHeight
                                        TextArea {
                                            id: bodyText
                                            objectName: "responseText"
                                            visible: segment.modelData.kind === "text"
                                            width: parent.width
                                            padding: 0
                                            background: Item {}
                                            // Render Ano's Markdown so **bold** and lists look right instead of showing raw ** marks.
                                            text: visible ? root.displayBody(segment.modelData.text) : ""
                                            textFormat: TextEdit.MarkdownText
                                            readOnly: true
                                            selectByMouse: true
                                            selectByKeyboard: true
                                            persistentSelection: true
                                            activeFocusOnTab: true
                                            wrapMode: TextEdit.Wrap
                                            color: root.ink
                                            selectionColor: root.accent
                                            selectedTextColor: root.palette.highlightedText
                                            palette.link: root.accent
                                            onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                                        }
                                        Rectangle {
                                            id: codeBox
                                            objectName: "codeBlock"
                                            visible: segment.modelData.kind === "code"
                                            width: parent.width
                                            implicitHeight: visible ? codeHeader.height + codeText.implicitHeight + 16 : 0
                                            radius: 8
                                            color: Qt.alpha(root.ink, root.dark ? 0.06 : 0.04)
                                            border.color: root.line
                                            RowLayout {
                                                id: codeHeader
                                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                                anchors.leftMargin: 10; anchors.rightMargin: 2
                                                height: 26
                                                Label { Layout.fillWidth: true; text: segment.modelData.lang || "code"; font.pixelSize: 11; color: root.muted }
                                                IconButton {
                                                    id: copyCode
                                                    property bool copied: false
                                                    implicitWidth: 24; implicitHeight: 24
                                                    glyph: copied ? "" : ""
                                                    ToolTip.text: copied ? "Copied" : "Copy code"
                                                    Accessible.name: "Copy code"
                                                    onClicked: { ano.copyText(segment.modelData.text); copied = true; codeCopied.restart() }
                                                    Timer { id: codeCopied; interval: 1800; onTriggered: copyCode.copied = false }
                                                }
                                            }
                                            TextArea {
                                                id: codeText
                                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: codeHeader.bottom
                                                anchors.leftMargin: 10; anchors.rightMargin: 10
                                                padding: 0
                                                background: Item {}
                                                text: codeBox.visible ? segment.modelData.text : ""
                                                textFormat: TextEdit.PlainText
                                                readOnly: true
                                                selectByMouse: true
                                                wrapMode: TextEdit.WrapAnywhere
                                                font.family: "Cascadia Mono, Consolas"
                                                font.pixelSize: 12
                                                color: root.ink
                                                selectionColor: root.accent
                                                selectedTextColor: root.palette.highlightedText
                                            }
                                        }
                                        Rectangle {
                                            id: tableBox
                                            objectName: "tableBlock"
                                            visible: segment.modelData.kind === "table"
                                            width: parent.width
                                            implicitHeight: visible ? tableGrid.implicitHeight + 2 : 0
                                            radius: 8
                                            color: "transparent"
                                            border.color: root.line
                                            clip: true
                                            GridLayout {
                                                id: tableGrid
                                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                                anchors.margins: 1
                                                columns: Math.max(1, segment.modelData.rows.length ? segment.modelData.rows[0].length : 1)
                                                rowSpacing: 0
                                                columnSpacing: 0
                                                Repeater {
                                                    model: tableBox.visible ? root.tableCells(segment.modelData.rows) : []
                                                    delegate: Rectangle {
                                                        required property var modelData
                                                        Layout.fillWidth: true
                                                        Layout.fillHeight: true
                                                        Layout.preferredWidth: 1
                                                        implicitHeight: cellText.implicitHeight + 12
                                                        color: modelData.header ? Qt.alpha(root.accent, root.dark ? 0.16 : 0.10)
                                                               : modelData.stripe ? Qt.alpha(root.ink, root.dark ? 0.04 : 0.03) : "transparent"
                                                        Label {
                                                            id: cellText
                                                            anchors.left: parent.left; anchors.right: parent.right
                                                            anchors.verticalCenter: parent.verticalCenter
                                                            anchors.leftMargin: 8; anchors.rightMargin: 8
                                                            text: modelData.text
                                                            textFormat: Text.MarkdownText
                                                            wrapMode: Text.Wrap
                                                            font.pixelSize: 12
                                                            font.weight: modelData.header ? Font.DemiBold : Font.Normal
                                                            color: root.ink
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                                    }
                                    // A soft fade over the fold so it reads as "continues".
                                    Rectangle {
                                        visible: segmentsBox.long && !segmentsBox.expanded
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                                        height: 56
                                        gradient: Gradient {
                                            GradientStop { position: 0; color: Qt.alpha(card.color, 0) }
                                            GradientStop { position: 1; color: card.color }
                                        }
                                    }
                                }
                                Label {
                                    objectName: "showMore"
                                    visible: segmentsBox.long
                                    text: segmentsBox.expanded ? "Show less" : "Show more"
                                    color: root.accent
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: segmentsBox.expanded = !segmentsBox.expanded }
                                }
                                // Windows would not let Ano bring the app forward: say what fixes it, in plain words.
                                Label {
                                    objectName: "focusHint"
                                    Layout.fillWidth: true
                                    visible: message.blocked && /did not focus|stayed in front|could not (?:focus|switch)/i.test(message.modelData.body)
                                    text: "Windows kept another window in front. Click the app once so it is on top, then tap Retry."
                                    wrapMode: Text.Wrap
                                    color: root.warn
                                    font.pixelSize: 12
                                }
                                // What you can do about a task that could not finish.
                                RowLayout {
                                    objectName: "blockedActions"
                                    visible: message.blocked
                                    spacing: 6
                                    readonly property string retryText: root.previousUserMessage(message.index)
                                    readonly property bool needsControl: /computer control is off|enable computer control|control (?:is )?(?:off|stopped)/i.test(message.modelData.body)
                                    AnoButton {
                                        visible: parent.needsControl
                                        primary: true
                                        text: "Turn on control"
                                        onClicked: settingsPanel.open()
                                    }
                                    AnoButton {
                                        visible: parent.retryText.length > 0
                                        primary: !parent.needsControl
                                        text: "Retry"
                                        enabled: !ano.busy && !ano.paused
                                        onClicked: ano.send(parent.retryText)
                                    }
                                    AnoButton { text: "Settings"; visible: !parent.needsControl; onClicked: settingsPanel.open() }
                                }
                                // "Handle it" for a notification: Ano does nothing until you tap Approve.
                                RowLayout {
                                    id: handleRow
                                    objectName: "handleActions"
                                    readonly property var found: message.modelData.role === "assistant"
                                                                 ? /Handle request #(\d+):/.exec(message.modelData.body) : null
                                    readonly property int number: found ? parseInt(found[1]) : 0
                                    readonly property string decision: number ? (ano.handleStates[String(number)] || "expired") : ""
                                    visible: number > 0
                                    spacing: 6
                                    // Hold to approve: a click can come from automation (another agent driving the
                                    // screen), a held press is a person. It is not exposed as an invokable button.
                                    Rectangle {
                                        id: holdApprove
                                        objectName: "holdApprove"
                                        visible: handleRow.decision === "pending"
                                        implicitWidth: holdLabel.implicitWidth + 28
                                        implicitHeight: 36
                                        radius: 7
                                        clip: true
                                        color: Qt.alpha(root.accent, root.dark ? 0.22 : 0.16)
                                        border.color: Qt.alpha(root.accent, 0.6)
                                        opacity: ano.paused ? 0.5 : 1
                                        Accessible.role: Accessible.StaticText
                                        Accessible.name: "Press and hold to approve"
                                        Rectangle {
                                            id: holdFill
                                            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                            width: 0
                                            color: root.accent
                                            opacity: 0.45
                                        }
                                        NumberAnimation {
                                            id: holdProgress
                                            target: holdFill; property: "width"
                                            from: 0; to: holdApprove.width; duration: 900
                                            onFinished: if (holdArea.pressed && holdFill.width >= holdApprove.width) ano.approveHandle(handleRow.number)
                                        }
                                        Label {
                                            id: holdLabel
                                            anchors.centerIn: parent
                                            text: holdArea.pressed ? "Keep holding…" : "Hold to approve"
                                            color: root.ink
                                            font.pixelSize: 13
                                            font.weight: Font.DemiBold
                                        }
                                        MouseArea {
                                            id: holdArea
                                            anchors.fill: parent
                                            enabled: !ano.paused
                                            cursorShape: Qt.PointingHandCursor
                                            onPressed: holdProgress.restart()
                                            onReleased: { holdProgress.stop(); holdFill.width = 0 }
                                            onCanceled: { holdProgress.stop(); holdFill.width = 0 }
                                        }
                                    }
                                    AnoButton {
                                        visible: handleRow.decision === "pending"
                                        text: "Dismiss"
                                        onClicked: ano.dismissHandle(handleRow.number)
                                    }
                                    Label {
                                        visible: handleRow.decision !== "pending"
                                        text: handleRow.decision === "approved" ? "Approved: Ano is on it"
                                              : handleRow.decision === "dismissed" ? "Dismissed" : "This request expired"
                                        color: root.muted
                                        font.pixelSize: 12
                                    }
                                }
                                Flow {
                                    Layout.fillWidth: true
                                    visible: message.parts.notes.length > 0
                                    spacing: 6
                                    Repeater {
                                        model: message.parts.notes
                                        delegate: Rectangle {
                                            required property string modelData
                                            width: Math.min(noteText.implicitWidth + 18, cardColumn.width)
                                            height: noteText.implicitHeight + 8
                                            radius: 9
                                            color: Qt.alpha(root.ink, root.dark ? 0.07 : 0.05)
                                            Label {
                                                id: noteText
                                                anchors.left: parent.left; anchors.right: parent.right
                                                anchors.leftMargin: 9; anchors.rightMargin: 9
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: root.shortNote(modelData)
                                                textFormat: Text.PlainText
                                                elide: Text.ElideRight
                                                font.pixelSize: 12
                                                color: root.muted
                                            }
                                            MouseArea { id: noteHover; anchors.fill: parent; hoverEnabled: true }
                                            ToolTip.visible: noteHover.containsMouse && (noteText.truncated || noteText.text !== modelData)
                                            ToolTip.text: modelData
                                        }
                                    }
                                }
                            }
                        }
                        RowLayout {
                            spacing: 2
                            IconButton {
                                id: copyResponse
                                objectName: "copyResponse"
                                property bool copied: false
                                property string responseText: message.modelData.body
                                implicitWidth: 28; implicitHeight: 28
                                glyph: copied ? "\uE73E" : "\uE8C8"
                                ToolTip.text: copied ? "Copied" : "Copy response"
                                Accessible.name: copied ? "Response copied" : "Copy response"
                                onClicked: { ano.copyText(responseText); copied = true; copyFeedback.restart() }
                                Timer { id: copyFeedback; interval: 1800; onTriggered: copyResponse.copied = false }
                            }
                            Label { text: root.clock(message.modelData.created_at); font.pixelSize: 11; color: root.muted }
                        }
                    }
                }
            }
            // While Ano works: its mark, animated dots and the live progress line; with a streaming brain,
            // the reply also types itself out in a card above the dots as it arrives.
            footer: Item {
                width: chat.width - chat.leftMargin - chat.rightMargin
                height: ano.busy ? (root.liveText.length ? liveCard.height + 10 : 0) + 46 : 0
                visible: ano.busy
                RowLayout {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    visible: root.liveText.length > 0
                    spacing: 8
                    Image {
                        Layout.alignment: Qt.AlignTop
                        Layout.topMargin: 6
                        Layout.preferredWidth: 22; Layout.preferredHeight: 22
                        source: "../assets/ano-mark.png"; sourceSize: Qt.size(44, 44); fillMode: Image.PreserveAspectFit; smooth: true
                    }
                    Rectangle {
                        id: liveCard
                        objectName: "liveReply"
                        Layout.fillWidth: true
                        // A RowLayout sizes children by their preferred height, so set that (not just height).
                        Layout.preferredHeight: liveLabel.implicitHeight + 22
                        radius: 12
                        color: root.surface
                        border.color: Qt.alpha(root.accent, 0.4)
                        Label {
                            id: liveLabel
                            x: 14; y: 11
                            width: parent.width - 28
                            wrapMode: Text.Wrap
                            textFormat: Text.MarkdownText
                            // A block cursor at the end shows it is still being written.
                            text: root.displayBody(root.liveText) + " ▍"
                            color: root.ink
                        }
                    }
                }
                RowLayout {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    spacing: 8
                    Image {
                        Layout.preferredWidth: 22; Layout.preferredHeight: 22
                        opacity: root.liveText.length ? 0 : 1
                        source: "../assets/ano-mark.png"; sourceSize: Qt.size(44, 44); fillMode: Image.PreserveAspectFit; smooth: true
                    }
                    Rectangle {
                        objectName: "workingIndicator"
                        Layout.fillWidth: true
                        implicitHeight: 34
                        radius: 12
                        color: root.surface
                        border.color: root.line
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 14; anchors.rightMargin: 12
                            spacing: 5
                            Repeater {
                                model: 3
                                delegate: Rectangle {
                                    required property int index
                                    Layout.preferredWidth: 6; Layout.preferredHeight: 6; radius: 3
                                    color: root.accent
                                    opacity: 0.25
                                    SequentialAnimation on opacity {
                                        running: ano.busy && root.visible
                                        loops: Animation.Infinite
                                        PauseAnimation { duration: index * 160 }
                                        NumberAnimation { to: 1; duration: 320; easing.type: Easing.OutQuad }
                                        NumberAnimation { to: 0.25; duration: 320; easing.type: Easing.InQuad }
                                        PauseAnimation { duration: (2 - index) * 160 }
                                    }
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                Layout.leftMargin: 6
                                text: ano.status
                                elide: Text.ElideRight
                                font.pixelSize: 12
                                color: root.muted
                            }
                        }
                    }
                }
            }
        }
        Connections {
            target: ano
            ignoreUnknownSignals: true
            function onBusyChanged() { if (!ano.busy) root.liveText = ""; chat.stickToEnd = true; scrollToEnd.restart() }
            // The streamed reply so far, for the open chat only; "" when it is finished or discarded.
            function onPartialReply(chatId, text) {
                root.liveText = chatId === ano.currentChat ? text : ""
                if (chat.stickToEnd) scrollToEnd.restart()
            }
        }

        // Website task progress: "Step 3 of 6 · clicking Create" while a browser task runs.
        Rectangle {
            id: taskBar
            objectName: "taskProgress"
            readonly property var task: ano.taskProgress || ({})
            readonly property int total: task.total || 0
            readonly property bool blocked: task.state === "blocked" || task.state === "partly_completed"
            visible: total > 0 && (task.state === "running" || (blocked && ano.busy))
            Layout.fillWidth: true
            Layout.leftMargin: 12
            Layout.rightMargin: 12
            Layout.topMargin: 6
            Layout.bottomMargin: -6
            implicitHeight: 46
            radius: 10
            color: root.surface
            border.color: Qt.alpha(blocked ? root.warn : root.accent, 0.4)
            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: 12; anchors.rightMargin: 12; anchors.topMargin: 7; anchors.bottomMargin: 8
                spacing: 5
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Label {
                        text: taskBar.blocked ? "Stopped at step " + taskBar.task.step + " of " + taskBar.total
                                              : "Step " + taskBar.task.step + " of " + taskBar.total
                        color: taskBar.blocked ? root.warn : root.accent
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "· " + (taskBar.task.instruction || taskBar.task.goal || "")
                        elide: Text.ElideRight
                        color: root.muted
                        font.pixelSize: 12
                        ToolTip.visible: barHover.hovered && !!taskBar.task.goal
                        ToolTip.text: taskBar.task.goal || ""
                        HoverHandler { id: barHover }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 4
                    radius: 2
                    color: root.line
                    Rectangle {
                        width: parent.width * Math.min(1, (taskBar.task.verified_steps || 0) / Math.max(1, taskBar.total))
                        height: parent.height
                        radius: 2
                        color: taskBar.blocked ? root.warn : root.accent
                        Behavior on width { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
                    }
                }
            }
        }

        // Composer: text, hold-to-talk mic, send.
        Rectangle {
            Layout.fillWidth: true
            Layout.margins: 12
            Layout.bottomMargin: 4
            implicitHeight: Math.min(Math.max(composer.implicitHeight + 16, 52), 140)
            radius: 10
            color: root.surface
            border.color: composer.activeFocus ? root.accent : root.line
            z: 5  // so the suggestion list above it draws over the chat

            // Command suggestions: built-in commands matching what you type ("/" lists them all).
            // They run free and exactly, so you don't have to remember the wording.
            Rectangle {
                id: suggestions
                objectName: "commandSuggestions"
                property int index: -1
                property bool dismissed: false
                readonly property string typed: composer.text.replace(/^\s+/, "").toLowerCase()
                readonly property var matches: {
                    if (typed.indexOf("\n") >= 0) return []
                    if (typed.charAt(0) === "/") {
                        var query = typed.slice(1)
                        return root.commandCatalog.filter(function(item) {
                            return !query || item.c.toLowerCase().indexOf(query) >= 0 || (item.h || "").toLowerCase().indexOf(query) >= 0
                        }).slice(0, 8)
                    }
                    if (typed.length < 2) return []
                    return root.commandCatalog.filter(function(item) {
                        return item.c.toLowerCase().indexOf(typed) === 0 && item.c.toLowerCase() !== typed
                    }).slice(0, 5)
                }
                readonly property bool shown: composer.activeFocus && !dismissed && matches.length > 0
                function accept(i) {
                    var command = matches[i].c
                    composer.text = command
                    var placeholder = command.indexOf("NAME")
                    if (placeholder >= 0) composer.select(placeholder, placeholder + 4)
                    else composer.cursorPosition = command.length
                    index = -1
                    dismissed = command.slice(-1) !== " " && placeholder < 0
                    composer.forceActiveFocus()
                }
                visible: shown
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.top
                anchors.bottomMargin: 6
                height: suggestionList.contentHeight + 12
                radius: 12
                color: root.dark ? "#1b2533" : "#ffffff"
                border.color: root.line
                ListView {
                    id: suggestionList
                    anchors.fill: parent
                    anchors.margins: 6
                    interactive: false
                    model: suggestions.matches
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        width: suggestionList.width
                        height: 38
                        radius: 8
                        color: index === suggestions.index || pick.containsMouse ? Qt.alpha(root.accent, root.dark ? 0.16 : 0.10) : "transparent"
                        ColumnLayout {
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 10; anchors.rightMargin: 10
                            spacing: 0
                            Label { Layout.fillWidth: true; text: modelData.c; elide: Text.ElideRight; color: root.ink; font.pixelSize: 13 }
                            Label { Layout.fillWidth: true; visible: !!modelData.h; text: modelData.h || ""; elide: Text.ElideRight; color: root.muted; font.pixelSize: 11 }
                        }
                        MouseArea { id: pick; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: suggestions.accept(index) }
                    }
                }
            }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 6
                spacing: 2
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    TextArea {
                        id: composer
                        objectName: "composer"
                        placeholderText: ano.paused ? "Ano is paused" : voice.recording ? "Listening… release to send" : "Ask Ano…"
                        enabled: !ano.paused && (!ano.busy || ano.queueEnabled) && !voice.busy
                        wrapMode: TextEdit.Wrap
                        color: root.ink
                        selectByMouse: true
                        verticalAlignment: TextEdit.AlignVCenter
                        background: Item {}
                        Accessible.name: "Message"
                        onTextChanged: { suggestions.index = -1; suggestions.dismissed = false }
                        Keys.onReturnPressed: function(event) {
                            if (event.modifiers & Qt.ShiftModifier) event.accepted = false
                            else if (suggestions.shown && suggestions.index >= 0) { suggestions.accept(suggestions.index); event.accepted = true }
                            else { root.submit(); event.accepted = true }
                        }
                        Keys.onTabPressed: function(event) {
                            if (suggestions.shown) { suggestions.accept(Math.max(0, suggestions.index)); event.accepted = true }
                            else event.accepted = false
                        }
                        Keys.onDownPressed: function(event) {
                            if (suggestions.shown) { suggestions.index = Math.min(suggestions.matches.length - 1, suggestions.index + 1); event.accepted = true }
                            else event.accepted = false
                        }
                        Keys.onUpPressed: function(event) {
                            if (suggestions.shown && suggestions.index > -1) { suggestions.index = suggestions.index - 1; event.accepted = true }
                            else event.accepted = false
                        }
                        Keys.onEscapePressed: function(event) {
                            if (suggestions.shown) { suggestions.dismissed = true; event.accepted = true }
                            else event.accepted = false
                        }
                    }
                }
                IconButton {
                    id: talkButton
                    objectName: "pushToTalk"
                    glyph: "\uE720"
                    primary: voice.recording
                    tint: root.danger
                    enabled: voice.available && !ano.paused && !ano.busy && !activity.locked && !screenCapture.capturing && !screenCapture.ready
                    onPressed: voice.start()
                    onReleased: voice.stop()
                    onCanceled: voice.cancel()
                    ToolTip.text: voice.available ? "Hold to talk" : "Voice needs setup"
                    Accessible.name: "Hold to talk, release to send"
                }
                IconButton {
                    glyph: "\uE724"
                    primary: true
                    enabled: !ano.paused && (!ano.busy || ano.queueEnabled) && !voice.busy && composer.text.trim().length > 0
                    onClicked: root.submit()
                    ToolTip.text: ano.connected ? "Send" : "Save"
                    Accessible.name: ano.connected ? "Send message to Ano" : "Save message locally"
                }
            }
        }
        // Live browser-connection health, updated without any AI request. Tap for setup help.
        Rectangle {
            objectName: "browserChip"
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            Layout.topMargin: 2
            // Only real faults get the full-width bar; "waiting" (reconnecting after a restart) is a small chip below.
            visible: browserHealth.state === "port_busy" || browserHealth.state === "outdated"
            implicitHeight: chipRow.implicitHeight + 12
            radius: 10
            color: root.surface
            border.color: browserHealth.state === "port_busy" || browserHealth.state === "outdated" ? root.danger : root.line
            RowLayout {
                id: chipRow
                anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 10; anchors.rightMargin: 10
                spacing: 8
                Rectangle {
                    Layout.preferredWidth: 9; Layout.preferredHeight: 9; radius: 4.5
                    color: browserHealth.state === "connected" ? "#3fbf6f"
                           : browserHealth.state === "waiting" || browserHealth.state === "unconfigured" ? "#e0a93b"
                           : root.danger
                }
                Label { Layout.fillWidth: true; text: "Browser: " + browserHealth.summary; elide: Text.ElideRight; color: root.ink; font.pixelSize: 11 }
                Label { text: "Help"; color: root.accent; font.pixelSize: 11; font.bold: true }
            }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: browserHelp.open() }
        }
        // Status line, only when it says something the chips don't (voice, errors).
        Label {
            id: statusLine
            objectName: "statusLine"
            Layout.fillWidth: true
            Layout.leftMargin: 18
            Layout.rightMargin: 18
            visible: text.length > 0 && (ano.voiceConversationActive || voice.busy || (!ano.busy && !root.routineStatus(ano.status)))
            text: ano.voiceConversationActive ? ano.voiceConversationStatus : voice.busy ? voice.status : ano.status
            elide: Text.ElideRight
            color: voice.recording ? root.danger : root.muted
            font.pixelSize: 11
            MouseArea { id: statusHover; anchors.fill: parent; hoverEnabled: true }
            ToolTip.visible: statusHover.containsMouse && statusLine.truncated
            ToolTip.text: statusLine.text
            ToolTip.delay: 300
        }
        // Status chips: which brain answers, computer control, monitors, the Codex watch, queued tasks.
        // A Flow wraps onto a second line at narrow sizes; a row would force the whole panel wider than the window.
        Flow {
            objectName: "statusChips"
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            Layout.topMargin: 4
            Layout.bottomMargin: 10
            spacing: 6
            StatusChip {
                id: brainChip
                objectName: "brainChip"
                visible: ano.connected
                dot: ano.brain === "openai" ? "#a78bfa" : root.good
                text: ano.brain === "openai" ? "OpenAI" : "Local AI"
                tip: (ano.brain === "openai" ? "OpenAI brain: paid per message. Tap to switch." : "Local brain: free, on this PC. Tap to switch.")
                     + "\n" + ano.savingsSummary.replace("Today (UTC): ", "Saved today: ")
                onClicked: brainMenu.popup(brainChip, 0, -brainMenu.implicitHeight - 4)
            }
            StatusChip {
                id: controlChip
                objectName: "controlChip"
                enabled: desktopControl.available
                dot: !desktopControl.enabled ? root.muted : desktopControl.permissionMode === "roam" ? root.danger
                     : desktopControl.permissionMode === "full" ? root.warn : root.good
                text: !desktopControl.enabled ? "Control off"
                      : desktopControl.permissionMode === "roam" ? "Free roam"
                      : desktopControl.permissionMode === "full" ? "Full control" : "Control · approve"
                onClicked: controlMenu.popup(controlChip, 0, -controlMenu.implicitHeight - 4)
            }
            StatusChip {
                // "Monitoring on": one chip for monitors, scheduled typing and the Codex watch; opens the Watching panel.
                objectName: "watchChip"
                readonly property var items: ano.watchItems || []
                // Only rules really being checked right now count as "Watching".
                readonly property int live: items.filter(function(item) { return item.live !== false }).length
                readonly property string codex: typeof ano.codexWatch === "string" ? ano.codexWatch : ""
                visible: items.length > 0
                dot: ano.monitoringPaused || live === 0 ? root.muted : /limit|resume/i.test(codex) ? root.warn : root.accent
                text: ano.monitoringPaused ? "Paused · " + items.length
                      : live > 0 ? "Watching " + live : "Waiting · " + items.length
                tip: (ano.monitorStatus ? ano.monitorStatus + ". " : "") + (codex ? "Codex: " + codex + ". " : "") + "Tap to manage."
                onClicked: watchPanel.open()
            }
            StatusChip {
                objectName: "browserMiniChip"
                visible: browserHealth.state !== "connected" && browserHealth.state !== "port_busy" && browserHealth.state !== "outdated"
                dot: browserHealth.state === "waiting" || browserHealth.state === "unconfigured" ? root.warn : root.danger
                text: "Browser"
                tip: "Browser: " + browserHealth.summary + ". Tap for help."
                onClicked: browserHelp.open()
            }
            StatusChip {
                visible: ano.queuedCount > 0
                dot: root.accent
                text: ano.queuedCount + " queued"
                onClicked: taskPopup.open()
            }
        }
    }

    AnoMenu {
        id: brainMenu
        objectName: "brainMenu"
        AnoMenuItem {
            text: "Local AI · free, on this PC"
            glyph: ""
            checkable: true
            checked: ano.brain !== "openai"
            onTriggered: ano.setBrain("local")
        }
        AnoMenuItem {
            text: "OpenAI · paid per message"
            glyph: ""
            checkable: true
            checked: ano.brain === "openai"
            enabled: ano.brains.indexOf("openai") >= 0
            onTriggered: ano.setBrain("openai")
        }
    }
    AnoMenu {
        id: controlMenu
        objectName: "controlMenu"
        enabled: desktopControl.available && !ano.paused && !activity.locked && !savedLogins.private
        AnoMenuItem {
            text: "Approve each step"
            glyph: ""
            checkable: true
            checked: desktopControl.enabled && desktopControl.permissionMode === "reviewed"
            onTriggered: root.chooseControl("reviewed")
        }
        AnoMenuItem {
            text: "Full permission"
            hint: "this session"
            glyph: ""
            checkable: true
            checked: desktopControl.enabled && desktopControl.permissionMode === "full"
            onTriggered: root.chooseControl("full")
        }
        AnoMenuItem {
            text: "Free roam"
            hint: "this session"
            glyph: ""
            checkable: true
            checked: desktopControl.enabled && desktopControl.permissionMode === "roam"
            onTriggered: root.chooseControl("roam")
        }
        AnoMenuSeparator {}
        AnoMenuItem { text: "Stop control"; glyph: ""; danger: true; enabled: desktopControl.enabled; onTriggered: desktopControl.stop() }
    }

    AnoMenu {
        id: mainMenu
        objectName: "mainMenu"
        height: Math.min(implicitHeight, root.height - 12)
        // Header card: who is answering and how to summon Ano.
        Item {
            implicitWidth: 240
            implicitHeight: 54
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10; anchors.rightMargin: 10; anchors.bottomMargin: 4
                spacing: 10
                Image {
                    Layout.preferredWidth: 28; Layout.preferredHeight: 28
                    source: "../assets/ano-mark.png"; sourceSize: Qt.size(56, 56); fillMode: Image.PreserveAspectFit; smooth: true
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Label { text: "Ano " + ano.appVersion; color: root.ink; font.pixelSize: 13; font.weight: Font.DemiBold }
                    Label {
                        Layout.fillWidth: true
                        text: (ano.brain === "openai" ? "OpenAI brain" : "Local brain") + " · Ctrl+Alt+A"
                        elide: Text.ElideRight
                        color: root.muted
                        font.pixelSize: 11
                    }
                }
            }
        }
        AnoMenuSeparator {}
        AnoMenuItem {
            text: ano.voiceConversationActive ? "Stop voice conversation" : "Voice conversation"
            glyph: ""
            shown: ano.voiceConversationActive || ano.voiceConversationAvailable
            enabled: ano.voiceConversationActive || (ano.voiceConversationAvailable && !ano.busy && !ano.paused && !voice.busy)
            onTriggered: ano.toggleVoiceConversation()
        }
        AnoMenuItem {
            objectName: "explainScreen"
            text: screenCapture.capturing ? "Capturing…" : "Explain my screen"
            glyph: ""
            enabled: !ano.paused && !ano.busy && !voice.busy && !activity.locked && !screenCapture.capturing && !screenCapture.ready
            onTriggered: {
                captureQuestion.text = composer.text.trim() || "Ano, what am I looking at?"
                screenCapture.begin()
            }
        }
        AnoMenuItem { text: "Search messages"; glyph: ""; onTriggered: { search.visible = true; search.forceActiveFocus() } }
        AnoMenuItem { objectName: "localCommandLibrary"; text: "Local commands"; hint: "free"; glyph: ""; enabled: !ano.busy && !ano.paused; onTriggered: ano.send("show commands") }
        AnoMenuSeparator { shown: ano.busy || ano.queueEnabled }
        AnoMenuItem { text: "Stop current task"; glyph: ""; danger: true; shown: ano.busy; onTriggered: ano.stopTask() }
        AnoMenuItem { text: "Tasks"; hint: ano.queuedCount ? ano.queuedCount + " queued" : ""; glyph: ""; shown: ano.queueEnabled; onTriggered: taskPopup.open() }
        AnoMenuSeparator {}
        AnoMenuItem { objectName: "openActivity"; text: "Activity"; glyph: ""; onTriggered: activityTimeline.open() }
        AnoMenuItem { objectName: "openFiles"; text: "Files Ano can read"; glyph: ""; onTriggered: filesPopup.open() }
        AnoMenuItem { objectName: "openPasswords"; text: "Passwords"; glyph: ""; onTriggered: controlPanels.openVault() }
        AnoMenuItem { objectName: "openSettings"; text: "Settings"; glyph: ""; onTriggered: settingsPanel.open() }
        AnoMenuSeparator {}
        AnoMenuItem { text: ano.paused ? "Resume Ano" : "Pause Ano"; glyph: ano.paused ? "" : ""; onTriggered: ano.togglePause() }
        AnoMenuItem { objectName: "quitAno"; text: "Quit Ano"; glyph: ""; danger: true; onTriggered: ano.requestQuit() }
    }

    Popup {
        id: settingsPanel
        objectName: "settingsPanel"
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: root.height - 40
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 0
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: root.bg; radius: 14; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 18; Layout.rightMargin: 8; Layout.topMargin: 10; Layout.bottomMargin: 4
                Label { Layout.fillWidth: true; text: "Settings"; font.pixelSize: 18; font.bold: true; color: root.ink }
                IconButton { glyph: ""; onClicked: settingsPanel.close(); ToolTip.text: "Close"; Accessible.name: "Close settings" }
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: root.line }
            ScrollView {
                id: settingsScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: settingsScroll.availableWidth
                    spacing: 6

                    SettingsHeading { text: "Brain" }
                    ChoiceCard {
                        objectName: "brainLocal"
                        title: "Local AI"
                        detail: "Free and private: runs on this PC. Slower and simpler."
                        dot: root.good
                        selected: ano.brain !== "openai"
                        onClicked: ano.setBrain("local")
                    }
                    ChoiceCard {
                        objectName: "brainOpenAI"
                        title: "OpenAI"
                        detail: "Smarter and faster; costs a few cents per message. Start a message with local: to use the free brain once."
                        dot: "#a78bfa"
                        enabled: ano.brains.indexOf("openai") >= 0
                        selected: ano.brain === "openai"
                        onClicked: ano.setBrain("openai")
                    }
                    RowLayout {
                        objectName: "spendingRow"
                        Layout.fillWidth: true
                        Layout.leftMargin: 18; Layout.rightMargin: 14
                        Text { text: ""; font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: 14; color: root.muted }
                        Label {
                            Layout.fillWidth: true
                            text: ano.spending ? "OpenAI spending: " + ano.spending : "No OpenAI spending recorded yet."
                            wrapMode: Text.Wrap
                            color: root.muted
                            font.pixelSize: 12
                        }
                        Label {
                            text: "Details"
                            color: root.accent
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { settingsPanel.close(); ano.send("paid usage") } }
                        }
                    }
                    // What the free local brain took off OpenAI's plate today.
                    RowLayout {
                        objectName: "savingsRow"
                        Layout.fillWidth: true
                        Layout.leftMargin: 18; Layout.rightMargin: 14
                        Text { text: ""; font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: 14; color: root.good }
                        Label {
                            Layout.fillWidth: true
                            text: ano.savingsSummary.indexOf("No route") === 0 ? "No free-brain savings recorded yet."
                                  : ano.savingsSummary.replace("Today (UTC): ", "Saved today: ")
                            wrapMode: Text.Wrap
                            color: root.muted
                            font.pixelSize: 12
                        }
                    }

                    SettingsHeading { text: "Computer control" }
                    Label {
                        Layout.fillWidth: true; Layout.leftMargin: 18; Layout.rightMargin: 18
                        visible: !desktopControl.available
                        text: "Computer control needs the optional control packages (Pillow and uiautomation)."
                        wrapMode: Text.Wrap; color: root.muted; font.pixelSize: 12
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        enabled: desktopControl.available && !ano.paused && !activity.locked && !savedLogins.private
                        ChoiceCard {
                            title: "Off"
                            detail: "Ano can't click, type or read app windows."
                            dot: root.muted
                            selected: !desktopControl.enabled
                            onClicked: root.chooseControl("off")
                        }
                        Label {
                            Layout.fillWidth: true; Layout.leftMargin: 18; Layout.rightMargin: 18
                            visible: controlPrefs.keepOn
                            text: "Kept on: Ano switches control back on at startup, after you unlock, and after Pause. Esc stops it any time."
                            wrapMode: Text.Wrap; color: root.muted; font.pixelSize: 12
                        }
                        ChoiceCard {
                            objectName: "reviewedControlMode"
                            title: "Approve each step"
                            detail: "You see and approve every click and keystroke."
                            dot: root.good
                            selected: desktopControl.enabled && desktopControl.permissionMode === "reviewed"
                            onClicked: root.chooseControl("reviewed")
                        }
                        ChoiceCard {
                            objectName: "fullControlMode"
                            title: "Full permission"
                            detail: "Acts without asking. Esc or Stop control stops it at once."
                            dot: root.warn
                            selected: desktopControl.enabled && desktopControl.permissionMode === "full"
                            onClicked: root.chooseControl("full")
                        }
                        ChoiceCard {
                            objectName: "freeRoamControlMode"
                            title: "Free roam"
                            detail: "No step limits; can use terminals and every local drive. Use only while you're watching. After a restart it comes back as Full permission."
                            dot: root.danger
                            selected: desktopControl.enabled && desktopControl.permissionMode === "roam"
                            onClicked: root.chooseControl("roam")
                        }
                    }

                    SettingsHeading { text: "Voice" }
                    SwitchRow {
                        objectName: "spokenReplies"
                        title: "Speak replies"
                        detail: speech.available ? "Ano reads its answers aloud." : "No speech voice is available on this PC."
                        enabled: speech.available
                        checked: speech.enabled
                        onClicked: speech.setEnabled(!speech.enabled)
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 18; Layout.rightMargin: 14
                        visible: speech.voices.length > 0
                        Label { Layout.fillWidth: true; text: "Voice"; color: root.ink }
                        ComboBox {
                            objectName: "voicePicker"
                            Layout.preferredWidth: 160
                            model: speech.voices.map(function(name) { return name.charAt(0).toUpperCase() + name.slice(1) })
                            currentIndex: speech.voices.indexOf(speech.voice)
                            onActivated: function(index) { speech.setVoice(speech.voices[index]) }
                            Accessible.name: "Speaking voice"
                        }
                    }

                    SettingsHeading { text: "Window size" }
                    RowLayout {
                        objectName: "sizePresets"
                        Layout.fillWidth: true
                        Layout.leftMargin: 14; Layout.rightMargin: 14
                        spacing: 6
                        Repeater {
                            model: [{ key: "compact", label: "Compact" }, { key: "standard", label: "Standard" }, { key: "large", label: "Large" }]
                            delegate: Rectangle {
                                required property var modelData
                                readonly property bool selected: root.sizePreset === modelData.key
                                objectName: "size_" + modelData.key
                                signal clicked()
                                onClicked: root.applySize(modelData.key)
                                Layout.fillWidth: true
                                implicitHeight: 54
                                radius: 10
                                color: selected ? Qt.alpha(root.accent, root.dark ? 0.16 : 0.10) : sizeArea.containsMouse ? root.surface : "transparent"
                                border.color: selected ? Qt.alpha(root.accent, 0.6) : root.line
                                Accessible.role: Accessible.RadioButton
                                Accessible.name: modelData.label + " window"
                                Accessible.checked: selected
                                Accessible.onPressAction: clicked()
                                ColumnLayout {
                                    anchors.centerIn: parent
                                    spacing: 4
                                    // A tiny drawing of the panel at this size.
                                    Rectangle {
                                        Layout.alignment: Qt.AlignHCenter
                                        width: root.sizePresets[modelData.key][0] / 26
                                        height: root.sizePresets[modelData.key][1] / 26
                                        radius: 3
                                        color: "transparent"
                                        border.width: 1.5
                                        border.color: selected ? root.accent : root.muted
                                    }
                                    Label { Layout.alignment: Qt.AlignHCenter; text: modelData.label; font.pixelSize: 11; color: selected ? root.ink : root.muted }
                                }
                                MouseArea { id: sizeArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: parent.clicked() }
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true; Layout.leftMargin: 18; Layout.rightMargin: 18
                        text: (root.sizePreset === "custom" ? "Custom size · " : "") + "Drag any edge to resize; double-click the header for compact."
                        wrapMode: Text.Wrap; color: root.muted; font.pixelSize: 11
                    }

                    SettingsHeading { text: "Appearance" }
                    RowLayout {
                        Layout.leftMargin: 18
                        spacing: 12
                        Swatch { objectName: "theme_aurora"; label: "Blue"; color: root.dark ? "#93b9fa" : "#315fbb"; selected: ano.theme === "aurora"; onClicked: ano.setTheme("aurora") }
                        Swatch { objectName: "theme_nebula"; label: "Purple"; color: root.dark ? "#b9a4ff" : "#6d4ae0"; selected: ano.theme === "nebula"; onClicked: ano.setTheme("nebula") }
                        Swatch { objectName: "theme_solar"; label: "Amber"; color: root.dark ? "#f2c27f" : "#b06a00"; selected: ano.theme === "solar"; onClicked: ano.setTheme("solar") }
                    }
                    SwitchRow {
                        objectName: "floatToggle"
                        title: "Floating icon"
                        detail: "Show Ano's face on the desktop when the chat is closed."
                        checked: ano.floating
                        onClicked: ano.setFloating(!ano.floating)
                    }
                    SwitchRow {
                        objectName: "livelyToggle"
                        title: "Lively Ano"
                        detail: "When a task finishes while you're in another app, Ano does a quick little flight around its spot to tell you. The floating icon also hops now and then."
                        checked: livelyPrefs.lively
                        onClicked: livelyPrefs.lively = !livelyPrefs.lively
                    }
                    AnoButton {
                        objectName: "previewFlight"
                        Layout.leftMargin: 18
                        text: "Show me"
                        enabled: livelyPrefs.lively && !root.flying && !ano.paused
                        onClicked: { settingsPanel.close(); root.flyAround("Hi! This is how I'll find you") }
                    }

                    SettingsHeading { text: "Apps & plugins" }
                    LinkRow { objectName: "appsLink"; title: "Apps, extensions & plugins"; glyph: ""; onClicked: { settingsPanel.close(); appsPanel.open() } }

                    SettingsHeading { text: "Health check" }
                    LinkRow { objectName: "challengesLink"; title: "Challenge results"; glyph: ""; onClicked: { settingsPanel.close(); challengesPanel.open() } }

                    SettingsHeading { text: "Privacy & data" }
                    LinkRow { title: "Files Ano can read"; glyph: ""; onClicked: { settingsPanel.close(); filesPopup.open() } }
                    LinkRow { title: "Passwords"; glyph: ""; onClicked: { settingsPanel.close(); controlPanels.openVault() } }
                    LinkRow { title: "Activity timeline"; glyph: ""; onClicked: { settingsPanel.close(); activityTimeline.open() } }

                    SettingsHeading { text: "About" }
                    Label {
                        Layout.fillWidth: true; Layout.leftMargin: 18; Layout.rightMargin: 18; Layout.bottomMargin: 18
                        text: "Ano " + ano.appVersion + " · Ctrl+Alt+A shows or hides Ano."
                        wrapMode: Text.Wrap; color: root.muted; font.pixelSize: 12
                    }
                }
            }
        }
    }

    // The newest challenge result for each area, with a Re-run button per area.
    Popup {
        id: challengesPanel
        objectName: "challengesPanel"
        property string expanded: ""
        readonly property var rows: ano.challengeSummary || []
        readonly property int passed: rows.reduce(function(sum, row) { return sum + row.passed }, 0)
        readonly property int total: rows.reduce(function(sum, row) { return sum + row.total }, 0)
        function rerun(command) { close(); ano.send(command) }
        onOpened: ano.refreshChallenges()
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: root.height - 40
        modal: true
        focus: true
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 16
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: root.bg; radius: 14; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Label { text: "Challenge results"; font.pixelSize: 18; font.bold: true; color: root.ink }
                    Label {
                        text: challengesPanel.total ? challengesPanel.passed + " of " + challengesPanel.total + " passed, newest run per area"
                                                    : "No challenge run yet"
                        color: root.muted; font.pixelSize: 12
                    }
                }
                Item { Layout.fillWidth: true }
                IconButton { glyph: ""; onClicked: challengesPanel.close(); ToolTip.text: "Close"; Accessible.name: "Close challenge results" }
            }
            ListView {
                id: challengeList
                objectName: "challengeList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: challengesPanel.rows
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    id: areaCard
                    required property var modelData
                    readonly property bool open: challengesPanel.expanded === modelData.area
                    readonly property color tone: !modelData.results.length ? root.muted
                                                  : modelData.passed === modelData.total && modelData.total > 0 ? root.good
                                                  : modelData.passed === 0 && modelData.total > 0 ? root.danger : root.warn
                    width: challengeList.width - 8
                    implicitHeight: areaColumn.implicitHeight + 16
                    radius: 10
                    color: root.surface
                    border.color: root.line
                    ColumnLayout {
                        id: areaColumn
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 8; anchors.leftMargin: 12
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Rectangle { width: 9; height: 9; radius: 5; color: areaCard.tone }
                            Label {
                                Layout.fillWidth: true
                                text: modelData.area.charAt(0).toUpperCase() + modelData.area.slice(1)
                                color: root.ink; font.weight: Font.DemiBold
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                            onClicked: challengesPanel.expanded = areaCard.open ? "" : modelData.area }
                            }
                            Label {
                                text: !modelData.results.length ? "not run"
                                      : modelData.total ? modelData.passed + "/" + modelData.total + (modelData.needs_you ? " · needs you" : "")
                                      : "needs you"
                                color: areaCard.tone; font.pixelSize: 12
                            }
                            AnoButton {
                                text: "Re-run"
                                enabled: !ano.busy && !ano.paused
                                onClicked: challengesPanel.rerun("run challenge tests " + modelData.area)
                            }
                        }
                        Repeater {
                            model: areaCard.open ? modelData.results : []
                            delegate: ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 1
                                Label {
                                    Layout.fillWidth: true
                                    text: (modelData.status === "passed" ? "Passed · " : modelData.status === "fixed on retry" ? "Fixed on try " + modelData.attempts + " · "
                                           : modelData.status === "needs you" ? "Needs you · " : "Failed · ") + modelData.title
                                    wrapMode: Text.Wrap; color: root.ink; font.pixelSize: 12
                                }
                                Label {
                                    Layout.fillWidth: true
                                    visible: !!modelData.why && modelData.status !== "passed"
                                    text: modelData.why || ""
                                    wrapMode: Text.Wrap; color: root.muted; font.pixelSize: 11
                                }
                            }
                        }
                        Label {
                            visible: areaCard.open && !!modelData.when
                            text: "Run " + (modelData.when || "").replace("T", " ")
                            color: root.muted; font.pixelSize: 11
                        }
                    }
                }
            }
            AnoButton {
                Layout.fillWidth: true
                primary: true
                text: "Run all challenge tests"
                enabled: !ano.busy && !ano.paused
                onClicked: challengesPanel.rerun("run challenge tests")
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap; color: root.muted; font.pixelSize: 11
                text: "Each test is a real task checked against its actual result. On the OpenAI brain a full run costs under a dollar."
            }
        }
    }

    // What is on this PC (names only, read locally): tap an app to start a rule for it.
    Popup {
        id: appsPanel
        objectName: "appsPanel"
        property string section: "apps"
        property string pickedApp: ""
        readonly property var sections: [
            { key: "apps", label: "Apps" }, { key: "running", label: "Running" },
            { key: "extensions", label: "Extensions" }, { key: "plugins", label: "Ano" }]
        readonly property var rows: {
            var all = (ano.inventory[section] || []).map(function(item) {
                return typeof item === "string" ? { name: item, detail: "" } : { name: item.name, detail: item.browser }
            })
            var query = appSearch.text.trim().toLowerCase()
            return query ? all.filter(function(row) { return row.name.toLowerCase().indexOf(query) >= 0 }) : all
        }
        // Starts the sentence in the composer; you read it and press Enter yourself.
        function startRule(text) {
            close()
            composer.text = text
            composer.forceActiveFocus()
            composer.cursorPosition = composer.text.length
        }
        onOpened: if (!ano.inventory.apps || !ano.inventory.apps.length) ano.refreshInventory()
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: root.height - 40
        modal: true
        focus: true
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 16
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: root.bg; radius: 14; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: "Apps & plugins"; font.pixelSize: 18; font.bold: true; color: root.ink }
                IconButton {
                    glyph: ""
                    enabled: !ano.inventory.loading
                    onClicked: ano.refreshInventory()
                    ToolTip.text: "Refresh"
                    Accessible.name: "Refresh the app list"
                }
                IconButton { glyph: ""; onClicked: appsPanel.close(); ToolTip.text: "Close"; Accessible.name: "Close apps panel" }
            }
            Flow {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: appsPanel.sections
                    delegate: Rectangle {
                        required property var modelData
                        readonly property bool chosen: appsPanel.section === modelData.key
                        width: sectionLabel.implicitWidth + 22
                        height: 30
                        radius: 15
                        color: chosen ? Qt.alpha(root.accent, root.dark ? 0.22 : 0.14) : root.surface
                        border.color: chosen ? Qt.alpha(root.accent, 0.6) : root.line
                        Accessible.role: Accessible.Button
                        Accessible.name: modelData.label
                        Label {
                            id: sectionLabel
                            anchors.centerIn: parent
                            text: modelData.label + " " + (ano.inventory[modelData.key] || []).length
                            color: parent.chosen ? root.accent : root.ink
                            font.pixelSize: 12
                        }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: appsPanel.section = modelData.key }
                    }
                }
            }
            TextField {
                id: appSearch
                Layout.fillWidth: true
                placeholderText: "Search"
                color: root.ink
                placeholderTextColor: root.muted
                background: Rectangle { radius: 9; color: root.surface; border.color: appSearch.activeFocus ? root.accent : root.line }
            }
            ListView {
                id: appList
                objectName: "appList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 4
                model: appsPanel.rows
                ScrollBar.vertical: ScrollBar {}
                Label {
                    anchors.centerIn: parent
                    width: parent.width - 20
                    visible: appList.count === 0
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    color: root.muted
                    text: ano.inventory.loading ? "Looking at what is installed…"
                          : appSearch.text ? "Nothing matches “" + appSearch.text + "”."
                          : appsPanel.section === "extensions" ? "No Chrome, Brave or Edge extensions found." : "Nothing here yet."
                }
                delegate: Rectangle {
                    required property var modelData
                    readonly property bool tappable: appsPanel.section === "apps" || appsPanel.section === "running"
                    width: appList.width - 8
                    height: 40
                    radius: 10
                    color: rowArea.containsMouse && tappable ? root.surface : "transparent"
                    Accessible.role: tappable ? Accessible.Button : Accessible.StaticText
                    Accessible.name: modelData.name
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
                        spacing: 10
                        Text {
                            text: appsPanel.section === "extensions" ? "" : appsPanel.section === "plugins" ? "" : ""
                            font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: 15; color: root.muted
                        }
                        Label { Layout.fillWidth: true; text: modelData.name; textFormat: Text.PlainText; elide: Text.ElideRight; color: root.ink }
                        Label { visible: !!modelData.detail; text: modelData.detail; color: root.muted; font.pixelSize: 12 }
                        Text {
                            visible: parent.parent.tappable
                            text: ""; font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: 11; color: root.muted
                        }
                    }
                    MouseArea {
                        id: rowArea
                        anchors.fill: parent
                        enabled: parent.tappable
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { appsPanel.pickedApp = modelData.name; appRuleMenu.popup(parent, 36, parent.height - 2) }
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: root.muted
                font.pixelSize: 11
                text: "Read on this PC only, names only. Tap an app to start a rule; you check it and press Enter."
            }
        }
        AnoMenu {
            id: appRuleMenu
            objectName: "appRuleMenu"
            readonly property string app: appsPanel.pickedApp.toLowerCase()
            AnoMenuItem { glyph: ""; text: "Tell me when it notifies me"; onTriggered: appsPanel.startRule("when I get a notification from " + appRuleMenu.app + ", tell me every time") }
            AnoMenuItem { glyph: ""; text: "Read its notifications to me"; onTriggered: appsPanel.startRule("when I get a notification from " + appRuleMenu.app + ", read it to me every time") }
            AnoMenuItem { glyph: ""; text: "Tell me when it opens"; onTriggered: appsPanel.startRule("when " + appRuleMenu.app + " opens, tell me") }
            AnoMenuItem { glyph: ""; text: "Tell me when it closes"; onTriggered: appsPanel.startRule("when " + appRuleMenu.app + " closes, tell me") }
        }
    }

    // Everything Ano is watching or waiting to do, each with a stop button, and one switch to pause monitors.
    Popup {
        id: watchPanel
        objectName: "watchPanel"
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: root.height - 40
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 16
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: root.bg; radius: 14; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: "Watching"; font.pixelSize: 18; font.bold: true; color: root.ink }
                IconButton { glyph: ""; onClicked: watchPanel.close(); ToolTip.text: "Close"; Accessible.name: "Close watching panel" }
            }
            SwitchRow {
                objectName: "monitoringSwitch"
                Layout.leftMargin: 2; Layout.rightMargin: 0
                title: "Monitoring"
                detail: ano.monitoringPaused ? "Paused: nothing is being watched." : "On: Ano checks your rules every few seconds. No AI or credits used."
                checked: !ano.monitoringPaused
                enabled: !ano.busy && !ano.paused
                onClicked: ano.send(ano.monitoringPaused ? "resume monitoring" : "pause monitoring")
            }
            ListView {
                id: watchList
                objectName: "watchList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: ano.watchItems
                ScrollBar.vertical: ScrollBar {}
                // Nothing yet: tappable examples that start the sentence for you.
                ColumnLayout {
                    anchors.centerIn: parent
                    width: parent.width - 20
                    visible: watchList.count === 0
                    spacing: 8
                    Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; text: "Nothing is being watched."; color: root.ink }
                    Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; color: root.muted; font.pixelSize: 12
                            text: "Tell Ano what to watch for, in a sentence starting with “when”." }
                    Repeater {
                        model: ["when claude is done, tell me and fly around", "when my download finishes, open it", "when codex is out of usage, resume it after the reset"]
                        delegate: AnoButton {
                            required property string modelData
                            Layout.alignment: Qt.AlignHCenter
                            text: modelData
                            onClicked: { watchPanel.close(); composer.text = modelData; composer.forceActiveFocus(); composer.cursorPosition = composer.text.length }
                        }
                    }
                }
                delegate: Rectangle {
                    required property var modelData
                    width: watchList.width - 8
                    implicitHeight: watchRow.implicitHeight + 16
                    radius: 10
                    color: root.surface
                    border.color: root.line
                    RowLayout {
                        id: watchRow
                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 12; anchors.rightMargin: 4
                        spacing: 10
                        Text {
                            text: modelData.kind === "monitor" ? "" : modelData.kind === "scheduled" ? "" : ""
                            font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: 15; color: root.accent
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Label { Layout.fillWidth: true; text: modelData.title; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: root.ink }
                            Label { Layout.fillWidth: true; text: modelData.detail; textFormat: Text.PlainText; color: root.muted; font.pixelSize: 12 }
                        }
                        IconButton {
                            glyph: ""
                            enabled: !ano.busy && !ano.paused
                            ToolTip.text: "Stop this"
                            Accessible.name: "Stop " + modelData.title
                            onClicked: ano.send(modelData.kind === "monitor" ? "stop monitor " + modelData.id
                                                : modelData.kind === "scheduled" ? "cancel scheduled action " + modelData.id
                                                : "stop watching codex")
                        }
                    }
                }
            }
            AnoButton {
                Layout.fillWidth: true
                text: "Watch for something new…"
                primary: true
                onClicked: { watchPanel.close(); composer.text = "when "; composer.forceActiveFocus(); composer.cursorPosition = composer.text.length }
            }
        }
    }

    Popup {
        id: taskPopup
        objectName: "taskPopup"
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: root.height - 40
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 16
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: root.bg; radius: 14; border.color: root.line }
        contentItem: ColumnLayout {
            Label { text: "Tasks · " + ano.queuedCount + " waiting"; color: root.ink; font.bold: true }
            Label { Layout.fillWidth: true; text: "Answered means a reply, not verified execution. Resume is available when no changing action was dispatched. Other interrupted tasks need inspection."; wrapMode: Text.WordWrap; color: root.muted }
            ListView {
                id: tasksList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 8
                model: ano.taskItems
                ScrollBar.vertical: ScrollBar {}
                delegate: RowLayout {
                    required property var modelData
                    width: tasksList.width - 8
                    ColumnLayout {
                        Layout.fillWidth: true
                        Label { Layout.fillWidth: true; text: modelData.goal; textFormat: Text.PlainText; elide: Text.ElideRight; color: root.ink }
                        Label { text: modelData.state + " · Chat " + modelData.chat_id; color: root.muted }
                        Label { Layout.fillWidth: true; visible: modelData.state === "paused"; text: modelData.recovery_note || ""; wrapMode: Text.WordWrap; color: root.muted; font.pixelSize: 11 }
                    }
                    AnoButton {
                        text: "Resume"
                        visible: modelData.state === "paused" && modelData.can_resume === true
                        onClicked: ano.resumeTask(modelData.id)
                    }
                    AnoButton {
                        text: "Cancel"
                        visible: ["queued", "running", "paused"].indexOf(modelData.state) >= 0
                        onClicked: ano.cancelTask(modelData.id)
                    }
                }
            }
            AnoButton { text: "Close"; onClicked: taskPopup.close() }
        }
    }
    Popup {
        id: chatsPopup
        objectName: "chatsPopup"
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: root.height - 40
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 16
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: root.bg; radius: 14; border.color: root.line }
        onOpened: { chatSearch.text = ""; chatSearch.forceActiveFocus() }
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Label { Layout.fillWidth: true; text: "Your chats"; font.pixelSize: 18; font.bold: true; color: root.ink }
                AnoButton {
                    text: "New chat"
                    primary: true
                    enabled: !ano.busy || ano.queueEnabled
                    onClicked: { ano.newChat(); chatsPopup.close() }
                }
            }
            TextField {
                id: chatSearch
                objectName: "chatSearch"
                Layout.fillWidth: true
                placeholderText: "Search chats\u2026"
                color: root.ink
                leftPadding: 32
                background: Field {
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: 11; anchors.verticalCenter: parent.verticalCenter
                        text: "\uE721"; font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"; font.pixelSize: 13; color: root.muted
                    }
                }
                Keys.onEscapePressed: { if (text.length) text = ""; else chatsPopup.close() }
                Accessible.name: "Search chats by title"
            }
            ListView {
                id: chatList
                objectName: "chatList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 2
                model: root.chatRows(ano.chats, chatSearch.text)
                ScrollBar.vertical: ScrollBar {}
                Label {
                    anchors.centerIn: parent
                    visible: chatList.count === 0
                    text: chatSearch.text.length ? "No chats match \u201C" + chatSearch.text + "\u201D." : "No chats yet."
                    color: root.muted
                }
                delegate: Item {
                    required property var modelData
                    readonly property bool current: !modelData.header && modelData.id === ano.currentChat
                    width: chatList.width - 8
                    implicitHeight: modelData.header ? 30 : 46
                    Label {
                        visible: modelData.header
                        anchors.left: parent.left; anchors.leftMargin: 6; anchors.bottom: parent.bottom; anchors.bottomMargin: 5
                        text: modelData.header ? modelData.label : ""
                        font.pixelSize: 11; font.weight: Font.DemiBold; font.capitalization: Font.AllUppercase
                        color: root.muted
                    }
                    Rectangle {
                        visible: !modelData.header
                        anchors.fill: parent
                        radius: 10
                        color: current ? Qt.alpha(root.accent, 0.16) : chatArea.containsMouse ? root.surface : "transparent"
                        MouseArea {
                            id: chatArea
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: { search.text = ""; ano.openChat(modelData.id); chatsPopup.close() }
                        }
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 4
                            spacing: 8
                            Label {
                                Layout.fillWidth: true
                                text: modelData.header ? "" : modelData.title
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: root.ink
                                font.weight: current ? Font.DemiBold : Font.Normal
                            }
                            Label { text: modelData.header ? "" : modelData.time; font.pixelSize: 11; color: root.muted }
                            IconButton {
                                glyph: "\uE74D"
                                implicitWidth: 30; implicitHeight: 30
                                opacity: chatArea.containsMouse || hovered || current ? 1 : 0
                                enabled: !modelData.header && !(ano.busy && current)
                                onClicked: ano.deleteChat(modelData.id)
                                ToolTip.text: "Delete chat"
                                Accessible.name: "Delete chat " + (modelData.title || "")
                            }
                        }
                    }
                }
            }
        }
    }
    Popup {
        id: filesPopup
        objectName: "filesPopup"
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: Math.min(root.height - 40, 480)
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 18
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: root.bg; radius: 12; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 12
            Label { text: "Files Ano can read"; font.pixelSize: 18; font.weight: Font.DemiBold; color: root.ink }
            Label {
                Layout.fillWidth: true
                text: desktopControl.freeRoam
                      ? "Free roam is active: Ano can search, read and open files across accessible local drives. Text it reads is sent to the selected AI model. Stopping control restores your folder list below."
                      : "When you ask, Ano can search, read and open files in these folders without asking first. Text it reads is sent to the selected AI model."
                wrapMode: Text.Wrap
                color: root.muted
            }
            ListView {
                id: folderList
                objectName: "folderList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: files.folders
                ScrollBar.vertical: ScrollBar {}
                Label {
                    anchors.centerIn: parent
                    visible: folderList.count === 0
                    text: "No folders. Ano cannot read any files."
                    color: root.muted
                }
                delegate: Rectangle {
                    required property string modelData
                    width: folderList.width - 8
                    implicitHeight: folderRow.implicitHeight + 12
                    radius: 8
                    color: root.surface
                    RowLayout {
                        id: folderRow
                        anchors.fill: parent
                        anchors.margins: 6
                        anchors.leftMargin: 10
                        Label { Layout.fillWidth: true; text: modelData; textFormat: Text.PlainText; elide: Text.ElideMiddle; color: root.ink }
                        AnoButton { text: "Remove"; onClicked: files.removeFolder(modelData); Accessible.name: "Stop Ano reading " + modelData }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AnoButton { text: "Add folder…"; onClicked: folderDialog.open() }
                Item { Layout.fillWidth: true }
                AnoButton { text: "Done"; primary: true; onClicked: filesPopup.close() }
            }
        }
    }
    FolderDialog {
        id: folderDialog
        title: "Choose a folder Ano can read"
        onAccepted: files.addFolder(selectedFolder.toString())
    }

    Popup {
        id: activityTimeline
        objectName: "activityTimeline"
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: root.height - 40
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 18
        closePolicy: Popup.CloseOnEscape
        background: Rectangle { color: root.bg; radius: 12; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Label { text: "Activity"; font.pixelSize: 18; font.weight: Font.DemiBold; color: root.ink }
                Item { Layout.fillWidth: true }
                Switch {
                    objectName: "activitySwitch"
                    checked: activity.enabled
                    text: activity.enabled ? "On" : "Off"
                    onToggled: activity.setEnabled(checked)
                    Accessible.name: "Enable local activity tracking"
                }
            }
            Label {
                Layout.fillWidth: true
                text: activity.status + "\nApp names and window titles only, stored on this PC. No screenshots."
                wrapMode: Text.Wrap
                color: root.muted
            }
            TextField {
                objectName: "activitySearch"
                Layout.fillWidth: true
                placeholderText: "Search apps and window titles…"
                onTextChanged: activity.search(text)
                color: root.ink
                background: Field {}
                Accessible.name: "Search activity timeline"
            }
            ListView {
                id: activityList
                objectName: "activityList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: activity.entries
                ScrollBar.vertical: ScrollBar {}
                Label {
                    anchors.centerIn: parent
                    visible: activityList.count === 0
                    text: "No recorded activity to show."
                    color: root.muted
                }
                delegate: Rectangle {
                    required property var modelData
                    width: activityList.width - 8
                    implicitHeight: activityItem.implicitHeight + 16
                    radius: 8
                    color: root.surface
                    RowLayout {
                        id: activityItem
                        anchors.fill: parent
                        anchors.margins: 8
                        anchors.leftMargin: 10
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label { Layout.fillWidth: true; text: modelData.app + " · " + modelData.title; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: root.ink }
                            Label { Layout.fillWidth: true; text: modelData.when + " · " + modelData.duration; font.pixelSize: 11; color: root.muted }
                        }
                        AnoButton { objectName: "deleteActivityEntry"; text: "Delete"; onClicked: activity.deleteEntry(modelData.id); Accessible.name: "Delete activity entry" }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AnoButton { objectName: "deleteAllActivity"; text: "Delete all"; onClicked: deleteActivityConfirm.open() }
                Item { Layout.fillWidth: true }
                AnoButton {
                    text: "What was I doing?"
                    enabled: !ano.paused && !ano.busy && !voice.busy
                    onClicked: { activityTimeline.close(); search.text = ""; ano.recallActivity() }
                }
                AnoButton { text: "Close"; primary: true; onClicked: activityTimeline.close() }
            }
        }
    }
    Dialog {
        id: deleteActivityConfirm
        objectName: "deleteActivityConfirm"
        anchors.centerIn: Overlay.overlay
        width: root.width - 48
        title: "Delete all activity?"
        modal: true
        standardButtons: Dialog.Yes | Dialog.Cancel
        onAccepted: activity.deleteAll()
        contentItem: Label {
            text: "This removes the local activity timeline. Previous chat answers stay in your history."
            wrapMode: Text.Wrap
            color: root.ink
        }
        background: Rectangle { color: root.bg; radius: 12; border.color: root.line }
    }

    Connections {
        target: voice
        function onTranscript(text) {
            // The session handles every turn-mode transcript, including the
            // spoken stop command which deactivates it before this signal runs.
            if (voice.turnMode) return
            // Spoken commands send on release; anything already typed goes first.
            composer.text = composer.text.trim() ? composer.text.trim() + " " + text : text
            root.submit()
        }
    }
    Connections {
        target: screenCapture
        function onChanged() {
            if (screenCapture.ready) capturePreview.open()
            else capturePreview.close()
        }
    }
    Popup {
        id: capturePreview
        objectName: "capturePreview"
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: root.height - 40
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 18
        closePolicy: Popup.CloseOnEscape
        onClosed: { if (screenCapture.ready) screenCapture.cancel() }
        background: Rectangle { color: root.bg; radius: 12; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 10
            Label { text: "Send this screenshot?"; font.pixelSize: 18; font.weight: Font.DemiBold; color: root.ink }
            Label {
                Layout.fillWidth: true
                text: ano.connected ? "Nothing has been sent yet. The image is not saved." : "Your local brain is not connected, so this can't be sent."
                wrapMode: Text.Wrap
                color: root.muted
            }
            Image {
                objectName: "captureImage"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 100
                source: screenCapture.previewUrl
                fillMode: Image.PreserveAspectFit
                cache: false
                asynchronous: false
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 80
                TextArea {
                    id: captureQuestion
                    objectName: "captureQuestion"
                    wrapMode: TextEdit.Wrap
                    color: root.ink
                    selectByMouse: true
                    Accessible.name: "Question about this screenshot"
                    background: Field {}
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AnoButton { text: "Cancel"; onClicked: screenCapture.cancel() }
                Item { Layout.fillWidth: true }
                AnoButton {
                    objectName: "approveCapture"
                    text: "Send"
                    primary: true
                    enabled: screenCapture.ready && ano.connected && !ano.paused && !ano.busy
                    onClicked: {
                        search.text = ""
                        ano.approveScreen(captureQuestion.text)
                    }
                }
            }
        }
    }

    Connections {
        target: outbox
        function onChanged() {
            if (outbox.ready && !emailReview.visible) {
                emailSubject.text = outbox.draft.subject
                emailBody.text = outbox.draft.body
                root.showNormal(); root.raise(); root.requestActivate()
                emailReview.open()
            } else if (!outbox.ready) {
                emailReview.close()
            }
        }
    }
    Popup {
        id: emailReview
        objectName: "emailReview"
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: root.height - 40
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 18
        closePolicy: Popup.CloseOnEscape
        onClosed: { if (outbox.ready && !outbox.sending) outbox.discard() }
        background: Rectangle { color: root.bg; radius: 12; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 10
            Label { text: "Send this email?"; font.pixelSize: 18; font.weight: Font.DemiBold; color: root.ink }
            Label {
                Layout.fillWidth: true
                text: "Nothing has been sent. Check who it goes to and edit the text."
                wrapMode: Text.Wrap
                color: root.muted
            }
            Label {
                objectName: "emailRecipients"
                Layout.fillWidth: true
                text: "To: " + (outbox.draft.to || "") + (outbox.draft.cc ? "\nCc: " + outbox.draft.cc : "")
                textFormat: Text.PlainText
                wrapMode: Text.WrapAnywhere
                color: root.ink
                font.bold: true
            }
            TextField {
                id: emailSubject
                objectName: "emailSubject"
                Layout.fillWidth: true
                color: root.ink
                placeholderText: "Subject"
                background: Field {}
                Accessible.name: "Email subject"
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                TextArea {
                    id: emailBody
                    objectName: "emailBody"
                    wrapMode: TextEdit.Wrap
                    color: root.ink
                    selectByMouse: true
                    Accessible.name: "Email text"
                    background: Field {}
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AnoButton { text: "Cancel"; enabled: !outbox.sending; onClicked: outbox.discard() }
                Item { Layout.fillWidth: true }
                AnoButton {
                    objectName: "sendEmail"
                    primary: true
                    text: outbox.sending ? "Sending…" : "Send"
                    enabled: outbox.ready && !outbox.sending && !ano.paused && emailBody.text.trim().length > 0
                    onClicked: outbox.send(emailSubject.text, emailBody.text)
                    Accessible.name: "Send this email from Gmail"
                }
            }
        }
    }

    Connections {
        target: fixes
        function onChanged() {
            if (fixes.ready && !fixReview.visible) {
                root.showNormal(); root.raise(); root.requestActivate()
                fixReview.open()
            } else if (!fixes.ready) {
                fixReview.close()
            }
        }
    }
    Popup {
        id: fixReview
        objectName: "fixReview"
        anchors.centerIn: Overlay.overlay
        width: root.width - 24
        height: Math.min(root.height - 40, 560)
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 18
        closePolicy: Popup.CloseOnEscape
        onClosed: { if (fixes.ready && !fixes.running) fixes.discard() }
        background: Rectangle { color: root.bg; radius: 12; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 10
            Label { text: "Fix this?"; font.pixelSize: 18; font.weight: Font.DemiBold; color: root.ink }
            Label {
                Layout.fillWidth: true
                text: fixes.fix.title || ""
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                font.bold: true
                color: root.ink
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ColumnLayout {
                    width: fixReview.availableWidth
                    spacing: 10
                    Label {
                        Layout.fillWidth: true
                        text: fixes.fix.explanation || ""
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: root.ink
                    }
                    Label { text: "Command"; color: root.muted; font.pixelSize: 11 }
                    TextArea {
                        objectName: "fixCommand"
                        Layout.fillWidth: true
                        text: fixes.fix.command || ""
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.WrapAnywhere
                        font.family: "Cascadia Mono, Consolas"
                        font.pixelSize: 12
                        color: root.ink
                        background: Field {}
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: !!fixes.fix.admin
                        text: "Needs administrator rights. Windows will ask for permission too."
                        wrapMode: Text.Wrap
                        color: root.danger
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AnoButton { text: "Cancel"; enabled: !fixes.running; onClicked: fixes.discard() }
                Item { Layout.fillWidth: true }
                AnoButton {
                    objectName: "runFix"
                    primary: true
                    text: fixes.running ? "Running…" : "Run fix"
                    enabled: fixes.ready && !fixes.running && !ano.paused
                    onClicked: fixes.run()
                    Accessible.name: "Run this fix on the PC"
                }
            }
        }
    }

    ControlPanels {
        id: controlPanels
        anchors.fill: parent
        bg: root.bg
        surface: root.surface
        ink: root.ink
        muted: root.muted
        line: root.line
        accent: root.accent
    }

    Popup {
        id: browserHelp
        objectName: "browserHelp"
        anchors.centerIn: Overlay.overlay
        width: root.width - 40
        modal: true
        focus: true
        // Panels fade and grow in over a dimmed chat (no exit animation: it would swallow the next click).
        enter: Transition { ParallelAnimation { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic } } }
        Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, root.dark ? 0.45 : 0.25) }
        padding: 18
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: root.bg; radius: 14; border.color: root.line }
        contentItem: ColumnLayout {
            spacing: 10
            Label { text: "Browser connection"; font.pixelSize: 18; font.bold: true; color: root.ink }
            Label { Layout.fillWidth: true; text: browserHealth.summary; wrapMode: Text.Wrap; color: root.ink; font.bold: true }
            Label { Layout.fillWidth: true; text: browserHealth.detail; wrapMode: Text.Wrap; color: root.muted }
            Label {
                Layout.fillWidth: true
                visible: browserHealth.browsers.length > 0
                text: "Connected: " + browserHealth.browsers.join(", ")
                wrapMode: Text.Wrap; color: root.accent; font.pixelSize: 12
            }
            AnoButton { Layout.alignment: Qt.AlignRight; text: "Got it"; primary: true; onClicked: browserHelp.close() }
        }
    }

    // ---------------------------------------------------------------- Lively Ano
    // When a task finishes while you are in another app, Ano's face flies around the screen with a
    // short message, then goes home. The back end can trigger the same flight with an alive(kind, message)
    // signal (for monitor events); until it exists the Connections below simply ignores it.
    Settings {
        id: livelyPrefs
        location: StandardPaths.writableLocation(StandardPaths.AppLocalDataLocation) + "/ui.ini"
        category: "alive"
        property bool lively: true
    }
    // ---------------------------------------------------------------- Keep control on
    // Your last control choice is remembered and switched back on at startup, after unlocking and after Pause.
    // Esc / Stop control still stop it at once and it stays off until the next restart, unlock or resume;
    // choosing Off in Settings ends "keep on". Free roam is restored as Full permission: unlimited access
    // (terminals, every drive) should never switch itself on while nobody may be watching.
    Settings {
        id: controlPrefs
        location: StandardPaths.writableLocation(StandardPaths.AppLocalDataLocation) + "/ui.ini"
        category: "control"
        property bool keepOn: false
        property string mode: "reviewed"
    }
    property bool controlHeld: false  // stopped by you this session: don't switch back on until unlock/resume/restart
    property bool controlWasOn: false
    function chooseControl(mode) {
        if (mode === "off") {
            controlPrefs.keepOn = false
            desktopControl.stop()
            return
        }
        controlPrefs.keepOn = true
        controlPrefs.mode = mode
        desktopControl.startSession(mode)
        controlHeld = false  // after: switching modes stops the old session first, which is not "you stopped it"
    }
    function restoreControl() {
        if (!controlPrefs.keepOn || controlHeld || desktopControl.enabled || !desktopControl.available
                || ano.paused || activity.locked || savedLogins.private) return
        desktopControl.startSession(controlPrefs.mode === "roam" ? "full" : controlPrefs.mode)
    }
    Connections {
        target: desktopControl
        function onChanged() {
            if (root.controlWasOn && !desktopControl.enabled && !ano.paused && !activity.locked && !savedLogins.private)
                root.controlHeld = true  // you (or Esc) stopped it: respect that
            root.controlWasOn = desktopControl.enabled
        }
    }
    Connections {
        target: ano
        function onPausedChanged() { if (!ano.paused) { root.controlHeld = false; Qt.callLater(root.restoreControl) } }
    }
    Connections {
        target: activity
        ignoreUnknownSignals: true
        function onStateChanged() { if (!activity.locked) { root.controlHeld = false; Qt.callLater(root.restoreControl) } }
    }
    // Startup, and a gentle retry for anything else that briefly blocked it (e.g. a private login ending).
    Timer { running: root.uiReady && controlPrefs.keepOn; interval: 5000; repeat: true; triggeredOnStart: true; onTriggered: root.restoreControl() }

    property bool flying: false
    property string flyMessage: ""
    readonly property int iconSize: 60
    // The face's centre where a flight starts and ends: the header face, or the parked floating icon.
    function flightHome() {
        if (root.visible && root.visibility !== Window.Minimized && root.visibility !== Window.Hidden) {
            var face = headerFace.mapToGlobal(headerFace.width / 2, headerFace.height / 2)
            return Qt.point(face.x, face.y)
        }
        var icon = ano.floatingPosition(iconSize, iconSize)
        return Qt.point(icon.x + iconSize / 2, icon.y + iconSize / 2)
    }
    // A short flight in a small circle around Ano's own spot: lift off, two laps, say the message, settle back.
    function flyAround(message) {
        if (!livelyPrefs.lively || flying || !uiReady || ano.paused || activity.locked) return
        var s = root.screen
        overlay.x = s.virtualX; overlay.y = s.virtualY
        overlay.width = s.width; overlay.height = s.height
        var home = flightHome()
        var margin = orbit.radius + iconSize / 2 + 6
        orbit.homeX = home.x - s.virtualX
        orbit.homeY = home.y - s.virtualY
        // The circle sits just above the spot (its lowest point is the spot itself), kept on screen.
        orbit.centerX = Math.max(margin, Math.min(s.width - margin, orbit.homeX))
        orbit.centerY = Math.max(margin, Math.min(s.height - margin, orbit.homeY - orbit.radius))
        flyMessage = message || "Done!"
        flying = true
        overlay.show()
        overlay.raise()
        flightSequence.restart()
    }
    function land() {
        flightSequence.stop()
        overlay.hide()  // shown and hidden explicitly: a visible binding on a native window can come undone
        flying = false
        flyMessage = ""
    }
    SequentialAnimation {
        id: flightSequence
        ScriptAction { script: { orbit.lift = 0; orbit.angle = 90; orbit.bank = 0; say.opacity = 0; say.scale = 0.85 } }
        // Ease from the spot onto the circle (90° is the circle's lowest point).
        NumberAnimation { target: orbit; property: "lift"; from: 0; to: 1; duration: 300; easing.type: Easing.OutCubic }
        ParallelAnimation {
            // Two smooth laps.
            NumberAnimation { target: orbit; property: "angle"; from: 90; to: -630; duration: 2400; easing.type: Easing.InOutSine }
            SequentialAnimation {  // lean into the turn while moving, level out when slowing
                NumberAnimation { target: orbit; property: "bank"; to: 1; duration: 500; easing.type: Easing.OutQuad }
                PauseAnimation { duration: 1300 }
                NumberAnimation { target: orbit; property: "bank"; to: 0; duration: 600; easing.type: Easing.InOutQuad }
            }
        }
        ParallelAnimation {  // the message pops in beside Ano
            NumberAnimation { target: say; property: "opacity"; to: 1; duration: 160 }
            NumberAnimation { target: say; property: "scale"; to: 1; duration: 260; easing.type: Easing.OutBack }
        }
        PauseAnimation { duration: 2400 }
        NumberAnimation { target: say; property: "opacity"; to: 0; duration: 180 }
        NumberAnimation { target: orbit; property: "lift"; to: 0; duration: 280; easing.type: Easing.InOutCubic }
        ScriptAction { script: root.land() }
    }
    // One transparent, click-through layer over the screen, only while flying.
    Window {
        id: overlay
        objectName: "flightOverlay"
        visible: false
        transientParent: null
        color: "transparent"
        flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint | Qt.WindowTransparentForInput
        Item {
            id: orbit
            anchors.fill: parent
            property real radius: 44
            property real angle: 90      // degrees on the circle; 90 is its lowest point
            property real lift: 0        // 0 = at the spot, 1 = on the circle
            property real bank: 0        // 0..1 how much to lean into the turn
            property real homeX: 0
            property real homeY: 0
            property real centerX: 0
            property real centerY: 0
            readonly property real circleX: centerX + radius * Math.cos(angle * Math.PI / 180)
            readonly property real circleY: centerY + radius * Math.sin(angle * Math.PI / 180)
            AnoFace {
                id: plane
                width: root.iconSize
                height: root.iconSize
                x: orbit.homeX + (orbit.circleX - orbit.homeX) * orbit.lift - width / 2
                y: orbit.homeY + (orbit.circleY - orbit.homeY) * orbit.lift - height / 2
                // Lean with the horizontal direction of travel around the circle.
                rotation: -16 * orbit.bank * Math.sin(orbit.angle * Math.PI / 180)
                mood: say.opacity > 0.5 ? "talk" : "idle"
                animated: overlay.visible
                interactive: false
                soundEnabled: false
            }
            // The message: a small rounded pill beside Ano.
            Rectangle {
                id: say
                readonly property bool leftSide: plane.x + plane.width + width + 16 > orbit.width
                width: sayText.implicitWidth + 28
                height: 34
                radius: 17
                x: leftSide ? plane.x - width - 8 : plane.x + plane.width + 8
                y: plane.y + plane.height / 2 - height / 2
                transformOrigin: leftSide ? Item.Right : Item.Left
                opacity: 0
                color: root.dark ? "#1b2533" : "#ffffff"
                border.width: 1
                border.color: Qt.alpha(root.accent, 0.6)
                Label {
                    id: sayText
                    anchors.centerIn: parent
                    text: root.flyMessage
                    color: root.ink
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
            }
        }
    }
    Connections {
        target: ano
        ignoreUnknownSignals: true
        function onBusyChanged() {
            // Finished while you were in another app: come and tell you.
            if (!ano.busy && !root.active && ano.messages.length) {
                root.unread += 1
                root.flyAround("Done! Your reply is ready")
            }
        }
        function onAlive(kind, message) { root.flyAround(message) }
    }

    // A small floating Ano that sits on the desktop when the panel is tucked away. Tap it to open the chat.
    Window {
        id: bubble
        objectName: "floatingIcon"
        title: "Ano floating icon"
        transientParent: null
        width: root.iconSize
        height: root.iconSize
        // Hidden during a flight, when the face itself is out flying on the overlay.
        visible: root.uiReady && !root.flying && ano.floating && (root.visibility === Window.Hidden || root.visibility === Window.Minimized)
        color: "transparent"
        flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
        onVisibleChanged: {
            if (visible) {
                var position = ano.floatingPosition(width, height)
                x = position.x
                y = position.y
            }
        }
        // A soft pulsing glow while a reply is waiting.
        Rectangle {
            anchors.fill: parent
            anchors.margins: 2
            radius: width / 2
            color: "transparent"
            border.width: 3
            border.color: root.accent
            visible: root.unread > 0
            SequentialAnimation on opacity {
                running: root.unread > 0 && bubble.visible
                loops: Animation.Infinite
                NumberAnimation { from: 0.25; to: 0.9; duration: 900; easing.type: Easing.InOutSine }
                NumberAnimation { from: 0.9; to: 0.25; duration: 900; easing.type: Easing.InOutSine }
            }
        }
        AnoFace {
            id: bubbleFace
            anchors.fill: parent
            anchors.margins: 6
            Accessible.name: "Open Ano chat"
            Accessible.onPressAction: { root.showNormal(); root.raise(); root.requestActivate() }
            mood: root.mood
            animated: bubble.visible
            interactive: false
            transform: Translate { id: hop; y: 0 }
        }
        // Now and then, while parked and idle, a little hop and giggle.
        SequentialAnimation {
            id: hopAnimation
            NumberAnimation { target: hop; property: "y"; to: -14; duration: 180; easing.type: Easing.OutQuad }
            NumberAnimation { target: hop; property: "y"; to: 0; duration: 420; easing.type: Easing.OutBounce }
        }
        Timer {
            running: bubble.visible && !root.flying && livelyPrefs.lively && root.mood === "idle"
            interval: 240000
            repeat: true
            onTriggered: { interval = 180000 + Math.random() * 240000; hopAnimation.restart(); bubbleFace.delight() }
        }
        MouseArea {
            anchors.fill: parent
            property real px: 0
            property real py: 0
            property bool moved: false
            hoverEnabled: true
            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
            onPressed: function(mouse) { px = mouse.x; py = mouse.y; moved = false }
            onPositionChanged: function(mouse) {
                if (!pressed) return
                var dx = mouse.x - px, dy = mouse.y - py
                if (Math.abs(dx) + Math.abs(dy) > 3) moved = true
                bubble.x += dx; bubble.y += dy
            }
            onReleased: {
                if (moved) ano.saveBubble(bubble.x, bubble.y)
                else { root.showNormal(); root.raise(); root.requestActivate() }
            }
            ToolTip.visible: containsMouse && !pressed
            ToolTip.text: root.unread > 0 ? root.unread + (root.unread === 1 ? " new reply" : " new replies") + " · tap to read"
                                         : "Open Ano · Drag to move"
        }
        // The number of waiting replies.
        Rectangle {
            objectName: "unreadBadge"
            visible: root.unread > 0
            anchors.top: parent.top
            anchors.right: parent.right
            width: Math.max(20, badgeText.implicitWidth + 10)
            height: 20
            radius: 10
            color: root.danger
            border.width: 2
            border.color: root.dark ? "#151c27" : "#ffffff"
            Label { id: badgeText; anchors.centerIn: parent; text: root.unread > 9 ? "9+" : root.unread; color: "white"; font.pixelSize: 11; font.bold: true }
        }
    }

    // Split a reply into its message, the trailing "· note" lines Ano appends (shown as chips),
    // and "· tool_name: reason" blocker lines (shown inside the warning card).
    function splitBody(text) {
        var lines = (text || "").split("\n")
        var notes = [], blockers = []
        var end = lines.length
        while (end > 0) {
            var line = lines[end - 1].trim()
            if (line === "") { end--; continue }
            if (line.indexOf("·") !== 0) break
            var item = line.replace(/^·\s*/, "")
            if (/^[a-z][a-z0-9_]*:\s/.test(item)) blockers.unshift(item)
            else if (item.length) notes.unshift(item)
            end--
        }
        return { main: lines.slice(0, end).join("\n").trim(), notes: notes, blockers: blockers }
    }
    // "OpenAI gpt-4.1 · 4 API call(s) · estimated $0.03730 · start with local: …" -> "GPT-4.1 · 4 calls · $0.037".
    function shortNote(note) {
        var cost = note.match(/^OpenAI (\S+) · (\d+) API call\(s\) · estimated \$(\d+\.\d+)/)
        if (!cost) return note
        var model = cost[1].replace(/^gpt-/i, "GPT-")
        return model + " · " + cost[2] + (cost[2] === "1" ? " call" : " calls") + " · $" + Number(cost[3]).toFixed(3)
    }
    // Idle status lines that only repeat what the brain/control chips already show.
    function routineStatus(text) {
        return /^(OpenAI \S+ brain|Local AI ·|Local \(|Ano ·|Ano ready|OpenAI \S+ ·|OpenAI task finished|Local AI answered)/.test(text || "")
    }
    function isBlocked(main) {
        return /^The task is not verified as complete/.test(main) || /^Stopped at step \d+:/.test(main)
               || main.indexOf("[Blocked after repeated tool failures") >= 0
    }
    function blockedBody(parts) {
        var text = parts.main.replace(/^The task is not verified as complete\.?\s*/, "").replace(/^What blocked it:\s*/, "")
        var reasons = parts.blockers.map(function(item) {
            var split = item.indexOf(": ")
            return "- **" + item.slice(0, split).replace(/_/g, " ") + "** — " + item.slice(split + 2)
        })
        return (text + (reasons.length ? "\n\n" + reasons.join("\n") : "")).trim() || "Ano stopped before finishing."
    }
    // Break a reply into text, ``` code blocks and | tables |, so code gets a monospace box with its own
    // copy button and tables get real columns (Qt's Markdown shows both as plain text).
    function segments(text) {
        var out = [], lines = (text || "").split("\n"), buffer = [], i = 0
        function flush() {
            var chunk = buffer.join("\n").trim()
            if (chunk.length) out.push({ kind: "text", text: chunk, lang: "", rows: [] })
            buffer = []
        }
        function cells(line) {
            return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function(cell) { return cell.trim() })
        }
        while (i < lines.length) {
            var line = lines[i]
            var fence = line.match(/^\s*```\s*([\w+#.-]*)\s*$/)
            if (fence) {
                flush()
                var code = []
                i++
                while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { code.push(lines[i]); i++ }
                i++
                out.push({ kind: "code", text: code.join("\n"), lang: fence[1] || "", rows: [] })
                continue
            }
            if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
                flush()
                var rows = [cells(line)]
                i += 2
                while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(cells(lines[i])); i++ }
                var width = rows[0].length
                rows = rows.map(function(row) { while (row.length < width) row.push(""); return row.slice(0, width) })
                out.push({ kind: "table", text: "", lang: "", rows: rows })
                continue
            }
            buffer.push(line)
            i++
        }
        flush()
        return out.length ? out : [{ kind: "text", text: "", lang: "", rows: [] }]
    }
    function tableCells(rows) {
        var flat = []
        for (var r = 0; r < rows.length; r++)
            for (var c = 0; c < rows[r].length; c++)
                flat.push({ text: rows[r][c], header: r === 0, stripe: r > 0 && r % 2 === 0 })
        return flat
    }
    // Chats filtered by title and grouped under Today / Yesterday / Previous 7 days / Older headings.
    function chatRows(chats, query) {
        var rows = [], last = "", words = (query || "").toLowerCase().split(/\s+/).filter(function(w) { return w.length })
        var today = new Date(); today.setHours(0, 0, 0, 0)
        for (var i = 0; i < chats.length; i++) {
            var chat = chats[i], title = (chat.title || "").toLowerCase()
            if (words.some(function(w) { return title.indexOf(w) < 0 })) continue
            var when = new Date(chat.updated_at)
            var days = Math.floor((today - new Date(when.getFullYear(), when.getMonth(), when.getDate())) / 86400000)
            var group = isNaN(days) ? "Older" : days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "Previous 7 days" : "Older"
            if (group !== last) { rows.push({ header: true, label: group }); last = group }
            rows.push({ header: false, id: chat.id, title: chat.title, updated_at: chat.updated_at,
                        time: isNaN(when.getTime()) ? "" : days <= 1 ? Qt.formatTime(when, "h:mm AP")
                              : days < 7 ? Qt.formatDate(when, "dddd") : Qt.formatDate(when, "d MMM yyyy") })
        }
        return rows
    }
    // Built-in commands for the composer's suggestions: c = exact command, h = what it does.
    // Commands ending in a space or containing NAME are templates to finish typing.
    readonly property var commandCatalog: [
        { c: "show commands", h: "Everything Ano can do without AI" },
        { c: "when claude is done, tell me and fly around", h: "Watch: Claude finishing in VS Code" },
        { c: "when claude is done, type \"continue\" in vs code every time", h: "Watch: keep Claude going" },
        { c: "when my download finishes, open it", h: "Watch: downloads (documents and media only)" },
        { c: "when codex is out of usage, resume it after the reset", h: "Watch: Codex usage limits" },
        { c: "whenever I open spotify, say \"time to focus\"", h: "Watch: an app opening" },
        { c: "when the internet is back, tell me", h: "Watch: network" },
        { c: "when my battery is low, tell me", h: "Watch: battery" },
        { c: "run all trading models", h: "ICT, SMC, Silver Bullet, AMD, gap fill, opening range and technicals (research/alerts)" },
        { c: "open reliability dashboard", h: "Service health, paper readiness and real reliability observations" },
        { c: "trading bot readiness", h: "Read-only broker and runner check; no orders placed" },
        { c: "ano health", h: "Current health, last evidence and recovery steps" },
        { c: "trading model status", h: "Latest timestamped evidence and missing-data reasons for every strategy" },
        { c: "stop the trading model", h: "Stop research monitoring; paper execution has separate controls" },
        { c: "smc qqq", h: "Order blocks, breakers, BOS/CHOCH and daily/4h/1h bias (research only)" },
        { c: "run silver bullet", h: "Monitor the strict NY windows and closed 15-minute bias" },
        { c: "local: show today's ICT 2022 setups on QQQ", h: "ICT 2022 model + AMD on Alpaca paper data (free, no orders)" },
        { c: "local: backtest the ICT 2022 model on QQQ for 10 days", h: "Win rate and R by grade (free, no orders)" },
        { c: "local: give me the ICT 2022 TradingView indicator", h: "Pine Script that draws the model on TradingView" },
        { c: "watch gap fills on qqq and dia", h: "Forward-test the gap fill each morning (free, alerts only)" },
        { c: "start paper trading the gap fill", h: "Alpaca PAPER orders for the gap fill (rule sheet v1.2)" },
        { c: "stop paper trading", h: "No new entries; cancel or close what Ano opened" },
        { c: "watch ict setups on qqq and dia", h: "Alert me when an ICT 2022 setup forms (free, alerts only)" },
        { c: "ict watch status", h: "" },
        { c: "stop watching ict setups", h: "" },
        { c: "when I get a notification from outlook, read it to me", h: "Watch: notifications from an app (banks and codes skipped)" },
        { c: "when a notification mentions \"interview\", tell me every time", h: "Watch: notifications mentioning a word" },
        { c: "when codex notifies me, fly around", h: "Watch: an app's notifications" },
        { c: "when I get a notification from my phone, handle it", h: "Ano offers to deal with it; you tap Approve first" },
        { c: "pause notifications", h: "Stop reading notifications; other monitors keep running" },
        { c: "resume notifications", h: "" },
        { c: "keep working on ", h: "A long goal: Ano keeps going turn after turn until it is done" },
        { c: "goal status", h: "How far the current goal has got" },
        { c: "stop the goal", h: "" },
        { c: "continue the goal", h: "Pick up a goal that stopped or needed you" },
        { c: "in the background: ", h: "Run a task in its own chat while you keep chatting" },
        { c: "background tasks", h: "What is running in the background" },
        { c: "claude: ", h: "Claude Code does the work hidden (fix, build, enable…) and posts the result here" },
        { c: "claude continue: ", h: "Follow up on the last Claude job" },
        { c: "claude jobs", h: "What Claude is working on" },
        { c: "stop claude", h: "Stop the running Claude job" },
        { c: "check my outlook inbox", h: "Newest emails from Outlook on this PC (read-only)" },
        { c: "what is on my outlook calendar this week", h: "Your Outlook calendar" },
        { c: "show monitors", h: "What Ano is watching" },
        { c: "pause monitoring", h: "Stop all watching at once" },
        { c: "resume monitoring", h: "" },
        { c: "at 11:30 pm type \"continue\" in chatgpt", h: "Type into an app at a set time" },
        { c: "in 30 minutes type \"continue\" in vs code", h: "Type into an app later" },
        { c: "show scheduled actions", h: "" },
        { c: "watch codex", h: "Resume Codex after usage limits" },
        { c: "codex watch status", h: "" },
        { c: "stop watching codex", h: "" },
        { c: "remind me in 25 minutes to ", h: "Reminder" },
        { c: "remind me tomorrow at 9 am to ", h: "Reminder" },
        { c: "show reminders", h: "" },
        { c: "take a note: ", h: "Save a note" },
        { c: "find notes about ", h: "" },
        { c: "show my notes", h: "" },
        { c: "open ", h: "An app or site: canvas, spotify, vs code, gmail…" },
        { c: "search web for ", h: "" },
        { c: "research ", h: "Collect three sources into a note" },
        { c: "read public page https://", h: "" },
        { c: "write word document NAME: ", h: "Exact text into a new .docx" },
        { c: "read word document ", h: "" },
        { c: "write document NAME: ", h: "Exact text into a new note file" },
        { c: "list documents", h: "" },
        { c: "generate image: ", h: "Free, made on this PC" },
        { c: "check laptop health", h: "CPU, memory, disk, battery" },
        { c: "check battery", h: "" },
        { c: "check disk space", h: "" },
        { c: "list windows", h: "" },
        { c: "show startup apps", h: "" },
        { c: "calculate ", h: "" },
        { c: "what time is it", h: "" },
        { c: "teach routine NAME = open canvas -> open word", h: "Your own multi-step shortcut" },
        { c: "run routine ", h: "" },
        { c: "show my routines", h: "" },
        { c: "show local ai status", h: "" },
        { c: "show recent actions", h: "What Ano actually did" },
        { c: "paid usage", h: "What Ano has spent on OpenAI" },
        { c: "run challenge tests", h: "Hard real tasks in all 9 areas, checked and fixed" },
        { c: "run challenge tests files", h: "Only one area: reasoning, apps, word, web, files, voice, projects, memory, images" },
        { c: "challenge results", h: "The last scorecard" },
        { c: "stop challenge tests", h: "" },
        { c: "local: ", h: "Ask the free local brain once" },
        { c: "paid: ", h: "Ask OpenAI once" }
    ]
    function dayKey(stamp) {
        var moment = new Date(stamp)
        return isNaN(moment.getTime()) ? "" : Qt.formatDate(moment, "yyyy-MM-dd")
    }
    function dayName(stamp) {
        var moment = new Date(stamp)
        if (isNaN(moment.getTime())) return ""
        var today = new Date(); today.setHours(0, 0, 0, 0)
        var days = Math.round((today - new Date(moment.getFullYear(), moment.getMonth(), moment.getDate())) / 86400000)
        return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? Qt.formatDate(moment, "dddd") : Qt.formatDate(moment, "d MMMM yyyy")
    }
    // The request a reply answered: the nearest of your messages before it.
    function previousUserMessage(index) {
        for (var i = index - 1; i >= 0; i--) {
            var item = ano.messages[i]
            if (item && item.role === "user") return item.body
        }
        return ""
    }
    function clock(stamp) {
        var moment = new Date(stamp)
        return isNaN(moment.getTime()) ? "" : Qt.formatTime(moment, "h:mm AP")
    }

    // Prepare a reply for Markdown display: drop image embeds (so untrusted text can't load
    // remote images) and keep single line breaks, which Markdown would otherwise swallow.
    function displayBody(text) {
        if (!text) return ""
        text = text.replace(/!\[[^\]]*\]\([^)]*\)/g, "")
        return text.replace(/\n/g, "  \n")
    }

    function submit() {
        if (ano.paused || (ano.busy && !ano.queueEnabled) || voice.busy || !composer.text.trim()) return
        search.text = ""
        search.visible = false
        ano.send(composer.text)
        composer.text = ""
    }
}
