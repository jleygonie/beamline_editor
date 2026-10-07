"""Element table, property panel and distance panel.

Panels talk to the main window `win`, which provides: bl, selection, unit,
apply(label, fn) -> bool and set_selection(indices, source).
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QPushButton, QRadioButton, QStyledItemDelegate,
                             QTableView, QVBoxLayout, QWidget)

import element_types as et

UNIT_SCALE = {"m": 1.0, "mm": 1000.0}
UNIT_DECIMALS = {"m": 6, "mm": 3}


def fmt_num(x: float, unit: str) -> str:
    return f"{x * UNIT_SCALE[unit] + 0.0:.{UNIT_DECIMALS[unit]}f}"


def fmt_len(x: float, unit: str) -> str:
    return f"{fmt_num(x, unit)} {unit}"


def type_label(key: str) -> str:
    return et.CATALOGUE.get(key, {}).get("label", key)


# ---------------------------------------------------------------- element table
COLUMNS = ["#", "Name", "Type", "Keyword", "s_start", "s_center", "s_end", "L", "Angle [rad]"]
POS_REF = {4: "start", 5: "center", 6: "end"}


class ElementTableModel(QAbstractTableModel):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.rows: list[int] = []  # element index of each visible row
        self.overlaps: set[int] = set()

    def refresh(self, text: str, drifts: bool) -> None:
        self.beginResetModel()
        bl = self.win.bl
        text = text.strip().upper()
        self.rows = [] if bl is None else [
            i for i, x in enumerate(bl.elements)
            if (drifts or not x.is_gap_drift) and text in x.name.upper()]
        self.overlaps = set() if bl is None else bl.overlapping()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            name = COLUMNS[section]
            return f"{name} [{self.win.unit}]" if 4 <= section <= 7 else name
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        i, col = self.rows[index.row()], index.column()
        x = self.win.bl.elements[i]
        if role == Qt.ItemDataRole.TextAlignmentRole and (col >= 4 or col == 0):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and i in self.overlaps:
            return QColor(200, 0, 0)
        if role == Qt.ItemDataRole.ToolTipRole and i in self.overlaps:
            return "Overlaps another element"
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        if col == 0:
            return str(i)
        if col == 1:
            return x.name
        if col == 2:
            return x.type if role == Qt.ItemDataRole.EditRole else type_label(x.type)
        if col == 3:
            return x.keyword
        if col == 8:
            return f"{x.angle + 0.0:.9g}"
        value = {4: x.s_start, 5: x.s_center, 6: x.s_end, 7: x.length}[col]
        return fmt_num(value, self.win.unit)

    def flags(self, index):
        f = super().flags(index)
        return f | Qt.ItemFlag.ItemIsEditable if index.column() > 0 else f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        i, col = self.rows[index.row()], index.column()
        x = self.win.bl.elements[i]
        if col == 1:
            return value != x.name and self.win.apply("Rename", lambda bl: bl.rename(i, value))
        if col == 2:
            return value != x.type and self.win.apply("Set type", lambda bl: bl.set_type(i, value))
        if col == 3:
            return value != x.keyword and self.win.apply("Set keyword", lambda bl: bl.set_keyword(i, value))
        try:
            v = float(value) / (1.0 if col == 8 else UNIT_SCALE[self.win.unit])
        except ValueError:
            return False
        if str(value) == self.data(index):
            return False
        if col == 8:
            return self.win.apply("Set angle", lambda bl: bl.set_angle(i, float(value)))
        if col == 7:
            return self.win.apply("Set length", lambda bl: bl.set_length(i, v, "center"))
        return self.win.apply("Set position", lambda bl: bl.set_position(i, v, POS_REF[col], "move"))


class TypeDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        for key, spec in et.CATALOGUE.items():
            combo.addItem(spec["label"], key)
        return combo

    def setEditorData(self, editor, index):
        editor.setCurrentIndex(max(editor.findData(index.data(Qt.ItemDataRole.EditRole)), 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData())


class ElementTable(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.filter = QLineEdit(placeholderText="Filter")
        self.drifts = QCheckBox("Drifts")
        self.model = ElementTableModel(win)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setItemDelegateForColumn(2, TypeDelegate(self.view))
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 6)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.view.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        top = QHBoxLayout()
        top.addWidget(self.filter)
        top.addWidget(self.drifts)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(self.view)
        self.filter.textChanged.connect(self.refresh)
        self.drifts.toggled.connect(self.refresh)
        self.view.selectionModel().selectionChanged.connect(self._on_select)
        self._syncing = False

    def refresh(self) -> None:
        self._syncing = True
        self.model.refresh(self.filter.text(), self.drifts.isChecked())
        self._syncing = False
        self.set_selection(self.win.selection)

    def set_selection(self, sel: list[int]) -> None:
        self._syncing = True
        sm = self.view.selectionModel()
        sm.clearSelection()
        for i in sel:
            if i in self.model.rows:
                r = self.model.rows.index(i)
                sm.select(self.model.index(r, 0), sm.SelectionFlag.Select | sm.SelectionFlag.Rows)
                self.view.scrollTo(self.model.index(r, 0))
        self._syncing = False

    def _on_select(self, *_):
        if self._syncing:
            return
        rows = {self.model.rows[ix.row()] for ix in self.view.selectionModel().selectedRows()}
        sel = [i for i in self.win.selection if i in rows]
        sel += sorted(rows - set(sel))
        self.win.set_selection(sel[-2:], source=self)


# ---------------------------------------------------------------- property panel
def make_spin(decimals: int = 6, suffix: str = "") -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(-1e9, 1e9)
    spin.setDecimals(decimals)
    spin.setSuffix(suffix)
    spin.setKeyboardTracking(False)
    spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    return spin


def set_spin(spin: QDoubleSpinBox, value: float) -> None:
    spin.blockSignals(True)
    spin.setValue(value)
    spin.setProperty("shown", spin.value())
    spin.blockSignals(False)


def spin_changed(spin: QDoubleSpinBox) -> bool:
    return spin.isEnabled() and spin.value() != spin.property("shown")


def set_edit(edit: QLineEdit, text: str) -> None:
    edit.blockSignals(True)
    edit.setText(text)
    edit.setProperty("shown", text)
    edit.blockSignals(False)


class PropertyPanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.index: int | None = None
        self.param_widgets: dict[str, QWidget] = {}
        self.param_type: tuple | None = None
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.type = QComboBox()
        for key, spec in et.CATALOGUE.items():
            self.type.addItem(spec["label"], key)
        self.keyword = QLineEdit()
        self.use_default = QPushButton("Use default")
        kw = QHBoxLayout()
        kw.addWidget(self.keyword)
        kw.addWidget(self.use_default)
        self.pos = {k: make_spin() for k in ("start", "center", "end")}
        self.length = make_spin()
        self.anchor = QComboBox()
        self.anchor.addItems(["start", "center", "end"])
        self.anchor.setCurrentText("center")
        self.mode = QComboBox()
        self.mode.addItems(["Move", "Shift"])
        self.up_gap, self.down_gap = make_spin(), make_spin()
        self.angle = make_spin(9, " rad")
        self.angle.setToolTip("Bend angle: a positive angle turns the beam to the right (MAD-X convention)")
        self.angle_deg = QLabel()
        angle = QHBoxLayout()
        angle.addWidget(self.angle, 1)
        angle.addWidget(self.angle_deg)
        self.params = QFormLayout()
        self.params.setContentsMargins(0, 0, 0, 0)
        self.comment = QLineEdit()
        form.addRow("Name", self.name)
        form.addRow("Type", self.type)
        form.addRow("Keyword", kw)
        form.addRow("s_start", self.pos["start"])
        form.addRow("s_center", self.pos["center"])
        form.addRow("s_end", self.pos["end"])
        form.addRow("Length", self.length)
        form.addRow("Anchor", self.anchor)
        form.addRow("Mode", self.mode)
        form.addRow("Up gap", self.up_gap)
        form.addRow("Down gap", self.down_gap)
        form.addRow("Angle", angle)
        form.addRow(self.params)
        form.addRow("Comment", self.comment)

        self.name.editingFinished.connect(self._rename)
        self.type.activated.connect(self._set_type)
        self.keyword.editingFinished.connect(self._set_keyword)
        self.use_default.clicked.connect(self._default_keyword)
        for ref, spin in self.pos.items():
            spin.editingFinished.connect(lambda ref=ref: self._set_position(ref))
        self.length.editingFinished.connect(self._set_length)
        self.up_gap.editingFinished.connect(lambda: self._set_gap(True))
        self.down_gap.editingFinished.connect(lambda: self._set_gap(False))
        self.angle.editingFinished.connect(self._set_angle)
        self.comment.editingFinished.connect(self._set_comment)

    @property
    def scale(self) -> float:
        return UNIT_SCALE[self.win.unit]

    def refresh(self) -> None:
        bl, sel = self.win.bl, self.win.selection
        self.index = sel[0] if bl is not None and len(sel) == 1 else None
        self.setEnabled(self.index is not None)
        if self.index is None:
            return
        i, x, u = self.index, bl.elements[self.index], self.win.unit
        set_edit(self.name, x.name)
        self.type.setCurrentIndex(max(self.type.findData(x.type), 0))
        set_edit(self.keyword, x.keyword)
        for spin in (*self.pos.values(), self.length, self.up_gap, self.down_gap):
            spin.setDecimals(UNIT_DECIMALS[u])
            spin.setSuffix(f" {u}")
        for ref, v in (("start", x.s_start), ("center", x.s_center), ("end", x.s_end)):
            set_spin(self.pos[ref], v * self.scale)
        set_spin(self.length, x.length * self.scale)
        up, down = bl.neighbours(i)
        self.up_gap.setEnabled(up is not None)
        self.down_gap.setEnabled(down is not None)
        set_spin(self.up_gap, 0.0 if up is None else (x.s_start - bl.elements[up].s_end) * self.scale)
        set_spin(self.down_gap, 0.0 if down is None else (bl.elements[down].s_start - x.s_end) * self.scale)
        set_spin(self.angle, x.angle)
        self.angle_deg.setText(f"{math.degrees(x.angle):.4f}°")
        self._build_params(x)
        set_edit(self.comment, x.comment)

    def _build_params(self, x) -> None:
        key = (self.index, x.type)
        if key != self.param_type:
            self.param_type = key
            while self.params.rowCount():
                self.params.removeRow(0)
            self.param_widgets = {}
            for pkey, label, unit, default in et.CATALOGUE.get(x.type, et.CATALOGUE["other"])["params"]:
                if pkey == "angle":  # edited in the Angle row
                    continue
                if isinstance(default, str):
                    w = QLineEdit()
                    w.editingFinished.connect(lambda k=pkey, w=w: self._set_param(k, w.text(), w))
                else:
                    w = make_spin(6)
                    w.editingFinished.connect(lambda k=pkey, w=w: self._set_param(k, w.value(), w))
                self.param_widgets[pkey] = w
                self.params.addRow(f"{label} [{unit}]" if unit else label, w)
        for pkey, w in self.param_widgets.items():
            value = x.params.get(pkey, "")
            if isinstance(w, QLineEdit):
                set_edit(w, str(value))
            else:
                set_spin(w, float(value or 0.0))

    def _apply(self, label: str, fn) -> None:
        if self.index is not None and not self.win.apply(label, fn):
            self.refresh()

    def _rename(self):
        if self.name.text() != self.name.property("shown"):
            i, name = self.index, self.name.text()
            self._apply("Rename", lambda bl: bl.rename(i, name))

    def _set_type(self):
        i, key = self.index, self.type.currentData()
        if i is not None and key != self.win.bl.elements[i].type:
            self._apply("Set type", lambda bl: bl.set_type(i, key))

    def _set_keyword(self):
        if self.keyword.text() != self.keyword.property("shown"):
            i, kw = self.index, self.keyword.text()
            self._apply("Set keyword", lambda bl: bl.set_keyword(i, kw))

    def _default_keyword(self):
        i = self.index
        x = self.win.bl.elements[i]
        kw = et.default_keyword(x.type, x.keyword)
        if kw != x.keyword:
            self._apply("Set keyword", lambda bl: bl.set_keyword(i, kw))

    def _set_position(self, ref: str):
        spin = self.pos[ref]
        if spin_changed(spin):
            i, v, mode = self.index, spin.value() / self.scale, self.mode.currentText().lower()
            self._apply("Set position", lambda bl: bl.set_position(i, v, ref, mode))

    def _set_length(self):
        if spin_changed(self.length):
            i, v, anchor = self.index, self.length.value() / self.scale, self.anchor.currentText()
            self._apply("Set length", lambda bl: bl.set_length(i, v, anchor))

    def _set_gap(self, upstream: bool):
        spin = self.up_gap if upstream else self.down_gap
        if not spin_changed(spin):
            return
        i = self.index
        delta = (spin.value() - spin.property("shown")) / self.scale
        shift = self.mode.currentText() == "Shift"
        if upstream:
            fn = (lambda bl: bl.shift(i, delta)) if shift else (lambda bl: bl.move(i, delta))
        else:
            down = self.win.bl.neighbours(i)[1]
            # Shift pushes the downstream neighbour and everything after it.
            fn = (lambda bl: bl.shift(down, delta)) if shift else (lambda bl: bl.move(i, -delta))
        self._apply("Set gap", fn)

    def _set_angle(self):
        if spin_changed(self.angle):
            i, v = self.index, self.angle.value()
            self._apply("Set angle", lambda bl: bl.set_angle(i, v))

    def _set_param(self, key: str, value, w):
        if (isinstance(w, QLineEdit) and value == w.property("shown")) or \
                (isinstance(w, QDoubleSpinBox) and not spin_changed(w)):
            return
        i = self.index
        self._apply("Set parameter", lambda bl: bl.set_param(i, key, value))

    def _set_comment(self):
        if self.comment.text() != self.comment.property("shown"):
            i, text = self.index, self.comment.text()
            self._apply("Set comment", lambda bl: bl.set_comment(i, text))


# ---------------------------------------------------------------- distance panel
DISTANCES = [("center_center", "Center–center"), ("edge_gap", "Edge gap"),
             ("start_start", "Start–start"), ("end_end", "End–end")]


class DistancePanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        form = QFormLayout(self)
        self.a, self.b = QLabel(), QLabel()
        form.addRow("A", self.a)
        form.addRow("B", self.b)
        self.spins = {}
        for key, label in DISTANCES:
            spin = make_spin()
            spin.editingFinished.connect(lambda key=key: self._set(key))
            self.spins[key] = spin
            form.addRow(label, spin)
        self.move_a, self.move_b = QRadioButton("A"), QRadioButton("B")
        self.move_b.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.move_a)
        group.addButton(self.move_b)
        moving = QHBoxLayout()
        moving.addWidget(self.move_a)
        moving.addWidget(self.move_b)
        moving.addStretch()
        form.addRow("Moving", moving)
        self.mode = QComboBox()
        self.mode.addItems(["Move", "Shift"])
        form.addRow("Mode", self.mode)
        self.swap = QPushButton("Swap reference")
        form.addRow(self.swap)
        self.swap.clicked.connect(lambda: (self.move_b if self.move_a.isChecked() else self.move_a).setChecked(True))
        self.move_a.toggled.connect(self._update_mode)

    def _update_mode(self):
        shift_item = self.mode.model().item(1)
        shift_item.setEnabled(self.move_b.isChecked())
        if self.move_a.isChecked():
            self.mode.setCurrentIndex(0)

    def refresh(self) -> None:
        bl, sel = self.win.bl, self.win.selection
        if bl is None or len(sel) != 2:
            return
        d = bl.distances(*sel)
        self.a.setText(bl.elements[d["a"]].name)
        self.b.setText(bl.elements[d["b"]].name)
        u = self.win.unit
        for key, spin in self.spins.items():
            spin.setDecimals(UNIT_DECIMALS[u])
            spin.setSuffix(f" {u}")
            set_spin(spin, d[key] * UNIT_SCALE[u])

    def _set(self, key: str):
        spin = self.spins[key]
        if not spin_changed(spin) or len(self.win.selection) != 2:
            return
        a, b = self.win.selection
        v = spin.value() / UNIT_SCALE[self.win.unit]
        moving = "B" if self.move_b.isChecked() else "A"
        mode = self.mode.currentText().lower()
        if not self.win.apply("Set distance", lambda bl: bl.set_distance(a, b, key, v, moving, mode)):
            self.refresh()
