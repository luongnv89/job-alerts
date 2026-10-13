# job-alerts

Daily evidence-backed AI engineering job alerts, published as a static site
set as a late-1800s / early-1900s printed newspaper (aged paper by default,
with a slightly darker paper variant), rendered from strict JSON research reports by a dependency-free Python 3
builder.

Entries record evidence checks made on each report date. They are not a live
availability guarantee, and `rejected`/`near-miss` entries are research leads —
never recommendations. Candidate personal details never appear in this
repository or on the site.

## Layout

```
assets/                site resources copied into dist/assets/
  template.html        page shell (tokens filled by the builder)
  style.css            printed-newspaper stylesheet (paper/ink custom properties)
  app.js               theme toggle + filter logic (no cookies, no network;
                       one local-storage key for the theme choice)
  report.schema.json   machine-readable report contract
data/reports/          YYYY-MM-DD.json research reports (site's only content)
design/
  coinbase.design.json archived former design source (no longer used)
scripts/
  validate.py          strict report validator
  build_site.py        standalone renderer + validator (stdlib only)
tests/                 unit tests with synthetic safety/growth fixtures and public-content baselines
  test_design_system.py  newspaper layout/palette/font/theme regression (no browser)
  test_content_preservation.py  redesign never drops job text, links or data
                       (compares with committed fixtures/newspaper-baseline;
                       runs in CI, fails if the required baseline is missing)
  browser_check.py     optional Playwright suites (seed, live, design, fonts)
  design_checks.py     Playwright newspaper checks used by browser_check.py
  seed_reports/        frozen snapshot of the original dated reports; exact-count
                       tests build from this so they stay green as data grows
dist/                  build output (gitignored)
SCHEMA.md              report contract documentation
```

## Commands

```bash
# validate report data (exit 1 on any violation)
python scripts/validate.py                 # or: --reports DIR

# validate only, write nothing
python scripts/build_site.py --validate-only

# unit tests
python -m unittest discover -s tests -v

# optional browser suites (needs: pip install playwright && playwright install chromium)
python tests/browser_check.py              # screenshots -> .work/screenshots/newspaper-*

# build the site into dist/
python scripts/build_site.py
python scripts/build_site.py --reports data/reports --output dist \
    --base-url https://luongnv89.github.io/job-alerts
```

`scripts/build_site.py` is standalone (Python standard library only): it
validates every report first and renders every page in memory, so an invalid
report can never replace good output. Each validated report is also exported
verbatim to `dist/reports/YYYY-MM-DD.json`, linked as **Download JSON** from
the home hero (latest report) and from each report page.

### Output safety

Before anything is written or deleted, `--output` is checked:

- **Symlinks are refused.** If the output path itself or any component of it
  is a symlink, the build stops; it is never resolved and followed.
- **Protected paths are refused:** the repository root and its ancestors, any
  directory containing (or inside) `.git`, the input `data/`, `design/`, `assets/`,
  `scripts/`, `tests/` directories and the `--reports` directory, including
  overlaps in either direction.
- **Only renderer-owned folders are replaced.** Every build writes a
  `.job-alerts-site` ownership marker into the output root. An existing output
  folder is replaced only if it carries that exact marker, is empty, or is an
  exact legacy generated layout (only `index.html` and `archive.html` from this
  renderer, dated `reports/*.html|json` files and the flat `assets/` copy —
  no unrelated files, folders or symlinks). Any other existing folder, or an
  existing file, is refused with nothing deleted. A fresh, absent path is
  allowed.

Replacement is a rename sequence on the same filesystem, not a single atomic
swap: the new site is staged in a sibling temporary folder, the old output is
renamed to a unique sibling backup (`.NAME-backup-…`), and the staging folder
is renamed into place. If that install rename fails, the backup is renamed
back and the build exits 1 saying the previous output was restored; if even
the rollback fails, the error names the backup folder where the previous
output is kept intact. The backup is removed only after a successful install.

## The site

- `dist/index.html` — latest report hero, a Download JSON link to that
  report's export, and the entry listing.
- `dist/reports/YYYY-MM-DD.html` — one page per report with the same hero,
  coverage and criteria panels, plus a Download JSON link.

Home and report pages share one hero block, filled only from the report data:

- **Result:** `None` when zero entries qualified; otherwise `Complete` or
  `Partial` by comparing the qualified count K with the recorded
  `requestedCount` N. If `requestedCount` is absent, the hero says
  "requested count not recorded" and a non-zero result is shown as
  `Undetermined` — N is never guessed.
- **Counts:** "K qualified of N requested · E examined", all calculated from
  the report.
- **Decision:** the recorded `decision`, or "Decision not recorded in this
  report."
- **Uncertainty & caveats:** every `uncertainty` entry plus an independent
  recheck reminder for the report date, shown openly (not inside a collapsed
  panel).
- `dist/reports/YYYY-MM-DD.json` — the exact validated public report payload
  for machine consumption (no private fields; strict schema only).
- `dist/archive.html` — daily archive with per-date qualified/examined counts.

Listing pages share one filter bar (AND semantics, visible counter, Reset,
no-results message, URL query state):

| Filter | Values |
| --- | --- |
| Search | company, title and role notes (case-insensitive substring) |
| Report date | every report date, or all dates |
| Status | qualified / rejected / near-miss |
| Work location | remote / hybrid / on-site / unknown |
| Equity | explicit equity / none stated / unknown |
| Salary evidence | known annual EUR base · known annual base non-EUR · numeric range with unconfirmed annual basis · no published figures |

Sections are **Qualified matches** and **Research leads** (not
recommendations). Each entry is set as a boxed classified notice and shows the recorded gates, gate notes, salary and
equity evidence, plus native `<details>` panels for company/role evidence
notes and per-source evidence notes with direct links. Qualified entries get
an Apply link; the others get a **View listing** link that is explicitly not a
recommendation.

Evidence note wording: source `quote` fields are recorded research notes that
may paraphrase the source. The site labels them "Evidence note" and never
presents them as verbatim employer quotations.

Noscript keeps the full pre-rendered content readable on the default aged
paper (filters and the theme toggle are hidden, and every entry is shown with
its date badge).

## Design: printed newspaper

The site is styled as a late-1800s / early-1900s printed newspaper in plain
CSS (no framework, no npm packages). The former Coinbase design JSON stays
archived in `design/` but is not used.

- **Full width:** `.wrap` is `width: 100%; max-width: none` with at most
  16px outer padding at every width; there is no narrow centred wrapper.
- **Masthead:** "Job Alerts" in UnifrakturMaguntia blackletter across the
  full width, then a dateline between double rules: `Vol. I · No. N` (N is
  the report's position in date order, oldest = No. 1), the real report date
  written out from the ISO date (e.g. "Tuesday, 13 October 2026") and
  "Price: Free · Web Edition". The archive shows the full issue and date
  range. No historical dates are invented. Navigation sits in a ruled bar.
- **Columns:** classified notices flow in CSS columns with thin vertical
  rules: 1 column below 640px, 2 from 640px, and from 1024px a page grid
  with Qualified matches in the first page column and Research leads filling
  the remaining 2 (1024px+) or 3 (1440px+), so desktops show 3–4 columns.
  Notices use `break-inside: avoid`; nothing is clipped, clamped or given a
  fixed height. The archive table keeps a bounded horizontal scroll on
  narrow screens.
- **Classified notices:** each `.job-card` is a boxed advertisement (single
  rule for research leads, double rule for qualified entries) headed by a
  decorative `aria-hidden` "Help Wanted" (qualified) or "Wanted" label,
  with the recorded company in small caps and the recorded title shown in
  bold uppercase through CSS only — the DOM keeps the original text. The
  links close the notice as a period line, "For particulars and
  application: Apply · Open listing" (qualified) or "For particulars: View
  listing" (research leads), with the original labels and targets. The
  research-lead warning is boxed in bold. Notice text uses 1.35 leading.
- **Type:** serif only, including inputs, selects, buttons, code and small
  print: UnifrakturMaguntia (masthead), Playfair Display (headlines),
  Libre Baskerville (body), then Georgia, Times New Roman, Times, serif.
  Body text is justified with `hyphens: auto` (`lang="en"`). Only the lead
  paragraph gets a drop cap: the home and archive standfirst, and the
  summary on report pages. Section heads are uppercase between thin rules.
- **Palette:** aged paper `#f4ecd8` (default) or `#e8dcc0` (darker variant),
  with paper patches `#ece2c8` / `#efe5cc` for inputs, and near-black ink
  `#2b2b2b` / `#1a1a1a` for text and rules. Rejected badges print in
  reverse (paper type on an ink block). There are no other colours, no
  rounded corners, shadows, gradients, images, SVG, icons or emoji.
  Disclosures hide the native marker (`list-style: none`,
  `::-webkit-details-marker`) and use underlined labels; selects use
  `appearance: none` with no chevron; buttons are square ruled text.
- **Theme:** aged paper (light) is the default. A small inline script in
  `<head>` runs before the stylesheets and adds `html.dark` only when the
  stored choice is `dark`, so there is no flash. The "Dark theme" toggle is
  a real `<button aria-pressed>` (Tab, Enter, Space) and saves
  `light`/`dark` under the local-storage key `job-alerts-theme`. Other open
  tabs follow the change. Storage errors are caught; if storage is blocked
  the toggle still works for the current page. The dark choice is a slightly
  darker paper that keeps dark ink, not an inverted UI. Without JavaScript
  the paper default is used and the toggle is hidden.
- **Focus:** `:focus-visible` is a 2px solid ink outline with a 2px offset
  and never a shadow.
- **Motion:** no animations or transitions; a `prefers-reduced-motion`
  guard keeps it that way.
- **Print:** prints on paper colour with every notice (including ones hidden
  by filters) and the contents of closed evidence panels (Chromium's
  `::details-content`), plus each notice link's URL. Filters and the toggle
  are not printed.

### Checks

These checks are evidence, not a guarantee of full WCAG conformance:

- `tests/test_design_system.py` parses the CSS, template and built pages:
  palette range, ink-on-paper contrast (>= 7:1 in both variants), serif-only
  stacks, no radius/shadow/gradient/image declarations, full-width wrapper,
  column counts, notice borders and labels, dateline derivation, theme
  wiring and the font disclosure.
- `tests/test_content_preservation.py` builds the hashed baseline report set
  and checks that every original text string, link target and anchor label
  survives per notice and across each page's `<main>`, that only the four
  decorative strings were added, and that the filter payload, JSON exports
  and source report hashes are unchanged.
- `tests/browser_check.py` measures computed styles in Chromium for both
  paper variants at 375, 639, 640, 768, 1280 and 1920px on the home, archive
  and a daily report page. It counts the rendered notice columns (1/1/2/2/3/4),
  checks <=16px outer padding and `max-width: none`, scans every element and
  pseudo-element for radius, shadow, gradient, image, filter, non-paper/ink
  colours and non-serif fonts, and checks notice borders and labels,
  justification, contrast (including hover, expanded evidence and zero
  states), shadow-free focus outlines, the toggle (cross-tab, blocked
  storage, pre-paint), reduced motion, noscript and print. In the last run
  the lowest measured text contrast was 10.98:1 (aged paper) and 12.79:1
  (darker paper).
- The browser checks do not cover other browsers, native `<select>`
  dropdown popups, forced-colours or high-contrast modes, or screen-reader
  behaviour.

## Privacy and network

- No analytics, and the site itself sets no cookies.
- The theme choice is kept only in this browser's local storage and is never
  transmitted.
- The UnifrakturMaguntia, Playfair Display and Libre Baskerville stylesheet
  and font files are requested from Google Fonts (`fonts.googleapis.com`,
  `fonts.gstatic.com`). Those requests reveal the visitor's IP address and
  browser details to Google. If they are blocked, Georgia, Times or another
  installed serif font is used and the site stays fully readable.
- There are no other external runtime requests.

The browser suites block Google Fonts so their results do not depend on the
network. A separate live check tries a real font load and reports
`UNTESTED` rather than passing if the network is unavailable.

## Adding a report

1. Create `data/reports/YYYY-MM-DD.json` following `SCHEMA.md`
   (camelCase contract, `schemaVersion: 1`; file name must equal `date`).
2. Run `python scripts/validate.py` and fix any reported violations.
3. Run `python -m unittest discover -s tests` and `python scripts/build_site.py`.
   No test changes are needed for a new report: exact-count tests build from
   the frozen `tests/seed_reports/` snapshot, and every live-data check derives
   its counts from `data/reports/` itself.
4. Commit the report JSON (never `dist/`, which is gitignored).

Never include candidate names, CV/work-authorization details, contact
information, local paths or non-public URLs. Unknown keys are rejected.

## Deployment

`.github/workflows/pages.yml` runs on pushes to `main` and on
`workflow_dispatch`:

1. `python scripts/validate.py` — report validation
2. `python -m unittest discover -s tests -v` — unit tests
3. `python scripts/build_site.py --output dist` — build
4. upload the `dist/` Pages artifact and deploy with `actions/deploy-pages`

The site is served from the `/job-alerts/` project path with relative asset
links, so no base-path configuration is needed.

## License

MIT — see [LICENSE](LICENSE).
