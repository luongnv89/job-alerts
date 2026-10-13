# job-alerts

Daily evidence-backed AI engineering job alerts, published as a static site:
editorial white/black/green pages rendered from strict JSON research reports by
a dependency-free Python 3 builder.

Entries record evidence checks made on each report date. They are not a live
availability guarantee, and `rejected`/`near-miss` entries are research leads —
never recommendations. Candidate personal details never appear in this
repository or on the site.

## Layout

```
assets/                site resources copied into dist/assets/
  template.html        page shell (tokens filled by the builder)
  style.css            editorial white/black/green stylesheet
  app.js               filter logic (no cookies, no storage, no network)
  report.schema.json   machine-readable report contract
data/reports/          YYYY-MM-DD.json research reports (site's only content)
scripts/
  validate.py          strict report validator
  build_site.py        standalone renderer + validator (stdlib only)
tests/                 unit tests (synthetic fixtures only)
  seed_reports/        frozen snapshot of the original dated reports; exact-count
                       tests build from this so they stay green as data grows
dist/                  build output (gitignored)
SCHEMA.md              report contract documentation
BUILD_BRIEF.md         approved build brief
```

## Commands

```bash
# validate report data (exit 1 on any violation)
python scripts/validate.py                 # or: --reports DIR

# validate only, write nothing
python scripts/build_site.py --validate-only

# unit tests
python -m unittest discover -s tests -v

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
  directory containing (or inside) `.git`, the input `data/`, `assets/`,
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

Noscript keeps the full pre-rendered content readable (filters are hidden and
every entry is shown with its date badge). No analytics, no cookies, no CDN,
no external runtime dependency.

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
