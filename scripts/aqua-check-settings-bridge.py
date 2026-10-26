#!/usr/bin/env python3
"""Exercise the production QML bridge against an explicit native companion binary."""
import argparse
import os
from pathlib import Path
import runpy
import signal
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error("an executable companion binary is required")
    core = runpy.run_path(str(ROOT / "scripts/aqua-manual-visual-matrix.py"))
    with tempfile.TemporaryDirectory(prefix="aqua-settings-bridge-") as temporary:
        shell, xdg = core["staged"](Path(temporary))
        (shell / "shell.qml").write_text((ROOT / "scripts/aqua-fixtures/settings/PreferencesBridgeProof.qml").read_text())
        env = core["private_env"](xdg, Path(temporary) / "unused.png")
        env["AQUA_SETTINGS_TEST_BINARY"] = str(binary)
        log_path = Path(temporary) / "bridge.txt"
        with log_path.open("w") as log:
            process = subprocess.Popen(["dbus-run-session", "--", "qs", "--path", str(shell / "shell.qml")],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True)
            try:
                code = process.wait(timeout=12)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
        content = log_path.read_text()
        bad = ("ReferenceError:", "TypeError:", "SyntaxError:", "Unable to assign", "Failed to load configuration", "AQUA_SETTINGS_BRIDGE=FAIL")
        if code or "AQUA_SETTINGS_BRIDGE=PASS" not in content or any(message in content for message in bad):
            print(content[-10000:])
            raise SystemExit("Companion settings/native bridge proof failed")
    print("AQUA_SETTINGS_REAL_QML_NATIVE_BRIDGE_PASS")


if __name__ == "__main__":
    main()
