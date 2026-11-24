import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// Tweaks: one scrolling list (the only scroll authority on the page), rows
// from the Python model, edits go back through tweaks.setValue (clamped there).
SkinPanel {
    id: root
    property bool confirmReset: false
    Component.onCompleted: tweaks.open()

    Item {
        id: head
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 34
        SkinText { kind: "panel.header"; text: app.t("TWEAKS"); anchors.verticalCenter: parent.verticalCenter }
    }
    Rectangle {
        id: rule
        anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 6 }
        height: 1
        color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
    }

    ListView {
        id: list
        anchors { left: parent.left; right: parent.right; top: rule.bottom; bottom: actions.top
                  topMargin: 6; bottomMargin: 8 }
        clip: true
        model: tweaks.model
        boundsBehavior: Flickable.StopAtBounds
        rightMargin: 12
        C.ScrollBar.vertical: SkinScrollBar {}
        delegate: Item {
            id: d
            width: ListView.view.width - 16
            height: model.kind === "section" ? 38 : Math.max(46, rowCol.implicitHeight + 12)
            SkinText {
                visible: model.kind === "section"
                kind: "text.heading"
                text: app.t(model.label)
                anchors { left: parent.left; bottom: parent.bottom; bottomMargin: 4 }
            }
            Item {
                visible: model.kind !== "section"
                anchors.fill: parent
                Column {
                    id: rowCol
                    anchors { left: parent.left; right: control.left; rightMargin: 16
                              verticalCenter: parent.verticalCenter }
                    spacing: 2
                    SkinText {
                        kind: "text.body"
                        text: app.t(model.label) + (model.changed ? "  •" : "")
                        font.bold: true
                    }
                    SkinText {
                        kind: "text.dim"
                        width: parent.width
                        wrapMode: Text.Wrap
                        visible: text !== ""
                        text: (model.tip !== "" ? app.t(model.tip) : "")
                              + (model.id === "fieldOfView"
                                 ? "  " + app.t("Suggested for this display:") + " " + tweaks.fovHint : "")
                    }
                }
                Item {
                    id: control
                    width: 150
                    height: 30
                    anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    SkinNumberField {
                        visible: model.kind === "number"
                        anchors.fill: parent
                        value: model.kind === "number" ? model.value : 0
                        from: model.min !== undefined ? model.min : 0
                        to: model.max !== undefined ? model.max : 100
                        step: model.step || 1
                        enabled: !tweaks.busy
                        onCommitted: function (t) { tweaks.setValue(model.id, t) }
                    }
                    SkinCheckBox {
                        visible: model.kind === "checkbox"
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        checked: model.value === true
                        enabled: !tweaks.busy
                        text: checked ? app.t("On") : app.t("Off")
                        onToggled: tweaks.setValue(model.id, !checked)
                    }
                    SkinButton {
                        id: langBtn
                        visible: model.kind === "dropdown"
                        anchors.fill: parent
                        iconKind: "icon.arrow-down"
                        iconSize: 10
                        enabled: !tweaks.busy
                        text: {
                            if (model.kind !== "dropdown") return ""
                            var ch = model.choices || []
                            for (var i = 0; i < ch.length; i++)
                                if (ch[i].code === model.value) return ch[i].label
                            return model.value
                        }
                        onClicked: langMenu.open()
                        SkinMenu {
                            id: langMenu
                            parent: langBtn
                            y: langBtn.height + 2
                            entries: {
                                var ch = model.choices || [], out = []
                                for (var i = 0; i < ch.length; i++)
                                    out.push({ label: ch[i].label, checked: ch[i].code === model.value })
                                return out
                            }
                            onChosen: function (label) {
                                var ch = model.choices || []
                                for (var i = 0; i < ch.length; i++)
                                    if (ch[i].label === label) tweaks.setValue(model.id, ch[i].code)
                                close()
                            }
                        }
                    }
                }
                Rectangle {
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                    height: 1
                    color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
                    opacity: 0.5
                }
            }
        }
    }

    Item {
        id: actions
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: root.confirmReset ? 76 : 40
        Column {
            anchors.fill: parent
            spacing: 6
            SkinText {
                visible: tweaks.message !== ""
                kind: tweaks.messageIsError ? "text.error" : "text.ok"
                text: tweaks.message
                width: parent.width
                elide: Text.ElideRight
            }
            Row {
                spacing: 10
                visible: !root.confirmReset
                SkinButton {
                    kind: "button.primary"
                    text: tweaks.busy ? app.t("Patching…") : app.t("Apply")
                    enabled: tweaks.dirty && !tweaks.busy
                    onClicked: tweaks.apply()
                }
                SkinButton {
                    text: app.t("Reset to defaults")
                    enabled: !tweaks.busy
                    onClicked: root.confirmReset = true
                }
                SkinText {
                    visible: !tweaks.hasGame
                    kind: "text.dim"
                    anchors.verticalCenter: parent.verticalCenter
                    text: app.t("WoW.exe not found – run Update first.")
                }
            }
            Row {
                spacing: 10
                visible: root.confirmReset
                SkinText {
                    kind: "text.body"
                    anchors.verticalCenter: parent.verticalCenter
                    text: app.t("Every tweak goes back to its default value.") + " "
                          + app.t("Your current values are not kept.")
                }
                SkinButton { kind: "button.primary"; text: app.t("Reset tweaks?")
                             onClicked: { root.confirmReset = false; tweaks.resetToDefaults() } }
                SkinButton { text: app.t("Cancel"); onClicked: root.confirmReset = false }
            }
        }
    }
}
