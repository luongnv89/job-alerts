# Report schema (version 1)

Every file in `data/reports/` is one daily research report. Reports are the only
source of site content: the static builder copies their claims into HTML, and
`scripts/validate.py` plus `scripts/build_site.py` reject anything that does
not match this document. The machine-readable form of this contract is
`assets/report.schema.json`.

- File name: `data/reports/YYYY-MM-DD.json` — must equal the report's `date`.
- Encoding: UTF-8 JSON, no comments, no trailing commas.
- Field names are camelCase (the contract used by the 2026-10-10, 2026-10-11,
  2026-10-12 and 2026-10-13 reports). Unknown keys are rejected at every level
  (top level, job, salary, gates, evidence), and snake_case spellings such as
  `listing_url` or `checked_date` are unknown keys.
- Private/internal fields are rejected: candidate names, CV/work-authorization
  notes, user statistics, local file paths, contact details (email addresses)
  and non-public URLs must never appear anywhere in a report.

## Top level

| Key | Type | Rules |
| --- | --- | --- |
| `schemaVersion` | number | Must be `1`. |
| `date` | string | ISO `YYYY-MM-DD`, parsed with `datetime.date.fromisoformat`. |
| `summary` | string | Non-empty human-readable summary of the day's result. |
| `coverage` | string | Non-empty description of how far the search reached. |
| `criteria` | array of string | Hard gates applied; at least one, each non-empty. |
| `uncertainty` | array of string | Caveats; at least one, each non-empty. |
| `jobs` | array of job | May be empty; entries describe checks on historical dates, not live availability. |
| `requestedCount` | integer | Optional. How many roles the day's search targeted. |
| `decision` | string | Optional. Report-level decision note. |

## Job object

| Key | Type | Rules |
| --- | --- | --- |
| `id` | string | Slug: `^[a-z0-9]+(-[a-z0-9]+)*$`, unique within the report. Used for anchors, never for file paths. |
| `company` | string | Non-empty. |
| `title` | string | Non-empty. |
| `status` | string | One of `qualified`, `rejected`, `near-miss`. |
| `listingUrl` | string | HTTPS, public host (see URL rules). |
| `applyUrl` | string \| null | HTTPS, public host, or `null`. |
| `checked` | string | ISO `YYYY-MM-DD` — date the listing evidence was checked. |
| `posted` | string \| null | ISO `YYYY-MM-DD`, or `null` when the employer does not publish one. |
| `location` | string | Work mode: one of `remote`, `hybrid`, `on-site`, `unknown`. |
| `locationQuote` | string | Non-empty recorded location/attendance wording from the listing. |
| `salary` | object | See below; unknown pay stays `null`, never `0`. |
| `reasons` | array of string | Gate notes: why the entry passed or failed. Each item non-empty; the array may be empty. |
| `gates` | object | Stored results of the historic hard gates; see below. |
| `product` | string \| null | Recorded product/role context note, or `null` when none was recorded. |
| `financials` | string \| null | Recorded company research note, or `null` when none was recorded. |
| `evidence` | array of evidence | Source-backed evidence notes; see below. |

### `salary` object

| Key | Type | Rules |
| --- | --- | --- |
| `lower` | number \| null | Published numeric floor as published. `null` when unknown. |
| `upper` | number \| null | Published numeric ceiling. `null` when unknown. `lower` must not exceed `upper`. |
| `currency` | string \| null | One of `EUR`, `USD`, `GBP`, or `null`. |
| `annualBase` | boolean \| null | `true` only when the figures are confirmed annual base pay. `null` = unconfirmed. |
| `equity` | boolean \| null | `true` only for an explicit equity/stock-option offer; `null` means unknown, not `false`. |
| `quote` | string | Non-empty recorded research note of the employer's published pay wording (or an explicit statement that none exists). It may paraphrase the employer's wording; it is not guaranteed verbatim. |

No currency conversion is performed anywhere in the schema or the site. A
non-EUR figure stays in its own currency; any converted figure a report mentions
must be explained inside `reasons`/`quote` with its own source.

### `gates` object

| Key | Type | Rules |
| --- | --- | --- |
| `open` | string | One of `pass`, `fail`, `unknown` — listing/application reachable on the report date. |
| `location` | string | One of `pass`, `fail`, `unknown` — location/attendance gate result. |
| `ai` | string | One of `pass`, `fail`, `unknown` — AI-central seniority gate result. |
| `pay` | string | One of `pass`, `fail`, `unknown` — pay gate result as recorded on the report date. |
| `product` | string | One of `pass`, `fail`, `unknown` — separate product/company diligence result. |

These values record how the criteria of *that report date* were judged. They
are historical research results, not a recomputation against any current rule.

### `evidence` object

| Key | Type | Rules |
| --- | --- | --- |
| `url` | string | `http` or `https` on a public host (see URL rules). |
| `label` | string | Non-empty source title. |
| `quote` | string | Non-empty recorded research note taken from that source. Evidence notes are research notes and may paraphrase the source; they are not necessarily verbatim employer quotations, and the site labels them as notes. |
| `checked` | string | ISO `YYYY-MM-DD` — date this source was checked. |

## URL rules

- `listingUrl` and `applyUrl` must be `https`.
- Evidence URLs may be `http` or `https`.
- Rejected: other schemes (`file:`, `javascript:`, `data:`, ...), empty hosts,
  credentials in the URL, `localhost` / `*.local` / `*.internal` hostnames, and
  private or link-local IP literals (loopback, `10.x`, `172.16-31.x`,
  `192.168.x`, `169.254.x`, `fc00::/7`, `::1`).

## Qualification gates

An entry may use `status: "qualified"` only when **all** of the following hold;
otherwise validation fails:

1. Every stored gate in `gates` is `"pass"` — the historic gate results are the
   eligibility record.
2. `evidence` contains at least one entry (source completeness).
3. `product` and `financials` are non-empty strings (company/role diligence was
   recorded).

Deliberately **not** enforced at publish time:

- numeric pay thresholds are not re-applied — a report's stored `pass` on `pay`
  stands for the criteria that applied on its own date, so an entry with all
  gates passing validates regardless of today's numbers;
- `currency` is not re-judged — a historically qualified USD salary (for
  example DualEntry's, with its documented sourced EUR conversion in `reasons`)
  is not disqualified for being denominated in USD.

`rejected` and `near-miss` entries must record at least one gate that is not
`pass`; their `reasons` carry the failures and they are published as research
leads, never as recommendations.

## Privacy rules

Reports are public. They must never contain:

- candidate names, CV content, or work-authorization/personal status;
- user or applicant statistics;
- local filesystem paths or `file://` URLs;
- email addresses or other contact details;
- non-public network locations (private IPs, internal hostnames).

`scripts/validate.py` and `scripts/build_site.py` enforce the mechanically
checkable parts of this list and fail with a descriptive message on any
violation.

## Rendering contract

- HTML output escapes every report string; the embedded JSON payload has `&`,
  `<` and `>` replaced with JSON unicode escapes so a `</script>` sequence can
  never break out of the script tag.
- Cards are keyed as `<date>/<job id>`; anchor ids are `job-<date>-<id>`;
  output paths are derived only from validated ISO dates and slugs (path
  traversal protection).
- Counts shown on pages (qualified, examined, filtered totals) are calculated
  from `jobs`, never asserted beyond the data.
- Evidence `quote` fields are rendered as "Evidence note" research notes, never
  as decorated verbatim quotations.
- A build validates every report before writing anything, and stages output in
  a temporary directory that replaces `dist/` only on success — an invalid
  report can never replace good output.
