import QtQuick
import QtQuick.Shapes
import QtMultimedia

// Native orb driven by the desktop app's real state, without browser capture.
Item {
    id: face
    property string mood: "idle"
    property bool animated: true
    property bool interactive: true
    property bool soundEnabled: true
    readonly property real s: Math.min(width, height)
    readonly property bool moving: animated && visible && mood !== "sleep"
    property real blink: 1
    property real bob: 0
    property real mouthOpen: 0
    property real wiggle: 0
    property bool giggling: false
    implicitWidth: 64; implicitHeight: 64
    Accessible.role: Accessible.Button
    Accessible.name: "Ano, " + mood + ". Tap to giggle."
    Accessible.onPressAction: delight()
    activeFocusOnTab: interactive
    Keys.onSpacePressed: delight()
    Keys.onReturnPressed: delight()
    function delight() {
        if (!interactive || !animated || mood !== "idle" || giggling) return
        giggling = true
        laughTimer.restart()
        if (soundEnabled) chuckle.play()
    }
    onMoodChanged: {
        giggling = false; chuckle.stop(); laughTimer.stop()
        blink = mood === "sleep" ? .08 : 1
    }
    onAnimatedChanged: if (!animated) { chuckle.stop(); giggling = false }
    SoundEffect { id: chuckle; source: "../assets/ano-giggle.wav"; volume: .22 }
    Timer { id: laughTimer; interval: 1050; onTriggered: face.giggling = false }
    Timer {
        running: face.moving; interval: 2700; repeat: true
        onTriggered: { blinking.restart(); interval = 2400 + Math.random() * 3300 }
    }
    SequentialAnimation {
        id: blinking
        NumberAnimation { target: face; property: "blink"; to: .08; duration: 80 }
        NumberAnimation { target: face; property: "blink"; to: 1; duration: 140 }
        onStopped: face.blink = face.mood === "sleep" ? .08 : 1
    }
    SequentialAnimation on bob {
        running: face.moving; loops: Animation.Infinite
        NumberAnimation { to: -.015; duration: 1500; easing.type: Easing.InOutSine }
        NumberAnimation { to: .015; duration: 1500; easing.type: Easing.InOutSine }
        onRunningChanged: if (!running) face.bob = 0
    }
    SequentialAnimation on wiggle {
        running: face.moving && (face.giggling || face.mood === "confused")
        loops: Animation.Infinite
        NumberAnimation { to: -5; duration: face.giggling ? 100 : 900; easing.type: Easing.InOutSine }
        NumberAnimation { to: 5; duration: face.giggling ? 100 : 900; easing.type: Easing.InOutSine }
        onRunningChanged: if (!running) face.wiggle = 0
    }
    SequentialAnimation on mouthOpen {
        running: face.moving && (face.mood === "talk" || face.giggling)
        loops: Animation.Infinite
        NumberAnimation { to: 1; duration: 125 }
        NumberAnimation { to: .18; duration: 95 }
        NumberAnimation { to: .7; duration: 160 }
        NumberAnimation { to: .05; duration: 110 }
        onRunningChanged: if (!running) face.mouthOpen = 0
    }
    Rectangle {
        width: face.s * .94; height: width; radius: width / 2
        anchors.centerIn: parent; color: "#123766"; opacity: .3
        scale: face.mood === "listen" ? 1.05 : 1
        Behavior on scale { NumberAnimation { duration: 250 } }
    }
    Rectangle {
        id: body
        readonly property real u: width
        width: face.s * .87; height: width; radius: width / 2
        anchors.centerIn: parent; anchors.verticalCenterOffset: face.bob * face.s
        rotation: face.wiggle
        border.width: Math.max(1, face.s * .008)
        border.color: face.mood === "sleep" ? "#3d526c" : "#72c6ff"
        gradient: Gradient {
            GradientStop { position: 0; color: "#2b69a7" }
            GradientStop { position: .3; color: "#0c3267" }
            GradientStop { position: .72; color: "#071e46" }
            GradientStop { position: 1; color: "#225fa5" }
        }
        opacity: face.mood === "sleep" ? .6 : 1
        Behavior on opacity { NumberAnimation { duration: 250 } }
        Repeater {
            model: 3
            delegate: Rectangle {
                required property int index
                width: body.u * .9; height: body.u * (.4 + index * .15)
                anchors.centerIn: parent; radius: width / 2
                color: "transparent"; border.color: index === 1 ? "#326fba" : "#4c91d2"
                border.width: Math.max(1, body.u * .009); opacity: .45
                rotation: 25 + index * 55
                NumberAnimation on rotation {
                    from: 25 + index * 55; to: 385 + index * 55
                    duration: face.mood === "think" ? 6000 : 27000
                    loops: Animation.Infinite; running: face.moving
                }
            }
        }
        Rectangle {
            x: body.u * .19; y: body.u * .08; width: body.u * .2; height: body.u * .06
            radius: height / 2; rotation: -30; color: "#c4ebff"; opacity: .7
        }
        Rectangle {
            id: visor
            width: body.u * .83; height: body.u * .51
            anchors.centerIn: parent; rotation: -8; radius: height * .44
            border.color: "#4384c7"; border.width: Math.max(1, body.u * .006)
            gradient: Gradient {
                GradientStop { position: 0; color: "#213653" }
                GradientStop { position: .4; color: "#071326" }
                GradientStop { position: 1; color: "#030c1c" }
            }
            Repeater {
                model: [.28, .72]
                delegate: Rectangle {
                    id: eye
                    required property real modelData
                    width: body.u * .16
                    height: body.u * (face.mood === "listen" ? .21 : .19)
                    radius: width / 2
                    x: visor.width * modelData - width / 2 + (face.mood === "think" ? body.u * .025 : 0)
                    y: visor.height * .44 - height / 2 - (face.mood === "think" ? body.u * .035 : 0)
                    transform: Scale { origin.y: eye.height / 2; yScale: face.giggling ? .35 : face.blink }
                    gradient: Gradient {
                        GradientStop { position: 0; color: "#a9d8ff" }
                        GradientStop { position: .6; color: "#6fbbf7" }
                        GradientStop { position: 1; color: "#daf7ff" }
                    }
                    Rectangle {
                        width: parent.width * .52; height: parent.height * .68
                        anchors.centerIn: parent; radius: width / 2; color: "#06132e"
                        Rectangle {
                            width: parent.width * .4; height: width; radius: width / 2
                            x: -width * .12; y: width * .15; color: "white"
                        }
                    }
                }
            }
            Shape {
                anchors.fill: parent
                visible: !face.giggling && (face.mood === "idle" || face.mood === "listen" || face.mood === "sleep")
                ShapePath {
                    strokeColor: "#bcecff"; strokeWidth: Math.max(1, body.u * .014)
                    fillColor: "transparent"; capStyle: ShapePath.RoundCap
                    startX: visor.width * .44; startY: visor.height * .74
                    PathQuad { x: visor.width * .57; y: visor.height * .72; controlX: visor.width * .51; controlY: visor.height * .87 }
                }
            }
            Rectangle {
                visible: face.mood === "talk" || face.mood === "confused" || face.giggling
                width: body.u * (face.mood === "confused" ? .045 : .1)
                height: body.u * (face.mood === "confused" ? .05 : .025 + .07 * face.mouthOpen)
                x: (visor.width - width) / 2; y: visor.height * .77 - height / 2
                radius: Math.min(width, height) / 2; color: "#020918"
                border.width: Math.max(1, body.u * .007); border.color: "#a5dcff"
            }
            Row {
                visible: face.mood === "think"; spacing: body.u * .035
                anchors.horizontalCenter: parent.horizontalCenter; y: visor.height * .76
                Repeater {
                    model: 3
                    delegate: Rectangle {
                        required property int index
                        width: body.u * .025; height: width; radius: width / 2; color: "#a7deff"
                        SequentialAnimation on opacity {
                            running: face.moving; loops: Animation.Infinite
                            PauseAnimation { duration: index * 140 }
                            NumberAnimation { to: .2; duration: 350 }
                            NumberAnimation { to: 1; duration: 350 }
                        }
                    }
                }
            }
        }
        Text {
            visible: face.mood === "confused" && body.u > 40
            text: "?"; color: "#c0e7ff"; font.pixelSize: body.u * .17
            x: body.u * .83; y: body.u * .04
        }
    }
    Rectangle { anchors.fill: parent; radius: width / 2; color: "transparent"; border.color: "#bcecff"; visible: face.activeFocus }
    MouseArea {
        anchors.fill: parent; enabled: face.interactive && face.mood === "idle"
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: face.delight()
    }
}
