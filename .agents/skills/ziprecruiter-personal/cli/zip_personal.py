#!/usr/bin/env python3
"""ZipRecruiter jobseeker search through a human-authenticated Chrome profile.

The ZipRecruiter twin of `indeed-personal`: the candidate's account is signed in ONCE (by a person) in a visible Chrome window,
the profile stays authenticated on disk in its own
directory, and later commands drive that same profile. Credentials are never read, stored,
typed or transmitted by this tool.

Browser plumbing (window handling, NEEDS_HUMAN alerts, captcha waits) is
shared with indeed_personal.py and imported from it, so the two stay identical.

Usage:
    python .agents/skills/ziprecruiter-personal/cli/zip_personal.py login
    python .agents/skills/ziprecruiter-personal/cli/zip_personal.py status
    python .agents/skills/ziprecruiter-personal/cli/zip_personal.py probe <url> [--out DIR]
    python .agents/skills/ziprecruiter-personal/cli/zip_personal.py search -q "<keywords>" -l "<City, ST>" --jobage 14
    python .agents/skills/ziprecruiter-personal/cli/zip_personal.py detail <job url>

WARNING - personal use only. Automated access is against ZipRecruiter's Terms of Service, on
the same footing as indeed-personal: low volume, paced, a person (the user) solves every captcha.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, ".agents", "skills", "indeed-personal", "cli"))
import indeed_personal as ip  # noqa: E402  (shared browser plumbing)

PROFILE_DIR = os.getenv("ZIP_PERSONAL_PROFILE_DIR", os.path.join(REPO, "job_scraper", ".zip_profile"))
STATE_FILE = os.path.join(REPO, "job_scraper", ".zip_session.json")
ip.PROFILE_DIR = PROFILE_DIR          # _context() reads this module global
BASE = "https://www.ziprecruiter.com"
LOGIN_URL = BASE + "/login"
LOGIN_WAIT_S = int(os.getenv("ZIP_LOGIN_WAIT", "900"))


def _log(msg: str) -> None:
    print(f"[zip] {msg}", file=sys.stderr, flush=True)


def _alert(reason: str, detail: str = "") -> None:
    ip.human_alert("ZipRecruiter: " + reason, detail)


def _save_state(**fields) -> None:
    state = {}
    try:
        state = json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    state.update(fields)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), indent=2)


def _goto(page, url: str, timeout: int = 60000) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as e:  # noqa: BLE001
        _log(f"goto {url[:80]} raised {type(e).__name__}")
    ip._settle(page, 2500)
    if ip._challenged(page):
        ip._await_challenge(page)


def _looks_logged_in(page) -> bool | None:
    """Tri-state DOM hint: True when an account/profile control is visible, False when a
    'Log in'/'Sign in' link is, None when neither. Refined once real markup is probed."""
    js = """
    () => {
      const txt = el => (el.innerText || el.textContent || '').trim().toLowerCase();
      const links = [...document.querySelectorAll('a,button')].filter(e => e.offsetParent !== null);
      if (links.some(e => /^(log in|sign in)$/.test(txt(e)))) return false;
      if (links.some(e => /^(my jobs|profile|my profile|account|sign out|log out)$/.test(txt(e)))
          || document.querySelector('[data-testid*="account" i], [aria-label*="account" i], [href*="/candidate/"]'))
        return true;
      return null;
    }"""
    try:
        return page.evaluate(js)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# parsing: the pages are a Next.js app that streams its data as React Server Component
# chunks (self.__next_f.push). Job cards and job details are plain JSON inside that stream,
# far steadier than the rendered markup. Measured 2026-10-14.
# --------------------------------------------------------------------------

import re  # noqa: E402

APPLY_TYPES = {"ONE_CLICK_APPLY": "one_click", "OPEN_EXTERNAL": "external"}
MODES = {"LOCATION_TYPE_NAME_REMOTE": "remote", "LOCATION_TYPE_NAME_HYBRID": "hybrid",
         "LOCATION_TYPE_NAME_IN_PERSON": "in person",
         "LOCATION_TYPE_NAME_REMOTE_OPTIONAL": "remote optional", "LOCATION_TYPE_NAME_UNSPECIFIED": "unspecified"}


def _rsc_stream(html: str) -> str:
    out = []
    for c in re.findall(r"self\.__next_f\.push\((\[.*?\])\)</script>", html, re.S):
        try:
            v = json.loads(c)
        except ValueError:
            continue
        if len(v) > 1 and isinstance(v[1], str):
            out.append(v[1])
    return "".join(out)


def _rsc_object(stream: str, key: str):
    """The JSON object that starts with {"<key>": ... in the stream, or None."""
    i = stream.find('{"%s"' % key)
    if i < 0:
        i = stream.find('"%s":' % key)
        if i < 0:
            return None
        try:
            return json.JSONDecoder().raw_decode(stream[i + len(key) + 3:])[0]
        except ValueError:
            return None
    try:
        return json.JSONDecoder().raw_decode(stream[i:])[0][key]
    except ValueError:
        return None


def _rsc_text(stream: str, ref: str) -> str:
    """Resolve an RSC text reference like "$25" (a chunk written as 25:T<hexlen>,<text>)."""
    m = re.fullmatch(r"\$([0-9a-f]+)", ref or "")
    if not m:
        return ref or ""
    hdr = re.search(r"(?:^|\n)%s:T([0-9a-f]+)," % m.group(1), stream)
    if not hdr:
        return ""
    raw = stream[hdr.end():].encode("utf-8")[: int(hdr.group(1), 16)]
    return raw.decode("utf-8", "replace")


def _pay_text(pay: dict) -> str | None:
    if not pay or not pay.get("metadata", {}).get("visible", True) or not (pay.get("min") or pay.get("max")):
        return None
    unit = {"PAY_INTERVAL_HOUR": "hr", "PAY_INTERVAL_YEAR": "yr", "PAY_INTERVAL_MONTH": "mo",
            "PAY_INTERVAL_WEEK": "wk", "PAY_INTERVAL_DAY": "day"}.get(pay.get("interval"), "")
    fmt = lambda v: ("$%gK" % (v / 1000)) if unit == "yr" and v >= 1000 else ("$%g" % v)
    lo, hi = pay.get("min"), pay.get("max")
    txt = fmt(lo) if lo == hi or not hi else f"{fmt(lo)} - {fmt(hi)}"
    if pay.get("metadata", {}).get("sourceType") not in (None, "SOURCE_PROVIDED"):
        txt += " (estimated)"
    return f"{txt}/{unit}" if unit else txt


def _card(c: dict) -> dict:
    url = c.get("rawCanonicalZipJobPageUrl") or ""
    jid = (re.search(r"[?&]jid=([0-9a-f]+)", url) or [None, None])[1]
    modes = [MODES.get(t.get("name"), t.get("name")) for t in c.get("locationTypes") or []]
    loc = (c.get("location") or {}).get("displayName") or ""
    loc = re.sub(r"\s+US(\s+\d{5})?$", "", loc)
    posted = ((c.get("status") or {}).get("rollingPostedAtUtc") or (c.get("status") or {}).get("postedAtUtc") or "")[:10]
    apply = (c.get("applyButtonConfig") or {}).get("applyButtonType")
    status = (c.get("applyButtonConfig") or {}).get("currentApplicationStatus") or {}
    return {
        "id": jid or c.get("listingKey"),
        "listing_key": c.get("listingKey"),
        "title": c.get("title"),
        "company": (c.get("company") or {}).get("name"),
        "location": loc + (" (" + ", ".join(modes) + ")" if modes else ""),
        "work_mode_facet": modes,
        "date": posted or None,
        "salary": {"text": _pay_text(c.get("pay"))} if _pay_text(c.get("pay")) else None,
        "apply_type": APPLY_TYPES.get(apply, (apply or "").lower() or None),
        "already_applied": bool(status),
        "url": (BASE + url) if url.startswith("/") else url,
    }


def _search_url(q: str, loc: str, jobage: int, remote: bool, page: int) -> str:
    from urllib.parse import urlencode
    params = {"search": q, "location": loc or "", "days": jobage or ""}
    if remote:
        params["refine_by_location_type"] = "only_remote"
    if page > 1:
        params["page"] = page
    return BASE + "/jobs-search?" + urlencode({k: v for k, v in params.items() if v != ""})


def cmd_search(args) -> int:
    from patchright.sync_api import sync_playwright
    results, seen, total = [], set(), None
    with sync_playwright() as p:
        ctx = ip._context(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for n in range(1, args.pages + 1):
                _goto(page, _search_url(args.query, args.location, args.jobage, args.remote, n))
                if "/login" in (page.url or "") or "/authn" in (page.url or ""):
                    print(json.dumps({"error": "logged_out", "action": "run `login`"})); return 2
                stream = _rsc_stream(page.content())
                cards = _rsc_object(stream, "serializedJobCardsData") or {}
                total = cards.get("totalListings", total)
                km = cards.get("jobKeysMap") or {}
                keys = [k.get("listingKey") for k in cards.get("jobKeys") or []] or list(km)
                new = 0
                for k in keys:
                    c = km.get(k)
                    if not c or k in seen:
                        continue
                    seen.add(k); new += 1
                    results.append(_card(c))
                if not new or len(results) >= (total or 0) or len(results) >= args.limit:
                    break
                time.sleep(2 + 2 * __import__("random").random())
        finally:
            ctx.close()
    results = results[: args.limit]
    out = {"meta": {"count": len(results), "total": total, "query": args.query, "location": args.location,
                    "jobage": args.jobage, "remote": args.remote, "portal": "ziprecruiter-personal",
                    "authenticated": True},
           "results": results}
    if args.format == "table":
        for r in results:
            print(f"{(r['date'] or ''):10} {r['apply_type'] or '':9} {r['title'][:45]:45} | {r['company'][:28]:28} | {r['location'][:30]}")
    else:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _html_to_text(html: str) -> str:
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", html or "")
    t = re.sub(r"(?i)<li[^>]*>", "- ", t)
    t = re.sub(r"<[^>]+>", "", t)
    import html as _h
    t = _h.unescape(t)
    return "\n".join(l.strip() for l in t.splitlines() if l.strip())


def cmd_detail(args) -> int:
    """One posting: full description, work mode, pay, apply type, live/closed. Accepts a job URL
    (canonical /c/...?jid=, /jobs/...?lvk=) or a listing key."""
    from patchright.sync_api import sync_playwright
    target = args.job
    if not target.startswith("http"):
        target = BASE + "/jobs-search?lk=" + target
    with sync_playwright() as p:
        ctx = ip._context(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _goto(page, target)
            ip._settle(page, 1500)
            stream = _rsc_stream(page.content())
            d = _rsc_object(stream, "jobDetails") or {}
            data = _card(d) if d else {"url": page.url}
            data["url"] = data.get("url") or page.url
            jid = re.search(r"[?&]jid=([0-9a-f]+)", args.job)
            if jid:
                data["id"] = jid.group(1)   # the job page itself carries only the listing key
            data["isActive"] = bool((d.get("status") or {}).get("isActive")) if d else False
            data["description"] = _html_to_text(_rsc_text(stream, d.get("htmlFullDescription", ""))) if d else ""
            if d and not data["description"]:
                try:
                    data["description"] = page.evaluate(
                        "() => { const t = document.body.innerText; const i = t.indexOf('Job description');"
                        " return i >= 0 ? t.slice(i + 15) : t; }").strip()
                except Exception:  # noqa: BLE001
                    pass
            if not d:
                data["error"] = "no job details on the page (closed, removed, or layout changed)"
            data["final_url"] = page.url
        finally:
            ctx.close()
    print(json.dumps({"result": data}, indent=2, ensure_ascii=False))
    return 0


PROFILE_URL = BASE + "/profile"


def _profile_resume(page) -> dict:
    """The resume card on /profile: file name and 'Added ...' line, read from visible text."""
    js = r"""() => {
      const t = document.body.innerText;
      const i = t.indexOf('\nResume\n');
      if (i < 0) return {};
      const lines = t.slice(i + 8).split('\n').map(s => s.trim()).filter(Boolean);
      return {file: lines[0] || '', added: (lines.find(l => l.startsWith('Added')) || '')};
    }"""
    try:
        return page.evaluate(js) or {}
    except Exception:  # noqa: BLE001
        return {}


def _replace_resume(page, pdf: str, out_dir: str) -> dict:
    """Upload pdf via Replace on /profile and confirm the profile now shows that file name."""
    rep = {}
    _goto(page, PROFILE_URL)
    rep["before"] = _profile_resume(page)
    want = os.path.basename(pdf)
    if rep["before"].get("file") == want:
        rep["status"] = "ok"; rep["after"] = rep["before"]; rep["note"] = "already the profile resume"
        return rep
    btn = page.get_by_role("button", name="Replace").first
    if not btn.count():
        rep["status"] = "no_replace_button"; return rep
    try:
        with page.expect_file_chooser(timeout=8000) as fc:
            btn.click()
        fc.value.set_files(pdf)
    except Exception as e:  # noqa: BLE001
        rep["status"] = "no_file_chooser: " + type(e).__name__; return rep
    for _ in range(20):
        ip._settle(page, 1500)
        for label in ("Save", "Done", "Upload", "Confirm", "Continue", "Replace"):
            b = page.locator("[role=dialog]").get_by_role("button", name=label)
            if b.count() and b.first.is_visible():
                b.first.click(); ip._settle(page, 1500); break
        if _profile_resume(page).get("file") == want:
            break
    _goto(page, PROFILE_URL)
    rep["after"] = _profile_resume(page)
    page.screenshot(path=os.path.join(out_dir, "resume_after.png"))
    rep["status"] = "ok" if rep["after"].get("file") == want else "not_confirmed"
    return rep


APPLIED_RE = re.compile(r"application (was )?(sent|submitted)|you applied|applied on|you've applied|already applied", re.I)


def _job_state(page) -> dict:
    stream = _rsc_stream(page.content())
    d = _rsc_object(stream, "jobDetails") or {}
    cfg = d.get("applyButtonConfig") or {}
    try:
        body = page.evaluate("() => document.body.innerText")
    except Exception:  # noqa: BLE001
        body = ""
    return {"apply_type": cfg.get("applyButtonType"), "status": cfg.get("currentApplicationStatus") or {},
            "isActive": (d.get("status") or {}).get("isActive"), "applied_text": bool(APPLIED_RE.search(body)),
            "title": d.get("title"), "company": (d.get("company") or {}).get("name")}


def cmd_apply(args) -> int:
    """Apply to one posting with ZipRecruiter 1-Click Apply, sending a tailored resume.
    Order: check the job (live, 1-Click, not already applied) -> replace the profile resume with
    --resume and confirm it -> (only with --submit) click 1-Click Apply -> record what appears.
    Without --submit it stops before the click (prepare mode). --present waits up to
    --wait-human seconds for a person to finish anything that appears (questions, captcha)."""
    from patchright.sync_api import sync_playwright
    out_dir = args.out or os.path.join(REPO, "job_scraper", ".zip_probe", "apply_" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    rep = {"job": args.job, "resume": os.path.abspath(args.resume), "steps": []}
    def done(status, code):
        rep["status"] = status
        json.dump(rep, open(os.path.join(out_dir, "apply_report.json"), "w"), indent=2)
        print(json.dumps(rep, indent=2)); return code
    if not os.path.isfile(rep["resume"]):
        return done("resume_missing", 2)
    with sync_playwright() as p:
        ctx = ip._context(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _goto(page, args.job)
            st = _job_state(page); rep["before"] = st
            page.screenshot(path=os.path.join(out_dir, "01_job.png"))
            if not st.get("title"):
                return done("job_not_found", 1)
            if st.get("isActive") is False:
                return done("expired", 1)
            if st["status"] or st["applied_text"]:
                return done("already_applied", 0)
            if st.get("apply_type") != "ONE_CLICK_APPLY":
                return done("not_one_click (" + str(st.get("apply_type")) + ")", 1)
            rr = _replace_resume(page, rep["resume"], out_dir); rep["resume_swap"] = rr
            if rr.get("status") != "ok":
                return done("resume_not_confirmed", 1)
            _goto(page, args.job)
            btn = page.get_by_role("button", name=re.compile(r"1-Click Apply", re.I)).first
            if not btn.count():
                page.screenshot(path=os.path.join(out_dir, "02_no_button.png"))
                return done("no_one_click_button", 1)
            if not args.submit:
                btn.scroll_into_view_if_needed()
                page.screenshot(path=os.path.join(out_dir, "02_ready.png"))
                return done("ready (prepare mode, not clicked)", 0)
            btn.click()
            rep["steps"].append("clicked 1-Click Apply")
            deadline = time.time() + (args.wait_human if args.present else 25)
            alerted = False
            while time.time() < deadline:
                ip._settle(page, 2500)
                try:
                    body = page.evaluate("() => document.body.innerText")
                    dialog = page.evaluate("() => [...document.querySelectorAll('[role=dialog]')].map(d => d.innerText).join('\\n---\\n')")
                except Exception:  # noqa: BLE001
                    body, dialog = "", ""
                if APPLIED_RE.search(body):
                    rep["confirmation"] = (re.search(r"[^\n]*" + APPLIED_RE.pattern + r"[^\n]*", body, re.I) or [""])[0][:200]
                    page.screenshot(path=os.path.join(out_dir, "03_confirmed.png"))
                    return done("submitted", 0)
                if dialog and not alerted:
                    rep["dialog"] = dialog[:3000]
                    page.screenshot(path=os.path.join(out_dir, "03_dialog.png"))
                    try:  # the form's markup, for building auto-answers later
                        html = page.evaluate("() => [...document.querySelectorAll('[role=dialog]')].map(d => d.outerHTML).join('\\n<!-- next -->\\n')")
                        open(os.path.join(out_dir, "03_dialog.html"), "w", encoding="utf-8").write(html)
                    except Exception:  # noqa: BLE001
                        pass
                    if not args.present:
                        # Unattended: questions need the candidate. Close the form unsent and park.
                        try:
                            page.keyboard.press("Escape")
                        except Exception:  # noqa: BLE001
                            pass
                        return done("needs_answers", 1)
                    _alert("1-Click Apply opened questions", "answer them in the Chrome window and submit")
                    alerted = True
            page.screenshot(path=os.path.join(out_dir, "03_end.png"))
            _goto(page, args.job)
            st2 = _job_state(page); rep["after"] = st2
            if st2["status"] or st2["applied_text"]:
                return done("submitted", 0)
            return done("unconfirmed", 1)
        finally:
            ctx.close()


def cmd_set_resume(args) -> int:
    """Replace the profile's resume with a tailored PDF, then confirm the profile shows that
    exact file name. 1-Click Apply sends the profile resume, so this runs before every apply.
    --dry-run clicks Replace, screenshots what opens, and cancels without uploading."""
    from patchright.sync_api import sync_playwright
    pdf = os.path.abspath(args.pdf) if args.pdf else None
    if not args.dry_run and (not pdf or not os.path.isfile(pdf)):
        print(json.dumps({"status": "error", "reason": "pdf not found"})); return 2
    out_dir = args.out or os.path.join(REPO, "job_scraper", ".zip_probe")
    os.makedirs(out_dir, exist_ok=True)
    report = {"pdf": pdf}
    with sync_playwright() as p:
        ctx = ip._context(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _goto(page, PROFILE_URL)
            report["before"] = _profile_resume(page)
            btn = page.get_by_role("button", name="Replace").first
            if not btn.count():
                report["status"] = "no_replace_button"; print(json.dumps(report, indent=2)); return 1
            chooser = None
            try:
                with page.expect_file_chooser(timeout=6000) as fc:
                    btn.click()
                chooser = fc.value
            except Exception:  # noqa: BLE001  (a modal opened instead of the OS picker)
                pass
            ip._settle(page, 1500)
            page.screenshot(path=os.path.join(out_dir, "replace_dialog.png"))
            report["opened"] = "file_chooser" if chooser else "dialog"
            if args.dry_run:
                try:
                    report["dialog_text"] = page.evaluate(
                        "() => [...document.querySelectorAll('[role=dialog]')].map(d => d.innerText).join('\\n---\\n')")[:1500]
                    page.keyboard.press("Escape")
                except Exception:  # noqa: BLE001
                    pass
                report["status"] = "dry_run"; print(json.dumps(report, indent=2)); return 0
            if chooser:
                chooser.set_files(pdf)
            else:
                inp = page.locator("[role=dialog] input[type=file], input[type=file]").first
                if not inp.count():
                    report["status"] = "no_file_input"; print(json.dumps(report, indent=2)); return 1
                inp.set_input_files(pdf)
            want = os.path.basename(pdf)
            for _ in range(20):            # upload + parse can take a few seconds
                ip._settle(page, 1500)
                for label in ("Save", "Done", "Upload", "Confirm", "Continue"):
                    b = page.locator("[role=dialog]").get_by_role("button", name=label)
                    if b.count() and b.first.is_visible():
                        b.first.click(); ip._settle(page, 1500); break
                cur = _profile_resume(page)
                if cur.get("file") == want:
                    break
            _goto(page, PROFILE_URL)
            report["after"] = _profile_resume(page)
            page.screenshot(path=os.path.join(out_dir, "resume_after.png"))
            report["status"] = "ok" if report["after"].get("file") == want else "not_confirmed"
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "ok" else 1
        finally:
            ctx.close()


def cmd_status(args) -> int:
    from patchright.sync_api import sync_playwright
    if not os.path.isdir(PROFILE_DIR):
        print(json.dumps({"authenticated": False, "reason": "no profile - run `login`"})); return 1
    with sync_playwright() as p:
        ctx = ip._context(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _goto(page, BASE + "/jobseeker/home")
            ok = "/login" not in page.url and "/authn" not in page.url and _looks_logged_in(page) is not False
            print(json.dumps({"authenticated": ok, "url": page.url, "profile_dir": PROFILE_DIR}, indent=2))
            return 0 if ok else 1
        finally:
            ctx.close()


def cmd_login(args) -> int:
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = ip._context(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _goto(page, LOGIN_URL)
            print("\n  A Chrome window is open on ZipRecruiter's login page.")
            if ip.VIEWER_URL:
                print("  It is on a remote screen: open %s" % ip.VIEWER_URL)
            print("  Sign in with your own account (Google/Apple/email code all fine), then stay")
            print("  on any ZipRecruiter page. This waits until you are signed in and says DONE.\n", flush=True)
            _alert("sign-in window is open", "sign in there; Claude never sees it")
            deadline, stable, beat = time.time() + (args.timeout or LOGIN_WAIT_S), 0, 0.0
            while time.time() < deadline:
                ip._settle(page, 3000)
                url = page.url or ""
                if time.time() - beat > 20:
                    _log("waiting... " + url[:100]); beat = time.time()
                if "/login" in url or "/authn" in url or "accounts.google" in url or "appleid" in url:
                    stable = 0; continue
                if _looks_logged_in(page) is True:
                    stable += 1
                    if stable >= 2:
                        _save_state(last_login=dt.datetime.now().isoformat(timespec="seconds"), profile_dir=PROFILE_DIR)
                        print("\n  DONE - signed in. Profile saved to %s\n" % PROFILE_DIR, flush=True)
                        ip._settle(page, 3000)
                        return 0
                else:
                    stable = 0
            print("\n  TIMED OUT - no signed-in session detected (the profile is kept).\n", flush=True)
            return 1
        finally:
            ctx.close()


def cmd_probe(args) -> int:
    """Load one URL in the signed-in profile and save HTML, a screenshot and the visible text,
    for working out selectors. Development aid."""
    from patchright.sync_api import sync_playwright
    out = args.out or os.path.join(REPO, "job_scraper", ".zip_probe")
    os.makedirs(out, exist_ok=True)
    with sync_playwright() as p:
        ctx = ip._context(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _goto(page, args.url)
            ip._settle(page, args.wait * 1000)
            stem = os.path.join(out, args.name or "probe")
            open(stem + ".html", "w", encoding="utf-8").write(page.content())
            open(stem + ".txt", "w", encoding="utf-8").write(page.evaluate("() => document.body.innerText"))
            page.screenshot(path=stem + ".png", full_page=False)
            print(json.dumps({"url": page.url, "title": page.title(), "logged_in_hint": _looks_logged_in(page),
                              "saved": stem}, indent=2))
            return 0
        finally:
            ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login"); s.add_argument("--timeout", type=int, default=0)
    sub.add_parser("status")
    s = sub.add_parser("search"); s.add_argument("-q", "--query", required=True)
    s.add_argument("-l", "--location", default=""); s.add_argument("--jobage", type=int, default=14)
    s.add_argument("--remote", action="store_true", help="ZipRecruiter's remote-only facet (verify per posting)")
    s.add_argument("-n", "--limit", type=int, default=40); s.add_argument("--pages", type=int, default=2)
    s.add_argument("--format", choices=["json", "table"], default="json")
    s = sub.add_parser("detail"); s.add_argument("job")
    s = sub.add_parser("apply"); s.add_argument("job"); s.add_argument("--resume", required=True)
    s.add_argument("--submit", action="store_true"); s.add_argument("--present", action="store_true")
    s.add_argument("--wait-human", type=int, default=900); s.add_argument("--out")
    s = sub.add_parser("set-resume"); s.add_argument("pdf", nargs="?"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--out")
    s = sub.add_parser("probe"); s.add_argument("url"); s.add_argument("--out"); s.add_argument("--name")
    s.add_argument("--wait", type=int, default=3)
    a = ap.parse_args()
    return {"login": cmd_login, "probe": cmd_probe, "status": cmd_status, "search": cmd_search,
            "detail": cmd_detail, "set-resume": cmd_set_resume, "apply": cmd_apply}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
