import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// The footer chip: a status dot and the number of players online. Click for the
// per-realm status. Green: every realm confirmed online. Red: every realm confirmed
// offline. Yellow: anything else, including old, missing or unknown data (EqUpdater
// never reports an outage because it could not fetch an update).
Item {
    id: root
    objectName: "serverChip"
    visible: server.enabled
    implicitWidth: row.implicitWidth + 10
    implicitHeight: 26
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: server.summary
    Keys.onSpacePressed: pop.opened ? pop.close() : pop.open()
    Keys.onReturnPressed: pop.opened ? pop.close() : pop.open()

    function toggle() { pop.opened ? pop.close() : pop.open() }

    function dotColour(state) {
        var c = Style.theme.components
        return state === "online" ? "#3ddc84" : state === "offline" ? "#ef5350" : "#f2c744"
    }

    Row {
        id: row
        anchors.verticalCenter: parent.verticalCenter
        spacing: 6
        Rectangle {
            objectName: "serverDot"
            property string state: server.dot
            width: 9; height: 9; radius: 5
            anchors.verticalCenter: parent.verticalCenter
            color: root.dotColour(state)
            border.width: 1
            border.color: Qt.darker(color, 1.4)
        }
        SkinText { kind: "text.dim"; text: server.chipText; anchors.verticalCenter: parent.verticalCenter
                   objectName: "serverChipText" }
    }
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: { server.refresh(); pop.opened ? pop.close() : pop.open() } }

    C.Popup {
        id: pop
        parent: C.Overlay.overlay
        modal: false
        padding: 0
        background: Item {}
        width: 320
        onAboutToShow: {
            var o = root.mapToItem(C.Overlay.overlay, 0, 0)
            x = Math.max(6, Math.min(o.x + root.width - width, C.Overlay.overlay.width - width - 6))
            y = Math.max(6, o.y - height - 8)
        }
        onHeightChanged: if (visible) {
            var o = root.mapToItem(C.Overlay.overlay, 0, 0)
            y = Math.max(6, o.y - height - 8)
        }
        contentItem: SkinPanel {
            objectName: "serverPopover"
            kind: "dialog"
            implicitHeight: col.implicitHeight + padTop + padBottom
            Column {
                id: col
                width: parent.width
                spacing: 6
                SkinText { width: parent.width; kind: "text.heading"; wrapMode: Text.Wrap; text: server.summary }
                Repeater {
                    model: server.lines
                    Row {
                        width: col.width
                        SkinText { width: parent.width * 0.55; kind: modelData.tone === "dim" ? "text.dim" : "text.body"
                                   elide: Text.ElideRight; text: modelData.label }
                        SkinText { width: parent.width * 0.45; kind: modelData.tone === "dim" ? "text.dim" : "text.body"
                                   elide: Text.ElideRight; horizontalAlignment: Text.AlignRight; text: modelData.value }
                    }
                }
                SkinText {
                    objectName: "serverSourceLink"
                    kind: "text.link"
                    text: app.t("Open source") + " ↗"
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: server.openSource() }
                    Accessible.role: Accessible.Link
                    Accessible.name: text
                }
            }
        }
    }
}
