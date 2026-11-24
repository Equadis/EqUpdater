import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as C
import Qt.labs.qmlmodels
import EqUi

// Mods: one virtualised list (the only scroller), sections and rows from the
// keyed Python model. A row changing repaints that delegate only.
SkinPanel {
    id: root
    objectName: "modsPanel"
    Component.onCompleted: mods.open()

    Item {
        id: head
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 34
        SkinText { kind: "panel.header"; text: app.t("MODS"); anchors.verticalCenter: parent.verticalCenter }
        SkinButton {
            objectName: "addCustomMod"
            kind: "button.small"
            anchors { right: refreshBox.left; rightMargin: 8; verticalCenter: parent.verticalCenter }
            text: app.t("Add custom git mod")
            onClicked: customSrc.open("mod")
        }
        Item {
            id: refreshBox
            width: 28; height: 28
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            SkinIconButton { anchors.centerIn: parent; kind: "icon.refresh"
                             tip: app.t("Check again"); onClicked: mods.refresh() }
        }
    }
    Rectangle {
        id: rule
        anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 6 }
        height: 1
        color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
    }

    ListView {
        id: list
        objectName: "modsList"
        anchors { left: parent.left; right: parent.right; top: rule.bottom; bottom: parent.bottom; topMargin: 4 }
        clip: true
        model: mods.model
        boundsBehavior: Flickable.StopAtBounds
        rightMargin: 12
        cacheBuffer: 400
        C.ScrollBar.vertical: SkinScrollBar {}

        reuseItems: true                       // pooled delegates, as on the Addons page
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
                    SkinButton {
                        visible: model.manageAll > 0
                        kind: "button.link"
                        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                        text: app.t("Manage all ({count})").replace("{count}", model.manageAll)
                        onClicked: mods.manageAll()
                    }
                    TapHandler { onTapped: mods.toggleSection(model.title) }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    Accessible.role: Accessible.Button
                    Accessible.name: model.title
                }
            }
            DelegateChoice {
                delegate: Item {
                    id: row
                    width: ListView.view.width - 16
                    height: Math.max(54, layout.implicitHeight + 14) + (model.problem !== "" ? 22 : 0)
                    RowLayout {
                        id: layout
                        anchors { left: parent.left; right: parent.right; top: parent.top; topMargin: 7 }
                        spacing: 10
                        Row {
                            Layout.preferredWidth: 230
                            Layout.alignment: Qt.AlignTop
                            spacing: 4
                            SkinText { kind: "text.heading"; text: model.essential ? "★" : " "
                                       width: 14; font.pixelSize: 12 }
                            SkinText {
                                kind: model.hasError ? "text.error" : "text.body"
                                font.bold: true
                                text: model.name
                                color: model.hasError ? Style.pick(Style.theme.components["text.error"].textColors, "normal", "#f66")
                                       : model.installedGreen ? Style.pick(Style.theme.components["text.ok"].textColors, "normal", "#6c6")
                                       : Style.pick(Style.theme.components["text.body"].textColors, "normal", "#fff")
                            }
                            SkinText { kind: "text.dim"; text: " " + model.version }
                        }
                        SkinText {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            kind: model.enabled ? "text.body" : "text.dim"
                            wrapMode: Text.Wrap
                            text: model.description
                        }
                        RowLayout {
                            Layout.alignment: Qt.AlignTop
                            spacing: 2
                            SkinButton {
                                visible: model.actionText !== ""
                                kind: "button.link"
                                text: model.actionText
                                enabled: model.actionMode !== "info"
                                onClicked: mods.clickAction(model.id, model.actionMode)
                                // right-click is the deliberate "install anyway"
                                TapHandler { acceptedButtons: Qt.RightButton
                                             onTapped: mods.forceReplace(model.id) }
                                SkinToolTipArea { tip: model.actionTip }
                            }
                            SkinCheckBox {
                                text: app.t("Ignore updates")
                                checked: model.ignore
                                onToggled: mods.setIgnore(model.id, !model.ignore)
                            }
                            SkinIconButton { visible: !model.here; kind: "icon.download"
                                             tip: app.t("Download from repository")
                                             onClicked: mods.install(model.id) }
                            SkinIconButton { visible: model.here; kind: "icon.remove"
                                             tip: app.t("Uninstall")
                                             onClicked: mods.uninstall(model.id) }
                            SkinIconButton { visible: model.here && model.managed && !model.custom
                                             kind: "icon.release"
                                             tip: app.t("Stop managing. Your files are kept.")
                                             onClicked: mods.release(model.id) }
                            SkinIconButton { visible: model.custom; kind: "icon.remove"
                                             tip: app.t("Remove from the list")
                                             onClicked: customSrc.remove(model.id) }
                            SkinIconButton { kind: "icon.link"; tip: app.t("Open repository in browser")
                                             onClicked: mods.openUrl(model.repoUrl) }
                        }
                    }
                    SkinText {
                        visible: model.problem !== ""
                        anchors { left: parent.left; leftMargin: 16; right: parent.right
                                  top: layout.bottom; topMargin: 2 }
                        kind: model.problemSeverity === "error" ? "text.error" : "text.link"
                        wrapMode: Text.Wrap
                        text: model.problem
                    }
                    Rectangle {
                        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                        height: 1
                        color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
                        opacity: 0.5
                    }
                }
            }
        }
    }
}
