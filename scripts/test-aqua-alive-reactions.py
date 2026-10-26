#!/usr/bin/env python3
"""Owned QML locomotion/drop physics and finite curiosity ownership behavior."""
import os
from pathlib import Path
import runpy
import signal
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
subprocess.run(["node","scripts/test-aqua-airborne.cjs"],cwd=ROOT,check=True,timeout=30)
subprocess.run(["node","scripts/test-aqua-curiosity-adapter.cjs"],cwd=ROOT,check=True,timeout=30)
core=runpy.run_path(str(ROOT/"scripts/aqua-manual-visual-matrix.py"))
def check_character(character):
    with tempfile.TemporaryDirectory(prefix="aqua-alive-") as temporary:
        private=Path(temporary);shell,xdg=core["staged"](private)
        (shell/"shell.qml").write_text('import Quickshell\nimport "scripts/aqua-fixtures/alive"\nShellRoot {AquaAliveProof {}}\n')
        env=core["private_env"](xdg,private/"result.json")
        env.update({"COMPANION_TEST_CHARACTER":character,"QT_QUICK_BACKEND":"software","QT_QUICK_CONTROLS_STYLE":"Basic","QT_QPA_PLATFORMTHEME":"generic","QT_NO_XDG_DESKTOP_PORTAL":"1"})
        with (private/"test.log").open("w") as output:
            process=subprocess.Popen(["dbus-run-session","--","qs","--path",str(shell/"shell.qml")],cwd=ROOT,
                env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
            try:code=process.wait(timeout=70)
            except subprocess.TimeoutExpired:code=-1
            finally:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGTERM)
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
        log=(private/"test.log").read_text()
        bad=("AQUA_ALIVE=FAIL","ReferenceError:","TypeError:","SyntaxError:","Unable to assign","Binding loop",
            "Failed to load configuration","Quickshell has crashed","Quickshell has been restarted","FAIL!")
        if code or "AQUA_ALIVE=PASS" not in log or any(m in log for m in bad):
            print("\n".join(line for line in log.splitlines() if any(m in line for m in (*bad,"ERROR","AQUA_ALIVE"))))
            raise SystemExit("Aqua lively-motion/curiosity QML proof failed")
        for line in log.splitlines():
            if "AQUA_ALIVE=PASS" in line:print(line.split("AQUA_ALIVE=",1)[1])

for character in ("aqua","octo"):
    check_character(character)
print("AQUA_OWNED_ALIVE_REACTIONS_PASS cast=2")
