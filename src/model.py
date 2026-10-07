"""Beamline data model: elements, derived roles, edit operations and validation."""
from __future__ import annotations

import copy
import functools
from dataclasses import dataclass, field

import element_types as et
from geometry import Geometry

TOL = 1e-9  # m


class EditError(Exception):
    """An edit operation could not be applied (nothing was changed)."""


@dataclass
class Issue:
    index: int
    level: str  # "error" | "warning"
    message: str


@dataclass
class Element:
    name: str
    keyword: str  # written to survey
    type: str  # key in element_types.CATALOGUE
    s_end: float  # = survey S (source of truth)
    length: float  # = survey L (source of truth)
    angle: float = 0.0
    params: dict = field(default_factory=dict)
    comment: str = ""
    extra: dict = field(default_factory=dict)  # unknown survey columns, passed through
    raw_line: str | None = None
    raw_sig: tuple | None = field(default=None, repr=False, compare=False)  # values raw_line was parsed from

    @property
    def s_start(self) -> float:
        return self.s_end - self.length

    @property
    def s_center(self) -> float:
        return self.s_end - self.length / 2

    @property
    def is_gap_drift(self) -> bool:
        return et.is_gap_drift_name(self.name, self.keyword)

    @property
    def is_boundary(self) -> bool:
        return "$START" in self.name or "$END" in self.name

    @property
    def is_thick(self) -> bool:
        return abs(self.length) > TOL

    def signature(self) -> tuple:
        return (self.name, self.keyword, self.s_end, self.length, self.angle, tuple(self.extra.items()))


@dataclass
class Aperture:
    """Beam pipe section drawn behind the elements. Positions and radii in m.

    start_ref / end_ref name an element whose s_start / s_end gives the position, so the section follows
    that element when it moves; s_start / s_end are used when the reference is empty or missing.
    """
    name: str
    s_start: float
    s_end: float
    radius: float
    radius_end: float | None = None  # linear taper from radius to radius_end
    start_ref: str = ""
    end_ref: str = ""
    comment: str = ""


def _edit(fn):
    """Run an edit atomically: restore the elements on error, then invalidate raw lines."""
    @functools.wraps(fn)
    def wrapper(self: Beamline, *args, **kwargs):
        if self._depth:
            return fn(self, *args, **kwargs)
        backup = copy.deepcopy(self.elements)
        self._depth += 1
        try:
            result = fn(self, *args, **kwargs)
        except Exception:
            self.elements = backup
            raise
        finally:
            self._depth -= 1
        for x in self.elements:
            if x.raw_line is not None and x.signature() != x.raw_sig:
                x.raw_line = None
        return result
    return wrapper


def _point_before(a: Element, b: Element) -> bool:
    """Whether zero-length a goes before b at the same position: points of a section, its $END, then the
    next section's $START and its points."""
    kind = lambda x: "end" if "$END" in x.name else "start" if "$START" in x.name else ""
    return {"": kind(b) != "start", "start": kind(b) != "end", "end": kind(b) == "start" or kind(b) == "end"}[kind(a)]


def _fmt(x: float) -> str:
    return f"{x:+.6g}"


@dataclass
class Beamline:
    header_lines: list[str] = field(default_factory=list)
    columns_line: str = "* NAME               KEYWORD            S L ANGLE "
    formats_line: str = "$ %s                 %s                 %le %le %le "
    value_column: int = 41  # column where the first number starts
    trailing_newline: bool = False
    elements: list[Element] = field(default_factory=list)  # file order
    apertures: list[Aperture] = field(default_factory=list)
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)  # floor x, y (m) and direction (rad) at the first s
    _depth: int = field(default=0, repr=False, compare=False)
    _removed: dict = field(default_factory=dict, repr=False, compare=False)  # name -> drift covered by a move

    # ------------------------------------------------------------ roles
    def chain_break(self, i: int) -> bool:
        e = self.elements
        return 0 < i < len(e) and abs(e[i].s_start - e[i - 1].s_end) > TOL

    def is_floating(self, i: int) -> bool:
        """Zero-length solid element that breaks the chain (e.g. listed before its drift)."""
        x = self.elements[i]
        return (not x.is_thick and not x.is_gap_drift and not x.is_boundary
                and (self.chain_break(i) or self.chain_break(i + 1)))

    def owner(self, i: int) -> int | None:
        """Index of the thick solid element that zero-length element i is attached to."""
        e = self.elements
        x = e[i]
        if x.is_thick or x.is_gap_drift or x.is_boundary:
            return None
        j = i - 1
        while j >= 0 and not e[j].is_thick:
            j -= 1
        if j >= 0 and not e[j].is_gap_drift and abs(x.s_end - e[j].s_end) <= TOL:
            return j
        j = i + 1
        while j < len(e) and not e[j].is_thick:
            j += 1
        if j < len(e) and not e[j].is_gap_drift and abs(x.s_end - e[j].s_start) <= TOL:
            return j
        return None

    def block(self, i: int) -> tuple[int, int, int]:
        """(first, last, root) indices of the block containing element i."""
        e = self.elements
        root = self.owner(i)
        root = i if root is None else root
        lo = hi = root
        k = root - 1
        while k >= 0 and not e[k].is_thick:
            if self.owner(k) == root:
                lo = k
            k -= 1
        k = root + 1
        while k < len(e) and not e[k].is_thick:
            if self.owner(k) == root:
                hi = k
            k += 1
        return lo, hi, root

    def limits(self) -> tuple[float, float]:
        """s range of the line, from the outermost $START and $END markers if present."""
        e = self.elements
        if not e:
            return 0.0, 0.0
        lo = min((x.s_end for x in e if "$START" in x.name), default=min(x.s_start for x in e))
        hi = max((x.s_end for x in e if "$END" in x.name), default=max(x.s_end for x in e))
        return lo, hi

    def total_length(self) -> float:
        lo, hi = self.limits()
        return hi - lo

    def neighbours(self, i: int) -> tuple[int | None, int | None]:
        """Nearest solid elements upstream and downstream by position, outside i's block."""
        e = self.elements
        if e[i].is_gap_drift:
            return None, None
        lo, hi, _ = self.block(i)
        me = (e[i].s_center, i)
        up = down = None
        for j, x in enumerate(e):
            if lo <= j <= hi or x.is_gap_drift:
                continue
            key = (x.s_center, j)
            if key < me and (up is None or key > (e[up].s_center, up)):
                up = j
            elif key > me and (down is None or key < (e[down].s_center, down)):
                down = j
        return up, down

    def geometry(self) -> Geometry:
        """Floor-plan orbit from the element angles."""
        e = self.elements
        bends = [(x.s_start, x.s_end, x.angle) for x in e if x.angle]
        lo = min((x.s_start for x in e), default=0.0)
        hi = max((x.s_end for x in e), default=0.0)
        return Geometry(bends, lo, hi, self.origin)

    def aperture_span(self, a: Aperture) -> tuple[float, float]:
        """(s_start, s_end) of an aperture section, following its reference elements."""
        names = {x.name: x for x in self.elements}
        s0 = names[a.start_ref].s_start if a.start_ref in names else a.s_start
        s1 = names[a.end_ref].s_end if a.end_ref in names else a.s_end
        return s0, s1

    def aperture_radius(self, a: Aperture, s: float) -> float:
        s0, s1 = self.aperture_span(a)
        if a.radius_end is None or s1 - s0 <= TOL:
            return a.radius
        return a.radius + (a.radius_end - a.radius) * min(max((s - s0) / (s1 - s0), 0.0), 1.0)

    def find(self, name: str) -> int:
        for i, x in enumerate(self.elements):
            if x.name == name:
                return i
        raise KeyError(name)

    # ------------------------------------------------------------ helpers
    def _check_movable(self, root: int) -> None:
        x = self.elements[root]
        if x.is_gap_drift:
            raise EditError("Drifts cannot be moved")

    def _neighbour(self, i: int, step: int) -> int | None:
        """Index of the next non-floating element from i in direction step."""
        j = i + step
        while 0 <= j < len(self.elements) and self.is_floating(j):
            j += step
        return j if 0 <= j < len(self.elements) else None

    def _move_edges(self, i: int, d_start: float, d_end: float) -> None:
        """Move the start edge of i's block by d_start and its end edge by d_end.

        The neighbouring drifts absorb the change when they can; otherwise the block is taken out and
        placed again at its new position, possibly overlapping other elements (reported by validate).
        """
        e = self.elements
        lo, hi, root = self.block(i)
        self._check_movable(root)
        if self.is_floating(root):
            raise EditError("Floating point: set its position instead")
        if e[root].length + d_end - d_start < -TOL:
            raise EditError("Start after end")
        u = self._neighbour(lo, -1) if d_start else None
        d = self._neighbour(hi, 1) if d_end else None
        # A drift used up entirely is removed (relocating), so points meeting there end up in order.
        if ((u is not None and (not e[u].is_gap_drift or e[u].length + d_start <= TOL))
                or (d is not None and (not e[d].is_gap_drift or e[d].length - d_end <= TOL))
                or (d_start and u is None) or (d_end and d is None)
                or self._swallowed(lo, hi, root)):
            self._relocate(lo, hi, root, d_start, d_end)
            return
        if d_start:
            e[u].length = max(e[u].length + d_start, 0.0)
            e[u].s_end += d_start
        for k in range(lo, root):
            e[k].s_end += d_start
        e[root].s_end += d_end
        e[root].length += d_end - d_start
        for k in range(root + 1, hi + 1):
            e[k].s_end += d_end
        if d_end:
            e[d].length = max(e[d].length - d_end, 0.0)

    def _swallowed(self, lo: int, hi: int, root: int) -> bool:
        """Whether a floating point next to block lo..hi lies inside its root element."""
        e = self.elements
        a, b = self._neighbour(lo, -1), self._neighbour(hi, 1)
        return any(e[root].s_start + TOL < e[k].s_end < e[root].s_end - TOL
                   for k in range(lo if a is None else a, (hi if b is None else b) + 1)
                   if not lo <= k <= hi and not e[k].is_gap_drift)

    def _relocate(self, lo: int, hi: int, root: int, d_start: float, d_end: float) -> None:
        """Take block lo..hi out of the line, move its edges and put it back where it now lies."""
        e = self.elements
        floating = {id(x) for j, x in enumerate(e) if self.is_floating(j)}  # before the line is cut open
        block, r = e[lo:hi + 1], e[root]
        s0, s1 = r.s_start, r.s_end
        # Floating points inside the block (it covered them): chained in again once it has moved.
        swallowed = [x for x in e if id(x) in floating and not x.is_boundary and s0 + TOL < x.s_end < s1 - TOL
                     and all(x is not y for y in block)]
        e[:] = [x for x in e if all(x is not y for y in swallowed)]
        floating -= {id(x) for x in swallowed + block}
        lo, root = self._index(block[0]), self._index(r)
        hi = lo + len(block) - 1
        # Close the hole: the drifts on both sides and the freed space become one drift.
        a, b = lo, hi
        u, d = self._neighbour(lo, -1), self._neighbour(hi, 1)
        if u is not None and e[u].is_gap_drift:
            a = u
        if d is not None and e[d].is_gap_drift:
            b = d
        prev, nxt = self._neighbour(a, -1), self._neighbour(b, 1)
        start, end = e[a].s_start, e[b].s_end
        for k, x in enumerate(block):
            x.s_end += d_start if k < root - lo else d_end
        r.length += d_end - d_start
        a, b = 0 if prev is None else prev + 1, len(e) - 1 if nxt is None else nxt - 1  # with floating points
        old = [x for x in e[a:b + 1] if x.is_gap_drift]
        e[a:b + 1] = [x for x in e[a:b + 1] if not x.is_gap_drift and all(x is not y for y in block)]
        self._place(block, r, old, floating)
        for x in swallowed:
            self._place([x], x, old, floating)
        lo_s, hi_s = self.limits()
        self._fill_gaps(max(min(start, r.s_start), lo_s), min(max(end, r.s_end), hi_s), old, floating)
        names = {x.name for x in e}
        self._removed.update((x.name, x) for x in old if x.name not in names)  # name kept for when it is freed

    def _index(self, x: Element) -> int:
        return next(j for j, y in enumerate(self.elements) if y is x)

    def _fill_gaps(self, lo: float, hi: float, old: list[Element], floating: set[int]) -> None:
        """Turn free space between lo and hi left by an element that moved away into drift."""
        e = self.elements
        chain = [j for j, x in enumerate(e) if id(x) not in floating]
        gaps = []
        for p, n in zip(chain, chain[1:]):
            a, b = e[p].s_end, e[n].s_start
            if b - a <= TOL or a < lo - TOL or b > hi + TOL:
                continue
            changed = True
            while changed and b - a > TOL:  # leave out what (overlapping) elements still cover
                changed = False
                for x in e:
                    if x.is_thick and not x.is_gap_drift and x.s_start < b - TOL and x.s_end > a + TOL:
                        if x.s_start <= a + TOL:
                            a = x.s_end
                        elif x.s_end >= b - TOL:
                            b = x.s_start
                        else:
                            b = a  # covered in the middle: left as a chain break
                        changed = True
            if b - a > TOL:
                gaps.append((p, n, a, b))
        new = {}
        for p, n, a, b in sorted(gaps, key=lambda g: g[2] - g[3]):  # longest first takes the old name
            if e[p].is_gap_drift and abs(a - e[p].s_end) <= TOL:
                e[p].s_end, e[p].length = b, e[p].length + b - a
            elif e[n].is_gap_drift and abs(b - e[n].s_start) <= TOL:
                e[n].length += b - a
            else:
                new[n] = self._make_drift(b, b - a, e[p], old)
                e.append(new[n])  # reserve its name
        for n in sorted(new, reverse=True):
            e.remove(new[n])
            e.insert(n, new[n])

    def _make_drift(self, s_end: float, length: float, template: Element, old: list[Element]) -> Element:
        """Gap drift ending at s_end, reusing a removed drift (name, raw line) when it fits."""
        names = {x.name for x in self.elements}
        free = [y for y in old if y.name not in names]
        free += [y for y in self._removed.values() if y.name not in names and all(y is not z for z in free)]
        for y in free:
            if abs(y.s_end - s_end) <= TOL and abs(y.length - length) <= TOL:
                return y
        s_start = s_end - length
        overlap = {id(y): min(y.s_end, s_end) - max(y.s_start, s_start) for y in free}
        best = max(free, key=lambda y: overlap[id(y)], default=None)
        if best is not None and overlap[id(best)] > TOL:  # mostly where that drift was
            return Element(best.name, "DRIFT", "drift", s_end, length, extra=dict(best.extra))
        name = template.name if template.is_gap_drift and template.name not in names \
            else self._new_drift_name(self._drift_prefix(template))
        return Element(name, "DRIFT", "drift", s_end, length, extra=dict(template.extra))

    def _place(self, block: list[Element], root: Element, old: list[Element], floating: set[int]) -> None:
        """Insert block, cutting the drifts it covers; overlaps with solid elements are left as they are."""
        e = self.elements
        s0, s1 = root.s_start, root.s_end
        j = 0
        while j < len(e):
            x = e[j]
            if not x.is_gap_drift or x.s_end <= s0 + TOL or x.s_start >= s1 - TOL:
                j += 1
                continue
            old.append(x)
            del e[j]
            parts = []
            if x.s_start < s0 - TOL:
                parts.append(self._make_drift(s0, s0 - x.s_start, x, old))
                e.insert(j, parts[-1])
            if x.s_end > s1 + TOL:
                parts.append(self._make_drift(x.s_end, x.s_end - s1, x, old))
                e.insert(j + len(parts) - 1, parts[-1])
            j += len(parts)
        # Before the first block (or drift) that starts after s0, keeping the order by position.
        g = len(e)
        for j, x in enumerate(e):
            if id(x) in floating or (not x.is_gap_drift and self.owner(j) is not None):
                continue
            if abs(x.s_start - s0) <= TOL and not x.is_thick and not root.is_thick:
                before = _point_before(root, x)  # points at the same position
            else:
                before = x.s_start > s0 + TOL or (x.s_start >= s0 - TOL and (x.is_thick or x.s_end >= s1 - TOL))
            if before:
                g = j if x.is_gap_drift else self.block(j)[0]
                break
        e[g:g] = block

    @staticmethod
    def _drift_prefix(x: Element) -> str:
        k = x.name.find("DRF")
        return x.name[:k] if k >= 0 else x.name[:x.name.find(".") + 1]

    def _new_drift_name(self, prefix: str) -> str:
        names = {x.name for x in self.elements}
        n = 1
        while f"{prefix}DRF_NEW_{n}" in names:
            n += 1
        return f"{prefix}DRF_NEW_{n}"

    # ------------------------------------------------------------ edit operations
    @_edit
    def move(self, i: int, delta: float) -> None:
        """Move the block of i by delta, keeping the total line length."""
        if delta:
            self._move_edges(i, delta, delta)

    @_edit
    def shift(self, i: int, delta: float) -> None:
        """Move the block of i and everything downstream by delta.

        The upstream drift absorbs the change; if it is too short the block overlaps what is upstream.
        """
        e = self.elements
        lo, _hi, root = self.block(i)
        self._check_movable(root)
        if self.is_floating(root):
            raise EditError("Floating point: set its position instead")
        if not delta:
            return
        u = self._neighbour(lo, -1)
        for k in range(0 if u is None else u + 1, len(e)):
            e[k].s_end += delta
        if u is None:
            return
        if e[u].is_gap_drift:
            if e[u].length + delta >= -TOL:
                e[u].length = max(e[u].length + delta, 0.0)
                e[u].s_end += delta
            else:
                self._removed[e[u].name] = e[u]
                del e[u]  # fully covered by the block
        else:
            gap = e[lo].s_start - e[u].s_end
            if gap > TOL:
                x = e[root]
                e.insert(u + 1, Element(self._new_drift_name(self._drift_prefix(x)), "DRIFT", "drift",
                                        e[lo].s_start, gap, extra=dict(x.extra)))

    @_edit
    def set_length(self, i: int, new_length: float, anchor: str = "center") -> None:
        if new_length < 0:
            raise EditError("Negative length")
        if self.owner(i) is not None:
            raise EditError("Attached element: edit its parent")
        delta = new_length - self.elements[i].length
        d_start, d_end = {"start": (0.0, delta), "end": (-delta, 0.0),
                          "center": (-delta / 2, delta / 2)}[anchor]
        self._move_edges(i, d_start, d_end)
        self.elements[i].length = new_length  # exact value

    @_edit
    def set_position(self, i: int, value: float, ref: str = "center", mode: str = "move") -> None:
        """Set the start, center or end of element i.

        center slides the block along the line (same length); start / end move only that edge, so the
        length changes (and the center follows). Shift also moves everything downstream by the same amount.
        """
        e = self.elements
        x = e[i]
        delta = value - {"start": x.s_start, "center": x.s_center, "end": x.s_end}[ref]
        if not delta:
            return
        if self.is_floating(i):  # take it out and chain it in where it lands
            floating = {id(y) for j, y in enumerate(e) if j != i and self.is_floating(j)}
            del e[i]
            x.s_end += delta
            self._place([x], x, [], floating)
            return
        root = self.block(i)[2]
        if ref == "center" or not x.is_thick or i != root:
            (self.shift if mode == "shift" else self.move)(i, delta)
        elif ref == "start":
            self._move_edges(i, delta, 0.0)
        elif mode == "shift":
            if x.length + delta < -TOL:
                raise EditError("Start after end")
            x.length += delta
            for k in range(i, len(e)):
                e[k].s_end += delta
        else:
            self._move_edges(i, 0.0, delta)

    def distances(self, a: int, b: int) -> dict:
        """Distances between two elements, A being the upstream one."""
        e = self.elements
        a, b = sorted((a, b), key=lambda j: (e[j].s_center, j))
        A, B = e[a], e[b]
        return dict(a=a, b=b,
                    center_center=B.s_center - A.s_center,
                    edge_gap=B.s_start - A.s_end,
                    start_start=B.s_start - A.s_start,
                    end_end=B.s_end - A.s_end)

    @_edit
    def set_distance(self, a: int, b: int, kind: str, value: float,
                     moving: str = "B", mode: str = "move") -> None:
        d = self.distances(a, b)
        delta = value - d[kind]
        if moving == "B":
            (self.shift if mode == "shift" else self.move)(d["b"], delta)
        elif mode == "shift":
            raise EditError("Shift only when B moves")
        else:
            self.move(d["a"], -delta)

    @_edit
    def insert(self, element: Element) -> int:
        """Insert element at its position, cutting the drift(s) it covers. Returns its index.

        It is inserted even where it overlaps other elements (reported by validate).
        """
        e = self.elements
        if not element.name or any(x.name == element.name for x in e):
            raise EditError("Name empty or already used")
        if element.length < 0:
            raise EditError("Negative length")
        if not element.extra:  # extra survey columns from the nearest drift
            drifts = [x for x in e if x.is_gap_drift]
            if drifts:
                near = min(drifts, key=lambda x: max(x.s_start - element.s_center, element.s_center - x.s_end, 0))
                element.extra = dict(near.extra)
        floating = {id(x) for j, x in enumerate(e) if self.is_floating(j)}
        self._place([element], element, [], floating)
        return self._index(element)

    def free_name(self, base: str = "NEW") -> str:
        names = {x.name for x in self.elements}
        n = 1
        while f"{base}_{n}" in names:
            n += 1
        return f"{base}_{n}"

    @_edit
    def remove(self, i: int) -> None:
        """Remove the block of i, merging it with its neighbouring gap drifts."""
        e = self.elements
        lo, hi, root = self.block(i)
        self._check_movable(root)
        if self.is_floating(root):
            del e[root]
            return
        a = lo - 1 if lo > 0 and e[lo - 1].is_gap_drift else lo
        b = hi + 1 if hi + 1 < len(e) and e[hi + 1].is_gap_drift else hi
        start = e[a].s_start if a < lo else e[root].s_start
        end = e[b].s_end if b > hi else e[root].s_end
        if end - start <= TOL and a == lo and b == hi:
            del e[lo:hi + 1]
            return
        ref = e[a] if a < lo else e[b] if b > hi else e[root]
        name = ref.name if ref.is_gap_drift else self._new_drift_name(ref.name[:ref.name.find(".") + 1])
        e[a:b + 1] = [Element(name, "DRIFT", "drift", end, end - start, extra=dict(ref.extra))]

    @_edit
    def rename(self, i: int, name: str) -> None:
        name = name.strip()
        if not name or any(x.name == name for j, x in enumerate(self.elements) if j != i):
            raise EditError("Name empty or already used")
        old = self.elements[i].name
        self.elements[i].name = name
        for a in self.apertures:
            a.start_ref = name if a.start_ref == old else a.start_ref
            a.end_ref = name if a.end_ref == old else a.end_ref

    @_edit
    def set_type(self, i: int, type_key: str) -> None:
        if type_key not in et.CATALOGUE:
            raise EditError(f"Unknown type {type_key}")
        x = self.elements[i]
        x.type = type_key
        x.params = et.default_params(type_key, x.length, x.angle, old=x.params)

    @_edit
    def set_keyword(self, i: int, keyword: str) -> None:
        keyword = keyword.strip().upper()
        if not keyword:
            raise EditError("Empty keyword")
        self.elements[i].keyword = keyword

    @_edit
    def set_param(self, i: int, key: str, value: float | str) -> None:
        x = self.elements[i]
        if key == "angle":
            x.angle = float(value)
        x.params[key] = value

    @_edit
    def set_angle(self, i: int, angle: float) -> None:
        x = self.elements[i]
        x.angle = float(angle)
        if "angle" in x.params:
            x.params["angle"] = x.angle

    @_edit
    def set_comment(self, i: int, comment: str) -> None:
        self.elements[i].comment = comment

    # ------------------------------------------------------------ validation
    def overlapping(self) -> set[int]:
        """Indices of solid elements that overlap another one (zero-length ones: strictly inside it)."""
        e = self.elements
        solid = sorted((j for j, x in enumerate(e) if not x.is_gap_drift and not x.is_boundary),
                       key=lambda j: (e[j].s_start, -e[j].length, j))
        out: set[int] = set()
        prev = None  # thick element reaching furthest downstream so far
        for j in solid:
            x = e[j]
            if prev is not None and x.s_start < e[prev].s_end - TOL and (x.is_thick or x.s_end > e[prev].s_start + TOL):
                out.update((j, prev))
            if x.is_thick and (prev is None or x.s_end > e[prev].s_end):
                prev = j
        return out

    def validate(self) -> list[Issue]:
        e = self.elements
        issues: list[Issue] = []
        for i, x in enumerate(e):
            if x.length < -TOL:
                issues.append(Issue(i, "error", "Negative length"))
            if self.chain_break(i):
                issues.append(Issue(i, "warning", f"Chain break ({_fmt(x.s_start - e[i - 1].s_end)} m)"))
        solid = sorted((j for j, x in enumerate(e) if not x.is_gap_drift and not x.is_boundary),
                       key=lambda j: (e[j].s_start, -e[j].length, j))
        prev = None
        for j in solid:
            x = e[j]
            if prev is not None and x.s_start < e[prev].s_end - TOL and (x.is_thick or x.s_end > e[prev].s_start + TOL):
                issues.append(Issue(j, "error", f"Overlaps {e[prev].name}"))
            if x.is_thick and (prev is None or x.s_end > e[prev].s_end):
                prev = j
        if any(x.is_boundary for x in e):
            lo, hi = self.limits()
            for i, x in enumerate(e):
                if x.s_start < lo - TOL or x.s_end > hi + TOL:
                    issues.append(Issue(i, "error", "Outside $START-$END"))
        issues.sort(key=lambda s: s.index)
        return issues
