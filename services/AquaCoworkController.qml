import QtQuick
import "../modules/abyss/companion/AquaCoworkPerformance.js" as Performance

Item {
    id: root
    property string character: "aqua"
    property var directorState: null
    property var hostContext: null
    property var clock: () => Date.now()
    property var performanceState: Performance.newState(character === "octo" ? "octo" : "aqua")
    property bool active: false
    property bool renderProp: false
    property string phase: "none"
    property string clip: ""
    property int epoch: -1
    property string reason: "initial"
    property var pose: null

    function resetCharacter(): void {
        if (performanceState && directorState && performanceState.phase !== "none")
            Performance.yieldNow(performanceState, directorState, "character_changed")
        performanceState = Performance.newState(character === "octo" ? "octo" : "aqua")
        step()
    }
    function revoke(): void {
        tick.stop()
        if (performanceState && directorState)
            Performance.yieldNow(performanceState, directorState, "revoked")
        active = false
        renderProp = false
        phase = "none"
        clip = ""
        pose = null
        reason = "revoked"
    }
    function step(): void {
        if (!directorState || !hostContext || !performanceState) {
            revoke()
            return
        }
        const v = Performance.update(performanceState, directorState, hostContext, clock())
        active = v.active
        renderProp = v.renderProp
        phase = v.phase
        clip = v.clip
        epoch = v.epoch
        reason = v.reason
        pose = v.active ? v.pose : null
        if (v.active) {
            if (!tick.running) tick.start()
        } else {
            tick.stop()
        }
    }
    function triggerCue(kind): bool {
        if (!performanceState || !directorState) return false
        const res = Performance.cue(performanceState, directorState, kind, clock())
        if (!res.accepted) return false
        step()
        return true
    }

    onCharacterChanged: resetCharacter()
    onHostContextChanged: step()
    onDirectorStateChanged: step()
    Timer {
        id: tick
        interval: 33
        repeat: true
        running: false
        onTriggered: root.step()
    }
    Component.onDestruction: revoke()
}
