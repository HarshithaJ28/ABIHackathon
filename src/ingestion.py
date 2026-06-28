"""P1 — Adaptive data ingestion from the PCC mock API.

Drop-in replacement for the old serial ingestion. Same output contract:
data/raw/{patients,diagnoses,coverage,notes,assessments}.json + id_lookup.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Dict, List

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from config import CONCURRENCY_MAX, FACILITY_IDS
from src.engine import Job, Stats, run_jobs

import asyncio, sys
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
RAW_DIR = "data/raw"


def _render(stats: Stats, progress: Progress, task_id) -> Panel:
    progress.update(task_id, completed=stats.completed, total=max(stats.total, 1))

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="bold cyan")
    table.add_column()
    bar_len = 24
    filled = int(bar_len * min(stats.limit, CONCURRENCY_MAX) / CONCURRENCY_MAX)
    conc_bar = "█" * filled + "░" * (bar_len - filled)

    table.add_row("Concurrency", f"{conc_bar} {stats.limit:>3}  (in-flight {stats.inflight})")
    table.add_row("Throughput", f"{stats.rps:6.1f} req/s")
    table.add_row("Latency p50", f"{stats.p50_ms:6.1f} ms")
    table.add_row("429 absorbed", f"{stats.throttled}  (rate {stats.throttle_rate * 100:4.1f}%)")
    table.add_row("Attempts", f"{stats.attempts}  for {stats.completed} records")
    table.add_row("Failures", f"[red]{stats.failed}[/red]" if stats.failed else "[green]0[/green]")

    return Panel(
        Group(progress, table),
        title="[bold]P1 · Adaptive Ingestion Engine[/bold]",
        border_style="cyan",
    )


async def _ingest_async() -> Dict[str, object]:
    stats = Stats()
    progress = Progress(
        TextColumn("[bold]Fetching"),
        BarColumn(bar_width=40),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    )
    task_id = progress.add_task("ingest", total=1)

    facility_jobs = [
        Job(
            key=facility_id,
            endpoint="/pcc/patients",
            params={"facility_id": facility_id},
            bucket="patients",
        )
        for facility_id in FACILITY_IDS
    ]

    with Live(_render(stats, progress, task_id), refresh_per_second=12) as live:

        async def tick(current_stats: Stats) -> None:
            live.update(_render(current_stats, progress, task_id))

        facility_results = await run_jobs(facility_jobs, stats, on_tick=tick)
        patients: List[dict] = []
        for facility_id in FACILITY_IDS:
            patients.extend(facility_results["patients"].get(facility_id, []))

        jobs: List[Job] = []
        for patient in patients:
            patient_id_str = patient["patient_id"]
            patient_id_int = patient["id"]
            jobs.append(Job(patient_id_str, "/pcc/diagnoses", {"patient_id": patient_id_str}, "diagnoses"))
            jobs.append(Job(patient_id_str, "/pcc/coverage", {"patient_id": patient_id_str}, "coverage"))
            jobs.append(Job(patient_id_int, "/pcc/notes", {"patient_id": patient_id_int}, "notes"))
            jobs.append(Job(patient_id_int, "/pcc/assessments", {"patient_id": patient_id_int}, "assessments"))

        stats2 = Stats()

        async def tick2(current_stats: Stats) -> None:
            live.update(_render(current_stats, progress, task_id))

        results = await run_jobs(jobs, stats2, on_tick=tick2)

    return {
        "patients": patients,
        "diagnoses": results.get("diagnoses", {}),
        "coverage": results.get("coverage", {}),
        "notes": results.get("notes", {}),
        "assessments": results.get("assessments", {}),
    }


def save_raw_data(patients, diagnoses, coverage, notes, assessments) -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    files = {
        "patients.json": patients,
        "diagnoses.json": diagnoses,
        "coverage.json": coverage,
        "notes.json": notes,
        "assessments.json": assessments,
    }

    for filename, data in files.items():
        path = os.path.join(RAW_DIR, filename)
        with open(path, "w") as file_handle:
            serializable = {str(key): value for key, value in data.items()} if isinstance(data, dict) else data
            json.dump(serializable, file_handle, indent=2)
        print(f"  Saved {path}")


def run_ingestion() -> Dict[str, object]:
    """Synchronous entry point — same contract the pipeline already uses."""
    print("=" * 50)
    print("P1 — ADAPTIVE DATA INGESTION")
    print("=" * 50 + "\n")

    data = asyncio.run(_ingest_async())

    print(f"\nTotal patients: {len(data['patients'])}")
    print("Saving raw data...")
    save_raw_data(
        data["patients"],
        data["diagnoses"],
        data["coverage"],
        data["notes"],
        data["assessments"],
    )

    id_lookup: Dict[object, object] = {}
    for patient in data["patients"]:
        id_lookup[patient["patient_id"]] = patient["id"]
        id_lookup[patient["id"]] = patient["patient_id"]
    data["id_lookup"] = id_lookup
    return data


