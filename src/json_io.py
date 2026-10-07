"""Read and write the JSON layout format."""
from __future__ import annotations

import json

import element_types as et
from model import TOL, Aperture, Beamline, Element

FORMAT = "beamline-layout"
VERSION = 1


def _r(x: float) -> float:
    return round(x, 9) + 0.0


def _neighbour(bl: Beamline, i: int, j: int | None, side: str) -> dict | None:
    if j is None:
        return None
    x, n = bl.elements[i], bl.elements[j]
    gap = x.s_start - n.s_end if side == "up" else n.s_start - x.s_end
    return {"name": n.name, "edge_gap": _r(gap), "center_gap": _r(abs(x.s_center - n.s_center))}


def _aperture_dict(bl: Beamline, a: Aperture) -> dict:
    s0, s1 = bl.aperture_span(a)
    d: dict = {"name": a.name}
    if a.start_ref:
        d["from"] = a.start_ref
    if a.end_ref:
        d["to"] = a.end_ref
    d.update(s_start=_r(s0), s_end=_r(s1), radius=_r(a.radius))
    if a.radius_end is not None:
        d["radius_end"] = _r(a.radius_end)
    d["comment"] = a.comment
    return d


def to_dict(bl: Beamline) -> dict:
    elements = []
    for i, x in enumerate(bl.elements):
        d: dict = {"name": x.name, "type": x.type, "keyword": x.keyword, "length": _r(x.length),
                   "s_start": _r(x.s_start), "s_center": _r(x.s_center), "s_end": _r(x.s_end),
                   "angle": _r(x.angle)}
        if not x.is_gap_drift:
            up, down = bl.neighbours(i)
            d["upstream"] = _neighbour(bl, i, up, "up")
            d["downstream"] = _neighbour(bl, i, down, "down")
        d.update(params=x.params, comment=x.comment, extra=x.extra)
        elements.append(d)
    x, y, angle = bl.origin
    return {"format": FORMAT, "version": VERSION, "units": "m",
            "origin": {"x": _r(x), "y": _r(y), "angle": _r(angle)},
            "apertures": [_aperture_dict(bl, a) for a in bl.apertures],
            "survey": {"header_lines": bl.header_lines, "columns_line": bl.columns_line,
                       "formats_line": bl.formats_line, "value_column": bl.value_column,
                       "trailing_newline": bl.trailing_newline},
            "elements": elements}


def from_dict(data: dict) -> tuple[Beamline, list[str]]:
    """Build a Beamline from JSON data. Returns (beamline, warnings)."""
    if data.get("format") != FORMAT:
        raise ValueError("Not a beamline-layout JSON file")
    sv = data.get("survey", {})
    bl = Beamline(header_lines=list(sv.get("header_lines", [])),
                  value_column=int(sv.get("value_column", 41)),
                  trailing_newline=bool(sv.get("trailing_newline", False)))
    if "columns_line" in sv:
        bl.columns_line, bl.formats_line = sv["columns_line"], sv["formats_line"]
    o = data.get("origin") or {}
    bl.origin = (float(o.get("x", 0.0)), float(o.get("y", 0.0)), float(o.get("angle", 0.0)))
    for d in data.get("elements", []):
        typ = d.get("type") if d.get("type") in et.CATALOGUE else et.infer_type(d["name"], d["keyword"])
        length, angle = float(d["length"]), float(d.get("angle", 0.0))
        bl.elements.append(Element(
            name=d["name"], keyword=d["keyword"], type=typ, s_end=float(d["s_end"]),
            length=length, angle=angle,
            params=et.default_params(typ, length, angle, old=d.get("params", {})),
            comment=d.get("comment", ""), extra={k: str(v) for k, v in d.get("extra", {}).items()}))

    warnings = _read_apertures(bl, data.get("apertures", []))

    # Derived fields are recomputed; report mismatches.
    derived = to_dict(bl)["elements"]
    for d, new in zip(data.get("elements", []), derived):
        for key in ("s_start", "s_center"):
            if key in d and abs(float(d[key]) - new[key]) > TOL + 1e-12:
                warnings.append(f"{d['name']}: {key} {d[key]} != {new[key]}")
        for key in ("upstream", "downstream"):
            old, cur = d.get(key), new.get(key)
            if old and cur and (old["name"] != cur["name"]
                                or abs(old["edge_gap"] - cur["edge_gap"]) > TOL + 1e-12):
                warnings.append(f"{d['name']}: {key} differs")
    return bl, warnings


def _read_apertures(bl: Beamline, items: list[dict]) -> list[str]:
    """Add the aperture sections to bl. Returns warnings."""
    warnings = []
    names = {x.name for x in bl.elements}
    for k, d in enumerate(items):
        name = str(d.get("name") or f"APERTURE_{k + 1}")
        start_ref, end_ref = str(d.get("from", "")), str(d.get("to", ""))
        for ref in (start_ref, end_ref):
            if ref and ref not in names:
                warnings.append(f"Aperture {name}: unknown element {ref}")
        if ("s_start" not in d and start_ref not in names) or ("s_end" not in d and end_ref not in names) \
                or "radius" not in d:
            warnings.append(f"Aperture {name}: needs s_start (or from), s_end (or to) and radius; skipped")
            continue
        a = Aperture(name=name, s_start=float(d.get("s_start", 0.0)), s_end=float(d.get("s_end", 0.0)),
                     radius=float(d["radius"]),
                     radius_end=float(d["radius_end"]) if d.get("radius_end") is not None else None,
                     start_ref=start_ref, end_ref=end_ref, comment=str(d.get("comment", "")))
        s0, s1 = a.s_start, a.s_end = bl.aperture_span(a)  # fallback if a reference element goes away
        if s1 < s0 - TOL:
            warnings.append(f"Aperture {name}: ends before it starts")
        if a.radius < 0 or (a.radius_end or 0.0) < 0:
            warnings.append(f"Aperture {name}: negative radius")
        bl.apertures.append(a)
    return warnings


def read_json(path: str) -> tuple[Beamline, list[str]]:
    with open(path, encoding="utf-8") as f:
        return from_dict(json.load(f))


def write_json(bl: Beamline, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_dict(bl), f, indent=2, ensure_ascii=False)
        f.write("\n")
