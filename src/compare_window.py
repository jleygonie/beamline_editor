"""Side-by-side comparison of two beamlines: A drawn above B on a shared s axis, plus a table of differences."""
from __future__ import annotations

import csv

from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QTransform
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QFileDialog, QHeaderView, QLabel, QMainWindow,
                             QMessageBox, QTableWidget, QTableWidgetItem, QToolTip, QVBoxLayout, QWidget)

import element_types as et
from canvas import H, LABEL_Y, MEASURE_SEL, Canvas
from compare import Diff, common_names, compare, median_offset, offset_on
from model import Beamline
from panels import fmt_len, type_label

TRACK = 200.0  # scene y of the B axis (A is at 0)
STATUS_COLOR = {"same": QColor(150, 150, 150), "changed": QColor(235, 140, 0),
                "added": QColor(30, 170, 80), "removed": QColor(215, 50, 50)}
STATUS_TEXT = {"same": "Same", "changed": "Changed", "added": "Only in B", "removed": "Only in A"}
COLUMNS = ["Status", "Name", "Type", "s_center A", "s_center B", "Δ s_center", "L A", "L B", "Δ L", "Details"]


def signed(x: float, unit: str) -> str:
    return ("+" if x >= 0 else "") + fmt_len(x, unit)


def describe(d: Diff, a: Beamline, b: Beamline, unit: str) -> str:
    if d.status in ("added", "removed"):
        return STATUS_TEXT[d.status]
    x, y = a.elements[d.a], b.elements[d.b]
    parts = []
    if "position" in d.changes and "length" not in d.changes:
        parts.append(f"moved {signed(d.d_center, unit)}")
    elif "position" in d.changes:
        parts.append(f"start {signed(d.d_start, unit)}, end {signed(d.d_end, unit)}")
    if "length" in d.changes:
        parts.append(f"length {signed(d.d_length, unit)}")
    if "type" in d.changes:
        parts.append(f"type {type_label(x.type)} → {type_label(y.type)}")
    if "keyword" in d.changes:
        parts.append(f"keyword {x.keyword} → {y.keyword}")
    if "angle" in d.changes:
        parts.append(f"angle {x.angle:.6g} → {y.angle:.6g}")
    parts += [c for c in ("params", "comment") if c in d.changes]
    return ", ".join(parts)


class CompareCanvas(Canvas):
    """Two beamlines on one s axis. Read only: click an element to pick its row, drag to pan."""
    picked = pyqtSignal(int)  # diff index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.a: Beamline | None = None
        self.b: Beamline | None = None
        self.diffs: list[Diff] = []
        self.offset = 0.0
        self.titles = ("A", "B")
        self.fade = True
        self.focus: int | None = None
        self.where: dict[str, dict[int, int]] = {"a": {}, "b": {}}  # element index -> diff index

    def set_compare(self, a: Beamline, b: Beamline, diffs: list[Diff], offset: float,
                    titles: tuple[str, str]) -> None:
        self.a, self.b, self.diffs, self.offset, self.titles = a, b, diffs, offset, titles
        self.bl = self.shown = a  # for the base class (ruler, zoom limits)
        if self.focus is not None and self.focus >= len(diffs):
            self.focus = None
        self.rebuild()

    def set_focus(self, k: int | None, centre: bool = False) -> None:
        self.focus = k
        self.rebuild()
        if centre and k is not None:
            d = self.diffs[k]
            x = (self.a.elements[d.a].s_center if d.a is not None
                 else self.b.elements[d.b].s_center + self.offset)
            self.centerOn(x * 1000, self.mapToScene(self.viewport().rect().center()).y())
            self.update_labels()

    def _range(self) -> tuple[float, float]:
        xs = [v for x in self.a.elements for v in (x.s_start, x.s_end)]
        xs += [v + self.offset for x in self.b.elements for v in (x.s_start, x.s_end)]
        return min(xs) * 1000, max(xs) * 1000

    def fit(self) -> None:
        if not self.a or not self.a.elements:
            return
        lo, hi = self._range()
        sx = max(self.viewport().width() - 20, 50) / (max(hi - lo, 1.0) * 1.04)
        self.setTransform(QTransform.fromScale(-sx if self.flipped else sx, 1.0))
        self.centerOn((lo + hi) / 2, TRACK / 2)
        self.rebuild()  # highlight bands are sized in pixels

    def wheelEvent(self, event) -> None:
        super().wheelEvent(event)
        self.rebuild()

    def rebuild(self) -> None:
        scene = self.scene()
        scene.clear()
        self.labels = []
        if not self.a or not self.a.elements:
            return
        lo, hi = self._range()
        self.where = {"a": {d.a: k for k, d in enumerate(self.diffs) if d.a is not None},
                      "b": {d.b: k for k, d in enumerate(self.diffs) if d.b is not None}}
        self._draw_track(self.a, 0.0, 0.0, self.where["a"], LABEL_Y, lo, hi)
        self._draw_track(self.b, TRACK, self.offset, self.where["b"], TRACK + H / 2 + 6, lo, hi)
        for k, d in enumerate(self.diffs):  # connectors between the two versions of an element
            if d.a is None or d.b is None or self.a.elements[d.a].is_gap_drift:
                continue
            x, y = self.a.elements[d.a], self.b.elements[d.b]
            changed = "position" in d.changes or "length" in d.changes
            color = MEASURE_SEL if k == self.focus else STATUS_COLOR["changed" if changed else "same"]
            line = scene.addLine(QLineF(x.s_center * 1000, self._half(x),
                                        (y.s_center + self.offset) * 1000, TRACK - self._half(y)),
                                 self._pen(2 if k == self.focus else 1.5 if changed else 1, color,
                                           Qt.PenStyle.SolidLine if changed else Qt.PenStyle.DotLine))
            line.setZValue(1)
            if self.fade and not changed and k != self.focus:
                line.setOpacity(0.35)
        margin = max((hi - lo) * 0.05, 1.0)
        top, bottom = LABEL_Y - 40, TRACK + H / 2 + 60
        scene.setSceneRect(QRectF(lo - margin, top, hi - lo + 2 * margin, bottom - top))
        self.update_labels()

    @staticmethod
    def _half(x) -> float:
        return et.CATALOGUE.get(x.type, et.CATALOGUE["other"])["height"] * H / 2

    def _draw_track(self, bl: Beamline, y0: float, offset: float, where: dict[int, int],
                    label_y: float, lo: float, hi: float) -> None:
        self.scene().addLine(QLineF(lo, y0, hi, y0), self._pen(1))
        for i, x in enumerate(bl.elements):
            if x.is_gap_drift:
                continue
            k = where.get(i)
            status = self.diffs[k].status if k is not None else "same"
            color = MEASURE_SEL if k is not None and k == self.focus else (
                STATUS_COLOR[status] if status != "same" else None)
            item = self._element_item(x, 3 if color is not None else 1, y0, offset, color)
            if color is not None:  # a band behind the element makes small ones visible when zoomed out
                h = self._half(x)
                pad = 5 / self._sx()
                band = self.scene().addRect(QRectF((x.s_start + offset) * 1000 - pad, y0 - h - 5,
                                                   x.length * 1000 + 2 * pad, 2 * h + 10))
                band.setPen(self._pen(0, QColor(0, 0, 0, 0)))
                fill = QColor(color)
                fill.setAlpha(60)
                band.setBrush(QBrush(fill))
                band.setZValue(0)
            elif self.fade:
                item.setOpacity(0.35)
            label = self._text(x.name, (x.s_center + offset) * 1000, label_y, True)
            label.setZValue(4)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)  # ruler
        if not self.a:
            return
        painter.save()
        painter.resetTransform()
        f = QFont(self.font())
        f.setBold(True)
        painter.setFont(f)
        for y0, title in ((LABEL_Y - 8, self.titles[0]), (TRACK - H / 2 - 6, self.titles[1])):
            y = self.mapFromScene(QPointF(0, y0)).y()
            r = painter.fontMetrics().boundingRect(title).adjusted(-4, -2, 4, 2)
            r.moveTo(6, round(y - r.height()))
            painter.fillRect(r, self.palette().base())
            painter.setPen(self.palette().text().color())
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, title)
        painter.restore()

    # ------------------------------------------------------------ interaction (read only)
    def hit(self, pos) -> int | None:
        return None  # no element selection or dragging in the base class

    def find(self, pos) -> int | None:
        """Diff index of the element under pos, on either track."""
        if not self.a:
            return None
        p = self.mapToScene(pos)
        tol = 4 / self._sx()
        best, best_key = None, None
        for bl, y0, offset, side in ((self.a, 0.0, 0.0, "a"), (self.b, TRACK, self.offset, "b")):
            for i, x in enumerate(bl.elements):
                if x.is_gap_drift or abs(p.y() - y0) > self._half(x) + 4:
                    continue
                x0, x1 = (x.s_start + offset) * 1000, (x.s_end + offset) * 1000
                if x0 - tol <= p.x() <= x1 + tol:
                    key = (x1 - x0, abs(p.x() - (x0 + x1) / 2))
                    k = self.where[side].get(i)
                    if k is not None and (best_key is None or key < best_key):
                        best, best_key = k, key
        return best

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            k = self.find(event.position().toPoint())
            if k is not None:
                self.set_focus(k)
                self.picked.emit(k)
                return
        super().mousePressEvent(event)  # pan

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None:
            super().mouseMoveEvent(event)
            return
        k = self.find(event.position().toPoint())
        if k is None:
            QToolTip.hideText()
            return
        d = self.diffs[k]
        text = f"{d.name}\n{STATUS_TEXT[d.status]}"
        if d.status == "changed":
            text += "\n" + describe(d, self.a, self.b, self.unit)
        QToolTip.showText(event.globalPosition().toPoint(), text, self)


class CompareWindow(QMainWindow):
    def __init__(self, parent, a: Beamline, b: Beamline, name_a: str, name_b: str, unit: str = "m",
                 flipped: bool = False):
        super().__init__(parent)
        self.a, self.b, self.names = a, b, [name_a, name_b]
        self.unit = unit
        self.diffs: list[Diff] = []
        self.offset = 0.0
        self.setWindowTitle(f"Compare {name_a} ↔ {name_b}")
        self.canvas = CompareCanvas()
        self.canvas.unit = unit
        self.canvas.flipped = flipped
        self.setCentralWidget(self.canvas)

        self.summary = QLabel()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 6)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._row_selected)
        self.canvas.picked.connect(self._select_row)
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.summary)
        lay.addWidget(self.table)
        dock = QDockWidget("Differences", self)
        dock.setObjectName("Differences")
        dock.setWidget(panel)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)

        tb = self.addToolBar("Compare")
        tb.setMovable(False)
        tb.addAction("Fit", self.canvas.fit)
        tb.addAction("Swap A/B", self.swap)
        tb.addAction("Export CSV…", self.export_csv)
        tb.addSeparator()
        tb.addWidget(QLabel(" Align B on "))
        self.align = QComboBox()
        self.align.setToolTip("Shift B along s before comparing")
        self.align.currentIndexChanged.connect(self.recompute)
        tb.addWidget(self.align)
        self.drifts = QCheckBox("Drifts")
        self.drifts.setToolTip("Also compare the drifts (they change whenever an element moves)")
        self.fade = QCheckBox("Fade unchanged")
        self.fade.setChecked(True)
        self.show_same = QCheckBox("List unchanged")
        self.flip = QCheckBox("Right to left")
        self.flip.setChecked(flipped)
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["m", "mm"])
        self.unit_combo.setCurrentText(unit)
        for w in (self.drifts, self.fade, self.show_same, self.flip):
            tb.addWidget(w)
        tb.addWidget(QLabel(" Unit "))
        tb.addWidget(self.unit_combo)
        self.drifts.toggled.connect(self.recompute)
        self.fade.toggled.connect(self._set_fade)
        self.show_same.toggled.connect(self._fill_table)
        self.flip.toggled.connect(self.canvas.set_flipped)
        self.unit_combo.currentTextChanged.connect(self._set_unit)

        self._fill_align()
        self.resize(1300, 850)
        self.recompute()
        self.canvas.fit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.canvas.fit()

    # ------------------------------------------------------------ state
    def _fill_align(self) -> None:
        self.align.blockSignals(True)
        self.align.clear()
        self.align.addItem("file positions", None)
        self.align.addItem("best fit (median offset)", "")
        for name in common_names(self.a, self.b):
            self.align.addItem(name, name)
        self.align.blockSignals(False)

    def recompute(self, *_) -> None:
        ref = self.align.currentData()
        self.offset = (0.0 if ref is None else median_offset(self.a, self.b) if ref == ""
                       else offset_on(self.a, self.b, ref))
        self.diffs = compare(self.a, self.b, self.offset, self.drifts.isChecked())
        title_b = f"B: {self.names[1]}" + (f"  (shifted {signed(self.offset, self.unit)})" if self.offset else "")
        self.canvas.focus = None
        self.canvas.set_compare(self.a, self.b, self.diffs, self.offset, (f"A: {self.names[0]}", title_b))
        counts = {s: sum(d.status == s for d in self.diffs) for s in STATUS_TEXT}
        self.summary.setText(f"{counts['changed']} changed, {counts['removed']} only in A, "
                             f"{counts['added']} only in B, {counts['same']} identical")
        self._fill_table()

    def _fill_table(self, *_) -> None:
        u, a, b = self.unit, self.a, self.b
        rows = [k for k, d in enumerate(self.diffs) if d.status != "same" or self.show_same.isChecked()]
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for r, k in enumerate(rows):
            d = self.diffs[k]
            x = a.elements[d.a] if d.a is not None else None
            y = b.elements[d.b] if d.b is not None else None
            both = x is not None and y is not None
            values = [STATUS_TEXT[d.status], d.name, type_label((x or y).type),
                      fmt_len(x.s_center, u) if x else "",
                      fmt_len(y.s_center + self.offset, u) if y else "",
                      signed(d.d_center, u) if both else "",
                      fmt_len(x.length, u) if x else "", fmt_len(y.length, u) if y else "",
                      signed(d.d_length, u) if both else "", describe(d, a, b, u)]
            fill = QColor(STATUS_COLOR[d.status])
            fill.setAlpha(45 if d.status != "same" else 0)
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.ItemDataRole.UserRole, k)
                item.setBackground(QBrush(fill))
                if 3 <= c <= 8:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        self._select_row(self.canvas.focus)

    def _row_selected(self) -> None:
        items = self.table.selectedItems()
        if items:
            self.canvas.set_focus(items[0].data(Qt.ItemDataRole.UserRole), centre=True)

    def _select_row(self, k: int | None) -> None:
        self.table.blockSignals(True)
        self.table.clearSelection()
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == k:
                self.table.selectRow(r)
                self.table.scrollToItem(self.table.item(r, 0))
        self.table.blockSignals(False)

    def _set_fade(self, on: bool) -> None:
        self.canvas.fade = on
        self.canvas.rebuild()

    def _set_unit(self, unit: str) -> None:
        self.unit = unit
        self.canvas.set_unit(unit)
        self.recompute()

    def swap(self) -> None:
        ref = self.align.currentData()
        self.a, self.b = self.b, self.a
        self.names.reverse()
        self.setWindowTitle(f"Compare {self.names[0]} ↔ {self.names[1]}")
        self._fill_align()
        i = self.align.findData(ref)
        self.align.setCurrentIndex(max(i, 0))
        self.recompute()

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export comparison", "comparison.csv", "*.csv;;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["# A", self.names[0]])
                w.writerow(["# B", self.names[1]])
                w.writerow(["# B offset [m]", f"{self.offset:.9f}"])
                w.writerow(["status", "name", "type", "s_center_A [m]", "s_center_B [m]", "d_s_center [m]",
                            "L_A [m]", "L_B [m]", "d_L [m]", "details"])
                for d in self.diffs:
                    x = self.a.elements[d.a] if d.a is not None else None
                    y = self.b.elements[d.b] if d.b is not None else None
                    both = x is not None and y is not None
                    num = lambda v: f"{v:.9f}"  # noqa: E731
                    w.writerow([d.status, d.name, (x or y).type,
                                num(x.s_center) if x else "", num(y.s_center + self.offset) if y else "",
                                num(d.d_center) if both else "", num(x.length) if x else "",
                                num(y.length) if y else "", num(d.d_length) if both else "",
                                describe(d, self.a, self.b, "m")])
        except OSError as err:
            QMessageBox.critical(self, "Export", str(err))
            return
        self.statusBar().showMessage(f"Exported {path}", 4000)
