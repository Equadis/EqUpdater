import QtQuick
import EqUi

// A skinned numeric field with step buttons. Emits `committed(text)` when
// editing finishes (Enter, focus lost, or a step); the owner decides what the
// stored value is and sets `value` back, so out-of-range input never stays.
Item {
    id: root
    property int value: 0
    property int from: 0
    property int to: 100
    property int step: 1
    signal committed(string text)
    function commit(t) {
        committed(t)
        // the owner may clamp to the value already shown: put the field back
        Qt.callLater(function () { field.text = String(root.value) })
    }
    readonly property var spec: Style.theme.components["input"]
    readonly property string vstate: !enabled ? "disabled" : field.activeFocus ? "selected"
                                     : hover.hovered ? "hover" : "normal"
    implicitWidth: 130
    implicitHeight: Math.max(spec.minHeight, 28)
    Accessible.role: Accessible.SpinBox
    Accessible.name: qsTr("Value")

    Rectangle {
        anchors.fill: parent
        color: Style.pick(root.spec.colors, root.vstate, "#222")
        border.color: Style.pick(root.spec.borderColors, root.vstate, "#555")
        border.width: root.spec.borderWidth
        radius: root.spec.radius
    }
    SkinButton {
        id: minus
        kind: "button.small"; text: "−"
        width: 26; height: parent.height - 4
        x: 2; y: 2
        enabled: root.enabled && root.value > root.from
        onClicked: root.commit(String(root.value - root.step))
        Accessible.name: qsTr("Decrease")
    }
    SkinButton {
        id: plus
        kind: "button.small"; text: "+"
        width: 26; height: parent.height - 4
        x: parent.width - width - 2; y: 2
        enabled: root.enabled && root.value < root.to
        onClicked: root.commit(String(root.value + root.step))
        Accessible.name: qsTr("Increase")
    }
    TextInput {
        id: field
        anchors { left: minus.right; right: plus.left; verticalCenter: parent.verticalCenter }
        horizontalAlignment: Text.AlignHCenter
        text: String(root.value)
        enabled: root.enabled
        selectByMouse: true
        inputMethodHints: Qt.ImhDigitsOnly
        validator: IntValidator { bottom: -99999999; top: 99999999 }
        color: Style.pick(root.spec.textColors, root.enabled ? "normal" : "disabled", "#fff")
        font.family: root.spec.font.family
        font.pixelSize: root.spec.font.size
        onEditingFinished: root.commit(text)
        Keys.onUpPressed: root.commit(String(root.value + root.step))
        Keys.onDownPressed: root.commit(String(root.value - root.step))
        // the owner may refuse or clamp: show what it stored
        Connections { target: root; function onValueChanged() { field.text = String(root.value) } }
    }
    HoverHandler { id: hover }
}
