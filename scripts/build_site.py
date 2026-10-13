#!/usr/bin/env python3
"""Standalone static renderer and validator for the Job Alerts site.

Standard library only; no third-party or network dependency.

Usage:
    python scripts/build_site.py [--reports DIR] [--output DIR]
                                 [--base-url URL] [--validate-only]

Exit codes:
    0 = success (validated, or built)
    1 = validation or build failure (nothing is written to --output)

Contract: report schema version 1 (camelCase), documented in SCHEMA.md and
assets/report.schema.json. Every report is validated before a single file is
written, so an invalid report can never replace good output. Each validated
report is also exported verbatim to reports/YYYY-MM-DD.json next to its HTML
page. --output is guarded (see output_guard_error): symlinked paths, the
repository root, .git directories, the input data/assets/scripts/tests
directories and existing folders without the renderer ownership marker are
refused before anything is written or deleted.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html as _html
import ipaddress
import json
import os
import secrets
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

SCHEMA_VERSION = 1

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORTS = REPO_ROOT / "data" / "reports"
DEFAULT_OUTPUT = REPO_ROOT / "dist"
ASSETS_DIR = REPO_ROOT / "assets"

# --------------------------------------------------------------------------
# Validation (report schema version 1)
# --------------------------------------------------------------------------

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
LOCAL_PATH_RE = re.compile(r"(?:^|[\s\"'(])(?:/home/|/Users/|[A-Za-z]:\\\\)[^\s\"']+")
FILE_URL_RE = re.compile(r"file://", re.IGNORECASE)
PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".home.arpa")


class _Ctx:
    def __init__(self, source: str) -> None:
        self.source = source
        self.errors: list[str] = []

    def fail(self, path: str, message: str) -> None:
        self.errors.append(f"{self.source}: {path}: {message}")


def _check_keys(ctx, path, obj, required, optional=frozenset()) -> bool:
    allowed = set(required) | set(optional)
    unknown = sorted(set(obj) - allowed)
    if unknown:
        ctx.fail(path, f"unknown field(s) rejected: {', '.join(unknown)}")
    missing = sorted(required - set(obj))
    if missing:
        ctx.fail(path, f"missing required field(s): {', '.join(missing)}")
    return not unknown and not missing


def _require_str(ctx, path, value, allow_empty: bool = False) -> bool:
    if not isinstance(value, str):
        ctx.fail(path, f"expected string, got {type(value).__name__}")
        return False
    if not allow_empty and not value.strip():
        ctx.fail(path, "must be a non-empty string")
        return False
    return True


def _check_iso_date(ctx, path, value) -> bool:
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


def _check_url(ctx, path, value, *, require_https: bool) -> bool:
    if not isinstance(value, str):
        ctx.fail(path, f"expected URL string, got {type(value).__name__}")
        return False
    parsed = urlparse(value)
    allowed = ("https",) if require_https else ("http", "https")
    if parsed.scheme not in allowed:
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


def _check_private_content(ctx, path, value: str) -> None:
    if EMAIL_RE.search(value):
        ctx.fail(path, "contact details (email address) are not allowed in public reports")
    if LOCAL_PATH_RE.search(value):
        ctx.fail(path, "local filesystem paths are not allowed in public reports")
    if FILE_URL_RE.search(value):
        ctx.fail(path, "file:// URLs are not allowed in public reports")


def _check_str_list(ctx, path, value, allow_empty: bool = True) -> None:
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


def _check_salary(ctx, path, salary) -> None:
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
        ctx.fail(f"{path}.currency", f"{currency!r} must be one of {sorted(CURRENCIES)} or null")
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
            ctx.fail(f"{path}.{key}", f"expected boolean or null, got {type(flag).__name__}")
    low, high = salary["lower"], salary["upper"]
    if _is_number(low) and _is_number(high) and low > high:
        ctx.fail(path, f"lower ({low}) must not exceed upper ({high})")


def _check_gates(ctx, path, gates) -> None:
    if not isinstance(gates, dict):
        ctx.fail(path, f"expected object, got {type(gates).__name__}")
        return
    if not _check_keys(ctx, path, gates, GATE_KEYS):
        return
    for key in sorted(GATE_KEYS):
        if gates[key] not in GATE_VALUES:
            ctx.fail(
                f"{path}.{key}",
                f"{gates[key]!r} must be one of {sorted(GATE_VALUES)}",
            )


def _check_evidence(ctx, path, evidence) -> None:
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
        and all(gates[k] == "pass" for k in GATE_KEYS)
    )


def _check_qualification(ctx, path, job) -> None:
    """Qualified entries must carry stored pass gates and complete sources.

    Historic eligibility lives in the report: the stored gate results and
    source completeness are enforced, and today's numeric pay thresholds or
    currency rules are deliberately not re-applied at publish time.
    """
    gates = job["gates"]
    if isinstance(gates, dict) and set(gates) == GATE_KEYS and not _gates_all_pass(gates):
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
                f"qualified entries need source completeness: a non-null recorded {key} note",
            )


def _check_job(ctx, path, job, seen_ids: set[str]) -> None:
    if not isinstance(job, dict):
        ctx.fail(path, f"expected object, got {type(job).__name__}")
        return
    if not _check_keys(ctx, path, job, JOB_KEYS):
        return
    if _require_str(ctx, f"{path}.id", job["id"]):
        if not SLUG_RE.match(job["id"]):
            ctx.fail(f"{path}.id", f"{job['id']!r} must be a lowercase slug (a-z, 0-9, hyphens)")
        elif job["id"] in seen_ids:
            ctx.fail(f"{path}.id", f"duplicate job id {job['id']!r} in this report")
        else:
            seen_ids.add(job["id"])
    for key in ("company", "title", "locationQuote"):
        if _require_str(ctx, f"{path}.{key}", job[key]):
            _check_private_content(ctx, f"{path}.{key}", job[key])
    if job["location"] not in WORK_MODES:
        ctx.fail(
            f"{path}.location",
            f"{job['location']!r} must be one of {sorted(WORK_MODES)}",
        )
    if job["status"] not in JOB_STATUSES:
        ctx.fail(f"{path}.status", f"{job['status']!r} must be one of {sorted(JOB_STATUSES)}")
    _check_url(ctx, f"{path}.listingUrl", job["listingUrl"], require_https=True)
    if job["applyUrl"] is not None:
        _check_url(ctx, f"{path}.applyUrl", job["applyUrl"], require_https=True)
    _check_iso_date(ctx, f"{path}.checked", job["checked"])
    if job["posted"] is not None:
        _check_iso_date(ctx, f"{path}.posted", job["posted"])
    _check_str_list(ctx, f"{path}.reasons", job["reasons"])
    for key in ("product", "financials"):
        value = job[key]
        if value is not None and _require_str(ctx, f"{path}.{key}", value):
            _check_private_content(ctx, f"{path}.{key}", value)
    _check_salary(ctx, f"{path}.salary", job["salary"])
    _check_gates(ctx, f"{path}.gates", job["gates"])
    _check_evidence(ctx, f"{path}.evidence", job["evidence"])

    gates = job["gates"]
    if job["status"] == "qualified":
        _check_qualification(ctx, path, job)
    elif isinstance(gates, dict) and set(gates) == GATE_KEYS and _gates_all_pass(gates):
        ctx.fail(
            f"{path}.gates",
            f"{job['status']!r} entries must record at least one gate that is not "
            "'pass' (all stored gates are pass)",
        )


def validate_report(data, source: str = "<report>", filename: str | None = None) -> list[str]:
    ctx = _Ctx(source)
    if not isinstance(data, dict):
        ctx.fail("$", f"report must be a JSON object, got {type(data).__name__}")
        return ctx.errors
    if not _check_keys(ctx, "$", data, TOP_REQUIRED, TOP_OPTIONAL):
        return ctx.errors
    if data["schemaVersion"] != SCHEMA_VERSION or isinstance(data["schemaVersion"], bool):
        ctx.fail("$.schemaVersion", f"must be {SCHEMA_VERSION}, got {data['schemaVersion']!r}")
    date_ok = _check_iso_date(ctx, "$.date", data["date"])
    if date_ok and filename is not None and filename != f"{data['date']}.json":
        ctx.fail("$.date", f"file name {filename!r} must match report date ({data['date']}.json)")
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
        seen: set[str] = set()
        for i, job in enumerate(jobs):
            _check_job(ctx, f"$.jobs[{i}]", job, seen)
    return ctx.errors


def load_report(path: Path) -> tuple[dict | None, list[str]]:
    source = str(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"{source}: cannot read file: {exc}"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"{source}: invalid JSON: {exc}"]
    errors = validate_report(data, source=source, filename=path.name)
    if errors:
        return None, errors
    return data, []


def discover_reports(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"report directory not found: {directory}")
    return sorted(directory.glob("*.json"))


# --------------------------------------------------------------------------
# Output safety guard
# --------------------------------------------------------------------------


# Ownership marker written into the root of every rendered output directory.
# An existing --output is only ever replaced when it carries this marker (or
# is an exact legacy generated layout, see _legacy_layout_error).
OWNER_MARKER = ".job-alerts-site"
OWNER_MARKER_TEXT = (
    json.dumps(
        {"generator": "job-alerts scripts/build_site.py", "markerVersion": 1},
        sort_keys=True,
    )
    + "\n"
)
LEGACY_TOP_LEVEL = {"index.html", "archive.html", "reports", "assets"}
LEGACY_REPORT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.(?:html|json)$")


def _is_same_or_ancestor(candidate: Path, other: Path) -> bool:
    return candidate == other or candidate in other.parents


def _symlink_component(path: Path) -> Path | None:
    """Return the first symlink among the path's components, without resolving."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return current
    return None


def _has_owner_marker(out: Path) -> bool:
    marker = out / OWNER_MARKER
    if marker.is_symlink() or not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data == json.loads(OWNER_MARKER_TEXT)


def _legacy_layout_error(out: Path) -> str | None:
    """Explain why `out` is not an exact pre-marker generated layout (or None).

    The legacy builder wrote only index.html, archive.html, reports/ (dated
    .html/.json files) and assets/ (a flat copy of this repository's assets).
    Anything else — extra files, nested folders, symlinks — means the folder
    is not demonstrably ours, so it must not be deleted.
    """
    asset_names = {p.name for p in ASSETS_DIR.iterdir()} if ASSETS_DIR.is_dir() else set()
    try:
        top = {entry.name: entry for entry in out.iterdir()}
    except OSError as exc:
        return f"cannot list it ({exc})"
    unrelated = sorted(set(top) - LEGACY_TOP_LEVEL)
    if unrelated:
        return f"unrelated entries: {', '.join(unrelated[:5])}"
    for name in ("index.html", "archive.html"):
        entry = top.get(name)
        if entry is None or entry.is_symlink() or not entry.is_file():
            return f"missing generated file {name}"
    try:
        index_text = top["index.html"].read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"cannot read index.html ({exc})"
    if 'id="site-data"' not in index_text:
        return "index.html was not produced by this renderer"
    for name, allowed in (("reports", None), ("assets", asset_names)):
        folder = top.get(name)
        if folder is None:
            continue
        if folder.is_symlink() or not folder.is_dir():
            return f"{name} is not a plain directory"
        for child in folder.iterdir():
            if child.is_symlink() or not child.is_file():
                return f"{name}/{child.name} is not a plain generated file"
            ok = LEGACY_REPORT_RE.match(child.name) if allowed is None else child.name in allowed
            if not ok:
                return f"unrelated file {name}/{child.name}"
    return None


def output_guard_error(output_dir: Path, reports_dir: Path) -> str | None:
    """Refuse --output paths whose replacement could destroy anything not ours.

    Checked before anything is written or deleted:
    - no component of the (absolute, unresolved) output path may be a symlink;
    - the repository root and its ancestors, any directory containing a
      `.git` entry (or nested inside one), the input data/assets/scripts and
      tests directories and the report input directory are refused —
      including overlapping paths in either direction;
    - an existing output must be a directory that is empty, carries the
      renderer ownership marker, or is an exact legacy generated layout with
      no unrelated files. Unowned folders are refused and left untouched.
    A fresh, absent output path is allowed.
    """
    raw = Path(os.path.abspath(Path(output_dir)))
    link = _symlink_component(raw)
    if link is not None:
        return (
            f"refusing --output {raw}: path component {link} is a symlink; "
            "pass a real directory path"
        )

    out = raw.resolve()
    repo = REPO_ROOT.resolve()
    assets = ASSETS_DIR.resolve()
    reports = Path(reports_dir).resolve()
    source_dirs = [
        repo / "data",
        repo / "design",
        assets,
        repo / "scripts",
        repo / "tests",
        reports,
    ]

    if ".git" in out.parts:
        return f"refusing --output {out}: it is inside a .git directory"
    if (out / ".git").exists():
        return f"refusing --output {out}: it contains a .git directory"
    if _is_same_or_ancestor(out, repo):
        return f"refusing --output {out}: it is the repository root or its ancestor"
    for source in source_dirs:
        source = source.resolve()
        if _is_same_or_ancestor(out, source):
            return (
                f"refusing --output {out}: it is the same as or an ancestor of "
                f"source directory {source}"
            )
        if _is_same_or_ancestor(source, out):
            return f"refusing --output {out}: it overlaps source directory {source}"

    if not out.exists():
        return None
    if not out.is_dir():
        return f"refusing --output {out}: it exists and is not a directory"
    if _has_owner_marker(out):
        return None
    try:
        empty = not any(out.iterdir())
    except OSError as exc:
        return f"refusing --output {out}: cannot inspect it ({exc})"
    if empty:
        return None
    legacy_error = _legacy_layout_error(out)
    if legacy_error is None:
        return None
    return (
        f"refusing --output {out}: existing folder has no {OWNER_MARKER} ownership "
        f"marker and is not an exact generated site layout ({legacy_error}); "
        "choose an empty or new folder — nothing was deleted"
    )


# --------------------------------------------------------------------------
# Derived display facts (calculated from data, never asserted beyond it)
# --------------------------------------------------------------------------

SALARY_LABELS = {
    "eur-annual": "Known annual EUR base",
    "non-eur-annual": "Known annual base, non-EUR (no conversion applied)",
    "numeric-unconfirmed": "Numeric range, annual basis unconfirmed",
    "unknown": "No published figures",
}
MODE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site", "unknown": "Unknown"}
STATUS_LABELS = {"qualified": "Qualified", "rejected": "Rejected", "near-miss": "Near-miss"}
GATE_NAMES = {
    "open": "Listing open",
    "location": "Location",
    "ai": "AI-central",
    "pay": "Pay",
    "product": "Product research",
}


def salary_category(job: dict) -> str:
    """Classify how solid the published pay evidence is for one entry."""
    salary = job["salary"]
    if salary["lower"] is None and salary["upper"] is None:
        return "unknown"
    if salary["annualBase"] is True:
        return "eur-annual" if salary["currency"] == "EUR" else "non-eur-annual"
    return "numeric-unconfirmed"


def equity_value(job: dict) -> str:
    flag = job["salary"]["equity"]
    if flag is True:
        return "yes"
    if flag is False:
        return "no"
    return "unknown"


def counts(report: dict) -> tuple[int, int]:
    jobs = report["jobs"]
    qualified = sum(1 for job in jobs if job["status"] == "qualified")
    return qualified, len(jobs)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def esc(value) -> str:
    return _html.escape(str(value), quote=True)


def iso_day(value: str) -> str:
    return f'<time datetime="{esc(value)}">{esc(value)}</time>'


def script_json(payload: dict) -> str:
    """Serialize JSON so it can never break out of a script tag."""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def render_tokens(template: str, mapping: dict[str, str]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: mapping.get(m.group(1), ""), template)


def render_card(report_date: str, job: dict, base: str, hidden: bool) -> str:
    category = salary_category(job)
    equity = equity_value(job)
    status = job["status"]
    job_key = f"{report_date}/{job['id']}"
    anchor = f"job-{report_date}-{job['id']}"
    parts: list[str] = []

    hidden_attr = " hidden" if hidden else ""
    parts.append(
        f'<article class="job-card status-{esc(status)}" id="{esc(anchor)}" '
        f'data-key="{esc(job_key)}" data-date="{esc(report_date)}" '
        f'data-status="{esc(status)}" data-mode="{esc(job["location"])}" '
        f'data-equity="{esc(equity)}" data-salary="{esc(category)}"{hidden_attr}>'
    )

    badge = STATUS_LABELS[status]
    parts.append('<header class="card-head">')
    # Decorative classified-advertisement label; the recorded title, company
    # and status below are rendered unchanged.
    notice = "Help Wanted" if status == "qualified" else "Wanted"
    parts.append(f'<p class="notice-label" aria-hidden="true">{notice}</p>')
    parts.append('<div class="card-id">')
    parts.append(f'<p class="card-company">{esc(job["company"])}</p>')
    parts.append(f'<h3 class="card-title">{esc(job["title"])}</h3>')
    parts.append("</div>")
    parts.append(
        f'<p class="status-badge status-badge-{esc(status)}"><span>{esc(badge)}</span></p>'
    )
    parts.append("</header>")

    posted = (
        iso_day(job["posted"]) if job["posted"] else "Not published by the employer"
    )
    parts.append('<dl class="card-meta mono">')
    parts.append(
        f"<div><dt>Report date</dt><dd>{iso_day(report_date)}</dd></div>"
    )
    parts.append(f"<div><dt>Evidence checked</dt><dd>{iso_day(job['checked'])}</dd></div>")
    parts.append(f"<div><dt>Posted</dt><dd>{posted}</dd></div>")
    parts.append(
        "<div><dt>Work location</dt><dd>"
        f'{esc(MODE_LABELS[job["location"]])} — {esc(job["locationQuote"])}'
        "</dd></div>"
    )
    parts.append(
        f"<div><dt>Salary evidence</dt><dd>{esc(SALARY_LABELS[category])}</dd></div>"
    )
    equity_label = {"yes": "Yes", "no": "No", "unknown": "Unknown"}[equity]
    parts.append(f"<div><dt>Equity</dt><dd>{esc(equity_label)}</dd></div>")
    parts.append("</dl>")

    parts.append(
        f'<p class="card-salary"><span class="evidence-tag mono">Pay note</span>'
        f'{esc(job["salary"]["quote"])}</p>'
    )

    parts.append('<p class="gates-label mono">Recorded gates (as checked on the report date)</p>')
    parts.append('<ul class="gate-list">')
    for key in ("open", "location", "ai", "pay", "product"):
        value = job["gates"][key]
        parts.append(
            f'<li class="gate gate-{esc(value)}">'
            f'<span class="gate-name">{esc(GATE_NAMES[key])}</span>'
            f'<span class="gate-value mono">{esc(value)}</span></li>'
        )
    parts.append("</ul>")

    parts.append('<div class="reasons">')
    if job["reasons"]:
        parts.append('<p class="reasons-label mono">Gate notes</p><ul>')
        for reason in job["reasons"]:
            parts.append(f"<li>{esc(reason)}</li>")
        parts.append("</ul>")
    else:
        parts.append('<p class="fine-print">No gate notes recorded.</p>')
    parts.append("</div>")

    parts.append('<div class="card-actions">')
    if status == "qualified" and job["applyUrl"]:
        parts.append('<span class="notice-closing-label">For particulars and application:</span> ')
        parts.append(f'<a class="btn" href="{esc(job["applyUrl"])}" rel="noopener">Apply</a>')
        parts.append(
            f'<a class="btn btn-quiet" href="{esc(job["listingUrl"])}" rel="noopener">'
            "Open listing</a>"
        )
    else:
        parts.append('<span class="notice-closing-label">For particulars:</span> ')
        parts.append(
            f'<a class="btn btn-quiet" href="{esc(job["listingUrl"])}" rel="noopener">'
            "View listing</a>"
        )
    parts.append("</div>")
    if status != "qualified":
        parts.append(
            '<p class="fine-print">Research lead — opening a listing is not a '
            "recommendation.</p>"
        )

    parts.append('<details class="panel card-panel">')
    parts.append("<summary>Company &amp; role evidence notes</summary>")
    if job["product"]:
        parts.append(
            f'<p><span class="evidence-tag mono">Role/product note</span>{esc(job["product"])}</p>'
        )
    if job["financials"]:
        parts.append(
            f'<p><span class="evidence-tag mono">Company research</span>'
            f'{esc(job["financials"])}</p>'
        )
    if not job["product"] and not job["financials"]:
        parts.append('<p class="fine-print">No company research recorded for this entry.</p>')
    parts.append("</details>")

    parts.append('<details class="panel card-panel">')
    parts.append(f"<summary>Source evidence notes ({len(job['evidence'])})</summary>")
    parts.append(
        '<p class="fine-print">Evidence notes are recorded research notes and may '
        "paraphrase the source; they are not guaranteed to be verbatim quotations.</p>"
    )
    if job["evidence"]:
        parts.append('<ul class="evidence-list">')
        for item in job["evidence"]:
            parts.append("<li>")
            parts.append(
                f'<p class="evidence-head"><a href="{esc(item["url"])}" rel="noopener">'
                f'{esc(item["label"])}</a> '
                f'<span class="mono">checked {iso_day(item["checked"])}</span></p>'
            )
            parts.append(
                f'<p class="evidence-note"><span class="evidence-tag mono">Evidence note</span>'
                f'{esc(item["quote"])}</p>'
            )
            parts.append("</li>")
        parts.append("</ul>")
    else:
        parts.append('<p class="fine-print">No sources recorded.</p>')
    parts.append("</details>")

    parts.append("</article>")
    return "".join(parts)


def iter_jobs(reports_desc: list[dict]):
    """Yield (report_date, job) newest report first, entry order preserved."""
    for report in reports_desc:
        for job in report["jobs"]:
            yield report["date"], job


def render_filter_bar(scope_date: str, dates_desc: list[str], total: int, scope_count: int) -> str:
    options = ['<option value="all">All dates</option>']
    for date in dates_desc:
        selected = " selected" if date == scope_date else ""
        options.append(f'<option value="{esc(date)}"{selected}>{esc(date)}</option>')
    date_options = "".join(options)
    return f"""
<section class="filters" aria-labelledby="filters-title">
  <h2 id="filters-title">Filter entries</h2>
  <form class="filter-bar" id="filter-bar" role="search" onsubmit="return false;">
    <div class="field field-search">
      <label for="filter-search">Search company, title, role notes</label>
      <input type="search" id="filter-search" name="q" placeholder="e.g. Pigment, RAG, agents" autocomplete="off">
    </div>
    <div class="field">
      <label for="filter-date">Report date</label>
      <select id="filter-date" name="date">{date_options}</select>
    </div>
    <div class="field">
      <label for="filter-status">Status</label>
      <select id="filter-status" name="status">
        <option value="all">All statuses</option>
        <option value="qualified">Qualified</option>
        <option value="rejected">Rejected</option>
        <option value="near-miss">Near-miss</option>
      </select>
    </div>
    <div class="field">
      <label for="filter-mode">Work location</label>
      <select id="filter-mode" name="mode">
        <option value="all">Any location</option>
        <option value="remote">Remote</option>
        <option value="hybrid">Hybrid</option>
        <option value="on-site">On-site</option>
        <option value="unknown">Unknown</option>
      </select>
    </div>
    <div class="field">
      <label for="filter-equity">Equity</label>
      <select id="filter-equity" name="equity">
        <option value="all">Any equity</option>
        <option value="yes">Explicit equity</option>
        <option value="no">No equity stated</option>
        <option value="unknown">Unknown</option>
      </select>
    </div>
    <div class="field">
      <label for="filter-salary">Salary evidence</label>
      <select id="filter-salary" name="salary">
        <option value="all">Any salary evidence</option>
        <option value="eur-annual">Known annual EUR base</option>
        <option value="non-eur-annual">Known annual base, non-EUR</option>
        <option value="numeric-unconfirmed">Numeric range, annual basis unconfirmed</option>
        <option value="unknown">No published figures</option>
      </select>
    </div>
    <div class="field field-actions">
      <button type="button" class="btn btn-quiet" id="filter-reset">Reset</button>
    </div>
  </form>
  <p class="result-count mono" id="result-count" aria-live="polite">Showing {scope_count} of {total} entries</p>
  <p class="noscript-note">JavaScript is off: all {total} entries from every report date are listed below.</p>
</section>"""


def render_sections(reports_desc: list[dict], scope_date: str) -> str:
    qualified_cards: list[str] = []
    lead_cards: list[str] = []
    scope_qualified = 0
    scope_leads = 0

    for date, job in iter_jobs(reports_desc):
        hidden = date != scope_date
        card = render_card(date, job, base="", hidden=hidden)
        if job["status"] == "qualified":
            qualified_cards.append(card)
            if date == scope_date:
                scope_qualified += 1
        else:
            lead_cards.append(card)
            if date == scope_date:
                scope_leads += 1

    qualified_data_note = (
        '<p class="empty-message" id="qualified-empty-data"'
        + ("" if scope_qualified == 0 else " hidden")
        + f">No role passed every recorded gate in the {esc(scope_date)} report. "
        "The research leads below are not recommendations.</p>"
    )
    leads_data_note = (
        '<p class="empty-message" id="leads-empty-data"'
        + ("" if scope_leads == 0 else " hidden")
        + f">No research leads recorded in the {esc(scope_date)} report.</p>"
    )

    return f"""
<section class="job-section" id="qualified-matches" aria-labelledby="qualified-title">
  <h2 id="qualified-title">Qualified matches</h2>
  <p class="section-note">Entries whose stored gates all passed on their report date. Historical checks — not a live availability guarantee.</p>
  {qualified_data_note}
  <p class="empty-message" id="qualified-empty-filter" hidden>No qualified entries in this view match the current filters.</p>
  <div class="job-list" id="qualified-list">
{"".join(f"    {card}\n" for card in qualified_cards)}  </div>
</section>
<section class="job-section" id="research-leads" aria-labelledby="leads-title">
  <h2 id="leads-title">Research leads</h2>
  <p class="section-note">Rejected and near-miss entries kept for research. These are not recommendations.</p>
  {leads_data_note}
  <p class="empty-message" id="leads-empty-filter" hidden>No research entries in this view match the current filters.</p>
  <div class="job-list" id="leads-list">
{"".join(f"    {card}\n" for card in lead_cards)}  </div>
</section>
<p class="empty-message" id="no-results" hidden>No entries match the current filters.</p>"""


def render_details_panel(title: str, body_html: str) -> str:
    return f'<details class="panel"><summary>{esc(title)}</summary>{body_html}</details>'


def render_list(items: list[str]) -> str:
    inner = "".join(f"<li>{esc(item)}</li>" for item in items)
    return f"<ul>{inner}</ul>"


RESULT_LABELS = {
    "complete": "Complete",
    "partial": "Partial",
    "none": "None",
    "undetermined": "Undetermined",
}


def result_status(report: dict) -> str:
    """Classify the day's result from recorded data only.

    none: zero qualified entries. complete/partial: qualified count compared
    with the recorded requestedCount. undetermined: some entries qualified but
    no requestedCount was recorded, so completeness is not guessed.
    """
    qualified, _ = counts(report)
    if qualified == 0:
        return "none"
    requested = report.get("requestedCount")
    if requested is None:
        return "undetermined"
    return "complete" if qualified >= requested else "partial"


def render_result_block(
    report: dict, base: str, heading_html: str, links: list[tuple[str, str, bool]]
) -> str:
    """Shared home/report result block: status, counts, decision, caveats.

    Everything shown is read from the report itself; missing optional fields
    are labelled as not recorded rather than inferred. `links` is a list of
    (label, path relative to the site root, primary?) tuples; a Download JSON
    link to this report's export is always appended.
    """
    qualified, examined = counts(report)
    status = result_status(report)
    date = report["date"]
    requested = report.get("requestedCount")
    if requested is None:
        requested_html = ' · <span class="stat">requested count not recorded</span>'
    else:
        requested_html = (
            f' of <span class="stat"><strong>{esc(requested)}</strong> requested</span>'
        )
    status_note = ""
    if status == "undetermined":
        status_note = (
            ' <span class="result-note">— completeness cannot be judged: '
            "requested count not recorded</span>"
        )
    decision = report.get("decision")
    if decision:
        decision_html = esc(decision)
    else:
        decision_html = '<span class="not-recorded">Decision not recorded in this report.</span>'
    caveats = [esc(item) for item in report["uncertainty"]]
    caveats.append(
        f"Independent recheck: every entry reflects checks made on {esc(date)}. "
        "Confirm the listing, pay and eligibility on the employer’s own pages "
        "before acting."
    )
    caveat_items = "".join(f"<li>{item}</li>" for item in caveats)
    link_html = "".join(
        f'<a class="btn{"" if primary else " btn-quiet"}" href="{esc(base)}{esc(path)}">'
        f"{esc(label)}</a>\n      "
        for label, path, primary in links
    )
    link_html += (
        f'<a class="btn btn-quiet" href="{esc(base)}reports/{esc(date)}.json" download>'
        "Download JSON</a>"
    )
    zero_state = ""
    if qualified == 0:
        zero_state = (
            '\n    <p class="empty-message">Zero roles passed every recorded gate on '
            f"{esc(date)}. The entries below are research leads, not recommendations.</p>"
        )
    return f"""<div class="hero-report">
    <h2>{heading_html}</h2>
    <p class="result-status result-{esc(status)}"><span class="result-label mono">Result</span> <strong class="result-value">{esc(RESULT_LABELS[status])}</strong>{status_note}</p>
    <p class="stat-line mono"><span class="stat"><strong>{qualified}</strong> qualified</span>{requested_html} · <span class="stat"><strong>{examined}</strong> examined</span></p>
    <p class="summary">{esc(report["summary"])}</p>
    <p class="decision"><span class="evidence-tag mono">Decision</span>{decision_html}</p>{zero_state}
    <div class="hero-caveats">
      <p class="caveats-label mono">Uncertainty &amp; caveats</p>
      <ul>{caveat_items}</ul>
    </div>
    <p class="hero-links">
      {link_html}
    </p>
  </div>"""


def render_home_hero(report: dict, base: str) -> str:
    return f"""
<section class="hero" aria-labelledby="page-title">
  <p class="eyebrow mono">Daily AI engineering job research</p>
  <h1 id="page-title">Job Alerts</h1>
  <p class="standfirst">Evidence-backed roles checked against stated gates: Paris hybrid with explicit attendance or fully remote explicitly allowing France, senior hands-on AI-central engineering, and an employer-published annual cash-base floor.</p>
  {render_result_block(report, base, f"Latest report — {iso_day(report['date'])}", [
      ("Open full report", f"reports/{report['date']}.html", True),
      ("Browse archive", "archive.html", False),
  ])}
  {render_details_panel("Search coverage", f"<p>{esc(report['coverage'])}</p>")}
  {render_details_panel("Criteria applied", render_list(report["criteria"]))}
</section>"""


def render_report_nav(report: dict, dates_desc: list[str], base: str) -> str:
    date = report["date"]
    links = [f'<a href="{esc(base)}archive.html">All reports</a>']
    index = dates_desc.index(date)
    if index + 1 < len(dates_desc):
        earlier = dates_desc[index + 1]
        links.append(
            f'<a href="{esc(base)}reports/{esc(earlier)}.html">← Earlier: {esc(earlier)}</a>'
        )
    if index > 0:
        later = dates_desc[index - 1]
        links.append(
            f'<a href="{esc(base)}reports/{esc(later)}.html">Later: {esc(later)} →</a>'
        )
    if date != dates_desc[0]:
        links.append(f'<a href="{esc(base)}index.html">Latest report</a>')
    joined = ' <span aria-hidden="true">·</span> '.join(links)
    return f'<nav class="report-nav" aria-label="Report navigation">{joined}</nav>'


def render_report_hero(report: dict, dates_desc: list[str], base: str) -> str:
    return f"""
<section class="hero" aria-labelledby="page-title">
  {render_report_nav(report, dates_desc, base)}
  <p class="eyebrow mono">Daily research report</p>
  <h1 id="page-title">Report — {iso_day(report["date"])}</h1>
  {render_result_block(report, base, f"Result of {iso_day(report['date'])}", [
      ("Latest report", "index.html", False),
      ("Browse archive", "archive.html", False),
  ])}
  {render_details_panel("Search coverage", f"<p>{esc(report['coverage'])}</p>")}
  {render_details_panel("Criteria applied", render_list(report["criteria"]))}
</section>"""


def render_archive(reports_desc: list[dict]) -> str:
    rows = []
    for report in reports_desc:
        qualified, examined = counts(report)
        date = report["date"]
        rows.append(
            "<tr>"
            f'<td>{iso_day(date)}</td>'
            f'<td class="count">{qualified}</td>'
            f'<td class="count">{examined}</td>'
            f"<td>{esc(report['summary'])}</td>"
            f'<td><a href="reports/{esc(date)}.html">Open</a></td>'
            "</tr>"
        )
    latest = reports_desc[0]
    return f"""
<section class="hero" aria-labelledby="page-title">
  <p class="eyebrow mono">Archive</p>
  <h1 id="page-title">Report archive</h1>
  <p class="standfirst">Every published report with counts calculated from its own data. Entries record checks made on their report date; they are not live availability claims.</p>
</section>
<div class="table-scroll">
  <table class="archive-table">
    <caption class="sr-only">Daily reports with qualified and examined counts</caption>
    <thead>
      <tr>
        <th scope="col">Report date</th>
        <th scope="col">Qualified</th>
        <th scope="col">Examined</th>
        <th scope="col">Summary</th>
        <th scope="col">Report</th>
      </tr>
    </thead>
    <tbody>
{"".join(f"      {row}\n" for row in rows)}    </tbody>
  </table>
</div>
{render_details_panel("Criteria applied (latest report)", render_list(latest["criteria"]))}
{render_details_panel("Uncertainty & caveats (latest report)", render_list(latest["uncertainty"]))}"""


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def long_day(value: str) -> str:
    """Spell out a validated ISO report date, e.g. Tuesday, 13 October 2026."""
    day = _dt.date.fromisoformat(value)
    return f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]} {day.year}"


def render_dateline(dates_desc: list[str], date: str | None) -> str:
    """Masthead dateline built only from published report dates.

    The issue number is the report's position in date order (oldest = No. 1),
    so it grows with the archive; volume is always I. The archive page, which
    covers every report, shows the full issue and date range instead.
    """
    oldest_first = sorted(dates_desc)
    if date is None:
        first, last = oldest_first[0], oldest_first[-1]
        issue = f"Nos. 1&ndash;{len(oldest_first)}" if len(oldest_first) > 1 else "No. 1"
        when = (
            f'Reports of <time datetime="{esc(first)}">{esc(long_day(first))}</time>'
            + (
                f' to <time datetime="{esc(last)}">{esc(long_day(last))}</time>'
                if last != first
                else ""
            )
        )
    else:
        issue = f"No. {oldest_first.index(date) + 1}"
        when = f'Report of <time datetime="{esc(date)}">{esc(long_day(date))}</time>'
    return (
        '<div class="dateline">'
        f'<p class="dateline-issue">Vol. I &middot; {issue}</p>'
        f'<p class="dateline-date">{when}</p>'
        '<p class="dateline-price">Price: Free &middot; Web Edition</p>'
        "</div>"
    )


def render_page(
    template: str,
    *,
    kind: str,
    title: str,
    description: str,
    base: str,
    content: str,
    payload: dict,
    base_url: str,
    rel_path: str,
    dateline: str = "",
) -> str:
    canonical = ""
    if base_url:
        href = base_url.rstrip("/") + "/" + rel_path
        canonical = f'  <link rel="canonical" href="{esc(href)}">\n'
    return render_tokens(
        template,
        {
            "base": base,
            "title": esc(title),
            "description": esc(description),
            "canonical": canonical,
            "page_kind": esc(kind),
            "dateline": dateline,
            "content": content if content.endswith("\n") else content + "\n",
            "payload": script_json(payload),
        },
    )


def payload_for(scope_date: str, reports_desc: list[dict], kind: str) -> dict:
    jobs = []
    for date, job in iter_jobs(reports_desc):
        searchable = [
            job["company"],
            job["title"],
            job["locationQuote"],
            job["salary"]["quote"],
            job["product"] or "",
            job["financials"] or "",
            *job["reasons"],
        ]
        jobs.append(
            {
                "key": f"{date}/{job['id']}",
                "date": date,
                "status": job["status"],
                "mode": job["location"],
                "equity": equity_value(job),
                "salary": salary_category(job),
                "text": " ".join(searchable).lower(),
            }
        )
    return {
        "page": {"kind": kind, "scopeDate": scope_date, "total": len(jobs)},
        "dates": [report["date"] for report in reports_desc],
        "jobs": jobs,
    }


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def _install_staging(staging: Path, output_dir: Path) -> None:
    """Move the fully rendered staging directory into place (one rename)."""
    staging.rename(output_dir)


def replace_output(staging: Path, output_dir: Path) -> bool:
    """Install `staging` at `output_dir`, keeping the old output until success.

    Returns True on success. On failure the previous output is restored from
    its backup (or, if even that fails, its backup location is reported) and
    the staging directory is removed.
    """
    backup = None
    if output_dir.exists():
        backup = output_dir.with_name(
            f".{output_dir.name}-backup-{os.getpid()}-{secrets.token_hex(6)}"
        )
        try:
            output_dir.rename(backup)
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            print(
                f"ERROR: could not move previous output aside ({exc}); "
                f"{output_dir} left unchanged.",
                file=sys.stderr,
            )
            return False
    try:
        _install_staging(staging, output_dir)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if backup is None:
            print(f"ERROR: could not install new output at {output_dir} ({exc}).",
                  file=sys.stderr)
            return False
        try:
            backup.rename(output_dir)
        except OSError as rollback_exc:
            print(
                f"ERROR: could not install new output at {output_dir} ({exc}) and "
                f"rollback failed ({rollback_exc}); the previous output is preserved "
                f"intact at {backup}.",
                file=sys.stderr,
            )
            return False
        print(
            f"ERROR: could not install new output at {output_dir} ({exc}); "
            "the previous output was restored unchanged.",
            file=sys.stderr,
        )
        return False
    if backup is not None:
        try:
            shutil.rmtree(backup)
        except OSError as exc:
            print(f"WARNING: new output installed, but the old backup {backup} "
                  f"could not be removed ({exc}).", file=sys.stderr)
    return True


def build(reports_dir: Path, output_dir: Path, base_url: str, validate_only: bool) -> int:
    # Fail fast before any write or deletion: never let --output point at a
    # directory whose replacement would destroy the repository or its inputs.
    if not validate_only:
        guard_error = output_guard_error(output_dir, reports_dir)
        if guard_error:
            print(f"ERROR: {guard_error}", file=sys.stderr)
            return 1

    try:
        paths = discover_reports(reports_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not paths:
        print(f"ERROR: {reports_dir}: no report JSON files found", file=sys.stderr)
        return 1

    reports: list[dict] = []
    errors: list[str] = []
    seen_keys: set[str] = set()
    for path in paths:
        data, file_errors = load_report(path)
        if file_errors:
            errors.extend(file_errors)
            continue
        for job in data["jobs"]:
            key = f"{data['date']}/{job['id']}"
            if key in seen_keys:
                errors.append(f"{path}: duplicate card key {key!r} across reports")
            seen_keys.add(key)
        reports.append(data)

    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        print(f"\n{len(errors)} error(s); no files written.", file=sys.stderr)
        return 1

    if validate_only:
        for path in paths:
            print(f"OK   {path}")
        print(f"\nAll {len(paths)} report file(s) valid; --validate-only, nothing written.")
        return 0

    template_path = ASSETS_DIR / "template.html"
    if not template_path.is_file():
        print(f"ERROR: template not found: {template_path}", file=sys.stderr)
        return 1
    template = template_path.read_text(encoding="utf-8")

    reports_desc = sorted(reports, key=lambda r: r["date"], reverse=True)
    dates_desc = [report["date"] for report in reports_desc]
    latest = reports_desc[0]
    total = sum(len(report["jobs"]) for report in reports_desc)

    pages: dict[str, str] = {}
    json_exports: dict[str, str] = {}

    home_payload = payload_for(latest["date"], reports_desc, "home")
    home_content = (
        render_home_hero(latest, base="")
        + render_filter_bar(latest["date"], dates_desc, total, len(latest["jobs"]))
        + render_sections(reports_desc, scope_date=latest["date"])
    )
    pages["index.html"] = render_page(
        template,
        kind="home",
        title="Job Alerts — daily AI engineering job research",
        description=latest["summary"],
        base="",
        content=home_content,
        payload=home_payload,
        base_url=base_url,
        rel_path="index.html",
        dateline=render_dateline(dates_desc, latest["date"]),
    )

    for report in reports_desc:
        date = report["date"]
        if not ISO_DATE_RE.match(date):
            print(f"ERROR: report date {date!r} is not a safe path segment", file=sys.stderr)
            return 1
        # Public machine-readable export: the exact validated report payload.
        json_exports[f"reports/{date}.json"] = (
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
        payload = payload_for(date, reports_desc, "report")
        content = (
            render_report_hero(report, dates_desc, base="../")
            + render_filter_bar(date, dates_desc, total, len(report["jobs"]))
            + render_sections(reports_desc, scope_date=date)
        )
        pages[f"reports/{date}.html"] = render_page(
            template,
            kind="report",
            title=f"Job Alerts — {date} report",
            description=report["summary"],
            base="../",
            content=content,
            payload=payload,
            base_url=base_url,
            rel_path=f"reports/{date}.html",
            dateline=render_dateline(dates_desc, date),
        )

    archive_payload = {"page": {"kind": "archive", "scopeDate": None, "total": total},
                       "dates": dates_desc, "jobs": []}
    pages["archive.html"] = render_page(
        template,
        kind="archive",
        title="Job Alerts — report archive",
        description="Archive of daily AI engineering job research reports.",
        base="",
        content=render_archive(reports_desc),
        payload=archive_payload,
        base_url=base_url,
        rel_path="archive.html",
        dateline=render_dateline(dates_desc, None),
    )

    # Everything is rendered in memory; now stage it next to the target, move
    # any previous output aside to a unique backup, install the staging
    # directory, and roll the backup back if installation fails. The backup is
    # deleted only after the new output is in place. This is a sequence of
    # same-filesystem renames, not one atomic swap: a crash between the two
    # renames leaves the previous output in the backup folder, never deleted.
    output_dir = Path(os.path.abspath(output_dir))
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-staging-", dir=output_dir.parent))
    try:
        for rel_path, text in {**pages, **json_exports}.items():
            target = staging / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        if ASSETS_DIR.is_dir():
            shutil.copytree(ASSETS_DIR, staging / "assets")
        (staging / OWNER_MARKER).write_text(OWNER_MARKER_TEXT, encoding="utf-8")
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"ERROR: could not stage output ({exc}); {output_dir} untouched.", file=sys.stderr)
        return 1

    if not replace_output(staging, output_dir):
        return 1

    print(
        f"Built {len(pages)} page(s) and {len(json_exports)} report JSON export(s), "
        f"{total} entr{'y' if total == 1 else 'ies'} "
        f"from {len(reports)} report(s) -> {output_dir}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render and validate the Job Alerts static site (stdlib only)."
    )
    parser.add_argument(
        "--reports",
        default=str(DEFAULT_REPORTS),
        help="directory containing YYYY-MM-DD.json reports (default: data/reports)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="directory to write the static site into (default: dist/)",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="optional absolute site URL used for canonical links",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate reports and exit without writing any output",
    )
    args = parser.parse_args(argv)
    return build(Path(args.reports), Path(args.output), args.base_url, args.validate_only)


if __name__ == "__main__":
    raise SystemExit(main())
