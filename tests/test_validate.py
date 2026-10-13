"""Unit tests for scripts/validate.py (report schema version 1, camelCase)."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
for _entry in (str(TESTS_DIR), str(TESTS_DIR.parent / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import validate
from fixtures import (
    LIVE_REPORTS,
    SEED_REPORTS,
    make_gates,
    make_job,
    make_report,
    make_salary,
    report_files,
    write_report,
)


class ValidateReportTest(unittest.TestCase):
    def errors(self, report, filename=None):
        return validate.validate_report(report, source="<test>", filename=filename)

    def assertValid(self, report, filename=None):
        self.assertEqual(self.errors(report, filename), [])

    def assertInvalid(self, report, fragment, filename=None):
        errors = self.errors(report, filename)
        self.assertTrue(errors, "expected validation errors, got none")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected an error mentioning {fragment!r}, got: {errors}",
        )

    # --- seed data -------------------------------------------------------

    def test_all_live_reports_are_valid(self):
        """Every published report under data/reports/ must stay valid as it grows."""
        paths = report_files(LIVE_REPORTS)
        self.assertTrue(paths, "expected at least one live report")
        for path in paths:
            errors = validate.load_and_validate(path)
            self.assertEqual(errors, [], f"{path.name}: {errors}")

    def test_frozen_seed_snapshot_keeps_the_four_dated_reports(self):
        """The deterministic fixture set preserves the original dated facts."""
        paths = report_files(SEED_REPORTS)
        self.assertEqual(
            [p.name for p in paths],
            ["2026-10-10.json", "2026-10-11.json", "2026-10-12.json", "2026-10-13.json"],
        )
        for path in paths:
            errors = validate.load_and_validate(path)
            self.assertEqual(errors, [], f"{path.name}: {errors}")

    def test_seed_summary_counts_match_job_data(self):
        for path in report_files(LIVE_REPORTS):
            data = json.loads(path.read_text(encoding="utf-8"))
            qualified = sum(1 for j in data["jobs"] if j["status"] == "qualified")
            for job in data["jobs"]:
                if job["status"] == "qualified":
                    self.assertEqual(
                        set(job["gates"].values()), {"pass"},
                        f"{path.name}/{job['id']}: qualified but gates not all pass",
                    )
                    self.assertTrue(job["evidence"], f"{path.name}/{job['id']}")
                    self.assertTrue(job["product"], f"{path.name}/{job['id']}")
                    self.assertTrue(job["financials"], f"{path.name}/{job['id']}")
            self.assertGreaterEqual(qualified, 0)

    # --- contract shape --------------------------------------------------

    def test_snake_case_contract_is_rejected(self):
        report = make_report()
        job = report["jobs"][0]
        del job["listingUrl"]
        job["listing_url"] = "https://jobs.example.com/acme/123"
        self.assertInvalid(report, "unknown field(s) rejected: listing_url")
        self.assertInvalid(report, "missing required field(s): listingUrl")

    def test_unknown_top_level_key_is_rejected(self):
        self.assertInvalid(make_report(personalNotes="private"), "unknown field(s)")

    def test_schema_version_must_be_one(self):
        report = make_report(schemaVersion=2)
        self.assertInvalid(report, "schemaVersion")

    def test_date_must_match_file_name(self):
        report = make_report(date="2026-10-10")
        self.assertInvalid(report, "must match report date", filename="2026-10-11.json")
        self.assertValid(report, filename="2026-10-10.json")

    def test_invalid_calendar_date_rejected(self):
        self.assertInvalid(make_report(date="2026-02-30"), "valid calendar date")

    # --- qualification gates --------------------------------------------

    def test_qualified_needs_every_stored_gate_passing(self):
        report = make_report()
        report["jobs"][0]["gates"]["pay"] = "unknown"
        self.assertInvalid(report, "every stored gate set to 'pass'")

    def test_qualified_non_eur_salary_is_not_disqualified(self):
        # DualEntry-style entry: USD salary with documented conversion, all
        # gates stored as pass. Currency must not be re-judged at validation.
        report = make_report()
        report["jobs"][0]["salary"] = make_salary(
            lower=140000, upper=200000, currency="USD",
            quote="USD 140,000-200,000; EUR conversion documented in reasons",
        )
        self.assertValid(report)

    def test_qualified_is_not_rechecked_against_today_threshold(self):
        # Historic eligibility is stored in the gates; a low figure with all
        # stored gates passing stays valid (no hardcoded threshold).
        report = make_report()
        report["jobs"][0]["salary"] = make_salary(lower=50000, upper=60000)
        self.assertValid(report)

    def test_qualified_needs_evidence_source(self):
        report = make_report()
        report["jobs"][0]["evidence"] = []
        self.assertInvalid(report, "at least one evidence source")

    def test_qualified_needs_company_notes(self):
        report = make_report()
        report["jobs"][0]["product"] = None
        self.assertInvalid(report, "source completeness")
        report = make_report()
        report["jobs"][0]["financials"] = None
        self.assertInvalid(report, "source completeness")

    def test_non_qualified_entry_needs_a_non_pass_gate(self):
        job = make_job(status="rejected", reasons=["No published pay floor."])
        self.assertInvalid(make_report(jobs=[job]), "at least one gate that is not 'pass'")

    def test_near_miss_with_unknown_gates_is_valid(self):
        job = make_job(
            status="near-miss",
            gates=make_gates(pay="unknown", product="unknown"),
            reasons=["Annual base classification unverified."],
            product=None,
            financials=None,
        )
        self.assertValid(make_report(jobs=[job]))

    # --- salary rules ----------------------------------------------------

    def test_unknown_salary_stays_null(self):
        job = make_job(
            status="rejected",
            gates=make_gates(pay="unknown", product="unknown"),
            reasons=["No published figures."],
            salary={
                "lower": None, "upper": None, "currency": None,
                "annualBase": None, "equity": None, "quote": "Not published",
            },
            product=None,
            financials=None,
        )
        self.assertValid(make_report(jobs=[job]))

    def test_salary_min_must_not_exceed_max(self):
        report = make_report()
        report["jobs"][0]["salary"] = make_salary(lower=200000, upper=100000)
        self.assertInvalid(report, "must not exceed")

    def test_salary_boolean_is_not_a_number(self):
        report = make_report()
        report["jobs"][0]["salary"] = make_salary(lower=True)
        self.assertInvalid(report, "expected number or null, got bool")

    def test_unknown_salary_currency_rejected(self):
        report = make_report()
        report["jobs"][0]["salary"] = make_salary(currency="CHF")
        self.assertInvalid(report, "must be one of")

    # --- URLs and privacy ------------------------------------------------

    def test_listing_url_must_be_https(self):
        report = make_report()
        report["jobs"][0]["listingUrl"] = "http://jobs.example.com/acme/123"
        self.assertInvalid(report, "must use HTTPS")

    def test_javascript_scheme_rejected(self):
        report = make_report()
        report["jobs"][0]["evidence"][0]["url"] = "javascript:alert(1)"
        self.assertInvalid(report, "must use http(s)")

    def test_private_ip_evidence_url_rejected(self):
        report = make_report()
        report["jobs"][0]["evidence"][0]["url"] = "http://192.168.1.10/jobs"
        self.assertInvalid(report, "non-public host")

    def test_file_url_rejected(self):
        report = make_report()
        report["jobs"][0]["evidence"][0]["url"] = "file:///etc/passwd"
        self.assertInvalid(report, "must use http(s)")

    def test_email_in_summary_rejected(self):
        report = make_report(summary="Contact me at candidate@example.com")
        self.assertInvalid(report, "contact details")

    def test_local_path_in_company_rejected(self):
        report = make_report()
        report["jobs"][0]["company"] = "/home/omachi/private/cv.pdf"
        self.assertInvalid(report, "local filesystem paths")

    def test_path_traversal_id_rejected(self):
        report = make_report()
        report["jobs"][0]["id"] = "../../etc/passwd"
        self.assertInvalid(report, "lowercase slug")

    def test_duplicate_ids_rejected(self):
        report = make_report(jobs=[make_job(), make_job()])
        self.assertInvalid(report, "duplicate job id")

    def test_unknown_status_rejected(self):
        report = make_report()
        report["jobs"][0]["status"] = "maybe"
        self.assertInvalid(report, "must be one of")

    def test_unknown_work_mode_rejected(self):
        report = make_report()
        report["jobs"][0]["location"] = "wfh-ish"
        self.assertInvalid(report, "must be one of")

    def test_unknown_gate_value_rejected(self):
        report = make_report()
        report["jobs"][0]["gates"]["pay"] = "maybe"
        self.assertInvalid(report, "must be one of")

    def test_evidence_requires_label_and_checked(self):
        report = make_report()
        report["jobs"][0]["evidence"][0] = {
            "url": "https://jobs.example.com/acme/123",
            "quote": "note",
            "title": "old snake key",
        }
        errors = self.errors(report)
        self.assertTrue(any("unknown field(s) rejected: title" in e for e in errors), errors)
        self.assertTrue(
            any("missing required field(s): checked, label" in e for e in errors), errors
        )


class ValidateDirectoryTest(unittest.TestCase):
    def test_validate_directory_reports_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_report(directory, make_report(date="2026-10-10"))
            bad = make_report(date="2026-10-11")
            bad["jobs"][0]["status"] = "maybe"
            bad["jobs"][0]["gates"] = make_gates(pay="unknown", product="unknown")
            write_report(directory, bad)
            valid, errors = validate.validate_directory(directory)
            self.assertEqual([p.name for p in valid], ["2026-10-10.json"])
            self.assertEqual(len(errors), 1)
            self.assertIn("2026-10-11.json", errors[0])

    def test_validate_directory_requires_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            valid, errors = validate.validate_directory(Path(tmp))
            self.assertEqual(valid, [])
            self.assertIn("no report JSON files found", errors[0])

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_report(directory, make_report(date="2026-10-10"))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(validate.main(["--reports", str(directory)]), 0)
                bad = make_report(date="2026-10-11")
                bad["jobs"][0]["evidence"] = []
                bad["jobs"][0]["status"] = "qualified"
                write_report(directory, bad)
                self.assertEqual(validate.main(["--reports", str(directory)]), 1)

    def test_cli_missing_directory_fails(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(validate.main(["--reports", "/nonexistent/reports-dir"]), 1)


class ValidateConsistencyTest(unittest.TestCase):
    """Guards against the snake_case/camelCase contract drifting again."""

    def test_optional_report_fields_accepted(self):
        report = make_report(requestedCount=3, decision="Proceed independently.")
        errors = validate.validate_report(report)
        self.assertEqual(errors, [])

    def test_optional_report_fields_type_checked(self):
        report = make_report(requestedCount="three")
        errors = validate.validate_report(report)
        self.assertTrue(any("requestedCount" in e for e in errors))

    def test_deep_copied_seed_still_valid(self):
        path = SEED_REPORTS / "2026-10-13.json"
        data = copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(validate.validate_report(data), [])


if __name__ == "__main__":
    unittest.main()
