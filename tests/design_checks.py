"""Playwright design-system checks (imported by tests/browser_check.py).

Everything here measures the real rendered page: colours come from
getComputedStyle, backgrounds are composited up the ancestor chain, and
contrast uses the WCAG 2.x relative-luminance formula.

Google Fonts requests are blocked in every context except the explicit
live-font check, so the main matrix is deterministic offline and proves the
fallback stack stays readable. The live check reports UNTESTED (not PASS)
when the network is unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN = json.loads((ROOT / "design" / "coinbase.design.json").read_text(encoding="utf-8"))
FONT_URL = DESIGN["fonts"]["urls"][0]["url"]
FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")
THEME_KEY = "job-alerts-theme"
SHOTS = ROOT / ".work" / "screenshots"
VIEWPORTS = [(375, 812), (768, 1024), (1280, 900)]
# home, archive (no filter bar: theme code must still run), daily report
# whose qualified section is a zero state.
PAGES = [("home", "index.html"), ("archive", "archive.html"), ("daily", "reports/2026-10-11.html")]

# Returns {checked, failures, min} for every visible text-bearing element
# under `root` (default body), plus input placeholders and control borders.
CONTRAST_JS = r"""
(rootSel) => {
  const parse = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c || "");
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (top, bot) => {
    const a = top[3];
    return [0, 1, 2].map((i) => top[i] * a + bot[i] * (1 - a)).concat([1]);
  };
  const bgOf = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    let base = [255, 255, 255, 1];
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
    return base;
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => {
    const x = lum(a), y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const visible = (el) => {
    if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && r.right > 0 && r.left < document.documentElement.scrollWidth;
  };
  const name = (el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
    (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).join(".") : "");
  const root = rootSel ? document.querySelector(rootSel) : document.body;
  const out = { checked: 0, failures: [], min: 99 };
  if (!root) return out;
  const els = [root, ...root.querySelectorAll("*")];
  for (const el of els) {
    if (["SCRIPT", "STYLE", "NOSCRIPT", "OPTION", "TEMPLATE"].includes(el.tagName)) continue;
    const isControl = el.tagName === "INPUT" || el.tagName === "SELECT";
    const hasText = isControl || [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!hasText || !visible(el)) continue;
    const cs = getComputedStyle(el);
    const bg = bgOf(el);
    const fg = over(parse(cs.color), bg);
    const size = parseFloat(cs.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700);
    const need = large ? 3 : 4.5;
    const r = ratio(fg, bg);
    out.checked++;
    out.min = Math.min(out.min, r);
    if (r + 1e-6 < need) {
      out.failures.push({ el: name(el), text: (el.innerText || el.value || "").trim().slice(0, 40), ratio: +r.toFixed(2), need });
    }
    if (isControl) {
      const parentBg = bgOf(el.parentElement);
      const border = parse(cs.borderTopColor);
      const br = ratio(over(border, parentBg), parentBg);
      if (br + 1e-6 < 3) out.failures.push({ el: name(el) + " [border]", ratio: +br.toFixed(2), need: 3 });
      if (el.placeholder && !el.value) {
        const ph = over(parse(getComputedStyle(el, "::placeholder").color), bg);
        const pr = ratio(ph, bg);
        out.checked++;
        if (pr + 1e-6 < 4.5) out.failures.push({ el: name(el) + "::placeholder", ratio: +pr.toFixed(2), need: 4.5 });
      }
    }
  }
  out.min = +out.min.toFixed(2);
  return out;
}
"""

# Focus indicator for document.activeElement: spec values plus contrast of
# the indicator against the surface around the element.
FOCUS_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  if (!el.dataset.focusId) {
    window.__focusIds = (window.__focusIds || 0) + 1;
    el.dataset.focusId = String(window.__focusIds);
  }
  const parse = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c || "");
    if (!m) return null;
    const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const bgOf = (e) => {
    for (; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c[3] >= 1) return c;
    }
    return [255, 255, 255, 1];
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => {
    const x = lum(a), y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const cs = getComputedStyle(el);
  const surface = bgOf(el.parentElement);
  const outline = parse(cs.outlineColor);
  const halo = cs.boxShadow && cs.boxShadow !== "none" ? parse(cs.boxShadow) : null;
  return {
    id: el.dataset.focusId,
    el: el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (typeof el.className === "string" && el.className ? "." + el.className.split(/\s+/).join(".") : ""),
    focusVisible: el.matches(":focus-visible"),
    style: cs.outlineStyle,
    width: cs.outlineWidth,
    offset: cs.outlineOffset,
    currentColor: cs.outlineColor === cs.color,
    ringVsSurface: +ratio(outline, surface).toFixed(2),
    haloVsSurface: halo ? +ratio(halo, surface).toFixed(2) : null,
    ringVsHalo: halo ? +ratio(outline, halo).toFixed(2) : null,
  };
}
"""


def new_context(browser, theme: str | None = None, **kwargs):
    """Browser context with Google Fonts blocked and an optional stored theme."""
    context = browser.new_context(**kwargs)
    attempted: list[str] = []

    def _block(route):
        attempted.append(route.request.url)
        route.abort()

    for host in FONT_HOSTS:
        context.route(f"https://{host}/**", _block)
    if theme:
        context.add_init_script(
            "try { if (!sessionStorage.getItem('__seeded')) {"
            f" localStorage.setItem('{THEME_KEY}', '{theme}');"
            " sessionStorage.setItem('__seeded', '1'); } } catch (e) {}"
        )
    context.font_requests = attempted  # type: ignore[attr-defined]
    return context


def record_errors(page) -> list[str]:
    """Console errors and page errors, ignoring the deliberately blocked fonts."""
    errors: list[str] = []

    def _console(msg):
        if msg.type != "error":
            return
        location = (msg.location or {}).get("url", "")
        if any(host in location for host in FONT_HOSTS) or "favicon" in location.lower():
            return
        if "net::ERR_FAILED" in msg.text and not location:
            return  # aborted font request reported without a location
        errors.append(msg.text)

    page.on("console", _console)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


def assert_contrast(page, label: str, root: str | None = None) -> dict:
    result = page.evaluate(CONTRAST_JS, root)
    assert result["checked"] > 0, f"{label}: no text elements measured"
    assert not result["failures"], f"{label}: contrast failures {result['failures'][:8]}"
    return result


def assert_no_overflow(page, label: str) -> None:
    sw, iw = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert sw <= iw + 1, f"{label}: horizontal overflow ({sw} > {iw})"


def assert_theme(page, theme: str, label: str) -> None:
    is_dark = page.evaluate("() => document.documentElement.classList.contains('dark')")
    assert is_dark == (theme == "dark"), f"{label}: expected {theme} theme"
    bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    want = "rgb(11, 12, 14)" if theme == "dark" else "rgb(255, 255, 255)"
    assert bg == want, f"{label}: body background {bg}, expected {want} for {theme}"


def shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def check_focus_cycle(page, label: str, max_tabs: int = 120) -> int:
    """Tab through the page; every stop must show the spec ring, visibly.

    Reloads first so sequential focus navigation starts at the document
    top (earlier clicks would otherwise move the starting point)."""
    page.reload(wait_until="load")
    seen: list[str] = []
    kinds: set[str] = set()
    for _ in range(max_tabs):
        page.keyboard.press("Tab")
        info = page.evaluate(FOCUS_JS)
        if info is None:
            break
        if info["id"] in seen:
            break
        seen.append(info["id"])
        kinds.add(info["el"])
        where = f"{label}: focus on {info['el']}"
        assert info["focusVisible"], f"{where}: not :focus-visible"
        assert info["style"] == "solid", f"{where}: outline style {info['style']}"
        assert info["width"] == "2px", f"{where}: outline width {info['width']}"
        assert info["offset"] == "2px", f"{where}: outline offset {info['offset']}"
        assert info["currentColor"], f"{where}: outline is not currentColor"
        if info["ringVsSurface"] < 3:
            assert info["haloVsSurface"] is not None and info["haloVsSurface"] >= 3, (
                f"{where}: ring {info['ringVsSurface']}:1 and no adequate halo ({info})"
            )
            assert info["ringVsHalo"] >= 3, f"{where}: ring vs halo {info['ringVsHalo']}:1"
    assert len(seen) >= 5, f"{label}: only {len(seen)} focus stops"
    for required in ("a.skip-link", "button#theme-toggle.btn-ghost.theme-toggle"):
        assert required in kinds, f"{label}: focus cycle never reached {required}"
    if page.locator(".btn:not(.btn-quiet):visible").count():
        assert "a.btn" in kinds, f"{label}: focus cycle never reached a primary button"
    return len(seen)


HOVER_TARGETS = [
    ".hero-links .btn:not(.btn-quiet)",
    ".hero-links .btn-quiet",
    "#theme-toggle",
    ".site-nav a",
    ".job-card:not([hidden]) .card-actions a",
    ".job-card:not([hidden]) .panel > summary",
    ".job-card:not([hidden]) .evidence-head a",
    ".archive-table tbody tr",
    ".report-nav a",
    "#filter-status",
    "#filter-reset",
]


def check_hover_states(page, label: str) -> int:
    done = 0
    for selector in HOVER_TARGETS:
        target = page.locator(selector).first
        if page.locator(selector).count() == 0 or not target.is_visible():
            continue
        target.scroll_into_view_if_needed()
        target.hover()
        handle = target.element_handle()
        handle.evaluate("el => el.setAttribute('data-hover-probe', '1')")
        assert_contrast(page, f"{label} hover {selector}", "[data-hover-probe]")
        handle.evaluate("el => el.removeAttribute('data-hover-probe')")
        done += 1
    page.mouse.move(0, 0)
    return done


def run_theme_matrix(browser, base: str) -> list[str]:
    """Both themes x 375/768/1280 x home/archive/daily."""
    lines = []
    for theme in ("dark", "light"):
        for width, height in VIEWPORTS:
            context = new_context(
                browser, theme=None if theme == "dark" else "light",
                viewport={"width": width, "height": height},
            )
            page = context.new_page()
            errors = record_errors(page)
            stats = []
            for name, rel in PAGES:
                label = f"design {theme} {width} {name}"
                page.goto(f"{base}/{rel}", wait_until="load")
                assert_theme(page, theme, label)
                toggle = page.locator("#theme-toggle")
                assert toggle.is_visible(), f"{label}: theme toggle not visible"
                assert toggle.get_attribute("aria-pressed") == ("true" if theme == "dark" else "false"), (
                    f"{label}: aria-pressed out of sync"
                )
                assert page.locator(f'link[rel="stylesheet"][href="{FONT_URL}"]').count() == 1, (
                    f"{label}: exact Google Fonts link missing"
                )
                family = page.evaluate("() => getComputedStyle(document.body).fontFamily")
                assert family.startswith("Inter,"), f"{label}: body font-family {family}"
                assert_no_overflow(page, label)
                result = assert_contrast(page, label)
                stats.append(f"{name} {result['checked']} texts min {result['min']}:1")
                shot(page, f"{theme}-{name}-{width}")

                if name == "home":
                    # Expanded evidence: open every details panel.
                    page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
                    assert_contrast(page, f"{label} expanded")
                    assert_no_overflow(page, f"{label} expanded")
                    shot(page, f"{theme}-home-expanded-{width}")
                    page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = false)")
                    # All dates: every status style (qualified/rejected/near-miss).
                    page.select_option("#filter-date", "all")
                    assert_contrast(page, f"{label} all dates")
                    # Zero state.
                    page.fill("#filter-search", "zzz-no-such-entry")
                    assert page.locator("#no-results").is_visible(), f"{label}: zero state hidden"
                    assert_contrast(page, f"{label} zero-state")
                    shot(page, f"{theme}-home-zero-{width}")
                    page.click("#filter-reset")
                    page.select_option("#filter-date", "all")
                    hovered = check_hover_states(page, label)
                    assert hovered >= 6, f"{label}: only {hovered} hover targets measured"
                    page.click("#filter-reset")
                elif name == "daily":
                    assert page.locator("#qualified-empty-data").is_visible(), f"{label}: zero state"
                    check_hover_states(page, label)
                else:
                    # Measure cells hidden inside the scrolling table too.
                    page.evaluate("() => { const s = document.querySelector('.table-scroll');"
                                  " if (s) s.scrollLeft = s.scrollWidth; }")
                    assert_contrast(page, f"{label} table scrolled")
                    check_hover_states(page, label)
                stops = check_focus_cycle(page, label)
                stats[-1] += f", {stops} focus stops"
            assert not errors, f"design {theme} {width}: console/page errors {errors}"
            assert any(FONT_URL == url for url in context.font_requests), (
                f"design {theme} {width}: page never requested the Google Fonts URL"
            )
            context.close()
            lines.append(f"OK   design {theme} {width}x{height}: " + "; ".join(stats))
    return lines


def run_toggle_checks(browser, base: str) -> list[str]:
    lines = []
    context = new_context(browser, viewport={"width": 768, "height": 1024})
    page = context.new_page()
    errors = record_errors(page)
    page.goto(f"{base}/index.html", wait_until="load")
    assert_theme(page, "dark", "toggle default")
    assert page.evaluate(f"() => localStorage.getItem('{THEME_KEY}')") is None

    # Keyboard: Tab to the toggle, activate with Enter, then Space.
    page.locator("body").focus()
    for _ in range(10):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.id") == "theme-toggle":
            break
    assert page.evaluate("() => document.activeElement.id") == "theme-toggle", "toggle not reachable by Tab"
    page.keyboard.press("Enter")
    assert_theme(page, "light", "toggle Enter")
    assert page.locator("#theme-toggle").get_attribute("aria-pressed") == "false"
    assert page.evaluate(f"() => localStorage.getItem('{THEME_KEY}')") == "light"
    info = page.evaluate(FOCUS_JS)
    assert info["focusVisible"] and info["ringVsSurface"] >= 3, f"toggle focus after switch {info}"
    page.keyboard.press("Space")
    assert_theme(page, "dark", "toggle Space")
    page.keyboard.press("Enter")
    assert_theme(page, "light", "toggle Enter again")

    # Persistence across navigation (archive has no filter bar) and reload.
    page.click(".site-nav a[href='archive.html']")
    page.wait_for_load_state("load")
    assert_theme(page, "light", "archive after navigation")
    page.click("#theme-toggle")
    assert_theme(page, "dark", "archive toggle click")
    assert page.evaluate(f"() => localStorage.getItem('{THEME_KEY}')") == "dark"
    page.goto(f"{base}/reports/2026-10-11.html", wait_until="load")
    assert_theme(page, "dark", "daily after archive toggle")
    page.click("#theme-toggle")
    page.reload(wait_until="load")
    assert_theme(page, "light", "daily after reload")
    assert not errors, f"toggle: errors {errors}"
    context.close()
    lines.append("OK   theme toggle: Tab+Enter/Space, aria-pressed, persists across "
                 "home -> archive -> daily and reload")

    # Applied before paint: with app.js blocked, only the inline head script
    # can apply the stored choice.
    context = new_context(browser, theme="light", viewport={"width": 768, "height": 1024})
    context.route("**/assets/app.js", lambda route: route.abort())
    page = context.new_page()
    page.goto(f"{base}/archive.html", wait_until="load")
    assert_theme(page, "light", "pre-paint (app.js blocked)")
    context.close()
    lines.append("OK   pre-paint: stored theme applied by the inline head script alone "
                 "(app.js blocked)")

    # Storage that throws: default dark, toggle still works, no errors.
    context = new_context(browser, viewport={"width": 768, "height": 1024})
    context.add_init_script(
        "Object.defineProperty(window, 'localStorage', { configurable: true,"
        " get() { throw new DOMException('blocked', 'SecurityError'); } });"
    )
    page = context.new_page()
    errors = record_errors(page)
    page.goto(f"{base}/index.html", wait_until="load")
    assert_theme(page, "dark", "storage blocked default")
    page.click("#theme-toggle")
    assert_theme(page, "light", "storage blocked toggle")
    assert not errors, f"storage blocked: errors {errors}"
    context.close()
    lines.append("OK   storage blocked: no errors, dark default, toggle works for the page")
    return lines


def run_motion_and_noscript(browser, base: str) -> list[str]:
    lines = []
    context = new_context(browser, viewport={"width": 768, "height": 1024}, reduced_motion="reduce")
    page = context.new_page()
    errors = record_errors(page)
    for _, rel in PAGES:
        page.goto(f"{base}/{rel}", wait_until="load")
        assert page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches")
        page.click("#theme-toggle")
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        moving = page.evaluate(
            """() => {
              const bad = [];
              for (const el of document.querySelectorAll('*')) {
                const cs = getComputedStyle(el);
                if (cs.animationName !== 'none' ||
                    cs.transitionDuration.split(',').some(d => parseFloat(d) > 0)) bad.push(el.tagName);
              }
              return { bad, running: document.getAnimations().length };
            }"""
        )
        assert not moving["bad"] and moving["running"] == 0, f"reduced motion {rel}: {moving}"
        page.click("#theme-toggle")
    assert not errors, f"reduced motion: errors {errors}"
    context.close()
    lines.append("OK   reduced motion: no animations/transitions on any element, "
                 "toggle and details included")

    for width, height in ((375, 812), (1280, 900)):
        context = new_context(browser, java_script_enabled=False, viewport={"width": width, "height": height})
        page = context.new_page()
        for name, rel in PAGES:
            label = f"noscript {width} {name}"
            page.goto(f"{base}/{rel}", wait_until="load")
            assert_theme(page, "dark", label)
            assert not page.locator("#theme-toggle").is_visible(), f"{label}: inert toggle shown"
            assert_contrast(page, label)
            assert_no_overflow(page, label)
            shot(page, f"noscript-{name}-{width}")
        context.close()
    lines.append("OK   noscript: dark default readable (contrast + overflow), toggle hidden, "
                 "home/archive/daily at 375 and 1280")
    return lines


def run_live_font_check(browser, base: str) -> str:
    """Attempt a real Google Fonts load. Network failure => UNTESTED, not FAIL."""
    context = browser.new_context(viewport={"width": 768, "height": 1024})
    page = context.new_page()
    statuses: dict[str, int] = {}
    page.on("response", lambda r: statuses.__setitem__(r.url, r.status)
            if any(h in r.url for h in FONT_HOSTS) else None)
    try:
        page.goto(f"{base}/index.html", wait_until="load", timeout=20000)
        page.evaluate("() => document.fonts.ready.then(() => true)")
        loaded = page.evaluate(
            "() => Promise.all(['400','500','600','700'].map(w => document.fonts.load(w + ' 16px Inter')))"
            ".then(() => ['400','500','600','700'].filter(w => document.fonts.check(w + ' 16px Inter')))"
        )
    except Exception as exc:  # noqa: BLE001 - any network/browser failure
        context.close()
        return f"UNTESTED live Google Fonts load: {type(exc).__name__}: {exc}"
    css_status = statuses.get(FONT_URL)
    woff = [u for u, s in statuses.items() if "fonts.gstatic.com" in u and s == 200]
    context.close()
    if css_status != 200 or not woff:
        return (f"UNTESTED live Google Fonts load: stylesheet status {css_status}, "
                f"{len(woff)} font files fetched (network blocked or offline)")
    assert loaded == ["400", "500", "600", "700"], f"live fonts: Inter weights loaded {loaded}"
    return (f"OK   live Google Fonts: stylesheet 200, {len(woff)} font file(s) 200, "
            f"Inter {'/'.join(loaded)} available (this network, this run)")
