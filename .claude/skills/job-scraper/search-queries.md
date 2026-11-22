# Search Queries for Job Scraper

<!-- SETUP: Customize these queries based on your skills, target roles, and location -->

## Installed portal CLIs (primary for `/scrape`)

`/scrape` discovers every portal skill under `.agents/skills/*/SKILL.md` and runs its CLI first. Shipped country-agnostic CLIs include `linkedin-search` and `freehire-search`; any skill you add with `/add-portal` is included the same way. You do **not** need a matching `site:` line below for those CLIs to run.

**Active portals for this profile:** <!-- Set for the candidate via /setup: which portal skills are `enabled: true` for the candidate's market -->

**`indeed-search` is MCP-backed, not a CLI** (`interface: mcp`). Two things follow, and both matter on every run:

1. **It has no recency filter.** Indeed returns postings of any age, and in practice many are months old. The 14-day window must be applied **client-side** on each result's `Posted on` date. This is the easiest way to pollute a run.
2. **It is the only portal that reliably reports salary.** LinkedIn and freehire usually return `null` for compensation; Indeed carries a range on most listings. Prefer Indeed's figure when portals disagree, and say when Indeed is the only source for one.

Treat it as **additive coverage, not a replacement** — in side-by-side testing it found roles the other portals missed and missed roles they found.

The `site:` query templates in this file are the **WebSearch fallback** — for portals without a CLI, company career pages, or when a CLI fails.

**Language scope:** write every query category in every language listed in your CLAUDE.md Languages table (typically 1-2, sometimes more). A posting requiring a language you have *not* declared, as a job condition, is excluded before scoring; a posting requiring a *higher level* than you declared in a language you *do* work in is flagged for your own judgment, not excluded — see `04-job-evaluation.md`'s Language Gate, the single source of truth for this rule. Translate each category's keywords rather than machine-translating word-for-word (e.g. "Frontend Developer" -> "Desarrollador Frontend", not a literal word-for-word translation) if you work in more than one language.

## Search Sites

Primary (your market's job boards - scaffold one with `/add-portal`):
- **[YOUR_JOB_BOARD]** - your market's largest general job board
- **linkedin.com/jobs** - LinkedIn job listings (filter: [YOUR_COUNTRY] / [YOUR_CITY]); also covered by `linkedin-search` CLI
- **[YOUR_INDUSTRY_JOB_BOARD]** - a niche/industry board for your field (optional)
- **[YOUR_ADDITIONAL_JOB_BOARD]** - another major board for your market (optional)

Secondary (company career pages via Google):
- Direct Google searches with `site:` filters for known target companies

## Query Categories

Queries are grouped by priority. Write **each category in every language from your Languages table** (see Language scope above). Combine each query with your location terms (e.g. your city, region, or metro area) where the site supports it.

**Organize by function, not job title.** The same underlying work carries different titles across companies and markets (a "Data Scientist" role at one employer may be posted as "Insights Analyst" or "Data Consultant" at another). Name each priority category after the function it covers, and list several plausible job titles as query variants within that category rather than betting an entire priority tier on one exact title string.

**Never decide the work mode from the search itself** (a location parameter, a remote facet or a displayed city). Read it from the posting body at the verification stage, and keep ambiguous postings marked `mode unclear` for the user (see `04-job-evaluation.md`, Location & Logistics).

### Which portal to use for which geography (measured 2026-09-13 — read before writing queries)

| Need | Use | Why |
|---|---|---|
| **Remote** | **Indeed** (`location="remote"`) | It returns `Location: Remote` as a real field. The only portal here whose remote signal can be trusted from search output. |
| **A metro area** | **LinkedIn** (`--location`) | `--location` *is* honored and resolves metros correctly. |
| Remote on LinkedIn | keyword only, then verify | See below. |

**`linkedin-search --remote remote` filters nothing.** LinkedIn's guest endpoint ignores the `f_WT` parameter: `--remote remote` and `--remote onsite` return byte-identical result sets, both matching the unfiltered query. **Do not put `--remote` in a LinkedIn query and do not treat its output as remote-filtered.** A measured run that did so reported ~90 postings as remote; a verification sweep of 47 of them found **zero** country-wide listings. `-l "Remote"` is not a workaround either — it searches globally.

To reach remote roles on LinkedIn, put the word in the **keywords** (`-q "operations remote"`), which the endpoint does honor, then verify each posting body per the Remote verification section below. Expect a low hit rate — this is supplementary coverage, with Indeed carrying the remote search.

### Priority 1: [YOUR_PRIMARY_ROLE_TYPE]

These match your strongest and most desired career direction.

```
site:[YOUR_JOB_BOARD] "[YOUR_PRIMARY_JOB_TITLE_1]" [YOUR_CITY]
site:[YOUR_JOB_BOARD] "[YOUR_PRIMARY_JOB_TITLE_2]" [YOUR_CITY]
site:[YOUR_JOB_BOARD] "[YOUR_KEY_SKILL]" [YOUR_CITY]
site:linkedin.com/jobs "[YOUR_PRIMARY_JOB_TITLE_1]" [YOUR_COUNTRY]
```

### Priority 2: [YOUR_DOMAIN_EXPERTISE]

These match your domain expertise.

```
site:[YOUR_JOB_BOARD] [YOUR_DOMAIN_KEYWORD_1] [YOUR_CITY] OR [YOUR_REGION]
site:[YOUR_JOB_BOARD] [YOUR_DOMAIN_KEYWORD_2] [YOUR_COUNTRY]
site:linkedin.com/jobs [YOUR_DOMAIN_KEYWORD_1] [YOUR_CITY] [YOUR_COUNTRY]
```

### Priority 3: [YOUR_ADJACENT_ROLE_TYPE]

Adjacent roles you could pivot into.

```
site:[YOUR_JOB_BOARD] "[YOUR_ADJACENT_TITLE_1]" [YOUR_KEY_SKILL] [YOUR_CITY]
site:[YOUR_JOB_BOARD] "[YOUR_ADJACENT_TITLE_2]" [YOUR_KEY_SKILL] [YOUR_CITY]
```

### Priority 4: Broader Technical / Consulting

Wider net for general technical roles.

```
site:[YOUR_JOB_BOARD] [YOUR_KEY_SKILL] developer [YOUR_CITY]
site:linkedin.com/jobs "[YOUR_KEY_SKILL] developer" [YOUR_CITY]
site:[YOUR_JOB_BOARD] "technical consultant" [YOUR_DOMAIN] [YOUR_CITY]
```

## Location Filter

When evaluating results, verify the job location is within reasonable commute distance from your home. Define acceptable areas:
- [YOUR_CITY] and surrounding areas
- [ACCEPTABLE_AREA_1]
- [ACCEPTABLE_AREA_2]
- [BORDERLINE_AREA] (borderline - ~X min by transit)
- [TOO_FAR_AREA] (too far)

### Remote verification — a portal's remote filter is a hint, never a verdict

**Measured on 2026-09-13: `linkedin-search --remote remote` returned a majority of roles that are not remote.** Their bodies carried only company-level remote boilerplate ("we're a remote-first company", "we offer hybrid work") with no role-level remote provision. Treat every portal's remote facet this way — LinkedIn's `f_WT` workplace filter is the measured case, but none of them are authoritative.

**A result counts as remote only after one of these confirms it:**

| Signal | Verdict |
|---|---|
| Displayed location is `Remote`, a country name, `Anywhere in <country>`, or `<Metro> (Remote)` | **Remote — accept.** The portal resolved it to a country-wide listing rather than a city. |
| Displayed location is a specific city **and** the query used a remote filter | **UNVERIFIED — fetch the detail and read the body** before calling it remote. This is the case that produced every false positive. |
| Body states a role-level remote provision ("this role is fully remote", "Location: Remote", "Remote (US)") | **Remote — accept.** |
| Body states only a company-level posture ("we are a remote-first company", "we offer hybrid work location") with a city in the header | **Not remote.** Company culture is not role eligibility. |
| Body names a residency or time-zone restriction ("must reside in the Eastern or Central time zone", "remote within commuting distance of Austin") | **Read it against the candidate's state and time zone.** Their state in the list is a PASS worth noting; a restriction that excludes it is a **FAIL**. |
| Header and body contradict each other (e.g. "San Francisco; Remote (US)" beside "Location Type: On-site") | **FLAG, never a silent PASS.** Present it to the user with both strings quoted. |

**Budget the verification.** Detail fetches cost a request each, so pre-filter on title first and verify only the results worth presenting — not the whole result set. Where the budget runs out, mark the remainder `remote_unverified` rather than promoting them; an unverified entry presented as remote is the failure this section exists to prevent.

**Never write a remote claim into `seen_jobs.json` that came only from a query flag.** Store the displayed location verbatim, as the schema already requires, and record the verification outcome separately.

**Pay banding:** when a result publishes state-banded pay, report the figure for the candidate's state of residence, not the "all other states" one. See `04-job-evaluation.md`, Pay banding by state.

## Language Filter

Your working languages and levels are in CLAUDE.md's Languages table. When filtering scraped results, apply `04-job-evaluation.md`'s Language Gate: a posting requiring a language you haven't declared at all is excluded; a posting requiring a higher level than you declared in a language you do work in is not excluded, flag it clearly instead (see `job-scraper/SKILL.md`'s Step 3 "Quick Fit Assessment" for how the flag surfaces in `/scrape` output). Postings simply *written* in a language you don't work in, that don't require it on the job, are fine.

## Date Filter

Only include jobs posted within the last 14 days, or with an application deadline that has not yet passed. If a posting date cannot be determined, include it but flag as "date unknown".

## Salary Filter

<!-- Set for the candidate via /setup: pay target or floor, if any. This filter flags, it does not drop. -->

Postings with no stated range are fine — note that the number will need handling in conversation and that the candidate should not go first. See `04-job-evaluation.md`, Compensation Guidance.

## Adapting Queries

If the user specifies a focus area, select queries from the matching category and also generate 2-3 custom queries for that focus. For example:
- "/scrape [focus_area]" -> relevant category queries + custom focus-specific queries
