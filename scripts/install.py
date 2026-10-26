#!/usr/bin/env python3
"""Install an immutable Alis-Companion payload independently of the Alis runtime."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = ("modules/abyss/companion", "modules/settings", "services", "scripts/aqua")
RUNTIME_FILES = ("manifest.json", "qmldir", "AlisSession.qml", "AlisOutput.qml", "scripts/native-dispatch", "LICENSE")


def assemble(source, target, binary, sha):
    """Copy only product source. No Blender sources, tests, models or user data."""
    source, target, binary = Path(source), Path(target), Path(binary)
    for name in RUNTIME_DIRS:
        shutil.copytree(source / name, target / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in RUNTIME_FILES:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, path)
    native = target / "native/bin"
    native.mkdir(parents=True)
    executable = native / "alis-companiond"
    shutil.copy2(binary, executable)
    executable.chmod(0o755)
    manifest = json.loads((target / "manifest.json").read_text())
    manifest["sourceSha"] = sha
    files = {}
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files[str(path.relative_to(target))] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["files"] = files
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def owned_link(link, data_root):
    if not link.is_symlink():
        raise ValueError(f"Refusing to replace a directory or file at {link}")
    destination = link.resolve()
    if not destination.is_relative_to(data_root / "releases"):
        raise ValueError(f"Refusing to change a foreign symlink at {link}")


def activate(data_root, release):
    link = data_root / "current"
    if link.exists() or link.is_symlink():
        owned_link(link, data_root)
    temporary = data_root / f".current-{os.getpid()}"
    try:
        temporary.symlink_to(release.relative_to(data_root))
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def install(data_root, source=ROOT, binary=None):
    source = Path(source).resolve()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=source, text=True)
    if dirty:
        raise ValueError("Commit source changes before installing an exact Alis-Companion revision")
    binary = Path(binary or source / "native/target/release/alis-companiond")
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("Build the native runtime with make build first")
    releases = data_root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release = releases / sha
    if release.exists():
        manifest = json.loads((release / "manifest.json").read_text())
        if manifest.get("sourceSha") != sha:
            raise ValueError("Existing release identity does not match")
        for name, digest in manifest["files"].items():
            if hashlib.sha256((release / name).read_bytes()).hexdigest() != digest:
                raise ValueError("Existing release was modified; preserve it before reinstalling")
    else:
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=releases) as stage:
            payload = Path(stage) / "payload"
            assemble(source, payload, binary, sha)
            os.replace(payload, release)
    activate(data_root, release)
    return release


def uninstall(data_root):
    link = data_root / "current"
    if not link.exists() and not link.is_symlink():
        return
    owned_link(link, data_root)
    link.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--data-home", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")))
    args = parser.parse_args()
    data_root = (args.data_home / "alis-companion").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    with (data_root / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "install":
            print(f"Installed Alis-Companion: {install(data_root)}")
        else:
            uninstall(data_root)
            print("Disabled the Alis-Companion installation; releases and user data are preserved")
    print("Run alis ipc alis-companion refresh, or restart Alis, to apply the change")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
