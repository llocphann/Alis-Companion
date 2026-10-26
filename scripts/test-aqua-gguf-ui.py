#!/usr/bin/env python3
"""Actual AI and Aqua QML against one isolated GGUF runtime fixture."""
import os
from pathlib import Path
import runpy
import signal
import struct
import subprocess
import tempfile
import json

ROOT=Path(__file__).resolve().parents[1]
core=runpy.run_path(str(ROOT/'scripts/aqua-manual-visual-matrix.py'))
fake=runpy.run_path(str(ROOT/'scripts/test-local-ai-gguf-runtime.py'))['FAKE']
with tempfile.TemporaryDirectory(prefix='wgg-',ignore_cleanup_errors=True) as temporary:
    private=Path(temporary);shell,xdg=core['staged'](private)
    models=private/'models';models.mkdir()
    with (models/'Tiny-UD-Q6_K_XL.gguf').open('wb') as f:
        f.write(struct.pack('<4sIQQ',b'GGUF',3,1,1));f.truncate(21*1024*1024)
    binary=private/'bin';binary.mkdir();server=binary/'llama-server';server.write_text(fake);server.chmod(0o700)
    (shell/'shell.qml').write_text('import Quickshell\nimport "scripts/aqua-fixtures/mind"\nShellRoot { AquaLocalModelProof {} }\n')
    env=core['private_env'](xdg,private/'result.json')
    env.update(QT_QUICK_BACKEND='software',QT_QUICK_CONTROLS_STYLE='Basic',QT_QPA_PLATFORMTHEME='generic',
        ALIS_GGUF_ROOTS=json.dumps([str(models)]),INIR_GGUF_ROOTS=json.dumps([str(models)]),
        INIR_TEST_GGUF_RECORD=str(private/'record.json'),
        PATH=str(binary)+':'+os.environ['PATH'],INIR_AQUA_HISTORY_DB=str(private/'aqua-history.sqlite3'))
    with (private/'test.log').open('w') as log:
        process=subprocess.Popen(['dbus-run-session','--','qs','--path',str(shell/'shell.qml')],env=env,
            cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:code=process.wait(timeout=35)
        except subprocess.TimeoutExpired:code=-1
        finally:
            if process.poll() is None:os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=4)
    text=(private/'test.log').read_text()
    bad=('AQUA_GGUF_UI=FAIL','ReferenceError:','TypeError:','SyntaxError:','Unable to assign','Binding loop','Failed to load configuration','FAIL!')
    if code or 'AQUA_GGUF_UI=PASS' not in text or any(s in text for s in bad):
        print(text[-12000:]);raise SystemExit('GGUF QML bridge proof failed')
    pid=json.loads((private/'record.json').read_text())['pid']
    try:os.kill(pid,0)
    except ProcessLookupError:pass
    else:raise SystemExit('GGUF model survived its finished request')
    print(next(line for line in text.splitlines() if 'AQUA_GGUF_UI=PASS' in line))
