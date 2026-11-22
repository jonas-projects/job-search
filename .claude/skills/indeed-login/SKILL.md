---
name: indeed_login
description: >
  Sign in to the candidate's Indeed account once, in a real Chrome window, so
  job searches and applications can run as the candidate afterwards. The human does the signing in; this skill only opens
  the window, waits, and verifies. Triggers on: indeed login, log into
  indeed, sign in to indeed, indeed session expired, reauth indeed,
  /indeed_login
allowed-tools: Read, Bash(python .agents/skills/indeed-personal/cli/indeed_personal.py *), Bash(python3 .agents/skills/indeed-personal/cli/indeed_personal.py *), Bash(python -c *), Read
---

# /indeed_login - Authenticate the Indeed Profile

Opens a real Chrome window on Indeed, waits while **the human** signs in,
then verifies and saves the session. Every later `/scrape` and
`/long_scrape` run uses that profile headlessly.

**The assistant never signs in.** It does not read, type, request, store, or
transmit credentials. It opens a window and watches the URL. Everything
typed goes into Chrome directly. If the user offers a password, refuse it -
it is not needed and must not be accepted.

**Whose account.** The profile must be signed in to the **candidate's** Indeed account (the
person named in `.claude/skills/apply-automate/facts.json`), because applications go out from it.
The candidate types their own credentials in the Chrome window. If the profile is signed in to
someone else's account, sign that account out (or delete `job_scraper/.indeed_profile/`) first.

---

## Where the browser runs

Chrome opens as a normal window on the user's own screen. When something needs a person (a captcha, a sign-in, a consent, the final Submit), the tool plays a sound where it can and prints one `NEEDS_HUMAN: <reason> | <detail>` line on stderr, at most every 5 minutes per reason; `apply_queue.py` forwards those lines to its stdout with the application folder appended. **Run login and queue runs as background tasks, watch for `NEEDS_HUMAN`, and tell the user in chat each time one appears.**

Running on a machine with no screen (a server driven from a phone) is supported but optional: see `docs/remote-server.md`.

## Step 1: Check whether this is even needed

```bash
python .agents/skills/indeed-personal/cli/indeed_personal.py status
```

| Result | Do this |
|---|---|
| `{"authenticated": true}` | **Stop.** Report the `last_login` date and say no action is needed. Do not re-run login and throw away a working profile. |
| `"reason": "no profile - run \`login\` first"` | First-time setup. Go to Step 2. |
| `"reason": "profile is logged out"` | Session expired. Go to Step 2. |
| `"reason": "challenge/captcha shown"` | Go to Step 2; the human clears the challenge in the same window. |

## Step 2: Preflight

Confirm the dependency is present before opening anything:

```bash
python -c "import patchright; print('patchright ok')"
```

If that fails: `python -m pip install patchright`. Chrome itself is driven
via `channel="chrome"`, so there is no browser download - but **Google
Chrome must be installed**. Chromium, Edge, Firefox and Brave are not
substitutes here.

## Step 3: Tell the user exactly what will happen, before opening the window

Print these instructions **before** launching, not after - the window steals
focus, and a user who does not already know the steps will sit on a blank
Chrome wondering what it wants.

> A Chrome window is about to open on Indeed. It is a **fresh, separate
> profile** - no bookmarks, no history, not signed in. That is expected and
> is not your normal Chrome.
>
> 1. Click **Sign in**, top right.
> 2. Sign in with your **personal** Indeed account.
> 3. Complete any Google SSO, emailed code, or 2FA.
> 4. Wait until you land back on an Indeed page, signed in.
> 5. **Leave the window open.** It closes itself once the session is
>    confirmed.
>
> Nothing you type passes through Claude. You are typing into Chrome. If a
> captcha appears, solve it in that window - that is exactly why this is
> done by hand.

## Step 4: Launch it

```bash
python .agents/skills/indeed-personal/cli/indeed_personal.py login
```

**Run it in the background** (`run_in_background: true`). It blocks for up to
10 minutes while the human works, and a foreground call would stall the
session. `--timeout <seconds>` widens the window when someone needs longer.

The script waits passively: it watches the URL, and only performs its one
verification navigation after the page has been off `secure.indeed.com/auth`
for two calm ticks - so it never yanks a half-finished redirect out from
under the user. If verification fails it returns them to a normal Indeed
page and keeps waiting rather than failing outright.

Progress lines land in the task output file: `waiting... current page: <url>`
every 15 seconds, then `checking session...`. **Read that file rather than
guessing** how it is going, and never claim success before the process exits
with a `DONE` line.

## Step 5: Verify independently

A `DONE` line is the script's own claim. Confirm it separately:

```bash
python .agents/skills/indeed-personal/cli/indeed_personal.py status
```

Only report success on `{"authenticated": true}`. If login printed DONE and
status disagrees, say so plainly rather than reconciling it - that
combination means the redirect test and the save path disagree, which is a
real bug worth surfacing.

## Step 6: Report

State: authenticated yes/no, the `last_login` timestamp, and where the
profile lives. Then mention that `/scrape` and `/long_scrape` now have their
Indeed leg, and offer to run one.

---

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `TIMED OUT` but the user did sign in | Verification nav blocked or slow | Run `status`; if true, it worked. If false, re-run with `--timeout 1200` |
| `challenge/captcha shown` | Cloudflare / hCaptcha interstitial | Re-run login; the human clears it in the window |
| No window appears | Chrome not installed, or `channel="chrome"` cannot resolve it | Install Google Chrome, or set `INDEED_PERSONAL_CHANNEL=""` to use bundled Chromium |
| `BrowserType.launch_persistent_context: ... in use` | The profile is open in another run | Close the other Chrome window driven by this tool, then retry |
| Works, then logs out days later | Indeed expired the session | Just re-run `/indeed_login`; this is normal and expected |

## Rules

1. **Never sign in on the user's behalf**, never ask for a password, never
   accept one if offered, and never type into the auth flow.
2. **Never re-run `login` on an already-authenticated profile** without
   asking - it risks a working session for nothing.
3. **Run the login command in the background.** It blocks for minutes.
4. **Verify with `status` before reporting success.** The script's own DONE
   line is a claim, not proof.
5. **The profile directory is the credential.** Never copy it, print its
   contents, or commit it. It is gitignored; keep it that way.
6. **A timeout is not a failure to report as breakage** - check `status`
   first, since the session often actually took.
