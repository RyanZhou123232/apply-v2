"""Page loop: read -> plan -> fill -> verify -> Next; stop before Submit unless submit=True.

CLI: python -m apply.runner --url URL --resume PDF --role-category SWE [--headless] [--legacy-browser]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import rules, workday
from .p2.jd_fetch import is_lever_url
from .browser import first_page, open_context
from .answer_planner import AnswerPlanner, Context, extract_resume_text
from .form_filler import FormFiller
from .form_reader import Snapshot, locator, read_page

log = logging.getLogger("apply.runner")
LEVER_SKIP_REASON = "Lever is skipped: the platform bot check rejects automated sessions."
ROOT = Path(__file__).resolve().parents[2]
MAX_RETRIES = 3
MAX_PAGES = 15
CONFIRM_RE = re.compile(r"thank you for (applying|your application|submitting)|application (has been |was )?(received|submitted)|"
                        r"successfully submitted|we('| ha)ve received your application|申请已提交|提交成功", re.I)


@dataclass
class RunResult:
    status: str = rules.FAILED
    ryan_status: str = ""
    reason: str = ""          # one-line why for 未提交 / 待决定 rows
    url: str = ""
    final_url: str = ""
    submitted: bool = False
    final_page_reached: bool = False
    pending_items: list[dict] = field(default_factory=list)
    filled: list[dict] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    screenshot: str = ""
    llm: dict = field(default_factory=dict)
    blocked_requests: list[str] = field(default_factory=list)
    submit_clicked: bool = False
    submit_clicked_at: str = ""
    confirmation_text: str = ""
    confirmation_excerpt: str = ""
    resubmit_safe: bool = True  # False once Submit was clicked and the outcome is not a provable non-submission
    seconds: float = 0.0

    def finish(self, status: str) -> "RunResult":
        self.status = status
        self.ryan_status = rules.map_status(status)
        return self


def _unfilled_required(snap: Snapshot, pending_keys: set[str]) -> list:
    return [f for f in snap.fields if f.required and not f.value and f.key not in pending_keys]


def _wait_settle(page, ms: int = 800):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
        page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def _shot(page, run_dir: Path, name: str) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / f"{name}.png"
    try:
        page.screenshot(path=str(p), full_page=True)
        return str(p)
    except Exception as e:
        log.warning("screenshot failed: %s", e)
        return ""


SUBMIT_REQUEST_RE = re.compile(r"submit|/apply/?(\?|$)|/applications?/?(\?|$)|/candidates/?(\?|$)", re.I)
UPLOAD_REQUEST_RE = re.compile(r"upload|parse|resume|attachment|file|presign|s3", re.I)


def install_submit_guard(page, blocked: list[str]) -> None:
    """Dry-run safety net: abort write requests that look like a final application submission.

    Defense in depth for submit=False; the runner also never clicks a submit-classified button.
    """
    def handle(route, request):  # noqa: C901
        if request.method in ("POST", "PUT", "PATCH"):
            body = ""
            try:
                body = (request.post_data or "")[:4000] if "json" in (request.headers.get("content-type") or "") else ""
            except Exception:
                pass
            hit = SUBMIT_REQUEST_RE.search(request.url) and not UPLOAD_REQUEST_RE.search(request.url)
            op = re.search(r"[?&]op=([\w]+)", request.url)
            op_name = op.group(1) if op else (re.search(r'"operationName"\s*:\s*"(\w+)"', body) or [None, ""])[1]
            gql = "graphql" in request.url and bool(re.search(r"submit|apply", op_name or "", re.I))
            if hit or gql:
                blocked.append(f"{request.method} {request.url[:200]}")
                return route.abort()
        return route.continue_()
    page.context.route("**/*", handle)  # context-wide, so tabs opened by an Apply button are covered


def run_application(page, url: str, ctx: Context, planner: AnswerPlanner, submit: bool = False,
                    run_dir: Optional[Path] = None, confirm_submit=None, get_verification_code=None,
                    captcha_wait_s: int = 0, credentials=None, on_account_created=None,
                    get_verification_link=None, prefer_signin: bool = False) -> RunResult:
    """confirm_submit(result, page) -> bool is an optional last gate called right before clicking Submit.

    Workday URLs go through workday.run_workday (account sign-up / sign-in with `credentials`, default
    Ryan's shared password from WORKDAY_PASSWORD; on_account_created(creds) records each new account;
    get_verification_link(since_epoch, host) reads the verification email)."""
    t0 = time.time()
    page.set_default_timeout(8000)
    run_dir = run_dir or ROOT / "runs" / time.strftime("%Y%m%d-%H%M%S")
    res = RunResult(url=url)
    if is_lever_url(url):
        res.reason = LEVER_SKIP_REASON
        res.seconds = 0.0
        return res.finish(rules.FAILED)
    if not submit:
        install_submit_guard(page, res.blocked_requests)
    filler = FormFiller(page, ctx.resume_path)
    pending: dict[str, dict] = {}  # field key -> item

    cur = {"page": page}  # the active tab (an Apply button may open a new one)

    def done(status: str, reason: str = "") -> RunResult:
        pg = cur["page"]
        if reason:
            res.errors.append(reason)
        res.reason = res.reason or reason or (res.errors[-1] if res.errors and status != rules.SUBMITTED else "")
        if not res.reason and status == rules.PENDING and pending:
            res.reason = "needs Ryan: " + "; ".join(f"{p['field'][:60]} ({p['reason'][:60]})" for p in pending.values())
        res.pending_items = list(pending.values())
        res.final_url = pg.url
        res.llm = planner.stats.as_dict()
        res.seconds = round(time.time() - t0, 1)
        if res.submit_clicked and status != rules.SUBMITTED:
            res.resubmit_safe = False
        if status in (rules.FAILED, rules.CAPTCHA, rules.LOGIN_REQUIRED, rules.BLOCKED) and not res.screenshot:
            res.screenshot = _shot(pg, run_dir, f"debug-{status.lower()}")
        return res.finish(status)

    def wall(s: Snapshot, where: str) -> Optional[RunResult]:
        """End cleanly on bot protection or an interactive captcha (unattended runs never solve these)."""
        reason = s.bot_block
        if s.challenge and not reason:
            return done(_challenge_status(s.challenge), f"{s.challenge} shown {where}; not solvable unattended")
        if reason:
            return done(rules.BLOCKED, f"blocked by site bot protection {where}: {reason}")
        return None

    if workday.is_workday(url):
        try:
            return workday.run_workday(page, url, ctx, planner, res, run_dir, done, submit, confirm_submit,
                                       credentials, on_account_created, get_verification_link, pending, prefer_signin=prefer_signin)
        except Exception as e:  # never leave an unknown state: a clicked Submit is not provably unsubmitted
            log.exception("workday run crashed")
            return done(rules.FAILED, f"workday crash: {e!r}"[:400])
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        res.errors.append(f"navigation failed: {e}")
        return done(rules.FAILED)
    _wait_settle(page, 1200)

    snap = read_page(page)
    if (r := wall(snap, "on the posting page")):
        return r
    if len(snap.fields) < 2 and rules.is_closed_posting(snap.text + " " + " ".join(b.text for b in snap.buttons)):
        res.errors.append("posting closed / expired")
        return done(rules.FAILED)
    for _ in range(2):  # job-description page: open the form via its Apply / I'm interested button
        if len([f for f in snap.fields if f.type != "checkbox"]) >= 2 or snap.login_required:
            break
        entry = next((b for b in snap.buttons if rules.is_entry_button(b.text)), None)
        if not entry:
            break
        if not ctx.job_context:
            ctx.job_context = f"{snap.title}\n{snap.text[:5000]}"
        res.pages.append({"page": 0, "url": snap.url, "entry_click": entry.text})
        n_pages = len(page.context.pages)
        locator(page, entry.frame_index, entry.apply_id).first.click(timeout=10000)
        _wait_settle(page, 2500)
        if len(page.context.pages) > n_pages:  # opened in a new tab
            page = page.context.pages[-1]
            cur["page"] = page
            _wait_settle(page, 2500)
        snap = read_page(page)
        if (r := wall(snap, "after opening the application")):
            return r
    filler = FormFiller(page, ctx.resume_path)
    if not ctx.job_context:
        ctx.job_context = f"{snap.title}\n{snap.text[:5000]}"

    for page_no in range(1, MAX_PAGES + 1):
        if snap.login_required:
            res.errors.append("login / account page detected")
            return done(rules.LOGIN_REQUIRED)
        if (r := wall(snap, f"on page {page_no}")):
            return r
        if not snap.fields and snap.captcha:
            res.errors.append("captcha / bot challenge before the form")
            return done(rules.CAPTCHA)
        info = {"page": page_no, "url": snap.url, "heading": snap.heading, "attempts": 0, "advanced": False}
        res.pages.append(info)
        history: list[str] = []
        advanced = False
        for attempt in range(MAX_RETRIES + 1):
            info["attempts"] = attempt + 1
            snap = ensure_entries(page, snap, filler)
            harvest_combobox_options(page, snap)
            actions = planner.plan(snap, attempt, history)
            for a in actions:
                f = snap.field(a.key)
                if a.action == "pending":
                    pending[a.key] = {"field": f.label if f else a.key, "reason": a.reason, "page": page_no,
                                      "required": bool(f and f.required)}
                elif a.key in pending and a.action != "skip":
                    pending.pop(a.key, None)
            results = filler.apply(snap, actions)
            for r in results:
                res.filled.append({"page": page_no, "attempt": attempt + 1, **r.as_dict()})
            bad = [r for r in results if not r.ok]
            history.append(f"attempt {attempt + 1}: filled {len(results)} fields, {len(bad)} mismatches: "
                           + "; ".join(f"{r.label[:60]} -> {r.note or r.actual}" for r in bad)[:800])
            page.wait_for_timeout(300)

            before = read_page(page)
            if before.is_final:
                res.final_page_reached = True
                missing = _unfilled_required(before, set(pending))
                if missing and attempt < MAX_RETRIES:
                    history.append("still empty required: " + "; ".join(m.label[:60] for m in missing))
                    snap = before
                    continue
                for m in missing:
                    pending.setdefault(m.key, {"field": m.label, "reason": "required field still empty after retries",
                                               "page": page_no, "required": True})
                info["final"] = True
                if before.captcha:
                    res.errors.append("captcha present on final page")
                    return done(rules.CAPTCHA)
                if not submit:
                    return done(rules.PENDING if pending else rules.READY_TO_SUBMIT)
                if pending:
                    res.errors.append("submit=True but pending items remain; not submitting")
                    return done(rules.PENDING)
                if confirm_submit is not None:
                    res.pending_items = list(pending.values())
                    if not confirm_submit(res, page):
                        res.errors.append("submit declined at final review gate")
                        return done(rules.PENDING)
                return _submit(page, before, res, run_dir, done, get_verification_code, captcha_wait_s,
                               job_label=(ctx.job_context or "").split("\n", 1)[0][:200])

            btn = before.nav_button()
            if not btn:
                res.errors.append(f"no Next/Submit button found on page {page_no}")
                return done(rules.FAILED)
            locator(page, btn.frame_index, btn.apply_id).click(timeout=10000)
            _wait_settle(page)
            after = read_page(page)
            if (r := wall(after, f"after leaving page {page_no}")):
                return r
            if rules.page_advanced(before.keys, after.keys, before.url, after.url, before.doc, after.doc):
                advanced = True
                info["advanced"] = True
                snap = after
                break
            errs = after.page_errors + [f"{f.label}: {e}" for f in after.fields for e in f.errors]
            history.append(f"clicked '{btn.text}' but page did not advance. errors: {errs[:10]}")
            log.info("page %s did not advance (attempt %s): %s", page_no, attempt + 1, errs[:5])
            snap = after
        if not advanced:
            res.errors.append(f"page {page_no} did not advance after {MAX_RETRIES} retries: {history[-1][:500]}")
            return done(rules.PENDING if any(p["required"] and p["page"] == page_no for p in pending.values())
                        else rules.FAILED)
    res.errors.append("too many pages")
    return done(rules.FAILED)


def _challenge_status(challenge: str) -> str:
    """DataDome's puzzle is a bot wall (BLOCKED); hCaptcha / reCAPTCHA puzzles are CAPTCHA."""
    return rules.BLOCKED if "DataDome" in challenge else rules.CAPTCHA


def ensure_entries(page, snap: Snapshot, filler: FormFiller, targets: Optional[dict] = None) -> Snapshot:
    """Open enough entries in repeatable sections (work experience / education) via their Add buttons.

    Only adds, never removes; stops when the entry count stops growing. Returns a fresh snapshot if anything changed.
    """
    targets = targets or rules.ENTRY_TARGETS
    changed = False
    for kind, want in targets.items():
        for _ in range(want):
            have = snap.entry_count(kind)
            btn = snap.add_button(kind)
            if have >= want or not btn:
                break
            if not filler.add_entry(btn):
                break
            new = read_page(page)
            changed = True
            if new.entry_count(kind) <= have:
                log.info("Add (%s) did not create a new %s entry; stopping", btn.text, kind)
                snap = new
                break
            snap = new
    return read_page(page) if changed else snap


def harvest_combobox_options(page, snap: Snapshot) -> None:
    """Open each empty custom combobox once so the planner sees its real option list."""
    for f in snap.fields:
        if f.type != "combobox" or f.options or f.value:
            continue
        try:
            loc = locator(page, f.frame_index, f.apply_id)
            loc.click(timeout=3000)
            page.wait_for_timeout(400)
            opts = page.frames[f.frame_index].locator('[role="option"]:visible, [class*="option"][id*="option"]:visible')
            if not opts.count():
                page.keyboard.press("ArrowDown")  # many widgets open their menu on ArrowDown, not on click
                page.wait_for_timeout(400)
            f.options = [t.strip() for t in opts.all_inner_texts() if t.strip()][:80]
            page.keyboard.press("Escape")
        except Exception as e:
            log.debug("option harvest failed for %s: %s", f.label, e)


VERIFY_RE = re.compile(r"verification code was sent|enter the \d+-character code|security code|verify your email", re.I)
CODE_LEN_RE = re.compile(r"(\d+)-character code|(\d+)-digit code", re.I)

_FILL_CODE_JS = """(code) => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = [...document.querySelectorAll('label, legend, p, div, span')].find(e => /security code|verification code/i.test(e.innerText || '') && e.innerText.length < 200);
  let scope = label; for (let i = 0; i < 4 && scope && scope.querySelectorAll('input').length === 0; i++) scope = scope.parentElement;
  const inputs = [...(scope || document).querySelectorAll('input')].filter(e => vis(e) && !['hidden','checkbox','radio','file','submit'].includes(e.type));
  const single = inputs.filter(e => e.maxLength === 1);
  const setVal = (el, v) => { const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value'); d.set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); };
  if (single.length >= code.length) { single.slice(0, code.length).forEach((el, i) => setVal(el, code[i])); return 'boxes:' + single.length; }
  const one = inputs.find(e => /code/i.test((e.name || '') + (e.id || '') + (e.getAttribute('aria-label') || '') + (e.placeholder || ''))) || inputs[0];
  if (!one) return 'none'; setVal(one, code); return 'single';
}"""


_BANNER_JS = """(lines) => {
  const old = document.getElementById('apply-v2-confirmation-banner'); if (old) old.remove();
  const d = document.createElement('div'); d.id = 'apply-v2-confirmation-banner';
  d.style.cssText = 'all:initial;display:block;box-sizing:border-box;width:100%;padding:8px 12px;background:#fff8c5;color:#111;' +
    'border-bottom:2px solid #d4a72c;font:13px/1.45 Arial,sans-serif;white-space:pre-wrap;word-break:break-all;position:relative;z-index:2147483647;';
  d.textContent = lines.join('\\n');
  document.body.insertBefore(d, document.body.firstChild);
  return d.textContent;
}"""


def confirmation_banner_lines(job_label: str, posting_url: str, page_url: str, confirmation: str, when: str) -> list[str]:
    lines = [f"Job: {job_label or '(unknown)'}", f"Posting: {posting_url}"]
    if page_url and page_url != posting_url:
        lines.append(f"Page: {page_url}")
    lines.append(f"Confirmation: {confirmation}  |  captured {when}")
    return lines


def _confirmation_shot(page, run_dir: Path, res: RunResult, job_label: str) -> str:
    """Confirmation screenshot with a visible header (job + URLs) and a JSON sidecar, so identical ATS pages stay distinguishable."""
    when = time.strftime("%Y-%m-%d %H:%M:%S %z")
    lines = confirmation_banner_lines(job_label, res.url, page.url, res.confirmation_text, when)
    try:
        page.evaluate(_BANNER_JS, lines)
        page.evaluate("() => window.scrollTo(0, 0)")
    except Exception as e:
        log.warning("could not add confirmation banner: %s", e)
    path = _shot(page, run_dir, "submission-confirmation")
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "submission-confirmation.json").write_text(json.dumps(
            {"job": job_label, "posting_url": res.url, "page_url": page.url, "title": page.title(),
             "confirmation_text": res.confirmation_text, "captured_at": when, "screenshot": path},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("could not write confirmation sidecar: %s", e)
    return path


def _submit(page, snap: Snapshot, res: RunResult, run_dir: Path, done, get_code=None,
            captcha_wait_s: int = 0, timeout_s: int = 45, job_label: str = "") -> RunResult:
    """Click Submit once, then watch for confirmation, an email code prompt, a captcha, or validation errors.

    get_code(since_epoch, length) -> code | None   (e.g. email_codes.wait_for_code)
    captcha_wait_s > 0: if an interactive captcha appears, wait for a human to solve it instead of failing.
    """
    btn = next((b for b in reversed(snap.buttons) if b.kind == "submit"), None)
    if not btn:
        res.errors.append("submit button not found")
        return done(rules.FAILED)
    before_text = snap.text + " " + snap.title
    submit_epoch = time.time()
    res.submit_clicked = True
    res.submit_clicked_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    locator(page, btn.frame_index, btn.apply_id).click(timeout=10000)
    deadline = time.time() + timeout_s
    code_entered = False
    captcha_seen = False
    while time.time() < deadline:
        _wait_settle(page, 2000)
        after = read_page(page)
        text = after.text + " " + after.title
        m = CONFIRM_RE.search(text)
        if m and not CONFIRM_RE.search(before_text):  # must be new, not JD boilerplate
            res.submitted = True
            res.confirmation_text = m.group(0)
            res.confirmation_excerpt = after.text[:600]
            res.screenshot = _confirmation_shot(page, run_dir, res, job_label)
            return done(rules.SUBMITTED)
        if not captcha_wait_s and (after.challenge or after.bot_block):
            # unattended: a puzzle or bot wall after Submit ends the run; the submit is never retried
            kind = _challenge_status(after.challenge) if after.challenge else rules.BLOCKED
            what = after.challenge or after.bot_block
            res.screenshot = _shot(page, run_dir, f"debug-{kind.lower()}")
            return done(kind, f"{what} appeared after clicking Submit; not retried (state unclear, do not resubmit)")
        if after.captcha or after.challenge:
            if captcha_wait_s and not captcha_seen:
                captcha_seen = True
                deadline = time.time() + captcha_wait_s
                res.errors.append(f"interactive captcha shown; waiting up to {captcha_wait_s}s for a human to solve it")
                _shot(page, run_dir, "waiting-captcha")
                log.info("CAPTCHA_WAITING")
                print("CAPTCHA_WAITING", flush=True)
            if not captcha_wait_s:
                res.errors.append("captcha challenge appeared after clicking submit; not retried")
                res.screenshot = _shot(page, run_dir, "debug-captcha")
                return done(rules.CAPTCHA)
            continue
        if VERIFY_RE.search(after.text) and not VERIFY_RE.search(before_text):
            if code_entered:
                continue  # still processing, or the code was rejected; wait until the deadline
            if not get_code:
                res.errors.append("employer sent an email verification code; application NOT complete until the code is entered")
                return done(rules.PENDING)
            lm = CODE_LEN_RE.search(after.text)
            length = int(next(g for g in lm.groups() if g)) if lm else None
            code = get_code(submit_epoch, length)
            if not code:
                res.errors.append("verification code email not found in time; application NOT complete")
                _shot(page, run_dir, "debug-code-timeout")
                return done(rules.PENDING)
            how = page.evaluate(_FILL_CODE_JS, code)
            res.errors.append(f"email verification code entered ({how})")
            page.wait_for_timeout(800)
            resubmit = next((b for b in reversed(after.buttons) if b.kind == "submit"), btn)
            locator(page, resubmit.frame_index, resubmit.apply_id).click(timeout=10000)
            code_entered = True
            deadline = time.time() + timeout_s
            continue
        se = rules.submit_rejection(after.text)
        if se and not rules.submit_rejection(before_text):
            res.screenshot = _shot(page, run_dir, "debug-blocked")
            return done(rules.BLOCKED, f"employer site rejected the submit: '{se}' (usually anti-bot scoring); not retried")
        errs = after.page_errors + [f"{f.label}: {e}" for f in after.fields for e in f.errors]
        if errs and after.is_final and not code_entered and not captcha_seen:
            res.errors.append("validation errors after submit (not retried): " + "; ".join(errs[:6]))
            return done(rules.FAILED)
    if captcha_seen:
        res.errors.append("captcha not solved / no confirmation within the wait window")
        return done(rules.CAPTCHA)
    res.errors.append("no confirmation detected after submit; state unclear, do not resubmit")
    return done(rules.FAILED)


def build_context(resume: str, role_category: str, all_info: Optional[str] = None, form_answers: Optional[str] = None,
                  salary_hint: Optional[dict] = None, jd_pay: Optional[dict] = None, job_context: str = "",
                  grad_year_on_resume: Optional[int] = None) -> Context:
    all_info_path = Path(all_info or os.environ.get("APPLY_ALL_INFO") or ROOT / "reference doc" / "all info.txt")
    fa_path = Path(form_answers or ROOT / "memory" / "FORM_ANSWERS.md")
    year = rules.effective_grad_year(grad_year_on_resume, resume)
    return Context(all_info=all_info_path.read_text(encoding="utf-8"), form_answers=fa_path.read_text(encoding="utf-8"),
                   resume_text=extract_resume_text(resume), role_category=role_category.upper(),
                   salary=rules.choose_salary(role_category, salary_hint, jd_pay), job_context=job_context,
                   resume_path=str(Path(resume).resolve()), grad_year_on_resume=year)


def new_browser(p, headed: bool = False):
    """Legacy (fresh Chromium context). Kept for old scripts; prefer browser.open_context."""
    from .browser import launch_legacy
    return launch_legacy(p, headed)


def run(url: str, resume: str, role_category: str, headed: bool = True, submit: bool = False,
        email_code: bool = False, captcha_wait_s: int = 0, persistent: bool = True,
        run_dir: Optional[Path] = None, **ctx_kwargs) -> RunResult:
    """Default browser: headed Chrome on the persistent, locked profile (browser.PROFILE_DIR)."""
    if is_lever_url(url):
        res = RunResult(url=url, reason=LEVER_SKIP_REASON)
        return res.finish(rules.FAILED)
    from playwright.sync_api import sync_playwright
    ctx = build_context(resume, role_category, **ctx_kwargs)
    planner = AnswerPlanner(ctx)
    get_code = None
    if email_code:
        from .email_codes import wait_for_code
        get_code = lambda since, length: wait_for_code(since, length)
    with sync_playwright() as p, open_context(p, headed=headed, persistent=persistent) as context:
        page = first_page(context)
        return run_application(page, url, ctx, planner, submit=submit, run_dir=run_dir,
                               get_verification_code=get_code, captcha_wait_s=captcha_wait_s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fill one job application form (P1).")
    ap.add_argument("--url", required=True)
    ap.add_argument("--resume", required=True)
    ap.add_argument("--role-category", required=True, choices=["SWE", "AI", "DATA", "swe", "ai", "data"])
    ap.add_argument("--headless", action="store_true", help="no visible window (higher bot-detection risk)")
    ap.add_argument("--headed", action="store_true", help=argparse.SUPPRESS)  # default now; kept for old commands
    ap.add_argument("--legacy-browser", action="store_true",
                    help="old behaviour: fresh Chromium context instead of the persistent Chrome profile")
    ap.add_argument("--run-dir", help="where screenshots go (default runs/<timestamp>)")
    ap.add_argument("--submit", action="store_true", help="actually click Submit (off by default in P1)")
    ap.add_argument("--all-info")
    ap.add_argument("--form-answers")
    ap.add_argument("--salary-hint", help='JSON, e.g. {"amount":21,"unit":"hour","source":"Levels.fyi"}')
    ap.add_argument("--jd-pay", help="JSON pay published in the JD, same shape as --salary-hint")
    ap.add_argument("--grad-year-on-resume", type=int, choices=[2028, 2029],
                    help="graduation year printed on the uploaded resume (default: infer from --resume path, else 2028)")
    ap.add_argument("--email-code", action="store_true", help="read email verification codes from Gmail (read-only)")
    ap.add_argument("--captcha-wait", type=int, default=0, help="seconds to wait for a human to solve a captcha after submit")
    ap.add_argument("--out", help="write result JSON here")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    headed = not a.headless if not a.legacy_browser else (a.headed and not a.headless)
    result = run(a.url, a.resume, a.role_category, headed=headed, submit=a.submit, email_code=a.email_code,
                 captcha_wait_s=a.captcha_wait, persistent=not a.legacy_browser,
                 run_dir=Path(a.run_dir) if a.run_dir else None, all_info=a.all_info,
                 form_answers=a.form_answers, salary_hint=json.loads(a.salary_hint) if a.salary_hint else None,
                 jd_pay=json.loads(a.jd_pay) if a.jd_pay else None,
                 grad_year_on_resume=a.grad_year_on_resume)
    out = json.dumps(result.__dict__, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
    print(out)
    return 0 if result.status in (rules.READY_TO_SUBMIT, rules.PENDING, rules.SUBMITTED) else 1


if __name__ == "__main__":
    sys.exit(main())
