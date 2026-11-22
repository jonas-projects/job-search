"""Background queue for Indeed Apply submissions (stage 1 of the 2026-09-28 redesign).

The candidate approves postings in chat; Claude drafts and
reviews them and adds each ready application here. This runner then works through the queue:

  python tools/apply_queue.py add <jk> <folder>        queue a drafted+reviewed application
  python tools/apply_queue.py status                   show the queue
  python tools/apply_queue.py run [--max N]            unattended: fill, park anything needing a person
  python tools/apply_queue.py run --present            the user at the browser: also clears parked items
                                                       (captchas, consents, hand-offs) with sound alerts
  python tools/apply_queue.py retry <folder>           move a parked/failed item back to ready
  python tools/apply_queue.py withdraw <folder> [why]  drop an item

Design (see apply-automate SKILL.md, "Queue runner"):
- One state file, documents/applications/apply_queue.json. Every item is ready | submitted |
  parked(reason) | withdrawn. Nothing is tracked by renaming folders.
- One pass per application: indeed_apply.py --submit walks the form once. Its own pre-submit
  checks (name, resume filename, cover letter) stop it from sending anything wrong.
- The final Submit click is a person's unless the candidate has opted in to auto-submit
  (facts.json automation.auto_submit: true; see candidate.auto_submit_opted_in). Without it,
  unattended runs park on the review page as awaiting_submit_click.
- Park and move on: an unattended run never waits. Captchas, consents and unanswerable
  questions park the item with a reason; the queue continues. A --present run clears them.
- Block pages back off and retry later on their own. Postings that expired are withdrawn.
- On submit, job_search_tracker.csv and job_scraper/seen_jobs.json are updated automatically.
- Every run writes a summary to documents/applications/_runs/<timestamp>.md, including every
  auto-answer Claude gave, for the candidate to skim.
Never run two copies at once (Chrome profile lock): a lock file enforces that.
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import candidate  # noqa: E402  (candidate name and PDF prefixes, from facts.json)
from candidate import resume_prefix  # noqa: E402
APPS = os.path.join(ROOT, "documents", "applications")
QUEUE = os.path.join(APPS, "apply_queue.json")
LOCK = os.path.join(APPS, ".apply_queue.lock")
RUNS = os.path.join(APPS, "_runs")
TRACKER = os.path.join(ROOT, "job_search_tracker.csv")
LEDGER = os.path.join(ROOT, "job_scraper", "seen_jobs.json")
BASE_ANSWERS = os.path.join(ROOT, ".claude", "skills", "apply-automate", "answers_base.json")
# 2026-10-10: 30-90 s between applications (was 3-8 s; bursts drew block pages in Sep).
PACE_S = (30, 90)
# One Chrome per run (tools/indeed_browser.py), so a Cloudflare check ticked once covers the batch.
# INDEED_SHARED_BROWSER=0 goes back to one Chrome per application.
CDP_PORT = int(os.environ.get("INDEED_CDP_PORT", "9223"))


class SharedBrowser:
    """Starts tools/indeed_browser.py and restarts it if it dies. url is None when unavailable,
    in which case indeed_apply.py launches its own Chrome as before."""

    def __init__(self):
        self.proc, self.url = None, None
        self.enabled = os.environ.get("INDEED_SHARED_BROWSER", "1") != "0"

    def _alive(self):
        import urllib.request
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=3).read()
            return True
        except Exception:  # noqa: BLE001
            return False

    def ensure(self):
        if not self.enabled:
            return None
        if self._alive():
            # ours, or a Chrome orphaned by a holder that was killed hard: both are usable
            self.url = f"http://127.0.0.1:{CDP_PORT}"
            return self.url
        self.stop()
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "indeed_browser.py"),
                                      "--port", str(CDP_PORT)], cwd=ROOT,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if self.proc.poll() is not None:
                break
            if self._alive():
                self.url = f"http://127.0.0.1:{CDP_PORT}"
                return self.url
            time.sleep(1)
        print("shared Chrome did not start; falling back to one Chrome per application", flush=True)
        self.stop()
        return None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._alive():
            # holder gone but its Chrome still up: closing every tab makes Chrome exit
            import urllib.request
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=3))
                for t in tabs:
                    urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/close/{t['id']}", timeout=3).read()
            except Exception:  # noqa: BLE001
                pass
            for _ in range(20):
                if not self._alive():
                    break
                time.sleep(1)
        self.proc, self.url = None, None

# indeed_apply.py status -> (new state, reason, retry automatically?)
OUTCOME = {
    "submitted": ("submitted", None, False),
    "already_applied": ("submitted", "Indeed already shows Applied", False),
    "expired": ("withdrawn", "posting expired", False),
    "blocked": ("parked", "Indeed block page", True),
    "error": ("parked", "script error", True),
    "too_many_steps": ("parked", "form did not advance", True),
    "unexpected_screen": ("parked", "unexpected screen", True),
    "submit_click_error": ("parked", "Submit click error: may have been sent; rerun checks Applied", True),
    "not_authenticated": ("parked", "Indeed signed out: run /indeed-login", False),
    "submit_disabled": ("parked", "captcha: needs the user present", False),
    "needs_answers": ("parked", "question(s) only the candidate can answer", False),
    "needs_followup_steps": ("parked", "AI recruiter / follow-up steps within an hour: the candidate", False),
    "requirements_not_met": ("parked", "Indeed says requirements not met: the candidate decides apply-anyway", False),
    "name_not_verified": ("parked", "name on review page wrong and not fixed", False),
    "wrong_resume": ("parked", "wrong resume attached on review page", False),
    "resume_not_selected": ("parked", "resume upload failed", True),
    "cover_letter_not_attached": ("parked", "cover letter not attached", True),
    "validation_error": ("parked", "form validation error", False),
    "not_indeed_apply": ("parked", "no apply control found (transient page?): check", True),
    "awaiting_submit_click": ("parked", "review page ready: a person clicks Submit (run --present; candidate has not opted in to auto-submit)", False),
    "company_site_only": ("parked", "company-site application only: needs a form_answers.md packet for the candidate", False),
}
MAX_AUTO_RETRIES = 3
BACKOFF_MIN = 0  # minutes x retry count before an auto-retry; 0 = immediate (2026-09-28, was 30)

# Employer messages (2026-10-10): /apply drafts documents/applications/<stem>/indeed_message.txt,
# and after the application is submitted the queue sends it through Indeed's "Message this
# employer" link (tools/indeed_message.py). The link is employer-gated and can appear days after
# submission, so a pending message is re-checked on later runs, then given up as unavailable.
MESSAGE_FILE = "indeed_message.txt"
MESSAGE_MAX_CHECKS = 6       # Applied-list looks with no link before giving up
MESSAGE_MAX_SEND_TRIES = 3   # failed send attempts (challenge, error) before giving up
MESSAGE_WINDOW_DAYS = 14     # never message an application older than this
# indeed_message.py status -> (message state, reason, what next: None | "try" (counts against
# MESSAGE_MAX_SEND_TRIES) | "stop" (environment-wide: end the pass, no item is charged for it))
MESSAGE_OUTCOME = {
    "sent": ("sent", None, None),
    "already_sent": ("sent", "thread already showed the message", None),
    "no_messaging": ("pending", "no 'Message this employer' link yet", None),
    "not_in_applied": ("pending", "not on the Applied list yet", None),
    "ready_to_send": ("pending", "prepare mode: typed, not sent", None),
    "challenge": ("pending", "Cloudflare check on messages.indeed.com: needs the user present (run message --present)", "stop"),
    "blocked": ("pending", "Indeed block page", "stop"),
    "not_authenticated": ("pending", "Indeed signed out: run /indeed-login", "stop"),
    "no_compose_box": ("failed", "compose page has no message box: fix indeed_message.py locators from message_report.json", None),
    "no_send_button": ("failed", "compose page has no Send button: fix indeed_message.py locators from message_report.json", None),
    "send_click_error": ("failed", "Send click error: MAY have gone; check the thread on Indeed before retrying", None),
    "send_unconfirmed": ("failed", "Send clicked, not confirmed: check the thread on Indeed before retrying", None),
    "error": ("pending", "script error", "try"),
}


def now():
    return dt.datetime.now().replace(microsecond=0)


def load():
    if os.path.exists(QUEUE):
        return json.load(open(QUEUE, encoding="utf-8"))
    return {"items": {}}


def save(q):
    tmp = QUEUE + ".tmp"
    json.dump(q, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(tmp, QUEUE)


def tracker_rows():
    raw = io.open(TRACKER, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    rows = list(csv.reader(io.StringIO(raw)))
    return rows, nl


def row_has_jk(r, h, jk):
    """A tracker row belongs to this Indeed posting if its source is the Indeed link, or, when the
    source is the employer's own link, its notes carry "Indeed jk=<jk>"."""
    return jk in r[h.index("source")] or f"jk={jk}" in r[h.index("notes")]


FINAL_OR_SENT = {"applied", "interview", "offer", "rejected", "accepted", "declined", "no_response"}


def tracker_update(jk, status, note):
    rows, nl = tracker_rows()
    h = rows[0]
    hit = False
    for r in rows[1:]:
        cur = r[h.index("status")]
        # Withdrawing from the queue never undoes an application sent by hand.
        if row_has_jk(r, h, jk) and cur != status and not (status == "withdrawn" and cur in FINAL_OR_SENT):
            r[h.index("status")] = status
            r[h.index("notes")] = (r[h.index("notes")] + " " + note).strip()
            hit = True
    if hit:
        out = io.StringIO()
        csv.writer(out, lineterminator=nl).writerows(rows)
        io.open(TRACKER, "w", encoding="utf-8", newline="").write(out.getvalue())
    return hit


def ledger_update(jk, status):
    if not os.path.exists(LEDGER):
        return False
    raw = io.open(LEDGER, encoding="utf-8", newline="").read()
    led = json.loads(raw)
    hit = False
    for v in led.get("seen", {}).values():
        if jk in (v.get("url") or "") and v.get("status") != status:
            v["status"] = status
            hit = True
    if hit:
        out = json.dumps(led, ensure_ascii=False, indent=1)
        if "\r\n" in raw[:1000]:
            out = out.replace("\n", "\r\n")
        io.open(LEDGER, "w", encoding="utf-8", newline="").write(out)
    return hit


def cmd_add(a):
    folder = a.folder.strip("/\\")
    d = os.path.join(APPS, folder)
    prefix = resume_prefix()
    cvs = [f for f in os.listdir(d) if f.startswith(prefix) and f.endswith(".pdf")] if os.path.isdir(d) else []
    problems = []
    if len(cvs) != 1:
        problems.append(f"expected exactly one {prefix}*.pdf in {folder}, found {cvs}")
    if not os.path.exists(os.path.join(d, "cover_letter.txt")):
        problems.append("cover_letter.txt missing")
    if problems:
        sys.exit("not added: " + "; ".join(problems))
    company = role = ""
    rows, _ = tracker_rows()
    h = rows[0]
    for r in rows[1:]:
        if row_has_jk(r, h, a.jk):
            company, role = r[h.index("company")], r[h.index("role")]
    q = load()
    q["items"][folder] = {"jk": a.jk, "folder": folder, "company": company, "role": role,
                          "state": "ready", "reason": None, "attempts": 0, "auto_retries": 0,
                          "added": now().isoformat(), "next_try": None, "history": [],
                          "message": message_init(folder)}
    save(q)
    print(f"queued {folder} ({company} - {role}); employer message: {q['items'][folder]['message']['state']}")


def message_init(folder):
    if os.path.exists(os.path.join(APPS, folder, MESSAGE_FILE)):
        return {"state": "pending", "reason": None, "checks": 0, "tries": 0, "history": []}
    return {"state": "skipped", "reason": f"no {MESSAGE_FILE} in the folder", "checks": 0, "tries": 0, "history": []}


def message_state(it):
    """An item queued before messages existed gets one the first time it is looked at, so a
    message drafted later for an already-submitted application still goes out."""
    m = it.get("message")
    if not m or (m["state"] == "skipped" and m.get("reason", "").startswith("no ")
                 and os.path.exists(os.path.join(APPS, it["folder"], MESSAGE_FILE))):
        it["message"] = m = message_init(it["folder"])
    return m


def submitted_when(it):
    s = it.get("submitted_at")
    if not s:
        hist = [h["at"] for h in it.get("history", []) if h.get("status") in ("submitted", "already_applied")]
        s = hist[-1] if hist else it.get("added")
    return dt.datetime.fromisoformat(s)


def message_candidates(q):
    out = []
    for it in q["items"].values():
        if it["state"] != "submitted":
            continue
        m = message_state(it)
        if m["state"] != "pending":
            continue
        if now() - submitted_when(it) > dt.timedelta(days=MESSAGE_WINDOW_DAYS):
            m["state"], m["reason"] = "expired", f"submitted over {MESSAGE_WINDOW_DAYS} days ago"
            continue
        out.append(it)
    return out


def cmd_status(a):
    q = load()
    order = {"ready": 0, "parked": 1, "submitted": 2, "withdrawn": 3}
    for it in sorted(q["items"].values(), key=lambda i: (order.get(i["state"], 9), i["folder"])):
        extra = f" - {it['reason']}" if it.get("reason") else ""
        nt = f" (retry after {it['next_try']})" if it.get("next_try") and it["state"] == "parked" else ""
        msg = ""
        if it["state"] == "submitted":
            m = message_state(it)
            msg = f" | msg: {m['state']}" + (f" ({m['reason']})" if m.get("reason") and m["state"] != "sent" else "")
        print(f"{it['state']:9} {it['company'][:34]:34} {it['role'][:40]:40}{extra}{nt}{msg}")


def cmd_set(a, state):
    q = load()
    it = q["items"].get(a.folder.strip("/\\"))
    if not it:
        sys.exit("no such item")
    it["state"], it["reason"], it["next_try"] = state, (getattr(a, "why", None) or None), None
    it["auto_retries"] = 0
    save(q)
    if state == "withdrawn":
        tracker_update(it["jk"], "withdrawn", f"Withdrawn from the apply queue {now().date()}: {it['reason'] or 'by hand'}.")
    print(f"{it['folder']} -> {state}")


def eligible(it, present):
    if it["state"] == "ready":
        return True
    if it["state"] != "parked":
        return False
    if present:
        # Company-site postings can't be finished in Indeed's form; re-walking them only burns
        # passes and drew block pages (2026-10-09: 3-5 re-runs each in one session).
        return it.get("reason") not in (OUTCOME["not_authenticated"][1], OUTCOME["company_site_only"][1])
    retry = any(it.get("reason") == v[1] and v[2] for v in OUTCOME.values())
    due = not it.get("next_try") or now() >= dt.datetime.fromisoformat(it["next_try"])
    return retry and due and it.get("auto_retries", 0) < MAX_AUTO_RETRIES


def run_one(it, present, cdp_url=None):
    d = os.path.join(APPS, it["folder"])
    cv = [f for f in os.listdir(d) if f.startswith(resume_prefix()) and f.endswith(".pdf")][0]
    ans = os.path.join(d, "indeed_answers.json")
    out = os.path.join(d, "indeed_attempts", now().strftime("%Y%m%d-%H%M%S"))
    wait = "900" if present else "0"
    cmd = [sys.executable, os.path.join(ROOT, "tools", "indeed_apply.py"), it["jk"], "--out", out,
           "--resume", os.path.join(d, cv), "--cover-letter", os.path.join(d, "cover_letter.txt"),
           "--answers", ans if os.path.exists(ans) else BASE_ANSWERS,
           "--submit", "--confirm-token", it["jk"], "--wait-human", wait, "--captcha-wait", wait,
           "--foreground"]
    # Who clicks the final Submit (2026-11-04). Default: a person. The run fills and gates the
    # form, then stops on the review page (--human-submit): unattended it parks the item as
    # awaiting_submit_click, --present waits at the browser window for the click. Only a candidate who
    # has explicitly opted in (facts.json automation.auto_submit: true) gets the script clicking
    # Submit itself once every gate passes. An opt-in never carries over between candidates.
    if not candidate.auto_submit_opted_in():
        cmd += ["--human-submit", "900" if present else "0"]
    if os.path.exists(os.path.join(d, "job_posting.md")):
        cmd += ["--posting", os.path.join(d, "job_posting.md")]
    # 2026-10-09: some employer forms require the cover letter as an uploaded file
    src = os.path.join(ROOT, "cover_letters", f"cover_{it['folder']}.pdf")
    if os.path.exists(src):
        dst = os.path.join(d, candidate.cover_letter_name())
        shutil.copyfile(src, dst)
        cmd += ["--form-cover-pdf", dst]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if cdp_url:
        env["INDEED_CDP_URL"] = cdp_url
    # Pass indeed_apply's NEEDS_HUMAN alert lines through: the Claude
    # session watching this run turns them into phone pushes. Everything else stays quiet.
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    for line in proc.stderr:
        if line.startswith("NEEDS_HUMAN:"):
            print(f"{line.rstrip()} | {it['folder']}", flush=True)
    proc.wait()
    try:
        return json.load(open(os.path.join(out, "apply_report.json"), encoding="utf-8")), out
    except (OSError, ValueError) as exc:
        return {"status": "error", "log": [f"no report: {exc!r}"]}, out


def run_messages(present, cdp_url, max_sends=50):
    """One pass over every submitted application whose employer message is still pending:
    one look at My Jobs > Applied for all of them, then one send per application that has a
    'Message this employer' link. Returns [(item, status, report)] for the run summary."""
    q = load()
    cands = message_candidates(q)
    save(q)  # message_state/candidates may have initialized or expired entries
    if not cands:
        return []
    wait = "900" if present else "0"
    stamp = now().strftime("%Y%m%d-%H%M%S")
    scan_dir = os.path.join(RUNS, f"messages-{stamp}")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if cdp_url:
        env["INDEED_CDP_URL"] = cdp_url

    def call(args, out):
        cmd = [sys.executable, os.path.join(ROOT, "tools", "indeed_message.py"), *args, "--out", out,
               "--wait-human", wait, "--foreground"]
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        for line in proc.stderr:
            if line.startswith("NEEDS_HUMAN:"):
                print(f"{line.rstrip()} | employer message", flush=True)
        proc.wait()
        try:
            return json.load(open(os.path.join(out, "message_report.json"), encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"status": "error", "log": [f"no report: {exc!r}"]}

    scan = call(["scan"], scan_dir)
    if scan.get("status") != "listed":
        print(f"employer messages: Applied list not readable ({scan.get('status')}); {len(cands)} still pending", flush=True)
        return [(it, f"scan_{scan.get('status')}", scan) for it in cands]
    links = {c["jk"]: c.get("message_url") for c in scan.get("cards", [])}
    done = []
    for it in cands:
        q = load()
        cur = q["items"][it["folder"]]
        m = message_state(cur)
        if not links.get(it["jk"]):
            m["checks"] += 1
            why = "not on the Applied list yet" if it["jk"] not in links else "no 'Message this employer' link yet"
            if m["checks"] >= MESSAGE_MAX_CHECKS:
                m["state"], m["reason"] = "unavailable", f"{why.replace(' yet', '')} after {m['checks']} looks: employer does not offer messaging"
            else:
                m["reason"] = f"{why} ({m['checks']}/{MESSAGE_MAX_CHECKS} looks)"
            m["history"].append({"at": now().isoformat(), "status": "no_link", "run_dir": os.path.relpath(scan_dir, APPS)})
            save(q)
            done.append((cur, "no_link", {}))
            continue
        if len([d for d in done if d[1] not in ("no_link",)]) >= max_sends:
            break
        if any(d[1] not in ("no_link",) for d in done):
            time.sleep(random.uniform(*PACE_S))
        out = os.path.join(APPS, it["folder"], "indeed_attempts", f"message-{now().strftime('%Y%m%d-%H%M%S')}")
        rep = call(["send", it["jk"], "--message", os.path.join(APPS, it["folder"], MESSAGE_FILE),
                    "--send", "--confirm-token", it["jk"]], out)
        st = rep.get("status", "error")
        state, reason, nxt = MESSAGE_OUTCOME.get(st, ("pending", f"unhandled status {st}", "try"))
        q = load()
        cur = q["items"][it["folder"]]
        m = message_state(cur)
        if nxt == "try":
            m["tries"] += 1
            if m["tries"] >= MESSAGE_MAX_SEND_TRIES:
                state, reason = "failed", f"{reason} ({m['tries']} tries)"
        m["state"], m["reason"] = state, reason
        if state == "sent":
            m["sent_at"] = now().isoformat()
            tracker_note(cur["jk"], f"Employer message sent on Indeed {now().date()}.")
        m["history"].append({"at": now().isoformat(), "status": st, "run_dir": os.path.relpath(out, APPS)})
        save(q)
        done.append((cur, st, rep))
        print(f"{cur['company'][:30]:30} message -> {state}{' - ' + reason if reason else ''}", flush=True)
        if nxt == "stop":
            # signed out, blocked, or Cloudflare on messages.indeed.com: every later send would
            # hit the same wall, so leave the rest pending for the next run
            print(f"employer messages: {reason}; stopping the pass, the rest stay pending", flush=True)
            break
    return done


def tracker_note(jk, note):
    """Append a note to the tracker row(s) for this posting without touching their status."""
    rows, nl = tracker_rows()
    h = rows[0]
    hit = False
    for r in rows[1:]:
        if row_has_jk(r, h, jk):
            r[h.index("notes")] = (r[h.index("notes")] + " " + note).strip()
            hit = True
    if hit:
        out = io.StringIO()
        csv.writer(out, lineterminator=nl).writerows(rows)
        io.open(TRACKER, "w", encoding="utf-8", newline="").write(out.getvalue())
    return hit


def cmd_message(a):
    """Only the employer-message pass (no applications): e.g. to send messages drafted after the
    fact for applications already submitted, or to clear pending ones with the user present."""
    q = load()
    cands = message_candidates(q)
    save(q)
    if not cands:
        print("no pending employer messages")
        return
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 6 * 3600:
        sys.exit(f"another runner holds {LOCK} (delete it if no runner is active)")
    open(LOCK, "w").write(str(os.getpid()))
    os.makedirs(RUNS, exist_ok=True)
    started = now()
    browser = SharedBrowser()
    try:
        msgs = run_messages(a.present, browser.ensure(), a.max)
    finally:
        browser.stop()
        os.remove(LOCK)
    path = write_summary(started, [], a.present, msgs)
    print("summary:", path)


def cmd_run(a):
    candidate.first_last()  # fail fast on an unset (placeholder) candidate, before taking the lock
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 6 * 3600:
        sys.exit(f"another runner holds {LOCK} (delete it if no runner is active)")
    open(LOCK, "w").write(str(os.getpid()))
    os.makedirs(RUNS, exist_ok=True)
    started = now()
    done = []
    msgs = []
    browser = SharedBrowser()
    tried = {}
    deadline = time.time() + a.hours * 3600
    try:
      while True:
        q = load()
        # at most 2 passes per posting per run, so a --present --until-done run cannot loop on
        # the same parked items for hours
        todo = [it for it in q["items"].values() if eligible(it, a.present) and tried.get(it["folder"], 0) < 2]
        todo.sort(key=lambda i: (i["state"] != "ready", i["added"]))
        if not todo:
            if not a.until_done:
                break
            # sleep until the earliest scheduled auto-retry, if any is still pending
            pending = [dt.datetime.fromisoformat(i["next_try"]) for i in q["items"].values()
                       if i["state"] == "parked" and i.get("next_try") and i.get("auto_retries", 0) < MAX_AUTO_RETRIES]
            if not pending:
                break
            wake = min(pending)
            secs = max(60, (wake - now()).total_seconds() + 30)
            if time.time() + secs > deadline:
                break
            print(f"waiting until {wake:%H:%M} for scheduled retries (checking for new items each minute)", flush=True)
            open(LOCK, "w").write(str(os.getpid()))  # refresh the lock's age
            # sleep in 60 s slices so items added (or retries withdrawn) meanwhile are seen promptly
            end = time.time() + secs
            while time.time() < end:
                time.sleep(min(60, max(1, end - time.time())))
                if any(it["state"] == "ready" for it in load()["items"].values()):
                    break
            continue
        stop = False
        for n, it in enumerate(todo[: a.max]):
            if n or done:
                time.sleep(random.uniform(*PACE_S))
            tried[it["folder"]] = tried.get(it["folder"], 0) + 1
            rep, out = run_one(it, a.present, browser.ensure())
            st = rep.get("status", "error")
            state, reason, auto = OUTCOME.get(st, ("parked", f"unhandled status {st}", False))
            if st == "needs_answers" and rep.get("unanswered"):
                reason += ": " + "; ".join(u["label"].split("\n")[0][:70] for u in rep["unanswered"][:3])
            q = load()  # re-read: add/withdraw may have happened meanwhile
            cur = q["items"][it["folder"]]
            cur["attempts"] += 1
            cur["history"].append({"at": now().isoformat(), "status": st, "run_dir": os.path.relpath(out, APPS)})
            cur["state"], cur["reason"] = state, reason
            if state == "parked" and auto:
                cur["auto_retries"] = cur.get("auto_retries", 0) + 1
                # retry right away (2026-09-28); the usual 30-90 s pacing gap still applies
                cur["next_try"] = (now() + dt.timedelta(minutes=BACKOFF_MIN * cur["auto_retries"])).isoformat() if BACKOFF_MIN else None
            else:
                cur["next_try"] = None
            if state == "submitted":
                cur["submitted_at"] = now().isoformat()
                tracker_update(cur["jk"], "applied", f"Submitted via apply_queue {now().date()}.")
                ledger_update(cur["jk"], "applied")
            elif state == "withdrawn":
                tracker_update(cur["jk"], "withdrawn", f"Not sent: {reason} ({now().date()}).")
            save(q)
            done.append((cur, st, rep))
            print(f"{cur['company'][:30]:30} -> {state}{' - ' + reason if reason else ''}", flush=True)
            if st == "not_authenticated":
                print("Indeed session expired; stopping the run. Run /indeed-login.", flush=True)
                stop = True
                break
            if len(done) >= a.max or time.time() > deadline:
                stop = True
                break
        if stop or not a.until_done:
            break
      # Employer messages for everything submitted (this run or earlier) whose message is still
      # pending: one Applied-list look, then one send per application with a link.
      if not a.no_messages and not any(st == "not_authenticated" for _, st, _ in done) and time.time() < deadline:
        if done:
            time.sleep(random.uniform(*PACE_S))
        msgs = run_messages(a.present, browser.ensure())
    finally:
        browser.stop()
        os.remove(LOCK)
    path = write_summary(started, done, a.present, msgs)
    print("summary:", path)


def write_summary(started, done, present, msgs=()):
    q = load()
    lines = [f"# Apply queue run {started:%Y-%m-%d %H:%M} ({'present' if present else 'unattended'})", ""]
    sub = [x for x in done if x[0]["state"] == "submitted"]
    park = [x for x in done if x[0]["state"] == "parked"]
    lines += [f"Attempted {len(done)}: {len(sub)} submitted, {len(park)} parked, "
              f"{len(done) - len(sub) - len(park)} other.", "", "## Submitted"]
    lines += [f"- {c['company']} - {c['role']}" for c, _, _ in sub] or ["- none"]
    warned = [(c, rep["name_warning"]) for c, _, rep in done if "name_warning" in rep]
    if warned:
        lines += ["", "## Sent with an unverified name on the review page (check Indeed > My Jobs)"]
        lines += [f"- {c['company']} - {c['role']}: review read {w!r}" for c, w in warned]
    lines += ["", "## Parked (needs a person or a retry)"]
    lines += [f"- {c['company']} - {c['role']}: {c['reason']}" for c, _, _ in park] or ["- none"]
    lines += ["", "## Auto-answers to skim"]
    any_aa = False
    for c, _, rep in done:
        for aa in rep.get("auto_answers", []):
            any_aa = True
            lines.append(f"- **{c['company']}**: {aa['question'].splitlines()[0][:90]} -> **{aa['answer'][:80]}** ({aa['reason'][:120]})")
    if not any_aa:
        lines.append("- none")
    lines += ["", "## Newly learned answers (reused on future postings; fix with `apply_queue.py learned`)"]
    new = [(c, e) for c, _, rep in done for e in rep.get("learned_new", [])]
    lines += [f"- {e['question'][:90]} -> **{e['answer']}** (from {c['company']})" for c, e in new] or ["- none"]
    if msgs:
        sent = [c for c, st, _ in msgs if c.get("message", {}).get("state") == "sent"]
        lines += ["", "## Employer messages (Indeed)", f"- sent: {len(sent)}"]
        lines += [f"  - {c['company']} - {c['role']}" for c in sent]
        rest = [c for c, st, _ in msgs if c.get("message", {}).get("state") != "sent"]
        lines += [f"- {c['company']} - {c['role']}: {c['message']['state']} - {c['message'].get('reason')}" for c in rest]
        if any(st.startswith("scan_") for _, st, _ in msgs):
            lines.append(f"- Applied list not readable this run ({msgs[0][1][5:]}); all pending messages wait for the next run")
    waiting = [i for i in q["items"].values() if i["state"] in ("ready", "parked")]
    lines += ["", f"## Queue now: {sum(i['state'] == 'ready' for i in waiting)} ready, "
              f"{sum(i['state'] == 'parked' for i in waiting)} parked"]
    path = os.path.join(RUNS, f"{started:%Y%m%d-%H%M%S}.md")
    io.open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return path


def cmd_learned(a):
    """List the learned-answers store, or correct/confirm/disable one entry by number."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import answer_store
    entries = answer_store.load_learned()
    if a.n is not None:
        e = entries[a.n]
        if a.set is not None:
            e["answer"], e["source"], e["confirmed"] = a.set, "human", True
        if a.confirm:
            e["confirmed"] = True
        if a.disable:
            e["disabled"] = True
        answer_store.save_learned(entries)
    for i, e in enumerate(entries):
        flag = "disabled" if e.get("disabled") else ("confirmed" if e.get("confirmed") else "unconfirmed")
        print(f"{i:3} [{flag:11}] {e['question'][:80]} -> {e['answer']}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("add"); p.add_argument("jk"); p.add_argument("folder")
    sp.add_parser("status")
    p = sp.add_parser("run"); p.add_argument("--max", type=int, default=50); p.add_argument("--present", action="store_true")
    p.add_argument("--until-done", action="store_true",
                   help="after a pass, sleep until scheduled retries and keep going (up to --hours)")
    p.add_argument("--hours", type=float, default=6)
    p.add_argument("--no-messages", action="store_true", help="skip the employer-message pass at the end of the run")
    p = sp.add_parser("retry"); p.add_argument("folder")
    p = sp.add_parser("withdraw"); p.add_argument("folder"); p.add_argument("why", nargs="?")
    p = sp.add_parser("message", help="only the employer-message pass over submitted applications with a pending message")
    p.add_argument("--max", type=int, default=50); p.add_argument("--present", action="store_true")
    p = sp.add_parser("message-skip", help="never message this employer"); p.add_argument("folder"); p.add_argument("why", nargs="?")
    p = sp.add_parser("message-retry", help="put a failed/unavailable/skipped message back to pending"); p.add_argument("folder")
    p = sp.add_parser("learned", help="list learned answers; N --set X / --confirm / --disable to fix one")
    p.add_argument("n", type=int, nargs="?"); p.add_argument("--set"); p.add_argument("--confirm", action="store_true")
    p.add_argument("--disable", action="store_true")
    a = ap.parse_args()
    try:
        _dispatch(a)
    except candidate.CandidateNotSet as e:
        sys.exit(f"error: {e}")


def _dispatch(a):
    if a.cmd == "learned":
        return cmd_learned(a)
    if a.cmd == "add":
        cmd_add(a)
    elif a.cmd == "status":
        cmd_status(a)
    elif a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "retry":
        cmd_set(a, "ready")
    elif a.cmd == "withdraw":
        cmd_set(a, "withdrawn")
    elif a.cmd == "message":
        cmd_message(a)
    elif a.cmd in ("message-skip", "message-retry"):
        q = load()
        it = q["items"].get(a.folder.strip("/\\"))
        if not it:
            sys.exit("no such item")
        m = message_state(it)
        if a.cmd == "message-skip":
            m["state"], m["reason"] = "skipped", (a.why or "by hand")
        else:
            if not os.path.exists(os.path.join(APPS, it["folder"], MESSAGE_FILE)):
                sys.exit(f"no {MESSAGE_FILE} in {it['folder']}: draft it first")
            m["state"], m["reason"], m["checks"], m["tries"] = "pending", None, 0, 0
        save(q)
        print(f"{it['folder']} message -> {m['state']}")


if __name__ == "__main__":
    main()
