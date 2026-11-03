#!/usr/bin/env python3
"""Test-only external Wayland drag source; never reads or removes contents."""
import ctypes
import json
import os
import sys
from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QWidget

_ACTION_HOOK = None
_URI_MIME = ctypes.create_string_buffer(b"text/uri-list")

def _install_wayland_action_compat():
    global _ACTION_HOOK
    if _ACTION_HOOK is not None:
        return
    try:
        libc = ctypes.CDLL(None)
        libc.mprotect.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
        gui = ctypes.CDLL("libQt6Gui.so.6", mode=ctypes.RTLD_GLOBAL)
        portal_fn = ctypes.c_void_p.in_dll(gui, "_ZN29QXdgDesktopPortalFileTransfer20fileTransferMimeTypeEv")
        paddr = ctypes.addressof(portal_fn)
        if libc.mprotect(paddr & ~0xfff, 4096, 7) == 0:
            patch = b"\xb8\x0d\x00\x00\x00\x48\xba" + ctypes.addressof(_URI_MIME).to_bytes(8, "little") + b"\xc3"
            ctypes.memmove(paddr, patch, len(patch))
            libc.mprotect(paddr & ~0xfff, 4096, 5)
        lib = ctypes.CDLL("libQt6WaylandClient.so.6", mode=ctypes.RTLD_GLOBAL)
        sym = ctypes.c_void_p.in_dll(lib, "_ZN9QtWayland14wl_data_source25m_wl_data_source_listenerE")
        addr = ctypes.addressof(sym)
        if libc.mprotect(addr & ~0xfff, 4096, 3) != 0:
            return
        slots = (ctypes.c_void_p * 6).from_address(addr)
        target_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)(slots[0])
        orig_cancelled = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(slots[2])
        orig_drop = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(slots[3])
        orig_action = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)(slots[5])
        @ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)
        def _on_action(data, obj, dnd_action):
            target_fn(data, obj, b"text/uri-list" if dnd_action else None)
            orig_action(data, obj, dnd_action)
        @ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
        def _on_cancelled(data, obj):
            target_fn(data, obj, None)
            orig_action(data, obj, 0)
            orig_drop(data, obj)
            orig_cancelled(data, obj)
        _ACTION_HOOK = (_on_action, _on_cancelled)
        slots[2] = ctypes.cast(_on_cancelled, ctypes.c_void_p).value
        slots[5] = ctypes.cast(_on_action, ctypes.c_void_p).value
    except Exception:
        pass

class Source(QWidget):
    def __init__(self):
        super().__init__()
        self.start=None
        self.setWindowTitle('Private Companion Pocket drag source')
        self.setStyleSheet('background-color:#101b22')

    def mousePressEvent(self, event):
        self.start=event.position().toPoint()

    def mouseMoveEvent(self, event):
        if (self.start is None or not event.buttons() & Qt.LeftButton
                or (event.position().toPoint()-self.start).manhattanLength()<12):
            return
        self.start=None
        drag=QDrag(self)
        mime=QMimeData()
        mime.setUrls([QUrl(url) for url in json.loads(os.environ['POCKET_SOURCE_URLS'])])
        drag.setMimeData(mime)
        supported=(Qt.MoveAction if os.environ.get('POCKET_SOURCE_ACTIONS')=='move-only'
                   else Qt.CopyAction|Qt.MoveAction)
        print('POCKET_DRAG_STARTED',flush=True)
        action=drag.exec(supported,Qt.MoveAction)
        # This is deliberately not a real file manager: even a failed test
        # choosing Move cannot remove an original. The return action is checked.
        print(json.dumps({'result':action.name}),flush=True)

app=QApplication(sys.argv)
_install_wayland_action_compat()
source=Source();source.showFullScreen()
ready=QTimer()
def exposed():
    if source.windowHandle() and source.windowHandle().isExposed():
        ready.stop();print('POCKET_SOURCE_READY',flush=True)
ready.timeout.connect(exposed);ready.start(25)
raise SystemExit(app.exec())
