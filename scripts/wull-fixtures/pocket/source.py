#!/usr/bin/env python3
"""Test-only external Wayland drag source; never reads or removes contents."""
import json
import os
import sys
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QWidget

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
        action=drag.exec(Qt.CopyAction|Qt.MoveAction,Qt.MoveAction)
        # This is deliberately not a real file manager: even a failed test
        # choosing Move cannot remove an original. The return action is checked.
        print(json.dumps({'result':action.name}),flush=True)

app=QApplication(sys.argv)
source=Source();source.showFullScreen()
print('POCKET_SOURCE_READY',flush=True)
raise SystemExit(app.exec())
