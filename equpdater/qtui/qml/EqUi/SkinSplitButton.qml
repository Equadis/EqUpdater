import QtQuick
import EqUi

// The Play split-button: a main action and a dropdown arrow that is a
// separate click target. Both parts are skin components (button.play and
// splitbutton.arrow), so a skin can texture each independently.
Row {
    id: root
    property alias text: main.text
    property bool menuOpen: false
    signal clicked()
    signal arrowClicked()
    spacing: 1
    SkinButton {
        id: main
        kind: "button.play"
        enabled: root.enabled
        onClicked: root.clicked()
    }
    SkinButton {
        id: arrow
        kind: "splitbutton.arrow"
        iconKind: "icon.arrow-down"
        selected: root.menuOpen
        enabled: root.enabled
        height: main.height
        onClicked: root.arrowClicked()
        Accessible.name: qsTr("Choose what Play starts")
    }
}
