"""Compare two beamlines element by element (matched by name)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from model import Beamline

TOL = 1e-7  # m, smaller differences are ignored


@dataclass
class Diff:
    name: str
    status: str  # "same" | "changed" | "added" (only in B) | "removed" (only in A)
    a: int | None  # index in A
    b: int | None  # index in B
    changes: set[str] = field(default_factory=set)  # position, length, type, keyword, angle, params, comment
    d_start: float = 0.0  # B - A, after applying the offset
    d_center: float = 0.0
    d_end: float = 0.0
    d_length: float = 0.0


def _keyed(bl: Beamline, drifts: bool) -> dict[tuple[str, int], int]:
    """(name, occurrence) -> index, so that repeated names are matched in order."""
    seen: dict[str, int] = {}
    out = {}
    for i, x in enumerate(bl.elements):
        if x.is_gap_drift and not drifts:
            continue
        n = seen.get(x.name, 0)
        seen[x.name] = n + 1
        out[(x.name, n)] = i
    return out


def common_names(a: Beamline, b: Beamline) -> list[str]:
    """Names of solid elements present once in both lines, in A order."""
    names_b = [x.name for x in b.elements if not x.is_gap_drift]
    names_a = [x.name for x in a.elements if not x.is_gap_drift]
    return [n for n in names_a if names_a.count(n) == 1 and names_b.count(n) == 1]


def offset_on(a: Beamline, b: Beamline, name: str) -> float:
    """Offset to add to B positions so that element `name` has the same centre in both."""
    return a.elements[a.find(name)].s_center - b.elements[b.find(name)].s_center


def median_offset(a: Beamline, b: Beamline) -> float:
    """Offset to add to B positions that best overlays the common elements."""
    names = common_names(a, b)
    return statistics.median(offset_on(a, b, n) for n in names) if names else 0.0


def compare(a: Beamline, b: Beamline, offset: float = 0.0, drifts: bool = False) -> list[Diff]:
    """Differences from A to B, B positions being shifted by offset. Sorted by position."""
    ka, kb = _keyed(a, drifts), _keyed(b, drifts)
    diffs = []
    for key, i in ka.items():
        x = a.elements[i]
        j = kb.get(key)
        if j is None:
            diffs.append(Diff(x.name, "removed", i, None))
            continue
        y = b.elements[j]
        d = Diff(x.name, "same", i, j, d_start=y.s_start + offset - x.s_start,
                 d_center=y.s_center + offset - x.s_center, d_end=y.s_end + offset - x.s_end,
                 d_length=y.length - x.length)
        if abs(d.d_start) > TOL or abs(d.d_end) > TOL:
            d.changes.add("position")
        if abs(d.d_length) > TOL:
            d.changes.add("length")
        if abs(x.angle - y.angle) > TOL:
            d.changes.add("angle")
        for attr in ("type", "keyword", "params", "comment"):
            if getattr(x, attr) != getattr(y, attr):
                d.changes.add(attr)
        if d.changes:
            d.status = "changed"
        diffs.append(d)
    diffs += [Diff(b.elements[j].name, "added", None, j) for key, j in kb.items() if key not in ka]

    def where(d: Diff) -> tuple[float, int]:
        return ((a.elements[d.a].s_center, 0) if d.a is not None
                else (b.elements[d.b].s_center + offset, 1))
    return sorted(diffs, key=where)
