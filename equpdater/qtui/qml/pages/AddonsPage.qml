import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as C
import Qt.labs.qmlmodels
import EqUi

// Addons: search, one virtualised list (the only scroller), a footer with
// Update all. Rows come from the keyed Python model: one addon changing
// repaints one delegate; typing in the search box filters in Python.
SkinPanel {
    id: root
    objectName: "addonsPanel"
    Component.onCompleted: addons.open()

    Item {
        id: head
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 38
        SkinText { kind: "panel.header"; text: app.t("ADDONS"); anchors.verticalCenter: parent.verticalCenter }
        SkinSearchField {
            objectName: "addonSearch"
            anchors { right: refresh.left; rightMargin: 10; verticalCenter: parent.verticalCenter }
            width: 260
            placeholder: app.t("Search addons")
            onEdited: function (t) { addons.setFilter(t) }
        }
        SkinIconButton {
            id: refresh
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            kind: "icon.refresh"; tip: app.t("Check again"); onClicked: addons.recheck()
        }
    }
    Rectangle {
        id: rule
        anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 4 }
        height: 1
        color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
    }

    ListView {
        id: list
        objectName: "addonsList"
        anchors { left: parent.left; right: parent.right; top: rule.bottom; bottom: foot.top
                  topMargin: 4; bottomMargin: 4 }
        clip: true
        model: addons.model
        boundsBehavior: Flickable.StopAtBounds
        rightMargin: 12
        cacheBuffer: 400
        C.ScrollBar.vertical: SkinScrollBar {}

        reuseItems: true
        delegate: DelegateChooser {
            role: "kind"
            DelegateChoice {
                roleValue: "header"
                delegate: Item {
                    width: ListView.view.width - 16
                    height: 40
                    Row {
                        anchors { left: parent.left; verticalCenter: parent.verticalCenter }
                        spacing: 8
                        SkinText { kind: "text.heading"; text: model.open ? "▾" : "▸" }
                        SkinText { kind: "text.heading"; text: app.t(model.title) }
                        SkinText { kind: "text.dim"; text: model.count }
                    }
                    TapHandler { onTapped: addons.toggleSection(model.title) }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    Accessible.role: Accessible.Button
                    Accessible.name: model.title
                }
            }
            DelegateChoice { roleValue: "addon"; delegate: AddonRow { } }
        }
    }

    Item {
        id: foot
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 36
        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; height: 1
                    color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }
        SkinButton {
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            visible: addons.footerMode === "update"
            kind: "button.primary"
            text: addons.footerText
            onClicked: addons.updateAll()
        }
        SkinText {
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            visible: addons.footerMode !== "update"
            kind: "text.dim"
            text: addons.footerText
        }
    }
}
