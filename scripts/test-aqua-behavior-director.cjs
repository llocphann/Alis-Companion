// Privacy-minimal, no-device regression for Alis-Companion's unified behavior owner.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const path=require("node:path");
const root=path.resolve(__dirname,"..");
const file=path.join(root,"modules/abyss/companion/AquaBehaviorDirector.js");
const code=fs.readFileSync(file,"utf8");
const api=vm.createContext({});
vm.runInContext(code.replace(/^\.pragma library\s*/u,""),api);
const plain=value=>JSON.parse(JSON.stringify(value));
const tokenA="0123456789abcdef",tokenB="fedcba9876543210";
const host={enabled:true,visible:true,locked:false,fullscreen:false,gameMode:false,
    dragging:false,chatOpen:false,modalOpen:false,portalTravel:false,manualTravel:false,
    optedIn:true,dnd:false,quiet:false,idle:false};
let clock=1000000;
const create=()=>api.newState();
const ctx=(s,patch={})=>plain(api.transition(s,{...host,...patch},clock));
let state=create();
assert.equal(ctx(state).mode,"idle");
assert.equal(ctx(state).reason,"no_live_context");

// Input vocabulary cannot contain prompt, file, tool command or raw session id.
for (const payload of [
    {word:"working",token:"path/home/user"},
    {word:"working",token:tokenA,prompt:"private user prompt"},
    {word:"tool_call",token:tokenA},
    {word:"working",token:tokenA,text:"hello"},
    {word:"working",token:tokenA,source:"claude"},
    {word:"working",token:"A".repeat(16)},
]) {
    const response=api.acceptAgent(state,payload,clock);
    assert.equal(response.accepted,false);
    assert.equal(response.reason,"invalid_event");
}
assert.equal(api.acceptAgent(state,{word:"activity",token:tokenA},clock).accepted,false);
assert.equal(api.acceptAgent(state,{word:"working",token:tokenA},clock).accepted,true);
let started=ctx(state);
assert.equal(started.mode,"cowork");
assert.equal(started.expression,"working");
assert.equal(started.reason,"agent_lifecycle");
assert.equal(JSON.stringify(started).includes(tokenA),false);
const epoch=started.epoch;
assert.equal(ctx(state).epoch,epoch,"repeated observations do not restart animation");
assert.equal(ctx(state,{chatOpen:true}).mode,"interactive");
assert.equal(ctx(state,{dragging:true}).reason,"human_interaction_priority");
assert.equal(ctx(state,{manualTravel:true}).mode,"travel");
assert.equal(ctx(state,{portalTravel:true}).mode,"travel");
assert.equal(ctx(state,{locked:true}).mode,"hidden");
assert.equal(ctx(state,{fullscreen:true}).mode,"hidden");
assert.equal(ctx(state,{gameMode:true}).mode,"hidden");
assert.equal(ctx(state,{dnd:true}).reason,"ambient_consent_or_quiet");
assert.equal(ctx(state,{optedIn:false}).mode,"idle");
assert.equal(ctx(state,{visible:false}).mode,"hidden");
assert.equal(ctx(state).mode,"cowork");

// Short permission prompts should stay quiet; one hint for a real timeout.
clock+=500;
assert.equal(api.acceptAgent(state,{word:"needs_input",token:tokenA},clock).accepted,true);
clock+=30000;
assert.equal(api.pollNeedsInput(state,host,clock).eligible,false);
assert.equal(ctx(state).mode,"cowork");
clock+=16000;
assert.equal(ctx(state).expression,"thinking");
assert.equal(api.pollNeedsInput(state,{...host,chatOpen:true},clock).eligible,false);
assert.equal(api.pollNeedsInput(state,{...host,quiet:true},clock).eligible,false);
assert.equal(api.pollNeedsInput(state,host,clock).eligible,true);
assert.equal(api.pollNeedsInput(state,host,clock).eligible,false);
assert.equal(api.acceptAgent(state,{word:"activity",token:tokenA},clock).accepted,true);
assert.equal(ctx(state).expression,"working");
assert.equal(api.pollNeedsInput(state,host,clock).eligible,false);

// Long completion can propose a low-priority celebration; no real animation occurs.
clock+=10000;
let result=api.acceptAgent(state,{word:"finished",token:tokenA},clock);
assert.equal(result.accepted,true);
assert.equal(result.finishedLong,false,"waiting run does not count as uninterrupted");
assert.equal(ctx(state).mode,"idle");
assert.equal(api.acceptAgent(state,{word:"activity",token:tokenA},clock).accepted,false);

// Two independent sessions keep one actor in cowork; no added windows.
clock+=500;
api.acceptAgent(state,{word:"working",token:tokenA},clock);
clock+=100;
api.acceptAgent(state,{word:"working",token:tokenB},clock);
clock+=60500;
result=api.acceptAgent(state,{word:"finished",token:tokenA},clock);
assert.equal(result.finishedLong,true);
assert.equal(ctx(state).mode,"cowork");
result=api.acceptAgent(state,{word:"ended",token:tokenB},clock);
assert.equal(result.finishedLong,false);
assert.equal(ctx(state).mode,"idle");

// Context category is coarse; Alt-Tab transient focus remains quiet.
state=create(); clock=2000000;
assert.equal(api.setFocus(state,"terminal",clock).accepted,true);
assert.equal(ctx(state).mode,"idle");
clock+=699;
assert.equal(ctx(state).mode,"idle");
clock+=1;
assert.equal(ctx(state).mode,"cowork");
assert.equal(ctx(state).reason,"focused_app_category");
assert.equal(ctx(state,{idle:true}).mode,"idle");
assert.equal(api.setFocus(state,"secret/file.txt",clock).accepted,false);
assert.equal(api.setFocus(state,"editor",clock).accepted,true);
assert.equal(ctx(state).mode,"idle");
clock+=700;
assert.equal(ctx(state).mode,"cowork");
assert.equal(api.setFocus(state,"none",clock).accepted,true);
assert.equal(ctx(state).mode,"idle");

// A stale animation callback is never accepted after replacement/interruption.
const intro=api.beginPresentation(state,"intro",clock);
assert.equal(intro.ok,true);
const loop=api.beginPresentation(state,"loop",clock);
assert.equal(api.completePresentation(state,intro.epoch).accepted,false);
assert.equal(api.completePresentation(state,loop.epoch).accepted,true);
assert.equal(api.completePresentation(state,loop.epoch).accepted,false);
const stale=api.beginPresentation(state,"intro",clock);
api.setFocus(state,"terminal",clock);
clock+=700;
assert.equal(ctx(state).mode,"cowork");
assert.equal(api.completePresentation(state,stale.epoch).accepted,false);
assert.equal(api.beginPresentation(state,"unknown",clock).ok,false);

// Cap memory to 16 anonymous sessions; stale sessions expire without linger.
state=create();clock=3000000;
for(let i=0;i<20;i++){
   const token=i.toString(16).padStart(16,"0");
   assert.equal(api.acceptAgent(state,{word:"working",token},clock+i).accepted,true);
}
assert.equal(Object.keys(state.sessions).length,16);
clock+=api.STALE_MS+20;
assert.equal(ctx(state).mode,"idle");
assert.equal(Object.keys(state.sessions).length,0);

// AI is a bounded expression proposal, including during an explicit chat.
// Neither model JSON nor a late response can acquire movement or modal state.
const expressionHost={...host,motionEnabled:true,chatOpen:true};
const beforeProposal=JSON.stringify(state);
for(const expression of ["idle","happy","excited","thinking","working","surprised","sleepy","sad","alert"]){
    const result=plain(api.proposeAiExpression(state,expressionHost,{expression},clock));
    assert.deepEqual(result,{accepted:true,expression,intensity:.5,ttlMs:3000,epoch:state.epoch});
}
for(const payload of [null,[],{},"happy",{expression:"fly"},{expression:"angry"},
    {expression:"happy",x:1},{expression:"happy",popup:"open"},
    {expression:"happy",ttlMs:900000},{expression:"happy",text:"private reply"}])
    assert.equal(api.proposeAiExpression(state,expressionHost,payload,clock).accepted,false);
for(const patch of [{enabled:false},{visible:false},{locked:true},{fullscreen:true},{gameMode:true},
    {dragging:true},{manualTravel:true},{portalTravel:true},{modalOpen:true},{dnd:true},{quiet:true},{motionEnabled:false}])
    assert.equal(api.proposeAiExpression(state,{...expressionHost,...patch},{expression:"happy"},clock).accepted,false);
for(const time of [NaN,Infinity,-1])
    assert.equal(api.proposeAiExpression(state,expressionHost,{expression:"happy"},time).accepted,false);
assert.equal(JSON.stringify(state),beforeProposal,"AI proposal mutated presentation/agent ownership");

const ambientHost={...expressionHost,ambientOptIn:true,idle:true,chatOpen:false};
assert.deepEqual(plain(api.proposeAmbientExpression(state,ambientHost,{expression:'working'},clock)),
    {accepted:true,expression:'working',intensity:.25,ttlMs:2000,epoch:state.epoch});
for(const patch of [{ambientOptIn:false},{idle:false},{chatOpen:true},{visible:false},
        {manualTravel:true},{portalTravel:true},{modalOpen:true},{dragging:true},{dnd:true},
        {quiet:true},{locked:true},{fullscreen:true},{gameMode:true},{motionEnabled:false}])
    assert.equal(api.proposeAmbientExpression(state,{...ambientHost,...patch},{expression:'happy'},clock).accepted,false);
assert.equal(api.proposeAmbientExpression(state,ambientHost,{expression:'happy',duration:99999},clock).accepted,false);

// No permission grant, local data, process spawning, timers or external services.
assert.doesNotMatch(code,/\b(?:fetch|XMLHttpRequest|WebSocket|exec|spawn|readFile|Qt\.createQmlObject)\s*\(/);
assert.doesNotMatch(code,/\b(?:setInterval|setTimeout|import\s+Quickshell)\b/);
console.log("ALIS_COMPANION_DIRECTOR_PRIVACY_PRIORITY_LIFECYCLE_PASS");
