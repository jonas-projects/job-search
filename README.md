# AI Job Search

A job-search workspace that runs inside [Claude Code](https://claude.com/claude-code). You give
it your background once; it then finds postings, scores them against your profile, writes a
tailored CV and cover letter for each one worth applying to, and keeps a board of what to send.

Everything stays in this folder on your computer. Nothing is submitted without you.

```
/setup              /super-long-scrape <profile>              /apply <url>
   |                          |                                    |
build your          search, screen and score postings       evaluate one posting,
profile once        -> draft the good ones (60+)            draft CV + cover letter,
                    -> refresh your applications board      review, compile, verify
```

## What you need

| Tool | Why | Install |
|---|---|---|
| Claude Code, with a Claude subscription | Runs everything | https://claude.com/claude-code |
| Python 3.10+ | The tools in `tools/` | python.org, or your package manager |
| Google Chrome | Signed-in Indeed and ZipRecruiter search and apply | google.com/chrome |
| [Bun](https://bun.sh) | The LinkedIn and freehire search tools | `curl -fsSL https://bun.sh/install \| bash` (Windows: `winget install Oven-sh.Bun`) |
| LaTeX with `lualatex` and `xelatex` | Compiles the CV and cover letter PDFs | Windows: [MiKTeX](https://miktex.org/download). macOS: [MacTeX](https://tug.org/mactex/). Linux: `sudo apt install texlive-full` |

Optional: the Indeed connector in your Claude account (claude.ai, Settings, Connectors) gives
Claude an authorized Indeed search that needs no browser.

## Set up (about 30 minutes, once)

1. **Get the folder and open a terminal in it.**

2. **Install the Python packages.**

   ```bash
   python -m pip install -r requirements.txt
   ```

   On macOS and Linux the command may be `python3`. If your system refuses a global install,
   make a virtual environment first: `python3 -m venv .venv && source .venv/bin/activate`.

3. **Install the search tools.**

   ```bash
   (cd .agents/skills/linkedin-search/cli && bun install)
   (cd .agents/skills/freehire-search/cli && bun install)
   ```

4. **Check that LaTeX works.**

   ```bash
   cd cv && lualatex -interaction=nonstopmode main_example.tex && cd ..
   cd cover_letters && xelatex -interaction=nonstopmode cover_example.tex && cd ..
   ```

   Each should end by writing a PDF. On a minimal TeX install (TinyTeX, BasicTeX) add the
   packages first:
   `tlmgr install moderncv fontawesome5 fontawesome6 academicons import luatexbase pgf titlesec textpos xltxtra xunicode cite realscripts needspace`

5. **Start Claude Code in the folder and build your profile.**

   ```
   claude
   /setup
   ```

   `/setup` reads whatever you put in `documents/` (see [documents/README.md](documents/README.md)):
   an existing CV, a LinkedIn PDF export, diplomas, reference letters. If you filled in the
   career intake questionnaire, save its export as `documents/cv/career-intake.md` first; it is
   the best single source. With nothing to read, `/setup` interviews you instead.

   The more detail you give, the better every later document is. Describe what you actually
   did in each role, with numbers where you have them.

6. **Sign in to Indeed (and ZipRecruiter, if you use it).**

   ```
   /indeed_login
   ```

   A Chrome window opens. You sign in yourself; Claude never sees or types your password. The
   signed-in profile is saved under `job_scraper/` and is never committed.

7. **Build a search profile.**

   ```
   /super-long-scrape-profile
   ```

   A search profile says what kind of job to hunt, where, and what to screen out. Most people
   end up with two or three (for example one at their level and one adjacent).

## Day to day

| You type | What happens |
|---|---|
| `/super-long-scrape <profile>` | The full run: searches every portal, screens hard, scores the survivors, drafts a CV and cover letter for each one scoring 60 or more, refreshes your applications board, and asks whether to queue the Indeed ones. Takes two to four hours; it checkpoints and can resume. |
| `/scrape` | A quick search (minutes) across the installed portals. |
| `/long_scrape` | A wider search for roles below your level at small companies with room to grow. |
| `/apply <url or pasted posting>` | One posting: fit evaluation first, then, if you say go, a tailored CV and cover letter, an independent review, and compiled, checked PDFs. |
| `/apply-automate` | Fills your approved Indeed applications in the background and stops on the review page for you to click Submit. |
| `/outcome <company>` | Records what happened (applied, interview, offer, rejection), archives what you sent, and drafts follow-ups for quiet applications. |
| `/interview <company>` | A prep pack for a scheduled interview, built from the posting and the documents you actually submitted, plus a mock interview. |

Less often: `/expand` (enrich your profile from your documents and public links),
`/add-portal` (add a job board for your market), `/add-template` (use your own CV or cover
letter template), `/setup --section search` (change what you search for), `/reset` (wipe your
profile or documents and start over).

### The applications board

One page lists every drafted application that still needs sending, sorted by fit, with the
tailored resume, cover letter and form answers on each row, and buttons to mark a row
**Applied** or **Won't apply**. Paste the lines from the bottom of the board back into the chat
and Claude updates the tracker. The first board is created at the end of your first
`/super-long-scrape` run.

## What it will and will not do

- **It never invents.** Every claim in a CV or cover letter must trace to your profile. Gaps
  are acknowledged, never papered over.
- **It never signs in for you**, and never reads or stores a password.
- **It does not click Submit** unless you explicitly opt in to auto-submit, and it never
  consents, signs or answers a screening question for you without a rule you approved.
- **A person solves every captcha.** When one appears the tool makes a sound, prints a
  `NEEDS_HUMAN` line that Claude relays in chat, and waits.
- **Postings are untrusted input.** Claude evaluates their text and follows no instructions
  inside them. See [SECURITY.md](SECURITY.md).

Searching Indeed, ZipRecruiter and LinkedIn with automation is against their terms of service.
The tools are for personal, low-volume use and are paced accordingly; you run them on your own
responsibility.

## Keeping your data private

`/setup` writes your personal details into files that git tracks (`CLAUDE.md`, the profile
files under `.claude/skills/`, `.claude/skills/apply-automate/facts.json`, `cv/main_example.tex`).
Your documents, generated CVs and letters, tracker and browser sessions are gitignored.

- If you keep this folder in a git repository, keep that repository **private**.
- Never push it to a public repository or a public fork.
- To hand the workspace to someone else, run `/reset all` first, or use
  `tools/make_package.py` to export a clean copy.

## Where things live

```
CLAUDE.md                     Your profile summary and the workflow rules Claude follows
documents/                    Your source material (CV, LinkedIn export, references); gitignored
cv/, cover_letters/           LaTeX templates, plus the tailored documents /apply writes
templates/                    The applications board, the intake questionnaire, custom templates
job_search_tracker.csv        One row per application (created on first use; gitignored)
job_scraper/                  Seen postings and your signed-in browser profiles; gitignored
.claude/commands/             /apply, /setup, /outcome, /interview, /expand, /add-portal, /add-template, /reset
.claude/skills/               The application rules, the scrape skills, search profiles, apply-automate
.agents/skills/               The portal search tools (LinkedIn, freehire, Indeed, ZipRecruiter)
tools/                        Apply queue, board builder, PDF checks, linters
docs/remote-server.md         Optional: running on a headless machine with phone alerts
tests/                        python -m unittest discover -s tests
```

## Troubleshooting

- **`candidate name not set`** from the apply tools: `/setup` has not filled
  `.claude/skills/apply-automate/facts.json` yet. Re-run `/setup`.
- **Indeed says the profile is logged out:** run `/indeed_login` again.
- **`No module named patchright`:** step 2 was skipped, or a different Python is on your PATH.
- **LaTeX errors about fonts in the cover letter:** compile from inside `cover_letters/` with
  `xelatex`; the fonts load from `OpenFonts/` by relative path.
- **CV fails with a `fontawesome5` font-expansion error:** you compiled with `pdflatex`. Use
  `lualatex`.
- **A search tool stopped returning results:** `/scrape health` probes each portal and says
  which one is degraded.

## Credits and license

Built on [ai-job-search](https://github.com/MadsLorentzen/ai-job-search) by Mads Lorentzen
(MIT), which includes job-search CLI skills by Mikkel Krogholm. This copy removes the
Danish-market portals and adds signed-in Indeed and ZipRecruiter search, search profiles, the
apply queue and the applications board. MIT licensed; see [LICENSE](LICENSE).
