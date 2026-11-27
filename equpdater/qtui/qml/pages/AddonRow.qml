import QtQuick
import QtQuick.Layouts
import EqUi

// One addon row. Pooled by the ListView (reuseItems): every value comes from
// the model, so a reused delegate is correct as soon as its roles change.
Item {
    id: rowRoot
    width: ListView.view ? ListView.view.width - 16 : 600

        height: Math.max(48, layout.implicitHeight + 12) + (model.problem !== "" ? 22 : 0)
        RowLayout {
            id: layout
            anchors { left: parent.left; right: parent.right; top: parent.top; topMargin: 6 }
            spacing: 10
            Row {
                Layout.preferredWidth: 250
                Layout.alignment: Qt.AlignTop
                spacing: 4
                SkinText { kind: "text.heading"; text: model.recommended ? "★" : " "
                           width: 14; font.pixelSize: 12 }
                SkinText { textFormat: Text.RichText; font.bold: true
                           text: model.titleHtml; width: 226; elide: Text.ElideRight }
            }
            SkinText {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                kind: "text.dim"
                wrapMode: Text.Wrap
                maximumLineCount: 3
                elide: Text.ElideRight
                text: model.description
            }
            RowLayout {
                Layout.alignment: Qt.AlignTop
                spacing: 2
                SkinText {
                    visible: model.statusKind === "busy" || model.statusKind === "info"
                    kind: model.statusTone === "error" ? "text.error"
                          : model.statusTone === "dim" ? "text.dim" : "text.link"
                    text: model.statusText
                    SkinToolTipArea { tip: model.statusTip }
                }
                SkinButton {
                    visible: model.offersReplace
                    kind: "button.link"; text: app.t("Replace…")
                    onClicked: addons.forceReplace(model.id)
                }
                SkinButton {
                    visible: model.statusKind === "button"
                    kind: "button.link"
                    text: model.statusText
                    onClicked: addons.clickStatus(model.id, model.statusAction)
                    SkinToolTipArea { tip: model.statusTip }
                }
                SkinIconButton { visible: model.switch; kind: "icon.update"
                                 tip: model.switchTip; onClicked: addons.switchSource(model.id) }
                Item {                                              // the Download slot keeps its width
                    implicitWidth: 28; implicitHeight: 28
                    SkinIconButton { visible: !model.installed; anchors.centerIn: parent; kind: "icon.download"
                                     tip: app.t("Download from repository")
                                     onClicked: addons.install(model.id) }
                }
                SkinRowActions {
                    folderEnabled: model.installed
                    linkEnabled: model.repoUrl !== ""
                    uninstallEnabled: model.installed
                    releaseEnabled: model.canRelease
                    onFolder: addons.openFolder(model.id)
                    onLink: addons.openUrl(model.repoUrl)
                    onUninstall: addons.remove(model.id)
                    onRelease: addons.release(model.id)
                }
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
