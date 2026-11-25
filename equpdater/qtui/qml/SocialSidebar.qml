import QtQuick
import QtQuick.Controls.Basic as C
import Qt.labs.qmlmodels
import EqUi

// The friends sidebar: available on every page, in every skin (component "sidebar").
// Collapses to a narrow rail. The list is local; statuses come from a presence
// service if one exists, and are shown as "unavailable" otherwise: never as
// "offline" just because a friend is not using EqUpdater.
Item {
    id: root
    objectName: "socialSidebar"
    readonly property bool collapsed: social.collapsed
    readonly property real expandedWidth: Style.theme.layout.sidebarWidth || 260
    readonly property real railWidth: 46
    width: collapsed ? railWidth : expandedWidth
    visible: social.enabled
    clip: true

    // Narrow window: an open sidebar floats over the page, so it needs an opaque backing or the
    // page's text shows through the translucent panel.
    Rectangle {
        anchors.fill: parent
        visible: !root.collapsed && !win.sidebarDocked
        color: Style.theme.background.color
    }

    SkinPanel {
        anchors.fill: parent
        kind: "sidebar"

        // ── collapsed rail ──
        Column {
            visible: root.collapsed
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 8
            SkinButton {
                objectName: "sidebarExpand"
                kind: "button.small"
                text: "‹"
                onClicked: social.toggleCollapsed()
                SkinToolTipArea { tip: app.t("Show friends") }
            }
            SkinText { anchors.horizontalCenter: parent.horizontalCenter; kind: "text.dim"; text: social.friendCount > 0 ? social.onlineText.split(" ")[0] : "" }
        }

        // ── expanded ──
        Item {
            visible: !root.collapsed
            anchors.fill: parent
            Row {
                id: head
                anchors { left: parent.left; right: parent.right; top: parent.top }
                height: 28
                spacing: 8
                SkinText { kind: "panel.header"; text: app.t("FRIENDS"); anchors.verticalCenter: parent.verticalCenter }
                SkinText { objectName: "onlineCount"; kind: "text.dim"; text: social.onlineText; anchors.verticalCenter: parent.verticalCenter }
            }
            SkinButton {
                objectName: "sidebarCollapse"
                kind: "button.small"
                anchors { right: parent.right; top: parent.top }
                text: "›"
                onClicked: social.toggleCollapsed()
                SkinToolTipArea { tip: app.t("Hide friends") }
            }
            SkinSearchField {
                id: search
                objectName: "friendSearch"
                anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 8 }
                placeholder: app.t("Search friends")
                onEdited: function (t) { social.setSearch(t) }
            }
            Flow {
                id: filters
                anchors { left: parent.left; right: parent.right; top: search.bottom; topMargin: 6 }
                spacing: 4
                SkinButton { kind: "button.small"; text: app.t("All"); selected: social.realmFilter === ""
                             onClicked: social.setRealmFilter("") }
                Repeater {
                    model: social.realms
                    SkinButton { kind: "button.small"; text: modelData.name; selected: social.realmFilter === modelData.id
                                 onClicked: social.setRealmFilter(social.realmFilter === modelData.id ? "" : modelData.id) }
                }
                SkinButton {
                    objectName: "sortButton"
                    kind: "button.small"
                    text: app.t("Sort") + ": " + (social.sort === "name" ? app.t("Name") : social.sort === "realm" ? app.t("Realm") : app.t("Status"))
                    onClicked: social.setSort(social.sort === "status" ? "name" : social.sort === "name" ? "realm" : "status")
                }
            }

            ListView {
                id: list
                objectName: "friendList"
                anchors { left: parent.left; right: parent.right; top: filters.bottom; topMargin: 6; bottom: foot.top; bottomMargin: 6 }
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
                            height: model.detail !== "" ? 46 : 34
                            readonly property bool unknown: model.state === "unknown" || model.state === "offline"
                            opacity: model.stale ? 0.65 : 1
                            Rectangle {
                                anchors.fill: parent
                                color: Style.pick(Style.theme.components["menu.item"].colors, hh.hovered ? "hover" : "normal", "transparent")
                                radius: 3
                            }
                            Rectangle {
                                id: dot
                                objectName: "friendDot"
                                x: 6; y: 12
                                width: 10; height: 10; radius: 5
                                color: entry.unknown ? "transparent" : model.dot
                                border.width: entry.unknown ? 2 : 0
                                border.color: model.dot
                                SkinText { visible: model.state === "unknown"; anchors.centerIn: parent; text: "?"; font.pixelSize: 8; kind: "text.dim" }
                            }
                            Column {
                                anchors { left: dot.right; leftMargin: 8; right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter }
                                spacing: 1
                                Row {
                                    spacing: 6
                                    SkinText {
                                        objectName: "friendName"
                                        text: (model.favourite ? "★ " : "") + model.name
                                        font.bold: true
                                        color: model.nameColour !== "" ? model.nameColour
                                               : Style.pick(Style.theme.components["text.body"].textColors, "normal", "#fff")
                                        width: Math.min(implicitWidth, entry.width * 0.58); elide: Text.ElideRight
                                    }
                                    SkinText { kind: "text.dim"; text: model.realmName; anchors.baseline: undefined
                                               width: Math.min(implicitWidth, entry.width * 0.3); elide: Text.ElideRight }
                                }
                                SkinText { visible: model.detail !== ""; kind: "text.dim"; width: entry.width - 30
                                           text: model.detail; elide: Text.ElideRight }
                            }
                            HoverHandler { id: hh }
                            TapHandler { acceptedButtons: Qt.RightButton
                                         onTapped: { ctx.friendId = model.id; ctx.fav = model.favourite; ctx.label = model.name; ctx.popup() } }
                            SkinToolTipArea { tip: model.stateText + (model.stale ? " · " + app.t("last update is old") : "")
                                                   + (model.realmName !== "" ? "\n" + model.realmName : "") }
                            Accessible.role: Accessible.ListItem
                            Accessible.name: model.name + ", " + model.stateText
                        }
                    }
                }
            }
            SkinText {
                visible: social.friendCount === 0
                anchors { left: parent.left; right: parent.right; top: filters.bottom; topMargin: 24 }
                kind: "text.dim"; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter
                text: app.t("No friends yet. Add a character by name and realm.")
            }
            Column {
                id: foot
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                spacing: 6
                SkinText { visible: social.sourceNote !== ""; width: parent.width; kind: "text.dim"; wrapMode: Text.Wrap
                           objectName: "sourceNote"; text: social.sourceNote; maximumLineCount: 4; elide: Text.ElideRight }
                SkinButton { objectName: "addFriendButton"; kind: "button"; width: parent.width; text: app.t("Add Friend"); onClicked: social.openAdd() }
            }
        }
    }

    TextEdit { id: copier; visible: false }

    SkinActionMenu {
        id: ctx
        property string friendId: ""
        property bool fav: false
        property string label: ""
        parent: C.Overlay.overlay
        function popup() { x = Math.max(6, root.mapToItem(C.Overlay.overlay, 0, 0).x - 170)
                           y = Math.min(C.Overlay.overlay.height - 130, root.mapToItem(C.Overlay.overlay, 0, 0).y + 120); open() }
        entries: [
            { id: "fav", label: ctx.fav ? app.t("Remove from Favourites") : app.t("Add to Favourites") },
            { id: "copy", label: app.t("Copy Name") },
            { separator: true },
            { id: "remove", label: app.t("Remove Friend"), tone: "danger" }
        ]
        onChosen: function (id) {
            if (id === "fav") social.toggleFavourite(friendId)
            else if (id === "remove") social.removeFriend(friendId)
            else if (id === "copy") { copier.text = label.replace(/^★ /, ""); copier.selectAll(); copier.copy() }
        }
    }
}
