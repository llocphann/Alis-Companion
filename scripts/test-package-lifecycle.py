#!/usr/bin/env python3
"""Independent atomic install/update/remove on owned directories; preserve user data."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(os.environ.get("HADANION_SOURCE", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
discovery = None
if os.environ.get("HADANION_TEST_HOST"):
    host = Path(os.environ["HADANION_TEST_HOST"])
    spec = importlib.util.spec_from_file_location("host_discovery", host / "scripts/hadanion-status.py")
    discovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(discovery)

with tempfile.TemporaryDirectory(prefix="hadanion-install-") as temporary:
    private = Path(temporary)
    source = private / "source"
    source.mkdir()
    for name in module.RUNTIME_DIRS:
        shutil.copytree(ROOT / name, source / name)
    for name in module.RUNTIME_FILES:
        p = source / name
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, p)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    def commit():
        subprocess.run(["git", "add", "--all"], cwd=source, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], cwd=source, check=True)
    commit()
    binary = private / "native"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$0"\nprintf "<%s>" "$@"\nprintf "\\n"\n')
    binary.chmod(0o755)
    data = private / "data/hadanion"
    data.mkdir(parents=True)
    preferences = private / "config.json"
    preferences.write_bytes(b'{"enabled":true,"character":"octo"}\n')
    transcript = private / "chat.sqlite3"
    transcript.write_bytes(b"private fixture history")
    journal = private / "journal.md"
    journal.write_bytes(b"---\nmood: good\nenergy: high\n---\n")
    preserved = {p: p.read_bytes() for p in (preferences, transcript, journal)}
    first = module.install(data, source, binary)
    assert (data / "current").resolve() == first
    assert not (first / "assets").exists() and not (first / "scripts/test-package-lifecycle.py").exists()
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["binary"] == "native/bin/alis-companiond"
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    for name in ("alis-companiond", "inir-companiond"):
        executable = first / "native/bin" / name
        assert executable.is_file() and os.access(executable, os.X_OK)
        assert manifest["files"][str(executable.relative_to(first))] == digest
        assert subprocess.check_output([str(executable), "--probe", "one argument"], text=True) == str(executable) + "\n<--probe><one argument>\n"
    dispatched = subprocess.check_output([str(first / "scripts/native-dispatch"), "companion", "--probe", "one argument"], text=True)
    assert dispatched == str(first / "native/bin/alis-companiond") + "\n<--probe><one argument>\n"
    rejected = subprocess.run([str(first / "scripts/native-dispatch"), "other"], capture_output=True, text=True)
    assert rejected.returncode == 64 and not rejected.stdout
    if discovery:
        result = discovery.inspect(private / "host", private / "data")
        assert result["available"] and result["diagnostic"] == "ready", result
        assert result["root"] == str(first) and result["sourceSha"] == manifest["sourceSha"], result
        print("PASS: current host discovers canonical packaged native entrypoint")
    assert module.install(data, source, binary) == first
    for name in ("alis-companiond", "inir-companiond"):
        executable = first / "native/bin" / name
        original = executable.read_bytes()
        try:
            executable.write_bytes(original + b"# modified fixture\n")
            try:
                module.install(data, source, binary)
            except ValueError as error:
                assert "Existing release was modified" in str(error), error
            else:
                raise AssertionError("modified native payload was reactivated")
            assert (data / "current").resolve() == first
        finally:
            executable.write_bytes(original)
    assert module.install(data, source, binary) == first
    untracked = source / "services/uncommitted.qml"
    untracked.write_text("import QtQuick\nItem {}\n")
    try:
        module.install(data, source, binary)
    except ValueError:
        pass
    else:
        raise AssertionError("untracked runtime source was published as an exact release")
    untracked.unlink()
    p = source / "manifest.json"
    payload = json.loads(p.read_text())
    payload["version"] = "0.1.1"
    p.write_text(json.dumps(payload))
    try:
        module.install(data, source, binary)
    except ValueError:
        pass
    else:
        raise AssertionError("dirty source was published as an exact release")
    commit()
    second = module.install(data, source, binary)
    assert second != first and first.exists() and (data / "current").resolve() == second
    module.activate(data, first)
    assert (data / "current").resolve() == first
    module.uninstall(data)
    module.uninstall(data)
    assert not (data / "current").exists() and first.exists() and second.exists()
    (data / "current").mkdir()
    try:
        module.activate(data, first)
    except ValueError:
        pass
    else:
        raise AssertionError("foreign directory was overwritten")
    assert all(path.read_bytes() == raw for path, raw in preserved.items())
print("PASS: canonical/legacy native entrypoints, dispatcher, tamper refusal, exact releases, atomic update/rollback, default-off removal, foreign-path guard and user-data preservation")
