# Search Profiles — the schema

A **profile** is the part of `/super-long-scrape` that changes when you go hunting for a
different kind of job. The skill's machinery — phase discipline, the free title/company
screen, the ATS JSON sweep, the body pass, provenance verification, bulk storage — is the
same whatever you are hunting. What changes is **what you search for, where, what kills a
posting for free, and how survivors are scored.** That, and only that, lives here.

One file per profile, `<name>.md`, this directory. Build them with
`/super-long-scrape-profile`, which enforces this schema; hand-editing is fine once a
profile exists.

---

## Index

No profiles yet. Build the candidate's with `/super-long-scrape-profile`. Add one row per profile:

| Profile | Thesis in one line | Scoring | Geography | Status |
|---|---|---|---|---|

---

## Frontmatter, required

```yaml
---
name: analyst                  # kebab-case, matches the filename
summary: >                     # one sentence, shown when the skill lists profiles
  Strictly entry-level data, business, operations and finance analyst roles.
scoring: standard              # standard | climb | tutoring — see Scoring below
geography: [portland-metro, remote]   # portland-metro | remote | one of each
onsite: out                    # out | in-metro | in-clark-county. Remote or hybrid only is the
                               # default (2026-09-23); in-metro is for tutoring; in-clark-county
                               # (2026-10-08) also passes fully in-person seats in Clark County, WA.
                               # Parsed in Phase 4, never filtered.
level: entry                   # optional. entry = the Phase 4 entry-level rule. Omit for none.
jobage_days: 14                # recency window; 14 is the default everywhere
track_tag: analyst             # written to seen_jobs.json as "track"; keeps ledgers separable
enabled: true                  # false parks a profile without deleting it
pay:
  local_target: 90000          # omit the whole block for hourly profiles and use pay_hourly
  local_flag_below: 75000
  remote_floor: 65000
employment:
  remote: [fulltime, contract, parttime]
  local: [fulltime]
portals: [indeed-search, indeed-personal, linkedin-search, freehire-search, ats-boards]
---
```

**`scoring`** picks the rubric, and it is the single most important field:

| Value | Rubric | Use when |
|---|---|---|
| `standard` | `04-job-evaluation.md` — Technical 30 / Experience 25 / Behavioral 15 / Career 30, bands Strong 75+, Good 60-74, Moderate 45-59, Weak 30-44 | The profile hunts roles at the candidate's level. Most profiles. |
| `climb` | `long-scrape/SKILL.md` Step 4 — Leverage gap 0-40, Carve-out 0-30, Ceiling 0-20, Floor tolerability 0-10 | The profile deliberately hunts *below* the candidate's level and scores room to grow, not present fit. |
| `tutoring` | Defined inside `tutoring.md` — subject fit, rate, flexibility, autonomy | Hourly and per-session work, where an annual band is meaningless. |

A profile may not invent a fourth rubric inline. If one is genuinely needed, add it here
first so every profile that uses it scores the same way.

---

## Body sections, in order

### 1. Thesis
Two to five sentences: what this profile is betting on, and why it is a different bet from
the others. If it inherits a thesis wholesale (as `climb` does from `/long_scrape`), say so
and link the file rather than restating it.

### 2. Query blocks
Named blocks of search terms, `**Block A — <what it is>**`, terms separated by ` · `.
Rules learned the hard way:

- **Volume comes from distinct query surface, not repeated terms or deeper pagination.**
  Two runs of the same 14 titles overlap almost completely (measured 2026-09-14).
- **Name blocks by function, not by job title.** The same work is posted under wildly
  different titles, and a block named "the coordinator rung" survives a title fashion
  cycle that a block named "operations coordinator" does not.
- **Mark each block's priority** so a short run can stop after the high-value ones.
- **Keep small-company modifiers out of the portal queries.** Indeed's keyword field is
  weak at them; they belong on the ATS `site:` searches instead.

### 3. Portal routing
Which portal carries which part of the search, with the reason. The defaults, measured
2026-09-13 and still current:

| Need | Portal | Why |
|---|---|---|
| Remote (US) | `indeed-search` (MCP) or `indeed-personal` | The only portals whose remote signal can be trusted from search output |
| Portland metro | `linkedin-search --location` | `--location` is honored and resolves metros correctly |
| Remote on LinkedIn | keyword only, then verify per posting | `--remote` is inert on the guest endpoint; `-l "Remote"` searches globally |
| Small companies | ATS board `site:` searches, then the board JSON API | Country, salary, liveness and open-role count for free |

A profile overrides this only where it has a measured reason to.

### 4. Screens — deltas only
The universal screens live in the skill and always run: staffing intermediaries and talent
pools, Big-4 and platform consulting, defense and federal contractors (public-sector
employers themselves are in scope unless a profile adds them), clinical and
licensed roles, BPO and call-center floors, `VP`/`Head of`/`Director`/`Principal`/`Staff`/
`Senior Manager`/`SVP`/`Chief` titles, and placeholder job ids.

This section lists only what **this profile adds or removes**. A staffing-operations profile
removing the staffing-company screen is the canonical example: a staffing firm *hiring the
candidate to work there* is the target, while a staffing firm *posting on a client's behalf* is still out.

### 5. Scoring notes
Any profile-specific reading of the chosen rubric — which dimension a domain gap hits,
what counts as a hard requirement here, how to treat a missing salary. Never a new rubric.

### 6. Presentation
What the results table should show for this profile. A climb run leads with the leverage
gap; an analyst run leads with score and pay; a tutoring run leads with hourly rate and
schedule. Say which columns and which one-line verdict.

### 7. Yield log
One line per run: date, queries cast, postings screened free, bodies fetched, presented,
applied. This is the only way to find out that a block has stopped producing. Append,
never rewrite.

```
| Date | Queries | Screened free | Bodies | Presented | Applied |
|---|---|---|---|---|---|
| 2026-09-18 | 13 | 58 → 23 | 11 | 9 | 7 |
```

---

## Rules

1. **A profile is data, not machinery.** If you find yourself writing phase discipline,
   retry logic or storage schema into a profile, it belongs in the skill instead.
2. **Every profile writes its own `track_tag`** so the board and the ledger can tell a
   tutoring hit from an analyst hit years later.
3. **Screens are deltas.** Restating the universal list in every profile guarantees the
   copies drift apart.
4. **Nothing in a profile may loosen an accuracy or honesty rule.** A profile decides what
   to search for and how to score it, never what may be claimed in a document.
5. **The yield log is part of the profile.** A profile with no yield log has never been
   measured, and should be treated as a hypothesis.
