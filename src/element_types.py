"""Element type catalogue, parameter definitions and type inference rules."""
from __future__ import annotations

import re

# Gap drifts are filler drifts that edit operations lengthen and shorten.
# "DRF_NEW_n" drifts created by insert/remove are gap drifts as well.
GAP_DRIFT_REGEX = r"DRF(_NEW_)?\d+"

# Param = (key, label, unit, default)
Param = tuple[str, str, str, "float | str"]

CATALOGUE: dict[str, dict] = {
    "drift": dict(label="Drift", default_keyword="DRIFT", fill=(200, 200, 200), height=0.2, params=[]),
    "marker": dict(label="Marker", default_keyword="MARKER", fill=(120, 120, 120), height=0.5, params=[]),
    "dipole": dict(label="Dipole", default_keyword="RBEND", fill=(110, 150, 200), height=0.9, params=[
        ("angle", "angle", "rad", 0.0), ("e1", "e1", "rad", 0.0),
        ("e2", "e2", "rad", 0.0), ("field", "field", "T", 0.0)]),
    "quadrupole": dict(label="Quadrupole", default_keyword="QUADRUPOLE", fill=(205, 110, 105), height=0.8, params=[
        ("k1", "k1", "m⁻²", 0.0), ("gradient", "gradient", "T/m", 0.0), ("aperture", "aperture", "mm", 0.0)]),
    "sextupole": dict(label="Sextupole", default_keyword="SEXTUPOLE", fill=(120, 175, 120), height=0.7, params=[
        ("k2", "k2", "m⁻³", 0.0)]),
    "corrector_h": dict(label="Corrector H", default_keyword="HKICKER", fill=(215, 180, 110), height=0.6, params=[
        ("kick", "kick", "mrad", 0.0)]),
    "corrector_v": dict(label="Corrector V", default_keyword="VKICKER", fill=(215, 180, 110), height=0.6, params=[
        ("kick", "kick", "mrad", 0.0)]),
    "corrector_hv": dict(label="Corrector HV", default_keyword="KICKER", fill=(215, 180, 110), height=0.6, params=[
        ("hkick", "hkick", "mrad", 0.0), ("vkick", "vkick", "mrad", 0.0)]),
    "solenoid": dict(label="Solenoid", default_keyword="SOLENOID", fill=(160, 130, 180), height=0.8, params=[
        ("field", "field", "T", 0.0)]),
    "rf_structure": dict(label="RF structure", default_keyword="RFCAVITY", fill=(190, 160, 120), height=0.7, params=[
        ("frequency", "frequency", "GHz", 0.0), ("gradient", "gradient", "MV/m", 0.0), ("phase", "phase", "deg", 0.0)]),
    "bpm": dict(label="BPM", default_keyword="MONITOR", fill=(120, 170, 170), height=0.5, params=[]),
    "screen": dict(label="Screen", default_keyword="MONITOR", fill=(120, 170, 170), height=0.6, params=[
        ("material", "material", "", "")]),
    "current_monitor": dict(label="Current monitor", default_keyword="MONITOR", fill=(120, 170, 170), height=0.5,
                            params=[]),
    "collimator": dict(label="Collimator", default_keyword="COLLIMATOR", fill=(140, 140, 140), height=0.8, params=[
        ("material", "material", "", ""), ("aperture_x", "aperture_x", "mm", 0.0),
        ("aperture_y", "aperture_y", "mm", 0.0)]),
    "scatterer": dict(label="Scatterer", default_keyword="ABSORBER", fill=(175, 140, 110), height=0.7, params=[
        ("material", "material", "", ""), ("thickness", "thickness", "mm", 0.0)]),
    "window": dict(label="Window", default_keyword="INTERFACE", fill=(150, 170, 200), height=0.6, params=[
        ("material", "material", "", ""), ("thickness", "thickness", "mm", 0.0)]),
    "source": dict(label="Source", default_keyword="MARKER", fill=(200, 140, 170), height=0.6, params=[
        ("energy", "energy", "MeV", 0.0)]),
    "other": dict(label="Other", default_keyword="", fill=(180, 180, 160), height=0.5, params=[]),
}

# Ordered name rules: (substrings, required keyword or None, type)
NAME_RULES: list[tuple[tuple[str, ...], str | None, str]] = [
    (("DRF",), None, "drift"),
    (("QFD", "QDD", "QF", "QD"), "QUADRUPOLE", "quadrupole"),
    (("BHB",), None, "dipole"),
    (("ACS",), "DRIFT", "rf_structure"),
    (("BPM", "BPC"), None, "bpm"),
    (("BTV",), None, "screen"),
    (("ICT",), None, "current_monitor"),
    (("DHG", "DHJ", "DHB"), None, "corrector_hv"),
    (("SDV",), None, "corrector_v"),
    (("COL",), None, "collimator"),
    (("FLT", "PSC"), None, "scatterer"),
    (("AIR",), None, "window"),
]

KEYWORD_RULES: dict[str, str] = {
    "MARKER": "marker", "QUADRUPOLE": "quadrupole", "RBEND": "dipole", "SBEND": "dipole",
    "MONITOR": "bpm", "KICKER": "corrector_hv", "HKICKER": "corrector_h", "VKICKER": "corrector_v",
    "ABSORBER": "scatterer", "COLLIMATOR": "collimator", "INTERFACE": "window", "DRIFT": "drift",
}


def infer_type(name: str, keyword: str) -> str:
    """Infer an element type from its name first, then from its keyword."""
    for subs, kw, typ in NAME_RULES:
        if any(s in name for s in subs) and (kw is None or kw == keyword):
            return typ
    return KEYWORD_RULES.get(keyword, "other")


def is_gap_drift_name(name: str, keyword: str) -> bool:
    return keyword == "DRIFT" and re.search(GAP_DRIFT_REGEX, name) is not None


def default_keyword(type_key: str, current: str = "") -> str:
    """Default MAD-X keyword of a type ('other' keeps the current keyword)."""
    return CATALOGUE[type_key]["default_keyword"] or current


def default_params(type_key: str, length: float = 0.0, angle: float = 0.0,
                   old: dict | None = None) -> dict:
    """Parameter dict for a type, keeping matching values from `old`."""
    old = old or {}
    out: dict = {}
    for key, _label, _unit, default in CATALOGUE[type_key]["params"]:
        if key in old and isinstance(default, str):
            out[key] = str(old[key])
        elif key in old and isinstance(old[key], (int, float)):
            out[key] = float(old[key])
        elif type_key == "scatterer" and key == "thickness":
            out[key] = round(length * 1000.0, 9)
        else:
            out[key] = default
    if "angle" in out:
        out["angle"] = angle
    return out
