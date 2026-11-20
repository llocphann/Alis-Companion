#!/usr/bin/env python3
"""Local ML + Weighted Multi-Dimension Auto Thinking Effort Router for Alis-Companion.

Architecture synthesized from proven open-source & provider mechanisms:
  1. Primary Local ML Classifier (`mmBERT-small`, Horizon-Labs/multilingual-prompt-task-complexity-small):
     Multilingual (100+ languages including Vietnamese & English) multi-head encoder exported to INT8 ONNX,
     implementing NVIDIA's 11-task + 6-complexity-dimension taxonomy over a warm CPU Unix-socket daemon
     (zero GPU VRAM, idle auto-unload).
  2. Deterministic Multi-Dimension Weighted + Sigmoid Fallback (`LiteLLM` Complexity Router &
     `BlockRunAI/ClawRouter`): sub-millisecond multi-dimension scorer with negative `simpleIndicators`,
     reasoning fast-track override, sigmoid confidence calibration, and session follow-up continuity.
  3. Provider-Native Translation Layer (`vLLM Semantic Router` `reasoning_families` & Anthropic /
     Gemini / OpenAI / Qwen3 / PlusIQ conventions).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata

XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
XDG_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
XDG_RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/alis-companion-rt-{os.getuid()}")

MODEL_REPO = "Horizon-Labs/multilingual-prompt-task-complexity-small"
MODEL_LABEL = "mmBERT-small (141M INT8)"
EFFORT_RANK = {"off": 0, "instant": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5}
CANONICAL_BUDGET = {"low": 1024, "medium": 4096, "high": 8192, "xhigh": 16384, "max": 24576}

MECHANICAL_CATEGORIES = frozenset({
    "slash_action",
    "desktop_ui_action",
    "screen_observe",
    "screen_inspect",
    "gui_action",
    "desktop_control",
    "system_control",
    "file_task",
    "shell_task",
    "git_task",
    "mcp_manage",
    "web_task",
    "voice_task",
    "permission_manage",
    "rule_manage",
    "memory_manage",
    "pocket_manage",
    "skill_manage",
    "confirmation_response",
    "stop",
    "resume",
})

_GREETING_OR_MIC_RE = re.compile(
    r"^(?:hi|hello|hey|yo|alo+|chao|xin chao|ok|okay|oke|thanks|thank you|cam on|bye|goodbye|tam biet)[!.,?\s]*$"
    r"|\b(?:can you hear me|do you hear me|hear me|are you listening|listening to me|mic test|test mic|testing mic"
    r"|nghe ro khong|nghe thay khong|co nghe khong|ban nghe khong|how are you|how is your day|how's your day"
    r"|how is it going|how's it going|what's up|whats up|what is your name|who are you|good morning|good afternoon"
    r"|good evening|ban ten la gi|ban la ai|ban khoe khong|hom nay the nao)\b"
)

_DEEP_SLASH_RE = re.compile(r"^/(?:plan|diagnostics|learn|workflow|reflect|swarm)\b")

_FOLLOWUP_RE = re.compile(
    r"^(?:why|how|why though|but why|explain more|elaborate|go deeper|details|more details|continue|go on|keep going"
    r"|fix it|fix that|do it|check again|verify|prove it|tai sao|vi sao|tai sao vay|vi sao vay|giai thich them"
    r"|giai thich ro hon|noi ro hon|chi tiet hon|tiep tuc|sua di|sua lai|lam di|kiem tra lai)[?!.,\s]*$"
)

# Layer 2 patterns (LiteLLM + ClawRouter weighted taxonomy)
_REASONING_MARKERS_RE = re.compile(
    r"\b(?:prove|proof|proof by contradiction|theorem|lemma|corollary|derive|derivation|formal proof"
    r"|mathematical proof|step by step|think through|reason about|analyze deeply|deep dive|root cause"
    r"|trade-?off|architecture|system design|algorithm|complexity|big-?o|deadlock|race condition"
    r"|lock-free|concurrency|dynamic programming|evaluate|critique|compare .* and .*|why does|why is"
    r"|chung minh|phan chung|dinh ly|suy luan|suy nghi sau|phan tich sau|phan tich ky|nguyen nhan goc"
    r"|kien truc|thuat toan|do phuc tap|quy hoach dong|lap ke hoach|len ke hoach|ke hoach chi tiet"
    r"|danh gia kien truc|thiet ke he thong|so sanh chi tiet|tung buoc|giai thich tai sao|vi sao lai)\b",
    re.I,
)

_EXHAUSTIVE_FASTTRACK_RE = re.compile(
    r"\b(?:prove that|proof by contradiction|formal proof|mathematical proof|exhaustive|exhaustively"
    r"|maximum effort|deepest|no spoon|einstein|chung minh rang|phan chung|cuc sau|chuyen sau nhat|toan dien)\b",
    re.I,
)

_TECH_TERMS_RE = re.compile(
    r"\b(?:python|rust|typescript|javascript|c\+\+|golang|bash|qml|quickshell|niri|wayland|x11|compositor"
    r"|layer-shell|wlr|systemd|journalctl|kernel|async|mutex|onnx|shader|glsl|sql|regex|compiler|ast"
    r"|kubernetes|docker|allocator|latency|throughput|protocol|ipc|socket|memory leak|refactor|debug"
    r"|traceback|exception|optimization|optimize|benchmark|pacman|arch linux|network|firewall|tls|oauth)\b",
    re.I,
)

_MULTISTEP_RE = re.compile(
    r"(?:\b(?:first|second|third|then|after that|finally|step\s*\d+|dau tien|tiep theo|sau do|cuoi cung|buoc\s*\d+)\b"
    r"|(?:^|\n)\s*(?:\d+[\.\)]|[-*])\s+\S)",
    re.I,
)

_CONSTRAINTS_RE = re.compile(
    r"\b(?:must|never|always|only|at most|at least|without|strictly|constraint|require|ensure|preserve"
    r"|backward compatible|zero-focus|phai|khong duoc|chi duoc|toi da|toi thieu|bat buoc|dam bao|giu nguyen)\b",
    re.I,
)

_OUTPUT_FORMAT_RE = re.compile(
    r"\b(?:json|yaml|markdown|table|csv|schema|patch|diff|script|function|class|bang|dinh dang)\b",
    re.I,
)

_SIMPLE_INDICATORS_RE = re.compile(
    r"^(?:what is|what's|whats|what are|who is|who's|when is|when did|where is|define|meaning of"
    r"|translate|convert|spell|capital of|how old|how tall|how many|summarize briefly)\b"
    r"|\b(?:la gi|nghia la gi|thu do cua|dich sang|viet tat cua|ai la|o dau|khi nao|bao nhieu)\b",
    re.I,
)

_ML_CACHE: dict | None = None


def _paths() -> dict[str, Path]:
    root = Path(os.environ.get("ALIS_THINKING_ROUTER_DIR") or (XDG_DATA_HOME / "alis-companion" / "thinking-router"))
    venv_py = root / "venv" / "bin" / "python"
    model_dir = root / "model"
    sock = Path(
        os.environ.get("ALIS_THINKING_ROUTER_SOCK")
        or (XDG_RUNTIME_DIR / "alis-companion-thinking-router.sock")
    )
    state_file = Path(
        os.environ.get("ALIS_THINKING_ROUTER_STATE")
        or (XDG_STATE_HOME / "alis-companion" / "last_auto_thinking.json")
    )
    return {
        "root": root,
        "venv_py": venv_py,
        "model_dir": model_dir,
        "onnx": model_dir / "model_quantized.onnx",
        "tokenizer": model_dir / "tokenizer.json",
        "config": model_dir / "config.json",
        "socket": sock,
        "state_file": state_file,
    }


def _fold_ascii(text: str) -> str:
    s = unicodedata.normalize("NFD", str(text or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D").lower().strip()


def _sigmoid(x: float) -> float:
    x = max(-40.0, min(40.0, float(x)))
    return 1.0 / (1.0 + math.exp(-x))


def is_ml_installed() -> bool:
    if os.environ.get("ALIS_THINKING_ROUTER_DISABLE_ML") == "1":
        return False
    p = _paths()
    return (
        p["venv_py"].is_file()
        and os.access(p["venv_py"], os.X_OK)
        and p["onnx"].is_file()
        and p["tokenizer"].is_file()
        and p["config"].is_file()
    )


def _is_daemon_alive(sock_path: Path) -> bool:
    if not sock_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            s.connect(str(sock_path))
            s.sendall(b'{"action":"ping"}\n')
            raw = s.makefile("r", encoding="utf-8").readline()
            return json.loads(raw).get("ok") is True
    except Exception:
        return False


def get_status() -> dict:
    p = _paths()
    installed = is_ml_installed()
    size_bytes = 0
    if p["model_dir"].is_dir():
        for f in p["model_dir"].iterdir():
            if f.is_file():
                try:
                    size_bytes += f.stat().st_size
                except OSError:
                    pass
    return {
        "installed": installed,
        "running": _is_daemon_alive(p["socket"]) if installed else False,
        "modelRepo": MODEL_REPO,
        "modelLabel": MODEL_LABEL,
        "modelDir": str(p["model_dir"]),
        "venvPython": str(p["venv_py"]),
        "socketPath": str(p["socket"]),
        "sizeMb": round(size_bytes / (1024 * 1024), 1),
    }


def _load_onnx_runtime():
    global _ML_CACHE
    if _ML_CACHE is not None:
        return _ML_CACHE
    p = _paths()
    import numpy as np  # type: ignore
    import onnxruntime as ort  # type: ignore
    from tokenizers import Tokenizer  # type: ignore

    tok = Tokenizer.from_file(str(p["tokenizer"]))
    tok.no_padding()
    tok.no_truncation()

    so = ort.SessionOptions()
    so.intra_op_num_threads = min(4, max(1, os.cpu_count() or 2))
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    sess = ort.InferenceSession(str(p["onnx"]), sess_options=so, providers=["CPUExecutionProvider"])
    cfg = json.loads(p["config"].read_text(encoding="utf-8"))
    id2label = {int(k): str(v) for k, v in (cfg.get("id2label") or {}).items()}
    input_names = [inp.name for inp in sess.get_inputs()]

    _ML_CACHE = {
        "np": np,
        "tokenizer": tok,
        "session": sess,
        "id2label": id2label,
        "input_names": input_names,
    }
    return _ML_CACHE


def _infer_ml_onnx_in_process(text: str) -> dict[str, float]:
    rt = _load_onnx_runtime()
    np = rt["np"]
    tok = rt["tokenizer"]
    sess = rt["session"]
    id2label = rt["id2label"]
    input_names = rt["input_names"]

    enc = tok.encode(str(text or ""))
    ids = list(enc.ids)
    # Head+Tail 512-token packing preserves CLS (idx 0) + opening problem statement + final constraints/SEP.
    if len(ids) > 512:
        ids = ids[:383] + ids[-129:]
    if not ids:
        ids = [0, 2]
    mask = [1] * len(ids)

    feeds = {}
    if "input_ids" in input_names:
        feeds["input_ids"] = np.asarray([ids], dtype=np.int64)
    if "attention_mask" in input_names:
        feeds["attention_mask"] = np.asarray([mask], dtype=np.int64)
    if "token_type_ids" in input_names:
        feeds["token_type_ids"] = np.zeros((1, len(ids)), dtype=np.int64)

    logits = sess.run(None, feeds)[0][0]
    scores: dict[str, float] = {}
    for idx, val in enumerate(logits):
        lbl = id2label.get(idx, f"label_{idx}")
        scores[lbl] = round(_sigmoid(float(val)), 4)
    return scores


def _score_ml_outputs(scores: dict[str, float]) -> dict:
    reasoning = float(scores.get("reasoning", 0.0))
    complexity = float(scores.get("complexity", 0.0))
    constraints = float(scores.get("constraints", 0.0))
    domain_knowledge = float(scores.get("domain_knowledge", 0.0))
    contextual = float(scores.get("contextual_knowledge", 0.0))
    creativity = float(scores.get("creativity", 0.0))

    task_items = [(k, v) for k, v in scores.items() if k.startswith("task_")]
    top_task = max(task_items, key=lambda kv: kv[1])[0] if task_items else "task_other"

    base = (
        0.48 * reasoning
        + 0.30 * complexity
        + 0.12 * constraints
        + 0.10 * domain_knowledge
    )
    if top_task == "task_chatbot" and reasoning < 0.22 and complexity < 0.22:
        base *= 0.55
    if float(scores.get("task_code_generation", 0.0)) >= 0.45:
        base = min(1.0, base + 0.08)
    if top_task in ("task_summarization", "task_rewrite", "task_extraction", "task_classification") and reasoning < 0.25:
        base = max(0.0, base - 0.06)

    if base >= 0.68 or (reasoning >= 0.72 and complexity >= 0.62):
        effort = "max"
    elif base >= 0.48:
        effort = "high"
    elif base >= 0.30:
        effort = "medium"
    elif base >= 0.16:
        effort = "low"
    else:
        effort = "off"

    boundaries = (0.16, 0.30, 0.48, 0.68)
    margin = min(abs(base - b) for b in boundaries)
    confidence = round(_sigmoid(margin * 12.0), 3)

    return {
        "effort": effort,
        "source": "ml_onnx",
        "task": top_task,
        "score": round(base, 4),
        "reasoning": round(reasoning, 4),
        "complexity": round(complexity, 4),
        "constraints": round(constraints, 4),
        "domainKnowledge": round(domain_knowledge, 4),
        "contextualKnowledge": round(contextual, 4),
        "creativity": round(creativity, 4),
        "confidence": confidence,
    }


def _ensure_daemon_and_query(prompt: str) -> dict[str, float] | None:
    p = _paths()
    sock_path = p["socket"]
    sock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    req_bytes = (json.dumps({"action": "score", "prompt": prompt}, ensure_ascii=False) + "\n").encode("utf-8")

    def _try_query(timeout_sec: float) -> dict[str, float] | None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_sec)
            s.connect(str(sock_path))
            s.sendall(req_bytes)
            line = s.makefile("r", encoding="utf-8").readline()
            resp = json.loads(line)
            if resp.get("ok") and isinstance(resp.get("scores"), dict):
                return resp["scores"]
        return None

    try:
        res = _try_query(0.6)
        if res is not None:
            return res
    except Exception:
        pass

    lock_path = Path(str(sock_path) + ".start.lock")
    lock_fh = None
    try:
        lock_fh = open(lock_path, "w", encoding="utf-8")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            have_lock = True
        except OSError:
            have_lock = False

        if have_lock and not _is_daemon_alive(sock_path):
            subprocess.Popen(
                [str(p["venv_py"]), str(Path(__file__).resolve()), "--daemon"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )

        deadline = time.monotonic() + 2.8
        while time.monotonic() < deadline:
            if sock_path.exists():
                try:
                    res = _try_query(1.0)
                    if res is not None:
                        return res
                except Exception:
                    pass
            time.sleep(0.04)
    except Exception:
        return None
    finally:
        if lock_fh is not None:
            try:
                lock_fh.close()
            except OSError:
                pass
    return None


def classify_weighted_fallback(prompt: str, options: dict | None = None) -> dict:
    """Sub-millisecond multi-dimension weighted + sigmoid scorer (LiteLLM + ClawRouter hybrid)."""
    raw = str(prompt or "").strip()
    folded = _fold_ascii(raw)
    words = [w for w in re.split(r"\s+", folded) if w]
    word_count = len(words)
    char_len = len(raw)

    # Signed token-length feature (-1.0 for trivial short prompts, up to +1.0 for long multi-clause prompts)
    if char_len < 32 and word_count <= 6:
        token_feat = -0.85
    elif char_len < 85 and word_count <= 16:
        token_feat = -0.25
    elif char_len < 220:
        token_feat = 0.20
    elif char_len < 520:
        token_feat = 0.62
    else:
        token_feat = 1.0

    has_fenced_code = "```" in raw or len(re.findall(r"[;{}=<>]{2,}", raw)) >= 2
    code_hits = len(re.findall(r"\b(?:function|class|def|async|await|import|struct|impl|shader|snippet|patch|bug|traceback)\b", folded))
    code_feat = 1.0 if has_fenced_code else min(1.0, code_hits * 0.38)

    reason_hits = len(_REASONING_MARKERS_RE.findall(folded))
    exhaustive_hit = bool(_EXHAUSTIVE_FASTTRACK_RE.search(folded))
    reason_feat = 1.0 if exhaustive_hit else min(1.0, reason_hits * 0.48)

    tech_hits = len(set(_TECH_TERMS_RE.findall(folded)))
    tech_feat = min(1.0, tech_hits * 0.28)

    step_hits = len(_MULTISTEP_RE.findall(raw))
    step_feat = min(1.0, step_hits * 0.45)

    constr_hits = len(_CONSTRAINTS_RE.findall(folded))
    constr_feat = min(1.0, constr_hits * 0.35)

    q_count = raw.count("?")
    multi_clause_q = min(1.0, max(0, q_count - 1) * 0.45 + (0.35 if q_count == 1 and char_len >= 70 else 0.0))

    fmt_feat = 0.55 if _OUTPUT_FORMAT_RE.search(folded) else 0.0

    # Simple indicator suppression (only applies cleanly when prompt is short and has no explicit deep proof/code cues)
    simple_match = bool(_SIMPLE_INDICATORS_RE.search(folded))
    if simple_match and reason_hits == 0 and not has_fenced_code and word_count <= 14:
        simple_feat = 1.0
    elif word_count <= 5 and q_count <= 1 and reason_hits == 0 and not has_fenced_code:
        simple_feat = 0.65
    else:
        simple_feat = 0.0

    raw_score = (
        0.22
        + 0.28 * reason_feat
        + 0.24 * code_feat
        + 0.16 * tech_feat
        + 0.12 * token_feat
        + 0.10 * step_feat
        + 0.08 * constr_feat
        + 0.06 * multi_clause_q
        + 0.04 * fmt_feat
        - 0.36 * simple_feat
    )
    score = max(0.0, min(1.0, raw_score))

    # LiteLLM / ClawRouter reasoning fast-track bypasses short-token penalty on explicit proofs/deep tasks
    if exhaustive_hit and (char_len >= 260 or reason_hits >= 2 or has_fenced_code):
        effort = "max"
        score = max(score, 0.78)
    elif exhaustive_hit or reason_hits >= 2 or (reason_hits >= 1 and (has_fenced_code or tech_hits >= 2 or char_len >= 200)):
        effort = "high"
        score = max(score, 0.60)
    elif score >= 0.72:
        effort = "max"
    elif score >= 0.50:
        effort = "high"
    elif score >= 0.32:
        effort = "medium"
    elif score >= 0.17:
        effort = "low"
    else:
        effort = "off"

    boundaries = (0.17, 0.32, 0.50, 0.72)
    margin = min(abs(score - b) for b in boundaries)
    confidence = round(_sigmoid(margin * 12.0), 3)

    # ClawRouter ambiguity pull: low-confidence extreme high drops toward medium unless fast-tracked
    if confidence < 0.70 and effort == "max" and not exhaustive_hit:
        effort = "high"

    if has_fenced_code or code_hits >= 2:
        task_guess = "task_code_generation"
    elif simple_match:
        task_guess = "task_closed_qa" if q_count else "task_open_qa"
    else:
        task_guess = "task_open_qa" if q_count else "task_chatbot"

    return {
        "effort": effort,
        "source": "weighted",
        "task": task_guess,
        "score": round(score, 4),
        "reasoning": round(reason_feat, 4),
        "complexity": round(score, 4),
        "confidence": confidence,
    }


def write_last_decision(detail: dict) -> None:
    p = _paths()
    state_file = p["state_file"]
    try:
        state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "effort": str(detail.get("effort") or "off"),
                "source": str(detail.get("source") or "weighted"),
                "task": str(detail.get("task") or ""),
                "score": float(detail.get("score") or 0.0),
                "reasoning": float(detail.get("reasoning") or 0.0),
                "complexity": float(detail.get("complexity") or 0.0),
                "confidence": float(detail.get("confidence") or 0.0),
                "latencyMs": float(detail.get("latencyMs") or 0.0),
                "tsMs": int(time.time() * 1000),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(prefix=".auto-think-", dir=str(state_file.parent))
        tmp = Path(tmp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.replace(tmp, state_file)
        except OSError:
            tmp.unlink(missing_ok=True)
    except OSError:
        pass


def route_thinking_effort(
    prompt: str,
    options: dict | None = None,
    route_meta: dict | None = None,
    *,
    persist_state: bool = False,
) -> dict:
    """Resolve Auto Thinking Effort via Short-Circuit -> Local mmBERT ONNX -> Weighted Fallback."""
    t0 = time.monotonic()
    opts = options if isinstance(options, dict) else {}
    meta = route_meta if isinstance(route_meta, dict) else {}
    raw = str(prompt or "").strip()
    folded = _fold_ascii(raw)
    from_voice = bool(opts.get("fromVoice"))
    category = str(meta.get("category") or opts.get("category") or "").strip()
    is_domain_cap = bool(meta.get("is_domain_cap") or opts.get("isDomainCap"))
    prev_effort = str(opts.get("prevEffort") or meta.get("prev_effort") or "").strip().lower()

    if (
        not raw
        or is_domain_cap
        or bool(_GREETING_OR_MIC_RE.search(folded))
        or category in MECHANICAL_CATEGORIES
    ):
        res = {
            "effort": "off",
            "source": "short_circuit",
            "task": "task_chatbot",
            "score": 0.0,
            "reasoning": 0.0,
            "complexity": 0.0,
            "confidence": 0.99,
            "latencyMs": round((time.monotonic() - t0) * 1000, 2),
        }
        if persist_state:
            write_last_decision(res)
        return res

    min_floor = "off"
    if category in ("slash_thinking", "deep_reasoning") or bool(_DEEP_SLASH_RE.match(folded)):
        min_floor = "high"

    result: dict | None = None
    if is_ml_installed():
        ml_scores: dict[str, float] | None = None
        if os.environ.get("ALIS_THINKING_ROUTER_IN_PROCESS") == "1" or (
            Path(sys.executable).resolve() == _paths()["venv_py"].resolve()
        ):
            try:
                ml_scores = _infer_ml_onnx_in_process(raw)
            except Exception:
                ml_scores = None
        else:
            ml_scores = _ensure_daemon_and_query(raw)
        if ml_scores:
            result = _score_ml_outputs(ml_scores)

    if result is None:
        result = classify_weighted_fallback(raw, opts)

    effort = str(result.get("effort") or "off")

    # Session continuity / hysteresis (LiteLLM session affinity / opencode adaptive pattern):
    # short follow-ups retain scaled reasoning context instead of collapsing abruptly to "off".
    words = [w for w in re.split(r"\s+", folded) if w]
    if len(words) <= 14 and _FOLLOWUP_RE.match(folded) and prev_effort in ("medium", "high", "xhigh", "max"):
        decay_floor = "high" if prev_effort in ("xhigh", "max") else ("medium" if prev_effort == "high" else "low")
        if EFFORT_RANK.get(effort, 0) < EFFORT_RANK.get(decay_floor, 0):
            effort = decay_floor

    if EFFORT_RANK.get(effort, 0) < EFFORT_RANK.get(min_floor, 0):
        effort = min_floor

    # Strong safety floor for short formal mathematical/algorithmic proofs if ONNX under-scores terse inputs
    if (
        _EXHAUSTIVE_FASTTRACK_RE.search(folded)
        and re.search(r"\b(?:prove|proof|irrational|theorem|chung minh|phan chung)\b", folded)
        and EFFORT_RANK.get(effort, 0) < EFFORT_RANK["high"]
    ):
        effort = "high"

    if from_voice:
        if effort == "max":
            effort = "high"
        elif effort == "high" and float(result.get("reasoning") or 0.0) < 0.62:
            effort = "medium"

    result["effort"] = effort
    result["latencyMs"] = round((time.monotonic() - t0) * 1000, 2)
    if persist_state:
        write_last_decision(result)
    return result


def apply_effort_to_payload(
    payload_obj: dict,
    effort: str,
    api_format: str = "",
    model_name: str = "",
    provider_id: str = "",
    endpoint: str = "",
) -> dict:
    """Layer 3: Translate resolved canonical effort into provider-native reasoning parameters."""
    if not isinstance(payload_obj, dict):
        return payload_obj
    eff = str(effort or "off").strip().lower()
    if eff not in EFFORT_RANK:
        eff = "off"
    fmt = str(api_format or "").strip().lower()
    mdl = str(model_name or payload_obj.get("model") or "").strip().lower()
    prov = str(provider_id or "").strip().lower()
    ep = str(endpoint or "").strip().lower()

    is_plusiq = any(k in mdl for k in ("plusiq", "davidau", "defiant-fable", "heretic", "neo-imatrix"))
    canonical = "none" if eff in ("off", "instant") else ("high" if eff in ("xhigh", "max") else eff)
    enable_thinking = eff not in ("off", "instant")

    # 1. DavidAU / Qwen3.5 PlusIQ single-model {REASON:...} in-chat tags
    if is_plusiq:
        payload_obj["reasoning_effort"] = "none"
        tag_map = {
            "off": "{REASON:none}",
            "instant": "{REASON:none}",
            "low": "{REASON:ilow}",
            "medium": "{REASON:imedium}",
            "high": "{REASON:ihigh}",
            "xhigh": "{REASON:ixhigh}",
            "max": "{REASON:einstein}",
        }
        tag = tag_map.get(eff, "{REASON:none}")
        msgs = payload_obj.get("messages")
        if isinstance(msgs, list):
            for idx in range(len(msgs) - 1, -1, -1):
                m = msgs[idx]
                if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
                    if "{REASON:" not in m["content"]:
                        m["content"] = f"{tag}\n{m['content']}"
                    break
        return payload_obj

    # 2. Anthropic Claude Messages API
    if fmt == "anthropic":
        if not enable_thinking:
            payload_obj.pop("thinking", None)
            oc = payload_obj.get("output_config")
            if isinstance(oc, dict):
                oc.pop("effort", None)
                if not oc:
                    payload_obj.pop("output_config", None)
        elif re.search(r"claude-(?:opus|sonnet)-(?:4-[6-9]|[5-9])", mdl):
            payload_obj["thinking"] = {"type": "adaptive"}
            oc = payload_obj.get("output_config")
            oc = dict(oc) if isinstance(oc, dict) else {}
            oc["effort"] = canonical
            payload_obj["output_config"] = oc
            payload_obj.pop("temperature", None)
        else:
            budget = CANONICAL_BUDGET.get(eff, 4096)
            payload_obj["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload_obj["max_tokens"] = max(int(payload_obj.get("max_tokens") or 4096), budget + 1024)
            payload_obj["temperature"] = 1
        return payload_obj

    # 3. Google Gemini (2.5 thinkingBudget / 3+ thinkingLevel)
    if fmt == "gemini":
        gen_cfg = dict(payload_obj.get("generationConfig") or {})
        t_cfg = dict(gen_cfg.get("thinkingConfig") or {})
        if "gemini-3" in mdl:
            t_cfg.pop("thinkingBudget", None)
            t_cfg["thinkingLevel"] = "minimal" if not enable_thinking else canonical
        elif "gemini-2.5" in mdl:
            t_cfg["thinkingBudget"] = 0 if not enable_thinking else CANONICAL_BUDGET.get(eff, 4096)
        if t_cfg:
            gen_cfg["thinkingConfig"] = t_cfg
            payload_obj["generationConfig"] = gen_cfg
        return payload_obj

    # 4. Native GGUF Python transport
    if payload_obj.get("modelPath"):
        payload_obj["thinkingEffort"] = eff if enable_thinking else "off"
        return payload_obj

    # 5. OpenAI Responses API (/v1/responses)
    if fmt == "openai-response" or ep.endswith("/responses"):
        r_cfg = dict(payload_obj.get("reasoning") or {})
        r_cfg["effort"] = canonical
        payload_obj["reasoning"] = r_cfg
        return payload_obj

    # 6. Native Ollama (/api/chat)
    if prov == "ollama" and "/v1/" not in ep and ep.endswith("/api/chat"):
        if not enable_thinking:
            payload_obj["think"] = False
        elif "gpt-oss" in mdl:
            payload_obj["think"] = canonical
        else:
            payload_obj["think"] = True
        return payload_obj

    # 7. OpenAI Chat Completions + vLLM / llama.cpp reasoning_families
    payload_obj["reasoning_effort"] = canonical
    if "qwen3" in mdl:
        kwargs = dict(payload_obj.get("chat_template_kwargs") or {})
        kwargs["enable_thinking"] = bool(enable_thinking)
        payload_obj["chat_template_kwargs"] = kwargs
    elif "deepseek" in mdl and ("r1" in mdl or "reasoner" in mdl or "v3.1" in mdl):
        kwargs = dict(payload_obj.get("chat_template_kwargs") or {})
        kwargs["thinking"] = bool(enable_thinking)
        payload_obj["chat_template_kwargs"] = kwargs
    return payload_obj


def _stop_daemon() -> bool:
    p = _paths()
    sock_path = p["socket"]
    stopped = False
    if sock_path.exists():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(str(sock_path))
                s.sendall(b'{"action":"shutdown"}\n')
                s.makefile("r", encoding="utf-8").readline()
                stopped = True
        except Exception:
            pass
        try:
            sock_path.unlink(missing_ok=True)
        except OSError:
            pass
    return stopped


def _run_daemon() -> int:
    idle_sec = max(30, int(os.environ.get("ALIS_THINKING_ROUTER_IDLE_SEC") or "600"))
    p = _paths()
    sock_path = p["socket"]
    sock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Pre-warm tokenizer & INT8 ONNX session once so client queries hit 15-40ms latency
    try:
        _load_onnx_runtime()
    except Exception:
        return 1

    sock_path.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(sock_path))
        os.chmod(sock_path, 0o600)
        srv.listen(16)
        srv.settimeout(5.0)
        last_active = time.monotonic()

        while True:
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                if time.monotonic() - last_active >= idle_sec:
                    break
                continue
            except OSError:
                break

            last_active = time.monotonic()
            with conn:
                conn.settimeout(2.0)
                try:
                    raw_line = conn.makefile("r", encoding="utf-8").readline()
                    if not raw_line:
                        continue
                    req = json.loads(raw_line)
                    act = str(req.get("action") or "score")
                    if act == "ping":
                        resp = {"ok": True}
                    elif act == "shutdown":
                        conn.sendall((json.dumps({"ok": True}) + "\n").encode("utf-8"))
                        break
                    else:
                        prompt_text = str(req.get("prompt") or "")
                        scores = _infer_ml_onnx_in_process(prompt_text)
                        resp = {"ok": True, "scores": scores}
                except Exception as exc:
                    resp = {"ok": False, "error": str(exc)[:180]}
                try:
                    conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
                except OSError:
                    pass
    finally:
        try:
            srv.close()
        except OSError:
            pass
        sock_path.unlink(missing_ok=True)
    return 0


def _uninstall() -> dict:
    import shutil

    _stop_daemon()
    p = _paths()
    root = p["root"]
    removed = False
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
        removed = not root.exists()
    p["state_file"].unlink(missing_ok=True)
    return {"uninstalled": removed, "installed": is_ml_installed()}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Alis Companion Auto Thinking Effort Router")
    parser.add_argument("--status", action="store_true", help="Print router installation & daemon status")
    parser.add_argument("--daemon", action="store_true", help="Run warm Unix-socket ONNX daemon")
    parser.add_argument("--stop-daemon", action="store_true", help="Stop running Unix-socket daemon")
    parser.add_argument("--uninstall", action="store_true", help="Remove local ML router files and venv")
    parser.add_argument("--classify", type=str, default=None, help="Classify prompt and print JSON routing output")
    parser.add_argument("--from-voice", action="store_true", help="Apply voice-latency effort bounds")
    parser.add_argument("--category", type=str, default="", help="Optional prompt category hint")
    parser.add_argument("--prev-effort", type=str, default="", help="Previous turn effort for continuity")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(get_status(), ensure_ascii=False))
        return 0
    if args.daemon:
        return _run_daemon()
    if args.stop_daemon:
        print(json.dumps({"stopped": _stop_daemon()}))
        return 0
    if args.uninstall:
        print(json.dumps(_uninstall(), ensure_ascii=False))
        return 0
    if args.classify is not None:
        res = route_thinking_effort(
            args.classify,
            {"fromVoice": bool(args.from_voice), "prevEffort": args.prev_effort},
            {"category": args.category},
        )
        print(json.dumps(res, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
