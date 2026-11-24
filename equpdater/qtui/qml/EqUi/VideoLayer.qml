import QtQuick
import QtMultimedia

// Only loaded for skins whose animation is a video file, so QtMultimedia is
// not initialised at all for the default (image) animation.
Item {
    id: root
    property bool play: true
    onPlayChanged: play ? player.play() : player.pause()
    MediaPlayer {
        id: player
        source: Style.theme.background.animation
        loops: MediaPlayer.Infinite
        videoOutput: out
        Component.onCompleted: if (root.play) play()
    }
    VideoOutput {
        id: out
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectCrop
    }
}
