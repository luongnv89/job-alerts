"""Playwright newspaper-design checks (imported by tests/browser_check.py).

Everything here measures the real rendered page: colours come from
getComputedStyle, backgrounds are composited up the ancestor chain,
contrast uses the WCAG 2.x relative-luminance formula and newspaper
columns are counted from the actual positions of the rendered notices.

Google Fonts requests are blocked in every context except the explicit
live-font check, so the main matrix is deterministic offline and proves the
serif fallback stack (Georgia / Times / installed serif) stays readable.
The live check reports UNTESTED (not PASS) when the network is unavailable.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_URL = (
    "https://fonts.googleapis.com/css2?family=Libre+Baskerville:ital,wght@0,400;0,700;1,400"
    "&family=Playfair+Display:wght@700;900&family=UnifrakturMaguntia&display=swap"
)
FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")
THEME_KEY = "job-alerts-theme"
SHOTS = ROOT / ".work" / "screenshots"
PREFIX = "newspaper"
# Width -> expected number of classified page columns (1 below 640px,
# 2 on tablets, 3 or 4 on desktops).
WIDTHS = {375: (1,), 639: (1,), 640: (2,), 768: (2,), 1280: (3, 4), 1920: (3, 4)}
EXACT_COLUMNS = {375: 1, 639: 1, 640: 2, 768: 2, 1280: 3, 1920: 4}
HEIGHT = 900
FOCUS_WIDTHS = (375, 1920)
PAGES = [("home", "index.html"), ("archive", "archive.html"), ("daily", "reports/2026-10-11.html")]
PAPER = {"light": "rgb(244, 236, 216)", "dark": "rgb(232, 220, 192)"}
INK = {"light": "rgb(43, 43, 43)", "dark": "rgb(26, 26, 26)"}

# Shared colour helpers injected into every measuring script.
COLOR_HELPERS = r"""
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
  const name = (el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
    (el.className && typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).join(".") : "");
"""

# Returns {checked, failures, min} for every visible text-bearing element
# under `root` (default body), plus input placeholders and control borders.
CONTRAST_JS = r"""
(rootSel) => {
""" + COLOR_HELPERS + r"""
  const visible = (el) => {
    if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && r.right > 0 && r.left < document.documentElement.scrollWidth;
  };
  const root = rootSel ? document.querySelector(rootSel) : document.body;
  const out = { checked: 0, failures: [], min: 99 };
  if (!root) return out;
  for (const el of [root, ...root.querySelectorAll("*")]) {
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

# Focus indicator of document.activeElement.
FOCUS_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  if (!el.dataset.focusId) {
    window.__focusIds = (window.__focusIds || 0) + 1;
    el.dataset.focusId = String(window.__focusIds);
  }
""" + COLOR_HELPERS + r"""
  const cs = getComputedStyle(el);
  const surface = bgOf(el.parentElement);
  return {
    id: el.dataset.focusId,
    el: name(el),
    focusVisible: el.matches(":focus-visible"),
    style: cs.outlineStyle,
    width: parseFloat(cs.outlineWidth),
    offset: cs.outlineOffset,
    boxShadow: cs.boxShadow,
    ringVsSurface: +ratio(over(parse(cs.outlineColor), surface), surface).toFixed(2),
  };
}
"""

# Whole-page newspaper invariants: no rounded corners, shadows, gradients,
# images, filters or icons; only paper/ink colours; serif fonts everywhere.
ORNAMENT_JS = r"""
(allowed) => {
  const bad = [];
  const okColour = (c) => c === "rgba(0, 0, 0, 0)" || allowed.includes(c);
  const els = [document.documentElement, ...document.querySelectorAll("*")];
  let checked = 0;
  for (const el of els) {
    if (["SCRIPT", "STYLE", "NOSCRIPT", "META", "LINK", "TITLE", "HEAD"].includes(el.tagName)) continue;
    for (const pseudo of [null, "::before", "::after", "::marker", "::placeholder"]) {
      const cs = getComputedStyle(el, pseudo);
      if (pseudo && pseudo !== "::marker" && pseudo !== "::placeholder" && (cs.content === "none" || cs.content === "normal")) continue;
      const tag = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (typeof el.className === "string" && el.className ? "." + el.className.split(/\s+/).join(".") : "") + (pseudo || "");
      checked++;
      for (const corner of ["borderTopLeftRadius", "borderTopRightRadius", "borderBottomLeftRadius", "borderBottomRightRadius"]) {
        if (cs[corner] !== "0px") bad.push(tag + " radius " + cs[corner]);
      }
      if (cs.boxShadow !== "none") bad.push(tag + " box-shadow " + cs.boxShadow);
      if (cs.textShadow !== "none") bad.push(tag + " text-shadow " + cs.textShadow);
      if (cs.backgroundImage !== "none") bad.push(tag + " background-image " + cs.backgroundImage);
      if (cs.filter !== "none") bad.push(tag + " filter " + cs.filter);
      if (cs.listStyleImage !== "none") bad.push(tag + " list-style-image");
      if (cs.maskImage && cs.maskImage !== "none") bad.push(tag + " mask");
      if (!okColour(cs.backgroundColor)) bad.push(tag + " background " + cs.backgroundColor);
      if (pseudo !== "::marker" && !okColour(cs.color)) bad.push(tag + " color " + cs.color);
      for (const side of ["Top", "Right", "Bottom", "Left"]) {
        if (cs["border" + side + "Style"] !== "none" && parseFloat(cs["border" + side + "Width"]) > 0 && !okColour(cs["border" + side + "Color"])) {
          bad.push(tag + " border " + cs["border" + side + "Color"]);
        }
      }
      const fam = cs.fontFamily.toLowerCase();
      if (/sans-serif|monospace|inter\b|system-ui|arial|helvetica/.test(fam) || !/serif$/.test(fam.trim())) {
        bad.push(tag + " font " + cs.fontFamily);
      }
    }
  }
  const media = document.querySelectorAll("img, svg, canvas, picture, video, iframe").length;
  if (media) bad.push(media + " image/svg/canvas elements");
  return { checked, bad: bad.slice(0, 12), total: bad.length };
}
"""

LAYOUT_JS = r"""
() => {
  const vw = document.documentElement.clientWidth;
  const wraps = [...document.querySelectorAll(".wrap")].map((w) => {
    const cs = getComputedStyle(w);
    const r = w.getBoundingClientRect();
    return { pl: parseFloat(cs.paddingLeft), pr: parseFloat(cs.paddingRight), maxWidth: cs.maxWidth,
             left: r.left, right: r.right, ml: parseFloat(cs.marginLeft), mr: parseFloat(cs.marginRight) };
  });
  const cards = [...document.querySelectorAll("#qualified-list .job-card, #leads-list .job-card")]
    .filter((c) => c.getBoundingClientRect().width > 0);  // rendered (noscript shows [hidden] notices)
  const lefts = [];
  for (const c of cards) {
    const l = c.getBoundingClientRect().left;
    if (!lefts.some((x) => Math.abs(x - l) < 8)) lefts.push(l);
  }
  const leads = document.getElementById("leads-list");
  const lcs = leads ? getComputedStyle(leads) : null;
  const mainRect = document.querySelector("main").getBoundingClientRect();
  const body = getComputedStyle(document.body);
  return {
    vw, wraps, columns: lefts.length, cards: cards.length,
    leadsColumnCount: lcs ? lcs.columnCount : null,
    ruleStyle: lcs ? lcs.columnRuleStyle : null,
    ruleWidth: lcs ? parseFloat(lcs.columnRuleWidth) : null,
    researchBorder: (() => { const s = document.getElementById("research-leads"); if (!s) return null;
      const cs = getComputedStyle(s); return cs.borderLeftStyle + " " + cs.borderLeftWidth; })(),
    main: { left: mainRect.left, right: mainRect.right },
    bodyMargin: [body.marginLeft, body.marginRight],
  };
}
"""

TYPE_JS = r"""
() => {
  const cs = (sel, pseudo) => { const el = document.querySelector(sel); return el ? getComputedStyle(el, pseudo || null) : null; };
  const out = { lang: document.documentElement.lang, notices: [], problems: [] };
  for (const card of document.querySelectorAll(".job-card")) {
    if (card.getBoundingClientRect().width === 0) continue;
    const c = getComputedStyle(card);
    const label = card.querySelector(".notice-label");
    const title = card.querySelector(".card-title");
    const lc = label && getComputedStyle(label);
    const tc = getComputedStyle(title);
    const para = card.querySelector(".card-salary");
    const pc = getComputedStyle(para);
    const lh = parseFloat(pc.lineHeight) / parseFloat(pc.fontSize);
    const qualified = card.classList.contains("status-qualified");
    const n = card.dataset.key;
    if (!["solid", "double"].includes(c.borderTopStyle) || parseFloat(c.borderTopWidth) < 1) out.problems.push(n + " border " + c.borderTopStyle);
    if (qualified && c.borderTopStyle !== "double") out.problems.push(n + " qualified notice not double-ruled");
    if (c.breakInside !== "avoid") out.problems.push(n + " break-inside " + c.breakInside);
    if (!label || lc.textTransform !== "uppercase" || Number(lc.fontWeight) < 700) out.problems.push(n + " label");
    if (label && label.textContent !== (qualified ? "Help Wanted" : "Wanted")) out.problems.push(n + " label text " + label.textContent);
    if (label && label.getAttribute("aria-hidden") !== "true") out.problems.push(n + " label not decorative");
    if (tc.textTransform !== "uppercase" || Number(tc.fontWeight) < 700) out.problems.push(n + " title " + tc.textTransform + " " + tc.fontWeight);
    // Uppercase comes from CSS: the DOM keeps the recorded mixed-case title.
    if (!/[a-z]/.test(title.textContent)) out.problems.push(n + " title text rewritten in capitals");
    if (lh < 1.3 - 1e-3 || lh > 1.4 + 1e-3) out.problems.push(n + " notice line-height " + lh.toFixed(2));
    if (pc.textAlign !== "justify") out.problems.push(n + " notice text-align " + pc.textAlign);
    if (pc.hyphens !== "auto") out.problems.push(n + " hyphens " + pc.hyphens);
    if (!qualified) {
      const warn = card.querySelector(":scope > .fine-print");
      if (!warn || !/Research lead/.test(warn.textContent) || Number(getComputedStyle(warn).fontWeight) < 700 || getComputedStyle(warn).borderTopStyle !== "solid") {
        out.problems.push(n + " research-lead warning not prominent");
      }
    }
    for (const s of card.querySelectorAll("summary")) {
      const sc = getComputedStyle(s);
      if (sc.display === "list-item" && sc.listStyleType !== "none") out.problems.push(n + " summary marker " + sc.listStyleType);
      if (!sc.textDecorationLine.includes("underline")) out.problems.push(n + " summary not underlined");
    }
    out.notices.push(n);
  }
  for (const sel of ["#filter-date", "#filter-status", "#filter-mode", "#filter-equity", "#filter-salary", "#filter-search"]) {
    const s = cs(sel);
    if (s && s.appearance !== "none") out.problems.push(sel + " appearance " + s.appearance);
  }
  const lead = cs(".hero .standfirst") || cs(".page-report .hero-report .summary");
  if (lead) {
    if (lead.textAlign !== "justify" || lead.hyphens !== "auto") out.problems.push("lead paragraph not justified/hyphenated");
    const drop = cs(".hero .standfirst", "::first-letter") || cs(".page-report .hero-report .summary", "::first-letter");
    if (drop && parseFloat(drop.fontSize) < 2.5 * parseFloat(lead.fontSize)) out.problems.push("no drop cap");
    out.dropCap = true;
  }
  const notice = document.querySelector(".job-card .card-salary");
  if (notice && parseFloat(getComputedStyle(notice, "::first-letter").fontSize) > 1.5 * parseFloat(getComputedStyle(notice).fontSize)) {
    out.problems.push("drop cap applied to notices");
  }
  for (const h of document.querySelectorAll(".job-section > h2, .filters > h2")) {
    const hc = getComputedStyle(h);
    if (hc.textTransform !== "uppercase" && hc.fontVariantCaps !== "small-caps") out.problems.push("section header caps");
    if (hc.borderTopStyle === "none" || hc.borderBottomStyle === "none") out.problems.push("section header rules");
  }
  const brand = cs(".brand");
  out.brandFont = brand.fontFamily;
  const dl = cs(".dateline");
  if (dl.borderTopStyle !== "double" || dl.borderBottomStyle !== "double") out.problems.push("dateline not between double rules");
  out.controlFonts = ["input", "select", "button", "code", ".mono", ".fine-print"]
    .map((s) => document.querySelector(s)).filter(Boolean).map((e) => getComputedStyle(e).fontFamily);
  return out;
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
    assert is_dark == (theme == "dark"), f"{label}: expected {theme} paper"
    bg, fg = page.evaluate(
        "() => [getComputedStyle(document.body).backgroundColor, getComputedStyle(document.body).color]"
    )
    assert bg == PAPER[theme], f"{label}: paper {bg}, expected {PAPER[theme]} for {theme}"
    assert fg == INK[theme], f"{label}: ink {fg}, expected {INK[theme]} (dark ink in both variants)"


def assert_ornament_free(page, label: str) -> int:
    allowed = sorted(set(PAPER.values()) | set(INK.values()) | {"rgb(236, 226, 200)", "rgb(239, 229, 204)"})
    result = page.evaluate(ORNAMENT_JS, allowed)
    assert result["total"] == 0, f"{label}: {result['total']} ornament/colour/font violations {result['bad']}"
    return result["checked"]


def assert_layout(page, width: int, label: str, classifieds: bool) -> dict:
    info = page.evaluate(LAYOUT_JS)
    assert info["bodyMargin"] == ["0px", "0px"], f"{label}: body margin {info['bodyMargin']}"
    for wrap in info["wraps"]:
        assert wrap["maxWidth"] == "none", f"{label}: .wrap max-width {wrap['maxWidth']}"
        assert wrap["pl"] <= 16 and wrap["pr"] <= 16, f"{label}: outer padding {wrap}"
        assert wrap["ml"] == 0 and wrap["mr"] == 0, f"{label}: outer margin {wrap}"
        assert abs(wrap["left"]) < 1 and abs(wrap["right"] - info["vw"]) < 1, (
            f"{label}: .wrap not full viewport width {wrap} vw {info['vw']}"
        )
    assert info["main"]["left"] < 1 and info["main"]["right"] > info["vw"] - 1, f"{label}: main {info['main']}"
    if classifieds:
        want = EXACT_COLUMNS[width]
        assert info["columns"] == want, (
            f"{label}: {info['columns']} classified page columns, expected {want} ({info})"
        )
        assert info["columns"] in WIDTHS[width]
        if want > 1:
            assert info["ruleStyle"] == "solid" and info["ruleWidth"] >= 1, f"{label}: column rule {info}"
        if width >= 1024:
            assert info["researchBorder"].startswith("solid"), f"{label}: no rule between sections"
    return info


def assert_typography(page, label: str) -> dict:
    info = page.evaluate(TYPE_JS)
    assert info["lang"] == "en", f"{label}: lang {info['lang']}"
    assert not info["problems"], f"{label}: {info['problems'][:10]}"
    assert info["brandFont"].startswith("UnifrakturMaguntia"), f"{label}: masthead font {info['brandFont']}"
    for family in info["controlFonts"]:
        assert family.strip().endswith("serif") and "sans-serif" not in family and "monospace" not in family, (
            f"{label}: control font {family}"
        )
    return info


def shot(page, name: str, full_page: bool = True) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{PREFIX}-{name}.png"), full_page=full_page)


def check_focus_cycle(page, label: str, max_tabs: int = 150) -> int:
    """Tab through the page; every stop shows a >=2px solid outline with
    >=3:1 contrast against its surface and no shadow."""
    page.reload(wait_until="load")
    seen: list[str] = []
    kinds: set[str] = set()
    for _ in range(max_tabs):
        page.keyboard.press("Tab")
        info = page.evaluate(FOCUS_JS)
        if info is None or info["id"] in seen:
            break
        seen.append(info["id"])
        kinds.add(info["el"])
        where = f"{label}: focus on {info['el']}"
        assert info["focusVisible"], f"{where}: not :focus-visible"
        assert info["style"] == "solid", f"{where}: outline style {info['style']}"
        assert info["width"] >= 2, f"{where}: outline width {info['width']}"
        assert info["boxShadow"] == "none", f"{where}: focus uses a shadow {info['boxShadow']}"
        assert info["ringVsSurface"] >= 3, f"{where}: outline contrast {info['ringVsSurface']}:1"
    assert len(seen) >= 5, f"{label}: only {len(seen)} focus stops"
    for required in ("a.skip-link", "button#theme-toggle.btn-ghost.theme-toggle"):
        assert required in kinds, f"{label}: focus cycle never reached {required}"
    return len(seen)


HOVER_TARGETS = [
    ".hero-links .btn:not(.btn-quiet)",
    ".hero-links .btn-quiet",
    "#theme-toggle",
    ".site-nav a",
    ".brand",
    ".job-card:not([hidden]) .card-actions a",
    ".job-card:not([hidden]) .panel > summary",
    ".archive-table tbody tr",
    ".report-nav a",
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


def open_one_source(page) -> None:
    """Expand the first visible notice's source evidence disclosure."""
    page.evaluate(
        """() => {
          const card = [...document.querySelectorAll('.job-card')].find(c => !c.hidden);
          const panels = card.querySelectorAll('details');
          panels[panels.length - 1].open = true;
        }"""
    )


def run_theme_matrix(browser, base: str) -> list[str]:
    """Both paper variants x 375/639/640/768/1280/1920 x home/archive/daily."""
    lines = []
    for theme in ("light", "dark"):
        for width in WIDTHS:
            context = new_context(
                browser, theme=None if theme == "light" else "dark",
                viewport={"width": width, "height": HEIGHT},
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
                    f"{label}: Google Fonts link missing"
                )
                family = page.evaluate("() => getComputedStyle(document.body).fontFamily")
                assert family.startswith('"Libre Baskerville"'), f"{label}: body font-family {family}"
                assert_no_overflow(page, label)
                if name != "archive":
                    page.select_option("#filter-date", "all")
                assert_layout(page, width, label, classifieds=name != "archive")
                assert_typography(page, label)
                elements = assert_ornament_free(page, label)
                result = assert_contrast(page, label)
                stats.append(f"{name} {result['checked']} texts min {result['min']}:1, {elements} styles")

                if name == "home":
                    open_one_source(page)
                    assert_contrast(page, f"{label} one source expanded")
                    if width in (375, 768, 1920):
                        shot(page, f"home-{theme}-{width}-all-dates-source-open")
                    page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
                    assert_contrast(page, f"{label} all expanded")
                    assert_ornament_free(page, f"{label} all expanded")
                    assert_no_overflow(page, f"{label} all expanded")
                    assert_layout(page, width, f"{label} all expanded", classifieds=True)
                    if width in (375, 1920):
                        shot(page, f"home-{theme}-{width}-all-expanded")
                    if width == 1920:
                        notice = page.locator(".job-card.status-qualified:not([hidden])").first
                        notice.screenshot(path=str(SHOTS / f"{PREFIX}-qualified-notice-{theme}.png"))
                    page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = false)")
                    page.fill("#filter-search", "zzz-no-such-entry")
                    assert page.locator("#no-results").is_visible(), f"{label}: zero state hidden"
                    assert_contrast(page, f"{label} zero-state")
                    page.click("#filter-reset")
                    page.select_option("#filter-date", "all")
                    hovered = check_hover_states(page, label)
                    assert hovered >= 6, f"{label}: only {hovered} hover targets measured"
                    page.click("#filter-reset")
                elif name == "daily":
                    page.click("#filter-reset")
                    assert page.locator("#qualified-empty-data").is_visible(), f"{label}: zero state"
                    check_hover_states(page, label)
                    if width in (375, 1920):
                        shot(page, f"daily-{theme}-{width}")
                else:
                    page.evaluate("() => { const s = document.querySelector('.table-scroll');"
                                  " if (s) s.scrollLeft = s.scrollWidth; }")
                    assert_contrast(page, f"{label} table scrolled")
                    check_hover_states(page, label)
                    if width in (375, 1920):
                        shot(page, f"archive-{theme}-{width}")
                if width in FOCUS_WIDTHS:
                    stops = check_focus_cycle(page, label)
                    stats[-1] += f", {stops} focus stops"
            assert not errors, f"design {theme} {width}: console/page errors {errors}"
            assert any(FONT_URL == url for url in context.font_requests), (
                f"design {theme} {width}: page never requested the Google Fonts URL"
            )
            context.close()
            lines.append(f"OK   design {theme} {width}x{HEIGHT} ({EXACT_COLUMNS[width]} col): " + "; ".join(stats))
    return lines


def run_toggle_checks(browser, base: str) -> list[str]:
    lines = []
    context = new_context(browser, viewport={"width": 768, "height": 1024})
    page = context.new_page()
    errors = record_errors(page)
    page.goto(f"{base}/index.html", wait_until="load")
    assert_theme(page, "light", "toggle default")
    assert page.evaluate(f"() => localStorage.getItem('{THEME_KEY}')") is None
    toggle = page.locator("#theme-toggle")
    assert toggle.inner_text().strip().lower() == "dark theme", "toggle accessible label changed"

    page.locator("body").focus()
    for _ in range(10):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.id") == "theme-toggle":
            break
    assert page.evaluate("() => document.activeElement.id") == "theme-toggle", "toggle not reachable by Tab"
    page.keyboard.press("Enter")
    assert_theme(page, "dark", "toggle Enter")
    assert toggle.get_attribute("aria-pressed") == "true"
    assert page.evaluate(f"() => localStorage.getItem('{THEME_KEY}')") == "dark"
    info = page.evaluate(FOCUS_JS)
    assert info["focusVisible"] and info["ringVsSurface"] >= 3 and info["boxShadow"] == "none", (
        f"toggle focus after switch {info}"
    )
    page.keyboard.press("Space")
    assert_theme(page, "light", "toggle Space")
    page.keyboard.press("Enter")
    assert_theme(page, "dark", "toggle Enter again")

    # Cross-tab: a second tab follows the stored choice via the storage event.
    other = context.new_page()
    other.goto(f"{base}/archive.html", wait_until="load")
    assert_theme(other, "dark", "second tab initial")
    page.click("#theme-toggle")
    other.wait_for_function("() => !document.documentElement.classList.contains('dark')")
    assert_theme(other, "light", "second tab after change in first tab")
    assert other.locator("#theme-toggle").get_attribute("aria-pressed") == "false"
    other.close()
    page.click("#theme-toggle")

    page.click(".site-nav a[href='archive.html']")
    page.wait_for_load_state("load")
    assert_theme(page, "dark", "archive after navigation")
    page.click("#theme-toggle")
    assert_theme(page, "light", "archive toggle click")
    assert page.evaluate(f"() => localStorage.getItem('{THEME_KEY}')") == "light"
    page.goto(f"{base}/reports/2026-10-11.html", wait_until="load")
    assert_theme(page, "light", "daily after archive toggle")
    page.click("#theme-toggle")
    page.reload(wait_until="load")
    assert_theme(page, "dark", "daily after reload")
    assert not errors, f"toggle: errors {errors}"
    context.close()
    lines.append("OK   paper toggle: Tab+Enter/Space, aria-pressed, cross-tab storage sync, persists "
                 "across home -> archive -> daily and reload")

    context = new_context(browser, theme="dark", viewport={"width": 768, "height": 1024})
    context.route("**/assets/app.js", lambda route: route.abort())
    page = context.new_page()
    page.goto(f"{base}/archive.html", wait_until="load")
    assert_theme(page, "dark", "pre-paint (app.js blocked)")
    context.close()
    lines.append("OK   pre-paint: stored darker paper applied by the inline head script alone "
                 "(app.js blocked)")

    context = new_context(browser, viewport={"width": 768, "height": 1024})
    context.add_init_script(
        "Object.defineProperty(window, 'localStorage', { configurable: true,"
        " get() { throw new DOMException('blocked', 'SecurityError'); } });"
    )
    page = context.new_page()
    errors = record_errors(page)
    page.goto(f"{base}/index.html", wait_until="load")
    assert_theme(page, "light", "storage blocked default")
    page.click("#theme-toggle")
    assert_theme(page, "dark", "storage blocked toggle")
    page.select_option("#filter-status", "rejected")
    assert "status=rejected" in page.url, "storage blocked: filters stopped working"
    assert not errors, f"storage blocked: errors {errors}"
    context.close()
    lines.append("OK   storage blocked: no errors, aged-paper default, toggle and filters work")
    return lines


def run_motion_noscript_print(browser, base: str) -> list[str]:
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

    for width in (375, 1920):
        context = new_context(browser, java_script_enabled=False, viewport={"width": width, "height": HEIGHT})
        page = context.new_page()
        for name, rel in PAGES:
            label = f"noscript {width} {name}"
            page.goto(f"{base}/{rel}", wait_until="load")
            assert_theme(page, "light", label)
            assert not page.locator("#theme-toggle").is_visible(), f"{label}: inert toggle shown"
            assert_contrast(page, label)
            assert_no_overflow(page, label)
            assert_ornament_free(page, label)
            if name != "archive":
                total = page.locator(".job-card").count()
                visible = page.eval_on_selector_all(".job-card", "els => els.filter(e => e.offsetParent !== null).length")
                assert visible == total, f"{label}: {visible} of {total} notices visible"
                assert_layout(page, width, label, classifieds=True)
                page.locator(".job-card details summary").first.click()
                assert page.locator(".job-card details").first.evaluate("d => d.open"), f"{label}: disclosure"
            shot(page, f"noscript-{name}-{width}")
        context.close()
    lines.append("OK   noscript: paper default readable, all notices shown in newspaper columns, "
                 "native disclosures work, toggle hidden (375 and 1920)")

    context = new_context(browser, viewport={"width": 1280, "height": HEIGHT})
    page = context.new_page()
    page.goto(f"{base}/index.html", wait_until="load")
    hidden_before = page.locator(".job-card[hidden]").count()
    assert hidden_before > 0, "print: expected filtered-out notices before printing"
    page.emulate_media(media="print")
    printed = page.evaluate(
        """() => {
          const cards = [...document.querySelectorAll('.job-card')];
          const shown = cards.filter(c => getComputedStyle(c).display !== 'none').length;
          const notes = [...document.querySelectorAll('.job-card details .evidence-note')];
          const notesShown = notes.filter(n => n.getBoundingClientRect().height > 0).length;
          return { cards: cards.length, shown, notes: notes.length, notesShown,
                   bg: getComputedStyle(document.body).backgroundColor,
                   filters: getComputedStyle(document.getElementById('filter-bar')).display };
        }"""
    )
    assert printed["shown"] == printed["cards"], f"print: notices dropped {printed}"
    assert printed["notesShown"] == printed["notes"] > 0, f"print: collapsed evidence not printed {printed}"
    assert printed["bg"] == PAPER["light"], f"print: paper {printed['bg']}"
    page.pdf(path=str(SHOTS / f"{PREFIX}-print-home.pdf"), print_background=True)
    context.close()
    lines.append(f"OK   print: all {printed['cards']} notices and {printed['notes']} collapsed "
                 "evidence notes printed on paper (PDF saved)")
    return lines


def run_live_font_check(browser, base: str) -> str:
    """Attempt a real Google Fonts load. Network failure => UNTESTED, not FAIL."""
    context = browser.new_context(viewport={"width": 768, "height": 1024})
    page = context.new_page()
    statuses: dict[str, int] = {}
    page.on("response", lambda r: statuses.__setitem__(r.url, r.status)
            if any(h in r.url for h in FONT_HOSTS) else None)
    faces = ["400 16px UnifrakturMaguntia", "700 16px 'Playfair Display'", "900 16px 'Playfair Display'",
             "400 16px 'Libre Baskerville'", "700 16px 'Libre Baskerville'", "italic 400 16px 'Libre Baskerville'"]
    try:
        page.goto(f"{base}/index.html", wait_until="load", timeout=20000)
        loaded = page.evaluate(
            "(faces) => Promise.all(faces.map(f => document.fonts.load(f)))"
            ".then(() => faces.filter(f => document.fonts.check(f)))", faces
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
    assert loaded == faces, f"live fonts: loaded {loaded}"
    return (f"OK   live Google Fonts: stylesheet 200, {len(woff)} font file(s) 200, "
            f"{len(loaded)} faces available (this network, this run)")
