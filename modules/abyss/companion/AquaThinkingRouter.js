.pragma library

// Local sub-millisecond Weighted Multi-Dimension + Sigmoid Calibration Auto Thinking Effort Router.
// Mirrors LiteLLM `complexity_router.py` + `BlockRunAI/ClawRouter` (`@blockrun/router-core`) and
// serves as Layer 2 / immediate UI preview alongside `thinking_router.py`'s local mmBERT ONNX engine.

var EFFORT_RANK = ({ off: 0, instant: 0, low: 1, medium: 2, high: 3, xhigh: 4, max: 5 });

var MECHANICAL_CATEGORIES = ({
    slash_action: true,
    desktop_ui_action: true,
    screen_observe: true,
    screen_inspect: true,
    gui_action: true,
    desktop_control: true,
    system_control: true,
    file_task: true,
    shell_task: true,
    git_task: true,
    mcp_manage: true,
    web_task: true,
    voice_task: true,
    permission_manage: true,
    rule_manage: true,
    memory_manage: true,
    pocket_manage: true,
    skill_manage: true,
    confirmation_response: true,
    stop: true,
    resume: true
});

function foldAscii(text) {
    return String(text ?? "")
        .toLowerCase()
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/\u0111/g, "d")
        .trim();
}

function sigmoid(x) {
    const v = Math.max(-40, Math.min(40, Number(x) || 0));
    return 1.0 / (1.0 + Math.exp(-v));
}

function classify(rawPrompt, options) {
    const opts = options || {};
    const raw = String(rawPrompt ?? "").trim();
    const folded = foldAscii(raw);
    const fromVoice = Boolean(opts.fromVoice);
    const category = String(opts.category ?? "");
    const isDomainCap = Boolean(opts.isDomainCap);
    const prevEffort = String(opts.prevEffort ?? "").trim().toLowerCase();

    if (!raw) {
        return { effort: "off", score: 0, confidence: 1.0, source: "short_circuit" };
    }

    const greetingOrMicRe = /^(?:hi|hello|hey|yo|alo+|chao|xin chao|ok|okay|oke|thanks|thank you|cam on|bye|goodbye|tam biet)[!.,?\s]*$|\b(?:can you hear me|do you hear me|hear me|are you listening|listening to me|mic test|test mic|testing mic|nghe ro khong|nghe thay khong|co nghe khong|ban nghe khong|how are you|how is your day|how's your day|how is it going|how's it going|what's up|whats up|what is your name|who are you|good morning|good afternoon|good evening|ban ten la gi|ban la ai|ban khoe khong|hom nay the nao)\b/;
    if (isDomainCap || greetingOrMicRe.test(folded) || Boolean(MECHANICAL_CATEGORIES[category])) {
        return { effort: "off", score: 0, confidence: 0.99, source: "short_circuit" };
    }

    const deepSlashRe = /^\/(?:plan|diagnostics|learn|workflow|reflect|swarm)\b/;
    const minFloor = (category === "slash_thinking" || category === "deep_reasoning" || deepSlashRe.test(folded))
        ? "high"
        : (category === "hybrid_ui_thinking" ? "medium" : "off");

    const words = folded.split(/\s+/).filter(Boolean);
    const wordCount = words.length;
    const charLen = raw.length;

    let tokenFeat = 1.0;
    if (charLen < 32 && wordCount <= 6)
        tokenFeat = -0.85;
    else if (charLen < 85 && wordCount <= 16)
        tokenFeat = -0.25;
    else if (charLen < 220)
        tokenFeat = 0.20;
    else if (charLen < 520)
        tokenFeat = 0.62;

    const hasFencedCode = raw.includes("```") || (raw.match(/[;{}=<>]{2,}/g) ?? []).length >= 2;
    const codeHits = (folded.match(/\b(?:function|class|def|async|await|import|struct|impl|shader|snippet|patch|bug|traceback)\b/g) ?? []).length;
    const codeFeat = hasFencedCode ? 1.0 : Math.min(1.0, codeHits * 0.38);

    const reasoningRe = /\b(?:prove|proof|proof by contradiction|theorem|lemma|corollary|derive|derivation|formal proof|mathematical proof|step by step|think through|reason about|analyze deeply|deep dive|root cause|trade-?off|architecture|system design|algorithm|complexity|big-?o|deadlock|race condition|lock-free|concurrency|dynamic programming|evaluate|critique|compare .* and .*|why does|why is|chung minh|phan chung|dinh ly|suy luan|suy nghi sau|phan tich sau|phan tich ky|nguyen nhan goc|kien truc|thuat toan|do phuc tap|quy hoach dong|lap ke hoach|len ke hoach|ke hoach chi tiet|danh gia kien truc|thiet ke he thong|so sanh chi tiet|tung buoc|giai thich tai sao|vi sao lai)\b/g;
    const reasonHits = (folded.match(reasoningRe) ?? []).length;

    const exhaustiveRe = /\b(?:prove that|proof by contradiction|formal proof|mathematical proof|exhaustive|exhaustively|maximum effort|deepest|no spoon|einstein|chung minh rang|phan chung|cuc sau|chuyen sau nhat|toan dien)\b/;
    const exhaustiveHit = exhaustiveRe.test(folded);
    const reasonFeat = exhaustiveHit ? 1.0 : Math.min(1.0, reasonHits * 0.48);

    const techRe = /\b(?:python|rust|typescript|javascript|c\+\+|golang|bash|qml|quickshell|niri|wayland|x11|compositor|layer-shell|wlr|systemd|journalctl|kernel|async|mutex|onnx|shader|glsl|sql|regex|compiler|ast|kubernetes|docker|allocator|latency|throughput|protocol|ipc|socket|memory leak|refactor|debug|traceback|exception|optimization|optimize|benchmark|pacman|arch linux|network|firewall|tls|oauth)\b/g;
    const techMatches = new Set(folded.match(techRe) ?? []);
    const techHits = techMatches.size;
    const techFeat = Math.min(1.0, techHits * 0.28);

    const stepHits = (raw.match(/\b(?:first|second|third|then|after that|finally|step\s*\d+|dau tien|tiep theo|sau do|cuoi cung|buoc\s*\d+)\b|(?:^|\n)\s*(?:\d+[\.\)]|[-*])\s+\S/gi) ?? []).length;
    const stepFeat = Math.min(1.0, stepHits * 0.45);

    const constrHits = (folded.match(/\b(?:must|never|always|only|at most|at least|without|strictly|constraint|require|ensure|preserve|backward compatible|zero-focus|phai|khong duoc|chi duoc|toi da|toi thieu|bat buoc|dam bao|giu nguyen)\b/g) ?? []).length;
    const constrFeat = Math.min(1.0, constrHits * 0.35);

    const qCount = (raw.match(/\?/g) ?? []).length;
    const multiClauseQ = Math.min(1.0, Math.max(0, qCount - 1) * 0.45 + (qCount === 1 && charLen >= 70 ? 0.35 : 0.0));
    const fmtFeat = /\b(?:json|yaml|markdown|table|csv|schema|patch|diff|script|function|class|bang|dinh dang)\b/.test(folded) ? 0.55 : 0.0;

    const simpleRe = /^(?:what is|what's|whats|what are|who is|who's|when is|when did|where is|define|meaning of|translate|convert|spell|capital of|how old|how tall|how many|summarize briefly)\b|\b(?:la gi|nghia la gi|thu do cua|dich sang|viet tat cua|ai la|o dau|khi nao|bao nhieu)\b/;
    const simpleMatch = simpleRe.test(folded);
    let simpleFeat = 0.0;
    if (simpleMatch && reasonHits === 0 && !hasFencedCode && wordCount <= 14)
        simpleFeat = 1.0;
    else if (wordCount <= 5 && qCount <= 1 && reasonHits === 0 && !hasFencedCode)
        simpleFeat = 0.65;

    const rawScore = 0.22
        + 0.28 * reasonFeat
        + 0.24 * codeFeat
        + 0.16 * techFeat
        + 0.12 * tokenFeat
        + 0.10 * stepFeat
        + 0.08 * constrFeat
        + 0.06 * multiClauseQ
        + 0.04 * fmtFeat
        - 0.36 * simpleFeat;
    let score = Math.max(0.0, Math.min(1.0, rawScore));

    let effort = "off";
    if (exhaustiveHit && (charLen >= 260 || reasonHits >= 2 || hasFencedCode)) {
        effort = "max";
        score = Math.max(score, 0.78);
    } else if (exhaustiveHit || reasonHits >= 2 || (reasonHits >= 1 && (hasFencedCode || techHits >= 2 || charLen >= 200))) {
        effort = "high";
        score = Math.max(score, 0.60);
    } else if (score >= 0.72) {
        effort = "max";
    } else if (score >= 0.50) {
        effort = "high";
    } else if (score >= 0.32) {
        effort = "medium";
    } else if (score >= 0.17) {
        effort = "low";
    }

    const boundaries = [0.17, 0.32, 0.50, 0.72];
    let margin = 1.0;
    for (let i = 0; i < boundaries.length; ++i)
        margin = Math.min(margin, Math.abs(score - boundaries[i]));
    const confidence = Math.round(sigmoid(margin * 12.0) * 1000) / 1000;

    if (confidence < 0.70 && effort === "max" && !exhaustiveHit)
        effort = "high";

    const followupRe = /^(?:why|how|why though|but why|explain more|elaborate|go deeper|details|more details|continue|go on|keep going|fix it|fix that|do it|check again|verify|prove it|tai sao|vi sao|tai sao vay|vi sao vay|giai thich them|giai thich ro hon|noi ro hon|chi tiet hon|tiep tuc|sua di|sua lai|lam di|kiem tra lai)[?!.,\s]*$/;
    if (wordCount <= 14 && followupRe.test(folded) && ["medium", "high", "xhigh", "max"].includes(prevEffort)) {
        const decayFloor = (prevEffort === "xhigh" || prevEffort === "max") ? "high" : prevEffort === "high" ? "medium" : "low";
        if ((EFFORT_RANK[effort] ?? 0) < (EFFORT_RANK[decayFloor] ?? 0))
            effort = decayFloor;
    }

    if ((EFFORT_RANK[effort] ?? 0) < (EFFORT_RANK[minFloor] ?? 0))
        effort = minFloor;

    if (fromVoice) {
        if (effort === "max")
            effort = "high";
        else if (effort === "high" && reasonFeat < 0.62)
            effort = "medium";
    }

    return {
        effort: effort,
        score: Math.round(score * 10000) / 10000,
        reasoning: Math.round(reasonFeat * 10000) / 10000,
        confidence: confidence,
        source: "weighted"
    };
}

function resolveEffort(rawPrompt, fromVoice = false, meta = null) {
    const res = classify(rawPrompt, Object.assign({ fromVoice: Boolean(fromVoice) }, meta || {}));
    return String(res.effort || "off");
}
