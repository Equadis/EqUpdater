import QtQuick
import QtQuick.Controls.Basic as C
import Qt.labs.qmlmodels
import EqUi

// The friends list, on every page and in every skin (component "sidebar"). Always there:
// no collapse, no filters, no sort. Favourites first, then by presence, offline last.
// Each friend is the forum user name, a presence dot, and "Status ・ Realm" (the realm only
// while playing). The list is local; statuses come from a presence service when one exists
// and read "Unavailable" otherwise: never "Offline" just because a friend is not in EqUpdater.
Item {
    id: root
    objectName: "socialSidebar"
    readonly property real fullWidth: Style.theme.layout.sidebarWidth || 260
    // A little narrower in a small window so the page beside it keeps its room.
    width: win.width < 1000 ? Math.min(fullWidth, 210) : fullWidth
    clip: true

    SkinPanel {
        anchors.fill: parent
        kind: "sidebar"

        Item {
            anchors.fill: parent
            Row {
                id: head
                anchors { left: parent.left; right: parent.right; top: parent.top }
                height: 28
                spacing: 8
                SkinText { kind: "panel.header"; text: app.t("FRIENDS"); anchors.verticalCenter: parent.verticalCenter }
                SkinText { objectName: "onlineCount"; kind: "text.dim"; text: social.onlineText; anchors.verticalCenter: parent.verticalCenter }
            }
            SkinSearchField {
                id: search
                objectName: "friendSearch"
                anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 6 }
                placeholder: app.t("Search friends")
                onEdited: function (t) { social.setSearch(t) }
            }

            ListView {
                id: list
                objectName: "friendList"
                anchors { left: parent.left; right: parent.right; top: search.bottom; topMargin: 6; bottom: foot.top; bottomMargin: 6 }
                clip: true
                model: social.model
                boundsBehavior: Flickable.StopAtBounds
                reuseItems: true
                C.ScrollBar.vertical: SkinScrollBar {}
                delegate: DelegateChooser {
                    role: "kind"
                    DelegateChoice {
                        roleValue: "header"
                        delegate: Item {
                            width: ListView.view.width - 8
                            height: 26
                            SkinText { anchors.verticalCenter: parent.verticalCenter; kind: "text.heading"
                                       font.pixelSize: Style.theme.fonts.body.size
                                       text: model.title + "  " + model.count }
                        }
                    }
                    DelegateChoice {
                        delegate: Item {
                            id: entry
                            objectName: "friendRow"
                            width: ListView.view.width - 8
                            height: 40
                            readonly property bool away: model.state === "unknown" || model.state === "offline"
                            opacity: model.stale ? 0.65 : 1
                            Rectangle {
                                anchors.fill: parent
                                color: Style.pick(Style.theme.components["menu.item"].colors, hh.hovered ? "hover" : "normal", "transparent")
                                radius: 3
                            }
                            Item {                                   // avatar (the first letter until a service gives one)
                                id: avatar
                                x: 6; anchors.verticalCenter: parent.verticalCenter
                                width: 28; height: 28
                                opacity: entry.away ? 0.6 : 1
                                Rectangle {
                                    anchors.fill: parent; radius: 14
                                    color: Style.pick(Style.theme.components["button"].colors, "normal", "#333")
                                    border.width: 1
                                    border.color: Style.pick(Style.theme.components["button"].borderColors, "normal", "#555")
                                    SkinText { anchors.centerIn: parent; text: (model.name || "?").charAt(0).toUpperCase(); font.bold: true }
                                }
                                Rectangle {
                                    id: dot
                                    objectName: "friendDot"
                                    width: 10; height: 10; radius: 5
                                    anchors { right: parent.right; bottom: parent.bottom; rightMargin: -2; bottomMargin: -2 }
                                    color: model.dot
                                    border.width: 2
                                    border.color: Style.pick(Style.theme.components["window"].colors, "normal", "#111")
                                }
                            }
                            Column {
                                anchors { left: avatar.right; leftMargin: 10; right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter }
                                spacing: 1
                                SkinText {
                                    objectName: "friendName"
                                    width: parent.width
                                    text: model.name
                                    font.bold: true
                                    elide: Text.ElideRight
                                }
                                Row {                                // "In-game · Realm": the separator is drawn, not typed
                                    objectName: "friendStatus"
                                    width: parent.width
                                    spacing: 5
                                    SkinText { id: st; kind: "text.dim"; text: model.status
                                               width: Math.min(implicitWidth, parent.width) ; elide: Text.ElideRight }
                                    Rectangle { visible: model.realm !== ""; width: 3; height: 3; radius: 1.5
                                                anchors.verticalCenter: parent.verticalCenter
                                                color: Style.pick(Style.theme.components["text.dim"].textColors, "normal", "#888") }
                                    SkinText { visible: model.realm !== ""; kind: "text.dim"; text: model.realm
                                               width: Math.max(0, Math.min(implicitWidth, parent.width - st.width - 14)); elide: Text.ElideRight }
                                }
                            }
                            HoverHandler { id: hh }
                            SkinToolTipArea { tip: model.statusName }
                            TapHandler { acceptedButtons: Qt.RightButton
                                         onTapped: { ctx.friendId = model.id; ctx.fav = model.favourite; ctx.label = model.name; ctx.popup() } }
                            Accessible.role: Accessible.ListItem
                            Accessible.name: model.name + ", " + model.statusLine
                        }
                    }
                }
            }
            SkinText {
                visible: social.friendCount === 0
                anchors { left: parent.left; right: parent.right; top: search.bottom; topMargin: 24 }
                kind: "text.dim"; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter
                text: app.t("No friends yet.")
            }
            SkinButton {
                id: foot
                objectName: "addFriendButton"
                kind: "button"
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                text: app.t("Add Friend")
                onClicked: social.openAdd()
            }
        }
    }

    TextEdit { id: copier; visible: false }

    SkinActionMenu {
        id: ctx
        objectName: "friendMenu"
        property string friendId: ""
        property bool fav: false
        property string label: ""
        parent: C.Overlay.overlay
        function popup() { x = Math.max(6, root.mapToItem(C.Overlay.overlay, 0, 0).x - 170)
                           y = Math.min(C.Overlay.overlay.height - 130, root.mapToItem(C.Overlay.overlay, 0, 0).y + 120); open() }
        entries: [
            { id: "fav", label: ctx.fav ? app.t("Unfavourite") : app.t("Favourite") },
            { id: "copy", label: app.t("Copy Name") },
            { separator: true },
            { id: "remove", label: app.t("Remove Friend"), tone: "danger" }
        ]
        onChosen: function (id) {
            if (id === "fav") social.toggleFavourite(friendId)
            else if (id === "remove") social.removeFriend(friendId)
            else if (id === "copy") { copier.text = label; copier.selectAll(); copier.copy() }
        }
    }
}
