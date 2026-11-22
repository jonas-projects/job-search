#!/usr/bin/env python3
"""Send the candidate's drafted interest message to an employer through Indeed's own messaging
(2026-10-10). Used by apply_queue.py after an application is submitted; also runnable by hand.

    python tools/indeed_message.py scan --out <dir>
        Load My Jobs > Applied once and list every application card with its
        "Message this employer" link (null when the employer does not offer messaging).
    python tools/indeed_message.py send <jk> --message <indeed_message.txt> --out <dir>
        [--send --confirm-token <jk>] [--wait-human N]
        Open that application's compose page, type the message and verify it. Nothing is sent
        unless --send is given AND --confirm-token equals the jk. There is no other code path
        that clicks Send.

Measured 2026-10-10 (read-only probe of the previous candidate's (the user's) account):
- The link lives on https://myjobs.indeed.com/applied, inside each card, as
  `a.atw-JobMetadata-contentLink` with the text "Message this employer", pointing at
  https://messages.indeed.com/conversations/new?...&token=<per-application token>.
- It is EMPLOYER-GATED: 2 of 66 applied cards carried it. Most employers (any ATS-backed
  posting especially) never offer it, so "no link" is the normal outcome, not a failure.
- Both links were on applications a few days old; none of the same-day ones had it, so the
  queue re-checks a pending message on later runs (apply_queue.MESSAGE_MAX_CHECKS).
- myjobs.indeed.com loaded without a challenge while www.indeed.com and messages.indeed.com sat
  on Cloudflare "Additional Verification Required". The compose page itself is therefore
  UNMEASURED: the locators below are generic (a visible textarea or contenteditable box, a
  button reading "Send"). The first run with the user present (apply_queue.py message --present)
  records the page in message_report.json and screenshots; fix the locators from that.

Exit codes: 0 sent / listed / ready; 3 needs a human or unconfirmed; 2 environment problem
(signed out, blocked, no link). Same ToS footing as indeed_apply.py: personal, low volume.
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, ".agents", "skills", "indeed-personal", "cli"))
import indeed_personal as ip  # noqa: E402
sys.path.insert(0, HERE)
import indeed_apply as ia  # noqa: E402  (launch/shared Chrome, call_human, alert_human)

APPLIED_URL = "https://myjobs.indeed.com/applied"
SEND_RE = re.compile(r"^\s*send( message)?\s*$", re.I)
MAX_SCROLLS = 8

# Cards in DOM order: each application's title link (viewjob?jk=...) is followed, inside the
# same card, by "Update status" and, when the employer offers it, "Message this employer".
# A message link therefore belongs to the nearest preceding title link.
CARDS_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const links = [...document.querySelectorAll('a[href]')];
  for (const a of links) {
    const href = a.href || '';
    const jk = (href.match(/[?&]jk=([0-9a-f]{16})/) || [])[1];
    if (jk) {
      if (seen.has(jk)) continue;
      seen.add(jk);
      out.push({jk, title: (a.innerText || '').split('\n')[0].trim().slice(0, 120), message_url: null});
    } else if (/messages\.indeed\.com\/conversations\/new/.test(href) && out.length) {
      if (!out[out.length - 1].message_url) out[out.length - 1].message_url = href;
    }
  }
  return out;
}
"""


def log(report, msg):
    report["log"].append(msg)
    print(f"[message] {msg}", file=sys.stderr, flush=True)


def _text(page):
    return ia.all_frames_text(page)


def _wait_challenge(page, report, wait_human, what):
    """Cloudflare on the page: ring and wait with the user reachable, else give up. True when clear."""
    if not ip._challenged(page):
        return True
    if wait_human <= 0:
        log(report, f"Cloudflare check on {what} in unattended mode; nothing sent")
        return False
    ia.call_human(page)
    log(report, f"Cloudflare check on {what}: waiting up to {wait_human}s for the user")
    t0 = time.time()
    while ip._challenged(page) and time.time() - t0 < wait_human:
        ia.alert_human("Cloudflare 'verify you are human' on Indeed messages", f"{what}: tick the checkbox")
        ip._settle(page, 3000)
    ip._settle(page, 3000)
    return not ip._challenged(page)


def load_cards(page, report, wait_human, want_jk=None):
    """My Jobs > Applied, scrolled until the wanted card appears or the list stops growing."""
    page.goto(APPLIED_URL, wait_until="domcontentloaded", timeout=60000)
    ip._settle(page, 5000)
    if ip.AUTH_HOST in (page.url or ""):
        return None, "not_authenticated"
    if ip._challenged(page) and not _wait_challenge(page, report, wait_human, "My Jobs"):
        return None, "challenge"
    if ip._blocked(page):
        return None, "blocked"
    cards = page.evaluate(CARDS_JS)
    for _ in range(MAX_SCROLLS):
        if want_jk and any(c["jk"] == want_jk for c in cards):
            break
        more = page.locator("button:visible", has_text=re.compile(r"(load|show|see) more", re.I))
        if more.count():
            try:
                more.first.click(timeout=10000)
            except Exception:  # noqa: BLE001
                pass
        page.mouse.wheel(0, 4000)
        ip._settle(page, 2500)
        fresh = page.evaluate(CARDS_JS)
        if len(fresh) <= len(cards):
            cards = fresh
            break
        cards = fresh
    log(report, f"Applied list: {len(cards)} card(s), {sum(1 for c in cards if c['message_url'])} with a message link")
    return cards, None


def compose_box(page):
    for fr in page.frames:
        try:
            if fr.is_detached():
                continue
            box = fr.locator("textarea:visible, [contenteditable=true]:visible")
            if box.count():
                return fr, box.first
        except Exception:  # noqa: BLE001
            continue
    return None, None


def send_button(page):
    for fr in page.frames:
        try:
            if fr.is_detached():
                continue
            btn = fr.get_by_role("button", name=SEND_RE)
            if btn.count():
                return btn.first
            btn = fr.locator("button:visible", has_text=SEND_RE)
            if btn.count():
                return btn.first
        except Exception:  # noqa: BLE001
            continue
    return None


def dump_page(page):
    """What the compose page shows, for fixing locators from the first live run."""
    try:
        return page.evaluate("""() => ({url: location.href, title: document.title,
            heads: [...document.querySelectorAll('h1,h2,h3')].map(h => h.innerText.trim().slice(0, 100)).slice(0, 8),
            buttons: [...document.querySelectorAll('button')].map(b => (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 60)).filter(Boolean).slice(0, 30),
            inputs: [...document.querySelectorAll('textarea, [contenteditable=true], input:not([type=hidden])')]
                .map(e => ({tag: e.tagName, id: e.id, name: e.name || null, placeholder: e.placeholder || null,
                            aria: e.getAttribute('aria-label'), maxlength: e.getAttribute('maxlength')})).slice(0, 10),
            text: (document.body.innerText || '').slice(0, 1500)})""")
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)[:200]}


def do_send(page, jk, message, out, report, send, wait_human):
    cards, err = load_cards(page, report, wait_human, want_jk=jk)
    if err:
        report["status"] = err
        return 2
    card = next((c for c in cards if c["jk"] == jk), None)
    report["card"] = card
    if card is None:
        report["status"] = "not_in_applied"
        log(report, "this jk is not on the Applied list (not submitted, or further down than the scroll reached)")
        return 2
    if not card["message_url"]:
        report["status"] = "no_messaging"
        log(report, f"'{card['title']}' has no 'Message this employer' link: the employer does not offer messaging (yet)")
        return 2
    page.goto(card["message_url"], wait_until="domcontentloaded", timeout=60000)
    ip._settle(page, 5000)
    if ip.AUTH_HOST in (page.url or ""):
        report["status"] = "not_authenticated"
        return 2
    if ip._challenged(page) and not _wait_challenge(page, report, wait_human, "the compose page"):
        report["status"] = "challenge"
        page.screenshot(path=os.path.join(out, "msg_challenge.png"), full_page=True)
        return 3
    if ip._blocked(page):
        report["status"] = "blocked"
        return 2
    report["compose_page"] = dump_page(page)
    page.screenshot(path=os.path.join(out, "msg_01_compose.png"), full_page=True)
    head = re.sub(r"\s+", " ", message.strip())[:60]
    if head and head in re.sub(r"\s+", " ", _text(page)):
        # An earlier run sent it (or a send reported unconfirmed went through): never send twice.
        report["status"] = "already_sent"
        log(report, "the thread already shows this message; nothing sent")
        return 0
    frame, box = compose_box(page)
    if box is None:
        report["status"] = "no_compose_box"
        log(report, "no message box found on the compose page; see compose_page in the report")
        return 3
    try:
        box.click(timeout=10000)
        box.fill(message, timeout=15000)
    except Exception as exc:  # noqa: BLE001
        report["status"] = "error"; report["error"] = f"fill: {exc!r}"[:300]
        return 3
    ip._settle(page, 1500)
    try:
        got = box.input_value(timeout=5000) if box.evaluate("e => e.tagName") == "TEXTAREA" else box.inner_text()
    except Exception:  # noqa: BLE001
        got = ""
    report["typed_ok"] = re.sub(r"\s+", " ", got.strip())[:60] == head
    page.screenshot(path=os.path.join(out, "msg_02_typed.png"), full_page=True)
    if not report["typed_ok"]:
        report["status"] = "error"; report["error"] = "message text did not stick in the box"
        log(report, report["error"])
        return 3
    btn = send_button(page)
    if btn is None:
        report["status"] = "no_send_button"
        log(report, "message typed but no Send button found; see compose_page in the report")
        return 3
    if not send:
        report["status"] = "ready_to_send"
        log(report, "prepare mode: message typed and verified; Send NOT clicked")
        return 0
    try:
        btn.scroll_into_view_if_needed(timeout=5000)
        btn.click(timeout=20000)
    except Exception as exc:  # noqa: BLE001
        # Like a Submit click error: the message MAY have gone. The next run's already_sent
        # check (the thread shows our text) is what makes a retry safe.
        report["status"] = "send_click_error"; report["error"] = repr(exc)[:300]
        page.screenshot(path=os.path.join(out, "msg_03_click_error.png"), full_page=True)
        return 3
    ip._settle(page, 6000)
    page.screenshot(path=os.path.join(out, "msg_03_after_send.png"), full_page=True)
    body = re.sub(r"\s+", " ", _text(page))
    _, box2 = compose_box(page)
    try:
        box_now = (box2.input_value(timeout=3000) if box2 is not None and box2.evaluate("e => e.tagName") == "TEXTAREA"
                   else (box2.inner_text() if box2 is not None else ""))
    except Exception:  # noqa: BLE001
        box_now = ""
    errs = re.findall(r"[^.]{0,80}(?:could not be sent|not sent|something went wrong|try again)[^.]{0,80}", body, re.I)
    report["after_send"] = {"url": page.url, "thread_shows_message": head in body,
                            "box_empty": not box_now.strip(), "errors": errs[:3]}
    ok = head in body and not box_now.strip() and not errs
    report["status"] = "sent" if ok else "send_unconfirmed"
    log(report, f"Send clicked; {'confirmed' if ok else 'NOT confirmed'} ({report['after_send']})")
    return 0 if ok else 3


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("scan"); p.add_argument("--out", required=True)
    p.add_argument("--wait-human", type=int, default=0, help="seconds to wait for the user on a Cloudflare check")
    p.add_argument("--foreground", action="store_true")
    p = sp.add_parser("send"); p.add_argument("jk"); p.add_argument("--out", required=True)
    p.add_argument("--message", required=True, help="plain-text message file (documents/applications/<stem>/indeed_message.txt)")
    p.add_argument("--send", action="store_true"); p.add_argument("--confirm-token")
    p.add_argument("--wait-human", type=int, default=0, help="seconds to wait for the user on a Cloudflare check")
    p.add_argument("--foreground", action="store_true")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    report = {"cmd": a.cmd, "log": [], "status": None}
    message = None
    if a.cmd == "send":
        a.jk = re.sub(r".*jk=", "", a.jk).strip()
        report["jk"] = a.jk
        if a.send and a.confirm_token != a.jk:
            print(json.dumps({"error": "refused", "message": "--send requires --confirm-token equal to the jk"}))
            return 2
        message = open(a.message, encoding="utf-8").read().strip()
        if len(message) < 40:
            print(json.dumps({"error": "message_too_short", "path": a.message}))
            return 2
        report["mode"] = "send" if a.send else "prepare"
    from patchright.sync_api import sync_playwright
    code = 3
    with sync_playwright() as p:
        ctx = ia.launch(p, background=not a.foreground)
        try:
            if ia.CDP_URL:
                page = [x for x in ctx.pages if x not in ia._SHARED_BEFORE][0]
                page.bring_to_front()
            else:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if a.cmd == "scan":
                cards, err = load_cards(page, report, a.wait_human)
                report["cards"] = cards or []
                report["status"] = err or "listed"
                code = 2 if err else 0
                page.screenshot(path=os.path.join(a.out, "applied_list.png"), full_page=True)
            else:
                code = do_send(page, a.jk, message, a.out, report, a.send, a.wait_human)
        except Exception as exc:  # noqa: BLE001
            report["status"] = "error"; report["error"] = repr(exc)[:500]; code = 3
        finally:
            json.dump(report, open(os.path.join(a.out, "message_report.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            try:
                if ia.CDP_URL:
                    for x in ctx.pages:
                        if x not in ia._SHARED_BEFORE and not x.is_closed():
                            x.close()
                else:
                    ctx.close()
            except Exception:  # noqa: BLE001
                pass
    summary = {k: report.get(k) for k in ("cmd", "jk", "mode", "status", "card", "typed_ok", "after_send", "error")}
    if a.cmd == "scan":
        summary["cards"] = report.get("cards")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
