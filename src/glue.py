"""Glue several beamlines into one."""
from __future__ import annotations

import copy

from model import TOL, Beamline, Element


def glue(parts: list[Beamline], end_to_end: bool = False) -> tuple[Beamline, list[str]]:
    """Concatenate beamlines in the given order. Returns (beamline, warnings).

    end_to_end: shift each part so that it starts where the previous one ends;
    otherwise keep the s positions from the files. Gaps are filled with a drift.
    """
    if not parts:
        raise ValueError("Nothing to glue")
    parts = [copy.deepcopy(p) for p in parts]
    first = parts[0]
    out = Beamline(header_lines=list(first.header_lines), columns_line=first.columns_line,
                   formats_line=first.formats_line, value_column=first.value_column,
                   trailing_newline=parts[-1].trailing_newline, origin=first.origin)
    warnings: list[str] = []
    names: set[str] = set()
    for k, part in enumerate(parts):
        if not part.elements:
            continue
        same_format = (part.columns_line.split() == first.columns_line.split()
                       and part.value_column == first.value_column)
        offset = 0.0
        if out.elements:
            prev_end = out.limits()[1]
            start = part.limits()[0]
            offset = prev_end - start if end_to_end else 0.0
            for x in part.elements:
                x.s_end += offset
            gap = start + offset - prev_end
            if gap > TOL:
                name = out._new_drift_name("GLUE.")
                out.elements.append(Element(name, "DRIFT", "drift", start + offset, gap,
                                            extra=dict(out.elements[-1].extra)))
                names.add(name)
                warnings.append(f"Part {k + 1}: gap of {gap:.9g} m filled with {name}")
            elif gap < -TOL:
                warnings.append(f"Part {k + 1}: overlaps the previous part by {-gap:.9g} m")
        renamed: dict[str, str] = {}
        for x in part.elements:
            if x.name in names:
                n = 2
                while f"{x.name}_{n}" in names:
                    n += 1
                warnings.append(f"Part {k + 1}: duplicate name {x.name} renamed {x.name}_{n}")
                renamed[x.name] = f"{x.name}_{n}"
                x.name = renamed[x.name]
            names.add(x.name)
            if not same_format or x.signature() != x.raw_sig:
                x.raw_line = None  # reformat with the columns of the first part
            out.elements.append(x)
        for a in part.apertures:
            a.s_start += offset
            a.s_end += offset
            a.start_ref = renamed.get(a.start_ref, a.start_ref)
            a.end_ref = renamed.get(a.end_ref, a.end_ref)
            out.apertures.append(a)
    return out, warnings
