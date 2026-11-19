"""Crash safety: error log, autosave of unsaved work and recovery after a crash.

Everything lives in ~/.beamline_editor/:
- errors.log         unhandled Python exceptions and hard crashes (faulthandler)
- autosave/<id>.pkl  unsaved work of a running editor window, removed when the window closes normally.
                     Each file has a <id>.pkl.lock held by its process: a file whose lock is free belongs to
                     an editor that did not close normally, and is offered for recovery at the next start.
"""
from __future__ import annotations

import faulthandler
import glob
import os
import pickle
import sys
import time
import traceback
import uuid

from PyQt6.QtCore import QLockFile

APP_DIR = os.path.join(os.path.expanduser("~"), ".beamline_editor")
AUTOSAVE_DIR = os.path.join(APP_DIR, "autosave")
LOG_PATH = os.path.join(APP_DIR, "errors.log")

_log = None  # kept open for faulthandler


def install_error_handlers(on_error) -> None:
    """Log unhandled exceptions instead of letting PyQt abort the application, then call on_error(text).

    Hard crashes (segmentation faults in Qt) cannot be caught, but faulthandler writes their traceback to the log.
    """
    global _log
    os.makedirs(APP_DIR, exist_ok=True)
    _log = open(LOG_PATH, "a", encoding="utf-8")
    faulthandler.enable(_log)

    def hook(exc_type, value, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, value, tb))
        _log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} unhandled exception\n{text}")
        _log.flush()
        if sys.__stderr__ is not None:
            sys.__stderr__.write(text)
        try:
            on_error(text)
        except Exception:  # never raise from the hook
            pass

    sys.excepthook = hook


class Autosave:
    """Autosave file of one editor window."""

    def __init__(self):
        os.makedirs(AUTOSAVE_DIR, exist_ok=True)
        self.path = os.path.join(AUTOSAVE_DIR, f"{uuid.uuid4().hex}.pkl")
        self.lock = QLockFile(self.path + ".lock")
        self.lock.setStaleLockTime(0)  # stale only when the process is gone, never because of its age
        self.lock.tryLock(0)

    def write(self, state: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(state, f)
        os.replace(tmp, self.path)

    def clear(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)

    def release(self) -> None:
        self.clear()
        self.lock.unlock()


class Orphan:
    """Autosave left by an editor that did not close normally. Holds its lock until resolved."""

    def __init__(self, path: str, lock: QLockFile):
        self.path, self.lock = path, lock
        self.time = os.path.getmtime(path)
        with open(path, "rb") as f:
            self.state: dict = pickle.load(f)

    def discard(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)
        self.lock.unlock()


def find_orphans() -> list[Orphan]:
    """Autosaves whose editor is no longer running, newest first. Unreadable ones are deleted."""
    for path in glob.glob(os.path.join(AUTOSAVE_DIR, "*.pkl.lock")):  # left without autosave: clean up
        if not os.path.exists(path[:-5]):
            lock = QLockFile(path)
            lock.setStaleLockTime(0)
            if lock.tryLock(0):
                lock.unlock()  # removes the file
    found = []
    for path in glob.glob(os.path.join(AUTOSAVE_DIR, "*.pkl")):
        lock = QLockFile(path + ".lock")
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):  # its editor is still running
            continue
        try:
            found.append(Orphan(path, lock))
        except Exception:  # e.g. written by an older version of the editor
            os.remove(path)
            lock.unlock()
    return sorted(found, key=lambda o: -o.time)
