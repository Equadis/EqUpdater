import QtQuick
import EqUi

// The X in the top-right corner of a window, panel or dialog: the skin's own close icon, one size and one
// position everywhere (anchor it right and vertically centred in a 30 px header). Escape is handled by the
// overlay it belongs to.
SkinIconButton {
    objectName: "closeButton"
    kind: "icon.close"
    size: 14
    tip: app.t("Close")
    width: 28
    height: 28
}
