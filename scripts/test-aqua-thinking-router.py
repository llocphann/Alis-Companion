#!/usr/bin/env python3
"""Offline isolated verification for the Auto Thinking Effort Router (Python + JS + provider mapping)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_AQUA = ROOT / "scripts" / "aqua"
sys.path.insert(0, str(SCRIPTS_AQUA))

os.environ["ALIS_THINKING_ROUTER_DISABLE_ML"] = "1"
TMP_ROOT = tempfile.TemporaryDirectory(prefix="alis-thinking-router-test-")
os.environ["ALIS_THINKING_ROUTER_DIR"] = str(Path(TMP_ROOT.name) / "router")
os.environ["ALIS_THINKING_ROUTER_SOCK"] = str(Path(TMP_ROOT.name) / "router.sock")
os.environ["ALIS_THINKING_ROUTER_STATE"] = str(Path(TMP_ROOT.name) / "state.json")

import thinking_router  # noqa: E402


def run_js_cases(cases: list[dict]) -> list[str]:
    js_path = ROOT / "modules/abyss/companion/AquaThinkingRouter.js"
    node_code = f"""
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync({json.dumps(str(js_path))}, 'utf8').replace(/^\\.pragma library\\s*/u, '');
const ctx = vm.createContext({{}});
vm.runInContext(src, ctx);
const cases = {json.dumps(cases)};
const out = cases.map(c => ctx.resolveEffort(c.prompt, !!c.fromVoice, {{
    category: c.category || '',
    isDomainCap: !!c.isDomainCap,
    prevEffort: c.prevEffort || ''
}}));
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(["node", "-e", node_code], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout.strip())


def main() -> None:
    try:
        cases = [
            # Greetings & mic tests -> short-circuit "off"
            {"prompt": "Hello", "expected": ("off",)},
            {"prompt": "Alo nghe rõ không?", "expected": ("off",)},
            {"prompt": "Can you hear me?", "expected": ("off",)},
            # Mechanical UI/action -> "off"
            {"prompt": "Open Firefox and switch workspace", "category": "desktop_ui_action", "expected": ("off",)},
            # Domain capability -> "off"
            {"prompt": "Do you know about Niri config?", "isDomainCap": True, "expected": ("off",)},
            # Simple factual QA containing tech terms -> must NOT false-escalate to medium/high/max!
            {"prompt": "What is Python?", "expected": ("off", "low")},
            {"prompt": "Thủ đô của Pháp là gì?", "expected": ("off", "low")},
            {"prompt": "Define recursion", "expected": ("off", "low")},
            # Moderate comparison / coding
            {
                "prompt": "Compare Wayland vs X11 compositor input latency and how Niri handles layer-shell focus",
                "expected": ("medium", "high"),
            },
            # Short formal mathematical proof -> fast-track "high"/"max" despite short length
            {
                "prompt": "Prove that sqrt(2) is irrational using proof by contradiction",
                "expected": ("high", "max"),
            },
            # Deep architectural + exhaustive prompt
            {
                "prompt": (
                    "Design a lock-free multi-producer SPSC ring buffer in Rust, prove memory ordering "
                    "correctness with Acquire/Release semantics, analyze false sharing trade-offs step by step, "
                    "and provide an exhaustive implementation:\n```rust\npub struct Ring<T> {}\n```"
                ),
                "expected": ("max", "high"),
            },
            # Voice cap: same exhaustive prompt with fromVoice=True must never return "max"
            {
                "prompt": (
                    "Design a lock-free multi-producer SPSC ring buffer in Rust, prove memory ordering "
                    "correctness with Acquire/Release semantics, analyze false sharing trade-offs step by step, "
                    "and provide an exhaustive implementation"
                ),
                "fromVoice": True,
                "expected": ("high", "medium"),
            },
            # Session continuity: terse follow-up after high effort keeps floor instead of "off"
            {"prompt": "Why?", "prevEffort": "high", "expected": ("medium", "high")},
            {"prompt": "Tại sao vậy?", "prevEffort": "high", "expected": ("medium", "high")},
            # Deep slash command floor
            {"prompt": "/plan refactor", "category": "slash_thinking", "expected": ("high", "max")},
        ]

        js_results = run_js_cases(cases)
        for idx, case in enumerate(cases):
            py_res = thinking_router.route_thinking_effort(
                case["prompt"],
                {"fromVoice": bool(case.get("fromVoice")), "prevEffort": case.get("prevEffort", "")},
                {"category": case.get("category", ""), "is_domain_cap": bool(case.get("isDomainCap"))},
                persist_state=False,
            )
            py_eff = py_res["effort"]
            js_eff = js_results[idx]
            assert py_eff in case["expected"], f"Python mismatch on {case['prompt']!r}: got {py_eff}, want {case['expected']} ({py_res})"
            assert js_eff in case["expected"], f"JS mismatch on {case['prompt']!r}: got {js_eff}, want {case['expected']}"
            assert py_eff == js_eff, f"Python/JS parity drift on {case['prompt']!r}: py={py_eff}, js={js_eff}"
            if case.get("fromVoice"):
                assert py_eff != "max" and js_eff != "max"

        # Test ML scoring head normalization (mmBERT-small logits mapping)
        ml_low = thinking_router._score_ml_outputs({"task_chatbot": 0.9, "reasoning": 0.08, "complexity": 0.12, "constraints": 0.05, "domain_knowledge": 0.05})
        assert ml_low["effort"] == "off", ml_low
        ml_med = thinking_router._score_ml_outputs({"task_code_generation": 0.8, "reasoning": 0.36, "complexity": 0.42, "constraints": 0.25, "domain_knowledge": 0.30})
        assert ml_med["effort"] in ("medium", "high"), ml_med
        ml_high = thinking_router._score_ml_outputs({"task_open_qa": 0.7, "reasoning": 0.82, "complexity": 0.78, "constraints": 0.65, "domain_knowledge": 0.70})
        assert ml_high["effort"] == "max", ml_high

        # Test Layer 3 Provider-Native reasoning_families translations
        anth_adapt = thinking_router.apply_effort_to_payload(
            {"model": "claude-sonnet-4-6", "temperature": 0.7}, "high", api_format="anthropic"
        )
        assert anth_adapt["thinking"] == {"type": "adaptive"}
        assert anth_adapt["output_config"]["effort"] == "high"
        assert "temperature" not in anth_adapt

        anth_off = thinking_router.apply_effort_to_payload(dict(anth_adapt), "off", api_format="anthropic")
        assert "thinking" not in anth_off and "output_config" not in anth_off

        gem25 = thinking_router.apply_effort_to_payload({"model": "gemini-2.5-pro"}, "medium", api_format="gemini")
        assert gem25["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 4096
        gem25_off = thinking_router.apply_effort_to_payload({"model": "gemini-2.5-pro"}, "off", api_format="gemini")
        assert gem25_off["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0

        gem3 = thinking_router.apply_effort_to_payload({"model": "gemini-3-flash"}, "high", api_format="gemini")
        assert gem3["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "high"

        qwen3 = thinking_router.apply_effort_to_payload(
            {"model": "qwen3-14b-instruct"}, "off", api_format="openai"
        )
        assert qwen3["reasoning_effort"] == "none" and qwen3["chat_template_kwargs"]["enable_thinking"] is False
        qwen3_on = thinking_router.apply_effort_to_payload(
            {"model": "qwen3.5-27b"}, "high", api_format="openai"
        )
        assert qwen3_on["reasoning_effort"] == "high" and qwen3_on["chat_template_kwargs"]["enable_thinking"] is True

        plusiq = thinking_router.apply_effort_to_payload(
            {"model": "Qwen3.5-27B-PlusIQ", "messages": [{"role": "user", "content": "Prove Fermat"}]},
            "max",
            api_format="openai",
        )
        assert plusiq["reasoning_effort"] == "none"
        assert plusiq["messages"][0]["content"].startswith("{REASON:einstein}\n")

        oai_resp = thinking_router.apply_effort_to_payload({"model": "gpt-5"}, "medium", api_format="openai-response")
        assert oai_resp["reasoning"]["effort"] == "medium"

        # Test local_mind.py --wrap-provider strips companionAutoThinking* keys before forwarding to upstream child
        fake_upstream = Path(TMP_ROOT.name) / "echo_upstream.py"
        fake_upstream.write_text(
            "import json, sys\n"
            "data = json.loads(sys.stdin.read())\n"
            "assert not any(k.startswith('companionAutoThinking') for k in data), f'Leaked internal keys: {list(data)}'\n"
            "print('data: ' + json.dumps({'choices': [{'delta': {'content': data.get('reasoning_effort', 'missing')}}]}))\n"
            "print('data: [DONE]')\n",
            encoding="utf-8",
        )
        wrap_in = json.dumps({
            "model": "qwen3-14b",
            "messages": [{"role": "user", "content": "Prove that sqrt(2) is irrational using proof by contradiction"}],
            "companionSessionId": "t1",
            "companionProvider": "openai",
            "companionAutoThinking": True,
            "companionAutoThinkingPrompt": "Prove that sqrt(2) is irrational using proof by contradiction",
            "companionAutoThinkingFromVoice": False,
            "companionAutoThinkingCategory": "",
            "companionAutoThinkingPrevEffort": "",
            "companionAutoThinkingApiFormat": "openai",
        }) + "\n"
        wrap_proc = subprocess.run(
            ["/usr/bin/python3", str(SCRIPTS_AQUA / "local_mind.py"), "--wrap-provider", "/usr/bin/python3", str(fake_upstream)],
            input=wrap_in,
            text=True,
            capture_output=True,
            env={**os.environ, "ALIS_TEST_ISOLATED": "1"},
            check=True,
        )
        assert '"high"' in wrap_proc.stdout, wrap_proc.stdout

        # Test CompanionConfig.qml contract fixes & UI elements
        cfg_src = (ROOT / "modules/settings/CompanionConfig.qml").read_text(encoding="utf-8")
        assert 'modelData.value === "auto" ? "Auto"' in cfg_src
        assert 'objectName: "aquaSettingsEffortAuto"' in cfg_src
        assert 'objectName: "aquaAutoThinkingRouterRow"' in cfg_src

        print(f"AQUA_THINKING_ROUTER_OFFLINE_PASS cases={len(cases)} providerFormats=6 wrapStrip=ok")
    finally:
        TMP_ROOT.cleanup()


if __name__ == "__main__":
    main()
