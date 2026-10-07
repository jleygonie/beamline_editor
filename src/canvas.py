"""Beamline drawing: QGraphicsView with s to scale (1 scene unit = 1 mm), schematic y.

In the straight view s runs along x and y offsets are scene units (the vertical scale stays 1).
In the floor-plan view the line follows the bend angles at an isotropic scale, and y offsets are pixels
measured to the right of the beam, so element sizes stay readable at any zoom.
"""
from __future__ import annotations

import copy
import math

from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PyQt6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsPolygonItem,
                             QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QToolTip)

import element_types as et
from model import TOL, Beamline, EditError
from panels import fmt_len

H = 60.0  # scene height of an element with relative height 1
LABEL_Y = -H / 2 - 16
DIM_Y = (H / 2 + 14, H / 2 + 34)  # centre-centre line, edge gap line
RULER_H = 26  # px
SNAP = 1e-4  # m
OUTLINE = QColor(40, 40, 40)
MEASURE_Y0 = LABEL_Y - 22  # y of the lowest measurement bar
MEASURE_STEP = 34  # vertical spacing between measurement bars
POINT_COLOR = QColor(230, 120, 0)
PICK_COLOR = QColor(0, 160, 70)
MEASURE_SEL = QColor(0, 120, 215)
KIND_LABEL = {"start": "start", "center": "centre", "end": "end"}
APERTURE_FILL = QColor(70, 130, 220, 45)
APERTURE_EDGE = QColor(70, 130, 220, 150)
OVERLAP_FILL = QColor(230, 40, 40)
OVERLAP_EDGE = QColor(170, 0, 0)
FLOOR_PAD = 80  # px kept free around the line when fitting the floor plan

PointRef = tuple[str, str]  # (element name, "start" | "center" | "end")


def nice_step(raw: float) -> float:
    """Smallest 1-2-5 step >= raw."""
    base = 10 ** math.floor(math.log10(raw))
    return next(m * base for m in (1, 2, 5, 10) if m * base >= raw)


class MeasureBox(QGraphicsItem):
    """Framed distance text, horizontally centred on its position and sitting just above it."""

    def __init__(self, text: str, font: QFont, fg: QColor, bg: QColor, border: QColor, width: float):
        super().__init__()
        self.text, self.font, self.fg, self.bg, self.border, self.width = text, font, fg, bg, border, width
        fm = QFontMetricsF(font)
        w, h = fm.horizontalAdvance(text) + 10, fm.height() + 4
        self.rect = QRectF(-w / 2, -h, w, h)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)

    def boundingRect(self) -> QRectF:
        return self.rect.adjusted(-2, -2, 2, 2)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(QPen(self.border, self.width))
        painter.setBrush(self.bg)
        painter.drawRoundedRect(self.rect, 3, 3)
        painter.setFont(self.font)
        painter.setPen(self.fg)
        painter.drawText(self.rect, Qt.AlignmentFlag.AlignCenter, self.text)


class Canvas(QGraphicsView):
    selectionChanged = pyqtSignal(list)  # element indices
    dragFinished = pyqtSignal(int, float)  # block root index, delta (m)
    message = pyqtSignal(str)
    measureModeExited = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.bl: Beamline | None = None
        self.shown: Beamline | None = None  # bl, or a trial copy while dragging
        self.selection: list[int] = []
        self.unit = "m"
        self.show_labels = True
        self.labels: list[tuple[QGraphicsSimpleTextItem, QPointF, bool, bool]] = []  # item, anchor, hideable,
        # vertically centred
        self._press = None  # (button, view pos, hit, scroll x)
        self._mode = ""  # "", "pan", "drag"
        self._drag: tuple[int, float] | None = None  # root, delta
        self.flipped = False  # s increasing to the left
        self.measure_mode = False
        self.measures: list[tuple[PointRef, PointRef]] = []
        self.selected_measure: int | None = None
        self._pick: PointRef | None = None  # first point of a measurement being created
        self._points: list[tuple[float, PointRef]] = []  # pickable points (s in m), measure mode only
        self._index: dict[str, int] = {}
        self._hover: QGraphicsEllipseItem | None = None
        self._content_top = LABEL_Y
        self.floor = False  # floor-plan view following the bend angles
        self.show_apertures = True
        self.geo = None  # Geometry of the shown beamline
        self._press_geo = None  # geometry used to project the mouse during a drag

    # ------------------------------------------------------------ public API
    def set_beamline(self, bl: Beamline | None, fit: bool = False) -> None:
        self.bl = self.shown = bl
        self.rebuild()
        if fit:
            self.fit()

    def set_selection(self, sel: list[int]) -> None:
        self.selection = list(sel)
        self.rebuild()

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        self.rebuild()

    def set_labels(self, on: bool) -> None:
        self.show_labels = on
        self.update_labels()

    def set_flipped(self, on: bool) -> None:
        """Draw s increasing to the left instead of to the right."""
        if on == self.flipped:
            return
        self.flipped = on
        centre = self.mapToScene(self.viewport().rect().center())
        t = self.transform()
        self.setTransform(QTransform.fromScale(-t.m11(), t.m22()))
        self.centerOn(centre)
        self.update_labels()
        self.viewport().update()

    def set_floor(self, on: bool) -> None:
        """Draw the floor plan (following the bend angles) instead of a straight s axis."""
        if on != self.floor:
            self.floor = on
            self.rebuild()
            self.fit()

    def set_apertures(self, on: bool) -> None:
        self.show_apertures = on
        self.rebuild()

    def set_measure_mode(self, on: bool) -> None:
        self.measure_mode = on
        self._pick = None
        self.rebuild()
        if on:
            self.message.emit("Measure: click two highlighted points (Esc to cancel)")

    def clear_measures(self) -> None:
        self.measures, self.selected_measure, self._pick = [], None, None
        self.rebuild()

    def delete_selected_measure(self) -> bool:
        if self.selected_measure is None or self.selected_measure >= len(self.measures):
            return False
        del self.measures[self.selected_measure]
        self.selected_measure = None
        self.rebuild()
        return True

    def fit(self) -> None:
        if not self.shown or not self.shown.elements:
            return
        if self.floor:
            sc = self._fit_scale()
            self.setTransform(QTransform.fromScale(-sc if self.flipped else sc, sc))
            self.rebuild()  # element sizes are in pixels
            x0, y0, x1, y1 = self._floor_bounds()
            self.centerOn((x0 + x1) / 2, (y0 + y1) / 2 + RULER_H / 2 / sc)
            self.update_labels()
            return
        lo, hi = self._range()
        span = max(hi - lo, 1.0)
        sx = max(self.viewport().width() - 20, 50) / (span * 1.04)
        self.setTransform(QTransform.fromScale(-sx if self.flipped else sx, 1.0))
        self.centerOn((lo + hi) / 2, (min(self._content_top, LABEL_Y) + DIM_Y[1] + 20) / 2)
        self.update_labels()

    # ------------------------------------------------------------ drawing
    def _sx(self) -> float:
        """Horizontal scale in px per scene unit (positive even when flipped)."""
        return abs(self.transform().m11()) or 1.0

    def _range(self) -> tuple[float, float]:
        e = self.shown.elements
        return min(x.s_start for x in e) * 1000, max(x.s_end for x in e) * 1000

    def _floor_bounds(self) -> tuple[float, float, float, float]:
        """Scene rectangle (x0, y0, x1, y1) of the floor-plan orbit."""
        x0, y0, x1, y1 = self.geo.bounds()
        return x0 * 1000, -y1 * 1000, x1 * 1000, -y0 * 1000

    def _fit_scale(self) -> float:
        """Scale (px per scene unit) that shows the whole line."""
        if not self.floor:
            lo, hi = self._range()
            return max(self.viewport().width() - 20, 50) / (max(hi - lo, 1.0) * 1.04)
        x0, y0, x1, y1 = self._floor_bounds()
        w = max(self.viewport().width() - 2 * FLOOR_PAD, 50)
        h = max(self.viewport().height() - RULER_H - 2 * FLOOR_PAD, 50)
        return min(w / max(x1 - x0, 1.0), h / max(y1 - y0, 1e-9))

    def _k(self) -> float:
        """Scene units per y unit: 1 in the straight view, 1 px in the floor plan."""
        return 1.0 / self._sx() if self.floor else 1.0

    def _pt(self, s: float, y: float = 0.0) -> QPointF:
        """Scene point at position s (m), offset by y to the right of the beam (below it when straight)."""
        if not self.floor:
            return QPointF(s * 1000, y)
        gx, gy, phi = self.geo.at(s)
        k = self._k()
        return QPointF(gx * 1000 + math.sin(phi) * y * k, -gy * 1000 + math.cos(phi) * y * k)

    def _samples(self, s0: float, s1: float) -> list[float]:
        return self.geo.samples(s0, s1) if self.floor else sorted((s0, s1))

    def _along(self, s0: float, s1: float, y: float) -> QPainterPath:
        """Path following the beam from s0 to s1 at offset y."""
        ss = self._samples(s0, s1)
        path = QPainterPath(self._pt(ss[0], y))
        for s in ss[1:]:
            path.lineTo(self._pt(s, y))
        return path

    def _band(self, s0: float, s1: float, top, bottom) -> QPolygonF:
        """Area between offsets top(s) and bottom(s) from s0 to s1."""
        ss = self._samples(s0, s1)
        if not self.floor:
            return QPolygonF([self._pt(s, top(s)) for s in ss] + [self._pt(s, bottom(s)) for s in reversed(ss)])

        def clamp(s: float, y: float) -> float:
            """Keep the inner side of a bend short of its centre, so the shape does not fold over."""
            c = self.geo.curvature(s)
            if not c:
                return y
            lim = 0.8 / abs(c) * 1000 / self._k()
            return min(y, lim) if c < 0 else max(y, -lim)  # c < 0: bending right, centre on the +y side
        return QPolygonF([self._pt(s, clamp(s, top(s))) for s in ss]
                         + [self._pt(s, clamp(s, bottom(s))) for s in reversed(ss)])

    def _sy(self, pos, geo=None) -> tuple[float, float]:
        """(s in m, y offset) of a viewport position."""
        p = self.mapToScene(pos)
        if not self.floor:
            return p.x() / 1000, p.y()
        s, d = (geo or self.geo).project(p.x() / 1000, -p.y() / 1000)
        return s, d * 1000 / self._k()

    def _text(self, s: str, x: float, y: float, hideable: bool, vcenter: bool = False) -> QGraphicsSimpleTextItem:
        """Text horizontally centred on scene point (x, y): its top is at y, or its centre if vcenter."""
        item = QGraphicsSimpleTextItem(s)
        f = QFont(self.font())
        f.setPointSizeF(max(f.pointSizeF() - 1.5, 6))
        item.setFont(f)
        item.setBrush(self.palette().text())
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        item.setPos(x, y)
        self.scene().addItem(item)
        self.labels.append((item, QPointF(x, y), hideable, vcenter))
        return item

    def _text_at(self, text: str, s: float, y: float, hideable: bool) -> QGraphicsSimpleTextItem:
        """Text centred on position s with its top at offset y (centred at the same place in the floor plan)."""
        if not self.floor:
            return self._text(text, s * 1000, y, hideable)
        h = QFontMetricsF(self.font()).height()
        p = self._pt(s, y + h / 2)
        return self._text(text, p.x(), p.y(), hideable, True)

    def _pen(self, width: float, color: QColor | None = None, style=Qt.PenStyle.SolidLine) -> QPen:
        pen = QPen(color or self.palette().text().color(), width, style)
        pen.setCosmetic(True)
        return pen

    def rebuild(self) -> None:
        scene = self.scene()
        scene.clear()
        self.labels = []
        self._hover = None
        self._points = []
        bl = self.shown
        if not bl or not bl.elements:
            return
        self._index = {x.name: i for i, x in enumerate(bl.elements)}
        self.geo = bl.geometry()
        lo, hi = self._range()
        scene.addPath(self._along(lo / 1000, hi / 1000, 0), self._pen(1))
        r_max = self._draw_apertures() if self.show_apertures else 0.0
        overlaps = bl.overlapping()
        for i, x in enumerate(bl.elements):
            if x.is_gap_drift:
                continue
            item = self._element_item(x, 3 if i in self.selection else 1,
                                      color=OVERLAP_EDGE if i in overlaps else None,
                                      fill=OVERLAP_FILL if i in overlaps else None)
            if i in overlaps:
                item.setToolTip(f"{x.name} overlaps another element")
            label = self._text_at(x.name, x.s_center, LABEL_Y, True)
            label.setZValue(4)
        if len(self.selection) == 2 and all(i < len(bl.elements) for i in self.selection):
            self._draw_dimensions(*self.selection)
        self._draw_measures()
        if self.measure_mode:
            self._draw_points()
        if self.floor:
            x0, y0, x1, y1 = self._floor_bounds()
            margin = max(x1 - x0, y1 - y0) * 0.05 + 1000 + 400 / self._sx()
            scene.setSceneRect(QRectF(x0 - margin, y0 - margin, x1 - x0 + 2 * margin, y1 - y0 + 2 * margin))
        else:
            margin = max((hi - lo) * 0.05, 1.0)
            top = min(self._content_top, LABEL_Y, -r_max) - 30
            bottom = max(DIM_Y[1] + 50, r_max + 30)
            scene.setSceneRect(QRectF(lo - margin, top, hi - lo + 2 * margin, bottom - top))
        self.update_labels()

    # ------------------------------------------------------------ apertures
    def _draw_apertures(self) -> float:
        """Draw the aperture sections behind everything. Returns the largest radius drawn (y units)."""
        bl, r_max = self.shown, 0.0
        for a in bl.apertures:
            s0, s1 = bl.aperture_span(a)
            if s1 - s0 <= TOL:
                continue
            radius = lambda s, a=a: bl.aperture_radius(a, s) * 1000  # 1 y unit per mm  # noqa: E731
            band = self.scene().addPolygon(self._band(s0, s1, lambda s: -radius(s), radius),
                                           self._pen(1, APERTURE_EDGE), QBrush(APERTURE_FILL))
            band.setZValue(-1)  # behind the beam line and the elements
            r_max = max(r_max, radius(s0), radius(s1))
        return r_max

    def _hit_aperture(self, pos):
        """Aperture section under pos, or None."""
        if not self.show_apertures or not self.shown:
            return None
        s, y = self._sy(pos)
        for a in reversed(self.shown.apertures):
            s0, s1 = self.shown.aperture_span(a)
            if s0 <= s <= s1 and abs(y) <= self.shown.aperture_radius(a, s) * 1000:
                return a
        return None

    def _aperture_tooltip(self, a) -> str:
        bl, u = self.shown, self.unit
        s0, s1 = bl.aperture_span(a)
        r = f"{a.radius * 1000:.6g} mm" + (f" → {a.radius_end * 1000:.6g} mm" if a.radius_end is not None else "")
        text = f"Aperture {a.name}\ns {fmt_len(s0, u)} → {fmt_len(s1, u)}\nradius {r}"
        return text + (f"\n{a.comment}" if a.comment else "")

    # ------------------------------------------------------------ measurements
    def _point_s(self, ref: PointRef) -> float | None:
        i = self._index.get(ref[0])
        if i is None:
            return None
        x = self.shown.elements[i]
        return {"start": x.s_start, "center": x.s_center, "end": x.s_end}[ref[1]]

    def _point_text(self, ref: PointRef) -> str:
        return f"{ref[0]} ({KIND_LABEL[ref[1]]})"

    def _dot(self, s: float, radius: float, color: QColor, z: float) -> QGraphicsEllipseItem:
        dot = QGraphicsEllipseItem(-radius, -radius, 2 * radius, 2 * radius)
        dot.setBrush(QBrush(color))
        dot.setPen(QPen(self.palette().base().color(), 1))
        dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        dot.setPos(self._pt(s))
        dot.setZValue(z)
        self.scene().addItem(dot)
        return dot

    def _draw_points(self) -> None:
        """Highlight every point a measurement can use: edges and centres of solid elements, markers."""
        found: dict[int, tuple[float, PointRef]] = {}
        for x in sorted(self.shown.elements, key=lambda x: not x.is_thick):  # prefer thick owners
            if x.is_gap_drift:
                continue
            for kind in ("start", "center", "end") if x.is_thick else ("end",):
                s = {"start": x.s_start, "center": x.s_center, "end": x.s_end}[kind]
                found.setdefault(round(s * 1e9), (s, (x.name, kind)))
        self._points = sorted(found.values())
        for s, _ref in self._points:
            self._dot(s, 3.5, POINT_COLOR, 6)
        if self._pick is not None and (s := self._point_s(self._pick)) is not None:
            self._dot(s, 5.5, PICK_COLOR, 7)
        self._hover = self._dot(0, 6.5, QColor(0, 0, 0, 0), 8)
        self._hover.setPen(QPen(PICK_COLOR, 2))
        self._hover.setVisible(False)

    def _draw_measures(self) -> None:
        scene, sx = self.scene(), self._sx()
        f = QFont(self.font())
        f.setPointSizeF(max(f.pointSizeF() - 1, 6))
        fm = QFontMetricsF(f)
        items = []
        for k, (a, b) in enumerate(self.measures):
            sa, sb = self._point_s(a), self._point_s(b)
            if sa is not None and sb is not None:  # skipped while an element is missing (e.g. deleted)
                items.append((min(sa, sb) * 1000, max(sa, sb) * 1000, k, abs(sb - sa)))
        level_end: list[float] = []  # right end (scene x) of what is drawn on each level
        for x0, x1, k, d in sorted(items):
            text = fmt_len(d, self.unit)
            half = (fm.horizontalAdvance(text) + 10) / 2 / sx
            mid = (x0 + x1) / 2
            left, right = min(x0, mid - half), max(x1, mid + half)
            level = next((n for n, end in enumerate(level_end) if end + 8 / sx < left), len(level_end))
            if level == len(level_end):
                level_end.append(right)
            else:
                level_end[level] = right
            y = MEASURE_Y0 - level * MEASURE_STEP
            sel = k == self.selected_measure
            color = MEASURE_SEL if sel else self.palette().text().color()
            pen = self._pen(2 if sel else 1, color)
            ext = self._pen(1, color if sel else QColor(140, 140, 140), Qt.PenStyle.DashLine)
            a, b = self.measures[k]
            tip = f"{self._point_text(a)} → {self._point_text(b)}\n{text}\nClick to select, Delete to remove"
            s0, s1, pt = x0 / 1000, x1 / 1000, self._pt
            parts = [scene.addLine(QLineF(pt(s0), pt(s0, y - 6)), ext), scene.addLine(QLineF(pt(s1), pt(s1, y - 6)), ext),
                     scene.addPath(self._along(s0, s1, y), pen),
                     scene.addLine(QLineF(pt(s0, y - 5), pt(s0, y + 5)), pen),
                     scene.addLine(QLineF(pt(s1, y - 5), pt(s1, y + 5)), pen)]
            for item in parts[:2]:
                item.setZValue(1)  # behind the elements
            target = scene.addPolygon(self._band(s0, s1, lambda s: y - 7, lambda s: y + 7),
                                      QPen(Qt.PenStyle.NoPen), QBrush(QColor(0, 0, 0, 0)))
            box = MeasureBox(text, f, color, self.palette().base().color(), color, 2 if sel else 1)
            box.setPos(pt(mid / 1000, y - 3))
            scene.addItem(box)
            for item in parts[2:] + [target, box]:
                item.setZValue(9)
                item.setData(0, k)
                item.setToolTip(tip)
        self._content_top = MEASURE_Y0 - len(level_end) * MEASURE_STEP if level_end else LABEL_Y

    def _hit_measure(self, pos) -> int | None:
        for item in self.items(pos):
            k = item.data(0)
            if isinstance(k, int) and k < len(self.measures):
                return k
        return None

    def _hit_point(self, pos) -> tuple[float, PointRef] | None:
        if not self._points:
            return None
        s, y = self._sy(pos)
        if abs(y) > H / 2 + 12:
            return None
        best = min(self._points, key=lambda t: abs(t[0] - s))
        return best if abs(best[0] - s) * 1000 * self._sx() <= 8 else None

    def _select_measure(self, k: int | None) -> None:
        if k != self.selected_measure:
            self.selected_measure = k
            self.rebuild()
        if k is not None:
            self.message.emit("Measurement selected: press Delete to remove it")

    def _pick_point(self, ref: PointRef) -> None:
        if self._pick is None or self._point_s(self._pick) is None:
            self._pick = ref
            self.message.emit(f"From {self._point_text(ref)}: click the second point")
        elif self._point_s(ref) == self._point_s(self._pick):
            return
        else:
            d = abs(self._point_s(ref) - self._point_s(self._pick))
            self.measures.append((self._pick, ref))
            self.message.emit(f"{self._point_text(self._pick)} → {self._point_text(ref)}: {fmt_len(d, self.unit)}")
            self._pick = None
        self.selected_measure = None
        self.rebuild()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._pick is not None:
            self._pick = None
            self.rebuild()
            self.message.emit("Measurement cancelled")
        elif event.key() == Qt.Key.Key_Escape and self.measure_mode:
            self.measureModeExited.emit()
        elif event.key() == Qt.Key.Key_Escape and self.selected_measure is not None:
            self._select_measure(None)
        else:
            super().keyPressEvent(event)

    def _element_item(self, x, width: float, y0: float = 0.0, offset: float = 0.0,
                      color: QColor | None = None, fill: QColor | None = None) -> QGraphicsItem:
        """Add the shape of element x centred on y0, shifted by offset (m)."""
        h = et.CATALOGUE.get(x.type, et.CATALOGUE["other"])["height"] * H
        if self.floor:
            if x.is_thick:
                item = QGraphicsPolygonItem(self._band(x.s_start + offset, x.s_end + offset,
                                                       lambda s: y0 - h / 2, lambda s: y0 + h / 2))
                item.setBrush(QBrush(fill or QColor(*et.CATALOGUE.get(x.type, et.CATALOGUE["other"])["fill"])))
                item.setPen(self._pen(width, color or OUTLINE))
            else:
                s = x.s_end + offset
                item = QGraphicsLineItem(QLineF(self._pt(s, y0 - h / 2), self._pt(s, y0 + h / 2)))
                item.setPen(self._pen(width, color))
        elif x.is_thick:
            item = QGraphicsRectItem((x.s_start + offset) * 1000, y0 - h / 2, x.length * 1000, h)
            item.setBrush(QBrush(fill or QColor(*et.CATALOGUE.get(x.type, et.CATALOGUE["other"])["fill"])))
            item.setPen(self._pen(width, color or OUTLINE))
        else:
            s = (x.s_end + offset) * 1000
            item = QGraphicsLineItem(s, y0 - h / 2, s, y0 + h / 2)
            item.setPen(self._pen(width, color))
        item.setZValue(2 if x.is_thick else 3)
        self.scene().addItem(item)
        return item

    def _draw_dimensions(self, a: int, b: int) -> None:
        bl, scene = self.shown, self.scene()
        d = bl.distances(a, b)
        A, B = bl.elements[d["a"]], bl.elements[d["b"]]
        pen, dash = self._pen(1), self._pen(1, style=Qt.PenStyle.DashLine)
        ca, cb, ea, sb = A.s_center, B.s_center, A.s_end, B.s_start
        y1, y2 = DIM_Y
        for s in (ca, cb, ea, sb):
            scene.addLine(QLineF(self._pt(s), self._pt(s, y2 + 4)), dash)
        scene.addPath(self._along(ca, cb, y1), pen)
        scene.addPath(self._along(ea, sb, y2), pen)
        self._text_at(f"c-c {fmt_len(d['center_center'], self.unit)}", (ca + cb) / 2, y1 - 14, False)
        self._text_at(f"gap {fmt_len(d['edge_gap'], self.unit)}", (ea + sb) / 2, y2 - 14, False)
        for s in (ea, sb):  # arrows marking the edges in use
            arrow = QGraphicsPolygonItem(QPolygonF([QPointF(0, 0), QPointF(-5, 9), QPointF(5, 9)]))
            arrow.setBrush(self.palette().text())
            arrow.setPen(QPen(Qt.PenStyle.NoPen))
            arrow.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            arrow.setPos(self._pt(s, y2 + 6))
            scene.addItem(arrow)

    def update_labels(self) -> None:
        """Centre ignore-transform texts on their anchor and hide overlapping labels."""
        t = self.transform()
        sx, sy = t.m11() or 1.0, t.m22() or 1.0  # sx is negative when flipped
        placed = []
        for item, anchor, hideable, vcenter in self.labels:
            br = item.boundingRect()
            w, h = br.width(), br.height() if vcenter else 0.0
            item.setPos(anchor.x() - w / 2 / sx, anchor.y() - h / 2 / sy)
            if hideable:
                c = self.mapFromScene(anchor)
                placed.append((c.x() - w / 2, QRectF(c.x() - w / 2 - 2, c.y() - h / 2, w + 4, br.height()), item))
        visible: list[QRectF] = []  # 4 px apart at least
        for _left, rect, item in sorted(placed, key=lambda p: p[0]):
            show = self.show_labels and not any(rect.intersects(r) for r in visible[-50:])
            item.setVisible(show)
            if show:
                visible.append(rect)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if not self.shown or not self.shown.elements:
            return
        painter.save()
        painter.resetTransform()
        vp = self.viewport().rect()
        top = vp.height() - RULER_H
        painter.fillRect(0, top, vp.width(), RULER_H, self.palette().base())
        painter.setPen(self.palette().text().color())
        painter.drawLine(0, top, vp.width(), top)
        f = QFont(self.font())
        f.setPointSizeF(max(f.pointSizeF() - 1.5, 6))
        painter.setFont(f)
        px_per_m = self._sx() * 1000
        scale = 1000.0 if self.unit == "mm" else 1.0
        step = nice_step(80 / px_per_m * scale) / scale  # m
        decimals = max(0, -math.floor(math.log10(step * scale) + 1e-9))
        s0, s1 = sorted((self.mapToScene(0, 0).x() / 1000, self.mapToScene(vp.width(), 0).x() / 1000))
        k = math.floor(s0 / step)
        while k * step <= s1:
            s = k * step
            x = round(self.mapFromScene(QPointF(s * 1000, 0)).x())
            painter.drawLine(x, top, x, top + 6)
            painter.drawText(x + 3, top + RULER_H - 6, f"{s * scale:.{decimals}f}")
            for m in range(1, 5):  # minor ticks
                xm = round(self.mapFromScene(QPointF((s + m * step / 5) * 1000, 0)).x())
                painter.drawLine(xm, top, xm, top + 3)
            k += 1
        caption = "x [m]" if self.floor and self.unit == "m" else "x [mm]" if self.floor else ""
        if caption:  # the floor plan ruler shows the floor x coordinate, not s
            w = painter.fontMetrics().horizontalAdvance(caption) + 8
            painter.fillRect(vp.width() - w, top + 1, w, RULER_H - 1, self.palette().base())
            painter.drawText(vp.width() - w + 4, top + RULER_H - 6, caption)
        painter.restore()

    # ------------------------------------------------------------ interaction
    def hit(self, pos) -> int | None:
        if not self.shown:
            return None
        s, y = self._sy(pos)
        px = s * 1000
        tol = 4 / self._sx()
        best, best_key = None, None
        for i, x in enumerate(self.shown.elements):
            if x.is_gap_drift:
                continue
            h = et.CATALOGUE.get(x.type, et.CATALOGUE["other"])["height"] * H
            if abs(y) > h / 2 + 4:
                continue
            x0, x1 = x.s_start * 1000, x.s_end * 1000
            if x0 - tol <= px <= x1 + tol:
                key = (x1 - x0, abs(px - (x0 + x1) / 2))
                if best_key is None or key < best_key:
                    best, best_key = i, key
        return best

    def tooltip(self, i: int) -> str:
        x, u = self.shown.elements[i], self.unit
        return (f"{x.name}\n{et.CATALOGUE.get(x.type, {}).get('label', x.type)}\n"
                f"s_start {fmt_len(x.s_start, u)}\ns_end {fmt_len(x.s_end, u)}\nL {fmt_len(x.length, u)}")

    def wheelEvent(self, event) -> None:
        if not self.shown or not self.shown.elements:
            return
        f = 1.25 ** (event.angleDelta().y() / 120)
        sx = self.transform().m11()  # signed: negative when flipped
        f = min(max(f, self._fit_scale() * 0.5 / abs(sx)), 2000 / abs(sx))  # at most 2000 px/mm
        pos = event.position().toPoint()
        before = self.mapToScene(pos)
        self.scale(f, f if self.floor else 1.0)
        if self.floor:
            self.rebuild()  # element sizes are in pixels
        after = self.mapToScene(pos)
        t = self.transform()  # keep the point under the cursor fixed
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() + round((before.x() - after.x()) * t.m11()))
        if self.floor:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + round((before.y() - after.y()) * t.m22()))
        self.update_labels()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update_labels()

    def mousePressEvent(self, event) -> None:
        btn = event.button()
        pos = event.position().toPoint()
        left = btn == Qt.MouseButton.LeftButton
        self._press, self._mode = None, ""
        if left and (k := self._hit_measure(pos)) is not None:
            self._select_measure(k)
            return
        if left and self.selected_measure is not None:
            self._select_measure(None)
        if left and self.measure_mode and (pt := self._hit_point(pos)) is not None:
            self._pick_point(pt[1])
            return
        hit = self.hit(pos) if left and not self.measure_mode else None
        self._press = (btn, pos, hit, self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        self._press_geo = self.geo  # the geometry changes while a bend is dragged
        if hit is not None:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                sel = [i for i in self.selection if i != hit]
                sel = (sel + [hit])[-2:] if hit not in self.selection else sel
                self._press = None
            else:
                sel = [hit]
            if sel != self.selection:
                self.selection = sel
                self.rebuild()
                self.selectionChanged.emit(sel)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._press is None:
            if (k := self._hit_measure(pos)) is not None:
                a, b = self.measures[k]
                QToolTip.showText(event.globalPosition().toPoint(),
                                  f"{self._point_text(a)} → {self._point_text(b)}", self)
                return
            if self.measure_mode:
                self._hover_point(event)
                return
            hit = self.hit(pos)
            if hit is not None:
                QToolTip.showText(event.globalPosition().toPoint(), self.tooltip(hit), self)
            elif (a := self._hit_aperture(pos)) is not None:
                QToolTip.showText(event.globalPosition().toPoint(), self._aperture_tooltip(a), self)
            else:
                QToolTip.hideText()
            return
        btn, start, hit, sx0, sy0 = self._press
        dx = pos.x() - start.x()
        if not self._mode and (abs(dx) > 3 or abs(pos.y() - start.y()) > 3):
            self._mode = "drag" if hit is not None else "pan"
            QToolTip.hideText()
        if self._mode == "pan":
            self.horizontalScrollBar().setValue(sx0 - dx)
            self.verticalScrollBar().setValue(sy0 - (pos.y() - start.y()))
        elif self._mode == "drag":
            ds = self._sy(pos, self._press_geo)[0] - self._sy(start, self._press_geo)[0]
            self._drag_to(hit, ds * 1000)

    def _hover_point(self, event) -> None:
        pt = self._hit_point(event.position().toPoint())
        if self._hover is not None:
            self._hover.setVisible(pt is not None)
            if pt is not None:
                self._hover.setPos(self._pt(pt[0]))
        if pt is None:
            QToolTip.hideText()
            return
        text = self._point_text(pt[1])
        if self._pick is not None and self._point_s(self._pick) is not None:
            text += f"\nΔ {fmt_len(abs(pt[0] - self._point_s(self._pick)), self.unit)}"
        QToolTip.showText(event.globalPosition().toPoint(), text, self)

    def _drag_to(self, i: int, dx_scene: float) -> None:
        bl = self.bl
        _lo, _hi, root = bl.block(i)
        x = bl.elements[root]
        delta = round((x.s_start + dx_scene / 1000) / SNAP) * SNAP - x.s_start
        self._drag = (root, delta)
        trial = copy.deepcopy(bl)
        try:
            trial.set_position(root, x.s_center + delta, "center")
        except EditError as err:
            self.message.emit(str(err))
            return
        self.shown = trial
        self.rebuild()
        up, down = trial.neighbours(root)
        y = trial.elements[root]
        parts = [f"Δ {fmt_len(delta, self.unit)}"]
        if up is not None:
            parts.append(f"up gap {fmt_len(y.s_start - trial.elements[up].s_end, self.unit)}")
        if down is not None:
            parts.append(f"down gap {fmt_len(trial.elements[down].s_start - y.s_end, self.unit)}")
        self.message.emit("   ".join(parts))

    def mouseReleaseEvent(self, event) -> None:
        press, mode, drag = self._press, self._mode, self._drag
        self._press, self._mode, self._drag = None, "", None
        if press is None:
            return
        if mode == "drag" and drag is not None:
            self.shown = self.bl
            if drag[1]:
                self.dragFinished.emit(*drag)  # main window applies it and refreshes
            else:
                self.rebuild()
        elif mode == "" and press[0] == Qt.MouseButton.LeftButton and press[2] is None and self.selection:
            self.selection = []
            self.rebuild()
            self.selectionChanged.emit([])

    def mouseDoubleClickEvent(self, event) -> None:
        self.mousePressEvent(event)
