// No processes, model, journal or desktop: exercise the real helper boundary.
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const publicReply = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, "services/WullReplyGuard.js"), "utf8")
    .replace(/^\.pragma library\s*/, ""), publicReply);
const guard = vm.createContext({PublicReply:publicReply});
vm.runInContext(fs.readFileSync(path.join(root, "services/WullHelperReplyGuard.js"), "utf8")
    .replace(/^\.pragma library\s*/, "").replace(/^\.import[^\n]*\n/, ""), guard);
const reply = (action, result) => guard.parse(action, JSON.stringify({ok:true, result}), 0);
const invalid = (action, result) => {
    const parsed = reply(action, result);
    assert.equal(parsed.ok, false, JSON.stringify({action, result}));
    assert.equal(parsed.malformed, true);
    assert.equal(parsed.error.message, guard.invalidMessage);
};
const row = {id:1, role:"user", content:"  Literal <tool_call> and [INST].\nKeep my exact text.  ",
    createdMs:0, model:"", metadata:{retained:true}};
const journal = {date:"2026-10-14", mood:"", energy:"", journalPath:"", schedule:[], marker:"retained"};
const schedule = {start:840, end:null, title:"Study", source:"Fixture vault", kind:"schedule"};
const checkIn = {saved:true, field:"mood", value:"good", date:"2026-10-14", journalPath:"/fixture/journal.md"};
const chat = {text:"Hello, little splash.", expression:"happy", historySaved:false,
    userMessageId:0, assistantMessageId:0, model:"fixture", source:"local"};

for (const raw of ["null", "[]", "true", "0", '"text"', "{", '{"ok":"false","result":{}}',
    '{"ok":1,"result":{}}', '{"ok":true}', '{"ok":true,"result":null}',
    '{"ok":false,"error":null}', '{"ok":false,"error":{"message":123}}']) {
    const result = guard.parse("context", raw, 0);
    assert.equal(result.ok, false, raw);
    assert.equal(result.malformed, true, raw);
}
assert.equal(guard.parse("context", "x".repeat(32769), 0).ok, false);
assert.equal(guard.parse("context", null, 0).ok, false);
assert.equal(guard.parse("context", JSON.stringify({ok:true,result:journal}), 1).ok, false);
const error = guard.parse("context", JSON.stringify({ok:false,
    error:{code:"integration_unavailable",message:"Obsidian integration is unavailable."}, metadata:"allowed"}), 0);
assert.equal(error.ok, false);
assert.equal(error.malformed, undefined);
assert.equal(error.error.code, "integration_unavailable");
assert.equal(error.error.message, "Obsidian integration is unavailable.");
assert.equal(guard.parse("context", JSON.stringify({ok:false,error:{message:"x".repeat(161)}}), 0).malformed, true);

const history = reply("history", {messages:[row],hasMore:false});
assert.equal(history.ok, true);
assert.equal(history.result.messages[0].content, row.content);
assert.equal(history.result.messages[0].metadata.retained, true);
assert.equal(reply("history", {messages:[],hasMore:false}).ok, true);
for (const result of [null, [], {messages:"oops",hasMore:false}, {messages:[null],hasMore:false},
    {messages:[row],hasMore:"false"}, {messages:Array(101).fill(row),hasMore:true}]) invalid("history", result);
for (const patch of [{id:0},{id:-1},{id:"1"},{id:Number.MAX_SAFE_INTEGER+1},{role:"system"},
    {content:{text:"nested"}},{content:""},{content:"x".repeat(6001)},{createdMs:-1},{createdMs:"1"},
    {model:123},{model:"x".repeat(161)}]) invalid("history", {messages:[{...row,...patch}],hasMore:false});
assert.equal(reply("history_append", {userMessageId:1,assistantMessageId:2,metadata:"allowed"}).ok, true);
for (const pair of [[0,0],[1,1],[2,1],[-1,2],["1",2],[1,Number.MAX_SAFE_INTEGER+1]])
    invalid("history_append", {userMessageId:pair[0],assistantMessageId:pair[1]});
assert.equal(reply("history_clear", {cleared:true}).ok, true);
for (const result of [{},{cleared:false},{cleared:"true"},{cleared:1},null]) invalid("history_clear", result);

assert.equal(reply("context", journal).result.marker, "retained");
assert.equal(reply("context", {...journal,schedule:[schedule],vaults:["Fixture vault"]}).ok, true);
for (const patch of [{date:null},{date:"wrong"},{mood:{}},{mood:"x".repeat(61)},{energy:null},
    {journalPath:123},{journalPath:"bad\npath"},{journalPath:"x".repeat(4097)},
    {schedule:"oops"},{schedule:[null]},{schedule:Array(17).fill(schedule)},
    {vaults:"oops"},{vaults:[null]},{vaults:["a","b","c"]}]) invalid("context", {...journal,...patch});
for (const patch of [{start:-1},{start:1440},{start:"840"},{start:1.5},{end:undefined},{end:1440},
    {title:[]},{title:""},{title:"x".repeat(181)},{source:null},{source:"x".repeat(256)},
    {kind:"tool"}]) invalid("context", {...journal,schedule:[{...schedule,...patch}]});
assert.equal(reply("check_in", checkIn).ok, true);
for (const patch of [{saved:"true"},{saved:false},{field:"unknown"},{value:"invented"},
    {date:null},{journalPath:""},{journalPath:null},{journalPath:"bad\u0000path"}])
    invalid("check_in", {...checkIn,...patch});

assert.equal(reply("chat", chat).ok, true);
assert.equal(reply("chat", {...chat,historySaved:true,userMessageId:1,assistantMessageId:2}).ok, true);
assert.equal(reply("chat", {...chat,expression:"unknown"}).ok, true); // existing idle fallback
for (const patch of [{text:null},{text:"<think>private</think>"},{text:"<|channel|>analysis"},
    {text:"x".repeat(6001)},{historySaved:"true"},{userMessageId:1},{assistantMessageId:-1},
    {userMessageId:1,assistantMessageId:Number.MAX_SAFE_INTEGER+1}]) invalid("chat", {...chat,...patch});
assert.equal(reply("probe", {ready:true,endpoint:"http://127.0.0.1:11434",
    models:[{name:"fixture",size:0,thinking:false}]}).ok, true);
invalid("probe", {ready:"true",endpoint:"http://127.0.0.1:11434",models:[]});
invalid("probe", {ready:true,endpoint:"http://127.0.0.1:11434",models:[null]});
invalid("unsupported", {});
const memory={id:1,scope:"shared",text:"Explicit preference",source:"explicit_user",created_ms:1,expires_ms:0};
assert.equal(reply("memory_list",{memories:[memory]}).ok,true);
for(const patch of [{id:0},{scope:"cloud"},{text:"line\nline"},{source:"ai_extraction"},{expires_ms:-1}])
    invalid("memory_list",{memories:[{...memory,...patch}]});
invalid("memory_list",{memories:Array(129).fill(memory)});
assert.equal(reply("memory_save",{saved:true,id:1}).ok,true);
invalid("memory_save",{saved:true,id:0});
assert.equal(reply("memory_forget",{deleted:false}).ok,true);
invalid("memory_forget",{deleted:"true"});
assert.equal(reply("memory_forget_all",{deletedCount:128}).ok,true);
invalid("memory_forget_all",{deletedCount:-1});

// Invoke the product completion function so rejection also proves state safety.
const qml = fs.readFileSync(path.join(root, "services/WullMind.qml"), "utf8");
const start = qml.indexOf("    function completed("), end = qml.indexOf("    onCharacterChanged:", start);
assert.ok(start >= 0 && end > start);
const completion = qml.slice(start, end).replace("): void {", ") {");
const state = vm.createContext({WullHelperReplyGuard:guard,WullReplyGuard:publicReply,
    epoch:0,pending:null,busy:false,deadline:{stop(){}},hostVisible:true,hostIdle:true,
    conversationOpen:false,contextOpen:false,proactiveIdleEnabled:true,connectionStatus:"ready",
    currentDay:journal.date,checkInComplete:true,checkInDate:journal.date,checkInStage:"mood",errorMessage:"",
    journal:{marker:"before"},history:[row],historyClearPending:true,historyLoadingOlder:true,
    lastContext:0,lastContextAttempt:0,Persistent:{states:{wullCheckIn:{date:journal.date,mood:"",energy:""}}},
    root:{offerAutomatic(){}},Qt:{callLater(){}},say(){},resolvePendingUser(){},
    checkInChoices(field){return field==="mood" ? ["good"] : field==="energy" ? ["high"] : []},
    choiceSaved(){throw new Error("malformed receipt advanced daily choices")}});
vm.runInContext(completion, state);
function complete(action, raw) {
    const serial = ++state.epoch;
    state.pending = {serial,action,request:{clientKey:"fixture",field:"mood",value:"good",date:journal.date},automatic:false};
    state.busy = true; state.errorMessage = "";
    assert.doesNotThrow(() => state.completed(raw, 0, serial));
    assert.equal(state.pending, null); assert.equal(state.busy, false);
}
const beforeHistory = JSON.stringify(state.history), beforeJournal = JSON.stringify(state.journal);
const beforeCheckIn = JSON.stringify(state.Persistent.states.wullCheckIn);
for (const [action,raw] of [["context","null"],["context",JSON.stringify({ok:true,result:null})],
    ["context",JSON.stringify({ok:"false",result:journal})],
    ["history",JSON.stringify({ok:true,result:{messages:"oops",hasMore:false}})],
    ["history_append",JSON.stringify({ok:true,result:{userMessageId:0,assistantMessageId:0}})],
    ["history_clear",JSON.stringify({ok:true,result:{cleared:"true"}})],
    ["check_in",JSON.stringify({ok:true,result:{...checkIn,saved:"true"}})],
    ["chat",JSON.stringify({ok:true,result:{...chat,text:"<analysis>private</analysis>"}})]]) {
    complete(action, raw);
    assert.equal(state.errorMessage, guard.invalidMessage);
    assert.equal(JSON.stringify(state.history), beforeHistory, action);
    assert.equal(JSON.stringify(state.journal), beforeJournal, action);
    assert.equal(JSON.stringify(state.Persistent.states.wullCheckIn), beforeCheckIn, action);
}
complete("history", JSON.stringify({ok:true,result:{messages:[row],hasMore:false}}));
assert.equal(state.history[0].content, row.content);
assert.equal(state.errorMessage, "");
complete("context", JSON.stringify({ok:true,result:journal}));
assert.equal(state.journal.marker, "retained");
assert.equal(state.errorMessage, "");
console.log("WULL_HELPER_REPLY_GUARD_PASS malformed-envelope typed-results state-preservation exact-user-text recovery");
