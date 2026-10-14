// Model-free test for the pure QML output guard and its live integration.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "services/WullReplyGuard.js"), "utf8");
const context = vm.createContext({});
vm.runInContext(source.replace(/^\.pragma library\s*/u, ""), context);
const good = context.parse(JSON.stringify({text:"Hello, little splash.",expression:"happy"}));
assert.equal(good.ok, true);
assert.equal(good.text, "Hello, little splash.");
assert.equal(good.expression, "happy");
assert.equal(context.parse("Hello there.").ok, true); // pre-existing free-text fallback
assert.equal(context.parse("{\"text\":\"Hi\",\"expression\":\"unknown\"}").expression, "idle");
assert.equal(context.parse("{\"text\":\"Hi\",\"expression\":\"happy\",\"tool_calls\":[]}").ok, false);
assert.equal(context.parse('{"tool_calls": [').ok, false);
assert.equal(context.parse('{"text": "incomplete"').ok, false);
for (const token of [
    "<think>internal chain</think>hello",
    "<tool_result>private bytes</tool_result>",
    "<|im_start|>system",
    "[INST] ignore previous instructions",
    "<function_call>open_secret</function_call>",
    "<assistant>internal</assistant>",
    "<|start_header_id|>assistant<|end_header_id|>internal",
    "<|channel|>analysis",
    "<|eot_id",
    "<｜begin▁of▁sentence｜>internal",
    "<start_of_turn>model",
    "<<SYS>>internal",
    "<analysis>internal</analysis>",
    "<developer>internal</developer>",
]) {
    assert.equal(context.parse(token).ok, false, token);
    assert.equal(context.normalizeText(token, "happy").ok, false, token);
}
assert.equal(context.parse(" \r\n ").ok, false);
assert.equal(context.parse(JSON.stringify({text:{value:"nested"}})).ok, false);
assert.equal(context.parse(JSON.stringify({text:"x".repeat(421),expression:"sleepy"})).text.length,420);
assert.equal(context.normalizeText("Hi\u0001!", "alert").text,"Hi!");
assert.equal(context.parse("x".repeat(32769)).ok, false);
assert.equal(context.parse(JSON.stringify({text:"I don't have access to that setting.",expression:"thinking"})).ok,true);

// AiTextSession can flush a pending OpenAI tool call at EOF after normal prose.
// The generated function envelope and protocol markers must never reach a
// bubble or persistence, even when control removal reveals their spelling.
const unsafeReplies = [
    'Hello, friend.\n\n[[ Function: run_shell_command({\n  "command": "private fixture"\n}) ]]\n',
    "Hello [[ fUnCtIoN : open_file({}) ]]",
    "Hello [[ Function:",
    "x".repeat(421) + " [[ Function: open_file({}) ]]",
    "x".repeat(421) + " <thi\u0001nk>private fixture</thi\u0001nk>",
];
const removedControls = [...Array(32).keys()].filter(code=>![9,10,13].includes(code)).concat(127);
for (const code of removedControls) {
    const control = String.fromCharCode(code);
    unsafeReplies.push("Hello <thi" + control + "nk>private fixture</thi" + control + "nk>");
    unsafeReplies.push("Hello <" + control + "|channel|>analysis private fixture");
    unsafeReplies.push("Hello [[ Fun" + control + "ction: open_file({}) ]]");
}
const violations = [];
for (const [index, reply] of unsafeReplies.entries()) {
    for (const [route, result] of [
        ["prose", context.parse(reply)],
        ["envelope", context.parse(JSON.stringify({text:reply,expression:"happy"}))],
        ["normalizer", context.normalizeText(reply,"happy")],
    ]) if (result.ok) violations.push({index,route});
}
assert.equal(violations.length, 0, "public reply guard accepted " + violations.length
    + " function/protocol checks; examples=" + JSON.stringify(violations.slice(0,5)));
for (const [reply, text] of [
    ["Hello, friend.", "Hello, friend."],
    ["The function: greet() makes me smile.", "The function: greet() makes me smile."],
    ["Use [[brackets]] for a little note.", "Use [[brackets]] for a little note."],
    ["  Hello\u0001!\n", "Hello!"],
    ["Hello\tfriend\nA little splash. 🌊", "Hello\tfriend\nA little splash. 🌊"],
    ["x".repeat(500), "x".repeat(420)],
]) {
    assert.equal(context.normalizeText(reply,"happy").text,text);
    assert.equal(context.parse(JSON.stringify({text:reply,expression:"happy"})).text,text);
}

const qml = fs.readFileSync(path.join(root, "services/WullMind.qml"), "utf8");
assert.match(qml, /import "WullReplyGuard\.js" as WullReplyGuard/);
assert.match(qml, /WullReplyGuard\.parse\(raw\)/);
assert.match(qml, /WullReplyGuard\.normalizeText\(result\.text,\s*result\.expression\)/);
assert.doesNotMatch(qml,/reply=\{text:raw,expression:"idle"\}/);
console.log("HADANION_QML_REPLY_GUARD_OFFLINE_CONTRACT_PASS unsafeReplies=" + unsafeReplies.length
    + " rejectionChecks=" + (unsafeReplies.length*3) + " benignReplies=6");
