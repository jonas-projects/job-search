---
name: super_long_scrape
description: >
  The widest net available, driven by a named SEARCH PROFILE: roughly 5x the query
  surface of /long_scrape, run in serialized phases with hard screening at every stage
  so the extra volume does not drown the signal. Profiles live in profiles/ (built per
  candidate with /super_long_scrape_profile) and decide what is hunted, where, what is
  screened for free and how survivors are scored. Built for a multi-hour sweep that
  checkpoints and resumes. Ends by drafting every 60+ survivor through the /apply pipeline,
  refreshing the candidate's applications board artifact, and asking whether to queue the
  Indeed ones for /apply-automate.
  Triggers on: super long scrape, really wide net, cast a really wide net,
  exhaustive search, scrape <profile>, /super_long_scrape
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(bun --version), Bash(bun run .agents/skills/*/cli/src/cli.ts *), Bash(python .agents/skills/indeed-personal/cli/indeed_personal.py *), Bash(python3 .agents/skills/indeed-personal/cli/indeed_personal.py *), Bash(python .agents/skills/ziprecruiter-personal/cli/zip_personal.py *), Bash(python3 .agents/skills/ziprecruiter-personal/cli/zip_personal.py *), Bash(python tools/job_key.py:*), Bash(python3 tools/job_key.py:*), Bash(python tools/apply_board.py:*), Bash(python3 tools/apply_board.py:*), Bash(python tools/posting_live.py:*), Bash(python3 tools/posting_live.py:*), Bash(python tools/indeed_apply.py:*), Bash(python3 tools/indeed_apply.py:*), Bash(python tools/zip_apply_queue.py:*), Bash(python3 tools/zip_apply_queue.py:*), Bash(curl:*), WebFetch, WebSearch, Agent, AskUserQuestion, Artifact, ArtifactData, Skill
---

# Super Long Scrape — The Widest Net, Screened Hard

## What this is, and what it is not

**This file is the machinery. A profile is the search.** Phase discipline, the free title/company screen, the ATS JSON sweep, the body pass, provenance verification and bulk storage are the same whatever is being hunted, and they live here. What is being hunted — the query blocks, the portal routing, the screen deltas, the scoring rubric and the presentation — lives in a profile under `profiles/`, and `profiles/README.md` is its schema.

**Pick the profile before anything else. See Step 0.**

**The storage schema, the retry rules and the honesty rules are inherited verbatim from `/long_scrape`. Read `.claude/skills/long-scrape/SKILL.md` first and follow it.** Relative to `/long_scrape`, this file changes three things: **how much** is searched, **in what order**, and **what has to be verified before a posting is allowed to reach the user.**

> **Note on climb scoring.** `/long_scrape`'s thesis and climb scoring are not the universal case; they are one rubric (`scoring: climb`) that a profile may declare. There is no default profile (see Step 0). Where this file says "the climb", read "whatever rubric the active profile declares".

**This is not "run `/long_scrape` five times."** Measured 2026-09-14: two `/long_scrape` runs the same day, ~14 queries each, produced 4 presentable candidates each, and the second run's results overlapped the first heavily on shared titles. Repeating titles buys overlap. **Volume has to come from distinct query surface, not from deeper pagination or repeated terms.**

---

## Step 0 — Pick the profile (do this first, every run)

The argument to the skill is a profile name: `/super-long-scrape <profile>` (for example, a hypothetical `/super-long-scrape ops-coordinator`).

1. **Read `profiles/README.md`** once — it is the schema and the index. Profiles are built per candidate; a previous candidate's profiles are archived in git, not kept here.
2. **Resolve the argument:**
   - **The registry is empty** (no profile files, or an index with no rows) → **stop.** Say no search profile exists yet for the current candidate and offer `/super-long-scrape-profile` to build one. Do not fall back to `/long_scrape`, `/scrape`, or an improvised query list.
   - A name that matches a file in `profiles/` → load it.
   - **No argument** → list the enabled profiles with their one-line summaries and ask which to run. Do not silently default; the profiles hunt genuinely different jobs and guessing wastes hours.
   - A name with no file → say so, list what exists, and offer `/super-long-scrape-profile` to build it. Never improvise a profile inline.
   - `enabled: false` → say the profile is parked and ask before running it.
3. **State the plan back in two lines before the first query:** the profile, its scoring rubric, its geography, its pay rule, and how many query blocks at which priorities you intend to run.
4. **Everything downstream reads the profile, not this file,** for: query blocks (Phase 1), screen deltas (Phase 2), portal routing (Phases 1 and 3), scoring rubric and bands (Phase 6), presentation columns (Phase 6), the `track` tag written to `seen_jobs.json` **in every phase**, and the work-mode and seniority rules (Phase 4).

5. **Sync the board first.** If the message carries lines copied from the bottom of the applications board (`Applied 2026-10-14: Company - Role`, `Won't apply 2026-10-14: Company - Role`), save them to a scratch file and run `python tools/apply_board.py sync <file>` before anything else, and report any `NO MATCH` line back. Postings the candidate marked then never get re-drafted.

**This skill is the front door (set 2026-10-14).** The user runs `/super-long-scrape` in series and expects one run to go all the way from search to drafted applications, an updated board, and an auto-apply offer. Phases 7-9 below are part of every run, not optional extras.

**More than one profile in a run is allowed but serialize them and report them separately.** Their screens and rubrics differ, so a merged table is not comparable and a merged funnel count is meaningless.

### Set expectations before starting, out loud

Tell the user, in one short paragraph, before the first search:

- **Wall clock.** `indeed-personal` takes roughly 40 seconds per search and per detail fetch, and it is serialized (see Phase discipline). A full run is **two to four hours**. Ask whether to run the whole thing or stop after Phase 3.
- **Yield does not scale with effort.** The pool of postings a profile hunts inside its recency window is finite. Expect roughly **2 to 3x** the candidates of a `/long_scrape`, not 5x. Saying so up front is part of the job.
- **An honest "nothing new cleared the bar" is still a valid outcome**, and a five-hour run that says so is more useful than one that pads a table.

---

## Phase discipline — the thing that makes volume survivable

**`indeed-personal` drives a single persistent Chrome profile and holds a lock on it.** Measured 2026-09-14: launching a detail batch while a search sweep was still running produced six consecutive `launch_persistent_context: ... in use` failures, all of which exited 0 and wrote empty files, so the failure was silent until the output was inspected. **Never run two `indeed-personal` processes at once.** Phases 1 and 4 both use it and must not overlap.

**Wait for the explicit sentinel, never for the last `done` line.** This rule was violated on the very first run of this skill, by its own author: `tail -1 p1.log` showed `done payrollcoord`, which is query 27 of 28, and Phase 4 was launched into a still-running Phase 1. All four detail fetches collided and wrote empty files. **Every phase script must end by echoing a sentinel (`P1 DONE`), and the next Indeed phase must not start until `grep -q "P1 DONE"` succeeds** — or until the background task's own completion notification arrives. A per-query `done` line says a query finished, not that the phase did.

**Verify output is non-empty before parsing it.** A zero-byte result file is the signature of this collision. Check file sizes after any `indeed-personal` batch; silent empty output is the failure mode, not an error exit.

Phases 2, 3 and 5 use different tools (HTTP, WebSearch, Agent) and may overlap with each other, but not with an active Indeed phase.

**Checkpoint after every phase.** Write results to the scratchpad and bulk-store screened entries to `seen_jobs.json` (Phase 2) before moving on. A run that dies in Phase 4 should not lose Phases 1-3.

---

## Phase 1 — Cast (serialized, query volume set by the profile)

**The query blocks come from the active profile, not from this file.** Run its blocks in
priority order, every term that has not been run in the last 7 days. Check `seen_jobs.json`
for recent entries from the same portal before spending a query. Budget roughly 40 seconds
per `indeed-personal` search, so 40 queries is about 27 minutes of pure search; the MCP
`indeed-search` connector is faster but shares one account-wide rate limit.

**Portal routing also comes from the profile.** The defaults it overrides, measured
2026-09-13 and still current:

| Need | Portal | Invocation |
|---|---|---|
| Remote (US), deep | `indeed-personal` | `-l "Remote" --remote --jobage <n> -n 20 --format json` — captcha-prone, needs the user at the keyboard |
| Remote (US), quick | `indeed-search` (MCP) | No captcha; apply the recency window client-side, it has no date argument |
| The candidate's metro | `linkedin-search` | `-l "<City>, <State>, United States" -q "<term>" --jobage <n> -n 20 --format json` (measured on `-l "Portland, Oregon, United States"`; the metro comes from the candidate's profile) |
| Remote on LinkedIn | keyword only | `--remote` is inert on the guest endpoint and `-l "Remote"` searches globally. Put the word in `-q`, then verify each body. |
| Tech-first remote | `freehire-search` | `--region us --remote remote --no-description` — low yield outside engineering |
| Remote + metro, second deep source (added 2026-10-14) | `ziprecruiter-personal` | `search -q <term> -l "Remote" --remote --jobage <n>` and `-l "<the candidate's metro city, ST>"`; cards carry apply type (`one_click`/`external`), posted date and pay. Uses its own signed-in Chrome profile, so it runs serialized with `indeed-personal` (one virtual screen) but never in parallel with it. Heavy overlap with Indeed: dedup by company + title too. |

**Location parameters and remote flags decide where to look, never what survives.** A result's work mode is read from its body in Phase 4, whatever query produced it.

⚠ **LinkedIn page size is fixed at 10.** `--limit`/`-n` caps client-side only and does not
deepen a search. A query returning exactly 10 has more behind it: paginate with `--page 2`,
`--page 3`. Measured 2026-09-17, 59% of the results that mattered on a deep sweep sat
beyond page 1, and a run that skipped pagination missed all of them.

**Small-company and vertical modifiers belong on the Phase 3 ATS `site:` queries, not on
Indeed**, whose keyword field handles them badly. The profile lists which ones it uses.

---

## Phase 2 — Free screening and bulk store (no requests)

**Screen on title and company alone, before any body fetch.** This is where most of a 40-query cast must die, and it costs nothing. Measured 2026-09-18 on an entry-level analyst profile (since archived): 35 of 58 new postings died here, 60% of the batch, for zero requests.

**The screens are universal-plus-delta.** The universal list below always runs. Then apply the active profile's "Screens — deltas" section, which may **add** screens and may **remove** them. A removal is deliberate and must be honored. *Examples:* a profile hunting back-office seats inside staffing agencies removes the staffing-company screen, because an agency hiring the candidate to work there is its entire target; an hourly tutoring profile removes the part-time, contract and pay-floor screens, because hourly work is the market it searches. Applying the universal list unmodified to profiles like those returns nothing.

Apply every `/long_scrape` screen, plus this **company blocklist measured on 2026-09-13/14** — these are firms whose postings are placements or talent pools, not jobs at the employer:

> Vaco · IDR · OpTech · Cybercoders · Robert Half · Insight Global · Experis · Manpower · Beacon Hill · EPM Scientific · Axial Search · Compass Career Solutions · Robbins Staffing · Maarut · CloudSrv · Infodell · Buchanan Technologies · Jobgether · Distro · Scale Army · The Global Talent Co · Assist World · Morningstar Staffing

**Two structural categories that consumed most of every run today and can be killed for free:**

1. **Big-4 / platform consulting.** EY, Deloitte, PwC, Accenture, Grant Thornton, NTT DATA, and any title containing SAP, Workday, Oracle, D365, Dynamics, ServiceNow, NetSuite, Epic, JDE, Deltek, Anaplan, QAD, Kinaxis, Ellucian, UKG, Five9, Genesys, WalkMe, Smartsheet, defi SOLUTIONS.
2. **Defence and federal contractors.** Halvik, CACI, Amentum, Koniag, Crystal Management, Thunderyard, ICF, H2 Performance. Clearance-gated work and rigid contract bands.

**Public-sector employers themselves (county, city, state, school district, public college) are NOT a universal screen** (changed 2026-09-23). They are in scope by default: a county analyst seat, school districts and community colleges for tutoring work, and county staffing offices have all been real hits. A profile that wants them out says so in its own **Adds** (as a `climb`-scored profile usually should, because its thesis needs carve-out room that civil-service bands don't give).

**Never screen on work mode or seniority requirements here** (set 2026-09-23). A displayed city, a portal's remote/onsite facet, a location parameter and an experience-level label are all unreliable, so those statuses are parsed later, from the body, with ambiguous cases left in. They are decided in Phase 4 from the posting text. The only seniority kill allowed in this phase is an explicit title word (the list below, plus any the profile adds), because a title is the posting's own statement and not a filter.

Also free: clinical and licensed roles (LCSW, LPN, RN, NP, therapist, medical coder, surgical/patient scheduler), BPO and call-center floors, and anything whose title carries `VP`, `Head of`, `Director`, `Principal`, `Staff`, `Senior Manager`, `SVP`, `Chief` (except Chief of Staff).

**Reject placeholder job ids.** Measured 2026-09-14: `890abcdef0123456` appeared three times as a fake duplicate of real postings. `indeed_personal.py`'s `_is_placeholder_jk` misses it because it tests `set(jk) == set("0123456789abcdef")` and that string contains no `7`. **Use a subset test instead:** a 16-character id whose characters are all hex and which contains 14 or more distinct hex characters is a placeholder. Drop it silently.

**Then bulk-store every screened posting** to `seen_jobs.json` with `"status": "skipped"`, `"track": "<the profile's track_tag>"`, and a one-line `screen_reason`. **Always use the active profile's tag, in this phase and in Phase 6.** A tutoring run that files its rejects under `long` corrupts both ledgers. Derive keys with `python tools/job_key.py`, never by hand. This is what stops a 40-query run from re-presenting the same 400 dead postings next week.

---

## Phase 3 — ATS board sweep and JSON verification (~20 queries)

**This is the highest-value phase and the one `/long_scrape` under-runs.** Small companies post on Greenhouse, Ashby and Lever, and those boards expose **JSON APIs that give country, salary, liveness and open-role count for free** — four of the facts that otherwise cost a body fetch each.

Run at least **20** `site:` searches, built from **the active profile's** query blocks and its ATS-only or vertical modifier list. The examples below are illustrative (an operations-shaped search) and show the shape, not the content:

```
site:job-boards.greenhouse.io "operations associate" remote
site:jobs.ashbyhq.com "operations generalist" OR "founder's associate" remote
site:jobs.lever.co "business operations" remote startup
site:jobs.ashbyhq.com "automation" operations remote
site:job-boards.greenhouse.io "first operations hire" OR "wear many hats"
site:job-boards.greenhouse.io "scheduling coordinator" OR "staffing coordinator" remote
site:jobs.ashbyhq.com "AI operations" OR "AI implementation" remote
site:wellfound.com operations remote startup
```

**Then hit the board API for every company that surfaces, before fetching any posting body:**

| Host | Endpoint |
|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/<org>/jobs` (add `/<id>` for one posting with `content`) |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/<org>?includeCompensation=true` |
| Lever | `https://api.lever.co/v0/postings/<org>?mode=json` |

**Read these four things off the JSON and act on them immediately.** Measured 2026-09-14, this single step killed eight candidates for the cost of eight HTTP requests:

1. **Liveness.** A role in the search index but absent from the live board is **expired** — F6, Tandem Care, NEAR Foundation, Open Core Ventures, Brisk Teaching and Process Street all failed here.
2. **Country and residency.** Ashby's `location`/`secondaryLocations`, Greenhouse's `Country of Hire` metadata. This is what caught `#paid` as a Canadian CAD 50-55K req that LinkedIn had labelled "United States", and Uproot Clean's "Operations Specialist" as Argentina.
3. **Compensation.** Ashby's `compensationTierSummary` caught Marco's "Remote" Operations Coordinator at **$1.1-1.5K per month**.
4. **Open-role count and posting age**, the two best free size and staleness proxies. A board with 200+ roles is enterprise; `publishedAt` two years old beside a role literally titled "Anything" is an evergreen talent pool, not a req (Icon, 2024-08-29).

**Encoding note:** always open these JSON files with `encoding='utf-8'`; the default Windows codepage throws on them.

---

## Phase 4 — Body pass (Indeed, serialized again)

Only now fetch bodies, and only for survivors. **Do not start this while Phase 1 is still running.**

### Work mode and seniority are decided here, and only here (set 2026-09-23)

**Work mode.** The candidate's work-mode rules come from `CLAUDE.md` and the candidate profile (their metro, any in-person areas, any residency limits); the profile's `onsite:` field says which of them apply to this hunt:

- `onsite: out` — remote, or hybrid at a site inside the candidate's metro. Onsite-only drops.
- `onsite: in-metro` — in-person work anywhere in the candidate's metro also passes.
- `onsite: in-<area>` (kebab-case, an area the candidate's profile defines, e.g. a county) — fully in-person work at a site inside that area also passes; onsite elsewhere in the metro still drops.

Any in-person value needs the candidate's explicit say-so. Read the posting text and classify each survivor:

| Posting text says | Mode | Action |
|---|---|---|
| A role-level remote provision ("this role is fully remote", "Remote (US)") | `remote` | keep |
| Set in-office days at a site inside the candidate's metro | `hybrid` | keep, name the city and days |
| Hybrid or onsite at a site outside the metro | — | drop, `screen_reason: "hybrid outside metro"` |
| Clearly onsite-only ("fully in-office", "5 days on site", a shift at a physical site with no remote element) | `onsite` | drop unless the profile allows onsite there (`in-metro`: anywhere in the metro; `in-<area>`: that area only, mode `onsite, <city>`), `screen_reason: "onsite only"` |
| Nothing role-level, only company boilerplate ("we're remote-first"), or header and body contradict each other | `mode unclear` | **keep**, quote the strings, the candidate decides |

If the candidate's own rules are broader (for example, they accept onsite work anywhere), the profile records that and this table follows it; never apply a stricter rule than the candidate set.

**Seniority**, only where the profile declares a `level:` rule (today only `entry` exists). Read the requirements block for the **minimum** years, not the preferred:

| Posting says | Level | Action |
|---|---|---|
| Minimum 0-2 years, or a range starting at 2 or below ("1-3 years", "2-4 years"), or "entry level", "new grad", "Analyst I", "Associate", "Junior" | `entry` | keep |
| A hard minimum of 3+ years | — | drop, `screen_reason: "min N years, not entry level"` |
| No years stated, "Analyst II", or wording that points both ways | `level unclear` | **keep**, quote the line, the candidate decides |

**When in doubt, keep.** Both rules exist to spare the candidate onsite and senior postings, not to decide close calls for them. A posting that ran out of body-fetch budget stays in as `mode unclear` / `level unclear`; it is never promoted to a pass and never quietly dropped. Store `work_mode` and, where used, `level` on the `seen_jobs.json` entry.


Read for the `/long_scrape` Step 4 climb signals, and additionally **read the qualifications before scoring anything** — measured 2026-09-14, three separate candidates were killed by a requirements line that the opening description gave no hint of:

- **Makers Gonna Learn**: "You must have: 2-5+ years of experience in bookkeeping, A/R, A/P... 2+ years of hands-on QuickBooks experience."
- **The Moore Group**: WalkMe hands-on, PMP, and Agile Scrum, all in the Technical Skills block.
- **KinderCare**: "10+ years of experience" — a Gate 4 veto under a Manager title.

**Also read for the things that only appear at the bottom:** a daily start time in a distant time zone (The Moore Group required an 8 a.m. ET start; convert to the candidate's time zone), a base that converts to commission after a ramp (Olio Group), required weekend availability, and outbound call volume stated as a number (Olio: "60+ outbound calls per day").

---

## Phase 5 — Provenance check (mandatory, before anything is presented)

**Added 2026-09-14 after a full `/apply` cycle was spent on a posting that could not be verified as an employer posting.**

For every candidate about to be presented, spend **one** fetch on the employer's own careers page, located by searching the company name — never by following a link inside the posting.

Compare, and report any mismatch to the user rather than resolving it silently:

| Signal | What it means |
|---|---|
| Role present on the employer's own board | Clean. Prefer the employer URL over the aggregator one. |
| Role absent, but the employer posts remote roles and has a plausible board | Note it. Companies do post reqs that never reach their site. |
| Role absent **and** the employer lists the same function as an **onsite** role under a different title | **Stop and report.** This was pmweb: "Remote Revenue Operations Specialist" on Indeed under a lowercase `pmweb` account, against "REVOps Administrator — Wakefield MA or Miami FL" on the company's own careers page, with a salary band the company publishes nowhere. |

**Two cheap corroborators:** run an authenticated Indeed search for the employer name and look at how many postings exist and whether the employer name is spelled the way the real company spells it (pmweb's were lowercase; LeadrPro's was correct). And check whether a **second unrelated company shares the name** — there is a Brazilian CRM agency also called Pmweb, and a people-development firm called Leadr distinct from LeadrPro.

---

## Phase 6 — Score, store, present

**Score with the rubric the profile's `scoring:` field names, and no other.** The four that exist:

| `scoring` | Rubric | Bands |
|---|---|---|
| `standard` | `04-job-evaluation.md` — Technical 30 / Experience 25 / Behavioral 15 / Career 30 | Strong 75+, Good 60-74, Moderate 45-59, Weak 30-44 |
| `climb` | `/long_scrape` Step 4 — Leverage gap 0-40, Carve-out 0-30, Ceiling 0-20, Floor tolerability 0-10, phone load scored only in Floor | Strong climb 75+, worth a look 55-74, thin 35-54, not presented under 35 |
| `tutoring` | Hourly/per-session work; dimensions defined inside the profile that declares it (the archived version used Subject fit 30 / Rate 25 / Flexibility 25 / Autonomy 20) | Pursue 75+, worth a look 55-74, list only 35-54, not presented under 35 |
| `automatable` | `profiles/README.md`, "The `automatable` rubric" — Automatable share 0-35 / Async and output-based 0-30 / Coexistence 0-20 / Entry cost 0-15, with the three stated caps | Pursue 75+, worth a look 60-74, list only 45-59, not presented under 45 |

A profile may narrow how a rubric is read — which dimension a domain gap lands on, what counts as a hard requirement — in its "Scoring notes". It may not invent another rubric inline; that goes in `profiles/README.md` first, so every profile using it scores the same way. **Apply the profile's pay rule, not a default one**, and never one stricter than the candidate's own pay rule in `CLAUDE.md` (which may be "no pay screen at all"): an hourly profile has no annual floor, and flagging a $38/hr tutoring posting against an annual floor is a bug. Always report the posted figure.

Store with `"track": "<the profile's track_tag>"` plus the `/long_scrape` additive fields, so a tutoring hit and an analyst hit stay tellable apart in the ledger years later.

**Present with a cast summary that is honest about the funnel**, because on a run this size the funnel is the most informative output, and **use the profile's own presentation columns** — a climb run leads with the leverage gap, an analyst run with score and pay, a tutoring run with rate and schedule:

```
## Super Long Scrape — <profile> — YYYY-MM-DD

Cast: N queries across M portals. Screened X at title/company (free), Y on ATS JSON,
Z on the body pass. Provenance-checked W. Presenting V.

<the profile's table columns>
```

**Every table carries a `Mode` column** (`remote` / `hybrid, <city>` / `in person, <city>` / `mode unclear`), and a `Level` column when the profile has a `level:` rule. List the unclear rows in the same table as the clear ones, sorted by score, and say how many there are, so the candidate can make those calls in one pass.

Then the `/long_scrape` presentation rules: two to three bullets for each top-band hit leading on whatever the profile says leads, the weakest dimension named for each middle-band hit, one line each for the thin ones, and **one plain statement of what the whole list is worth.**

**Append one line to the profile's yield log** before you finish: date, queries cast, screened free, bodies fetched, presented, applied. It is the only mechanism that ever reveals a block has stopped producing.

**Report the funnel counts even when the yield is good.** A run that screened 400 postings to present 6 is telling the user something real about the market, and it is the only way to know whether the next run should change tactics or just wait for new postings.

Then go straight on to Phase 7. Do not stop and ask which rows to draft.

---

## Phase 7 — Draft every row scoring 60 or higher (set 2026-10-14)

**Every presented row scoring 60+ gets the full `/apply` treatment, without asking first.** That threshold is the standing approval to *draft* (drafting submits nothing); `/apply` Step 1's "Should I proceed?" question and its Step 7 "what's left" printout are skipped in this pipeline. `mode unclear` and `level unclear` rows at 60+ are drafted too; the board groups them so the candidate decides there.

**Skip, and say which and why in the report:**
- a Phase 5 provenance **stop**;
- a posting already in `job_search_tracker.csv` under the same company and role, at any status;
- a posting whose own text excludes residents of the candidate's state (`CLAUDE.md`).

**How (the background pipeline, `.claude/skills/apply-automate/briefs/`):**
1. Per posting, a **drafter** subagent (`general-purpose`), prompted with `briefs/drafter.md`, the stem `<company>_<role>` (`/outcome` Step 1.4's rule), the profile name, the posting URL (employer URL first), and the full posting text you hold. Then a **reviewer** subagent with `briefs/reviewer.md` on the same stem. A reviewer never reviews its own draft.
2. Run **up to 4 postings at a time**, drafter then reviewer per posting. The briefs forbid Indeed tools and tracker edits inside agents, so this can overlap with nothing Indeed-based.
3. **You, not the agents, write each tracker row** per `/apply` Step 6b: status `drafted`, `fit_rating` = the drafter's score, `source` = employer URL (else the aggregator), `deadline`, and `notes` beginning `Super-long-scrape <date> (<profile>); <mode>; pay <posted pay or "not posted">.` If the drafter returned **STOP**, start the note with `STOP: <reason>` so the board files it under Held.
4. **Indeed check, serialized, after all drafting:** for every Indeed posting, `python tools/indeed_apply.py <jk> --out <scratch>/<stem> --check-only`. Indeed Apply → an auto-apply candidate for Phase 9. "Apply on company site" → it stays on the board for the candidate, and the drafter's `form_answers.md` packet must exist; write it if missing. Never run this beside an `indeed-personal` phase or a running `apply_queue.py` (the `.apply_queue.lock` file).
5. **ZipRecruiter postings:** the search card's `apply_type` decides it. `one_click` → an auto-apply candidate for Phase 9 (ZipRecruiter queue). `external` → it stays on the board for the candidate with the `form_answers.md` packet. Put `ZipRecruiter jid=<jid>` in the tracker row's notes when the source is the employer's own link.
6. A drafter or reviewer that fails verification (page counts, compile) is re-run once; if it fails again, leave the row out of the tracker and name it in the report.

Budget: roughly 10-15 minutes of agent time per posting. Tell the user the count and the estimate when Phase 7 starts.

---

## Phase 8 — Refresh the applications board

There is **one** board artifact for the candidate, kept for good. Its URL is in `documents/applications/apply_board_url.txt`. Never publish a separate list page for a run.

1. `python tools/posting_live.py`: checks every drafted row and records closed postings in `documents/applications/liveness.json`. Indeed links take about 40 seconds each and need the Indeed Chrome profile free; with the queue lock present it skips them (re-run later).
2. `python tools/apply_board.py build --out <scratchpad>/apply_board.html`. Closed postings are left off and their tracker rows set to `withdrawn`. Page 1, **To send**: every drafted, unsent, still-open application, with Applied and Won't apply marks and a copy box whose lines get pasted back (Step 0.5). Page 2, **Indeed auto-apply**: only what the queue is working through (queued and submitted, with employer-message state); parked items move to To send with the reason.
3. Publish to the saved URL: in a new conversation, `Artifact read` it once, then publish the file with `url` set to it (omit `capabilities` and `icon` so both carry over). In the same conversation, republish the same file path. **Always pass `files`** = the build's `apply_board.files.json` map (paths relative to the repo root), so every row's **Resume PDF** link works: applications are sent from the candidate's own computer, not the server (set 2026-10-14). Cover letters, form answers and written answers are already embedded in the page with copy buttons.
4. Give the link and the counts the build printed (to send, by group; auto-apply by status; closed postings removed).

---

## Phase 9 — Offer auto-apply (always ask, every run)

**Auto-submit is off unless the candidate has explicitly opted in** (`automation.auto_submit: true` in `.claude/skills/apply-automate/facts.json`; the current candidate has not). With it off, the Indeed queue fills each application and **stops at the review page for a person to click Submit**, and the ZipRecruiter 1-Click queue is **not run at all** (1-Click submits on click). Say which mode applies when you ask.

End the run with one question, using AskUserQuestion. List the postings drafted **this run** whose Phase 7 check said Indeed Apply or ZipRecruiter 1-Click (company, role, fit, portal), and ask whether to queue them for `/apply-automate`. Options: queue all of them; let me pick; not now. Ask even when the list is empty: say so and offer to run the queue's parked items instead.

ZipRecruiter items, **only when auto-submit is on**, go to `python tools/zip_apply_queue.py add <job url> <stem>` and `run` (unattended parks anything that opens an employer question form; `run --present` with the user at the browser window answers them). With auto-submit off, they stay on the board for the candidate to apply by hand. Never run it at the same time as an `indeed-personal` or `ziprecruiter-personal` phase (one virtual screen).

On yes: invoke the `apply-automate` skill and follow its Workflow (`apply_queue.py add <jk> <stem>` per approved posting, then the run). After the run, rebuild and republish the board (Phase 8 steps 2-3) so page 2 shows the result.

---

## Rules (additions to `/long_scrape`'s ten, which all still apply)

11. **Never run two `indeed-personal` processes concurrently.** The profile lock fails silently with exit 0 and empty output.
12. **Screen free before you screen expensive.** Title and company first, then ATS JSON, then bodies. Never fetch a body for a posting a blocklist would have killed.
13. **Read the qualifications before scoring.** The opening paragraph of a posting is marketing; the requirements block is the posting.
14. **Verify provenance before presenting, not before drafting.** A posting that cannot be confirmed as an employer posting gets reported with the discrepancy, never quietly shortlisted.
15. **Bulk-store what you screen.** A wide run that does not persist its rejections makes every future run re-screen the same corpus.
16. **Checkpoint between phases.** This run is long enough to be interrupted, and Phases 1-3 are expensive to redo.
17. **Report the funnel, not just the finds.** And if nothing clears the bar, say so in one sentence without padding the table.
18. **Never run without a profile, and never improvise one inline.** No argument means ask. A name with no file means offer `/super-long-scrape-profile`, not a guess. A profile invented in the moment is unmeasurable and unrepeatable, which defeats the yield log.
19. **Honor the profile's screen removals as strictly as its additions.** They are the reason the profile exists: applying the universal screens unmodified to a profile built around a removal returns nothing and looks like a dry market rather than a bug.
20. **A profile decides what to search for and how to score it — never what may be claimed in a document.** No profile can loosen an accuracy, honesty or scope rule from `CLAUDE.md` or the job-application-assistant files.
21. **Append to the yield log every run.** A profile with no yield log has never been measured and is a hypothesis, not a strategy.
22. **Work mode and seniority are parsed from the posting in Phase 4, never filtered at search time, and ambiguity stays in.** Only clear posting text drops a role. `mode unclear` and `level unclear` rows go to the candidate, not to the bin.
23. **A run ends with drafted applications, a refreshed board and the auto-apply question** (Phases 7-9). A run that stops at the results table is unfinished.
24. **One board, one URL.** The candidate's marks come back as pasted lines from its copy box; sync them before drafting so nothing already sent or declined is drafted again.
25. **Nothing is submitted without a yes in this run**, and with auto-submit off, nothing is submitted by the queue at all — a person clicks Submit on the review page. Phase 9's answer is the approval `/apply-automate` Rule 1 requires.
