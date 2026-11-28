import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// Troubleshooting: the tools for when something is wrong, one row each (button, then what it does).
// Opened from the wrench beside Settings. Every action closes this panel first, as it did in Settings.
Item {
    id: root
    objectName: "troubleshootingOverlay"
    visible: false
    property bool showLog: false
    onShowLogChanged: forceActiveFocus()                  // the button that was clicked is hidden now: keep Escape working
    function open() { visible = true; showLog = false }
    function close() { if (visible) visible = false }
    focus: visible
    onVisibleChanged: if (visible) forceActiveFocus()          // so Escape reaches this window
    Keys.onEscapePressed: if (showLog) showLog = false; else close()

    // what each action does, read from its implementation: see docs/v3/QA_CHECKLIST.md
    readonly property var actions: [
        { id: "scan", name: "Verify game files",
          what: "Compares each game file with the client manifest. It only reads: nothing is changed or downloaded." },
        { id: "doctor", name: "Login Doctor",
          what: "Checks for common login problems. It changes nothing unless you press Repair and confirm." },
        { id: "attempt", name: "Game Diagnostics",
          what: "Shows what happened on recent launches and can export a support report. It changes nothing." },
        { id: "logs", name: "Open logs folder",
          what: "Opens the folder where EqUpdater saves error reports. Nothing is changed or sent." },
        { id: "session", name: "Show Session Log",
          what: "Shows what EqUpdater has done since it started. It is read-only." }
    ]
    function run(id) {
        if (id === "logs") { general.openLogsFolder(); return }
        if (id === "session") { general.refreshLog(); root.showLog = true; return }
        root.close()
        diag.openView(id)
    }

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        // as Settings: close only for a tap that really landed outside the window
        TapHandler {
            onTapped: function (ep) {
                var l = box.mapFromItem(null, ep.scenePosition.x, ep.scenePosition.y)
                if (l.x < 0 || l.y < 0 || l.x > box.width || l.y > box.height) root.close()
            }
        }
    }
    SkinPanel {
        id: box
        kind: "dialog"
        width: Math.min(720, parent.width - 40)
        height: Math.min(420, parent.height - 40)          // one height for both views: a click on Back must stay inside the window
        anchors.centerIn: parent
        TapHandler { }                                    // swallow clicks

        Item {
            id: head
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 30
            SkinText { kind: "panel.header"; text: root.showLog ? app.t("Show Session Log") : app.t("Troubleshooting")
                       anchors.verticalCenter: parent.verticalCenter }
            Row {
                spacing: 6
                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                SkinButton { visible: root.showLog; kind: "button.small"; text: app.t("Back")
                             anchors.verticalCenter: parent.verticalCenter; onClicked: root.showLog = false }
                SkinCloseButton { onClicked: root.close() }
            }
        }

        Column {
            id: rows
            objectName: "troubleshootingRows"
            visible: !root.showLog
            anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 14 }
            spacing: 12
            Repeater {
                model: root.actions
                Item {
                    width: rows.width
                    height: Math.max(30, what.implicitHeight)
                    SkinButton {
                        objectName: "trouble_" + modelData.id
                        kind: "button.small"
                        width: 190
                        anchors.verticalCenter: parent.verticalCenter
                        text: app.t(modelData.name)
                        onClicked: root.run(modelData.id)
                    }
                    SkinText {
                        id: what
                        objectName: "troubleDesc_" + modelData.id
                        anchors { left: parent.left; leftMargin: 206; right: parent.right; verticalCenter: parent.verticalCenter }
                        kind: "text.body"
                        wrapMode: Text.Wrap
                        text: app.t(modelData.what)
                    }
                }
            }
        }

        Item {
            visible: root.showLog
            anchors { left: parent.left; right: parent.right; top: head.bottom; bottom: parent.bottom; topMargin: 8 }
            Flickable {
                id: logFlick
                anchors.fill: parent
                clip: true
                contentWidth: width
                contentHeight: logText.contentHeight + 8
                boundsBehavior: Flickable.StopAtBounds
                C.ScrollBar.vertical: SkinScrollBar {}
                TextEdit {
                    id: logText
                    objectName: "sessionLogText"
                    width: logFlick.width - 14
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    text: general.sessionLog
                    color: Style.pick(Style.theme.components["text.dim"].textColors, "normal", "#aaa")
                    font.family: "Consolas"
                    font.pixelSize: Style.theme.fonts.dim.size
                }
            }
        }
    }
}
