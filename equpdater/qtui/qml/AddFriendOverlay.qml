import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// Add a friend by OctoWoW forum user name.
Item {
    id: root
    objectName: "addFriendOverlay"
    visible: social.addOpen
    focus: visible
    Keys.onEscapePressed: social.closeAdd()
    property string error: ""
    onVisibleChanged: if (visible) { nameField.text = ""; error = ""; nameField.forceActiveFocus() }

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler {
            onTapped: function (ep) {
                var l = card.mapFromItem(null, ep.scenePosition.x, ep.scenePosition.y)
                if (l.x < 0 || l.y < 0 || l.x > card.width || l.y > card.height) social.closeAdd()
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
        Column {
            id: col
            width: parent.width
            spacing: 8
            SkinText { kind: "panel.header"; text: app.t("ADD FRIEND") }
            SkinSearchField {
                id: nameField
                objectName: "friendNameField"
                width: parent.width
                placeholder: app.t("OctoWoW forum user name")
                onEdited: function (t) { root.error = "" }
            }
            SkinText { objectName: "addFriendError"; visible: root.error !== ""; kind: "text.error"; width: parent.width; wrapMode: Text.Wrap; text: root.error }
            Row {
                spacing: 8
                SkinButton {
                    id: confirm
                    objectName: "addFriendConfirm"
                    kind: "button.primary"; text: app.t("Add Friend")
                    onClicked: {
                        var err = social.addFriend(nameField.text)
                        if (err === "") social.closeAdd(); else root.error = err
                    }
                }
                SkinButton { kind: "button.small"; text: app.t("Cancel"); onClicked: social.closeAdd() }
            }
        }
    }
}
