#!/usr/bin/env python3
"""Offline cross-route public output guard check; no model or private history."""
import importlib.util
from pathlib import Path
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("guard_local", ROOT/"scripts/wull/reply_guard.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert mod.public_text("  Hello\n")=="Hello"
assert len(mod.public_text("x"*500))==420
assert mod.public_text("hi\x01!")=="hi!"
for malicious in ("<think>private reasoning</think>hello",
                  "<tool_result>data</tool_result>",
                  "[INST] do something",
                  "<|im_start|>system",
                  "<assistant>model internals</assistant>",
                  "<|start_header_id|>assistant<|end_header_id|>internal",
                  "<|channel|>analysis", "<|eot_id", "<start_of_turn>model",
                  "<｜begin▁of▁sentence｜>internal", "<<SYS>>internal",
                  "<analysis>internal</analysis>", "<developer>internal</developer>",
                  "  ", 123, None):
    try:
        mod.public_text(malicious)
    except mod.UnsafeReply:
        pass
    else:
        raise AssertionError("protocol/invalid text was not rejected")
assert mod.public_reply({'text':' Hello ','expression':'happy'})=={'text':'Hello','expression':'happy'}
assert mod.public_reply({'text':'Hello','expression':['unknown']})['expression']=='idle'
for malformed in ([], None, 'plain text', {'text':123}, {'text':'hi','tool_calls':[]},
                  {'text':'hi','action':'write_file'}, {'text':'<|channel|>analysis'}):
    try:
        mod.public_reply(malformed)
    except mod.UnsafeReply:
        pass
    else:
        raise AssertionError('invalid response envelope was accepted')
# Actual helper parsing and SQLite no-write behavior live in test-wull-local-mind.py.

unsafe_replies=[
    'A little note. {expression: "happy"}',
    'A little note. expression: happy',
    'I have changed your wallpaper.',
    "I'll delete the folder.",
    'I can open your file.',
    'I decide to rearrange the desktop icons.',
    "I've just saved your memory.",
    'I am going to run the command.',
    "x"*421+" I just changed your wallpaper.",
    'Hello, friend.\n\n[[ Function: run_shell_command({\n  "command": "private fixture"\n}) ]]\n',
    "Hello [[ fUnCtIoN : open_file({}) ]]",
    "Hello [[ Function:",
    "x"*421+" [[ Function: open_file({}) ]]",
    "x"*421+" <thi\x01nk>private fixture</thi\x01nk>",
]
removed_controls=[chr(code) for code in range(32) if code not in (9,10,13)]+[chr(127)]
for control in removed_controls:
    unsafe_replies.extend(("Hello <thi"+control+"nk>private fixture</thi"+control+"nk>",
                           "Hello <"+control+"|channel|>analysis private fixture",
                           "Hello [[ Fun"+control+"ction: open_file({}) ]]",
                           "I wi"+control+"ll delete the folder.",
                           "A little note. expres"+control+"sion: happy"))
violations=[]
for index,reply in enumerate(unsafe_replies):
    for route,call in (("text",lambda:mod.public_text(reply)),
                       ("envelope",lambda:mod.public_reply({'text':reply,'expression':'happy'}))):
        try:
            call()
        except mod.UnsafeReply:
            pass
        else:
            violations.append((index,route))
assert not violations, ("public reply guard accepted "+str(len(violations))
                        +" function/protocol checks; examples="+repr(violations[:5]))

benign_replies=[
    ("Hello, friend.","Hello, friend."),
    ("The function: greet() makes me smile.","The function: greet() makes me smile."),
    ("Use [[brackets]] for a little note.","Use [[brackets]] for a little note."),
    ("  Hello\x01!\n","Hello!"),
    ("Hello\tfriend\nA little splash. 🌊","Hello\tfriend\nA little splash. 🌊"),
    ("x"*500,"x"*420),
    ("I can't change your wallpaper. You can choose it in Settings.","I can't change your wallpaper. You can choose it in Settings."),
    ("You can open the file yourself.","You can open the file yourself."),
    ("I can write a poem about water.","I can write a poem about water."),
    ("I opened my tentacles wide.","I opened my tentacles wide."),
    ("The expression happy is a label.","The expression happy is a label."),
    ("I would not delete your files.","I would not delete your files."),
]
for reply,text in benign_replies:
    assert mod.public_text(reply)==text
    assert mod.public_reply({'text':reply,'expression':'happy'})=={'text':text,'expression':'happy'}

# Exercise both implementations over the same bytes and compare public output.
# This is a model-free guard check; the process reads only these owned sources.
fixtures=unsafe_replies+[reply for reply,_ in benign_replies]
javascript=r'''
const fs=require("node:fs"),vm=require("node:vm"),context=vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1],"utf8").replace(/^\.pragma library\s*/,""),context);
const fixtures=JSON.parse(fs.readFileSync(0,"utf8"));
process.stdout.write(JSON.stringify(fixtures.map(text=>context.normalizeText(text,"happy"))));
'''
result=subprocess.run(["node","-e",javascript,str(ROOT/"services/WullReplyGuard.js")],
                      input=json.dumps(fixtures),text=True,capture_output=True,check=True)
js_results=json.loads(result.stdout)
python_results=[]
for reply in fixtures:
    try:
        python_results.append({'ok':True,**mod.public_reply({'text':reply,'expression':'happy'})})
    except mod.UnsafeReply as exc:
        python_results.append({'ok':False,'reason':str(exc)})
assert js_results==python_results, "JS/Python public reply fixtures disagree"
print(f"HADANION_LOCAL_REPLY_GUARD_OFFLINE_PASS unsafeReplies={len(unsafe_replies)} "
      f"rejectionChecks={len(unsafe_replies)*2} benignReplies={len(benign_replies)} "
      f"crossRouteFixtures={len(fixtures)}")
