"""P3 (partial) — Coverage-based eligibility check.

This is the first gate: does the patient have active Medicare Part B?
Wound-based routing (auto_accept / flag_for_review) comes later after P2
produces extracted wound records.
"""

from typing import Union

from config import TARGET_PAYER_CODE, FACILITY_NAMES


def check_part_b(coverage_records: list) -> dict:
    """Check if any coverage record is active Medicare Part B.

    From the API doc:
    - payer_code "MCB" = Medicare Part B
    - effective_to is null = currently active
    """
    for record in coverage_records:
        is_mcb = record.get("payer_code") == TARGET_PAYER_CODE
        is_active = record.get("effective_to") is None

        if is_mcb and is_active:
            return {
                "has_part_b": True,
                "payer_name": record.get("payer_name"),
                "effective_from": record.get("effective_from"),
            }

    return {"has_part_b": False, "payer_name": None, "effective_from": None}


def build_eligibility_table(patients: list, coverage: dict) -> list:
    """Produces one row per patient with Part B status and initial routing.

    Patients WITHOUT Part B → reject (done, won't be processed further)
    Patients WITH Part B → pending_wound_review (P2 still needs to extract
    wound data)
    """
    results = []

    for patient in patients:
        pid = patient["patient_id"]
        coverage_records = coverage.get(pid, [])
        partb = check_part_b(coverage_records)

        results.append(
            {
                "id": patient["id"],
                "patient_id": pid,
                "facility_id": patient["facility_id"],
                "facility": FACILITY_NAMES.get(patient["facility_id"], "Unknown"),
                "first_name": patient.get("first_name"),
                "last_name": patient.get("last_name"),
                "name": f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip(),
                "birth_date": patient.get("birth_date"),
                "gender": patient.get("gender"),
                "primary_payer_code": patient.get("primary_payer_code"),
                "has_part_b": partb["has_part_b"],
                "decision": (
                    "pending_wound_review"
                    if partb["has_part_b"]
                    else "reject"
                ),
                "reason": (
                    "Active Medicare Part B — proceed to wound data extraction"
                    if partb["has_part_b"]
                    else "No active Medicare Part B coverage"
                ),
            }
        )

    return results