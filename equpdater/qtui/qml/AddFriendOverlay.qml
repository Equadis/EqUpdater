import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// Add a friend by character name and realm (and, where a service can resolve one,
// by forum user name). The realm list comes from OctoBot's published list.
Item {
    id: root
    objectName: "addFriendOverlay"
    visible: social.addOpen
    focus: visible
    Keys.onEscapePressed: social.closeAdd()
    property string realm: social.realmFilter !== "" ? social.realmFilter : (social.realms.length ? social.realms[0].id : "")
    property string error: ""
    onVisibleChanged: if (visible) { nameField.text = ""; forumField.text = ""; error = "" }

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler { onTapped: social.closeAdd() }
    }
    SkinPanel {
        kind: "dialog"
        width: Math.min(420, parent.width - 40)
        height: col.implicitHeight + padTop + padBottom
        anchors.centerIn: parent
        TapHandler { }
        Column {
            id: col
            width: parent.width
            spacing: 8
            SkinText { kind: "panel.header"; text: app.t("ADD FRIEND") }
            SkinText { kind: "text.body"; text: app.t("Character name") }
            SkinSearchField {
                id: nameField
                objectName: "friendNameField"
                width: parent.width
                placeholder: app.t("Name")
                onEdited: function (t) { root.error = "" }
            }
            SkinText { kind: "text.body"; text: app.t("Realm") }
            Flow {
                width: parent.width
                spacing: 6
                Repeater {
                    model: social.realms
                    SkinButton { objectName: "realm_" + modelData.id; kind: "button.small"
                                 text: modelData.name + (modelData.kind !== "" ? "  (" + modelData.kind + ")" : "")
                                 selected: root.realm === modelData.id
                                 onClicked: root.realm = modelData.id }
                }
            }
            SkinText { visible: !social.realmsAuthoritative; kind: "text.dim"; width: parent.width; wrapMode: Text.Wrap
                       text: app.t("The realm list could not be read from OctoBot; these are the usual realms.") }
            Column {
                visible: social.accountFriends
                width: parent.width
                spacing: 4
                SkinText { kind: "text.body"; text: app.t("Or a forum user name") }
                SkinSearchField { id: forumField; objectName: "friendForumField"; width: parent.width; placeholder: app.t("Forum user name") }
            }
            SkinText { visible: !social.accountFriends; kind: "text.dim"; width: parent.width; wrapMode: Text.Wrap
                       text: app.t("Adding friends by forum user name needs an OctoWoW service that can show an account's characters. There is none yet.") }
            SkinText { objectName: "addFriendError"; visible: root.error !== ""; kind: "text.error"; width: parent.width; wrapMode: Text.Wrap; text: root.error }
            Row {
                spacing: 8
                SkinButton {
                    objectName: "addFriendConfirm"
                    kind: "button.primary"; text: app.t("Add Friend")
                    onClicked: {
                        var err = social.addFriend(nameField.text, root.realm, forumField.text)
                        if (err === "") social.closeAdd(); else root.error = err
                    }
                }
                SkinButton { kind: "button.small"; text: app.t("Cancel"); onClicked: social.closeAdd() }
            }
        }
    }
}
