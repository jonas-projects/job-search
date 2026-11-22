#!/usr/bin/env python3
"""Drive Indeed's "Apply now" (smartapply.indeed.com) flow for ONE posting, human-in-the-loop.

Used by /apply_automate. It reuses the signed-in Chrome profile that indeed-personal
created, so it must never run while another indeed-personal process is alive (the
profile lock fails silently).

    python tools/indeed_apply.py <jk> --out <dir> [--resume cv/main_x.pdf]
        [--answers answers.json] [--cover-letter letter.txt]
        [--submit --confirm-token <jk>]

Default mode is PREPARE: walk every step, upload the resume, answer screening questions
from --answers, and STOP on the "Review your application" page with screenshots. Nothing is
sent. Closing the window leaves an unsent draft on Indeed.

SUBMIT mode clicks "Submit your application" only when --submit is given AND
--confirm-token equals the jk AND --human-submit is not given. /apply_automate passes the token
only for an application the candidate approved. apply_queue.py adds --human-submit (stop on the
review page; a person clicks Submit) unless the candidate has explicitly opted in to
auto-submit (facts.json automation.auto_submit: true). There is no other code path that clicks
a submit button.

The candidate's name (name gate, resume filename check) comes from facts.json via
tools/candidate.py; placeholder facts exit 2 with error "candidate_not_set".

Exit codes: 0 reached review (prepare) or submitted (submit); 3 needs a human (unanswered
question, unexpected screen, captcha); 2 environment problem (not signed in, blocked,
not an Indeed Apply posting).

Automated applying is against Indeed's Terms of Service, like indeed-personal's searching.
Personal use, low volume, one application per run.
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
import indeed_personal as ip  # noqa: E402  (PROFILE_DIR, _context, _settle, _blocked, AUTH_HOST)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auto_answer  # noqa: E402  (Claude answers unmatched questions from the profile)
import answer_store  # noqa: E402  (facts.json, templated base answers, learned answers)
import candidate  # noqa: E402  (the candidate's name, for the name gate and resume filenames)

SUBMIT_RE = re.compile(r"submit", re.I)
ADVANCE_RE = re.compile(r"^\s*(Continue|Continue applying|Review details|Review your application|Next|Skip|Skip for now)\s*$", re.I)
MAX_STEPS = 15

SNAP_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  // Head and tail of long labels: Taconic's AI opt-out sentence sat past char 300 (2026-10-10).
  // Widened 2026-10-14: Westport's eligible-state list put WA in the clipped middle and the
  // question was answered "No" for a state that was on the list.
  const clip = t => (t || '').length > 1500 ? t.slice(0, 1200) + ' … ' + t.slice(-300) : (t || '');
  const labelFor = e => {
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return l.innerText.trim(); }
    const l = e.closest('label'); if (l) return l.innerText.trim();
    const fs = e.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) return lg.innerText.trim(); }
    const q = e.closest('[data-testid*="question"], [class*="Question"]');
    if (q) { const t = q.querySelector('label, legend, span, div'); if (t) return t.innerText.trim(); }
    return e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.name || '';
  };
  const groupLabel = e => { const fs = e.closest('fieldset'); const lg = fs && fs.querySelector('legend');
                            return lg ? lg.innerText.trim() : ''; };
  const sectionOf = e => {
    let n = e;
    for (let i = 0; i < 8 && n; i++) {
      n = n.parentElement; if (!n) break;
      const h = n.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > legend, :scope > div > h3, :scope > div > h4');
      if (h && h.innerText.trim()) return h.innerText.trim().slice(0, 120);
    }
    return '';
  };
  // A styled native <select> can be visually collapsed behind its own widget, so it counts as
  // visible when its wrapper is (ACV 2026-09-23: State and salary dropdowns were invisible here).
  const shown = e => vis(e) || (e.tagName === 'SELECT' && e.parentElement && vis(e.parentElement));
  const fields = [...document.querySelectorAll('input, textarea, select')]
    .filter(e => e.type !== 'hidden' && e.type !== 'file' && shown(e) && !/current page/i.test(e.getAttribute('aria-label') || ''))
    .map(e => ({ tag: e.tagName.toLowerCase(), type: e.type || null, name: e.name || null, id: e.id || null,
      label: clip(labelFor(e)), group: clip(groupLabel(e)), section: sectionOf(e),
      required: e.required || e.getAttribute('aria-required') === 'true',
      value: (e.type === 'checkbox' || e.type === 'radio') ? e.checked : (e.value || '').slice(0, 200),
      options: e.tagName === 'SELECT' ? [...e.options].map(o => o.text) : null }));
  // Custom dropdown widgets (a button or div with role=combobox / aria-haspopup=listbox, no native
  // <select>): ACV's "What state..." and "expected base compensation" (2026-09-23).
  const lblBy = e => { const ids = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
                       return ids.map(i => { const n = document.getElementById(i); return n ? n.innerText.trim() : ''; })
                                 .filter(Boolean).join(' '); };
  [...document.querySelectorAll('[role=combobox], [aria-haspopup=listbox]')]
    .filter(e => !/^(INPUT|SELECT|TEXTAREA)$/.test(e.tagName) && vis(e))
    .forEach(e => { const val = (e.innerText || '').trim();
      let lab = labelFor(e); if (!lab || lab === val) lab = lblBy(e) || lab;
      if (lab.startsWith(val) && val) lab = lab.slice(val.length).trim() || lab;
      lab = lab.split('\n')[0].trim();  // "What state...? *\nrequired\nOregon" -> the question line
      fields.push({ tag: 'combobox', type: 'combobox', name: null, id: e.id || null,
        label: clip(lab), group: '', section: sectionOf(e),
        required: e.getAttribute('aria-required') === 'true' || /\*\s*$/.test(lab),
        value: /^select( an)? option/i.test(val) ? '' : val.slice(0, 200), options: null }); });
  const buttons = [...document.querySelectorAll('button, [role=button]')].filter(vis)
      .map(b => (b.innerText || b.getAttribute('aria-label') || '').trim()).filter(t => t && t.length < 80);
  const heading = [...document.querySelectorAll('h1,h2,h3')].filter(vis).map(h => h.innerText.trim()).slice(0, 8);
  return { url: location.href, heading, fields, buttons: [...new Set(buttons)] };
}
"""


def log(report, msg):
    report["log"].append(msg)
    print(f"[apply] {msg}", file=sys.stderr, flush=True)


def module_of(url):
    m = re.search(r"/form/([^/?]+)", url or "")
    if m:
        return m.group(1)
    return "review-module" if "review" in (url or "") else (url or "")


BASE_ANSWERS = os.path.join(REPO, ".claude", "skills", "apply-automate", "answers_base.json")


def load_answers(path):
    """Answer patterns, in priority order:
    1. the posting's own extras (documents/applications/<stem>/indeed_answers.json), if any;
    2. answers_base.json with {placeholders} filled from facts.json (the single source of facts);
    3. learned_answers.json: earlier auto-answers, reused so repeat questions skip Claude.
    Per-posting files hold only extras (2026-09-28): embedded copies of the base let a stale
    address outrank the fresh one."""
    data = []
    if path and os.path.abspath(path) != os.path.abspath(BASE_ANSWERS):
        data += json.load(open(path, encoding="utf-8"))
    data += answer_store.base_answers()
    data += answer_store.learned_patterns()
    # [{"match": "<regex on question label>", "answer": "<text or option label>"}]
    return [(re.compile(a["match"], re.I), a["answer"]) for a in data]


def norm_label(label):
    """'First name *' -> 'first name'. Answers must match the WHOLE normalized label."""
    # Strip ANY trailing run of *, :, ? and whitespace (incl. the non-breaking space Indeed puts
    # between "?" and "*": "authorized to work in the United States?\u00a0*").
    return re.sub(r"\s+", " ", re.sub(r"[\s*:?]+$", "", (label or ""))).strip().lower()


def find_answer(answers, label):
    lab = norm_label(label)
    for rx, ans in answers:
        if rx.fullmatch(lab):
            return ans
    return None


# Never auto-fill inside these sections: the same labels (City, Start Date, Phone) mean
# something else there, and history belongs to the candidate to enter or confirm.
# The first field of an employment/education block on a flat custom form. Nothing at or
# after it is auto-filled on that page.
HISTORY_START = re.compile(r"(company|employer)( name)?|position title|job title|school( name)?|institution( name)?")
# Consents (SMS/email communications, AI review, privacy policies, e-signatures) are the
# candidate's own decision. Nothing here accepts any of them: a consent or attestation is filled
# only when answers_base.json / learned answers carry a rule the candidate approved; otherwise it
# is handed to the candidate (see ATTESTATION below and auto_answer.GUARDED).
# Phone-number boxes (not "phone type" or "phone interview" questions).
PHONE_LABEL = re.compile(r"\b(phone|mobile|cell)( number)?\b(?!.*\b(type|interview|screen)\b)", re.I)
# Signatures, attestations and agreements.
ATTESTATION = re.compile(r"signature|first and last name|full (legal )?name.*sign|today'?s date|i agree|"
                         r"i certify|acknowledg|attest|read and understand", re.I)
NO_AUTOFILL_SECTION = re.compile(
    # Anchored: a section header, not any question that happens to contain "employment"
    # (Harbor Health's sponsorship question mentions "employment visa status").
    r"^\s*(employment( history)?|work history|work experience|previous employers?|education|"
    r"school|references?)\b", re.I)


def upload_resume(page, pdf, report):
    """Select the tailored PDF on the resume step. Returns True when it is the selected file."""
    name = os.path.basename(pdf)
    # ALWAYS upload the current file. Measured 2026-09-28: skipping when a file of the same
    # name was already on the Indeed account sent a STALE copy (old header) after the CV had
    # been regenerated, because Indeed keeps the earlier upload under that name.
    body = page.inner_text("body") or ""
    # NOTE 2026-10-09: an "Uploaded just now" shortcut was tried here and removed the same day.
    # Indeed kept showing a morning upload as "Uploaded just now" hours later, so the shortcut
    # reused a stale CV after it had been regenerated (Old Navy city added). Always upload fresh.
    if name in body:
        log(report, f"a file named {name} is already on the account; uploading the current copy anyway")
    opts = page.locator("button:visible", has_text=re.compile(r"Resume options", re.I))
    if opts.count():
        opts.first.click(timeout=15000)
        ip._settle(page, 1500)
    # Measured 2026-10-09 (Talkspace, AVIBE): a "Review your resume" variant shows the last upload
    # with a "Use a different resume" / "Upload a different resume" button; the file input only
    # appears after it is clicked, and the click may open the file chooser directly.
    diff = page.locator("button:visible, a:visible", has_text=re.compile(r"(use|upload) a different resume", re.I))
    if diff.count():
        try:
            with page.expect_file_chooser(timeout=6000) as fc:
                diff.first.click(timeout=15000)
            fc.value.set_files(pdf)
            ip._settle(page, 6000)
            ok = name in (page.inner_text("body") or "")
            log(report, f"resume upload via 'Use a different resume' {'confirmed' if ok else 'NOT confirmed'}: {name}")
            return ok
        except Exception:  # noqa: BLE001
            ip._settle(page, 1500)  # no direct chooser: fall through to the upload option / file input
    up = page.locator("button:visible, [role=menuitem]:visible, a:visible",
                      has_text=re.compile(r"upload", re.I))
    try:
        if up.count():
            try:
                with page.expect_file_chooser(timeout=8000) as fc:
                    up.first.click(timeout=15000)
                fc.value.set_files(pdf)
            except Exception:  # noqa: BLE001
                # the click opened a dialog or revealed an input instead of a chooser
                ip._settle(page, 1500)
                page.locator("input[type=file]").first.set_input_files(pdf, timeout=15000)
        else:
            page.locator("input[type=file]").first.set_input_files(pdf, timeout=15000)
    except Exception as exc:  # noqa: BLE001
        log(report, f"resume upload failed: {exc!r}")
        return False
    ip._settle(page, 6000)
    ok = name in (page.inner_text("body") or "")
    log(report, f"resume upload {'confirmed' if ok else 'NOT confirmed'}: {name}")
    return ok


def upload_form_cover(page, pdf, report):
    """Measured 2026-10-09 (406 Recovery): a questions-module page can carry a required
    "Cover Letter *" file upload ("Upload a file to continue."). The snapshot skips file
    inputs, so attach the cover letter PDF to the one file input whose block names a cover letter."""
    for fr in page.frames:
        try:
            inputs = fr.locator("input[type=file]")
            n = inputs.count()
        except Exception:  # noqa: BLE001
            continue
        for k in range(n):
            inp = inputs.nth(k)
            try:
                ctx = inp.evaluate("""e => { let n = e; for (let i = 0; i < 6 && n; i++) {
                    n = n.parentElement; if (n && /cover letter/i.test(n.innerText || '')) return n.innerText.slice(0, 200); }
                    return ''; }""")
            except Exception:  # noqa: BLE001
                ctx = ""
            if not ctx or re.search(re.escape(os.path.basename(pdf)), ctx):
                continue
            try:
                inp.set_input_files(pdf, timeout=15000)
                page.wait_for_timeout(3000)
                log(report, f"form cover-letter upload: attached {os.path.basename(pdf)}")
                report["form_cover_uploaded"] = os.path.basename(pdf)
                return True
            except Exception as exc:  # noqa: BLE001
                log(report, f"form cover-letter upload failed: {type(exc).__name__}")
    return False


def _frame_with(page, pattern):
    """First frame (main page included) whose visible text matches pattern, else the page."""
    rx = re.compile(pattern, re.I)
    for f in page.frames:
        try:
            if rx.search(f.evaluate("() => document.body ? document.body.innerText : ''") or ""):
                return f
        except Exception:  # noqa: BLE001
            continue
    return page


def _submit_visible(page):
    for f in page.frames:
        try:
            if f.locator("button:visible", has_text=SUBMIT_RE).count():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False



def expected_name():
    """(first, last) the review page must show: the candidate's, from facts.json. Raises
    candidate.CandidateNotSet while facts.json still holds placeholders."""
    return candidate.first_last()


def all_frames_text(page):
    parts = []
    for f in page.frames:
        try:
            if not f.is_detached():
                parts.append(f.evaluate("() => document.body ? document.body.innerText : ''") or "")
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(parts)


def _same_name(got, want):
    # 2026-10-10: the whitespace-blind comparison from 2026-10-09 let a "Buch el" surname go out
    # on Thrive; the review page really showed the space. Compare exactly (runs of whitespace
    # collapsed to one space, case-insensitive). A false alarm only costs a correction pass.
    norm = lambda x: re.sub(r"\s+", " ", x or "").strip().lower()
    return bool(got) and norm(got) == norm(want)


def review_name(page):
    m = re.search(r"Full name\s*\n\s*([^\n]+)", all_frames_text(page))
    return m.group(1).strip() if m else None


def ensure_contact_name(page, out, report):
    """Measured 2026-09-23: Indeed's stored name came back garbled (one letter of the surname replaced) on a
    posting whose flow skipped the contact step. Fix it on the review page via Contact
    information > Edit, then verify. Returns True when the review shows the expected name."""
    first, last = expected_name()
    want = f"{first} {last}"
    got = review_name(page)
    report["review_name_before"] = got
    if _same_name(got, want):
        return True
    log(report, f"review shows name {got!r}; correcting via Contact information > Edit")
    scope = _frame_with(page, r"Contact information")
    # Loose match: the heading's element often carries more than the bare words (2026-09-28:
    # the exact-text lookup found nothing and the fix returned silently on every posting).
    hb = None
    for fr in [scope] + [f for f in page.frames if f is not scope]:
        try:
            head = fr.get_by_text(re.compile(r"Contact information", re.I))
            for k in range(head.count()):
                bb = head.nth(k).bounding_box()
                if bb and bb["width"] > 0:
                    hb = bb
                    break
        except Exception:  # noqa: BLE001
            continue
        if hb:
            break
    edits = []
    # Search EVERY frame, by role and by exact visible text: on 2026-09-28 the role lookup in
    # one frame found no Edit control although one sat beside "Contact information".
    for fr in page.frames:
        try:
            if fr.is_detached():
                continue
            cands = [fr.get_by_role(r, name=re.compile(r"^\s*edit\b", re.I)) for r in ("button", "link")]
            cands.append(fr.get_by_text(re.compile(r"^\s*Edit\s*$")))
        except Exception:  # noqa: BLE001
            continue
        for loc in cands:
            try:
                n = loc.count()
            except Exception:  # noqa: BLE001
                continue
            for k in range(n):
                try:
                    bb = loc.nth(k).bounding_box()
                except Exception:  # noqa: BLE001
                    bb = None
                if bb and bb["width"] > 0 and not any(abs(bb["y"] - y) < 2 and abs(bb["x"] - x) < 2 for y, x, _ in edits):
                    edits.append((bb["y"], bb["x"], loc.nth(k)))
    report["edit_controls"] = [{"y": round(y), "x": round(x)} for y, x, _ in edits]
    if not edits:
        log(report, "no Edit controls found on the review page; cannot fix the name")
        return False
    edits.sort(key=lambda e: e[0])
    if hb:
        # the Edit closest to (and at the height of) the Contact information heading
        target = min(edits, key=lambda e: abs(e[0] - hb["y"]))[2]
    else:
        # Contact information is the first section of the review page, so its Edit is topmost
        log(report, "Contact information heading not located; using the topmost Edit control")
        target = edits[0][2]
    log(report, f"found {len(edits)} Edit control(s); clicking the Contact information one")
    try:
        target.scroll_into_view_if_needed(timeout=5000)
        target.click(timeout=15000)
    except Exception as exc:  # noqa: BLE001
        log(report, f"clicking Edit failed: {type(exc).__name__}")
        return False
    ip._settle(page, 4000)
    form = _frame_with(page, r"First name")
    page.screenshot(path=os.path.join(out, "name_fix_form_before.png"), full_page=True)
    for lab, val in ((r"^\s*first name", first), (r"^\s*last name", last)):
        fld = form.get_by_label(re.compile(lab, re.I))
        if not fld.count():
            log(report, f"contact field {lab} not found")
            return False
        box = fld.first
        # 2026-10-12: a one-shot fill() on Indeed's React input left the stored last name garbled
        # ("Buch el" -> "Wuchel Achel"). Clear by keyboard, type slowly, read back, retry.
        got = None
        for attempt in range(3):
            box.click(timeout=10000)
            box.press("Control+a")
            box.press("Backspace")
            if box.input_value():
                box.fill("", timeout=10000)
            box.type(val, delay=60)
            page.wait_for_timeout(300)
            got = box.input_value()
            if got == val:
                break
            log(report, f"contact field {lab}: read back {got!r}, retrying")
        report.setdefault("name_fix_fields", []).append({"field": lab, "want": val, "got": got})
        if got != val:
            log(report, f"contact field {lab} would not take {val!r} (shows {got!r}); leaving it for the candidate")
            return False
    page.screenshot(path=os.path.join(out, "name_fix_form.png"), full_page=True)
    for _ in range(6):
        if _submit_visible(page):
            break
        nxt = None
        for f in page.frames:
            try:
                loc = f.locator("button:visible", has_text=ADVANCE_RE)
                if loc.count():
                    nxt = loc.first
                    break
            except Exception:  # noqa: BLE001
                continue
        if nxt is None:
            break
        nxt.click(timeout=15000)
        ip._settle(page, 4000)
    got2 = review_name(page)
    report["review_name_after"] = got2
    ok = _same_name(got2, want) and _submit_visible(page)
    log(report, f"name after fix: {got2!r} ({'ok' if ok else 'NOT fixed'})")
    return ok



def alert_human(reason="Indeed application needs you", detail="", times=3):
    """Get the user to the window: ip.human_alert's NEEDS_HUMAN line (plus a sound and, when
    configured, a phone push), and on Windows two more system sounds so it is hard to miss."""
    ip.human_alert(reason, detail)
    if os.name != "nt":
        return
    try:
        import winsound
        for _ in range(times - 1):
            time.sleep(0.4)
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
    except Exception:  # noqa: BLE001
        print("\a" * times, end="", flush=True)


def captcha_frame(page):
    """A visible reCAPTCHA widget/challenge iframe, or None. The page-wide 'protected by
    reCAPTCHA' notice is NOT a challenge; a sizeable recaptcha iframe is."""
    loc = page.locator("iframe[src*='recaptcha']")
    for k in range(loc.count()):
        try:
            el = loc.nth(k)
            bb = el.bounding_box()
            if bb and bb["height"] > 40 and bb["width"] > 100 and el.is_visible():
                return el
        except Exception:  # noqa: BLE001
            continue
    return None


# Background mode (2026-09-23: "don't have Chrome pop open over whatever I'm working on").
# Chrome starts minimized and stays there; it is restored only when the script needs a person's hands
# (captcha, consent hand-off). The throttling flags keep a minimized window rendering and its
# timers running; they are Chromium's own automation defaults, not detection evasion.
BG_ARGS = ["--start-minimized", "--disable-backgrounding-occluded-windows",
           "--disable-renderer-backgrounding", "--disable-background-timer-throttling"]


CDP_URL = os.environ.get("INDEED_CDP_URL", "")  # set by apply_queue.py: attach to its shared Chrome
_SHARED_BEFORE = []  # tabs that existed before this run attached; never closed by it


def launch(p, background):
    if CDP_URL:
        # 2026-10-10: one Chrome per queue batch (tools/indeed_browser.py), so a Cloudflare
        # check the user ticks once carries over to the rest of the batch. We work in our own tab.
        ctx = p.chromium.connect_over_cdp(CDP_URL, timeout=30000).contexts[0]
        _SHARED_BEFORE[:] = list(ctx.pages)
        ctx.new_page()
        return ctx
    if not background:
        return ip._context(p, headless=False)
    kwargs = dict(user_data_dir=ip.PROFILE_DIR, headless=False, no_viewport=True,
                  args=list(ip._ARGS) + BG_ARGS)
    if ip.CHANNEL:
        kwargs["channel"] = ip.CHANNEL
    return p.chromium.launch_persistent_context(**kwargs)


def window_state(page, state):
    """'minimized' or 'normal', via CDP. Best effort: never fails a run."""
    try:
        cdp = page.context.new_cdp_session(page)
        wid = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": state}})
        cdp.detach()
    except Exception:  # noqa: BLE001
        pass


def call_human(page):
    """Restore the window and bring it forward: the one moment it is allowed to take the screen."""
    window_state(page, "normal")
    try:
        page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass


def wait_for_human_captcha(page, submit_btn, out, report, max_wait):
    """Measured 2026-09-23: Indeed shows a reCAPTCHA at the bottom of the review page and keeps
    'Submit your application' disabled until it is solved. A human solves it; we only ring,
    bring the window forward, and wait. Returns True once Submit is enabled."""
    if submit_btn.first.is_enabled():
        return True
    cf = captcha_frame(page)
    report["captcha_seen"] = bool(cf)
    if max_wait <= 0:
        # Unattended run (apply_queue.py): don't pop Chrome up or ring; park and move on.
        page.screenshot(path=os.path.join(out, "captcha_parked.png"), full_page=True)
        log(report, "CAPTCHA / disabled Submit in unattended mode; parking for the user, nothing sent")
        return False
    call_human(page)
    try:
        (cf or submit_btn.first).scroll_into_view_if_needed(timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    page.screenshot(path=os.path.join(out, "captcha_waiting.png"), full_page=True)
    log(report, f"CAPTCHA / disabled Submit: waiting up to {max_wait}s for the user to solve it (alerting)")
    t0, last = time.time(), 0.0
    while time.time() - t0 < max_wait:
        try:
            if submit_btn.first.is_enabled():
                waited = int(time.time() - t0)
                report["captcha_wait_seconds"] = waited
                log(report, f"Submit enabled after {waited}s")
                ip._settle(page, 1500)
                return True
        except Exception:  # noqa: BLE001
            return False
        if time.time() - last > 20:
            alert_human("captcha on the review page", f"jk {report.get('jk')}: solve it; Submit unlocks after")
            last = time.time()
        # NEVER time.sleep() here: with the sync API a blocked Python thread stops the driver
        # from servicing Chrome, and the page, reloads and new tabs hang (measured 2026-09-23,
        # which made the captcha unsolvable). wait_for_timeout keeps the driver pumping.
        try:
            page.wait_for_timeout(2000)
        except Exception:  # noqa: BLE001
            return False
    log(report, "captcha wait timed out; nothing sent")
    return False


def set_cover_letter(page, letter_text, extra_pdf, out, report, remove_names=()):
    """Open 'Supporting documents' from the review page and make the cover letter exactly ours.

    Indeed pre-selects the LAST cover letter typed in any earlier application (seen
    2026-09-23: a Congruex letter pre-filled on an S4 application). So this always replaces
    the text, or explicitly chooses 'Apply without a cover letter' when we have none.
    Returns True (set and back on review), None (posting offers no slot), False (failed).
    """
    scope = _frame_with(page, r"Supporting\s+documents")
    head = scope.get_by_text(re.compile(r"Supporting\s+documents", re.I))
    if not head.count():
        log(report, "no Supporting documents slot on this posting")
        return None
    hb = head.first.bounding_box()
    target, best = None, None
    for role in ("button", "link"):
        loc = scope.get_by_role(role, name=re.compile(r"^\s*(add|edit)\b", re.I))
        for k in range(loc.count()):
            try:
                bb = loc.nth(k).bounding_box()
            except Exception:  # noqa: BLE001
                bb = None
            if bb and hb and abs(bb["y"] - hb["y"]) < 60 and (best is None or abs(bb["y"] - hb["y"]) < best):
                target, best = loc.nth(k), abs(bb["y"] - hb["y"])
    if target is None:
        log(report, "Supporting documents heading found but no Add/Edit control next to it")
        return False
    target.click(timeout=15000)
    ip._settle(page, 4000)

    doc = _frame_with(page, r"Add supporting documents|Write a cover letter|Apply without a cover letter")
    page.screenshot(path=os.path.join(out, "cl_01_supporting_screen.png"), full_page=True)
    area = doc.locator("textarea:visible")
    if area.count():
        report["prefilled_cover_letter_start"] = (area.first.input_value() or "")[:160]

    if letter_text:
        if not area.count():
            opt = doc.get_by_text(re.compile(r"Write a cover letter", re.I))
            if opt.count():
                opt.first.click(timeout=15000)
                ip._settle(page, 2000)
            area = doc.locator("textarea:visible")
        if not area.count():
            log(report, "no cover-letter text box appeared")
            return False
        area.first.fill(letter_text, timeout=15000)
        ip._settle(page, 1500)
        got = area.first.input_value() or ""
        if got.strip()[:60] != letter_text.strip()[:60]:
            log(report, "cover letter text did not stick")
            return False
        log(report, "cover letter text set (prefilled text replaced)")
    else:
        opt = doc.get_by_text(re.compile(r"Apply without a cover letter", re.I))
        if opt.count():
            opt.first.click(timeout=15000)
            log(report, "chose 'Apply without a cover letter' (never leave a stale letter selected)")

    if extra_pdf:
        name = os.path.basename(extra_pdf)
        if name not in (doc.evaluate("() => document.body.innerText") or ""):
            try:
                with page.expect_file_chooser(timeout=15000) as fc:
                    doc.get_by_text(re.compile(r"Upload a file", re.I)).first.click(timeout=15000)
                fc.value.set_files(extra_pdf)
                ip._settle(page, 5000)
            except Exception as exc:  # noqa: BLE001
                log(report, f"additional document upload failed: {exc!r}")
                return False

    for rname in remove_names or ():
        # Remove one previously attached additional document by file name. Only the
        # "Remove" control directly BELOW that file name is eligible, so the cover-letter
        # text box's own "Remove" (above it) can never be hit.
        label = doc.get_by_text(rname, exact=False)
        if not label.count():
            log(report, f"nothing to remove: {rname} not attached")
            continue
        lb = label.first.bounding_box()
        rm = doc.locator("button:visible, [role=button]:visible, a:visible", has_text=re.compile(r"remove", re.I))
        report.setdefault("remove_candidates", []).append(rm.count())
        pick, best = None, None
        for k in range(rm.count()):
            try:
                bb = rm.nth(k).bounding_box()
            except Exception:  # noqa: BLE001
                bb = None
            if bb and lb:
                dy = bb["y"] - lb["y"]
                if 0 < dy < 140 and (best is None or dy < best):
                    pick, best = rm.nth(k), dy
        if pick is None:
            log(report, f"could not find a Remove control under {rname}")
            return False
        pick.click(timeout=15000)
        ip._settle(page, 3000)
        page.screenshot(path=os.path.join(out, "cl_03_after_remove_click.png"), full_page=True)
        # Removal may ask for confirmation; confirm only a dialog that names removing a file.
        dlg = doc.locator("[role=dialog]:visible, [role=alertdialog]:visible")
        if dlg.count() and re.search(r"remove|delete", dlg.first.inner_text() or "", re.I):
            yes = dlg.first.locator("button:visible", has_text=re.compile(r"^\s*(remove|delete|yes|confirm)\s*$", re.I))
            if yes.count():
                yes.first.click(timeout=15000)
                ip._settle(page, 3000)
        still = bool(doc.locator("div:visible, span:visible, p:visible", has_text=rname).count())
        if still:
            log(report, f"removal of {rname} NOT confirmed")
            return False
        report.setdefault("removed_supporting", []).append(rname)
        log(report, f"removed additional document: {rname}")
        # the text letter must survive the removal
        area = doc.locator("textarea:visible")
        if letter_text and (not area.count() or (area.first.input_value() or "").strip()[:60] != letter_text.strip()[:60]):
            log(report, "cover letter text lost after removal")
            return False

    page.screenshot(path=os.path.join(out, "cl_02_before_continue.png"), full_page=True)
    cont = doc.locator("button:visible", has_text=re.compile(r"^\s*Continue\s*$", re.I))
    if not cont.count():
        log(report, "no Continue on the supporting documents screen")
        return False
    cont.first.click(timeout=15000)
    ip._settle(page, 5000)
    back = _submit_visible(page)
    log(report, f"returned to review: {back}")
    return True if back else False


def _canon(s):
    """Option comparison ignoring case and punctuation: "U.S. Citizen" == "US Citizen"."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _combobox(page, f):
    if f["id"]:
        return page.locator(f'[id="{f["id"]}"]').first
    return page.get_by_role("combobox", name=re.compile(re.escape(f["label"][:40]), re.I)).first


def combobox_options(page, f):
    """Open a custom dropdown just long enough to read its choices (for the hand-off), then close it."""
    try:
        _combobox(page, f).click(timeout=10000)
        page.wait_for_timeout(800)
        opts = page.get_by_role("option")
        out = [opts.nth(k).inner_text().strip() for k in range(min(opts.count(), 40))]
        page.keyboard.press("Escape")
        return out
    except Exception as exc:  # noqa: BLE001
        return [f"(could not read options: {type(exc).__name__})"]


def answer_fields(page, snap, answers, cover_letter, report):
    """Fill every visible field it can ground; return the labels it could not answer."""
    unanswered = []
    done_groups = set()
    in_history = False
    for f in snap["fields"]:
        if f["type"] == "checkbox" and re.search(r"email updates|job alerts", f["label"], re.I):
            continue  # marketing opt-ins: leave unticked
        label = f["group"] if f["type"] in ("radio", "checkbox") and f["group"] else f["label"]
        req = bool(f["required"]) or "*" in label or re.search(r"\brequired\b", label, re.I) is not None
        if HISTORY_START.fullmatch(norm_label(f["label"])):
            in_history = True
        # An attestation is filled only when an answers_base rule covers it, i.e. the candidate has
        # approved e-signing / certifying that kind of statement. Otherwise it goes to the candidate.
        if ATTESTATION.search(label) and find_answer(answers, label) is None:
            # Signatures, dates and agreements are the candidate's own act. Never fill, never keep a pre-fill.
            unanswered.append({"label": label, "type": f["type"], "required": req,
                               "prefilled": f.get("value"), "why": "attestation: the candidate signs this"})
            continue
        if in_history or NO_AUTOFILL_SECTION.search(f.get("section") or ""):
            if str(f.get("value") or "").strip() and f["type"] not in ("radio", "checkbox"):
                report.setdefault("prefilled_needs_review", []).append({"question": label, "value": f["value"]})
            if req:
                unanswered.append({"label": label, "section": f.get("section"), "type": f["type"], "required": True})
            else:
                report.setdefault("optional_left_blank", []).append(f"{f.get('section')}: {label}")
            continue
        if f["tag"] == "combobox" and PHONE_LABEL.search(label or ""):
            # Indeed labels the country-code picker beside a phone box "Phone number"; typing the
            # number into it timed out on 17 hand-offs (AVIBE, Harvest...). US is the default.
            continue
        if re.search(r"cover letter", label, re.I) and cover_letter:
            ans = cover_letter
        else:
            ans = find_answer(answers, label)
        # 2026-10-10 (Thrive): the "allow AI review" Yes rule matched "How do you currently use AI
        # tools...?" and typed "Yes" into an essay box. A bare yes/no answer never goes into a text
        # field; leave it unmatched so the auto-answer (or the candidate) writes a real answer.
        # Essay boxes only: a one-line text box asking a yes/no question (Care Advantage 2026-10-10,
        # "Have you ever been convicted...?") legitimately takes "No".
        if ans is not None and (f["type"] == "textarea" or f.get("tag") == "textarea"):
            first = ans[0] if isinstance(ans, list) and ans else ans
            if re.fullmatch(r"\s*(yes|no|i agree|i consent|agree|accept|i accept|i acknowledge)\.?\s*",
                            str(first), re.I):
                ans = None
        if ans is None and f["type"] not in ("radio", "checkbox") and str(f.get("value") or "").strip() \
                and re.search(r"salary|compensation|pay (rate|expectation)|desired pay|wage", label, re.I):
            # Never keep a pre-filled pay figure: Indeed carries it over from a DIFFERENT posting
            # (Coldstream 2026-09-28: "80000" pre-filled on a $25-35/hr job). Pay needs a
            # per-posting answer (by the candidate's pay rule in facts.json) or the candidate.
            unanswered.append({"label": label, "type": f["type"], "required": req,
                               "prefilled": f.get("value"), "why": "pre-filled pay from another posting"})
            continue
        if ans is None and f["type"] not in ("radio", "checkbox") and PHONE_LABEL.search(label or "") \
                and re.fullmatch(r"[\d\s().+-]{10,}", str(f.get("value") or "").strip()) \
                and re.search(r"\D", str(f.get("value")).strip()):
            ans = f["value"]  # pre-filled with dashes; refilled as digits below (2026-10-10)
        if ans is None and f["type"] not in ("radio", "checkbox") and str(f.get("value") or "").strip():
            report.setdefault("prefilled_kept", []).append({"question": label, "value": f["value"]})
            continue  # Indeed pre-filled it from the profile; leave it
        required = req or (
            f["type"] in ("radio", "select-one") and not re.search(r"optional", label, re.I))
        if ans is None:
            if required:
                opts = f["options"] if f["tag"] != "combobox" else combobox_options(page, f)
                unanswered.append({"label": label, "type": f["type"], "options": opts, "required": True})
            else:
                report.setdefault("optional_left_blank", []).append(label)
            continue
        try:
            cands = [str(c) for c in ans] if isinstance(ans, list) else [str(ans)]
            if f["type"] in ("radio", "checkbox"):
                if label in done_groups:
                    continue
                # Scope to this question's own options. A page-wide get_by_label("No").first
                # hits the first "No" on the page, i.e. another question's (Harbor Health loop).
                # An answer may be a list of acceptable option texts, tried in order (self-ID
                # wording differs per form: "I am not a protected veteran" / "No" / ...).
                mine = []
                for cand in cands:
                    mine = [g for g in snap["fields"] if g["type"] == f["type"] and g["group"] == f["group"]
                            and g.get("name") == f.get("name") and _canon(g["label"]) == _canon(cand)]
                    if len(mine) == 1:
                        ans = cand
                        break
                if not mine and f["type"] == "checkbox" and f.get("id") and _canon(cands[0]) == "yes":
                    # A single opt-in box (Taconic 2026-10-10: "(Optional) I would like to receive text
                    # messages..."): the box itself is the option, and "Yes" means ticked.
                    mine = [f]
                if len(mine) != 1 or not mine[0]["id"]:
                    raise RuntimeError(f"no unique option {cands!r} in group {label!r}")
                oid = mine[0]["id"]
                if mine[0].get("value") is True:
                    # Already the right option (Indeed pre-selects saved answers). Clicking it anyway
                    # timed out on Huron's self-ID page (2026-09-28), so leave it alone.
                    done_groups.add(label)
                    report["answers_given"].append({"question": label, "answer": ans, "note": "already selected"})
                    continue
                try:
                    page.locator(f'[id="{oid}"]').check(timeout=5000)
                except Exception:  # noqa: BLE001
                    page.locator(f'label[for="{oid}"]').click(timeout=5000)
                if not page.locator(f'[id="{oid}"]').is_checked():
                    raise RuntimeError(f"option {ans!r} did not stay checked in {label!r}")
                done_groups.add(label)
            elif f["tag"] == "combobox":
                cur = _canon(f.get("value") or "")
                if cur and any(_canon(c) and _canon(c) in cur for c in cands):
                    # e.g. "White (United States of America)" already selected for "White"
                    report["answers_given"].append({"question": label, "answer": f.get("value"), "note": "already selected"})
                    continue
                cb = _combobox(page, f)
                cb.click(timeout=10000)
                page.wait_for_timeout(800)
                opts = page.get_by_role("option")
                texts = [opts.nth(k).inner_text() for k in range(opts.count())]
                pick = None
                for cand in cands:
                    hit = [k for k, t in enumerate(texts) if _canon(t) == _canon(cand)]
                    if hit:
                        pick, ans = opts.nth(hit[0]), cand
                        break
                if pick is None:
                    # SOLV 2026-10-12: options can carry a country suffix ("White (Not Hispanic or
                    # Latino) (United States of America)"). Accept a prefix match, but only when it
                    # is the one option that starts with the answer.
                    for cand in cands:
                        c = _canon(cand)
                        hit = [k for k, t in enumerate(texts) if c and _canon(t).startswith(c)]
                        if len(hit) == 1:
                            pick, ans = opts.nth(hit[0]), cand
                            break
                if pick is None:
                    page.keyboard.press("Escape")
                    raise RuntimeError(f"no option {cands!r} in dropdown {label!r}")
                pick.click(timeout=10000)
                page.wait_for_timeout(500)
                if _canon(ans) not in _canon(cb.inner_text()):
                    raise RuntimeError(f"dropdown {label!r} did not take {ans!r}")
            elif f["tag"] == "select":
                sel = page.locator(f"select#{f['id']}") if f["id"] else page.get_by_label(f["label"])
                have = f.get("options") or []
                ans = next((c for c in cands for o in have if _canon(o) == _canon(c)), cands[0])
                ans = next((o for o in have if _canon(o) == _canon(ans)), ans)
                sel.first.select_option(label=str(ans), timeout=10000, force=True)  # may sit behind a widget
            else:
                ans = cands[0]
                loc = page.locator(f"#{f['id']}") if f["id"] else page.get_by_label(f["label"])
                # Measured 2026-10-09 (AVIBE, Henco, Harvest): Indeed's "How many years of X" boxes accept
                # digits only ("Answer must be a valid number (no decimals)"), but answers arrive as
                # "4+ years". Reduce a years answer to its leading whole number for these boxes.
                # 2026-10-10 (NLBM): a free-text "How many years of GoHighLevel... describe..." box got
                # "30" pulled from the middle of a "None yet... about 30 staff" answer. Never shorten a
                # textarea, and take only a LEADING number ("None/No" means 0).
                if re.match(r"\s*how many years", label or "", re.I) and f.get("type") != "textarea" \
                        and not re.fullmatch(r"\s*\d+\s*", str(ans)):
                    lead = re.match(r"\s*(\d+)", str(ans))
                    if lead:
                        ans = lead.group(1)
                    elif re.match(r"\s*(none|no|zero|n/?a)\b", str(ans), re.I):
                        ans = "0"
                # Measured 2026-10-10 (The Rural Collaborative): a "Date Available" box rejected
                # "Immediately" with "Please enter dates in the format MM/DD/YYYY". A date field whose
                # answer holds no digits ("Immediately", "ASAP") gets today's date instead.
                if re.search(r"\bdate\b", label or "", re.I) and not re.search(r"\d", str(ans)):
                    import datetime as _dt
                    ans = _dt.date.today().strftime("%Y-%m-%d" if f.get("type") == "date" else "%m/%d/%Y")
                # 2026-10-10: some phone boxes reject dashes. Phone numbers go in as digits only.
                if PHONE_LABEL.search(label or "") and len(re.sub(r"\D", "", str(ans))) >= 10:
                    ans = re.sub(r"\D", "", str(ans))
                loc.first.fill(str(ans), timeout=10000)
            report["answers_given"].append({"question": label, "answer": ans})
        except Exception as exc:  # noqa: BLE001
            unanswered.append({"label": label, "type": f["type"], "error": repr(exc)[:200]})
    # de-duplicate radio groups reported once per option
    seen, uniq = set(), []
    for u in unanswered:
        if u["label"] not in seen:
            seen.add(u["label"])
            uniq.append(u)
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jk")
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume")
    ap.add_argument("--answers")
    ap.add_argument("--cover-letter")
    ap.add_argument("--form-cover-pdf", help="cover letter PDF for an employer question that requires a file upload labelled 'Cover Letter' (2026-10-09, 406 Recovery)")
    ap.add_argument("--cover-letter-pdf", help="PDF to attach under the review page's Supporting documents")
    ap.add_argument("--remove-supporting", action="append", default=[],
                    help="file name of a previously attached additional document to remove (repeatable)")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--check-only", action="store_true", help="only report whether the posting is live and uses Indeed Apply")
    ap.add_argument("--apply-anyway", action="store_true",
                    help="continue past Indeed's 'you don't meet these employer requirements' screen (explicit approval only)")
    ap.add_argument("--posting", help="job_posting.md for this application (context for auto-answers)")
    ap.add_argument("--no-auto-answer", action="store_true",
                    help="never ask Claude to answer unmatched questions; hand them to a person instead")
    ap.add_argument("--foreground", action="store_true",
                    help="open Chrome as a normal window (default: start minimized, restore only when the user is needed)")
    ap.add_argument("--wait-human", type=int, default=0,
                    help="on unanswerable fields, alert and wait this long for a person to fill them and click Continue")
    ap.add_argument("--captcha-wait", type=int, default=900,
                    help="seconds to wait (with sound alerts) for the user to solve a captcha before giving up")
    ap.add_argument("--hold-seconds", type=int, default=0,
                    help="prepare mode: leave the browser open on the review page this long (or until the window is closed) so a human can inspect it")
    ap.add_argument("--human-submit", type=int, default=None,
                    help="submit mode: stop on the review page, alert, and wait this many seconds for a person to "
                         "click Submit (0 = park immediately). The script never clicks Submit. apply_queue.py "
                         "passes this unless the candidate opted in to auto-submit.")
    ap.add_argument("--confirm-token")
    a = ap.parse_args()

    jk = re.sub(r".*jk=", "", a.jk).strip()
    os.makedirs(a.out, exist_ok=True)
    if a.submit and a.confirm_token != jk:
        print(json.dumps({"error": "refused", "message": "--submit requires --confirm-token equal to the jk"}))
        return 2
    if not a.check_only:
        try:
            expected_name(); candidate.resume_prefix()
        except candidate.CandidateNotSet as e:
            print(json.dumps({"error": "candidate_not_set", "message": str(e)}))
            return 2
    if a.cover_letter_pdf and not os.path.isfile(a.cover_letter_pdf):
        print(json.dumps({"error": "cover_letter_missing", "path": a.cover_letter_pdf}))
        return 2
    if a.resume and not os.path.isfile(a.resume):
        print(json.dumps({"error": "resume_missing", "path": a.resume}))
        return 2
    answers = load_answers(a.answers)
    cover = open(a.cover_letter, encoding="utf-8").read().strip() if a.cover_letter else None

    report = {"jk": jk, "mode": "submit" if a.submit else "prepare", "steps": [], "answers_given": [],
              "log": [], "status": None}
    from patchright.sync_api import sync_playwright
    code = 3
    with sync_playwright() as p:
        ctx = launch(p, background=not a.foreground)
        console = []
        def _on_console(msg):  # noqa: E306
            try:
                if msg.type in ("error", "warning"):
                    console.append(f"{msg.type}: {msg.text[:300]}")
            except Exception:  # noqa: BLE001
                pass
        ctx.on("page", lambda pgx: pgx.on("console", _on_console))
        for _pg in ctx.pages:
            _pg.on("console", _on_console)
        report["console"] = console
        try:
            if CDP_URL:
                page = [x for x in ctx.pages if x not in _SHARED_BEFORE][0]
                page.bring_to_front()
            else:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not a.foreground and not CDP_URL:
                window_state(page, "minimized")  # in case the restored profile ignored --start-minimized
            page.goto(f"https://www.indeed.com/viewjob?jk={jk}", wait_until="domcontentloaded", timeout=60000)
            ip._settle(page, 5000)
            t0 = time.time()
            while ip._blocked(page) and time.time() - t0 < 45:
                ip._settle(page, 3000)
            # Measured 2026-10-10: the job page sits on Cloudflare's "Verify you
            # are human" checkbox, which never clears by itself. With the user reachable, ring and wait
            # for the user to click it in the browser window, exactly as for a review-page captcha.
            if ip._challenged(page) and a.captcha_wait > 0:
                call_human(page)
                log(report, f"Cloudflare check on the job page: waiting up to {a.captcha_wait}s for the user")
                t0 = time.time()
                while ip._challenged(page) and time.time() - t0 < a.captcha_wait:
                    alert_human("Cloudflare 'verify you are human' on Indeed", f"jk {jk}: tick the checkbox")
                    ip._settle(page, 3000)
                ip._settle(page, 3000)
            if ip.AUTH_HOST in (page.url or ""):
                report["status"] = "not_authenticated"; code = 2; raise SystemExit
            if ip._blocked(page):
                report["status"] = "blocked"; code = 2; raise SystemExit
            btn = page.locator("a[data-testid='viewjob-indeed-apply'], a[href*='smartapply.indeed.com'], "
                               "#indeedApplyButton, button[data-testid='indeedApplyButton']")
            body0 = page.inner_text("body") or ""
            report["page_title"] = page.title()
            if re.search(r"this job has expired|no longer accepting applications|job (is )?no longer available", body0, re.I):
                report["status"] = "expired"; code = 2
                log(report, "posting has expired on Indeed")
                raise SystemExit
            if re.search(r"you applied|applied on ", body0, re.I) and not btn.count():
                report["status"] = "already_applied"; code = 2
                raise SystemExit
            report["apply_controls"] = page.evaluate("""() => [...document.querySelectorAll('button, a')]
                .filter(b => /apply|applied|continue/i.test(b.innerText || b.getAttribute('aria-label') || ''))
                .map(b => ({text: (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 80),
                            testid: b.getAttribute('data-testid'), href: (b.getAttribute('href') || '').slice(0, 120)}))
                .slice(0, 10)""")
            report["page_status_text"] = [m.group(0) for m in re.finditer(
                r"[^\n]*(applied|application (was )?submitted|no longer accepting|expired)[^\n]*", body0, re.I)][:5]
            if not btn.count() and any(re.fullmatch(r"\s*applied\s*", c.get("text") or "", re.I)
                                       for c in report["apply_controls"]):
                # The apply button reads "Applied": this account already applied (measured 2026-09-23,
                # Your Legacy, after a submit click that had reported a timeout).
                report["status"] = "already_applied"; code = 0
                log(report, "Indeed shows this job as Applied; nothing to do")
                raise SystemExit
            if not btn.count():
                report["status"] = "not_indeed_apply"; code = 2
                ext = page.locator("a:visible, button:visible", has_text=re.compile(r"apply on company site", re.I))
                report["company_site"] = ext.first.get_attribute("href") if ext.count() else None
                if ext.count():
                    # Indeed's own button says so: definitive, not a transient page (First Legal and
                    # Cultura 2026-09-28 retried pointlessly under the old catch-all status).
                    report["status"] = "company_site_only"
                log(report, "no Indeed Apply control: this posting applies on the company site")
                raise SystemExit
            if a.check_only:
                report["status"] = "indeed_apply_available"; code = 0
                log(report, "Indeed Apply available (check-only; form not opened)")
                raise SystemExit
            before = len(ctx.pages)
            btn.first.click(timeout=15000)
            ip._settle(page, 6000)
            pg = ctx.pages[-1] if len(ctx.pages) > before else page
            ip._settle(pg, 3000)

            for i in range(1, MAX_STEPS + 1):
                if re.search(r"captcha|verify you are human", pg.inner_text("body") or "", re.I) and \
                        not re.search(r"protected by reCAPTCHA", pg.inner_text("body") or "", re.I):
                    report["status"] = "captcha"; code = 3
                    pg.screenshot(path=os.path.join(a.out, f"{i:02d}_captcha.png"), full_page=True)
                    raise SystemExit
                snap = pg.evaluate(SNAP_JS)
                mod = module_of(snap["url"])
                snap["module"] = mod
                # mod is the raw URL on non-form pages; ":" and "?" are illegal in Windows file
                # names (OSError 22 killed a queue run on 2026-09-28).
                safe_mod = re.sub(r"[^\w.-]+", "_", mod)[:40]
                pg.screenshot(path=os.path.join(a.out, f"{i:02d}_{safe_mod}.png"), full_page=True)
                report["steps"].append(snap)
                log(report, f"step {i}: {mod} | {snap['heading'][-1] if snap['heading'] else ''}")

                submit_btn = pg.locator("button:visible", has_text=SUBMIT_RE)
                if submit_btn.count():
                    if (cover or a.cover_letter_pdf or a.remove_supporting) and not report.get("cover_letter_attached"):
                        got = set_cover_letter(pg, cover, a.cover_letter_pdf, a.out, report,
                                               remove_names=a.remove_supporting)
                        if got is False:
                            report["status"] = "cover_letter_not_attached"; code = 3; raise SystemExit
                        # None = the posting offers no slot (common on Indeed); report it, carry on
                        report["cover_letter_attached"] = ("text set" + (" + " + os.path.basename(a.cover_letter_pdf)
                                                           if a.cover_letter_pdf else "") if got
                                                           else "not offered by this posting")
                        pg.screenshot(path=os.path.join(a.out, f"{i:02d}_review_with_letter.png"), full_page=True)
                        snap = pg.evaluate(SNAP_JS)
                        submit_btn = pg.locator("button:visible", has_text=SUBMIT_RE)
                    if not ensure_contact_name(pg, a.out, report):
                        # 2026-10-10: first try to get back to the review page (a fix attempt can strand us
                        # elsewhere); the name gate below then decides. Nothing is sent with an unverified name.
                        #
                        pg.screenshot(path=os.path.join(a.out, "zz_name_problem.png"), full_page=True)
                        if not _submit_visible(pg):
                            try:
                                pg.go_back(timeout=15000)
                                ip._settle(pg, 4000)
                            except Exception:  # noqa: BLE001
                                pass
                        # 2026-10-10 (after "Buch el" went out on Thrive): a wrong name must be fixed,
                        # never sent. With the user reachable, ring and wait for a fix via Contact
                        # information > Edit; otherwise park. Nothing is sent with an unverified name.
                        want_name = " ".join(expected_name())
                        if a.wait_human > 0 and _submit_visible(pg):
                            call_human(pg)
                            log(report, f"review name {review_name(pg)!r} != {want_name!r}: waiting for a person to fix it")
                            t0 = time.time()
                            while time.time() - t0 < a.wait_human and not _same_name(review_name(pg), want_name):
                                alert_human("name on Indeed is wrong", f"jk {report['jk']}: review shows {review_name(pg)!r}; "
                                            "use Contact information > Edit to set " + " / ".join(expected_name()))
                                pg.wait_for_timeout(3000)
                        if not (_submit_visible(pg) and _same_name(review_name(pg), want_name)):
                            report["status"] = "name_not_verified"; code = 3
                            report["review_name_final"] = review_name(pg)
                            log(report, "name still not verified; nothing sent")
                            raise SystemExit
                        log(report, "name corrected; continuing")
                    if a.resume:
                        # Measured 2026-09-23 (United Site Services): a retry resumed straight onto the
                        # review page, skipped the resume step, and Indeed attached the LAST uploaded CV
                        # (another posting's). Never submit unless the review shows this posting's file.
                        want_cv = os.path.basename(a.resume)
                        shown = re.findall(re.escape(candidate.resume_prefix()) + r"[\w\-]+\.pdf", all_frames_text(pg))
                        report["review_resume"] = shown
                        if want_cv not in shown or any(s != want_cv for s in shown):
                            report["status"] = "wrong_resume"; code = 3
                            pg.screenshot(path=os.path.join(a.out, "zz_wrong_resume.png"), full_page=True)
                            log(report, f"review page shows {shown}, expected {want_cv}; NOT submitting")
                            raise SystemExit
                    pg.screenshot(path=os.path.join(a.out, "zz_review_final.png"), full_page=True)
                    submit_btn = pg.locator("button:visible", has_text=SUBMIT_RE)
                    report["review"] = {"heading": snap["heading"], "buttons": snap["buttons"],
                                        "review_text": all_frames_text(pg)[:60000]}
                    try:
                        sb = submit_btn.first
                        report["submit_button"] = {
                            "enabled": sb.is_enabled(),
                            "html": (sb.evaluate("e => e.outerHTML") or "")[:600],
                            "aria_disabled": sb.get_attribute("aria-disabled"),
                            "title": sb.get_attribute("title"),
                        }
                    except Exception as exc:  # noqa: BLE001
                        report["submit_button"] = {"error": repr(exc)[:200]}
                    if not a.submit:
                        report["status"] = "ready_for_review"; code = 0
                        log(report, f"reached review page; submit enabled={report['submit_button'].get('enabled')}. Nothing was submitted.")
                        if a.hold_seconds > 0:
                            json.dump(report, open(os.path.join(a.out, "apply_report.json"), "w", encoding="utf-8"),
                                      ensure_ascii=False, indent=1)
                            log(report, f"HOLDING: browser left open on the review page for up to {a.hold_seconds}s. "
                                        "Close the Chrome window when you are done. Nothing will be clicked.")
                            t_end = time.time() + a.hold_seconds
                            while time.time() < t_end:
                                try:
                                    live = [x for x in ctx.pages if not x.is_closed()]
                                    if not live:
                                        break
                                    live[0].wait_for_timeout(2000)  # pumps the driver; see wait_for_human_captcha
                                except Exception:  # noqa: BLE001
                                    break
                            log(report, "hold ended")
                        raise SystemExit
                    # SUBMIT mode, confirmed by token
                    # Measured 2026-09-23: after ~30 automated passes in one day, Indeed rendered
                    # "Submit your application" DISABLED with no visible reason (reCAPTCHA-protected
                    # page). Treat that as a block: stop, never try to work around it.
                    if not wait_for_human_captcha(pg, submit_btn, a.out, report, a.captcha_wait):
                        report["status"] = "submit_disabled"; code = 3
                        log(report, "Submit still disabled after waiting; stopping, nothing sent")
                        raise SystemExit
                    submit_btn = pg.get_by_role("button", name=re.compile(r"^\s*submit your application\s*$", re.I))
                    if not submit_btn.count():
                        submit_btn = pg.locator("button:visible", has_text=SUBMIT_RE)
                    if a.human_submit is not None:
                        # Human-submit (the default for a candidate who has not opted in to auto-submit):
                        # everything up to the review page is filled and gated as usual; the script never
                        # clicks Submit here.
                        if a.human_submit <= 0:
                            report["status"] = "awaiting_submit_click"; code = 3
                            pg.screenshot(path=os.path.join(a.out, "zz_ready_for_submit.png"), full_page=True)
                            log(report, "review page ready; human-submit mode with nobody present: parking, nothing sent")
                            raise SystemExit
                        call_human(pg)
                        try:
                            submit_btn.first.scroll_into_view_if_needed(timeout=10000)
                        except Exception:  # noqa: BLE001
                            pass
                        pg.screenshot(path=os.path.join(a.out, "zz_ready_for_submit.png"), full_page=True)
                        log(report, f"review page ready: waiting up to {a.human_submit}s for a person to click Submit (alerting)")
                        t0, last, body = time.time(), 0.0, ""
                        clicked = False
                        while time.time() - t0 < a.human_submit:
                            if time.time() - last > 30:
                                alert_human("review page ready: tap Submit", f"jk {report['jk']}: check the page, then click Submit your application")
                                last = time.time()
                            try:
                                pg.wait_for_timeout(2000)  # never time.sleep: keeps the driver pumping
                                body = " ".join((f.evaluate("() => document.body ? document.body.innerText : ''") or "")
                                                for f in pg.frames if not f.is_detached())
                            except Exception:  # noqa: BLE001
                                break
                            if re.search(r"application (was |has been )?submitted|your application was sent|"
                                         r"complete these steps before your application is submitted", body, re.I):
                                clicked = True
                                break
                        if not clicked:
                            report["status"] = "awaiting_submit_click"; code = 3
                            log(report, "nobody clicked Submit within the wait; nothing sent by the script")
                            raise SystemExit
                        ip._settle(pg, 4000)
                        body = " ".join((f.evaluate("() => document.body ? document.body.innerText : ''") or "")
                                        for f in pg.frames if not f.is_detached())
                        pg.screenshot(path=os.path.join(a.out, "zz_after_submit.png"), full_page=True)
                        if re.search(r"complete these steps before your application is submitted", body, re.I):
                            report["status"] = "needs_followup_steps"; code = 3
                            report["followup"] = re.findall(r"[^\n]*(?:questions|assessment|hour)[^\n]*", body, re.I)[:6]
                            alert_human("follow-up steps due within the hour", f"jk {report['jk']}: Indeed wants AI-recruiter answers")
                            log(report, "Indeed requires follow-up steps within an hour; the candidate completes them")
                            raise SystemExit
                        report["confirmation_text"] = (re.search(r"[^\n]*application[^\n]*submitted[^\n]*", body, re.I) or
                                                       [None])[0]
                        report["status"] = "submitted"; code = 0
                        log(report, "Submit clicked by hand; confirmation seen")
                        raise SystemExit
                    try:
                        # Measured 2026-09-23 (Your Legacy, long essay page): the click straight after the
                        # captcha reported a timeout, the screenshot still showed the review page, and yet
                        # the job was APPLIED. So a click error does NOT prove nothing was sent: never
                        # re-submit on it. Re-run prepare/check first; it reports already_applied.
                        pg.wait_for_timeout(3000)
                        submit_btn.first.scroll_into_view_if_needed(timeout=10000)
                        submit_btn.first.click(timeout=30000)
                    except Exception as exc:  # noqa: BLE001
                        pg.screenshot(path=os.path.join(a.out, "zz_submit_click_failed.png"), full_page=True)
                        report["status"] = "submit_click_error"; code = 3
                        log(report, f"Submit click raised {type(exc).__name__}; the application MAY have been "
                                    "sent. Check the job with --check-only (already_applied) before any re-run.")
                        raise SystemExit
                    ip._settle(pg, 8000)
                    body = " ".join((f.evaluate("() => document.body ? document.body.innerText : ''") or "")
                                    for f in pg.frames if not f.is_detached())
                    pg.screenshot(path=os.path.join(a.out, "zz_after_submit.png"), full_page=True)
                    if re.search(r"complete these steps before your application is submitted", body, re.I):
                        # Measured 2026-09-23 (ACV): after Submit, Indeed may demand "AI recruiter
                        # questions" (video/audio/text) within an HOUR or the application is not sent.
                        # Those are the candidate's own answers; alert and stop.
                        report["status"] = "needs_followup_steps"; code = 3
                        report["followup"] = re.findall(r"[^\n]*(?:questions|assessment|hour)[^\n]*", body, re.I)[:6]
                        call_human(pg)
                        alert_human("follow-up steps due within the hour", f"jk {report['jk']}: Indeed wants AI-recruiter answers")
                        log(report, "Indeed requires follow-up steps within an hour before the application is "
                                    "submitted; the candidate must complete them")
                        raise SystemExit
                    # Measured 2026-09-23: "Your application was submitted to <Company>"
                    ok = bool(re.search(r"application (was |has been )?submitted|your application was sent", body, re.I))
                    report["confirmation_text"] = (re.search(r"[^\n]*application[^\n]*submitted[^\n]*", body, re.I) or
                                                   [None])[0]
                    report["status"] = "submitted" if ok else "submit_unconfirmed"
                    code = 0 if ok else 3
                    log(report, f"submit clicked; confirmation {'seen' if ok else 'NOT seen'}")
                    raise SystemExit

                if "intervention" in (snap.get("url") or "") or any(
                        re.search(r"don.t meet these employer requirements", h, re.I) for h in snap["heading"]):
                    report["intervention"] = [h for h in snap["heading"] if "requirement" in h.lower()]
                    if not a.apply_anyway:
                        report["status"] = "requirements_not_met"; code = 3
                        log(report, "Indeed says the screening answers do not meet the employer's requirements; "
                                    "stopping (pass --apply-anyway only on the candidate's explicit say-so)")
                        raise SystemExit
                    anyway = pg.locator("button:visible", has_text=re.compile(r"^\s*Apply anyway\s*$", re.I))
                    if not anyway.count():
                        report["status"] = "unexpected_screen"; code = 3; raise SystemExit
                    anyway.first.click(timeout=15000)
                    ip._settle(pg, 5000)
                    log(report, "clicked 'Apply anyway' (explicitly authorized)")
                    continue

                if "resume-selection" in mod and a.resume:
                    if not upload_resume(pg, a.resume, report):
                        report["status"] = "resume_not_selected"; code = 3; raise SystemExit
                    snap = pg.evaluate(SNAP_JS)

                if a.form_cover_pdf and not re.search(r"resume|review|supporting", mod):
                    upload_form_cover(pg, a.form_cover_pdf, report)

                if snap["fields"] and not re.search(r"resume|review", mod):
                    missing = answer_fields(pg, snap, answers, cover, report)
                    if missing and not a.no_auto_answer:
                        # Before summoning a person, let Claude answer from the candidate's profile (guarded
                        # categories - signatures, consents, termination/criminal - are never sent).
                        qs = []
                        for m in missing:
                            opts = m.get("options")
                            if not opts and m.get("type") in ("radio", "checkbox"):
                                opts = [g["label"] for g in snap["fields"] if g["type"] == m.get("type")
                                        and (g["group"] or g["label"]) == m["label"]]
                            qs.append({"label": m["label"], "type": m.get("type"), "options": opts})
                        # Respect employer "no AI writing" notices (seen on Makai Labs 2026-09-28): free
                        # text on such a page is the candidate's own writing, never Claude's.
                        if re.search(r"without (the )?(use of )?AI|AI (writing )?tools?.{0,40}(disqualif|not (be )?(allowed|permitted))|"
                                     r"no AI|not use (ChatGPT|AI)", all_frames_text(pg), re.I):
                            held = [q for q in qs if q["type"] in ("textarea", "text")]
                            qs = [q for q in qs if q["type"] not in ("textarea", "text")]
                            if held:
                                log(report, f"page forbids AI-written answers; {len(held)} free-text question(s) left for the candidate")
                        got, note = auto_answer.answer_questions(qs, a.posting, wait=pg.wait_for_timeout)
                        log(report, f"auto-answer: {note}")
                        if got:
                            report.setdefault("auto_answers", []).extend(
                                {"question": k, "answer": v["answer"], "reason": v["reason"]} for k, v in got.items())
                            # remember reusable answers so the next posting skips the Claude call
                            byl = {q["label"]: q for q in qs}
                            new = answer_store.remember(
                                [{"label": k, "type": byl.get(k, {}).get("type"), "options": byl.get(k, {}).get("options"),
                                  "answer": v["answer"], "reason": v["reason"]} for k, v in got.items()],
                                answer_store.company_from_posting(a.posting), jk)
                            if new:
                                report.setdefault("learned_new", []).extend(new)
                                log(report, f"learned {len(new)} new answer(s) for reuse")
                            extra = [(re.compile(re.escape(norm_label(k)), re.I), v["answer"]) for k, v in got.items()]
                            snap = pg.evaluate(SNAP_JS)
                            missing = answer_fields(pg, snap, extra + answers, cover, report)
                    if missing:
                        report["unanswered"] = missing
                        pg.screenshot(path=os.path.join(a.out, f"{i:02d}_needs_answers.png"), full_page=True)
                        if a.wait_human > 0:
                            # Hand-off: a person completes the listed fields (signatures, attestations,
                            # history corrections) in the open window and clicks Continue.
                            # We alert, keep the driver pumping, and resume once the page moves on.
                            here = pg.url
                            log(report, f"HAND-OFF: {len(missing)} field(s) need a person; waiting up to "
                                        f"{a.wait_human}s for a person to complete them and click Continue")
                            call_human(pg)
                            t0, last = time.time(), 0.0
                            while time.time() - t0 < a.wait_human and pg.url == here:
                                if time.time() - last > 30:
                                    alert_human("hand-off: fields only you can fill", f"jk {report['jk']}: {len(missing)} field(s), then click Continue")
                                    last = time.time()
                                pg.wait_for_timeout(2000)
                            if pg.url == here:
                                report["status"] = "needs_answers"; code = 3
                                log(report, "hand-off timed out; nothing sent")
                                raise SystemExit
                            report.setdefault("handoffs", []).append({"step": i, "fields": [m["label"] for m in missing]})
                            log(report, "the step was completed by hand; resuming")
                            ip._settle(pg, 3000)
                            continue
                        report["status"] = "needs_answers"; code = 3
                        log(report, f"{len(missing)} question(s) with no grounded answer; stopping")
                        raise SystemExit

                adv = pg.locator("button:visible", has_text=ADVANCE_RE)
                if not adv.count() and not report.get("_waited_" + str(i)):
                    # A "Preparing review" spinner can hold the review page for a while (Twin Health
                    # 2026-09-23). Wait up to 45s for Submit or an advance button, then re-snapshot.
                    report["_waited_" + str(i)] = True
                    for _ in range(15):
                        pg.wait_for_timeout(3000)
                        if _submit_visible(pg) or pg.locator("button:visible", has_text=ADVANCE_RE).count():
                            break
                    if _submit_visible(pg):
                        log(report, "page finished loading; re-reading it")
                        continue
                    adv = pg.locator("button:visible", has_text=ADVANCE_RE)
                if not adv.count():
                    report["status"] = "unexpected_screen"; code = 3
                    log(report, f"no advance button on {mod}; stopping for a human")
                    raise SystemExit
                url_before = pg.url
                adv.first.click(timeout=15000)
                ip._settle(pg, 5000)
                if pg.url == url_before:
                    # Indeed's inline errors ("Choose an option to continue.") are not always role=alert
                    # (ACV 2026-09-23 looped 15 times on one). Look for error-styled text too, and treat
                    # a second no-move on the same page as stuck even when no message is found.
                    errs = pg.evaluate("""() => [...document.querySelectorAll(
                            '[role=alert], [id*=error i], [class*=error i], [data-testid*=error i]')]
                        .map(e => (e.innerText || '').trim()).filter(t => t && t.length < 200)
                        .filter((t, k, arr) => arr.indexOf(t) === k).slice(0, 10)""")
                    stuck_count = report.get("_stuck", 0) + 1
                    report["_stuck"] = stuck_count
                    if errs or stuck_count >= 2:
                        errs = errs or ["page did not advance and showed no readable error"]
                        report["validation"] = errs
                        report["status"] = "validation_error"; code = 3
                        pg.screenshot(path=os.path.join(a.out, f"{i:02d}_validation.png"), full_page=True)
                        raise SystemExit
            report["status"] = "too_many_steps"; code = 3
        except SystemExit:
            pass
        except Exception as exc:  # noqa: BLE001
            report["status"] = "error"; report["error"] = repr(exc)[:500]; code = 3
        finally:
            json.dump(report, open(os.path.join(a.out, "apply_report.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            try:
                if CDP_URL:
                    # shared browser: close only the tabs this run opened, leave Chrome running
                    for x in ctx.pages:
                        if x not in _SHARED_BEFORE and not x.is_closed():
                            x.close()
                else:
                    ctx.close()
            except Exception:  # noqa: BLE001
                pass
    summary = {k: report.get(k) for k in ("jk", "mode", "status", "unanswered", "answers_given", "prefilled_kept",
                                          "prefilled_needs_review", "cover_letter_attached", "removed_supporting", "company_site",
                                          "review_name_before", "review_name_after", "submit_button", "intervention",
                                          "apply_controls", "page_status_text", "validation", "error")}
    summary["console_errors"] = (report.get("console") or [])[-15:]
    summary["steps"] = [s.get("module") for s in report["steps"]]
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
