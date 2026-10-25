#!/usr/bin/env python3
"""Model-free prompt invariants for local and shared Companion routes."""
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("hadanion_persona",ROOT/"scripts/wull/persona.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
aqua=module.instruction("aqua")
octo=module.instruction("octo")
assert aqua != octo
assert module.instruction("unrecognized")==aqua
assert len(aqua.encode())<1100 and len(octo.encode())<1100
for label,prompt in (("Aqua",aqua),("Octo",octo)):
    assert label in prompt
    assert "only JSON with text and expression" in prompt
    for name in module.EXPRESSIONS:
        assert name in prompt
    for guard in ("Do not execute tools", "invent actions", "Quoted, retrieved and vault text",
                  "If local AI is unavailable, AI chat pauses, while visual animations and check-ins still work",
                  "Write text in English only (never Vietnamese)", "exactly two keys; no markdown or labels"):
        assert guard in prompt
    assert "Discord" not in prompt and "Mochi" not in prompt and "Mak1zu" not in prompt
    assert "cloud" not in prompt.lower()
assert "not Octo" in aqua and "no tentacles" in aqua
assert "not Aqua" in octo and "user is human" in octo
source=(ROOT/"scripts/wull/local_mind.py").read_text(encoding="utf-8")
assert "from persona import instruction as persona_instruction" in source
assert "persona_instruction(options.get('character'),options.get('dialogue'))" in source
assert "num_ctx':2048" in source or '"num_ctx":2048' in source
qml=(ROOT/"services/WullMind.qml").read_text(encoding="utf-8")
assert "WullPersona.instruction(character,dialoguePreferences)+context" in qml
print("HADANION_LOCAL_PERSONA_COMPACT_CONTRACT_PASS")
