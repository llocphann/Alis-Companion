const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const api=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname,"../services/AquaReminderPolicy.js"),"utf8").replace(/^\.pragma library\s*/,""),api);
const now=new Date(2026,9,23,9),day="2026-10-23",consent={todo:true,planner:true,calendar:true,journal:true};
const task={done:false,sourceDate:day,startTime:"09:05",content:"<tool_call> ignored as instructions"};
const plannerEvent={id:"evt_101",title:"Weekly Planner Standup",repeat:"none",leadMinutes:15,
    occurrences:[{startTime:new Date(2026,9,23,10,0,0).getTime(),endTime:new Date(2026,9,23,10,30,0).getTime(),notified:false}]};
const journal={date:day,schedule:[{start:650,title:"Obsolete Obsidian schedule",kind:"schedule"}]};
const collect=(c=consent,t=[task],a=[plannerEvent],j=journal)=>api.collect(now,c,t,a,j);
assert.equal(collect({}).length,0);
assert.equal(collect({todo:true}).length,1);
assert.equal(collect({planner:true}).length,1);
assert.equal(collect({calendar:true}).length,1);
// Obsidian journal schedule is retired as a reminder source even if passed with journal:true.
assert.equal(collect({journal:true}).length,0);
assert.equal(collect().length,2);
assert.equal(collect()[0].title,task.content);
assert.equal(collect()[1].title,"Weekly Planner Standup");
assert.equal(collect()[1].source,"planner");
assert.equal(collect()[1].eventId,"evt_101");
assert.equal(collect()[1].occurrenceIndex,0);
assert.equal(collect()[1].leadMinutes,15);
assert.equal(collect()[1].key,"planner:evt_101:0:600");

// Multi-occurrence grouped Weekly Planner event on the same day: distinct stable keys per slot.
const multiSlotEvent={id:"evt_multi",title:"Two-part workshop",repeat:"none",
    occurrences:[
        {startTime:new Date(2026,9,23,9,30,0).getTime(),endTime:new Date(2026,9,23,10,0,0).getTime(),notified:false},
        {startTime:new Date(2026,9,23,14,15,0).getTime(),endTime:new Date(2026,9,23,15,0,0).getTime(),notified:false}
    ]};
const multiRows=api.collect(now,{planner:true},[],[multiSlotEvent]);
assert.equal(multiRows.length,2);
assert.deepEqual(Array.from(multiRows,r=>r.key),["planner:evt_multi:0:570","planner:evt_multi:1:855"]);

// Repeating Weekly Planner occurrences: daily, weekly, monthly, yearly.
const dailyRecurring={id:"evt_daily",title:"Daily Stretch",repeat:"daily",
    occurrences:[{startTime:new Date(2026,9,20,8,45,0).getTime(),endTime:new Date(2026,9,20,9,0,0).getTime(),notified:false}]};
const weeklyRecurring={id:"evt_weekly",title:"Friday Sync",repeat:"weekly",
    occurrences:[{startTime:new Date(2026,9,9,11,30,0).getTime(),endTime:new Date(2026,9,9,12,0,0).getTime(),notified:false}]};
const monthlyRecurring={id:"evt_monthly",title:"Monthly Review",repeat:"monthly",
    occurrences:[{startTime:new Date(2026,7,23,16,0,0).getTime(),endTime:new Date(2026,7,23,17,0,0).getTime(),notified:false}]};
const yearlyRecurring={id:"evt_yearly",title:"Annual Milestone",repeat:"yearly",
    occurrences:[{startTime:new Date(2024,9,23,13,0,0).getTime(),endTime:new Date(2024,9,23,14,0,0).getTime(),notified:false}]};
const recurringRows=api.collect(now,{planner:true},[],[dailyRecurring,weeklyRecurring,monthlyRecurring,yearlyRecurring]);
assert.deepEqual(Array.from(recurringRows,r=>({key:r.key,recurring:r.recurring})),[
    {key:"planner:evt_daily:0:525",recurring:true},
    {key:"planner:evt_weekly:0:690",recurring:true},
    {key:"planner:evt_yearly:0:780",recurring:true},
    {key:"planner:evt_monthly:0:960",recurring:true}
]);

// Single-instance override (plannerOverrides) moving today's recurring projection to a new time or another day.
const overriddenMoved={...weeklyRecurring,id:"evt_override_move",
    plannerOverrides:[{occurrenceIndex:0,
        originalStartDate:new Date(2026,9,23,11,30,0).toISOString(),
        startDate:new Date(2026,9,23,15,45,0).toISOString(),
        endDate:new Date(2026,9,23,16,15,0).toISOString()}]};
const overriddenMovedAway={...weeklyRecurring,id:"evt_override_away",
    plannerOverrides:[{occurrenceIndex:0,
        originalStartDate:new Date(2026,9,23,11,30,0).toISOString(),
        startDate:new Date(2026,9,24,11,30,0).toISOString(),
        endDate:new Date(2026,9,24,12,0,0).toISOString()}]};
const overrideRows=api.collect(now,{planner:true},[],[overriddenMoved,overriddenMovedAway]);
assert.deepEqual(Array.from(overrideRows,r=>r.key),["planner:evt_override_move:0:945"]);

// Future schedule branch rule ("this and following" / plannerFutureSchedule) & converted template (selectedTemplate).
const rootStart=new Date(2026,9,9,11,30,0),rootEnd=new Date(2026,9,9,12,0,0);
const branchKey=api.branchSignature("weekly",rootStart,rootEnd);
const anchorCycle=api.cycleIndex(new Date(2026,9,16,17,10,0),"weekly");
const futureBranched={id:"evt_future_branch",title:"Branched Weekly",recurrence:"weekly",
    occurrences:[{startDate:rootStart.toISOString(),endDate:rootEnd.toISOString(),notified:false}],
    plannerFutureSchedule:[{version:1,mode:"weekly",key:branchKey,fromCycle:anchorCycle,anchorCycle:anchorCycle,
        rootStartDate:rootStart.toISOString(),rootEndDate:rootEnd.toISOString(),
        startDate:new Date(2026,9,16,17,10,0).toISOString(),endDate:new Date(2026,9,16,17,40,0).toISOString()}]};
const convertedSingle={id:"evt_converted",title:"Converted Single",recurrence:"none",
    occurrences:[
        {startDate:new Date(2026,9,23,8,0,0).toISOString(),endDate:new Date(2026,9,23,8,30,0).toISOString(),notified:false},
        {startDate:new Date(2026,9,23,18,20,0).toISOString(),endDate:new Date(2026,9,23,18,50,0).toISOString(),notified:false}
    ],
    selectedTemplate:[{startDate:new Date(2026,9,23,18,20,0).toISOString(),endDate:new Date(2026,9,23,18,50,0).toISOString()}]};
const branchRows=api.collect(now,{planner:true},[],[futureBranched,convertedSingle]);
assert.deepEqual(Array.from(branchRows,r=>r.key),["planner:evt_future_branch:0:1030","planner:evt_converted:1:1100"]);

// Event update at same start time keeps identical deduplication key; rescheduling changes key; deletion removes row.
const renamedEvent={...plannerEvent,title:"Renamed Weekly Planner Standup"};
assert.equal(api.collect(now,{planner:true},[],[renamedEvent])[0].key,"planner:evt_101:0:600");
const movedEvent={...plannerEvent,occurrences:[{startTime:new Date(2026,9,23,10,30,0).getTime(),endTime:new Date(2026,9,23,11,0,0).getTime(),notified:false}]};
assert.equal(api.collect(now,{planner:true},[],[movedEvent])[0].key,"planner:evt_101:0:630");
assert.equal(api.collect(now,{planner:true},[],[{...plannerEvent,deleted:true}]).length,0);

for(const patch of [{sourceDate:"2026-10-22"},{done:true},{startTime:"24:00"},{startTime:"09:60"},
    {startTime:905},{content:{}},{content:"\x00\n"}]) assert.equal(collect({todo:true},[{...task,...patch}]).length,0);
for(const patch of [{startDate:"bad",occurrences:[]},{allDay:true},{startDate:new Date(2026,9,22,10).toISOString(),occurrences:[]},
    {title:{},summary:{}}]) assert.equal(collect({planner:true},[],[{...plannerEvent,...patch}]).length,0);
assert.equal(collect(consent,[null],[null],{date:day,schedule:[null]}).length,0);
assert.equal(collect({todo:true},Array(1000).fill(task)).length,128);
assert.equal(collect({todo:true},[{...task,content:"a\n\x00b"}])[0].title,"a  b");
assert(collect().every(r=>/^(?:todo:\d+|(?:planner|calendar):(?:[A-Za-z0-9_-]+:\d+:)?\d+)$/.test(r.key)));
console.log("COMPANION_REMINDER_POLICY_PASS sourceConsent weeklyPlannerEvents multiSlot recurring overrides futureSchedule noObsidianSchedule typedRows bounds todayOnly invalidTime untrustedText noScheduling");
