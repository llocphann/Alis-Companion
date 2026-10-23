const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const api=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname,"../services/WullProactivePolicy.js"),"utf8").replace(/^\.pragma library\s*/,""),api);
const p={event:"reminder",nowMs:100000000,day:"2026-10-23",key:"todo:540",optedIn:true,
    talkEnabled:true,manual:false,visible:true,blocked:false,chatOpen:false,contextOpen:false,busy:false,
    quietEnabled:true,minuteOfDay:720,quietStart:1320,quietEnd:480,dailyCap:6,eventCap:3,idle:true,intervalMs:0};
let s=api.fresh(p.day);
const check=(patch,state=s)=>api.decide(state,{...p,...patch});
assert.equal(check({}).eligible,true);
for(const patch of [{optedIn:false},{manual:true},{talkEnabled:false},{visible:false},{blocked:true},
    {blocked:undefined},{chatOpen:true},{contextOpen:true},{busy:true}]) assert.equal(check(patch).eligible,false);
assert.equal(check({minuteOfDay:1400}).reason,"quiet_hours");
assert.equal(check({minuteOfDay:479}).reason,"quiet_hours");
assert.equal(check({quietStart:NaN}).reason,"invalid_quiet_config");
assert.equal(check({minuteOfDay:480}).eligible,true);
assert.equal(check({event:"playful",idle:false}).reason,"not_idle");
assert.equal(check({event:"checkin",idle:false}).reason,"not_idle");
assert.equal(check({event:"reminder",idle:false}).eligible,true);
for(const cap of [-1,0,21,NaN]) assert.equal(check({dailyCap:cap}).eligible,false);
for(const cap of [-1,0,11,NaN]) assert.equal(check({eventCap:cap}).eligible,false);
assert.equal(check({event:"unknown"}).eligible,false);
assert.equal(check({key:"todo:540:private title"}).eligible,false);
assert.equal(api.record(s,{...p,preview:true}),null);
s=api.record(s,p);
assert.equal(s.dailyCount,1);assert.equal(s.reminder,1);assert.equal(s.unanswered,1);
assert.equal(check({nowMs:p.nowMs+1,key:"calendar:542"}).reason,"cooldown");
assert.equal(check({nowMs:p.nowMs-1}).reason,"invalid_clock");
assert.equal(check({nowMs:p.nowMs+3600001}).reason,"duplicate_or_invalid_reminder");
// Restarting round-trips the counters and content-free keys.
s=JSON.parse(JSON.stringify(s));
s=api.record(s,{...p,nowMs:p.nowMs+3600001,key:"calendar:542"});
assert.equal(s.dailyCount,2);
assert.equal(check({dailyCap:2,nowMs:p.nowMs+20000000,key:"todo:600"}).reason,"daily_cap");
assert.equal(check({eventCap:2,nowMs:p.nowMs+20000000,key:"todo:600"}).reason,"event_cap");
assert.equal(check({nowMs:p.nowMs+20000000,key:"todo:600"},{...s,unanswered:3}).reason,"ignored_stop");
assert.equal(check({nowMs:p.nowMs+20000000,key:"todo:600"},{...s,dismissals:3}).reason,"ignored_stop");
assert.equal(check({nowMs:p.nowMs+20000000,key:"todo:600"},{...s,dailyCount:-1}).reason,"invalid_state");
assert.equal(check({day:"2026-10-24",key:"todo:600"}).eligible,true);
assert(!JSON.stringify(s).includes("private title"));
console.log("COMPANION_PROACTIVE_RUNTIME_POLICY_PASS consent quiet quota eventCap backoff ignoredStop restart day clock noContent");
