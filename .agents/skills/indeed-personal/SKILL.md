---
name: indeed-personal
version: 1.0.0
description: >
  Search Indeed job listings through the candidate's own signed-in Chrome profile.
  A human signs in once in a visible window; every later search drives that
  authenticated profile headlessly. Replaces the MCP Indeed connector when
  it is rate-limited or unattached. Trigger phrases: indeed search, indeed
  jobs, search indeed, remote jobs on indeed.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
interface: cli  # NOT a bun CLI - the runner is `python`. See "Interface" below.
allowed-tools: Bash(python .agents/skills/indeed-personal/cli/indeed_personal.py *), Bash(python3 .agents/skills/indeed-personal/cli/indeed_personal.py *)
---

# Indeed Personal-Session Search

## Measured 2026-09-13 - what actually needs a login

Tested end to end against live Indeed. **Auth is required for detail, not for search.**

| Operation | Anonymous | Notes |
|---|---|---|
| `search` (result cards) | **Works** | Titles, companies, locations, salary all parse |
| `detail` (`/viewjob` body) | **Fails** | Redirects to `secure.indeed.com/auth`; title becomes "Sign In / Indeed Accounts" |
| Headless anything | **Hard-blocked** | Page title is literally `Blocked - Indeed.com`. Not a challenge, does not self-clear |
| Headed, anonymous | **Works** | Lands on Cloudflare "Just a moment..." and clears in ~10s |

Consequences that matter:

- **Headed is the default and `--headless` is opt-in.** Headless is blocked outright, so the flag is close to useless - it exists only to make the failure explicit rather than mysterious.
- **Wait out the interstitial.** The results *title* lands several seconds before the cards hydrate; parsing on title alone returned "0 results" for a page holding 16 postings. `_await_page` waits for actual cards, an explicit empty state, or timeout.
- **Search returns `date: null`.** Current result cards carry no posted date at all - `[data-testid="timing-attribute"]` sounds like one but holds company + location. Recency is enforced by the `fromage=` URL parameter, and the real date comes from the detail page's JSON-LD. A `null` here is honest, not a parser failure.
- **`window.mosaic` is absent**, so the DOM parser is the normal path. `meta.parse_path` says which ran; `degraded_parse` now means a genuine total parse failure, not "used the DOM".
- **Iterate `div.job_seen_beacon`, never `[data-jk]`.** The page carries ~37 `[data-jk]` nodes for 16 real cards; iterating them yielded duplicate rows plus a placeholder id (`cdef0123456789ab`, sequential hex) that is not a job.

**So: sign in when posting bodies are needed** - which is any run that scores postings, since `/long_scrape`'s climb scoring reads the body. Skip the login for pure title-level discovery.


Searches Indeed as a **signed-in jobseeker**, using a Chrome profile signed in
by hand to the candidate's Indeed account (see `/indeed_login`). No credentials are ever read, typed, stored, or
transmitted by this skill — the human does the sign-in, and the profile
directory on disk is the whole session.

## Why this exists

`indeed-search` (the MCP connector) is the preferred Indeed path when it
works: authorized, first-party, no ToS exposure. It is also **account-rate-
limited to roughly one call per minute, and a failed attach is sticky for
the whole session** (see that skill's own notes). On 2026-09-13 it was
unavailable for an entire run, which mattered because Indeed is the only
portal whose remote signal is trustworthy from search output.

This skill is the fallback that does not depend on that quota.

| | `indeed-search` (MCP) | `indeed-personal` (this) |
|---|---|---|
| Auth | First-party connector | The candidate's own signed-in browser profile |
| Rate limit | ~1 call/min, account-wide | None imposed; self-throttled ~3s between pages |
| ToS | Authorized integration | **Against Indeed's ToS** — personal use only |
| Availability | Fails when quota is spent | Works while the profile stays signed in |
| Salary data | Yes | Yes, from the embedded JSON |

**Order of preference: try `indeed-search` first. Fall back to this one when
it is unavailable, rate-limited, or returns nothing.** Never run both for the
same query in the same run — it doubles the footprint for identical results.

## WARNING - personal use only

Automated access is against Indeed's Terms of Service, exactly as with
`linkedin-search`. **Keep volume low, never use it commercially or for bulk
data collection, and run it on your own responsibility.** The self-imposed
delay between page loads is not decoration; do not remove it, and do not
raise `--limit` into the hundreds.

## Interface

**The runner is `python`, not `bun`.** This is the first non-bun CLI portal
in the repo. `/scrape` Step 1b-cli dispatches on the `interface:` key, and
`cli` is correct here — but a failed `bun --version` check must **not**
disable this portal or push it to the WebSearch fallback. It has no bun
dependency.

Requires `patchright` and Chrome installed locally. Chrome is driven via
`channel="chrome"`, so there is no browser download.

## Commands

### One-time sign-in (human, visible window)

```bash
python .agents/skills/indeed-personal/cli/indeed_personal.py login
```

Opens Chrome, waits up to 10 minutes for the human to complete sign-in
(including Google SSO and any 2FA), verifies an authenticated session, and
records the timestamp. **Run this yourself — the assistant cannot and must
not do the signing in.**

### Check session health

```bash
python .agents/skills/indeed-personal/cli/indeed_personal.py status
```

Exit 0 and `{"authenticated": true}` when the profile is live. Exit 1 with a
`reason` when it is logged out or facing a challenge. **Run this before a
search sweep** — it is one page load and turns a confusing empty result set
into a clear "re-run login".

### Search

```bash
python .agents/skills/indeed-personal/cli/indeed_personal.py search \
  --query "operations associate" --location "Remote" \
  --jobage 14 --limit 20 --format json
```

| Flag | Meaning |
|---|---|
| `--query` / `-q` | **Required.** Keywords. |
| `--location` / `-l` | Place string, or `Remote`. Default `Remote`. |
| `--jobage <days>` | Posted within N days. Default 14; capped at 30 by Indeed. |
| `--limit` / `-n` | Max results (paginates by 10). Default 20. |
| `--remote` | Applies Indeed's own remote facet on top of the location. |
| `--format json\|table` | Default `json`. |
| `--headed` | Show the window (debugging, or clearing a challenge). |

Output matches the other portals' contract:

```json
{"meta": {"count": 12, "query": "...", "portal": "indeed-personal",
          "degraded_parse": false},
 "results": [{"id": "<jk>", "title": "...", "company": "...",
              "location": "...", "date": "YYYY-MM-DD",
              "salary": {"min": 55000, "max": 70000, "type": "yearly"},
              "url": "https://www.indeed.com/viewjob?jk=<jk>"}]}
```

`date` is a real ISO date, derived from the embedded epoch timestamp where
Indeed provides one and from "Posted N days ago" otherwise. **The 14-day
window is applied client-side as well**, so results are already scoped.

### Detail

```bash
python .agents/skills/indeed-personal/cli/indeed_personal.py detail <jk>
```

Accepts a bare job key or a full `viewjob?jk=` URL. Returns title, company,
location, salary line, full description, and `isActive` (false when Indeed
shows the expired-posting banner — treat exactly like `linkedin-search`'s
`isActive: false` and store `status: "expired"`).

**This is the piece the MCP path could not do.** `to.indeed.com` short links
have no WebFetch fallback and `/viewjob` 403s anonymously; inside the
authenticated profile it loads normally.

## Failure modes, and what each means

| Output | Meaning | Fix |
|---|---|---|
| `{"error": "not_authenticated"}`, exit 2 | Profile logged out | Re-run `login` |
| `{"error": "challenge"}`, exit 2 | Captcha / Cloudflare interstitial | `login --headed`, clear it by hand, then retry |
| `meta.degraded_parse: true` | Embedded JSON moved; DOM fallback used | Results are thinner but real. Report as **degraded** per `/scrape` Step 4.75 |
| Empty `results`, authenticated | Genuinely no matches in the window | Not a health problem |

**A challenge is never a health verdict of "broken"** — same rule as a rate
limit. Record it, back off, and do not hammer.

## Storage and secrets

| Path | What it is |
|---|---|
| `job_scraper/.indeed_profile/` | The authenticated Chrome profile. **This directory IS the credential.** |
| `job_scraper/.indeed_session.json` | Last-login timestamp and profile path. No secrets. |

Both are gitignored. Never commit them, never copy them out of the machine,
and never paste their contents anywhere — anyone holding that profile is
signed in as the candidate. Override the location with `INDEED_PERSONAL_PROFILE_DIR`
if it needs to live outside the repo entirely.

## Rules

1. **Never attempt to sign in.** No credential entry, no 2FA handling, no
   password prompts. If the profile is logged out, stop and tell the user to run
   `login` with the candidate signing in in the Chrome window.
2. **Prefer the MCP connector when it is available.** This is the fallback.
3. **Keep volume low.** 2-4 searches per run, `--limit` in the tens. The
   inter-page delay stays.
4. **`source` in `seen_jobs.json` is `cli`** for entries from this skill, with
   `portal: "indeed-personal"` distinguishing it from the MCP path's `mcp`.
5. **Store the `viewjob?jk=` URL**, never a search-results page — the same
   listing-page rule every portal follows.
6. **A challenge or a logged-out profile is `unavailable`, not `broken`.**
