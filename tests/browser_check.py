#!/usr/bin/env python3
"""Optional browser smoke test for the built Job Alerts site.

Uses the Playwright dev dependency (never needed by the site itself):

    pip install playwright && playwright install chromium
    python tests/browser_check.py [--reports DIR]

Two clearly separated suites:

* Seed (deterministic): builds the frozen tests/seed_reports/ snapshot and
  runs the full filter / reset / evidence / deep-link / noscript / overflow /
  console matrix at 375 / 768 / 1280 with exact expected counts. These
  assertions never change while data/reports/ grows.
* Live (dynamic): builds the real --reports directory, derives every count,
  date and row total from the report files themselves, and smoke-checks
  counters, archive rows, the report JSON export and noscript visibility.
  Nothing here hardcodes a date or total, so appended reports stay green.

* Design (deterministic): the printed-newspaper checks in
  tests/design_checks.py — both paper variants at 375 / 639 / 640 / 768 /
  1280 / 1920 on home, archive and a daily report: measured classified
  column counts (1 / 2 / 3 / 4), full-width wrappers with <=16px outer
  padding, no rounded corners / shadows / gradients / images, paper and ink
  colours only, serif fonts on every element and control, boxed HELP
  WANTED notices, justified hyphenated text, contrast (including hover,
  expanded evidence and zero states), shadow-free focus outlines, toggle
  persistence and cross-tab sync, pre-paint paper, blocked storage,
  reduced motion, noscript and print. Screenshots go to
  .work/screenshots/newspaper-* (gitignored).
* Live fonts (network): one real Google Fonts load attempt, reported as
  UNTESTED rather than passed when the network is unavailable.

Google Fonts requests are blocked in every other context, so results do
not depend on the network.

Exit codes: 0 = pass or skipped (Playwright unavailable), 1 = failure.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urljoin

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
for _entry in (str(TESTS_DIR), str(ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import build_site  # noqa: E402
from fixtures import SEED_REPORTS, load_report_data, report_files  # noqa: E402
import design_checks  # noqa: E402
from design_checks import FONT_HOSTS, new_context  # noqa: E402

VIEWPORTS = [(375, 812), (768, 1024), (1280, 900), (1920, 1080)]
LIVE_VIEWPORT = (768, 1024)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102 - silence request logging
        return


def start_server(directory: Path):
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def assert_no_overflow(page, label: str) -> None:
    metrics = page.evaluate(
        "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})"
    )
    assert metrics["sw"] <= metrics["iw"] + 1, (
        f"{label}: horizontal overflow (scrollWidth {metrics['sw']} > "
        f"innerWidth {metrics['iw']})"
    )


def counter(page) -> str:
    return page.locator("#result-count").inner_text().strip()


def expect_counter(page, wanted: str, step: str) -> None:
    got = counter(page)
    assert got == wanted, f"{step}: expected counter {wanted!r}, got {got!r}"


def record_console(page) -> list[str]:
    errors: list[str] = []

    def _record(msg):
        if msg.type == "error":
            location = (msg.location or {}).get("url", "")
            if any(host in location for host in FONT_HOSTS):
                return  # Google Fonts are blocked on purpose in these suites
            if "net::ERR_FAILED" in msg.text and not location:
                return
            if "favicon" not in msg.text.lower() and "favicon" not in location.lower():
                errors.append(msg.text)

    page.on("console", _record)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


# ---------------------------------------------------------------------------
# Seed suite — deterministic against tests/seed_reports/
# ---------------------------------------------------------------------------


def run_seed_viewport(browser, base: str, width: int, height: int) -> None:
    label = f"seed {width}x{height}"
    context = new_context(browser, viewport={"width": width, "height": height})
    page = context.new_page()
    errors = record_console(page)

    # --- home: initial scope --------------------------------------------
    page.goto(f"{base}/index.html", wait_until="load")
    expect_counter(page, "Showing 4 of 11 entries", f"{label} initial")
    assert_no_overflow(page, f"{label} index")
    hero = page.locator(".hero-report")
    assert hero.locator(".result-value").inner_text().strip().lower() == "none", (
        f"{label}: home result status not shown as None"
    )
    hero_text = hero.inner_text()
    assert "requested count not recorded" in hero_text, f"{label}: denominator label"
    assert "Decision not recorded" in hero_text, f"{label}: decision label"
    caveats = page.locator(".hero-caveats")
    assert caveats.is_visible(), f"{label}: caveats hidden on home"
    assert "Independent recheck" in caveats.inner_text(), f"{label}: recheck caveat"
    home_json = page.locator('.hero-links a[download][href="reports/2026-10-13.json"]')
    assert home_json.count() == 1, f"{label}: home Download JSON link missing"
    response = page.request.get(urljoin(page.url, home_json.get_attribute("href")))
    assert response.ok and response.json() == load_report_data(
        SEED_REPORTS / "2026-10-13.json"
    ), f"{label}: home JSON export differs from source"

    # --- search ----------------------------------------------------------
    page.fill("#filter-search", "doctolib")
    expect_counter(page, "Showing 1 of 11 entries", f"{label} search")
    assert "q=doctolib" in page.url, f"{label}: search state missing from URL"
    page.fill("#filter-search", "no-such-company-anywhere")
    expect_counter(page, "Showing 0 of 11 entries", f"{label} search zero matches")
    assert page.locator("#no-results").is_visible(), f"{label}: no-results message hidden"
    page.click("#filter-reset")
    expect_counter(page, "Showing 4 of 11 entries", f"{label} reset after search")
    assert "q=" not in page.url, f"{label}: query kept after Reset"
    page.fill("#filter-search", "")

    # --- status ----------------------------------------------------------
    page.select_option("#filter-status", "qualified")
    expect_counter(page, "Showing 0 of 11 entries", f"{label} status qualified in scope")
    assert page.locator("#qualified-empty-data").is_visible(), (
        f"{label}: zero-qualified state not shown for this date scope"
    )
    assert page.locator("#no-results").is_visible(), f"{label}: no-results message hidden"
    page.click("#filter-reset")

    # --- date + remote + salary + equity (AND semantics) -----------------
    page.select_option("#filter-date", "all")
    expect_counter(page, "Showing 11 of 11 entries", f"{label} date all")
    page.select_option("#filter-mode", "remote")
    expect_counter(page, "Showing 5 of 11 entries", f"{label} remote")
    page.select_option("#filter-salary", "eur-annual")
    expect_counter(page, "Showing 1 of 11 entries", f"{label} remote + eur-annual")
    page.select_option("#filter-equity", "yes")
    expect_counter(page, "Showing 1 of 11 entries", f"{label} + equity yes")
    page.select_option("#filter-status", "qualified")
    expect_counter(page, "Showing 1 of 11 entries", f"{label} + qualified")
    visible_keys = page.eval_on_selector_all(
        ".job-card:not([hidden])", "els => els.map(e => e.dataset.key)"
    )
    assert visible_keys == ["2026-10-10/pigment-genai"], (
        f"{label}: unexpected AND-filter result {visible_keys}"
    )
    for param in ("mode=remote", "salary=eur-annual", "equity=yes", "status=qualified"):
        assert param in page.url, f"{label}: {param} missing from URL query state"

    # --- no-match salary evidence ---------------------------------------
    page.click("#filter-reset")
    page.select_option("#filter-date", "all")
    page.select_option("#filter-salary", "non-eur-annual")
    expect_counter(page, "Showing 1 of 11 entries", f"{label} non-EUR annual only")
    non_eur = page.eval_on_selector_all(
        ".job-card:not([hidden])", "els => els.map(e => e.dataset.key)"
    )
    assert non_eur == ["2026-10-10/dualentry-ai"], f"{label}: {non_eur}"
    page.click("#filter-reset")

    # --- details panels --------------------------------------------------
    details = page.locator("#job-2026-10-13-doctolib-applied-ai details").first
    details.locator("summary").click()
    assert details.evaluate("el => el.open"), f"{label}: details did not expand"
    details.locator("summary").click()
    assert not details.evaluate("el => el.open"), f"{label}: details did not collapse"

    # --- report page + JSON export --------------------------------------
    page.goto(f"{base}/reports/2026-10-11.html", wait_until="load")
    expect_counter(page, "Showing 2 of 11 entries", f"{label} report scope")
    stat_line = page.locator(".hero-report .stat-line").inner_text()
    assert "0 qualified of 3 requested" in stat_line, f"{label}: K of N missing ({stat_line})"
    assert page.locator(".hero-caveats").is_visible(), f"{label}: report caveats hidden"
    assert page.locator("#qualified-empty-data").is_visible(), (
        f"{label}: zero-qualified state missing on 2026-10-11"
    )
    assert page.locator('link[rel="stylesheet"][href$="assets/style.css"]').count() == 1
    download = page.locator('a[download][href$="2026-10-11.json"]')
    assert download.count() == 1, f"{label}: Download JSON link missing"
    assert download.inner_text().strip() == "Download JSON", f"{label}: link text"
    json_href = download.get_attribute("href")
    response = page.request.get(urljoin(page.url, json_href))
    assert response.ok, f"{label}: JSON export fetch failed ({response.status})"
    source = load_report_data(SEED_REPORTS / "2026-10-11.json")
    assert response.json() == source, f"{label}: JSON export differs from source"
    assert_no_overflow(page, f"{label} report page")

    # --- archive ---------------------------------------------------------
    page.goto(f"{base}/archive.html", wait_until="load")
    rows = page.locator(".archive-table tbody tr").count()
    assert rows == 4, f"{label}: archive rows {rows}"
    assert_no_overflow(page, f"{label} archive")

    # --- deep link reveals a hidden-card entry ---------------------------
    page.goto(f"{base}/archive.html", wait_until="load")
    page.goto(f"{base}/index.html#job-2026-10-10-pigment-genai", wait_until="load")
    target = page.locator("#job-2026-10-10-pigment-genai")
    assert target.is_visible(), f"{label}: deep-linked card stayed hidden"
    assert page.locator("#filter-date").input_value() == "all", (
        f"{label}: deep link did not widen the date scope"
    )

    real_errors = [e for e in errors if "favicon" not in e.lower()]
    assert not real_errors, f"{label}: console/page errors: {real_errors}"
    context.close()


def run_seed_noscript(browser, base: str) -> None:
    context = new_context(browser, java_script_enabled=False, viewport={"width": 768, "height": 1024})
    page = context.new_page()
    page.goto(f"{base}/index.html", wait_until="load")
    visible = page.eval_on_selector_all(
        ".job-card", "els => els.filter(e => e.offsetParent !== null).length"
    )
    assert visible == 11, f"seed noscript: expected all 11 entries visible, got {visible}"
    assert page.locator(".noscript-note").is_visible(), "seed noscript: note not shown"
    assert not page.locator("#filter-bar").is_visible(), "seed noscript: filter bar still shown"
    context.close()


# ---------------------------------------------------------------------------
# Live suite — every count derived from the growing --reports directory
# ---------------------------------------------------------------------------


def live_facts(reports_dir: Path) -> dict:
    """Derive dates, per-date job counts and totals from live report files."""
    paths = report_files(reports_dir)
    assert paths, f"no live report files found in {reports_dir}"
    data = {path.stem: load_report_data(path) for path in paths}
    dates = sorted(data)
    jobs = {date: len(data[date]["jobs"]) for date in dates}
    return {
        "dates": dates,
        "jobs": jobs,
        "total": sum(jobs.values()),
        "latest": dates[-1],
    }


def run_live_checks(browser, base: str, reports_dir: Path) -> None:
    facts = live_facts(reports_dir)
    dates, jobs, total, latest = facts["dates"], facts["jobs"], facts["total"], facts["latest"]
    label = "live (dynamic)"
    width, height = LIVE_VIEWPORT
    context = new_context(browser, viewport={"width": width, "height": height})
    page = context.new_page()
    errors = record_console(page)

    # --- home scope derives from the newest live report ------------------
    page.goto(f"{base}/index.html", wait_until="load")
    expect_counter(page, f"Showing {jobs[latest]} of {total} entries", f"{label} initial")
    assert_no_overflow(page, f"{label} index")
    assert page.locator(".hero-caveats").is_visible(), f"{label}: caveats hidden on home"
    home_json = page.locator(f'.hero-links a[download][href="reports/{latest}.json"]')
    assert home_json.count() == 1, f"{label}: home Download JSON link missing"
    response = page.request.get(urljoin(page.url, home_json.get_attribute("href")))
    assert response.ok and response.json() == load_report_data(
        reports_dir / f"{latest}.json"
    ), f"{label}: home JSON export differs from {latest}.json source"

    # --- search with a term that matches nothing -------------------------
    page.fill("#filter-search", "zzz-no-such-company-zzz")
    expect_counter(page, f"Showing 0 of {total} entries", f"{label} zero search")
    assert page.locator("#no-results").is_visible(), f"{label}: no-results hidden"
    page.click("#filter-reset")
    page.fill("#filter-search", "")

    # --- every-dates scope shows the derived total -----------------------
    page.select_option("#filter-date", "all")
    expect_counter(page, f"Showing {total} of {total} entries", f"{label} date all")
    page.click("#filter-reset")

    # --- a report page scopes to its own derived counts ------------------
    page.goto(f"{base}/reports/{latest}.html", wait_until="load")
    expect_counter(page, f"Showing {jobs[latest]} of {total} entries", f"{label} report page")
    assert_no_overflow(page, f"{label} report page")

    # --- JSON export equals the live source file -------------------------
    download = page.locator(f'a[download][href$="{latest}.json"]')
    assert download.count() == 1, f"{label}: Download JSON link missing on {latest}"
    href = download.get_attribute("href")
    response = page.request.get(urljoin(page.url, href))
    assert response.ok, f"{label}: JSON export fetch failed ({response.status})"
    assert response.json() == load_report_data(reports_dir / f"{latest}.json"), (
        f"{label}: JSON export differs from {latest}.json source"
    )

    # --- archive row count derives from the file set ---------------------
    page.goto(f"{base}/archive.html", wait_until="load")
    rows = page.locator(".archive-table tbody tr").count()
    assert rows == len(dates), f"{label}: archive rows {rows}, expected {len(dates)}"
    assert_no_overflow(page, f"{label} archive")

    real_errors = [e for e in errors if "favicon" not in e.lower()]
    assert not real_errors, f"{label}: console/page errors: {real_errors}"
    context.close()

    # --- noscript shows every live entry ---------------------------------
    noscript = new_context(
        browser, java_script_enabled=False, viewport={"width": width, "height": height}
    )
    npage = noscript.new_page()
    npage.goto(f"{base}/index.html", wait_until="load")
    visible = npage.eval_on_selector_all(
        ".job-card", "els => els.filter(e => e.offsetParent !== null).length"
    )
    assert visible == total, f"{label} noscript: expected {total} entries visible, got {visible}"
    assert npage.locator(".noscript-note").is_visible(), f"{label} noscript: note not shown"
    noscript.close()


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reports",
        default=str(ROOT / "data" / "reports"),
        help="live report directory exercised by the dynamic suite "
             "(default: data/reports)",
    )
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: Playwright is not installed (optional dev dependency).")
        return 0

    live_reports = Path(args.reports)
    facts = live_facts(live_reports)
    print(
        f"Live data: {len(facts['dates'])} report(s), {facts['total']} entries, "
        f"latest {facts['latest']} (counts derived dynamically)"
    )

    with tempfile.TemporaryDirectory() as tmp:
        seed_dist = Path(tmp) / "seed-dist"
        live_dist = Path(tmp) / "live-dist"
        if build_site.build(SEED_REPORTS, seed_dist, "", False) != 0:
            print("FAIL: seed fixture build failed", file=sys.stderr)
            return 1
        if build_site.build(live_reports, live_dist, "", False) != 0:
            print("FAIL: live data build failed", file=sys.stderr)
            return 1

        seed_server = start_server(seed_dist)
        live_server = start_server(live_dist)
        seed_base = f"http://127.0.0.1:{seed_server.server_address[1]}"
        live_base = f"http://127.0.0.1:{live_server.server_address[1]}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                print("\n-- Seed suite (deterministic fixtures, exact counts) --")
                for width, height in VIEWPORTS:
                    run_seed_viewport(browser, seed_base, width, height)
                    print(f"OK   seed {width}x{height}: filters, links, JSON export, "
                          "no overflow, no console errors")
                run_seed_noscript(browser, seed_base)
                print("OK   seed noscript: full content rendered without JavaScript")

                print("\n-- Live suite (growing data, all counts derived) --")
                run_live_checks(browser, live_base, live_reports)
                print(f"OK   live {LIVE_VIEWPORT[0]}x{LIVE_VIEWPORT[1]}: derived counters, "
                      f"{facts['total']} entries, archive rows, JSON export, noscript")

                print("\n-- Design suite (printed newspaper, both paper variants, measured) --")
                for line in design_checks.run_theme_matrix(browser, seed_base):
                    print(line)
                for line in design_checks.run_toggle_checks(browser, seed_base):
                    print(line)
                for line in design_checks.run_motion_noscript_print(browser, seed_base):
                    print(line)
                print(f"     screenshots: {design_checks.SHOTS.relative_to(ROOT)}/{design_checks.PREFIX}-*")

                print("\n-- Live fonts (real network; not deterministic) --")
                print(design_checks.run_live_font_check(browser, seed_base))
                browser.close()
        except AssertionError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        finally:
            seed_server.shutdown()
            live_server.shutdown()

    print("\nBrowser smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
