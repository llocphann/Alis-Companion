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
import signal
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts/aqua') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts/aqua'))
import context_policy
import history_store


def resolve_provider_context_policy(provider, model, endpoint_url='', reserved_output=256, context_window=None):
    window = context_policy.resolve_route_context_window(
        provider=provider,
        model=model,
        endpoint=endpoint_url,
        explicit_context_window=context_window,
    )
    budget = context_policy.compute_input_budget(window, reserved_output=reserved_output)
    return {'contextWindow': window, 'inputBudget': budget, 'reservedOutput': int(reserved_output)}


def compact_provider_messages(messages, provider, model, endpoint_url='', reserved_output=256, context_window=None):
    return context_policy.compact_messages_for_route(
        messages,
        provider=provider,
        model=model,
        endpoint=endpoint_url,
        reserved_output=reserved_output,
        context_window=context_window,
    )


def finalize_cancelled_stream(user_prompt, partial_content, partial_reasoning='', model='', thread_id=None, persist=False):
    clean_content = str(partial_content or '').strip()
    clean_reasoning = str(partial_reasoning or '').strip()
    has_prefix = bool(clean_content or clean_reasoning)
    record = {
        'saved': False,
        'role': 'assistant',
        'content': clean_content or ('[Interrupted during reasoning]' if clean_reasoning else ''),
        'reasoning': clean_reasoning,
        'interrupted': has_prefix,
        'stopReason': 'cancelled' if has_prefix else '',
        'model': str(model or ''),
    }
    if persist and has_prefix and str(user_prompt or '').strip():
        res = history_store.append_cancelled_prefix(
            str(user_prompt),
            clean_content,
            clean_reasoning,
            model=str(model or ''),
            thread_id=thread_id,
        )
        record['saved'] = bool(res.get('saved', False))
        record['userMessageId'] = int(res.get('userMessageId', 0))
        record['assistantMessageId'] = int(res.get('assistantMessageId', 0))
    return record


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
        port=parsed.port if parsed.port is not None else (443 if parsed.scheme=='https' else 80)
        if not ipaddress.ip_address(parsed.hostname).is_loopback or not 1 <= port <= 65535:
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
import "optional/alis-companion/services"
import "optional/alis-companion/services/AquaReplyGuard.js" as ReplyGuard
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
  target:AquaMind.aiSession?.message ?? null
  function onContentChanged(): void {
   if(root.waiting && root.firstToken<0 && String(AquaMind.aiSession?.message?.content ?? "").length)
    root.firstToken=Date.now()-root.started
  }
 }
 function finish(): void {
  if(done)return
  done=true
  const partialContent=root.waiting ? String(AquaMind.aiSession?.message?.content ?? "").trim() : ""
  const partialRaw=root.waiting ? String(AquaMind.aiSession?.message?.rawContent ?? "").trim() : ""
  AquaMind.cancel();AquaMind.dismiss()
  const outObj={attempts:attempts,responses:responses}
  if(partialContent.length>0 || partialRaw.length>0){
   outObj.cancelledPrefix={interrupted:true,stopReason:"cancelled",content:partialContent,reasoning:partialRaw}
  }
  result.setText(JSON.stringify(outObj))
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
    Config.setNestedValues({"abyss.companionMind.model":"qualification-selected",
     "abyss.companionMind.localOnly":true,"abyss.companionMind.proactiveEnabled":false,
     "abyss.companionMind.proactive":"manual","abyss.companionMind.obsidianEnabled":false,
     "abyss.companionMind.memoryEnabled":false})
    AquaMind.hostVisible=true;AquaMind.hostIdle=false;AquaMind.hostBlocked=true
    AquaMind.historyLoaded=true;root.initialized=true
    return
   }
   if(root.waiting){
    if(AquaMind.busy || AquaMind.draining)return
    const job=root.cases[Math.floor(root.index/2)],character=root.index%2 ? "octo" : "aqua"
    const reply=AquaMind.history.find(row=>row.role==="assistant")
    const parsed=ReplyGuard.parse(String(AquaMind.aiSession?.message?.content ?? ""))
    const accepted=AquaMind.connectionStatus==="ready" && !!reply && !reply.ephemeral
        && parsed.ok && parsed.text===reply.content
    root.attempts.push({case_id:job.id,character:character,accepted:accepted,
     firstContentMs:root.firstToken<0 ? null : root.firstToken,wallMs:Date.now()-root.started,
     failure:accepted ? "" : AquaMind.connectionStatus})
    if(accepted)root.responses.push({case_id:job.id,character:character,
     response:{text:reply.content,expression:parsed.expression}})
    console.info("COMPANION_PROVIDER_PROGRESS",root.index+1,accepted ? "accepted" : "rejected")
    root.index++;root.waiting=false
   }
   if(root.index>=root.cases.length*2){root.finish();return}
   if(AquaMind.busy || AquaMind.draining)return
   const job=root.cases[Math.floor(root.index/2)]
   AquaMind.history=[];AquaMind.character=root.index%2 ? "octo" : "aqua"
   root.started=Date.now();root.firstToken=-1;root.waiting=true
   if(!AquaMind.sendMessage(job.prompt)){
    root.attempts.push({case_id:job.id,character:AquaMind.character,accepted:false,
     firstContentMs:null,wallMs:Date.now()-root.started,failure:"request_not_accepted"})
    root.index++;root.waiting=false
   }
  }
 }
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alis-root',type=Path,required=True)
    parser.add_argument('--endpoint',type=endpoint,required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--provider',choices=('lmstudio','ollama'),default='lmstudio')
    parser.add_argument('--context-window',type=int,default=None)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}',args.model):
        raise ValueError('invalid_model_id')
    host=args.alis_root.resolve();output=args.output.resolve()
    if output.is_relative_to(ROOT) or output.is_relative_to(host):
        raise ValueError('output_inside_source_repository')
    sources={'companion':identity(ROOT),'host':identity(host)}
    admission=module('provider_admission',host/'scripts/ai/local_provider.py')
    verified=admission.verify(args.provider,args.endpoint,args.model)
    fixture=ROOT/'scripts/fixtures/alis-companion-voice-scenarios.json'
    cases=json.loads(fixture.read_text())['cases']
    if len(cases)!=14 or len({c['id'] for c in cases})!=14:
        raise ValueError('synthetic_matrix_changed')
    if any(not isinstance(c['prompt'],str) or not 0<len(c['prompt'])<=1200 for c in cases):
        raise ValueError('invalid_synthetic_prompt')
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    validator=module('provider_validation',ROOT/'scripts/validate.py')
    native=module('provider_native',host/'scripts/native_test_session.py')
    ctx_meta=resolve_provider_context_policy(args.provider,args.model,args.endpoint,reserved_output=256,context_window=args.context_window)
    descriptor={'name':args.model,'model':args.model,'endpoint':args.endpoint,
        'local':True,'local_verified':True,'local_fingerprint':verified['fingerprint'],
        'requires_key':False,'provider_id':args.provider,'api_format':'openai',
        'context_window':ctx_meta['contextWindow'],
        'capabilities':{'chat':'supported','reasoning':'unknown'},'extraParams':{'max_tokens':256}}
    cancelled_by_signal=False
    prev_sigterm=signal.getsignal(signal.SIGTERM)
    prev_sigint=signal.getsignal(signal.SIGINT)
    def _mark_cancelled(_signum,_frame):
        nonlocal cancelled_by_signal
        cancelled_by_signal=True
    signal.signal(signal.SIGTERM,_mark_cancelled)
    signal.signal(signal.SIGINT,_mark_cancelled)
    run_returncode=130
    run_stdout=''
    try:
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
                           PYTHONDONTWRITEBYTECODE='1',ALIS_GGUF_ROOTS='[]')
                (folder/'curl-home').mkdir()
                run=native.run_qs(work,env,timeout=620)
                run_returncode=run.returncode
                run_stdout=run.stdout or ''
            (output/'native.log').write_text(run_stdout)
    except KeyboardInterrupt:
        cancelled_by_signal=True
    finally:
        signal.signal(signal.SIGTERM,prev_sigterm)
        signal.signal(signal.SIGINT,prev_sigint)
    report={'schema':1,'sources':sources,'provider':{'model':args.model,'endpoint':args.endpoint,
        'verifiedLocalMetadata':verified,
        'contextWindow':ctx_meta['contextWindow'],'inputBudget':ctx_meta['inputBudget'],
        'maxTokens':256,'thinking':'provider default (catalog support unknown)'},
        'fixtureSha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),'exitCode':run_returncode,
        'interrupted':bool(cancelled_by_signal),'stopReason':'cancelled' if cancelled_by_signal else '',
        'expected':28,'accepted':0,'complete':False,'tokens':None,'gpuCostQualified':False,
        'ownerVoiceAcceptance':False}
    if (output/'raw.json').is_file():
        raw=json.loads((output/'raw.json').read_text())
        rows=raw['responses'];attempts=raw['attempts']
        if isinstance(raw.get('cancelledPrefix'),dict):
            cp=raw['cancelledPrefix']
            report['cancelledPrefix']=finalize_cancelled_stream(
                '',
                cp.get('content',''),
                cp.get('reasoning',''),
                model=args.model,
                persist=False,
            )
            if report['cancelledPrefix'].get('interrupted'):
                report['interrupted']=True
                report['stopReason']='cancelled'
        (output/'responses.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
        (output/'attempts.json').write_text(json.dumps(attempts,indent=2)+'\n')
        report.update(accepted=len(rows),complete=(not report['interrupted']) and run_returncode==0 and len(attempts)==28)
        if rows:
            evaluator=module('provider_evaluation',ROOT/'scripts/aqua-companion-voice-eval.py')
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
