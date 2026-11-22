"""Who the candidate is, for code that names files or checks names (2026-11-04).

The candidate (whose name goes on resumes and Indeed forms) is read from
.claude/skills/apply-automate/facts.json, never hardcoded. The user who runs the
sessions and clears captchas is a separate person and is not described here.

    import candidate
    candidate.full_name()        # "Ada Lovelace"
    candidate.first_last()       # ("Ada", "Lovelace")
    candidate.file_prefix()      # "Ada_Lovelace"
    candidate.resume_prefix()    # "Ada_Lovelace_Resume_"
    candidate.cover_letter_name()  # "Ada_Lovelace_Cover_Letter.pdf"
    candidate.auto_submit_opted_in()  # False unless facts.json says automation.auto_submit: true

Placeholder values such as "[FIRST_NAME]" (a reset, not yet onboarded facts.json) raise
CandidateNotSet with a clear message instead of producing nonsense filenames.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTS = os.path.join(ROOT, ".claude", "skills", "apply-automate", "facts.json")


class CandidateNotSet(RuntimeError):
    pass


def _placeholder(v):
    return not isinstance(v, str) or not v.strip() or bool(re.search(r"[\[\]{}<>]", v))


def _load(facts):
    if facts is not None:
        return facts
    try:
        return json.load(open(FACTS, encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CandidateNotSet(f"cannot read candidate facts ({FACTS}): {e}") from e


def first_last(facts=None):
    """(first, last) from facts.json name.first / name.last. Raises CandidateNotSet if unset."""
    facts = _load(facts)
    name = facts.get("name") or {}
    first, last = name.get("first"), name.get("last")
    if _placeholder(first) or _placeholder(last):
        raise CandidateNotSet(
            f"candidate name not set in {os.path.relpath(FACTS, ROOT)} "
            f"(name.first={first!r}, name.last={last!r}); fill in the candidate's facts first")
    return first.strip(), last.strip()


def full_name(facts=None):
    """name.full if set, else 'First Last'."""
    facts = _load(facts)
    first, last = first_last(facts)
    full = (facts.get("name") or {}).get("full")
    return full.strip() if not _placeholder(full) else f"{first} {last}"


def _slug(s):
    return re.sub(r"[^A-Za-z0-9\-]+", "_", s).strip("_")


def file_prefix(facts=None):
    """'First_Last', the prefix every application PDF carries."""
    first, last = first_last(facts)
    return f"{_slug(first)}_{_slug(last)}"


def resume_prefix(facts=None):
    return file_prefix(facts) + "_Resume_"


def cover_letter_name(facts=None):
    return file_prefix(facts) + "_Cover_Letter.pdf"


def auto_submit_opted_in(facts=None):
    """True only when the candidate has explicitly opted in to the queue clicking the final
    Submit (facts.json "automation": {"auto_submit": true}). Anything else, including a missing
    key or an unreadable file, is False: the run stops on the review page and a person clicks
    Submit. ZipRecruiter 1-Click has no review page, so without the opt-in it is not clicked."""
    try:
        facts = _load(facts)
    except CandidateNotSet:
        return False
    return (facts.get("automation") or {}).get("auto_submit") is True
