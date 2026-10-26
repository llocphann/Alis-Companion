// Pure fixture policy tests; no AI, notification or host manipulation.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const path=require("node:path");
const root=path.resolve(__dirname,"..");
const filename=path.join(root,"scripts/aqua-proactive-policy-prototype.js");
const api=vm.createContext({});
vm.runInContext(fs.readFileSync(filename,"utf8"),api);
const base={
 nowMs:24*60*60*1000, event:"playful", optedIn:true, manual:false,
 talkEnabled:true, visible:true, dnd:false, fullscreen:false,
 chatOpen:false, contextOpen:false, busy:false, quietEnabled:true,
 minuteOfDay:14*60, quietStartMinute:23*60, quietEndMinute:8*60,
 dailyCount:0, dailyCap:2, unanswered:0, dismissals:0,
 idle:true, lastNudgeMs:0
};
const decision=extras=>api.decide({...base,...extras});
assert.equal(decision({}).eligible,true);
assert.equal(decision({optedIn:false}).reason,"disabled_or_manual");
assert.equal(decision({manual:true}).reason,"disabled_or_manual");
for(const key of ["visible","dnd","fullscreen","chatOpen","contextOpen","busy"]){
  assert.equal(decision({[key]:key!=="visible"}).reason,"host_ineligible",key);
}
assert.equal(decision({quietEnabled:true,minuteOfDay:23*60+5}).reason,"quiet_hours");
assert.equal(decision({quietEnabled:true,minuteOfDay:7*60+59}).reason,"quiet_hours");
assert.equal(decision({quietEnabled:true,minuteOfDay:8*60}).eligible,true);
assert.equal(decision({quietStartMinute:2000}).reason,"invalid_quiet_config");
assert.equal(decision({dailyCount:2}).reason,"daily_cap");
assert.equal(decision({unanswered:3}).reason,"ignored_stop");
assert.equal(decision({dismissals:3}).reason,"ignored_stop");
assert.equal(decision({idle:false}).reason,"not_idle");
assert.equal(decision({event:"reminder",idle:false}).eligible,true);
assert.equal(decision({event:"playful",unanswered:1,lastNudgeMs:base.nowMs-25*60*1000}).reason,"cooldown");
assert.equal(decision({event:"playful",unanswered:2,lastNudgeMs:base.nowMs-3*60*60*1000}).eligible,true);
assert.equal(decision({lastNudgeMs:base.nowMs+1}).reason,"invalid_clock");
assert.equal(decision({event:"bad"}).reason,"invalid_request");
assert.equal(decision({unanswered:-1}).reason,"invalid_streak");
// A dormant policy must not have a live import, event source, timer or network.
const qml=fs.readFileSync(path.join(root,"services/AquaMind.qml"),"utf8");
assert.doesNotMatch(qml,/aqua-proactive-policy-prototype\.js/i);
assert.doesNotMatch(fs.readFileSync(filename,"utf8"),/\b(?:fetch|XMLHttpRequest|exec|spawn|require)\s*\(/);
console.log("ALIS_COMPANION_PROACTIVE_POLICY_DORMANT_CONTRACT_PASS");
