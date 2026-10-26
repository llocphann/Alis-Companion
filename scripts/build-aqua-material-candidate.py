#!/usr/bin/env python3
"""Build the strict-lossless Aqua bubble-ray shader source candidate."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

if len(sys.argv) != 2:
    raise SystemExit("usage: build-aqua-material-candidate.py OUTPUT")

src = ROOT / "modules/abyss/companion/WaterDropletMaterial.frag"
text = src.read_text(encoding="utf-8")
old = """            float along=dot(bubble-surface,internal);
            float offset=length(surface+internal*along-bubble);
            float radius=0.007+hash(vec2(index,4.0))*0.018;
            float edge=exp(-abs(offset-radius)*170.0)*step(0.0,along)*step(along,travel);
            float attenuation=exp(-along*0.70);
            color+=mix(waterLight,white,0.25)*edge*0.30*attenuation;
            float glint=exp(-offset*offset/(radius*radius*0.08))
                *step(0.0,along)*step(along,travel);
            color+=mix(waterLight,white,0.20)*glint*0.50*attenuation;
"""
new = """            float along=dot(bubble-surface,internal);
            // Both old bubble contributions are multiplied by
            // step(0,along)*step(along,travel). Outside that closed segment
            // they are exactly zero, so skip the expensive distance/hash/exp
            // path without changing either boundary case.
            if(along<0.0 || along>travel) continue;
            float offset=length(surface+internal*along-bubble);
            float radius=0.007+hash(vec2(index,4.0))*0.018;
            float edge=exp(-abs(offset-radius)*170.0);
            float attenuation=exp(-along*0.70);
            color+=mix(waterLight,white,0.25)*edge*0.30*attenuation;
            float glint=exp(-offset*offset/(radius*radius*0.08));
            color+=mix(waterLight,white,0.20)*glint*0.50*attenuation;
"""
if text.count(old) != 1:
    raise SystemExit("unexpected WaterDropletMaterial bubble block")
out = Path(sys.argv[1])
out.parent.mkdir(parents=True, exist_ok=True)
# Evidence is immutable: never overwrite a previously measured candidate.
with out.open("x", encoding="utf-8") as stream:
    stream.write(text.replace(old, new))
