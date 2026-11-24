import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// An icon-only button from the skin (icon.*) with a skinned tooltip.
Item {
    id: root
    property string kind: "icon.link"
    property string tip: ""
    property int size: 16
    signal clicked()
    implicitWidth: size + 12
    implicitHeight: size + 12
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: tip
    Keys.onSpacePressed: if (enabled) clicked()
    SkinIcon {
        kind: root.kind
        size: root.size
        anchors.centerIn: parent
        opacity: !root.enabled ? 0.35 : hover.hovered ? 1.0 : 0.72
    }
    Rectangle {
        anchors.fill: parent
        visible: root.activeFocus && root.focusReason === Qt.TabFocusReason
        color: "transparent"
        border.color: Style.pick(Style.theme.components["text.link"].textColors, "normal", "#fff")
        radius: 3
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor; enabled: root.enabled }
    TapHandler { enabled: root.enabled; onTapped: root.clicked() }
    // The tooltip popup is created only while the pointer is over the control:
    // a Popup per control, built for every delegate that scrolls into view,
    // was the main cost of scrolling long lists (measured).
    Loader {
        active: hover.hovered && root.tip !== ""
        sourceComponent: Component {
            C.ToolTip {
                id: tt
                visible: true
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
    }
}
