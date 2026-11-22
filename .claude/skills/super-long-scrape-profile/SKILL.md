---
name: super_long_scrape_profile
description: >
  Build, validate, revise or retire a search profile for /super-long-scrape. A profile
  decides what a scrape hunts, where, what dies for free, and how survivors are scored.
  This skill standardizes how one gets made: interview, draft, dry-run against the real
  portals to measure yield, then write and register it. Triggers on: new search profile,
  build a profile, add a scrape profile, search profile for, /super_long_scrape_profile
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(bun --version), Bash(bun run .agents/skills/*/cli/src/cli.ts *), Bash(python tools/job_key.py:*), Bash(python3 tools/job_key.py:*), WebFetch, WebSearch, AskUserQuestion
---

# Build a Search Profile

A profile is the difference between "search for jobs" and "search for **this** kind of
job". `/super-long-scrape` supplies the machinery; the profile supplies the hunt. This
skill exists so profiles are built the same way every time and are measured before they
are trusted.

**Read `.claude/skills/super-long-scrape/profiles/README.md` first.** It is the schema and
this skill enforces it. Everything below is about how to arrive at a good one.

---

## Step 0: Parse input

`$ARGUMENTS` may be:

- **A name and a description** (`/super-long-scrape-profile ai-ops, AI implementation roles`) → build it.
- **A name that already exists** → this is a revision, not a build. Skip to Step 5, and preserve the yield log.
- **`--list`** → print the index from `profiles/README.md` with each profile's status and last yield-log line. An empty index is normal for a new candidate: say no profiles exist yet and offer to build the first. Stop.
- **`--retire <name>`** → set `enabled: false`, add a dated note saying why. Never delete a profile file; the yield log is the only record of what was tried. (The one exception is a change of candidate: the previous candidate's profiles are archived to a git tag and removed from `profiles/`.)
- **Nothing** → ask what kind of work the profile should hunt, in one question.

---

## Step 1: Establish the thesis before touching any query

**A profile without a thesis is a keyword list, and keyword lists rot.** The thesis is what
lets a future run judge whether an unfamiliar posting belongs.

Ask the candidate (directly, or through the user relaying their answers), and do not skip these — each one changes the file materially. Read `CLAUDE.md` and the candidate profile first so you can propose answers the candidate confirms, but never assume them:

1. **What is the bet?** Why would an employer in this category want the candidate specifically, and why would the candidate want them? One or two sentences in the candidate's own words if possible; a thesis that quotes the candidate directly is stronger for it.
2. **Is this at the candidate's level, below it, or beside it?** This decides `scoring:` — `standard` for at-level, `climb` for deliberately below, a named alternative (`tutoring`, or a new rubric added to `profiles/README.md` first) for work that annual bands do not describe.
3. **Geography and mode.** Metro, remote, or both — and which metro? Start from the candidate's work-mode rules in `CLAUDE.md`. The default is `onsite: out` (remote, or hybrid inside the candidate's metro). In-person work needs the candidate's explicit say-so for this profile: `onsite: in-metro` for in-person anywhere in the metro, or `onsite: in-<area>` (kebab-case, naming an area the candidate's profile defines, e.g. a county) for in-person seats in that area only. Work mode is always parsed from the body in Phase 4 and never filtered at search time, so the profile states the rule, not a filter. If the profile has a seniority target, set `level:` (today only `entry` exists).
4. **Employment types.** Full-time only, or are contract and part-time in scope? Ask separately for remote and local, because a candidate's rules often split them.
5. **Pay.** Annual or hourly? Does the candidate want a pay screen at all (some do not; then the profile only reports the posted figure)? If so, what is the target, and what is the floor below which a posting has to justify itself? An hourly profile must say so explicitly, or the machinery will flag every posting against an annual floor.
6. **What should die for free?** The universal screens always run. What does this profile *add*, and — the important half — **what does it remove?** A profile that removes nothing usually has not been thought through.

Check the answers against `01-candidate-profile.md`, `02-behavioral-profile.md` and
`CLAUDE.md`. **A profile may not contradict them.** If the user asks for a profile that
hunts something a deal-breaker excludes, say so plainly once, and build it if they confirm
— it is their search.

---

## Step 2: Derive the query blocks

This is the part that decides whether the profile finds anything.

**Name blocks by function, not by title.** The same work is posted under wildly different
titles, and titles churn. "The coordinator rung" survives a fashion cycle that "operations
coordinator" does not.

**Build each block from three sources, in this order:**

1. **Titles the candidate has actually held or been scored well against.** Mine `job_search_tracker.csv` for high-`fit_rating` rows in this category — they are measured evidence, not guesses.
2. **The synonym sweep.** For each core title, ask what else the same job is called: by industry, by company size, and by seniority one notch either side. `data analyst` → `reporting analyst`, `insights analyst`, `BI analyst`, `decision support analyst`.
3. **The adjacent-function sweep.** What sits either side of this work in an org chart? Those postings sometimes describe the same job.

**Rules, all learned from measured failures:**

- **6 to 10 terms per block, 4 to 7 blocks.** Fewer and coverage is thin; more and the run stops being affordable.
- **Mark each block priority 1, 2 or 3**, so a short run can stop after the high-value ones. Roughly a third should be priority 1 — if everything is priority 1, nothing is.
- **Never pad with near-duplicates.** `data analyst` and `data analyst II` return the same corpus. Distinct query surface is the only thing that buys volume (measured 2026-09-14: two same-day runs of the same ~14 titles overlapped almost completely).
- **Keep modifiers out of portal queries.** `startup`, `"Series A"`, `"wear many hats"`, vertical names — Indeed's keyword field handles these badly. They belong on the Phase 3 ATS `site:` searches, in their own list.
- **Include at least one trap-flagged term where one exists.** For example, an analyst profile would list `analytics engineer` with a note that it is usually an engineering title to skip unless the candidate is an engineer. Naming the trap is more useful than omitting the term, because a future run will otherwise rediscover it.

---

## Step 3: Route the portals

Start from the defaults in `profiles/README.md` and change them only where this profile has
a reason. Then write down the reason, because the next person will want to know.

Routing questions worth asking explicitly:

- **Is this market Indeed-native or LinkedIn-native?** Hourly, part-time, small-independent and trades work is Indeed-native. Corporate, salaried, mid-to-large is LinkedIn-native. Getting this backwards wastes the whole run.
- **Do regional boards matter here?** For anything in-person and local — tutoring, learning centers, trades — a metro's regional board and ZipRecruiter can out-yield both majors (measured for Portland, where `macslist.org` did; find the equivalent for the candidate's metro).
- **Do the ATS boards apply?** They are gold for small tech-adjacent companies and near-useless for verticals running industry-specific systems (Bullhorn, Avionté) that expose no public JSON.

---

## Step 4: Dry-run before writing the file

**Do not ship an unmeasured profile.** Spend a bounded probe — roughly **6 to 10 queries,
one or two per block, at the profile's own recency window** — and record what comes back.

What the dry run is looking for:

| Signal | Reading |
|---|---|
| A block returns nothing across two terms | The block is wrong, or the portal routing for it is wrong. Fix before shipping. |
| A block returns the same companies as another block | They are one block, not two. Merge. |
| Most results die on the universal screens | Either the query is too generic or the profile needs an added screen. Say which. |
| The same three companies dominate every query | The portal's corpus for these terms is exhausted. Cap the block and note it in the profile's Portal routing section. |
| Good hits arrive but outside the geography or pay rule | The frontmatter is wrong, not the queries. |

Report the dry-run numbers to the user before writing anything. **A profile that finds
nothing in a dry run should be reported as such, not shipped and left to fail quietly on a
four-hour run.**

---

## Step 5: Write, validate, register

1. **Write `profiles/<name>.md`** to the schema: frontmatter, then Thesis, Query blocks, Portal routing, Screens (deltas), Scoring notes, Presentation, Yield log — in that order.
2. **Seed the yield log with the dry run**, marked as a dry run, so the profile ships measured.
3. **Run the validator**, which checks the structural half of the schema for you:

   ```bash
   python tools/validate_profiles.py
   ```

   It checks name/filename agreement, a legal `scoring` value, a legal `onsite` value
   (`out`, `in-metro`, or `in-<area>` in kebab-case), a unique `track_tag`, the pay-model
   split, per-side employment types, all seven required sections, a priority on every
   block, that the Screens section reads as deltas, that a yield-log table exists, and
   that the profile is listed in the README index. An empty registry passes (exit 0). It
   cannot tell you whether the profile finds anything — that is Step 4's job.

4. **Then check by eye what the validator cannot:**
   - [ ] `name` matches the filename, kebab-case
   - [ ] `scoring` is one of `standard`, `climb`, `tutoring` — or the new rubric was added to `profiles/README.md` first
   - [ ] `track_tag` is unique across profiles
   - [ ] Pay block matches the pay model (`pay` for annual, `pay_hourly` for hourly — never both)
   - [ ] `employment` is stated separately for remote and local
   - [ ] Screens section contains **deltas only**, never a restatement of the universal list
   - [ ] Every block carries a priority
   - [ ] Nothing in the profile loosens an accuracy, honesty or scope rule
5. **Register it** in the index table at the top of `profiles/README.md`. A profile absent from the index is invisible to `/super-long-scrape` Step 0's listing.
6. **Tell the user how to run it**, and say plainly that the first real run is still a hypothesis test whose job is to fill in the yield log.

---

## Worked example — a hypothetical `ai-ops`, half-built

**Example only.** This profile does not exist; it is a model of the shape, for an imagined
candidate who has built LLM tooling into internal workflows. It is left deliberately
unfinished. A real profile's content comes from the real candidate's interview.

**Thesis.** Companies embedding LLMs into real internal workflows. The imagined candidate
built a production AI layer inside a business (agents with a bounded action set, mandatory
escalation, verification around the outputs) and kept it cheap. That is scarce, it is
recent, and postings that name it are the ones where the candidate's strongest
differentiator is legible rather than buried. (A real thesis would quote the candidate.)

**Frontmatter.** `scoring: standard`, `geography: [remote, <candidate-metro>]`,
`onsite: out`, `track_tag: ai-ops`, pay per the candidate's answer to Step 1.5, remote
contract and part-time in scope only if the candidate said so.

**Blocks, drafted.**
- **A, the named function** *(p1)*: `AI operations` · `AI implementation` · `AI enablement` · `AI program manager` · `AI adoption` · `AI workflow`
- **B, forward-deployed** *(p1)*: `forward deployed engineer` (flag: usually an engineer title) · `solutions architect AI` (same flag) · `implementation specialist AI` · `technical account manager AI` · `solutions consultant AI`
- **C, automation and internal tools** *(p2)*: `automation specialist` · `business systems automation` · `internal tools` · `workflow automation` · `RPA analyst` (flag: usually a named-platform seat)
- **D, the applied-AI analyst seat** *(p2)*: `applied AI analyst` · `AI product associate` · `prompt engineer` · `LLM operations` · `AI quality analyst`

**Screens — deltas, drafted.** Adds: model-training and ML-research seats (the imagined
candidate's AI work is LLM application, **not** model training); anything requiring
evidence the candidate's profile says they lack (a published research record, reviewable
code). Removes: nothing.

**Unfinished on purpose:** the dry run, the portal routing question of whether AI-ops
postings are Indeed-native or ATS-native, and the scoring note on how to treat "5+ years of
ML" as a bar. Run Steps 3 and 4 and it is ready.

---

## Rules

1. **Interview before drafting.** A profile assembled from assumption rather than from the candidate's own words is the failure mode this skill exists to prevent. Never carry a previous candidate's answers over.
2. **Measure before shipping.** Step 4 is not optional. An unmeasured profile is a hypothesis wearing a file extension.
3. **Screens are deltas.** Restating the universal list guarantees the copies drift.
4. **Never delete a profile.** Retire it with `enabled: false` and a dated reason. The yield log is the institutional memory of what was tried and what it returned.
5. **Profiles are data, not machinery.** Retry logic, phase discipline and storage schema belong in `/super-long-scrape`. If a profile needs new machinery, change the skill.
6. **A profile cannot loosen an accuracy or honesty rule**, and cannot override a deal-breaker silently. Surface the conflict, then follow the user's decision.
