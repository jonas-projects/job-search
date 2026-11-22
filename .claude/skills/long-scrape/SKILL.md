---
name: long_scrape
description: >
  Wide-net search for roles deliberately BELOW the candidate's current level at
  SMALL companies, where the candidate's distinctive skills (from their profile) are
  scarce and the space to expand is uncontested. Scores climb potential, not
  present-day fit. Triggers on: long scrape, wide net, cast a wide net, below my pay
  grade, small company jobs, room to grow jobs, /long_scrape
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(bun --version), Bash(bun run .agents/skills/*/cli/src/cli.ts *), Bash(python tools/job_key.py:*), Bash(python3 tools/job_key.py:*), WebFetch, WebSearch, Agent, AskUserQuestion
---

# Long Scrape — Wide Net, Low Rung, Small Room

## The thesis

This is not `/scrape` with looser filters. It searches for a **different kind of job on purpose.**

The bet: a candidate whose skills are rare in a small, manual operation can take a seat somewhat below their current level and climb quickly, because small companies leave room to carve out space that large companies, where people are watched closely and roles are tightly leveled, do not.

**Run this skill only when the candidate wants that bet.** Check `CLAUDE.md` and `01-candidate-profile.md` for the candidate's own statement of it (and any career history that shows it working before, such as an earlier promotion out of a frontline seat). If the profile says nothing either way, ask the candidate once before casting. Quote the candidate's own words in the presentation when they exist; do not invent a rationale for them.

So this skill hunts for **the starting conditions under which that climb happens**, not for roles that match the candidate's current résumé:

| The bet | What to look for |
|---|---|
| The candidate will be the scarcest skill set in the room | A small operation running on manual process where the candidate's distinctive skills are absent |
| The space is uncontested | No incumbent owner of the function the candidate would grow into |
| Nobody is watching too closely | Flat structure, founder-adjacent, no rigid leveling |
| The ceiling is reachable | A company growing fast enough to need more than it hired for |

**A role the candidate is overqualified for is a feature here, not a flaw.** `/scrape` would score most of these low. That is exactly why this skill exists separately, and why it must never be collapsed back into `/scrape`'s scoring.

---

## Relationship to the other skills

| | `/scrape` | `/long_scrape` |
|---|---|---|
| Aperture | Titles at the candidate's level | Deliberately **below** it |
| Geography | The candidate's work-mode rules | **Remote first**; local or hybrid only where the candidate's work-mode rules allow it |
| Pay | The candidate's pay rule | Low pay is expected; the **ceiling** is what's scored |
| Company | Any size | **Small strongly preferred** |
| Scored on | Fit today | **Climb potential** |
| Seniority screen | Rejects too-senior AND too-junior | Rejects too-senior **only** |

Both write to the same `job_scraper/seen_jobs.json`. Entries from this skill carry `"track": "long"` so the two pipelines stay legible in one ledger.

**Re-examine `/scrape`'s rejects.** A posting this ledger holds with `status: "skipped"` was screened by `/scrape`'s rules, which are the wrong rules here. Long-scrape MAY reconsider any `skipped` entry; it must NOT re-present anything with status `new`, `ranked`, or `expired`, or anything already in `job_search_tracker.csv`.

---

## Execution

### Step 0: Load State

1. `job_scraper/seen_jobs.json` (create as `{"seen": {}}` if missing)
2. `job_search_tracker.csv` — applied companies and roles
3. `CLAUDE.md` — the candidate's work-mode, location, residency and pay rules, and deal-breakers
4. `.claude/skills/job-application-assistant/01-candidate-profile.md` — the skills that make the candidate scarce, and their career history
5. `.claude/skills/job-application-assistant/04-job-evaluation.md` — for the hard screens this skill still honors (below)

### Step 1: Build the query set

Wide net means **more queries at lower titles**, not deeper pagination on the same ones.

**Target titles — the rung below.** Derive them from the candidate profile, not from this file:

1. The candidate's most recent titles, one rung down (`Manager` → `Coordinator`/`Associate`/`Specialist`).
2. Frontline seats in the domains the candidate already knows, where they would arrive without a ramp.
3. Generalist small-company seats where the candidate's distinctive skills would be the scarce thing (`Operations Associate`, `Founder's Associate`, `Program Coordinator` and similar).

Combine each with a remote term. *Example only, for an operations-and-automation candidate:* `Operations Associate · Operations Coordinator · Business Operations Associate · Automation Specialist · Reporting Analyst · Founder's Associate · Chief of Staff (seed / Series A only)`. A different candidate gets a different list.

**Small-company modifiers.** Append to force the corpus toward the target, since almost no posting states headcount:

```
startup · "seed stage" · "Series A" · "small team" · "first hire" · "first operations hire"
"wear many hats" · "generalist" · "no two days are the same" · "founding team"
```

**Portal routing** (same measured constraints as `/scrape`, see `job-scraper/search-queries.md`):

| Portal | Use | Note |
|---|---|---|
| `indeed-search` (MCP) | Primary for remote | Only portal whose remote signal is trustworthy from search output. Serialize its calls — the quota is per account, not per agent. |
| `linkedin-search` (CLI) | Keyword-only remote | `--remote` is inert; put "remote" in `-q`. `-l "United States"`. |
| `freehire-search` (CLI) | `--region us --remote remote` | Tech-first corpus; use `--no-description` for discovery. |
| **ATS boards via WebSearch** | **Highest-value source for this skill** | See below. |

**ATS-board search is the distinguishing move here, and it is where small companies actually post.** Greenhouse, Ashby and Lever are startup-dominated, and unlike Indeed listing pages these return **real, storable posting URLs**. The shape (substitute the candidate's own titles):

```
site:job-boards.greenhouse.io "operations associate" remote
site:jobs.ashbyhq.com "operations generalist" OR "founder's associate" remote
site:jobs.lever.co "business operations" remote startup
site:job-boards.greenhouse.io "first operations hire" OR "wear many hats"
site:wellfound.com operations remote startup
```

Run at least four ATS-board queries every time. **Never store an Indeed or LinkedIn *listing-page* URL** (a search-results page, or anything with a `#fragment`) — it is not a posting, and it breaks every later `/apply` on that entry.

### Step 2: Two-pass fetch (keeps a wide net affordable)

1. **Discovery pass** — titles, companies, locations, dates only. `--no-description` on freehire; search output alone on LinkedIn. Apply the 14-day window client-side. Cap ~25 per query.
2. **Screen** — run Step 3's screens on titles and companies. Most of the net dies here, for free.
3. **Body pass** — fetch full detail **only** for survivors. This is where the Step 4 signals are read.

### Step 3: Screens

**Hard screens retained in full** — these are about what the candidate cannot do or will not accept, and a growth thesis does not soften them. All are defined in `04-job-evaluation.md` and `CLAUDE.md`:

- **Eligibility Gate** (work authorization, clearance, citizenship)
- **Language Gate**
- **Any candidate-specific hard screen** in `04-job-evaluation.md` (for example, a screen against roles that require evidence the candidate does not have, such as a reviewable code portfolio or a licence)
- **Licensed / clinical instructional roles**, unless the candidate holds the licence
- **Relocation or onsite beyond what the candidate's work-mode rules allow** — a hub anchor, "remote within X of <city>", or required onsite days outside the candidate's allowed area is out
- **Residency and time-zone restrictions** that exclude the candidate's state of residence (`CLAUDE.md`). *Measured 2026-09-13: a "Business Analyst (Remote)" posting required Colorado residency in the body — the restriction is often only in the body.*
- **Categories the candidate has declined** (recorded in `CLAUDE.md` or `04-job-evaluation.md`). A generalist role that touches a declined category among many hats is usually fine; a role that *is* the declined category is not.

**Screens deliberately INVERTED or dropped for this skill:**

- **Downward seniority is a target, not a reject.** "Coordinator", "Associate", "Assistant", "Specialist", "I/II" are what this skill is looking for. The *upward* screen still runs: `VP`, `Head of`, `Director`, `Principal`, `Staff`, `Senior Manager` are out — they are not climbable-into, and they're not the bet.
- **Low pay is not a negative.** Do not flag a posting for falling under the candidate's normal floor. Flag only below a floor the candidate sets for below-level work (ask if none is recorded), or where no ceiling evidence exists at all (Step 4, Ceiling). If the candidate's profile has no pay rule, report the posted figure and do not flag.
- **Experience-level Gate 4 relaxes downward only.** A posting asking for 1-3 years is ideal. The 8+ year veto still stands.

**Screens ADDED, specific to this skill.** Each one kills the thesis outright:

| Screen out | Why it kills the bet |
|---|---|
| **Enterprise (roughly 1,000+ staff)** | The stated reason this skill exists: watched closely, no room to carve |
| **Government, civil service, public university** | Rigid classification bands; promotion is a posted req, not a carve-out |
| **Staffing *intermediaries* and recruiter pipeline ads** | No company to climb inside — the employer is a placement firm, not the workplace. *Measured 2026-09-13: "Manager, AI Solutions" at Axial Search was a search firm's talent-pool ad — "apply to express your interest in roles like this one."* Robert Half, Insight Global, Axial Search and similar are out **when they are posting on a client's behalf or building a talent pool**. This is NOT a screen against staffing companies as employers — see below. |
| **BPO, call center, scaled support floor** | Metrics-managed seat, by design |
| **A named platform in the title** (SAP, Workday, Oracle, D365, ServiceNow, NetSuite, Epic, JDE, Deltek) | The platform is the job; the ceiling is that platform |
| **Existing owner of the function implied** | "Partner with IT to submit requests" means the space is taken |

### Companies in the candidate's own domain are a TARGET, not a screen

The distinction is the whole point:

| | Verdict |
|---|---|
| A small company in an industry the candidate already knows **hiring them to work there**, in a frontline seat | **Target — often the highest leverage gap available** |
| A staffing firm **posting a role on a client's behalf**, or advertising a talent pool | Screened out (above) |

A frontline seat in a domain the candidate knows cold is the thesis in its purest form: no ramp, an operation whose toil they already recognize, and a seat from which they can see what to build. Such seats should score **high on Leverage gap** when:

- The candidate knows the domain without a ramp (per `01-candidate-profile.md`)
- The operation is visibly manual, which is the strongest buy signal in Step 4
- The candidate's distinctive skills are rare in that kind of back office

If the candidate's background is in staffing or scheduling, note that a staffing agency hiring for its own back office is an *employer* here, not an intermediary — read who the employer actually is.

**Candidate tolerances are scored, not vetoed, unless the candidate made them a veto.** If the candidate has said a feature of the day-to-day is unwelcome but not disqualifying (phone load is the common one), score it in Floor tolerability and quote the posting's own language so the candidate can make the call.

### Step 4: Score the climb (0-100)

This replaces `/scrape`'s fit assessment entirely. Four dimensions.

**1. Leverage gap (0-40) — the core of the bet.**
How far the candidate's skills exceed what the role asks, *and* how scarce they are in that organization.

| Score | Looks like |
|---|---|
| 32-40 | Manual process described explicitly (spreadsheets, copy-paste, "manually track"), nobody with the candidate's distinctive skills evident, the candidate's work would be visibly novel |
| 20-31 | Ordinary role at a small company; the candidate's skills would be welcomed but aren't begged for |
| 8-19 | Some of that capability already in place; the candidate would be one capable person among several |
| 0-7 | A team already owns this; no leverage gap |

**Read the posting for toil.** Phrases like "maintain spreadsheets", "manual data entry", "compile weekly reports", "coordinate between systems" are the strongest possible buy signals for a candidate whose distinctive skills remove that kind of work. Adjust the toil list to what the candidate's skills actually remove.

**2. Carve-out room (0-30).**
Evidence the candidate could expand beyond the job description.

Positive: "wear many hats" · "first operations hire" · "define the process" · "0 to 1" · "own this end to end" · small named team · founder or owner as the direct report · a mandate broader than the title · no incumbent in the function.
Negative: narrow task list · "support the X team" · heavy approval chains · "follow established procedures" · a named manager layer above a named manager.

**3. Ceiling (0-20).**
Can the candidate actually move up, and is there anywhere to move to?

Positive: company is visibly growing (multiple open roles, recent funding) · flat structure · internal-promotion language · the function is currently understaffed.
Negative: rigid job families · union or civil-service bands · a role that exists to be a role forever · a company too small to ever need more (a 3-person lifestyle business has no rungs) · franchise back-office.

**Sweet spot is roughly 10-200 employees.** Below ~10 there may be no ceiling to climb; above ~200 the space is usually already owned.

**4. Floor tolerability (0-10).**
Is the day-to-day and the pay acceptable *while* the candidate climbs? Use the candidate's pay rule from `CLAUDE.md`, or the below-level band they gave for this skill; a posting under any hard floor they set scores 0 here regardless of everything else. If the candidate has no pay rule, score this dimension on the day-to-day only. No published range is normal at this size and is not a penalty — note it for the conversation.

**Day-to-day tolerances are scored here, and only here** (see the domain section above). A typical scale for a tolerance the candidate named, phone load as the example:

| Load | Effect |
|---|---|
| The unwelcome thing is the deliverable — a call queue, dial targets, an answer-time SLA | **-6**, and say so in the presentation |
| It is a regular named duty | **-3** |
| Occasional, incidental | **0** — normal, never penalized |

Quote the posting's own language in the highlights so the candidate can make the call rather than trusting the score.

**Bands:**

| Total | Verdict |
|---|---|
| 75-100 | **Strong climb** — present first, with the carve-out thesis stated explicitly |
| 55-74 | **Worth a look** — present with the weakest dimension named |
| 35-54 | **Thin** — list only, one line, no highlights |
| 0-34 | Do not present; store as `skipped` |

### Step 5: Company-size estimation (no paid lookups)

Headcount is rarely stated. Estimate from evidence already in hand, and **say which signal was used** — never assert a number you did not see:

| Signal | Reads as |
|---|---|
| **ATS host** | Ashby / Lever / Greenhouse → startup-to-midsize. Workday / Taleo / iCIMS / SuccessFactors / Oracle → enterprise. This is the single most reliable free proxy. |
| Benefits boilerplate | Long legal benefits blocks, EEO appendices, "job family", "grade level" → large |
| Team language | "our team of 12", "you'll be our second ops hire" → small, and quotable |
| Open-role count on the same board | 3 roles → small; 400 roles → enterprise |
| Posting voice | First-person founder voice → small; templated third person → large |

Budget **at most one** WebFetch per shortlisted company (its own About/careers page) to confirm size. Never scrape LinkedIn company pages programmatically.

### Step 6: Store

Same schema and key helper as `/scrape` — `python tools/job_key.py --company … --title … --url …`, never hand-slugified. Additive fields for this track:

```json
"track": "long",
"climb_score": 0-100,
"climb_verdict": "strong climb | worth a look | thin",
"company_size_est": "~10-50 (Ashby board, 4 open roles)",
"leverage_note": "one line: what toil the candidate's skills would remove"
```

Keep every existing field; never drop scoring fields already on the entry. Store the displayed location verbatim and never write a remote claim that came only from a query flag.

### Step 7: Present

```
## Long Scrape — YYYY-MM-DD

Cast: N queries across M portals. Screened X → presenting Y.

| # | Climb | Title | Company | Size (signal) | Pay | URL |
|---|-------|-------|---------|---------------|-----|-----|
```

For each **strong climb**, three bullets:
- **The leverage gap** — the specific toil in the posting the candidate's skills would remove
- **The carve-out** — the quoted language suggesting room, or its honest absence
- **The ceiling** — why this company would have somewhere to promote the candidate, or the doubt

Then state plainly, once, what the whole list is worth: whether these are genuinely climbable or whether the run mostly found underpaid work with no upside. **An honest "nothing here clears the bar" is a valid outcome** and is better than padding the table — the entire premise of taking a step down is that the climb is real.

Close with:
> "Want me to evaluate any of these in detail? Give me the number(s)."

---

## Rules

1. **Never fabricate postings.** Only what a CLI, MCP tool, or fetched page actually returned.
2. **Never store a listing-page URL.** Real postings only.
3. **Overqualified is the point.** Do not down-score a role for being junior — that is `/scrape`'s job, and it is wrong here.
4. **The step down must buy something.** A low-paying role with no carve-out room and no ceiling is just a low-paying role. Score it honestly and let it fail.
5. **Small is a means, not an end.** A tiny company with nowhere to climb fails the Ceiling dimension.
6. **Never screen out a company in the candidate's domain as an employer.** Screen out placement firms posting for clients. The two look alike in a result list and are opposites here — read who the employer actually is.
7. **Hard screens are not negotiable** — the candidate's hard screens from `04-job-evaluation.md`, their work-mode and relocation rules, and residency mismatches.
8. **Serialize MCP portal calls.** The Indeed quota is per account; parallel agents starve each other.
9. **Say which size signal was used.** An estimated headcount without its evidence is a guess presented as a fact.
10. **Keep the two tracks legible.** Everything this skill writes carries `"track": "long"`.
