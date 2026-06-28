"""
P2 — wound-field extraction.

Trust order: structured assessment (raw_json) > clinical note > none.
Notes are scanned in ONE Aho-Corasick pass. Measurements use a small grammar that
handles every real format: "LxWxD", "L x Wcm, depth Z", and labeled length/width/depth.
Every field is returned as {value, confidence, source, evidence}.
"""
from __future__ import annotations
import json, re
from src.lexicon import match_terms, WOUND_ICD_PREFIXES

REQUIRED = ["wound_type", "location", "length_cm", "width_cm", "depth_cm", "drainage_amount"]

_TRIPLE = re.compile(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)", re.I)
_PAIR   = re.compile(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)", re.I)
_DEPTH  = re.compile(r"depth[:\s]*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*cm\s*deep", re.I)
_LABELED = {
    "length_cm": re.compile(r"length[:\s]+(\d+(?:\.\d+)?)", re.I),
    "width_cm":  re.compile(r"width[:\s]+(\d+(?:\.\d+)?)", re.I),
    "depth_cm":  re.compile(r"depth[:\s]+(\d+(?:\.\d+)?)", re.I),
}
_STAGE = re.compile(r"stage[:\s]*(\d+|unstageable|deep tissue)", re.I)


def _f(value, confidence, source, evidence):
    return {"value": value, "confidence": confidence, "source": source, "evidence": evidence}


def normalize(text: str) -> str:
    """Repair real-world noise: em-dash, and doubled words like 'Diabetic diabetic'."""
    if not text:
        return ""
    t = text.replace("\u2014", "-").replace("\u2013", "-")
    t = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", t, flags=re.I)   # collapse duplicate words
    return re.sub(r"[ \t]+", " ", t)


def _scan_entities(text: str) -> dict:
    """One AC pass -> first wound_type, first location, laterality, drainage."""
    out, lat = {}, None
    for cat, canonical, s, e in match_terms(text):
        snippet = text[max(0, s - 0):e]
        if cat == "wound_type" and "wound_type" not in out:
            out["wound_type"] = _f(canonical, "clean", "note", f"'{snippet}'")
        elif cat == "location" and "location" not in out:
            out["_loc_canon"] = canonical
        elif cat == "laterality" and lat is None:
            lat = canonical.strip()
        elif cat == "drainage" and "drainage_amount" not in out:
            out["drainage_amount"] = _f(canonical, "clean", "note", f"'{snippet}'")
    if "_loc_canon" in out:
        loc = out.pop("_loc_canon").replace("_", " ")
        value = f"{lat} {loc}".strip() if lat else loc
        out["location"] = _f(value, "clean", "note", f"'{value}'")
    return out


def _scan_measurements(text: str) -> dict:
    out = {}
    m = _TRIPLE.search(text)
    if m:
        for field, g in zip(("length_cm", "width_cm", "depth_cm"), m.groups()):
            out[field] = _f(float(g), "clean", "note", f"'{m.group(0)}'")
    else:
        p = _PAIR.search(text)
        if p:
            out["length_cm"] = _f(float(p.group(1)), "clean", "note", f"'{p.group(0)}'")
            out["width_cm"]  = _f(float(p.group(2)), "clean", "note", f"'{p.group(0)}'")
        d = _DEPTH.search(text)
        if d:
            val = d.group(1) or d.group(2)
            out["depth_cm"] = _f(float(val), "clean", "note", f"'{d.group(0)}'")
    for field, rx in _LABELED.items():        # labeled values override
        lm = rx.search(text)
        if lm:
            out[field] = _f(float(lm.group(1)), "clean", "note", f"'{lm.group(0)}'")
    sm = _STAGE.search(text)
    if sm:
        out["stage"] = _f(sm.group(1).lower(), "clean", "note", f"'{sm.group(0)}'")
    return out


def from_note(note_text: str) -> dict:
    """Linear in note length. Handles Envive / SOAP / brief templates uniformly."""
    t = normalize(note_text).lower()
    fields = _scan_entities(t)
    fields.update({k: v for k, v in _scan_measurements(note_text).items()
                   if k not in fields})
    return fields


# ── structured assessment (highest trust) ─────────────────────────────────
def _canon_from_text(text: str, category: str):
    for cat, canonical, _, _ in match_terms(normalize(text).lower()):
        if cat == category:
            return canonical
    return None


def from_assessment(records: list) -> dict:
    """Parse raw_json. Handles BOTH real templates:
       (1) sections[WOUND/LOCATION/DRAINAGE] with question/answer pairs
       (2) single WOUND_INFO 'Wound narrative' free-text string."""
    out = {}
    current = [a for a in records if a.get("is_current", True) and a.get("raw_json")]
    if not current:
        return out
    a = max(current, key=lambda r: r.get("assessment_date") or "")
    try:
        doc = json.loads(a["raw_json"])
    except (json.JSONDecodeError, TypeError):
        return out

    qa, lat = {}, None
    for section in doc.get("sections", []):
        for q in section.get("questions", []):
            qa[(q.get("question") or "").strip().lower()] = (q.get("answer") or "").strip()

    # ── template 1: narrative ("Diabetic to Left foot / Measures 5.3 cm x 4.5 cm ...")
    narrative = qa.get("wound narrative")
    if narrative:
        return _from_narrative(narrative)

    # ── template 2: structured Q/A
    def put(field, value, ev):
        if value not in (None, "", "n/a", "N/A"):
            out[field] = _f(value, "clean", "assessment", ev)

    wt = qa.get("wound type")
    if wt:
        put("wound_type", _canon_from_text(wt, "wound_type") or wt.lower(), f"WoundType='{wt}'")
    loc = qa.get("location")
    lat = (qa.get("laterality") or "").strip().lower()
    if loc:
        canon = _canon_from_text(loc, "location")
        base = (canon.replace("_", " ") if canon else loc.lower())
        value = f"{lat} {base}".strip() if lat in ("left", "right") else base
        put("location", value, f"Location='{loc}'")
    for field, key in (("length_cm", "length (cm)"), ("width_cm", "width (cm)"),
                       ("depth_cm", "depth (cm)")):
        if qa.get(key):
            try:
                put(field, float(qa[key]), f"{key}='{qa[key]}'")
            except ValueError:
                pass
    if qa.get("drainage amount"):
        amt = _canon_from_text(qa["drainage amount"], "drainage") or qa["drainage amount"].lower()
        put("drainage_amount", amt, f"DrainageAmount='{qa['drainage amount']}'")
    if qa.get("stage"):
        put("stage", qa["stage"].lower(), f"Stage='{qa['stage']}'")
    return out


def _from_narrative(text: str) -> dict:
    out = from_note(text)                      # reuse the note grammar
    for f in out.values():                     # but mark provenance as assessment
        f["source"] = "assessment"
    return out


def active_wound_dx(diagnoses: list):
    """Deterministic wound gate. Returns the matching active wound dx, or None.
       Correctly accepts E11.621 / I70.234 and rejects E11.9 / I70.209."""
    for dx in diagnoses:
        code = (dx.get("icd10_code") or "").upper().strip()
        if dx.get("clinical_status") == "active":
            for prefix, wtype in WOUND_ICD_PREFIXES.items():
                if code.startswith(prefix):
                    return {"icd10_code": code, "wound_type": wtype,
                            "description": dx.get("icd10_description")}
    return None