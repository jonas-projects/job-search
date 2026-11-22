---
framework_version: 1.0.0
---

# Indeed Employer Message

Added 2026-10-10. For every posting that will be submitted through Indeed Apply, `/apply` writes a **fourth artifact**: a short message of interest that the apply queue sends to the employer through Indeed's own messaging **after** the application is submitted. It is drafted with the CV and letter, reviewed with them, and sent by `tools/apply_queue.py` (see `.claude/skills/apply-automate/SKILL.md`, "Employer messages"). Nothing here is sent by hand.

## When it applies

Draft it whenever the posting came from Indeed (the source URL is on `indeed.com`, or the application is headed for `/apply_automate`). Company-site postings get no message: there is no Indeed thread to send it in.

**Where it goes:** `documents/applications/<company>_<role>/indeed_message.txt`, next to `cover_letter.txt`. Plain text, paste-ready, nothing else in the file.

## What it is, and is not

A recruiter reads it in an Indeed inbox, usually on a phone, between dozens of other applicants' notes. It is not a second cover letter. It does one job: put a name, one concrete reason to open the CV, and an availability line in front of the person screening the pile.

- **60 to 110 words.** Count them.
- **Two short paragraphs at most**, plus greeting and sign-off. No bullets, no headings.
- **No links.** Not even the LinkedIn URL. Messaging filters and recruiters both read a link in a first message as spam; the CV already carries it.
- **No attachments mentioned**, no "please see my resume attached". The application is already there.

## Structure

1. **Greeting.** `Hi <Company> team,` or `Hi <Name>,` when the posting names the hiring contact. Never a guessed name.
2. **One sentence of context.** That the candidate applied for `<role>` through Indeed, and (only if it is verified and specific) the one thing about the company or role that drew them. No flattery, no "exciting opportunity".
3. **One sentence of evidence.** The single strongest match between the posting's main requirement and the profile, with a number where the profile has one. Pick the match the cover letter leads with, but say it differently: the recruiter may read both.
4. **Close.** Availability (as recorded in the profile) and that the candidate is glad to talk this week. Then `[YOUR_NAME]` on its own line.

Example, for a customer-success role at a small software company:

```
Hi Example Co team,

I applied for the Operations Coordinator role through Indeed today and wanted to add a short note. At my last employer I [ONE CONCRETE, VERIFIED ACHIEVEMENT FROM THE PROFILE THAT MATCHES THE POSTING'S MAIN REQUIREMENT, WITH A NUMBER WHERE THE PROFILE HAS ONE], so the core of this role is work I already do.

I am available [AVAILABILITY FROM THE PROFILE] and glad to talk any day this week.

[YOUR_NAME]
```

(Fill the bracketed parts from the profile; the finished message must land at 60-110 words.)

## Rules

- **Grounding.** Every claim traces to the same three sources as the CV and letter (`01-candidate-profile.md`, `cv/main_example.tex`, CLAUDE.md's Candidate Profile). The reviewer audits the message exactly as it audits the letter.
- **Consistent with the letter, never a copy of it.** Same facts, different sentence.
- **Never:** a salary figure, anything covered by the candidate's sensitive-handling notes in `02-behavioral-profile.md` (e.g. how a past role ended), where the candidate lives or their commute, a stated gap (gaps live in the letter only), em-dashes, cliches, hedging. `03-writing-style.md` applies in full.
- **Formatting for the file:** each paragraph is one long line, a blank line between paragraphs, no hard wraps, no quote marks, no "DRAFT" label, no Markdown. The queue types the file verbatim into Indeed's message box.
- **Tone:** warm, direct, low ego. It should read like something the candidate typed in two minutes, not like a template.
- **Posting language:** English only; this is an Indeed US feature.

## What happens after drafting

`apply_queue.py add` records the message as `pending` when the file exists. After the application is submitted, the queue looks for the employer's "Message this employer" link on My Jobs > Applied and sends the file's text when the link is there. **Most employers never offer the link** (measured 2026-10-10: 2 of 66 applications), so "no message sent" is the normal outcome and not a defect in the draft. The run summary lists what went out.
