import QtQuick
import QtQuick.Controls.Basic as C
import EqUi

// One news column (Announcements or Changelog).
//
// Scrolling: this ListView is the ONLY scrolling item in the panel. Posts are
// plain delegates (no nested Flickables or scrolling text widgets), and a
// post's text is one TextEdit whose contentHeight is the only height authority.
// Delegates are virtualised: only visible posts exist as items.
SkinPanel {
    id: root
    objectName: "newsPanel"
    property var section            // NewsSectionBridge (Python)
    readonly property int previewLines: 14

    // header ---------------------------------------------------------
    Item {
        id: head
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 34
        SkinText {
            kind: "panel.header"
            text: app.t(root.section.title).toUpperCase()
            anchors.verticalCenter: parent.verticalCenter
        }
        Item {
            id: refresh
            width: 28; height: 28
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            activeFocusOnTab: true
            Accessible.role: Accessible.Button
            Accessible.name: qsTr("Refresh")
            Keys.onSpacePressed: root.section.refresh()
            SkinIcon {
                id: ricon
                kind: "icon.refresh"
                size: 16
                anchors.centerIn: parent
                opacity: rh.hovered ? 1.0 : 0.75
                RotationAnimator on rotation {
                    running: root.section.loading && root.visible
                    from: 0; to: 360; duration: 900; loops: Animation.Infinite
                }
            }
            HoverHandler { id: rh; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: root.section.refresh() }
        }
    }
    Rectangle {
        id: rule
        anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 6 }
        height: 1
        color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
    }
    SkinText {
        id: banner
        anchors { left: parent.left; right: parent.right; top: rule.bottom; topMargin: 8 }
        visible: text !== ""
        kind: root.section.error !== "" ? "text.error" : "text.dim"
        wrapMode: Text.Wrap
        text: root.section.error !== ""
              ? (list.count > 0
                 ? app.t("{section} unavailable: {reason}").replace("{section}", app.t(root.section.title)).replace("{reason}", app.t(root.section.error)) + " · " + app.t("showing cached content")
                 : app.t("{section} unavailable: {reason}").replace("{section}", app.t(root.section.title)).replace("{reason}", app.t(root.section.error)))
              : root.section.note
    }

    ListView {
        id: list
        objectName: root.section.id + "List"
        anchors {
            left: parent.left; right: parent.right; bottom: parent.bottom
            top: banner.visible ? banner.bottom : rule.bottom
            topMargin: 4
        }
        clip: true
        model: root.section.model
        boundsBehavior: Flickable.StopAtBounds
        reuseItems: false
        cacheBuffer: 600
        rightMargin: 10
        C.ScrollBar.vertical: SkinScrollBar {}

        delegate: Item {
            id: d
            objectName: "post-" + model.id
            width: ListView.view.width - 14
            height: col.implicitHeight + 1
            // No scroll compensation here on purpose: ListView lays items out from
            // the first visible one, so a post above the viewport that grows or
            // shrinks (text arriving in the background) does not move what the
            // reader is looking at. tests/test_v3_qt.py holds that to account.

            Column {
                id: col
                width: parent.width
                spacing: 0

                // title row (click to expand/collapse)
                Item {
                    width: parent.width
                    implicitHeight: titleCol.implicitHeight + 14
                    height: implicitHeight
                    Column {
                        id: titleCol
                        y: 10
                        width: parent.width
                        spacing: 3
                        Flow {
                            width: parent.width
                            spacing: 8
                            SkinText {
                                kind: "text.heading"
                                text: model.title
                                width: Math.min(implicitWidth, titleCol.width)
                                wrapMode: Text.Wrap
                                vstate: tp.hovered ? "hover" : "normal"
                            }
                            SkinText {
                                kind: "text.new"
                                visible: model.isNew
                                text: app.t("NEW!")
                                font.bold: true
                                font.italic: true
                            }
                        }
                        SkinText {
                            kind: "text.dim"
                            width: parent.width
                            elide: Text.ElideRight
                            text: (model.author !== "" ? app.t("by") + " " + model.author + " · " : "")
                                  + model.dateText + "  " + (model.expanded ? "▴" : "▾")
                        }
                    }
                    HoverHandler { id: tp; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: root.section.toggle(model.id) }
                    Accessible.role: Accessible.Button
                    Accessible.name: model.title
                    activeFocusOnTab: true
                    Keys.onSpacePressed: root.section.toggle(model.id)
                    Keys.onReturnPressed: root.section.toggle(model.id)
                }

                // body: created only while the post is open, so a collapsed post
                // costs no text layout (and a long post none until it is read)
                Loader {
                    id: bodyLoader
                    width: parent.width
                    active: model.expanded
                    visible: active
                    sourceComponent: bodyComp
                    height: item ? item.implicitHeight : 0
                }
                Component {
                    id: bodyComp
                    Item {
                        id: bodyBox
                        width: bodyLoader.width
                        implicitHeight: bodyCol.implicitHeight
                        readonly property real lineH: fm.lineSpacing
                        readonly property real limit: lineH * root.previewLines
                        readonly property bool canClamp: model.clamp && edit.contentHeight > limit + lineH
                        readonly property bool clamped: canClamp && !model.full
                        FontMetrics { id: fm; font: edit.font }
                        Column {
                            id: bodyCol
                            width: parent.width
                            spacing: 6
                            Item {
                                width: parent.width
                                height: bodyBox.clamped ? bodyBox.limit : Math.max(edit.contentHeight, 18)
                                clip: bodyBox.clamped
                                TextEdit {
                                    id: edit
                                    width: parent.width
                                    readOnly: true
                                    selectByMouse: true
                                    wrapMode: TextEdit.Wrap
                                    textFormat: TextEdit.RichText
                                    text: model.bodyHtml
                                    visible: model.hasBody
                                    color: Style.pick(Style.theme.components["text.body"].textColors, "normal", "#fff")
                                    selectionColor: Style.pick(Style.theme.components["text.link"].textColors, "normal", "#58f")
                                    font.family: Style.theme.components["text.body"].font.family
                                    font.pixelSize: Style.theme.components["text.body"].font.size
                                    onLinkActivated: function (link) { root.section.openUrl(link) }
                                    HoverHandler {
                                        cursorShape: edit.hoveredLink !== "" ? Qt.PointingHandCursor : Qt.IBeamCursor
                                    }
                                }
                                SkinText {
                                    visible: !model.hasBody && model.error === ""
                                    kind: "text.dim"
                                    text: app.t("Loading…")
                                }
                                SkinText {
                                    visible: model.error !== ""
                                    kind: "text.error"
                                    width: parent.width
                                    wrapMode: Text.Wrap
                                    text: model.error
                                }
                            }
                            SkinButton {
                                visible: bodyBox.canClamp
                                kind: "button.link"
                                text: model.full ? app.t("Show less") : app.t("Read more")
                                onClicked: root.section.toggleFull(model.id)
                            }
                            SkinButton {
                                visible: model.url !== ""
                                kind: "button.link"
                                text: app.t("Open in Browser") + " ↗"
                                onClicked: root.section.openUrl(model.url)
                            }
                        }
                    }
                }

                Rectangle {
                    width: parent.width; height: 1
                    color: Style.pick(Style.theme.components["divider"].colors, "normal", "#444")
                }
            }
        }

        footer: Item {
            width: list.width
            height: visible ? 52 : 0
            visible: root.section.hasMore || root.section.loading
            SkinButton {
                anchors.centerIn: parent
                visible: !root.section.loading
                text: root.section.id === "changelog" ? app.t("Show more") : app.t("Load more")
                onClicked: root.section.loadMore()
            }
            SkinText {
                anchors.centerIn: parent
                visible: root.section.loading
                kind: "text.dim"
                text: app.t(root.section.id === "changelog" ? "Loading changelog…" : "Loading announcements…")
            }
        }
    }

    SkinText {
        anchors.centerIn: list
        visible: list.count === 0
        kind: root.section.error !== "" ? "text.error" : "text.dim"
        text: root.section.loading ? app.t(root.section.id === "changelog"
                                           ? "Loading changelog…" : "Loading announcements…")
                                   : ""
    }
}
