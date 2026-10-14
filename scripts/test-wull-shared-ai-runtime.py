#!/usr/bin/env python3
"""Wull uses the real Ai service with a private loopback protocol fixture."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, os, shutil, sqlite3, subprocess, tempfile, threading, time
from native_test_session import private_wayland, run_qs

ROOT=Path(__file__).resolve().parents[1]
if not shutil.which("qs"):
    print("SKIP: Wull shared AI runtime requires Quickshell")
    raise SystemExit(0)
requests=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append((self.path,data,dict(self.headers)))
        if self.path=="/slow":time.sleep(.7)
        failure=self.path=="/failure"
        self.send_response(429 if failure else 200)
        self.send_header("Content-Type","text/event-stream");self.end_headers()
        content={
            '/extra':json.dumps({'text':'Not public','tool_calls':[{'name':'open_file'}]}),
            '/tokens':json.dumps({'text':'<|channel|>analysis secret','expression':'happy'}),
            '/malformed':'{"text":"unfinished"',
        }.get(self.path,json.dumps({'text':'Fixture hello','expression':'happy'}))
        payload={"error":{"message":"fixture rate limit"}} if failure else {
            "choices":[{"delta":{"content":content},"finish_reason":"stop"}]}
        try:self.wfile.write(("data: "+json.dumps(payload)+"\n\ndata: [DONE]\n\n").encode())
        except (BrokenPipeError,ConnectionResetError):pass

server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 with tempfile.TemporaryDirectory(prefix="hadalis-wull-ai-") as name:
    folder=Path(name)
    for entry in ["modules","services","GlobalStates.qml","qmldir","assets","scripts","defaults","translations","optional"]:
        (folder/entry).symlink_to(ROOT/entry)
    config=folder/"config/illogical-impulse";config.mkdir(parents=True)
    shutil.copy(ROOT/"defaults/config.json",config/"config.json")
    (folder/"shell.qml").write_text(r'''
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import qs.modules.common
import qs.services
import "optional/hadanion/services"
import qs.services.deferred
ShellRoot {
 id:root
 property var optionalHost:Hadanion
 Component.onCompleted: Quickshell.watchFiles=false
 property int step:0
 property int ticks:0
 property var mainIDs:[]
 property var invalidModels:["fixture-extra","fixture-tokens","fixture-malformed"]
 property int invalidIndex:0
 property string previousText:""
 property int assistantCount:0
 property bool cadenceStarted:false
 property bool cadenceComplete:false
 function check(ok,message): bool {
  if(ok)return true
  console.error("WULL_SHARED_AI_FAIL",message);Qt.quit();return false
 }
 function add(id,path): void {
  Ai.addModel(id,{name:id,model:id,endpoint:Quickshell.env("AI_FIXTURE_URL")+path,
   local:true,requires_key:false,provider_id:"fixture",api_format:"openai",
   capabilities:{chat:"supported",reasoning:"supported"}})
 }
 function helperContext(marker): var {
  return {date:WullMind.today(),mood:"",energy:"",journalPath:"",schedule:[],marker:marker}
 }
 function checkHelperReplies(): bool {
  // Completion injection only: no helper process, provider or vault is used.
  const state=Persistent.states.wullCheckIn
  const saved={history:WullMind.history,journal:WullMind.journal,date:state.date,
   mood:state.mood,energy:state.energy,loaded:WullMind.historyLoaded,
   more:WullMind.historyHasMore,loading:WullMind.historyLoadingOlder,
   stage:WullMind.checkInStage,checkDate:WullMind.checkInDate,text:WullMind.text,
   lastContext:WullMind.lastContext,lastAttempt:WullMind.lastContextAttempt}
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
   check_in:[{saved:"true",field:"mood",value:"good",date:WullMind.today(),journalPath:"/fixture/journal.md"}]
  }
  for(const action of Object.keys(badResults)){
   const cases=invalid.concat(badResults[action].map(result=>JSON.stringify({ok:true,result:result})))
   for(const raw of cases){
    WullMind.history=held.slice();WullMind.journal=context
    WullMind.historyLoadingOlder=true;WullMind.historyClearPending=action==="history_clear"
    WullMind.checkInStage="mood";WullMind.checkInDate=WullMind.today()
    state.date=WullMind.today();state.mood="";state.energy=""
    WullMind.errorMessage=""
    const serial=++WullMind.epoch
    WullMind.pending={serial:serial,action:action,automatic:false,
     request:{clientKey:"fixture",field:"mood",value:"good",date:WullMind.today()}}
    WullMind.busy=true
    try {WullMind.completed(raw,0,serial)}
    catch(e){return root.check(false,"malformed helper threw for "+action)}
    if(!root.check(!WullMind.busy && !WullMind.pending && WullMind.errorMessage.length>0
       && JSON.stringify(WullMind.history)===JSON.stringify(held)
       && JSON.stringify(WullMind.journal)===JSON.stringify(context)
       && state.mood==="" && state.energy==="" && WullMind.checkInStage==="mood",
       "malformed helper changed data for "+action))return false
    if(!root.check(action!=="history" || !WullMind.historyLoadingOlder,
       "malformed history left pagination busy"))return false
    if(!root.check(action!=="history_clear" || !WullMind.historyClearPending,
       "malformed clear left deletion pending"))return false
   }
  }
  // A valid page must still recover, preserving literal user content.
  const serial=++WullMind.epoch
  WullMind.pending={serial:serial,action:"history",automatic:false,request:{beforeId:0}}
  WullMind.busy=true
  WullMind.completed(JSON.stringify({ok:true,result:{messages:held,hasMore:false}}),0,serial)
  if(!root.check(WullMind.history[0]?.content===userText && !WullMind.busy
      && WullMind.historyLoaded && !WullMind.historyLoadingOlder,
      "valid history did not recover after malformed helpers"))return false
  WullMind.history=saved.history;WullMind.journal=saved.journal
  state.date=saved.date;state.mood=saved.mood;state.energy=saved.energy
  WullMind.historyLoaded=saved.loaded;WullMind.historyHasMore=saved.more
  WullMind.historyLoadingOlder=saved.loading;WullMind.historyClearPending=false
  WullMind.checkInStage=saved.stage;WullMind.checkInDate=saved.checkDate;WullMind.text=saved.text
  WullMind.lastContext=saved.lastContext;WullMind.lastContextAttempt=saved.lastAttempt
  WullMind.errorMessage=""
  return true
 }
 function checkAutomaticCompletion(): bool {
  // Synthetic helper completion only: no vault, model, notification or timer
  // is invoked. The startup gate keeps deferred automatic offers dormant.
  Config.setNestedValue("abyss.companionMind.obsidianEnabled",false)
  WullMind.hostVisible=true;WullMind.hostIdle=true
  WullMind.conversationOpen=false;WullMind.contextOpen=false
  for(const cadence of ["rare","occasional","regular","often"]){
   Config.setNestedValue("abyss.companionMind.proactive",cadence)
   const serial=++WullMind.epoch
   WullMind.journal=({schedule:[],marker:"before"})
   WullMind.pending={serial:serial,action:"context",request:{},automatic:true}
   WullMind.busy=true
   WullMind.completed(JSON.stringify({ok:true,result:root.helperContext(cadence)}),0,serial)
   if(!root.check(!WullMind.busy && !WullMind.pending && WullMind.journal.marker===cadence,
       "automatic helper discarded enabled cadence "+cadence))return false
  }
  for(const gate of ["manual","hidden","active","conversation","context"]){
   Config.setNestedValue("abyss.companionMind.proactive",gate==="manual" ? "manual" : "occasional")
   WullMind.hostVisible=gate!=="hidden";WullMind.hostIdle=gate!=="active"
   WullMind.conversationOpen=gate==="conversation";WullMind.contextOpen=gate==="context"
   const serial=++WullMind.epoch
   WullMind.journal=({schedule:[],marker:"held"})
   WullMind.pending={serial:serial,action:"context",request:{},automatic:true}
   WullMind.busy=true
   WullMind.completed(JSON.stringify({ok:true,result:root.helperContext("late")}),0,serial)
   if(!root.check(!WullMind.busy && !WullMind.pending && WullMind.journal.marker==="held",
       "automatic helper bypassed "+gate+" gate"))return false
  }
  // An explicit context request remains valid while its own UI is open.
  Config.setNestedValue("abyss.companionMind.proactive","manual")
  WullMind.hostVisible=true;WullMind.hostIdle=false
  WullMind.conversationOpen=false;WullMind.contextOpen=true
  const serial=++WullMind.epoch
  WullMind.pending={serial:serial,action:"context",request:{},automatic:false}
  WullMind.busy=true
  WullMind.completed(JSON.stringify({ok:true,result:root.helperContext("explicit")}),0,serial)
  if(!root.check(WullMind.journal.marker==="explicit","explicit context request used automatic gates"))return false
  // Cadence revocation invalidates the original epoch before a late response.
  WullMind.contextOpen=false;WullMind.hostIdle=true
  Config.setNestedValue("abyss.companionMind.proactive","regular")
  if(!root.check(WullMind.proactive==="regular" && WullMind.proactiveIdleEnabled,
      "revocation fixture did not observe the enabled cadence"))return false
  const revoked=++WullMind.epoch
  WullMind.pending={serial:revoked,action:"context",request:{},automatic:true}
  WullMind.busy=true
  // Give settings bindings and the late delivery separate event-loop turns,
  // matching an actual in-flight helper rather than coalesced test writes.
  Qt.callLater(()=>{
   Config.setNestedValue("abyss.companionMind.proactive","manual")
   Qt.callLater(()=>{
    if(!root.check(WullMind.epoch>revoked && !WullMind.busy && !WullMind.pending,
        "cadence revocation did not cancel the pending automatic request"))return
    WullMind.completed(JSON.stringify({ok:true,result:root.helperContext("revoked")}),0,revoked)
    if(!root.check(WullMind.epoch>revoked && !WullMind.busy && !WullMind.pending
        && WullMind.journal.marker==="explicit","late helper survived cadence revocation "+
        JSON.stringify({epoch:WullMind.epoch,revoked:revoked,busy:WullMind.busy,
          pending:!!WullMind.pending,marker:WullMind.journal.marker,
          cadence:WullMind.proactive,enabled:WullMind.proactiveIdleEnabled})))return
    WullMind.hostVisible=false;WullMind.hostIdle=false
    WullMind.journal=({schedule:[],mood:"",energy:"",journalPath:""})
    root.cadenceComplete=true
   })
  })
  return true
 }
 function checkJournalReceipts(): bool {
  // Synthetic acknowledgements: no journal path is read or written and no
  // worker/model is invoked. The receipt must match the actual button request.
  const state=Persistent.states.wullCheckIn
  const saved={date:state.date,mood:state.mood,energy:state.energy}
  const date=WullMind.today(),path="/synthetic-vault/journal.md"
  const good={saved:true,field:"mood",value:"good",date:date,journalPath:path}
  const invalid=[Object.assign({},good,{field:"energy",value:"high"}),
   Object.assign({},good,{value:"great"}),Object.assign({},good,{field:"unknown"}),
   Object.assign({},good,{date:"1900-01-01"}),Object.assign({},good,{saved:false}),
   Object.assign({},good,{journalPath:null}),Object.assign({},good,{journalPath:""}),
   Object.assign({},good,{journalPath:"bad\npath"}),Object.assign({},good,{journalPath:123}),
   Object.assign({},good,{journalPath:"x".repeat(4097)}),null]
  for(const reply of invalid){
   state.date=date;state.mood="";state.energy=""
   WullMind.checkInDate=date;WullMind.checkInStage="mood"
   WullMind.journal=({schedule:[],mood:"",energy:"",journalPath:"held"})
   WullMind.errorMessage="";WullMind.text=""
   const serial=++WullMind.epoch
   WullMind.pending={serial:serial,action:"check_in",automatic:false,
    request:{field:"mood",value:"good",date:date}}
   WullMind.busy=true
   try {WullMind.completed(JSON.stringify({ok:true,result:reply}),0,serial)}
   catch(e){return root.check(false,"invalid journal receipt threw")}
   if(!root.check(!WullMind.busy && !WullMind.pending && state.mood==="" && state.energy===""
       && WullMind.journal.journalPath==="held" && WullMind.checkInStage==="mood"
       && WullMind.errorMessage.length>0,"invalid journal receipt advanced daily choices"))return false
  }
  for(const [field,value] of [["mood","good"],["energy","high"]]){
   const serial=++WullMind.epoch
   WullMind.pending={serial:serial,action:"check_in",automatic:false,request:{field:field,value:value,date:date}}
   WullMind.busy=true
   WullMind.completed(JSON.stringify({ok:true,result:{saved:true,field:field,value:value,date:date,journalPath:path}}),0,serial)
   if(!root.check(state[field]===value && WullMind.journal.journalPath===path
       && WullMind.checkInStage===(field==="mood" ? "energy" : ""),"matched journal receipt lost sequential choices"))return false
  }
  state.date=saved.date;state.mood=saved.mood;state.energy=saved.energy
  WullMind.dismiss();WullMind.journal=({schedule:[],mood:"",energy:"",journalPath:""})
  return true
 }
 FloatingWindow {visible:true;implicitWidth:360;implicitHeight:180;color:"#111820"}
 Timer {
  interval:100;running:true;repeat:true
  onTriggered:{
   if(++root.ticks>160){root.check(false,"requests did not settle");return}
   if(!Config.ready || !Hadanion.available)return
   if(root.step===0){
    if(!root.cadenceStarted){
     root.cadenceStarted=true
     if(!root.checkHelperReplies())return
     if(!root.checkJournalReceipts())return
     if(!root.checkAutomaticCompletion())return
     return
    }
    if(!root.cadenceComplete)return
    Ai._initialized=true
    const originalPrompt=Config.options.ai.systemPrompt
    const substitutions=Ai.promptSubstitutions
    Config.setNestedValue("ai.systemPrompt","{DISTRO}/{DISTRO}|{DATETIME}|{WINDOWCLASS}|{DE}|$& literal")
    if(!root.check(Ai.systemPrompt===substitutions["{DISTRO}"]+"/"+substitutions["{DISTRO}"]+"|"+substitutions["{DATETIME}"]+"|"+substitutions["{WINDOWCLASS}"]+"|"+substitutions["{DE}"]+"|$& literal","live repeated context substitutions preserve literal text"))return
    Config.setNestedValue("ai.systemPrompt","")
    if(!root.check(Ai.systemPrompt==="","clearing the AI prompt leaves an empty typed prompt"))return
    Config.setNestedValue("ai.systemPrompt",originalPrompt)
    KeyringStorage.keyringData=({apiKeys:{fixture:"fixture-credential"}});KeyringStorage.loaded=true
    root.add("fixture-main","/ok");root.add("fixture-wull","/ok")
    root.add("fixture-failure","/failure");root.add("fixture-slow","/slow")
    root.add("fixture-extra","/extra");root.add("fixture-tokens","/tokens");root.add("fixture-malformed","/malformed")
    root.add("fixture-cloud","/cloud");Ai.models["fixture-cloud"].local=false
    root.add("fixture-spoof","/spoof");Ai.models["fixture-spoof"].endpoint="http://localhost@cloud.invalid/v1"
    Ai.modelList=Object.keys(Ai.models)
    Ai.models["fixture-wull"].requires_key=true
    Ai.models["fixture-wull"].key_id="fixture";Ai.models["fixture-wull"].auth_scheme="bearer"
    Ai.currentModelId="fixture-main";Ai.addMessage("Ordinary AI history","user")
    root.mainIDs=Ai.messageIDs.slice()
    if(!root.check(WullMind.localOnly && !WullMind.selectableModels.some(entry=>["fixture-cloud","fixture-spoof"].includes(entry.name)),"local default lists a remote/spoofed model"))return
    for(const id of ["fixture-cloud","fixture-spoof"]){
     Config.setNestedValue("abyss.companionMind.model",id)
     if(!root.check(!WullMind.available && !WullMind.sendMessage("Never send this fixture"),"local-only request accepted a cloud or spoofed model"))return
    }
    Config.setNestedValue("abyss.companionMind.model","")
    Ai.currentModelId="fixture-cloud"
    if(!root.check(WullMind.model!=="fixture-cloud" && WullMind.available && Ai.currentModelId==="fixture-cloud","automatic local selection modified the main AI model or fell back to cloud"))return
    Ai.currentModelId="fixture-main"
    Config.setNestedValues({"abyss.companionMind.model":"fixture-wull",
     "abyss.companionMind.endpoint":"http://127.0.0.1:1",
     "abyss.companionMind.thinkingEffort":"high"})
    WullMind.historyLoaded=true;WullMind.openChat()
    if(!root.check(WullMind.selectableModels.some(entry=>entry.name==="fixture-wull"),"Wull exposes the AI catalog"))return
    if(!root.check(WullMind.sendMessage("Hello fixture"),"shared request accepted"))return
    root.step++
   }else if(root.step===1 && !WullMind.busy){
    if(!root.check(WullMind.history[WullMind.history.length-1]?.content==="Fixture hello"
        && WullMind.history[WullMind.history.length-1]?.persisted,"shared reply is rendered and persisted"))return
    if(!root.check(Ai.currentModelId==="fixture-main" && JSON.stringify(Ai.messageIDs)===JSON.stringify(root.mainIDs)
        && Ai.messageByID[root.mainIDs[0]].rawContent==="Ordinary AI history", "main AI conversation/model stay intact"))return
    Config.setNestedValue("abyss.companionMind.model","fixture-failure")
    if(!root.check(WullMind.sendMessage("Fail fixture"),"failure request accepted"))return
    root.step++
   }else if(root.step===2 && !WullMind.busy){
    if(!root.check(WullMind.connectionStatus==="error" && WullMind.errorMessage.includes("429"),"HTTP errors do not become successful replies"))return
    Config.setNestedValue("abyss.companionMind.model","fixture-slow")
    if(!root.check(WullMind.sendMessage("Cancel fixture"),"cancellable request accepted"))return
    root.step++
   }else if(root.step===3){WullMind.cancel();root.step++
   }else if(root.step===4 && !WullMind.aiSession.busy){
    if(!root.check(!WullMind.history.some(entry=>entry.content==="Cancel fixture"),"cancelled pending message is released"))return
    Config.setNestedValue("abyss.companionMind.model","")
    if(!root.check(WullMind.model===Ai.currentModelId,"empty override follows the AI tab model"))return
    if(!root.check(WullMind.sendMessage("Retry fixture"),"request works after cancellation"))return
    root.step++
   }else if(root.step===5 && !WullMind.busy){
    if(!root.check(WullMind.history[WullMind.history.length-1]?.content==="Fixture hello" && Ai.messageIDs.length===root.mainIDs.length,"retry preserves both conversations"))return
    root.previousText=WullMind.text
    root.assistantCount=WullMind.history.filter(entry=>entry.role==="assistant").length
    Config.setNestedValue("abyss.companionMind.model",root.invalidModels[0])
    if(!root.check(WullMind.sendMessage("Reject fixture 0"),"invalid-output request accepted"))return
    root.step=6
   }else if(root.step===6 && !WullMind.busy){
    if(!root.check(WullMind.connectionStatus==="error"
        && WullMind.history.filter(entry=>entry.role==="assistant").length===root.assistantCount
        && WullMind.text===root.previousText,"invalid model output was displayed or appended"))return
    if(++root.invalidIndex<root.invalidModels.length){
     Config.setNestedValue("abyss.companionMind.model",root.invalidModels[root.invalidIndex])
     if(!root.check(WullMind.sendMessage("Reject fixture "+root.invalidIndex),"next invalid-output request accepted"))return
    }else{
     Config.setNestedValue("abyss.companionMind.model","")
     if(!root.check(WullMind.sendMessage("Recovery fixture"),"valid request after rejected outputs"))return
     root.step=7
    }
   }else if(root.step===7 && !WullMind.busy){
    if(!root.check(WullMind.history[WullMind.history.length-1]?.content==="Fixture hello"
        && WullMind.history[WullMind.history.length-1]?.persisted
        && Ai.messageIDs.length===root.mainIDs.length,"rejected outputs prevented valid recovery"))return
    Config.setNestedValue("abyss.companionMind.localOnly",false)
    Config.setNestedValue("abyss.companionMind.model","fixture-cloud")
    if(!root.check(WullMind.selectableModels.some(entry=>entry.name==="fixture-cloud") && WullMind.sendMessage("Queued cloud fixture"),"explicit cloud opt-in is unavailable"))return
    Config.setNestedValue("abyss.companionMind.localOnly",true)
    if(!root.check(!WullMind.busy && !WullMind.aiSession.busy && !WullMind.history.some(entry=>entry.content==="Queued cloud fixture"),"revoking cloud opt-in did not cancel the queued request"))return
    Config.setNestedValue("abyss.companionMind.model","")
    console.info("WULL_SHARED_AI_PASS catalog request history-isolation persistence HTTP-error cancel retry model-follow output-rejection recovery localDefault cloudBlock spoofBlock localAutoFallback consentRevoke automaticCadences automaticGates cadenceRevocation malformedHelperRejection helperRecovery");Qt.quit()
   }
  }
 }
}
''')
    with private_wayland(folder) as env:
        if env is None:
            print("SKIP: Wull shared AI runtime requires a private Niri session")
            raise SystemExit(0)
        env["AI_FIXTURE_URL"]=f"http://127.0.0.1:{server.server_port}"
        env.update(http_proxy="http://127.0.0.1:1",HTTP_PROXY="http://127.0.0.1:1",
                   ALL_PROXY="http://127.0.0.1:1",all_proxy="http://127.0.0.1:1",NO_PROXY="",no_proxy="")
        result=run_qs(folder,env)
    output=result.stdout+result.stderr
    errors=["WULL_SHARED_AI_FAIL","ReferenceError:","TypeError:","Binding loop","Unable to assign","is not a type"]
    if result.returncode or "WULL_SHARED_AI_PASS" not in output or any(word in output for word in errors):
        print(output);raise SystemExit(1)
    for line in output.splitlines():
        if "WULL_SHARED_AI_PASS" in line:print(line)
    ok=[row for path,row,headers in requests if path=="/ok"]
    assert len(ok)==3 and ok[0]["reasoning_effort"]=="high"
    assert all("Ordinary AI history" not in json.dumps(row) and "tools" not in row for row in ok)
    headers=[headers for path,row,headers in requests if path=="/ok"]
    assert headers[0].get("Authorization")=="Bearer fixture-credential"
    assert "Authorization" not in headers[1], "previous provider credential survived a keyless switch"
    assert len([row for path,row,headers in requests if path in ('/extra','/tokens','/malformed')])==3
    assert not any(path in ('/cloud','/spoof') for path,row,headers in requests), 'blocked or revoked request reached transport'
    with sqlite3.connect(Path(env['XDG_STATE_HOME'])/'inir/wull/chat.sqlite3') as db:
        rows=db.execute('SELECT role,content FROM messages ORDER BY id').fetchall()
    assert rows==[('user','Hello fixture'),('assistant','Fixture hello'),
                  ('user','Retry fixture'),('assistant','Fixture hello'),
                  ('user','Recovery fixture'),('assistant','Fixture hello')], 'rejected output entered persistent history'
finally:
    server.shutdown();server.server_close()
