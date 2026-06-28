"""Main entry point — runs the full pipeline.

Currently: P1 (ingestion) + initial P3 (coverage check).
"""

import csv
import json
import os
import sys

# Add parent directory to path for config imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion import run_ingestion
from src.eligibility import build_eligibility_table


def print_summary(eligibility: list[dict]):
    total = len(eligibility)
    partb = [e for e in eligibility if e["has_part_b"]]
    rejected = [e for e in eligibility if not e["has_part_b"]]

    print("=" * 50)
    print("PIPELINE SUMMARY")
    print("=" * 50)
    print(f"Total patients: {total}")
    print(f"Medicare Part B: {len(partb)} ({len(partb) / total * 100:.1f}%)")
    print(f"Rejected (no Part B): {len(rejected)} ({len(rejected) / total * 100:.1f}%)")

    print("\nBy facility:")
    for fid in [101, 102, 103]:
        fac = [e for e in eligibility if e["facility_id"] == fid]
        fac_partb = [e for e in fac if e["has_part_b"]]
        print(f" Facility {fid}: {len(fac)} patients, {len(fac_partb)} with Part B")

    print("\nBy payer code:")
    codes = {}
    for e in eligibility:
        code = e.get("primary_payer_code", "Unknown")
        codes[code] = codes.get(code, 0) + 1
    for code, count in sorted(codes.items(), key=lambda x: -x[1]):
        print(f" {code}: {count}")

    print("\nPart B patients ready for wound review:")
    for e in eligibility:
        if e["has_part_b"]:
            print(f" {e['patient_id']} — {e['name']}")

    print("=" * 50)


def save_output(eligibility: list[dict]):
    os.makedirs("data/output", exist_ok=True)

    # CSV
    csv_path = "data/output/eligibility_output.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=eligibility[0].keys())
        writer.writeheader()
        writer.writerows(eligibility)

    # JSON
    json_path = "data/output/eligibility_output.json"
    with open(json_path, "w") as f:
        json.dump(eligibility, f, indent=2)

    print(f"\nOutput saved:")
    print(f" {csv_path}")
    print(f" {json_path}\n")


def main():
    print("\n" + "=" * 50)
    print("WOUND CARE BILLING PIPELINE")
    print("=" * 50 + "\n")

    # P1: Ingest all raw data
    data = run_ingestion()

    # P3 (partial): Coverage-based eligibility
    print("=" * 50)
    print("ELIGIBILITY CHECK — Medicare Part B")
    print("=" * 50 + "\n")

    eligibility = build_eligibility_table(data["patients"], data["coverage"])
    save_output(eligibility)
    print_summary(eligibility)


if __name__ == "__main__":
    main()

