"""P3 — deterministic routing. Pure function of field confidences + hard gates.
   A guessed/conflict/missing required field can never reach auto_accept."""
from __future__ import annotations
from config import REQUIRED_FIELDS, MAX_MISSING_BEFORE_REJECT, WOUND_REIMBURSEMENT

REMEDY = {
    "wound_type":      "a diagnosis or assessment naming the wound type",
    "location":        "a note/assessment stating the anatomic site (and laterality)",
    "length_cm":       "a measured length (cm) in a note or assessment",
    "width_cm":        "a measured width (cm) in a note or assessment",
    "depth_cm":        "a measured depth (cm) — often absent from summary notes",
    "drainage_amount": "a note stating drainage amount (none/light/moderate/heavy)",
}


def _value(wound_type) -> int:
    return WOUND_REIMBURSEMENT.get(wound_type, WOUND_REIMBURSEMENT["_default"])


def decide(fused: dict, has_part_b: bool, wound_dx) -> dict:
    fields = fused["fields"]
    wt = fields["wound_type"]["value"] or (wound_dx or {}).get("wound_type")
    reimb = _value(wt)

    # ── hard deterministic gates (no AI) ──────────────────────────────────
    if not has_part_b:
        return _r("reject", "No active Medicare Part B coverage.",
                  ["Confirm or activate Medicare Part B before billing."], fields, 0)
    if wound_dx is None and fields["wound_type"]["value"] is None:
        return _r("reject", "No active wound: no active wound ICD-10 and no wound type documented.",
                  ["Add an active wound diagnosis (e.g. L89.x / E11.62x) or a wound assessment."],
                  fields, 0)

    missing  = [f for f in REQUIRED_FIELDS if fields[f]["confidence"] == "missing"]
    guessed  = [f for f in REQUIRED_FIELDS if fields[f]["confidence"] == "guessed"]
    conflict = [f for f in REQUIRED_FIELDS if fields[f]["confidence"] == "conflict"]

    # ── clean & complete -> bill ──────────────────────────────────────────
    if not (missing or guessed or conflict):
        return _r("auto_accept", "All required wound fields documented and corroborated.",
                  [], fields, reimb)

    # ── too sparse to trust -> reject, but say exactly what is needed ──────
    if len(missing) >= MAX_MISSING_BEFORE_REJECT:
        return _r("reject", f"Not reliably extractable — {len(missing)} required fields absent.",
                  [f"Provide {REMEDY[f]}." for f in missing], fields, reimb)

    # ── otherwise human review, with precise why + how-to-fix ─────────────
    reasons, remedy = [], []
    for f in conflict:
        reasons.append(f"{f} disagrees across sources ({fields[f]['evidence']})")
        remedy.append(f"Clinician reconciles {f}; sources currently disagree.")
    if guessed:
        reasons.append(f"{', '.join(guessed)} inferred by AI, unverified by structured data")
        remedy += [f"Confirm {REMEDY[f]}." for f in guessed]
    if missing:
        reasons.append(f"{', '.join(missing)} not documented")
        remedy += [f"Provide {REMEDY[f]}." for f in missing]
    return _r("flag_for_review", "; ".join(reasons) + ".", remedy, fields, reimb)


def _r(decision, reason, remediation, fields, reimb):
    return {
        "decision": decision,
        "reason": reason,
        "remediation": remediation,
        "estimated_value": reimb if decision == "auto_accept" else (
            round(reimb * 0.6) if decision == "flag_for_review" else 0),
        "evidence": {f: {"value": v["value"], "confidence": v["confidence"],
                         "source": v["source"], "cite": v["evidence"]} for f, v in fields.items()},
        **{f: fields[f]["value"] for f in fields},
    }