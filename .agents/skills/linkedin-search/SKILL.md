---
name: linkedin-search
version: 1.0.0
description: >
  Use this skill whenever the user wants to search for jobs in any location or
  market, find job listings, or look up a specific job posting — in any country,
  city, or remotely. Invoke for open positions, vacancies, and hiring across any
  sector or role (software, data, design, marketing, finance, legal, operations,
  etc.). The location is always supplied explicitly by the user. Trigger phrases:
  find a job, job search, search for jobs, job openings, vacancies, hiring,
  positions open, remote jobs, "are there any X jobs in <place>", look up this
  job posting.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
allowed-tools: Bash(bun run .agents/skills/linkedin-search/cli/src/cli.ts *)
---

# LinkedIn Search Skill

Search live job listings from LinkedIn's public job board for **any country/region**
(and remote). No authentication, no API key, and **zero runtime dependencies** — it runs
with just `bun`. The location is always passed explicitly, so the same skill works for a
forker in any market out of the box.

> This is a country-agnostic worked example of the repo's job-portal-skill pattern.
> LinkedIn's `jobs-guest` endpoints are global and the HTML parsing is country-independent;
> only the `--location` you pass changes per market.

## ⚠️ Personal use only

This uses LinkedIn's public job pages; automated access is against LinkedIn's Terms of
Service, so **keep volume low and don't use it commercially or for bulk data collection.**
Run it on your own responsibility.

## When to use this skill

- Search for job openings in a given location (any country/city) or remotely
- Filter by recency (posted today / last 7 / 14 / 30 days) or workplace type (remote/hybrid/onsite)
- Get the full description of a specific job listing

## Commands

### Search job listings

```bash
bun run .agents/skills/linkedin-search/cli/src/cli.ts search --location "<place>" [flags]
```

Key flags:
- `--location <text>` / `-l <text>` — **required.** A LinkedIn place string, e.g. `"Mumbai, Maharashtra, India"`, `"Berlin, Germany"`, `"London, United Kingdom"`, or `"Remote"`.
- `--query <text>` / `-q <text>` — keyword search (title, skill, role). Recommended.
- `--jobage <days>` — posted within N days: `1`, `7`, `14`, `30`. Omit for all postings.
- `--jobage-minutes <n>` — posted within N minutes (sub-day precision, e.g. `30`). Conflicts with `--jobage` — pass only one.
- `--remote <mode>` — `remote`, `hybrid`, or `onsite` (workplace-type filter). **⚠ Measured inert on the guest endpoint (2026-09-13) — see below.**
- `--page <n>` — page number (1-indexed, 10 results per page).
- `--limit <n>` / `-n <n>` — cap total results emitted (client-side).
- `--format json|table|plain` — default `json`.

### Fetch full job detail

```bash
bun run .agents/skills/linkedin-search/cli/src/cli.ts detail <id|url> [--format json|plain]
```

`id` is the job ID from `search` results (e.g. `4426311357`). You may also pass a full
LinkedIn `jobs/view/...` URL or a `urn:li:jobPosting:...` URN. Returns the full description,
seniority, employment type, job function, and industries.

## Usage examples

```bash
# Data engineer roles in Bengaluru, last 30 days
bun run .agents/skills/linkedin-search/cli/src/cli.ts search -q "data engineer" -l "Bengaluru, Karnataka, India" --jobage 30 --format table

# Product manager roles in Berlin, remote
bun run .agents/skills/linkedin-search/cli/src/cli.ts search -q "product manager" -l "Berlin, Germany" --remote remote --format table

# Any role, fully remote
bun run .agents/skills/linkedin-search/cli/src/cli.ts search -q "paralegal" -l "Remote" --format table

# Engineer roles, remote, posted in the last 30 minutes
bun run .agents/skills/linkedin-search/cli/src/cli.ts search -q "engineer" -l "Remote" --jobage-minutes 30 --format table

# Full details for a specific job
bun run .agents/skills/linkedin-search/cli/src/cli.ts detail 4426311357 --format plain
```

## Output formats

| Format | Best for |
|--------|----------|
| `json` | Default — programmatic use, passing IDs to `detail` |
| `table` | Quick human-readable scanning |
| `plain` | Reading a single job's full detail (`detail` command) |

All errors are written to **stderr** as `{ "error": "...", "code": "..." }` and the process exits with code `1`.

## ⚠ `--remote` does not filter anything (measured 2026-09-13)

**LinkedIn's `jobs-guest` endpoint ignores the `f_WT` workplace-type parameter.** The CLI builds and sends it correctly (`commands/search.ts` sets `f_WT` from `workTypeFlag`), but the endpoint drops it. Verified directly:

```
search -q "business operations" -l "United States"                    -> 10 results
search -q "business operations" -l "United States" --remote remote    -> the SAME 10 job ids
search -q "business operations" -l "United States" --remote onsite    -> the SAME 10 job ids
```

`remote` and `onsite` — logical opposites — return byte-identical result sets, and both match the unfiltered query. The flag is accepted, sent, and silently discarded.

**Consequences, all of which have bitten a real run:**

1. **Never treat a result as remote because `--remote remote` was passed.** In a 2026-09-13 scrape this produced ~90 postings reported as remote; ranking found the majority were onsite in San Francisco, Austin, Chicago, Las Vegas, New York, Milwaukee, Morrisville and Redmond. A later sweep of 47 such results found **zero** that were country-wide.
2. **`-l "Remote"` is worse, not a workaround.** "Remote" does not resolve to a US geography, so the search goes global: a test returned postings in Pakistan, Colombia, India, Canada and the UK. Never use it for a US search.
3. **What actually works:** put the word in the *keywords* (`-q "AI operations remote"`), which the endpoint does honor, and then **confirm remote status from each posting's body** via `detail`. Keyword targeting helps but does not guarantee — the same test still returned city-labelled onsite roles alongside genuine ones.
4. **For reliable remote discovery, prefer a portal that labels it in the data.** Indeed's connector returns `Location: Remote` as a real field and is the better remote source; use LinkedIn for geographic searches where `--location` *is* honored.

`--remote` is left in the interface because the parameter is correct and LinkedIn may honor it again; treat any result it returns as unfiltered until that is re-verified.

## Notes

- Data is from LinkedIn's public `jobs-guest` endpoints — no credentials required.
- Page size is fixed at 10 results per page.
- LinkedIn may rate-limit; the CLI retries 429/5xx with exponential backoff. Keep volume low (see ToS note above).
- Job IDs are numeric (e.g. `4426311357`) — pass them as-is to `detail`.
