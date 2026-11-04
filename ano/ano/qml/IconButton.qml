import QtQuick
import QtQuick.Controls

// Compact button showing a Windows icon-font glyph.
Button {
    id: control
    property string glyph: ""
    property bool primary: false
    property color tint: control.palette.highlight
    hoverEnabled: true
    implicitWidth: 38
    implicitHeight: 38
    padding: 0
    ToolTip.visible: hovered && ToolTip.text.length > 0
    ToolTip.delay: 600
    contentItem: Text {
        text: control.glyph
        font.family: "Segoe Fluent Icons, Segoe MDL2 Assets"
        font.pixelSize: 16
        color: control.primary && control.enabled ? control.palette.highlightedText : control.palette.buttonText
        opacity: control.enabled ? 1 : 0.4
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: 8
        color: control.primary ? control.tint : control.down ? control.palette.midlight : control.hovered ? control.palette.alternateBase : "transparent"
        opacity: control.enabled ? 1 : 0.5
        border.width: control.activeFocus ? 1 : 0
        border.color: control.palette.highlight
    }
}
