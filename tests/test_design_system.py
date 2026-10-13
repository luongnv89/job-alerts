"""Deterministic regression tests for the printed-newspaper design.

These tests parse assets/style.css, assets/template.html and freshly built
pages as text — no browser — and pin the newspaper constraints: full-width
layout, ruled newspaper columns, boxed classified notices, aged-paper and
ink palette only, serif-only typography, no rounded corners / shadows /
gradients / images, the masthead dateline, the paper-variant theme wiring
and WCAG contrast of every ink-on-paper pair. Browser-measured checks live
in tests/design_checks.py.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
for _entry in (str(TESTS_DIR), str(ROOT / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import build_site  # noqa: E402
from fixtures import SEED_REPORTS  # noqa: E402

CSS = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")
CSS_NC = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
TEMPLATE = (ROOT / "assets" / "template.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
FONT_URL = (
    "https://fonts.googleapis.com/css2?family=Libre+Baskerville:ital,wght@0,400;0,700;1,400"
    "&family=Playfair+Display:wght@700;900&family=UnifrakturMaguntia&display=swap"
)
THEME_KEY = "job-alerts-theme"

PAPER_MIN = (0xE8, 0xDC, 0xC0)
PAPER_MAX = (0xF4, 0xEC, 0xD8)
INK_MIN, INK_MAX = 0x1A, 0x2B


def css_block(selector: str, css: str = CSS_NC) -> str:
    """Body of the first rule whose selector list is exactly `selector`."""
    match = re.search(r"(?:^|\n)\s*" + re.escape(selector) + r"\s*\{([^{}]*)\}", css)
    assert match, f"no {selector!r} block in style.css"
    return match.group(1)


def media_block(query: str) -> str:
    """Concatenated bodies of every `@media <query>` block in the stylesheet."""
    bodies = []
    needle = f"@media {query} {{"
    start = CSS_NC.find(needle)
    assert start != -1, f"no @media {query}"
    while start != -1:
        depth, i = 0, CSS_NC.index("{", start)
        for j in range(i, len(CSS_NC)):
            if CSS_NC[j] == "{":
                depth += 1
            elif CSS_NC[j] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(CSS_NC[i + 1:j])
                    break
        start = CSS_NC.find(needle, j)
    return "\n".join(bodies)


def custom_props(selector: str) -> dict[str, str]:
    return dict(
        (name, value.strip())
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", css_block(selector))
    )


LIGHT = custom_props(":root")
DARK = {**LIGHT, **custom_props(".dark")}


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def luminance(rgb) -> float:
    def channel(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(hex_rgb(a)), luminance(hex_rgb(b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def is_paper(value: str) -> bool:
    rgb = hex_rgb(value)
    return all(lo <= c <= hi for c, lo, hi in zip(rgb, PAPER_MIN, PAPER_MAX)) and rgb[0] > rgb[1] > rgb[2]


def is_ink(value: str) -> bool:
    r, g, b = hex_rgb(value)
    return r == g == b and INK_MIN <= r <= INK_MAX


def build_seed(tmp: str) -> Path:
    out = Path(tmp) / "site"
    assert build_site.build(SEED_REPORTS, out, "", False) == 0
    return out


class PaletteTest(unittest.TestCase):
    def test_only_aged_paper_and_ink_hex_colours(self):
        colours = re.findall(r"#[0-9a-fA-F]{3,8}\b", CSS_NC)
        self.assertTrue(colours)
        for value in colours:
            self.assertTrue(is_paper(value) or is_ink(value), f"{value} is neither paper nor ink")

    def test_no_functional_or_named_bright_colours(self):
        self.assertNotRegex(CSS_NC, r"\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color-mix)\(")
        named = re.findall(r"(?<![-\w#])(red|green|blue|yellow|orange|purple|pink|white|black|gray|grey|navy|teal|gold)(?![-\w])", CSS_NC)
        self.assertEqual(named, [])

    def test_paper_variants_and_ink_tokens(self):
        self.assertEqual(LIGHT["--paper"].lower(), "#f4ecd8")
        self.assertEqual(DARK["--paper"].lower(), "#e8dcc0")
        for theme in (LIGHT, DARK):
            self.assertTrue(is_paper(theme["--paper"]))
            self.assertTrue(is_paper(theme["--paper-patch"]))
            self.assertTrue(is_ink(theme["--ink"]))
        # The darker paper keeps dark ink; it is not an inverted UI.
        self.assertLess(luminance(hex_rgb(DARK["--ink"])), 0.05)
        self.assertLess(luminance(hex_rgb(DARK["--paper"])), luminance(hex_rgb(LIGHT["--paper"])))

    def test_ink_on_paper_contrast_in_both_variants(self):
        for name, theme in (("light", LIGHT), ("dark", DARK)):
            for surface in ("--paper", "--paper-patch"):
                ratio = contrast(theme["--ink"], theme[surface])
                self.assertGreaterEqual(ratio, 7.0, f"{name}: ink on {surface} {ratio:.2f}")
                # Reverse type (rejected badge) uses the same pair.
        # Print colours are also ink on paper.
        print_css = media_block("print")
        self.assertIn("#f4ecd8", print_css)
        self.assertIn("#1a1a1a", print_css)


class OrnamentBanTest(unittest.TestCase):
    def test_no_rounded_corners(self):
        for value in re.findall(r"border(?:-[\w-]+)?-radius\s*:\s*([^;]+);", CSS_NC):
            self.assertEqual(value.strip(), "0")
        self.assertIn("border-radius: 0;", css_block("*,\n*::before,\n*::after"))

    def test_no_shadows_gradients_images_or_filters(self):
        for value in re.findall(r"(?:box|text)-shadow\s*:\s*([^;]+);", CSS_NC):
            self.assertEqual(value.strip(), "none")
        self.assertNotRegex(CSS_NC, r"gradient\(")
        self.assertNotRegex(CSS_NC, r"url\(")
        self.assertNotRegex(CSS_NC, r"image-set\(|data:|<svg|\bfilter\s*:|backdrop-filter|mask")
        for value in re.findall(r"background-image\s*:\s*([^;]+);", CSS_NC):
            self.assertEqual(value.strip(), "none")
        for value in re.findall(r"(?<![-\w])background\s*:\s*([^;]+);", CSS_NC):
            self.assertRegex(value.strip(), r"^(var\(--[\w-]+\)|transparent|#[0-9a-fA-F]{6}( !important)?)$")
        self.assertNotRegex(TEMPLATE, r"<svg|<canvas|<img|data:image|\.png|\.svg|\.ico")

    def test_no_icon_glyphs_or_emoji_in_generated_content(self):
        for value in re.findall(r"content\s*:\s*([^;]+);", CSS_NC):
            for escape in re.findall(r"\\([0-9a-fA-F]{2,6})", value):
                # Only dash, no-break space and middle dot are used.
                self.assertIn(escape.lower(), {"2014", "00a0", "00b7"}, value)
        self.assertNotRegex(CSS + TEMPLATE, "[\U0001F300-\U0001FAFF☀-➿]")

    def test_no_motion(self):
        self.assertNotIn("@keyframes", CSS_NC)
        outside = CSS_NC.split("@media (prefers-reduced-motion: reduce)")[0]
        self.assertNotRegex(outside, r"\btransition\s*:|\banimation\s*:")
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS_NC)


class TypographyTest(unittest.TestCase):
    def test_every_font_stack_is_serif_only(self):
        stacks = re.findall(r"(--font-[\w-]+)\s*:\s*([^;]+);", css_block(":root"))
        self.assertEqual({name for name, _ in stacks}, {"--font-masthead", "--font-display", "--font-body"})
        for name, stack in stacks:
            self.assertTrue(stack.rstrip().endswith("serif"), name)
            self.assertNotRegex(stack, r"sans-serif|monospace|Inter|system-ui|Arial|Helvetica", name)
            self.assertIn("Georgia", stack, name)
            self.assertIn("Times", stack, name)
        self.assertTrue(dict(stacks)["--font-masthead"].startswith('"UnifrakturMaguntia"'))
        self.assertTrue(dict(stacks)["--font-display"].startswith('"Playfair Display"'))
        self.assertTrue(dict(stacks)["--font-body"].startswith('"Libre Baskerville"'))
        for decl in re.findall(r"font-family\s*:\s*([^;]+);", CSS_NC):
            self.assertRegex(decl.strip(), r"^var\(--font-(masthead|display|body)\)$")
        self.assertNotRegex(CSS_NC, r"\bmonospace\b|sans-serif")

    def test_controls_code_and_small_print_use_serif(self):
        block = css_block("input,\nselect,\nbutton,\ntextarea,\ncode,\nkbd,\nsamp,\npre,\n.mono")
        self.assertIn("font-family: var(--font-body);", block)
        self.assertIn("font: inherit;", css_block(".field input,\n.field select"))
        self.assertIn("font: inherit;", css_block(".btn,\n.btn-ghost"))
        self.assertIn("font-family: var(--font-masthead);", css_block(".brand"))

    def test_google_fonts_link_replaces_inter(self):
        self.assertIn(f'<link rel="stylesheet" href="{FONT_URL}">', TEMPLATE)
        self.assertEqual(TEMPLATE.count("fonts.googleapis.com/css2"), 1)
        self.assertNotIn("Inter", TEMPLATE)
        self.assertNotIn("Inter", CSS)

    def test_justified_hyphenated_body(self):
        block = css_block("main p,\nmain li,\nmain dd,\n.site-footer p")
        self.assertIn("text-align: justify;", block)
        self.assertIn("hyphens: auto;", block)
        self.assertIn('<html lang="en">', TEMPLATE)

    def test_drop_cap_only_on_lead_paragraph(self):
        selectors = re.findall(r"([^{}]+)::first-letter", CSS_NC)
        joined = " ".join(selectors)
        self.assertIn(".hero .standfirst", joined)
        self.assertIn(".page-report .hero-report .summary", joined)
        self.assertNotIn(".job-card", joined)
        self.assertEqual(CSS_NC.count("::first-letter"), 2)

    def test_section_headers_ruled_caps(self):
        block = css_block(".filters > h2,\n.job-section > h2")
        for decl in ("text-transform: uppercase;", "border-top: 1px solid var(--ink);",
                     "border-bottom: 1px solid var(--ink);"):
            self.assertIn(decl, block)

    def test_compact_notice_leading(self):
        self.assertEqual(LIGHT["--leading-notice"], "1.35")
        self.assertIn("line-height: var(--leading-notice);", css_block(".job-card"))
        self.assertTrue(1.3 <= float(LIGHT["--leading-notice"]) <= 1.4)


class LayoutTest(unittest.TestCase):
    def test_full_width_wrap(self):
        block = css_block(".wrap")
        self.assertIn("width: 100%;", block)
        self.assertIn("max-width: none;", block)
        self.assertIn("margin: 0;", block)
        self.assertIn("padding: 0 var(--gutter);", block)
        gutters = [LIGHT["--gutter"]] + re.findall(r"--gutter:\s*([^;]+);", CSS_NC)
        for value in gutters:
            self.assertLessEqual(int(value.strip().rstrip("px")), 16)
        # No other max-width constraint anywhere on the page.
        for value in re.findall(r"(?<!\()max-width\s*:\s*([^;]+);", CSS_NC):
            self.assertEqual(value.strip(), "none")

    def test_newspaper_column_counts(self):
        base = css_block(".job-list")
        self.assertIn("column-count: 1;", base)
        self.assertIn("column-rule: 1px solid var(--ink);", base)
        self.assertRegex(media_block("(min-width: 640px)"),
                         r"\.job-list,\s*\.hero-report,\s*\.site-footer \.wrap \{\s*column-count: 2;")
        self.assertRegex(media_block("(min-width: 1024px)"), r"#leads-list \{\s*column-count: 2;")
        self.assertRegex(media_block("(min-width: 1024px)"), r"grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);")
        self.assertRegex(media_block("(min-width: 1440px)"), r"#leads-list \{\s*column-count: 3;")
        self.assertRegex(media_block("(min-width: 1440px)"), r"grid-template-columns: repeat\(4, minmax\(0, 1fr\)\);")
        self.assertIn("border-left: 1px solid var(--ink);",
                      css_block(".page-home main > #research-leads,\n  .page-report main > #research-leads",
                                media_block("(min-width: 1024px)")))

    def test_no_clipping_or_fixed_heights(self):
        self.assertNotRegex(CSS_NC, r"line-clamp|text-overflow\s*:\s*ellipsis")
        for value in re.findall(r"(?<![-\w])(?:max-)?height\s*:\s*([^;]+);", CSS_NC):
            self.assertIn(value.strip(), {"1px", "12px", "auto"})
        # overflow:hidden only on the visually-hidden helpers.
        self.assertEqual(CSS_NC.count("overflow: hidden;"), 2)
        self.assertIn("overflow-x: auto;", css_block(".table-scroll"))


class ClassifiedNoticeTest(unittest.TestCase):
    def test_notice_borders_and_break_inside(self):
        card = css_block(".job-card")
        self.assertIn("border: 1px solid var(--ink);", card)
        self.assertIn("break-inside: avoid;", card)
        self.assertIn("border: 4px double var(--ink);", css_block(".job-card.status-qualified"))

    def test_classified_typography(self):
        label = css_block(".notice-label")
        self.assertIn("text-transform: uppercase;", label)
        self.assertIn("font-weight: 900;", label)
        title = css_block(".card-title")
        self.assertIn("text-transform: uppercase;", title)
        self.assertIn("font-weight: 900;", title)
        self.assertIn("font-family: var(--font-display);", title)

    def test_native_disclosure_and_select_without_icons(self):
        self.assertIn("list-style: none;", css_block(".panel > summary"))
        self.assertIn("text-decoration: underline;", css_block(".panel > summary"))
        self.assertIn("display: none;", css_block(".panel > summary::-webkit-details-marker"))
        field = css_block(".field input,\n.field select")
        self.assertIn("appearance: none;", field)
        self.assertIn("background-image: none;", field)

    def test_focus_is_outline_not_shadow(self):
        block = css_block(":focus-visible")
        self.assertIn("outline: 2px solid var(--ink);", block)
        self.assertIn("outline-offset: 2px;", block)
        self.assertIn("box-shadow: none;", block)
        self.assertNotRegex(CSS_NC, r"outline:\s*(none|0)")

    def test_print_stylesheet_keeps_everything(self):
        printed = media_block("print")
        self.assertIn(".job-card[hidden]", printed)
        self.assertIn("details::details-content", printed)
        self.assertIn("content-visibility: visible;", printed)
        self.assertIn("print-color-adjust: exact;", printed)


class BuiltPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = build_seed(cls._tmp.name)
        cls.pages = {
            p.relative_to(cls.out).as_posix(): p.read_text(encoding="utf-8")
            for p in [cls.out / "index.html", cls.out / "archive.html", *sorted((cls.out / "reports").glob("*.html"))]
        }
        cls.dates = sorted(p.stem for p in SEED_REPORTS.glob("*.json"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_masthead_dateline_uses_real_report_dates_and_sequence(self):
        for rel, html in self.pages.items():
            self.assertIn('<a class="brand" href=', html)
            self.assertIn(">Job Alerts</a>", html)
            dateline = re.search(r'<div class="dateline">(.*?)</div>', html).group(1)
            self.assertIn("Price: Free &middot; Web Edition", dateline)
            self.assertIn("Vol. I", dateline)
            times = re.findall(r'<time datetime="([^"]+)">', dateline)
            self.assertTrue(times, rel)
            for value in times:
                self.assertIn(value, self.dates, f"{rel}: dateline date {value} is not a report date")
            if rel == "archive.html":
                self.assertIn(f"Nos. 1&ndash;{len(self.dates)}", dateline)
            else:
                date = self.dates[-1] if rel == "index.html" else Path(rel).stem
                self.assertEqual(times, [date])
                self.assertIn(f"No. {self.dates.index(date) + 1}<", dateline)
            # The weekday is computed from the date, not invented.
            for value in times:
                self.assertIn(build_site.long_day(value), dateline)
        self.assertEqual(build_site.long_day("2026-10-13"), "Tuesday, 13 October 2026")

    def test_notices_have_classified_labels_and_original_text(self):
        html = self.pages["index.html"]
        cards = re.findall(r"<article class=\"job-card status-([\w-]+)\".*?</article>", html, re.S)
        self.assertEqual(len(cards), 11)
        for match in re.finditer(r"<article class=\"job-card status-([\w-]+)\".*?</article>", html, re.S):
            status, card = match.group(1), match.group(0)
            label = "Help Wanted" if status == "qualified" else "Wanted"
            self.assertIn(f'<p class="notice-label" aria-hidden="true">{label}</p>', card)
            closing = re.search(r'<div class="card-actions">(.*?)</div>', card).group(1)
            if status == "qualified":
                self.assertIn("For particulars and application:", closing)
            else:
                self.assertIn("For particulars:", closing)
                self.assertNotIn("application", closing)
                self.assertIn("Research lead — opening a listing is not a recommendation.", card)
        lowered = html.lower()
        for invented in ("in person", "by letter", "apply by post", "write to", "box no"):
            self.assertNotIn(invented, lowered)

    def test_paper_default_and_storage_semantics(self):
        self.assertIn('<html lang="en">', TEMPLATE)
        self.assertNotIn('class="dark"', TEMPLATE)
        head = TEMPLATE.split("</head>")[0]
        script_at = head.index(f'localStorage.getItem("{THEME_KEY}")')
        self.assertLess(script_at, head.index("fonts.googleapis.com/css2"))
        self.assertLess(script_at, head.index("assets/style.css"))
        self.assertRegex(head, r'try \{\s*stored = window\.localStorage\.getItem\("job-alerts-theme"\);\s*\} catch')
        self.assertIn('if (stored === "dark") root.classList.add("dark");', head)
        self.assertIn('aria-pressed="false"', TEMPLATE)
        self.assertIn('<span class="theme-toggle-label">Dark theme</span>', TEMPLATE)
        # app.js keeps the same key, values and cross-tab sync.
        self.assertIn(f'var KEY = "{THEME_KEY}";', APP_JS)
        self.assertIn('window.addEventListener("storage"', APP_JS)
        self.assertIn('event.newValue === "light" || event.newValue === "dark"', APP_JS)

    def test_toggle_hidden_without_js(self):
        self.assertIn(".theme-toggle { display: none !important; }", TEMPLATE)
        self.assertIn("html:not(.js) .theme-toggle", CSS)

    def test_theme_code_runs_before_filter_early_return(self):
        filter_return = APP_JS.index("if (!dataEl || !bar) return;")
        self.assertLess(APP_JS.index('getElementById("theme-toggle")'), filter_return)
        self.assertLess(APP_JS.index("})();"), filter_return)

    def test_footer_discloses_fonts_and_storage_truthfully(self):
        footer = TEMPLATE.split('<footer class="site-footer">')[1]
        for needle in ("local storage", THEME_KEY, "fonts.googleapis.com", "fonts.gstatic.com",
                       "sets no cookies", "UnifrakturMaguntia", "Playfair Display",
                       "Libre Baskerville", "Georgia"):
            self.assertIn(needle, footer)
        self.assertNotIn("Inter ", footer)
        self.assertNotIn("system fonts", footer)

    def test_every_page_ships_fonts_toggle_and_masthead(self):
        self.assertGreaterEqual(len(self.pages), 6)
        for rel, html in self.pages.items():
            self.assertIn('<html lang="en">', html, rel)
            self.assertEqual(html.count('id="theme-toggle"'), 1, rel)
            self.assertIn(f'href="{FONT_URL}"', html, rel)
            self.assertEqual(html.count('class="dateline"'), 1, rel)
            self.assertIn("assets/app.js", html, rel)


if __name__ == "__main__":
    unittest.main()
