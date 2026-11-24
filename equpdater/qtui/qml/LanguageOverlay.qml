import QtQuick
import EqUi

// First launch: one button per language, each in its own words (the player may
// read only one of them). The choice is the Language tweak.
Item {
    id: root
    objectName: "languageOverlay"
    visible: general.languagePromptOpen
    focus: visible
    Keys.onEscapePressed: general.chooseLanguage("")

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
    }
    SkinPanel {
        kind: "dialog"
        width: Math.min(360, parent.width - 30)
        height: col.implicitHeight + 40
        anchors.centerIn: parent
        TapHandler { }
        Column {
            id: col
            anchors { left: parent.left; right: parent.right; top: parent.top; topMargin: 4 }
            spacing: 8
            SkinText { kind: "panel.header"; width: parent.width; horizontalAlignment: Text.AlignHCenter
                       text: "Language · Sprache · Язык · 语言 · Idioma" }
            Repeater {
                model: general.languageChoices
                SkinButton {
                    width: col.width
                    selected: modelData.current
                    text: modelData.label
                    onClicked: general.chooseLanguage(modelData.code)
                }
            }
        }
    }
}
