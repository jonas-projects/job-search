#!/usr/bin/env python3
"""Export a clean copy of this workspace for another person.

    python tools/make_package.py <target-dir>

The copy holds only tracked files from the current commit (so no documents, generated
applications, tracker, browser profiles or other gitignored data), with CLAUDE.md's candidate
header restored to placeholders, and a fresh single-commit git history. It refuses to finish
if anything in the copy still looks personal. Stdlib only.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BLANK_HEADER = """# Job Application Assistant for [YOUR_NAME]

<!-- SETUP: This file is populated by running /setup -->
<!-- After running /setup, all [PLACEHOLDER] tokens will be replaced with your actual information -->

## Role
This repo is a job application workspace. Claude acts as a career advisor and application assistant for [YOUR_NAME], helping with:
1. **Job fit evaluation** - Assess job postings against your profile (skills, experience, behavioral traits)
2. **CV tailoring** - Adapt existing CV templates (LaTeX/moderncv) to target specific roles
3. **Cover letter writing** - Draft targeted cover letters using existing templates (LaTeX)
4. **Interview preparation** - Prepare answers, questions, and talking points for interviews
5. **Career strategy** - Advise on positioning and personal branding

The candidate is the person named below. Every profile fact, answer, CV and cover letter must come from them; never fill a gap by guessing.

## Candidate Profile

<!-- This section is auto-populated by /setup. You can also fill it in manually. -->

### Identity
- **Name:** [YOUR_NAME]
- **Pronouns:** [YOUR_PRONOUNS]
"""

# Files that must still carry a placeholder, as proof the profile in them is blank.
MUST_BE_BLANK = {
    "CLAUDE.md": "[YOUR_NAME]",
    "cv/main_example.tex": "\\name{[First]}{[Last]}",
    "cover_letters/cover_example.tex": "[YOUR NAME]",
    ".claude/skills/job-application-assistant/01-candidate-profile.md": "[YOUR_EMAIL]",
    ".claude/skills/job-application-assistant/04-job-evaluation.md": "[YOUR_PRIMARY_SKILLS]",
    ".claude/skills/apply-automate/facts.json": "[NAME_FIRST]",
}


def fail(msg: str) -> None:
    sys.exit("make_package: " + msg)


def git(*args: str, cwd: str = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def blank_claude_md(text: str) -> str:
    marker = "- **Location:**"
    if marker not in text:
        fail("CLAUDE.md has no '- **Location:**' line; cannot find the end of the identity header")
    return BLANK_HEADER + text[text.index(marker):]


def personal_terms() -> list[str]:
    """Names that must not appear in the copy: the git author and whoever CLAUDE.md names."""
    terms = set()
    for key in ("user.name", "user.email"):
        try:
            terms.update(git("config", key).split())
        except subprocess.CalledProcessError:
            pass
    head = open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8").read(4000)
    for m in re.finditer(r"\*\*(?:Name:\*\* |The (?:candidate|operator) is )([A-Z][\w'-]+(?: [A-Z][\w'-]+)+)", head):
        terms.update(m.group(1).split())
    home = os.path.basename(os.path.expanduser("~"))
    if home:
        terms.add(home)
    return sorted(t for t in terms if len(t) > 3 and not t.startswith("["))


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: python tools/make_package.py <target-dir>")
    target = os.path.abspath(sys.argv[1])
    if os.path.exists(target) and os.listdir(target):
        fail(f"{target} exists and is not empty")
    if git("status", "--porcelain").strip():
        fail("the working tree has uncommitted changes; commit them first (the copy is made from HEAD)")
    terms = personal_terms()
    os.makedirs(target, exist_ok=True)

    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")

    path = os.path.join(target, "CLAUDE.md")
    with open(path, encoding="utf-8") as fh:
        text = blank_claude_md(fh.read())
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    problems = []
    for rel, token in MUST_BE_BLANK.items():
        with open(os.path.join(target, rel), encoding="utf-8") as fh:
            if token not in fh.read():
                problems.append(f"{rel}: placeholder {token} is gone, so it holds someone's data (run /reset all first)")
    with open(os.path.join(target, ".claude/skills/apply-automate/learned_answers.json"), encoding="utf-8") as fh:
        if json.load(fh):
            problems.append("learned_answers.json is not empty")
    with open(os.path.join(target, ".claude/skills/apply-automate/facts.json"), encoding="utf-8") as fh:
        if json.load(fh).get("automation", {}).get("auto_submit"):
            problems.append("facts.json has auto_submit switched on")
    profiles = os.path.join(target, ".claude/skills/super-long-scrape/profiles")
    extra = [f for f in os.listdir(profiles) if f != "README.md"]
    if extra:
        problems.append(f"search profiles present: {extra}")
    pattern = re.compile("|".join(r"\b%s\b" % re.escape(t) for t in terms), re.I) if terms else None
    for base, _dirs, files in os.walk(target):
        for name in files:
            full = os.path.join(base, name)
            try:
                with open(full, encoding="utf-8") as fh:
                    body = fh.read()
            except UnicodeDecodeError:
                continue  # fonts and other binaries
            if pattern:
                for n, line in enumerate(body.splitlines(), 1):
                    if pattern.search(line):
                        problems.append(f"{os.path.relpath(full, target)}:{n}: personal term: {line.strip()[:100]}")
    if problems:
        fail("the copy is NOT clean, do not share it:\n  " + "\n  ".join(problems[:40]))

    git("init", "-q", "-b", "main", cwd=target)
    git("add", "-A", cwd=target)
    subprocess.run(["git", "-c", "user.name=ai-job-search", "-c", "user.email=noreply@example.com",
                    "commit", "-q", "-m", "Initial commit: clean job-search workspace"],
                   cwd=target, check=True)
    count = len(git("ls-files", cwd=target).splitlines())
    print(f"make_package: clean copy written to {target} ({count} files, checked for: {', '.join(terms) or 'no names'})")


if __name__ == "__main__":
    main()
