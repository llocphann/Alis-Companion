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
assert.equal(check({key:"journal:650"}).reason,"duplicate_or_invalid_reminder");
assert.equal(check({key:"planner:evt_101:0:600"}).eligible,true);
assert.equal(api.record(s,{...p,preview:true}),null);
s=api.record(s,p);
assert.equal(s.dailyCount,1);assert.equal(s.reminder,1);assert.equal(s.unanswered,1);
assert.equal(check({nowMs:p.nowMs+1,key:"planner:evt_101:0:542"}).reason,"cooldown");
assert.equal(check({nowMs:p.nowMs-1}).reason,"invalid_clock");
assert.equal(check({nowMs:p.nowMs+3600001}).reason,"duplicate_or_invalid_reminder");
// Restarting round-trips the counters and content-free keys while stripping obsolete journal:* keys.
s.reminderKeys.push("journal:650");
s=JSON.parse(JSON.stringify(s));
const migrated=api.stateFor(s,p.day);
assert.deepEqual(Array.from(migrated.reminderKeys),["todo:540"]);
s=api.record(s,{...p,nowMs:p.nowMs+3600001,key:"planner:evt_101:0:542"});
assert.equal(s.dailyCount,2);
assert.deepEqual(Array.from(s.reminderKeys),["todo:540","planner:evt_101:0:542"]);
// A second occurrence of the same event ID on the same day has a distinct slot/start key and is not treated as duplicate.
assert.equal(api.decide(s,{...p,dailyCap:6,eventCap:3,nowMs:p.nowMs+18000000,key:"planner:evt_101:1:840"}).eligible,true);
assert.equal(check({dailyCap:2,nowMs:p.nowMs+20000000,key:"todo:600"}).reason,"daily_cap");
assert.equal(check({eventCap:2,nowMs:p.nowMs+20000000,key:"todo:600"}).reason,"event_cap");
assert.equal(check({nowMs:p.nowMs+20000000,key:"todo:600"},{...s,unanswered:3}).reason,"ignored_stop");
assert.equal(check({nowMs:p.nowMs+20000000,key:"todo:600"},{...s,dismissals:3}).reason,"ignored_stop");
assert.equal(check({nowMs:p.nowMs+20000000,key:"todo:600"},{...s,dailyCount:-1}).reason,"invalid_state");
assert.equal(check({day:"2026-10-24",key:"todo:600"}).eligible,true);
assert(!JSON.stringify(s).includes("private title"));
console.log("COMPANION_PROACTIVE_RUNTIME_POLICY_PASS consent quiet quota eventCap backoff ignoredStop restart legacyJournalPurge plannerKeys day clock noContent");
