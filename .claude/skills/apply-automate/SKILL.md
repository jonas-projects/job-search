---
name: apply_automate
description: >
  Fill the candidate's approved, drafted and reviewed applications through Indeed's "Apply now"
  form in the background: a queue runner (tools/apply_queue.py) makes one pass per application,
  answers questions from the candidate's profile (auto-answer for unmatched ones), gates on
  name/CV/letter, parks anything needing a person (captchas, consents) and keeps going. It stops
  on the review page for a person to click Submit unless the candidate has opted in to
  auto-submit. Triggers on: apply automate, auto apply, submit on indeed, apply through indeed,
  run the queue, /apply_automate
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(python tools/indeed_apply.py *), Bash(python tools/apply_queue.py *), Bash(python tools/indeed_message.py *), Bash(python .agents/skills/indeed-personal/cli/indeed_personal.py status*), Bash(tools/run_apply_queue.sh*), AskUserQuestion
---

# /apply_automate: background submission of approved Indeed applications

`/apply` decides whether to apply and writes the documents. This skill does the clicking
that comes after, for **Indeed "Apply now" postings only**. It never decides to apply: only
postings the candidate approved in chat are queued. Since 2026-09-28 it runs as a background
queue (see Workflow). The sections after the workflow record what was measured along the way.

**Whose application.** The **candidate** is whoever `.claude/skills/apply-automate/facts.json`
names: their name goes on the résumé and the Indeed form, their facts answer the questions, their
Indeed account applies, and only they can consent, sign, attest or approve an application.
Normally the candidate is also the person in the chat; when someone else runs the session for
them, that person may clear captchas but never consents, signs or answers for the candidate. The code reads the candidate's name from
facts.json through `tools/candidate.py` (`full_name()`, `file_prefix()` = `<First>_<Last>`,
`resume_prefix()`, `cover_letter_name()`). While facts.json still holds placeholders
(`[FIRST_NAME]`...), `apply_queue.py run`/`add` and `indeed_apply.py` refuse with a clear
"candidate not set" error; fill the facts first (`/setup`).

**Status: experimental (built 2026-09-23).** What has been measured is below. Treat the
untested parts as untested.

## Submit policy: a person clicks Submit unless the candidate opted in

**Default: the queue never clicks the final Submit.** It fills and gates every page up to
"Review your application", then (`indeed_apply.py --human-submit`):
- unattended runs park the item as `awaiting_submit_click`;
- `run --present` scrolls Submit into view, alerts, and waits up to 15 minutes for a person to
  click it in the browser window, then records `submitted` from Indeed's confirmation text.

**Auto-submit is opt-in, per candidate.** Only when facts.json carries
`"automation": {"auto_submit": true}` (`candidate.auto_submit_opted_in()`) does `apply_queue.py`
drop `--human-submit` and let the script click Submit once every gate passes. Record the opt-in
only on the candidate's own explicit say-so in chat, with the date. A new candidate has not
opted in, and an opt-in never carries over from one candidate to another.

ZipRecruiter 1-Click (`tools/zip_apply_queue.py`) has no review page: the click is the
submission. Without the opt-in, `zip_apply_queue.py run` refuses to run.

## Where the browser runs

Chrome opens as a normal window on the user's own screen. When something needs a person (a captcha, a sign-in, a consent, the final Submit), the tool plays a sound where it can and prints one `NEEDS_HUMAN: <reason> | <detail>` line on stderr, at most every 5 minutes per reason; `apply_queue.py` forwards those lines to its stdout with the application folder appended. **Run login and queue runs as background tasks, watch for `NEEDS_HUMAN`, and tell the user in chat each time one appears.**

Running on a machine with no screen (a server driven from a phone) is supported but optional: see `docs/remote-server.md`.

## Form-filling rules

- **Phone format** (2026-10-10): phone fields are filled as **digits only**, including Indeed's dash-formatted pre-fills.
- **Consents, e-signatures, attestations and screening answers are the candidate's rules, recorded in `answers_base.json` and facts.json `screening`.** The code accepts none on its own: a consent or attestation is filled only when an `answers_base.json` rule the candidate approved covers it; anything else goes to the candidate. No such rule ships by default. A candidate who wants the queue to e-sign for them says so explicitly; only then add the rules (a signature rule answering `{name.first} {name.last}` and a date-signed rule answering `Today`), with a dated note recording their approval. Until the candidate has set those rules, expect every consent, e-sign and screening category (criminal record, driving, equipment, hours, pets...) to be handed off or escalated. Marketing and job-alert opt-ins always stay unticked.
- Labels over 300 chars keep their tail so an AI-review opt-out clause is visible.
- The phone **country-code picker** Indeed labels "Phone number" is left alone (it caused 17 hand-offs).
- **Date fields** (2026-10-10): a date box whose answer has no digits ("Immediately") gets today's date as MM/DD/YYYY.
- **State eligibility can hide inside the form.** One employer (2026-10-10) said nothing in the posting but asked "This job is only available in the following states: ..." on its questions page. Answer truthfully from facts.json; withdraw such postings when the candidate's state is not listed rather than submitting.
- A Chrome profile cannot be copied between machines (cookies are locked to the OS account); each machine has its own profile, signed in to the **candidate's** Indeed account in the Chrome window (`/indeed_login`). The profile signed in before 2026-11-04 is the previous candidate's account: sign it out and sign the current candidate in before any run.

## Employer messages (2026-10-10)

After an application is submitted, the queue sends a short message of interest to the employer through Indeed's own messaging. `/apply` drafts it (`documents/applications/<stem>/indeed_message.txt`, rules in `job-application-assistant/10-indeed-message.md`); `tools/indeed_message.py` sends it; `apply_queue.py` decides when. The message goes out in the candidate's name, so it is part of what the candidate approves; `--no-messages` or `message-skip` turns it off.

**Measured 2026-10-10 (read-only probe of an account):**
- The control is a per-application link on **My Jobs > Applied** (`https://myjobs.indeed.com/applied`): `a.atw-JobMetadata-contentLink`, text "Message this employer", href `https://messages.indeed.com/conversations/new?...&token=<token>`. Nothing on the job page or the post-submit confirmation page offers it.
- **Employer-gated: 2 of 66 (later 2 of 100) applied cards had the link.** Most employers never offer it, so "no link" is the normal outcome. Both links sat on applications a few days old and none of the same-day ones had it, so a pending message is re-checked on later runs (`MESSAGE_MAX_CHECKS = 6` looks, within `MESSAGE_WINDOW_DAYS = 14` of submission) before it is marked `unavailable`.
- `myjobs.indeed.com` loaded without a challenge while `www.indeed.com` and `messages.indeed.com` were on Cloudflare "Additional Verification Required". **The compose page itself is unmeasured:** `indeed_message.py` uses generic locators (a visible textarea or contenteditable box; a button named "Send") and records the page (`compose_page` in `message_report.json`, `msg_01_compose.png`) on every attempt. **Treat the first live send as a test:** run `python tools/apply_queue.py message --present` with the user on the viewer, read the report, and fix the locators from it if the status is `no_compose_box` or `no_send_button`.
- The list renders 66 cards and loads more on scroll (100 after 8 scrolls); the scan stops early once the wanted card is found, and new submissions sit at the top.

**How it runs:**
- `apply_queue.py add` records `message: pending` when the folder holds `indeed_message.txt`, else `skipped`. An item queued before this existed becomes `pending` the moment a message file is drafted for it, so messages can be written after the fact for applications already submitted (`/apply` on the posting again, or write the file by hand to the rules).
- **At the end of every `run`** (and on `apply_queue.py message`), one pass: a single look at the Applied list for every submitted application with a pending message, then one `indeed_message.py send --send --confirm-token <jk>` per application that has a link, with the usual 30-90 s pacing. `--no-messages` skips the pass.
- **Never twice:** before typing, the sender checks whether the thread already shows the message's first 60 characters (`already_sent`). A `send_click_error` or `send_unconfirmed` is marked `failed` and never retried automatically; check the thread on Indeed and run `message-retry <stem>` if it really did not go.
- Cloudflare on the compose page parks the message as pending (`tries` +1; `failed` after 3); with `--present` it rings `NEEDS_HUMAN` and waits like a captcha.
- `status` shows `msg: <state>` on every submitted item; `message-skip <stem> [why]` and `message-retry <stem>` adjust it. On a send the tracker row gets "Employer message sent on Indeed <date>." appended; the run summary has an "Employer messages (Indeed)" section.
- Same footing as everything else here: personal use, low volume, and the message is the candidate's own drafted words, reviewed before it is queued.

## Workflow (queue runner, since 2026-09-28)

The candidate approves postings **in chat**. Everything after that approval runs without them,
except captchas (the user), signatures, consents, guarded questions and, without the
auto-submit opt-in, the final Submit click.

1. **Draft and review.** Run `/apply` (drafter, then an independent reviewer) for each approved
   posting. Stage `documents/applications/<stem>/` with exactly one
   `<First>_<Last>_Resume_<Token>.pdf` (`<First>_<Last>` = `candidate.file_prefix()`, from facts.json),
   `cover_letter.txt`, `job_posting.md` and `indeed_message.txt`, the employer message sent
   after submission (see "Employer messages" above). Check that the CV uses the header from
   `05-cv-templates.md`.
2. **Check it is Indeed Apply:** `python tools/indeed_apply.py <jk> --out <tmp> --check-only`.
   Company-site postings are drafted for the candidate to send by hand; they never enter the queue.
3. **Queue it:** `python tools/apply_queue.py add <jk> <stem>`. It refuses the item unless the
   folder holds exactly one `<First>_<Last>_Resume_*.pdf` and the letter. A per-posting
   `indeed_answers.json` is optional: `answers_base.json` plus auto-answer cover most forms. Add
   one only for a posting-specific fact the candidate has confirmed.
4. **Run it:** `python tools/apply_queue.py run --until-done` (or `tools/run_apply_queue.cmd`,
   also usable from Windows Task Scheduler). `--until-done` sleeps until scheduled retries and keeps
   going for up to 6 hours. Per-posting `indeed_answers.json` files hold ONLY that posting's
   extras; the current `answers_base.json` is always loaded after them. This is unattended:
   - one pass per application;
   - randomized 30-90 s spacing between applications (2026-10-10; it was 3-8 s from 2026-10-09, and 60-150 s before that);
   - **one Chrome per run** (2026-10-10): `tools/indeed_browser.py` holds the signed-in profile open on a local DevTools port, and each application attaches to it (`INDEED_CDP_URL`), works in its own tab and closes only that tab. A Cloudflare "Verify you are human" check ticked once then covers the rest of the batch. The runner restarts the holder if it dies, falls back to one Chrome per application if it cannot start, and `INDEED_SHARED_BROWSER=0` turns it off;
   - Chrome runs visibly (`--foreground`);
   - anything needing a person is **parked** with a reason, and the run continues. Without the auto-submit opt-in that includes every application that reached its review page (`awaiting_submit_click`);
   - block pages and transient errors retry automatically and immediately, after only the normal pacing gap, up to 3 times (`BACKOFF_MIN = 0`, set 2026-09-28; it used to be 30 min x retry count);
   - a posting whose Indeed button reads "Apply on company site" is parked as `company_site_only` with no retry, and needs a `form_answers.md` packet;
   - expired postings are withdrawn;
   - on submit, the tracker and `seen_jobs.json` are updated automatically;
   - at the end of the run, the employer-message pass sends every pending `indeed_message.txt`
     whose employer offers "Message this employer" (see "Employer messages" above).
5. **Read the summary** at `documents/applications/_runs/<timestamp>.md`: what went out, what
   is parked and why, and every auto-answer with its reason. Skim the auto-answers. Fold any fact
   the candidate corrects into `01-candidate-profile.md` and, where it recurs, into `answers_base.json`.
6. **Clear parked items with a person present:** `python tools/apply_queue.py run --present`.
   Parked items then get the alert and a 15-minute hand-off each: captchas (the user),
   consent boxes and questions only the candidate can answer (relayed to the candidate), and the
   Submit click on review-ready items. `apply_queue.py status` shows the queue. Use `retry <stem>`
   and `withdraw <stem> [why]` to adjust it.
7. **Update the board after every run** (2026-10-14). The queue is followed on the
   "Indeed auto-apply" page of the candidate's one applications board, not in the run summary.
   Once the run (or a `message` pass) ends: `python tools/apply_board.py build --out <scratchpad>/apply_board.html`,
   then publish it to the URL in `documents/applications/apply_board_url.txt` (`Artifact read` it
   first in a new conversation; omit `capabilities` and `icon`; pass the build's `.files.json` map as
   `files` so the resume PDFs stay attached). Give the link with the counts:
   submitted, queued, awaiting Submit, messages sent. Parked items leave page 2 and appear on To send
   with the reason in the note (page 2 is only what the queue is working through); name them in
   chat as well. When the candidate applies to a parked item by hand, `withdraw` it from the queue.
   The same applies to `tools/zip_apply_queue.py` (ZipRecruiter 1-Click, its own state file
   `zip_queue.json`, and only with the auto-submit opt-in): the board's page 2 shows both queues,
   labelled by portal.

Usually this skill is entered from `/super-long-scrape` Phase 9, after the candidate says yes to
queueing that run's Indeed Apply drafts. That yes is the in-chat approval Rule 1 requires.

Never run two runners, or a runner and `indeed-personal`, at once. The runner holds
`documents/applications/.apply_queue.lock`. If a crash leaves the lock behind, delete it only
after confirming no runner is active.

## Where answers come from (stage 2, 2026-09-28)

Priority order, in `indeed_apply.load_answers`:
1. **The posting's own extras**, in `documents/applications/<stem>/indeed_answers.json`. Optional; typically just a pay figure.
2. **`answers_base.json`.** Question patterns whose answers are `{placeholders}` filled from **`facts.json`**, the single source of truth for name, contact, address, work authorization, education, availability, self-ID and pay rules. **To change a fact (for example the zip), edit `facts.json` only.**
3. **`learned_answers.json`.** Earlier auto-answers, reused so a repeat question skips the Claude call. Start it empty for a new candidate: a previous candidate's learned answers are their facts, not this one's.
4. **`tools/auto_answer.py`.** Claude answers what is left, for the candidate named in facts.json, from `facts.json`, CLAUDE.md, `01-candidate-profile.md` and the posting. Reusable answers are saved to step 3.

**Never learned:** pay questions, free text (textareas, or text over 60 characters), questions naming the posting's company, and guarded categories.

**Auto-answer rules** (`tools/auto_answer.py`, 2026-09-28; headless `claude -p`, model `claude-sonnet-5`, on the user's plan; no API key):
- **Guarded, never sent to Claude:** signatures, attestations, consents, terms and privacy notices, AI-review opt-outs, and termination, criminal-history and reason-for-leaving questions.
- **Honesty:** skills and experience the profile does not record get the truthful negative ("No", "0", "Less than 1 year"); years in a title never held get the lowest option.
- **Pay:** only by the candidate's own rule in facts.json `pay`. With no rule that settles it, Claude returns ESCALATE and the question goes to the candidate. Indeed's pre-filled pay figures (carried over from other postings) are never kept.
- **Logging:** every auto-answer and its reason is logged in the run report under `auto_answers`. Audit these after each batch. `--no-auto-answer` turns it off.

**Reviewing the store:**
- `python tools/apply_queue.py learned` lists every entry.
- `learned N --set "X"` corrects entry N, marking it human-confirmed.
- `learned N --confirm` confirms it as is; `learned N --disable` stops it being used.

Each run summary lists the newly learned answers so a bad one gets caught before it spreads.

## What the per-application pass does (`tools/indeed_apply.py --submit`)

- **Résumé:** always uploads the staged résumé fresh (a same-named older upload once went out
  stale).
- **Answers:** fills questions from `answers_base.json` (and the posting file). It asks
  `tools/auto_answer.py` for anything unmatched, and never trusts Indeed's pre-filled pay.
- **Pre-submit gates:** it will not reach Submit unless:
  - the review page's "Full name" equals facts.json `name.first` + `name.last` exactly
    (whitespace-collapsed, case-insensitive), correcting it via Contact information > Edit if
    needed. An unverified name is never sent: with a person present it alerts and waits for the fix,
    otherwise it parks as `name_not_verified`;
  - the review page shows exactly this posting's `<First>_<Last>_Resume_*.pdf`;
  - the cover letter is replaced with ours (or the posting offers no slot).
- **Captcha:** unattended, it parks; with `--present`, it rings and waits for the user.
- **Submit:** a person clicks it (`--human-submit`), unless the candidate opted in to auto-submit.
- **After Submit:** confirmation text means `submitted`. "Complete these steps before your
  application is submitted" (AI recruiter questions) means `needs_followup_steps`, which the
  candidate handles within the hour.

## Measured history (2026-09-23 to 2026-10-14, previous candidate's runs)

Technical findings from the first candidate's runs. They describe Indeed's behavior and the
script's fixes, and hold for any candidate.

**Flow (measured 2026-09-23, never submitted):**

| Step (`/form/<module>`) | What it is | Handled by the script |
|---|---|---|
| `contact-info-module` | First/last name, phone, city | Yes. Filled from `answers_base.json`; tested |
| `profile-location` | Street address, zip (optional) | Left blank when optional |
| `resume-selection-module/resume-selection` | "Add a resume". **Defaults to the most recently uploaded CV** | `--resume` uploads the tailored PDF (this adds a file to the candidate's Indeed account) |
| `resume-module/structured-data-intro` | "Highlight details from your resume" | Advances; tested |
| `resume-module/structured-data-review` | Indeed's parse of the resume into jobs and education | Advances without editing; tested |
| employer questions (varies) | Screening questions | Answers from the answers file; stops on anything it cannot ground |
| `review-module` | "Review your application", **Submit your application** | Stops here in prepare mode and in human-submit mode |

The flow step order is **not fixed**: walk by what each screen shows, not by a hard-coded
sequence. Each page carries a reCAPTCHA notice.

**Permission:** the allow rule is `Bash(python tools/indeed_apply.py:*)`. Run the script exactly
as `python tools/indeed_apply.py ...` so it matches.

**Hazards: read before every run.**
- **Indeed pre-fills from the account's Indeed profile, and pre-fills are not trustworthy.** Seen: a stale street address and zip from before a move; a garbled stored name (one letter of the surname replaced); the home address in a work-history "Company Address" field; values left over from an earlier run. The script never trusts a pre-fill silently. It reports `prefilled_kept` and `prefilled_needs_review`, and both lists are shown to the candidate before any submit.
- **Drafts persist.** Anything the script types stays in Indeed's saved draft for that posting, even though nothing is sent. A wrong fill has to be corrected before submitting.
- **Custom employer forms are flat.** Employment, education and references blocks have no detectable section headings. The script stops auto-filling at the first `Company Name` / `Position Title` / `School Name` style field on a page, and treats everything after it as the candidate's to fill or confirm. (A first run with loose substring matching put the phone number into Company Phone and the home city into a work-history City; fixed with whole-label matching and the history-block rule.)
- **Signatures and attestations** ("First and Last Name", "Today's Date", "I Agree", "read and understand") are never filled from a pre-fill, and filled at all only by a rule the candidate approved.
- **Indeed's default CV is whatever was uploaded last.** Always pass `--resume`. A retry once resumed straight onto the review page with another posting's CV attached; the script now refuses (`wrong_resume`) unless the review shows exactly this posting's file.
- **Indeed pre-selects the last cover letter typed in any earlier application.** Always pass `--cover-letter <plain .txt>`: the script replaces the text box's contents and verifies them; with no letter it chooses "Apply without a cover letter"; the pre-fill is recorded as `prefilled_cover_letter_start`.
- **Not every posting offers a cover letter slot.** The script reports `cover_letter_attached: "not offered by this posting"`; say so in the review packet.
- **Supporting documents and part of the review page render inside an `about:srcdoc` frame.** Locators search all frames.
- `--cover-letter-pdf` also uploads the PDF under "Additional documents" (usually redundant). `--remove-supporting` clicks only the Remove directly below the named file.
- **Confirmation text** reads "Your application was submitted to <Company>"; the check reads all frames.
- **A disabled Submit is a reCAPTCHA at the bottom of the review page.** After ~30 automated passes in one day, Submit rendered disabled with no visible reason. The script detects it (`submit_disabled`), alerts, brings Chrome forward and waits up to `--captcha-wait` (900 s) for the user to solve it. **Never try to solve or evade a captcha.**
- **Never `time.sleep()` while a person is using the window.** With the sync API a blocked thread stops the driver servicing Chrome. All waits use `page.wait_for_timeout()`.
- **Knockout screen** ("It looks like you don't meet these employer requirements", `/questions-module/intervention`): the script stops with `requirements_not_met` unless `--apply-anyway` is passed, which it gets only on the candidate's explicit say-so. The optional "Reason for applying" box after "Apply anyway" is left blank (the candidate's own words).
- **A transient "no Apply now button"** happened right after a force-stopped session. Re-check once before concluding a posting applies on the company site.
- **Pacing:** every prepare run is a full pass through the form, and passes add up. Prepare once, check the packet, then submit. Spread applications across days. Block pages and empty job pages are frequent after several runs in a row; a blocked run stops before the form, so nothing is sent.
- **Radio answers are clicked by id within the question's own `name` group** and checked to confirm they stuck (`get_by_label("No").first` once answered the wrong question).
- **Custom dropdowns** (`[role=combobox]`, `aria-haspopup=listbox`, hidden native `<select>`) are read and filled by option text; their options are listed on a hand-off.
- **Loops stop:** inline errors that are not `role=alert` are read from error-styled text, and a second failure to advance is `validation_error`.
- **Option matching ignores punctuation** ("US Citizen" matches "U.S. Citizen"); `norm_label` strips trailing whitespace (including non-breaking), `*`, `:` and `?`.
- **A Submit click that raises does not prove nothing was sent.** Status `submit_click_error` ("may have been sent"); a job page whose button reads "Applied" returns `already_applied`. **Never re-submit after a click error without that check.**
- **"Preparing review" spinner:** the script waits up to 45 s for Submit before calling it `unexpected_screen`.
- **AI recruiter follow-up:** after Submit, Indeed may require AI-recruiter questions within an hour (`needs_followup_steps`). These are the candidate's own answers; the script alerts and stops.
- **A bare yes/no answer never goes into an essay box** (an "allow AI review: Yes" rule once matched "How do you currently use AI tools?").
- **"Review your resume" variant:** shows the last upload with a **Use a different resume** button; the file chooser appears only after that click, and `upload_resume` handles it.
- **Typing names:** a one-shot `fill()` on Indeed's React input garbled a stored surname; the name fix clears by keyboard, types slowly, reads back and retries. The name comparison is exact (whitespace-collapsed), because a whitespace-blind check once let a name with a stray space go out.

**Not yet tested:** hand-off for signatures and dates on a custom employer form.

## Rules

1. **Only postings the candidate approved enter the queue.** They approve in chat; the queue never
   adds postings on its own. Approval of a reviewed application with clean gates covers filling it;
   the final Submit click is a person's unless the candidate has opted in to auto-submit
   (facts.json `automation.auto_submit: true`). The current candidate has not opted in.
2. **Answers are truthful and grounded in the profile.** Unrecorded skills get the negative.
   Guarded categories (signatures, consents, termination, criminal history, reason for leaving)
   go to the candidate unless a rule they approved covers them.
3. **Never click Submit twice.** A Submit click error parks the item. The retry first checks
   whether Indeed already shows "Applied".
4. **A person solves every captcha** (the user). Nothing here evades bot
   detection. The pacing gap exists to avoid tripping it.
5. **Leave marketing opt-ins unticked.**
6. **Automated applying breaks Indeed's Terms of Service.** Personal use, low volume, same
   footing as `indeed-personal`.
