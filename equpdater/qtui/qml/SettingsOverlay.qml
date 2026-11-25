import QtQuick
import QtQuick.Dialogs
import QtQuick.Controls.Basic as C
import EqUi

// Settings. Game folder, launch and auto-install options, font, language
// (follows the Language tweak), skin and animation, session log. Skins are
// only selected here: open the folder, pick one, restore the default.
Item {
    id: root
    objectName: "settingsOverlay"
    visible: false
    property bool showLog: false
    property string folderError: ""
    function open() { visible = true; showLog = false; settings.rescanSkins() }
    function close() { if (visible) { visible = false; general.closed() } }
    focus: visible
    Keys.onEscapePressed: if (showLog) showLog = false; else close()

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        TapHandler { onTapped: root.close() }
    }
    SkinPanel {
        id: box
        kind: "dialog"
        width: Math.min(640, parent.width - 40)
        height: Math.min(640, parent.height - 40)
        anchors.centerIn: parent
        TapHandler { }                                    // swallow clicks

        Item {
            id: head
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 30
            SkinText { kind: "panel.header"; text: root.showLog ? app.t("Show Session Log") : app.t("SETTINGS")
                       anchors.verticalCenter: parent.verticalCenter }
            SkinButton { kind: "button.small"; text: root.showLog ? app.t("Back") : app.t("Close")
                         anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                         onClicked: root.showLog ? root.showLog = false : root.close() }
        }

        Flickable {
            id: flick
            visible: !root.showLog
            anchors { left: parent.left; right: parent.right; top: head.bottom; bottom: parent.bottom
                      topMargin: 8 }
            contentWidth: width
            contentHeight: col.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: SkinScrollBar {}

            Column {
                id: col
                width: flick.width - 14
                spacing: 8

                // ── GAME FOLDER ────────────────────────────────────────
                SkinText { kind: "text.heading"; text: app.t("GAME FOLDER") }
                Row {
                    spacing: 8
                    width: parent.width
                    SkinSearchField {
                        id: dirField
                        objectName: "gameDirField"
                        width: parent.width - 310
                        text: general.gameDir
                        placeholder: "C:\\Games\\OctoWoW"
                    }
                    SkinButton { kind: "button.small"; text: app.t("Change")
                                 onClicked: root.folderError = general.changeGameDir(dirField.text) }
                    SkinButton { kind: "button.small"; text: app.t("Browse…"); onClicked: folderDialog.open() }
                    SkinButton { kind: "button.small"; text: app.t("Find…")
                                 onClicked: { root.close(); setup.open() }
                                 SkinToolTipArea { tip: app.t("Looks for a game already on this computer, or extracts an archive you downloaded.") } }
                    SkinIconButton { kind: "icon.link"; tip: app.t("Open folder"); onClicked: general.openGameDir() }
                }
                SkinText { visible: root.folderError !== ""; kind: "text.error"; text: app.t(root.folderError) }
                FolderDialog {
                    id: folderDialog
                    onAccepted: {
                        var p = selectedFolder.toString().replace(/^file:\/\/\//, "")
                        dirField.text = p
                        root.folderError = general.changeGameDir(p)
                    }
                }
                Rectangle { width: parent.width; height: 1
                            color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }

                // ── ANTIVIRUS (Windows) ─────────────────────────────────
                Column {
                    visible: general.isWindows
                    width: parent.width
                    spacing: 6
                    SkinText { kind: "text.heading"; text: app.t("ANTIVIRUS") }
                    SkinButton {
                        objectName: "defenderButton"
                        kind: "button.small"
                        text: app.t("Exclude from Defender")
                        onClicked: root.folderError = general.excludeFromDefender()
                        SkinToolTipArea { tip: app.t("Asks Windows to stop scanning the game folder. Antivirus can wrongly block mods. Windows asks you to confirm.") }
                    }
                    Rectangle { width: parent.width; height: 1
                                color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }
                }

                // ── GAME LAUNCHER (Linux) ───────────────────────────────
                Column {
                    visible: general.hasLauncher
                    objectName: "launcherSection"
                    width: parent.width
                    spacing: 6
                    SkinText { kind: "text.heading"; text: app.t("GAME LAUNCHER") }
                    SkinText { kind: "text.body"; text: app.t("Launch command") }
                    SkinSearchField {
                        objectName: "launchCommandField"
                        width: parent.width
                        text: general.launcher.launch_command
                        placeholder: "umu-run {exe}"
                        onEdited: function (t) { general.setLauncher("launch_command", t) }
                    }
                    SkinText { width: parent.width; kind: "text.dim"; wrapMode: Text.Wrap
                               text: app.t("Leave empty to use Wine, or UMU if Wine is not installed. {exe} stands for the game's path; without it, the path is added at the end.") }
                    SkinText { kind: "text.body"; text: app.t("Wine prefix") }
                    SkinSearchField {
                        objectName: "winePrefixField"
                        width: parent.width
                        text: general.launcher.wine_prefix
                        onEdited: function (t) { general.setLauncher("wine_prefix", t) }
                    }
                    SkinText { width: parent.width; kind: "text.dim"; text: general.runnerText; wrapMode: Text.Wrap }
                    SkinText { kind: "text.body"; text: app.t("Environment variables") }
                    Rectangle {
                        width: parent.width; height: 70
                        color: Style.pick(Style.theme.components["input"].colors, "normal", "#222")
                        border.color: Style.pick(Style.theme.components["input"].borderColors, "normal", "#555")
                        TextEdit {
                            objectName: "envVarsField"
                            anchors { fill: parent; margins: 6 }
                            text: general.launcher.env_vars
                            font.family: "Consolas"; font.pixelSize: 12
                            color: Style.pick(Style.theme.components["input"].textColors, "normal", "#fff")
                            selectByMouse: true
                            onTextChanged: if (text !== general.launcher.env_vars) general.setLauncher("env_vars", text)
                        }
                    }
                    SkinText { width: parent.width; kind: "text.dim"; wrapMode: Text.Wrap
                               text: app.t("Optional. One NAME=value per line, given only to the game when PLAY starts it – for example DXVK_HUD=fps.") }
                    SkinText { visible: general.envProblems !== ""; width: parent.width; kind: "text.error"
                               wrapMode: Text.Wrap; text: general.envProblems }
                    Rectangle { width: parent.width; height: 1
                                color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }
                }

                // ── GENERAL ────────────────────────────────────────────
                SkinText { kind: "text.heading"; text: app.t("GENERAL") }
                SkinCheckBox { text: app.t("Clear WDB on game launch")
                               checked: general.clearWdb; onToggled: general.clearWdb = !general.clearWdb
                               SkinToolTipArea { tip: app.t("Deletes the game's WDB cache before each launch: the item and creature data it keeps is fetched again from the server.") } }
                SkinCheckBox { text: app.t("Minimize {app} on game launch").replace("{app}", "EqUpdater")
                               checked: general.closeOnLaunch; onToggled: general.closeOnLaunch = !general.closeOnLaunch }
                SkinCheckBox { text: app.t("Install essential mods")
                               checked: general.autoMods; onToggled: general.autoMods = !general.autoMods }
                SkinCheckBox { text: app.t("Install recommended addons")
                               checked: general.autoAddons; onToggled: general.autoAddons = !general.autoAddons }
                SkinCheckBox { text: app.t("Show server status in the footer")
                               objectName: "serverStatusToggle"
                               checked: server.enabled; onToggled: server.enabled = !server.enabled
                               SkinToolTipArea { tip: app.t("Shows how many players are online and whether the login server answers, from OctoBot's public status files. Turn it off and EqUpdater makes no such requests.") } }
                SkinCheckBox { text: app.t("Animated background")
                               checked: settings.animatedBackground
                               onToggled: settings.animatedBackground = !settings.animatedBackground }
                SkinCheckBox { text: app.t("Low-memory rendering")
                               objectName: "softwareRenderingToggle"
                               checked: settings.softwareRendering
                               onToggled: settings.softwareRendering = !settings.softwareRendering
                               SkinToolTipArea { tip: app.t("Draws with the processor instead of the graphics card: about 30 MB less memory and a safe choice without a working graphics driver, at the cost of a little more processor use. Takes effect after a restart.") } }
                Row {
                    visible: settings.rendererNeedsRestart
                    spacing: 10
                    SkinText { kind: "text.dim"; anchors.verticalCenter: parent.verticalCenter
                               text: app.t("Restart EqUpdater to change the rendering.") }
                    SkinButton { kind: "button.small"; text: app.t("Restart now"); onClicked: app.restartNow() }
                }
                SkinCheckBox { text: app.t("Pause animation when the window is not focused")
                               checked: settings.pauseWhenUnfocused
                               onToggled: settings.pauseWhenUnfocused = !settings.pauseWhenUnfocused }
                Rectangle { width: parent.width; height: 1
                            color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }

                // ── SOCIAL ─────────────────────────────────────────────
                SkinText { kind: "text.heading"; text: app.t("SOCIAL") }
                SkinCheckBox { objectName: "socialEnabledToggle"; text: app.t("Enable social sidebar")
                               checked: social.values.social_enabled
                               onToggled: social.setSetting("social_enabled", !social.values.social_enabled) }
                SkinCheckBox { objectName: "showLauncherToggle"; text: app.t("Show my launcher presence")
                               checked: social.values.show_launcher_presence
                               onToggled: social.setSetting("show_launcher_presence", !social.values.show_launcher_presence) }
                SkinCheckBox { objectName: "showGameToggle"; text: app.t("Show my in-game presence")
                               checked: social.values.show_game_presence
                               onToggled: social.setSetting("show_game_presence", !social.values.show_game_presence) }
                SkinCheckBox { objectName: "showCharacterToggle"; text: app.t("Show my current character")
                               checked: social.values.show_character
                               onToggled: social.setSetting("show_character", !social.values.show_character) }
                SkinCheckBox { objectName: "showRealmToggle"; text: app.t("Show my realm")
                               checked: social.values.show_realm
                               onToggled: social.setSetting("show_realm", !social.values.show_realm) }
                SkinCheckBox { objectName: "showZoneToggle"; text: app.t("Show my current zone")
                               checked: social.values.show_zone
                               onToggled: social.setSetting("show_zone", !social.values.show_zone) }
                SkinCheckBox { objectName: "notifyToggle"; text: app.t("Friend login notifications")
                               checked: social.values.friend_notifications
                               onToggled: social.setSetting("friend_notifications", !social.values.friend_notifications) }
                SkinText { kind: "text.body"; text: app.t("Default presence status") }
                Row {
                    spacing: 6
                    Repeater {
                        model: social.statusChoices
                        SkinButton { kind: "button.small"; selected: social.values.default_status === modelData
                                     text: modelData === "online" ? app.t("Online") : modelData === "away" ? app.t("Away") : app.t("Do Not Disturb")
                                     onClicked: social.setSetting("default_status", modelData) }
                    }
                }
                SkinText { kind: "text.body"; text: app.t("Set Away automatically when EqUpdater is idle") }
                Row {
                    spacing: 6
                    Repeater {
                        model: social.autoAwayChoices
                        SkinButton { kind: "button.small"; selected: social.values.auto_away_minutes === modelData
                                     text: modelData === 0 ? app.t("Never") : modelData + " " + app.t("min")
                                     onClicked: social.setSetting("auto_away_minutes", modelData) }
                    }
                }
                SkinText { kind: "text.body"; text: app.t("Refresh friends every") }
                Row {
                    spacing: 6
                    Repeater {
                        model: social.intervalChoices
                        SkinButton { kind: "button.small"; selected: social.values.presence_interval === modelData
                                     text: modelData + " s"
                                     onClicked: social.setSetting("presence_interval", modelData) }
                    }
                }
                SkinText { width: parent.width; kind: "text.dim"; wrapMode: Text.Wrap
                           text: app.t("Nothing about you is shared today: OctoWoW offers no service for presence yet. These choices are kept and applied when one exists. Your friends list stays on this computer.") }
                SkinButton { objectName: "settingsSignOut"; visible: account.signedIn; kind: "button.small"
                             text: app.t("Sign Out") + " (" + account.username + ")"; onClicked: account.signOut() }
                Rectangle { width: parent.width; height: 1
                            color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }

                // ── LANGUAGE / FONT ────────────────────────────────────
                SkinText { kind: "text.heading"; text: app.t("Language") }
                SkinText { kind: "text.body"; text: general.language }
                SkinText { kind: "text.dim"; width: parent.width; wrapMode: Text.Wrap
                           text: app.t("Follows the game's Language (Tweaks). A change shows here after a restart, which you are offered.") }
                SkinText { kind: "text.heading"; text: app.t("FONT") }
                Row {
                    spacing: 6
                    Repeater {
                        model: general.fontChoices
                        SkinButton {
                            kind: "button.small"
                            enabled: !modelData.locked
                            selected: general.fontOverride === modelData.id
                            text: modelData.id === "" ? app.t("Skin default") : modelData.family
                            onClicked: general.setFontOverride(modelData.id)
                        }
                    }
                }
                Rectangle { width: parent.width; height: 1
                            color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }

                // ── SKIN ────────────────────────────────────────────────
                SkinText { kind: "text.heading"; text: app.t("Skin") }
                Column {
                    width: parent.width
                    spacing: 4
                    Repeater {
                        model: settings.skins
                        SkinButton {
                            width: parent.width
                            selected: modelData.id === settings.currentSkin
                            text: modelData.name + (modelData.author !== "" ? "  ·  " + modelData.author : "")
                                  + (modelData.problems.length > 0 ? "   ⚠" : "")
                            onClicked: settings.setSkin(modelData.id)
                        }
                    }
                }
                SkinText {
                    visible: settings.skinProblems.length > 0
                    width: parent.width
                    kind: "text.dim"
                    wrapMode: Text.Wrap
                    text: settings.skinProblems.join("\n")
                    maximumLineCount: 6
                    elide: Text.ElideRight
                }
                Row {
                    spacing: 10
                    SkinButton { kind: "button.small"; text: app.t("Open skins folder")
                                 onClicked: settings.openSkinsFolder() }
                    SkinButton { kind: "button.small"; text: app.t("Restore default skin")
                                 onClicked: settings.restoreDefaultSkin() }
                }
                Rectangle { width: parent.width; height: 1
                            color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }

                // ── TROUBLESHOOTING ─────────────────────────────────────
                SkinText { kind: "text.heading"; text: app.t("TROUBLESHOOTING") }
                Flow {
                    width: parent.width
                    spacing: 8
                    SkinButton { kind: "button.small"; text: app.t("Verify game files")
                                 onClicked: { root.close(); diag.openView("scan") }
                                 SkinToolTipArea { tip: app.t("Reads every file in the game folder and compares it with the client's manifest. Nothing is changed or downloaded.") } }
                    SkinButton { kind: "button.small"; text: app.t("Login Doctor")
                                 onClicked: { root.close(); diag.openView("doctor") }
                                 SkinToolTipArea { tip: app.t("Checks this computer for common login problems: the game client, its login settings, OctoLogin and the connection to OctoWoW.") } }
                    SkinButton { kind: "button.small"; text: app.t("Game Diagnostics")
                                 onClicked: { root.close(); diag.openView("attempt") }
                                 SkinToolTipArea { tip: app.t("What happened after you pressed PLAY, a check of the game folder, and a report you can send for help.") } }
                    SkinButton { objectName: "openLogsButton"; kind: "button.small"; text: app.t("Open logs folder")
                                 onClicked: general.openLogsFolder()
                                 SkinToolTipArea { tip: app.t("Where EqUpdater saves a report if it stops with an error. Nothing is sent anywhere: attach a report yourself if you ask for help.") } }
                    SkinButton { kind: "button.small"; text: app.t("Show Session Log")
                                 onClicked: { general.refreshLog(); root.showLog = true }
                                 SkinToolTipArea { tip: app.t("Everything EqUpdater has done since it started: what it checked, what it changed and why anything failed.") } }
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
