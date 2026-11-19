"""Version history of saved layouts.

Every save keeps a copy of the written file in a .history folder next to it:
    data/1_LINAC_Survey.txt  ->  data/.history/1_LINAC_Survey.txt/2026-10-07_14-03-12.txt
The file on disk before the first save (if not in the history yet) is kept too, dated by its modification time.
Identical consecutive versions are stored once.
"""
from __future__ import annotations

import copy
import filecmp
import os
import shutil
import time

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QVBoxLayout)

HISTORY_DIR = ".history"
STAMP = "%Y-%m-%d_%H-%M-%S"


def history_dir(path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(path)), HISTORY_DIR, os.path.basename(path))


def versions(path: str) -> list[str]:
    """Saved versions of path, newest first."""
    d = history_dir(path)
    if not os.path.isdir(d):
        return []
    return sorted((os.path.join(d, f) for f in os.listdir(d) if not f.startswith(".")), reverse=True)


def version_time(version: str) -> str:
    """Readable date of a version, from its file name."""
    stem = os.path.splitext(os.path.basename(version))[0]
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.strptime(stem[:19], STAMP)) + stem[19:]
    except ValueError:
        return stem


def _archive(path: str, when: float) -> None:
    """Copy path into its history, unless it is identical to the newest version."""
    old = versions(path)
    if old and filecmp.cmp(path, old[0], shallow=False):
        return
    d = history_dir(path)
    os.makedirs(d, exist_ok=True)
    ext = os.path.splitext(path)[1]
    base = os.path.join(d, time.strftime(STAMP, time.localtime(when)))
    target, n = base + ext, 2
    while os.path.exists(target):  # several saves within one second
        target, n = f"{base}_{n}{ext}", n + 1
    shutil.copy2(path, target)


def before_save(path: str) -> None:
    """Keep the file about to be overwritten, if the history does not have it yet."""
    if os.path.isfile(path):
        _archive(path, os.path.getmtime(path))


def after_save(path: str) -> None:
    """Add the file just written to its history."""
    _archive(path, time.time())


class HistoryDialog(QDialog):
    """List the versions of the current file: compare them, or open one as the current layout."""

    def __init__(self, win, path: str):
        super().__init__(win)
        self.win, self.path = win, path
        self.setWindowTitle(f"History of {os.path.basename(path)}")
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        on_disk = path if os.path.isfile(path) else None
        for v in versions(path):
            text = f"{version_time(v)}    {os.path.getsize(v) / 1024:.1f} kB"
            if on_disk and filecmp.cmp(v, on_disk, shallow=False):
                text += "    (= file on disk)"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, v)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.compare_current = QPushButton("Compare with current layout")
        self.compare_current.setToolTip("Compare the selected version with the layout open in the editor")
        self.compare_two = QPushButton("Compare the two selected")
        self.restore = QPushButton("Open this version…")
        self.restore.setToolTip("Load the selected version in the editor. Save to make it the newest version.")
        folder = QPushButton("Show folder")
        close = QPushButton("Close")
        self.compare_current.clicked.connect(self._compare_current)
        self.compare_two.clicked.connect(self._compare_two)
        self.restore.clicked.connect(self._restore)
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(history_dir(path))))
        close.clicked.connect(self.close)
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.list.itemDoubleClicked.connect(lambda _: self._compare_current())
        buttons = QHBoxLayout()
        for b in (self.compare_current, self.compare_two, self.restore, folder):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Saved versions, newest first. They are kept in\n{history_dir(path)}"))
        lay.addWidget(self.list)
        lay.addLayout(buttons)
        self.resize(640, 360)
        self._update_buttons()

    def _selected(self) -> list[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()]

    def _update_buttons(self) -> None:
        n = len(self._selected())
        self.compare_current.setEnabled(n == 1 and self.win.bl is not None)
        self.compare_two.setEnabled(n == 2)
        self.restore.setEnabled(n == 1)

    def _label(self, version: str) -> str:
        return f"{os.path.basename(self.path)} @ {version_time(version)}"

    def _read(self, version: str):
        result = self.win._read(version)
        return None if result is None else result[0]

    def _compare_current(self) -> None:
        sel = self._selected()
        if len(sel) != 1 or self.win.bl is None or (bl := self._read(sel[0])) is None:
            return
        name = os.path.basename(self.win.path) + ("*" if not self.win.undo.isClean() else "")
        self.win.open_compare(bl, self._label(sel[0]), copy.deepcopy(self.win.bl), name)

    def _compare_two(self) -> None:
        sel = sorted(self._selected())  # older first
        if len(sel) != 2:
            return
        a, b = self._read(sel[0]), self._read(sel[1])
        if a is not None and b is not None:
            self.win.open_compare(a, self._label(sel[0]), b, self._label(sel[1]))

    def _restore(self) -> None:
        sel = self._selected()
        if len(sel) != 1 or not self.win.maybe_discard():
            return
        if self.win.open_version(sel[0], self.path):
            QMessageBox.information(self, "History", f"Opened the version of {version_time(sel[0])}.\n"
                                    "Save to make it the newest version.")
            self.close()
