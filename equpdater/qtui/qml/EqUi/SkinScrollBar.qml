import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// Thin overlay scrollbar, skinned. Attach with `C.ScrollBar.vertical: SkinScrollBar {}`.
C.ScrollBar {
    id: bar
    readonly property var spec: Style.theme.components["scrollbar"]
    readonly property int w: Style.theme.metrics.scrollbarWidth || 8
    policy: C.ScrollBar.AsNeeded
    minimumSize: 0.08
    padding: 2
    implicitWidth: w + 4
    contentItem: Item {
        implicitWidth: bar.w
        implicitHeight: 40
        Rectangle {
            anchors.fill: parent
            visible: !Style.has(bar.spec.textures, "normal")
            radius: bar.spec.radius
            color: Style.pick(bar.spec.colors, bar.pressed ? "pressed" : (bar.hovered ? "hover" : "selected"), "#888")
            opacity: bar.active ? 1.0 : 0.0
            Behavior on opacity { NumberAnimation { duration: Style.theme.metrics.animationMs || 120 } }
        }
        BorderImage {
            anchors.fill: parent
            visible: Style.has(bar.spec.textures, "normal")
            source: Style.pick(bar.spec.textures, bar.pressed ? "pressed" : (bar.hovered ? "hover" : "normal"), "")
            border.left: bar.spec.slice[0]; border.top: bar.spec.slice[1]
            border.right: bar.spec.slice[2]; border.bottom: bar.spec.slice[3]
        }
    }
    background: Rectangle {
        color: Style.pick(bar.spec.colors, "normal", "transparent")
    }
}
