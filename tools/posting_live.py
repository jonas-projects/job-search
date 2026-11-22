#!/usr/bin/env python3
"""Is a drafted application's posting still taking applications?

Checks every row `tools/apply_board.py` would put on the board and records the answer in
documents/applications/liveness.json ({row id: {status, via, checked, detail}}), which the
board build reads: `closed` rows are left off and their tracker rows set to `withdrawn`.

  python tools/posting_live.py [--max-age-days 2] [--no-indeed] [--only ID ...]

How each link is read (employer link first, then the aggregator):
  Greenhouse / Lever / Ashby   the board's public JSON API (missing id = closed)
  LinkedIn                     linkedin-search `detail` (isActive false = closed)
  Indeed                       indeed-personal `detail` (expired banner = closed), headed on the
                               screen (headless trips Cloudflare). ~40 s each; a
                               captcha rings NEEDS_HUMAN and waits up to 5 min. It holds the Indeed
                               Chrome profile: never run it beside apply_queue.py or indeed-personal.
  Anything else                one GET with a browser UA: 404/410 or a closed phrase = closed,
                               otherwise `unknown` (kept on the board).

An employer verdict beats an aggregator one: LinkedIn "no longer accepting" with the
employer's own board still listing the role stays live. When in doubt, the row stays.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import apply_board  # noqa: E402

CACHE = os.path.join(apply_board.APPS, "liveness.json")
LOCK = os.path.join(apply_board.APPS, ".apply_queue.lock")
ZIP_LOCK = os.path.join(apply_board.APPS, ".zip_queue.lock")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CLOSED_TEXT = re.compile(
    r"no longer accepting applications|job (?:posting )?(?:is )?no longer available|position has been filled"
    r"|this (?:job|posting|position) has (?:expired|closed|been closed)|job not found|requisition is no longer"
    r"|the job you are looking for (?:is no longer|no longer exists)|posting (?:is )?closed", re.I)


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(600_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # network, TLS, timeout
        return None, str(e)


def check_greenhouse(u):
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", u) or \
        re.search(r"[?&]for=([\w-]+).*?[?&]token=(\d+)", u)
    if not m:
        return None
    code, _ = get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
    return {200: "live", 404: "closed"}.get(code)


def check_lever(u):
    m = re.search(r"jobs\.lever\.co/([\w-]+)/([0-9a-f-]{36})", u)
    if not m:
        return None
    code, _ = get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
    return {200: "live", 404: "closed"}.get(code)


def check_ashby(u):
    m = re.search(r"jobs\.ashbyhq\.com/([\w.-]+)/([0-9a-f-]{36})", u)
    if not m:
        return None
    code, body = get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
    if code != 200:
        return None
    return "live" if m.group(2) in body else "closed"


def check_linkedin(u):
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/?]*-)?(\d{8,})", u)
    if not m:
        return None
    try:
        out = subprocess.run(["bun", "run", ".agents/skills/linkedin-search/cli/src/cli.ts", "detail", m.group(1),
                              "--format", "json"], cwd=ROOT, capture_output=True, text=True, timeout=90).stdout
        d = json.loads(out)
    except Exception:
        return None
    return "live" if d.get("isActive") else "closed"


def check_indeed(u, allow):
    m = re.search(r"indeed\.com/(?:viewjob|rc/clk|m/viewjob)\?(?:.*&)?jk=([0-9a-f]{16})", u)
    if not m or not allow:
        return None
    try:
        out = subprocess.run([sys.executable, ".agents/skills/indeed-personal/cli/indeed_personal.py", "detail",
                              m.group(1), "--format", "json"],
                             cwd=ROOT, capture_output=True, text=True, timeout=420).stdout
        d = json.loads(out)
    except Exception:
        return None
    if d.get("error"):
        return None
    d = d.get("result", d)
    if d.get("expired_note") or d.get("expired"):
        return "closed"
    return "live" if (d.get("title") or d.get("description")) else None


def check_zip(u):
    """ZipRecruiter: zip_personal.py detail (isActive from the job's own JSON). Needs the signed-in
    ZipRecruiter profile and a visible window; run serialized like the Indeed checks."""
    try:
        out = subprocess.run([sys.executable, ".agents/skills/ziprecruiter-personal/cli/zip_personal.py", "detail", u],
                             cwd=ROOT, capture_output=True, text=True, timeout=240).stdout
        d = json.loads(out).get("result", {})
    except Exception:
        return None
    if d.get("error") or d.get("isActive") is False:
        return "closed"
    return "live" if d.get("isActive") else None


def check_generic(u):
    code, body = get(u)
    if code in (404, 410):
        return "closed"
    if code == 200 and CLOSED_TEXT.search(body):
        return "closed"
    return None  # a 200 page with no closed phrase proves little on a JS-rendered board


def check_url(u, allow_indeed):
    if not u.startswith("http"):
        return None
    for host, fn in (("greenhouse.io", check_greenhouse), ("lever.co", check_lever), ("ashbyhq.com", check_ashby),
                     ("linkedin.com", check_linkedin)):
        if host in u:
            return fn(u)
    if "indeed.com" in u:
        return check_indeed(u, allow_indeed)
    if "ziprecruiter.com" in u:
        # the ZipRecruiter queue owns that Chrome profile while it runs
        return check_zip(u) if allow_indeed and not os.path.exists(ZIP_LOCK) else None
    return check_generic(u)


def verdict(item, allow_indeed):
    emp = check_url(item["emp"], allow_indeed) if item["emp"] else None
    if emp:
        return emp, "employer"
    agg = check_url(item["listing"], allow_indeed) if item["listing"] else None
    if agg:
        return agg, "listing"
    return "unknown", ""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--max-age-days", type=float, default=2, help="re-check rows older than this (default 2)")
    p.add_argument("--no-indeed", action="store_true", help="skip Indeed links (fast pass)")
    p.add_argument("--only", nargs="*", help="row ids to check regardless of age")
    a = p.parse_args()
    allow_indeed = not a.no_indeed
    if allow_indeed and os.path.exists(LOCK):
        print("apply_queue lock present: Indeed links skipped this pass (the queue owns the Chrome profile)")
        allow_indeed = False
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    now = dt.datetime.now()
    items = apply_board.build_items(include_closed=True)
    todo = [i for i in items if (a.only and i["id"] in a.only) or (not a.only and (
        i["id"] not in cache or cache[i["id"]].get("status") == "unknown"
        or now - dt.datetime.fromisoformat(cache[i["id"]]["checked"]) > dt.timedelta(days=a.max_age_days)))]
    print(f"checking {len(todo)} of {len(items)} rows")
    for n, i in enumerate(todo, 1):
        status, via = verdict(i, allow_indeed)
        cache[i["id"]] = {"status": status, "via": via, "checked": now.isoformat(timespec="seconds")}
        print(f"[{n}/{len(todo)}] {status:7} {via:8} {i['company']} - {i['role']}", flush=True)
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    counts = {}
    for i in items:
        s = cache.get(i["id"], {}).get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    print("LIVENESS DONE", counts)


if __name__ == "__main__":
    main()
