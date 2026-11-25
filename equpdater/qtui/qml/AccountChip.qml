import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// The optional OctoWoW account, upper right (as in Battle.net). Nothing here is
// needed to update, configure or play. Signed out: Sign In and a link to create
// an account on the official site. Signed in: avatar, name, status dot, a menu.
Item {
    id: root
    objectName: "accountChip"
    implicitWidth: row.implicitWidth
    implicitHeight: 32

    Row {
        id: row
        spacing: 10
        anchors.verticalCenter: parent.verticalCenter
        // ── signed out ──
        SkinButton {
            objectName: "signInButton"
            visible: !account.signedIn && !account.busy
            kind: "button.small"
            text: app.t("Sign In")
            onClicked: account.signIn()
            SkinToolTipArea { tip: account.available ? app.t("Sign in with your OctoWoW forum account in your browser. Optional: nothing needs it.")
                                                     : app.t("Sign-in is not available yet. Click to see why.") }
        }
        SkinText {
            objectName: "createAccountLink"
            visible: !account.signedIn && !account.busy
            kind: "text.link"
            anchors.verticalCenter: parent.verticalCenter
            text: app.t("Create account")
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: account.createAccount() }
            Accessible.role: Accessible.Link
            Accessible.name: text
        }
        // ── signing in ──
        SkinText { visible: account.busy; kind: "text.dim"; anchors.verticalCenter: parent.verticalCenter
                   text: app.t("Waiting for your browser…") }
        SkinButton { visible: account.busy; kind: "button.small"; text: app.t("Cancel"); onClicked: account.cancelSignIn() }
        // ── signed in ──
        Item {
            id: me
            objectName: "accountMenuButton"
            visible: account.signedIn
            width: meRow.implicitWidth + 8; height: 32
            activeFocusOnTab: true
            Accessible.role: Accessible.Button
            Accessible.name: account.username
            Keys.onReturnPressed: menu.open()
            Keys.onSpacePressed: menu.open()
            Row {
                id: meRow
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Item {
                    width: 28; height: 28
                    Rectangle {
                        anchors.fill: parent; radius: 14
                        color: Style.pick(Style.theme.components["button"].colors, "normal", "#333")
                        border.width: 1
                        border.color: Style.pick(Style.theme.components["button"].borderColors, "normal", "#555")
                        clip: true
                        SkinText { anchors.centerIn: parent; visible: avatar.status !== Image.Ready; text: account.initial; font.bold: true }
                        Image { id: avatar; anchors.fill: parent; source: account.avatarUrl; fillMode: Image.PreserveAspectCrop
                                visible: status === Image.Ready; asynchronous: true; cache: true }
                    }
                    Rectangle {
                        objectName: "accountStatusDot"
                        width: 10; height: 10; radius: 5
                        anchors { right: parent.right; bottom: parent.bottom; rightMargin: -2; bottomMargin: -2 }
                        color: account.statusDot
                        border.width: 2
                        border.color: Style.pick(Style.theme.components["window"].colors, "normal", "#111")
                    }
                }
                SkinText { anchors.verticalCenter: parent.verticalCenter; kind: "text.body"; font.bold: true
                           text: account.username; elide: Text.ElideRight; width: Math.min(implicitWidth, 150) }
                SkinText { anchors.verticalCenter: parent.verticalCenter; kind: "text.dim"; text: "▾" }
            }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: menu.open() }
        }
    }

    function openMenu() { menu.open() }

    SkinActionMenu {
        id: menu
        parent: C.Overlay.overlay
        entries: [
            { id: "profile", label: app.t("View Profile") },
            { separator: true },
            { heading: true, label: app.t("Set Status") },
            { id: "online", label: app.t("Online"), checked: account.status === "online" },
            { id: "away", label: app.t("Away"), checked: account.status === "away" },
            { id: "dnd", label: app.t("Do Not Disturb"), checked: account.status === "dnd" },
            { separator: true },
            { id: "signout", label: app.t("Sign Out"), tone: "danger" }
        ]
        onAboutToShow: {
            var o = me.mapToItem(C.Overlay.overlay, 0, me.height + 4)
            x = Math.max(6, Math.min(o.x + me.width - width, C.Overlay.overlay.width - width - 6))
            y = o.y
        }
        onChosen: function (id) {
            if (id === "profile") account.viewProfile()
            else if (id === "signout") account.signOut()
            else account.setStatus(id)
        }
    }
}
