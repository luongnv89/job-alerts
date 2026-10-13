"""Regression: a new valid next-day report must need zero test changes.

The deterministic unit tests build from the frozen `tests/seed_reports/`
snapshot, so live `data/reports/` can grow every day. This test copies the
repository into an isolated directory, appends a valid *empty* next-day
report, and proves the full pipeline still passes unchanged: report
validation, a standalone site build (HTML + JSON exports) and the complete
unit-test suite itself.

The nested suite run is guarded by an environment variable so the recursion
stops at one level.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
for _entry in (str(TESTS_DIR), str(TESTS_DIR.parent / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from fixtures import LIVE_REPORTS, make_report, report_files, write_report

REPO_PARTS = ("scripts", "tests", "design", "assets", "data")
GROWTH_GUARD_ENV = "JOB_ALERTS_SKIP_GROWTH_TEST"


def _run(args, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@unittest.skipIf(
    os.environ.get(GROWTH_GUARD_ENV) == "1",
    "nested suite run (set by the outer growth regression test)",
)
class ReportGrowthRegressionTest(unittest.TestCase):
    def test_appended_empty_next_day_report_needs_no_test_changes(self):
        latest = report_files(LIVE_REPORTS)[-1].stem
        next_day = (_dt.date.fromisoformat(latest) + _dt.timedelta(days=1)).isoformat()

        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "repo"
            for name in REPO_PARTS:
                shutil.copytree(Path(TESTS_DIR).parent / name, copy / name)

            # Append a valid report with zero examined roles for the next day.
            write_report(copy / "data" / "reports", make_report(date=next_day, jobs=[]))
            self.assertEqual(report_files(copy / "data" / "reports")[-1].stem, next_day)

            env = dict(os.environ)
            env[GROWTH_GUARD_ENV] = "1"

            validate = _run(["scripts/validate.py"], cwd=copy, env=env)
            self.assertEqual(validate.returncode, 0, validate.stdout + validate.stderr)

            build = _run(
                ["scripts/build_site.py", "--output", str(copy / "dist")],
                cwd=copy,
                env=env,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            self.assertTrue((copy / "dist" / "reports" / f"{next_day}.html").is_file())
            self.assertTrue((copy / "dist" / "reports" / f"{next_day}.json").is_file())

            suite = _run(["-m", "unittest", "discover", "-s", "tests"], cwd=copy, env=env)
            self.assertEqual(suite.returncode, 0, suite.stdout + suite.stderr)


if __name__ == "__main__":
    unittest.main()
