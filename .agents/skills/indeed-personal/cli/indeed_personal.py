#!/usr/bin/env python3
"""Indeed jobseeker search through a human-authenticated Chrome profile.

Indeed cannot self-login (Google SSO plus one-time human 2FA), and its
jobseeker pages 403 plain `requests` calls. So this does not log in and
does not scrape anonymously: a human signs in ONCE in a visible Chrome
window, the profile stays authenticated on disk, and every later search
drives that same profile headlessly.

Credentials are never read, stored, typed, or transmitted by this tool.
The only thing on disk is the Chrome profile the human created.

Usage:
    python .agents/skills/indeed-personal/cli/indeed_personal.py login
    python .agents/skills/indeed-personal/cli/indeed_personal.py status
    python .agents/skills/indeed-personal/cli/indeed_personal.py search \
        --query "operations associate" --location "Remote" --jobage 14 --limit 20
    python .agents/skills/indeed-personal/cli/indeed_personal.py detail 1a2b3c4d5e6f7890

WARNING - Personal use only. Automated access is against Indeed's Terms of
Service, exactly as with `linkedin-search`. Keep volume low, never use it
commercially or for bulk collection, and run it on your own responsibility.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))

PROFILE_DIR = os.getenv(
    "INDEED_PERSONAL_PROFILE_DIR",
    os.path.join(REPO, "job_scraper", ".indeed_profile"),
)
STATE_FILE = os.path.join(REPO, "job_scraper", ".indeed_session.json")

SEARCH_URL = "https://www.indeed.com/jobs"
AUTH_HOST = "secure.indeed.com/auth"
BOOTSTRAP_WAIT_SECONDS = int(os.getenv("INDEED_BOOTSTRAP_WAIT", "600"))
CHANNEL = os.getenv("INDEED_PERSONAL_CHANNEL", "chrome")

# Deliberately minimal, and deliberately NOT the jovie/care360 arg set.
# Chrome does not merge repeated --disable-features flags, so piling them
# on silently drops earlier ones. patchright already handles automation
# fingerprinting; adding --disable-blink-features=AutomationControlled on
# top is what makes it detectable.
_ARGS = ["--no-first-run", "--no-default-browser-check"]

# Optional headless-machine setup (docs/remote-server.md): Chrome runs headed on a virtual
# display (Xvfb :99) that the user watches through a viewer such as noVNC. Default DISPLAY
# here so every command works without an env prefix, and size the window to the screen.
def _xvfb_up() -> bool:
    # Under WSL, WSLg owns /tmp/.X11-unix, so Xvfb :99 only has its abstract socket.
    if os.path.exists("/tmp/.X11-unix/X99"):
        return True
    try:
        with open("/proc/net/unix", encoding="utf-8") as fh:
            return "@/tmp/.X11-unix/X99" in fh.read()
    except OSError:
        return False


if sys.platform.startswith("linux"):
    if not os.environ.get("DISPLAY") and _xvfb_up():
        os.environ["DISPLAY"] = ":99"
    _ARGS += ["--window-size=1440,900", "--window-position=0,0"]


def _viewer_url() -> str:
    """Optional link to a remote viewer of the browser (docs/remote-server.md). Machine-specific,
    so it lives outside the repo."""
    url = os.environ.get("INDEED_VIEWER_URL", "")
    if not url:
        try:
            with open(os.path.expanduser("~/.config/jobsearch/viewer_url"), encoding="utf-8") as fh:
                url = fh.read().strip()
        except OSError:
            pass
    return url


VIEWER_URL = _viewer_url()
ALERT_REPEAT_S = int(os.environ.get("INDEED_ALERT_REPEAT", "300"))
_last_alert: dict = {}


def _ntfy_topic() -> str:
    """Optional ntfy.sh topic for phone pushes (docs/remote-server.md), kept outside the repo
    in ~/.config/jobsearch/ntfy_topic. The topic name is the only secret."""
    topic = os.environ.get("INDEED_NTFY_TOPIC", "")
    if not topic:
        try:
            with open(os.path.expanduser("~/.config/jobsearch/ntfy_topic"), encoding="utf-8") as fh:
                topic = fh.read().strip()
        except OSError:
            pass
    return topic


def _ntfy(title: str, body: str) -> None:
    """Push to the user's phone via ntfy.sh, when a topic is configured. Tapping the
    notification opens the viewer link, if one is set.
    Best effort: a network failure never interrupts the run."""
    topic = _ntfy_topic()
    if not topic:
        return
    import urllib.request
    headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": "high",
               "Tags": "warning"}
    if VIEWER_URL:
        headers["Click"] = VIEWER_URL
    try:
        req = urllib.request.Request("https://ntfy.sh/" + topic, data=body.encode("utf-8"),
                                     headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:  # noqa: BLE001
        pass


def _beep() -> None:
    """One short system sound, best effort. Silent on platforms without one."""
    try:
        if os.name == "nt":
            import winsound
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def human_alert(reason: str, detail: str = "") -> None:
    """Get the user's attention. A sound where the platform has one (Windows system sound, macOS
    `afplay`), plus one `NEEDS_HUMAN: ...` line on stderr that the Claude session watching the
    run relays in chat, and an ntfy push when a topic is configured. Callers loop every 20-30 s,
    so the line and the push repeat at most every ALERT_REPEAT_S per reason.
    INDEED_CAPTCHA_SOUND=0 silences everything."""
    if os.environ.get("INDEED_CAPTCHA_SOUND", "1") == "0":
        return
    _beep()
    now = time.time()
    if now - _last_alert.get(reason, 0.0) < ALERT_REPEAT_S:
        return
    _last_alert[reason] = now
    msg = "NEEDS_HUMAN: " + reason
    if detail:
        msg += " | " + detail
    if VIEWER_URL:
        msg += " | " + VIEWER_URL
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
    _ntfy("Indeed needs you: " + reason,
          (detail + "\n" if detail else "") + ("Tap to open the viewer." if VIEWER_URL else ""))


# Windows consoles default to cp1252, so a redirected `--format json`
# run writes mojibake and the reader blows up with "codec can't decode
# byte 0xb7". Measured 2026-09-13: 3 of 6 discovery files were corrupted
# this way before this line existed. Force UTF-8 on both streams.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def _log(msg: str) -> None:
    print(f"[indeed] {msg}", file=sys.stderr, flush=True)


def _settle(page, ms: int) -> None:
    try:
        page.wait_for_timeout(ms)
    except Exception:  # noqa: BLE001
        time.sleep(ms / 1000)


def _polite() -> None:
    """Human-ish gap between page loads. Volume discipline is the whole
    reason this stays usable."""
    time.sleep(random.uniform(2.0, 4.5))


# A real Chrome UA. Headless Chrome otherwise advertises "HeadlessChrome"
# in its user agent, which is the single loudest automation tell. The
# care360 minter overrides this for exactly the same reason.
REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def _context(p, headless: bool):
    os.makedirs(PROFILE_DIR, exist_ok=True)
    kwargs = dict(
        user_data_dir=PROFILE_DIR,
        headless=headless,
        no_viewport=True,
        args=list(_ARGS),
    )
    if not headless and os.environ.get("INDEED_BACKGROUND") == "1":
        # Keep a headed (signed-in, captcha-capable) window out of the user's way while they work
        # (2026-09-27). Throttling flags keep the minimized window rendering normally.
        kwargs["args"] += ["--start-minimized", "--disable-backgrounding-occluded-windows",
                           "--disable-renderer-backgrounding", "--disable-background-timer-throttling"]
    if CHANNEL:
        kwargs["channel"] = CHANNEL
    if headless:
        kwargs["user_agent"] = REAL_UA
    ctx = p.chromium.launch_persistent_context(**kwargs)
    if not headless and os.environ.get("INDEED_BACKGROUND") == "1":
        # Measured 2026-10-09: Chrome ignores --start-minimized when the persistent profile
        # restores its last window state, so the window opened in front of the user's work.
        # Minimize explicitly via CDP, for the restored page and any page opened later.
        for pg in ctx.pages:
            _minimize(pg)
        ctx.on("page", _minimize)
    return ctx


def _minimize(page) -> None:
    """Minimize the window holding this page (best effort, never fails a run)."""
    try:
        cdp = page.context.new_cdp_session(page)
        wid = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
        cdp.detach()
    except Exception:  # noqa: BLE001
        pass


def _save_state(**fields) -> None:
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            state = json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            state = {}
    state.update(fields)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def _rel_date_to_iso(text: str, today: dt.date) -> str | None:
    """'Posted 3 days ago' / 'Active 2 days ago' / 'Today' -> ISO date."""
    if not text:
        return None
    t = text.lower()
    if "just posted" in t or "today" in t:
        return today.isoformat()
    m = re.search(r"(\d+)\+?\s*day", t)
    if m:
        return (today - dt.timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\+?\s*hour", t)
    if m:
        return today.isoformat()
    m = re.search(r"(\d+)\+?\s*month", t)
    if m:
        return (today - dt.timedelta(days=30 * int(m.group(1)))).isoformat()
    return None


def _is_placeholder_jk(jk: str) -> bool:
    """Reject Indeed's synthetic demo cards.

    Measured 2026-09-13: the result DOM carries planted cards whose job
    keys are "cdef0123456789ab" and "a1b2c3d4e5f67890" - each uses all 16
    hex digits exactly once, and fetching one returns "We can't find this
    page". A real 16-char key hitting all 16 distinct digits has odds of
    roughly 2 in 10 million, so this test is safe and catches the whole
    family rather than blacklisting two strings.
    """
    jk = (jk or "").lower()
    return len(jk) == 16 and set(jk) == set("0123456789abcdef")


def _from_mosaic(page, today: dt.date) -> list[dict]:
    """Primary path: Indeed embeds the result set as JSON in the page.

    Far more reliable than scraping rendered cards, and it carries real
    epoch timestamps instead of 'Posted 5 days ago'.
    """
    js = """
    () => {
      try {
        const pd = window.mosaic && window.mosaic.providerData;
        const p = pd && pd["mosaic-provider-jobcards"];
        const r = p && p.metaData && p.metaData.mosaicProviderJobCardsModel;
        if (r && r.results) return JSON.stringify(r.results);
      } catch (e) {}
      try {
        if (window._initialData && window._initialData.jobList)
          return JSON.stringify(window._initialData.jobList);
      } catch (e) {}
      return null;
    }
    """
    try:
        raw = page.evaluate(js)
    except Exception:  # noqa: BLE001
        return []
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []

    out = []
    for r in rows:
        jk = r.get("jobkey") or r.get("jobKey") or r.get("jk")
        if not jk or _is_placeholder_jk(jk):
            continue
        date = None
        for key in ("pubDate", "createDate", "publishDate"):
            v = r.get(key)
            if isinstance(v, (int, float)) and v > 0:
                date = dt.datetime.utcfromtimestamp(v / 1000).date().isoformat()
                break
        if not date:
            date = _rel_date_to_iso(r.get("formattedRelativeTime") or "", today)
        loc = r.get("formattedLocation") or r.get("jobLocationCity") or ""
        if r.get("remoteLocation") and "remote" not in loc.lower():
            loc = (loc + " (Remote)").strip()
        salary = None
        ex = r.get("extractedSalary") or {}
        if ex.get("min") or ex.get("max"):
            salary = {"min": ex.get("min"), "max": ex.get("max"),
                      "type": ex.get("type")}
        elif r.get("salarySnippet", {}).get("text"):
            salary = {"text": r["salarySnippet"]["text"]}
        out.append({
            "id": jk,
            "title": (r.get("displayTitle") or r.get("title") or "").strip(),
            "company": (r.get("company") or "").strip(),
            "location": loc.strip(),
            "date": date,
            "salary": salary,
            "url": f"https://www.indeed.com/viewjob?jk={jk}",
        })
    return out


def _from_dom(page, today: dt.date) -> list[dict]:
    """Parse the rendered result cards.

    This is the NORMAL path, not a fallback: measured 2026-09-13,
    `window.mosaic` is absent from current result pages entirely.

    Iterate `div.job_seen_beacon`, NOT `[data-jk]`. The page carries
    roughly twice as many `[data-jk]` nodes as real cards (anchors plus
    other elements), and iterating them produced duplicate rows and one
    placeholder id (`cdef0123456789ab`, sequential hex) that is not a job
    at all. One beacon == one posting.
    """
    js = """
    () => {
      const out = [];
      document.querySelectorAll('div.job_seen_beacon').forEach(card => {
        const a = card.querySelector('a[data-jk]') || card.querySelector('[data-jk]');
        const jk = a && a.getAttribute('data-jk');
        if (!jk) return;
        const pick = sel => { const e = card.querySelector(sel);
                              return e ? e.innerText.trim() : ''; };
        out.push({
          jk,
          title: pick('h2 a span[title], h2 span, a.jcs-JobTitle span')
                 || (a.textContent || '').trim(),
          company: pick('[data-testid="company-name"]'),
          location: pick('[data-testid="text-location"]'),
          // NOTE: [data-testid="timing-attribute"] sounds like a date but
          // holds company + location (measured 2026-09-13). Current result
          // cards carry NO posted date at all, so `date` comes back null
          // and recency is enforced by the fromage= URL parameter instead.
          // The detail fetch supplies a real date via JSON-LD.
          rel: pick('[data-testid="myJobsStateDate"], span.date'),
          salary: pick('[data-testid*="salary-snippet-container"]'),
        });
      });
      return JSON.stringify(out);
    }
    """
    try:
        rows = json.loads(page.evaluate(js) or "[]")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        if not r.get("jk") or _is_placeholder_jk(r["jk"]):
            continue
        out.append({
            "id": r["jk"],
            "title": (r.get("title") or "").strip(),
            "company": (r.get("company") or "").strip(),
            "location": (r.get("location") or "").strip(),
            "date": _rel_date_to_iso(r.get("rel", ""), today),
            "salary": ({"text": r["salary"].strip()} if r.get("salary") else None),
            "url": f"https://www.indeed.com/viewjob?jk={r['jk']}",
        })
    return out


def _await_page(page, timeout_s: int = 40) -> str:
    """Wait out Cloudflare's interstitial and report what we ended on.

    Measured 2026-09-13: a headed run lands on "Just a moment..." and
    clears to real results in about 10 seconds. Parsing before that
    yields zero rows and looks exactly like "no matches", which is the
    failure this function exists to prevent.

    Returns "ok" | "blocked" | "challenge" (still challenging at timeout).

    A challenge that has not cleared on its own after a few seconds is
    usually a click-through captcha. Then this plays a sound (repeated
    every 30s) and stretches the deadline to CAPTCHA_WAIT_S so the user can
    click it (2026-10-08).
    """
    deadline = time.time() + timeout_s
    challenged_since, last_alert = None, 0.0
    while time.time() < deadline:
        try:
            title = (page.title() or "").lower()
        except Exception:  # noqa: BLE001
            title = ""
        if title.startswith("blocked") or "blocked - indeed" in title:
            return "blocked"
        if _challenged(page):
            now = time.time()
            challenged_since = challenged_since or now
            if now - challenged_since >= 8 and now - last_alert >= 30:
                if not last_alert:
                    _log("captcha/challenge on screen - waiting up to %ds for a click"
                         % CAPTCHA_WAIT_S)
                    deadline = max(deadline, challenged_since + CAPTCHA_WAIT_S)
                _alert()
                last_alert = now
        else:
            challenged_since = None
        try:
            cards = page.evaluate(
                "() => document.querySelectorAll('[data-jk]').length")
        except Exception:  # noqa: BLE001
            cards = 0
        if cards:
            return "ok"
        # A real title is NOT enough. Measured 2026-09-13: the results
        # title lands several seconds before the cards hydrate, so
        # returning here parsed an empty DOM and reported "0 results" for
        # a page that had 16 postings on it. Only cards, an explicit
        # empty state, or the timeout may end this loop.
        try:
            empty = page.evaluate(
                "() => !!document.querySelector("
                "'.jobsearch-NoResult, [data-testid=\"empty-state\"]')")
        except Exception:  # noqa: BLE001
            empty = False
        if empty:
            return "ok"
        _settle(page, 2500)
    try:
        t = (page.title() or "").lower()
    except Exception:  # noqa: BLE001
        t = ""
    # Out of time on a page that never challenged: treat as a real, empty
    # result rather than crying challenge.
    return "challenge" if ("just a moment" in t or not t) else "ok"


CAPTCHA_WAIT_S = int(os.environ.get("INDEED_CAPTCHA_WAIT", "300"))
# 2026-10-12: alert only once a challenge has stayed up this long, since many clear alone.
CAPTCHA_ALERT_AFTER_S = int(os.environ.get("INDEED_CAPTCHA_ALERT_AFTER", "15"))


def _challenged(page) -> bool:
    """A Cloudflare/hCaptcha challenge, as opposed to Indeed's hard block."""
    try:
        low = ((page.title() or "") + " " + (page.url or "")).lower()
    except Exception:  # noqa: BLE001
        return False
    return ("just a moment" in low or "cf-challenge" in low
            or "/challenge" in low or "hcaptcha" in low
            or "security check" in low or "verify you are human" in low)


def _alert() -> None:
    """Nudge that a captcha / Cloudflare check needs a human click."""
    human_alert("captcha on Indeed search", "click the check in the Chrome window")


def _await_challenge(page) -> None:
    """Hold on a challenge page, sounding every 30s, until it clears or
    CAPTCHA_WAIT_S runs out. The caller re-checks _blocked() afterwards."""
    start = time.time()
    _settle(page, 8000)  # most interstitials clear themselves (alert waits CAPTCHA_ALERT_AFTER_S)
    if not _challenged(page):
        return
    _log("captcha/challenge on screen - waiting up to %ds for a click" % CAPTCHA_WAIT_S)
    last_alert = 0.0
    while _challenged(page) and time.time() - start < CAPTCHA_WAIT_S:
        if (time.time() - start >= CAPTCHA_ALERT_AFTER_S
                and time.time() - last_alert >= 30):
            _alert()
            last_alert = time.time()
        _settle(page, 2500)
    _settle(page, 3000)


def _blocked(page) -> bool:
    try:
        txt = (page.title() or "") + " " + (page.url or "")
    except Exception:  # noqa: BLE001
        return False
    low = txt.lower()
    return ("just a moment" in low or "cf-challenge" in low
            or "/challenge" in low or "hcaptcha" in low
            # Indeed's own hard block, distinct from a Cloudflare
            # interstitial and NOT self-clearing. Measured 2026-09-13:
            # headless Chrome gets exactly this, headed does not.
            or low.startswith("blocked") or "blocked - indeed" in low)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_login(args) -> int:
    from patchright.sync_api import sync_playwright

    wait_s = args.timeout or BOOTSTRAP_WAIT_SECONDS
    with sync_playwright() as p:
        ctx = _context(p, headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(SEARCH_URL + "?q=operations&l=Remote",
                          wait_until="domcontentloaded", timeout=60000)
            except Exception:  # noqa: BLE001
                pass
            _settle(page, 2000)

            print("")
            print("  A Chrome window is now open on Indeed.")
            if VIEWER_URL:
                print("  It is on a remote screen: open %s" % VIEWER_URL)
            human_alert("Indeed sign-in window is open", "sign in there; Claude never sees it")
            print("  1. Click 'Sign in' (top right).")
            print("  2. Sign in with your PERSONAL Indeed account.")
            print("  3. Finish any Google SSO / email code / 2FA.")
            print("  4. Wait until you are back on an Indeed page, signed in.")
            print("  5. Leave the window open. This will say DONE and close it.")
            print("")
            print("  Nothing you type goes through this tool - you are typing")
            print("  into Chrome directly. Credentials are never read or stored.")
            print("  Waiting up to %d minutes..." % (wait_s // 60))
            print("", flush=True)

            deadline = time.time() + wait_s
            stable = 0
            last_beat = 0.0
            while time.time() < deadline:
                _settle(page, 3000)
                url = page.url or ""

                if time.time() - last_beat > 15:
                    _log("waiting... current page: %s" % url[:90])
                    last_beat = time.time()

                if AUTH_HOST in url:
                    stable = 0
                    continue

                # Off the auth host. Do NOT navigate to verify yet: the
                # ordinary jobs page is also "off the auth host", and a
                # verification nav here fires every few seconds, yanking
                # the page away while the human is still trying to click
                # Sign in. Gate the expensive check on a cheap, passive
                # DOM hint that the account menu actually exists.
                hint = _signed_in(page, verify_nav=False)
                if hint is not True:
                    stable = 0
                    continue

                stable += 1
                if stable < 2:
                    continue

                _log("account menu detected - confirming session...")
                if _signed_in(page, verify_nav=True):
                    _save_state(
                        last_login=dt.datetime.now().isoformat(timespec="seconds"),
                        profile_dir=PROFILE_DIR)
                    print("")
                    print("  DONE - signed in. Profile saved to:")
                    print("  %s" % PROFILE_DIR)
                    print("  Future searches run headlessly. You can close this.")
                    print("", flush=True)
                    return 0

                # Not authenticated yet: put them back on a normal page
                # and keep waiting rather than failing.
                stable = 0
                try:
                    page.goto(SEARCH_URL + "?q=operations&l=Remote",
                              wait_until="domcontentloaded", timeout=45000)
                except Exception:  # noqa: BLE001
                    pass

            print("")
            print("  TIMED OUT - no signed-in session detected.")
            print("  If you did sign in, re-run `status` to check; the profile")
            print("  is kept either way.")
            print("", flush=True)
            return 1
        finally:
            ctx.close()


SAVED_URL = "https://myjobs.indeed.com/saved"


def _signed_in(page, *, verify_nav: bool = True) -> bool:
    """True when this profile has a live jobseeker session.

    The reliable signal is a redirect test, not a CSS selector: a
    logged-out profile asking for a signed-in-only page gets bounced to
    secure.indeed.com/auth. Indeed reshuffles its markup constantly, so
    DOM checks rot; the redirect does not.

    verify_nav=False skips the navigation and falls back to DOM hints -
    used while a human is mid-login, where navigating would wreck the
    flow they are in the middle of.
    """
    if verify_nav:
        try:
            page.goto(SAVED_URL, wait_until="domcontentloaded", timeout=45000)
            _settle(page, 1500)
            return AUTH_HOST not in (page.url or "")
        except Exception:  # noqa: BLE001
            return False
    js = """
    () => {
      if (document.querySelector('[data-gnav-element-name="SignIn"]')) return false;
      if (document.querySelector('[data-gnav-element-name="AccountMenu"], '
                               + '[data-testid="gnav-AccountMenu"]')) return true;
      return null;
    }
    """
    try:
        # Tri-state on purpose: True (account menu), False (Sign in link),
        # None (neither visible yet). Callers must not collapse None into
        # False - during login that difference is what stops us from
        # navigating away while the human is mid-flow.
        return page.evaluate(js)
    except Exception:  # noqa: BLE001
        return None


def cmd_status(args) -> int:
    from patchright.sync_api import sync_playwright

    if not os.path.isdir(PROFILE_DIR):
        print(json.dumps({"authenticated": False,
                          "reason": "no profile - run `login` first"}, indent=2))
        return 1

    with sync_playwright() as p:
        ctx = _context(p, headless=bool(getattr(args, "headless", False)))
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(SEARCH_URL + "?q=test&l=Remote",
                          wait_until="domcontentloaded", timeout=60000)
            except Exception:  # noqa: BLE001
                pass
            _settle(page, 2500)
            state = {}
            if os.path.exists(STATE_FILE):
                try:
                    state = json.load(open(STATE_FILE, encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    pass
            if _blocked(page):
                out = {"authenticated": False, "reason": "challenge/captcha shown",
                       "action": "run `login --headed` and clear it by hand"}
            elif AUTH_HOST in (page.url or "") or not _signed_in(page):
                out = {"authenticated": False, "reason": "profile is logged out",
                       "action": "re-run `login`"}
            else:
                out = {"authenticated": True, "last_login": state.get("last_login")}
            out["profile_dir"] = PROFILE_DIR
            print(json.dumps(out, indent=2))
            return 0 if out.get("authenticated") else 1
        finally:
            ctx.close()


def cmd_search(args) -> int:
    from patchright.sync_api import sync_playwright

    today = dt.date.today()
    cutoff = today - dt.timedelta(days=args.jobage) if args.jobage else None
    results, degraded, parse_path = [], False, None

    with sync_playwright() as p:
        ctx = _context(p, headless=bool(getattr(args, "headless", False)))
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            start, seen, authed = 0, set(), None
            while len(results) < args.limit:
                params = [f"q={args.query.replace(' ', '+')}",
                          f"l={args.location.replace(' ', '+')}"]
                if args.jobage:
                    params.append(f"fromage={min(args.jobage, 30)}")
                if args.remote:
                    # Indeed's own remote facet; still verify in the body.
                    params.append("sc=0kf%3Aattr(DSQF7)%3B")
                if start:
                    params.append(f"start={start}")
                url = SEARCH_URL + "?" + "&".join(params)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception:  # noqa: BLE001
                    pass
                state = _await_page(page)
                if state == "blocked":
                    _log("Indeed hard-blocked this request. Headless is "
                         "blocked outright; run without --headless.")
                    break
                if state == "challenge":
                    _log("still on a Cloudflare challenge after 40s - "
                         "stopping rather than parsing an interstitial.")
                    break
                # Auth is NOT required: /jobs and /viewjob are public, and
                # what defeats the anonymous 403 is driving a real Chrome,
                # not holding a session. Signing in only matters when
                # Indeed starts challenging this profile. So record the
                # state and keep going.
                if AUTH_HOST in (page.url or ""):
                    print(json.dumps({
                        "error": "auth_redirect",
                        "message": "Indeed redirected to sign-in. Run "
                                   "`/indeed_login` if this persists.",
                    }, indent=2))
                    return 2

                if authed is None:
                    authed = _signed_in(page, verify_nav=False)

                page_rows = _from_mosaic(page, today)
                if page_rows:
                    parse_path = "mosaic"
                else:
                    page_rows = _from_dom(page, today)
                    if page_rows:
                        # Measured 2026-09-13: window.mosaic is simply absent
                        # on current result pages, so DOM is the NORMAL path,
                        # not a degradation. Only a total parse failure is.
                        parse_path = "dom"
                if not page_rows:
                    if start == 0:
                        degraded = True
                        _log("no rows parsed from a page that loaded - both "
                             "the JSON and DOM parsers missed. Selectors have "
                             "likely moved.")
                    break

                fresh = 0
                for r in page_rows:
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    if cutoff and r["date"]:
                        try:
                            if dt.date.fromisoformat(r["date"]) < cutoff:
                                continue
                        except ValueError:
                            pass
                    results.append(r)
                    fresh += 1
                    if len(results) >= args.limit:
                        break
                if fresh == 0:
                    break
                start += 10
                if len(results) < args.limit:
                    _polite()
        finally:
            ctx.close()

    payload = {
        "meta": {"count": len(results), "query": args.query,
                 "location": args.location, "jobage": args.jobage,
                 "portal": "indeed-personal",
                 "authenticated": authed,
                 "parse_path": parse_path,
                 "degraded_parse": degraded},
        "results": results[:args.limit],
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for r in payload["results"]:
            print(f"{r['date'] or '????-??-??'}  {r['title'][:52]:52}  "
                  f"{r['company'][:26]:26}  {r['location'][:24]:24}  {r['url']}")
        print(f"\n{len(payload['results'])} results", file=sys.stderr)
    return 0


def _strip_html(html: str) -> str:
    """JSON-LD descriptions are HTML. Flatten to readable text.

    Written without backslash escapes on purpose - these patterns are
    edited through shell heredocs that collapse them silently, which
    already corrupted this function once (a backreference became a raw
    control character). Backreference-free patterns cannot rot that way.
    """
    if not html:
        return ""
    NL = chr(10)
    txt = html
    for tag in ("script", "style"):
        txt = re.sub("(?is)<" + tag + "[^>]*>.*?</" + tag + ">", " ", txt)
    txt = re.sub("(?i)<br[^>]*>", NL, txt)
    txt = re.sub("(?i)</(p|div|li|h[1-6]|tr)>", NL, txt)
    txt = re.sub("(?i)<li[^>]*>", "- ", txt)
    txt = re.sub("<[^>]+>", "", txt)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", chr(34)), ("&#39;", chr(39)), ("&nbsp;", " ")):
        txt = txt.replace(a, b)
    txt = re.sub(NL + "{3,}", NL + NL, txt)
    return NL.join(ln.rstrip() for ln in txt.split(NL)).strip()


def cmd_detail(args) -> int:
    """Full posting, parsed from JSON-LD.

    Measured 2026-09-13: the viewjob DOM selectors returned nothing while
    the page's schema.org JSON-LD carried the entire posting - title,
    company, location, salary, datePosted, validThrough and a 7k-character
    description. Parse the structured data and treat the DOM as a
    fallback, not the other way round.
    """
    from patchright.sync_api import sync_playwright

    jk = args.jk.strip()
    m = re.search(r"jk=([0-9a-f]+)", jk)
    if m:
        jk = m.group(1)

    with sync_playwright() as p:
        ctx = _context(p, headless=bool(getattr(args, "headless", False)))
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(f"https://www.indeed.com/viewjob?jk={jk}",
                          wait_until="domcontentloaded", timeout=60000)
            except Exception:  # noqa: BLE001
                pass
            _settle(page, 4000)

            url_now = page.url or ""
            if AUTH_HOST in url_now:
                print(json.dumps({
                    "error": "not_authenticated",
                    "message": "viewjob requires a signed-in session. "
                               "Run /indeed_login.",
                }, indent=2))
                return 2
            if _challenged(page):
                _await_challenge(page)
            if _blocked(page):
                print(json.dumps({"error": "blocked",
                                  "message": "Indeed blocked this request"},
                                 indent=2))
                return 2

            ld_raw = page.evaluate(
                "() => { const n = document.querySelector("
                "'script[type=\"application/ld+json\"]');"
                " return n ? n.textContent : null; }")

            data = {"id": jk, "url": f"https://www.indeed.com/viewjob?jk={jk}"}
            obj = {}
            if ld_raw:
                try:
                    obj = json.loads(ld_raw)
                    if isinstance(obj, list):
                        obj = obj[0] if obj else {}
                except Exception:  # noqa: BLE001
                    obj = {}

            if obj:
                data["title"] = obj.get("title")
                org = obj.get("hiringOrganization") or {}
                data["company"] = org.get("name")
                loc = obj.get("jobLocation") or {}
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                addr = (loc or {}).get("address") or {}
                data["location"] = ", ".join(
                    x for x in (addr.get("addressLocality"),
                                addr.get("addressRegion"),
                                addr.get("addressCountry")) if x) or None
                data["remote_type"] = obj.get("jobLocationType")
                # Residency / geography restrictions - the field that
                # catches "remote, but only in <state>".
                alr = obj.get("applicantLocationRequirements")
                if isinstance(alr, dict):
                    alr = [alr]
                data["location_requirements"] = [
                    a.get("name") for a in (alr or []) if isinstance(a, dict)
                ] or None
                sal = (obj.get("baseSalary") or {}).get("value") or {}
                if sal.get("minValue") or sal.get("maxValue") or sal.get("value"):
                    data["salary"] = {
                        "min": sal.get("minValue") or sal.get("value"),
                        "max": sal.get("maxValue") or sal.get("value"),
                        "unit": sal.get("unitText"),
                        "currency": (obj.get("baseSalary") or {}).get("currency"),
                    }
                else:
                    data["salary"] = None
                dp = obj.get("datePosted")
                data["date"] = str(dp)[:10] if dp else None
                vt = obj.get("validThrough")
                data["deadline"] = str(vt)[:10] if vt else None
                data["employment_type"] = obj.get("employmentType")
                data["description"] = _strip_html(obj.get("description") or "")

            if not data.get("description"):
                # DOM fallback, only when the structured data is absent.
                data["description"] = page.evaluate(
                    "() => { const e = document.querySelector("
                    "'#jobDescriptionText'); return e ? e.innerText : ''; }") or ""
                data.setdefault("title", page.evaluate(
                    "() => { const e = document.querySelector('h1');"
                    " return e ? e.innerText.trim() : null; }"))

            # Expiry detection: selector AND body text. Measured
            # 2026-09-14 on jk=45e0cb4390fdc626 - Indeed rendered the
            # plain sentence "This job has expired on Indeed" with
            # neither of the two banner classes present, so the selector
            # check alone returned isActive=true for a dead posting and
            # the JSON-LD was absent, yielding an empty shell that looked
            # like a parse failure rather than an expiry. Reporting a dead
            # job as live is the worst outcome here.
            data["isActive"] = not page.evaluate(
                "() => { const sel = document.querySelector("
                "'.jobsearch-JobInfoHeader-expired, "
                "[data-testid=\"expired-job-banner\"]');"
                " const t = document.body ? document.body.innerText : '';"
                " return !!sel || /this job has expired|no longer accepting"
                "|posting has expired/i.test(t); }")
            if not data["isActive"]:
                data["expired_note"] = ("Indeed reports this posting as "
                                        "expired; fields may be empty")

            if args.format == "text":
                for k in ("title", "company", "location", "remote_type",
                          "salary", "date", "deadline", "location_requirements"):
                    print(f"{k:22}: {data.get(k)}")
                print("-" * 60)
                print(data.get("description", "")[:4000])
            else:
                print(json.dumps({"result": data}, indent=2, ensure_ascii=False))
            return 0
        finally:
            ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("login", help="one-time human sign-in (opens a window)")
    lg.add_argument("--timeout", type=int, default=0,
                    help="seconds to wait for sign-in (default 600)")

    st = sub.add_parser("status", help="is the saved profile still signed in?")
    st.add_argument("--headless", action="store_true")

    se = sub.add_parser("search", help="search job listings")
    se.add_argument("--query", "-q", required=True)
    se.add_argument("--location", "-l", default="Remote")
    se.add_argument("--jobage", type=int, default=14,
                    help="posted within N days (default 14)")
    se.add_argument("--limit", "-n", type=int, default=20)
    se.add_argument("--remote", action="store_true",
                    help="apply Indeed's remote facet")
    se.add_argument("--format", choices=["json", "table"], default="json")
    se.add_argument("--headless", action="store_true",
                    help="run without a window (usually BLOCKED by Indeed)")

    de = sub.add_parser("detail", help="full posting by job key or viewjob URL")
    de.add_argument("jk")
    de.add_argument("--headless", action="store_true",
                    help="run without a window (works once authenticated)")
    de.add_argument("--format", choices=["json", "text"], default="json")

    args = ap.parse_args()
    try:
        return {"login": cmd_login, "status": cmd_status,
                "search": cmd_search, "detail": cmd_detail}[args.cmd](args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
