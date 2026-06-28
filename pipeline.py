"""Main entry point — runs the full pipeline.

P1 (adaptive ingestion) -> P2 (grounded extraction + fusion) -> P3 (deterministic
decision lattice with remediation). AI, if wired in, is advisory-only.
"""

import csv
import json
import os
import sys
from collections import Counter

# Add parent directory to path for config imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import FACILITY_IDS, FACILITY_NAMES
from src.ingestion import run_ingestion
from src.decision import run_decision


# ── reporting ──────────────────────────────────────────────────────────────
def print_summary(rows: list[dict]):
    total = len(rows)
    buckets = Counter(r["decision"] for r in rows)
    recoverable = sum(r["estimated_value"] for r in rows if r["decision"] == "auto_accept")
    pipeline_value = sum(r["estimated_value"] for r in rows)

    print("\n" + "=" * 56)
    print("DECISION SUMMARY")
    print("=" * 56)
    print(f"  Total patients   : {total}")
    print(f"  Recoverable now  : ${recoverable:,}  (auto-accept)")
    print(f"  Pipeline value   : ${pipeline_value:,}  (incl. review-weighted)")
    print()
    print(f"  auto_accept      : {buckets['auto_accept']:>3}  ({_pct(buckets['auto_accept'], total)})")
    print(f"  flag_for_review  : {buckets['flag_for_review']:>3}  ({_pct(buckets['flag_for_review'], total)})")
    print(f"  reject           : {buckets['reject']:>3}  ({_pct(buckets['reject'], total)})")
    print(f"  human workload   : {buckets['flag_for_review']} / {total} patients need eyes")

    print("\nBy facility:")
    for fid in FACILITY_IDS:
        name = FACILITY_NAMES.get(fid, f"Facility {fid}")
        fac = [r for r in rows if r["facility"] == name]
        acc = sum(1 for r in fac if r["decision"] == "auto_accept")
        print(f"  {name:<12}: {len(fac):>3} patients, {acc} auto-accepted")

    print("\nTop billable opportunities (EV-ranked):")
    for r in [x for x in rows if x["decision"] == "auto_accept"][:8]:
        print(f"  ${r['estimated_value']:>4}  {r['patient_id']:<8} {r['name']:<22} {r.get('wound_type') or '-'}")

    print("\nTop remediation reasons among flagged:")
    reasons = Counter(rem for r in rows if r["decision"] == "flag_for_review" for rem in r["remediation"])
    for reason, n in reasons.most_common(5):
        print(f"  {n:>3}x  {reason}")

    print("=" * 56)


def _pct(n: int, total: int) -> str:
    return f"{(n / total * 100):.1f}%" if total else "0.0%"


# ── persistence ──────────────────────────────────────────────────────────────
def _flatten(row: dict) -> dict:
    """CSV-safe view: drop nested objects, serialize lists to strings."""
    return {
        "patient_id": row["patient_id"],
        "id": row["id"],
        "name": row["name"],
        "facility": row["facility"],
        "decision": row["decision"],
        "estimated_value": row["estimated_value"],
        "icd10": row.get("icd10") or "",
        "wound_type": row.get("wound_type") or "",
        "location": row.get("location") or "",
        "length_cm": row.get("length_cm") or "",
        "width_cm": row.get("width_cm") or "",
        "depth_cm": row.get("depth_cm") or "",
        "drainage_amount": row.get("drainage_amount") or "",
        "reason": row["reason"],
        "remediation": " | ".join(row["remediation"]),
    }


def save_output(rows: list[dict]):
    os.makedirs("data/output", exist_ok=True)

    # JSON — full audit trail (evidence spans + remediation preserved)
    json_path = "data/output/decision_output.json"
    with open(json_path, "w") as f:
        json.dump(rows, f, indent=2)

    # CSV — flattened, biller-friendly view
    csv_path = "data/output/decision_output.csv"
    flat = [_flatten(r) for r in rows]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=flat[0].keys())
        writer.writeheader()
        writer.writerows(flat)

    print("\nOutput saved:")
    print(f"  {json_path}")
    print(f"  {csv_path}")


# ── entry point ──────────────────────────────────────────────────────────────
def main():
    print("\n" + "=" * 56)
    print("WOUND CARE BILLING PIPELINE")
    print("=" * 56 + "\n")

    # P1: adaptive ingestion of all 5 raw tables
    data = run_ingestion()

    # P2 + P3: grounded extraction -> fusion -> deterministic decision
    # Pass llm_call=<your fn> to enable advisory, grounded gap-filling.
    rows = run_decision(data, llm_call=None)

    save_output(rows)
    print_summary(rows)
    return rows


if __name__ == "__main__":
    main()