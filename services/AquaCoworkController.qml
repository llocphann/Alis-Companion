import QtQuick
import "../modules/abyss/companion/AquaCoworkPerformance.js" as Performance

Item {
    id: root
    property string character: "aqua"
    property var directorState: null
    property var hostContext: null
    property bool quietSettled: false
    property bool powerSaver: false
    property string powerProfile: "balanced"
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
    function isConstantClip(s, clipName): bool {
        const tracks = s?.clips?.[clipName]?.tracks
        if (!tracks) return false
        for (const channel in tracks) {
            const keys = tracks[channel]
            if (!keys || keys.length === 0) continue
            const first = keys[0][1]
            for (let i = 1; i < keys.length; ++i) {
                if (Math.abs(keys[i][1] - first) > 1e-5)
                    return false
            }
        }
        return true
    }
    function nextSessionDeadlineMs(d, nowMs): int {
        const sessions = d?.sessions
        if (!sessions) return 0
        let best = 0
        for (const k in sessions) {
            const q = sessions[k]
            if (!q) continue
            if (q.waiting >= 0 && nowMs - q.waiting < 45000) {
                const waitRem = Math.max(50, Math.ceil(45000 - (nowMs - q.waiting)))
                best = best === 0 ? waitRem : Math.min(best, waitRem)
            }
            const staleRem = Math.max(50, Math.ceil(900000 - (nowMs - q.seen)))
            best = best === 0 ? staleRem : Math.min(best, staleRem)
        }
        return best
    }
    function step(): void {
        if (!directorState || !hostContext || !performanceState) {
            revoke()
            return
        }
        const now = clock()
        const v = Performance.update(performanceState, directorState, hostContext, now)
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
            const blending = Boolean(performanceState.blendFrom) && (now - performanceState.started < 140)
            const settling = performanceState.lostSince >= 0
            const saverActive = root.powerSaver || root.powerProfile === "power-saver"
            if (v.phase === "loop" && !blending && !settling && (isConstantClip(performanceState, v.clip) || (saverActive && root.quietSettled))) {
                const deadline = nextSessionDeadlineMs(directorState, now)
                if (deadline > 0) {
                    if (tick.interval !== deadline) tick.interval = deadline
                    if (!tick.running) tick.start()
                } else {
                    tick.stop()
                }
            } else {
                const targetInterval = (v.phase === "loop" ? 50 : 33)
                if (tick.interval !== targetInterval) tick.interval = targetInterval
                if (!tick.running) tick.start()
            }
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
        interval: 33
        repeat: true
        running: false
        onTriggered: root.step()
    }
    Component.onDestruction: revoke()
}
