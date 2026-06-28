"""Field-level fusion of note + assessment with deterministic reconciliation.

Reconciliation policy (escalate to human review ONLY when truly incompatible):
  * numeric (dims)      -> agree within tolerance
  * location (hierarch) -> subsumption: if one refines the other, keep the most
                           specific. Conflict ONLY if the anatomical sites differ.
  * drainage (ordinal)  -> none<light<moderate<heavy. Prefer the structured
                           assessment; conflict ONLY if >=2 levels apart.
  * wound_type          -> generic vs specific reconciles to specific; else conflict.
This collapses spurious "disagreements" so humans only see genuine ambiguity.
"""
from __future__ import annotations
from config import DIM_TOLERANCE_CM
from src.extract import REQUIRED

ALL_FIELDS = REQUIRED + ["stage"]
_NUM = {"length_cm", "width_cm", "depth_cm"}
_DRAIN_RANK = {"none": 0, "light": 1, "moderate": 2, "heavy": 3}
# wound_type families that subsume a more specific child
_GENERIC_WOUND = {"chronic_ulcer", "ulcer", None, ""}


def _split_loc(v):
    """'left foot' -> ('left', 'foot'); 'sacrum' -> (None, 'sacrum')."""
    parts = str(v).strip().lower().split()
    if parts and parts[0] in ("left", "right"):
        return parts[0], " ".join(parts[1:])
    return None, " ".join(parts)


def _reconcile(field, a_val, b_val):
    """Return (resolved_value | None, status) where status is 'agree' or 'conflict'.
       a_val = assessment (higher trust), b_val = note."""
    if field in _NUM:
        try:
            if abs(float(a_val) - float(b_val)) <= DIM_TOLERANCE_CM:
                return a_val, "agree"
        except (TypeError, ValueError):
            pass
        return None, "conflict"

    if field == "location":
        la, sa = _split_loc(a_val)
        lb, sb = _split_loc(b_val)
        if sa != sb and sa not in sb and sb not in sa:     # genuinely different sites
            return None, "conflict"
        site = sa if len(sa) >= len(sb) else sb             # keep the richer site
        lat = la or lb                                       # keep whichever has laterality
        return (f"{lat} {site}".strip() if lat else site), "agree"

    if field == "drainage_amount":
        ra, rb = _DRAIN_RANK.get(str(a_val).lower()), _DRAIN_RANK.get(str(b_val).lower())
        if ra is None or rb is None or abs(ra - rb) <= 1:    # adjacent = close enough
            return a_val, "agree"                            # trust structured source
        return None, "conflict"

    if field == "wound_type":
        if str(a_val).lower() == str(b_val).lower():
            return a_val, "agree"
        if a_val in _GENERIC_WOUND:
            return b_val, "agree"
        if b_val in _GENERIC_WOUND:
            return a_val, "agree"
        return None, "conflict"

    # default categorical (e.g. stage): exact match
    if str(a_val).strip().lower() == str(b_val).strip().lower():
        return a_val, "agree"
    return None, "conflict"


def fuse(note: dict, assess: dict) -> dict:
    fields, conflicts = {}, []
    for f in ALL_FIELDS:
        n, a = note.get(f), assess.get(f)
        if n and a:
            resolved, status = _reconcile(f, a["value"], n["value"])
            if status == "agree":
                fields[f] = {**a, "value": resolved, "confidence": "clean",
                             "evidence": f"reconciled: assessment={a['value']!r}, note={n['value']!r} -> {resolved!r}"}
            else:
                fields[f] = {**a, "confidence": "conflict",
                             "evidence": f"incompatible: assessment={a['value']!r} vs note={n['value']!r}"}
                conflicts.append(f)
        elif a:
            fields[f] = a
        elif n:
            fields[f] = n
        else:
            fields[f] = {"value": None, "confidence": "missing", "source": "none", "evidence": None}
    return {"fields": fields, "conflicts": conflicts}