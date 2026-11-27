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
                        id: dotMark
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
            TapHandler {
                onTapped: function (ep) {
                    var l = dotHit.mapFromItem(null, ep.scenePosition.x, ep.scenePosition.y)
                    if (l.x < 0 || l.y < 0 || l.x > dotHit.width || l.y > dotHit.height) menu.open()
                }
            }
            // The presence dot is its own, larger click target: it opens the status menu.
            Item {
                id: dotHit
                objectName: "statusDotButton"
                x: 14; y: 14
                width: 20; height: 20
                Accessible.role: Accessible.Button
                Accessible.name: account.statusText
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: statusMenu.open() }
            }
        }
    }

    function openMenu() { menu.open() }

    SkinActionMenu {
        id: menu
        parent: C.Overlay.overlay
        entries: [
            { id: "profile", label: app.t("View Profile") },
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
        }
    }

    // The presence menu: opened from the dot beside the avatar. Four choices, nothing else.
    SkinActionMenu {
        id: statusMenu
        objectName: "statusMenu"
        parent: C.Overlay.overlay
        entries: {
            var out = [], ch = account.statusChoices
            for (var i = 0; i < ch.length; i++)
                out.push({ id: ch[i].id, label: ch[i].label, dot: ch[i].dot, checked: account.status === ch[i].id })
            return out
        }
        onAboutToShow: {
            var o = dotHit.mapToItem(C.Overlay.overlay, 0, dotHit.height + 4)
            x = Math.max(6, Math.min(o.x - 10, C.Overlay.overlay.width - width - 6))
            y = o.y
        }
        onChosen: function (id) { account.setStatus(id) }
    }
}
