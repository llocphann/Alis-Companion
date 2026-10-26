#!/usr/bin/env python3
"""Synthetic voice-run protocol/identity/cancellation proof; no real inference."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
HOST=Path(os.environ.get("HADALIS_ROOT",ROOT.parent/"Hadalis"))
spec=importlib.util.spec_from_file_location("voice_run",ROOT/"scripts/aqua-companion-voice-run.py")
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
# Exercise read-only imports even if the outer validator suppresses bytecode.
sys.dont_write_bytecode=False
os.environ.pop('PYTHONDONTWRITEBYTECODE',None)


def git_commit(root):
    subprocess.run(["git","add","--all"],cwd=root,check=True,stdout=subprocess.DEVNULL)
    subprocess.run(["git","-c","user.name=Fixture","-c","user.email=fixture@example.invalid",
        "commit","-qm","synthetic fixture"],cwd=root,check=True)


FAKE='''import json,sys,os
from pathlib import Path
r=json.load(sys.stdin)
assert r['action']=='chat' and r['shareObsidian'] is False and r['persistHistory'] is False
assert r['history']==[] and r['thinkingEffort']=='off' and r['modelPath'].endswith('.gguf')
assert '--host-root' in sys.argv
mode=os.environ.get('ALIS_COMPANION_VOICE_FAKE','good')
if mode=='failure':print(json.dumps({'ok':False,'error':{'code':'disconnected'}}));sys.exit(0)
if mode=='mutate':
 with Path(r['modelPath']).open('ab') as f:f.write(b'x')
if mode=='remove':Path(r['modelPath']).unlink()
if mode=='dirty':Path(__file__).with_name('unexpected').write_text('concurrent change')
if mode=='cancel':
 Path(os.environ['ALIS_COMPANION_VOICE_MARKER']).write_text(str(os.getpid()))
 import time;time.sleep(30)
text='A little ripple of cheer.' if r['character']=='aqua' else 'Four arms. One tiny thought.'
if mode=='unsafe':text='<think>internal data</think>'
print(json.dumps({'ok':True,'result':{'source':'cloud' if mode=='cloud' else 'local',
 'model':r['model'],'thinkingEffort':'off','text':text,'expression':'happy',
 'userMessageId':0,'assistantMessageId':0,'evalCount':12}}))
'''


with tempfile.TemporaryDirectory(prefix="alis-companion-voice-run-") as temporary:
    private=Path(temporary);source=private/"source";host=private/"host"
    for repo in (source,host):
        repo.mkdir();subprocess.run(["git","init","-q",str(repo)],check=True)
    for name in ("scripts/aqua/reply_guard.py","scripts/aqua-companion-voice-eval.py",
                 "scripts/fixtures/alis-companion-voice-scenarios.json"):
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,path)
    helper=source/"scripts/aqua/local_mind.py";helper.write_text(FAKE)
    path=host/"scripts/ai/local_models.py";path.parent.mkdir(parents=True);shutil.copy2(HOST/"scripts/ai/local_models.py",path)
    git_commit(source);git_commit(host)
    model=private/"synthetic spaces '$.gguf"
    with model.open("wb") as stream:
        stream.write(struct.pack("<4sIQQ",b"GGUF",3,1,1));stream.truncate(21*1024*1024)
    runtime=private/"llama-server";runtime.write_text("#!/bin/sh\nexit 1\n");runtime.chmod(0o700)
    # This executable is never invoked: the owned fake helper proves the client
    # contract. The existing host supervisor has its own separate regression.
    baseline=runner.run(model,runtime,host,private/"good",source)
    assert baseline["helperRequests"]==28 and baseline["acceptedReplies"]==28
    assert baseline["status"]=="LOCAL_HELPER_RESPONSES_REVIEW_REQUIRED"
    rows=[json.loads(r) for r in (private/"good/responses.jsonl").read_text().splitlines()]
    assert len(rows)==28 and len({(r['case_id'],r['character']) for r in rows})==28
    assert all(p.stat().st_mode&0o077==0 for p in (private/"good",private/"good/result.json",private/"good/responses.jsonl"))
    assert baseline["formatMetrics"]["model_invocations"]==0
    original_model=model.read_bytes()
    for mode in ("failure","unsafe","cloud","mutate","remove","dirty"):
        os.environ["ALIS_COMPANION_VOICE_FAKE"]=mode
        output=private/mode
        report=runner.run(model,runtime,host,output,source)
        assert report["status"]=="INCONCLUSIVE_LOCAL_RUN" and report["helperRequests"]==1
        assert report["acceptedReplies"]==0 and not (output/"responses.jsonl").read_bytes()
        assert report['failureCode']
        assert '<think>' not in (output/"result.json").read_text()
        if mode in ('mutate','remove'):model.write_bytes(original_model)
        if mode=='dirty':helper.with_name('unexpected').unlink()
    os.environ.pop("ALIS_COMPANION_VOICE_FAKE",None)
    assert not list(source.rglob('__pycache__')) and not list(host.rglob('__pycache__'))
    # Kill only an owned client. Its finally block must reap its owned helper;
    # this proves client cleanup, not real llama-server inference/cleanup.
    client=private/'cancel-client.py'
    client.write_text('import runpy,signal\n'
        'def canceled(signum,frame):raise r["RunCanceled"]("run_canceled")\n'
        'signal.signal(signal.SIGTERM,canceled)\n'
        f'r=runpy.run_path({str(ROOT/"scripts/aqua-companion-voice-run.py")!r})\n'
        f'r["run"]({str(model)!r},{str(runtime)!r},{str(host)!r},{str(private/"cancel")!r},{str(source)!r})\n')
    marker=private/'helper.pid'
    process=subprocess.Popen([sys.executable,str(client)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env={**os.environ,'ALIS_COMPANION_VOICE_FAKE':'cancel','ALIS_COMPANION_VOICE_MARKER':str(marker)},start_new_session=True)
    try:
        deadline=time.monotonic()+5
        while not marker.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.01)
        assert marker.exists(),'owned helper did not start'
        helper_pid=int(marker.read_text())
        process.terminate()
        stdout,stderr=process.communicate(timeout=15)
        assert process.returncode==0,(stdout,stderr)
        report=json.loads((private/'cancel/result.json').read_text())
        assert report['status']=='INCONCLUSIVE_LOCAL_RUN' and report['failureCode']=='run_canceled'
        assert report['helperRequests']==1 and report['acceptedReplies']==0
        try:os.kill(helper_pid,0)
        except ProcessLookupError:pass
        else:raise AssertionError('owned fake helper remains after cancellation')
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
        if marker.exists():
            try:os.killpg(int(marker.read_text()),signal.SIGKILL)
            except ProcessLookupError:pass
    for model_path,runtime_path,output in ((private/'missing.gguf',runtime,private/'missing'),
            (model,private/'absent-runtime',private/'runtime-missing'),(model,runtime,private/'good'),
            (model,runtime,source/'evidence'),(model,runtime,host/'evidence')):
        try:runner.run(model_path,runtime_path,host,output,source)
        except ValueError:pass
        else:raise AssertionError('invalid preflight or reused evidence accepted')
        if output.name!='good':assert not output.exists()
    (source/'uncommitted').write_text('concurrent change')
    try:runner.run(model,runtime,host,private/'preflight-dirty',source)
    except ValueError:pass
    else:raise AssertionError('dirty sources accepted')
    assert not (private/'preflight-dirty').exists()
print('COMPANION_VOICE_RUN_SYNTHETIC_PASS paired28 localOnly noHistory noVault boundedFailure artifactChange sourceChange cancellation privateEvidence noRealInference')
