import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// The TEMPORARY test login: choose a username, nothing else (no password, no email, no account).
// Shown only when the test backend is in use; the real OctoWoW sign-in opens the browser instead.
Item {
    id: root
    objectName: "loginOverlay"
    visible: account.loginOpen
    focus: visible
    Keys.onEscapePressed: account.closeLogin()
    property bool waiting: false
    onVisibleChanged: if (visible) { nameField.text = ""; waiting = false; nameField.forceActiveFocus() }

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler {
            onTapped: function (ep) {
                var l = card.mapFromItem(null, ep.scenePosition.x, ep.scenePosition.y)
                if (l.x < 0 || l.y < 0 || l.x > card.width || l.y > card.height) account.closeLogin()
            }
        }
    }
    SkinPanel {
        id: card
        kind: "dialog"
        width: Math.min(360, parent.width - 40)
        height: col.implicitHeight + padTop + padBottom
        anchors.centerIn: parent
        TapHandler { }
        Column {
            id: col
            width: parent.width
            spacing: 8
            SkinText { kind: "panel.header"; text: account.signedIn ? app.t("SWITCH ACCOUNT") : app.t("SIGN IN") }
            SkinSearchField {
                id: nameField
                objectName: "loginNameField"
                width: parent.width
                placeholder: app.t("Choose a username")
                enabled: !root.waiting
                onEdited: function (t) { root.waiting = false }
            }
            SkinText { objectName: "loginError"; visible: account.message !== "" && !account.busy; kind: "text.error"
                       width: parent.width; wrapMode: Text.Wrap; text: account.message }
            Row {
                spacing: 8
                SkinButton {
                    objectName: "loginConfirm"
                    kind: "button.primary"
                    text: account.busy ? app.t("Signing in…") : app.t("Sign In")
                    enabled: !account.busy && nameField.text.length >= 3
                    onClicked: account.signInAs(nameField.text)
                }
                SkinButton { kind: "button.small"; text: app.t("Cancel"); onClicked: account.closeLogin() }
            }
            SkinText { kind: "text.dim"; width: parent.width; wrapMode: Text.Wrap
                       text: app.t("Test login: no password. Anyone can choose any free name.") }
        }
    }
}
