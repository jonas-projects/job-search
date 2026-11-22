"""Background queue for ZipRecruiter 1-Click Apply, the twin of tools/apply_queue.py (2026-10-14).

  python tools/zip_apply_queue.py add <job url> <folder>     queue a drafted+reviewed application
  python tools/zip_apply_queue.py status
  python tools/zip_apply_queue.py run [--max N]              unattended: send what goes in one click,
                                                             park anything that opens questions
  python tools/zip_apply_queue.py run --present              the user at the browser: the
                                                             question forms get answered there; parked items included
  python tools/zip_apply_queue.py retry <folder>
  python tools/zip_apply_queue.py withdraw <folder> [why]

`run` refuses unless the candidate has opted in to auto-submit (candidate.auto_submit_opted_in):
1-Click has no review page, so the click is the submission.

Each pass calls zip_personal.py apply, which checks the job is live, 1-Click and not already applied,
replaces the ZipRecruiter profile resume with the folder's tailored PDF, confirms the swap, then
clicks 1-Click Apply. 1-Click sends the PROFILE resume, so the swap is the guarantee the tailored CV
goes out; if it can't be confirmed nothing is clicked.

Measured on the first watched test (Bridge Crest, 2026-10-14): 1-Click opened a 9-question employer
form (employment/relatives at the parent company, licences, right to work, sponsorship, 18+,
background check, drug test, essential functions). Unattended runs close such a form unsent and
park the item ("questions"); the form's HTML is saved to the attempt folder so auto-answering can
be built from real markup later.

State: documents/applications/zip_queue.json, items ready | submitted | parked | withdrawn.
Tracker rows are matched by the ZipRecruiter jid in source or notes ("ZipRecruiter jid=<jid>").
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import random
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import candidate  # noqa: E402  (candidate name -> resume prefix; auto-submit opt-in)
APPS = os.path.join(ROOT, "documents", "applications")
QUEUE = os.path.join(APPS, "zip_queue.json")
LOCK = os.path.join(APPS, ".zip_queue.lock")
TRACKER = os.path.join(ROOT, "job_search_tracker.csv")
ZIP = os.path.join(ROOT, ".agents", "skills", "ziprecruiter-personal", "cli", "zip_personal.py")
PACE_S = (30, 90)
FINAL_OR_SENT = {"applied", "interview", "offer", "rejected", "accepted", "declined", "no_response"}

# zip_personal apply status -> (queue state, reason)
OUTCOME = {
    "submitted": ("submitted", None),
    "already_applied": ("submitted", "ZipRecruiter shows it already applied"),
    "expired": ("withdrawn", "posting no longer active"),
    "job_not_found": ("withdrawn", "posting not found"),
    "needs_answers": ("parked", "questions: 1-Click opened an employer form; run with --present"),
    "resume_not_confirmed": ("parked", "resume swap not confirmed: nothing was sent"),
    "resume_missing": ("parked", "tailored resume PDF missing"),
    "no_one_click_button": ("parked", "no 1-Click Apply button on the page"),
    "unconfirmed": ("parked", "clicked, not confirmed: check My Jobs on ZipRecruiter before retrying"),
}


def now():
    return dt.datetime.now().replace(microsecond=0)


def load():
    try:
        return json.load(open(QUEUE, encoding="utf-8"))
    except (OSError, ValueError):
        return {"items": {}}


def save(q):
    tmp = QUEUE + ".tmp"
    json.dump(q, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(tmp, QUEUE)


def jid_of(url):
    m = re.search(r"[?&]jid=([0-9a-f]+)", url)
    return m.group(1) if m else url


def tracker_rows():
    raw = io.open(TRACKER, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    return list(csv.reader(io.StringIO(raw))), nl


def row_has_jid(r, h, jid):
    return jid in r[h.index("source")] or f"jid={jid}" in r[h.index("notes")]


def tracker_update(jid, status, note):
    rows, nl = tracker_rows()
    h = rows[0]
    hit = False
    for r in rows[1:]:
        cur = r[h.index("status")]
        if row_has_jid(r, h, jid) and cur != status and not (status == "withdrawn" and cur in FINAL_OR_SENT):
            r[h.index("status")] = status
            r[h.index("notes")] = (r[h.index("notes")] + " " + note).strip()
            hit = True
    if hit:
        out = io.StringIO()
        csv.writer(out, lineterminator=nl).writerows(rows)
        io.open(TRACKER, "w", encoding="utf-8", newline="").write(out.getvalue())
    return hit


def resume_of(folder):
    d = os.path.join(APPS, folder)
    prefix = candidate.resume_prefix()
    cvs = [f for f in os.listdir(d) if f.startswith(prefix) and f.endswith(".pdf")] if os.path.isdir(d) else []
    return os.path.join(d, cvs[0]) if len(cvs) == 1 else None, cvs


def cmd_add(a):
    folder = a.folder.strip("/\\")
    cv, cvs = resume_of(folder)
    if not cv:
        sys.exit(f"not added: expected exactly one {candidate.resume_prefix()}*.pdf in {folder}, found {cvs}")
    jid = jid_of(a.url)
    company = role = ""
    rows, _ = tracker_rows()
    h = rows[0]
    for r in rows[1:]:
        if row_has_jid(r, h, jid):
            company, role = r[h.index("company")], r[h.index("role")]
    if not company:
        print(f"warning: no tracker row carries jid {jid}; add 'ZipRecruiter jid={jid}' to its notes")
    q = load()
    q["items"][folder] = {"portal": "ziprecruiter", "jid": jid, "url": a.url, "folder": folder,
                          "company": company, "role": role, "state": "ready", "reason": None,
                          "attempts": 0, "added": now().isoformat(), "history": []}
    save(q)
    print(f"queued {folder} ({company} - {role})")


def cmd_status(a):
    order = {"ready": 0, "parked": 1, "submitted": 2, "withdrawn": 3}
    for it in sorted(load()["items"].values(), key=lambda i: (order.get(i["state"], 9), i["folder"])):
        extra = f" - {it['reason']}" if it.get("reason") else ""
        print(f"{it['state']:9} {it['company'][:34]:34} {it['role'][:40]:40}{extra}")


def cmd_set(a, state):
    q = load()
    it = q["items"].get(a.folder.strip("/\\"))
    if not it:
        sys.exit("no such item")
    it["state"], it["reason"] = state, (getattr(a, "why", None) or None)
    save(q)
    if state == "withdrawn":
        tracker_update(it["jid"], "withdrawn", f"Withdrawn from the ZipRecruiter queue {now().date()}: {it['reason'] or 'by hand'}.")
    print(f"{it['folder']} -> {state}")


def eligible(it, present):
    if it["state"] == "ready":
        return True
    # Parked items only go again with the user present; "unconfirmed" never does on its own
    # (it may have been sent: check My Jobs and use retry).
    return present and it["state"] == "parked" and not (it.get("reason") or "").startswith("clicked, not confirmed")


def run_one(it, present):
    cv, _ = resume_of(it["folder"])
    out = os.path.join(APPS, it["folder"], "zip_attempts", now().strftime("%Y%m%d-%H%M%S"))
    if not cv:
        return {"status": "resume_missing"}, out
    cmd = [sys.executable, ZIP, "apply", it["url"], "--resume", cv, "--submit", "--out", out]
    if present:
        cmd += ["--present", "--wait-human", "900"]
    proc = subprocess.Popen(cmd, cwd=ROOT, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    for line in proc.stderr:
        if line.startswith("NEEDS_HUMAN:"):
            print(f"{line.rstrip()} | {it['folder']}", flush=True)
    proc.wait()
    try:
        return json.load(open(os.path.join(out, "apply_report.json"), encoding="utf-8")), out
    except (OSError, ValueError) as exc:
        return {"status": "error", "log": [f"no report: {exc!r}"]}, out


def cmd_run(a):
    # 1-Click Apply sends on the click: there is no review page to stop at. So the queue clicks
    # only for a candidate who has explicitly opted in to auto-submit (facts.json
    # automation.auto_submit: true). An opt-in covers that candidate's own applications only.
    candidate.first_last()
    if not candidate.auto_submit_opted_in():
        sys.exit("not run: ZipRecruiter 1-Click submits on the click, and the candidate has not opted in to "
                 "auto-submit (facts.json automation.auto_submit). Apply by hand from the board instead.")
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 6 * 3600:
        sys.exit(f"another runner holds {LOCK} (delete it if no runner is active)")
    open(LOCK, "w").write(str(os.getpid()))
    done = []
    try:
        todo = [it for it in load()["items"].values() if eligible(it, a.present)]
        todo.sort(key=lambda i: (i["state"] != "ready", i["added"]))
        for n, it in enumerate(todo[: a.max]):
            if n:
                time.sleep(random.uniform(*PACE_S))
            rep, out = run_one(it, a.present)
            st = rep.get("status", "error")
            if st.startswith("not_one_click"):
                state, reason = "parked", "not 1-Click any more: apply on the company site"
            else:
                state, reason = OUTCOME.get(st, ("parked", f"unhandled status {st}"))
            q = load()
            cur = q["items"][it["folder"]]
            cur["attempts"] += 1
            cur["history"].append({"at": now().isoformat(), "status": st, "run_dir": os.path.relpath(out, APPS)})
            cur["state"], cur["reason"] = state, reason
            if state == "submitted":
                cur["submitted_at"] = now().isoformat()
                tracker_update(cur["jid"], "applied", f"Submitted via zip_apply_queue {now().date()}.")
            elif state == "withdrawn":
                tracker_update(cur["jid"], "withdrawn", f"Not sent: {reason} ({now().date()}).")
            save(q)
            done.append(cur)
            print(f"{cur['company'][:30]:30} -> {state}{' - ' + reason if reason else ''}", flush=True)
            open(LOCK, "w").write(str(os.getpid()))
    finally:
        os.remove(LOCK)
    print(f"done: {sum(c['state'] == 'submitted' for c in done)} submitted, "
          f"{sum(c['state'] == 'parked' for c in done)} parked, {len(done)} attempted")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("add"); p.add_argument("url"); p.add_argument("folder")
    sp.add_parser("status")
    p = sp.add_parser("run"); p.add_argument("--max", type=int, default=25); p.add_argument("--present", action="store_true")
    p = sp.add_parser("retry"); p.add_argument("folder")
    p = sp.add_parser("withdraw"); p.add_argument("folder"); p.add_argument("why", nargs="?")
    a = ap.parse_args()
    try:
        _dispatch(a)
    except candidate.CandidateNotSet as e:
        sys.exit(f"error: {e}")


def _dispatch(a):
    {"add": cmd_add, "status": cmd_status, "run": cmd_run,
     "retry": lambda a: cmd_set(a, "ready"), "withdraw": lambda a: cmd_set(a, "withdrawn")}[a.cmd](a)


if __name__ == "__main__":
    main()
