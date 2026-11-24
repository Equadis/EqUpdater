import QtQuick
import EqUi

// Pages not migrated yet (Phase 3). Loaded lazily, only when opened.
SkinPanel {
    property string pageName: ""
    SkinText {
        anchors.centerIn: parent
        kind: "text.dim"
        horizontalAlignment: Text.AlignHCenter
        text: pageName + "\n" + app.t("This page is still on the 2.x launcher in this build.")
    }
}
