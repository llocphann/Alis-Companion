.pragma library
.import "WullBehaviorDirector.js" as Director

// Staging-only presentation controller. No Timer, desktop reads, actor/window,
// inference or IPC. The existing actor owner supplies one monotonic clock and
// the existing Director state. Assets/cowork is excluded from runtime packages.
var EXIT_GRACE_MS = 1000
var BLEND_MS = 140
var CUE_COOLDOWN_MS = 6000
var CURVES = {"aqua":{"laptop_agent_loop":{"duration":2400,"loop":true,"tracks":{"arm0X":[[0.0,20.0],[1.0,20.0]],"arm0Y":[[0.0,-32.0],[1.0,-32.0]],"arm0Z":[[0.0,-14.0],[0.19999999470180935,-11.0],[0.30000000529819065,-14.0],[0.6400000254313151,-14.0],[0.75,-14.0],[1.0,-14.0]],"arm1X":[[0.0,-20.0],[1.0,-20.0]],"arm1Y":[[0.0,-32.0],[1.0,-32.0]],"arm1Z":[[0.0,-14.0],[0.19999999470180935,-14.0],[0.30000000529819065,-14.0],[0.6400000254313151,-11.0],[0.75,-14.0],[1.0,-14.0]],"eyeOpen":[[0.0,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[1.0,0.550000011920929]]}},"laptop_alert":{"duration":1200,"loop":false,"tracks":{"arm0X":[[0.0,20.0],[1.0,20.0]],"arm0Y":[[0.0,-32.0],[1.0,-32.0]],"arm0Z":[[0.0,-14.0],[1.0,-14.0]],"arm1X":[[0.0,-20.0],[1.0,-20.0]],"arm1Y":[[0.0,-32.0],[1.0,-32.0]],"arm1Z":[[0.0,-14.0],[1.0,-14.0]],"eyeOpen":[[0.0,1.0],[0.19999999470180935,0.30000001192092896],[0.36000000105963814,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[0.25,1.0],[0.5,0.550000011920929],[0.75,1.0],[1.0,0.550000011920929]],"yaw":[[0.0,0.0],[0.25,-9.0],[0.5,9.0],[0.75,-4.0],[1.0,0.0]]}},"laptop_close":{"duration":1200,"loop":false,"tracks":{"arm0X":[[0.0,20.0],[0.3500000105963813,20.0],[0.9000000423855252,0.0],[1.0,0.0]],"arm0Y":[[0.0,-32.0],[0.3500000105963813,-32.0],[0.9000000423855252,0.0],[1.0,0.0]],"arm0Z":[[0.0,-14.0],[0.3500000105963813,-14.0],[0.9000000423855252,0.0],[1.0,0.0]],"arm1X":[[0.0,-20.0],[0.3500000105963813,-20.0],[0.9000000423855252,0.0],[1.0,0.0]],"arm1Y":[[0.0,-32.0],[0.3500000105963813,-32.0],[0.9000000423855252,0.0],[1.0,0.0]],"arm1Z":[[0.0,-14.0],[0.3500000105963813,-14.0],[0.9000000423855252,0.0],[1.0,0.0]],"eyeOpen":[[0.0,1.0],[0.3500000105963813,1.0],[0.9000000423855252,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[0.18000000052981907,106.0],[0.619999991522895,0.0],[1.0,0.0]],"laptopLift":[[0.0,0.0],[0.3500000105963813,0.0],[0.9000000423855252,0.0],[1.0,0.0]],"laptopSlide":[[0.0,0.0],[0.619999991522895,0.0],[0.9199999703301324,18.0],[1.0,18.0]],"laptopVisible":[[0.0,1.0],[0.9199999703301324,1.0],[0.9299999872843424,0.0],[1.0,0.0]],"pitch":[[0.0,6.0],[0.3500000105963813,6.0],[0.9000000423855252,0.0],[1.0,0.0]],"screenPulse":[[0.0,0.550000011920929],[0.3500000105963813,0.550000011920929],[0.9000000423855252,0.550000011920929],[1.0,0.550000011920929]]}},"laptop_open":{"duration":1450,"loop":false,"tracks":{"arm0X":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,20.0],[1.0,20.0]],"arm0Y":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,-32.0],[1.0,-32.0]],"arm0Z":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,-14.0],[1.0,-14.0]],"arm1X":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,-20.0],[1.0,-20.0]],"arm1Y":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,-32.0],[1.0,-32.0]],"arm1Z":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,-14.0],[1.0,-14.0]],"eyeOpen":[[0.0,1.0],[0.3000000043847095,1.0],[0.350000008769419,0.11999999731779099],[0.4099999789533944,1.0],[1.0,1.0]],"laptopHinge":[[0.0,0.0],[0.3000000043847095,0.0],[0.7300000245543732,109.0],[0.8799999719378592,104.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[0.2399999903536391,5.0],[0.5799999894766972,0.0],[1.0,0.0]],"laptopSlide":[[0.0,18.0],[0.13000000482318044,18.0],[0.5799999894766972,0.0],[1.0,0.0]],"laptopVisible":[[0.0,0.0],[0.11999999517681954,0.0],[0.13000000482318044,1.0],[1.0,1.0]],"pitch":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[0.2300000026308257,0.550000011920929],[0.7600000315699084,0.550000011920929],[1.0,0.550000011920929]]}},"laptop_pause":{"duration":1800,"loop":true,"tracks":{"arm0X":[[0.0,20.0],[1.0,20.0]],"arm0Y":[[0.0,-32.0],[1.0,-32.0]],"arm0Z":[[0.0,-14.0],[1.0,-14.0]],"arm1X":[[0.0,-20.0],[1.0,-20.0]],"arm1Y":[[0.0,-32.0],[1.0,-32.0]],"arm1Z":[[0.0,-14.0],[1.0,-14.0]],"eyeOpen":[[0.0,1.0],[0.619999991522895,1.0],[0.6599999886971933,0.18000000715255737],[0.6999999858714916,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[1.0,0.550000011920929]]}},"laptop_success":{"duration":1100,"loop":false,"tracks":{"arm0X":[[0.0,20.0],[1.0,20.0]],"arm0Y":[[0.0,-32.0],[1.0,-32.0]],"arm0Z":[[0.0,-14.0],[1.0,-14.0]],"arm1X":[[0.0,-20.0],[1.0,-20.0]],"arm1Y":[[0.0,-32.0],[1.0,-32.0]],"arm1Z":[[0.0,-14.0],[1.0,-14.0]],"eyeOpen":[[0.0,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"roll":[[0.0,0.0],[0.24000000231193774,-4.0],[0.5,4.0],[0.7400000023119377,-2.0],[1.0,0.0]],"screenPulse":[[0.0,0.550000011920929],[0.25999999768806226,1.0],[0.6500000231193773,0.800000011920929],[1.0,0.550000011920929]]}},"laptop_thinking_loop":{"duration":3200,"loop":true,"tracks":{"arm0X":[[0.0,20.0],[1.0,20.0]],"arm0Y":[[0.0,-32.0],[1.0,-32.0]],"arm0Z":[[0.0,-14.0],[1.0,-14.0]],"arm1X":[[0.0,-20.0],[1.0,-20.0]],"arm1Y":[[0.0,-32.0],[1.0,-32.0]],"arm1Z":[[0.0,-14.0],[1.0,-14.0]],"eyeOpen":[[0.0,1.0],[0.2799999912579854,1.0],[0.3199999928474426,0.11999999731779099],[0.36000001430511475,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[1.0,0.550000011920929]],"yaw":[[0.0,0.0],[0.25,-7.0],[0.5199999809265137,4.0],[0.8000000317891439,0.0],[1.0,0.0]]}},"laptop_typing_loop":{"duration":1800,"loop":true,"tracks":{"arm0X":[[0.0,20.0],[1.0,20.0]],"arm0Y":[[0.0,-32.0],[1.0,-32.0]],"arm0Z":[[0.0,-14.0],[0.1200000003532127,-11.0],[0.2400000007064254,-14.0],[0.380000008477105,-14.0],[0.5,-14.0],[0.619999991522895,-11.0],[0.73999998304579,-14.0],[0.880000008477105,-14.0],[1.0,-14.0]],"arm1X":[[0.0,-20.0],[1.0,-20.0]],"arm1Y":[[0.0,-32.0],[1.0,-32.0]],"arm1Z":[[0.0,-14.0],[0.1200000003532127,-14.0],[0.2400000007064254,-14.0],[0.380000008477105,-11.0],[0.5,-14.0],[0.619999991522895,-14.0],[0.73999998304579,-14.0],[0.880000008477105,-11.0],[1.0,-14.0]],"eyeOpen":[[0.0,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[1.0,0.550000011920929]]}}},"octo":{"laptop_agent_loop":{"duration":2400,"loop":true,"tracks":{"eyeOpen":[[0.0,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopReach0":[[0.0,1.0],[1.0,1.0]],"laptopReach1":[[0.0,1.0],[1.0,1.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopTap0":[[0.0,0.0],[0.19999999470180935,3.0],[0.30000000529819065,0.0],[0.6400000254313151,0.0],[0.75,0.0],[1.0,0.0]],"laptopTap1":[[0.0,0.0],[0.19999999470180935,0.0],[0.30000000529819065,0.0],[0.6400000254313151,3.0],[0.75,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[1.0,0.550000011920929]]}},"laptop_alert":{"duration":1200,"loop":false,"tracks":{"eyeOpen":[[0.0,1.0],[0.19999999470180935,0.30000001192092896],[0.36000000105963814,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopReach0":[[0.0,1.0],[1.0,1.0]],"laptopReach1":[[0.0,1.0],[1.0,1.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopTap0":[[0.0,0.0],[1.0,0.0]],"laptopTap1":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[0.25,1.0],[0.5,0.550000011920929],[0.75,1.0],[1.0,0.550000011920929]],"yaw":[[0.0,0.0],[0.25,-9.0],[0.5,9.0],[0.75,-4.0],[1.0,0.0]]}},"laptop_close":{"duration":1200,"loop":false,"tracks":{"eyeOpen":[[0.0,1.0],[0.3500000105963813,1.0],[0.9000000423855252,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[0.18000000052981907,106.0],[0.619999991522895,0.0],[1.0,0.0]],"laptopLift":[[0.0,0.0],[0.3500000105963813,0.0],[0.9000000423855252,0.0],[1.0,0.0]],"laptopReach0":[[0.0,1.0],[0.3500000105963813,1.0],[0.9000000423855252,0.0],[1.0,0.0]],"laptopReach1":[[0.0,1.0],[0.3500000105963813,1.0],[0.9000000423855252,0.0],[1.0,0.0]],"laptopSlide":[[0.0,0.0],[0.619999991522895,0.0],[0.9199999703301324,18.0],[1.0,18.0]],"laptopTap0":[[0.0,0.0],[0.3500000105963813,0.0],[0.9000000423855252,0.0],[1.0,0.0]],"laptopTap1":[[0.0,0.0],[0.3500000105963813,0.0],[0.9000000423855252,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[0.9199999703301324,1.0],[0.9299999872843424,0.0],[1.0,0.0]],"pitch":[[0.0,6.0],[0.3500000105963813,6.0],[0.9000000423855252,0.0],[1.0,0.0]],"screenPulse":[[0.0,0.550000011920929],[0.3500000105963813,0.550000011920929],[0.9000000423855252,0.550000011920929],[1.0,0.550000011920929]]}},"laptop_open":{"duration":1450,"loop":false,"tracks":{"eyeOpen":[[0.0,1.0],[0.3000000043847095,1.0],[0.350000008769419,0.11999999731779099],[0.4099999789533944,1.0],[1.0,1.0]],"laptopHinge":[[0.0,0.0],[0.3000000043847095,0.0],[0.7300000245543732,109.0],[0.8799999719378592,104.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[0.2399999903536391,5.0],[0.5799999894766972,0.0],[1.0,0.0]],"laptopReach0":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,1.0],[1.0,1.0]],"laptopReach1":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,1.0],[1.0,1.0]],"laptopSlide":[[0.0,18.0],[0.13000000482318044,18.0],[0.5799999894766972,0.0],[1.0,0.0]],"laptopTap0":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,0.0],[1.0,0.0]],"laptopTap1":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,0.0],[1.0,0.0]],"laptopVisible":[[0.0,0.0],[0.11999999517681954,0.0],[0.13000000482318044,1.0],[1.0,1.0]],"pitch":[[0.0,0.0],[0.2300000026308257,0.0],[0.7600000315699084,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[0.2300000026308257,0.550000011920929],[0.7600000315699084,0.550000011920929],[1.0,0.550000011920929]]}},"laptop_pause":{"duration":1800,"loop":true,"tracks":{"eyeOpen":[[0.0,1.0],[0.619999991522895,1.0],[0.6599999886971933,0.18000000715255737],[0.6999999858714916,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopReach0":[[0.0,1.0],[1.0,1.0]],"laptopReach1":[[0.0,1.0],[1.0,1.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopTap0":[[0.0,0.0],[1.0,0.0]],"laptopTap1":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[1.0,0.550000011920929]]}},"laptop_success":{"duration":1100,"loop":false,"tracks":{"eyeOpen":[[0.0,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopReach0":[[0.0,1.0],[1.0,1.0]],"laptopReach1":[[0.0,1.0],[1.0,1.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopTap0":[[0.0,0.0],[1.0,0.0]],"laptopTap1":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"roll":[[0.0,0.0],[0.24000000231193774,-4.0],[0.5,4.0],[0.7400000023119377,-2.0],[1.0,0.0]],"screenPulse":[[0.0,0.550000011920929],[0.25999999768806226,1.0],[0.6500000231193773,0.800000011920929],[1.0,0.550000011920929]]}},"laptop_thinking_loop":{"duration":3200,"loop":true,"tracks":{"eyeOpen":[[0.0,1.0],[0.2799999912579854,1.0],[0.3199999928474426,0.11999999731779099],[0.36000001430511475,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopReach0":[[0.0,1.0],[1.0,1.0]],"laptopReach1":[[0.0,1.0],[1.0,1.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopTap0":[[0.0,0.0],[1.0,0.0]],"laptopTap1":[[0.0,0.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[1.0,0.550000011920929]],"yaw":[[0.0,0.0],[0.25,-7.0],[0.5199999809265137,4.0],[0.8000000317891439,0.0],[1.0,0.0]]}},"laptop_typing_loop":{"duration":1800,"loop":true,"tracks":{"eyeOpen":[[0.0,1.0],[1.0,1.0]],"laptopHinge":[[0.0,106.0],[1.0,106.0]],"laptopLift":[[0.0,0.0],[1.0,0.0]],"laptopReach0":[[0.0,1.0],[1.0,1.0]],"laptopReach1":[[0.0,1.0],[1.0,1.0]],"laptopSlide":[[0.0,0.0],[1.0,0.0]],"laptopTap0":[[0.0,0.0],[0.1200000003532127,3.0],[0.2400000007064254,0.0],[0.380000008477105,0.0],[0.5,0.0],[0.619999991522895,3.0],[0.73999998304579,0.0],[0.880000008477105,0.0],[1.0,0.0]],"laptopTap1":[[0.0,0.0],[0.1200000003532127,0.0],[0.2400000007064254,0.0],[0.380000008477105,3.0],[0.5,0.0],[0.619999991522895,0.0],[0.73999998304579,0.0],[0.880000008477105,3.0],[1.0,0.0]],"laptopVisible":[[0.0,1.0],[1.0,1.0]],"pitch":[[0.0,6.0],[1.0,6.0]],"screenPulse":[[0.0,0.550000011920929],[1.0,0.550000011920929]]}}}}
var CLIPS = ["laptop_open", "laptop_typing_loop", "laptop_thinking_loop",
    "laptop_agent_loop", "laptop_pause", "laptop_close", "laptop_success", "laptop_alert"]

function neutral(channel) {
    return channel.indexOf("scale") === 0 || channel.endsWith("Curl") || channel === "eyeOpen" ? 1 : 0
}

function newState(character, clips=CURVES[character]) {
    if (["aqua", "octo"].indexOf(character) < 0 || !clips
        || CLIPS.some(name => !clips[name] || !Number.isFinite(clips[name].duration)
            || clips[name].duration < 1000 || clips[name].duration > 3200))
        throw new Error("Verified staged laptop curves are required")
    const channels = ["scaleX", "scaleY", "lift", "normal", "height", "journey"]
    for (const name of CLIPS) for (const channel of Object.keys(clips[name].tracks))
        if (channels.indexOf(channel) < 0) channels.push(channel)
    return {character:character, clips:clips, channels:channels, phase:"none", clip:"",
        started:0, duration:0, reverseFrom:0, epoch:-1, lastTick:-1,
        lostSince:-1, lastCue:-CUE_COOLDOWN_MS, blendFrom:null, finished:"", reason:"initial"}
}

function sample(s, clip, channel, progress) {
    const keys = s.clips[clip]?.tracks[channel]
    if (!keys) return neutral(channel)
    const t = Math.max(0, Math.min(1, progress))
    for (let i=1; i<keys.length; ++i) {
        const a=keys[i-1], b=keys[i]
        if (t<=b[0]) return a[1]+(b[1]-a[1])*(t-a[0])/(b[0]-a[0])
    }
    return keys[keys.length-1][1]
}

function progress(s, nowMs) {
    if (s.phase === "none") return 0
    const elapsed = Math.max(0, nowMs-s.started)
    if (s.phase === "loop") return (elapsed%s.duration)/s.duration
    const fraction = Math.min(1, elapsed/Math.max(1, s.duration))
    return s.reverseFrom > 0 ? s.reverseFrom*(1-fraction) : fraction
}

function pose(s, nowMs) {
    const values = {}, p = progress(s, nowMs)
    const blend = s.blendFrom ? Math.min(1, Math.max(0, (nowMs-s.started)/BLEND_MS)) : 1
    for (const channel of s.channels) {
        const target = s.phase === "none" ? neutral(channel) : sample(s, s.clip, channel, p)
        values[channel] = s.blendFrom ? s.blendFrom[channel]*(1-blend)+target*blend : target
    }
    return values
}

function view(s, nowMs) {
    const values = pose(s, nowMs)
    return {character:s.character, phase:s.phase, clip:s.clip, epoch:s.epoch,
        progress:progress(s, nowMs), active:s.phase!=="none", loop:s.phase==="loop",
        renderProp:s.phase!=="none" && values.laptopVisible>=.5,
        pose:values, reason:s.reason}
}

function ticket(s, director, nowMs) {
    const phase = s.phase === "cue" ? "loop" : s.phase
    const next = Director.beginPresentation(director, phase, nowMs)
    s.epoch = next.epoch
}

function start(s, director, phase, clip, nowMs, blend, reverseFrom=0) {
    const from = blend ? pose(s, nowMs) : null
    s.phase=phase; s.clip=clip; s.started=nowMs; s.reverseFrom=reverseFrom
    s.finished=""
    s.duration=s.clips[clip].duration*(reverseFrom>0 ? reverseFrom : 1)
    s.blendFrom=from
    ticket(s, director, nowMs)
}

function yieldNow(s, director, reason) {
    if (s.phase!=="none") {
        // Revocation also works when the newly supplied clock is invalid.
        const revoked=Director.beginPresentation(director, "outro", Math.max(0,s.lastTick))
        Director.completePresentation(director, revoked.epoch)
        s.epoch=revoked.epoch
    }
    s.phase="none"; s.clip=""; s.blendFrom=null; s.reverseFrom=0
    s.duration=0; s.lostSince=-1; s.finished=""; s.reason=reason
}

function desiredLoop(intent) {
    return intent.expression === "thinking" ? "laptop_thinking_loop"
        : intent.reason === "agent_lifecycle" ? "laptop_agent_loop" : "laptop_typing_loop"
}

function complete(s, director, epoch, nowMs) {
    if (!Director.finiteTime(nowMs) || nowMs<s.lastTick || s.phase==="none"
        || s.phase==="loop" || epoch!==s.epoch || nowMs-s.started<s.duration)
        return {accepted:false, reason:"stale_or_early_completion"}
    const result=Director.completePresentation(director, epoch)
    if (!result.accepted) return result
    s.finished=s.phase; s.lastTick=nowMs
    s.phase="none"; s.clip=""; s.blendFrom=null; s.reverseFrom=0
    return {accepted:true, phase:result.phase}
}

function update(s, director, host, nowMs) {
    if (!Director.finiteTime(nowMs) || nowMs<s.lastTick) {
        yieldNow(s, director, "invalid_clock")
        return view(s, Math.max(0,s.lastTick))
    }
    s.lastTick=nowMs
    const intent=Director.transition(director, host, nowMs)
    // These host values are supplied by the permitted actor owner, not a model.
    if (!host || host.coworkEnabled!==true || host.optedIn!==true
        || host.motionEnabled!==true || host.grounded!==true || host.character!==s.character
        || host.dnd===true || host.quiet===true
        || intent.mode==="hidden" || intent.mode==="interactive" || intent.mode==="travel") {
        yieldNow(s, director, "higher_priority_or_disabled")
        return view(s, nowMs)
    }
    const wanted=intent.mode==="cowork"
    if (wanted) s.lostSince=-1
    else if (s.lostSince<0) s.lostSince=nowMs

    if (s.phase!=="none" && director.pending?.epoch!==s.epoch)
        ticket(s, director, nowMs) // fresh ownership, same timing/pose

    if (!wanted && s.phase!=="none" && s.phase!=="outro"
        && nowMs-s.lostSince>=EXIT_GRACE_MS) {
        const reverse=s.phase==="intro" ? progress(s,nowMs) : 0
        start(s,director,"outro",reverse>0 ? "laptop_open" : "laptop_close",nowMs,reverse===0,reverse)
        s.reason="context_ended"
    }
    if (s.phase!=="none" && s.phase!=="loop" && nowMs-s.started>=s.duration) {
        const done=complete(s,director,s.epoch,nowMs)
        if (!done.accepted) {
            yieldNow(s,director,"lost_presentation_ownership")
            return view(s,nowMs)
        }
    }
    if (s.finished!=="") {
        const completed=s.finished
        s.finished=""
        if (completed==="outro") {
            s.reason="closed"
            return view(s,nowMs) // final hidden prop precedes any new intro
        }
        if (wanted) start(s,director,"loop",desiredLoop(intent),nowMs,false)
        else start(s,director,"loop","laptop_pause",nowMs,false)
    }
    if (wanted && s.phase==="none") {
        start(s,director,"intro","laptop_open",nowMs,false)
        s.reason="context_started"
    } else if (s.phase==="loop") {
        const clip=wanted ? desiredLoop(intent) : "laptop_pause"
        if (s.clip!==clip) start(s,director,"loop",clip,nowMs,true)
        s.reason=wanted ? "context_active" : "context_settling"
    }
    return view(s,nowMs)
}

function cue(s, director, kind, nowMs) {
    // Optional confirmed host success/input cues; never a model action protocol.
    if (!Director.finiteTime(nowMs) || nowMs<s.lastTick || s.phase!=="loop"
        || ["success","alert"].indexOf(kind)<0 || nowMs-s.lastCue<CUE_COOLDOWN_MS)
        return {accepted:false, reason:"cue_unavailable"}
    s.lastTick=nowMs; s.lastCue=nowMs
    start(s,director,"cue","laptop_"+kind,nowMs,true)
    s.reason="confirmed_host_cue"
    return {accepted:true, epoch:s.epoch}
}
