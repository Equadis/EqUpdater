import QtQuick
import EqUi

Item {
    id: root
    property string text: ""
    property bool checked: false
    readonly property var spec: skin.theme.components["checkbox"]
    readonly property string vstate: Style.stateOf(enabled, false, false, hover.hovered)
    signal toggled()
    implicitWidth: row.implicitWidth
    implicitHeight: Math.max(18, label.implicitHeight)
    activeFocusOnTab: true
    Accessible.role: Accessible.CheckBox
    Accessible.name: text
    Accessible.checked: checked
    Keys.onSpacePressed: toggled()
    Row {
        id: row
        spacing: 8
        anchors.verticalCenter: parent.verticalCenter
        Item {
            width: 16; height: 16
            anchors.verticalCenter: parent.verticalCenter
            BorderImage {
                anchors.fill: parent
                visible: source != ""
                source: Style.pick(root.spec.textures, root.checked ? "selected" : root.vstate, "")
                border.left: root.spec.slice[0]; border.top: root.spec.slice[1]
                border.right: root.spec.slice[2]; border.bottom: root.spec.slice[3]
            }
            Rectangle {
                anchors.fill: parent
                visible: !Style.has(root.spec.textures, "normal")
                color: Style.pick(root.spec.colors, "normal", "transparent")
                border.color: Style.pick(root.spec.borderColors, "normal", "#888")
                border.width: 1
                radius: 2
                Rectangle {
                    anchors.centerIn: parent
                    width: 8; height: 8; radius: 1
                    visible: root.checked
                    color: Style.pick(root.spec.colors, "selected", "#fff")
                }
            }
        }
        Text {
            id: label
            text: root.text
            color: Style.pick(root.spec.textColors, root.vstate, "#fff")
            font.family: skin.theme.fonts.body.family
            font.pixelSize: skin.theme.fonts.body.size
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: root.toggled() }
}
