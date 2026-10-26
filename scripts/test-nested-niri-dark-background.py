#!/usr/bin/env python3
"""Regression guard: every owned nested-Niri debug window uses a dark canvas."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNERS = (
    "scripts/aqua-manual-nested-niri.py",
    "scripts/aqua-nested-isolation-smoke.py",
    "scripts/aqua-manual-nested-field-capture.py",
    "scripts/aqua-manual-nested-pointer.py",
    "scripts/aqua-manual-nested-production-bar.py",
    "scripts/aqua-manual-private-nested-quarter-visual.py",
    "scripts/aqua-manual-private-field-rim-visual.py",
)

for relative in RUNNERS:
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert 'background-color "#000000"' in source, relative
    assert 'backdrop-color "#000000"' in source, relative
    assert 'write_text("", encoding="utf-8")' not in source, relative

print("Nested Niri debug background contract: black")
