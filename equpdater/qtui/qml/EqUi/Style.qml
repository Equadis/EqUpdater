pragma Singleton
import QtQuick

// Pure helpers for reading a resolved component spec. No colours, paths or
// sizes live in QML: everything comes from the skin (see core/skin.py).
QtObject {
    // The resolved theme, converted to a JS object ONCE per skin change.
    // Reading `skin.theme` in every component would rebuild the whole nested
    // map from Python on each access (measured: dominated start-up and skin
    // switching), so everything reads this instead.
    readonly property var theme: skin.theme

    function pick(map, state, fallback) {
        if (!map) return fallback
        var v = map[state]
        if (v !== undefined && v !== "") return v
        v = map.normal
        return (v !== undefined && v !== "") ? v : fallback
    }
    function has(map, state) {
        return !!(map && map[state] !== undefined && map[state] !== "")
    }
    // The visual state of a control, one rule for every component.
    function stateOf(enabled, pressed, selected, hovered) {
        if (!enabled) return "disabled"
        if (pressed) return "pressed"
        if (selected) return "selected"
        if (hovered) return "hover"
        return "normal"
    }
}
