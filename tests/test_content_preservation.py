"""Content-preservation regression for the newspaper redesign.

Compares a fresh build of data/reports/ with the pre-redesign baseline
output (NEWSPAPER_BASELINE, default the committed public-content fixture
under tests/fixtures/newspaper-baseline). The redesign may only add decorative text; every original text
string, link target and anchor label — per job notice and across each
page's <main> — must survive (for the hashed baseline report set; reports
appended later are covered by the growth tests), the inline filter payload and the report JSON
exports must be byte-identical, and the source report hashes must still
match the recorded baseline hashes.

The portable baseline is committed so preservation is exercised in CI;
missing baseline files fail rather than silently skipping the checks.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
for _entry in (str(TESTS_DIR), str(ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import build_site  # noqa: E402

BASELINE = Path(
    os.environ.get(
        "NEWSPAPER_BASELINE",
        str(TESTS_DIR / "fixtures" / "newspaper-baseline"),
    )
)
BASELINE_DIST = BASELINE / "dist"
HASHES = BASELINE / "report-hashes.json"

# The only text the redesign is allowed to add inside a job notice.
DECORATIVE_NOTICE_TEXT = {
    "Help Wanted",
    "Wanted",
    "For particulars and application:",
    "For particulars:",
}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class PageContent(HTMLParser):
    """Text nodes and links, grouped by job notice and by <main>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None]] = []  # (tag, card key opened here)
        self.card: str | None = None
        self.in_main = 0
        self.skip = 0
        self.main_text: Counter = Counter()
        self.main_links: Counter = Counter()
        self.card_text: dict[str, Counter] = {}
        self.card_links: dict[str, Counter] = {}
        self.card_attrs: dict[str, dict] = {}
        self.card_titles: dict[str, tuple[str, str]] = {}
        self.payload = ""
        self._link: list | None = None
        self._capture: str | None = None
        self._captured: dict[str, str] = {}
        self._in_payload = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        opened = None
        if tag == "main":
            self.in_main += 1
        if tag in ("script", "style"):
            self.skip += 1
            self._in_payload = attrs.get("id") == "site-data"
        if tag == "article" and "job-card" in (attrs.get("class") or "").split():
            opened = attrs["data-key"]
            self.card = opened
            self.card_text.setdefault(opened, Counter())
            self.card_links.setdefault(opened, Counter())
            self.card_attrs[opened] = {k: v for k, v in attrs.items() if k.startswith("data-") or k == "id"}
            self._captured = {}
        cls = (attrs.get("class") or "").split()
        if self.card and ("card-title" in cls or "card-company" in cls):
            self._capture = "card-title" if "card-title" in cls else "card-company"
            self._captured[self._capture] = ""
        if tag == "a":
            self._link = [attrs.get("href"), ""]
        if tag not in VOID:
            self.stack.append((tag, opened))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
            self._in_payload = False
        if tag == "a" and self._link is not None:
            link = (self._link[0], norm(self._link[1]))
            if self.in_main:
                self.main_links[link] += 1
            if self.card:
                self.card_links[self.card][link] += 1
            self._link = None
        if tag in ("h3", "p") and self._capture:
            self._capture = None
        while self.stack:
            open_tag, opened = self.stack.pop()
            if opened:
                self.card_titles[opened] = (
                    self._captured.get("card-company", ""),
                    self._captured.get("card-title", ""),
                )
                self.card = None
            if open_tag == tag:
                break
        if tag == "main":
            self.in_main -= 1

    def handle_data(self, data):
        if self.skip:
            if self._in_payload:
                self.payload += data
            return
        if self._link is not None:
            self._link[1] += data
        if self._capture:
            self._captured[self._capture] += data
        text = norm(data)
        if not text:
            return
        if self.in_main:
            self.main_text[text] += 1
        if self.card:
            self.card_text[self.card][text] += 1


def parse(path: Path) -> PageContent:
    parser = PageContent()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def missing(old: Counter, new: Counter) -> Counter:
    return old - new


class ContentPreservationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BASELINE_DIST.is_dir() or not HASHES.is_file():
            raise AssertionError(f"Required public-content baseline missing: {BASELINE}")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "dist"
        # Build exactly the hashed baseline report set, so reports appended
        # to data/reports/ later never change what is compared.
        reports = Path(cls._tmp.name) / "reports"
        reports.mkdir()
        for rel in json.loads(HASHES.read_text(encoding="utf-8")):
            shutil.copyfile(ROOT / rel, reports / Path(rel).name)
        with contextlib.redirect_stdout(io.StringIO()):
            code = build_site.build(reports, cls.out, "", False)
        assert code == 0, "current build failed"
        cls.pages = sorted(
            p.relative_to(BASELINE_DIST).as_posix() for p in BASELINE_DIST.rglob("*.html")
            if p.relative_to(BASELINE_DIST).parts[0] != "assets"
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_source_report_hashes_unchanged(self):
        recorded = json.loads(HASHES.read_text(encoding="utf-8"))
        self.assertTrue(recorded)
        for rel, digest in recorded.items():
            current = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            self.assertEqual(current, digest, f"{rel} changed")
        for rel in recorded:
            self.assertEqual((ROOT / rel).read_bytes(), (BASELINE / rel).read_bytes(), rel)

    def test_same_page_set(self):
        self.assertGreaterEqual(len(self.pages), 6)
        for rel in self.pages:
            self.assertTrue((self.out / rel).is_file(), rel)

    def test_report_json_exports_byte_identical(self):
        for old in sorted((BASELINE_DIST / "reports").glob("*.json")):
            self.assertEqual((self.out / "reports" / old.name).read_bytes(), old.read_bytes(), old.name)

    def test_inline_filter_payload_identical(self):
        for rel in self.pages:
            old, new = parse(BASELINE_DIST / rel), parse(self.out / rel)
            self.assertEqual(json.loads(new.payload), json.loads(old.payload), rel)

    def test_every_notice_keeps_all_text_links_and_hooks(self):
        checked = 0
        for rel in self.pages:
            old, new = parse(BASELINE_DIST / rel), parse(self.out / rel)
            self.assertEqual(sorted(new.card_text), sorted(old.card_text), f"{rel}: notice set changed")
            for key in old.card_text:
                where = f"{rel} {key}"
                self.assertEqual(missing(old.card_text[key], new.card_text[key]), Counter(),
                                 f"{where}: original text lost")
                added = new.card_text[key] - old.card_text[key]
                self.assertTrue(set(added) <= DECORATIVE_NOTICE_TEXT,
                                f"{where}: non-decorative text added {sorted(set(added) - DECORATIVE_NOTICE_TEXT)}")
                self.assertEqual(new.card_links[key], old.card_links[key], f"{where}: links changed")
                self.assertEqual(new.card_attrs[key], old.card_attrs[key], f"{where}: data hooks changed")
                self.assertEqual(new.card_titles[key], old.card_titles[key], f"{where}: company/title changed")
                checked += 1
        self.assertGreater(checked, 0)

    def test_main_content_text_and_links_preserved(self):
        for rel in self.pages:
            old, new = parse(BASELINE_DIST / rel), parse(self.out / rel)
            self.assertEqual(missing(old.main_text, new.main_text), Counter(), f"{rel}: text lost")
            self.assertEqual(missing(old.main_links, new.main_links), Counter(), f"{rel}: links lost")
            added = new.main_text - old.main_text
            self.assertTrue(set(added) <= DECORATIVE_NOTICE_TEXT,
                            f"{rel}: unexpected text added in <main> {sorted(set(added) - DECORATIVE_NOTICE_TEXT)}")
            # Links in <main> are exactly the same multiset (no new targets).
            self.assertEqual(new.main_links, old.main_links, f"{rel}: link set changed")

    def test_no_invented_application_method(self):
        for rel in self.pages:
            text = (self.out / rel).read_text(encoding="utf-8").lower()
            for phrase in ("in person", "by letter", "by post", "postal", "write to"):
                self.assertEqual(
                    text.count(phrase),
                    (BASELINE_DIST / rel).read_text(encoding="utf-8").lower().count(phrase),
                    f"{rel}: {phrase!r}",
                )


if __name__ == "__main__":
    unittest.main()
