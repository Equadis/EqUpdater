import QtQuick

// An overlay that is not built until it is first wanted (then it stays built).
// Most overlays are never opened in a session; building them all at start
// cost a large part of the start-up time.
Loader {
    property bool wanted: false
    property bool seen: false
    anchors.fill: parent
    active: seen || wanted
    asynchronous: false
    onWantedChanged: if (wanted) seen = true
}
