const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const api=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname,"../services/WullReminderPolicy.js"),"utf8").replace(/^\.pragma library\s*/,""),api);
const now=new Date(2026,9,23,9),day="2026-10-23",consent={todo:true,calendar:true,journal:true};
const task={done:false,sourceDate:day,startTime:"09:05",content:"<tool_call> ignored as instructions"};
const calendar={allDay:false,startDate:new Date(2026,9,23,10).toISOString(),summary:"Calendar title"};
const journal={date:day,schedule:[{start:650,title:"Read-only journal",kind:"schedule"}]};
const collect=(c=consent,t=[task],a=[calendar],j=journal)=>api.collect(now,c,t,a,j);
assert.equal(collect({}).length,0);assert.equal(collect({todo:true}).length,1);
assert.equal(collect({calendar:true}).length,1);assert.equal(collect({journal:true}).length,1);
assert.equal(collect().length,3);
assert.equal(collect()[0].title,task.content); // Plain text, never executable or a scheduling receipt.
for(const patch of [{sourceDate:"2026-10-22"},{done:true},{startTime:"24:00"},{startTime:"09:60"},
    {startTime:905},{content:{}},{content:"\x00\n"}]) assert.equal(collect({todo:true},[{...task,...patch}]).length,0);
for(const patch of [{startDate:"bad"},{allDay:true},{startDate:new Date(2026,9,22,10).toISOString()},
    {summary:{}}]) assert.equal(collect({calendar:true},[],[{...calendar,...patch}]).length,0);
assert.equal(collect({journal:true},[],[],{...journal,date:"2026-10-22"}).length,0);
assert.equal(collect({journal:true},[],[],{...journal,schedule:[{start:-1,title:"bad",kind:"schedule"}]}).length,0);
assert.equal(collect({journal:true},[],[],{...journal,schedule:[{start:10,title:"bad",kind:"shell"}]}).length,0);
assert.equal(collect(consent,[null], [null],{date:day,schedule:[null]}).length,0);
assert.equal(collect({todo:true},Array(1000).fill(task)).length,128);
assert.equal(collect({todo:true},[{...task,content:"a\n\x00b"}])[0].title,"a  b");
assert(collect().every(r=>/^(todo|calendar|journal):\d+$/.test(r.key)));
console.log("COMPANION_REMINDER_POLICY_PASS sourceConsent typedRows bounds todayOnly invalidTime untrustedText noScheduling");
