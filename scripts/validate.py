#!/usr/bin/env python3
"""Qualify Hadanion on an isolated Hadalis host; no live installation or inference."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def stage(host, target, integration=None, integration_sha=None):
    target.mkdir()
    for name in ("modules", "services", "scripts", "defaults", "translations"):
        shutil.copytree(host / name, target / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("assets", "docs", "to-do", "native", "sdata"):
        (target / name).symlink_to(host / name)
    for path in host.glob("*.qml"):
        shutil.copy2(path, target / path.name)
    for path in ROOT.glob("wull*.qml"):
        shutil.copy2(path, target / path.name)
    for name in ("qmldir", "Makefile"):
        shutil.copy2(host / name, target / name)
    shutil.copy2(ROOT / "HadalisOutput.qml", target / "HadalisOutput.qml")
    for path in (ROOT / "scripts").iterdir():
        destination = target / "scripts" / path.name
        if path.is_dir():
            shutil.copytree(path, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(path, destination)
    binary = ROOT / "native/target/release/inir-companiond"
    installer.assemble(ROOT, target / "optional/hadanion", binary, "validation-working-source")
    # Historical tooling retains original source paths in a test-only overlay.
    if (target / "modules/abyss/companion").exists():
        (target / "modules/abyss/companion").rmdir()
    (target / "modules/abyss/companion").symlink_to(target / "optional/hadanion/modules/abyss/companion")
    shutil.copy2(ROOT / "services/WullMind.qml", target / "services/WullMind.qml")
    for path in (ROOT / "services").glob("*.js"):
        shutil.copy2(path, target / "services" / path.name)
    for path in (host / "scripts/ai").glob("*.py"):
        shutil.copy2(path, target / "scripts/wull" / path.name)
    if integration:
        # Private optional dependency payload, never a user installation or
        # permission change. Reuse the integration owner's package builder.
        spec = importlib.util.spec_from_file_location("hadalird_installer", integration / "scripts/install.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        builder.assemble(integration, target / "optional/hadalird", integration_sha)
    # Authoring and reference checks operate on the repository assets, not payload.
    (target / "assets").unlink()
    shutil.copytree(host / "assets", target / "assets")
    shutil.copytree(ROOT / "assets", target / "assets", dirs_exist_ok=True)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hadalis-root", type=Path, required=True)
    parser.add_argument("--hadalird-root", type=Path, help="optional pinned journal integration for private tests")
    parser.add_argument("--require-clean", action="store_true", help="require exact committed source in both repositories")
    parser.add_argument("--only", nargs="*", help="run selected checks during development")
    args = parser.parse_args()
    host = args.hadalis_root.resolve()
    if not (host / "services/Hadanion.qml").is_file():
        raise SystemExit("Hadalis host API 1 source is required")
    revisions = {}
    sources = [("Hadanion", ROOT), ("Hadalis", host)]
    integration = args.hadalird_root.resolve() if args.hadalird_root else None
    if integration:
        sources.append(("Hadalird", integration))
    for name, repo in sources:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, capture_output=True, text=True)
        dirty = bool(status.stdout.strip())
        sha = revision.stdout.strip() if revision.returncode == 0 else "uncommitted import"
        revisions[repo] = sha
        print(name + " source: " + sha + (" WORKING TREE" if dirty else " committed"), flush=True)
        if args.require_clean and (revision.returncode or dirty):
            raise SystemExit("Exact committed source is required")
    checks = [["cargo", "test", "--locked", "--offline", "--workspace", "--manifest-path", str(ROOT / "native/Cargo.toml")]]
    with tempfile.TemporaryDirectory(prefix="hadanion-validation-") as temporary:
        work = stage(host, Path(temporary) / "host", integration, revisions.get(integration))
        environment = os.environ.copy()
        environment.update(HADALIS_ROOT=str(work), HADANION_SOURCE=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        # A per-run isolated user package is essential when validating on a machine
        # where another Hadanion release may already be installed.
        environment["HADANION_TEST_HOST"] = str(work)
        environment["HADANION_HOVER_HOST_SHA"] = revisions[host]
        base_environment = environment.copy()
        checks += [["node", str(path.relative_to(ROOT))] for path in sorted((ROOT / "scripts").glob("test-*.cjs"))]
        checks += [["python3", "scripts/" + name] for name in (
            "test-package-lifecycle.py", "test-host-runtime.py", "test-wull-context-runtime.py", "test-module-hover-runtime.py", "test-wull-local-mind.py",
            "test-wull-gguf-ui.py", "test-wull-shared-ai-runtime.py", "test-wull-abyss-water.py",
            "test-wull-portal-runtime.py", "test-wull-presence-interaction.py", "test-wull-alive-reactions.py",
            "test-wull-mature-popup.py", "test-companion-airborne-orientation.py",
            "test-companion-volume.py", "test-wull-shader-ab-contract.py",
            "test-wull-shader-sequence-contract.py", "test-wull-g1-resource-contract.py",
            "test-wull-g1-compare-contract.py", "test-wull-companion-voice-eval.py", "test-wull-reply-guard-local.py",
            "test-wull-memory-store.py", "test-wull-local-persona.py", "test-wull-companion-voice-run.py", "test-wull-companion-settings.py", "test-wull-quality-ui.py", "test-wull-mind-ui.py", "test-wull-journal-ui.py",
            "test-wull-immersion-runtime.py", "test-wull-cloud-orbit.py", "test-wull-cowork-qml.py", "test-wull-cowork-native.py")]
        qml_parser = "/usr/lib/qt6/bin/qmlformat" if Path("/usr/lib/qt6/bin/qmlformat").is_file() else shutil.which("qmlformat")
        if not qml_parser:
            raise SystemExit("Qt qmlformat is required")
        product_qml = list((ROOT / "modules").rglob("*.qml")) + list((ROOT / "services").rglob("*.qml")) + [ROOT / "HadalisSession.qml", ROOT / "HadalisOutput.qml"]
        checks += [[qml_parser, str(path)] for path in sorted(product_qml)]
        labels = {Path(command[1]).name if command[0] != "cargo" else "Rust behavior/protocol tests" for command in checks}
        if args.only and set(args.only) - labels:
            raise SystemExit("Unknown checks: " + ", ".join(sorted(set(args.only) - labels)))
        passed = failed = skipped = 0
        native_spec = importlib.util.spec_from_file_location("native_session", work / "scripts/native_test_session.py")
        native_session = importlib.util.module_from_spec(native_spec)
        native_spec.loader.exec_module(native_session)
        private_fixtures = {
            "test-wull-gguf-ui.py", "test-wull-shared-ai-runtime.py", "test-wull-abyss-water.py",
            "test-wull-portal-runtime.py", "test-wull-presence-interaction.py", "test-wull-alive-reactions.py",
            "test-wull-mature-popup.py", "test-companion-airborne-orientation.py", "test-companion-volume.py",
            "test-wull-mind-ui.py", "test-wull-journal-ui.py", "test-wull-cloud-orbit.py",
        }
        sessions = work / "validation-sessions"
        sessions.mkdir()
        for command in checks:
            label = Path(command[1]).name if command[0] != "cargo" else "Rust behavior/protocol tests"
            if args.only and label not in args.only:
                continue
            if label in private_fixtures:
                session_root = sessions / label
                session_root.mkdir()
                # Fresh private windows prevent parent-compositor occlusion and
                # pointer/focus interference with another running Qt validator.
                # These two proofs depend on real rendered frames and an
                # animation-driven hide deadline. An occluded nested output
                # may suspend that clock. Focus only this owned compositor;
                # the helper restores a known previous target unless the
                # Owner changed focus in the meantime. Pixel/input oracles
                # and deadlines remain unchanged.
                with native_session.private_wayland(session_root, capture_focus=label in {
                        "test-companion-volume.py", "test-wull-cloud-orbit.py"}) as native_environment:
                    run_environment = base_environment.copy()
                    if native_environment:
                        for key in ("WAYLAND_DISPLAY", "NIRI_SOCKET", "QT_QPA_PLATFORM",
                                    "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
                            run_environment[key] = native_environment[key]
                        for key in ("QS_CONFIG_NAME", "QS_CONFIG_PATH", "QS_MANIFEST"):
                            run_environment.pop(key, None)
                    else:
                        run_environment.pop("WAYLAND_DISPLAY", None)
                    result = subprocess.run(command, cwd=work, env=run_environment, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, timeout=180)
            else:
                # Native host/immersion fixtures already own private compositors.
                result = subprocess.run(command, cwd=work, env=base_environment, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        timeout=430 if label == "test-module-hover-runtime.py" else 180)
            skipped_check = result.returncode == 77 or result.stdout.lstrip().startswith("SKIP:")
            print(("SKIP " if skipped_check else "PASS " if result.returncode == 0 else "FAIL ") + label, flush=True)
            if skipped_check:
                skipped += 1
                continue
            if result.returncode:
                failed += 1
                print(result.stdout[-12000:], flush=True)
            else:
                passed += 1
                if command[0] != qml_parser:
                    print(result.stdout[-1200:], flush=True)
        if args.require_clean:
            for repo, original in revisions.items():
                final = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
                dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, text=True)
                if final != original or dirty.strip():
                    failed += 1
                    print("FAIL source changed during validation: " + str(repo), flush=True)
        print(f"Hadanion checks: {passed} PASS / {failed} FAIL / {skipped} SKIP", flush=True)
        raise SystemExit(bool(failed))


if __name__ == "__main__":
    main()
