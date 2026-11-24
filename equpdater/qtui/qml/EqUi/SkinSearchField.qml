import QtQuick
import EqUi

// A skinned single-line search/text field (component `input`). `edited(text)`
// fires on every change; the owner debounces.
Item {
    id: root
    property string text: ""
    property string placeholder: ""
    signal edited(string text)
    readonly property var spec: Style.theme.components["input"]
    readonly property string vstate: !enabled ? "disabled" : field.activeFocus ? "selected"
                                     : hover.hovered ? "hover" : "normal"
    implicitWidth: 220
    implicitHeight: Math.max(spec.minHeight, 28)
    Accessible.role: Accessible.EditableText
    Accessible.name: placeholder

    Rectangle {
        anchors.fill: parent
        color: Style.pick(root.spec.colors, root.vstate, "#222")
        border.color: Style.pick(root.spec.borderColors, root.vstate, "#555")
        border.width: root.spec.borderWidth
        radius: root.spec.radius
    }
    TextInput {
        id: field
        anchors { left: parent.left; right: clear.left; verticalCenter: parent.verticalCenter
                  leftMargin: root.spec.padding[0]; rightMargin: 4 }
        text: root.text
        selectByMouse: true
        clip: true
        color: Style.pick(root.spec.textColors, "normal", "#fff")
        font.family: root.spec.font.family
        font.pixelSize: root.spec.font.size
        onTextEdited: { root.text = text; root.edited(text) }
        Text {
            visible: field.text === "" && !field.activeFocus
            text: root.placeholder
            color: Style.pick(Style.theme.components["text.dim"].textColors, "normal", "#888")
            font: field.font
        }
    }
    SkinIconButton {
        id: clear
        visible: field.text !== ""
        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
        kind: "icon.remove"; size: 12
        tip: qsTr("Clear")
        onClicked: { field.text = ""; root.edited("") }
    }
    HoverHandler { id: hover }
}
