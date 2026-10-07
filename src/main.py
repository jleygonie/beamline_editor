"""Beamline Layout Editor: entry point and main window."""
from __future__ import annotations

import copy
import json
import os
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QUndoCommand, QUndoStack
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
                             QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QRadioButton,
                             QStackedWidget, QToolButton, QVBoxLayout, QWidget)

import element_types as et
from canvas import Canvas
from compare_window import CompareWindow
from glue import glue
from json_io import from_dict, write_json
from model import TOL, Beamline, EditError, Element, Issue
from panels import UNIT_DECIMALS, UNIT_SCALE, DistancePanel, ElementTable, PropertyPanel, fmt_len, make_spin
from survey_io import parse_survey, write_survey


def snapshot(bl: Beamline) -> tuple:
    return copy.deepcopy((bl.elements, bl.apertures))


class SnapshotCommand(QUndoCommand):
    """Undo step holding deep copies of the elements and apertures before and after an edit."""

    def __init__(self, win: MainWindow, text: str, before: tuple, after: tuple):
        super().__init__(text)
        self.win, self.before, self.after = win, before, after

    def redo(self) -> None:
        self.win.bl.elements, self.win.bl.apertures = copy.deepcopy(self.after)
        self.win.refresh()

    def undo(self) -> None:
        self.win.bl.elements, self.win.bl.apertures = copy.deepcopy(self.before)
        self.win.refresh()


class InsertDialog(QDialog):
    def __init__(self, parent: MainWindow, s_center: float, name: str):
        super().__init__(parent)
        self.setWindowTitle("Insert")
        unit = parent.unit
        self.used = {x.name for x in parent.bl.elements}
        self.name = QLineEdit(name)
        self.type = QComboBox()
        for key, spec in et.CATALOGUE.items():
            self.type.addItem(spec["label"], key)
        self.length = make_spin(UNIT_DECIMALS[unit], f" {unit}")
        self.center = make_spin(UNIT_DECIMALS[unit], f" {unit}")
        self.center.setValue(s_center * UNIT_SCALE[unit])
        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Type", self.type)
        form.addRow("Length", self.length)
        form.addRow("s_center", self.center)
        self.hint = QLabel()
        self.hint.setStyleSheet("color: rgb(200, 0, 0)")
        form.addRow(self.hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.name.textChanged.connect(self._check_name)
        self._check_name()
        self.scale = UNIT_SCALE[unit]

    def _check_name(self) -> None:
        name = self.name.text().strip()
        problem = "Enter a name" if not name else "Name already used" if name in self.used else ""
        self.hint.setText(problem)
        self.hint.setVisible(bool(problem))
        self.ok.setEnabled(not problem)

    def element(self) -> Element:
        typ = self.type.currentData()
        length = self.length.value() / self.scale
        return Element(name=self.name.text().strip(), keyword=et.default_keyword(typ, "MARKER"), type=typ,
                       s_end=self.center.value() / self.scale + length / 2, length=length,
                       params=et.default_params(typ, length))


def read_layout(path: str) -> tuple[Beamline, list[str]]:
    """Read a survey or JSON layout. Returns (beamline, warnings)."""
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    if text.lstrip().startswith("{"):
        return from_dict(json.loads(text))
    return parse_survey(text), []


class GlueDialog(QDialog):
    """Order the files to glue and choose how their positions are combined."""

    def __init__(self, parent: MainWindow, parts: list[tuple[str, Beamline]]):
        super().__init__(parent)
        self.setWindowTitle("Glue files")
        self.parts = dict(parts)
        self.list = QListWidget()
        for path, bl in sorted(parts, key=lambda t: t[1].limits()[0]):
            lo, hi = bl.limits()
            item = QListWidgetItem(f"{os.path.basename(path)}   [{fmt_len(lo, parent.unit)} → "
                                   f"{fmt_len(hi, parent.unit)}]")
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        up, down = QPushButton("Up"), QPushButton("Down")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        self.keep = QRadioButton("Keep the s positions from the files (gaps are filled with a drift)")
        self.chain = QRadioButton("Place the files end to end, in this order")
        self.keep.setChecked(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        side = QVBoxLayout()
        side.addWidget(up)
        side.addWidget(down)
        side.addStretch()
        row = QHBoxLayout()
        row.addWidget(self.list)
        row.addLayout(side)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Upstream file first:"))
        lay.addLayout(row)
        lay.addWidget(self.keep)
        lay.addWidget(self.chain)
        lay.addWidget(buttons)
        self.resize(520, 300)

    def _move(self, step: int) -> None:
        row = self.list.currentRow()
        if 0 <= row + step < self.list.count():
            self.list.insertItem(row + step, self.list.takeItem(row))
            self.list.setCurrentRow(row + step)

    def ordered(self) -> list[Beamline]:
        return [self.parts[self.list.item(r).data(Qt.ItemDataRole.UserRole)] for r in range(self.list.count())]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.bl: Beamline | None = None
        self.path = ""
        self.selection: list[int] = []
        self.unit = "m"
        self.issues: list[Issue] = []
        self.undo = QUndoStack(self)
        self.issue_dialog: QDialog | None = None
        self.compare_windows: list[CompareWindow] = []

        self.canvas = Canvas()
        self.setCentralWidget(self.canvas)
        self.table = ElementTable(self)
        self.props = PropertyPanel(self)
        self.dist = DistancePanel(self)
        self.stack = QStackedWidget()
        self.stack.addWidget(QWidget())
        self.stack.addWidget(self.props)
        self.stack.addWidget(self.dist)
        for title, widget, area in (("Elements", self.table, Qt.DockWidgetArea.BottomDockWidgetArea),
                                    ("Properties", self.stack, Qt.DockWidgetArea.RightDockWidgetArea)):
            dock = QDockWidget(title, self)
            dock.setObjectName(title)
            dock.setWidget(widget)
            dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                             | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
            self.addDockWidget(area, dock)

        self._build_toolbar()
        self._build_statusbar()
        self.canvas.selectionChanged.connect(lambda sel: self.set_selection(sel, source=self.canvas))
        self.canvas.dragFinished.connect(self._drag_finished)
        self.canvas.message.connect(lambda text: self.statusBar().showMessage(text, 4000))
        self.canvas.measureModeExited.connect(lambda: self.measure_act.setChecked(False))
        self.undo.cleanChanged.connect(self._update_title)
        self.resize(1300, 850)
        self.refresh()

    # ------------------------------------------------------------ construction
    def _action(self, text: str, slot, shortcut=None) -> QAction:
        act = QAction(text, self)
        act.triggered.connect(slot)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        self.addAction(act)
        return act

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        tb.setObjectName("Main")
        tb.setMovable(False)
        tb.addAction(self._action("Open…", self.open_dialog, QKeySequence.StandardKey.Open))
        tb.addAction(self._action("Open && glue…", self.glue_dialog, "Ctrl+Shift+O"))
        compare_act = self._action("Compare…", self.compare_dialog, "Ctrl+D")
        compare_act.setToolTip("Compare two files, or the current layout with a file (Ctrl+D)")
        tb.addAction(compare_act)
        tb.addAction(self._action("Save survey…", self.save_survey, "Ctrl+S"))
        tb.addAction(self._action("Save JSON…", self.save_json, "Ctrl+Shift+S"))
        tb.addSeparator()
        self.undo_act = self._action("Undo", self.undo.undo, "Ctrl+Z")
        self.redo_act = self._action("Redo", self.undo.redo, "Ctrl+Y")
        self.undo_act.setEnabled(False)
        self.redo_act.setEnabled(False)
        self.undo.canUndoChanged.connect(self.undo_act.setEnabled)
        self.undo.canRedoChanged.connect(self.redo_act.setEnabled)
        tb.addAction(self.undo_act)
        tb.addAction(self.redo_act)
        tb.addSeparator()
        tb.addAction(self._action("Fit", self.canvas.fit, "F"))
        tb.addAction(self._action("Insert…", self.insert_element, "Ins"))
        tb.addAction(self._action("Delete", self.remove_element, QKeySequence.StandardKey.Delete))
        tb.addSeparator()
        self.measure_act = self._action("Measure", lambda: None, "M")
        self.measure_act.setCheckable(True)
        self.measure_act.toggled.connect(self.canvas.set_measure_mode)  # also when unchecked by Esc
        self.measure_act.setToolTip("Measure distances: click two highlighted points (M)")
        tb.addAction(self.measure_act)
        tb.addAction(self._action("Clear measures", self.canvas.clear_measures))
        self.flip_box = QCheckBox("Right to left")
        self.flip_box.setToolTip("Draw the beamline with s increasing to the left")
        self.flip_box.toggled.connect(self.canvas.set_flipped)
        tb.addWidget(self.flip_box)
        self.floor_box = QCheckBox("Floor plan")
        self.floor_box.setToolTip("Draw the top view, bending the line at each element with an angle")
        self.floor_box.toggled.connect(self.canvas.set_floor)
        tb.addWidget(self.floor_box)
        self.aperture_box = QCheckBox("Apertures")
        self.aperture_box.setChecked(True)
        self.aperture_box.setToolTip("Show the aperture sections (beam pipe) from the JSON file")
        self.aperture_box.toggled.connect(self.canvas.set_apertures)
        tb.addWidget(self.aperture_box)
        tb.addSeparator()
        tb.addWidget(QLabel(" Unit "))
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["m", "mm"])
        self.unit_combo.currentTextChanged.connect(self.set_unit)
        tb.addWidget(self.unit_combo)
        self.labels_box = QCheckBox("Labels")
        self.labels_box.setChecked(True)
        self.labels_box.toggled.connect(self.canvas.set_labels)
        tb.addWidget(self.labels_box)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.file_label, self.length_label, self.count_label = QLabel(), QLabel(), QLabel()
        self.issue_button = QToolButton()
        self.issue_button.setAutoRaise(True)
        self.issue_button.clicked.connect(self.show_issues)
        for w in (self.file_label, self.length_label, self.count_label, self.issue_button):
            sb.addPermanentWidget(w)

    # ------------------------------------------------------------ state
    def apply(self, label: str, fn) -> bool:
        """Run fn(beamline) as one undo step. Returns False on EditError."""
        if self.bl is None:
            return False
        before = snapshot(self.bl)
        try:
            fn(self.bl)
        except EditError as err:
            self.statusBar().showMessage(str(err), 6000)
            self.refresh()
            return False
        after = snapshot(self.bl)
        if after != before:
            self.undo.push(SnapshotCommand(self, label, before, after))
        return True

    def refresh(self) -> None:
        bl = self.bl
        n = len(bl.elements) if bl else 0
        self.selection = [i for i in self.selection if i < n]
        self.issues = bl.validate() if bl else []
        self.canvas.bl = self.canvas.shown = bl
        self.canvas.selection = list(self.selection)
        self.canvas.rebuild()
        QTimer.singleShot(0, self.table.refresh)  # not while the table delegate is committing
        self._refresh_panels()
        self.file_label.setText(os.path.basename(self.path) if self.path else "No file")
        self.length_label.setText(f"L = {fmt_len(bl.total_length(), self.unit)}" if bl else "")
        self.count_label.setText(f"{n} elements")
        errors = sum(s.level == "error" for s in self.issues)
        warnings = len(self.issues) - errors
        parts = [f"{errors} error{'s' * (errors != 1)}"] if errors else []
        parts += [f"{warnings} warning{'s' * (warnings != 1)}"] if warnings else []
        self.issue_button.setText(", ".join(parts) or "No issues")
        if self.issue_dialog is not None and self.issue_dialog.isVisible():
            self._fill_issues()
        self._update_title()

    def _refresh_panels(self) -> None:
        k = len(self.selection) if self.bl else 0
        self.stack.setCurrentIndex(k if k <= 2 else 0)
        self.props.refresh()
        self.dist.refresh()

    def set_selection(self, sel: list[int], source=None) -> None:
        self.selection = list(sel)
        if source is not self.canvas:
            self.canvas.set_selection(self.selection)
        if source is not self.table:
            self.table.set_selection(self.selection)
        self._refresh_panels()

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        self.canvas.set_unit(unit)
        self.table.model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, 7)
        self.refresh()

    def _update_title(self, *_) -> None:
        name = os.path.basename(self.path) if self.path else "untitled"
        self.setWindowTitle(f"{name}{'*' if not self.undo.isClean() else ''} - Beamline Layout Editor")

    # ------------------------------------------------------------ files
    def maybe_discard(self) -> bool:
        if self.undo.isClean():
            return True
        answer = QMessageBox.question(self, "Unsaved changes", "Discard unsaved changes?",
                                      QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Discard

    def open_dialog(self) -> None:
        if not self.maybe_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open", os.path.dirname(self.path),
                                              "Layouts (*.txt *.tfs *.json);;All files (*)")
        if path:
            self.load(path)

    def _read(self, path: str) -> tuple[Beamline, list[str]] | None:
        try:
            return read_layout(path)
        except (OSError, ValueError, KeyError) as err:
            QMessageBox.critical(self, "Open", f"Cannot read {os.path.basename(path)}:\n{err}")
            return None

    def _set_beamline(self, bl: Beamline, path: str, warnings: list[str]) -> None:
        self.bl, self.path, self.selection = bl, path, []
        self.undo.clear()
        self.undo.setClean()
        self.canvas.clear_measures()
        self.refresh()
        QTimer.singleShot(0, self.canvas.fit)
        if warnings:
            QMessageBox.warning(self, "Open", "\n".join(warnings[:20] + (["…"] if len(warnings) > 20 else [])))

    def load(self, path: str) -> None:
        result = self._read(path)
        if result is not None:
            self._set_beamline(result[0], path, result[1])

    def glue_dialog(self) -> None:
        if not self.maybe_discard():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Open files to glue", os.path.dirname(self.path),
                                                "Layouts (*.txt *.tfs *.json);;All files (*)")
        if paths:
            self.load_glued(paths, ask=True)

    def compare_dialog(self) -> None:
        """Pick 2 files to compare, or 1 file to compare with the current layout."""
        hint = "Choose 1 file (compared with the current layout) or 2 files" if self.bl else "Choose 2 files"
        paths, _ = QFileDialog.getOpenFileNames(self, hint, os.path.dirname(self.path),
                                                "Layouts (*.txt *.tfs *.json);;All files (*)")
        if not paths:
            return
        if len(paths) > 2 or (len(paths) == 1 and self.bl is None):
            QMessageBox.warning(self, "Compare", hint + ".")
            return
        sides: list[tuple[Beamline, str]] = []
        if len(paths) == 1:
            name = os.path.basename(self.path) if self.path else "current layout"
            sides.append((copy.deepcopy(self.bl), name + ("*" if not self.undo.isClean() else "")))
        for path in paths:
            result = self._read(path)
            if result is None:
                return
            sides.append((result[0], os.path.basename(path)))
        self.open_compare(*sides[0], *sides[1])

    def open_compare(self, a: Beamline, name_a: str, b: Beamline, name_b: str) -> CompareWindow:
        win = CompareWindow(self, a, b, name_a, name_b, self.unit, self.flip_box.isChecked())
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        win.destroyed.connect(lambda: self.compare_windows.remove(win))
        self.compare_windows.append(win)
        win.show()
        return win

    def load_glued(self, paths: list[str], ask: bool = False) -> None:
        """Read several files and glue them into one beamline (ordered by start position unless asked)."""
        parts = []
        warnings: list[str] = []
        for path in paths:
            result = self._read(path)
            if result is None:
                return
            parts.append((path, result[0]))
            warnings += [f"{os.path.basename(path)}: {w}" for w in result[1]]
        if len(parts) == 1:
            self._set_beamline(parts[0][1], paths[0], warnings)
            return
        end_to_end = False
        ordered = [bl for _, bl in sorted(parts, key=lambda t: t[1].limits()[0])]
        if ask:
            dlg = GlueDialog(self, parts)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            ordered, end_to_end = dlg.ordered(), dlg.chain.isChecked()
        bl, glue_warnings = glue(ordered, end_to_end)
        path = os.path.join(os.path.dirname(paths[0]), "glued_layout")
        self._set_beamline(bl, path, warnings + glue_warnings)
        self.statusBar().showMessage(f"Glued {len(parts)} files, {len(bl.elements)} elements", 6000)

    def _save(self, title: str, ext: str, writer) -> None:
        if self.bl is None:
            return
        base = os.path.splitext(self.path)[0] if self.path else "layout"
        path, _ = QFileDialog.getSaveFileName(self, title, base + ext, f"*{ext};;All files (*)")
        if not path:
            return
        try:
            writer(self.bl, path)
        except OSError as err:
            QMessageBox.critical(self, title, str(err))
            return
        self.path = path
        self.undo.setClean()
        self.refresh()
        self.statusBar().showMessage(f"Saved {os.path.basename(path)}", 4000)

    def save_survey(self) -> None:
        self._save("Save survey", ".txt", write_survey)

    def save_json(self) -> None:
        self._save("Save JSON", ".json", write_json)

    def closeEvent(self, event) -> None:
        if self.maybe_discard():
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------ element actions
    def _drag_finished(self, root: int, delta: float) -> None:
        x = self.bl.elements[root]
        target = x.s_center + delta
        self.apply("Drag", lambda bl: bl.set_position(root, target, "center"))

    def insert_element(self) -> None:
        if self.bl is None:
            return
        e = self.bl.elements
        prefix = ""
        if self.selection:  # middle of the next drift downstream of the selection
            x = e[self.selection[0]]
            prefix = x.name[:x.name.find(".") + 1]
            after = [d for d in e if d.is_gap_drift and d.length > TOL and d.s_start >= x.s_end - TOL]
            s = min(after, key=lambda d: d.s_start).s_center if after else x.s_center
        else:
            s = sum(self.bl.limits()) / 2
        dlg = InsertDialog(self, s, self.bl.free_name(f"{prefix}NEW"))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result: list[int] = []
        if self.apply("Insert", lambda bl: result.append(bl.insert(dlg.element()))):
            self.set_selection(result)

    def remove_element(self) -> None:
        if self.canvas.delete_selected_measure():
            return
        if self.bl is None or len(self.selection) != 1:
            return
        i = self.selection[0]
        name = self.bl.elements[i].name
        answer = QMessageBox.question(self, "Delete", f"Remove {name}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.selection = []
        self.apply("Delete", lambda bl: bl.remove(i))
        self.set_selection([])

    # ------------------------------------------------------------ issues
    def show_issues(self) -> None:
        if self.issue_dialog is None:
            self.issue_dialog = QDialog(self)
            self.issue_dialog.setWindowTitle("Issues")
            self.issue_list = QListWidget()
            self.issue_list.itemDoubleClicked.connect(
                lambda item: self.set_selection([item.data(Qt.ItemDataRole.UserRole)]))
            lay = QVBoxLayout(self.issue_dialog)
            lay.addWidget(self.issue_list)
            self.issue_dialog.resize(420, 240)
        self._fill_issues()
        self.issue_dialog.show()
        self.issue_dialog.raise_()

    def _fill_issues(self) -> None:
        self.issue_list.clear()
        for s in self.issues:
            self.issue_list.addItem(f"{s.level}  {self.bl.elements[s.index].name}: {s.message}")
            self.issue_list.item(self.issue_list.count() - 1).setData(Qt.ItemDataRole.UserRole, s.index)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    if len(sys.argv) > 2:
        win.load_glued(sys.argv[1:])
    elif len(sys.argv) > 1:
        win.load(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
