// One caller-owned clock, real staged curves and the existing pure Director.
const assert=require("node:assert/strict"), fs=require("node:fs"), vm=require("node:vm"), path=require("node:path");
const root=path.resolve(__dirname,"..");
const director=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root,"modules/abyss/companion/AquaBehaviorDirector.js"),"utf8")
    .replace(/^\.pragma library\s*/u,""),director);
const api=vm.createContext({Director:director});
vm.runInContext(fs.readFileSync(path.join(root,"assets/cowork/CoworkPerformance.js"),"utf8")
    .replace(/^\.pragma library\s*/u,"").replace(/^\.import .*$/mu,""),api);
const plain=v=>JSON.parse(JSON.stringify(v));
const token="0123456789abcdef";
function create(character="aqua") {
    const curves=JSON.parse(fs.readFileSync(path.join(root,`assets/cowork/${character==="aqua" ? "Aqua" : "Octo"}LaptopMotion.json`)));
    return {s:api.newState(character,curves),d:director.newState(),host:{enabled:true,visible:true,
        optedIn:true,coworkEnabled:true,motionEnabled:true,grounded:true,character,
        locked:false,gameMode:false,fullscreen:false,chatOpen:false,dragging:false,modalOpen:false,
        dnd:false,quiet:false,manualTravel:false,portalTravel:false,idle:false},clock:100000};
}
function tick(q,delta=0,patch={}) {q.clock+=delta;return plain(api.update(q.s,q.d,{...q.host,...patch},q.clock));}
function working(q) {director.acceptAgent(q.d,{word:"working",token},q.clock);}
for(const character of ["aqua","octo"]) {
    const q=create(character); working(q);
    let p=tick(q);
    assert.equal(p.phase,"intro"); assert.equal(p.renderProp,false);
    const introEpoch=p.epoch;
    assert.equal(api.complete(q.s,q.d,introEpoch,q.clock+500).accepted,false,"early callback");
    p=tick(q,1450); assert.equal(p.phase,"loop"); assert.equal(p.clip,"laptop_agent_loop");
    assert.equal(p.renderProp,true);
    const started=q.s.started,epoch=p.epoch;
    for(let i=0;i<50;++i) assert.equal(tick(q,10).epoch,epoch);
    assert.equal(q.s.started,started,"repeated observations cannot restart a loop");
    assert.equal(api.complete(q.s,q.d,introEpoch,q.clock).accepted,false);
    director.acceptAgent(q.d,{word:"needs_input",token},q.clock);
    p=tick(q,45001); assert.equal(p.clip,"laptop_thinking_loop");
    const poseBefore=plain(api.pose(q.s,q.clock));
    const cue=api.cue(q.s,q.d,"alert",q.clock);
    assert.equal(cue.accepted,true); p=tick(q);
    assert.equal(p.clip,"laptop_alert"); assert.deepEqual(p.pose,poseBefore,"continuous cue start");
    p=tick(q,1200); assert.equal(p.phase,"loop"); assert.equal(p.clip,"laptop_thinking_loop");
    assert.equal(api.cue(q.s,q.d,"success",q.clock).accepted,false,"cue cooldown");
    p=tick(q,6000); assert.equal(api.cue(q.s,q.d,"success",q.clock).accepted,true);
    p=tick(q,1100); assert.equal(p.phase,"loop");
    director.acceptAgent(q.d,{word:"ended",token},q.clock);
    p=tick(q); assert.equal(p.clip,"laptop_pause");
    const lost=q.clock;
    p=tick(q,999); assert.equal(p.phase,"loop");
    p=tick(q,1); assert.equal(p.phase,"outro"); assert.equal(q.s.started,lost+1000);
    const closeEpoch=p.epoch;
    p=tick(q,1200); assert.equal(p.phase,"none"); assert.equal(p.renderProp,false);
    assert.equal(api.complete(q.s,q.d,closeEpoch,q.clock).accepted,false);
    working(q); p=tick(q,1); assert.equal(p.phase,"intro");
    // A real animation finished signal follows the same path as a clock tick.
    const external=create(character); working(external); const opening=tick(external);
    external.clock+=1450;
    assert.equal(api.complete(external.s,external.d,opening.epoch,external.clock).accepted,true);
    assert.equal(tick(external).phase,"loop","external completion cannot restart intro");
    assert.equal(api.complete(external.s,external.d,opening.epoch,external.clock).accepted,false);
}
// Each priority condition releases the prop immediately at every finite phase.
for(const patch of [{enabled:false},{visible:false},{locked:true},{fullscreen:true},{gameMode:true},
    {dragging:true},{chatOpen:true},{modalOpen:true},{manualTravel:true},{portalTravel:true},
    {optedIn:false},{coworkEnabled:false},{motionEnabled:false},{grounded:false},{dnd:true},{quiet:true},
    {character:"octo"}]) for(const elapsed of [300,1450,1700]) {
    const q=create(); working(q); tick(q); const p=tick(q,elapsed); const old=p.epoch;
    const canceled=tick(q,1,patch);
    assert.equal(canceled.phase,"none"); assert.equal(canceled.renderProp,false);
    assert.equal(canceled.active,false); assert.equal(canceled.clip,"");
    assert.equal(api.complete(q.s,q.d,old,q.clock+3000).accepted,false);
}
// A short category switch does not reopen a prop after Director's 700 ms settle.
const focus=create(); director.setFocus(focus.d,"terminal",focus.clock);
assert.equal(tick(focus).active,false);
assert.equal(tick(focus,700).phase,"intro");
tick(focus,1450);
director.setFocus(focus.d,"editor",focus.clock);
assert.equal(tick(focus).clip,"laptop_pause");
assert.equal(tick(focus,701).phase,"loop");
assert.equal(focus.s.clip,"laptop_typing_loop");
// Context lost during opening reverses the actual current pose.
const early=create(); working(early); tick(early); tick(early,100);
director.acceptAgent(early.d,{word:"ended",token},early.clock); tick(early);
const before=plain(api.pose(early.s,early.clock+1000));
const reversed=tick(early,1000);
assert.equal(reversed.phase,"outro"); assert.equal(reversed.clip,"laptop_open");
assert.deepEqual(reversed.pose,before);
const reversedEpoch=reversed.epoch;
assert.equal(tick(early,1100).renderProp,false);
assert.equal(api.complete(early.s,early.d,reversedEpoch,early.clock).accepted,false);
// New interest during closing waits for a fully removed prop, then reopens.
const renewed=create(); working(renewed); tick(renewed); tick(renewed,1450);
director.acceptAgent(renewed.d,{word:"ended",token},renewed.clock); tick(renewed); tick(renewed,1000);
working(renewed); assert.equal(tick(renewed,500).phase,"outro");
assert.equal(tick(renewed,700).renderProp,false);
assert.equal(tick(renewed,1).phase,"intro");
// Invalid/backwards clocks revoke callbacks even if the new clock is unusable.
for(const invalid of [NaN,Infinity,-1,99999]) {
    const q=create();working(q);const p=tick(q);
    const result=api.update(q.s,q.d,q.host,invalid);
    assert.equal(result.active,false); assert.equal(result.renderProp,false);
    assert.equal(api.complete(q.s,q.d,p.epoch,q.clock+2000).accepted,false);
}
// All returned samples remain authored, bounded 3D transforms, without travel.
const samples=create("octo");working(samples);
for(let i=0;i<2000;++i) {
    const p=tick(samples,17);
    for(const v of Object.values(p.pose)) assert.ok(Number.isFinite(v));
    for(const k of ["scaleX","scaleY"]) assert.equal(p.pose[k],1);
    for(const k of ["normal","height","journey","lift"]) assert.equal(p.pose[k],0);
}
console.log("COMPANION_COWORK_PERFORMANCE_PASS pairedSequences priorityYield phaseReversal staleEpochs quietClock focusSettle cueBounds noTravel");
