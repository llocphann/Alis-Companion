// AC-R04: retire runtime falls while retaining all authored/shared assets.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const path=require('node:path'),root=path.resolve(__dirname,'..');
function load(name,scope={}) {
    const api=vm.createContext(scope);
    vm.runInContext(fs.readFileSync(path.join(root,'modules/abyss/companion',name),'utf8')
        .replace(/^\.(?:pragma|import).*$/gm,''),api);
    return api;
}
const Aqua=load('AquaMotionData.js'),Octo=load('OctoMotionData.js');
const before=[JSON.stringify(Aqua.clips),JSON.stringify(Octo.clips)];
const motion=load('CompanionMotion.js',{Aqua,Octo});
const retired=['fall','faceplant','buttplant','fallVanish','ice'];
let samples=0;
for(const [character,authored,specific] of [['aqua',Aqua,['push','pulled']],['octo',Octo,['pull','sink']]]) {
    const runtime=motion.forCharacter(character);
    assert.deepEqual(Object.keys(runtime.clips).sort(),Object.keys(authored.clips).filter(k=>!retired.includes(k)).sort());
    for(const clip of retired) {
        assert.ok(authored.clips[clip],`archival ${character}/${clip} must remain intact`);
        assert.equal(runtime.clips[clip],undefined,`${character}/${clip} is still reachable`);
        for(const channel of ['pitch','roll','scaleX','scaleY','eyeOpen','tentacle0Curl'])
            for(const phase of [0,.17,.5,.83,1])
                assert.equal(runtime.sample(clip,channel,phase),authored.sample('',channel,phase));
    }
    for(const [clip,value] of Object.entries(authored.clips)) {
        if(retired.includes(clip))continue;
        assert.equal(runtime.clips[clip],value,`${character}/${clip} was rewritten`);
        for(const channel of Object.keys(value.tracks))for(const phase of [0,.17,.5,.83,1]) {
            assert.equal(runtime.sample(clip,channel,phase),authored.sample(clip,channel,phase));
            samples++;
        }
    }
    for(const clip of ['float','walk','run','fly','jump','drag','land','balance','launch','dive',...specific])
        assert.ok(runtime.clips[clip],`${character}/${clip} was lost`);
    assert.equal(motion.forCharacter(character),runtime);
}
assert.equal(JSON.stringify(Aqua.clips),before[0]);
assert.equal(JSON.stringify(Octo.clips),before[1]);
const settings=fs.readFileSync(path.join(root,'modules/settings/CompanionConfig.qml'),'utf8');
assert.doesNotMatch(settings,/value:\s*"fall"/);
console.log(`COMPANION_RETIRED_FALL_PASS cast=2 preserved_samples=${samples} no_asset_mutation`);
