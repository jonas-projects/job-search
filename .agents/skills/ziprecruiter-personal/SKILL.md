---
name: ziprecruiter-personal
version: 1.0.0
description: >
  Search ZipRecruiter through the candidate's own signed-in Chrome profile, the twin of indeed-personal.
  Search cards carry the apply type (ZipRecruiter 1-Click vs company site), the work-mode facet,
  pay and the posted date as structured JSON; detail returns the full description and whether
  the posting is still active. Trigger phrases: ziprecruiter, zip recruiter, search ziprecruiter.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
interface: cli  # runner is `python`, not bun
allowed-tools: Bash(python .agents/skills/ziprecruiter-personal/cli/zip_personal.py *), Bash(python3 .agents/skills/ziprecruiter-personal/cli/zip_personal.py *)
---

# ZipRecruiter Personal-Session Search

Built 2026-10-14. Same footing as `indeed-personal`: the candidate's ZipRecruiter account is signed in once (by a person, never the tool) in a visible Chrome
window; the profile at `job_scraper/.zip_profile/`
IS the session (gitignored, never commit it). Credentials are never read, typed or stored by
the tool. Browser plumbing (window handling, `NEEDS_HUMAN` alerts, captcha
waits) is imported from `indeed_personal.py`, so both portals behave the same.

**Against ZipRecruiter's Terms of Service.** Personal use, low volume, paced (2-4 s between
pages), a person (the user) solves any captcha.

## Commands

```
python .agents/skills/ziprecruiter-personal/cli/zip_personal.py login        # the candidate signs in in the Chrome window
python .agents/skills/ziprecruiter-personal/cli/zip_personal.py status
python .agents/skills/ziprecruiter-personal/cli/zip_personal.py search -q "<keywords>" -l "<City, ST>" --jobage 14 [--pages 2] [--format table]
python .agents/skills/ziprecruiter-personal/cli/zip_personal.py search -q "scheduling coordinator" -l "Remote" --remote --jobage 14
python .agents/skills/ziprecruiter-personal/cli/zip_personal.py detail "<job url>"
python .agents/skills/ziprecruiter-personal/cli/zip_personal.py probe "<url>" --name x   # dev aid: saves html/png/txt to job_scraper/.zip_probe/
```

Output matches `indeed-personal` (`{"meta":..., "results":[...]}`; detail returns `{"result": ...}`)
so `/super-long-scrape`'s phases read either. Extra fields per result:

| Field | Meaning |
|---|---|
| `id` | ZipRecruiter `jid` (16 hex), from the canonical job URL; the stable dedup key |
| `listing_key` | ZipRecruiter's own listing key (what the apply button acts on) |
| `apply_type` | `one_click` (ZipRecruiter's own application, stays on site) or `external` (company site) |
| `work_mode_facet` | `remote` / `hybrid` / `in person` / `remote optional` / `unspecified`: the portal's label, **never trusted**: Phase 4 reads the body |
| `already_applied` | ZipRecruiter shows an application status for this listing |
| `date` | posted date (YYYY-MM-DD), real, unlike Indeed's search cards |

## Measured 2026-10-14

- **The pages are a Next.js app; the data is JSON in the RSC stream** (`self.__next_f.push`).
  Search: `serializedJobCardsData.jobKeysMap` (cards) and `totalListings`. Detail:
  `jobDetails`, with the description as an RSC text reference (`"$25"` -> chunk `25:T<hexlen>,<html>`).
  Far steadier than the markup; parse the stream, not the DOM.
- **20 cards per page**, `page=N` paginates; `days=N` sets recency; `search=`, `location=`.
- **Remote:** `refine_by_location_type=only_remote` with **`location=Remote`**. With an empty
  location ZipRecruiter scopes to the profile's home area and returned 1 result instead of 89.
- **Metro:** a `location=<City, ST>` search covers ~25 miles (measured 2026-10-14: a border-city
  search came back mostly from across the state line), so the candidate's Phase 4 work-mode
  rules still apply to every hit.
- **Apply mix** (measured 2026-10-14): a local search had 6 of 20 `one_click`; remote: most of page 1.
- **Overlap with Indeed and LinkedIn is heavy** (same employers reposted). Dedup by company +
  title as well as by URL before spending a detail fetch.
- Remote results include job-mill and placement fronts ("Catalystcareernetwork", travel
  "coordinator" seats); the universal screens and Phase 5 provenance still apply.
- Headed only (same as Indeed). No captcha seen in the first session.

## 1-Click Apply (queue built 2026-10-14)

```
python .agents/skills/ziprecruiter-personal/cli/zip_personal.py set-resume <pdf> [--dry-run]
python .agents/skills/ziprecruiter-personal/cli/zip_personal.py apply "<job url>" --resume <pdf> [--submit] [--present --wait-human 900] [--out <dir>]
python tools/zip_apply_queue.py add "<job url>" <folder> | status | run [--present] | retry <folder> | withdraw <folder> [why]
```

**1-Click sends the resume stored on the ZipRecruiter profile**, so `apply` first replaces the
profile resume with the tailored PDF and confirms the profile shows that exact file name; if the
swap can't be confirmed nothing is clicked. Without `--submit` it stops before the click.

**Watched test, Bridge Crest Post Acute, 2026-10-14 (submitted):** the resume swap worked (profile
file name changed, confirmed). Clicking 1-Click Apply did **not** send at once: it opened an
employer question form (9 dropdowns, mostly marked Optional: employed by / relatives at the parent
company, licence by start date, right to work, sponsorship, 18+, background check + dispute
resolution agreement, drug test/physical, essential functions; then a truthfulness attestation and
Continue). It was answered by hand in the browser window; ZipRecruiter showed "Your application was submitted".

**First queue run, 2026-10-14 (previous candidate; three employers, all submitted with a person present):** every one of
the four 1-Click applications so far opened an employer form, so treat a form as the norm, not the
exception. They ranged from one question ("agree to work 8am-5pm EST?") to 16 (legal name, state,
years of experience as ranges, experience checkboxes, start date, salary within range). The form
can also restate the job's location (one showed a different city from the card's): read it, it settles `mode unclear` cases. Mostly yes/no dropdowns plus a few
free-text fields, all answerable from `answers_base.json` and the profile, which makes auto-answer
the clear next build.

So: an **unattended** run closes any such form unsent and parks the item (`needs_answers`); a
`--present` run alerts (`NEEDS_HUMAN`) and waits up to 15 minutes for the form to be answered in
the browser window by the candidate. Every
form's HTML is saved as `03_dialog.html` in the attempt folder: auto-answering (from
`answers_base.json`, as the Indeed form does) is the next step once a few forms are captured.
Only candidate-approved postings are queued; the queue never clicks for anything else.

**1-Click is a submission, so `zip_apply_queue.py run` refuses unless the candidate has opted in
to auto-submit** (facts.json `automation.auto_submit: true`, `tools/candidate.py`
`auto_submit_opted_in()`). The previous candidate's opt-in does not carry over; the current
candidate has not opted in. Until then, send ZipRecruiter applications by hand from the board.
Résumé files must be `<First>_<Last>_Resume_*.pdf` from facts.json (`candidate.resume_prefix()`).
