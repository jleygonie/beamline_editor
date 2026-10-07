"""Read and write MAD-X survey (TFS) files with a byte-exact round trip."""
from __future__ import annotations

import re
import shlex

import element_types as et
from model import Beamline, Element

KNOWN = ("NAME", "KEYWORD", "S", "L", "ANGLE")
DEFAULT_VALUE_COLUMN = 41


def _num(x: float) -> str:
    return f"{round(x, 9) + 0.0:.9f}"


def parse_survey(text: str) -> Beamline:
    trailing = text.endswith("\n")
    lines = text.split("\n")
    if trailing:
        lines.pop()
    bl = Beamline(trailing_newline=trailing)
    columns: list[str] = []
    formats: list[str] = []
    value_column = None
    for line in lines:
        if line.startswith("@"):
            bl.header_lines.append(line)
        elif line.startswith("*"):
            bl.columns_line = line
            columns = line.split()[1:]
        elif line.startswith("$"):
            bl.formats_line = line
            formats = line.split()[1:]
        elif line.strip():
            if not columns:
                raise ValueError("Missing '*' column line")
            values = dict(zip(columns, shlex.split(line)))
            if value_column is None:
                m = re.match(r'\s*"[^"]*"\s+"[^"]*"\s+', line)
                value_column = m.end() if m else DEFAULT_VALUE_COLUMN
            name = values.get("NAME", "")
            keyword = values.get("KEYWORD", "")
            length = float(values.get("L", 0.0))
            angle = float(values.get("ANGLE", 0.0))
            typ = et.infer_type(name, keyword)
            x = Element(name=name, keyword=keyword, type=typ, s_end=float(values["S"]),
                        length=length, angle=angle,
                        params=et.default_params(typ, length, angle),
                        extra={k: v for k, v in values.items() if k not in KNOWN},
                        raw_line=line)
            x.raw_sig = x.signature()
            bl.elements.append(x)
    bl.value_column = value_column or DEFAULT_VALUE_COLUMN
    return bl


def format_row(bl: Beamline, x: Element) -> str:
    """Format a new or modified row."""
    line = " " + f'"{x.name}"'.ljust(19) + " " + f'"{x.keyword}"'
    line += " " * max(1, bl.value_column - len(line))
    columns = bl.columns_line.split()[1:]
    formats = dict(zip(columns, bl.formats_line.split()[1:]))
    known = {"S": x.s_end, "L": x.length, "ANGLE": x.angle}
    values = []
    for col in columns:
        if col in ("NAME", "KEYWORD"):
            continue
        if col in known:
            values.append(_num(known[col]))
        else:
            v = x.extra.get(col, "")
            values.append(f'"{v}"' if formats.get(col, "").endswith("s") else (v or "0"))
    return line + " ".join(values)


def format_survey(bl: Beamline) -> str:
    lines = [*bl.header_lines, bl.columns_line, bl.formats_line]
    lines += [x.raw_line if x.raw_line is not None else format_row(bl, x) for x in bl.elements]
    return "\n".join(lines) + ("\n" if bl.trailing_newline else "")


def read_survey(path: str) -> Beamline:
    with open(path, encoding="utf-8", newline="") as f:
        return parse_survey(f.read())


def write_survey(bl: Beamline, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(format_survey(bl))
