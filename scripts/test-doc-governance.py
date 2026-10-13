#!/usr/bin/env python3
"""Fail-closed offline guard for active/retired Hadanion documentation.

No network or dependencies. Checks curated active Markdown links and that
pre-cleanup imports stay byte-identical in the archive, not active roadmaps.
"""
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
VISUAL = Path("to-do/cloud-bot/ABYSS_WATER_DROPLET_COMPANION.md")
AI = Path("to-do/cloud-bot/WULL_LOCAL_AI.md")
ARCHIVES = {
    "to-do/archive/ABYSS_WATER_DROPLET_COMPANION_PRE_CLEANUP_20261009.md":
        "411532f45e330b8a9ed3bcf19ea39e227bb26390",
    "to-do/archive/WULL_LOCAL_AI_PRE_CLEANUP_20261009.md":
        "69882457bdf83b17f9a981ad6c99ca975bb0c728",
    "docs/archive/COMPANION_MAK1ZU_RESEARCH_20261008.md":
        "7d7c2575ea4274fc8d1c0674ea2a56fabe994579",
}
ACTIVE = (
    "README.md", "docs/README.md", "docs/HADALIS_EXTRACTION.md",
    "docs/HADANION_COMPANION_SYNTHESIS_20261009.md",
    "docs/HADANION_RENDERER_OPTIMIZATION_DECISION_20261008.md",
    "to-do/README.md", "to-do/cloud-bot/README.md",
    str(VISUAL), str(AI),
    "to-do/archive/README.md", "docs/archive/README.md",
)
# URL schemes, fragments and external GitHub URLs are not file paths.
MD_LINK = re.compile(r"!?\[[^\]\n]+\]\(([^)\n]+)\)")
NOT_LOCAL = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|#|/)")
LEGACY_HEADERS = (
    "## Implementation phases", "## 17. Detailed implementation phases",
    "## Checkpoint — 2026-10-01", "## Checkpoint — 2026-10-02",
    "## 24. Research pass", "## 25. Research pass",
)


def blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def main():
    for relative, expected in ARCHIVES.items():
        target = ROOT / relative
        assert target.is_file(), "missing immutable archive: " + relative
        assert blob_sha(target.read_bytes()) == expected, "mutated archived source: " + relative
    assert not (ROOT / "docs/COMPANION_MAK1ZU_RESEARCH_20261008.md").exists(), \
        "retired Mak1zu roadmap returned to active docs"
    source = (ROOT / VISUAL).read_text(encoding="utf-8")
    ai = (ROOT / AI).read_text(encoding="utf-8")
    for name, text in ((str(VISUAL), source), (str(AI), ai)):
        assert "github.com/llocphann/Alis/issues" in text, (
            name + ": tasks must link directly to GitHub Issues"
        )
        assert len(text.splitlines()) < 30, name + ": old task log reappeared"
        assert not re.search(r"(?m)^\\s*- \\[[ xX]\\] \\*\\*[A-Z]+-[FRI]\\d{2}", text), (
            name + ": no active Markdown task checklists"
        )
        assert not any(header in text for header in LEGACY_HEADERS)
    for rel in ACTIVE:
        file = ROOT / rel
        assert file.is_file(), "missing active documentation: " + rel
        markdown = file.read_text(encoding="utf-8")
        for match in MD_LINK.finditer(markdown):
            url = match.group(1).strip().split(" ", 1)[0]
            if NOT_LOCAL.match(url) or url.startswith("//"):
                continue
            # Keep folder moves harmless: check relative file/directory, not
            # GitHub anchor spelling or remote links.
            address = url.split("#", 1)[0].split("?", 1)[0]
            if not address:
                continue
            target = (file.parent / address).resolve()
            assert target.is_relative_to(ROOT.resolve()), "link escapes repository: " + rel + " → " + url
            assert target.exists(), "broken active doc link: " + rel + " → " + url
        if rel.startswith("to-do/cloud-bot/"):
            assert "archive/COMPANION_MAK1ZU" not in markdown, \
                "legacy Mak1zu research must not be promoted to an active task"
    for rel in (str(VISUAL), str(AI)):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "git clone" not in source or "Hadanion" in source
    print("HADANION_DOC_GOVERNANCE_PASS (2 GitHub Issue redirects; 3 immutable archives; "
          + str(len(ACTIVE)) + " checked entrypoints)")


if __name__ == "__main__":
    main()
