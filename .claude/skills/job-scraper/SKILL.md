---
name: scrape
description: >
  Finds new job postings matching your profile via installed portal-search CLIs
  (LinkedIn, local job boards, and any skills added with /add-portal). Deduplicates
  across runs. Triggers on: job scrape, find jobs, search jobs, new jobs, job search,
  scrape jobs, /scrape
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(bun --version), Bash(bun run .agents/skills/*/cli/src/cli.ts *), Bash(python tools/job_key.py:*), Bash(python3 tools/job_key.py:*), WebFetch, WebSearch, Agent, AskUserQuestion
---

# Job Scraper

---

## How It Works

This skill searches job portals using the **installed portal-search CLIs** in
`.agents/skills/` (plus WebSearch as a fallback), using queries from your profile.
It deduplicates against previously seen jobs and the application tracker, and
presents new matches with a quick fit assessment.

## Invocation

The user triggers this skill by saying things like:
- "Find new jobs"
- "Scrape for jobs"
- "Any new positions?"
- "/scrape"

Optional arguments:
- A focus area, e.g. "/scrape data science" or "/scrape geophysics"
- "broad" to run all search categories, e.g. "/scrape broad"
- "health" to run the portal health check only (Step 4.75), without searching, deduplicating, or presenting jobs - e.g. "/scrape health", or "/scrape health linkedin" to probe one portal even if disabled

---

## Execution Steps

### Step 0: Load State

1. Read `job_scraper/seen_jobs.json` (create if missing - start with `{"seen": {}}`)
2. Read `job_search_tracker.csv` to extract already-applied companies+roles
3. Read `search-queries.md` (this directory) for the search strategy

### Step 1: Search

Read `search-queries.md` (this directory) for the search strategy. By default, run the top 3 priority query categories. If the user said "broad", run all categories. If the user specified a focus area (e.g. "data science"), prioritize queries from that category.

**Use the installed CLI tools as the primary search mechanism.** Fall back to `WebSearch` only for portals that do not have a CLI skill, or if `bun` is unavailable on the system.

#### 1a. Check bun availability

```bash
bun --version
```

If this fails (bun not installed), skip to **1c (WebSearch fallback)** for all **bun-backed** portals and note the fallback in the Step 5 output. **A `cli` portal is not automatically a bun portal** - read its `SKILL.md` for the actual runner before routing it anywhere. `indeed-personal` is a `python` CLI with no bun dependency, and a failed `bun --version` must not disable it or push it to the fallback. **MCP-backed portals are unaffected by a missing `bun`** — see 1b-mcp below — so never route those to the fallback on a bun failure.

#### 1b. Run portal tools (primary — run these in parallel where possible)

Discover all installed portal skills by reading every `SKILL.md` found under `.agents/skills/*/SKILL.md`. Each file documents that portal's exact interface and usage examples. **Use each portal's own documented interface — do not guess flags or tool names.** This approach automatically includes any new portals added via `/add-portal` without requiring changes to this file.

**Honor the `enabled` toggle.** A portal is enabled unless its `SKILL.md` frontmatter sets `enabled: false` (a missing key means enabled — the default). Skip each disabled portal and record it for the Step 5 summary. A fork can thus keep a portal installed but sit out a run without deleting its directory.

**Dispatch on the `interface` key.** A portal's `SKILL.md` frontmatter may declare `interface:`:

| Value | Meaning | Runner |
|---|---|---|
| `cli` **or key absent** | Wraps a TypeScript CLI. The default, and what every portal written before this key existed is. | 1b-cli below |
| `mcp` | Backed by MCP tools the host provides. No `bun` command exists. | 1b-mcp below |

A missing key means `cli`, so existing portal skills keep working untouched.

##### 1b-cli. CLI-backed portals

For each **enabled** CLI-backed portal skill:

1. Read its `SKILL.md` to find the correct invocation and supported flags. Usually `bun run …`; `indeed-personal` uses `python …`. Never guess the runner.
2. Translate the query terms from `search-queries.md` into that portal's flag format (e.g. `--key`, `--search-string`, `--query`, filter codes — whatever the portal's SKILL.md specifies).
3. Scope to the last 14 days using the portal's supported recency **filter** flag (`--jobage`, `--since <YYYY-MM-DD>`, etc. — as documented per portal). A portal with **no recency flag** still gets scoped: every portal's search output carries a `date` field, so filter client-side — drop results whose `date` is older than 14 days after the call returns, and never invent a flag the portal's SKILL.md does not document (the CLIs reject unknown flags). `--order PublicationDate` is a sort, and a sort is not a filter — pairing it with a `--limit` is a defensible approximation on a portal that offers nothing better, but apply the client-side date filter on top all the same.
4. Cap results to ~20 per call using the portal's limit flag.
5. Use `--format json` for machine-readable output.

Run all portal CLI calls in parallel where possible using the Agent tool. Collect all `results` arrays into a single pool for Step 2, keeping each result tagged with its source portal skill (for Step 2 `detail` lookups).

If a CLI tool exits with a non-zero code, log the error message and continue — do not abort the whole search.

##### 1b-mcp. MCP-backed portals

For each **enabled** portal whose frontmatter sets `interface: mcp`:

1. Read its `SKILL.md` for the exact tool names and argument shapes. The `allowed-tools` frontmatter lists them.
2. **Load the tools first.** They are usually deferred: batch every tool the portal documents into a single `ToolSearch` call (`select:toolA,toolB,toolC`) rather than one call each.
3. **If the tools cannot be loaded, the portal is `unavailable`, not `broken`.** Record it for the Step 4.75 health line, cover the market via 1c if a `site:` query exists for it, and continue. An unconnected connector is a configuration fact, not a parser failure, and must never be reported as breakage.
4. Translate the query terms from `search-queries.md` into that portal's documented argument names.
5. **Apply the 14-day window client-side unless the portal documents a recency argument.** MCP portals frequently have none (Indeed does not), so parse each result's posting date and drop anything older. This is the most common way an MCP portal silently pollutes a run with months-old listings — Indeed's own SKILL.md calls it out as the single most important thing to get right.
6. **Cap results by taking the first ~20 and stopping** when the portal documents no limit argument.
7. **Do not assume JSON.** An MCP portal may return markdown or prose; parse the labeled fields its SKILL.md describes.

Tag every result with its source portal skill, exactly as for CLI portals, and merge into the same Step 2 pool. Step 4 records these entries with `"source": "mcp"`.

**Never route an MCP portal to the 1c WebSearch fallback because `bun` is missing** — it does not use bun. Fall back only if its own tools are unavailable or its calls fail.

**Serialize MCP portal work across parallel agents — the quota is shared, per account, not per agent.** Measured 2026-09-13: four scoring agents running concurrently each needed the Indeed connector, and one of them made ~25 calls over ~35 minutes without a single success, every one returning `Rate limit exceeded ... on toolset claude`. Each retry after the stated cooldown reported a *fresh* 45-55s cooldown, because the sibling agents consumed the quota the instant it refilled. The starved agent retrieved nothing at all.

Two rules follow:

1. **Give MCP-portal fetches to one agent at a time**, or hand every Indeed-dependent posting to a single agent rather than spreading them across a fan-out. Parallelism that shares one rate-limited connector is slower than serial, not faster.
2. **A rate-limit exhaustion is never `expired`.** A posting whose body was never retrieved is unretrieved, and marking it `expired` silently retires a live job — Rule 1's "never rank unfetched postings" cuts both ways. Report it as `unretrieved` with the reason and re-run it later; the agent that hit this correctly refused to score from titles and refused the `expired` label, which is the behavior to copy.

**`to.indeed.com` short links have no WebFetch fallback.** They 302 to a long `indeed.com/uie/clk?e=...` tracking URL that WebFetch will not follow, and `/viewjob` 403s. Two paths that have worked: the connector's own `get_job_details`, and resolving the short link to its `jk=` id then fetching `https://www.indeed.com/job/x-<jk>` (that path serves embedded JSON where `/viewjob` does not).

#### 1c. WebSearch fallback

Use `WebSearch` for:
- Portals listed in `search-queries.md` that do **not** have a corresponding directory under `.agents/skills/`
- Any portal whose CLI fails at runtime
- When bun is unavailable (Step 1a failed)

Use the site-specific query strings from `search-queries.md` directly as WebSearch queries for these portals.

Tag each fallback result as WebSearch-sourced, keeping the portal tag when the fallback stands in for an installed portal whose CLI failed. Step 4 persists this as the entry's `source`, and Step 5 reports which portals ran on the fallback this run.

### Step 2: Fetch & Parse

For each promising result from Step 1:

**From CLI results:** Search output already includes title, company, location, date,
and URL. For jobs worth a deeper look, fetch full detail with that portal's `detail`
command (see its SKILL.md — do not guess flags) to extract **key requirements**,
**application deadline**, and a brief description snippet.

**From MCP results:** Same pre-filter-then-fetch discipline, using the portal's documented
detail tool and its own identifier (Indeed's `Job Id`, e.g. `JOBSEARCH_31`, not a URL). Pass
the identifier exactly as search returned it. **Preserve the apply URL's query parameters**
when storing the entry — Indeed's connector requires intact links, and a stripped one can stop
resolving.

**Closed-at-source detection:** `linkedin-search detail` also returns `isActive`.
`false` means the posting page itself renders LinkedIn's "No longer accepting
applications" banner — the job died between being indexed and being fetched (expired
LinkedIn URLs redirect to *similar live jobs*, so a search hit can be a ghost). Mark
such a job, never silently drop it: write its entry to `seen_jobs.json` in Step 4 with
`"status": "expired"` and leave it out of the Step 5 presentation — an absent entry
looks identical to a job never seen, and the recorded status is what makes a later
ghost report self-triaging. `isActive: true` is only the absence of that banner, not
proof the posting is open; deadlines and dead URLs are checked again when the posting is drafted (`tools/posting_live.py`).

**From WebSearch results:** Use `WebFetch` on the posting URL and extract the same
fields manually. If it returns HTTP 403, retry with browser headers via curl per
`.claude/skills/job-application-assistant/09-web-research.md` before giving up — most
bank and corporate sites reject WebFetch's user agent while serving browsers normally.

**Store a URL that actually resolves to the posting.** A listing-page URL with a
`#fragment` appended (`.../jobs/ciso/#ikerian`) is not a posting: it fetches fine and
returns unrelated job titles, which makes every later `/apply` run fail on
that entry. When WebSearch only yields a listing page, search the employer's own careers
site for the role and store that URL instead, or drop the candidate rather than saving a
fragment link.

For every candidate:
- Skip if the URL matches any existing `seen_jobs.json` entry, regardless of
  that entry's key. This preserves dedup continuity for postings stored under
  the pre-helper key rule while new entries use the canonical key from Step 4.
- Otherwise, skip if the company+title combo already exists in `seen_jobs.json`
- Skip if the company+role already appears in `job_search_tracker.csv`

### Step 2.5: Mass-Posting Detection (within this run)

A distribution pattern worth flagging to the user as a caution signal, not as an accusation against the employer - it describes how a listing is being distributed, not a verdict on whether the company is legitimate. It alone proves nothing is wrong (companies do legitimately hire the same role across several cities); flag it so the user can factor it in when deciding whether to invest time, don't downgrade fit or silently exclude the result because of it.

If two or more results in this run's pool (from the same company, or sharing the same req/job ID visible in the URL or title) have substantially the same description and differ only in city/location/title, don't present them as separate rows. Consolidate into a single row and note the spread, e.g. "posted identically across 6 cities (BR, MX, GT)".

### Step 2.75: Remote Verification (run before fit assessment)

A portal's remote filter is a hint, not a verdict. Measured on 2026-09-13, `linkedin-search --remote remote` returned a **majority** of roles that were not remote — onsite postings in San Francisco, Austin, Chicago, Las Vegas, New York, Milwaukee, Morrisville NC, Culver City and Redmond, whose bodies carried only company-level boilerplate ("we're a remote-first company") with no role-level remote provision. That run presented ~90 postings as remote on the strength of the flag alone.

For every result a remote-filtered query produced:

1. **Displayed location is country-wide** (`Remote`, `United States`, `Anywhere in the US`, `<Metro> (Remote)`) → accept as remote.
2. **Displayed location is a specific city** → **UNVERIFIED.** Do not call it remote on the flag's say-so. Pre-filter on title, then fetch the detail for the ones worth presenting and read the body.
3. **Body check:** a role-level provision ("Location: Remote", "this role is fully remote") confirms it. A company-level posture ("we are remote-first", "we offer hybrid work") beside a city header does **not** — company culture is not role eligibility.
4. **Residency and time-zone restrictions decide it against the candidate's own location**, which the location-filter section of `search-queries.md` defines. A restriction that excludes them is a FAIL even though the posting says "remote".
5. **Header and body contradicting each other** (e.g. "San Francisco; Remote (US)" beside "Location Type: On-site") → FLAG with both strings quoted, never a silent PASS.

Where the fetch budget runs out, mark the remainder `remote_unverified` and say so in Step 5 rather than promoting them. Persist the displayed location verbatim per Step 4's schema — **never write a remote claim that came only from a query flag.**

### Step 3: Quick Fit Assessment

For each new job, do a rapid fit check (NOT the full evaluation from `04-job-evaluation.md` - just a quick signal):

- **High match**: Role directly involves your core skills
- **Medium match**: Role is adjacent to your experience
- **Low match**: Role requires significant skills you lack

**Language override:** before assigning a match level, check the posting against `04-job-evaluation.md`'s Language Gate (a required language you haven't declared at all in your CLAUDE.md Languages table). A required language that's entirely undeclared overrides skill fit: mark it **Low** regardless of how well the skills align, and name it in the highlight bullets so it isn't buried under an otherwise-good-looking match. A **declared** language at a requirement that reads higher than your declared level is *not* an override — score fit normally, but add a red-flag bullet under that job's highlights (Step 5) quoting the posting's requirement next to your declared level, so the gap is visible without being auto-downgraded.

### Step 4: Deduplicate & Store

1. Derive each entry's key with the helper, never by slugifying in the moment:

```bash
python3 tools/job_key.py --company "<company>" --title "<title>" --url "<url>"
```

It prints one line: the canonical key for that posting. The key must be a pure function of the posting, because two runs that slugify differently store the same job twice and defeat the dedup this step exists to provide. The helper also length-caps long titles and disambiguates the cap with a hash of the full slug, so a truncated title is stable across runs and two different long titles never collide. `python3 tools/job_key.py --audit` reports entries in an existing state file that predate this rule; it only reports, and never rewrites keys, since a rewritten key breaks the tracker's own company+role matching.

2. Add ALL fetched jobs (new and skipped) to `seen_jobs.json` with structure:
```json
{
  "seen": {
    "<key from tools/job_key.py>": {
      "title": "...",
      "company": "...",
      "url": "...",
      "first_seen": "YYYY-MM-DD",
      "posted_date": "YYYY-MM-DD" | null,
      "deadline": "YYYY-MM-DD" | null,
      "fit": "high/medium/low",
      "status": "new/skipped/ranked/expired",
      "portal": "<source portal skill, e.g. linkedin-search>",
      "source": "cli/websearch"
    }
  }
}
```

The `portal` field records which CLI skill produced the job (results are already tagged per portal in Step 1b - persist that tag here). Entries written before this field existed lack it; the health check (Step 4.75) attributes those by matching the URL's domain against each portal's base URL, so do not backfill.

The `source` field records which mechanism produced the entry: `cli` for Step 1b-cli portal-CLI output, `mcp` for Step 1b-mcp MCP-connector output, `websearch` for the Step 1c fallback. (`mcp` was added alongside the `interface: mcp` dispatch; entries written before it exists carry `cli` or `websearch` only, and are never rewritten.) This is what keeps a ghost-job report diagnosable after the run's summary is gone: a stored entry whose URL later resolves to nothing (or to a different job) reads very differently depending on whether it came from live CLI output or from a search index that can be weeks stale - and a presented job with no entry here at all points at fabrication, which Rule 1 forbids. Entries written before this field existed lack it; never backfill it - the mechanism was not recorded.

Scored entries extend this schema additively: they also carry `rank_score` (0–100 overall score), `rank_verdict` (fit band, e.g. "strong fit"), `rank_date` (ISO date of ranking), the veto fields `location_verdict` and `language_gate` (both PASS/FAIL/FLAG) with `language_note` (the quoted requirement explaining a non-PASS), and `strengths`/`gaps` (1-3 verbatim bullets each, copied from the scoring agent's findings). Their `status` is `"ranked"`. Do not drop any of these fields when re-writing entries. Entries ranked before `strengths`/`gaps` existed simply lack them; readers tolerate their absence and never backfill by guessing. Entries ranked before the verdict rename may carry a legacy PASS/FAIL/FLAG string in `location` - read that as the verdict when `location_verdict` is absent; in fresh entries `location` is always a place, never a verdict.

`deadline` is a base field rather than a scoring extension: Step 2's detail fetch already extracts the application deadline, so it is written when the job is first seen and refreshed when a later read of the posting returns a different value. `null` means the posting states no deadline; a missing key means the entry predates this field - **never infer a deadline** from either, and never backfill by guessing.

`posted_date` is the posting's own publication date, taken from the `date` field Step 2's contract already guarantees on every portal CLI's search output. Step 1b uses that date to scope the run to the last 14 days and then drops it, so nothing downstream can distinguish a posting published yesterday from one published two years ago - `first_seen` is when this scraper first saw the entry, not when the employer posted it. Persisting it makes Step 1b's window auditable after the run and gives `/rank` a freshness signal to weigh, instead of rediscovering the date and recording it in prose that nothing reads. That gap landed for real: a freehire-search posting dated 2024-05-13 was scraped and ranked Strong Fit at position 1 of 133, its own scoring note observing the listing "may be long stale" with nothing able to act on it. `null` means the portal returned no date for that result (the CLIs emit `date: null` when a listing omits it); a missing key means the entry predates this field - **never infer a posting date** from either, and never backfill by guessing.

3. Only present jobs NOT already in the seen list (matched by URL or
   company+title) or tracker.

### Step 4.5: Generate Referral Contact Links (High & Medium Fit Only)

For every job from this run with `fit` of **high** or **medium** (skip low-fit jobs),
build two LinkedIn people-search URLs so the user can find a recruiter or team member to
reach out to for a referral or a warm intro. This is deliberately a link-generation step,
not an automated lookup: no scraping, no third-party API, zero runtime dependencies or
credentials required.

**A. Recruiters / Talent Acquisition (the referral path)**
```
https://www.linkedin.com/search/results/people/?keywords=<url-encoded "<Company Name> recruiter">&origin=GLOBAL_SEARCH_HEADER
```

**B. Role/team peers (informational-outreach / warm-intro path)**
```
https://www.linkedin.com/search/results/people/?keywords=<url-encoded "<Company Name> <role keyword>">&origin=GLOBAL_SEARCH_HEADER
```
Use a short keyword drawn from the posting's title for `<role keyword>` - e.g. a posting
titled "AI Program Manager" becomes `"<Company Name> AI Program Manager"`.

Both links are for the user to open and browse themselves - never fetch or scrape the
LinkedIn people-search result pages programmatically. Never fabricate contacts or claim a
specific person was found; these are search links, not results.

### Step 4.75: Portal Health Check

Scraper-based portal CLIs rot silently: when a portal changes its markup, the parser usually exits 0 with zero results or with null/garbled fields, and the Step 1c fallback never fires because it only triggers on hard failure. This step catches that from evidence the run already holds.

**Free pass (no extra requests).** For each enabled portal that ran in Step 1b:

- **Degraded scan:** inspect the results it returned this run. Flags: `company` null or empty on every result, empty titles, undecoded entities (`&amp;`) or HTML fragments in titles, URLs that do not point at the portal. Any of these means the parser is half-working and `/scrape` is silently collecting junk.
- **Yield history:** if the portal returned zero results across all of this run's queries, check whether `seen_jobs.json` holds prior entries from it (via the `portal` field, or by matching URL domains for entries predating the field). A portal that produced jobs on earlier runs and produces nothing now is suspect - the same queries worked before.

**Escalation (bounded, on suspicion only).** A suspect portal gets **one** sentinel probe: run its documented `search` with the example query from its own SKILL.md (that query provably worked when the skill was registered), the portal's limit flag capped at 3, `--format json`. If that returns nothing, retry **once** with a single common word. Only then is the verdict **broken**. A 429 or block page is **never** evidence of breakage - record the portal as **inconclusive (rate-limited)**, back off, and do not retry.

**MCP-backed portals are checked differently.** They have no markup to rot, so the degraded-parser criteria (null company, entity garbage, HTML in titles) essentially never fire and a zero-result run points at the query or the connector, not a parser. Apply only these:

- **Tools unavailable** (deferred load failed, or the connector is not attached to this session) → verdict **unavailable**, never *broken*. It is a configuration fact, and the fix is connecting the integration, not repairing a parser.
- **Tools available but every call errored** → **broken**, with the error text quoted.
- **Tools available, calls succeeded, zero results across all queries** → apply the same yield-history check as CLI portals before concluding anything; absent prior entries, report **inconclusive**.

Never run the sentinel-probe escalation against an MCP portal whose tools failed to load — there is nothing to probe.

**Verdicts.** Healthy portals get silence - no table, no line. Anything else surfaces in the Step 5 summary as a health line.

**Probe-only mode (`/scrape health`).** Skip Steps 1-4 and this step's free pass (there is no fresh run to scan); instead probe every installed portal directly - enabled ones by default, a disabled one only when named explicitly (e.g. `/scrape health linkedin`). Each portal gets the sentinel probe above, the degraded criteria applied to whatever it returns, and - since the user explicitly asked for diagnosis - one `detail` fetch on the first result of each healthy portal (description must be readable decoded text; a failure downgrades to degraded). Report all statuses in this mode, including healthy. Volume stays bounded: one search, at most one retry, at most one detail per portal.

### Step 5: Present Results

Present new jobs in a table sorted by fit (high first). When Step 1b skipped
portals (`enabled: false`), report them with the `skipped (disabled):` line below
so opting one out stays visible rather than silent; omit the line when nothing
was skipped. When any portal's results came from the Step 1c fallback this run
(bun unavailable, or its CLI failed at runtime), report it with the
`fallback (websearch):` line - fallback results come from a search index that
can be stale, so the reader should know which rows carry that caveat; omit the
line when every portal ran its CLI. When Step 4.75 found a portal degraded, broken, or inconclusive,
add one `health:` line per suspect portal (healthy portals get no line); after
the report, offer to set that portal's `enabled: false` so `/scrape` stops
running it (and covers it via the Step 1c fallback) until it is fixed - only
edit the toggle with the user's confirmation, and never edit anything else in
the skill.

```
## New Job Matches - YYYY-MM-DD

Found X new positions (Y high, Z medium, W low match).

skipped (disabled): <portal-name>, <portal-name>

fallback (websearch): <portal-name>, <portal-name>

health: <portal-name> - degraded (company null on all 12 results); parsing anchors in .agents/skills/<portal-name>/url-reference.md
health: <portal-name> - broken (0 results for the SKILL.md test query and a broader retry); parsing anchors in .agents/skills/<portal-name>/url-reference.md
health: <portal-name> - unavailable (MCP tools could not be loaded; connector not attached to this session). Not a parser fault - connect the integration to restore coverage.

| # | Fit | Title | Company | Location | Deadline | URL |
|---|-----|-------|---------|----------|----------|-----|
| 1 | High | ... | ... | ... | ... | [Link](...) |

If Step 2.5 flagged a mass-posting pattern, note it in the Title cell (e.g. "Frontend Developer (posted in 6 cities)") rather than burying it. Do the same for a declared-language-insufficient-level flag from the Language Gate (e.g. "Backend Engineer ⚠ fluent English required") - both are signals the user should see at a glance, not just in the detail highlights below.

### High-Match Highlights
For each high-match job, add 2-3 bullet points:
- Why it matches your profile
- Key requirements to check
- Any red flags (including mass-posting signals from Step 2.5)

### Contacts
For each high/medium-fit job from Step 4.5, add a short contacts block with the two
LinkedIn search links:
- Recruiters/TA search link, for the referral path
- Role/team-peer search link, for the warm-intro / informational-outreach path
```

After presenting, ask:
> "Want me to evaluate any of these in detail? Just give me the number(s)."

If the user picks a number, invoke the **job-application-assistant** skill workflow (fit evaluation first, then CV + cover letter if approved).

Entries in `seen_jobs.json` with status `ranked` or `expired` count as already seen for dedup purposes.

### Step 6: Update Tracker (Optional)

If the user decides to apply to any job, the tracker row is written by **job-application-assistant Step 3b**, which Step 5 already routes into - do not add a second row here. Only when the user says they applied to something outside that path, add a row using the header and the match-then-update rule in `/outcome` Step 1.

---

## Important Rules

1. **Never fabricate job postings.** Only present jobs from actual CLI search/detail output or WebSearch/WebFetch results.
2. **Respect deduplication.** Always check seen_jobs.json AND job_search_tracker.csv before presenting.
3. **Focus on configured geographic area.** Skip jobs that require relocation or are clearly outside commute range.
4. **Only open positions.** Skip postings with expired deadlines or those marked as closed.
5. **Be efficient with detail fetches.** Don't run `detail` or WebFetch on every search hit — pre-filter by title/snippet, then fetch only promising matches.
6. **Parallel searches.** Run portal CLI searches in parallel; use WebSearch only for gaps the CLIs don't cover.
7. **No automated people lookups.** Referral contacts (Step 4.5) are LinkedIn search links only - never fetch or scrape LinkedIn people-search result pages programmatically.
8. **Health checks are bounded and honest.** Step 4.75 spends at most one probe, one retry, and (in `health` mode) one detail fetch per portal - a diagnosis, not a crawl. A rate-limit is never evidence of breakage. Health verdicts come only from observed CLI output; a portal that could not be tested is reported as inconclusive, never guessed. The `enabled` toggle is the only thing the health check may edit, and only with confirmation.
9. **Flag distribution patterns, never accuse.** The mass-posting signal (Step 2.5) describes how a listing is being distributed, not a claim that the employer is a scam. Never name a company as fraudulent or untrustworthy - present the observation and let the user decide.
