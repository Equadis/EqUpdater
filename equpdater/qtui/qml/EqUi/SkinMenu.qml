import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// A skinned popup list: menu (frame) + menu.item rows. Entries are checkable
// ({label, checked}) - what the Play arrow needs.
C.Popup {
    id: root
    property string heading: ""
    property var entries: []
    signal chosen(string label)
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
        SkinText {
            visible: root.heading !== ""
            kind: "text.heading"
            text: root.heading
            leftPadding: 10; rightPadding: 10; topPadding: 6; bottomPadding: 6
            font.pixelSize: Style.theme.fonts.body.size
        }
        Rectangle {
            visible: root.heading !== ""
            width: parent.width; height: 1
            color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
        }
        Repeater {
            model: root.entries
            delegate: Item {
                width: Math.max(220, implicitWidth)
                implicitWidth: cb.implicitWidth + 20
                implicitHeight: Style.theme.components["menu.item"].minHeight
                Rectangle {
                    anchors.fill: parent
                    color: Style.pick(Style.theme.components["menu.item"].colors,
                                      ma.containsMouse ? "hover" : "normal", "transparent")
                }
                SkinCheckBox {
                    id: cb
                    x: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.label
                    checked: modelData.checked
                    onToggled: root.chosen(modelData.label)
                }
                MouseArea {
                    id: ma
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: root.chosen(modelData.label)
                }
            }
        }
    }
}
