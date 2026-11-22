#!/usr/bin/env python3
"""Build the candidate's one applications board, and write their marks back to the tracker.

There is ONE board artifact, URL in documents/applications/apply_board_url.txt. It is
rebuilt from repo state and republished to that URL at the end of every
/super-long-scrape and every /apply-automate run. Two pages (tabs):

  To send     tracker rows at `drafted`: drafted and reviewed, not yet sent, and still
              taking applications (tools/posting_live.py). Each row has an Applied tick and
              a Won't apply button. The copy box at the bottom lists what was marked.
  Auto-apply  only what the Indeed queue is working through: queued and submitted items from
              documents/applications/apply_queue.json, with the employer-message state.
              Parked items need a person, so they sit on To send with the reason in the note.

Marks reach the tracker two ways, both handled by `sync`:
  * The candidate pastes the copy-box lines into chat ("Applied 2026-10-14: Company - Role",
    "Won't apply 2026-10-14: Company - Role"). Save them to a file and sync it.
  * The page also saves marks to the artifact's `db` (collection `marks`, doc per row id,
    {"status": "applied"|"skip", "date"}); ArtifactData `list` them to a JSON file and sync it.

  python tools/apply_board.py build [--out PATH] [--no-prune]
      Also writes <out>.files.json, the {published path: source} map of resume PDFs. Publish
      the page with that map as `files`, so each row's Resume link works from any device.
      Cover letters, form answers and written answers are embedded with copy buttons.
      Closed postings (liveness.json) are left off, and their tracker rows set to
      `withdrawn` ("Not sent: posting closed") unless --no-prune.
  python tools/apply_board.py sync FILE [--dry-run]
      FILE is pasted text or a marks JSON. applied -> `applied`; won't apply -> `withdrawn`.

Groups on the To send page come from the tracker note, channel and queue state. A note
starting with `[group:<key>]` overrides the heuristic.
"""
import argparse
import csv
import datetime as dt
import difflib
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = os.path.join(ROOT, "documents", "applications")
TRACKER = os.path.join(ROOT, "job_search_tracker.csv")
QUEUE = os.path.join(APPS, "apply_queue.json")
ZIP_QUEUE = os.path.join(APPS, "zip_queue.json")  # tools/zip_apply_queue.py, same item shape
LIVENESS = os.path.join(APPS, "liveness.json")
TEMPLATE = os.path.join(ROOT, "templates", "apply_board.html")
BOARD_URL = os.path.join(APPS, "apply_board_url.txt")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import candidate  # noqa: E402  (candidate name -> PDF filename prefixes)


def _resume_prefix():
    """<First>_<Last>_Resume_ from facts.json; any *_Resume_ PDF while the name is unset, so a
    board can still be built for an un-onboarded profile."""
    try:
        return candidate.resume_prefix()
    except candidate.CandidateNotSet:
        return ""


def _cover_name():
    try:
        return candidate.cover_letter_name()
    except candidate.CandidateNotSet:
        return "Cover_Letter.pdf"

# Group descriptions are generic: where the candidate can work comes from their profile and is
# written into each tracker note at drafting time ("in scope", "far side", "mode unclear"...).
GROUPS = [
    ["scope", "In scope", "Matches the candidate's work-mode and location rules. Start here."],
    ["email", "Apply by email", "Address and draft email are in the folder's form_answers.md."],
    ["question", "Commute or eligibility question", "A long commute, or an eligibility point to check first."],
    ["mode", "Mode unclear or likely onsite", "Your call: the posting may need an onsite seat the profile doesn't cover."],
    ["stop", "Held or skip advised", "Eligibility or a hard requirement doesn't fit. Marking these Won't apply clears them."],
]
FAR = r"far side|long commute"
AGGREGATOR = ("indeed.com", "linkedin.com", "ziprecruiter.com")


def load_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_queue():
    out = {}
    for path in (QUEUE, ZIP_QUEUE):
        items = load_json(path, {}).get("items", {})
        items = items.values() if isinstance(items, dict) else items
        out.update({i["folder"]: i for i in items})
    return out


def read_tracker():
    if not os.path.exists(TRACKER):
        return [], []  # fresh candidate: no tracker yet, so an empty board
    rows = list(csv.DictReader(open(TRACKER, encoding="utf-8")))
    return rows, list(rows[0].keys()) if rows else []


def write_tracker(rows, fields):
    with open(TRACKER, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def folder_of(row):
    m = re.search(r"main_(.+)\.tex", row.get("cv_file", ""))
    if m and os.path.isdir(os.path.join(APPS, m.group(1))):
        return m.group(1)
    return None


def row_id(row):
    return folder_of(row) or re.sub(r"\W+", "_", (row["company"] + "_" + row["role"]).lower())[:80]


def posting_fields(folder):
    out = {}
    try:
        text = open(os.path.join(APPS, folder, "job_posting.md"), encoding="utf-8").read(6000)
    except OSError:
        return out
    for k, v in re.findall(r"^- \*\*([^:*]+):\*\*\s*(.+)$", text, re.M):
        out.setdefault(k.strip().lower(), v.strip())
    return out


def first_url(s):
    m = re.search(r"https?://\S+", s or "")
    return m.group(0).rstrip(").,;") if m else ""


def links(row, fields):
    emp, listing = "", ""
    for k in ("employer apply url", "apply url", "apply", "employer listing", "employer careers page"):
        u = first_url(fields.get(k))
        if u and not any(a in u for a in AGGREGATOR):
            emp = u
            break
    for k in ("url", "url (aggregator)", "aggregator url", "indeed url"):
        u = first_url(fields.get(k))
        if not u:
            continue
        if any(a in u for a in AGGREGATOR):
            listing = listing or u
        elif not emp:
            emp = u
    src = first_url(row.get("source"))
    if src:
        if any(a in src for a in AGGREGATOR):
            listing = listing or src
        elif not emp:
            emp = src
    return emp, listing


def clean_note(note):
    note = re.sub(r"^\[group:\w+\]\s*", "", note or "")
    note = re.sub(r"^(Super-long-scrape|Admin batch|Implementation batch|Payroll batch)[^;]*;\s*", "", note)
    note = re.sub(r"\b(HUMAN|CANDIDATE)\b", "(you answer)", note)
    return note.strip()


def group_of(row):
    forced = re.match(r"\[group:(\w+)\]", row.get("notes", ""))
    if forced:
        return forced.group(1)
    n = row.get("notes", "")
    if re.search(r"\bsoft STOP\b", n):
        return "mode" if re.search(r"onsite", n, re.I) else "stop"
    if re.search(r"\bSTOP mode\b|\bFLAG\b", n):
        return "mode"
    if re.search(r"\bSTOP\b|\bHELD\b|not eligible|knockout", n):
        return "stop"
    if row.get("channel") == "email" or re.search(r"apply by email|sends email", n, re.I):
        return "email"
    in_scope = re.search(r"in scope", n, re.I)
    if re.search(r"mode unclear|likely onsite|reads onsite|mode not stated|work mode unstated", n, re.I) and not re.search(r"\(in scope", n):
        return "mode"
    if re.search(r"onsite|in person|in-office", n, re.I) and not in_scope:
        return "mode"
    if re.search(FAR, n, re.I) and not in_scope:
        return "question"
    if re.search(r"unconfirmed|eligibility|talent-pool|evergreen", n, re.I):
        return "question"
    return "scope"


ANSWERS = os.path.join(ROOT, "application_answers")


def read_text(path, limit=60000):
    try:
        return open(path, encoding="utf-8").read(limit).strip()
    except OSError:
        return ""


def app_docs(folder):
    """What the candidate needs at hand to send an application from any device: the resume PDF (published
    beside the page) and every paste-ready text, embedded in the page with a copy button."""
    if not folder:
        return "", []
    d = os.path.join(APPS, folder)
    pdfs = sorted(f for f in os.listdir(d) if f.startswith(_resume_prefix()) and "_Resume_" in f and f.endswith(".pdf"))
    resume = f"files/{folder}/{pdfs[0]}" if pdfs else ""
    docs = []
    for name, label in (("cover_letter.txt", "Cover letter"), ("form_answers.md", "Form answers")):
        t = read_text(os.path.join(d, name))
        if t:
            docs.append({"label": label, "text": t})
    qa = os.path.join(ANSWERS, folder)
    if os.path.isdir(qa):
        q = read_text(os.path.join(qa, "_questions.txt"))
        if q:
            docs.append({"label": "Application questions", "text": q})
        for f in sorted(os.listdir(qa)):
            if re.match(r"\d+_.*\.txt$", f):
                t = read_text(os.path.join(qa, f))
                if t:
                    docs.append({"label": "Answer " + f[:-4].replace("_", " ", 1), "text": t})
    return resume, docs


def _braced(s, i):
    """The balanced {...} group starting at s[i] == '{': (content, index after it)."""
    depth, j = 0, i
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    return s[i + 1:], len(s)


def tex_to_text(t):
    t = re.sub(r"(?<!\\)%.*", "", t)
    t = re.sub(r"\\(vspace|hspace|needspace|enlargethispage)\*?\{[^}]*\}", "", t)
    t = re.sub(r"\\(textbf|textit|emph|underline|mbox|textsc|small|footnotesize)\{([^{}]*)\}", r"\2", t)
    t = re.sub(r"\\href\{[^}]*\}\{([^{}]*)\}", r"\1", t)
    for a, b in (("\\%", "%"), ("\\$", "$"), ("\\&", "&"), ("\\#", "#"), ("\\_", "_"), ("---", "-"), ("--", "-"),
                 ("~", " "), ("``", '"'), ("''", '"'), ("\\,", " "), ("\\ldots", "..."), ("$\\sim$", "~"), ("\\textasciitilde", "~")):
        t = t.replace(a, b)
    t = re.sub(r"\\(begin|end)\{itemize\}", "", t)
    t = re.sub(r"\\item\s*", "\n- ", t)
    t = re.sub(r"\\[a-zA-Z]+\*?", "", t)
    t = t.replace("{", "").replace("}", "")
    lines = [re.sub(r"\s+", " ", l).strip() for l in t.split("\n")]
    return "\n".join(l for l in lines if l and l != "-")


def work_history(cv_tex):
    """Each \\cventry in the tailored CV as plain fields, for forms (Workday and the like) that want
    title, company, dates and a description per role. Experience and other work entries only."""
    try:
        s = open(os.path.join(ROOT, cv_tex), encoding="utf-8").read()
    except OSError:
        return []
    out = []
    for m in re.finditer(r"\\cventry", s):
        i, args = m.end(), []
        for _ in range(6):
            while i < len(s) and s[i] in " \n\t":
                i += 1
            if i >= len(s) or s[i] != "{":
                break
            a, i = _braced(s, i)
            args.append(a)
        if len(args) < 6:
            continue
        section = re.findall(r"\\section\{([^}]*)\}", s[:m.start()])
        section = section[-1] if section else ""
        if re.search(r"education|project|certific|award|publication", section, re.I):
            continue
        out.append({"dates": tex_to_text(args[0]), "title": tex_to_text(args[1]), "company": tex_to_text(args[2]),
                    "location": tex_to_text(args[3]), "description": tex_to_text(args[5]), "section": tex_to_text(section)})
    return out


def resume_files(items):
    """{published path: source path} for every resume and cover letter PDF on the board."""
    out = {}
    for i in items:
        if i.get("resume"):
            folder = i["resume"].split("/")[1]
            out[i["resume"]] = os.path.join(APPS, folder, i["resume"].split("/")[-1])
        if i.get("cover"):
            out[i["cover"]] = i["_cover_src"]
    return out


def build_items(include_closed=False):
    queue = load_queue()
    live = load_json(LIVENESS, {})
    rows, _ = read_tracker()
    items = []
    for r in rows:
        if r.get("status") != "drafted":
            continue
        folder = folder_of(r)
        qi = queue.get(folder or "")
        if qi and qi.get("state") == "submitted":
            continue  # tracker lagging the queue: already sent
        if qi and qi.get("state") == "withdrawn" and re.search(r"expired|not eligible|withdrew", qi.get("reason") or ""):
            continue  # queue closed it for good; tracker not yet updated
        rid = row_id(r)
        lv = live.get(rid, {})
        if lv.get("status") == "closed" and not include_closed:
            continue
        fields = posting_fields(folder) if folder else {}
        emp, listing = links(r, fields)
        note = clean_note(r.get("notes"))
        if qi and qi.get("state") == "parked":
            why = qi.get("reason") or "needs you"
            if "company-site" in why:
                if "company site" not in note:
                    note = (note + "; " if note else "") + "applies on the company site (Indeed button)"
            else:
                src = "ZipRecruiter" if qi.get("portal") == "ziprecruiter" else "Indeed"
                note = f"{src} auto-apply stopped ({why}): apply yourself via the listing. " + note
        pay = fields.get("pay", "")
        if pay and "$" in pay and "$" not in note:
            note = (note + "; " if note else "") + pay
        try:
            fit = int(float(r.get("fit_rating") or 0))
        except ValueError:
            fit = 0
        items.append({
            "id": rid, "company": r["company"], "role": r["role"], "fit": fit, "note": note,
            "emp": emp, "listing": listing, "deadline": (r.get("deadline") or "").strip(),
            "group": group_of(r), "folder": f"documents/applications/{folder}/" if folder else "",
            "drafted": r.get("date", ""),
            "live": lv.get("status", "unknown"), "checked": (lv.get("checked") or "")[:10],
        })
        items[-1]["resume"], items[-1]["docs"] = app_docs(folder)
        cl = os.path.join(ROOT, (r.get("cover_letter_file") or "").replace(".tex", ".pdf"))
        items[-1]["cover"] = f"files/{folder}/{_cover_name()}" if folder and r.get("cover_letter_file") and os.path.isfile(cl) else ""
        items[-1]["_cover_src"] = cl
        items[-1]["jobs"] = work_history(r.get("cv_file") or "")
    items.sort(key=lambda i: (-i["fit"], i["deadline"] or "9999"))
    return items


AUTO_ORDER = {"ready": 0, "submitted": 1}
# Page 2 is only what the queue is working through for the candidate. Parked items need a person, so they
# live on the To send page (their tracker rows are `drafted`); withdrawn ones are finished.


def auto_items():
    out = []
    for it in load_queue().values():
        if it["state"] not in AUTO_ORDER:
            continue
        hist = it.get("history") or []
        last = hist[-1]["at"] if hist else ""
        msg = (it.get("message") or {})
        reason = it.get("reason") or ""
        out.append({
            "id": it["folder"], "company": it["company"], "role": it["role"], "state": it["state"],
            "reason": reason, "attempts": it.get("attempts", 0),
            "submitted": (it.get("submitted_at") or "")[:10],
            "added": (it.get("added") or "")[:10], "last": last[:16].replace("T", " "),
            "next": (it.get("next_try") or "")[:16].replace("T", " "),
            "msg": msg.get("state", "") if it["state"] == "submitted" else "",
            "msgReason": msg.get("reason") or "",
            "listing": it.get("url") or f"https://www.indeed.com/viewjob?jk={it['jk']}",
            "portal": "ZipRecruiter" if it.get("portal") == "ziprecruiter" else "Indeed",
        })
    out.sort(key=lambda i: (AUTO_ORDER.get(i["state"], 9), "" if i["state"] != "submitted" else "~",
                            -(int(re.sub(r"\D", "", i["submitted"] or "0") or 0))))
    return out


def prune_closed():
    """Tracker rows whose posting is closed become `withdrawn`, so they never come back."""
    live = load_json(LIVENESS, {})
    rows, fields = read_tracker()
    closed = []
    for r in rows:
        lv = live.get(row_id(r), {})
        if r.get("status") == "drafted" and lv.get("status") == "closed":
            r["status"] = "withdrawn"
            r["notes"] = (r["notes"] + " " if r["notes"] else "") + \
                f"Not sent: posting closed ({(lv.get('checked') or '')[:10]}, {lv.get('via') or 'check'})."
            closed.append(f"{r['company']} - {r['role']}")
    if closed:
        write_tracker(rows, fields)
    return closed


def cmd_build(a):
    closed = [] if a.no_prune else prune_closed()
    items = build_items()
    files = resume_files(items)
    for i in items:
        i.pop("_cover_src", None)
    auto = auto_items()
    tpl = open(TEMPLATE, encoding="utf-8").read()
    js = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
    stamp = dt.datetime.now().strftime("%-d %b %Y, %H:%M") if os.name != "nt" else dt.datetime.now().strftime("%d %b %Y, %H:%M")
    page = (tpl.replace("{{TITLE}}", html.escape(a.title))
               .replace("{{UPDATED}}", html.escape(stamp))
               .replace("/*{{ITEMS}}*/[]", js(items))
               .replace("/*{{GROUPS}}*/[]", js(GROUPS))
               .replace("/*{{AUTO}}*/[]", js(auto)))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(page)
    manifest = os.path.splitext(os.path.abspath(a.out))[0] + ".files.json"
    json.dump(files, open(manifest, "w", encoding="utf-8"), indent=1)
    counts = {g[0]: sum(1 for i in items if i["group"] == g[0]) for g in GROUPS}
    acounts = {}
    for i in auto:
        acounts[i["state"]] = acounts.get(i["state"], 0) + 1
    print(f"to send: {len(items)} {counts}")
    print(f"auto-apply: {len(auto)} {acounts}")
    unknown = sum(1 for i in items if i["live"] == "unknown")
    if unknown:
        print(f"{unknown} rows have no liveness verdict (kept)")
    for c in closed:
        print(f"closed -> withdrawn: {c}")
    print(f"page -> {a.out}")
    print(f"resume and cover letter PDFs -> {manifest} (pass as the publish `files` map; {len(files)} files)")
    try:
        print("publish to:", open(BOARD_URL, encoding="utf-8").read().strip())
    except OSError:
        print("no board URL saved yet: publish, then write the URL to", BOARD_URL)


LINE = re.compile(r"^\s*(Applied|Won't apply|Wont apply|Won’t apply)\s+(\d{4}-\d{2}-\d{2}):\s*(.+?)\s*$", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def parse_marks(path):
    """{('id', value) or ('text', 'Company - Role'): {status, date}}"""
    raw = open(path, encoding="utf-8").read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    marks = []
    if data is not None:
        if isinstance(data, dict) and "documents" in data:
            data = data["documents"]
        entries = data.items() if isinstance(data, dict) else \
            [((d.get("id") or d.get("doc_id") or d.get("path", "").split("/")[-1]), d.get("data", d)) for d in data]
        for k, v in entries:
            if v.get("status") in ("applied", "skip"):
                marks.append((("id", k), v))
        return marks
    for line in raw.splitlines():
        m = LINE.match(line)
        if m:
            status = "applied" if m.group(1).lower() == "applied" else "skip"
            marks.append((("text", m.group(3)), {"status": status, "date": m.group(2)}))
    return marks


def cmd_sync(a):
    marks = parse_marks(a.file)
    rows, fields = read_tracker()
    by_id = {row_id(r): r for r in rows}
    unmatched, changed = [], 0
    for (kind, key), m in marks:
        if kind == "id":
            r = by_id.get(key)
        else:
            cands = {norm(f"{r['company']} - {r['role']}"): r for r in rows}
            r = cands.get(norm(key))
            if r is None:
                best = difflib.get_close_matches(norm(key), list(cands), n=1, cutoff=0.75)
                r = cands[best[0]] if best else None
        if r is None:
            unmatched.append(key)
            continue
        want = "applied" if m["status"] == "applied" else "withdrawn"
        if r["status"] == want or (want == "withdrawn" and r["status"] == "applied"):
            print(f"already {r['status']:9} {r['company']} - {r['role']}")
            continue
        if r["status"] not in ("drafted", "withdrawn"):
            print(f"left as {r['status']:9} {r['company']} - {r['role']} (board says {m['status']})")
            continue
        r["status"] = want
        note = f"Applied {m['date']} (marked on board)." if want == "applied" else \
            f"Not sent: chose not to apply ({m['date']}, board)."
        r["notes"] = (r["notes"] + " " if r["notes"] else "") + note
        changed += 1
        print(f"{want:9} {r['company']} - {r['role']}")
    for u in unmatched:
        print(f"NO MATCH  {u}")
    if a.dry_run:
        print(f"{changed} rows would change")
        return
    if changed:
        write_tracker(rows, fields)
    print(f"{changed} tracker rows updated, {len(unmatched)} unmatched")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    b = s.add_parser("build")
    b.add_argument("--out", default="apply_board.html")
    b.add_argument("--title", default="Applications Board")
    b.add_argument("--no-prune", action="store_true")
    b.set_defaults(fn=cmd_build)
    y = s.add_parser("sync")
    y.add_argument("file")
    y.add_argument("--dry-run", action="store_true")
    y.set_defaults(fn=cmd_sync)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
