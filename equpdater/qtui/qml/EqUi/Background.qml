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
    readonly property var bg: Style.theme.background
    // One switch decides whether anything animates: the setting, the skin
    // having an animation, and the window being visible/focused as configured.
    readonly property bool animate: settings.animatedBackground
                                    && app.animationsActive
                                    && bg.animation !== ""
    readonly property bool animating: animate

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

    // Animations are loaded once and kept: a skin switch shows or hides them,
    // it never destroys one. Qt's animated-WebP reader leaks the whole file
    // (about 1.5x its size) every time a movie is released, and GIF a little;
    // reloading on each switch grew the process by ~8 MB per switch to Classic
    // (found by tools/bench/soak.py, reproduced in isolation). Kept, the cost
    // is a one-time ~5 MB per animated skin the player actually visits.
    readonly property bool wantsImage: root.bg.animation !== "" && settings.animatedBackground
                                       && root.bg.animationKind !== "video"
    ListModel { id: pool }
    function remember() {
        if (!wantsImage || root.bg.animation === "") return
        for (var i = 0; i < pool.count; ++i)
            if (pool.get(i).url === root.bg.animation) return
        pool.append({ url: root.bg.animation })
    }
    onWantsImageChanged: remember()
    onBgChanged: remember()
    Component.onCompleted: remember()
    Repeater {
        model: pool
        AnimatedImage {
            required property string url
            objectName: "backgroundAnimation"
            width: root.width; height: root.height
            source: url
            visible: url === root.bg.animation && root.wantsImage
            fillMode: Image.PreserveAspectCrop
            cache: false                  // stream; do not keep every frame decoded
            playing: visible && root.animate
            smooth: true
            // shown only once its first frame is ready, so the still never blinks
            opacity: status === Image.Ready ? 1 : 0
        }
    }
    Loader {
        id: anim
        anchors.fill: parent
        active: root.bg.animation !== "" && settings.animatedBackground
                && root.bg.animationKind === "video"
        source: "VideoLayer.qml"
        onLoaded: item.play = Qt.binding(function () { return root.animate })
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
            GradientStop { position: 0.0; color: Style.pick(Style.theme.components["window"].colors, "normal", "#000") }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
    Rectangle {
        visible: root.bg.fadeBottom > 0
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: root.bg.fadeBottom
        gradient: Gradient {
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 1.0; color: Style.pick(Style.theme.components["window"].colors, "normal", "#000") }
        }
    }
}
