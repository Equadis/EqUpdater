import QtQuick
import EqUi

// The window background, on its own layer: foreground items never bind to
// anything in here, and nothing in here binds to them, so an animation tick
// repaints this layer only.
//
// Measured choice (docs/v3/PERFORMANCE.md): an AnimatedImage with cache:false
// streams frames from the file (no decoded-frame cache), starts in ~0.2 s and
// idles near 0% CPU; H.264 video was slower to start and used ~20x the CPU.
// Video is still supported for skins that ship it (animationKind "video").
Item {
    id: root
    readonly property var bg: skin.theme.background
    // One switch decides whether anything animates: the setting, the skin
    // having an animation, and the window being visible/focused as configured.
    readonly property bool animate: settings.animatedBackground
                                    && app.animationsActive
                                    && bg.animation !== ""
    readonly property bool animating: anim.status === Loader.Ready && animate

    Rectangle { anchors.fill: parent; color: root.bg.color }

    Image {
        anchors.fill: parent
        visible: root.bg.static !== ""
        source: root.bg.static
        fillMode: Image.PreserveAspectCrop
        cache: true
        smooth: true
        mipmap: false
    }

    Loader {
        id: anim
        anchors.fill: parent
        active: root.bg.animation !== "" && settings.animatedBackground
        sourceComponent: root.bg.animationKind === "video" ? videoC : imageC
    }
    Component {
        id: imageC
        AnimatedImage {
            source: root.bg.animation
            fillMode: Image.PreserveAspectCrop
            cache: false                  // stream; do not keep every frame decoded
            playing: root.animate
            smooth: true
            // shown only once its first frame is ready, so the still never blinks
            opacity: status === Image.Ready ? 1 : 0
        }
    }
    Component {
        id: videoC
        Loader {
            source: "VideoLayer.qml"
            onLoaded: item.play = Qt.binding(function () { return root.animate })
        }
    }

    // darken: one translucent rectangle instead of re-processing every frame
    Rectangle {
        anchors.fill: parent
        color: "black"
        opacity: 1.0 - root.bg.darken
        visible: opacity > 0
    }
    Rectangle { anchors.fill: parent; color: root.bg.overlay; visible: root.bg.overlay !== "#00000000" }
    Rectangle {
        visible: root.bg.fadeTop > 0
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: root.bg.fadeTop
        gradient: Gradient {
            GradientStop { position: 0.0; color: Style.pick(skin.theme.components["window"].colors, "normal", "#000") }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
    Rectangle {
        visible: root.bg.fadeBottom > 0
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: root.bg.fadeBottom
        gradient: Gradient {
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 1.0; color: Style.pick(skin.theme.components["window"].colors, "normal", "#000") }
        }
    }
}
