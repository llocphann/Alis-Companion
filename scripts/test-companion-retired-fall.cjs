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
let samples=0,transitionSamples=0;
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
    // Entry/exit names alone are not a pose oracle. Sweep the retained
    // composite clips too: intentional launch/settle and sinking must stay
    // upright, then release the pose at the shared clock's endpoint.
    const entries=['emerge','launch','stuckLaunch','stuckJump','riseJump'];
    const exits=['dive','diveJump',character==='octo' ? 'sink' : 'pulled'];
    for(const clip of [...entries,...exits]) {
        for(let i=0;i<=1000;i++) {
            const t=i/1000,pitch=runtime.sample(clip,'pitch',t),roll=runtime.sample(clip,'roll',t);
            assert.ok(Math.abs(pitch)<=30 && Math.abs(roll)<=20,`${character}/${clip}@${t} tips over`);
            assert.ok(runtime.sample(clip,'scaleX',t)>=.6 && runtime.sample(clip,'scaleY',t)>=.6,
                `${character}/${clip}@${t} collapses`);
            transitionSamples++;
        }
        assert.equal(runtime.sample(clip,'pitch',1),0);
        assert.equal(runtime.sample(clip,'roll',1),0);
        assert.equal(runtime.sample(clip,'scaleX',1),1);
        assert.equal(runtime.sample(clip,'scaleY',1),1);
        assert.equal(runtime.sample(clip,'normal',0),entries.includes(clip) ? 1 : 0);
        assert.ok(entries.includes(clip) ? runtime.sample(clip,'normal',1)===0
            : runtime.sample(clip,'normal',1)>=1,`${character}/${clip} loses its launch/sink endpoint`);
    }
    // Independent negative controls: the oracle must reject the archived
    // seated/prone poses even when they are presented as an ordinary entry.
    assert.ok(Math.abs(authored.sample('faceplant','pitch',.6))>60);
    assert.ok(Math.abs(authored.sample('buttplant','pitch',.6))>60);
    assert.equal(motion.forCharacter(character),runtime);
}
assert.equal(JSON.stringify(Aqua.clips),before[0]);
assert.equal(JSON.stringify(Octo.clips),before[1]);
const settings=fs.readFileSync(path.join(root,'modules/settings/CompanionConfig.qml'),'utf8');
assert.doesNotMatch(settings,/value:\s*"fall"/);
console.log(`COMPANION_RETIRED_FALL_PASS cast=2 preserved_samples=${samples} transition_samples=${transitionSamples} compositePoseBounds stableEndpoints no_asset_mutation`);
