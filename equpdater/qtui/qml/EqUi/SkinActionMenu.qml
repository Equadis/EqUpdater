import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// A skinned popup of plain actions (the account menu, a friend's context menu).
// entries: [{id, label, checked?, enabled?, separator?, heading?, tone?, dot?}]  (dot: a colour, drawn before the label)
C.Popup {
    id: root
    property var entries: []
    signal chosen(string id)
    readonly property var spec: Style.theme.components["menu"]
    padding: spec.padding[0]
    closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
    modal: false
    focus: true
    background: Rectangle {
        color: Style.pick(root.spec.colors, "normal", "#222")
        border.color: Style.pick(root.spec.borderColors, "normal", "#444")
        border.width: root.spec.borderWidth
        radius: root.spec.radius
    }
    contentItem: Column {
        spacing: 0
        Repeater {
            model: root.entries
            delegate: Item {
                width: Math.max(190, implicitWidth)
                implicitWidth: label.implicitWidth + 36 + (modelData.dot ? 18 : 0)
                implicitHeight: modelData.separator ? 7 : Style.theme.components["menu.item"].minHeight
                readonly property bool actionable: !modelData.separator && !modelData.heading && modelData.enabled !== false
                Rectangle {
                    visible: modelData.separator === true
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter }
                    height: 1
                    color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
                }
                Rectangle {
                    anchors.fill: parent
                    visible: parent.actionable
                    color: Style.pick(Style.theme.components["menu.item"].colors, ma.containsMouse ? "hover" : "normal", "transparent")
                }
                Rectangle {
                    visible: !!modelData.dot
                    x: 12; width: 10; height: 10; radius: 5
                    anchors.verticalCenter: parent.verticalCenter
                    color: modelData.dot || "transparent"
                }
                SkinText {
                    id: label
                    visible: !modelData.separator
                    x: modelData.dot ? 30 : 12
                    anchors.verticalCenter: parent.verticalCenter
                    kind: modelData.heading ? "text.dim" : modelData.tone === "danger" ? "text.error"
                          : modelData.enabled === false ? "text.dim" : "text.body"
                    text: (modelData.checked ? "✓ " : modelData.checked === false ? "    " : "") + (modelData.label || "")
                }
                MouseArea {
                    id: ma
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: parent.actionable
                    onClicked: { root.chosen(modelData.id); root.close() }
                }
            }
        }
    }
}
