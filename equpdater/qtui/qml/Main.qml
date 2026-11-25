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
        function onMinimizeRequested() { win.showMinimized() }
        function onDebugAction(name) {
            if (name === "settings") win.openSettings()
            else if (name === "menu") menu.open()
            else if (name === "server") footerChip.toggle()
            else if (name.indexOf("diag:") === 0) diag.openView(name.substring(5))
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
            id: logoRow
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
            // `navAlign: "left"` puts the tabs beside the logo (Battle.net's way)
            readonly property bool alignLeft: win.layout.navAlign === "left"
            x: alignLeft ? logoRow.x + logoRow.width + (win.layout.navLeftMargin || 40)
                    : parent.width - width - win.layout.navRight
            anchors { verticalCenter: parent.verticalCenter; verticalCenterOffset: 4 }
            spacing: win.layout.navSpacing
            Repeater {
                model: win.pages
                SkinNavTab {
                    text: app.t(modelData.label)
                    selected: app.tab === modelData.id
                    badge: modelData.id === "mods" ? app.modUpdates
                         : modelData.id === "addons" ? app.addonUpdates
                         : modelData.id === "mpq" ? app.mpqUpdates : 0
                    onClicked: app.tab = modelData.id
                }
            }
        }
        AccountChip {
            id: accountChip
            anchors { right: gear.left; rightMargin: 12; verticalCenter: gear.verticalCenter }
        }
        Item {
            id: gear
            width: 28; height: 28
            anchors { right: parent.right; rightMargin: 10; top: parent.top; topMargin: 8 }
            activeFocusOnTab: true
            Accessible.role: Accessible.Button
            Accessible.name: app.t("Settings")
            Keys.onSpacePressed: win.openSettings()
            SkinIcon { kind: "icon.settings"; size: 18; anchors.centerIn: parent
                       opacity: gh.hovered ? 1 : 0.7 }
            HoverHandler { id: gh; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: win.openSettings() }
        }
    }

    // ── pages: created on first visit, then kept (instant switching) ───
    // The social sidebar sits to the right of the pages when there is room (or when it is
    // only the narrow rail); on a narrow window an open sidebar covers the page's edge instead.
    SocialSidebar {
        id: sidebar
        z: 20
        anchors { right: parent.right; top: header.bottom; bottom: footer.top; bottomMargin: 8
                  rightMargin: 14 }
    }
    Binding { target: social; property: "skinDefaultExpanded"; value: win.layout.sidebarDefault === "expanded" }
    readonly property bool sidebarDocked: !social.enabled || social.collapsed || win.width >= 940
    Item {
        id: content
        objectName: "pageArea"
        anchors { left: parent.left; right: parent.right; top: header.bottom; bottom: footer.top
                  leftMargin: win.layout.contentMargin
                  rightMargin: win.layout.contentMargin + (social.enabled && win.sidebarDocked
                               ? sidebar.width + 10 : 0)
                  bottomMargin: 8 }
        Repeater {
            model: win.pages
            Loader {
                anchors.fill: parent
                active: app.visited.indexOf(modelData.id) >= 0
                visible: app.tab === modelData.id
                source: modelData.id === "news" ? "pages/NewsPage.qml"
                      : modelData.id === "tweaks" ? "pages/TweaksPage.qml"
                      : modelData.id === "mods" ? "pages/ModsPage.qml"
                      : modelData.id === "addons" ? "pages/AddonsPage.qml"
                      : modelData.id === "mpq" ? "pages/MpqPage.qml"
                      : "pages/PlaceholderPage.qml"
                onLoaded: if (item.pageName !== undefined) item.pageName = app.t(modelData.label)
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
                    objectName: "batchProgress"
                    visible: app.busy
                    width: 160
                    anchors.verticalCenter: parent.verticalCenter
                    indeterminate: !app.progressActive
                    value: app.progressValue
                }
                SkinText {
                    objectName: "batchProgressText"
                    visible: app.busy && app.progressActive
                    anchors.verticalCenter: parent.verticalCenter
                    kind: "text.dim"
                    text: app.progressText
                }
            }
        }
        Row {
            anchors { right: parent.right; rightMargin: win.layout.contentMargin
                      bottom: parent.bottom; bottomMargin: 14 }
            spacing: 14
            SkinIcon {
                objectName: "updateAllSpinner"
                visible: app.updateAllRunning
                kind: "icon.update"; size: 24
                anchors.verticalCenter: parent.verticalCenter
                RotationAnimator on rotation { running: app.updateAllRunning; from: 0; to: 360
                                               duration: 1200; loops: Animation.Infinite }
            }
            SkinIconButton {
                objectName: "updateAllButton"
                visible: app.updateAllCount > 0 && !app.busy && !app.updateAllRunning
                kind: "icon.update"
                tip: app.updateAllCount === 1 ? app.t("Update all addons and mods (1 update available)")
                     : app.t("Update all addons and mods ({count} updates available)").replace("{count}", app.updateAllCount)
                anchors.verticalCenter: parent.verticalCenter
                onClicked: app.updateAll()
            }
            ServerChip { id: footerChip; anchors.verticalCenter: parent.verticalCenter }
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

    // Overlays are built the first time they are wanted, not at start-up.
    LazyOverlay { id: settingsL; source: "SettingsOverlay.qml" }
    LazyOverlay { z: 90; source: "DiagnosticsOverlay.qml"; wanted: diag.isOpen }
    LazyOverlay { z: 90; source: "CustomOverlay.qml"; wanted: customSrc.isOpen }
    LazyOverlay { z: 90; source: "SetupOverlay.qml"; wanted: setup.isOpen }
    LazyOverlay { z: 95; source: "LanguageOverlay.qml"; wanted: general.languagePromptOpen }
    LazyOverlay { z: 100; source: "DialogHost.qml"; wanted: dialog.open }

    LazyOverlay { z: 92; source: "AddFriendOverlay.qml"; wanted: social.addOpen }

    // A friend coming online (only if the player turned notifications on)
    Rectangle {
        id: toast
        objectName: "toast"
        property string title: ""
        property string body: ""
        visible: false
        z: 120
        anchors { right: parent.right; rightMargin: 20; bottom: footer.top; bottomMargin: 6 }
        width: toastCol.implicitWidth + 28; height: toastCol.implicitHeight + 20
        radius: 6
        color: Style.pick(Style.theme.components["tooltip"].colors, "normal", "#222")
        border.color: Style.pick(Style.theme.components["tooltip"].borderColors, "normal", "#555")
        Column { id: toastCol; anchors.centerIn: parent; spacing: 2
            SkinText { kind: "text.heading"; text: toast.title }
            SkinText { kind: "text.body"; text: toast.body } }
        Timer { id: toastTimer; interval: 5000; onTriggered: toast.visible = false }
        Connections { target: social
            function onToast(title, text) { toast.title = title; toast.body = text; toast.visible = true; toastTimer.restart() } }
    }

    function openSettings() {
        settingsL.seen = true
        if (settingsL.item) settingsL.item.open()
    }
}
