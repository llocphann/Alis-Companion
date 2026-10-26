.pragma library

var personalities = ["calm", "balanced", "energetic"]
var frequencies = ["always", "frequent", "occasional", "rare"]
// Preserve the persisted "quality" tier and add an explicit higher tier.
// The settings labels are Performance, Balanced and Quality respectively.
var qualities = ["performance", "quality", "detailed"]

function defaults() {
    return {
        enabled: false, character: "aqua", alternateCompanions: false,
        contextEnabled:false,contextFocusEnabled:false,contextAiEnabled:false,
        ambientEnabled:true,ambientFocusEnabled:false,ambientBatteryEnabled:true,ambientMediaEnabled:true,
        pocketEnabled:true,
        output: "", edge: "auto", along: 0.72, size: 1,
        interactive: true, soundEnabled: false, personality: "balanced",
        appearanceFrequency: "always", animationsEnabled: true,
        effectsEnabled: true, hideInFullscreen: false, renderQuality: "quality", autoQuality: false,
        translucency: 0.16, exploreFeatures: true
    }
}

function bounded(value, fallback, minimum, maximum) {
    return typeof value === "number" && Number.isFinite(value)
        ? Math.max(minimum, Math.min(maximum, value)) : fallback
}

function normalize(options) {
    const source = options ?? {}
    const result = defaults()
    for (const key of ["enabled", "alternateCompanions", "interactive", "soundEnabled", "animationsEnabled", "effectsEnabled", "hideInFullscreen", "exploreFeatures", "autoQuality", "contextEnabled", "contextFocusEnabled", "contextAiEnabled", "ambientEnabled", "ambientFocusEnabled", "ambientBatteryEnabled", "ambientMediaEnabled", "pocketEnabled"])
        if (typeof source[key] === "boolean") result[key] = source[key]
    result.character = source.character === "octo" ? "octo" : "aqua"
    // Older persisted placement values are tolerated but no longer pin Aqua.
    // Keep schema compatibility while geometry chooses every visit dynamically.
    result.size = bounded(source.size, result.size, 0.65, 1.5)
    result.translucency = bounded(source.translucency, result.translucency, 0, 0.35)
    result.personality = personalities.includes(source.personality) ? source.personality : "balanced"
    result.appearanceFrequency = frequencies.includes(source.appearanceFrequency) ? source.appearanceFrequency : "always"
    result.renderQuality = qualities.includes(source.renderQuality) ? source.renderQuality : "quality"
    return result
}

function motionScale(personality) {
    return personality === "calm" ? 0.55 : personality === "energetic" ? 1.35 : 1
}

function renderTier(quality, shellQuality) {
    return quality === "performance" ? 0 : quality === "detailed" ? 2 : 1
}
