"""Agent Skills (`SKILL.md`) discovery, progressive disclosure, self-learning, and execution for Alis-Companion.

Inspired by the Hermes Agent closed self-learning loop:
- Progressive disclosure: Level 0 compact index (`list_skills`), Level 1 full `SKILL.md` (`inspect_skill`),
  Level 2 on-demand `references/` documents (`read_skill_reference`).
- Living skills: `patch_skill` (copy-on-write for built-ins, targeted span replacement or learned note
  appending), `learn_skill` (`/learn` from paths, instructions, or recent session trajectories),
  and autonomous execution telemetry & workflow reflection (`record_skill_feedback`, `auto_reflect_run`).
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from sandbox_runner import SandboxError, run_sandboxed


class SkillError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    folder = (base / "alis-companion").resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def _user_skills_dir() -> Path:
    d = _state_dir() / "skills"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _skills_state_file() -> Path:
    return _state_dir() / "skills_state.json"


def _skills_telemetry_file() -> Path:
    return _state_dir() / "skills_telemetry.json"


def _workflow_reflections_file() -> Path:
    return _state_dir() / "workflow_reflections.json"


def _atomic_write_json(path: Path, data: dict | list) -> None:
    payload = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise SkillError("skill_store_unavailable", f"Could not save {path.name}.") from exc


def _load_disabled_set() -> set[str]:
    path = _skills_state_file()
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        disabled = raw.get("disabled", []) if isinstance(raw, dict) else []
        return {str(x).strip().lower() for x in disabled if x}
    except (OSError, ValueError):
        return set()


def _save_disabled_set(disabled: set[str]) -> None:
    _atomic_write_json(_skills_state_file(), {"version": 1, "disabled": sorted(disabled)[:128]})


def _load_telemetry() -> dict[str, dict]:
    path = _skills_telemetry_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        skills = raw.get("skills", {}) if isinstance(raw, dict) else {}
        return skills if isinstance(skills, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_telemetry(telemetry: dict[str, dict]) -> None:
    _atomic_write_json(_skills_telemetry_file(), {"version": 1, "skills": telemetry})


def _load_reflections() -> list[dict]:
    path = _workflow_reflections_file()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("reflections", []) if isinstance(raw, dict) else []
        out: list[dict] = []
        for x in items:
            if not isinstance(x, dict):
                continue
            item = dict(x)
            ops = [str(op).strip() for op in (item.get("ops") or []) if str(op).strip()]
            item["ops"] = ops
            lesson = str(item.get("lesson") or "").strip()
            if " ->  ->" in lesson or lesson.endswith("workflow:") or lesson.endswith("workflow: "):
                if ops:
                    item["lesson"] = f"Completed {len(ops)}-step workflow: " + " -> ".join(ops[:5])
                else:
                    item["lesson"] = str(item.get("summary") or "")[:240]
            out.append(item)
        return out
    except (OSError, ValueError):
        return []


def _save_reflections(items: list[dict]) -> None:
    _atomic_write_json(_workflow_reflections_file(), {"version": 1, "reflections": items[-60:]})


def _slugify(raw_name: str, fallback: str = "learned-skill") -> str:
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", str(raw_name or "").strip())
    cleaned = camel_split.lower().replace(" ", "-").replace("/", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", cleaned)
    slug = re.sub(r"-+", "-", slug)[:48].strip("-")
    return slug or fallback


def _normalize_version(raw_ver: str | int | None) -> str:
    s = str(raw_ver if raw_ver is not None else "1.0.0").strip().strip("'\"")
    if re.match(r"^\d+\.\d+\.\d+$", s):
        return s
    try:
        n = max(1, int(float(s)))
        return f"1.0.{n - 1}"
    except ValueError:
        return "1.0.0"


def _bump_version(raw_ver: str | int | None) -> str:
    norm = _normalize_version(raw_ver)
    parts = [int(x) for x in norm.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def parse_skill_markdown(raw_text: str, fallback_name: str = "") -> dict:
    text = str(raw_text or "").replace("\r\n", "\n")
    name = fallback_name
    description = ""
    triggers: list[str] = []
    command = ""
    category = "general"
    version = "1.0.0"
    body = text.strip()

    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)(.*)$", text, re.DOTALL)
    if m:
        front, body = m.group(1), m.group(2).strip()
        for line in front.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            key = k.strip().lower()
            val = v.strip().strip("'\"")
            if key == "name" and val:
                name = val[:64]
            elif key == "description" and val:
                description = val[:300]
            elif key == "command" and val:
                command = val[:160]
            elif key == "category" and val:
                category = val[:40].lower()
            elif key == "version" and val:
                version = _normalize_version(val)
            elif key == "triggers":
                raw_tr = v.strip()
                if raw_tr.startswith("[") and raw_tr.endswith("]"):
                    try:
                        parsed_tr = json.loads(raw_tr)
                        if isinstance(parsed_tr, list):
                            triggers = [str(x).strip().lower()[:60] for x in parsed_tr if str(x).strip()]
                    except ValueError:
                        triggers = [
                            x.strip().strip("'\"").lower()[:60]
                            for x in raw_tr[1:-1].split(",")
                            if x.strip().strip("'\"")
                        ]
                elif val:
                    triggers = [x.strip().lower()[:60] for x in val.split(",") if x.strip()]

    if not description and body:
        for ln in body.splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                description = s[:240]
                break

    return {
        "name": (name or fallback_name or "skill")[:64],
        "description": description[:300],
        "triggers": triggers[:16],
        "command": command[:160],
        "category": category or "general",
        "version": version,
        "instructions": body[:4800],
    }


def _format_skill_markdown(
    *,
    name: str,
    description: str,
    triggers: list[str],
    instructions: str,
    command: str = "",
    category: str = "general",
    version: str | int = "1.0.0",
) -> str:
    ver_str = _normalize_version(version)
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"category: {category or 'general'}",
        f"version: {ver_str}",
        f"triggers: {json.dumps(triggers[:16], ensure_ascii=False)}",
    ]
    if command:
        lines.append(f"command: {command}")
    lines.append("---")
    lines.append("")
    lines.append(instructions.strip())
    lines.append("")
    return "\n".join(lines)


def _skill_roots(companion_root: Path | None = None) -> list[tuple[str, Path]]:
    comp = (companion_root or Path(__file__).resolve().parents[2]).resolve(strict=False)
    cfg_base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    roots = [
        ("user", _user_skills_dir()),
        ("config", (cfg_base / "alis-companion/skills").resolve(strict=False)),
        ("builtin", (Path(__file__).resolve().parent / "skills").resolve(strict=False)),
        ("builtin", (comp / "skills").resolve(strict=False)),
    ]
    shared_agents = Path.home() / ".agents/skills"
    if shared_agents.is_dir():
        roots.append(("shared", shared_agents.resolve(strict=False)))
    return roots


def list_skills(companion_root: Path | None = None, include_disabled: bool = True) -> list[dict]:
    disabled = _load_disabled_set()
    telemetry = _load_telemetry()
    discovered: dict[str, dict] = {}
    for source_kind, root_dir in _skill_roots(companion_root):
        if not root_dir.is_dir():
            continue
        for child in sorted(root_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file() or skill_md.is_symlink():
                continue
            try:
                if skill_md.stat().st_size > 256 * 1024:
                    continue
                raw = skill_md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parsed = parse_skill_markdown(raw, fallback_name=child.name)
            sname = parsed["name"].lower()
            if sname in discovered:
                continue
            is_enabled = sname not in disabled
            if not include_disabled and not is_enabled:
                continue
            scripts_dir = child / "scripts"
            scripts = []
            if scripts_dir.is_dir() and not scripts_dir.is_symlink():
                for sf in sorted(scripts_dir.iterdir()):
                    if sf.is_file() and not sf.is_symlink() and sf.suffix in (".py", ".sh"):
                        scripts.append(sf.name)
            refs_dir = child / "references"
            references = []
            if refs_dir.is_dir() and not refs_dir.is_symlink():
                for rf in sorted(refs_dir.iterdir()):
                    if rf.is_file() and not rf.is_symlink() and rf.suffix in (".md", ".txt", ".json"):
                        references.append(rf.name)
            stat = telemetry.get(sname, {}) if isinstance(telemetry.get(sname), dict) else {}
            discovered[sname] = {
                "name": parsed["name"],
                "description": parsed["description"],
                "triggers": parsed["triggers"],
                "command": parsed["command"],
                "category": parsed["category"],
                "version": parsed["version"],
                "source": source_kind,
                "builtIn": (source_kind == "builtin"),
                "enabled": is_enabled,
                "path": str(skill_md),
                "folder": str(child),
                "scripts": scripts[:12],
                "references": references[:20],
                "usageCount": int(stat.get("usageCount") or 0),
                "successCount": int(stat.get("successCount") or 0),
                "lastUsedMs": int(stat.get("lastUsedMs") or 0),
                "learnedNotes": list(stat.get("learnedNotes") or [])[-8:],
                "instructions": parsed["instructions"],
            }
    return list(discovered.values())


def inspect_skill(name: str, companion_root: Path | None = None) -> dict:
    q = str(name or "").strip().lower().lstrip("/")
    if not q:
        raise SkillError("invalid_skill_name", "Specify a skill name to inspect.")
    for s in list_skills(companion_root, include_disabled=True):
        if s["name"].lower() == q or Path(s["folder"]).name.lower() == q:
            return s
    raise SkillError("skill_not_found", f"Skill '{name}' was not found.")


def read_skill_reference(name: str, reference_file: str, companion_root: Path | None = None) -> dict:
    """Progressive disclosure Level 2: load a reference document under `<skill>/references/`."""
    skill = inspect_skill(name, companion_root)
    raw_ref = str(reference_file or "").strip()
    if not raw_ref or ".." in raw_ref or "/" in raw_ref or "\\" in raw_ref:
        raise SkillError("invalid_reference_path", "Reference file name must be a safe filename inside references/.")
    ref_clean = Path(raw_ref).name
    if not ref_clean or ref_clean in (".", ".."):
        raise SkillError("invalid_reference_path", "Specify a reference file name inside references/.")
    refs_dir = (Path(skill["folder"]) / "references").resolve()
    target = (refs_dir / ref_clean).resolve()
    if not target.is_relative_to(refs_dir) or not target.is_file() or target.is_symlink():
        raise SkillError("reference_not_found", f"Reference '{ref_clean}' not found in skill '{skill['name']}'.")
    content = target.read_text(encoding="utf-8", errors="replace")[:8000]
    return {
        "skill": skill["name"],
        "reference": ref_clean,
        "content": content,
        "availableReferences": skill.get("references", []),
    }


def write_skill_reference(name: str, reference_file: str, content: str, companion_root: Path | None = None) -> dict:
    """Write or update a progressive-disclosure reference file under `<user_skill>/references/`."""
    skill = _ensure_user_writable_skill(name, companion_root)
    raw_ref = str(reference_file or "notes.md").strip()
    if ".." in raw_ref or "/" in raw_ref or "\\" in raw_ref:
        raise SkillError("invalid_reference_path", "Reference file name must not contain path traversal.")
    ref_clean = re.sub(r"[^a-zA-Z0-9._\-]", "-", Path(raw_ref).name)[:64]
    if not ref_clean.endswith((".md", ".txt", ".json")):
        ref_clean += ".md"
    refs_dir = Path(skill["folder"]) / "references"
    refs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = refs_dir / ref_clean
    target.write_text(str(content or "").strip()[:16000] + "\n", encoding="utf-8")
    return inspect_skill(skill["name"], companion_root)


def match_skills(prompt: str, companion_root: Path | None = None, max_results: int = 2) -> list[dict]:
    raw = str(prompt or "").strip().lower()
    if not raw:
        return []
    stop_desc = {
        "for", "and", "the", "with", "from", "into", "when", "use", "user", "companion",
        "linux", "file", "files", "folder", "folders", "code", "tool", "tools", "skill",
    }
    words = {w for w in re.findall(r"[a-zA-Z0-9_\u00C0-\u1EF9\-]{3,}", raw) if w not in stop_desc}
    scored = []
    for s in list_skills(companion_root):
        if not s.get("enabled", True):
            continue
        score = 0
        sname_low = s["name"].lower()
        if raw.startswith(f"/{sname_low}") and (len(raw) == len(sname_low) + 1 or raw[len(sname_low) + 1].isspace()):
            score += 25
        elif re.search(rf"(?:^|[\s/._\-]){re.escape(sname_low)}(?:$|[\s/._\-,:;!?])", raw):
            score += 8
        for trig in s.get("triggers", []):
            if trig and trig in raw:
                score += 5
        desc_words = {
            w for w in re.findall(r"[a-zA-Z0-9_\u00C0-\u1EF9\-]{3,}", s["description"].lower())
            if w not in stop_desc
        }
        overlap = len(words & desc_words)
        if overlap >= 2:
            score += overlap
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda item: (-item[0], item[1]["name"]))
    return [item[1] for item in scored[:max_results]]


def create_skill(
    name: str,
    description: str,
    instructions: str,
    triggers: list[str] | None = None,
    category: str = "learned",
    references: dict[str, str] | None = None,
) -> dict:
    slug = _slugify(name, fallback="")
    if not slug:
        raise SkillError("invalid_skill_name", "Skill name must contain letters or numbers.")
    desc = str(description or f"Custom Companion skill {slug}").strip()[:280]
    body = str(instructions or f"# {slug}\n\n{desc}").strip()[:4800]
    tr_list = [str(t).strip().lower()[:40] for t in (triggers or [slug, slug.replace("-", " ")]) if str(t).strip()][:12]
    if slug not in tr_list:
        tr_list.insert(0, slug)

    skill_folder = _user_skills_dir() / slug
    skill_folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    skill_md = skill_folder / "SKILL.md"
    next_version = "1.0.0"
    if skill_md.is_file():
        try:
            old_parsed = parse_skill_markdown(skill_md.read_text(encoding="utf-8", errors="replace"), fallback_name=slug)
            next_version = _bump_version(old_parsed.get("version"))
        except OSError:
            next_version = "1.0.0"

    content = _format_skill_markdown(
        name=slug,
        description=desc,
        triggers=tr_list,
        instructions=body,
        category=category or "learned",
        version=next_version,
    )
    skill_md.write_text(content, encoding="utf-8")

    if isinstance(references, dict) and references:
        refs_dir = skill_folder / "references"
        refs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for ref_name, ref_body in list(references.items())[:10]:
            safe_name = re.sub(r"[^a-zA-Z0-9._\-]", "-", str(ref_name).strip())[:64]
            if not safe_name.endswith((".md", ".txt", ".json")):
                safe_name += ".md"
            (refs_dir / safe_name).write_text(str(ref_body or "").strip()[:12000] + "\n", encoding="utf-8")

    disabled = _load_disabled_set()
    if slug in disabled:
        disabled.discard(slug)
        _save_disabled_set(disabled)
    return inspect_skill(slug)


def _ensure_user_writable_skill(name: str, companion_root: Path | None = None) -> dict:
    """Ensure a skill exists in `_user_skills_dir()`, cloning a built-in/shared skill on write."""
    target = inspect_skill(name, companion_root)
    slug = _slugify(target["name"], fallback=Path(target["folder"]).name)
    user_folder = _user_skills_dir() / slug
    src_folder = Path(target["folder"]).resolve()
    if target["source"] != "user" or src_folder != user_folder.resolve():
        user_folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for sub in ("SKILL.md",):
            sfile = src_folder / sub
            if sfile.is_file() and not sfile.is_symlink():
                shutil.copy2(sfile, user_folder / sub)
        for subdir_name in ("scripts", "references"):
            sdir = src_folder / subdir_name
            if sdir.is_dir() and not sdir.is_symlink():
                udir = user_folder / subdir_name
                udir.mkdir(mode=0o700, parents=True, exist_ok=True)
                for item in sdir.iterdir():
                    if item.is_file() and not item.is_symlink():
                        shutil.copy2(item, udir / item.name)
    return inspect_skill(slug, companion_root)


def patch_skill(
    name: str,
    *,
    old_text: str = "",
    new_text: str = "",
    append_note: str = "",
    append_section: str = "",
    description: str | None = None,
    triggers: list[str] | None = None,
    category: str | None = None,
    companion_root: Path | None = None,
) -> dict:
    """Hermes-style targeted skill update & continuous self-refinement.

    Supports copy-on-write for built-in skills so Companion can refine any skill
    based on user feedback or execution experience without mutating package files.
    """
    current = _ensure_user_writable_skill(name, companion_root)
    slug = current["name"].lower()
    skill_md = Path(current["path"])
    raw = skill_md.read_text(encoding="utf-8", errors="replace")
    parsed = parse_skill_markdown(raw, fallback_name=slug)
    body = parsed["instructions"]

    replaced = False
    if old_text and old_text.strip():
        needle = old_text.strip()
        if needle not in body:
            raise SkillError("skill_patch_mismatch", f"Target text to replace was not found in skill '{slug}'.")
        body = body.replace(needle, str(new_text or "").strip(), 1)
        replaced = True

    if append_section and append_section.strip():
        sec = append_section.strip()
        if sec not in body:
            body = f"{body.rstrip()}\n\n{sec}"

    if append_note and append_note.strip():
        note_clean = re.sub(r"\s+", " ", append_note.strip())[:320]
        header = "## Learned Adjustments & Pitfalls"
        bullet = f"- {note_clean}"
        if bullet not in body:
            if header in body:
                body = f"{body.rstrip()}\n{bullet}"
            else:
                body = f"{body.rstrip()}\n\n{header}\n{bullet}"

    merged_triggers = list(parsed["triggers"])
    if isinstance(triggers, list):
        for t in triggers:
            tc = str(t).strip().lower()[:40]
            if tc and tc not in merged_triggers:
                merged_triggers.append(tc)

    new_version = _bump_version(parsed.get("version"))
    updated_md = _format_skill_markdown(
        name=parsed["name"],
        description=(str(description).strip()[:280] if description else parsed["description"]),
        triggers=merged_triggers[:16],
        instructions=body[:4800],
        command=parsed.get("command") or "",
        category=(str(category).strip().lower()[:40] if category else parsed.get("category") or "general"),
        version=new_version,
    )
    skill_md.write_text(updated_md, encoding="utf-8")
    updated = inspect_skill(slug, companion_root)
    updated["patched"] = True
    updated["replacedSpan"] = replaced
    updated["skill"] = dict(updated)
    return updated


def record_skill_feedback(
    name: str,
    *,
    success: bool = True,
    note: str = "",
    companion_root: Path | None = None,
) -> dict:
    """Record execution outcome and optionally append a learned adjustment note to a skill."""
    skill = inspect_skill(name, companion_root)
    sname = skill["name"].lower()
    telemetry = _load_telemetry()
    entry = telemetry.get(sname) if isinstance(telemetry.get(sname), dict) else {}
    usage_count = int(entry.get("usageCount") or 0) + 1
    success_count = int(entry.get("successCount") or 0) + (1 if success else 0)
    notes = list(entry.get("learnedNotes") or [])
    note_clean = re.sub(r"\s+", " ", str(note or "").strip())[:280]
    if note_clean and note_clean not in notes:
        notes.append(note_clean)
    telemetry[sname] = {
        "usageCount": usage_count,
        "successCount": success_count,
        "lastUsedMs": int(time.time() * 1000),
        "lastSuccess": bool(success),
        "learnedNotes": notes[-12:],
    }
    _save_telemetry(telemetry)
    if note_clean:
        try:
            skill = patch_skill(sname, append_note=note_clean, companion_root=companion_root)
        except SkillError:
            pass
    else:
        skill = inspect_skill(sname, companion_root)
    return skill


def learn_skill(
    target_or_instruction: str = "",
    *,
    name: str = "",
    category: str = "learned",
    session_ctx: dict | None = None,
    recent_steps: list[dict] | None = None,
    companion_root: Path | None = None,
) -> dict:
    """Hermes-style `/learn`: synthesize or refine a reusable Skill (`SKILL.md` + optional `references/`)
    from a file/directory path, a workflow description, or recent task trajectory.
    """
    raw = str(target_or_instruction or "").strip()
    sess = session_ctx if isinstance(session_ctx, dict) else {}
    workspace = str(sess.get("workspace") or "").strip()

    # Check if `name: instruction` or `name | instruction` syntax was used
    explicit_name = str(name or "").strip()
    if not explicit_name and ":" in raw and not raw.startswith(("/", "~", "./", "../")):
        first_part, rest_part = raw.split(":", 1)
        if 1 <= len(first_part.strip().split()) <= 3 and len(rest_part.strip()) >= 8:
            cand_slug = _slugify(first_part.strip(), fallback="")
            if cand_slug and not Path(raw).expanduser().exists():
                explicit_name = cand_slug
                raw = rest_part.strip()

    # Mode 1: Path / Directory / Codebase exploration -> Skill + Level 2 reference map
    cand_path: Path | None = None
    path_token = raw.split()[0] if raw else ""
    if path_token and (path_token.startswith(("/", "~", "./", "../")) or "/" in path_token):
        p = Path(path_token).expanduser()
        if not p.is_absolute() and workspace:
            p = (Path(workspace).expanduser() / p).resolve(strict=False)
        else:
            p = p.resolve(strict=False)
        if p.exists():
            cand_path = p

    if cand_path is not None:
        slug = _slugify(explicit_name or f"{cand_path.stem or cand_path.name}-workflow")
        extra_notes = raw[len(path_token):].strip(" :-|")
        ref_docs: dict[str, str] = {}
        if cand_path.is_dir():
            files_summary: list[str] = []
            key_files: list[str] = []
            for item in sorted(cand_path.rglob("*")):
                if len(files_summary) >= 40:
                    break
                if item.is_dir() or any(part.startswith(".") or part == "__pycache__" for part in item.parts):
                    continue
                rel = str(item.relative_to(cand_path))
                files_summary.append(f"- `{rel}` ({item.stat().st_size} bytes)")
                if item.name.lower() in ("readme.md", "agents.md", "makefile", "package.json", "pyproject.toml") or len(key_files) < 5:
                    key_files.append(rel)
            ref_docs["source-map.md"] = (
                f"# Source Map for `{cand_path}`\n\n"
                + ("\n".join(files_summary) if files_summary else "No regular files indexed.")
            )
            desc = f"Learned domain & workflow guide for directory {cand_path.name} ({len(files_summary)} files)"
            body = (
                f"# {slug}\n\n"
                f"## When to Use\n"
                f"- Working inside `{cand_path}` or modifying `{cand_path.name}` components.\n\n"
                f"## Key Files & Structure\n"
                + ("\n".join(f"- `{kf}`" for kf in key_files[:8]) if key_files else f"- Root: `{cand_path}`")
                + "\n- See `references/source-map.md` for the full file inventory.\n\n"
                f"## Standard Workflow\n"
                f"1. Inspect key files and entrypoints before making edits.\n"
                f"2. Preserve existing public contracts, data formats, and tests.\n"
                f"3. Run scoped verification after edits.\n"
            )
            if extra_notes:
                body += f"\n## Learned Adjustments & Pitfalls\n- {extra_notes}\n"
        else:
            try:
                snippet = cand_path.read_text(encoding="utf-8", errors="replace")[:2000].strip()
            except OSError:
                snippet = ""
            ref_docs["file-excerpt.md"] = f"# Excerpt of `{cand_path.name}`\n\n```\n{snippet}\n```"
            desc = f"Learned workflow & conventions from {cand_path.name}"
            body = (
                f"# {slug}\n\n"
                f"## When to Use\n"
                f"- Tasks involving `{cand_path.name}` (`{cand_path}`).\n\n"
                f"## Workflow & Conventions\n"
                f"1. Reference `{cand_path}` and `references/file-excerpt.md`.\n"
                f"2. Keep changes consistent with surrounding conventions.\n"
            )
            if extra_notes:
                body += f"\n## Learned Adjustments & Pitfalls\n- {extra_notes}\n"

        skill = create_skill(
            name=slug,
            description=desc,
            instructions=body,
            triggers=[slug, cand_path.name.lower(), cand_path.stem.lower()],
            category="domain",
            references=ref_docs,
        )
        return {"mode": "path", "action": "created", "skill": skill}

    # Mode 2: Trajectory / Recent Session Reflection (`/learn` with empty arg or "from recent/last task")
    low = raw.lower()
    is_trajectory_req = (
        not raw
        or low in ("last", "recent", "session", "trajectory", "workflow", "tác vụ vừa rồi", "rút kinh nghiệm")
        or any(p in low for p in ("from last", "from recent", "từ tác vụ", "tự học từ"))
    )
    if is_trajectory_req:
        steps = list(recent_steps or [])
        if not steps:
            try:
                from run_store import list_recent_runs
                runs = list_recent_runs(limit=3)
                for r in runs:
                    for st in r.get("steps") or []:
                        if isinstance(st, dict):
                            steps.append(st)
                    if steps:
                        break
            except Exception:
                steps = []
        history = [h for h in (sess.get("history") or []) if isinstance(h, dict)]
        last_user_msgs = [str(h.get("content") or "").strip() for h in history if h.get("role") == "user" and str(h.get("content") or "").strip()]
        topic_hint = explicit_name or (last_user_msgs[-1][:40] if last_user_msgs else "session-workflow")
        slug = _slugify(explicit_name or "adaptive-workflow")
        step_lines = []
        for idx, st in enumerate(steps[:8], 1):
            op_name = str(st.get("op") or st.get("id") or "step")
            st_detail = str(st.get("detail") or st.get("target") or "").strip()
            step_lines.append(f"{idx}. `{op_name}` — {st_detail[:140]}" if st_detail else f"{idx}. `{op_name}`")
        if not step_lines:
            step_lines = [
                "1. Inspect context and active workspace state before modifying files.",
                "2. Execute targeted operations and verify outputs step by step.",
                "3. Record newly discovered pitfalls via `/learn` or `skill_manage(patch)`.",
            ]
        desc = f"Self-learned workflow synthesized from session experience ({topic_hint[:60]})"
        body = (
            f"# {slug}\n\n"
            f"## When to Use\n"
            f"- Repeating or adapting workflow for: {topic_hint[:120]}\n\n"
            f"## Learned Procedure\n"
            + "\n".join(step_lines)
            + "\n\n## Learned Adjustments & Pitfalls\n"
            + (f"- {raw}\n" if raw and raw not in ("last", "recent", "session", "trajectory") else "- Verify tool outputs and update this skill when environment paths change.\n")
        )
        try:
            existing = inspect_skill(slug, companion_root)
            skill = patch_skill(
                existing["name"],
                append_note=f"Trajectory update ({topic_hint[:80]}): " + "; ".join(step_lines[:3]),
                companion_root=companion_root,
            )
            return {"mode": "trajectory", "action": "patched", "skill": skill}
        except SkillError:
            skill = create_skill(
                name=slug,
                description=desc,
                instructions=body,
                triggers=[slug, "workflow", "adaptive"],
                category="workflow",
            )
            return {"mode": "trajectory", "action": "created", "skill": skill}

    # Mode 3: Topic / Workflow Instruction — either patch an existing matching skill or create a new skill
    target_skill: dict | None = None
    if explicit_name:
        try:
            target_skill = inspect_skill(explicit_name, companion_root)
        except SkillError:
            target_skill = None
    if target_skill is None:
        matched = match_skills(raw, companion_root, max_results=1)
        if matched and (
            matched[0]["name"].lower() in low
            or any(k in low for k in ("update ", "patch ", "adjust ", "cập nhật", "điều chỉnh", "sửa skill", "bổ sung"))
        ):
            target_skill = matched[0]

    if target_skill is not None:
        patched = patch_skill(
            target_skill["name"],
            append_note=raw,
            companion_root=companion_root,
        )
        return {"mode": "instruction", "action": "patched", "skill": patched}

    # Otherwise synthesize a brand-new structured skill from the instruction
    words = [w for w in re.findall(r"[a-zA-Z0-9]{3,}", explicit_name or raw.lower()) if w not in {"the", "and", "for", "with", "when", "always", "never", "skill", "learn", "workflow"}]
    slug = _slugify(explicit_name or "-".join(words[:3]) or "custom-workflow")
    raw_steps = [s.strip() for s in re.split(r"(?:\n+|;\s*|\s+->\s+|\s+then\s+)", raw) if s.strip()]
    if len(raw_steps) <= 1:
        raw_steps = [raw]
    numbered = "\n".join(f"{i}. {step}" for i, step in enumerate(raw_steps[:10], 1))
    desc = raw[:180]
    triggers = list(dict.fromkeys([slug, slug.replace("-", " ")] + [w.lower() for w in words[:5]]))
    body = (
        f"# {slug}\n\n"
        f"## When to Use\n"
        f"- {desc}\n\n"
        f"## Procedure & Workflow\n"
        f"{numbered}\n\n"
        f"## Learned Adjustments & Pitfalls\n"
        f"- Created via Companion self-learning (`/learn`). Refine with `/learn {slug}: <adjustment>` as workflows evolve.\n"
    )
    created = create_skill(
        name=slug,
        description=desc,
        instructions=body,
        triggers=triggers,
        category=category or "learned",
    )
    return {"mode": "instruction", "action": "created", "skill": created}


def auto_reflect_run(
    prompt: str,
    status: str,
    summary: str,
    steps: list[dict],
    matched_skills: list[dict] | None = None,
    companion_root: Path | None = None,
) -> dict:
    """Hermes-style post-turn reflection: updates skill telemetry and logs procedural lessons
    from multi-step runs or error-recovery sequences.
    """
    success = status == "completed"
    skills_updated: list[str] = []
    step_list = [s for s in (steps or []) if isinstance(s, dict)]

    def _step_op_name(s: dict) -> str:
        return str(s.get("op") or s.get("toolName") or s.get("name") or s.get("id") or "").strip()

    # Detect if the run recovered from an intermediate failed step
    failed_ops = [_step_op_name(s) for s in step_list if str(s.get("status") or "") in ("failed", "error") and _step_op_name(s)]
    succeeded_ops = [_step_op_name(s) for s in step_list if str(s.get("status") or "") == "completed" and _step_op_name(s)]
    recovery_note = ""
    if failed_ops and succeeded_ops and success:
        recovery_note = f"Recovered after {', '.join(failed_ops[:2])} by running {', '.join(succeeded_ops[-2:])}."

    for sk in matched_skills or []:
        sname = str(sk.get("name") or "").strip()
        if not sname:
            continue
        try:
            record_skill_feedback(
                sname,
                success=success,
                note=recovery_note,
                companion_root=companion_root,
            )
            skills_updated.append(sname)
        except SkillError:
            pass

    reflection_saved = False
    if len(step_list) >= 2 or recovery_note or skills_updated:
        reflections = _load_reflections()
        entry = {
            "id": f"ref_{int(time.time() * 1000)}",
            "tsMs": int(time.time() * 1000),
            "prompt": str(prompt or "")[:200],
            "status": str(status or "completed"),
            "ops": [_step_op_name(s) for s in step_list[:10] if _step_op_name(s)],
            "summary": str(summary or "")[:240],
            "lesson": recovery_note or (f"Completed {len(step_list)}-step workflow: " + " -> ".join(succeeded_ops[:5]) if succeeded_ops else ""),
            "skillsUpdated": skills_updated,
        }
        reflections.append(entry)
        try:
            _save_reflections(reflections)
            reflection_saved = True
        except SkillError:
            reflection_saved = False

    return {
        "reflected": reflection_saved,
        "skillsUpdated": skills_updated,
        "recoveryNote": recovery_note,
    }


def list_workflow_reflections(limit: int = 12) -> list[dict]:
    reflections = _load_reflections()
    return list(reversed(reflections[-max(1, min(50, int(limit))):]))


def set_skill_enabled(name: str, enabled: bool, companion_root: Path | None = None) -> dict:
    target = inspect_skill(name, companion_root)
    slug = target["name"].lower()
    disabled = _load_disabled_set()
    if enabled:
        disabled.discard(slug)
    else:
        disabled.add(slug)
    _save_disabled_set(disabled)
    target["enabled"] = bool(enabled)
    return target


def delete_skill(name: str, companion_root: Path | None = None) -> bool:
    target = inspect_skill(name, companion_root)
    if target["source"] != "user":
        raise SkillError("builtin_skill_protected", "Built-in or system skills cannot be deleted (use disable instead).")
    folder = Path(target["folder"]).resolve()
    if folder.is_relative_to(_user_skills_dir().resolve()) and folder.is_dir():
        shutil.rmtree(folder)
        return True
    return False


def run_skill(
    name: str,
    prompt: str = "",
    companion_root: Path | None = None,
    workspace: str | None = None,
) -> dict:
    comp_root = companion_root or Path(__file__).resolve().parents[2]
    skill = inspect_skill(name, comp_root)
    if not skill.get("enabled", True):
        raise SkillError("skill_disabled", f"Skill '{skill['name']}' is currently disabled.")

    folder = Path(skill["folder"]).resolve()
    scripts = skill.get("scripts") or []
    cmd_script = skill.get("command") or (scripts[0] if scripts else "")

    output_lines: list[str] = []
    executed_script = ""
    script_ok = True

    if cmd_script:
        cand = (folder / "scripts" / cmd_script).resolve() if not (folder / cmd_script).is_file() else (folder / cmd_script).resolve()
        if cand.is_file() and cand.is_relative_to(folder):
            runner = ["python3", str(cand)] if cand.suffix == ".py" else ["sh", str(cand)]
            if prompt:
                runner.append(prompt[:400])
            cwd_dir = Path(workspace).expanduser().resolve(strict=False) if workspace else Path.home()
            if not cwd_dir.is_dir():
                cwd_dir = Path.home()
            try:
                res = run_sandboxed(
                    runner,
                    cwd=cwd_dir,
                    writable_dirs=[cwd_dir],
                    readonly_dirs=[folder, comp_root, cwd_dir],
                    allow_network=False,
                    timeout_sec=10.0,
                )
                executed_script = cand.name
                output_lines.extend(res.get("lines") or [])
            except SandboxError as exc:
                script_ok = False
                output_lines.append(f"Script error: {exc}")

    # Built-in domain enrichments when running built-in skills without external arguments
    sname = skill["name"].lower()
    ponytail_intensity = "full"
    if sname in ("niri-wm", "quickshell-qml", "arch-linux"):
        try:
            import domain_rag
            dom_map = {"niri-wm": ["niri"], "quickshell-qml": ["quickshell"], "arch-linux": ["archlinux"]}
            q_text = prompt or skill["description"]
            d_rag = domain_rag.query_domain_rag(q_text, domains=dom_map[sname], companion_root=comp_root)
            if d_rag.get("contextMarkdown"):
                output_lines.append(d_rag["contextMarkdown"])
        except Exception:
            pass
    elif sname == "rag-researcher" and prompt:
        from code_tools import retrieve_rag
        rag = retrieve_rag(prompt, companion_root=comp_root, page=1, page_size=3)
        if rag["snippets"]:
            output_lines.append("RAG Citations:")
            output_lines.extend(f"  {s}" for s in rag["snippets"])
    elif sname == "code-assistant":
        from code_tools import build_repo_map
        target_ws = workspace or str(comp_root)
        try:
            rmap = build_repo_map(target_ws, token_budget=300)
            output_lines.append(f"Workspace `{Path(target_ws).name}` ({rmap['fileCount']} files):")
            output_lines.extend(f"  {e['summary']}" for e in rmap["entries"][:8])
        except Exception:
            pass
    elif sname == "screen-copilot":
        try:
            from screen_context import observe_active_window
            obs = observe_active_window(allow_screenshot=False, skip_ocr=True)
            output_lines.append(f"Active Window: `{obs.get('packageId', 'desktop')}` (source: `{obs.get('version', 'metadata')}`)")
            for h in (obs.get("headings") or [])[:8]:
                output_lines.append(f"  - {h}")
        except Exception:
            pass
    elif sname == "desktop-organizer":
        target_dir = Path(workspace).expanduser() if workspace and Path(workspace).expanduser().is_dir() else (Path.home() / "Downloads")
        if not target_dir.is_dir():
            target_dir = Path.home()
        try:
            cats: dict[str, int] = {"images": 0, "videos": 0, "archives": 0, "docs": 0, "code": 0, "other": 0}
            largest: list[tuple[int, str]] = []
            for item in sorted(target_dir.iterdir()):
                if item.name.startswith(".") or not item.is_file():
                    continue
                ext = item.suffix.lower()
                sz = item.stat().st_size
                largest.append((sz, item.name))
                if ext in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"):
                    cats["images"] += 1
                elif ext in (".mp4", ".mkv", ".webm", ".mov"):
                    cats["videos"] += 1
                elif ext in (".zip", ".tar", ".gz", ".xz", ".7z", ".rar", ".deb", ".rpm", ".appimage"):
                    cats["archives"] += 1
                elif ext in (".pdf", ".md", ".txt", ".docx", ".xlsx", ".pptx", ".csv"):
                    cats["docs"] += 1
                elif ext in (".py", ".qml", ".js", ".ts", ".rs", ".sh", ".json", ".toml", ".yaml", ".yml"):
                    cats["code"] += 1
                else:
                    cats["other"] += 1
            total_files = sum(cats.values())
            output_lines.append(f"Folder `{target_dir}` ({total_files} top-level files):")
            output_lines.append("  Categories: " + ", ".join(f"{k}={v}" for k, v in cats.items() if v > 0))
            largest.sort(reverse=True)
            for sz, fname in largest[:5]:
                output_lines.append(f"  - `{fname}` ({sz // 1024} KiB)")
        except Exception:
            pass
    elif sname == "window-workspace-layout":
        try:
            import desktop_control
            res = desktop_control.manage_desktop(action="list_windows")
            output_lines.append(res.get("summary", "Open windows:"))
            output_lines.extend(f"  {ln}" for ln in (res.get("lines") or [])[:10])
        except Exception:
            pass
    elif sname == "git-daily-brief":
        target_ws = workspace or str(comp_root)
        try:
            import desktop_control
            st = desktop_control.execute_git_task(workspace=target_ws, subaction="status")
            lg = desktop_control.execute_git_task(workspace=target_ws, subaction="log")
            output_lines.append(st.get("summary", f"Git status ({Path(target_ws).name}):"))
            output_lines.extend(f"  {ln}" for ln in (st.get("lines") or [])[:6])
            output_lines.append("Recent commits:")
            output_lines.extend(f"  {ln}" for ln in (lg.get("lines") or [])[:5])
        except Exception:
            pass
    elif sname == "system-tuner":
        try:
            import mcp_client
            import desktop_control
            sinfo = mcp_client._collect_system_info()
            output_lines.append("System Health Snapshot:")
            for k, v in sinfo.items():
                output_lines.append(f"  - {k}: {v}")
            procs = desktop_control.manage_processes(action="list")
            output_lines.append("Top Processes:")
            output_lines.extend(f"  {ln}" for ln in (procs.get("lines") or [])[:6])
        except Exception:
            pass
    elif sname == "clipboard-transformer":
        try:
            import mcp_client
            clip_res = mcp_client._execute_builtin_tool("clipboard_read", {}, companion_root=comp_root)
            clip_txt = "\n".join(c.get("text", "") for c in clip_res.get("content", []))
            output_lines.append(f"Clipboard preview ({len(clip_txt)} chars):")
            output_lines.append(f"  {clip_txt[:400]}")
        except Exception:
            pass
    elif sname == "graphify":
        try:
            import mcp_client
            target_ws = workspace or str(comp_root)
            stats_res = mcp_client._execute_graphify_tool("graph_stats", {"project_path": target_ws}, companion_root=comp_root)
            god_res = mcp_client._execute_graphify_tool("god_nodes", {"project_path": target_ws, "top_n": 6}, companion_root=comp_root)
            for item in stats_res.get("content", []):
                if item.get("text"):
                    output_lines.append(item["text"])
            for item in god_res.get("content", []):
                if item.get("text"):
                    output_lines.append(item["text"])
            clean_q = re.sub(r"^/graphify\b", "", str(prompt or ""), flags=re.IGNORECASE).strip()
            if clean_q and not clean_q.startswith("-"):
                q_res = mcp_client._execute_graphify_tool(
                    "query_graph",
                    {"question": clean_q, "project_path": target_ws},
                    companion_root=comp_root,
                )
                for item in q_res.get("content", []):
                    if item.get("text"):
                        output_lines.append(item["text"])
        except Exception:
            pass
    elif sname == "ponytail":
        clean_p = re.sub(r"^/ponytail\b", "", str(prompt or ""), flags=re.IGNORECASE).strip()
        tokens = clean_p.split()
        intensity = "full"
        task_rem = clean_p
        if tokens and tokens[0].lower() in ("lite", "full", "ultra"):
            intensity = tokens[0].lower()
            task_rem = " ".join(tokens[1:]).strip()
        elif "ultra" in [t.lower() for t in tokens[:2]]:
            intensity = "ultra"
        elif "lite" in [t.lower() for t in tokens[:2]]:
            intensity = "lite"
        ponytail_intensity = intensity
        mode_desc = {
            "lite": "lite — Build what was asked; name the smaller/lazier option in one line and let the user pick.",
            "full": "full (default) — Enforce The Ladder strictly; no speculative abstractions, wrappers, or unrequested options; never cut security, trust-boundary validation, or data-loss safeguards.",
            "ultra": "ultra — YAGNI extremist; question parts of the request that the underlying need does not justify before writing code.",
        }[intensity]
        output_lines.append('Motto: "The best code is the code you never wrote."')
        output_lines.append(f"Active Intensity: `{intensity}` ({mode_desc})")
        output_lines.append("The Ladder:")
        output_lines.append("  1. Does this need to exist? (YAGNI)")
        output_lines.append("  2. Does the repo already have a helper/pattern for it?")
        output_lines.append("  3. Can the standard library or native platform do it?")
        output_lines.append("  4. What is the fewest lines that solve it cleanly? (Deletion beats addition)")
        if task_rem:
            output_lines.append(f"Target Task: {task_rem}")
        output_lines.append(f"\n### Ponytail Receipt (`{intensity}`)")
        output_lines.append("- **Skipped bloat**: Zero speculative wrappers, unrequested flags, or single-use abstractions.")
        output_lines.append("- **Reused**: Existing repo helpers and Python/POSIX standard library primitives.")
        output_lines.append("- **Trade-offs**: Security, trust-boundary validation, and data-loss safeguards remain non-negotiable.")
    elif sname in ("docx", "pdf", "xlsx"):
        try:
            import code_tools
            ext_pat = (
                r"\.docx\b" if sname == "docx"
                else (r"\.(?:xlsx|csv|tsv)\b" if sname == "xlsx" else r"\.pdf\b")
            )
            m_file = re.search(rf"([~/A-Za-z0-9_.\-/]+{ext_pat})", str(prompt or ""), re.IGNORECASE)
            if m_file:
                cand_str = m_file.group(1).strip()
                cand_p = Path(cand_str).expanduser()
                if not cand_p.is_absolute() and workspace:
                    cand_p = (Path(workspace).expanduser() / cand_p).resolve(strict=False)
                if cand_p.is_file():
                    info = code_tools.read_file(cand_p, max_chars=2400)
                    if info.get("ok"):
                        if sname == "xlsx":
                            f_cnt = len(info.get("formulas") or [])
                            err_cnt = len(info.get("formulaErrors") or [])
                            output_lines.append(
                                f"Extracted `{cand_p.name}` ({len(info.get('sheets') or [])} sheet(s), {f_cnt} formula(s), {err_cnt} formula error(s)):"
                            )
                        else:
                            output_lines.append(
                                f"Extracted `{cand_p.name}` ({len(info.get('paragraphs') or [])} paragraphs, {len(info.get('tables') or [])} tables):"
                            )
                        output_lines.append(info.get("text", "")[:1600])
            if not output_lines:
                if sname == "docx":
                    output_lines.append(
                        "DOCX pipeline ready: `code_tools.read_file` (OOXML `word/document.xml` paragraphs + Markdown tables), "
                        "`code_tools.convert_or_create_document` (Markdown/OOXML `.docx` builder), `pandoc`, and `libreoffice --headless --convert-to pdf`."
                    )
                elif sname == "xlsx":
                    output_lines.append(
                        "XLSX pipeline ready: `code_tools.read_file` (OOXML `xl/workbook.xml`, `xl/sharedStrings.xml`, `xl/worksheets/sheet*.xml` + `<f>` formulas), "
                        "`code_tools.convert_or_create_document` (zero-formula-error OOXML `.xlsx` builder), and `libreoffice --headless`."
                    )
                else:
                    output_lines.append(
                        "PDF pipeline ready: `code_tools.read_file` (`pdftotext -layout` + stream parser), "
                        "`code_tools.convert_or_create_document` (PDF 1.4 writer), `pdfinfo`, `pdfimages`, and `pdftoppm`."
                    )
        except Exception:
            pass

    try:
        record_skill_feedback(skill["name"], success=script_ok, companion_root=comp_root)
    except SkillError:
        pass

    res_payload = {
        "skill": skill["name"],
        "description": skill["description"],
        "source": skill["source"],
        "version": skill.get("version", "1.0.0"),
        "references": skill.get("references", []),
        "executedScript": executed_script,
        "output": "\n".join(output_lines)[:3200],
        "instructions": skill["instructions"][:2400],
    }
    if sname == "ponytail":
        res_payload["ponytailMode"] = ponytail_intensity
    return res_payload


def _compact_skill_summaries(companion_root: Path | None = None) -> list[dict]:
    return [
        {
            "name": s["name"],
            "description": s["description"],
            "triggers": s["triggers"],
            "category": s.get("category", "general"),
            "version": s.get("version", "1.0.0"),
            "source": s["source"],
            "builtIn": s.get("builtIn", s["source"] == "builtin"),
            "enabled": s["enabled"],
            "folder": str(s.get("folder", "")),
            "path": str(s.get("path", "")),
            "scripts": s["scripts"],
            "references": s.get("references", []),
            "usageCount": s.get("usageCount", 0),
            "successCount": s.get("successCount", 0),
            "learnedNotes": s.get("learnedNotes", []),
        }
        for s in list_skills(companion_root, include_disabled=True)
    ]


def execute_skill_action(options: dict, companion_root: Path | None = None) -> dict:
    op = str(options.get("op") or options.get("subaction") or "list").strip().lower()
    if op == "list":
        return {"op": op, "skills": _compact_skill_summaries(companion_root)}
    if op == "reference":
        ref = read_skill_reference(
            str(options.get("name") or ""),
            str(options.get("reference") or options.get("file_path") or ""),
            companion_root,
        )
        return {"op": "reference", **ref}
    if op in ("inspect", "view"):
        ref_file = str(options.get("reference") or options.get("file_path") or "").strip()
        if ref_file:
            ref = read_skill_reference(str(options.get("name") or ""), ref_file, companion_root)
            return {"op": "reference", "reference": ref, **ref}
        return {"op": "inspect", "skill": inspect_skill(str(options.get("name") or ""), companion_root)}
    if op == "match":
        matched = match_skills(str(options.get("prompt") or ""), companion_root)
        return {"op": op, "skills": matched}
    if op == "run":
        res = run_skill(
            name=str(options.get("name") or ""),
            prompt=str(options.get("prompt") or ""),
            companion_root=companion_root,
            workspace=str(options.get("workspace") or "") or None,
        )
        return {"op": op, "result": res, "skills": _compact_skill_summaries(companion_root)}
    if op == "create":
        created = create_skill(
            name=str(options.get("name") or ""),
            description=str(options.get("description") or ""),
            instructions=str(options.get("instructions") or ""),
            triggers=options.get("triggers") if isinstance(options.get("triggers"), list) else None,
            category=str(options.get("category") or "learned"),
            references=options.get("references") if isinstance(options.get("references"), dict) else None,
        )
        return {"op": op, "skill": created, "skills": _compact_skill_summaries(companion_root)}
    if op == "patch":
        patched = patch_skill(
            name=str(options.get("name") or ""),
            old_text=str(options.get("oldText") or options.get("old_string") or options.get("old_text") or ""),
            new_text=str(options.get("newText") or options.get("new_string") or options.get("new_text") or ""),
            append_note=str(options.get("appendNote") or options.get("note") or options.get("append_note") or ""),
            append_section=str(options.get("appendSection") or options.get("append_section") or ""),
            description=options.get("description") if options.get("description") is not None else None,
            triggers=options.get("triggers") if isinstance(options.get("triggers"), list) else None,
            category=options.get("category") if options.get("category") is not None else None,
            companion_root=companion_root,
        )
        return {"op": op, "skill": patched, "skills": _compact_skill_summaries(companion_root)}
    if op == "learn":
        learned = learn_skill(
            target_or_instruction=str(options.get("prompt") or options.get("target") or options.get("instructions") or ""),
            name=str(options.get("name") or ""),
            category=str(options.get("category") or "learned"),
            session_ctx=options.get("session") if isinstance(options.get("session"), dict) else None,
            recent_steps=options.get("steps") if isinstance(options.get("steps"), list) else None,
            companion_root=companion_root,
        )
        return {"op": op, **learned, "skills": _compact_skill_summaries(companion_root)}
    if op == "write_reference":
        updated = write_skill_reference(
            name=str(options.get("name") or ""),
            reference_file=str(options.get("reference") or options.get("file_path") or "notes.md"),
            content=str(options.get("content") or ""),
            companion_root=companion_root,
        )
        return {
            "op": op,
            "skill": updated,
            "references": updated.get("references", []),
            "skills": _compact_skill_summaries(companion_root),
        }
    if op == "feedback":
        updated = record_skill_feedback(
            name=str(options.get("name") or ""),
            success=bool(options.get("success", True)),
            note=str(options.get("note") or ""),
            companion_root=companion_root,
        )
        return {"op": op, "skill": updated, "skills": _compact_skill_summaries(companion_root)}
    if op == "reflections":
        return {"op": op, "reflections": list_workflow_reflections(int(options.get("limit") or 12))}
    if op in ("enable", "disable"):
        updated = set_skill_enabled(str(options.get("name") or ""), enabled=(op == "enable"), companion_root=companion_root)
        return {"op": op, "skill": updated, "skills": _compact_skill_summaries(companion_root)}
    if op == "delete":
        deleted = delete_skill(str(options.get("name") or ""), companion_root=companion_root)
        return {"op": op, "deleted": deleted, "skills": _compact_skill_summaries(companion_root)}
    if op == "export_finetune":
        return export_finetune_dataset(
            output_path=options.get("outputPath") or options.get("path"),
            format=str(options.get("format") or "sharegpt"),
        )
    raise SkillError("invalid_skill_op", f"Unsupported skill operation '{op}'.")


def export_finetune_dataset(output_path: str | Path | None = None, format: str = "sharegpt") -> dict:
    """Export successful user/assistant/tool trajectories and learned preferences into a ShareGPT or ChatML JSONL file."""
    import learning_store

    return learning_store.export_finetune_dataset(output_path=output_path, format=format)

