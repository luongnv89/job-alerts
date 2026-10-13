# job-alerts

Daily evidence-backed AI engineering job alerts, published as a static site
styled with the Coinbase design system (dark by default, with a light theme),
rendered from strict JSON research reports by a dependency-free Python 3
builder.

Entries record evidence checks made on each report date. They are not a live
availability guarantee, and `rejected`/`near-miss` entries are research leads —
never recommendations. Candidate personal details never appear in this
repository or on the site.

## Layout

```
assets/                site resources copied into dist/assets/
  template.html        page shell (tokens filled by the builder)
  style.css            Coinbase design-system stylesheet (CSS custom properties)
  app.js               theme toggle + filter logic (no cookies, no network;
                       one local-storage key for the theme choice)
  report.schema.json   machine-readable report contract
data/reports/          YYYY-MM-DD.json research reports (site's only content)
design/
  coinbase.design.json verbatim copy of the public design JSON (token source)
scripts/
  validate.py          strict report validator
  build_site.py        standalone renderer + validator (stdlib only)
tests/                 unit tests (synthetic fixtures only)
  test_design_system.py  token/font/theme/contrast regression (no browser)
  browser_check.py     optional Playwright suites (seed, live, design, fonts)
  design_checks.py     Playwright design checks used by browser_check.py
  seed_reports/        frozen snapshot of the original dated reports; exact-count
                       tests build from this so they stay green as data grows
dist/                  build output (gitignored)
SCHEMA.md              report contract documentation
design/coinbase.design.json  fetched design-system source
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
python tests/browser_check.py              # screenshots -> .work/screenshots/

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
recommendations). Each card shows the recorded gates, gate notes, salary and
equity evidence, plus native `<details>` panels for company/role evidence
notes and per-source evidence notes with direct links. Qualified entries get
an Apply link; the others get a **View listing** link that is explicitly not a
recommendation.

Evidence note wording: source `quote` fields are recorded research notes that
may paraphrase the source. The site labels them "Evidence note" and never
presents them as verbatim employer quotations.

Noscript keeps the full pre-rendered content readable in the default dark
theme (filters and the theme toggle are hidden, and every entry is shown with
its date badge).

## Design system

Styling follows `design/coinbase.design.json` (the Coinbase design from
VoltAgent/awesome-design-md, as published in the sleek-ui design schema). It
is plain CSS: no Tailwind, no framework, no npm packages.

- **Colour tokens** are copied exactly as HSL triples: `tokens.colors.light`
  on `:root`, `tokens.colors.dark` on `.dark`, used as `hsl(var(--token))`.
- **Radius, typography, spacing and shadows** become custom properties:
  `--radius: 0.375rem`, `--radius-sm/-lg/-full`, `--text-*`, `--font-*`
  weights, `--leading-*`, `--tracking-*`, `--space-*`, `--shadow-sm`,
  `--shadow`, `--shadow-lg`.
- **Fonts:** `--font-sans` is Inter, `--font-serif` is Georgia and `--font-mono`
  is JetBrains Mono, each with system fallbacks. Only Inter is loaded, from the
  exact Google Fonts URL in `fonts.urls`. The JSON supplies no JetBrains Mono
  URL, so mono text uses JetBrains Mono only if it is installed locally;
  otherwise it falls back to the system monospace font.
- **Components:** `.btn` is the primary button, `.btn-quiet` the secondary
  button and `.btn-ghost` the ghost button (used by the theme toggle).
  `.hero-report` and `.job-card` use the card component. The search box and
  selects use the input component.
- **Theme:** dark is the default (`agentInstructions.defaultMode`). Pages ship
  `<html class="dark">`. A small inline script in `<head>` runs before the
  stylesheets and removes the class only when the stored choice is `light`,
  so there is no flash of the wrong theme. The header toggle is a real
  `<button aria-pressed>`, so it works with Tab, Enter and Space. It saves
  `light`/`dark` under the local-storage key `job-alerts-theme`. Storage
  errors are caught; if storage is blocked, the toggle still works for the
  current page. Without JavaScript the dark default is used and the toggle
  is hidden.
- **Focus:** `:focus-visible` uses `outline: 2px solid currentColor` with a
  2px offset (`accessibility.focusRing`). Primary buttons have white text, so
  their currentColor ring would be invisible on a white page. They also get a
  6px `--ring`-coloured halo behind the unchanged ring (ring vs halo 5.8:1,
  halo vs white 5.8:1).
- **Motion:** the JSON defines no motion tokens, so the site has no
  animations or transitions. A `prefers-reduced-motion` guard keeps it that
  way.

### Contrast adjustments

The raw tokens stay canonical. Where a raw token fails WCAG AA for a pair
the site actually renders, a documented alias is used instead. Each alias is
defined in `assets/style.css`:

| Alias | Light | Dark | Why |
| --- | --- | --- | --- |
| `--primary-text` | `= --primary` | `221 100% 72%` | raw blue on dark surfaces is 2.7–3.4:1 |
| `--destructive-text` | `0 72% 42%` | `0 90% 72%` | raw destructive as text: 3.4–3.8:1 light, 1.6–2.0:1 dark |
| `--destructive-solid` | `0 72% 42%` | `= --destructive` | white on raw light destructive is 3.8:1 |
| `--muted-text` | `240 3.8% 42%` | `= --muted-foreground` | raw light muted-foreground on `--muted` is 4.39:1 |
| `--control-border` | `240 3.8% 46.1%` | `240 15% 55%` | raw `--input`/`--border` are 1.1–1.4:1, too faint for control boundaries |
| `--primary-hover` | `221 100% 44%` | `221 100% 44%` | primary button hover fill (white text 7.0:1) |

These checks are evidence, not a guarantee of full WCAG conformance:

- `tests/test_design_system.py` computes every colour pair the stylesheet
  uses from the CSS values: at least 4.5:1 for text and 3:1 for UI
  boundaries and focus indicators.
- `tests/browser_check.py` measures computed styles in Chromium. It covers
  both themes at 375, 768 and 1280px on the home, archive and a daily report
  page, including hover states, expanded evidence panels, zero states and
  noscript. In the last run the lowest measured text contrast was 5.1:1
  (light) and 5.8:1 (dark).
- The browser checks do not cover other browsers, native `<select>`
  dropdown popups, forced-colours or high-contrast modes, or screen-reader
  behaviour.

## Privacy and network

- No analytics, and the site itself sets no cookies.
- The theme choice is kept only in this browser's local storage and is never
  transmitted.
- The Inter stylesheet and font files are requested from Google Fonts
  (`fonts.googleapis.com`, `fonts.gstatic.com`). Those requests reveal the
  visitor's IP address and browser details to Google. If they are blocked,
  system fonts are used and the site stays fully readable.
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
