#!/usr/bin/env python3
"""Pocket consent, original safety, bounded atomic batches and legacy restart."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/aqua/pocket.py'
spec = importlib.util.spec_from_file_location('pocket', HELPER)
pocket = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pocket)


def request(action, **extra):
    return dict(version=1, consent=True, action=action, **extra)


def denied(value, code=None):
    try:
        pocket.dispatch(value)
    except pocket.PocketError as exc:
        if code:
            assert str(exc) == code, (str(exc), code)
    else:
        raise AssertionError('invalid request accepted')


with tempfile.TemporaryDirectory(prefix='companion-pocket-store-') as temp:
    private = Path(temp)
    originals = private / 'originals'
    originals.mkdir()
    files = [originals / ('file %02d <plain>.txt' % n) for n in range(36)]
    for n, file in enumerate(files):
        file.write_bytes(('private content %d\n' % n).encode())
    first = files[0].as_uri()
    with patch.dict(os.environ, {'XDG_STATE_HOME': str(private / 'state')}):
        target = pocket.path()
        assert pocket.dispatch(request('list'))['items'] == []
        assert not target.exists() and not target.parent.exists()
        for invalid in (None, [], {}, request('add', urls=[first], source='ai') | {'consent': False},
                        request('list') | {'version': True}, request('list') | {'version': 1.0},
                        request('list') | {'action': []}, request('list') | {'model': 'test'}):
            denied(invalid)
        assert not target.exists()
        for urls in ([], [first] * 9, ['https://example.com/a'], ['file://remote/a'],
                     [None], ['file:///a%00b'], ['file:///a%0ab'], ['file:///a%2Fb/../c'],
                     ['file:///bad%GG'], ['file:///a?secret=1'], ['file:///a#fragment'],
                     [originals.as_uri()], [(originals/'missing').as_uri()]):
            denied(request('add', urls=urls, source='picker'))
        symlink = originals / 'link';symlink.symlink_to(files[0])
        linkdir = private / 'linked-directory';linkdir.symlink_to(originals, target_is_directory=True)
        fifo = originals / 'fifo';os.mkfifo(fifo)
        for file in (symlink, fifo, linkdir/files[0].name):
            denied(request('add', urls=[file.as_uri()], source='drop'))
        assert not target.exists()
        original_open = os.open
        def directories_only(path, flags, *args, **kwargs):
            assert flags & os.O_DIRECTORY, 'metadata opened file contents'
            return original_open(path, flags, *args, **kwargs)
        with patch.object(os, 'open', directories_only):
            assert pocket.metadata(first)['size'] == files[0].stat().st_size
        saved = pocket.dispatch(request('add', urls=[first, first], source='picker'))
        assert len(saved['items']) == 1
        old = saved['items'][0]
        # A sanitized historical version-1 row retains IDs, timestamps, source
        # and unrelated data through reload, repeated add and later releases.
        with sqlite3.connect(target) as db:
            db.execute('UPDATE items SET id=27,created_ms=41 WHERE id=?', (old['id'],))
        legacy = pocket.dispatch(request('list'))['items'][0]
        assert legacy['id'] == 27 and legacy['created_ms'] == 41
        for unused in range(3):
            again = pocket.dispatch(request('add', urls=[first, files[1].as_uri()], source='drop'))
            retained = next(item for item in again['items'] if item['path'] == str(files[0]))
            assert retained == legacy
            assert len(again['items']) == 2
        before = target.read_bytes()
        denied(request('add', urls=[files[2].as_uri(), (originals/'missing').as_uri()], source='picker'))
        assert target.read_bytes() == before, 'partial batch persisted'
        # Kill an uncommitted transaction, then let SQLite recover on restart.
        code = '''import importlib.util,os,sys
s=importlib.util.spec_from_file_location('pocket',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
d=m.connect();d.execute('BEGIN IMMEDIATE')
d.execute("INSERT INTO items(path,name,size,modified_ms,created_ms,source) VALUES('/crashed','crashed',0,0,1,'drop')")
os._exit(9)
'''
        crashed = subprocess.run([sys.executable, '-c', code, str(HELPER)], env=os.environ.copy())
        assert crashed.returncode == 9
        assert len(pocket.dispatch(request('list'))['items']) == 2
        for start in range(2, 32, 6):
            batch = [f.as_uri() for f in files[start:min(start+6, 32)]]
            pocket.dispatch(request('add', urls=batch, source='drop'))
        assert len(pocket.dispatch(request('list'))['items']) == 32
        denied(request('add', urls=[files[32].as_uri()], source='drop'), 'pocket_full')
        assert len(pocket.dispatch(request('add', urls=[first], source='picker'))['items']) == 32
        for bad in (False, 0, -1, '27', 1.5):
            denied(request('remove', id=bad))
        assert pocket.dispatch(request('remove', id=27))['changed'] == 1
        assert pocket.dispatch(request('remove', id=27))['changed'] == 0
        denied(request('clear', confirmed=False))
        assert pocket.dispatch(request('clear', confirmed=True))['changed'] == 31
        assert pocket.dispatch(request('clear', confirmed=True))['changed'] == 0
        assert pocket.dispatch(request('add', urls=[first], source='drop'))['items'][0]['id'] > 27
        assert (target.stat().st_mode & 0o777) == 0o600
        # Future/foreign schemas fail without rewriting bytes or version.
        with sqlite3.connect(target) as db:db.execute('PRAGMA user_version=99')
        before = target.read_bytes()
        denied(request('list'), 'unsupported_store_version')
        assert target.read_bytes() == before
    for n, file in enumerate(files):
        assert file.read_bytes() == ('private content %d\n' % n).encode()
    assert symlink.is_symlink() and fifo.exists()
    # The one-shot CLI never echoes private input on malformed JSON.
    result = subprocess.run([sys.executable, str(HELPER)], input='{"private":"token",', text=True,
                            capture_output=True, env=dict(os.environ, XDG_STATE_HOME=str(private/'cli')))
    assert result.returncode == 1 and not result.stderr
    assert json.loads(result.stdout)['error'] == 'invalid_request' and 'token' not in result.stdout
    assert not (private/'cli').exists()
    native_bin = ROOT / 'native/bin/alis-companiond'
    if not native_bin.is_file():
        native_bin = Path(os.environ.get('ALIS_COMPANION_SOURCE', ROOT)) / 'native/target/release/alis-companiond'
    if native_bin.is_file():
        rs_cli = subprocess.run([str(native_bin), '--pocket'], input=json.dumps(request('list')) + '\n',
                                text=True, capture_output=True, env=dict(os.environ, XDG_STATE_HOME=str(private/'rs-cli')))
        assert rs_cli.returncode == 0 and json.loads(rs_cli.stdout)['items'] == []
        assert not (private/'rs-cli').exists()

print('COMPANION_POCKET_STORE_PASS consent copyReferences metadataOnly atomicBatch dedupe32 legacyV1 restart retry originalsIntact futurePreserved')
