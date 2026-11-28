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
    property string folderError: ""
    function open() { visible = true; settings.rescanSkins() }
    function close() { if (visible) { visible = false; general.closed() } }
    focus: visible
    onVisibleChanged: if (visible) forceActiveFocus()          // so Escape reaches this window
    Keys.onEscapePressed: close()

    Rectangle {
        anchors.fill: parent
        color: Style.pick(Style.theme.components["scrim"].colors, "normal", "#99000000")
        // Close only for a tap that really landed outside the window. A TapHandler on the scrim
        // also sees taps inside the window (the window's own handler does not stop siblings
        // below it), and a control that is rebuilt by its own click, as a theme change does,
        // lets its tap through. That is what used to close Settings on every selection.
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
        width: Math.min(640, parent.width - 40)
        height: Math.min(640, parent.height - 40)
        anchors.centerIn: parent
        TapHandler { }                                    // swallow clicks

        Item {
            id: head
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 30
            SkinText { kind: "panel.header"; text: app.t("SETTINGS")
                       anchors.verticalCenter: parent.verticalCenter }
            SkinCloseButton { anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                              onClicked: root.close() }
        }

        Flickable {
            id: flick
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
                    SkinButton { kind: "button.small"; text: app.t("Browse"); onClicked: folderDialog.open() }
                    SkinButton { kind: "button.small"; text: app.t("Find")
                                 onClicked: { root.close(); setup.open() }
                                 SkinToolTipArea { tip: app.t("Looks for a game already on this computer, or extracts an archive you downloaded.") } }
                    SkinIconButton { kind: "icon.folder"; tip: app.t("Open folder"); onClicked: general.openGameDir() }
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
                    SkinText { kind: "text.body"; text: app.t("Launch command")
                               SkinToolTipArea { tip: app.t("Leave empty to use Wine, or UMU if Wine is not installed. {exe} stands for the game's path; without it, the path is added at the end.") } }
                    SkinSearchField {
                        objectName: "launchCommandField"
                        width: parent.width
                        text: general.launcher.launch_command
                        placeholder: "umu-run {exe}"
                        onEdited: function (t) { general.setLauncher("launch_command", t) }
                    }
                    SkinText { kind: "text.body"; text: app.t("Wine prefix") }
                    SkinSearchField {
                        objectName: "winePrefixField"
                        width: parent.width
                        text: general.launcher.wine_prefix
                        onEdited: function (t) { general.setLauncher("wine_prefix", t) }
                    }
                    SkinText { width: parent.width; kind: "text.dim"; text: general.runnerText; wrapMode: Text.Wrap }
                    SkinText { kind: "text.body"; text: app.t("Environment variables")
                               SkinToolTipArea { tip: app.t("Optional. One NAME=value per line, given only to the game when PLAY starts it – for example DXVK_HUD=fps.") } }
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
                SkinCheckBox { text: app.t("Pause animation when the window is not focused")
                               checked: settings.pauseWhenUnfocused
                               onToggled: settings.pauseWhenUnfocused = !settings.pauseWhenUnfocused }
                Rectangle { width: parent.width; height: 1
                            color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }

                // ── APPEARANCE ─────────────────────────────────────────
                SkinText { kind: "text.heading"; text: app.t("APPEARANCE") }
                Grid {
                    columns: 2
                    columnSpacing: 12; rowSpacing: 8
                    verticalItemAlignment: Grid.AlignVCenter
                    SkinText { kind: "text.body"; text: app.t("Skin"); width: 60 }
                    Row {
                        spacing: 6
                        SkinSelect {
                            objectName: "skinSelect"
                            controlWidth: 200
                            options: { var out = []
                                       for (var i = 0; i < settings.skins.length; i++)
                                           out.push({ id: settings.skins[i].id, label: settings.skins[i].name })
                                       return out }
                            current: settings.currentSkin
                            onChosen: function (id) { settings.setSkin(id) }
                        }
                        SkinIconButton { objectName: "skinsFolderButton"; kind: "icon.folder"; tip: app.t("Open skins folder")
                                         onClicked: settings.openSkinsFolder() }
                        SkinIconButton { objectName: "restoreSkinButton"; kind: "icon.refresh"; tip: app.t("Restore default skin")
                                         onClicked: settings.restoreDefaultSkin() }
                    }
                    SkinText { kind: "text.body"; text: app.t("Font"); width: 60 }
                    SkinSelect {
                        objectName: "fontSelect"
                        controlWidth: 200
                        options: { var out = [], ch = general.fontChoices
                                   for (var i = 0; i < ch.length; i++)
                                       // each option is drawn in its own typeface, whatever font is in use now;
                                       // a font that is not installed is offered as a download (to the fonts folder)
                                       out.push({ id: ch[i].id, enabled: !ch[i].locked && general.fontBusy === "",
                                                  label: ch[i].id === "" ? app.t("Skin default")
                                                         : ch[i].installed ? ch[i].family
                                                         : ch[i].family + " (" + app.t("download") + ")",
                                                  family: ch[i].id === "" ? skin.nativeFont : ch[i].family,
                                                  installed: ch[i].installed })
                                   return out }
                        current: general.fontOverride
                        onChosen: function (id) {
                            var ch = general.fontChoices, missing = false
                            for (var i = 0; i < ch.length; i++) if (ch[i].id === id && !ch[i].installed) missing = true
                            if (missing) general.downloadFont(id); else general.setFontOverride(id)
                        }
                    }
                }
                SkinText {
                    objectName: "fontStatus"
                    visible: general.fontStatus !== ""
                    width: parent.width
                    kind: "text.dim"
                    wrapMode: Text.Wrap
                    text: general.fontStatus
                }
                SkinText {
                    visible: settings.skinProblems.length > 0
                    width: parent.width
                    kind: "text.dim"
                    wrapMode: Text.Wrap
                    text: settings.skinProblems.join("
")
                    maximumLineCount: 6
                    elide: Text.ElideRight
                }
                Rectangle { width: parent.width; height: 1
                            color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444") }

                // ── LANGUAGE ───────────────────────────────────────────
                SkinText { kind: "text.heading"; text: app.t("LANGUAGE") }
                SkinSelect {
                    objectName: "languageSelect"
                    controlWidth: 200
                    enabled: !tweaks.busy
                    options: tweaks.languageChoices
                    current: { var ch = tweaks.languageChoices
                               for (var i = 0; i < ch.length; i++) if (ch[i].current) return ch[i].id
                               return "" }
                    onChosen: function (id) { tweaks.setLanguage(id) }
                    SkinToolTipArea { tip: app.t("The language of EqUpdater and of the game. EqUpdater offers to restart to show the change.") }
                }
            }
        }
    }
}
