import QtQuick
import EqUi

// One button for every skin: textures per state (nine-slice) or plain colours.
// kind picks the component id: button, button.primary, button.play, ...
Item {
    id: root
    property string kind: "button"
    property string text: ""
    property bool selected: false
    property string iconKind: ""
    property int iconSize: 14
    property string fontFamily: ""         // a label drawn in one fixed face (the font selector's previews)
    readonly property var spec: Style.theme.components[kind]
    readonly property bool hovered: hover.hovered
    readonly property bool pressed: tap.pressed
    readonly property string vstate: Style.stateOf(enabled, pressed, selected, hovered)
    signal clicked()

    implicitWidth: row.implicitWidth + spec.padding[0] + spec.padding[2]
    implicitHeight: Math.max(spec.minHeight, row.implicitHeight + spec.padding[1] + spec.padding[3])
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: text
    Keys.onSpacePressed: if (enabled) clicked()
    Keys.onReturnPressed: if (enabled) clicked()

    readonly property string tex: Style.pick(spec.textures, vstate, "")

    Rectangle {
        anchors.fill: parent
        visible: root.tex === ""
        color: Style.pick(root.spec.colors, root.vstate, "transparent")
        border.color: Style.pick(root.spec.borderColors, root.vstate, "transparent")
        border.width: root.spec.borderWidth
        radius: root.spec.radius
        opacity: root.spec.opacity
    }
    BorderImage {
        anchors.fill: parent
        visible: root.tex !== ""
        source: root.tex
        border.left: root.spec.slice[0]
        border.top: root.spec.slice[1]
        border.right: root.spec.slice[2]
        border.bottom: root.spec.slice[3]
        smooth: true
    }
    Rectangle {      // focus ring: only while keyboard-focused
        anchors.fill: parent
        anchors.margins: -2
        visible: root.activeFocus && root.focusReason === Qt.TabFocusReason
        color: "transparent"
        border.color: Style.pick(Style.theme.components["text.link"].textColors, "normal", "#fff")
        border.width: 1
        radius: root.spec.radius
    }
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6
        SkinIcon {
            visible: root.iconKind !== ""
            kind: root.iconKind === "" ? "icon.refresh" : root.iconKind
            size: root.iconSize
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            visible: root.text !== ""
            text: root.text
            color: Style.pick(root.spec.textColors, root.vstate, "#fff")
            font.family: root.fontFamily !== "" ? root.fontFamily : root.spec.font.family
            font.pixelSize: root.spec.font.size
            font.bold: !!root.spec.font.bold
            anchors.verticalCenter: parent.verticalCenter
            renderType: Text.QtRendering
        }
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor; enabled: root.enabled }
    TapHandler { id: tap; enabled: root.enabled; onTapped: root.clicked() }
}
