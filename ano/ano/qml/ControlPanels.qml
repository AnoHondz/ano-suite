import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: panels
    property color bg: "#fbfdfd"
    property color surface: "#eef3f4"
    property color ink: "#1d2026"
    property color muted: "#626873"
    property color line: "#dce4e6"
    property color accent: "#0d9488"
    function openVault() { vaultPopup.open() }
    function closeVault() { vaultPopup.close(); panels.clearFields() }
    function clearFields() { loginLabel.text = ""; loginTarget.text = ""; loginUser.text = ""; loginPassword.text = "" }

    Connections {
        target: reviews
        function onChanged() {
            if (reviews.ready) actionReview.open()
            else actionReview.close()
        }
    }
    Popup {
        id: actionReview
        palette.highlight: panels.accent
        palette.highlightedText: panels.bg
        objectName: "actionReview"
        anchors.centerIn: Overlay.overlay
        width: panels.width - 24
        height: panels.height - 32
        modal: true
        focus: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        onClosed: { if (reviews.ready) reviews.cancel() }
        background: Rectangle { radius: 14; color: panels.bg; border.color: panels.line }
        contentItem: ColumnLayout {
            spacing: 10
            Label { text: reviews.review.title || "Review action"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.bold: true; font.pixelSize: 18; color: panels.ink }
            Label { text: reviews.review.target || ""; textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.Wrap; color: panels.muted }
            Image {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: visible ? 90 : 0
                visible: !!reviews.review.preview
                source: reviews.review.preview || ""
                fillMode: Image.PreserveAspectFit
                cache: false
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: !reviews.review.preview
                Layout.preferredHeight: 110
                TextArea { text: reviews.review.detail || ""; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; color: panels.ink; background: Rectangle { color: panels.surface; radius: 8 } }
            }
            Label { Layout.fillWidth: true; text: "Approve once, or cancel. Pause and Windows lock cancel pending approvals."; wrapMode: Text.Wrap; font.pixelSize: 10; color: panels.muted }
            RowLayout {
                Layout.fillWidth: true
                AnoButton { text: "Cancel"; onClicked: reviews.cancel() }
                Item { Layout.fillWidth: true }
                AnoButton {
                    objectName: "approveComputerAction"
                    text: reviews.review.button || "Approve once"
                    primary: true
                    enabled: reviews.ready && !ano.paused && !activity.locked
                    onClicked: reviews.approve(reviews.review.id)
                }
            }
        }
    }
    Popup {
        id: vaultPopup
        palette.highlight: panels.accent
        palette.highlightedText: panels.bg
        objectName: "vaultPopup"
        anchors.centerIn: Overlay.overlay
        width: panels.width - 24
        height: panels.height - 32
        modal: true
        focus: true
        padding: 16
        closePolicy: Popup.CloseOnEscape
        onVisibleChanged: { if (visible) savedLogins.setVisible(true) }
        onClosed: { panels.clearFields(); savedLogins.setVisible(false) }
        background: Rectangle { radius: 14; color: panels.bg; border.color: panels.line }
        contentItem: ColumnLayout {
            spacing: 8
            Label { text: "Saved logins"; font.pixelSize: 18; font.bold: true; color: panels.ink }
            Label { Layout.fillWidth: true; text: savedLogins.status; wrapMode: Text.Wrap; color: panels.muted; font.pixelSize: 10 }
            ListView {
                Layout.fillWidth: true
                Layout.preferredHeight: count ? Math.min(110, contentHeight) : 24
                clip: true
                spacing: 6
                model: savedLogins.entries
                ScrollBar.vertical: ScrollBar {}
                Label { visible: parent.count === 0; text: "No saved logins yet."; color: panels.muted; font.pixelSize: 11 }
                delegate: RowLayout {
                    required property var modelData
                    width: ListView.view.width
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Label { Layout.fillWidth: true; text: modelData.label; textFormat: Text.PlainText; elide: Text.ElideRight; color: panels.ink }
                        Label { Layout.fillWidth: true; text: modelData.target; textFormat: Text.PlainText; elide: Text.ElideMiddle; color: panels.muted; font.pixelSize: 10 }
                    }
                    AnoButton { text: "Remove"; enabled: !savedLogins.busy; onClicked: { deleteLogin.loginId = modelData.id; deleteLogin.open() } }
                }
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: parent.width
                    spacing: 8
                    Label { text: "Add a login"; font.bold: true; color: panels.ink }
                    TextField { id: loginLabel; objectName: "loginLabel"; Layout.fillWidth: true; placeholderText: "Name, e.g. My email"; maximumLength: 100; Accessible.name: "Login name" }
                    ComboBox { id: loginKind; Layout.fillWidth: true; model: ["Website", "Windows app"]; Accessible.name: "Login type" }
                    RowLayout {
                        Layout.fillWidth: true
                        TextField { id: loginTarget; objectName: "loginTarget"; Layout.fillWidth: true; placeholderText: loginKind.currentIndex === 0 ? "https://example.com" : "Full path to the app .exe"; Accessible.name: "Exact website or application" }
                        AnoButton { text: "Browse"; visible: loginKind.currentIndex === 1; onClicked: appPicker.open() }
                    }
                    TextField { id: loginUser; objectName: "loginUser"; Layout.fillWidth: true; placeholderText: "Username or email"; maximumLength: 320; Accessible.name: "Username" }
                    TextField { id: loginPassword; objectName: "loginPassword"; Layout.fillWidth: true; placeholderText: "Password"; echoMode: TextInput.Password; maximumLength: 4096; selectByMouse: false; Accessible.name: "Password, hidden" }
                    Label { Layout.fillWidth: true; text: "Stored in Windows Credential Manager. Website fills require the updated Ano extension; app fills require a focused password field. You finish submitting the login yourself."; wrapMode: Text.Wrap; color: panels.muted; font.pixelSize: 10 }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AnoButton { text: "Close"; onClicked: vaultPopup.close() }
                Item { Layout.fillWidth: true }
                AnoButton {
                    objectName: "saveLogin"
                    text: "Save login"
                    primary: true
                    enabled: !ano.paused && !activity.locked && !savedLogins.busy
                    onClicked: {
                        if (savedLogins.save(loginLabel.text, loginKind.currentIndex === 0 ? "website" : "app", loginTarget.text, loginUser.text, loginPassword.text)) panels.clearFields()
                    }
                }
            }
        }
    }
    FileDialog { id: appPicker; title: "Choose the app for this login"; fileMode: FileDialog.OpenFile; nameFilters: ["Windows apps (*.exe)"]; onAccepted: loginTarget.text = selectedFile.toString() }
    Dialog {
        id: deleteLogin
        palette.highlight: panels.accent
        property string loginId: ""
        anchors.centerIn: Overlay.overlay
        width: panels.width - 40
        title: "Remove saved login?"
        height: 180
        modal: true
        standardButtons: Dialog.Yes | Dialog.Cancel
        onAccepted: savedLogins.delete(loginId)
        contentItem: Label { text: "This removes this login from Ano's Windows credential vault."; wrapMode: Text.Wrap; color: panels.ink }
        background: Rectangle { color: panels.bg; radius: 12; border.color: panels.line }
    }
}
