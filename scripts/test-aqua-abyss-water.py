#!/usr/bin/env python3
"""Own-window real Abyss body registry, Aqua contact and finite wave coupling.

Software checks actual QML state, geometry and solver events. GPU pixels and
native compositor input remain separate. No live config, daemon or automation.
"""
import os
from pathlib import Path
import runpy
import signal
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
subprocess.run(["node","scripts/test-aqua-water.cjs"],cwd=ROOT,check=True,timeout=35)
core=runpy.run_path(str(ROOT/"scripts/aqua-manual-visual-matrix.py"))
with tempfile.TemporaryDirectory(prefix="aqua-abyss-test-") as temporary:
    private=Path(temporary)
    shell,xdg=core["staged"](private)
    (shell/"shell.qml").write_text((ROOT/"aquaAbyss.qml").read_text())
    env=core["private_env"](xdg,private/"result.json")
    env.update({"AQUA_ABYSS_TEST":"1","QT_QUICK_BACKEND":"software","QT_QUICK_CONTROLS_STYLE":"Basic","QT_QPA_PLATFORMTHEME":"generic","QT_NO_XDG_DESKTOP_PORTAL":"1"})
    with (private/"test.log").open("w") as output:
        process=subprocess.Popen(["dbus-run-session","--","qs","--path",str(shell/"shell.qml")],cwd=ROOT,
            env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            code=process.wait(timeout=160)
        except subprocess.TimeoutExpired:
            code=-1
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
    log=(private/"test.log").read_text()
    bad=("AQUA_ABYSS_CHECK=FAIL","ReferenceError:","TypeError:","SyntaxError:","Unable to assign","Binding loop","non-root animation nodes","Failed to load configuration","Quickshell has crashed","Quickshell has been restarted","FAIL!")
    if code or "AQUA_ABYSS_CHECK=PASS" not in log or any(message in log for message in bad):
        print(log[-14000:])
        raise SystemExit("Actual Aqua/Abyss coupling check failed")
    for line in log.splitlines():
        if "AQUA_ABYSS_CHECK=PASS" in line:print(line.split("AQUA_ABYSS_CHECK=",1)[1])
print("AQUA_OWNED_ABYSS_WATER_COUPLING_PASS")
