import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// The TEMPORARY test login: choose a username. Behind it the name is a real account on the test backend, so
// the first time the name is claimed a recovery code is shown (it moves the account to another computer), and
// a tester key may be asked for once. Shown only when the test backend is in use; the real OctoWoW sign-in
// opens the browser instead.
Item {
    id: root
    objectName: "loginOverlay"
    visible: account.loginOpen
    focus: visible
    Keys.onEscapePressed: account.closeLogin()
    property bool useRecovery: false
    readonly property bool showingCode: account.recoveryCode !== ""
    onVisibleChanged: if (visible) { nameField.text = ""; keyField.text = ""; recoveryField.text = ""; useRecovery = false
                                     if (!showingCode) nameField.forceActiveFocus() }

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler {
            onTapped: function (ep) {
                var l = card.mapFromItem(null, ep.scenePosition.x, ep.scenePosition.y)
                if (!root.showingCode && (l.x < 0 || l.y < 0 || l.x > card.width || l.y > card.height)) account.closeLogin()
            }
        }
    }
    SkinPanel {
        id: card
        kind: "dialog"
        width: Math.min(380, parent.width - 40)
        height: col.implicitHeight + padTop + padBottom
        anchors.centerIn: parent
        TapHandler { }
        TextEdit { id: copier; visible: false }

        // ── a new account: its recovery code ──
        Column {
            id: codeCol
            visible: root.showingCode
            width: parent.width
            spacing: 8
            SkinText { kind: "panel.header"; text: app.t("RECOVERY CODE") }
            Rectangle {
                width: parent.width; height: 34
                color: Style.pick(Style.theme.components["input"].colors, "normal", "#222")
                border.color: Style.pick(Style.theme.components["input"].borderColors, "normal", "#555")
                TextEdit {
                    objectName: "recoveryCodeText"
                    anchors { fill: parent; margins: 6 }
                    text: account.recoveryCode
                    readOnly: true; selectByMouse: true
                    font.family: "Consolas"; font.pixelSize: 13
                    color: Style.pick(Style.theme.components["input"].textColors, "normal", "#fff")
                    verticalAlignment: TextEdit.AlignVCenter
                }
            }
            SkinText { kind: "text.dim"; width: parent.width; wrapMode: Text.Wrap
                       text: app.t("Keep this code. It is the only way to use this name on another computer.") }
            Row {
                spacing: 8
                SkinButton { kind: "button.small"; text: app.t("Copy")
                             onClicked: { copier.text = account.recoveryCode; copier.selectAll(); copier.copy() } }
                SkinButton { objectName: "recoveryDone"; kind: "button.primary"; text: app.t("Done"); onClicked: account.dismissRecovery() }
            }
        }

        // ── choose a username ──
        Column {
            id: col
            visible: !root.showingCode
            width: parent.width
            spacing: 8
            SkinText { kind: "panel.header"; text: account.signedIn ? app.t("SWITCH ACCOUNT") : app.t("SIGN IN") }
            SkinSearchField {
                id: nameField
                objectName: "loginNameField"
                width: parent.width
                placeholder: app.t("Choose a username")
                enabled: !account.busy
            }
            SkinSearchField {
                id: keyField
                objectName: "loginKeyField"
                visible: account.requiresKey && !root.useRecovery
                width: parent.width
                placeholder: app.t("Tester key")
                enabled: !account.busy
            }
            SkinSearchField {
                id: recoveryField
                objectName: "loginRecoveryField"
                visible: root.useRecovery
                width: parent.width
                placeholder: app.t("Recovery code")
                enabled: !account.busy
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
                    onClicked: account.signInAs(nameField.text, keyField.text, root.useRecovery ? recoveryField.text : "")
                }
                SkinButton { kind: "button.small"; text: app.t("Cancel"); onClicked: account.closeLogin() }
            }
            SkinText {
                objectName: "recoveryToggle"
                kind: "text.link"
                text: root.useRecovery ? app.t("Back") : app.t("I have a recovery code")
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: root.useRecovery = !root.useRecovery }
            }
        }
    }
}
