#!/usr/bin/env node
// AC-R04: runtime cast policy retires falls without rewriting authored assets.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const dir='modules/abyss/companion/';
function load(name,scope={}) {
    const api=vm.createContext(scope);
    vm.runInContext(fs.readFileSync(dir+name,'utf8').replace(/^\.(?:pragma|import).*$/gm,''),api);
    return api;
}
const Aqua=load('AquaMotionData.js'),Octo=load('OctoMotionData.js');
const before=[JSON.stringify(Aqua.clips),JSON.stringify(Octo.clips)];
const motion=load('CompanionMotion.js',{Aqua,Octo});
const retired=['fall','faceplant','buttplant','fallVanish','ice'];
let samples=0;
for(const [character,original] of [['aqua',Aqua],['octo',Octo]]) {
    const runtime=motion.forCharacter(character);
    for(const clip of retired) {
        assert.ok(original.clips[clip],`archival ${character}/${clip} must remain intact`);
        assert.equal(runtime.clips[clip],undefined,`${character}/${clip} is still reachable`);
        for(const channel of ['pitch','roll','scaleX','scaleY','eyeOpen','tentacle0Curl'])
            assert.equal(runtime.sample(clip,channel,.6),original.sample('',channel,.6),
                `direct retired sampling reintroduced ${character}/${clip}/${channel}`);
    }
    for(const [clip,value] of Object.entries(original.clips)) {
        if(retired.includes(clip))continue;
        assert.equal(runtime.clips[clip],value,`${character}/${clip} was rewritten`);
        for(const channel of Object.keys(value.tracks))for(const phase of [0,.17,.5,.83,1]) {
            assert.equal(runtime.sample(clip,channel,phase),original.sample(clip,channel,phase));
            samples++;
        }
    }
    for(const clip of ['float','walk','run','fly','jump','drag','land','balance','launch','dive','sink'])
        assert.ok(runtime.clips[clip],`${character}/${clip} was lost`);
    assert.equal(motion.forCharacter(character),runtime,'runtime library must be reused');
}
assert.equal(JSON.stringify(Aqua.clips),before[0]);
assert.equal(JSON.stringify(Octo.clips),before[1]);
const settings=fs.readFileSync('modules/settings/CompanionConfig.qml','utf8');
assert.doesNotMatch(settings,/value:\s*"fall"/,'Settings still offers the retired action');
console.log(`COMPANION_RETIRED_FALL_PASS cast=2 preserved_samples=${samples} no_asset_mutation`);
