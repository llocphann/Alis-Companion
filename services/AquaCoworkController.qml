import QtQuick
import "../modules/abyss/companion/AquaCoworkPerformance.js" as Performance

Item {
    id: root
    property string character: "aqua"
    property var directorState: null
    property var hostContext: null
    property bool quietSettled: false
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
    function samePose(a, b): bool {
        if (a === b) return true
        if (!a || !b) return false
        for (const k in b) {
            if (Math.abs((a[k] ?? 0) - (b[k] ?? 0)) > 1e-5)
                return false
        }
        return true
    }
    function step(): void {
        if (!directorState || !hostContext || !performanceState) {
            revoke()
            return
        }
        const v = Performance.update(performanceState, directorState, hostContext, clock())
        if (active !== v.active) active = v.active
        if (renderProp !== v.renderProp) renderProp = v.renderProp
        if (phase !== v.phase) phase = v.phase
        if (clip !== v.clip) clip = v.clip
        if (epoch !== v.epoch) epoch = v.epoch
        if (reason !== v.reason) reason = v.reason
        const nextPose = v.active ? v.pose : null
        if (!samePose(pose, nextPose))
            pose = nextPose
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
    onQuietSettledChanged: if (active) step()
    Timer {
        id: tick
        interval: root.phase === "loop" ? 50 : 33
        repeat: true
        running: false
        onTriggered: root.step()
    }
    Component.onDestruction: revoke()
}
