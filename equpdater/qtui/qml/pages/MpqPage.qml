import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as C
import Qt.labs.qmlmodels
import EqUi

// MPQ packs: INSTALLED (with source, state, update / replace / unlink /
// uninstall) and AVAILABLE (install). One scroller; keyed model; pooled rows.
SkinPanel {
    id: root
    objectName: "mpqPanel"
    Component.onCompleted: mpq.open()

    Item {
        id: head
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 34
        SkinText { kind: "panel.header"; text: app.t("MPQ"); anchors.verticalCenter: parent.verticalCenter }
        SkinIconButton { anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                         kind: "icon.refresh"; tip: app.t("Check again"); onClicked: mpq.recheck() }
    }
    Rectangle {
        id: rule
        anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 6 }
        height: 1
        color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
    }

    ListView {
        id: list
        objectName: "mpqList"
        anchors { left: parent.left; right: parent.right; top: rule.bottom; bottom: foot.top
                  topMargin: 4; bottomMargin: 4 }
        clip: true
        model: mpq.model
        boundsBehavior: Flickable.StopAtBounds
        rightMargin: 12
        reuseItems: true
        C.ScrollBar.vertical: SkinScrollBar {}
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
                    TapHandler { onTapped: mpq.toggleSection(model.title) }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    Accessible.role: Accessible.Button
                    Accessible.name: model.title
                }
            }
            DelegateChoice {
                roleValue: "pack"
                delegate: Item {
                    width: ListView.view.width - 16
                    height: col.implicitHeight + 14
                    Column {
                        id: col
                        anchors { left: parent.left; right: parent.right; top: parent.top; topMargin: 6 }
                        spacing: 3
                        RowLayout {
                            width: parent.width
                            spacing: 8
                            SkinText { kind: "text.heading"; text: model.name
                                       font.pixelSize: Style.theme.fonts.body.size + 1 }
                            SkinText { kind: "text.dim"; visible: model.showFile; text: model.file }
                            Item { Layout.fillWidth: true; height: 1 }
                            SkinText { visible: model.busy !== ""; kind: "text.dim"; text: model.busy }
                            RowLayout {
                                visible: model.busy === ""
                                spacing: 4
                                SkinText {
                                    visible: model.stateText !== ""
                                    kind: model.stateTone === "warn" ? "text.link" : "text.dim"
                                    text: model.stateText
                                    SkinToolTipArea { tip: model.stateTip }
                                }
                                SkinButton { visible: model.offersReplace; kind: "button.link"
                                             text: app.t("Replace…"); onClicked: mpq.replace(model.file) }
                                SkinButton {
                                    visible: model.action !== ""
                                    kind: "button.primary"
                                    text: model.actionText
                                    onClicked: mpq.click(model.file, model.action)
                                }
                                SkinIconButton { visible: model.installed; kind: "icon.remove"
                                                 tip: app.t("Uninstall"); onClicked: mpq.uninstall(model.file) }
                                SkinIconButton { visible: model.linked; kind: "icon.release"
                                                 tip: app.t("Stop managing. Your files are kept.")
                                                 onClicked: mpq.unlink(model.file) }
                            }
                        }
                        SkinText { visible: model.description !== ""; kind: "text.dim"
                                   width: parent.width; wrapMode: Text.Wrap; text: model.description }
                        Row {
                            visible: model.author !== "" || model.homepage !== ""
                            spacing: 6
                            SkinText { kind: "text.dim"; visible: model.author !== ""
                                       text: app.t("By {author}").replace("{author}", model.author) }
                            SkinButton { visible: model.homepage !== ""; kind: "button.link"
                                         text: app.t("Project page")
                                         onClicked: mpq.openUrl(model.homepage) }
                        }
                        Row {
                            visible: model.sourceText !== ""
                            spacing: 6
                            SkinText { kind: "text.dim"; text: model.sourceText }
                            SkinButton { visible: model.canLink && model.busy === ""; kind: "button.link"
                                         text: app.t("Link source…"); onClicked: mpq.openLink(model.file)
                                         SkinToolTipArea { tip: model.linkTip } }
                        }
                        Rectangle { width: parent.width; height: 1; opacity: 0.5
                                    color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }
                    }
                }
            }
        }
    }

    Item {
        id: foot
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 36
        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    height: 1; color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }
        SkinCheckBox {
            anchors { left: parent.left; verticalCenter: parent.verticalCenter }
            text: app.t("Ignore speech.mpq")
            checked: mpq.ignoreSpeech
            onToggled: mpq.ignoreSpeech = !mpq.ignoreSpeech
            SkinToolTipArea { tip: app.t("Ignores verification and updates for speech.mpq, allowing custom speech sounds.") }
        }
    }

    // "Where does <file> come from?" - a catalogue entry or a pasted link.
    Item {
        id: linkDlg
        objectName: "mpqLinkDialog"
        anchors.fill: parent
        visible: mpq.linkFor !== ""
        property string choice: ""
        property string err: ""
        onVisibleChanged: if (visible) { err = ""; urlField.text = ""; choice = "url" }
        Rectangle { anchors.fill: parent
                    color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
                    TapHandler { onTapped: mpq.closeLink() } }
        SkinPanel {
            kind: "dialog"
            width: Math.min(560, parent.width - 40)
            height: lcol.implicitHeight + 40
            anchors.centerIn: parent
            TapHandler { }
            Column {
                id: lcol
                width: parent.width
                spacing: 10
                SkinText { kind: "panel.header"; text: app.t("LINK TEXTURE PACK SOURCE") }
                SkinText { kind: "text.body"; width: parent.width; wrapMode: Text.Wrap
                           text: app.t("Where does {file} come from?").replace("{file}", mpq.linkFor) }
                Repeater {
                    model: mpq.catalogue
                    SkinCheckBox {
                        text: modelData.name + " (" + modelData.file + ")"
                        checked: linkDlg.choice === "cat:" + modelData.file
                        onToggled: linkDlg.choice = "cat:" + modelData.file
                    }
                }
                SkinCheckBox { text: app.t("Somewhere else:"); checked: linkDlg.choice === "url"
                               onToggled: linkDlg.choice = "url" }
                SkinSearchField {
                    id: urlField
                    width: parent.width
                    placeholder: "https://github.com/…"
                    onEdited: linkDlg.choice = "url"
                }
                SkinText { kind: "text.dim"; width: parent.width; wrapMode: Text.Wrap
                           text: app.t("A GitHub, Codeberg or OctoWoW Git repository or release download, or a dl.octowow.st link. Linking changes no files.") }
                SkinText { kind: "text.error"; visible: linkDlg.err !== ""; width: parent.width
                           wrapMode: Text.Wrap; text: linkDlg.err }
                Row {
                    spacing: 10
                    anchors.right: parent.right
                    SkinButton { text: app.t("Cancel"); onClicked: mpq.closeLink() }
                    SkinButton {
                        kind: "button.primary"; text: app.t("Link")
                        onClicked: linkDlg.err = mpq.link(linkDlg.choice, urlField.text)
                    }
                }
            }
        }
    }
}
