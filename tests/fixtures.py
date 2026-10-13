"""Synthetic fixtures and frozen seed snapshots shared by the unit tests.

`make_*`/`write_report` are fabricated test data kept inside tests/; nothing in
this module is ever published as report data.

`SEED_REPORTS` is a frozen byte-for-byte snapshot of the four original dated
reports under data/reports/. Deterministic tests (exact counts, card keys,
dated facts) build from that snapshot so they stay stable while live
data/reports/ keeps growing; tests that touch live data must derive every
count from the files themselves instead of hardcoding totals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
for entry in (str(ROOT / "scripts"), str(TESTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# Frozen deterministic snapshot: never grows, never changes.
SEED_REPORTS = TESTS_DIR / "seed_reports"
# Live published reports: grows over time; derive counts, never hardcode.
LIVE_REPORTS = ROOT / "data" / "reports"


def report_files(directory: Path) -> list[Path]:
    return sorted(Path(directory).glob("*.json"))


def load_report_data(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_reports(directory: Path) -> list[dict]:
    return [load_report_data(path) for path in report_files(directory)]


def report_dates(directory: Path) -> list[str]:
    return [path.stem for path in report_files(directory)]


def make_salary(**overrides):
    salary = {
        "lower": 120000,
        "upper": 150000,
        "currency": "EUR",
        "annualBase": True,
        "equity": True,
        "quote": "EUR 120,000-150,000 annual base plus equity",
    }
    salary.update(overrides)
    return salary


def make_gates(**overrides):
    gates = {"open": "pass", "location": "pass", "ai": "pass", "pay": "pass", "product": "pass"}
    gates.update(overrides)
    return gates


def make_job(**overrides):
    job = {
        "id": "acme-ai-engineer",
        "company": "Acme AI",
        "title": "Senior AI Engineer",
        "status": "qualified",
        "listingUrl": "https://jobs.example.com/acme/123",
        "applyUrl": "https://jobs.example.com/acme/123/apply",
        "checked": "2026-10-10",
        "posted": None,
        "location": "remote",
        "locationQuote": "Remote; France allowed",
        "salary": make_salary(),
        "reasons": ["Published annual base clears the recorded floor."],
        "gates": make_gates(),
        "product": "Internal AI platform for automation.",
        "financials": "Employer-reported Series B funding.",
        "evidence": [
            {
                "label": "Employer listing",
                "url": "https://jobs.example.com/acme/123",
                "quote": "Recorded note from the listing.",
                "checked": "2026-10-10",
            }
        ],
    }
    job.update(overrides)
    return job


def make_report(date="2026-10-10", jobs=None, **overrides):
    if jobs is None:
        jobs = [make_job()]
    report = {
        "schemaVersion": 1,
        "date": date,
        "summary": "Synthetic fixture report used only by unit tests.",
        "coverage": "Synthetic coverage statement for tests.",
        "criteria": ["Synthetic criterion one.", "Synthetic criterion two."],
        "uncertainty": ["Synthetic uncertainty note."],
        "jobs": jobs,
    }
    report.update(overrides)
    return report


def write_report(directory: Path, report: dict, filename: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = filename or f"{report['date']}.json"
    path = directory / name
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
