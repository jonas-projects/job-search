# Security notes

## Threat model, honestly stated

This is an agentic workflow: an LLM with file access reads untrusted web content (job postings)
alongside your personal data (CV, profile, application history). That combination is the main
risk, and it cannot be fully eliminated, only narrowed. What the workspace does about it:

- **Untrusted-input rules.** The scrape skills and `/apply` treat posting text as data, never
  instructions. Agents are told not to follow directions embedded in postings and not to fetch
  URLs found inside posting text (the posting URL you supply is the one exception). Reviewer
  research starts from the company identity you confirmed, never from links in the posting body.
- **Permission allowlist.** `.claude/settings.json` pre-approves only the specific commands the
  workflow needs. `python tools/security_guards.py` fails if that list is widened, if a hook is
  added, if a package manifest gains an install script, or if a personal-data gitignore rule is
  weakened. The allowlist governs shell commands; Claude's own web tools are outside its reach,
  which is why the instruction-level rules above exist.
- **Personal data boundaries.** Your documents, generated applications, tracker and research
  cache are gitignored. A signed-in browser profile under `job_scraper/` is a credential: never
  copy, share or commit it.
- **Applications are yours to send.** The apply queue stops before Submit unless you opted in,
  and never accepts a consent, signature or attestation without a rule you approved.

Instruction-level defenses raise the bar; they are not a sandbox. On a job board you do not
trust, review what Claude fetched and wrote before sending anything.

## Adding a portal tool from somewhere else

Portal tools run pre-approved on your machine against your career data. Before installing one
you did not write: read all of its code, check that its only network calls go to the job board
it claims to search, and run its tests offline (`bun test` in its `cli/` folder).
