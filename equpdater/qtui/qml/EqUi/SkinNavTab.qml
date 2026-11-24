import QtQuick
import EqUi

Item {
    id: root
    property string text: ""
    property bool selected: false
    property int badge: 0
    readonly property var spec: Style.theme.components["nav.tab"]
    readonly property string vstate: Style.stateOf(enabled, false, selected, hover.hovered)
    signal clicked()
    implicitWidth: label.implicitWidth + spec.padding[0] + spec.padding[2]
    implicitHeight: label.implicitHeight + spec.padding[1] + spec.padding[3]
    activeFocusOnTab: true
    Accessible.role: Accessible.PageTab
    Accessible.name: text
    Keys.onSpacePressed: clicked()
    Keys.onReturnPressed: clicked()

    BorderImage {
        anchors.fill: parent
        visible: source != ""
        source: Style.pick(root.spec.textures, root.vstate, "")
        border.left: root.spec.slice[0]; border.top: root.spec.slice[1]
        border.right: root.spec.slice[2]; border.bottom: root.spec.slice[3]
    }
    Text {
        id: label
        anchors.centerIn: parent
        text: root.text
        color: Style.pick(root.spec.textColors, root.vstate, "#fff")
        font.family: root.spec.font.family
        font.pixelSize: root.spec.font.size
        font.bold: !!root.spec.font.bold
        font.letterSpacing: 0.5
    }
    Rectangle {                         // the active indicator
        visible: root.selected
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        width: label.width
        height: 2
        color: Style.pick(root.spec.textColors, "selected", "#fff")
    }
    Rectangle {                         // count badge (updates available)
        visible: root.badge > 0
        x: label.x + label.width - 4
        y: label.y - 6
        width: Math.max(16, bt.implicitWidth + 8)
        height: 16
        radius: 8
        color: Style.pick(Style.theme.components["badge"].colors, "normal", "#fa0")
        Text {
            id: bt
            anchors.centerIn: parent
            text: root.badge
            font.pixelSize: 10
            font.bold: true
            color: Style.pick(Style.theme.components["badge"].textColors, "normal", "#000")
        }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: root.clicked() }
}
