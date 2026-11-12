"""Workday applications: Apply -> Apply Manually -> Create Account / Sign In -> email verification -> wizard -> Submit once.

Workday pages are driven by their stable data-automation-id attributes. Each wizard step is read into the same
Snapshot/Field model the generic runner uses, so the AnswerPlanner and every code-level FORM_ANSWERS rule
(AI-use pending, SSN, transcript, source, major, export control, experience entries = PwC / SDIC / CMU HCII ...)
apply unchanged. Passwords are never logged.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from . import rules
from .accounts import Credentials, generate_password, is_password_rejection, shared_credentials
from .form_reader import Field, Snapshot

log = logging.getLogger("apply.workday")

WORKDAY_URL = re.compile(r"\.myworkdayjobs\.com/|\.myworkdaysite\.com/|\.myworkday\.com/", re.I)
WD_CONFIRM = re.compile(r"application (was )?(successfully )?submitted|thank you for (applying|your application|submitting)|"
                        r"we('| ha)ve received your application|successfully applied|your application has been received|"
                        r"you have no more tasks", re.I)
EMAIL_CHOICE = ('[data-automation-id="SignInWithEmailButton"], button:has-text("Sign in with email"), '
                'button:has-text("Sign In with Email"), button:has-text("Continue with email")')
ALREADY_APPLIED = re.compile(r"already applied|you have applied|previously applied for this job", re.I)
ACCOUNT_EXISTS = re.compile(r"already (exists|in use|registered|taken|associated)|account (with|for) this email|"
                            r"email address is already", re.I)
BAD_LOGIN = re.compile(r"(wrong|invalid|incorrect).{0,30}(email|password|credentials|sign ?in)|"
                       r"(email|password).{0,30}(incorrect|invalid|not match|wrong)|could not sign you in", re.I)
NEEDS_VERIFY = re.compile(r"verif(y|ication).{0,60}(email|account)|(email|account).{0,40}not (yet )?verified|"
                          r"sent (you )?(an|a verification) email|check your (email|inbox)|activate your account", re.I)
LOCKED = re.compile(r"account (is |has been )?locked|too many (failed|unsuccessful) attempts", re.I)
MAX_STEPS = 12
MAX_RETRIES = 3

_WD_TRANSIENT = re.compile(r"something went wrong|please refresh the page|error code:\s*VPS\|", re.I)


def _recover_transient(page, tries: int = 3) -> bool:
    """Refresh when Workday shows the generic 'Something went wrong / Error Code: VPS|...' page."""
    for i in range(tries):
        if not _WD_TRANSIENT.search(_body_text(page)):
            return True
        log.warning("Workday transient error page, refreshing (%s/%s)", i + 1, tries)
        try:
            page.reload(wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            log.warning("reload failed: %s", e)
        _settle(page, 2500)
    return not _WD_TRANSIENT.search(_body_text(page))


def _wait_wizard_ready(page, timeout_ms: int = 20000) -> bool:
    """Wait until the wizard footer Next/Save and Continue button is visible (form finished loading)."""
    if _WD_TRANSIENT.search(_body_text(page)):
        return False
    loc = page.locator('button[data-automation-id="pageFooterNextButton"]').first
    try:
        loc.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return bool(page.locator('button[data-automation-id="pageFooterNextButton"]').count())


def is_workday(url: str) -> bool:
    return bool(WORKDAY_URL.search(url or ""))


def tenant_host(url: str) -> str:
    return urlparse(url).netloc.lower()


def A(aid: str) -> str:
    return f'[data-automation-id="{aid}"]'


def _visible(page, sel: str, timeout: int = 0) -> bool:
    try:
        loc = page.locator(sel).first
        if timeout:
            loc.wait_for(state="visible", timeout=timeout)
        return loc.is_visible()
    except Exception:
        return False


def _settle(page, ms: int = 900) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def _errors(page) -> list[str]:
    try:
        return page.evaluate("""() => [...document.querySelectorAll(
            '[data-automation-id="errorMessage"],[data-automation-id="errorBanner"],[data-automation-id="inputAlert"],[role="alert"]')]
            .filter(e => e.offsetParent !== null).map(e => e.innerText.trim()).filter(Boolean).slice(0, 20)""")
    except Exception:
        return []


def _body_text(page) -> str:
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''")[:20000]
    except Exception:
        return ""


def _captcha(page) -> str:
    try:
        return page.evaluate("""() => {
          const f = [...document.querySelectorAll('iframe')].map(x => x.src || '').join(' ');
          if (/hcaptcha\\.com/.test(f)) return 'hCaptcha';
          if (/recaptcha|google\\.com\\/recaptcha/.test(f)) return 'reCAPTCHA';
          if (/captcha-delivery|datadome/.test(f)) return 'DataDome';
          if (document.querySelector('[data-automation-id="captcha"],#captcha,.g-recaptcha,.h-captcha')) return 'captcha';
          return '';
        }""")
    except Exception:
        return ""


def _click_any(page, selectors: list[str], timeout: int = 6000) -> bool:
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=timeout)
                return True
        except Exception:
            try:
                loc.first.click(timeout=timeout, force=True)
                return True
            except Exception:
                continue
    return False


# ------------------------------------------------------------------ states
def page_state(page) -> str:
    """'wizard' | 'create' | 'signin' | 'apply_modal' | 'job' | 'verify_sent' | 'confirmation' | 'unknown'."""
    # account screens first: on many tenants "Create Account/Sign In" is step 1 of the wizard's progress bar
    if _visible(page, 'input[data-automation-id="verifyPassword"]'):
        return "create"
    if _visible(page, A("applyManually")):
        return "apply_modal"
    if _visible(page, 'input[data-automation-id="password"]') or _visible(page, 'input[type="password"]'):
        return "signin"
    if _visible(page, EMAIL_CHOICE):
        return "email_choice"  # "Sign in with Apple / Google / LinkedIn / OR Sign in with email" (KLA)
    if _visible(page, A("progressBar")) or _visible(page, A("pageFooterNextButton")):
        return "wizard"
    text = _body_text(page)
    if WD_CONFIRM.search(text) and not _visible(page, A("adventureButton")):
        return "confirmation"
    if NEEDS_VERIFY.search(text):
        return "verify_sent"
    if _visible(page, A("adventureButton")) or _visible(page, A("applyButton")):
        return "job"
    return "unknown"


def _fill_input(page, sel: str, value: str) -> bool:
    loc = page.locator(sel).first
    try:
        loc.click(timeout=4000)
        loc.fill("")
        loc.fill(value)
        loc.press("Tab")
        return (loc.input_value() or "") == value
    except Exception:
        return False


def _submit_auth(page, label: str, aid: str) -> None:
    # Workday often lays a transparent click_filter div over the real button
    if not _click_any(page, [f'{A("click_filter")}[aria-label="{label}"]', f'button{A(aid)}', A(aid),
                             f'button:has-text("{label}")']):
        page.keyboard.press("Enter")


class AuthOutcome:
    def __init__(self, ok: bool, status: str = "", reason: str = "", created: bool = False, source: str = ""):
        self.ok, self.status, self.reason, self.created, self.source = ok, status, reason, created, source


def authenticate(page, creds: Credentials, on_created: Optional[Callable[[Credentials], None]] = None,
                 get_link: Optional[Callable[[float, str], Optional[str]]] = None, max_rounds: int = 10,
                 prefer_signin: bool = False) -> AuthOutcome:
    """From the Workday sign-in / create-account screen to the application wizard.

    Create Account first (Ryan's shared password; site-suggested or generated strong password if the site rejects it
    for complexity). If the account already exists, sign in. on_created(creds) is called right after a new account
    is accepted (so 公司账号.xlsx gets the password actually used). Email verification links come from get_link.
    """
    tried_create = tried_generated = False
    created = False
    verify_since: Optional[float] = None
    host = tenant_host(page.url)
    signin_failures = 0
    for _ in range(max_rounds):
        _settle(page, 700)
        if (c := _captcha(page)):
            return AuthOutcome(False, rules.BLOCKED if c == "DataDome" else rules.CAPTCHA, f"{c} on the Workday sign-in page", created)
        st = page_state(page)
        if st == "wizard":
            return AuthOutcome(True, created=created, source=creds.source)
        if st == "email_choice":
            _click_any(page, [EMAIL_CHOICE])
            _settle(page, 1200)
            continue
        text = _body_text(page)
        if LOCKED.search(text):
            return AuthOutcome(False, rules.LOGIN_REQUIRED, "Workday account locked / too many attempts", created)
        if st == "verify_sent" or (verify_since and NEEDS_VERIFY.search(text) and st != "create"):
            if not get_link:
                return AuthOutcome(False, rules.LOGIN_REQUIRED, "account created; email verification needed (no mail reader)", created)
            link = get_link(verify_since or time.time() - 120, host)
            if not link:
                return AuthOutcome(False, rules.LOGIN_REQUIRED, "account created; verification email not found in Gmail", created)
            page.goto(link, wait_until="domcontentloaded", timeout=45000)
            verify_since = None
            _settle(page, 1500)
            if page_state(page) not in ("signin", "create", "wizard"):
                _click_any(page, [A("signInLink"), A("utilityButtonSignIn"), 'button:has-text("Sign In")', 'a:has-text("Sign In")'])
            continue
        if st == "create":
            if prefer_signin:
                log.info("prefer_signin: switching from Create Account to Sign In")
                if not _click_any(page, [A("signInLink"), 'button:has-text("Sign In")', 'a:has-text("Sign In")',
                                         'button:has-text("Sign in")', 'a:has-text("Sign in")']):
                    return AuthOutcome(False, rules.LOGIN_REQUIRED, "prefer_signin but no Sign In link on create page", created)
                continue
            if tried_create and not tried_generated and creds.source == "shared" and is_password_rejection(" ".join(_errors(page)) + " " + text):
                pass  # handled below after submit
            _fill_input(page, f'input{A("email")}', creds.email)
            _fill_input(page, f'input{A("password")}', creds.password)
            _fill_input(page, f'input{A("verifyPassword")}', creds.password)
            cb = page.locator(f'input{A("createAccountCheckbox")}')
            if cb.count() and not cb.first.is_checked():
                try:
                    cb.first.check(timeout=3000, force=True)
                except Exception:
                    pass
            tried_create = True
            t_click = time.time()
            _submit_auth(page, "Create Account", "createAccountSubmitButton")
            _settle(page, 1500)
            errs = " ".join(_errors(page))
            after = page_state(page)
            if ACCOUNT_EXISTS.search(errs + " " + (_body_text(page) if after == "create" else "")):
                log.info("Workday account already exists -> sign in")
                _click_any(page, [A("signInLink"), 'button:has-text("Sign In")', 'a:has-text("Sign In")'])
                continue
            if after == "create" and is_password_rejection(errs):
                if tried_generated:
                    return AuthOutcome(False, rules.LOGIN_REQUIRED, "site rejected the generated password too", created)
                suggested = _suggested_password(page)
                rules_text = errs + " " + _password_rules_text(page)
                creds = Credentials(creds.email, suggested or generate_password(rules_text),
                                    "site_suggested" if suggested else "generated")
                tried_generated = True
                log.info("shared password rejected for complexity; using a %s password", creds.source)
                continue
            if after == "create":
                return AuthOutcome(False, rules.FAILED, f"Create Account did not go through: {errs[:200] or 'no error shown'}", created)
            if after not in ("wizard", "verify_sent", "signin", "email_choice"):
                log.info("Create Account left an unexpected page state %s; not treating as a new account", after)
                continue
            created = True
            if on_created:
                on_created(creds)
            verify_since = t_click
            continue
        if st == "signin":
            if (not prefer_signin) and not tried_create and (_visible(page, A("createAccountLink")) or _visible(page, 'button:has-text("Create Account"), a:has-text("Create Account")')):
                _click_any(page, [A("createAccountLink"), 'button:has-text("Create Account")', 'a:has-text("Create Account")'])
                continue
            _fill_input(page, f'input{A("email")}', creds.email)
            _fill_input(page, f'input{A("password")}', creds.password)
            t_click = time.time()
            _submit_auth(page, "Sign In", "signInSubmitButton")
            _settle(page, 1500)
            if page_state(page) == "wizard":
                return AuthOutcome(True, created=created, source=creds.source)
            errs = " ".join(_errors(page)) + " " + _body_text(page)[:3000]
            if NEEDS_VERIFY.search(errs) and get_link:
                _click_any(page, [A("resendAccountVerificationLink"), 'button:has-text("Resend")', 'a:has-text("Resend")'])
                verify_since = min(t_click, verify_since or t_click) - 5
                continue
            if BAD_LOGIN.search(errs):
                signin_failures += 1
                if signin_failures >= 2 or not created:
                    return AuthOutcome(False, rules.LOGIN_REQUIRED,
                                       "existing Workday account: sign-in with the recorded password failed", created)
            continue
        if st == "apply_modal":
            _click_any(page, [A("applyManually")])
            continue
        if st == "job":
            _click_any(page, [A("adventureButton"), A("applyButton")])
            continue
        if st == "confirmation":
            return AuthOutcome(True, created=created, source=creds.source)
        _click_any(page, [A("utilityButtonSignIn"), A("signInLink")])
    return AuthOutcome(False, rules.LOGIN_REQUIRED, "could not reach the Workday application after sign-in", created)


def _suggested_password(page) -> str:
    """A site-offered strong password (some tenants show one in a 'suggested password' element)."""
    try:
        return page.evaluate("""() => {
          const e = document.querySelector('[data-automation-id="suggestedPassword"],[data-automation-id="passwordSuggestion"]');
          return e ? (e.value || e.innerText || '').trim() : '';
        }""") or ""
    except Exception:
        return ""


def _password_rules_text(page) -> str:
    try:
        return page.evaluate("""() => [...document.querySelectorAll('[data-automation-id="passwordRequirements"],[id*="password" i] li,ul li')]
          .map(e => e.innerText).filter(t => /character|upper|lower|number|special|digit/i.test(t)).join('. ').slice(0, 1500)""")
    except Exception:
        return ""


# ------------------------------------------------------------------ wizard reading
_READ_JS = r"""
() => {
  const vis = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  let n = 0;
  const all = [...document.querySelectorAll('[data-automation-id^="formField-"]')].filter(vis)
    .filter(c => !c.parentElement.closest('[data-automation-id^="formField-"]'));
  for (const c of all) {
    const key = 'wd' + (n++);
    c.setAttribute('data-wd-key', key);
    const lab = c.querySelector('label, legend, [data-automation-id="richText"]');
    let label = clean(lab ? lab.innerText : (c.getAttribute('aria-label') || ''));
    const required = /\*\s*$/.test(label) || !!c.querySelector('[aria-required="true"],[required]');
    label = label.replace(/\s*\*+\s*$/, '');
    const aid = c.getAttribute('data-automation-id').slice('formField-'.length);
    let type = 'text', value = '', options = [], extra = {}, input_type = '', placeholder = '';
    const file = c.querySelector('input[type=file]');
    const lb = c.querySelector('button[aria-haspopup="listbox"]');
    const prompt = c.querySelector('[data-automation-id="multiselectInputContainer"], [data-uxi-widget-type="selectinput"], input[data-automation-id="searchBox"]');
    const radios = [...c.querySelectorAll('input[type=radio]')];
    const checks = [...c.querySelectorAll('input[type=checkbox]')];
    const dates = c.querySelector('[data-automation-id="dateSectionMonth-input"], [data-automation-id="dateSectionYear-input"]');
    const ta = c.querySelector('textarea');
    const inp = c.querySelector('input:not([type=hidden]):not([type=radio]):not([type=checkbox]):not([type=file])');
    const labelFor = el => { const l = el.id && c.querySelector('label[for="' + el.id + '"]'); return clean(l ? l.innerText : (el.getAttribute('aria-label') || el.value)); };
    if (file) { type = 'file'; value = clean([...c.querySelectorAll('[data-automation-id="file-upload-item-name"],[data-automation-id="fileName"]')].map(e => e.innerText).join(', ')); }
    else if (dates) {
      type = 'date';
      const m = c.querySelector('[data-automation-id="dateSectionMonth-input"]'), d = c.querySelector('[data-automation-id="dateSectionDay-input"]'), y = c.querySelector('[data-automation-id="dateSectionYear-input"]');
      extra.wd_date = (m ? 'MM/' : '') + (d ? 'DD/' : '') + 'YYYY';
      placeholder = extra.wd_date;
      value = [m, d, y].filter(Boolean).map(e => e.value || e.getAttribute('aria-valuetext') || '').filter(v => v && !/^(MM|DD|YYYY)$/.test(v)).join('/');
    }
    else if (lb) { type = 'select'; value = clean(lb.innerText); if (/^select one$/i.test(value)) value = ''; }
    else if (prompt) { type = 'combobox'; extra.multi = true; extra.wd_prompt = true;
      value = clean([...c.querySelectorAll('[data-automation-id="selectedItem"],[data-automation-id="selectedItemList"] li')].map(e => e.innerText).join(', ')); }
    else if (radios.length) { type = 'radio'; options = radios.map(labelFor); const ch = radios.find(r => r.checked); value = ch ? labelFor(ch) : ''; }
    else if (checks.length > 1) { type = 'checkbox_group'; options = checks.map(labelFor); value = checks.filter(x => x.checked).map(labelFor).join(', '); }
    else if (checks.length === 1) { type = 'checkbox'; value = checks[0].checked ? 'checked' : '';
      if (!label || label.length < 3) label = labelFor(checks[0]); }
    else if (ta) { type = 'textarea'; value = ta.value; extra.maxlength = ta.maxLength > 0 ? ta.maxLength : undefined; }
    else if (inp) { type = 'text'; value = inp.value; input_type = inp.type || ''; placeholder = inp.placeholder || ''; }
    else continue;
    // repeatable entry: nearest panel heading like "Work Experience 2" / "Education 1"
    let section = '', p = c.parentElement;
    while (p && p !== document.body) {
      const h = p.querySelector(':scope > h3, :scope > h4, :scope > div > h3, :scope > div > h4, :scope > [role=heading]');
      if (h && /\d\s*$/.test(clean(h.innerText))) { section = clean(h.innerText); break; }
      p = p.parentElement;
    }
    const errs = [...c.querySelectorAll('[data-automation-id="errorMessage"],[data-automation-id="inputAlert"]')].map(e => clean(e.innerText)).filter(Boolean);
    out.push({key, aid, type, label: label.slice(0, 600), required, value: clean(value).slice(0, 400), options, extra, input_type, placeholder, section, errors: errs});
  }
  const step = document.querySelector('[data-automation-id="progressBarActiveStep"]');
  const h = document.querySelector('h2, [data-automation-id="pageHeader"]');
  return {fields: out, step: clean(step ? step.innerText : ''), heading: clean(h ? h.innerText : ''),
          title: document.title, text: (document.body.innerText || '').slice(0, 6000)};
}
"""


def read_step(page) -> tuple[Snapshot, str]:
    data = page.evaluate(_READ_JS)
    fields: list[Field] = []
    for d in data["fields"]:
        f = Field(d["key"], 0, d["key"], d["type"], d["label"] or d["aid"], bool(d["required"]), d["value"] or "",
                  d["options"] or [], d["errors"] or [], name=d["aid"], input_type=d["input_type"] or "",
                  placeholder=d["placeholder"] or "", extra={k: v for k, v in (d["extra"] or {}).items() if v is not None},
                  section=d["section"] or "")
        m = re.match(r"(.*?)\s*(\d{1,2})\s*$", d["section"] or "")
        if m:
            kind = rules.section_kind(m.group(1))
            if kind:
                f.entry_kind, f.entry_index = kind, int(m.group(2)) - 1
        fields.append(f)
    step = data["step"] or data["heading"]
    heading = step
    if re.search(r"self.?identif|disclosure", step, re.I):
        heading += f" (today's date is {date.today():%m/%d/%Y})"
    snap = Snapshot(url=page.url, title=data["title"], heading=heading, doc="", fields=fields, buttons=[],
                    page_errors=_errors(page), captcha=bool(_captcha(page)), text=data["text"])
    return snap, step


def _open_listbox(page, key: str):
    page.locator(f'[data-wd-key="{key}"] button[aria-haspopup="listbox"]').first.click(timeout=5000)
    page.wait_for_timeout(400)
    return page.locator('[role="listbox"]:visible [role="option"]')


def _open_prompt(page, key: str):
    inp = page.locator(f'[data-wd-key="{key}"] input').first
    inp.click(timeout=5000)
    page.wait_for_timeout(500)
    return inp, page.locator('[role="listbox"]:visible [role="option"], [data-automation-id="promptOption"]:visible')


def harvest_options(page, snap: Snapshot) -> None:
    """Workday dropdown / prompt options only exist after opening the widget: open, read, close."""
    for f in snap.fields:
        if f.options or f.type not in ("select", "combobox"):
            continue
        try:
            opts = _open_listbox(page, f.key) if f.type == "select" else _open_prompt(page, f.key)[1]
            texts = [t.strip() for t in opts.all_inner_texts() if t.strip()][:120]
            f.options = [t for t in texts if not re.fullmatch(r"select one", t, re.I)]
        except Exception as e:
            log.debug("could not open %s: %s", f.label[:40], e)
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)


# ------------------------------------------------------------------ filling
class FillResultWD:
    def __init__(self, f: Field, action: str, value, ok: bool, actual: str = "", note: str = ""):
        self.key, self.label, self.type, self.action, self.value, self.ok, self.actual, self.note = \
            f.key, f.label, f.type, action, value, ok, actual, note

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _best(options: list[str], want: str) -> Optional[int]:
    w = _norm(want)
    if not w:
        return None
    ns = [_norm(o) for o in options]
    for i, n in enumerate(ns):
        if n == w:
            return i
    for i, n in enumerate(ns):
        if n.startswith(w) or w.startswith(n) and len(n) > 2:
            return i
    for i, n in enumerate(ns):
        if w in n:
            return i
    return None


def _date_parts(value: str, fmt: str) -> Optional[list[str]]:
    v = (value or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v) or re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        a, b, c = m.groups()
        mm, dd, yy = (b, c, a) if len(a) == 4 else (a, b, c)
    else:
        m2 = re.fullmatch(r"(\d{1,2})/(\d{4})", v) or re.fullmatch(r"(\d{4})-(\d{1,2})", v)
        if m2:
            a, b = m2.groups()
            mm, yy = (b, a) if len(a) == 4 else (a, b)
            dd = "01"
        else:
            p = rules.picker_date(v)
            m3 = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", p or "")
            if m3:
                mm, dd, yy = m3.groups()
            elif re.fullmatch(r"\d{4}", v):
                mm, dd, yy = "01", "01", v
            else:
                return None
    parts = []
    if "MM" in fmt:
        parts.append(f"{int(mm):02d}")
    if "DD" in fmt:
        parts.append(f"{int(dd):02d}")
    parts.append(yy)
    return parts


class WorkdayFiller:
    def __init__(self, page, resume_path: str):
        self.page, self.resume = page, resume_path

    def c(self, f: Field):
        return self.page.locator(f'[data-wd-key="{f.key}"]').first

    def apply(self, snap: Snapshot, actions) -> list[FillResultWD]:
        out = []
        for a in actions:
            f = snap.field(a.key)
            if not f or a.action in ("skip", "pending"):
                continue
            try:
                out.append(self._one(f, a))
            except Exception as e:
                out.append(FillResultWD(f, a.action, a.value, False, note=f"error: {str(e)[:160]}"))
            self.page.wait_for_timeout(150)
        return out

    def _one(self, f: Field, a) -> FillResultWD:
        c, page, v = self.c(f), self.page, a.value
        if a.action == "upload_resume" or (f.type == "file" and a.action in ("fill", "select")):
            if f.type != "file" or not self.resume:
                return FillResultWD(f, a.action, v, False, note="no file input / resume")
            c.locator("input[type=file]").first.set_input_files(self.resume)
            page.wait_for_timeout(2500)
            return FillResultWD(f, a.action, Path(self.resume).name, True, Path(self.resume).name)
        if f.type == "checkbox":
            box = c.locator("input[type=checkbox]").first
            want = a.action != "uncheck" and not (a.action in ("fill", "select") and str(v).lower() in ("false", "no", "unchecked"))
            if box.is_checked() != want:
                try:
                    box.click(timeout=3000)
                except Exception:
                    box.click(timeout=3000, force=True)
            return FillResultWD(f, a.action, want, box.is_checked() == want, str(box.is_checked()))
        if f.type in ("radio", "checkbox_group"):
            wants = v if isinstance(v, list) else [v]
            boxes = c.locator("input[type=radio], input[type=checkbox]")
            ok = True
            for w in wants:
                i = _best(f.options, str(w))
                if i is None:
                    ok = False
                    continue
                b = boxes.nth(i)
                if not b.is_checked():
                    try:
                        b.click(timeout=3000)
                    except Exception:
                        b.click(timeout=3000, force=True)
                ok = ok and b.is_checked()
            return FillResultWD(f, a.action, v, ok, ", ".join(str(w) for w in wants))
        if f.type == "select":
            opts = _open_listbox(page, f.key)
            texts = [t.strip() for t in opts.all_inner_texts()]
            i = _best(texts, str(v))
            if i is None:
                page.keyboard.press("Escape")
                return FillResultWD(f, a.action, v, False, note=f"no option matching {str(v)[:60]!r}; options: {texts[:12]}")
            opts.nth(i).click(timeout=4000)
            page.wait_for_timeout(400)
            shown = (c.locator('button[aria-haspopup="listbox"]').first.inner_text() or "").strip()
            return FillResultWD(f, a.action, v, _norm(shown) == _norm(texts[i]), shown)
        if f.type == "combobox":
            return self._prompt(f, a)
        if f.type == "date":
            parts = _date_parts(str(v), f.extra.get("wd_date", "MM/YYYY"))
            if not parts:
                return FillResultWD(f, a.action, v, False, note="unparseable date")
            first = c.locator('[data-automation-id="dateSectionMonth-input"], [data-automation-id="dateSectionDay-input"], '
                              '[data-automation-id="dateSectionYear-input"]').first
            try:
                first.click(timeout=2500)
            except Exception:
                # live tenants overlay the inputs with a dateSection*-display div that intercepts the click
                try:
                    c.locator('[data-automation-id$="-display"]').first.click(timeout=2500)
                except Exception:
                    first.focus()
            page.keyboard.type("".join(parts), delay=60)
            page.keyboard.press("Tab")
            page.wait_for_timeout(300)
            got = c.evaluate("""c => [...c.querySelectorAll('[data-automation-id^="dateSection"][data-automation-id$="-input"]')]
                                 .map(e => e.value || e.getAttribute('aria-valuetext') || '')""")
            ok = [g.lstrip("0") for g in got] == [p.lstrip("0") for p in parts]
            return FillResultWD(f, a.action, "/".join(parts), ok, "/".join(got))
        # text / textarea
        box = c.locator("textarea, input:not([type=hidden]):not([type=radio]):not([type=checkbox]):not([type=file])").first
        box.click(timeout=4000)
        box.fill("")
        box.fill(str(v))
        box.press("Tab")
        got = box.input_value()
        return FillResultWD(f, a.action, v, got.strip() == str(v).strip(), got[:200])

    def _prompt(self, f: Field, a) -> FillResultWD:
        """Workday multiselect prompt: type, pick the matching option (drilling into a category if needed), verify the chip."""
        page, c = self.page, self.c(f)
        wants = a.value if isinstance(a.value, list) else [a.value]
        picked = []
        for w in wants:
            w = str(w)
            have = self._chips(c)
            if _best(have, w) is not None:  # already selected (retry / prefilled): never add a duplicate
                picked.append(have[_best(have, w)])
                continue
            inp, opts = _open_prompt(page, f.key)
            texts = [t.strip() for t in opts.all_inner_texts()]
            i = _best(texts, w)
            if i is None:
                inp.fill(w)
                inp.press("Enter")
                page.wait_for_timeout(1200)
                opts = page.locator('[role="listbox"]:visible [role="option"], [data-automation-id="promptOption"]:visible')
                texts = [t.strip() for t in opts.all_inner_texts()]
                i = _best(texts, w)
                if i is None and len(texts) == 1 and not re.search(r"no items|no results", texts[0], re.I):
                    i = 0
            if i is None:
                page.keyboard.press("Escape")
                continue
            before = self._chips(c)
            opts.nth(i).click(timeout=4000)
            page.wait_for_timeout(700)
            if self._chips(c) == before:  # a category: choose the leaf
                sub = page.locator('[role="listbox"]:visible [role="option"], [data-automation-id="promptOption"]:visible')
                st = [t.strip() for t in sub.all_inner_texts()]
                j = _best(st, w)
                if j is None:
                    j = next((k for k, t in enumerate(st) if re.fullmatch(r"other\b.*", t, re.I)), None)
                if j is None and st:
                    j = next((k for k, t in enumerate(st) if not re.search(r"^back$|^\W*$", t, re.I)), None)
                if j is not None:
                    sub.nth(j).click(timeout=4000)
                    page.wait_for_timeout(600)
            page.keyboard.press("Escape")
            chips = self._chips(c)
            if chips != before:
                picked.append(chips[-1] if chips else w)
        chips = self._chips(c)
        ok = bool(chips) and len(picked) == len(wants)
        return FillResultWD(f, a.action, a.value, ok, ", ".join(chips),
                            "" if ok else f"prompt selection not confirmed (chips: {chips})")

    @staticmethod
    def _chips(c) -> list[str]:
        try:
            return [t.strip() for t in c.locator('[data-automation-id="selectedItem"]').all_inner_texts() if t.strip()]
        except Exception:
            return []


# ------------------------------------------------------------------ My Experience: entries
_SECTION_JS = """kind => {
  const isEdu = t => /academic|education|school|degree/i.test(t);
  const isWork = t => !isEdu(t) && /work|employment|experience|relevant/i.test(t) && !/language|skill|website|social/i.test(t);
  const want = kind === 'edu' ? isEdu : isWork;
  const cands = [];
  document.querySelectorAll('[aria-labelledby$="-section"]').forEach(g => {
    if (g.offsetParent === null) return;  // hidden leftovers of other steps (e.g. previousWorker-section)
    const id = g.getAttribute('aria-labelledby'); const h = document.getElementById(id);
    if (!h && !/[A-Z][a-z]+-[A-Z]/.test(id)) return;  // camelCase field ids are questions, not entry sections
    cands.push({sel: `[aria-labelledby="${id}"]`, title: (h ? h.innerText : id.replace(/-section$/, '').replace(/-/g, ' ')).trim()});
  });
  for (const [aid, t] of [['workExperienceSection', 'Work Experience'], ['educationSection', 'Education']])
    if (document.querySelector(`[data-automation-id="${aid}"]`)) cands.push({sel: `[data-automation-id="${aid}"]`, title: t});
  return cands.find(c => want(c.title)) || null;
}"""


def _entry_section(page, kind: str) -> Optional[dict]:
    try:
        return page.evaluate(_SECTION_JS, kind)
    except Exception:
        return None


def _entry_count(page, sec: Optional[dict], title: str = "") -> int:
    """Panels inside the section whose heading ends in a number ("Work Experience 2", "Academic Experience 1")."""
    return page.evaluate("""([sel, title]) => {
        const root = sel ? document.querySelector(sel) : document;
        if (!root) return 0;
        return [...root.querySelectorAll('h3, h4, h5, [role=heading]')].filter(h => h.offsetParent !== null
            && /\\S.*\\s\\d{1,2}\\s*$/.test(h.innerText) && (sel || new RegExp('^\\\\s*' + title, 'i').test(h.innerText))).length;
    }""", [sec["sel"] if sec else None, title])


def setup_entries(page, resume_path: str, n_work: int = len(rules.EXPERIENCE_ENTRIES), n_edu: int = 1) -> list[str]:
    """Upload the resume, drop whatever Workday parsed from it (it would list Lucina etc.), then open exactly
    n_work work-experience and n_edu education panels. The planner fills them from EXPERIENCE_ENTRIES / ALL_INFO.
    Tenants rename the sections ("Work History/Relevant Experience", "Academic Experience"), so sections are found
    by their aria-labelledby heading and the generic data-automation-id="add-button"."""
    notes = []
    up = page.locator('input[type=file]')
    if resume_path and up.count():
        try:
            up.first.set_input_files(resume_path)
            notes.append("resume uploaded")
            page.wait_for_timeout(4000)
        except Exception as e:
            notes.append(f"resume upload failed: {e}"[:120])
    for kind, want in (("work", n_work), ("edu", n_edu)):
        sec = None
        for _ in range(10):  # the step renders after the progress bar updates
            sec = _entry_section(page, kind)
            if sec:
                break
            page.wait_for_timeout(800)
        if not sec:
            notes.append(f"no {kind} section")
            continue
        title = sec["title"]
        root = page.locator(sec["sel"]).first
        for _ in range(12):  # delete parsed / leftover entries
            btn = root.locator('button[aria-label^="Delete"]:not([aria-label*=".pdf"]):not([aria-label*=".doc"]), '
                               'button[data-automation-id="panel-set-delete-button"]')
            if not btn.count():
                break
            try:
                btn.first.click(timeout=4000)
            except Exception:
                break
            page.wait_for_timeout(600)
        for _ in range(want):
            if _entry_count(page, sec, title) >= want:
                break
            add = root.locator('button[data-automation-id="add-button"], button[aria-label^="Add"], button:has-text("Add Another"), button:has-text("Add")')
            if not add.count():
                notes.append(f"no Add button for {title}")
                break
            try:
                add.last.click(timeout=4000)
            except Exception as e:
                notes.append(f"Add failed for {title}: {e}"[:120])
                break
            page.wait_for_timeout(900)
        notes.append(f"{title}: {_entry_count(page, sec, title)} entries")
    return notes


def _maybe_submitted(page, res, run_dir, ctx, done):
    """If the confirmation modal or Candidate Home 'Application Submitted' page is showing, record it."""
    body = _body_text(page)
    m = WD_CONFIRM.search(body)
    if not m and not (
        re.search(r"/jobTasks/completed", page.url or "", re.I)
        and re.search(r"under review|date submitted", body, re.I)
    ):
        return None
    text = m.group(0) if m else "Application Submitted"
    res.submitted = True
    res.submit_clicked = True
    res.final_page_reached = True
    res.confirmation_text = text
    res.confirmation_excerpt = re.sub(r"\s+", " ", body[max(0, (m.start() if m else 0) - 200): (m.end() if m else 200) + 300])[:600]
    from .runner import _confirmation_shot
    res.screenshot = _confirmation_shot(page, run_dir, res, (ctx.job_context or "").split("\n", 1)[0][:200])
    return done(rules.SUBMITTED)


# ------------------------------------------------------------------ main loop
def run_workday(page, url: str, ctx, planner, res, run_dir: Path, done, submit: bool, confirm_submit=None,
                creds: Optional[Credentials] = None, on_account_created=None, get_verification_link=None,
                pending: Optional[dict] = None, prefer_signin: bool = False):
    """Called by runner.run_application for Workday URLs; uses the runner's RunResult and done()."""
    from .runner import _confirmation_shot, _shot
    creds = creds or shared_credentials()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        return done(rules.FAILED, f"navigation failed: {e}")
    _settle(page, 2000)
    text = _body_text(page)
    if rules.is_closed_posting(text) and not _visible(page, A("adventureButton")):
        return done(rules.FAILED, "posting closed / expired")
    if not ctx.job_context:
        ctx.job_context = text[:5000]
    # Apply -> Apply Manually
    if ALREADY_APPLIED.search(text):
        return done(rules.FAILED, "Workday says this job was already applied to; not resubmitting")
    # signed in with a saved draft: the button reads "Continue Application"
    if not _click_any(page, [A("adventureButton"), A("applyButton"), A("continueButton"), 'a:has-text("Continue Application")',
                             'button:has-text("Continue Application")', 'a:has-text("Apply")', 'button:has-text("Apply")']):
        return done(rules.FAILED, "Workday: Apply button not found on the job page")
    res.pages.append({"page": 0, "url": page.url, "entry_click": "Apply"})
    _settle(page, 1500)
    if _visible(page, A("applyManually"), 4000):
        _click_any(page, [A("applyManually")])
        res.pages.append({"page": 0, "url": page.url, "entry_click": "Apply Manually"})
        _settle(page, 1500)
    if ALREADY_APPLIED.search(_body_text(page)):
        return done(rules.FAILED, "Workday says this job was already applied to; not resubmitting")
    if page_state(page) != "wizard":
        if not creds:
            return done(rules.LOGIN_REQUIRED, "Workday account needed but WORKDAY_PASSWORD is not set")
        out = authenticate(page, creds, on_account_created, get_verification_link, prefer_signin=prefer_signin)
        res.pages.append({"page": 0, "url": page.url, "auth": "created" if out.created else "signed_in",
                          "ok": out.ok, "password_source": out.source})
        if not out.ok:
            return done(out.status or rules.LOGIN_REQUIRED, f"Workday sign-in: {out.reason}")
        _settle(page, 1500)
        if page_state(page) != "wizard":
            if _visible(page, A("applyManually"), 3000) or _click_any(page, [A("adventureButton")]):
                _click_any(page, [A("applyManually")])
                _settle(page, 1500)
        if ALREADY_APPLIED.search(_body_text(page)):
            return done(rules.FAILED, "Workday says this job was already applied to; not resubmitting")
    filler = WorkdayFiller(page, ctx.resume_path)
    pending = pending if pending is not None else {}  # shared with runner.done() so pending_items are reported
    entries_done = False
    for step_no in range(1, MAX_STEPS + 1):
        if (cp := _captcha(page)):
            return done(rules.BLOCKED if cp == "DataDome" else rules.CAPTCHA, f"{cp} in the Workday wizard")
        if not _recover_transient(page):
            return done(rules.FAILED, "Workday showed a transient error page (VPS) that did not clear after refresh")
        if (out := _maybe_submitted(page, res, run_dir, ctx, done)):
            return out
        if page_state(page) == "wizard":
            if not _wait_wizard_ready(page):
                if (out := _maybe_submitted(page, res, run_dir, ctx, done)):
                    return out
                if _WD_TRANSIENT.search(_body_text(page)) and _recover_transient(page):
                    _wait_wizard_ready(page)
                elif not _wait_wizard_ready(page, 15000):
                    log.warning("wizard footer Next not visible after wait; continuing to read the step")
        if page_state(page) in ("create", "signin", "email_choice"):
            if not creds:
                return done(rules.LOGIN_REQUIRED, "Workday account needed but WORKDAY_PASSWORD is not set")
            out = authenticate(page, creds, on_account_created, get_verification_link, prefer_signin=prefer_signin)
            res.pages.append({"page": 0, "url": page.url, "auth": "created" if out.created else "signed_in",
                              "ok": out.ok, "password_source": out.source})
            if not out.ok:
                return done(out.status or rules.LOGIN_REQUIRED, f"Workday sign-in: {out.reason}")
            _settle(page, 1500)
            continue
        snap, step = read_step(page)
        info = {"page": step_no, "url": page.url, "heading": step, "attempts": 0, "advanced": False}
        res.pages.append(info)
        nxt = page.locator(f'button{A("pageFooterNextButton")}').first
        nxt_text = (nxt.inner_text() if nxt.count() else "").strip()
        is_review = bool(re.search(r"^review", step, re.I)) or re.fullmatch(r"submit", nxt_text or "", re.I) is not None
        if re.search(r"assessment", step, re.I) and _visible(page, 'button:has-text("Take Assessment"), a:has-text("Take Assessment")'):
            # third-party questionnaire (e.g. WOTC tax-credit screening, often asks SSN / date of birth) in a popup:
            # never filled automatically; the draft is saved so Ryan can finish it.
            body = _body_text(page)
            what = "WOTC tax-credit questionnaire" if re.search(r"WOTC|work opportunity tax", body, re.I) else "assessment"
            pending[f"{step}:assessment"] = {"field": f"Take Assessment ({what})", "page": step_no, "step": step, "required": True,
                                             "reason": "third-party assessment in a popup (may ask SSN / date of birth); Ryan completes it, then Review → Submit"}
            _shot(page, run_dir, f"pending-step{step_no}")
            return done(rules.PENDING, f"Workday step '{step}' needs a third-party {what}; draft saved, not submitted")
        if is_review:
            info["final"] = True
            res.final_page_reached = True
            if pending:
                res.errors.append("Workday review reached but pending items remain; not submitting")
                return done(rules.PENDING)
            if not submit:
                return done(rules.READY_TO_SUBMIT)
            if confirm_submit is not None:
                res.pending_items = list(pending.values())
                if not confirm_submit(res, page):
                    return done(rules.PENDING, "submit declined at final review gate")
            res.submit_clicked = True
            res.submit_clicked_at = time.strftime("%Y-%m-%d %H:%M:%S")
            nxt.click(timeout=10000)
            _settle(page, 3000)
            for _ in range(10):
                body = _body_text(page)
                if WD_CONFIRM.search(body):
                    break
                if (cp := _captcha(page)):
                    return done(rules.CAPTCHA, f"{cp} after clicking Submit; not retried")
                page.wait_for_timeout(1500)
            body = _body_text(page)
            m = WD_CONFIRM.search(body)
            if not m:
                res.errors.append(f"no confirmation after Submit: {(_errors(page) or [body[:200]])[0][:200]}")
                return done(rules.FAILED)
            res.submitted = True
            res.confirmation_text = m.group(0)
            res.confirmation_excerpt = re.sub(r"\s+", " ", body[max(0, m.start() - 200): m.end() + 300])[:600]
            res.screenshot = _confirmation_shot(page, run_dir, res, (ctx.job_context or "").split("\n", 1)[0][:200])
            return done(rules.SUBMITTED)
        if not entries_done and re.search(r"experience", step, re.I):
            res.pages[-1]["entries"] = setup_entries(page, ctx.resume_path)
            entries_done = True
            snap, step = read_step(page)
        history: list[str] = []
        advanced = False
        for attempt in range(MAX_RETRIES + 1):
            info["attempts"] = attempt + 1
            harvest_options(page, snap)
            actions = planner.plan(snap, attempt, history)
            for a in actions:
                f = snap.field(a.key)
                pk = f"{step}:{f.label if f else a.key}:{f.entry_index if f else ''}"
                if a.action == "pending":
                    pending[pk] = {"field": f.label if f else a.key, "reason": a.reason, "page": step_no,
                                   "step": step, "required": bool(f and f.required)}
                elif a.action != "skip":
                    pending.pop(pk, None)
            results = filler.apply(snap, actions)
            for r in results:
                res.filled.append({"page": step_no, "step": step, "attempt": attempt + 1, **r.as_dict()})
            bad = [r for r in results if not r.ok]
            history.append(f"attempt {attempt + 1}: {len(results)} filled, {len(bad)} mismatches: "
                           + "; ".join(f"{r.label[:60]} -> {r.note or r.actual}" for r in bad)[:800])
            if any(p["required"] and p["step"] == step for p in pending.values()):
                res.errors.append(f"required item(s) left for Ryan on Workday step '{step}'")
                _shot(page, run_dir, f"pending-step{step_no}")
                return done(rules.PENDING)
            before_step = step
            nxt = page.locator('button[data-automation-id="pageFooterNextButton"]').first
            if not _wait_wizard_ready(page, 20000):
                if _WD_TRANSIENT.search(_body_text(page)):
                    if not _recover_transient(page):
                        return done(rules.FAILED, f"Workday VPS error on '{before_step}' and refresh did not recover")
                    snap, step = read_step(page)
                    history.append(f"recovered from VPS error on '{before_step}', now '{step}'")
                    continue
                res.errors.append(f"Next/Save and Continue not visible on '{before_step}'")
                return done(rules.FAILED, f"Next button missing on Workday step '{before_step}'")
            try:
                nxt.click(timeout=10000)
            except Exception as e:
                if _WD_TRANSIENT.search(_body_text(page)):
                    if not _recover_transient(page):
                        return done(rules.FAILED, f"Workday VPS error after Next on '{before_step}'")
                    snap, step = read_step(page)
                    history.append(f"Next click failed ({e.__class__.__name__}); recovered, now '{step}'")
                    continue
                raise
            _settle(page, 1500)
            _wait_wizard_ready(page, 8000)
            if (out := _maybe_submitted(page, res, run_dir, ctx, done)):
                return out
            if (cp := _captcha(page)):
                return done(rules.BLOCKED if cp == "DataDome" else rules.CAPTCHA, f"{cp} after '{before_step}'")
            new_snap, new_step = read_step(page)
            errs = _errors(page)
            if new_step != before_step and not errs:
                advanced = info["advanced"] = True
                break
            history.append(f"clicked Save and Continue but stayed on '{before_step}'. errors: {errs[:10]}")
            snap = new_snap
        if not advanced:
            res.errors.append(f"Workday step '{step}' did not advance: {history[-1][:400]}")
            return done(rules.FAILED)
    return done(rules.FAILED, "too many Workday steps")
