"""Field-level fusion of note + assessment.
   agree -> clean (corroborated) | disagree -> conflict | one source -> kept | neither -> missing."""
from __future__ import annotations
from config import DIM_TOLERANCE_CM
from src.extract import REQUIRED

ALL_FIELDS = REQUIRED + ["stage"]
_NUM = {"length_cm", "width_cm", "depth_cm"}


def _agree(a, b, field) -> bool:
    if field in _NUM:
        try:
            return abs(float(a) - float(b)) <= DIM_TOLERANCE_CM
        except (TypeError, ValueError):
            return False
    return str(a).strip().lower() == str(b).strip().lower()


def fuse(note: dict, assess: dict) -> dict:
    fields, conflicts = {}, []
    for f in ALL_FIELDS:
        n, a = note.get(f), assess.get(f)
        if n and a:
            if _agree(n["value"], a["value"], f):
                fields[f] = {**a, "confidence": "clean",
                             "evidence": f"{a['evidence']} ✓ corroborated by note {n['evidence']}"}
            else:
                fields[f] = {**a, "confidence": "conflict",
                             "evidence": f"assessment={a['value']!r} vs note={n['value']!r}"}
                conflicts.append(f)
        elif a:
            fields[f] = a
        elif n:
            fields[f] = n
        else:
            fields[f] = {"value": None, "confidence": "missing", "source": "none", "evidence": None}
    return {"fields": fields, "conflicts": conflicts}