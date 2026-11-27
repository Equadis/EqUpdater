import QtQuick
import EqUi

// The per-item actions of the Addons, Mods and MPQ pages: one component, so the three pages
// cannot drift apart. Always these four, always in this order and in these slots; an action that
// does not apply is dimmed and inert, never removed, so the others do not move.
//   folder (open the folder) · link (open the repository) · trash (uninstall: deletes the
//   installed files) · X (stop managing: the files stay, EqUpdater lets go of them)
Row {
    id: root
    property bool folderEnabled: true
    property bool linkEnabled: true
    property bool uninstallEnabled: true
    property bool releaseEnabled: true
    property string releaseTip: app.t("Stop managing. Your files are kept.")
    signal folder()
    signal link()
    signal uninstall()
    signal release()
    spacing: 2

    SkinIconButton { objectName: "actionFolder"; kind: "icon.folder"; tip: app.t("Open folder")
                     enabled: root.folderEnabled; onClicked: root.folder() }
    SkinIconButton { objectName: "actionLink"; kind: "icon.link"; tip: app.t("Open repository")
                     enabled: root.linkEnabled; onClicked: root.link() }
    SkinIconButton { objectName: "actionUninstall"; kind: "icon.remove"; tip: app.t("Uninstall")
                     enabled: root.uninstallEnabled; onClicked: root.uninstall() }
    SkinIconButton { objectName: "actionRelease"; kind: "icon.close"; tip: root.releaseTip
                     enabled: root.releaseEnabled; onClicked: root.release() }
}
