# /apply reviewer brief (repo copy, 2026-09-28)

You are the REVIEWER, acting as a hiring-manager proxy, for ONE drafted application named in your prompt. Work in the repo root (your working directory). A different agent drafted it. Give an independent critique, then apply only the grounded fixes yourself.

Read "Current facts", "Sources of truth" and "Track framing" in `.claude/skills/apply-automate/briefs/drafter.md`. They apply to you too.

## Tasks
1. **Grounding audit.** Read `cv/main_<stem>.tex` and `cover_letters/cover_<stem>.tex`. Check every date, title, number, tool, platform, credential and company claim against `01-candidate-profile.md`, `cv/main_example.tex` and CLAUDE.md. Remove or soften anything unsourced.
   - Watch especially for inflated numbers or scope beyond 01's rules, titles the candidate never held, tools 01 marks as not used, and Claude Code outside the tools list.
   - Watch for anything the profile marks as sensitive (for example how a past role ended).
   - Apply every claim-specific caveat recorded in `01-candidate-profile.md` (which role a claim belongs to, which figure needs which qualifier).
1b. **Written answers** in `application_answers/<stem>/` (repo root): apply the same grounding audit to every `NN_*.txt` file and to `_questions.txt`. Also check:
   - each answer is within any stated length;
   - each file holds only paste-ready text;
   - the answers are consistent with the letter;
   - nothing guarded (signatures, consents, termination, references, a no-AI form) was answered instead of being marked CANDIDATE.

   If the posting has an Ashby, Greenhouse or Lever board URL in the tracker and the answers folder is missing, run `python tools/form_questions.py <url>` and write the answers yourself, per step 6 of the drafter brief.
1c. **Indeed message** in `documents/applications/<stem>/indeed_message.txt` (Indeed Apply postings only): the same grounding audit, plus `10-indeed-message.md`'s rules: 60-110 words, no links, no salary, no gaps, no sentence copied from the letter, nothing the profile marks sensitive, paste-ready plain text with no hard wraps. If the drafter skipped it on an Indeed Apply posting, write it yourself per step 5 of the drafter brief.
2. **Missing skills never appear on the CV.** Gaps are stated once, plainly, in the letter.
3. **Targeting.** Every stated requirement is addressed or honestly gapped. Use the posting's own terms where they are true. The opening leads with the candidate's strongest match.
4. **Style:** `03-writing-style.md` and `02-behavioral-profile.md`. Warm, direct, low ego. No em-dashes.
5. **Company claims:** at most 2 WebSearch/WebFetch calls, starting from the company NAME only. Verify each claim or cut it.
6. **Apply fixes** with the Edit/Write tools. Never write LaTeX through a heredoc or `python -c`. Then:
   - recompile (CV with lualatex twice, letter with xelatex);
   - run `verify_pdf` (CV 2 pages, letter 1 page);
   - read both PDFs;
   - delete the aux/log/out files;
   - re-copy the CV PDF over `documents/applications/<stem>/<First>_<Last>_Resume_*.pdf` (the one file already staged there; see the drafter brief, step 5);
   - rewrite `cover_letter.txt` to match the letter exactly.
7. Do NOT edit the tracker, seen_jobs.json, CLAUDE.md or skill files. Do NOT run any Indeed tool.

## Return (20 lines max)
- Grounding fixes (old -> new).
- Targeting and style fixes.
- Remaining gaps.
- Questions for the candidate.
- Verification results.
