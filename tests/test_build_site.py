"""Unit tests for scripts/build_site.py (renderer + validator)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
for _entry in (str(TESTS_DIR), str(TESTS_DIR.parent / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import build_site
import validate
from fixtures import (
    LIVE_REPORTS,
    ROOT,
    SEED_REPORTS,
    load_report_data,
    make_gates,
    make_job,
    make_report,
    make_salary,
    report_files,
    write_report,
)


def run_build(reports, output, base_url="", validate_only=False):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = build_site.build(Path(reports), Path(output), base_url, validate_only)
    return code, out.getvalue(), err.getvalue()


def extract_payload(html_text: str) -> dict:
    match = re.search(
        r'<script id="site-data" type="application/json">(.*?)</script>',
        html_text,
        re.S,
    )
    assert match, "inline site-data payload missing"
    return json.loads(match.group(1))


def card_block(html_text: str, key: str) -> str:
    marker = f'data-key="{key}"'
    idx = html_text.find(marker)
    assert idx != -1, f"card {key} missing"
    start = html_text.rfind("<article", 0, idx)
    end = html_text.index("</article>", idx) + len("</article>")
    return html_text[start:end]


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under root (relative path -> exact bytes)."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(Path(root).rglob("*"))
        if path.is_file()
    }


def _deep_keys(value):
    """Structure fingerprint: keys and value types at every nesting level."""
    if isinstance(value, dict):
        return {key: _deep_keys(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_deep_keys(item) for item in value]
    return type(value).__name__


class BuildRealDataTest(unittest.TestCase):
    """Build the frozen four-report seed snapshot and inspect the output pages.

    Deterministic by design: the fixture set under tests/seed_reports/ never
    grows, so exact counts and dated facts stay stable while live
    data/reports/ keeps accumulating new days (see BuildLiveDataTest).
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "dist"
        cls.code, cls.stdout, cls.stderr = run_build(SEED_REPORTS, cls.out)
        cls.index = (cls.out / "index.html").read_text(encoding="utf-8")
        cls.archive = (cls.out / "archive.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def report_html(self, date: str) -> str:
        return (self.out / "reports" / f"{date}.html").read_text(encoding="utf-8")

    def test_build_succeeds(self):
        self.assertEqual(self.code, 0, self.stderr)
        for rel in (
            "index.html",
            "archive.html",
            "reports/2026-10-10.html",
            "reports/2026-10-11.html",
            "reports/2026-10-12.html",
            "reports/2026-10-13.html",
        ):
            self.assertTrue((self.out / rel).is_file(), f"missing {rel}")

    def test_assets_copied(self):
        for name in ("style.css", "app.js", "report.schema.json", "template.html"):
            self.assertTrue((self.out / "assets" / name).is_file(), f"missing asset {name}")

    def test_report_json_exports_equal_source_data_exactly(self):
        """dist/reports/*.json must be the exact validated public report payload."""
        for path in report_files(SEED_REPORTS):
            exported = json.loads(
                (self.out / "reports" / path.name).read_text(encoding="utf-8")
            )
            self.assertEqual(exported, load_report_data(path), path.name)
            # Recursively: no private or derived fields were added or dropped.
            self.assertEqual(
                _deep_keys(exported), _deep_keys(load_report_data(path)), path.name
            )

    def test_report_pages_link_download_json(self):
        for date in ("2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"):
            html = self.report_html(date)
            self.assertIn(
                f'<a class="btn btn-quiet" href="../reports/{date}.json" download>'
                "Download JSON</a>",
                html,
                f"Download JSON link missing on {date}",
            )
        home_links = re.search(r'<p class="hero-links">.*?</p>', self.index, re.S).group(0)
        self.assertIn(
            '<a class="btn btn-quiet" href="reports/2026-10-13.json" download>'
            "Download JSON</a>",
            home_links,
            "home Download JSON link must point at the current report export",
        )
        self.assertTrue((self.out / "reports" / "2026-10-13.json").is_file())

    def test_home_is_scoped_to_latest_report(self):
        payload = extract_payload(self.index)
        self.assertEqual(payload["page"]["scopeDate"], "2026-10-13")
        self.assertEqual(payload["page"]["total"], 11)
        self.assertEqual(payload["dates"][0], "2026-10-13")
        # Only the four latest-report cards are visible before JavaScript runs.
        cards = re.findall(r'class="job-card[^"]*"[^>]*?data-date="([^"]+)"([^>]*)', self.index)
        visible = [date for date, rest in cards if " hidden" not in rest]
        self.assertEqual(sorted(set(visible)), ["2026-10-13"])
        self.assertEqual(len(visible), 4)

    def test_home_zero_qualified_state(self):
        self.assertIn("Zero roles passed every recorded gate", self.index)
        self.assertIn('<strong>0</strong> qualified', self.index)
        self.assertIn('<strong>4</strong> examined', self.index)
        self.assertIn('id="qualified-empty-data"', self.index)

    def test_report_hero_counts_come_from_data(self):
        html_1010 = self.report_html("2026-10-10")
        self.assertIn('<strong>2</strong> qualified', html_1010)
        self.assertIn('<strong>2</strong> examined', html_1010)
        html_1012 = self.report_html("2026-10-12")
        self.assertIn('<strong>0</strong> qualified', html_1012)
        self.assertIn('<strong>3</strong> examined', html_1012)

    def test_archive_counts_match_report_data(self):
        for date in ("2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"):
            data = json.loads((SEED_REPORTS / f"{date}.json").read_text(encoding="utf-8"))
            qualified = sum(1 for j in data["jobs"] if j["status"] == "qualified")
            row = re.search(
                rf'<td><time datetime="{date}">{date}</time></td>'
                rf'<td class="count">(\d+)</td><td class="count">(\d+)</td>',
                self.archive,
            )
            self.assertIsNotNone(row, f"archive row for {date} missing")
            self.assertEqual(int(row.group(1)), qualified)
            self.assertEqual(int(row.group(2)), len(data["jobs"]))

    def test_qualified_gets_apply_rejected_gets_view_listing(self):
        qualified = card_block(self.index, "2026-10-10/pigment-genai")
        self.assertIn(">Apply</a>", qualified)
        self.assertNotIn(">View listing</a>", qualified)
        self.assertNotIn("not a recommendation", qualified)

        lead = card_block(self.index, "2026-10-12/qonto-agents")
        self.assertIn(">View listing</a>", lead)
        self.assertNotIn(">Apply</a>", lead)
        self.assertIn("not a recommendation", lead)

    def test_sections_are_separated(self):
        self.assertIn('id="qualified-matches"', self.index)
        self.assertIn("Qualified matches", self.index)
        self.assertIn('id="research-leads"', self.index)
        self.assertIn("Research leads", self.index)
        self.assertIn("not recommendations", self.index)

    def test_filter_controls_and_labels(self):
        for control in (
            "filter-search", "filter-date", "filter-status", "filter-mode",
            "filter-equity", "filter-salary", "filter-reset",
            "result-count", "no-results",
        ):
            self.assertIn(f'id="{control}"', self.index)
        for label in (
            "Search company, title, role notes",
            "Report date",
            "Status",
            "Work location",
            "Equity",
            "Salary evidence",
        ):
            self.assertIn(f"<label", self.index)
            self.assertIn(label, self.index)
        self.assertIn("No entries match the current filters.", self.index)
        self.assertIn("Showing 4 of 11 entries", self.index)
        self.assertIn("Reset", self.index)

    def test_salary_evidence_categories_are_distinct(self):
        self.assertIn('data-salary="eur-annual"', self.index)
        self.assertIn('data-salary="non-eur-annual"', self.index)
        self.assertIn('data-salary="numeric-unconfirmed"', self.index)
        self.assertIn('data-salary="unknown"', self.index)
        for label in (
            "Known annual EUR base",
            "Known annual base, non-EUR (no conversion applied)",
            "Numeric range, annual basis unconfirmed",
            "No published figures",
        ):
            self.assertIn(label, self.index)
        # DualEntry stays qualified despite a USD salary.
        dualentry = re.search(
            r'<article class="job-card status-qualified"[^>]*data-key="2026-10-10/dualentry-ai"[^>]*>',
            self.index,
        )
        self.assertIsNotNone(dualentry)
        self.assertIn('data-salary="non-eur-annual"', dualentry.group(0))

    def test_equity_values_include_unknown(self):
        self.assertIn('data-equity="yes"', self.index)
        self.assertIn('data-equity="unknown"', self.index)
        self.assertIn("<dt>Equity</dt><dd>Unknown</dd>", self.index)

    def test_deep_link_anchor_exists(self):
        self.assertIn('id="job-2026-10-10-pigment-genai"', self.index)
        self.assertIn('data-key="2026-10-10/pigment-genai"', self.index)

    def test_evidence_notes_are_not_decorated_as_verbatim_quotes(self):
        self.assertIn("Evidence note", self.index)
        self.assertIn(
            "Evidence notes are recorded research notes and may paraphrase the source",
            self.index,
        )
        self.assertIn("are not guaranteed to be verbatim quotations", self.index)
        self.assertNotIn("<blockquote", self.index)
        self.assertNotIn(">Quote<", self.index)
        self.assertNotIn("Verbatim quote", self.index)

    def test_nested_pages_use_relative_asset_links(self):
        html_1010 = self.report_html("2026-10-10")
        self.assertIn('href="../assets/style.css"', html_1010)
        self.assertIn('src="../assets/app.js"', html_1010)
        self.assertIn('href="../index.html"', html_1010)
        self.assertNotIn('href="assets/style.css"', html_1010)
        self.assertIn('href="assets/style.css"', self.index)

    def test_payload_matches_report_data(self):
        payload = extract_payload(self.index)
        self.assertEqual(len(payload["jobs"]), 11)
        keys = {job["key"] for job in payload["jobs"]}
        self.assertIn("2026-10-10/pigment-genai", keys)
        by_key = {job["key"]: job for job in payload["jobs"]}
        dualentry = by_key["2026-10-10/dualentry-ai"]
        self.assertEqual(dualentry["status"], "qualified")
        self.assertEqual(dualentry["salary"], "non-eur-annual")
        self.assertEqual(dualentry["mode"], "remote")
        self.assertIn("rag", dualentry["text"])  # responsibilities are searchable
        cohere = by_key["2026-10-11/cohere-safety"]
        self.assertEqual(cohere["equity"], "unknown")
        self.assertEqual(cohere["salary"], "unknown")

    def test_report_pages_share_the_full_date_scope(self):
        payload = extract_payload(self.report_html("2026-10-11"))
        self.assertEqual(payload["page"]["scopeDate"], "2026-10-11")
        self.assertEqual(len(payload["jobs"]), 11)

    def test_output_contains_no_private_content(self):
        email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        for path in list(self.out.rglob("*.html")) + list(self.out.rglob("*.json")):
            if path.is_dir():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(email.search(text), f"email leaked into {path}")
            self.assertNotIn("/home/", text, f"local path leaked into {path}")

    def test_inline_json_cannot_break_out_of_script_tag(self):
        payload_text = re.search(
            r'<script id="site-data" type="application/json">(.*?)</script>',
            self.index,
            re.S,
        ).group(1)
        # `&`, `<` and `>` only ever appear as JSON unicode escapes.
        for raw in ("&", "<", ">"):
            self.assertNotIn(raw, payload_text, f"raw {raw!r} in inline JSON")
        json.loads(payload_text)


class BuildLiveDataTest(unittest.TestCase):
    """Live data/reports/ grows every day: every count here is derived.

    Nothing in this class may hardcode report dates, report counts or entry
    totals; a valid appended report must keep these tests green untouched.
    """

    @classmethod
    def setUpClass(cls):
        cls.live_paths = report_files(LIVE_REPORTS)
        assert cls.live_paths, "expected at least one live report"
        cls.live_data = {p.stem: load_report_data(p) for p in cls.live_paths}
        cls.dates = sorted(cls.live_data)
        cls.total = sum(len(cls.live_data[d]["jobs"]) for d in cls.dates)
        cls.latest = cls.dates[-1]
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "dist"
        cls.code, cls.stdout, cls.stderr = run_build(LIVE_REPORTS, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_build_succeeds_for_every_live_report(self):
        self.assertEqual(self.code, 0, self.stderr)
        for date in self.dates:
            self.assertTrue((self.out / "reports" / f"{date}.html").is_file(), date)
            self.assertTrue((self.out / "reports" / f"{date}.json").is_file(), date)

    def test_live_json_exports_equal_sources_exactly(self):
        for date in self.dates:
            exported = json.loads(
                (self.out / "reports" / f"{date}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(exported, self.live_data[date], date)

    def test_home_scope_and_totals_derive_from_live_data(self):
        index = (self.out / "index.html").read_text(encoding="utf-8")
        payload = extract_payload(index)
        self.assertEqual(payload["page"]["scopeDate"], self.latest)
        self.assertEqual(payload["page"]["total"], self.total)
        self.assertEqual(payload["dates"], list(reversed(self.dates)))
        self.assertIn(f"Showing {len(self.live_data[self.latest]['jobs'])} of {self.total} entries", index)

    def test_archive_rows_match_live_reports(self):
        archive = (self.out / "archive.html").read_text(encoding="utf-8")
        rows = re.findall(r"<tr><td><time datetime=\"(\d{4}-\d{2}-\d{2})\"", archive)
        self.assertEqual(rows, list(reversed(self.dates)))
        for date in self.dates:
            data = self.live_data[date]
            qualified = sum(1 for j in data["jobs"] if j["status"] == "qualified")
            row = re.search(
                rf'<td><time datetime="{date}">{date}</time></td>'
                rf'<td class="count">(\d+)</td><td class="count">(\d+)</td>',
                archive,
            )
            self.assertIsNotNone(row, f"archive row for {date} missing")
            self.assertEqual(int(row.group(1)), qualified, date)
            self.assertEqual(int(row.group(2)), len(data["jobs"]), date)

    def test_every_live_report_page_scopes_to_its_own_date(self):
        for date in self.dates:
            html = (self.out / "reports" / f"{date}.html").read_text(encoding="utf-8")
            payload = extract_payload(html)
            self.assertEqual(payload["page"]["scopeDate"], date)
            self.assertEqual(payload["page"]["total"], self.total)


class BuildOutputSafetyGuardTest(unittest.TestCase):
    """--output must never destroy the repository, its inputs or a git tree."""

    def test_repo_root_is_refused_and_intact(self):
        code, _, stderr = run_build(SEED_REPORTS, ROOT)
        self.assertEqual(code, 1)
        self.assertIn("refusing", stderr)
        self.assertTrue((ROOT / "scripts" / "build_site.py").is_file())
        self.assertTrue((ROOT / "data" / "reports").is_dir())
        self.assertTrue((ROOT / "assets" / "template.html").is_file())

    def test_ancestor_of_repository_is_refused_and_intact(self):
        code, _, stderr = run_build(SEED_REPORTS, ROOT.parent)
        self.assertEqual(code, 1)
        self.assertIn("refusing", stderr)
        self.assertTrue((ROOT / "data" / "reports").is_dir())

    def test_repository_source_directories_are_refused_and_intact(self):
        for name in ("data", "assets", "scripts", "tests"):
            out = ROOT / name
            before = sorted(p.name for p in out.iterdir())
            code, _, stderr = run_build(SEED_REPORTS, out)
            self.assertEqual(code, 1, name)
            self.assertIn("refusing", stderr)
            self.assertTrue(out.is_dir(), name)
            self.assertEqual(sorted(p.name for p in out.iterdir()), before, name)

    def test_reports_input_directory_overlap_is_refused_and_intact(self):
        before = sorted(p.name for p in SEED_REPORTS.iterdir())
        for out in (SEED_REPORTS, SEED_REPORTS.parent, SEED_REPORTS / "nested-out"):
            code, _, stderr = run_build(SEED_REPORTS, out)
            self.assertEqual(code, 1, str(out))
            self.assertIn("refusing", stderr)
        self.assertEqual(sorted(p.name for p in SEED_REPORTS.iterdir()), before)
        self.assertFalse((SEED_REPORTS / "nested-out").exists())

    def test_git_containing_directory_is_refused_and_markers_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "clone"
            (out / ".git").mkdir(parents=True)
            marker = out / "IMPORTANT.txt"
            marker.write_text("keep me", encoding="utf-8")
            code, _, stderr = run_build(SEED_REPORTS, out)
            self.assertEqual(code, 1)
            self.assertIn("refusing", stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep me")
            self.assertTrue((out / ".git").is_dir())

    def test_output_inside_dot_git_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / ".git" / "nested-output"
            out.mkdir(parents=True)
            code, _, stderr = run_build(SEED_REPORTS, out)
            self.assertEqual(code, 1)
            self.assertIn("refusing", stderr)
            self.assertTrue(out.is_dir())

    def test_fresh_folder_and_dist_rebuilds_stay_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "site-out"
            code, _, stderr = run_build(SEED_REPORTS, fresh)
            self.assertEqual(code, 0, stderr)
            self.assertTrue((fresh / "index.html").is_file())
            self.assertEqual(
                (fresh / build_site.OWNER_MARKER).read_text(encoding="utf-8"),
                build_site.OWNER_MARKER_TEXT,
            )

            # A marked (renderer-owned) output is rebuilt and stale files go.
            (fresh / "stale.txt").write_text("old", encoding="utf-8")
            code, _, stderr = run_build(SEED_REPORTS, fresh)
            self.assertEqual(code, 0, stderr)
            self.assertTrue((fresh / "index.html").is_file())
            self.assertFalse((fresh / "stale.txt").exists())

            empty = Path(tmp) / "empty-out"
            empty.mkdir()
            code, _, stderr = run_build(SEED_REPORTS, empty)
            self.assertEqual(code, 0, stderr)
            self.assertTrue((empty / build_site.OWNER_MARKER).is_file())

    def test_validate_only_skips_the_guard_but_writes_nothing(self):
        out = ROOT / "data"
        code, stdout, _ = run_build(SEED_REPORTS, out, validate_only=True)
        self.assertEqual(code, 0)
        self.assertIn("validate-only", stdout)
        self.assertTrue(out.is_dir())


class BuildEscapingTest(unittest.TestCase):
    """Malicious report text must be escaped in HTML and inline JSON."""

    def test_hostile_report_text_is_escaped(self):
        hostile_job = make_job(
            id="hostile-entry",
            status="rejected",
            company='<script>alert("x")</script>',
            title='AI Engineer <img src=x onerror=alert(1)>',
            gates=make_gates(pay="unknown", product="unknown"),
            reasons=["Break out </script><script>alert(2)</script>"],
            salary=make_salary(
                lower=None, upper=None, currency=None, annualBase=None,
                equity=None, quote="Not published",
            ),
            product=None,
            financials=None,
            evidence=[
                {
                    "label": "Listing <b>bold</b>",
                    "url": "https://jobs.example.com/x",
                    "quote": 'Pay "not published" </script><b>boom</b>',
                    "checked": "2026-10-10",
                }
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            out = Path(tmp) / "dist"
            write_report(reports, make_report(date="2026-10-10", jobs=[hostile_job]))
            code, _, stderr = run_build(reports, out)
            self.assertEqual(code, 0, stderr)
            html_text = (out / "index.html").read_text(encoding="utf-8")

        self.assertNotIn('<script>alert("x")</script>', html_text)
        self.assertNotIn("<img src=x onerror=alert(1)>", html_text)
        self.assertNotIn("</script><script>alert(2)", html_text)
        self.assertNotIn("</script><b>boom</b>", html_text)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", html_text)

        payload_text = re.search(
            r'<script id="site-data" type="application/json">(.*?)</script>',
            html_text,
            re.S,
        ).group(1)
        self.assertNotIn("</script><script>", payload_text)
        self.assertIn("\\u003c", payload_text)
        json.loads(payload_text)


class BuildFailureTest(unittest.TestCase):
    """A bad report must never replace good output."""

    def test_invalid_report_fails_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            out = Path(tmp) / "dist"
            write_report(reports, make_report(date="2026-10-10"))
            code, _, stderr = run_build(reports, out)
            self.assertEqual(code, 0, stderr)
            before = _tree_bytes(out)

            bad = make_report(date="2026-10-11")
            bad["jobs"][0]["status"] = "maybe"
            write_report(reports, bad)

            code, _, stderr = run_build(reports, out)
            self.assertEqual(code, 1)
            self.assertIn("status", stderr)
            self.assertEqual(_tree_bytes(out), before)

    def test_snake_case_report_fails_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            out = Path(tmp) / "dist"
            report = make_report(date="2026-10-10")
            report["jobs"][0]["listing_url"] = report["jobs"][0].pop("listingUrl")
            write_report(reports, report)
            code, _, stderr = run_build(reports, out)
            self.assertEqual(code, 1)
            self.assertIn("unknown field(s)", stderr)
            self.assertFalse(out.exists())

    def test_path_traversal_id_fails_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            out = Path(tmp) / "dist"
            report = make_report(date="2026-10-10")
            report["jobs"][0]["id"] = "../../evil"
            write_report(reports, report)
            code, _, stderr = run_build(reports, out)
            self.assertEqual(code, 1)
            self.assertIn("lowercase slug", stderr)
            self.assertFalse(out.exists())

    def test_filename_date_mismatch_fails_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            out = Path(tmp) / "dist"
            write_report(reports, make_report(date="2026-10-10"), filename="2026-10-11.json")
            code, _, stderr = run_build(reports, out)
            self.assertEqual(code, 1)
            self.assertIn("must match report date", stderr)

    def test_missing_reports_directory_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = run_build(Path(tmp) / "nope", Path(tmp) / "dist")
            self.assertEqual(code, 1)
            self.assertIn("report directory not found", stderr)

    def test_validate_only_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            code, stdout, stderr = run_build(SEED_REPORTS, out, validate_only=True)
            self.assertEqual(code, 0, stderr)
            self.assertIn("validate-only", stdout)
            self.assertFalse(out.exists())


class BuildCliTest(unittest.TestCase):
    def test_cli_validate_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            out_buf, err_buf = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                code = build_site.main(
                    ["--reports", str(SEED_REPORTS), "--output", str(out), "--validate-only"]
                )
            self.assertEqual(code, 0, err_buf.getvalue())
            self.assertFalse(out.exists())

    def test_cli_build_with_base_url_adds_canonical_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            out_buf, err_buf = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                code = build_site.main(
                    [
                        "--reports", str(SEED_REPORTS),
                        "--output", str(out),
                        "--base-url", "https://example.github.io/job-alerts",
                    ]
                )
            self.assertEqual(code, 0, err_buf.getvalue())
            index = (out / "index.html").read_text(encoding="utf-8")
            report = (out / "reports" / "2026-10-10.html").read_text(encoding="utf-8")
            self.assertIn(
                '<link rel="canonical" href="https://example.github.io/job-alerts/index.html">',
                index,
            )
            self.assertIn(
                '<link rel="canonical" href="https://example.github.io/job-alerts/reports/2026-10-10.html">',
                report,
            )


class CategorizationTest(unittest.TestCase):
    def test_salary_categories(self):
        cases = [
            (make_salary(currency="EUR", annualBase=True, lower=80000), "eur-annual"),
            (make_salary(currency="USD", annualBase=True, lower=140000), "non-eur-annual"),
            (make_salary(currency="EUR", annualBase=None, lower=85900), "numeric-unconfirmed"),
            (
                make_salary(
                    lower=None, upper=None, currency=None, annualBase=None,
                    equity=None, quote="Not published",
                ),
                "unknown",
            ),
        ]
        for salary, expected in cases:
            job = make_job(salary=salary)
            self.assertEqual(build_site.salary_category(job), expected)

    def test_equity_values(self):
        self.assertEqual(build_site.equity_value(make_job(salary=make_salary(equity=True))), "yes")
        self.assertEqual(build_site.equity_value(make_job(salary=make_salary(equity=False))), "no")
        self.assertEqual(build_site.equity_value(make_job(salary=make_salary(equity=None))), "unknown")

    def test_counts(self):
        report = make_report(
            jobs=[
                make_job(id="one"),
                make_job(id="two", status="rejected", gates=make_gates(pay="unknown", product="unknown"),
                         reasons=["No figures."], product=None, financials=None),
            ]
        )
        self.assertEqual(build_site.counts(report), (1, 2))

    def test_seed_job_categories(self):
        expected = {
            ("2026-10-10", "pigment-genai"): ("eur-annual", "yes"),
            ("2026-10-10", "dualentry-ai"): ("non-eur-annual", "yes"),
            ("2026-10-11", "cohere-safety"): ("unknown", "unknown"),
            ("2026-10-11", "parsio-founding"): ("numeric-unconfirmed", "yes"),
            ("2026-10-13", "front-design-engineer"): ("numeric-unconfirmed", "yes"),
        }
        for (date, job_id), (salary, equity) in expected.items():
            data = json.loads((SEED_REPORTS / f"{date}.json").read_text(encoding="utf-8"))
            job = next(j for j in data["jobs"] if j["id"] == job_id)
            self.assertEqual(build_site.salary_category(job), salary, job_id)
            self.assertEqual(build_site.equity_value(job), equity, job_id)


class ContractAgreementTest(unittest.TestCase):
    """build_site.py and validate.py must enforce the same contract."""

    def test_both_validators_accept_all_seed_reports(self):
        for directory in (SEED_REPORTS, LIVE_REPORTS):
            for path in report_files(directory):
                _, build_errors = build_site.load_report(path)
                self.assertEqual(build_errors, [], f"build_site rejects {path.name}")
                self.assertEqual(validate.load_and_validate(path), [], f"validate rejects {path.name}")

    def test_both_validators_reject_the_same_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = make_report(date="2026-10-10")
            report["jobs"][0]["gates"]["ai"] = "nope"
            path = write_report(Path(tmp), report)
            self.assertTrue(build_site.load_report(path)[1])
            self.assertTrue(validate.load_and_validate(path)[1])


class StaticResourcesTest(unittest.TestCase):
    def test_app_js_avoids_unsafe_dom_and_storage(self):
        app_js = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", app_js)
        self.assertNotIn("localStorage", app_js)
        self.assertNotIn("sessionStorage", app_js)
        self.assertNotIn("document.cookie", app_js)
        self.assertIn("replaceState", app_js)

    def test_report_schema_declares_camel_case_contract(self):
        schema = json.loads((ROOT / "assets" / "report.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$defs"]["job"]["required"][:3], ["id", "company", "title"])
        self.assertIn("listingUrl", schema["$defs"]["job"]["properties"])
        self.assertNotIn("listing_url", schema["$defs"]["job"]["properties"])
        self.assertEqual(
            set(schema["$defs"]["job"]["properties"]["gates"]["properties"]),
            {"open", "location", "ai", "pay", "product"},
        )

    def test_template_has_no_analytics(self):
        template = (ROOT / "assets" / "template.html").read_text(encoding="utf-8")
        lowered = template.lower()
        for needle in (
            "googletagmanager",
            "google-analytics",
            "plausible",
            "matomo",
            "segment.com",
            "document.cookie",
            'src="http',
        ):
            self.assertNotIn(needle, lowered, f"analytics/remote reference: {needle}")


class OutputOwnershipGuardTest(unittest.TestCase):
    """Existing outputs are replaced only when demonstrably renderer-owned."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def assertRefusedIntact(self, out: Path, needle: str):
        before = _tree_bytes(out) if out.is_dir() else None
        code, _, stderr = run_build(SEED_REPORTS, out)
        self.assertEqual(code, 1, stderr)
        self.assertIn("refusing", stderr)
        self.assertIn(needle, stderr)
        if before is not None:
            self.assertEqual(_tree_bytes(out), before)
        # Nothing was staged or backed up next to the refused target.
        siblings = [p.name for p in out.parent.iterdir() if "-staging-" in p.name
                    or "-backup-" in p.name]
        self.assertEqual(siblings, [])

    def test_unowned_folder_with_user_files_is_refused_and_preserved(self):
        out = self.tmp / "my-documents"
        (out / "notes").mkdir(parents=True)
        (out / "thesis.txt").write_text("irreplaceable", encoding="utf-8")
        (out / "notes" / "a.md").write_text("keep", encoding="utf-8")
        self.assertRefusedIntact(out, "ownership marker")

    def test_legacy_lookalike_with_one_unrelated_file_is_refused(self):
        legacy = self.tmp / "legacy"
        self.assertEqual(run_build(SEED_REPORTS, legacy)[0], 0)
        (legacy / build_site.OWNER_MARKER).unlink()
        (legacy / "reports" / "private-notes.txt").write_text("mine", encoding="utf-8")
        self.assertRefusedIntact(legacy, "reports/private-notes.txt")

    def test_legacy_index_not_from_renderer_is_refused(self):
        out = self.tmp / "site"
        out.mkdir()
        (out / "index.html").write_text("<p>hand-written</p>", encoding="utf-8")
        (out / "archive.html").write_text("<p>hand-written</p>", encoding="utf-8")
        self.assertRefusedIntact(out, "not produced by this renderer")

    def test_forged_or_symlinked_marker_is_not_trusted(self):
        out = self.tmp / "forged"
        out.mkdir()
        (out / "keep.txt").write_text("keep", encoding="utf-8")
        (out / build_site.OWNER_MARKER).write_text("{}", encoding="utf-8")
        self.assertRefusedIntact(out, "ownership marker")

        real = self.tmp / "real-marker"
        real.write_text(build_site.OWNER_MARKER_TEXT, encoding="utf-8")
        (out / build_site.OWNER_MARKER).unlink()
        os.symlink(real, out / build_site.OWNER_MARKER)
        self.assertRefusedIntact(out, "ownership marker")

    def test_exact_legacy_generated_layout_is_replaced(self):
        legacy = self.tmp / "legacy"
        self.assertEqual(run_build(SEED_REPORTS, legacy)[0], 0)
        (legacy / build_site.OWNER_MARKER).unlink()
        code, _, stderr = run_build(SEED_REPORTS, legacy)
        self.assertEqual(code, 0, stderr)
        self.assertTrue((legacy / build_site.OWNER_MARKER).is_file())

    def test_symlink_output_target_is_refused_and_victim_untouched(self):
        victim = self.tmp / "victim"
        victim.mkdir()
        (victim / "data.txt").write_text("precious", encoding="utf-8")
        link = self.tmp / "dist-link"
        os.symlink(victim, link, target_is_directory=True)
        self.assertRefusedIntact(link, "symlink")
        self.assertTrue(link.is_symlink())
        self.assertEqual((victim / "data.txt").read_text(encoding="utf-8"), "precious")

    def test_symlink_to_owned_output_is_still_refused(self):
        owned = self.tmp / "owned"
        self.assertEqual(run_build(SEED_REPORTS, owned)[0], 0)
        link = self.tmp / "alias"
        os.symlink(owned, link, target_is_directory=True)
        self.assertRefusedIntact(link, "symlink")
        self.assertTrue(link.is_symlink())

    def test_symlink_parent_component_is_refused_before_resolution(self):
        real_parent = self.tmp / "real-parent"
        real_parent.mkdir()
        os.symlink(real_parent, self.tmp / "parent-link", target_is_directory=True)
        out = self.tmp / "parent-link" / "dist"
        code, _, stderr = run_build(SEED_REPORTS, out)
        self.assertEqual(code, 1)
        self.assertIn("symlink", stderr)
        self.assertEqual(list(real_parent.iterdir()), [])

    def test_symlink_into_repository_sources_is_refused(self):
        link = self.tmp / "sneaky"
        os.symlink(ROOT / "data", link, target_is_directory=True)
        before = sorted(p.name for p in (ROOT / "data").iterdir())
        code, _, stderr = run_build(SEED_REPORTS, link)
        self.assertEqual(code, 1)
        self.assertIn("symlink", stderr)
        self.assertEqual(sorted(p.name for p in (ROOT / "data").iterdir()), before)

    def test_existing_file_output_is_refused(self):
        target = self.tmp / "file-out"
        target.write_text("not a folder", encoding="utf-8")
        code, _, stderr = run_build(SEED_REPORTS, target)
        self.assertEqual(code, 1)
        self.assertIn("not a directory", stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "not a folder")


class OutputReplacementRollbackTest(unittest.TestCase):
    """The previous good output survives a failed install, byte for byte."""

    def test_failed_install_rename_restores_previous_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            out = tmp / "dist"
            code, _, stderr = run_build(SEED_REPORTS, out)
            self.assertEqual(code, 0, stderr)
            before = _tree_bytes(out)

            reports = tmp / "reports"
            write_report(reports, make_report(date="2026-10-10"))
            with mock.patch.object(
                build_site, "_install_staging", side_effect=OSError("injected install failure")
            ):
                code, _, stderr = run_build(reports, out)

            self.assertEqual(code, 1)
            self.assertIn("injected install failure", stderr)
            self.assertIn("previous output was restored unchanged", stderr)
            self.assertEqual(_tree_bytes(out), before)
            leftovers = sorted(p.name for p in tmp.iterdir())
            self.assertEqual(leftovers, ["dist", "reports"])

    def test_failed_rollback_reports_where_old_output_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            out = tmp / "dist"
            self.assertEqual(run_build(SEED_REPORTS, out)[0], 0)
            before = _tree_bytes(out)
            real_rename = Path.rename

            def flaky_rename(self_path, target):
                if "-backup-" in self_path.name:
                    raise OSError("injected rollback failure")
                return real_rename(self_path, target)

            with mock.patch.object(
                build_site, "_install_staging", side_effect=OSError("injected install failure")
            ), mock.patch.object(Path, "rename", flaky_rename):
                code, _, stderr = run_build(SEED_REPORTS, out)

            self.assertEqual(code, 1)
            self.assertIn("rollback failed", stderr)
            backups = [p for p in tmp.iterdir() if "-backup-" in p.name]
            self.assertEqual(len(backups), 1)
            self.assertIn(str(backups[0]), stderr)
            self.assertEqual(_tree_bytes(backups[0]), before)

    def test_successful_rebuild_removes_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            out = tmp / "dist"
            self.assertEqual(run_build(SEED_REPORTS, out)[0], 0)
            code, _, stderr = run_build(SEED_REPORTS, out)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(sorted(p.name for p in tmp.iterdir()), ["dist"])


def _hero(html_text: str) -> str:
    return re.search(r'<div class="hero-report">.*?<p class="hero-links">.*?</p>\s*</div>', html_text, re.S).group(0)


def _without_details(html_text: str) -> str:
    return re.sub(r"<details.*?</details>", "", html_text, flags=re.S)


class ResultBlockTest(unittest.TestCase):
    """Shared home/report hero: status, K of N, decision and visible caveats."""

    def build_pages(self, report: dict) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            out = Path(tmp) / "dist"
            write_report(reports, report)
            code, _, stderr = run_build(reports, out)
            self.assertEqual(code, 0, stderr)
            home = (out / "index.html").read_text(encoding="utf-8")
            page = (out / "reports" / f"{report['date']}.html").read_text(encoding="utf-8")
        return home, page

    def rejected(self, job_id: str) -> dict:
        return make_job(id=job_id, status="rejected", gates=make_gates(pay="fail"))

    def assertBoth(self, pages, needle):
        for html_text in pages:
            self.assertIn(needle, _hero(html_text))

    def test_zero_qualified_is_none(self):
        pages = self.build_pages(
            make_report(jobs=[self.rejected("a-role")], requestedCount=3, decision="Hold.")
        )
        self.assertBoth(pages, '<strong class="result-value">None</strong>')
        self.assertBoth(pages, "<strong>0</strong> qualified</span> of "
                               '<span class="stat"><strong>3</strong> requested</span>')
        self.assertBoth(pages, "Zero roles passed every recorded gate")

    def test_partial_shows_actual_k_of_n(self):
        jobs = [make_job(id="q-one"), self.rejected("r-one")]
        pages = self.build_pages(make_report(jobs=jobs, requestedCount=3, decision="Hold."))
        self.assertBoth(pages, '<strong class="result-value">Partial</strong>')
        self.assertBoth(pages, "<strong>1</strong> qualified</span> of "
                               '<span class="stat"><strong>3</strong> requested</span>')
        self.assertBoth(pages, "<strong>2</strong> examined")

    def test_complete_when_k_meets_n(self):
        jobs = [make_job(id="q-one"), make_job(id="q-two")]
        pages = self.build_pages(make_report(jobs=jobs, requestedCount=2, decision="Go."))
        self.assertBoth(pages, '<strong class="result-value">Complete</strong>')
        self.assertBoth(pages, "<strong>2</strong> qualified</span> of "
                               '<span class="stat"><strong>2</strong> requested</span>')

    def test_missing_denominator_is_explicitly_unrecorded_not_guessed(self):
        pages = self.build_pages(make_report(jobs=[make_job(id="q-one")]))
        self.assertBoth(pages, '<strong class="result-value">Undetermined</strong>')
        self.assertBoth(pages, "requested count not recorded")
        for html_text in pages:
            self.assertNotIn("requested</span>", _hero(html_text))
            self.assertNotIn(">Complete<", _hero(html_text))
            self.assertNotIn(">Partial<", _hero(html_text))
        zero = self.build_pages(make_report(jobs=[self.rejected("r-one")]))
        self.assertBoth(zero, '<strong class="result-value">None</strong>')
        self.assertBoth(zero, "requested count not recorded")

    def test_decision_recorded_or_explicitly_not_recorded(self):
        pages = self.build_pages(make_report(decision="Recheck before applying."))
        self.assertBoth(pages, "Decision</span>Recheck before applying.</p>")
        pages = self.build_pages(make_report())
        self.assertBoth(pages, "Decision not recorded in this report.")

    def test_caveats_and_recheck_visible_without_expansion(self):
        report = make_report(
            uncertainty=["Historic check only; employer page may have changed."],
            requestedCount=1,
        )
        for html_text in self.build_pages(report):
            visible = _without_details(_hero(html_text))
            self.assertIn("Historic check only; employer page may have changed.", visible)
            self.assertIn("Independent recheck", visible)
            self.assertIn("2026-10-10", visible)

    def test_hero_strings_are_escaped(self):
        report = make_report(
            summary='<script>alert("s")</script>',
            decision="<img src=x onerror=alert(1)>",
            uncertainty=["</ul><b>break</b>"],
        )
        for html_text in self.build_pages(report):
            hero = _hero(html_text)
            self.assertNotIn("<script>alert", hero)
            self.assertNotIn("<img src=x", hero)
            self.assertNotIn("</ul><b>", hero)
            self.assertIn("&lt;img src=x onerror=alert(1)&gt;", hero)
            self.assertIn("&lt;/ul&gt;&lt;b&gt;break&lt;/b&gt;", hero)

    def test_home_and_report_link_download_json_export(self):
        home, page = self.build_pages(make_report(date="2026-10-11"))
        self.assertIn('href="reports/2026-10-11.json" download>Download JSON</a>', _hero(home))
        self.assertIn('href="../reports/2026-10-11.json" download>Download JSON</a>', _hero(page))

    def test_renderer_has_no_delivery_or_local_only_wording(self):
        source = Path(build_site.__file__).read_text(encoding="utf-8").lower()
        for needle in ("unpublished delivery", "local-only", "not delivered", "telegram"):
            self.assertNotIn(needle, source)


if __name__ == "__main__":
    unittest.main()
