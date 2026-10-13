"""Deterministic regression tests for the Coinbase design-system mapping.

The source of truth is design/coinbase.design.json (a verbatim copy of the
public design JSON). These tests parse assets/style.css and
assets/template.html as text — no browser — and check tokens, fonts, theme
wiring and WCAG contrast of the colour pairs the stylesheet uses.
"""

from __future__ import annotations

import colorsys
import json
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

DESIGN = json.loads((ROOT / "design" / "coinbase.design.json").read_text(encoding="utf-8"))
CSS = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "assets" / "template.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
FONT_URL = DESIGN["fonts"]["urls"][0]["url"]


def css_block(selector: str) -> str:
    """Body of the first top-level rule whose selector is exactly `selector`."""
    match = re.search(r"(?:\A|\n\n|\*/\n)" + re.escape(selector) + r" \{\n(.*?)\n\}", CSS, re.S)
    assert match, f"no {selector} block in style.css"
    return match.group(1)


def declarations(selector: str) -> dict[str, str]:
    body = re.sub(r"/\*.*?\*/", "", css_block(selector), flags=re.S)
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)
    }


LIGHT = declarations(":root")
DARK = {**LIGHT, **declarations(".dark")}


def resolve(theme: dict[str, str], name: str) -> str:
    value = theme[name]
    seen = {name}
    while value.startswith("var("):
        ref = re.fullmatch(r"var\((--[\w-]+)\)", value).group(1)
        assert ref not in seen, f"cycle at {ref}"
        seen.add(ref)
        value = theme[ref]
    return value


def hsl_to_rgb(triple: str) -> tuple[float, float, float]:
    h, s, l = (float(part.rstrip("%")) for part in triple.split())
    return colorsys.hls_to_rgb(h / 360, l / 100, s / 100)


def luminance(rgb: tuple[float, float, float]) -> float:
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(theme: dict[str, str], a: str, b: str) -> float:
    la = luminance(hsl_to_rgb(resolve(theme, a)))
    lb = luminance(hsl_to_rgb(resolve(theme, b)))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# (foreground, background, minimum) pairs actually rendered by style.css.
TEXT = 4.5
UI = 3.0
USED_PAIRS = [
    ("--foreground", "--background", TEXT),
    ("--foreground", "--muted", TEXT),            # filters, nested panels, th
    ("--card-foreground", "--card", TEXT),         # hero, cards, table
    ("--primary-foreground", "--primary", TEXT),   # primary button, qualified badge
    ("--primary-foreground", "--primary-hover", TEXT),
    ("--secondary-foreground", "--secondary", TEXT),
    ("--secondary-foreground", "--border", TEXT),  # secondary button hover
    ("--accent-foreground", "--accent", TEXT),     # ghost/nav/table-row hover
    ("--destructive-foreground", "--destructive-solid", TEXT),  # rejected badge
    ("--muted-foreground", "--background", TEXT),  # input placeholder
    ("--control-border", "--background", UI),
    ("--control-border", "--card", UI),
    ("--control-border", "--muted", UI),
    ("--primary-foreground", "--ring", UI),        # white ring on the halo
]
# Primary-button focus indicator against the surfaces it sits on: in light
# mode the ring-coloured halo carries it (the white ring is invisible on
# white); in dark mode the white currentColor ring itself does.
FOCUS_PAIRS = {
    "light": [("--ring", "--background", UI), ("--ring", "--card", UI)],
    "dark": [("--primary-foreground", "--background", UI), ("--primary-foreground", "--card", UI)],
}
for _alias in ("--primary-text", "--destructive-text", "--muted-text"):
    for _surface in ("--background", "--card", "--muted"):
        USED_PAIRS.append((_alias, _surface, TEXT))


class TokenMappingTest(unittest.TestCase):
    def test_light_tokens_on_root_exact(self):
        for name, value in DESIGN["tokens"]["colors"]["light"].items():
            self.assertEqual(LIGHT.get(f"--{name}"), value, name)

    def test_dark_tokens_on_dark_class_exact(self):
        dark_only = declarations(".dark")
        for name, value in DESIGN["tokens"]["colors"]["dark"].items():
            self.assertEqual(dark_only.get(f"--{name}"), value, name)

    def test_radius_and_aliases(self):
        radius = DESIGN["tokens"]["radius"]
        self.assertEqual(LIGHT["--radius"], radius["default"])
        self.assertEqual(LIGHT["--radius"], "0.375rem")
        for key in ("sm", "lg", "full"):
            self.assertEqual(LIGHT[f"--radius-{key}"], radius[key], key)

    def test_typography_spacing_shadow_aliases(self):
        typo = DESIGN["tokens"]["typography"]
        for key, value in typo["fontSize"].items():
            self.assertEqual(LIGHT[f"--text-{key}"], value, key)
        for key, value in typo["fontWeight"].items():
            self.assertEqual(LIGHT[f"--font-{key}"], str(value), key)
        for key, value in typo["lineHeight"].items():
            self.assertEqual(LIGHT[f"--leading-{key}"], value, key)
        for key, value in typo["letterSpacing"].items():
            self.assertEqual(LIGHT[f"--tracking-{key}"], value, key)
        for key, value in DESIGN["tokens"]["spacing"].items():
            self.assertEqual(LIGHT[f"--space-{key}"], value, key)
        shadows = DESIGN["tokens"]["shadows"]
        self.assertEqual(LIGHT["--shadow-sm"], shadows["sm"])
        self.assertEqual(LIGHT["--shadow"], shadows["default"])
        self.assertEqual(LIGHT["--shadow-lg"], shadows["lg"])

    def test_font_family_mapping_with_fallbacks(self):
        fam = DESIGN["tokens"]["typography"]["fontFamily"]
        self.assertTrue(LIGHT["--font-sans"].startswith(f'"{fam["sans"]}",'))
        self.assertTrue(LIGHT["--font-serif"].startswith(f'{fam["serif"]},'))
        self.assertTrue(LIGHT["--font-mono"].startswith(f'"{fam["mono"]}",'))
        self.assertTrue(LIGHT["--font-sans"].endswith("sans-serif"))
        self.assertTrue(LIGHT["--font-serif"].endswith("serif"))
        self.assertTrue(LIGHT["--font-mono"].endswith("monospace"))
        self.assertRegex(css_block("body"), r"font-family: var\(--font-sans\);")

    def test_colours_only_via_hsl_custom_properties(self):
        """No legacy hardcoded palette (white/black/green hex or names)."""
        no_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        self.assertNotRegex(no_comments, r"#[0-9a-fA-F]{3,8}\b")
        for legacy in ("--ink", "--paper", "--green", "--gray-"):
            self.assertNotIn(legacy, no_comments, legacy)
        self.assertNotRegex(no_comments, r"(?<![\w-])(white|black|green)(?![\w-])")
        for value in re.findall(r"(?<![-\w])(?:color|background|border[\w-]*)\s*:\s*([^;]+);", no_comments):
            if any(ch.isdigit() for ch in value) and "hsl(" not in value and "var(" not in value:
                self.assertRegex(value, r"^\d+px (solid|dashed) transparent$|^0$|^transparent$", value)

    def test_focus_ring_matches_spec(self):
        ring = DESIGN["accessibility"]["focusRing"]
        self.assertEqual(LIGHT["--focus-ring-width"], ring["width"])
        self.assertEqual(LIGHT["--focus-ring-offset"], ring["offset"])
        self.assertEqual(ring["color"], "currentColor")
        block = css_block(":focus-visible")
        self.assertIn("outline: var(--focus-ring-width) solid currentColor;", block)
        self.assertIn("outline-offset: var(--focus-ring-offset);", block)
        # Primary (white text) buttons add a ring-coloured halo; nothing
        # removes the outline itself.
        self.assertIn("box-shadow: 0 0 0 6px hsl(var(--ring));", css_block(".btn:focus-visible"))
        self.assertNotRegex(CSS, r"outline:\s*(none|0)")

    def test_no_invented_motion_and_reduced_motion_guard(self):
        self.assertNotIn("motion", DESIGN["tokens"])
        self.assertNotIn("@keyframes", CSS)
        outside = CSS.split("@media (prefers-reduced-motion: reduce)")[0]
        self.assertNotRegex(outside, r"\btransition\s*:|\banimation\s*:")
        self.assertTrue(DESIGN["accessibility"]["reducedMotion"])
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS)

    def test_components_mapped(self):
        btn = css_block(".btn")
        self.assertIn("background: hsl(var(--primary));", btn)
        self.assertIn("color: hsl(var(--primary-foreground));", btn)
        self.assertIn("font-weight: var(--font-semibold);", btn)
        shared = css_block(".btn,\n.btn-ghost")
        self.assertIn("border-radius: var(--radius);", shared)
        self.assertIn("padding: var(--space-sm) var(--space-md);", shared)
        quiet = css_block(".btn-quiet")
        self.assertIn("background: hsl(var(--secondary));", quiet)
        self.assertIn("color: hsl(var(--secondary-foreground));", quiet)
        self.assertIn("font-weight: var(--font-medium);", quiet)
        ghost = css_block(".btn-ghost")
        self.assertIn("background: transparent;", ghost)
        self.assertIn("color: hsl(var(--foreground));", ghost)
        card = css_block(".hero-report,\n.job-card")
        for decl in (
            "background: hsl(var(--card));",
            "color: hsl(var(--card-foreground));",
            "border: 1px solid hsl(var(--border));",
            "border-radius: var(--radius-lg);",
            "box-shadow: var(--shadow);",
            "padding: var(--space-lg);",
        ):
            self.assertIn(decl, card)
        field = css_block(".field input,\n.field select")
        for decl in (
            "background: hsl(var(--background));",
            "color: hsl(var(--foreground));",
            "border-radius: var(--radius);",
            "padding: var(--space-sm) var(--space-md);",
        ):
            self.assertIn(decl, field)
        self.assertIn("color: hsl(var(--muted-foreground));", css_block(".field input::placeholder"))


class ContrastTest(unittest.TestCase):
    def test_used_pairs_meet_targets_in_both_themes(self):
        self.assertEqual(DESIGN["accessibility"]["contrastTarget"], 4.5)
        for theme_name, theme in (("light", LIGHT), ("dark", DARK)):
            for fg, bg, minimum in USED_PAIRS + FOCUS_PAIRS[theme_name]:
                ratio = contrast(theme, fg, bg)
                self.assertGreaterEqual(
                    ratio, minimum, f"{theme_name}: {fg} on {bg} = {ratio:.2f}"
                )

    def test_aliases_exist_because_raw_tokens_fail(self):
        """Documents why each contrast-safe alias is needed."""
        self.assertLess(contrast(DARK, "--primary", "--card"), TEXT)
        self.assertLess(contrast(DARK, "--primary", "--background"), TEXT)
        self.assertLess(contrast(LIGHT, "--destructive", "--background"), TEXT)
        self.assertLess(contrast(LIGHT, "--destructive-foreground", "--destructive"), TEXT)
        self.assertLess(contrast(DARK, "--destructive", "--card"), TEXT)
        self.assertLess(contrast(LIGHT, "--muted-foreground", "--muted"), TEXT)
        self.assertLess(contrast(LIGHT, "--input", "--background"), UI)

    def test_primary_focus_ring_would_vanish_without_halo(self):
        # currentColor on a primary button is primary-foreground (white).
        self.assertLess(contrast(LIGHT, "--primary-foreground", "--background"), 1.01)


class ThemeWiringTest(unittest.TestCase):
    def test_default_dark_and_exact_font_link(self):
        self.assertEqual(DESIGN["agentInstructions"]["defaultMode"], "dark")
        self.assertIn('<html lang="en" class="dark">', TEMPLATE)
        self.assertIn(f'<link rel="stylesheet" href="{FONT_URL}">', TEMPLATE)
        self.assertEqual(TEMPLATE.count("fonts.googleapis.com/css2"), 1)

    def test_prepaint_script_precedes_stylesheets_and_catches_storage(self):
        head = TEMPLATE.split("</head>")[0]
        script_at = head.index('localStorage.getItem("job-alerts-theme")')
        self.assertLess(script_at, head.index(FONT_URL))
        self.assertLess(script_at, head.index("assets/style.css"))
        self.assertRegex(
            head,
            r'try \{\s*stored = window\.localStorage\.getItem\("job-alerts-theme"\);\s*\} catch',
        )
        self.assertIn('if (stored === "light") root.classList.remove("dark");', head)

    def test_toggle_hidden_without_js(self):
        self.assertIn(".theme-toggle { display: none !important; }", TEMPLATE)
        self.assertIn("html:not(.js) .theme-toggle", CSS)

    def test_theme_code_runs_before_filter_early_return(self):
        theme_at = APP_JS.index('getElementById("theme-toggle")')
        filter_return = APP_JS.index("if (!dataEl || !bar) return;")
        self.assertLess(theme_at, filter_return)
        # The theme IIFE is closed before the filter IIFE begins.
        self.assertLess(APP_JS.index("})();"), filter_return)

    def test_footer_discloses_storage_and_google_fonts(self):
        footer = TEMPLATE.split('<footer class="site-footer">')[1]
        self.assertIn("local storage", footer)
        self.assertIn("job-alerts-theme", footer)
        self.assertIn("fonts.googleapis.com", footer)
        self.assertIn("fonts.gstatic.com", footer)
        self.assertIn("sets no cookies", footer)
        self.assertNotIn("no CDN", footer)

    def test_every_built_page_has_toggle_fonts_and_default_dark(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "site"
            self.assertEqual(build_site.build(SEED_REPORTS, out, "", False), 0)
            pages = [out / "index.html", out / "archive.html", *sorted((out / "reports").glob("*.html"))]
            self.assertGreaterEqual(len(pages), 6)
            for page in pages:
                html = page.read_text(encoding="utf-8")
                self.assertIn('<html lang="en" class="dark">', html, page.name)
                self.assertEqual(html.count('id="theme-toggle"'), 1, page.name)
                self.assertIn('aria-pressed="true"', html, page.name)
                self.assertIn(f'href="{FONT_URL}"', html, page.name)
                self.assertIn("assets/app.js", html, page.name)


if __name__ == "__main__":
    unittest.main()
