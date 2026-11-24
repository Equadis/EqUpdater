import QtQuick

// An icon from the skin (icon.*), rendered at the size asked from the source
// image. Nothing here repaints unless the source or size changes.
Image {
    property string kind: "icon.refresh"
    property int size: 16
    source: Style.theme.components[kind].icon
    sourceSize.width: size * 2
    sourceSize.height: size * 2
    width: size
    height: size
    smooth: true
    mipmap: true
    asynchronous: false
    fillMode: Image.PreserveAspectFit
}
