import QtQuick
import QtQuick.Dialogs
import QtQuick.Controls.Basic as C
import EqUi

// Find or add your game: folders already on this computer, or a ZIP/TAR the
// player downloaded. EqUpdater never downloads the game itself.
Item {
    id: root
    objectName: "setupOverlay"
    visible: setup.isOpen
    focus: visible
    Keys.onEscapePressed: setup.close()
    function tone(t) { return t === "err" ? "text.error" : t === "ok" ? "text.ok"
                            : t === "warn" ? "text.link" : "text.dim" }
    function localPath(url) { return url.toString().replace(/^file:\/\/\//, "") }

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler { onTapped: setup.close() }
    }
    SkinPanel {
        kind: "dialog"
        width: Math.min(700, parent.width - 30)
        height: Math.min(560, parent.height - 30)
        anchors.centerIn: parent
        TapHandler { }

        Item {
            id: head
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 30
            SkinText { kind: "panel.header"; text: app.t("FIND YOUR GAME"); anchors.verticalCenter: parent.verticalCenter }
            SkinButton { kind: "button.small"; text: app.t("Close"); enabled: !setup.extracting
                         anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                         onClicked: setup.close() }
        }
        Flickable {
            anchors { left: parent.left; right: parent.right; top: head.bottom; bottom: parent.bottom; topMargin: 8 }
            clip: true
            contentWidth: width
            contentHeight: col.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: SkinScrollBar {}
            Column {
                id: col
                width: parent.width - 14
                spacing: 8
                SkinText {
                    width: parent.width; kind: "text.dim"; wrapMode: Text.Wrap
                    text: app.t("EqUpdater does not download the game. Use a copy you already have, or extract the archive you downloaded from OctoWoW.")
                }
                SkinText { kind: "text.heading"; text: app.t("ON THIS COMPUTER") }
                Repeater {
                    model: setup.candidates
                    Item {
                        width: col.width
                        height: info.implicitHeight + 10
                        Column {
                            id: info
                            anchors { left: parent.left; right: use.left; rightMargin: 8; top: parent.top }
                            SkinText { width: parent.width; kind: "text.body"; font.bold: true; elide: Text.ElideMiddle; text: modelData.path }
                            SkinText { width: parent.width; kind: modelData.state === "complete" ? "text.ok" : "text.dim"
                                       wrapMode: Text.Wrap; text: modelData.sourceText + ". " + modelData.stateText }
                            SkinText { width: parent.width; kind: "text.dim"; wrapMode: Text.Wrap; text: modelData.evidenceText }
                        }
                        SkinButton {
                            id: use
                            kind: "button.small"
                            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                            visible: modelData.usable
                            text: app.t("Use this folder")
                            onClicked: setup.use(modelData.path)
                        }
                    }
                }
                SkinText { visible: setup.candidates.length === 0 && !setup.scanning; kind: "text.dim"
                           text: app.t("Nothing found yet.") }
                Row {
                    spacing: 8
                    SkinButton { kind: "button.small"; text: app.t("Choose a folder…"); onClicked: pickFolder.open() }
                    SkinButton { kind: "button.small"; text: "⟳  " + app.t("Look again"); enabled: !setup.scanning
                                 onClicked: setup.rescan() }
                }
                Rectangle { width: parent.width; height: 1
                            color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }
                SkinText { kind: "text.heading"; text: app.t("EXTRACT AN ARCHIVE") }
                Row {
                    spacing: 8
                    SkinButton { kind: "button.small"; text: app.t("Choose a ZIP or TAR…"); enabled: !setup.extracting
                                 onClicked: pickArchive.open() }
                    SkinText { anchors.verticalCenter: parent.verticalCenter; kind: "text.dim"; elide: Text.ElideMiddle
                               width: 360; text: setup.archive }
                }
                Row {
                    spacing: 8
                    visible: setup.archiveOk
                    SkinButton { kind: "button.primary"; text: app.t("Extract to…"); enabled: !setup.extracting
                                 onClicked: pickDest.open() }
                    SkinButton { kind: "button.small"; text: app.t("Cancel"); visible: setup.extracting
                                 onClicked: setup.cancel() }
                }
                SkinProgressBar { visible: setup.extracting; width: parent.width; value: setup.progress }
                SkinText { objectName: "setupStatus"; width: parent.width; wrapMode: Text.Wrap
                           kind: root.tone(setup.statusTone); text: setup.statusText }
            }
        }
        FolderDialog {
            id: pickFolder
            onAccepted: setup.use(root.localPath(selectedFolder))
        }
        FileDialog {
            id: pickArchive
            nameFilters: ["Archives (*.zip *.tar *.tar.gz *.tgz *.tar.bz2 *.tar.xz)"]
            onAccepted: setup.pickArchive(root.localPath(selectedFile))
        }
        FolderDialog {
            id: pickDest
            onAccepted: setup.extractTo(root.localPath(selectedFolder) + "/OctoWoW")
        }
    }
}
