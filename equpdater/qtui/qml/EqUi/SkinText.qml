import QtQuick
import EqUi

// Text in one of the skin's text styles (text.body, text.dim, text.heading ...).
Text {
    id: root
    property string kind: "text.body"
    property string vstate: "normal"
    readonly property var spec: Style.theme.components[kind]
    color: Style.pick(spec.textColors, vstate, "#ffffff")
    linkColor: Style.pick(Style.theme.components["text.link"].textColors, "normal", color)
    font.family: spec.font.family
    font.pixelSize: spec.font.size
    font.bold: !!spec.font.bold
    font.italic: !!spec.font.italic
    renderType: Text.QtRendering
    textFormat: Text.PlainText
    elide: Text.ElideNone
}
