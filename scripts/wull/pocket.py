#!/usr/bin/env python3
"""Explicit local-file metadata references. Never opens file contents or a vault.

One bounded request per process; a separate versioned store leaves existing
chat, memories and journal data untouched. Removing references cannot unlink
their originals. A whole selection commits atomically, including retries.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import unicodedata
from urllib.parse import unquote, urlsplit

MAX_ITEMS = 32
MAX_SELECTION = 8
MAX_REQUEST = 128 * 1024
MAX_PATH = 4096
MAX_INTEGER = 9007199254740991
COLUMNS = ['id', 'path', 'name', 'size', 'modified_ms', 'created_ms', 'source']


class PocketError(Exception):
    pass


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_INTEGER


def _clean(value):
    return isinstance(value, str) and not any(
        unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value)


def local_path(url):
    if not _clean(url) or not 1 <= len(url) <= MAX_PATH * 3:
        raise PocketError('invalid_file_url')
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'file' or parsed.netloc or parsed.query or parsed.fragment:
            raise PocketError('local_files_only')
        if re.search(r'%(?![0-9a-fA-F]{2})', parsed.path):
            raise PocketError('invalid_file_url')
        value = unquote(parsed.path, encoding='utf-8', errors='strict')
    except (ValueError, UnicodeError) as exc:
        raise PocketError('invalid_file_url') from exc
    if not _clean(value) or not 1 < len(value) <= MAX_PATH or not value.startswith('/'):
        raise PocketError('invalid_file_url')
    if any(part in ('.', '..', '') for part in value.split('/')[1:]):
        raise PocketError('invalid_file_url')
    if len(value.split('/')) > 128:
        raise PocketError('invalid_file_url')
    return value


def metadata(url):
    value = local_path(url)
    parts = value.split('/')[1:]
    # Traverse directories with no-follow descriptors, then stat only the
    # selected entry. No file open, sniffing, directory listing or recursion.
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        info = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
    except OSError as exc:
        raise PocketError('file_unavailable') from exc
    finally:
        os.close(directory)
    if not stat.S_ISREG(info.st_mode):
        raise PocketError('regular_files_only')
    modified = info.st_mtime_ns // 1_000_000
    if not _integer(info.st_size) or not _integer(modified):
        raise PocketError('unsupported_metadata')
    return dict(path=value, name=parts[-1], size=info.st_size, modified_ms=modified)


def path():
    root = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
    return root / 'alis/companion/pocket.sqlite3'


def connect(create=False):
    target = path()
    if not target.exists() and not target.is_symlink() and not create:
        return None
    db = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = target.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid():
            raise PocketError('unsafe_store')
        if not target.exists() and not target.is_symlink():
            fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise PocketError('unsafe_store')
        for suffix in ('-journal', '-wal', '-shm'):
            sidecar = Path(str(target) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                side = sidecar.lstat()
                if not stat.S_ISREG(side.st_mode) or side.st_uid != os.getuid() or side.st_nlink != 1:
                    raise PocketError('unsafe_store')
        # Inspect before changing permissions, pragmas or schema. Unknown
        # releases and foreign databases remain byte-for-byte untouched.
        db = sqlite3.connect(target, timeout=1.5)
        version = db.execute('PRAGMA user_version').fetchone()[0]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if version not in (0, 1) or tables - {'items', 'sqlite_sequence'}:
            raise PocketError('unsupported_store_version')
        if 'items' in tables:
            columns = [r[1] for r in db.execute('PRAGMA table_info(items)')]
            if columns != COLUMNS or version != 1:
                raise PocketError('unsupported_store_version')
        elif tables or version != 0:
            raise PocketError('unsupported_store_version')
        os.chmod(target, 0o600)
        db.execute('PRAGMA busy_timeout=1500')
        db.execute('PRAGMA synchronous=FULL')
        if not tables:
            with db:
                db.execute('''CREATE TABLE items(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    size INTEGER NOT NULL, modified_ms INTEGER NOT NULL,
                    created_ms INTEGER NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('picker','drop'))
                )''')
                db.execute('PRAGMA user_version=1')
        return db
    except PocketError:
        if db is not None:
            db.close()
        raise
    except (OSError, sqlite3.Error) as exc:
        if db is not None:
            db.close()
        raise PocketError('store_unavailable') from exc


def rows(db):
    if db is None:
        return []
    values = db.execute('SELECT ' + ','.join(COLUMNS) + ' FROM items ORDER BY id DESC LIMIT ?',
                        (MAX_ITEMS + 1,)).fetchall()
    if len(values) > MAX_ITEMS:
        raise PocketError('invalid_stored_metadata')
    result = []
    for row in values:
        item = dict(zip(COLUMNS, row))
        if (not _integer(item['id'], 1) or not _clean(item['path'])
                or not 1 < len(item['path']) <= MAX_PATH or not item['path'].startswith('/')
                or not _clean(item['name']) or not 1 <= len(item['name']) <= 255
                or item['source'] not in ('picker', 'drop')
                or not all(_integer(item[key]) for key in ('size', 'modified_ms', 'created_ms'))):
            raise PocketError('invalid_stored_metadata')
        result.append(item)
    return result


def dispatch(request):
    if (not isinstance(request, dict) or type(request.get('version')) is not int
            or request['version'] != 1 or request.get('consent') is not True):
        raise PocketError('explicit_consent_required')
    action = request.get('action')
    fields = {'list': set(), 'add': {'urls', 'source'}, 'remove': {'id'}, 'clear': {'confirmed'}}
    if not isinstance(action, str) or action not in fields or set(request) != {'version', 'action', 'consent'} | fields[action]:
        raise PocketError('invalid_request')
    selection = []
    if action == 'add':
        urls = request['urls']
        if (request['source'] not in ('picker', 'drop') or not isinstance(urls, list)
                or not 1 <= len(urls) <= MAX_SELECTION):
            raise PocketError('selection_limit')
        selection = list({item['path']: item for item in map(metadata, urls)}.values())
    if action == 'remove' and not _integer(request['id'], 1):
        raise PocketError('invalid_request')
    if action == 'clear' and request['confirmed'] is not True:
        raise PocketError('explicit_consent_required')
    db = connect(create=action == 'add')
    try:
        existing = rows(db)
        changed = 0
        if db is not None and action != 'list':
            with db:
                db.execute('BEGIN IMMEDIATE')
                if action == 'add':
                    known = {r['path'] for r in rows(db)}
                    if len(known | {r['path'] for r in selection}) > MAX_ITEMS:
                        raise PocketError('pocket_full')
                    now = int(time.time() * 1000)
                    for item in selection:
                        db.execute('''INSERT INTO items(path,name,size,modified_ms,created_ms,source)
                            VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                            name=excluded.name,size=excluded.size,modified_ms=excluded.modified_ms''',
                            (item['path'], item['name'], item['size'], item['modified_ms'], now, request['source']))
                        changed += 1
                elif action == 'remove':
                    changed = db.execute('DELETE FROM items WHERE id=?', (request['id'],)).rowcount
                elif action == 'clear':
                    changed = db.execute('DELETE FROM items').rowcount
                existing = rows(db)
        return dict(version=1, ok=True, action=action, items=existing, changed=changed)
    except sqlite3.Error as exc:
        raise PocketError('store_unavailable') from exc
    finally:
        if db is not None:
            db.close()


def main():
    os.umask(0o077)
    try:
        line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if len(line) > MAX_REQUEST:
            raise PocketError('selection_limit')
        result = dispatch(json.loads(line))
    except (PocketError, ValueError, OSError) as exc:
        code = str(exc) if isinstance(exc, PocketError) else 'invalid_request'
        result = dict(version=1, ok=False, error=code)
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
