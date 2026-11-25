#!/usr/bin/env python3
"""Qualify Alis-Companion on an isolated Alis host; no live installation or inference."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def stage(host, target, integration=None, integration_sha=None):
    target.mkdir()
    for name in ("modules", "services", "scripts", "defaults", "translations"):
        shutil.copytree(host / name, target / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("assets", "docs", "to-do", "native", "sdata"):
        if (host / name).exists():
            (target / name).symlink_to(host / name)
    for path in host.glob("*.qml"):
        shutil.copy2(path, target / path.name)
    for path in ROOT.glob("aqua*.qml"):
        shutil.copy2(path, target / path.name)
    for name in ("qmldir", "Makefile"):
        shutil.copy2(host / name, target / name)
    shutil.copy2(ROOT / "AlisOutput.qml", target / "AlisOutput.qml")
    for path in (ROOT / "scripts").iterdir():
        destination = target / "scripts" / path.name
        if path.is_dir():
            shutil.copytree(path, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(path, destination)
    binary = ROOT / "native/target/release/alis-companiond"
    installer.assemble(ROOT, target / "optional/alis-companion", binary, "validation-working-source")
    if not (target / "optional/alis_companion").exists():
        (target / "optional/alis_companion").symlink_to(target / "optional/alis-companion")
    # Historical tooling retains original source paths in a test-only overlay.
    if (target / "modules/abyss/companion").exists():
        (target / "modules/abyss/companion").rmdir()
    (target / "modules/abyss/companion").symlink_to(target / "optional/alis-companion/modules/abyss/companion")
    shutil.copy2(ROOT / "services/AquaMind.qml", target / "services/AquaMind.qml")
    for path in (ROOT / "services").glob("*.js"):
        shutil.copy2(path, target / "services" / path.name)
    for path in (host / "scripts/ai").glob("*.py"):
        shutil.copy2(path, target / "scripts/aqua" / path.name)
    if integration:
        # Private optional dependency payload, never a user installation or
        # permission change. Reuse the integration owner's package builder.
        spec = importlib.util.spec_from_file_location("alis_intergration_installer", integration / "scripts/install.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        builder.assemble(integration, target / "optional/alis-intergration", integration_sha)
    # Authoring and reference checks operate on the repository assets, not payload.
    (target / "assets").unlink()
    shutil.copytree(host / "assets", target / "assets")
    shutil.copytree(ROOT / "assets", target / "assets", dirs_exist_ok=True)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alis-root", type=Path, required=True)
    parser.add_argument("--alis-intergration-root", type=Path, help="optional pinned journal integration for private tests")
    parser.add_argument("--require-clean", action="store_true", help="require exact committed source in both repositories")
    parser.add_argument("--only", nargs="*", help="run selected checks during development")
    parser.add_argument("--fast", action="store_true", help="skip private Wayland fixtures and long hover calibration")
    parser.add_argument("--list", action="store_true", help="list planned checks without running them")
    parser.add_argument("--json", type=Path, help="write structured JSON summary to PATH")
    color_grp = parser.add_mutually_exclusive_group()
    color_grp.add_argument("--color", action="store_true", help="force ANSI styling")
    color_grp.add_argument("--no-color", action="store_true", help="disable ANSI styling")
    args = parser.parse_args()

    use_color = args.color or (not args.no_color and not os.environ.get("NO_COLOR") and sys.stdout.isatty())
    c_rst = "\033[0m" if use_color else ""
    c_bold = "\033[1m" if use_color else ""
    c_dim = "\033[2m" if use_color else ""
    c_pri = "\033[36m" if use_color else ""
    c_ok = "\033[1;32m" if use_color else ""
    c_err = "\033[1;31m" if use_color else ""
    c_warn = "\033[1;33m" if use_color else ""

    host = args.alis_root.resolve()
    if not (host / "services/AlisCompanion.qml").is_file():
        raise SystemExit("Alis host API 1 source is required")
    revisions = {}
    sources = [("Alis-Companion", ROOT), ("Alis", host)]
    default_integration = Path(os.environ["ALIS_INTERGRATION_ROOT"]) if os.environ.get("ALIS_INTERGRATION_ROOT") else (ROOT.parent / "Alis-Intergration")
    integration_path = args.alis_intergration_root or (default_integration if (default_integration / "scripts/install.py").is_file() else None)
    integration = integration_path.resolve() if integration_path else None
    if integration:
        sources.append(("Alis-Intergration", integration))
    for name, repo in sources:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, capture_output=True, text=True)
        dirty = bool(status.stdout.strip())
        sha = revision.stdout.strip() if revision.returncode == 0 else "uncommitted import"
        revisions[repo] = sha
        print(f"{c_dim}{name} source: {sha[:9]} ({'WORKING TREE' if dirty else 'committed'}){c_rst}", flush=True)
        if args.require_clean and (revision.returncode or dirty):
            raise SystemExit("Exact committed source is required")

    python_checks = [
        "test-package-lifecycle.py", "test-host-runtime.py", "test-aqua-context-runtime.py", "test-module-hover-runtime.py",
        "test-aqua-local-mind.py", "test-aqua-gguf-ui.py", "test-aqua-shared-ai-runtime.py", "test-aqua-abyss-water.py",
        "test-aqua-portal-runtime.py", "test-aqua-presence-interaction.py", "test-aqua-alive-reactions.py",
        "test-aqua-mature-popup.py", "test-companion-airborne-orientation.py", "test-companion-volume.py",
        "test-aqua-frozen-variance.py", "test-aqua-shader-ab-contract.py", "test-aqua-shader-sequence-contract.py",
        "test-aqua-g1-resource-contract.py", "test-aqua-g1-compare-contract.py", "test-aqua-companion-voice-eval.py",
        "test-aqua-reply-guard-local.py", "test-aqua-memory-store.py", "test-aqua-local-persona.py",
        "test-aqua-companion-voice-run.py", "test-aqua-companion-settings.py", "test-aqua-quality-ui.py",
        "test-aqua-mind-ui.py", "test-aqua-journal-ui.py", "test-aqua-immersion-runtime.py",
        "test-aqua-cloud-orbit.py", "test-aqua-cowork-qml.py", "test-aqua-cowork-native.py",
        "test-aqua-ambient-controller.py", "test-aqua-pocket-store.py", "test-aqua-pocket-ui.py",
        "test-aqua-pocket-wayland.py", "test-aqua-local-tools.py", "test-aqua-agent-f11.py",
        "test-deepseek-harness-upgrades.py", "test-companion-jump-stutter-silent.py", "test-aqua-bubble-power-profiles.py",
        "test-aqua-a2ui-and-commentary.py", "test-aqua-p0-hardening.py", "test-aqua-phase1-architecture.py",
        "test-aqua-phase2-runtime.py", "test-aqua-phase3-swarm-qml.py", "test-aqua-hybrid-and-mascot.py",
        "test-aqua-response-and-tts-polish.py", "test-aqua-local-nexus-rag.py", "test-aqua-thinking-router.py",
        "test-aqua-thinking-immersion.py", "test-aqua-audit-hardening.py",
        "test-aqua-desktop-agent-upgrades.py",
    ]
    qml_parser = (
        shutil.which("qmlformat6")
        or ("/usr/lib/qt6/bin/qmlformat" if Path("/usr/lib/qt6/bin/qmlformat").is_file() else shutil.which("qmlformat"))
    )
    if not qml_parser:
        raise SystemExit("Qt qmlformat is required")
    product_qml = list((ROOT / "modules").rglob("*.qml")) + list((ROOT / "services").rglob("*.qml")) + [ROOT / "AlisSession.qml", ROOT / "AlisOutput.qml"]

    private_fixtures = {
        "test-aqua-gguf-ui.py", "test-aqua-shared-ai-runtime.py", "test-aqua-abyss-water.py",
        "test-aqua-portal-runtime.py", "test-aqua-presence-interaction.py", "test-aqua-alive-reactions.py",
        "test-aqua-mature-popup.py", "test-companion-airborne-orientation.py", "test-companion-volume.py",
        "test-aqua-mind-ui.py", "test-aqua-journal-ui.py", "test-aqua-cloud-orbit.py",
        "test-aqua-pocket-ui.py", "test-aqua-pocket-wayland.py", "test-aqua-immersion-runtime.py",
    }
    slow_in_fast = private_fixtures | {"test-module-hover-runtime.py"}

    entries = []
    entries.append(("rust", "Rust behavior/protocol tests", ["cargo", "test", "--locked", "--offline", "--workspace", "--manifest-path", str(ROOT / "native/Cargo.toml")]))
    for path in sorted((ROOT / "scripts").glob("test-*.cjs")):
        entries.append(("cjs", path.name, ["node", str(path.relative_to(ROOT))]))
    for name in python_checks:
        kind = "wayland" if name in private_fixtures else "python"
        entries.append((kind, name, ["python3", "scripts/" + name]))
    for path in sorted(product_qml):
        entries.append(("qml", path.name, [qml_parser, str(path)]))

    labels = {lbl for _, lbl, _ in entries}
    if args.only and set(args.only) - labels:
        raise SystemExit("Unknown checks: " + ", ".join(sorted(set(args.only) - labels)))

    selected = []
    for kind, label, cmd in entries:
        if args.only and label not in args.only:
            continue
        if args.fast and label in slow_in_fast:
            continue
        selected.append((kind, label, cmd))

    if args.list:
        for i, (kind, label, _) in enumerate(selected, 1):
            print(f"[{i:03d}] {kind:<8s} {label}")
        print(f"Total planned checks: {len(selected)}")
        return

    print(f"{c_pri}{c_bold}ALIS-COMPANION VALIDATION{c_rst}  {c_dim}checks={len(selected)}  fast={args.fast}{c_rst}", flush=True)
    suite_t0 = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="alis-comp-val-") as temporary:
        work = stage(host, Path(temporary) / "host", integration, revisions.get(integration))
        environment = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY")}
        environment.update(
            ALIS_ROOT=str(work),
            ALIS_COMPANION_SOURCE=str(ROOT),
            QT_QPA_PLATFORM="offscreen",
            PYTHONDONTWRITEBYTECODE="1",
            ALIS_COMPANION_TEST_HOST=str(work),
            ALIS_COMPANION_HOVER_HOST_SHA=revisions[host],
        )
        base_environment = environment.copy()
        native_spec = importlib.util.spec_from_file_location("native_session", work / "scripts/native_test_session.py")
        native_session = importlib.util.module_from_spec(native_spec)
        native_spec.loader.exec_module(native_session)
        sessions = work / "vs"
        sessions.mkdir()

        passed = failed = skipped = 0
        results = []
        total = len(selected)

        for idx, (kind, label, command) in enumerate(selected, 1):
            t0 = time.monotonic()
            if label in private_fixtures:
                session_root = sessions / f"s{idx}"
                session_root.mkdir()
                with native_session.private_wayland(session_root) as native_environment:
                    run_environment = base_environment.copy()
                    if native_environment:
                        for key in ("WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "NIRI_SOCKET", "QT_QPA_PLATFORM",
                                    "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
                            if key in native_environment:
                                run_environment[key] = native_environment[key]
                            else:
                                run_environment.pop(key, None)
                        for key in ("QS_CONFIG_NAME", "QS_CONFIG_PATH", "QS_MANIFEST"):
                            run_environment.pop(key, None)
                    else:
                        run_environment.pop("WAYLAND_DISPLAY", None)
                    result = subprocess.run(command, cwd=work, env=run_environment, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, timeout=180)
            else:
                result = subprocess.run(command, cwd=work, env=base_environment, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        timeout=430 if label == "test-module-hover-runtime.py" else 180)
            dt = time.monotonic() - t0
            dur_ms = int(round(dt * 1000))
            skipped_check = result.returncode == 77 or result.stdout.lstrip().startswith("SKIP:")
            status = "SKIP" if skipped_check else ("PASS" if result.returncode == 0 else "FAIL")
            badge_col = c_warn if skipped_check else (c_ok if status == "PASS" else c_err)
            dur_col = c_warn if dt >= 5.0 else c_dim
            print(f"[{idx:03d}/{total:03d}] {label:<44s} {badge_col}{status}{c_rst}  {dur_col}{dt:5.2f}s{c_rst}", flush=True)
            results.append({"kind": kind, "name": label, "status": status, "exit_code": result.returncode, "duration_ms": dur_ms})

            if skipped_check:
                skipped += 1
                continue
            if result.returncode:
                failed += 1
                print(result.stdout[-12000:], flush=True)
            else:
                passed += 1
                if args.only and command[0] != qml_parser:
                    print(result.stdout[-1200:], flush=True)

        if args.require_clean:
            for repo, original in revisions.items():
                final = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
                dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, text=True)
                if final != original or dirty.strip():
                    failed += 1
                    print(f"{c_err}FAIL source changed during validation: {repo}{c_rst}", flush=True)

        total_dt = time.monotonic() - suite_t0
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps({
                "result": "PASS" if failed == 0 else "FAIL",
                "duration_ms": int(round(total_dt * 1000)),
                "counts": {"passed": passed, "failed": failed, "skipped": skipped, "total": total},
                "checks": results,
            }, indent=2), encoding="utf-8")

        slowest = sorted(results, key=lambda r: -r["duration_ms"])[:5]
        print(f"\n{c_bold}Alis-Companion checks: {c_ok}{passed} PASS{c_rst} / {c_err if failed else c_dim}{failed} FAIL{c_rst} / {c_warn}{skipped} SKIP{c_rst}  {c_dim}({total_dt:.2f}s){c_rst}", flush=True)
        if slowest:
            print(f"{c_dim}Slowest:{c_rst} " + ", ".join(f"{r['name']} ({r['duration_ms']/1000:.2f}s)" for r in slowest), flush=True)
        raise SystemExit(bool(failed))


if __name__ == "__main__":
    main()
