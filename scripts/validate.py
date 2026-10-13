#!/usr/bin/env python3
"""Strict validator for Job Alerts report JSON files.

Usage:
    python scripts/validate.py [--reports DIR]

Exit codes: 0 = all reports valid, 1 = validation or input failure.

The rules implemented here are documented in SCHEMA.md (report schema
version 1, camelCase contract as used by the 2026-10-10/11/12 seed reports).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

SCHEMA_VERSION = 1

TOP_REQUIRED = {
    "schemaVersion",
    "date",
    "summary",
    "coverage",
    "criteria",
    "uncertainty",
    "jobs",
}
TOP_OPTIONAL = {"requestedCount", "decision"}

JOB_KEYS = {
    "id",
    "company",
    "title",
    "status",
    "listingUrl",
    "applyUrl",
    "checked",
    "posted",
    "location",
    "locationQuote",
    "salary",
    "reasons",
    "gates",
    "product",
    "financials",
    "evidence",
}

SALARY_KEYS = {"lower", "upper", "currency", "annualBase", "equity", "quote"}
GATE_KEYS = {"open", "location", "ai", "pay", "product"}
EVIDENCE_KEYS = {"label", "url", "quote", "checked"}

JOB_STATUSES = {"qualified", "rejected", "near-miss"}
WORK_MODES = {"remote", "hybrid", "on-site", "unknown"}
GATE_VALUES = {"pass", "fail", "unknown"}
CURRENCIES = {"EUR", "USD", "GBP"}

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
LOCAL_PATH_RE = re.compile(
    r"(?:^|[\s\"'(])(?:/home/|/Users/|[A-Za-z]:\\\\)[^\s\"']+",
)
FILE_URL_RE = re.compile(r"file://", re.IGNORECASE)

PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".home.arpa")


class _Ctx:
    """Collects descriptive error strings for one report."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.errors: list[str] = []

    def fail(self, path: str, message: str) -> None:
        self.errors.append(f"{self.source}: {path}: {message}")


def _check_keys(
    ctx: _Ctx,
    path: str,
    obj: dict,
    required: set[str],
    optional: set[str] | frozenset = frozenset(),
) -> bool:
    """Check for unknown and missing fields. Returns True only when clean."""
    allowed = set(required) | set(optional)
    unknown = sorted(set(obj) - allowed)
    if unknown:
        ctx.fail(path, f"unknown field(s) rejected: {', '.join(unknown)}")
    missing = sorted(required - set(obj))
    if missing:
        ctx.fail(path, f"missing required field(s): {', '.join(missing)}")
    return not unknown and not missing


def _require_str(ctx: _Ctx, path: str, value, allow_empty: bool = False) -> bool:
    if not isinstance(value, str):
        ctx.fail(path, f"expected string, got {type(value).__name__}")
        return False
    if not allow_empty and not value.strip():
        ctx.fail(path, "must be a non-empty string")
        return False
    return True


def _check_iso_date(ctx: _Ctx, path: str, value) -> bool:
    if not isinstance(value, str):
        ctx.fail(path, f"expected ISO date string, got {type(value).__name__}")
        return False
    if not ISO_DATE_RE.match(value):
        ctx.fail(path, f"{value!r} is not an ISO YYYY-MM-DD date")
        return False
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        ctx.fail(path, f"{value!r} is not a valid calendar date")
        return False
    return True


def _host_is_private(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(PRIVATE_HOST_SUFFIXES):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
    )


def _check_url(ctx: _Ctx, path: str, value, *, require_https: bool) -> bool:
    if not isinstance(value, str):
        ctx.fail(path, f"expected URL string, got {type(value).__name__}")
        return False
    parsed = urlparse(value)
    allowed_schemes = ("https",) if require_https else ("http", "https")
    if parsed.scheme not in allowed_schemes:
        want = "HTTPS" if require_https else "http(s)"
        ctx.fail(path, f"{value!r} must use {want}")
        return False
    if not parsed.hostname:
        ctx.fail(path, f"{value!r} has no host")
        return False
    if parsed.username or parsed.password:
        ctx.fail(path, f"{value!r} must not embed credentials")
        return False
    if _host_is_private(parsed.hostname):
        ctx.fail(path, f"{value!r} points at a non-public host")
        return False
    return True


def _check_private_content(ctx: _Ctx, path: str, value: str) -> None:
    if EMAIL_RE.search(value):
        ctx.fail(path, "contact details (email address) are not allowed in public reports")
    if LOCAL_PATH_RE.search(value):
        ctx.fail(path, "local filesystem paths are not allowed in public reports")
    if FILE_URL_RE.search(value):
        ctx.fail(path, "file:// URLs are not allowed in public reports")


def _check_str_list(ctx: _Ctx, path: str, value, allow_empty: bool = True) -> None:
    if not isinstance(value, list):
        ctx.fail(path, f"expected array of strings, got {type(value).__name__}")
        return
    if not allow_empty and not value:
        ctx.fail(path, "must contain at least one entry")
        return
    for i, item in enumerate(value):
        if _require_str(ctx, f"{path}[{i}]", item):
            _check_private_content(ctx, f"{path}[{i}]", item)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_salary(ctx: _Ctx, path: str, salary) -> None:
    if not isinstance(salary, dict):
        ctx.fail(path, f"expected object, got {type(salary).__name__}")
        return
    if not _check_keys(ctx, path, salary, SALARY_KEYS):
        return
    _require_str(ctx, f"{path}.quote", salary["quote"])
    if isinstance(salary["quote"], str):
        _check_private_content(ctx, f"{path}.quote", salary["quote"])

    currency = salary["currency"]
    if currency is not None and currency not in CURRENCIES:
        ctx.fail(
            f"{path}.currency",
            f"{currency!r} must be one of {sorted(CURRENCIES)} or null",
        )

    for key in ("lower", "upper"):
        number = salary[key]
        if number is None:
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            ctx.fail(f"{path}.{key}", f"expected number or null, got {type(number).__name__}")
        elif number < 0:
            ctx.fail(f"{path}.{key}", "must not be negative")

    for key in ("annualBase", "equity"):
        flag = salary[key]
        if flag is not None and not isinstance(flag, bool):
            ctx.fail(
                f"{path}.{key}",
                f"expected boolean or null, got {type(flag).__name__}",
            )

    low, high = salary["lower"], salary["upper"]
    if _is_number(low) and _is_number(high) and low > high:
        ctx.fail(path, f"lower ({low}) must not exceed upper ({high})")

    if (
        currency is None
        and low is None
        and high is None
        and isinstance(salary["quote"], str)
        and re.search(r"\d", salary["quote"])
    ):
        # Numeric pay wording without numeric fields is fine; the quote carries
        # the figures. Unknown pay stays null, never 0.
        pass


def _check_gates(ctx: _Ctx, path: str, gates) -> None:
    if not isinstance(gates, dict):
        ctx.fail(path, f"expected object, got {type(gates).__name__}")
        return
    if not _check_keys(ctx, path, gates, GATE_KEYS):
        return
    for key in sorted(GATE_KEYS):
        value = gates[key]
        if value not in GATE_VALUES:
            ctx.fail(
                f"{path}.{key}",
                f"{value!r} must be one of {sorted(GATE_VALUES)}",
            )


def _check_evidence(ctx: _Ctx, path: str, evidence) -> None:
    if not isinstance(evidence, list):
        ctx.fail(path, f"expected array of objects, got {type(evidence).__name__}")
        return
    for i, item in enumerate(evidence):
        item_path = f"{path}[{i}]"
        if not isinstance(item, dict):
            ctx.fail(item_path, f"expected object, got {type(item).__name__}")
            continue
        if not _check_keys(ctx, item_path, item, EVIDENCE_KEYS):
            continue
        _require_str(ctx, f"{item_path}.label", item["label"])
        _require_str(ctx, f"{item_path}.quote", item["quote"])
        for key in ("label", "quote"):
            if isinstance(item[key], str):
                _check_private_content(ctx, f"{item_path}.{key}", item[key])
        _check_url(ctx, f"{item_path}.url", item["url"], require_https=False)
        _check_iso_date(ctx, f"{item_path}.checked", item["checked"])


def _gates_all_pass(gates) -> bool:
    return (
        isinstance(gates, dict)
        and set(gates) == GATE_KEYS
        and all(gates[key] == "pass" for key in GATE_KEYS)
    )


def _check_qualification(ctx: _Ctx, path: str, job: dict) -> None:
    """Qualified entries must pass every stored gate and be fully sourced.

    Historic eligibility is recorded in the report itself: this check enforces
    the stored gate results and source completeness, and deliberately does not
    re-apply today's numeric pay thresholds or currency rules.
    """
    gates = job["gates"]
    if isinstance(gates, dict) and set(gates) == GATE_KEYS:
        if not _gates_all_pass(gates):
            failed = sorted(k for k in GATE_KEYS if gates[k] != "pass")
            ctx.fail(
                f"{path}.gates",
                "qualified entries must have every stored gate set to 'pass' "
                f"(not pass: {', '.join(failed)})",
            )

    evidence = job["evidence"]
    if isinstance(evidence, list) and not evidence:
        ctx.fail(f"{path}.evidence", "qualified entries need at least one evidence source")

    for key in ("product", "financials"):
        value = job[key]
        if not isinstance(value, str) or not value.strip():
            ctx.fail(
                f"{path}.{key}",
                "qualified entries need source completeness: a non-null "
                f"recorded {key} note",
            )


def _check_job(ctx: _Ctx, path: str, job, seen_ids: set[str]) -> None:
    if not isinstance(job, dict):
        ctx.fail(path, f"expected object, got {type(job).__name__}")
        return
    if not _check_keys(ctx, path, job, JOB_KEYS):
        return

    if _require_str(ctx, f"{path}.id", job["id"]):
        if not SLUG_RE.match(job["id"]):
            ctx.fail(
                f"{path}.id",
                f"{job['id']!r} must be a lowercase slug (a-z, 0-9, hyphens)",
            )
        elif job["id"] in seen_ids:
            ctx.fail(f"{path}.id", f"duplicate job id {job['id']!r} in this report")
        else:
            seen_ids.add(job["id"])

    for key in ("company", "title", "locationQuote"):
        if _require_str(ctx, f"{path}.{key}", job[key]):
            _check_private_content(ctx, f"{path}.{key}", job[key])

    location = job["location"]
    if location not in WORK_MODES:
        ctx.fail(
            f"{path}.location",
            f"{location!r} must be one of {sorted(WORK_MODES)}",
        )

    status = job["status"]
    if status not in JOB_STATUSES:
        ctx.fail(
            f"{path}.status",
            f"{status!r} must be one of {sorted(JOB_STATUSES)}",
        )

    _check_url(ctx, f"{path}.listingUrl", job["listingUrl"], require_https=True)
    apply_url = job["applyUrl"]
    if apply_url is not None:
        _check_url(ctx, f"{path}.applyUrl", apply_url, require_https=True)

    _check_iso_date(ctx, f"{path}.checked", job["checked"])
    posted = job["posted"]
    if posted is not None:
        _check_iso_date(ctx, f"{path}.posted", posted)

    _check_str_list(ctx, f"{path}.reasons", job["reasons"])

    for key in ("product", "financials"):
        value = job[key]
        if value is not None:
            if _require_str(ctx, f"{path}.{key}", value):
                _check_private_content(ctx, f"{path}.{key}", value)

    _check_salary(ctx, f"{path}.salary", job["salary"])
    _check_gates(ctx, f"{path}.gates", job["gates"])
    _check_evidence(ctx, f"{path}.evidence", job["evidence"])

    gates = job["gates"]
    if status == "qualified":
        _check_qualification(ctx, path, job)
    elif isinstance(gates, dict) and set(gates) == GATE_KEYS:
        if _gates_all_pass(gates):
            ctx.fail(
                f"{path}.gates",
                f"{status!r} entries must record at least one gate that is not "
                "'pass' (all stored gates are pass)",
            )


def validate_report(data, source: str = "<report>", filename: str | None = None) -> list[str]:
    """Validate one decoded report. Returns a list of descriptive errors."""
    ctx = _Ctx(source)

    if not isinstance(data, dict):
        ctx.fail("$", f"report must be a JSON object, got {type(data).__name__}")
        return ctx.errors

    if not _check_keys(ctx, "$", data, TOP_REQUIRED, TOP_OPTIONAL):
        return ctx.errors

    if data["schemaVersion"] != SCHEMA_VERSION or isinstance(data["schemaVersion"], bool):
        ctx.fail("$.schemaVersion", f"must be {SCHEMA_VERSION}, got {data['schemaVersion']!r}")

    date_ok = _check_iso_date(ctx, "$.date", data["date"])
    if date_ok and filename is not None:
        expected = f"{data['date']}.json"
        if filename != expected:
            ctx.fail("$.date", f"file name {filename!r} must match report date ({expected})")

    if "requestedCount" in data:
        count = data["requestedCount"]
        if isinstance(count, bool) or not isinstance(count, int):
            ctx.fail("$.requestedCount", f"expected integer, got {type(count).__name__}")
        elif count < 0:
            ctx.fail("$.requestedCount", "must not be negative")

    if "decision" in data:
        if _require_str(ctx, "$.decision", data["decision"]):
            _check_private_content(ctx, "$.decision", data["decision"])

    if _require_str(ctx, "$.summary", data["summary"]):
        _check_private_content(ctx, "$.summary", data["summary"])
    if _require_str(ctx, "$.coverage", data["coverage"]):
        _check_private_content(ctx, "$.coverage", data["coverage"])

    _check_str_list(ctx, "$.criteria", data["criteria"], allow_empty=False)
    _check_str_list(ctx, "$.uncertainty", data["uncertainty"], allow_empty=False)

    jobs = data["jobs"]
    if not isinstance(jobs, list):
        ctx.fail("$.jobs", f"expected array, got {type(jobs).__name__}")
    else:
        seen_ids: set[str] = set()
        for i, job in enumerate(jobs):
            _check_job(ctx, f"$.jobs[{i}]", job, seen_ids)

    return ctx.errors


def load_and_validate(path: Path) -> list[str]:
    """Read one report file and validate it; returns descriptive errors."""
    source = str(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{source}: cannot read file: {exc}"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"{source}: invalid JSON: {exc}"]
    return validate_report(data, source=source, filename=path.name)


def discover_reports(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"report directory not found: {directory}")
    return sorted(directory.glob("*.json"))


def validate_directory(directory: Path) -> tuple[list[Path], list[str]]:
    """Validate every report in a directory.

    Returns (valid_paths, errors). Fails if the directory has no reports.
    """
    paths = discover_reports(directory)
    if not paths:
        return [], [f"{directory}: no report JSON files found"]
    valid: list[Path] = []
    errors: list[str] = []
    for path in paths:
        file_errors = load_and_validate(path)
        if file_errors:
            errors.extend(file_errors)
        else:
            valid.append(path)
    return valid, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Job Alerts report JSON files.")
    parser.add_argument(
        "--reports",
        default="data/reports",
        help="directory containing YYYY-MM-DD.json reports (default: data/reports)",
    )
    args = parser.parse_args(argv)

    directory = Path(args.reports)
    try:
        paths = discover_reports(directory)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not paths:
        print(f"ERROR: {directory}: no report JSON files found", file=sys.stderr)
        return 1

    total_errors = 0
    for path in paths:
        errors = load_and_validate(path)
        if errors:
            total_errors += len(errors)
            for error in errors:
                print(f"FAIL {error}", file=sys.stderr)
        else:
            print(f"OK   {path}")

    if total_errors:
        print(f"\n{total_errors} error(s) in {len(paths)} report file(s)", file=sys.stderr)
        return 1
    print(f"\nAll {len(paths)} report file(s) valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
