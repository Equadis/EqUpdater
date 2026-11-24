import QtQuick
import EqUi

// The one modal question the Python side may ask at a time (dialog.ask blocks
// its caller in a nested event loop until answer() is called).
Item {
    id: root
    objectName: "dialogHost"
    visible: dialog.open
    focus: dialog.open
    Keys.onEscapePressed: dialog.answer(false)
    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler { }                                  // modal: swallow clicks
    }
    SkinPanel {
        kind: "dialog"
        width: Math.min(520, parent.width - 60)
        height: col.implicitHeight + 40
        anchors.centerIn: parent
        Column {
            id: col
            width: parent.width
            spacing: 14
            SkinText { kind: "panel.header"; text: dialog.title; width: parent.width
                       wrapMode: Text.Wrap }
            SkinText { kind: "text.body"; text: dialog.body; width: parent.width
                       wrapMode: Text.Wrap }
            Row {
                spacing: 10
                anchors.right: parent.right
                SkinButton {
                    visible: dialog.kind === "yes_no" || dialog.kind === "ok_cancel" || dialog.kind === "yes_no_cancel"
                    kind: "button.primary"
                    text: dialog.kind === "ok_cancel" ? app.t("OK") : app.t("Yes")
                    onClicked: dialog.answer(true)
                }
                SkinButton {
                    visible: dialog.kind === "yes_no" || dialog.kind === "ok_cancel" || dialog.kind === "yes_no_cancel"
                    text: dialog.kind === "ok_cancel" ? app.t("Cancel") : app.t("No")
                    onClicked: dialog.answer(false)
                }
                SkinButton {
                    visible: dialog.kind === "yes_no_cancel"
                    text: app.t("Cancel")
                    onClicked: dialog.cancel()
                }
                SkinButton {
                    visible: dialog.kind === "info" || dialog.kind === "error"
                    kind: "button.primary"
                    text: app.t("OK")
                    onClicked: dialog.answer(true)
                }
            }
        }
    }
}
