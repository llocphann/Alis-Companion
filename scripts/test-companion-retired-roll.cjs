// AC-R05: whole-body turns stay retired, including legacy travel requests.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
function load(name,scope={}) {
    const api=vm.createContext(scope);
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../modules/abyss/companion',name),'utf8')
        .replace(/^\.(?:pragma|import).*$/gm,''),api);
    return api;
}
const Aqua=load('AquaMotionData.js'),Octo=load('OctoMotionData.js');
const motion=load('CompanionMotion.js',{Aqua,Octo}),travel=load('WullTravel.js');
assert.ok(motion.retired('roll'),'legacy roll gestures must be retired');
assert.equal(motion.travelMode('roll'),'run','legacy travel must retain deliberate translation');
for(const mode of ['walk','run','jump','fly','portal'])assert.equal(motion.travelMode(mode),mode);
let samples=0;
for(const [cast,authored] of [['aqua',Aqua],['octo',Octo]]) {
    const before=JSON.stringify(authored.clips),runtime=motion.forCharacter(cast);
    assert.equal(runtime.clips.roll,undefined);
    for(const phase of [-1,0,.15,.5,.85,1,2,NaN])
        assert.equal(runtime.sample('roll','roll',phase),0);
    for(const [clip,data] of Object.entries(runtime.clips))for(let i=0;i<=1000;i++) {
        const phase=i/1000;
        for(const channel of ['roll','pitch','yaw']) {
            const angle=runtime.sample(clip,channel,phase);
            assert.ok(Number.isFinite(angle) && Math.abs(angle)<=30,`${cast}/${clip}/${channel}@${phase} tumbles`);
            assert.equal(angle,authored.sample(clip,channel,phase),'retained authored pose changed');
            samples++;
        }
    }
    assert.equal(JSON.stringify(authored.clips),before,'authored archive mutated');
}
for(const distance of [1,35,145,500,5000,NaN,Infinity,-1])
    for(const phase of [-1,0,.25,.5,.75,1,2,NaN])
        for(const sign of [-1,1])for(const scale of [.5,1,2,NaN,0])
            assert.equal(travel.rollingAngle(distance,phase,sign,scale),0,'legacy helper revived a turn');
// The old distance-driven generator is a separate negative control: checking
// only upright endpoints misses a full revolution through intermediate frames.
const oldAngle=(distance,phase)=>360*Math.max(1,Math.ceil(distance/(2*Math.PI*32)))*phase;
assert.equal(oldAngle(145,1)%360,0);
assert.ok(Math.abs(oldAngle(145,.5))>=180);
console.log(`COMPANION_RETIRED_ROLL_PASS cast=2 pose_samples=${samples} legacy_translation no_generated_turns retained_assets negative_control`);
