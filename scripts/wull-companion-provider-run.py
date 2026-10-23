#!/usr/bin/env python3
"""Explicit synthetic voice matrix through the shared host AI text session.

Only a caller-selected, already available keyless loopback provider is allowed.
No server startup, model download, real history, vault or installed pet is used.
"""
import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def identity(root):
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=root,text=True).strip():
        raise ValueError('clean_sources_required')
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()


def endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http','https') or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError('loopback_endpoint_required')
    try:
        if not ipaddress.ip_address(parsed.hostname).is_loopback or not 1 <= (parsed.port or 80) <= 65535:
            raise ValueError('loopback_endpoint_required')
    except (ValueError,TypeError):
        raise ValueError('loopback_endpoint_required') from None
    return value


QML = r'''
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import qs.modules.common
import qs.services
import qs.services.deferred
import qs.services.ai
import "optional/hadanion/services"
import "optional/hadanion/services/WullReplyGuard.js" as ReplyGuard
ShellRoot {
 id:root
 property var cases: CASES
 property var descriptor: DESCRIPTOR
 property var attempts:[]
 property var responses:[]
 property int index:0
 property bool initialized:false
 property bool waiting:false
 property bool done:false
 property double started:0
 property double firstToken:-1
 property double began:Date.now()
 FileView {id:result;path:Quickshell.env("COMPANION_PROVIDER_RESULT")}
 FloatingWindow {visible:true;implicitWidth:280;implicitHeight:90;color:"#111820"}
 Connections {
  target:WullMind.aiSession?.message ?? null
  function onContentChanged(): void {
   if(root.waiting && root.firstToken<0 && String(WullMind.aiSession?.message?.content ?? "").length)
    root.firstToken=Date.now()-root.started
  }
 }
 function finish(): void {
  if(done)return
  done=true;WullMind.cancel();WullMind.dismiss()
  result.setText(JSON.stringify({attempts:attempts,responses:responses}))
  shutdown.start()
 }
 Timer {id:shutdown;interval:250;onTriggered:Qt.quit()}
 Timer {
  interval:40;running:true;repeat:true
  onTriggered:{
   if(root.done)return
   if(Date.now()-root.began>600000){root.finish();return}
   if(!Config.ready)return
   if(!root.initialized){
    Ai._initialized=true;AiProviderCatalog.initialized=true
    Ai.models=({});Ai.modelList=[]
    Ai.addModel("qualification-selected",root.descriptor)
    Ai.modelList=["qualification-selected"];Ai.currentModelId="qualification-selected"
    Config.setNestedValues({"policies.ai":2,"abyss.companionMind.model":"qualification-selected",
     "abyss.companionMind.localOnly":true,"abyss.companionMind.proactiveEnabled":false,
     "abyss.companionMind.proactive":"manual","abyss.companionMind.obsidianEnabled":false,
     "abyss.companionMind.memoryEnabled":false})
    WullMind.hostVisible=true;WullMind.hostIdle=false;WullMind.hostBlocked=true
    WullMind.historyLoaded=true;root.initialized=true
    return
   }
   if(root.waiting){
    if(WullMind.busy || WullMind.draining)return
    const job=root.cases[Math.floor(root.index/2)],character=root.index%2 ? "octo" : "aqua"
    const reply=WullMind.history.find(row=>row.role==="assistant")
    const parsed=ReplyGuard.parse(String(WullMind.aiSession?.message?.content ?? ""))
    const accepted=WullMind.connectionStatus==="ready" && !!reply && !reply.ephemeral
        && parsed.ok && parsed.text===reply.content
    root.attempts.push({case_id:job.id,character:character,accepted:accepted,
     firstContentMs:root.firstToken<0 ? null : root.firstToken,wallMs:Date.now()-root.started,
     failure:accepted ? "" : WullMind.connectionStatus})
    if(accepted)root.responses.push({case_id:job.id,character:character,
     response:{text:reply.content,expression:parsed.expression}})
    console.info("COMPANION_PROVIDER_PROGRESS",root.index+1,accepted ? "accepted" : "rejected")
    root.index++;root.waiting=false
   }
   if(root.index>=root.cases.length*2){root.finish();return}
   if(WullMind.busy || WullMind.draining)return
   const job=root.cases[Math.floor(root.index/2)]
   WullMind.history=[];WullMind.character=root.index%2 ? "octo" : "aqua"
   root.started=Date.now();root.firstToken=-1;root.waiting=true
   if(!WullMind.sendMessage(job.prompt)){
    root.attempts.push({case_id:job.id,character:WullMind.character,accepted:false,
     firstContentMs:null,wallMs:Date.now()-root.started,failure:"request_not_accepted"})
    root.index++;root.waiting=false
   }
  }
 }
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hadalis-root',type=Path,required=True)
    parser.add_argument('--endpoint',type=endpoint,required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}',args.model):
        raise ValueError('invalid_model_id')
    host=args.hadalis_root.resolve();output=args.output.resolve()
    if output.is_relative_to(ROOT) or output.is_relative_to(host):
        raise ValueError('output_inside_source_repository')
    sources={'companion':identity(ROOT),'host':identity(host)}
    fixture=ROOT/'scripts/fixtures/hadanion-voice-scenarios.json'
    cases=json.loads(fixture.read_text())['cases']
    if len(cases)!=14 or len({c['id'] for c in cases})!=14:
        raise ValueError('synthetic_matrix_changed')
    if any(not isinstance(c['prompt'],str) or not 0<len(c['prompt'])<=1200 for c in cases):
        raise ValueError('invalid_synthetic_prompt')
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    validator=module('provider_validation',ROOT/'scripts/validate.py')
    native=module('provider_native',host/'scripts/native_test_session.py')
    descriptor={'name':args.model,'model':args.model,'endpoint':args.endpoint,
        'local':True,'requires_key':False,'provider_id':'qualification-loopback','api_format':'openai',
        'capabilities':{'chat':'supported','reasoning':'unknown'},'extraParams':{'max_tokens':256}}
    with tempfile.TemporaryDirectory(prefix='companion-provider-') as temporary:
        folder=Path(temporary)
        work=validator.stage(host,folder/'host')
        (work/'shell.qml').write_text(QML.replace('CASES',json.dumps(cases,ensure_ascii=False))
            .replace('DESCRIPTOR',json.dumps(descriptor)))
        config=folder/'config/alis';config.mkdir(parents=True)
        (config/'config.json').write_bytes((host/'defaults/config.json').read_bytes())
        with native.private_wayland(folder) as env:
            if env is None:raise ValueError('private_wayland_unavailable')
            for key in ('QS_CONFIG_NAME','QS_CONFIG_PATH','QS_MANIFEST'):env.pop(key,None)
            env.update(COMPANION_PROVIDER_RESULT=str(output/'raw.json'),CURL_HOME=str(folder/'curl-home'),
                       PYTHONDONTWRITEBYTECODE='1',INIR_GGUF_ROOTS='[]',ALIS_GGUF_ROOTS='[]')
            (folder/'curl-home').mkdir()
            run=native.run_qs(work,env,timeout=620)
        (output/'native.log').write_text(run.stdout)
    report={'schema':1,'sources':sources,'provider':{'model':args.model,'endpoint':args.endpoint,
        'maxTokens':256,'thinking':'provider default (catalog support unknown)'},
        'fixtureSha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'exitCode':run.returncode,
        'expected':28,'accepted':0,'complete':False,'tokens':None,'gpuCostQualified':False,
        'ownerVoiceAcceptance':False}
    if (output/'raw.json').is_file():
        raw=json.loads((output/'raw.json').read_text())
        rows=raw['responses'];attempts=raw['attempts']
        (output/'responses.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
        (output/'attempts.json').write_text(json.dumps(attempts,indent=2)+'\n')
        report.update(accepted=len(rows),complete=run.returncode==0 and len(attempts)==28)
        if rows:
            evaluator=module('provider_evaluation',ROOT/'scripts/wull-companion-voice-eval.py')
            supplied,digest=evaluator.load_fixture(output/'responses.jsonl')
            (output/'evaluation.json').write_text(json.dumps(evaluator.evaluate(supplied,digest),indent=2)+'\n')
    report['sourceUnchanged']=sources=={'companion':identity(ROOT),'host':identity(host)}
    (output/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
    for path in output.iterdir():path.chmod(0o600)
    print(json.dumps(report))
    return 0 if report['complete'] and report['accepted']==28 and report['sourceUnchanged'] else 1


if __name__=='__main__':
    os.umask(0o077)
    raise SystemExit(main())
