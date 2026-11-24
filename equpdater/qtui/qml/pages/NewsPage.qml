import QtQuick
import EqUi

Item {
    id: root
    // Two columns, left slightly wider as in 2.x. They are independent
    // scrolling authorities; resizing only re-flows delegate widths.
    NewsPanel {
        id: left
        section: news.announcements
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        width: (parent.width - skin.theme.layout.panelGap) * 0.52
    }
    NewsPanel {
        section: news.changelog
        anchors { left: left.right; right: parent.right; top: parent.top; bottom: parent.bottom
                  leftMargin: skin.theme.layout.panelGap }
    }
}
