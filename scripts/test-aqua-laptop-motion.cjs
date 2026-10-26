// Offline checks for the original, staged Blender laptop bundle.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "..");
const bundle = path.join(root, "assets/cowork");
const receipt = JSON.parse(fs.readFileSync(path.join(bundle, "authoring.json")));
const digest = file => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const phases = ["laptop_open", "laptop_typing_loop", "laptop_thinking_loop", "laptop_agent_loop",
    "laptop_pause", "laptop_close", "laptop_success", "laptop_alert"];
const loops = phases.slice(1, 5);
const neutral = channel => /^(scale)/.test(channel) || channel.endsWith("Curl") || channel === "eyeOpen" ? 1 : 0;
function sample(clip, channel, phase) {
    const keys = clip.tracks[channel];
    if (!keys) return neutral(channel);
    const t = Math.max(0, Math.min(1, phase));
    for (let i = 1; i < keys.length; ++i) {
        const [a, b] = [keys[i-1], keys[i]];
        if (t <= b[0]) return a[1] + (b[1]-a[1]) * (t-a[0]) / (b[0]-a[0]);
    }
    return keys.at(-1)[1];
}
assert.equal(receipt.stagedOnly, true);
let keys = 0;
for (const character of ["Aqua", "Octo"]) {
    const info = receipt.characters[character];
    assert.equal(digest(path.join(root, info.source)), info.sourceSha256,
        "original shipped Blender anatomy and actions must be unchanged");
    assert.equal(digest(path.join(bundle, character + "Laptop.blend")), info.blendSha256);
    const file = path.join(bundle, character + "LaptopMotion.json");
    assert.equal(digest(file), info.curvesSha256);
    const clips = JSON.parse(fs.readFileSync(file));
    assert.deepEqual(Object.keys(clips).sort(), [...phases].sort());
    assert.equal(info.oldClips, 30);
    assert.equal(info.newClips, 8);
    assert.equal(info.propMeshes, 29);
    const channels = new Set(Object.values(clips).flatMap(c => Object.keys(c.tracks)));
    for (const [name, clip] of Object.entries(clips)) {
        assert.ok(clip.duration >= 1000 && clip.duration <= 3200);
        assert.equal(clip.loop, loops.includes(name));
        for (const [channel, track] of Object.entries(clip.tracks)) {
            assert.ok(track.length >= 2);
            assert.equal(track[0][0], 0);
            assert.ok(Math.abs(track.at(-1)[0] - 1) < .000001);
            for (let i = 0; i < track.length; ++i) {
                assert.ok(track[i].every(Number.isFinite));
                if (i) assert.ok(track[i][0] > track[i-1][0]);
            }
            keys += track.length;
        }
        // The prop sequence cannot move, portal, flatten or add legs to an actor.
        for (let i = 0; i <= 100; ++i) {
            for (const channel of ["height", "normal", "journey", "lift"])
                assert.equal(sample(clip, channel, i/100), 0);
            for (const channel of ["scaleX", "scaleY"])
                assert.equal(sample(clip, channel, i/100), 1);
            assert.ok(sample(clip, "laptopHinge", i/100) >= 0);
            assert.ok(sample(clip, "laptopHinge", i/100) <= 109.001);
        }
        if (clip.loop) for (const channel of channels) {
            assert.ok(Math.abs(sample(clip, channel, 0) - sample(clip, channel, 1)) < .00001,
                `${character}/${name}/${channel}: loop seam`);
            assert.ok(Math.abs(sample(clip, channel, 0) - sample(clips.laptop_open, channel, 1)) < .00001,
                `${character}/${name}/${channel}: intro-to-loop discontinuity`);
        }
    }
    for (const channel of channels)
        assert.ok(Math.abs(sample(clips.laptop_open, channel, 1) - sample(clips.laptop_close, channel, 0)) < .00001);
    assert.equal(sample(clips.laptop_open, "laptopVisible", 0), 0);
    assert.equal(sample(clips.laptop_close, "laptopVisible", 1), 0);
    assert.equal(sample(clips.laptop_close, "laptopHinge", 1), 0);
    const strikes = character === "Aqua" ? ["arm0Z", "arm1Z"] : ["laptopTap0", "laptopTap1"];
    assert.ok(sample(clips.laptop_typing_loop, strikes[0], .12) > sample(clips.laptop_typing_loop, strikes[0], 0));
    assert.ok(sample(clips.laptop_typing_loop, strikes[1], .38) > sample(clips.laptop_typing_loop, strikes[1], 0));
    assert.equal(sample(clips.laptop_typing_loop, strikes[0], .38), sample(clips.laptop_typing_loop, strikes[0], 0));
    if (character === "Octo") {
        assert.equal(sample(clips.laptop_typing_loop, "laptopReach0", .5), 1);
        assert.equal(sample(clips.laptop_typing_loop, "laptopReach1", .5), 1);
        assert.equal(clips.laptop_typing_loop.tracks.laptopReach2, undefined);
    } else {
        assert.equal(sample(clips.laptop_typing_loop, "arm0Y", .5), -32);
        assert.equal(sample(clips.laptop_typing_loop, "arm1Y", .5), -32);
    }
}
console.log(`COMPANION_LAPTOP_CURVES_PASS pairedClips=16 exactKeys=${keys} loopSeams noPortal originalRigsPreserved`);
