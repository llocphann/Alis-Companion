#!/usr/bin/env python3
"""Aqua uses the real Ai service with a private loopback protocol fixture."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib, json, os, shutil, sqlite3, subprocess, tempfile, threading, time
from native_test_session import private_wayland, run_qs

ROOT=Path(__file__).resolve().parents[1]
if not shutil.which("qs"):
    print("SKIP: Aqua shared AI runtime requires Quickshell")
    raise SystemExit(0)
requests=[]
model_ids=['main','aqua','failure','slow','extra','tokens','malformed','controltag','eof-tool',
           'cloud','responses','spoof','verified','unverified','instant','mixed-meta','action-claim','forget-claim']
model_rows=[{'id':'fixture-'+name,'type':'vlm','compatibility_type':'gguf','arch':'fixture',
            'quantization':'Q4_K_M','state':'loaded','max_context_length':4096} for name in model_ids]
fingerprints={row['id']:hashlib.sha256(json.dumps({'provider':'lmstudio','model':row['id'],
    'format':'gguf','architecture':'fixture','quantization':'Q4_K_M','contextTokens':4096},
    sort_keys=True,separators=(',',':')).encode()).hexdigest() for row in model_rows}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        if self.path=='/api/v1/models':
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'models':[{'key':'fixture-instant','type':'llm','format':'gguf',
                'architecture':'fixture','quantization':{'name':'Q4_K_M'},'max_context_length':4096,
                'capabilities':{'reasoning':{'allowed_options':['off','on'],'default':'on'}}}]}).encode())
            return
        if self.path!='/api/v0/models':return self.send_error(404)
        self.send_response(200);self.end_headers()
        self.wfile.write(json.dumps({'data':model_rows}).encode())
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append((self.path,data,dict(self.headers)))
        if self.path=='/local-verified':
            next(row for row in model_rows if row['id']=='fixture-verified')['type']='cloud'
        if self.path=="/slow":time.sleep(.7)
        failure=self.path=="/failure"
        self.send_response(429 if failure else 200)
        self.send_header("Content-Type","text/event-stream");self.end_headers()
        if self.path in ("/responses","/v1/responses"):
            events=[{'type':'response.output_text.delta','delta':json.dumps({'text':'Responses hello','expression':'happy'})},
                    {'type':'response.completed'}]
            self.wfile.write(''.join('data: '+json.dumps(event)+'\n\n' for event in events).encode())
            return
        content={
            '/extra':json.dumps({'text':'Not public','tool_calls':[{'name':'open_file'}]}),
            '/tokens':json.dumps({'text':'<|channel|>analysis secret','expression':'happy'}),
            '/controltag':json.dumps({'text':'Hello <thi\x01nk>fixture internal</thi\x01nk>','expression':'happy'}),
            '/malformed':'{"text":"unfinished"',
            '/mixed-meta':'A little note. {expression: "happy"}',
            '/action-claim':json.dumps({'text':'I decide to rearrange the desktop icons.','expression':'happy'}),
            '/forget-claim':json.dumps({'text':'Got it, the slate is clean.','expression':'happy'}),
        }.get(self.path,json.dumps({'text':'Fixture hello','expression':'happy'}))
        if self.path=="/eof-tool":
            # Normal prose followed by an unfinished tool stream exercises the
            # host strategy's EOF flush, without invoking or authorizing a tool.
            events=[{"choices":[{"delta":{"content":"Hello, friend."}}]},
                    {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"fixture-call",
                     "type":"function","function":{"name":"run_shell_command",
                     "arguments":json.dumps({"command":"fixture must never run"})}}]}}]}]
            try:self.wfile.write("".join("data: "+json.dumps(event)+"\n\n" for event in events).encode())
            except (BrokenPipeError,ConnectionResetError):pass
            return
        payload={"error":{"message":"fixture rate limit"}} if failure else {
            "choices":[{"delta":{"content":content},"finish_reason":"stop"}]}
        try:self.wfile.write(("data: "+json.dumps(payload)+"\n\ndata: [DONE]\n\n").encode())
        except (BrokenPipeError,ConnectionResetError):pass

server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 with tempfile.TemporaryDirectory(prefix="hadalis-aqua-ai-") as name:
    folder=Path(name)
    for entry in ["modules","services","GlobalStates.qml","qmldir","assets","scripts","defaults","translations","optional"]:
        (folder/entry).symlink_to(ROOT/entry)
    config=folder/"config/alis";config.mkdir(parents=True)
    shutil.copy(ROOT/"defaults/config.json",config/"config.json")
    curl_home=folder/'curl-home';curl_home.mkdir()
    (curl_home/'.curlrc').write_text('url = "http://127.0.0.1:'+str(server.server_port)+'/curlrc-leak"\n'
        'header = "X-Curlrc-Fixture: unwanted"\nlocation\n')
    (folder/"shell.qml").write_text(r'''
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import qs.modules.common
import qs.services
import "optional/alis-companion/services"
import qs.services.deferred
ShellRoot {
 id:root
 property var optionalHost:AlisCompanion
 Component.onCompleted: Quickshell.watchFiles=false
 property int step:0
 property int ticks:0
 property var mainIDs:[]
 property var invalidModels:["fixture-extra","fixture-tokens","fixture-malformed","fixture-controltag","fixture-eof-tool","fixture-mixed-meta","fixture-action-claim"]
 property int invalidIndex:0
 property string previousText:""
 property int assistantCount:0
 property int privacyIndex:0
 property var privacyRows:[]
 property string privacyOriginalCharacter:""
 property var privacyCases:[{character:"aqua",prompt:"Forget what I said before."},
  {character:"octo",prompt:"Forget what I said before."},
  {character:"aqua",prompt:"Hãy quên những gì tôi đã nói."},
  {character:"octo",prompt:"Hãy quên những gì tôi đã nói."}]
 property string replacedToken:""
 property bool cadenceStarted:false
 property bool cadenceComplete:false
 property bool aiConfigured:false
 property var localFingerprints: LOCAL_FINGERPRINTS
 function check(ok,message): bool {
  if(ok)return true
  console.error("AQUA_SHARED_AI_FAIL",message);Qt.quit();return false
 }
 function add(id,path): void {
  Ai.addModel(id,{name:id,model:id,endpoint:Quickshell.env("AI_FIXTURE_URL")+path,
   local:true,local_verified:true,local_fingerprint:root.localFingerprints[id],
   requires_key:false,provider_id:"lmstudio",api_format:"openai",
   capabilities:{chat:"supported",reasoning:"supported",reasoningOff:id==="fixture-instant" ? "supported" : "unknown"}})
 }
 function helperContext(marker): var {
  return {date:AquaMind.today(),mood:"",energy:"",journalPath:"",schedule:[],marker:marker}
 }
 function checkLiveOffers(): bool {
  if(!root.check(!AquaMind.proactiveIdleEnabled && !AquaMind.todoRemindersEnabled
      && !AquaMind.plannerRemindersEnabled && !AquaMind.calendarRemindersEnabled && !AquaMind.journalRemindersEnabled
      && !AquaMind.reminderNotificationsEnabled,"new source or proactive consent defaulted on"))return false
  const anchor=new Date();anchor.setHours(12,0,0,0)
  const state=Persistent.states.aquaProactive,now=anchor.getTime(),day=AquaMind.today()
  function reset(): void {
   for(const key of ["dailyCount","reminder","checkin","playful","unanswered","dismissals","lastNudgeMs"])state[key]=0
   state.day=day;state.reminderKeys=[]
  }
  reset()
  AquaMind.hostVisible=true;AquaMind.hostBlocked=false;AquaMind.hostIdle=false
  Config.setNestedValues({"abyss.companionMind.proactiveEnabled":true,
   "abyss.companionMind.proactive":"often","abyss.companionMind.proactiveQuietEnabled":false,
   "abyss.companionMind.proactiveDailyCap":2,"abyss.companionMind.proactiveEventCap":2,
   "abyss.companionMind.journalRemindersEnabled":true,"abyss.companionMind.obsidianEnabled":true})
  if(!root.check(!AquaMind.journalRemindersEnabled
      && !AquaMind.sayAutomatic("Obsolete journal source","reminder",now,"journal:540"),
      "retired journal reminder source was accepted"))return false
  Config.setNestedValues({"abyss.companionMind.journalRemindersEnabled":false,"abyss.companionMind.obsidianEnabled":false})
  if(!root.check(!AquaMind.sayAutomatic("No source consent","reminder",now,"todo:540")
      && !AquaMind.sayAutomatic("No planner consent","reminder",now,"planner:evt_live:0:720"),
      "unconsented reminder source was used"))return false
  Config.setNestedValue("abyss.companionMind.todoRemindersEnabled",true)
  if(!root.check(AquaMind.sayAutomatic("Owned reminder one","reminder",now,"todo:540")
      && state.dailyCount===1 && state.reminder===1 && state.unanswered===1
      && AquaMind.text==="Owned reminder one","consented offer failed to record actual quotas"))return false
  AquaMind.hostBlocked=true
  if(!root.check(AquaMind.text==="" && !AquaMind.automaticOffer
      && !AquaMind.sayAutomatic("Blocked","reminder",now+7200000,"todo:541"),"modal/host suppression failed"))return false
  AquaMind.hostBlocked=false
  TimerService.pomodoroRunning=true;TimerService.pomodoroPaused=false;TimerService.pomodoroBreak=false
  if(!root.check(AquaMind.focusSessionActive && !AquaMind.sayAutomatic("Focus blocked","reminder",now+7200000,"todo:541"),"existing Focus session did not suppress offers"))return false
  TimerService.pomodoroRunning=false
  const minute=new Date(now).getHours()*60+new Date(now).getMinutes()
  Config.setNestedValues({"abyss.companionMind.proactiveQuietEnabled":true,
   "abyss.companionMind.proactiveQuietStart":minute,"abyss.companionMind.proactiveQuietEnd":(minute+1)%1440})
  if(!root.check(!AquaMind.sayAutomatic("Quiet blocked","reminder",now,"todo:541"),"quiet hours ignored"))return false
  Config.setNestedValue("abyss.companionMind.proactiveQuietEnabled",false)
  if(!root.check(!AquaMind.sayAutomatic("Duplicate","reminder",now+7200000,"todo:540")
      && AquaMind.sayAutomatic("Owned reminder two","reminder",now+7200000,"todo:541")
      && state.dailyCount===2,"deduplication or second quota receipt failed"))return false
  AquaMind.dismiss()
  if(!root.check(state.dismissals===1 && !AquaMind.sayAutomatic("Over cap","reminder",now+18000000,"todo:542"),"dismissal or daily cap failed"))return false
  AquaMind.acknowledgeAutomatic()
  if(!root.check(state.unanswered===0 && state.dismissals===0 && state.dailyCount===2
      && !AquaMind.sayAutomatic("Quota remains","reminder",now+18000000,"todo:542"),"interaction reset the quota"))return false
  reset()
  Config.setNestedValue("abyss.companionMind.calendarRemindersEnabled",true)
  state.reminderKeys=["journal:540"]
  const eventStart=new Date(anchor.getTime()),eventEnd=new Date(anchor.getTime()+1800000)
  const liveEvent={id:"evt_live",title:"Weekly Planner Sync",recurrence:"none",
   occurrences:[{startDate:eventStart.toISOString(),endDate:eventEnd.toISOString(),notified:false}]}
  if(!root.check(AquaMind.sayAutomatic("Psst! Weekly Planner Sync · 12:00","reminder",now,"planner:evt_live:0:720","schedule")
      && AquaMind.activeReminderKey==="planner:evt_live:0:720"
      && !state.reminderKeys.includes("journal:540")
      && AquaMind.reconcileActiveReminder(anchor,[],[Object.assign({},liveEvent,{title:"Renamed Planner Sync"})])
      && AquaMind.text==="Psst! Renamed Planner Sync · 12:00","live Weekly Planner title reconciliation or legacy journal key purge failed"))return false
  if(!root.check(!AquaMind.reconcileActiveReminder(anchor,[],[])
      && AquaMind.text==="" && !AquaMind.automaticOffer && AquaMind.activeReminderKey==="","deleting Weekly Planner event retained stale active reminder"))return false
  Config.setNestedValue("abyss.companionMind.calendarRemindersEnabled",false)
  reset()
  if(!root.check(AquaMind.sayAutomatic("Revoked","reminder",now,"todo:543"),"revocation setup failed"))return false
  Config.setNestedValue("abyss.companionMind.proactiveEnabled",false)
  if(!root.check(!AquaMind.proactiveIdleEnabled && AquaMind.text==="" && !AquaMind.automaticOffer,"revoking consent retained automatic content"))return false
  Config.setNestedValue("abyss.companionMind.todoRemindersEnabled",false)
  reset();AquaMind.hostVisible=false
  return true
 }
 function checkHelperReplies(): bool {
  // Completion injection only: no helper process, provider or vault is used.
  const state=Persistent.states.aquaCheckIn
  const saved={history:AquaMind.history,journal:AquaMind.journal,date:state.date,
   mood:state.mood,energy:state.energy,loaded:AquaMind.historyLoaded,
   more:AquaMind.historyHasMore,loading:AquaMind.historyLoadingOlder,
   stage:AquaMind.checkInStage,checkDate:AquaMind.checkInDate,text:AquaMind.text,
   lastContext:AquaMind.lastContext,lastAttempt:AquaMind.lastContextAttempt}
  const userText="Explain <tool_call> literally.\nKeep my exact text."
  const held=[{id:7,role:"user",content:userText,createdMs:1,model:"fixture"}]
  const context=root.helperContext("held")
  const invalid=["null","[]","0","\"reply\"","{",
   JSON.stringify({ok:"false",result:root.helperContext("bad")}),
   JSON.stringify({ok:1,result:root.helperContext("bad")}),
   JSON.stringify({ok:true,result:null}),JSON.stringify({ok:true,result:[]}),
   JSON.stringify({ok:true,result:{}})]
  const badResults={
   context:[Object.assign({},context,{schedule:"oops"}),
    Object.assign({},context,{schedule:[null]}),Object.assign({},context,{mood:{private:"bytes"}})],
   history:[{messages:"oops",hasMore:false},{messages:[null],hasMore:false},
    {messages:[Object.assign({},held[0],{content:{private:"bytes"}})],hasMore:false}],
   history_append:[{userMessageId:0,assistantMessageId:1},
    {userMessageId:7,assistantMessageId:7},{userMessageId:"7",assistantMessageId:8}],
   history_clear:[{cleared:false},{cleared:"true"}],
   check_in:[{saved:"true",field:"mood",value:"good",date:AquaMind.today(),journalPath:"/fixture/journal.md"}]
  }
  for(const action of Object.keys(badResults)){
   const cases=invalid.concat(badResults[action].map(result=>JSON.stringify({ok:true,result:result})))
   for(const raw of cases){
    AquaMind.history=held.slice();AquaMind.journal=context
    AquaMind.historyLoadingOlder=true;AquaMind.historyClearPending=action==="history_clear"
    AquaMind.checkInStage="mood";AquaMind.checkInDate=AquaMind.today()
    state.date=AquaMind.today();state.mood="";state.energy=""
    AquaMind.errorMessage=""
    const serial=++AquaMind.epoch
    AquaMind.pending={serial:serial,action:action,automatic:false,
     request:{clientKey:"fixture",field:"mood",value:"good",date:AquaMind.today()}}
    AquaMind.busy=true
    try {AquaMind.completed(raw,0,serial)}
    catch(e){return root.check(false,"malformed helper threw for "+action)}
    if(!root.check(!AquaMind.busy && !AquaMind.pending && AquaMind.errorMessage.length>0
       && JSON.stringify(AquaMind.history)===JSON.stringify(held)
       && JSON.stringify(AquaMind.journal)===JSON.stringify(context)
       && state.mood==="" && state.energy==="" && AquaMind.checkInStage==="mood",
       "malformed helper changed data for "+action))return false
    if(!root.check(action!=="history" || !AquaMind.historyLoadingOlder,
       "malformed history left pagination busy"))return false
    if(!root.check(action!=="history_clear" || !AquaMind.historyClearPending,
       "malformed clear left deletion pending"))return false
   }
  }
  // A valid page must still recover, preserving literal user content.
  const serial=++AquaMind.epoch
  AquaMind.pending={serial:serial,action:"history",automatic:false,request:{beforeId:0}}
  AquaMind.busy=true
  AquaMind.completed(JSON.stringify({ok:true,result:{messages:held,hasMore:false}}),0,serial)
  if(!root.check(AquaMind.history[0]?.content===userText && !AquaMind.busy
      && AquaMind.historyLoaded && !AquaMind.historyLoadingOlder,
      "valid history did not recover after malformed helpers"))return false
  AquaMind.history=saved.history;AquaMind.journal=saved.journal
  state.date=saved.date;state.mood=saved.mood;state.energy=saved.energy
  AquaMind.historyLoaded=saved.loaded;AquaMind.historyHasMore=saved.more
  AquaMind.historyLoadingOlder=saved.loading;AquaMind.historyClearPending=false
  AquaMind.checkInStage=saved.stage;AquaMind.checkInDate=saved.checkDate;AquaMind.text=saved.text
  AquaMind.lastContext=saved.lastContext;AquaMind.lastContextAttempt=saved.lastAttempt
  AquaMind.errorMessage=""
  return true
 }
 function checkAutomaticCompletion(): bool {
  // Synthetic helper completion only: no vault, model, notification or timer
  // is invoked. The startup gate keeps deferred automatic offers dormant.
  Config.setNestedValues({"abyss.companionMind.obsidianEnabled":true,
   "abyss.companionMind.journalRemindersEnabled":true,"abyss.companionMind.proactiveEnabled":true})
  AquaMind.hostBlocked=false
  AquaMind.hostVisible=true;AquaMind.hostIdle=true
  AquaMind.conversationOpen=false;AquaMind.contextOpen=false
  for(const cadence of ["rare","occasional","regular","often"]){
   Config.setNestedValue("abyss.companionMind.proactive",cadence)
   const serial=++AquaMind.epoch
   AquaMind.journal=({schedule:[],marker:"before"})
   AquaMind.pending={serial:serial,action:"context",request:{},automatic:true}
   AquaMind.busy=true
   AquaMind.completed(JSON.stringify({ok:true,result:root.helperContext(cadence)}),0,serial)
   if(!root.check(!AquaMind.busy && !AquaMind.pending && AquaMind.journal.marker===cadence,
       "automatic helper discarded enabled cadence "+cadence))return false
  }
  for(const gate of ["manual","hidden","active","conversation","context"]){
   Config.setNestedValue("abyss.companionMind.proactive",gate==="manual" ? "manual" : "occasional")
   AquaMind.hostVisible=gate!=="hidden";AquaMind.hostIdle=gate!=="active"
   AquaMind.conversationOpen=gate==="conversation";AquaMind.contextOpen=gate==="context"
   const serial=++AquaMind.epoch
   AquaMind.journal=({schedule:[],marker:"held"})
   AquaMind.pending={serial:serial,action:"context",request:{},automatic:true}
   AquaMind.busy=true
   AquaMind.completed(JSON.stringify({ok:true,result:root.helperContext("late")}),0,serial)
   if(!root.check(!AquaMind.busy && !AquaMind.pending && AquaMind.journal.marker==="held",
       "automatic helper bypassed "+gate+" gate"))return false
  }
  // An explicit context request remains valid while its own UI is open.
  Config.setNestedValue("abyss.companionMind.proactive","manual")
  AquaMind.hostVisible=true;AquaMind.hostIdle=false
  AquaMind.conversationOpen=false;AquaMind.contextOpen=true
  const serial=++AquaMind.epoch
  AquaMind.pending={serial:serial,action:"context",request:{},automatic:false}
  AquaMind.busy=true
  AquaMind.completed(JSON.stringify({ok:true,result:root.helperContext("explicit")}),0,serial)
  if(!root.check(AquaMind.journal.marker==="explicit","explicit context request used automatic gates"))return false
  // Cadence revocation invalidates the original epoch before a late response.
  AquaMind.contextOpen=false;AquaMind.hostIdle=true
  Config.setNestedValue("abyss.companionMind.proactive","regular")
  if(!root.check(AquaMind.proactive==="regular" && AquaMind.proactiveIdleEnabled,
      "revocation fixture did not observe the enabled cadence"))return false
  const revoked=++AquaMind.epoch
  AquaMind.pending={serial:revoked,action:"context",request:{},automatic:true}
  AquaMind.busy=true
  // Give settings bindings and the late delivery separate event-loop turns,
  // matching an actual in-flight helper rather than coalesced test writes.
  Qt.callLater(()=>{
   Config.setNestedValue("abyss.companionMind.proactive","manual")
   Qt.callLater(()=>{
    if(!root.check(AquaMind.epoch>revoked && !AquaMind.busy && !AquaMind.pending,
        "cadence revocation did not cancel the pending automatic request"))return
    AquaMind.completed(JSON.stringify({ok:true,result:root.helperContext("revoked")}),0,revoked)
    if(!root.check(AquaMind.epoch>revoked && !AquaMind.busy && !AquaMind.pending
        && AquaMind.journal.marker==="explicit","late helper survived cadence revocation "+
        JSON.stringify({epoch:AquaMind.epoch,revoked:revoked,busy:AquaMind.busy,
          pending:!!AquaMind.pending,marker:AquaMind.journal.marker,
          cadence:AquaMind.proactive,enabled:AquaMind.proactiveIdleEnabled})))return
    AquaMind.hostVisible=false;AquaMind.hostIdle=false
    Config.setNestedValue("abyss.companionMind.obsidianEnabled",false)
    AquaMind.journal=({schedule:[],mood:"",energy:"",journalPath:""})
    root.cadenceComplete=true
   })
  })
  return true
 }
 function checkJournalReceipts(): bool {
  // Synthetic acknowledgements: no journal path is read or written and no
  // worker/model is invoked. The receipt must match the actual button request.
  const state=Persistent.states.aquaCheckIn
  const saved={date:state.date,mood:state.mood,energy:state.energy}
  const date=AquaMind.today(),path="/synthetic-vault/journal.md"
  const good={saved:true,field:"mood",value:"good",date:date,journalPath:path}
  const invalid=[Object.assign({},good,{field:"energy",value:"high"}),
   Object.assign({},good,{value:"great"}),Object.assign({},good,{field:"unknown"}),
   Object.assign({},good,{date:"1900-01-01"}),Object.assign({},good,{saved:false}),
   Object.assign({},good,{journalPath:null}),Object.assign({},good,{journalPath:""}),
   Object.assign({},good,{journalPath:"bad\npath"}),Object.assign({},good,{journalPath:123}),
   Object.assign({},good,{journalPath:"x".repeat(4097)}),null]
  for(const reply of invalid){
   state.date=date;state.mood="";state.energy=""
   AquaMind.checkInDate=date;AquaMind.checkInStage="mood"
   AquaMind.journal=({schedule:[],mood:"",energy:"",journalPath:"held"})
   AquaMind.errorMessage="";AquaMind.text=""
   const serial=++AquaMind.epoch
   AquaMind.pending={serial:serial,action:"check_in",automatic:false,
    request:{field:"mood",value:"good",date:date}}
   AquaMind.busy=true
   try {AquaMind.completed(JSON.stringify({ok:true,result:reply}),0,serial)}
   catch(e){return root.check(false,"invalid journal receipt threw")}
   if(!root.check(!AquaMind.busy && !AquaMind.pending && state.mood==="" && state.energy===""
       && AquaMind.journal.journalPath==="held" && AquaMind.checkInStage==="mood"
       && AquaMind.errorMessage.length>0,"invalid journal receipt advanced daily choices"))return false
  }
  for(const [field,value] of [["mood","good"],["energy","high"]]){
   const serial=++AquaMind.epoch
   AquaMind.pending={serial:serial,action:"check_in",automatic:false,request:{field:field,value:value,date:date}}
   AquaMind.busy=true
   AquaMind.completed(JSON.stringify({ok:true,result:{saved:true,field:field,value:value,date:date,journalPath:path}}),0,serial)
   if(!root.check(state[field]===value && AquaMind.journal.journalPath===path
       && AquaMind.checkInStage===(field==="mood" ? "energy" : ""),"matched journal receipt lost sequential choices"))return false
  }
  state.date=saved.date;state.mood=saved.mood;state.energy=saved.energy
  AquaMind.dismiss();AquaMind.journal=({schedule:[],mood:"",energy:"",journalPath:""})
  return true
 }
 FloatingWindow {visible:true;implicitWidth:360;implicitHeight:180;color:"#111820"}
 Timer {
  interval:100;running:true;repeat:true
  onTriggered:{
   if(++root.ticks>160){root.check(false,"requests did not settle "+JSON.stringify({step:root.step,
       configReady:Config.ready,packageDiagnostic:AlisCompanion.diagnostic}));return}
   if(!Config.ready || !AlisCompanion.available || !Persistent.ready)return
   if(root.step===0){
    if(!root.cadenceStarted){
     root.cadenceStarted=true
     if(!root.checkLiveOffers())return
     if(!root.checkHelperReplies())return
     if(!root.checkJournalReceipts())return
     if(!root.checkAutomaticCompletion())return
     return
    }
    if(!root.cadenceComplete)return
    if(!root.aiConfigured){
    Ai._initialized=true
    const originalPrompt=Config.options.ai.systemPrompt
    const substitutions=Ai.promptSubstitutions
    Config.setNestedValue("ai.systemPrompt","{DISTRO}/{DISTRO}|{DATETIME}|{WINDOWCLASS}|{DE}|$& literal")
    if(!root.check(Ai.systemPrompt===substitutions["{DISTRO}"]+"/"+substitutions["{DISTRO}"]+"|"+substitutions["{DATETIME}"]+"|"+substitutions["{WINDOWCLASS}"]+"|"+substitutions["{DE}"]+"|$& literal","live repeated context substitutions preserve literal text"))return
    Config.setNestedValue("ai.systemPrompt","")
    if(!root.check(Ai.systemPrompt==="","clearing the AI prompt leaves an empty typed prompt"))return
    Config.setNestedValue("ai.systemPrompt",originalPrompt)
    KeyringStorage.keyringData=({apiKeys:{fixture:"fixture-credential"}});KeyringStorage.loaded=true
    root.add("fixture-main","/ok");root.add("fixture-aqua","/ok")
    root.add("fixture-failure","/failure");root.add("fixture-slow","/slow")
    root.add("fixture-extra","/extra");root.add("fixture-tokens","/tokens");root.add("fixture-malformed","/malformed")
    root.add("fixture-controltag","/controltag");root.add("fixture-eof-tool","/eof-tool")
    root.add("fixture-mixed-meta","/mixed-meta");root.add("fixture-action-claim","/action-claim")
    root.add("fixture-forget-claim","/forget-claim")
    root.add("fixture-cloud","/cloud");Ai.models["fixture-cloud"].local=false
    root.add("fixture-responses","/responses");Ai.models["fixture-responses"].api_format="openai-response"
    root.add("fixture-spoof","/spoof");Ai.models["fixture-spoof"].endpoint="http://localhost@cloud.invalid/v1"
    root.add("fixture-verified","/local-verified")
    root.add("fixture-unverified","/unverified");Ai.models["fixture-unverified"].local_verified=false
    Ai.modelList=Object.keys(Ai.models)
    Ai.models["fixture-aqua"].requires_key=true
    Ai.models["fixture-aqua"].key_id="fixture";Ai.models["fixture-aqua"].auth_scheme="bearer"
    Ai.currentModelId="fixture-main";Ai.addMessage("Ordinary AI history","user")
    root.mainIDs=Ai.messageIDs.slice()
    if(!root.check(AquaMind.localOnly && !AquaMind.selectableModels.some(entry=>["fixture-cloud","fixture-spoof","fixture-unverified"].includes(entry.name)),"local default lists a remote/spoofed/unverified model"))return
    for(const id of ["fixture-cloud","fixture-spoof","fixture-unverified"]){
     Config.setNestedValue("abyss.companionMind.model",id)
     if(!root.check(!AquaMind.available && !AquaMind.sendMessage("Never send this fixture"),"local-only request accepted a cloud or spoofed model"))return
    }
    Config.setNestedValue("abyss.companionMind.model","")
    Ai.currentModelId="fixture-cloud"
    if(!root.check(AquaMind.model!=="fixture-cloud" && AquaMind.available && Ai.currentModelId==="fixture-cloud","automatic local selection modified the main AI model or fell back to cloud"))return
    Ai.currentModelId="fixture-main"
    Config.setNestedValues({"abyss.companionMind.model":"fixture-aqua",
     "abyss.companionMind.endpoint":"http://127.0.0.1:1",
     "abyss.companionMind.thinkingEffort":"high"})
    root.aiConfigured=true
    return
    }
    AquaMind.historyLoaded=true;AquaMind.openChat()
    if(!root.check(AquaMind.selectableModels.some(entry=>entry.name==="fixture-aqua"),"Aqua exposes the AI catalog"))return
    if(!root.check(AquaMind.sendMessage("Hello fixture"),"shared request accepted"))return
    root.step++
   }else if(root.step===1 && !AquaMind.busy){
    if(!root.check(AquaMind.history[AquaMind.history.length-1]?.content==="Fixture hello"
        && AquaMind.history[AquaMind.history.length-1]?.persisted,"shared reply is rendered and persisted "+JSON.stringify({
            status:AquaMind.connectionStatus,error:AquaMind.errorMessage,rows:AquaMind.history.length,
            pendingAction:AquaMind.pending?.action ?? "",model:AquaMind.model})))return
    if(!root.check(Ai.currentModelId==="fixture-main" && JSON.stringify(Ai.messageIDs)===JSON.stringify(root.mainIDs)
        && Ai.messageByID[root.mainIDs[0]].rawContent==="Ordinary AI history", "main AI conversation/model stay intact"))return
    Config.setNestedValue("abyss.companionMind.model","fixture-failure")
    if(!root.check(AquaMind.sendMessage("Fail fixture"),"failure request accepted"))return
    root.step++
   }else if(root.step===2 && !AquaMind.busy){
    if(!root.check(AquaMind.connectionStatus==="error" && AquaMind.errorMessage.includes("429"),"HTTP errors do not become successful replies"))return
    Config.setNestedValue("abyss.companionMind.model","fixture-slow")
    if(!root.check(AquaMind.sendMessage("Cancel fixture"),"cancellable request accepted"))return
    root.step++
   }else if(root.step===3){AquaMind.cancel();root.step++
   }else if(root.step===4 && !AquaMind.aiSession.busy){
    if(!root.check(!AquaMind.history.some(entry=>entry.content==="Cancel fixture"),"cancelled pending message is released"))return
    Config.setNestedValue("abyss.companionMind.model","")
    if(!root.check(AquaMind.model===Ai.currentModelId,"empty override follows the AI tab model"))return
    if(!root.check(AquaMind.sendMessage("Retry fixture"),"request works after cancellation"))return
    root.step++
   }else if(root.step===5 && !AquaMind.busy){
    if(!root.check(AquaMind.history[AquaMind.history.length-1]?.content==="Fixture hello" && Ai.messageIDs.length===root.mainIDs.length,"retry preserves both conversations"))return
    root.previousText=AquaMind.text
    root.assistantCount=AquaMind.history.filter(entry=>entry.role==="assistant").length
    Config.setNestedValue("abyss.companionMind.model",root.invalidModels[0])
    if(!root.check(AquaMind.sendMessage("Reject fixture 0"),"invalid-output request accepted"))return
    root.step=6
   }else if(root.step===6 && !AquaMind.busy){
    if(!root.check(AquaMind.connectionStatus==="error"
        && AquaMind.history.filter(entry=>entry.role==="assistant").length===root.assistantCount
        && AquaMind.text===root.previousText,"invalid model output was displayed or appended"))return
    if(++root.invalidIndex<root.invalidModels.length){
     Config.setNestedValue("abyss.companionMind.model",root.invalidModels[root.invalidIndex])
     if(!root.check(AquaMind.sendMessage("Reject fixture "+root.invalidIndex),"next invalid-output request accepted"))return
    }else{
     Config.setNestedValue("abyss.companionMind.model","")
     if(!root.check(AquaMind.sendMessage("Recovery fixture"),"valid request after rejected outputs"))return
     root.step=7
    }
   }else if(root.step===7 && !AquaMind.busy){
    if(!root.check(AquaMind.history[AquaMind.history.length-1]?.content==="Fixture hello"
        && AquaMind.history[AquaMind.history.length-1]?.persisted
        && Ai.messageIDs.length===root.mainIDs.length,"rejected outputs prevented valid recovery"))return
    root.previousText=AquaMind.text
    root.assistantCount=AquaMind.history.filter(entry=>entry.role==="assistant").length
    Config.setNestedValue("abyss.companionMind.model","fixture-slow")
    if(!root.check(AquaMind.sendMessage("Reconfigured fixture"),"same-ID replacement request accepted"))return
    root.replacedToken=String(AquaMind.pending.serial)
    Ai.models["fixture-slow"].endpoint=Quickshell.env("AI_FIXTURE_URL")+"/replacement"
    root.step=8
   }else if(root.step===8 && !AquaMind.aiSession.busy){
    AquaMind.completeAi(root.replacedToken,JSON.stringify({text:"Stale provider reply",expression:"happy"}),"")
    if(!root.check(!AquaMind.busy && !AquaMind.pending && AquaMind.text===root.previousText
        && AquaMind.history.filter(entry=>entry.role==="assistant").length===root.assistantCount
        && !AquaMind.history.some(entry=>entry.content==="Reconfigured fixture"),"same-ID provider change retained the old result"))return
    Config.setNestedValue("abyss.companionMind.model","fixture-responses")
    if(!root.check(AquaMind.available && AquaMind.sendMessage("Responses fixture"),"canonical local Responses protocol unavailable"))return
    root.step=9
   }else if(root.step===9 && !AquaMind.busy){
    if(!root.check(AquaMind.history[AquaMind.history.length-1]?.content==="Responses hello"
        && AquaMind.history[AquaMind.history.length-1]?.persisted,"Responses reply was not decoded and persisted"))return
    const slow=Ai.models["fixture-slow"]
    slow.endpoint=Quickshell.env("AI_FIXTURE_URL")+"/slow";slow.requires_key=true;slow.key_id="fixture"
    Config.setNestedValue("abyss.companionMind.model","fixture-slow")
    if(!root.check(AquaMind.sendMessage("Rotated credential fixture"),"credential rotation setup rejected"))return
    KeyringStorage.keyringData=({apiKeys:{fixture:"fixture-credential",unrelated:"changed"}})
    const unchangedIdentity=AquaMind.modelIdentity
    if(!root.check(AquaMind.busy,"unrelated credential change cancelled this provider"))return
    KeyringStorage.keyringData=({apiKeys:{fixture:"rotated-credential"}})
    const rotatedIdentity=AquaMind.modelIdentity
    if(!root.check(unchangedIdentity!==rotatedIdentity && !AquaMind.busy && !AquaMind.aiSession.busy
        && !AquaMind.history.some(entry=>entry.content==="Rotated credential fixture"),"active credential replacement retained a queued request"))return
    Config.setNestedValue("abyss.companionMind.localOnly",false)
    Config.setNestedValue("abyss.companionMind.model","fixture-cloud")
    if(!root.check(AquaMind.selectableModels.some(entry=>entry.name==="fixture-cloud") && AquaMind.sendMessage("Queued cloud fixture"),"explicit cloud opt-in is unavailable"))return
    Config.setNestedValue("abyss.companionMind.localOnly",true)
    if(!root.check(!AquaMind.busy && !AquaMind.aiSession.busy && !AquaMind.history.some(entry=>entry.content==="Queued cloud fixture"),"revoking cloud opt-in did not cancel the queued request"))return
    Config.setNestedValue("abyss.companionMind.model","fixture-verified")
    if(!root.check(AquaMind.sendMessage("Verified local fixture"),"verified local model did not start"))return
    root.step=10
   }else if(root.step===10 && !AquaMind.busy){
    if(!root.check(AquaMind.history[AquaMind.history.length-1]?.content==="Fixture hello"
        && AquaMind.history[AquaMind.history.length-1]?.persisted,"verified local reply was lost"))return
    root.assistantCount=AquaMind.history.filter(entry=>entry.role==="assistant").length
    if(!root.check(AquaMind.sendMessage("Never forward after remote replacement"),"fresh admission fixture did not start"))return
    root.step=11
   }else if(root.step===11 && !AquaMind.busy){
    if(!root.check(AquaMind.connectionStatus==="error"
        && AquaMind.history.filter(entry=>entry.role==="assistant").length===root.assistantCount
        && AquaMind.history.find(entry=>entry.content==="Never forward after remote replacement")?.failed===true,
        "remote replacement produced a reply or left an active request"))return
    root.add("fixture-instant","/v1/chat/completions")
    Ai.models["fixture-instant"].extraParams=({store:true,previous_response_id:"resp-foreign",
        max_output_tokens:65536,tools:[{type:"mcp",server_url:"https://must-not-contact.invalid"}]})
    Config.setNestedValue("abyss.companionMind.model","fixture-instant")
    AquaMind.setThinkingEffort("instant")
    if(!root.check(AquaMind.instantSupported && AquaMind.effectiveThinkingEffort==="instant"
        && AquaMind.sendMessage("Instant fixture"),"explicit local Instant mode unavailable"))return
    root.step=12
   }else if(root.step===12 && !AquaMind.busy){
    if(!root.check(AquaMind.history[AquaMind.history.length-1]?.content==="Responses hello"
        && AquaMind.aiSession.modelSnapshot.api_format==="openai-response"
        && Ai.models["fixture-instant"].api_format==="openai"
        && Ai.models["fixture-instant"].endpoint.endsWith("/v1/chat/completions"),
        "Instant mode changed the catalog or lost its isolated reply"))return
    AquaMind.setThinkingEffort("off")
    root.privacyOriginalCharacter=AquaMind.character
    root.privacyRows=AquaMind.history.slice()
    Config.setNestedValue("abyss.companionMind.model","fixture-forget-claim")
    AquaMind.character=root.privacyCases[0].character
    if(!root.check(AquaMind.sendMessage(root.privacyCases[0].prompt),"privacy request rejected"))return
    root.step=13
   }else if(root.step===13 && !AquaMind.busy){
    const last=AquaMind.history[AquaMind.history.length-1]
    if(!root.check(last?.persisted && last.content.startsWith("Nothing was deleted.")
       && last.content.includes("Clear conversation") && last.content.includes("memory controls")
       && AquaMind.text===last.content
       && JSON.stringify(AquaMind.history.slice(0,-2))===JSON.stringify(root.privacyRows),
       "privacy reply claimed deletion, lost storage or changed older history"))return
    if(++root.privacyIndex<root.privacyCases.length){
     root.privacyRows=AquaMind.history.slice()
     const next=root.privacyCases[root.privacyIndex];AquaMind.character=next.character
     if(!root.check(AquaMind.sendMessage(next.prompt),"next privacy request rejected"))return
     return
    }
    AquaMind.character=root.privacyOriginalCharacter
    Config.setNestedValue("abyss.companionMind.model","")
    console.info("AQUA_SHARED_AI_PASS catalog request history-isolation persistence HTTP-error cancel retry model-follow output-rejection recovery localDefault cloudBlock spoofBlock unverifiedBlock freshLocalAdmission remoteReplacementBlock localAutoFallback consentRevoke automaticCadences automaticGates cadenceRevocation malformedHelperRejection helperRecovery sameIdReplacement staleReply responses proactiveOptIn sourceConsent quietHours focusSuppression quotaReceipt dismissal dailyCap contentRevocation");Qt.quit()
   }
  }
 }
}
'''.replace('LOCAL_FINGERPRINTS',json.dumps(fingerprints)))
    with private_wayland(folder) as env:
        if env is None:
            print("SKIP: Aqua shared AI runtime requires a private Niri session")
            raise SystemExit(0)
        env["AI_FIXTURE_URL"]=f"http://127.0.0.1:{server.server_port}"
        env['CURL_HOME']=str(curl_home)
        env.update(http_proxy="http://127.0.0.1:1",HTTP_PROXY="http://127.0.0.1:1",
                   ALL_PROXY="http://127.0.0.1:1",all_proxy="http://127.0.0.1:1",NO_PROXY="",no_proxy="")
        result=run_qs(folder,env)
    output=result.stdout+result.stderr
    errors=["AQUA_SHARED_AI_FAIL","ReferenceError:","TypeError:","Binding loop","Unable to assign","is not a type"]
    if result.returncode or "AQUA_SHARED_AI_PASS" not in output or any(word in output for word in errors):
        print(output);raise SystemExit(1)
    for line in output.splitlines():
        if "AQUA_SHARED_AI_PASS" in line:print(line)
    ok=[row for path,row,headers in requests if path=="/ok"]
    assert len(ok)==3 and ok[0]["reasoning_effort"]=="high"
    assert all("Ordinary AI history" not in json.dumps(row) and "tools" not in row for row in ok)
    headers=[headers for path,row,headers in requests if path=="/ok"]
    assert headers[0].get("Authorization")=="Bearer fixture-credential"
    assert "Authorization" not in headers[1], "previous provider credential survived a keyless switch"
    assert len([row for path,row,headers in requests if path in ('/extra','/tokens','/malformed','/controltag','/eof-tool','/mixed-meta','/action-claim')])==7
    assert not any(path in ('/cloud','/spoof','/replacement','/unverified') for path,row,headers in requests), 'blocked or revoked request reached transport'
    assert len([row for path,row,headers in requests if path=='/local-verified'])==1, 'private input reached remote model replacement'
    assert not any(path=='/curlrc-leak' or 'X-Curlrc-Fixture' in headers for path,row,headers in requests), 'curl defaults modified private transport'
    responses=[row for path,row,headers in requests if path=='/responses']
    assert len(responses)==1 and 'input' in responses[0] and 'tools' not in responses[0]
    instant=[row for path,row,headers in requests if path=='/v1/responses']
    assert len(instant)==1 and instant[0]['reasoning']=={'effort':'none'}
    assert instant[0]['store'] is False and instant[0]['max_output_tokens']==256
    assert 'tools' not in instant[0] and 'previous_response_id' not in instant[0]
    assert any(row.get('role')=='assistant' for row in instant[0]['input']), 'Instant mode lost assistant history roles'
    with sqlite3.connect(Path(env['XDG_STATE_HOME'])/'inir/aqua/chat.sqlite3') as db:
        rows=db.execute('SELECT role,content FROM messages ORDER BY id').fetchall()
    original_rows=[('user','Hello fixture'),('assistant','Fixture hello'),
                  ('user','Retry fixture'),('assistant','Fixture hello'),
                  ('user','Recovery fixture'),('assistant','Fixture hello'),
                  ('user','Responses fixture'),('assistant','Responses hello'),
                  ('user','Verified local fixture'),('assistant','Fixture hello'),
                  ('user','Instant fixture'),('assistant','Responses hello')]
    assert rows[:len(original_rows)]==original_rows, 'rejected output entered persistent history'
    assert len(rows)==len(original_rows)+8
    expected_prompts=['Forget what I said before.']*2+['Hãy quên những gì tôi đã nói.']*2
    for index,prompt in enumerate(expected_prompts):
        assert rows[len(original_rows)+2*index]==('user',prompt)
        role,text=rows[len(original_rows)+2*index+1]
        assert role=='assistant' and text.startswith('Nothing was deleted.') and 'Clear conversation' in text
    assert len([row for path,row,headers in requests if path=='/forget-claim'])==4
finally:
    server.shutdown();server.server_close()
