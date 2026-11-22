"""Facts, templated standard answers, and the learned-answers store (stage 2, 2026-09-28).

- facts.json: the single source of truth for form facts (address, contact, eligibility, self-ID,
  pay rules). answers_base.json refers to facts as {dotted.path} placeholders, and
  auto_answer.py passes the whole file to Claude.
- learned_answers.json: answers Claude gave to questions no pattern covered, saved so the next
  posting asking the same question gets the same answer instantly (no Claude call). The candidate or
  the user can correct or delete entries; `python tools/apply_queue.py learned` lists them.
  Never learned: pay questions, free text over 60 characters, questions naming the posting's
  company, and guarded categories (auto_answer.is_guarded).
"""
import datetime as dt
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, ".claude", "skills", "apply-automate")
FACTS = os.path.join(SKILL, "facts.json")
BASE = os.path.join(SKILL, "answers_base.json")
LEARNED = os.path.join(SKILL, "learned_answers.json")

PAY = re.compile(r"salary|compensation|pay|wage|hourly rate|rate of pay|\$", re.I)


def norm(label):
    """Same normalization as indeed_apply.norm_label: trailing *, :, ?, whitespace stripped."""
    return re.sub(r"\s+", " ", re.sub(r"[\s*:?]+$", "", (label or ""))).strip().lower()


def load_facts():
    return json.load(open(FACTS, encoding="utf-8"))


def _lookup(facts, path):
    cur = facts
    for part in path.split("."):
        cur = cur[part]
    return cur


def expand(value, facts):
    """Fill {dotted.path} placeholders from facts; lists are expanded element by element."""
    if isinstance(value, list):
        out = []
        for v in value:
            e = expand(v, facts)
            if e not in out:
                out.append(e)
        return out
    if not isinstance(value, str):
        return value
    return re.sub(r"\{([a-z_]+(?:\.[a-z_0-9]+)+)\}", lambda m: str(_lookup(facts, m.group(1))), value)


def base_answers():
    facts = load_facts()
    return [{"match": a["match"], "answer": expand(a["answer"], facts)}
            for a in json.load(open(BASE, encoding="utf-8"))]


def load_learned():
    if not os.path.exists(LEARNED):
        return []
    return json.load(open(LEARNED, encoding="utf-8"))


def save_learned(entries):
    tmp = LEARNED + ".tmp"
    json.dump(entries, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(tmp, LEARNED)


def learned_patterns():
    """Learned entries as exact-label patterns (anchored by indeed_apply's fullmatch)."""
    return [{"match": re.escape(e["question"]), "answer": e["answer"]}
            for e in load_learned() if not e.get("disabled")]


def learnable(label, qtype, answer, company):
    if PAY.search(label or ""):
        return False
    if qtype == "textarea" or (qtype == "text" and len(str(answer)) > 60):
        return False  # free text is posting-specific ("why this role") even when short
    generic = {"inc", "llc", "the", "group", "company", "corp", "corporation", "services", "solutions",
               "management", "health", "healthcare", "consulting", "partners", "team", "careers", "career"}
    words = [w for w in re.findall(r"[a-z]{4,}", (company or "").lower()) if w not in generic]
    if any(w in (label or "").lower() for w in words):
        return False  # a company-specific question ("Have you worked at Harbor Health before?")
    return True


def company_from_posting(posting_path):
    try:
        t = open(posting_path, encoding="utf-8").read(3000)
    except (OSError, TypeError):
        return ""
    m = re.search(r"Company:\**\s*(.+)", t)
    return m.group(1).strip() if m else ""


def remember(items, company, posting):
    """items: [{"label","type","options","answer","reason"}]. Returns the newly added entries."""
    entries = load_learned()
    have = {e["question"] for e in entries}
    today = dt.date.today().isoformat()
    added = []
    for it in items:
        q = norm(it["label"])
        if q in have or not learnable(it["label"], it.get("type"), it["answer"], company):
            continue
        e = {"question": q, "answer": it["answer"], "type": it.get("type"),
             "options": it.get("options"), "reason": it.get("reason", ""), "source": "claude",
             "confirmed": False, "first_seen": today, "posting": posting}
        entries.append(e)
        have.add(q)
        added.append(e)
    if added:
        save_learned(entries)
    return added
