import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// Settings (proof of concept): skin choice and the animation options.
// Skins are only selected here: open the folder, pick one, restore the default.
Item {
    id: root
    visible: false
    function open() { visible = true; settings.rescanSkins() }
    function close() { visible = false }
    focus: visible
    Keys.onEscapePressed: close()

    Rectangle {
        anchors.fill: parent
        color: Style.pick(skin.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler { onTapped: root.close() }
    }
    SkinPanel {
        id: box
        width: Math.min(560, parent.width - 60)
        height: Math.min(560, parent.height - 60)
        anchors.centerIn: parent
        TapHandler { }                                    // swallow clicks
        Column {
            anchors.fill: parent
            spacing: 12
            Item {
                width: parent.width; height: 30
                SkinText { kind: "panel.header"; text: app.t("SETTINGS")
                           anchors.verticalCenter: parent.verticalCenter }
                SkinButton { kind: "button.small"; text: app.t("Close")
                             anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                             onClicked: root.close() }
            }
            SkinText { kind: "text.heading"; text: app.t("Skin")
                       font.pixelSize: skin.theme.fonts.body.size }
            ListView {
                id: skins
                width: parent.width
                height: Math.min(contentHeight, 190)
                clip: true
                spacing: 4
                model: settings.skins
                C.ScrollBar.vertical: SkinScrollBar {}
                delegate: SkinButton {
                    width: skins.width - 12
                    selected: modelData.id === settings.currentSkin
                    text: modelData.name + (modelData.author !== "" ? "  ·  " + modelData.author : "")
                          + (modelData.problems.length > 0 ? "   ⚠" : "")
                    onClicked: settings.setSkin(modelData.id)
                }
            }
            SkinText {
                visible: settings.skinProblems.length > 0
                width: parent.width
                kind: "text.dim"
                wrapMode: Text.Wrap
                text: settings.skinProblems.join("\n")
                maximumLineCount: 6
                elide: Text.ElideRight
            }
            Row {
                spacing: 10
                SkinButton { kind: "button.small"; text: app.t("Open skins folder")
                             onClicked: settings.openSkinsFolder() }
                SkinButton { kind: "button.small"; text: app.t("Restore default skin")
                             onClicked: settings.restoreDefaultSkin() }
            }
            Rectangle { width: parent.width; height: 1
                        color: Style.pick(skin.theme.components["divider"].colors, "normal", "#444") }
            SkinCheckBox { text: app.t("Animated background")
                           checked: settings.animatedBackground
                           onToggled: settings.animatedBackground = !settings.animatedBackground }
            SkinCheckBox { text: app.t("Pause animation when the window is not focused")
                           checked: settings.pauseWhenUnfocused
                           onToggled: settings.pauseWhenUnfocused = !settings.pauseWhenUnfocused }
        }
    }
}
