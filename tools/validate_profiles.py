#!/usr/bin/env python3
"""Validate /super-long-scrape search profiles against the schema in profiles/README.md.

Run with no arguments to check every profile:

    python tools/validate_profiles.py

Exits non-zero if any profile fails, so it can gate a commit. Checks structure only --
it cannot tell you whether a profile finds anything, which is what the yield log is for.
An empty registry (no profiles built yet for the current candidate) is valid: exit 0.
No particular profile is required to exist.
"""
import glob
import io
import os
import re
import sys

PROFILE_DIR = os.path.join(".claude", "skills", "super-long-scrape", "profiles")
VALID_SCORING = ("standard", "climb", "tutoring")
# out = remote or hybrid only (the default). in-metro = in-person seats in the candidate's metro.
# in-<area> (kebab-case) = in-person seats in a named area the candidate's profile defines.
VALID_ONSITE = ("out", "in-metro", "in-<area>")
ONSITE_RE = re.compile(r"^(out|in-[a-z0-9]+(-[a-z0-9]+)*)$")
VALID_LEVEL = ("entry",)
REQUIRED_SECTIONS = (
    "## Thesis",
    "## Query blocks",
    "## Portal routing",
    "## Screens",
    "## Scoring notes",
    "## Presentation",
    "## Yield log",
)


def field(frontmatter, key):
    m = re.search(r"^%s:\s*(.*)$" % re.escape(key), frontmatter, re.M)
    return m.group(1).strip() if m else None


def check(path):
    """Return (name, track_tag, [problems])."""
    base = os.path.basename(path)
    text = io.open(path, encoding="utf-8").read()
    problems = []

    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return base[:-3], None, ["no YAML frontmatter"]
    fm = m.group(1)

    name = field(fm, "name")
    scoring = field(fm, "scoring")
    tag = field(fm, "track_tag")

    if name != base[:-3]:
        problems.append("name %r does not match filename %r" % (name, base[:-3]))
    if scoring not in VALID_SCORING:
        problems.append("scoring %r is not one of %s (add a new rubric to README first)"
                        % (scoring, ", ".join(VALID_SCORING)))
    if not tag:
        problems.append("no track_tag")
    if not field(fm, "summary") and "summary: >" not in fm:
        problems.append("no summary")
    if field(fm, "enabled") not in ("true", "false"):
        problems.append("enabled must be true or false")
    if not ONSITE_RE.match(field(fm, "onsite") or ""):
        problems.append("onsite %r is not one of %s (remote or hybrid only is the default; any in-person value needs the candidate's say-so)"
                        % (field(fm, "onsite"), ", ".join(VALID_ONSITE)))
    level = field(fm, "level")
    if level is not None and level not in VALID_LEVEL:
        problems.append("level %r is not one of %s; add a new Phase 4 rule to the skill first"
                        % (level, ", ".join(VALID_LEVEL)))

    has_annual = re.search(r"^pay:", fm, re.M) is not None
    has_hourly = re.search(r"^pay_hourly:", fm, re.M) is not None
    if has_annual and has_hourly:
        problems.append("declares both pay and pay_hourly; pick the one that matches the market")
    if not has_annual and not has_hourly:
        problems.append("no pay or pay_hourly block")

    if not re.search(r"^employment:", fm, re.M):
        problems.append("no employment block")
    else:
        for side in ("remote", "local"):
            if not re.search(r"^\s+%s:" % side, fm, re.M):
                problems.append("employment does not state %s" % side)

    for section in REQUIRED_SECTIONS:
        if section not in text:
            problems.append("missing section %s" % section)

    # Every query block needs a priority so a short run can stop early.
    for line in text.split("\n"):
        if line.startswith("**Block ") and "priority" not in line:
            problems.append("block has no priority: %s" % line[:60])

    blocks = [l for l in text.split("\n") if l.startswith("**Block ")]
    if len(blocks) < 3:
        problems.append("only %d query blocks; the schema expects 4-7" % len(blocks))

    # Screens must be deltas, not a restatement of the universal list.
    screens = text.split("## Screens", 1)[-1].split("\n## ", 1)[0] if "## Screens" in text else ""
    if screens and not re.search(r"\b(Adds|Removes)\b", screens):
        problems.append("Screens section names neither additions nor removals; it should be deltas only")

    if "## Yield log" in text:
        log = text.split("## Yield log", 1)[1]
        if "|" not in log:
            problems.append("Yield log has no table")

    return name, tag, problems


def main():
    if not os.path.isdir(PROFILE_DIR):
        print("profile directory not found: %s" % PROFILE_DIR)
        return 2

    paths = [p for p in sorted(glob.glob(os.path.join(PROFILE_DIR, "*.md")))
             if os.path.basename(p) != "README.md"]
    if not paths:
        print("no profiles yet in %s (build one with /super-long-scrape-profile)" % PROFILE_DIR)
        return 0

    readme_path = os.path.join(PROFILE_DIR, "README.md")
    readme = io.open(readme_path, encoding="utf-8").read() if os.path.exists(readme_path) else ""
    failed = 0
    tags = {}

    for path in paths:
        name, tag, problems = check(path)
        if name and "`%s`" % name not in readme:
            problems.append("not listed in the README index table")
        if tag:
            tags.setdefault(tag, []).append(name)
        status = "OK" if not problems else "FAIL"
        print("%-18s %s" % (os.path.basename(path), status))
        for p in problems:
            print("    - %s" % p)
        if problems:
            failed += 1

    for tag, owners in sorted(tags.items()):
        if len(owners) > 1:
            print("FAIL duplicate track_tag %r shared by %s" % (tag, ", ".join(owners)))
            failed += 1

    print()
    print("%d profile(s) checked, %d with problems" % (len(paths), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
