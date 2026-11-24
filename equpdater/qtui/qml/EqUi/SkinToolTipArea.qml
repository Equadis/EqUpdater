import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// A skinned tooltip for the item it sits in (fills its parent, takes no clicks).
Item {
    id: root
    property string tip: ""
    anchors.fill: parent
    HoverHandler { id: hover }
    C.ToolTip {
        id: tt
        visible: hover.hovered && root.tip !== ""
        delay: 450
        text: root.tip
        readonly property var spec: Style.theme.components["tooltip"]
        contentItem: Text {
            text: tt.text
            wrapMode: Text.Wrap
            color: Style.pick(tt.spec.textColors, "normal", "#fff")
            font.family: Style.theme.fonts.dim.family
            font.pixelSize: Style.theme.fonts.dim.size
        }
        background: Rectangle {
            color: Style.pick(tt.spec.colors, "normal", "#222")
            border.color: Style.pick(tt.spec.borderColors, "normal", "#444")
            radius: 3
        }
    }
}
