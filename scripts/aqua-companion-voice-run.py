#!/usr/bin/env python3
"""Explicit, bounded GGUF Companion voice qualification client.

Runs only the 14 checked-in synthetic scenarios, matched for Aqua and Octo.
Uses the existing one-shot local_mind and Alis GGUF supervisor; no model
download, alternate backend, cloud, user history, vault or desktop activation.
Accepted responses stay in a new private directory for human review. A report
does not independently establish real inference, voice quality or GPU costs.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MAX_MODEL = 16 * 1024**3
MAX_HELPER_REPLY = 16384
FAILURE_CODES = frozenset((
    "clean_sources_required", "output_already_exists", "output_inside_source_repository",
    "selected_gguf_missing_or_incomplete",
    "selected_llama_server_missing", "synthetic_matrix_changed", "invalid_synthetic_prompt",
    "artifact_changed_during_hash", "artifact_changed_during_run", "matrix_deadline_exceeded",
    "helper_protocol_failed", "local_request_not_accepted", "unexpected_helper_route_or_history",
    "invalid_public_reply", "internal_protocol", "empty_text", "invalid_text",
    "invalid_response_object", "run_canceled", "source_changed_during_run",
))


class RunCanceled(Exception):
    """Not InterruptedError: selectors may swallow that as a syscall retry."""


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(value)
    finally:
        sys.dont_write_bytecode = previous
    return value


def failure_code(error):
    """Only client/guard constants enter receipts; never raw model/error prose."""
    message = str(error)
    return message if message in FAILURE_CODES else type(error).__name__


def identity(root):
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, text=True)
    if dirty:
        raise ValueError("clean_sources_required")
    return sha


def fingerprint(path):
    stat = path.stat()
    return (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            result.update(data)
    return result.hexdigest()


def call_helper(source, host, payload):
    """The existing helper owns model startup, global locking and cleanup."""
    process = subprocess.Popen([sys.executable,str(source/"scripts/aqua/local_mind.py"),
        "--host-root",str(host)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"},start_new_session=True)
    try:
        raw,_ = process.communicate(json.dumps(payload,separators=(",",":")).encode()+b"\n",timeout=90)
        if process.returncode or len(raw)>MAX_HELPER_REPLY:
            raise ValueError("helper_protocol_failed")
        value = json.loads(raw)
        if not isinstance(value,dict) or value.get("ok") is not True:
            raise ValueError("local_request_not_accepted")
        return value["result"]
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait(timeout=3)


def run(model, runtime, host, output, source=ROOT):
    model,runtime,host,output,source = [Path(p).expanduser().resolve() for p in (model,runtime,host,output,source)]
    if output.exists():
        raise ValueError("output_already_exists")
    if output.is_relative_to(source) or output.is_relative_to(host):
        raise ValueError("output_inside_source_repository")
    sources = {"Alis-Companion":identity(source),"Alis":identity(host)}
    scanner = module("voice_local_models",host/"scripts/ai/local_models.py")
    if not model.is_file() or model.suffix.lower()!=".gguf" or model.stat().st_size>MAX_MODEL or not scanner.valid_file(model):
        raise ValueError("selected_gguf_missing_or_incomplete")
    if runtime.name!="llama-server" or not runtime.is_file() or not os.access(runtime,os.X_OK):
        raise ValueError("selected_llama_server_missing")
    guard = module("voice_reply_guard",source/"scripts/aqua/reply_guard.py")
    evaluator = module("voice_evaluator",source/"scripts/aqua-companion-voice-eval.py")
    fixture = source/"scripts/fixtures/alis-companion-voice-scenarios.json"
    cases = json.loads(fixture.read_text())["cases"]
    if len(cases)!=14 or len({c["id"] for c in cases})!=14:
        raise ValueError("synthetic_matrix_changed")
    if any(not isinstance(c["prompt"],str) or not 0<len(c["prompt"])<=1200 for c in cases):
        raise ValueError("invalid_synthetic_prompt")
    pinned = {"model":fingerprint(model),"runtime":fingerprint(runtime)}
    model_sha,runtime_sha = digest(model),digest(runtime)
    if fingerprint(model)!=pinned["model"] or fingerprint(runtime)!=pinned["runtime"]:
        raise ValueError("artifact_changed_during_hash")
    model_id = "gguf:"+model_sha[:20]
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    responses = output/"responses.jsonl"
    rows=[];attempts=[];failure=None;code=None;started=time.monotonic()
    # Stop quickly on transport/protocol failure rather than repeatedly loading
    # an unavailable model or retrying a consumed sample.
    try:
        with responses.open("x",encoding="utf-8") as stream:
            responses.chmod(0o600)
            for case in cases:
                for character in ("aqua","octo"):
                    if time.monotonic()-started>600:
                        raise ValueError("matrix_deadline_exceeded")
                    if fingerprint(model)!=pinned["model"] or fingerprint(runtime)!=pinned["runtime"]:
                        raise ValueError("artifact_changed_during_run")
                    if identity(source)!=sources["Alis-Companion"] or identity(host)!=sources["Alis"]:
                        raise ValueError("source_changed_during_run")
                    request = {"action":"chat","model":model_id,"modelPath":str(model),
                        "runtimePath":str(runtime),"character":character,"prompt":case["prompt"],
                        "thinkingEffort":"off","history":[],"persistHistory":False,"shareObsidian":False}
                    before=time.monotonic()
                    item={"case_id":case["id"],"character":character,"accepted":False}
                    attempts.append(item)
                    answer=call_helper(source,host,request)
                    if fingerprint(model)!=pinned["model"] or fingerprint(runtime)!=pinned["runtime"]:
                        raise ValueError("artifact_changed_during_run")
                    if identity(source)!=sources["Alis-Companion"] or identity(host)!=sources["Alis"]:
                        raise ValueError("source_changed_during_run")
                    if (not isinstance(answer,dict) or answer.get("source")!="local" or
                            answer.get("model")!=model_id or answer.get("thinkingEffort")!="off" or
                            answer.get("userMessageId")!=0 or answer.get("assistantMessageId")!=0):
                        raise ValueError("unexpected_helper_route_or_history")
                    if (not isinstance(answer.get("expression"),str) or answer["expression"] not in guard.EXPRESSIONS or
                            not isinstance(answer.get("text"),str) or len(answer["text"])>420):
                        raise ValueError("invalid_public_reply")
                    reply=guard.public_reply({"text":answer["text"],"expression":answer["expression"]})
                    row={"case_id":case["id"],"character":character,"response":reply}
                    stream.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n");stream.flush()
                    rows.append(row)
                    count=answer.get("evalCount")
                    item.update(accepted=True,wallSecondsIncludingStartup=round(time.monotonic()-before,6),
                        serverReportedCompletionTokens=count if isinstance(count,int) and not isinstance(count,bool) and 0<count<=1000000 else None)
    except (OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired,RunCanceled,KeyboardInterrupt) as error:
        failure,code=type(error).__name__,failure_code(error)
    try:
        if fingerprint(model)!=pinned["model"] or fingerprint(runtime)!=pinned["runtime"]:
            failure,code="ArtifactChanged","artifact_changed_during_run"
    except OSError:
        failure,code="ArtifactUnavailable","artifact_unavailable_after_run"
    try:
        if identity(source)!=sources["Alis-Companion"] or identity(host)!=sources["Alis"]:
            failure,code="SourceChanged","source_changed_during_run"
    except (OSError,ValueError,subprocess.SubprocessError):
        failure,code="SourceChanged","source_unavailable_or_dirty_after_run"
    complete = failure is None and len(rows)==28
    report={"schema":1,"status":"LOCAL_HELPER_RESPONSES_REVIEW_REQUIRED" if complete else "INCONCLUSIVE_LOCAL_RUN",
        "sources":sources,"modelSha256":model_sha,"runtimeSha256":runtime_sha,
        "scenarioSha256":digest(fixture),"helperRequests":len(attempts),"acceptedReplies":len(rows),
        "failureClass":failure,"failureCode":code,"thinkingEffort":"off","shareObsidian":False,"persistHistory":False,
        "wallSecondsIncludingStartup":round(time.monotonic()-started,6),"attempts":attempts,
        "formatMetrics":evaluator.evaluate(rows,digest(responses)),"humanReview":"REQUIRED",
        "inferenceQualification":"NOT_ESTABLISHED_BY_THIS_REPORT",
        "scope":"Explicit pinned local helper path and synthetic scenarios only; not production inference, voice quality, CPU/GPU/RAM or lossless acceptance"}
    path=output/"result.json"
    with path.open("x",encoding="utf-8") as stream:
        json.dump(report,stream,indent=2);stream.write("\n")
    path.chmod(0o600)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path",type=Path,required=True,help="explicit existing GGUF; never downloaded")
    parser.add_argument("--runtime-path",type=Path,required=True,help="explicit existing llama-server executable")
    parser.add_argument("--alis-root",type=Path,required=True,help="clean compatible Alis source")
    parser.add_argument("--output",type=Path,required=True,help="new private directory, never reused")
    args=parser.parse_args()
    original=signal.getsignal(signal.SIGTERM)
    def canceled(signum,frame):raise RunCanceled("run_canceled")
    signal.signal(signal.SIGTERM,canceled)
    try:
        report=run(args.model_path,args.runtime_path,args.alis_root,args.output)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError,RunCanceled) as error:
        parser.exit(2,"ALIS_COMPANION_VOICE_RUN_BLOCKED:"+failure_code(error)+"\n")
    finally:
        signal.signal(signal.SIGTERM,original)
    print(report["status"]+" helperRequests="+str(report["helperRequests"])+" accepted="+str(report["acceptedReplies"]))
    raise SystemExit(0 if report["status"]=="LOCAL_HELPER_RESPONSES_REVIEW_REQUIRED" else 2)


if __name__=="__main__":main()
