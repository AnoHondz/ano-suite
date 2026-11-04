import QtQuick
import QtQuick.Controls

// Colours come from the window palette, so buttons follow Windows light/dark mode.
Button {
    id: control
    property bool primary: false
    hoverEnabled: true
    implicitHeight: 36
    implicitWidth: Math.max(64, label.implicitWidth + 28)
    leftPadding: 14
    rightPadding: 14
    contentItem: Text {
        id: label
        text: control.text
        font.family: control.font.family
        font.pixelSize: 13
        font.weight: Font.DemiBold
        color: control.primary && control.enabled ? control.palette.highlightedText : control.palette.buttonText
        opacity: control.enabled ? 1 : 0.45
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 7
        color: control.primary ? control.palette.highlight : control.down ? control.palette.midlight : control.hovered ? control.palette.alternateBase : control.palette.button
        opacity: control.enabled ? 1 : 0.5
        border.width: control.primary ? 0 : 1
        border.color: control.activeFocus ? control.palette.highlight : control.palette.mid
        Behavior on color { ColorAnimation { duration: 120 } }
    }
}
