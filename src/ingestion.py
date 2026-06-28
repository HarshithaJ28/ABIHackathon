"""P1 — Data ingestion from the PCC mock API.

Fetches all 5 raw tables and saves them to data/raw/. Handles the two-ID mapping:

patient_id (str like "FA-001") → /diagnoses, /coverage
id (int like 1) → /notes, /assessments
"""

from typing import Union

import json
import os

from config import FACILITY_IDS
from src.api_client import api_get

RAW_DIR = "data/raw"


def fetch_all_patients() -> list[dict]:
    """Fetch all patients across all 3 facilities."""
    all_patients = []

    for facility_id in FACILITY_IDS:
        print(f"Fetching patients for facility {facility_id}...")
        patients = api_get("/pcc/patients", {"facility_id": facility_id})
        print(f" → {len(patients)} patients")
        all_patients.extend(patients)

    print(f"Total patients: {len(all_patients)}\n")
    return all_patients


def fetch_all_diagnoses(patients: list) -> dict:
    """Fetch diagnoses for every patient.

    Uses patient_id (STRING like "FA-001").

    Returns:
        { "FA-001": [...], "FA-002": [...], ... }
    """
    result = {}
    total = len(patients)

    print(f"Fetching diagnoses for {total} patients...")
    for i, patient in enumerate(patients):
        pid = patient["patient_id"]
        if (i + 1) % 50 == 0:
            print(f" Progress: {i + 1}/{total}")
        result[pid] = api_get("/pcc/diagnoses", {"patient_id": pid})

    print(f"Diagnoses fetched for all {total} patients\n")
    return result


def fetch_all_coverage(patients: list) -> dict:
    """Fetch coverage for every patient.

    Uses patient_id (STRING like "FA-001").

    Returns:
        { "FA-001": [...], "FA-002": [...], ... }
    """
    result = {}
    total = len(patients)

    print(f"Fetching coverage for {total} patients...")
    for i, patient in enumerate(patients):
        pid = patient["patient_id"]
        if (i + 1) % 50 == 0:
            print(f" Progress: {i + 1}/{total}")
        result[pid] = api_get("/pcc/coverage", {"patient_id": pid})

    print(f"Coverage fetched for all {total} patients\n")
    return result


def fetch_all_notes(patients: list) -> dict:
    """Fetch progress notes for every patient.

    Uses id (INTEGER like 1) — NOT patient_id.
    The API param is called patient_id but takes the integer id.

    Returns:
        { 1: [...], 2: [...], ... }
    """
    result = {}
    total = len(patients)

    print(f"Fetching notes for {total} patients...")
    for i, patient in enumerate(patients):
        int_id = patient["id"]
        if (i + 1) % 50 == 0:
            print(f" Progress: {i + 1}/{total}")
        result[int_id] = api_get("/pcc/notes", {"patient_id": int_id})

    print(f"Notes fetched for all {total} patients\n")
    return result


def fetch_all_assessments(patients: list) -> dict:
    """Fetch wound assessments for every patient.

    Uses id (INTEGER like 1) — NOT patient_id.
    The API param is called patient_id but takes the integer id.

    Returns:
        { 1: [...], 2: [...], ... }
    """
    result = {}
    total = len(patients)

    print(f"Fetching assessments for {total} patients...")
    for i, patient in enumerate(patients):
        int_id = patient["id"]
        if (i + 1) % 50 == 0:
            print(f" Progress: {i + 1}/{total}")
        result[int_id] = api_get("/pcc/assessments", {"patient_id": int_id})

    print(f"Assessments fetched for all {total} patients\n")
    return result


def save_raw_data(patients, diagnoses, coverage, notes, assessments):
    """Save all raw API responses to data/raw/ as JSON."""
    os.makedirs(RAW_DIR, exist_ok=True)

    files = {
        "patients.json": patients,
        "diagnoses.json": diagnoses,
        "coverage.json": coverage,
        "notes.json": notes,
        "assessments.json": assessments,
    }

    for filename, data in files.items():
        filepath = os.path.join(RAW_DIR, filename)
        with open(filepath, "w") as f:
            # Convert int keys to strings for JSON serialization
            if isinstance(data, dict):
                serializable = {str(k): v for k, v in data.items()}
            else:
                serializable = data
            json.dump(serializable, f, indent=2)
        print(f" Saved {filepath}")

    print()


def run_ingestion():
    """Main ingestion entry point.

    Fetches all 5 endpoints, saves raw JSON.
    Returns all data for downstream use.
    """
    print("=" * 50)
    print("P1 — DATA INGESTION")
    print("=" * 50 + "\n")

    patients = fetch_all_patients()
    diagnoses = fetch_all_diagnoses(patients)
    coverage = fetch_all_coverage(patients)
    notes = fetch_all_notes(patients)
    assessments = fetch_all_assessments(patients)

    print("Saving raw data...")
    save_raw_data(patients, diagnoses, coverage, notes, assessments)

    # Build the ID lookup so downstream code can map between the two IDs
    # This is the key thing P1 owns — nobody else should have to think about this
    id_lookup = {}
    for p in patients:
        id_lookup[p["patient_id"]] = p["id"]  # "FA-001" → 1
        id_lookup[p["id"]] = p["patient_id"]  # 1 → "FA-001"

    return {
        "patients": patients,
        "diagnoses": diagnoses,
        "coverage": coverage,
        "notes": notes,
        "assessments": assessments,
        "id_lookup": id_lookup,
    }


