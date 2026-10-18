.pragma library
.import "AquaMotionData.js" as Aqua
.import "OctoMotionData.js" as Octo

// Preserve the public wull IPC/config/history namespace while naming the cast
// Aqua/Octo. The curves are authored by Blender, not runtime sprite assets.
function retired(action) {
    return ["fall", "faceplant", "buttplant", "fallVanish", "ice"].includes(action)
}
function runtimeLibrary(authored) {
    const clips = {}
    for (const action of Object.keys(authored.clips))
        if (!retired(action)) clips[action] = authored.clips[action]
    return {
        clips: clips,
        // Keep the archived Blender curves intact. Direct/legacy requests for
        // a retired action sample neutral channels, never a tipping pose.
        sample: function(action, channel, phase) {
            return authored.sample(retired(action) ? "" : action, channel, phase)
        }
    }
}
var aquaRuntime = null
var octoRuntime = null
function forCharacter(character) {
    if (character === "octo") {
        if (!octoRuntime) octoRuntime = runtimeLibrary(Octo)
        return octoRuntime
    }
    if (!aquaRuntime) aquaRuntime = runtimeLibrary(Aqua)
    return aquaRuntime
}
function other(character) { return character === "octo" ? "aqua" : "octo" }
function name(character) { return character === "octo" ? "Octo" : "Aqua" }
