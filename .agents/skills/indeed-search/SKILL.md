---
name: indeed-search
version: 1.0.0
description: >
  Use this skill to search live job listings on Indeed in any country and
  location (including remote), or to look up a specific Indeed posting. Backed by
  Indeed's first-party MCP connector rather than a scraper, so results are
  authorized, structured, and carry compensation on most listings. Trigger
  phrases: find a job, job search, search for jobs, job openings, vacancies,
  hiring, positions open, remote jobs, salary for this role, "are there any X
  jobs in <place>", look up this Indeed posting.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
interface: mcp  # NOT a bun CLI - see "Interface" below. /scrape Step 1b branches on this key.
allowed-tools: mcp__claude_ai_Indeed__search_jobs, mcp__claude_ai_Indeed__get_job_details, mcp__claude_ai_Indeed__get_company_data
---

# Indeed Search Skill

Search live job listings from **Indeed** through its first-party MCP connector.

## Interface: MCP, not a CLI

**This portal has no `bun run` command.** Every other portal skill in this repo wraps a
TypeScript CLI; this one calls MCP tools the host already provides. `/scrape` Step 1b reads
the `interface: mcp` frontmatter key and dispatches accordingly. If that key is missing or
set to `cli`, the portal is treated as CLI-backed (the default, for backward compatibility
with every skill written before this one).

Consequences worth knowing:

- **`bun` is irrelevant here.** A failed `bun --version` check (Step 1a) does not disable this
  portal, and must not push it to the WebSearch fallback.
- **Tools may be deferred.** Load them with `ToolSearch` before the first call, batching all
  three into one query. If the connector is not available to the session at all, record the
  portal as **unavailable** for the Step 4.75 health line and continue - it is not "broken",
  it is not connected.
- **`source` in `seen_jobs.json` is `mcp`**, not `cli` or `websearch`. This is a third value,
  added with this skill. Never backfill it onto entries that predate it.

### A failed attach is sticky for the whole session (measured 2026-09-13)

**MCP servers attach at session start.** If the connector is rate-limited at that moment, the
failure is recorded once and the tools stay unavailable for the *entire* session - even after
the endpoint recovers. `ToolSearch` will keep returning the same cached error string verbatim
(including the original "Try again in N seconds", with N frozen at its startup value), which is
the tell that you are reading a stored failure rather than probing a live one.

**How to tell the difference:**

| Check | What it means |
|---|---|
| `ToolSearch` returns the identical error, same N | Cached session-startup failure. Probing again tells you nothing. |
| `claude mcp list` shows a *changing* N | Live probe. The endpoint is really still limited, and N is counting down. |

**Do not poll `claude mcp list` in a loop to wait for recovery.** Each invocation probes every
configured server and spends an Indeed call, so a poll consumes each refill the instant it
lands - the same starvation pattern documented above, and the same one that starved a scoring
agent for 35 minutes. Measured on 2026-09-13: a 30-second poll drove the cooldown 49s -> 19s
-> 8s and was extending the window rather than clearing it.

**The actual recovery, in order:**

1. **Stop probing.** Let the bucket refill with no traffic for a few minutes.
2. **Re-attach the connector to the session** - this is a user action, not something the
   assistant can do: run `/mcp` in the session and reconnect the Indeed server.
3. **If that does not offer a reconnect, restart the session** (`claude --continue` preserves
   the conversation). A fresh start re-attaches every configured server.
4. **Then probe once** - a single `claude mcp list`, never a loop.

**Root cause to check before blaming the connector:** the quota is **account-wide**, shared by
every Claude client signed into that account - other Claude Code sessions on the machine, the
desktop app, and claude.ai web tabs with the Indeed connector enabled all draw from the same
bucket. A session that looks idle still attached at startup. Closing the others is often the
whole fix.

## ⚠️ Authorization note (this is the point of this skill)

Unlike the HTML-scraping portals, this is an **authorized first-party integration**. There is
no Terms-of-Service risk, no rate-limit gymnastics, and no parser to rot when Indeed changes
its markup. When Indeed and a scraped portal both cover a market, prefer this one.

## When to use this skill

- Search job openings in any location or remotely
- Filter by employment type (full-time, part-time, contract, internship, temporary)
- Get the full description, salary, and apply link for a specific posting
- Look up company data for an employer seen in results

## Commands

### Search job listings

```
mcp__claude_ai_Indeed__search_jobs(
  search="<title or keywords>",
  location="<City, ST>" | "remote",
  country_code="<ISO 3166 alpha-2>",
  job_type="fulltime" | "parttime" | "contract" | "internship" | "temporary"   # optional
)
```

- `search`, `location`, and `country_code` are **required**. `job_type` is optional.
- `location` takes free text ("Portland, OR"), or the literal `"remote"`.
- Returns markdown, not JSON: one block per hit with **Job Title**, **Job Id**, **Company**,
  **Location**, **Posted on** (e.g. `August 03, 2026`), **Job Type**, **Compensation**, and a
  **View Job URL**. Parse these fields out rather than expecting a `results` array.

### Fetch full job detail

```
mcp__claude_ai_Indeed__get_job_details(job_id="<Job Id from search>")
```

Pass the `Job Id` string exactly as search returned it (e.g. `JOBSEARCH_31`). Returns the full
description, salary, and application link.

### Company lookup

```
mcp__claude_ai_Indeed__get_company_data(...)
```

Useful for the Company Research Checklist in `04-job-evaluation.md`. Still subject to the
independent-verification rule: a connector result is a lead, not a verified claim.

## Known limitations (read before relying on this portal alone)

1. **No recency filter.** There is no `--jobage` equivalent. Searches return postings of any
   age, and in practice a sizable share are months old. **You must filter client-side** on the
   `Posted on` date, per `/scrape` Step 1b item 3. This is the single most important thing to
   get right about this portal.
2. **No result-count cap.** There is no `--limit`. Take the first ~20 blocks and stop.
3. **No pagination.** One call returns one page.
4. **Markdown output, not JSON.** Parse the labeled fields.
5. **Recall is moderate.** In side-by-side testing against `linkedin-search` and
   `freehire-search` on the same query, Indeed surfaced roles the others missed *and* missed
   roles they found. Treat it as additive coverage, never a replacement.
6. **Account-level rate limiting, and it is tight.** The connector shares an account-wide MCP
   quota. Observed in practice: roughly **one call per minute at best**, and **retrying while
   limited appears to extend the window rather than reset it** (observed sequence: 45s → 27s →
   14s → 8s → 58s after an immediate retry). Consequences for `/scrape`:

   - **Do not plan a 9-query sweep.** A three-priority run will not complete. Budget **2-4
     Indeed calls per run** and pick the highest-yield queries.
   - **Never retry on a rate-limit error.** Do other work (CLI portals, detail fetches,
     state writes) and come back once. Repeated immediate retries make it worse.
   - **A rate limit is never a health verdict.** Per Step 4.75, record **inconclusive
     (rate-limited)**, back off, and never report the portal as broken or degraded.
   - Because coverage will be partial, always say in the Step 5 summary which Indeed queries
     actually ran, so a thin Indeed showing is not mistaken for a thin Indeed market.

## What this portal is uniquely good for

**Compensation.** Most Indeed results carry a salary range, where LinkedIn and freehire usually
return `null`. For a candidate with a stated target and an instruction not to name a number
first, that is the most decision-relevant field in a search result. Prefer Indeed's number when
portals disagree, and note when only Indeed has one.

## Usage examples

```
# AI operations roles in Portland, full-time
mcp__claude_ai_Indeed__search_jobs(search="AI operations", location="Portland, OR",
                                   country_code="US", job_type="fulltime")

# Remote internal-tooling roles
mcp__claude_ai_Indeed__search_jobs(search="internal tools automation", location="remote",
                                   country_code="US")

# Full detail for one posting
mcp__claude_ai_Indeed__get_job_details(job_id="JOBSEARCH_31")
```

## Notes

- **Keep apply URLs intact.** The connector's own rules require preserving `View Job URL`
  parameters. Do not strip them when storing to `seen_jobs.json`.
- Results are sourced from Indeed's job search API, not scraped pages, so the Step 4.75
  degraded-parser checks (null company, entity garbage in titles) will essentially never fire
  here. A zero-result run points at the query or the connector, not at a broken parser.
