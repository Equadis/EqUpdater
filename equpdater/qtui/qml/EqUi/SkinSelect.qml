import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// A compact drop-down: the current choice on a skinned button, the choices in a skinned list.
// options: [{id, label, flag?, family?, enabled?}]. `flag` is a file name in qml/flags; `family`
// draws that option in its own typeface (the font selector), whatever font is in use.
Item {
    id: root
    property var options: []
    property string current: ""
    property real controlWidth: 200
    signal chosen(string id)
    readonly property var spec: Style.theme.components["button"]
    readonly property var currentOption: {
        for (var i = 0; i < options.length; i++) if (options[i].id === current) return options[i]
        return options.length ? options[0] : ({ label: "" })
    }
    implicitWidth: controlWidth
    implicitHeight: 28
    width: controlWidth
    height: 28

    SkinButton {
        id: button
        anchors.fill: parent
        iconKind: ""
        onClicked: pop.opened ? pop.close() : pop.open()
        text: ""
        // the label row is drawn here so a flag and a typeface can sit in it
        Row {
            anchors { left: parent.left; leftMargin: 8; right: arrow.left; rightMargin: 6; verticalCenter: parent.verticalCenter }
            spacing: 8
            Image {
                visible: !!root.currentOption.flag
                source: root.currentOption.flag ? Qt.resolvedUrl("../flags/" + root.currentOption.flag + ".png") : ""
                width: 24; height: 16; sourceSize.width: 48; sourceSize.height: 32
                anchors.verticalCenter: parent.verticalCenter
                smooth: true
            }
            Text {
                text: root.currentOption.label
                width: parent.width - (root.currentOption.flag ? 32 : 0)
                elide: Text.ElideRight
                anchors.verticalCenter: parent.verticalCenter
                color: Style.pick(button.spec.textColors, button.vstate, "#fff")
                font.family: root.currentOption.family ? root.currentOption.family : button.spec.font.family
                font.pixelSize: button.spec.font.size
                renderType: Text.QtRendering
            }
        }
        SkinIcon { id: arrow; kind: "icon.arrow-down"; size: 10
                   anchors { right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter } }
    }

    C.Popup {
        id: pop
        parent: button
        y: button.height + 2
        width: Math.max(button.width, 180)
        padding: Style.theme.components["menu"].padding[0]
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
        modal: false
        background: Rectangle {
            color: Style.pick(Style.theme.components["menu"].colors, "normal", "#222")
            border.color: Style.pick(Style.theme.components["menu"].borderColors, "normal", "#444")
            border.width: Style.theme.components["menu"].borderWidth
            radius: Style.theme.components["menu"].radius
        }
        contentItem: Column {
            Repeater {
                model: root.options
                delegate: Item {
                    width: pop.width - pop.leftPadding - pop.rightPadding
                    height: Style.theme.components["menu.item"].minHeight
                    readonly property bool usable: modelData.enabled !== false
                    Rectangle {
                        anchors.fill: parent
                        color: Style.pick(Style.theme.components["menu.item"].colors,
                                          ma.containsMouse && parent.usable ? "hover" : "normal", "transparent")
                    }
                    Row {
                        x: 10; anchors.verticalCenter: parent.verticalCenter
                        spacing: 8
                        Image {
                            visible: !!modelData.flag
                            source: modelData.flag ? Qt.resolvedUrl("../flags/" + modelData.flag + ".png") : ""
                            width: 24; height: 16; sourceSize.width: 48; sourceSize.height: 32
                            anchors.verticalCenter: parent.verticalCenter
                            smooth: true
                        }
                        Text {
                            text: modelData.label
                            anchors.verticalCenter: parent.verticalCenter
                            color: Style.pick(Style.theme.components["menu.item"].textColors,
                                              !parent.parent.usable ? "disabled" : modelData.id === root.current ? "selected" : "normal",
                                              Style.pick(Style.theme.components["text.body"].textColors, "normal", "#fff"))
                            font.family: modelData.family ? modelData.family : Style.theme.fonts.body.family
                            font.pixelSize: Style.theme.fonts.body.size
                            font.bold: modelData.id === root.current
                            renderType: Text.QtRendering
                        }
                    }
                    MouseArea {
                        id: ma
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: parent.usable
                        onClicked: { pop.close(); root.chosen(modelData.id) }
                    }
                }
            }
        }
    }
}
