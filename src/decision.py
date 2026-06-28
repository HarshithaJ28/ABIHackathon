"""Runs P2 + P3 over the 5 raw tables. Complexity O(M + total note text) — optimal."""
from __future__ import annotations
from config import FACILITY_NAMES, TARGET_PAYER_CODE
from src.extract import from_assessment, from_note, active_wound_dx
from src.fusion import fuse
from src.ai_assist import fill_gaps
from src.decide import decide


def _has_part_b(coverage: list) -> bool:
    return any(c.get("payer_code") == TARGET_PAYER_CODE and c.get("effective_to") is None
               for c in coverage)


def _get(table: dict, *keys):
    for k in keys:
        if k in table:
            return table[k]
        if str(k) in table:
            return table[str(k)]
    return []


def run_decision(data: dict, llm_call=None) -> list:
    rows = []
    for p in data["patients"]:
        pid, iid = p["patient_id"], p["id"]

        assess_fields = from_assessment(_get(data["assessments"], iid))
        notes = _get(data["notes"], iid)
        note_text = max((n.get("note_text") or "" for n in notes), key=len, default="")
        note_fields = from_note(note_text)

        fused = fuse(note_fields, assess_fields)
        fused = fill_gaps(note_text, fused, llm_call)              # advisory only

        wound_dx = active_wound_dx(_get(data["diagnoses"], pid))
        has_b = _has_part_b(_get(data["coverage"], pid))
        result = decide(fused, has_b, wound_dx)

        rows.append({
            "patient_id": pid, "id": iid,
            "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "facility": FACILITY_NAMES.get(p.get("facility_id"), "Unknown"),
            "icd10": (wound_dx or {}).get("icd10_code"),
            **result,
        })
    rows.sort(key=lambda r: r["estimated_value"], reverse=True)    # EV-ranked worklist
    return rows