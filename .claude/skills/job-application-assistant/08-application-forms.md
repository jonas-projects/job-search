---
framework_version: 1.0.0
---

# Application Form Fields

`/apply` produces two artifacts: a CV and a cover letter. Many applications need a **third** — free-text fields typed directly into an application portal. Graduate programs, large-employer ATS systems and startup forms routinely ask for things neither document covers, under a character or word limit, in a box with no formatting.

This file governs that third artifact. It is not a document you compile; it is text the candidate pastes.

## When this applies

Trigger it whenever a posting or portal asks for any of:

- A self-introduction / personal statement / "tell us about yourself" paragraph
- Structured project entries (project name, role, start and end date, description)
- A short pitch under a hard character limit ("stand out in 140 characters", "why you, in one sentence")
- Motivation questions ("why this company", "why this program")
- Competency questions with a word cap ("describe a time you…", 200 words)

## The rule that governs everything here

**Every claim in a form field must already be defensible from the same sources the CV and cover letter are grounded against** — the union of `01-candidate-profile.md`, the master CV (`cv/main_example.tex`), and `CLAUDE.md`'s Candidate Profile section, with a claim grounded if ANY of the three supports it. The interviewer reads the form alongside the CV. A form field is not a place to introduce new claims, inflate scope, or fill space — it is a place to *select* from what is already true and arrange it for the question asked.

All accuracy rules from `05-cv-templates.md` and `03-writing-style.md` apply unchanged.

## Field type: self-introduction paragraph

Usually 100–200 words, one paragraph, no formatting.

**Structure that works:**
1. Current status — what they are doing or completing now
2. The single strongest piece of evidence, with its number and scale
3. One line of trajectory: how they got here, if a pivot or specialisation is genuinely interesting
4. What they want next, connected to this employer's actual work

**Rules:**
- **Lead with the strongest evidence, not chronology.** A career history told in order buries the best material when the strongest work is recent.
- **Write one version per role type, not one for all applications.** The same history framed for a backend role and a data role are different paragraphs. Produce both, label them, and say which goes where.
- **Tie it to this employer in the final sentence.** Generic self-introductions are the default and read as such.
- **Count the words and state the count.** Portals truncate silently. Supply a trimmed variant and name which sentence to cut first.

## Field type: structured project entries

Typically **project name, role, start date, end date, description.**

**Project name.** Give the project a descriptive name, not the employer's name — "Warehouse Inventory Forecasting Platform" is a project, "Acme Corp" is an employer. Where a client is more recognisable than the employer, name the client only if the relationship is truthful (placed on-site with, delivered to).

**Role.** The candidate's role *on that project*, which may be narrower than their job title. Do not upgrade it.

**Dates.** The dates they worked on **that project**, which are not automatically the employment dates. If a role spanned two years but the named project occupied the later part, saying so is both more accurate and avoids the low-output reading described in `05-cv-templates.md` ("Check tenure against visible output"). Only narrow the dates when the candidate can say when the project actually started — never invent a boundary to improve the ratio.

**Description.** 100–150 words: what the system did and who used it, then the hardest technical problem and how it was solved, then the outcome with its number. Supply a **~60-word short version** as well; portals vary and the candidate should not have to improvise a cut.

**Scope discipline is stricter here than on a CV.** A CV bullet can be terse enough to be ambiguous about ownership. A project entry with the candidate's name and role attached reads as ownership of the whole thing. Where they contributed rather than owned, say so inside the description.

## Field type: hard character limits

These reward **a specific situation over an adjective**. Most applicants submit adjectives — "passionate", "fast learner", "team player" — so a concrete situation stands out by contrast.

**Method:**
1. Pick the single most distinctive true thing: usually a number, an unusual combination of backgrounds, or a problem shape that maps onto the employer's own work.
2. Draft 4–6 candidates at different angles.
3. **Count characters programmatically. Do not estimate.** Over-limit text is truncated mid-word.
4. Present all candidates with counts, recommend one, and say why.

Prefer the version that **maps the candidate's problem onto the employer's problem**, where a truthful mapping exists. That is what "stand out" is actually asking for.

## Output format

**Default: answer in chat, not in a file (set 2026-09-14).** Screening questions, short free-text fields, and one-off portal questions get drafted **directly in the conversation**, formatted so each answer can be copied straight into the form. The reason: it is "less work for me than going and finding a file you created." The user is filling the form in a browser while reading the reply, so a file path is friction, not delivery.

**Write a `.txt` file only when** the fields are numerous or structured enough that they will not survive a chat reply (a long application with many separate boxes, structured project entries with dates, or something the user wants kept alongside the CV and cover letter), **or when asked for a file**. When in doubt, answer in chat and offer the file.

### Background drafting pipeline: questions answered up front

When applications are drafted in the background (`.claude/skills/apply-automate/briefs/drafter.md`, step 6), answering the application questions is part of preparing the CV and letter, not an optional extra.
- `python tools/form_questions.py <ATS url>` lists the live form's questions for Ashby, Greenhouse and Lever.
- Answers go to `application_answers/<stem>/` at the repo root (gitignored). This supersedes the Downloads rule below for background-drafted applications:
  - `_questions.txt`: every question with its answer, a pointer to a file, or `CANDIDATE` where only the candidate can answer;
  - one paste-ready `NN_<slug>.txt` per written answer, never hard-wrapped.

The interactive rules below (chat first, clipboard) still apply when the user is filling a form live with Claude.

### STANDARD PROCESS for follow-up / screening questions

This is the default for any posting that asks free-text questions beyond the CV and cover letter. Do all four, in order:

1. **Write one `.txt` file per employer** to the user's Downloads folder as `<company>_application_answers.txt`. Not the repo - see below for why.
2. **Do NOT hard-wrap the prose.** Each paragraph is **one single long line**, however long that runs; separate paragraphs with a blank line. The file is read with word wrap on and pasted paragraph by paragraph into a browser form. Hard-wrapped text pastes with newlines mid-sentence and has to be cleaned up by hand, which is the whole problem this avoids. Section rules (`====`) and numbered note headings may be their own short lines; running prose may not.
3. **Structure it:** a short header (employer, role, date, posting URL, salary, deadline), then each question verbatim as a heading with its **word count**, then the answer, then a clearly marked `NOT FOR PASTING - notes to self` section.
4. **Summarise in chat too**, briefly - what each answer leads on and why - so the user can approve without opening the file. The file is for pasting; the chat is for judging.

**What goes in the notes section**, and it is as valuable as the answers: gaps deliberately omitted from the answers and why, the prepared pivot if a screener raises one, any behavioural trap in the posting worth rehearsing, and an explicit split of what was **verified** versus what was **not** and therefore left out.

**When a file IS the ask, put it in the user's Downloads folder.** The objection was never to files as such, it was to "going and finding a file you created" buried in the repo. A file in Downloads is two clicks from the browser the form is being filled in; a file under `documents/applications/<slug>/` is a navigation errand. Name it for the company so it is obvious in a folder full of unrelated downloads, and do not also archive a copy into the repo unless asked - the tracker row already records that the answers exist. (Use the user's own Downloads folder for their operating system: `~/Downloads` on macOS and Linux, `%USERPROFILE%\Downloads` on Windows.)

### Deliver via the clipboard, not the scrollback

**Chat output is for reading; the clipboard is for pasting.** Copying an answer out of a terminal breaks it: the terminal hard-wraps lines at the window width so the paste arrives with newlines mid-sentence, and Markdown decoration (`>` blockquotes, `**bold**`, smart quotes) comes through as literal characters or mangled spacing.

**So put each answer on the clipboard directly, one at a time:**

```bash
cat <<'ANSWER' | clip.exe
<the answer text, plain ASCII, no Markdown, paragraphs separated by blank lines>
ANSWER
```

- `clip.exe` is the Windows and WSL clipboard. On macOS pipe to `pbcopy`; on Linux to `wl-copy` or `xclip -selection clipboard`.
- Use a **quoted heredoc** (`<<'ANSWER'`) so the shell does not expand `$`, backticks or backslashes in the text.
- **Plain ASCII only.** No `**bold**`, no `>` quoting, no curly quotes, no em dashes. The destination is a plain textarea.
- **One answer per call.** Announce which one is loaded and wait; the clipboard holds exactly one thing, so batching defeats the purpose.
- **Verify it landed** with `powershell.exe -NoProfile -Command "(Get-Clipboard -Raw).Length"` and report the character count. It is a cheap confirmation, and character counts matter anyway when a field has a limit.
- Still show the answer in the chat reply as well, so the user can read and approve it before pasting. The clipboard is the delivery mechanism, not a substitute for showing the work.

`clip.exe` works from Windows and from WSL. On other hosts the equivalents are `pbcopy` (macOS) and `xclip -selection clipboard` / `wl-copy` (Linux); check which exists before relying on it.

When a file IS warranted, save it as a plain `.txt` the candidate can copy from, alongside their other application material for that employer. One file per employer, containing every field that employer asked for.

Include:
- A header naming the employer and the roles it covers
- Each field, labelled, with word or character counts stated
- Short variants where limits may be tighter than expected
- **`NOTE TO SELF` blocks** for scope reminders and prepared answers to questions the content invites — clearly marked as *not for pasting into the form*
- A dates quick-reference, so date fields stay consistent without re-deriving them

## Verification before handing it over

- [ ] Every factual claim traces to the union of `01-candidate-profile.md`, the master CV (`cv/main_example.tex`), and `CLAUDE.md`'s Candidate Profile section
- [ ] No claim contradicts the CV or cover letter submitted for the same role
- [ ] Ownership scoped correctly on contributory work
- [ ] Word and character counts measured, not estimated
- [ ] In-progress qualifications described as in progress
- [ ] `NOTE TO SELF` blocks clearly marked as internal
