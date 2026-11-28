import QtQuick
import QtQuick.Dialogs
import QtQuick.Controls.Basic as C
import EqUi

// Game Diagnostics (recorded launches), the folder scan ("Verify game files",
// read-only) and Login Doctor. Opening any of it never starts the game.
Item {
    id: root
    objectName: "diagnosticsOverlay"
    visible: diag.isOpen
    focus: visible
    onVisibleChanged: if (visible) forceActiveFocus()          // so Escape reaches this window
    Keys.onEscapePressed: diag.close()
    property bool deep: true
    property bool mpq: false

    function toneKind(t) {
        return t === "err" ? "text.error" : t === "ok" ? "text.ok" : t === "warn" ? "text.link"
             : t === "dim" ? "text.dim" : t === "head" ? "text.heading" : "text.body"
    }

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler { onTapped: diag.close() }
    }
    SkinPanel {
        kind: "dialog"
        width: Math.min(900, parent.width - 30)
        height: Math.min(620, parent.height - 30)
        anchors.centerIn: parent
        TapHandler { }

        Item {
            id: head
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 30
            SkinText {
                kind: "panel.header"
                anchors.verticalCenter: parent.verticalCenter
                text: diag.view === "doctor" ? app.t("LOGIN DOCTOR")
                    : diag.view === "scan" ? app.t("Verify game files") : app.t("Game Diagnostics")
            }
            Row {
                spacing: 6
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                SkinButton { kind: "button.small"; text: app.t("Game Diagnostics")
                             selected: diag.view === "attempt"; onClicked: diag.openView("attempt") }
                SkinButton { kind: "button.small"; text: app.t("Verify game files")
                             selected: diag.view === "scan"; onClicked: diag.openView("scan") }
                SkinButton { kind: "button.small"; text: app.t("Login Doctor")
                             selected: diag.view === "doctor"; onClicked: diag.openView("doctor") }
                SkinCloseButton { anchors.verticalCenter: parent.verticalCenter; onClicked: diag.close() }
            }
        }

        // toolbar for the current view ------------------------------------------------
        Row {
            id: tools
            anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 6 }
            spacing: 8
            height: 30
            SkinButton { visible: diag.view === "attempt"; kind: "button.small"; text: app.t("Check again")
                         enabled: diag.hasAttempts; onClicked: diag.recheck() }
            SkinButton { visible: diag.view === "attempt"; kind: "button.small"; text: app.t("Launch with diagnostics")
                         enabled: diag.canLaunch; onClicked: diag.launchWithDiagnostics() }
            SkinButton { visible: diag.view === "attempt"; kind: "button.small"; text: app.t("Export support ZIP…")
                         enabled: diag.hasAttempts && !diag.exporting
                         onClicked: if (diag.confirmExport()) exportDialog.open() }
            SkinCheckBox { visible: diag.view === "scan"; text: app.t("Check every file's contents")
                           checked: root.deep; onToggled: root.deep = !root.deep }
            SkinCheckBox { visible: diag.view === "scan"; text: app.t("Include MPQ archives")
                           checked: root.mpq; onToggled: root.mpq = !root.mpq }
            SkinButton { visible: diag.view === "scan"; kind: "button.primary"; text: app.t("Scan")
                         enabled: !diag.scanning; onClicked: diag.startScan(root.deep, root.mpq) }
            SkinButton { visible: diag.view === "scan"; kind: "button.small"; text: app.t("Cancel")
                         enabled: diag.scanning; onClicked: diag.cancelScan() }
            SkinButton { visible: diag.view === "doctor"; kind: "button.primary"; text: app.t("Repair login configuration")
                         enabled: diag.canRepair; onClicked: diag.repairLogin() }
            SkinButton { visible: diag.view === "doctor"; kind: "button.small"; text: "⟳  " + app.t("Run again")
                         onClicked: diag.doctorAgain() }
            SkinText { anchors.verticalCenter: parent.verticalCenter; kind: "text.dim"; text: diag.statusText }
        }
        FileDialog {
            id: exportDialog
            fileMode: FileDialog.SaveFile
            nameFilters: ["ZIP (*.zip)"]
            defaultSuffix: "zip"
            currentFile: "file:///" + diag.exportDefaultName()
            onAccepted: diag.exportTo(selectedFile.toString().replace(/^file:\/\/\//, ""))
        }

        // list of recorded launches (attempt view only) -------------------------------------
        ListView {
            id: attempts
            objectName: "attemptsList"
            visible: diag.view === "attempt"
            anchors { left: parent.left; top: tools.bottom; bottom: parent.bottom; topMargin: 8 }
            width: visible ? 230 : 0
            clip: true
            model: diag.model
            boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: SkinScrollBar {}
            delegate: SkinButton {
                width: attempts.width - 10
                kind: "button.small"
                selected: model.id === diag.selectedId
                text: (model.tone === "ok" ? "✓  " : model.tone === "err" ? "✕  "
                       : model.tone === "warn" ? "!  " : "…  ") + model.when + "  " + model.outcomeText
                onClicked: diag.select(model.id)
            }
        }

        // the text of the current view ---------------------------------------------------------
        ListView {
            id: body
            objectName: "diagLines"
            anchors { left: attempts.visible ? attempts.right : parent.left; right: parent.right
                      top: tools.bottom; bottom: note.visible ? note.top : parent.bottom; topMargin: 8
                      leftMargin: attempts.visible ? 12 : 0 }
            clip: true
            model: diag.lines
            boundsBehavior: Flickable.StopAtBounds
            reuseItems: true
            C.ScrollBar.vertical: SkinScrollBar {}
            delegate: SkinText {
                width: body.width - 14
                wrapMode: Text.Wrap
                kind: root.toneKind(modelData.tone)
                text: modelData.text
                textFormat: Text.PlainText
            }
        }
        SkinText {
            id: note
            visible: diag.view === "doctor"
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            kind: "text.dim"
            wrapMode: Text.Wrap
            text: app.t("Priority Sign In") + "\n" + diag.priorityNote
        }
    }
}
