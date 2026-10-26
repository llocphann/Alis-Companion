// Synthetic protocol and priority behavior; no desktop, model or user data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const directory = path.join(root, "modules/abyss/companion");
const director = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(directory, "AquaBehaviorDirector.js"), "utf8")
    .replace(/^\.pragma library\s*/u, ""), director);
const api = vm.createContext({Director:director});
vm.runInContext(fs.readFileSync(path.join(directory, "AquaContextProtocol.js"), "utf8")
    .replace(/^\.pragma library\s*/u, "")
    .replace(/^\.import .*$/mu, ""), api);
const plain = value => JSON.parse(JSON.stringify(value));
const generationA = "0123456789abcdef";
const generationB = "fedcba9876543210";
const generationC = "c".repeat(16), generationD = "d".repeat(16);
const tokenA = "a".repeat(16), tokenB = "b".repeat(16);
const host = {enabled:true, visible:true, optedIn:true, locked:false,
    fullscreen:false, gameMode:false, chatOpen:false, dragging:false,
    modalOpen:false, manualTravel:false, portalTravel:false, quiet:false, dnd:false};
let now = 1000000;
let state = api.newState();
const frame = (sequence, kind="agent", payload={word:"working",token:tokenA}, patch={}) =>
    JSON.stringify({version:1, generation:state.generation || generationA,
        sequence, issuedAtMs:now, kind, payload, ...patch});
const accept = (sequence, kind, payload, patch={}) =>
    plain(api.accept(state, frame(sequence,kind,payload,patch), true, now));
const observe = (patch={}) => plain(api.observe(state, {...host,...patch}, now));
const sessions = () => Object.keys(state.director.sessions);

// No handshake from a packet; default off discards even valid-looking traffic.
const disabled = JSON.stringify(state);
assert.equal(api.accept(state, "not json", true, now).reason, "disabled");
assert.equal(JSON.stringify(state), disabled);
assert.equal(accept(1).reason, "disabled");
for (const generation of ["", "ABCDEF0123456789", "../private/file", null]) {
    assert.equal(api.configure(state,true,generation,now).accepted, false);
}
assert.equal(api.configure(state,1,generationA,now).accepted, false);
assert.equal(api.configure(state,true,generationA,-1).accepted, false);
assert.equal(api.configure(state,true,generationA,now).accepted, true);
assert.equal(api.accept(state,frame(1),false,now).reason, "untrusted_peer");
assert.equal(api.accept(state,frame(1),1,now).reason, "untrusted_peer");
assert.equal(state.sequence, 0);

// Extra context, wire-level permission claims and invalid vocabulary fail closed.
for (const text of ["", "{", "null", "[]", '"text"', "x".repeat(api.MAX_FRAME_LENGTH+1)]) {
    assert.equal(api.accept(state,text,true,now).accepted, false);
}
for (const patch of [
    {version:2}, {version:"1"}, {generation:generationB},
    {generation:generationA.toUpperCase()}, {sequence:0}, {sequence:-1},
    {sequence:1.5}, {sequence:9007199254740992}, {issuedAtMs:now+1},
    {issuedAtMs:now-api.MAX_EVENT_AGE_MS-1}, {issuedAtMs:"1000000"},
    {kind:"tool_call"}, {peerVerified:true}, {prompt:"synthetic private value"},
    {title:"synthetic window title"}, {url:"https://example.invalid"},
]) {
    const response = accept(1, "agent", {word:"working",token:tokenA}, patch);
    assert.equal(response.accepted, false);
    assert.equal(JSON.stringify(response).includes("synthetic"), false);
    assert.equal(state.sequence, 0);
    assert.equal(sessions().length, 0);
}
for (const payload of [
    null, [], {}, {word:"working",token:tokenA,prompt:"synthetic"},
    {word:"tool_call",token:tokenA}, {word:"working",token:"private/path"},
]) {
    assert.equal(accept(1,"agent",payload).accepted, false);
}
for (const payload of [{category:"terminal",title:"synthetic"}, {},
    {category:"raw title"}, {category:2}, []]) {
    assert.equal(accept(1,"focus",payload).accepted, false);
}
assert.equal(accept(1,"heartbeat",{source:"unapproved"}).accepted,false);
assert.equal(accept(1,"agent",{word:"activity",token:tokenA}).reason, "unknown_session");
assert.equal(state.sequence, 0, "rejected data cannot spend sequence or work credit");

assert.equal(accept(1).accepted, true);
const first = observe();
assert.equal(first.mode, "cowork");
assert.equal(first.expression, "working");
const started = state.director.sessions[tokenA].started;
assert.equal(api.configure(state,true,generationA,now).changed,false);
assert.equal(accept(1).reason, "out_of_order");
assert.equal(accept(1,"heartbeat",{}).reason, "out_of_order");
now += 5000;
assert.equal(accept(2,"heartbeat",{}).accepted,true);
assert.equal(state.director.sessions[tokenA].started,started);
assert.equal(observe().epoch,first.epoch,"heartbeats do not restart presentation");
assert.equal(accept(3,"agent",{word:"working",token:tokenB}).accepted,true);
assert.equal(sessions().length,2);
for (const [patch, mode] of [
    [{chatOpen:true},"interactive"], [{dragging:true},"interactive"],
    [{modalOpen:true},"interactive"], [{manualTravel:true},"travel"],
    [{portalTravel:true},"travel"], [{locked:true},"hidden"],
    [{fullscreen:true},"hidden"], [{gameMode:true},"hidden"],
    [{visible:false},"hidden"], [{enabled:false},"hidden"],
    [{optedIn:false},"idle"], [{quiet:true},"idle"], [{dnd:true},"idle"],
]) assert.equal(observe(patch).mode,mode);

// Preserve the existing 45-second wait and 60-second active-work policies.
let sequence = 4;
assert.equal(accept(sequence++,"agent",{word:"needs_input",token:tokenA}).accepted,true);
assert.equal(director.pollNeedsInput(state.director,host,now).eligible,false);
for (let i=0;i<9;i++) {
    now += 5000;
    assert.equal(accept(sequence++,"heartbeat",{}).accepted,true);
}
assert.equal(director.pollNeedsInput(state.director,{...host,chatOpen:true},now).eligible,false);
assert.equal(director.pollNeedsInput(state.director,host,now).eligible,true);
assert.equal(director.pollNeedsInput(state.director,host,now).eligible,false);
assert.equal(accept(sequence++,"agent",{word:"finished",token:tokenA}).finishedLong,false);
assert.equal(observe().mode,"cowork","the second session retains one shared intent");
for (let i=0;i<3;i++) {
    now += 5000;
    assert.equal(accept(sequence++,"heartbeat",{}).accepted,true);
}
assert.equal(accept(sequence++,"agent",{word:"finished",token:tokenB}).finishedLong,true);
assert.equal(observe().mode,"idle");

assert.equal(accept(sequence++,"focus",{category:"terminal"}).accepted,true);
assert.equal(observe().mode,"idle");
now += 699;
assert.equal(observe().mode,"idle");
now++;
assert.equal(observe().mode,"cowork");
const oldPresentation = director.beginPresentation(state.director,"intro",now);

// Disconnect clears anonymous context, permissions and old completion ownership.
now += api.CONNECTION_TTL_MS + 1;
assert.equal(observe().mode,"idle");
assert.equal(state.enabled,false);
assert.equal(state.director.focus,"none");
assert.equal(sessions().length,0);
assert.equal(director.completePresentation(state.director,oldPresentation.epoch).accepted,false);
assert.equal(accept(sequence++,"heartbeat",{}).reason,"disabled");
assert.equal(api.configure(state,true,generationA,now).reason,"fresh_generation_required");
assert.equal(api.configure(state,true,generationB,now).accepted,true);
assert.equal(accept(1,"agent",{word:"working",token:tokenA},{generation:generationA}).reason,"old_generation");
assert.equal(accept(1).accepted,true);
const newPresentation = director.beginPresentation(state.director,"intro",now);
assert.ok(newPresentation.epoch > oldPresentation.epoch);
assert.equal(director.completePresentation(state.director,oldPresentation.epoch).accepted,false);
assert.equal(director.completePresentation(state.director,newPresentation.epoch).accepted,true);
assert.equal(director.completePresentation(state.director,newPresentation.epoch).accepted,false);

// Re-reading settings does not serve as a heartbeat; backwards clocks revoke.
const lastReceived = state.lastReceivedMs;
now += 1000;
assert.equal(api.configure(state,true,generationB,now).changed,false);
assert.equal(state.lastReceivedMs,lastReceived);
observe();
assert.equal(api.accept(state,frame(2),true,now-1).reason,"invalid_clock");
assert.equal(state.enabled,false);
assert.equal(sessions().length,0);
assert.equal(api.configure(state,true,generationC,now).accepted,true);
assert.equal(accept(1).accepted,true);
assert.equal(api.configure(state,false,"",NaN).changed,true,
    "explicit revocation must succeed even if the owner clock is unavailable");
assert.equal(sessions().length,0);
const off = JSON.stringify(state);
assert.equal(accept(2).reason,"disabled");
assert.equal(JSON.stringify(state),off);
assert.equal(api.configure(state,true,generationD,now).accepted,true);
assert.equal(accept(1).accepted,true);
observe();
assert.equal(api.configure(state,true,generationD,now-1).reason,"invalid_clock");
assert.equal(state.enabled,false);
assert.equal(sessions().length,0);

// Bound anonymous state and verify exact age boundaries without real timers.
state = api.newState();
assert.equal(api.configure(state,true,generationA,now).accepted,true);
for (let i=1;i<=20;i++) {
    assert.equal(accept(i,"agent",{word:"working",token:i.toString(16).padStart(16,"0")}).accepted,true);
}
assert.equal(sessions().length,16);
assert.equal(accept(21,"heartbeat",{}, {issuedAtMs:now-api.MAX_EVENT_AGE_MS}).accepted,true);
assert.equal(accept(22,"heartbeat",{}, {issuedAtMs:now-api.MAX_EVENT_AGE_MS-1}).accepted,false);
assert.equal(state.sequence,21);
assert.equal(JSON.stringify(state).includes("synthetic"),false);
console.log("ALIS_COMPANION_CONTEXT_PROTOCOL_CONSENT_REPLAY_LIFECYCLE_PASS");
