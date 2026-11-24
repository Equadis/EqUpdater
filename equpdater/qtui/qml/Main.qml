import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as C
import EqUi
import "pages"

Window {
    id: win
    visible: true
    width: 1000
    height: 700
    minimumWidth: 860
    minimumHeight: 600
    title: "EqUpdater"
    color: Style.pick(Style.theme.components["window"].colors, "normal", "#000")

    // Only these two facts reach Python; it decides what animates.
    onVisibilityChanged: function (v) { app.setWindowState(win.visibility === Window.Minimized, win.active) }
    onActiveChanged: app.setWindowState(win.visibility === Window.Minimized, win.active)
    Component.onCompleted: app.windowShown()

    readonly property var layout: Style.theme.layout
    readonly property var pages: [
        { id: "news", label: "NEWS" }, { id: "tweaks", label: "TWEAKS" },
        { id: "addons", label: "ADDONS" }, { id: "mods", label: "MODS" },
        { id: "mpq", label: "MPQ" }
    ]

    Connections {
        target: app
        function onDebugAction(name) {
            if (name === "settings") settingsView.open()
            else if (name === "menu") menu.open()
            else app.tab = name
        }
        function onScreenshotRequested(path) {
            win.contentItem.grabToImage(function (r) { r.saveToFile(path) })
        }
    }

    Background { anchors.fill: parent }

    // ── header: logo left, navigation right, settings gear ─────────────
    Item {
        id: header
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: win.layout.headerHeight
        Row {
            anchors { left: parent.left; leftMargin: win.layout.contentMargin
                      verticalCenter: parent.verticalCenter }
            spacing: 14
            SkinIcon { kind: "icon.logo"; size: win.layout.logoSize
                       anchors.verticalCenter: parent.verticalCenter }
            SkinText { kind: "text.title"; text: "EqUpdater"
                       anchors.verticalCenter: parent.verticalCenter }
        }
        Row {
            id: nav
            anchors { right: parent.right; rightMargin: win.layout.navRight
                      verticalCenter: parent.verticalCenter; verticalCenterOffset: 4 }
            spacing: win.layout.navSpacing
            Repeater {
                model: win.pages
                SkinNavTab {
                    text: app.t(modelData.label)
                    selected: app.tab === modelData.id
                    badge: modelData.id === "mods" ? app.modUpdates
                         : modelData.id === "addons" ? app.addonUpdates : 0
                    onClicked: app.tab = modelData.id
                }
            }
        }
        Item {
            width: 28; height: 28
            anchors { right: parent.right; rightMargin: 10; top: parent.top; topMargin: 8 }
            activeFocusOnTab: true
            Accessible.role: Accessible.Button
            Accessible.name: app.t("Settings")
            Keys.onSpacePressed: settingsView.open()
            SkinIcon { kind: "icon.settings"; size: 18; anchors.centerIn: parent
                       opacity: gh.hovered ? 1 : 0.7 }
            HoverHandler { id: gh; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: settingsView.open() }
        }
    }

    // ── pages: created on first visit, then kept (instant switching) ───
    Item {
        id: content
        anchors { left: parent.left; right: parent.right; top: header.bottom; bottom: footer.top
                  leftMargin: win.layout.contentMargin; rightMargin: win.layout.contentMargin
                  bottomMargin: 8 }
        Repeater {
            model: win.pages
            Loader {
                anchors.fill: parent
                active: app.visited.indexOf(modelData.id) >= 0
                visible: app.tab === modelData.id
                source: modelData.id === "news" ? "pages/NewsPage.qml" : "pages/PlaceholderPage.qml"
                onLoaded: if (modelData.id !== "news") item.pageName = app.t(modelData.label)
            }
        }
    }

    // ── footer: status + Play (left), links + version (right) ──────────
    Item {
        id: footer
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: win.layout.footerHeight
        Column {
            anchors { left: parent.left; leftMargin: win.layout.contentMargin
                      verticalCenter: parent.verticalCenter }
            spacing: 8
            SkinText {
                kind: app.statusIsError ? "text.error" : "text.body"
                font.bold: true
                text: app.statusText
            }
            Row {
                spacing: 14
                SkinSplitButton {
                    id: play
                    text: launch.playText
                    enabled: launch.canPlay
                    menuOpen: menu.visible
                    onClicked: launch.play()
                    onArrowClicked: menu.visible ? menu.close() : menu.open()
                }
                SkinProgressBar {
                    visible: app.busy
                    width: 160
                    anchors.verticalCenter: parent.verticalCenter
                    indeterminate: true
                }
            }
        }
        Row {
            anchors { right: parent.right; rightMargin: win.layout.contentMargin
                      bottom: parent.bottom; bottomMargin: 14 }
            spacing: 14
            SkinText { kind: "text.dim"; text: "v" + app.version
                       anchors.verticalCenter: parent.verticalCenter }
            Repeater {
                model: [{ k: "icon.discord", url: app.discordUrl, tip: "Discord" },
                        { k: "icon.support", url: app.supportUrl, tip: "Support" }]
                Item {
                    width: 26; height: 26
                    activeFocusOnTab: true
                    Accessible.role: Accessible.Link
                    Accessible.name: modelData.tip
                    Keys.onSpacePressed: app.openUrl(modelData.url)
                    SkinIcon { kind: modelData.k; size: 24; anchors.centerIn: parent
                               opacity: lh.hovered ? 1 : 0.8 }
                    HoverHandler { id: lh; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: app.openUrl(modelData.url) }
                }
            }
        }
    }

    SkinMenu {
        id: menu
        parent: C.Overlay.overlay
        heading: launch.heading
        entries: launch.entries
        onChosen: function (label) { launch.chooseDefault(label); close() }
        onAboutToShow: launch.refresh()
        // Above the button, left edges in line, as in 2.x. Placed on every
        // open (and when its content changes size), not bound once.
        function place() {
            var o = play.mapToItem(C.Overlay.overlay, 0, 0)
            x = o.x
            y = Math.max(4, o.y - height - 6)
        }
        onOpened: place()
        onHeightChanged: if (visible) place()
    }

    SettingsOverlay { id: settingsView; anchors.fill: parent }
}
