import QtQuick
import EqUi

// A skinned panel: nine-slice texture when the skin gives one, else a plain
// rectangle from colours. Children go inside the padded content area.
Item {
    id: root
    property string kind: "panel"
    default property alias content: contentArea.data
    readonly property var spec: skin.theme.components[kind]
    readonly property bool textured: Style.has(spec.textures, "normal")
    property alias contentItem: contentArea
    property real padLeft: spec.padding[0]
    property real padTop: spec.padding[1]
    property real padRight: spec.padding[2]
    property real padBottom: spec.padding[3]

    Rectangle {
        anchors.fill: parent
        visible: !root.textured
        color: Style.pick(root.spec.colors, "normal", "transparent")
        border.color: Style.pick(root.spec.borderColors, "normal", "transparent")
        border.width: root.spec.borderWidth
        radius: root.spec.radius
        opacity: root.spec.opacity
    }
    BorderImage {
        anchors.fill: parent
        visible: root.textured
        source: Style.pick(root.spec.textures, "normal", "")
        border.left: root.spec.slice[0]
        border.top: root.spec.slice[1]
        border.right: root.spec.slice[2]
        border.bottom: root.spec.slice[3]
        opacity: root.spec.opacity
        smooth: true
    }
    Item {
        id: contentArea
        anchors.fill: parent
        anchors.leftMargin: root.padLeft
        anchors.topMargin: root.padTop
        anchors.rightMargin: root.padRight
        anchors.bottomMargin: root.padBottom
    }
}
