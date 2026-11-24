import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// Add a mod or addon EqUpdater does not list. Nothing is installed until the
// warning that follows has been confirmed.
Item {
    id: root
    objectName: "customOverlay"
    visible: customSrc.isOpen
    focus: visible
    Keys.onEscapePressed: customSrc.close()
    readonly property bool isMod: customSrc.kind === "mod"
    function tone(t) { return t === "err" ? "text.error" : t === "ok" ? "text.ok" : "text.dim" }

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler { onTapped: customSrc.close() }
    }
    SkinPanel {
        kind: "dialog"
        width: Math.min(580, parent.width - 40)
        height: Math.min(root.isMod ? 420 : 260, parent.height - 40)
        anchors.centerIn: parent
        TapHandler { }

        Item {
            id: head
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 30
            SkinText { kind: "panel.header"; anchors.verticalCenter: parent.verticalCenter
                       text: root.isMod ? app.t("ADD CUSTOM GIT MOD") : app.t("ADD CUSTOM GIT ADDON") }
            SkinButton { kind: "button.small"; text: app.t("Close")
                         anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                         onClicked: customSrc.close() }
        }
        Column {
            anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 10; bottom: parent.bottom }
            spacing: 8
            SkinText { kind: "text.heading"; text: root.isMod ? app.t("RELEASE LINK") : app.t("REPOSITORY URL") }
            SkinSearchField {
                id: link
                objectName: "customLink"
                width: parent.width
                placeholder: root.isMod ? "https://github.com/owner/repo" : "https://github.com/owner/addon"
                onEdited: function (t) { customSrc.linkChanged(t) }
                Connections { target: customSrc; function onOpenChanged() { if (customSrc.isOpen) link.text = "" } }
            }
            SkinText {
                width: parent.width; kind: "text.dim"; wrapMode: Text.Wrap
                text: root.isMod ? app.t("A GitHub, Codeberg or OctoWoW Git repository, or one of its release downloads.")
                                 : customSrc.hosts
            }
            SkinText {
                visible: root.isMod
                width: parent.width; kind: "text.link"; wrapMode: Text.Wrap
                text: "⚠  " + app.t("A mod runs code inside the game. Only add mods you trust.")
            }
            SkinText {
                objectName: "customStatus"
                width: parent.width; wrapMode: Text.Wrap
                kind: root.tone(customSrc.statusTone); text: customSrc.statusText
            }
            Column {
                visible: root.isMod && customSrc.found
                width: parent.width
                spacing: 4
                Repeater {
                    model: customSrc.choices
                    SkinCheckBox {
                        text: modelData
                        checked: (customSrc.choice || customSrc.choices[0]) === modelData
                        onToggled: customSrc.choose(modelData)
                    }
                }
            }
            SkinButton {
                objectName: "customSubmit"
                kind: "button.primary"
                enabled: !customSrc.busy
                text: root.isMod ? (customSrc.found ? app.t("Install") : app.t("Find mod files")) : app.t("Install")
                onClicked: customSrc.submit(link.text)
            }
        }
    }
}
