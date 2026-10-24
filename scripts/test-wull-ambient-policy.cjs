const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const api=vm.createContext({});
vm.runInContext(fs.readFileSync('modules/abyss/companion/WullAmbientPolicy.js','utf8')
    .replace(/^\.pragma library\s*/,''),api);
const plain=x=>JSON.parse(JSON.stringify(x));
const input={focus:'off',power:'normal',media:'paused'};
let state=api.newState(),now=1000000;
const observe=(patch={},allowed=true,stamp=now)=>plain(api.observe(state,{...input,...patch},allowed,stamp));
assert.equal(observe().reason,'initial_snapshot');
assert.deepEqual(observe({focus:'focus'}),{eligible:true,reason:'focus_started',expression:'working'});
now+=1000;
assert.equal(observe({focus:'focus',power:'low'}).reason,'quiet_focus');
assert.equal(observe({focus:'focus',power:'low',media:'playing'}).reason,'quiet_focus');
now+=api.COOLDOWN_MS;
assert.deepEqual(observe({focus:'break',power:'low',media:'playing'}),
    {eligible:true,reason:'focus_break',expression:'happy'});
now+=1;
assert.equal(observe({power:'charging'}).reason,'cooldown');
now+=api.COOLDOWN_MS;
assert.equal(observe({power:'low'},false).reason,'suppressed');
assert.equal(observe({power:'low'}).eligible,false,'unlock replays suppressed battery event');
assert.equal(observe({power:'charging'}).expression,'happy');
now+=api.COOLDOWN_MS;
assert.equal(observe({power:'normal',media:'playing'}).expression,'happy');
api.revoke(state);
now+=api.COOLDOWN_MS;
assert.equal(observe({power:'low'}).reason,'initial_snapshot','consent change replays old state');
for(const bad of [null,[],{...input,title:'private sentinel'},{...input,power:20},
        {...input,focus:'running'},{...input,media:'track title'}]) {
    const result=plain(api.observe(state,bad,true,now));
    assert.equal(result.reason,'invalid_observation');
    assert.ok(!JSON.stringify(state).includes('private sentinel'));
}
for(const stamp of [NaN,Infinity,-1,now-1]) {
    assert.equal(plain(api.observe(state,input,true,stamp)).reason,'invalid_clock');
    assert.equal(state.baseline,null);
}
state=api.newState();observe();
for(let i=0;i<100;i++) {
    now+=100;
    const result=observe({media:i%2 ? 'paused' : 'playing'});
    if(i===0)assert.equal(result.eligible,true);
    else assert.equal(result.eligible,false,'rapid playback toggles bypassed shared cooldown');
}
console.log('COMPANION_AMBIENT_POLICY_PASS typedCoarseStates initialQuiet focusPriority cooldown revoke suppressedNoReplay invalidClock noContent noSecondClock');
