import QtQuick
import EqUi

Item {
    id: root
    property real value: 0          // 0..1
    property bool indeterminate: false
    readonly property var spec: skin.theme.components["progress"]
    implicitHeight: 10
    Accessible.role: Accessible.ProgressBar
    Rectangle {
        anchors.fill: parent
        color: Style.pick(root.spec.colors, "normal", "#222")
        border.color: Style.pick(root.spec.borderColors, "normal", "transparent")
        border.width: 1
        radius: root.spec.radius
        clip: true
        Rectangle {
            id: fill
            x: 1; y: 1; height: parent.height - 2
            width: root.indeterminate ? parent.width * 0.3 : Math.max(0, (parent.width - 2) * root.value)
            radius: root.spec.radius
            color: Style.pick(root.spec.colors, "selected", "#fa0")
        }
        // only animates while indeterminate: no idle rendering otherwise
        SequentialAnimation {
            running: root.indeterminate && root.visible
            loops: Animation.Infinite
            NumberAnimation { target: fill; property: "x"; from: 0; to: root.width * 0.7; duration: 900; easing.type: Easing.InOutQuad }
            NumberAnimation { target: fill; property: "x"; to: 0; duration: 900; easing.type: Easing.InOutQuad }
        }
    }
}
