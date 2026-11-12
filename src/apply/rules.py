"""Deterministic rules enforced in code (not just in the prompt).

Everything here is pure and unit-tested without network or browser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# ---------------------------------------------------------------- statuses
READY_TO_SUBMIT = "READY_TO_SUBMIT"
PENDING = "PENDING"
CAPTCHA = "CAPTCHA"
LOGIN_REQUIRED = "LOGIN_REQUIRED"
FAILED = "FAILED"
SUBMITTED = "SUBMITTED"
BLOCKED = "BLOCKED"  # site bot protection (DataDome, Cloudflare, "access denied", anti-bot submit rejection)
ALL_STATUSES = {READY_TO_SUBMIT, PENDING, CAPTCHA, LOGIN_REQUIRED, FAILED, SUBMITTED, BLOCKED}

RYAN_SUBMITTED = "已提交"
RYAN_PENDING = "待决定"
RYAN_NOT_SUBMITTED = "未提交"


def map_status(status: str) -> str:
    """Map a runner status to Ryan's three tracking statuses."""
    if status == SUBMITTED:
        return RYAN_SUBMITTED
    if status in (READY_TO_SUBMIT, PENDING):
        return RYAN_PENDING
    if status in (FAILED, CAPTCHA, LOGIN_REQUIRED, BLOCKED):
        return RYAN_NOT_SUBMITTED
    raise ValueError(f"unknown status: {status}")


# ------------------------------------------------------ forced-PENDING rules
_AI_TERM = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|chat ?gpt|gpt|llms?|generative|gen ?ai|copilot|claude|gemini)\b|人工智能|ＡＩ",
    re.I,
)
_AI_VERB = re.compile(
    r"\b(use|used|using|assist\w*|help\w*|fill\w*|complet\w*|generat\w*|writ\w*|wrote|prepar\w*|draft\w*|creat\w*|aid\w*|tool\w*)\b"
    r"|填写|生成|撰写|完成|辅助",
    re.I,
)
_AI_OBJECT = re.compile(
    r"\b(application|form|answers?|responses?|submission|resume|cv|cover letter|questionnaire|materials?)\b|申请|表|简历|回答",
    re.I,
)


_AI_RECRUITING = re.compile(
    r"\b(recruiting|hiring|talent acquisition|candidate|applicants?|selection decisions?)\b",
    re.I,
)
_AI_OPT_OUT = re.compile(r"\bopt[\s-]?out\b", re.I)
_AI_MATCH_VERB = re.compile(
    r"\b(match|screen|evaluat\w*|rank|score|consider)\b.{0,80}\b(candidate|applicant|your (information|profile|application|data))\b"
    r"|\b(candidate|applicant|your (information|profile|application|data))\b.{0,80}\b(match|screen|evaluat\w*|rank|score)\b",
    re.I,
)
_AI_NOTETAKER = re.compile(
    r"\b(note[\s-]?tak\w*|meeting (notes?|record\w*|transcript\w*)|interview record\w*"
    r"|transcription of (the )?(meeting|interview)|ai (meeting |interview )?(assistant|scribe|recorder|notetaker))\b",
    re.I,
)
# "I do not opt out" / "I consent" / "opt in" — the option that ALLOWS employer AI matching
_ALLOW_AI_MATCH_OPT = re.compile(
    r"do not (wish to )?opt[\s-]?out|don'?t opt[\s-]?out|i (do )?not opt[\s-]?out"
    r"|decline to opt[\s-]?out|opt[\s-]?in|i (consent|agree|accept|authorize)"
    r"|allow( the use)?|i do not object|please use|i (have )?no objection",
    re.I,
)
_OPT_OUT_OPT = re.compile(
    r"\bopt[\s-]?out\b|do not (consent|agree|allow|authorize)|i (do not|don't) (consent|agree|authorize)|i object",
    re.I,
)
_STEM_OPT_EXT = re.compile(
    r"(24[\s-]?month|two[\s-]?year).{0,80}\bopt\b.{0,80}(extension|\bstem\b)"
    r"|\bopt\b.{0,80}(24[\s-]?month|extension).{0,60}\bstem\b"
    r"|eligible for a 24[\s-]?month opt extension"
    r"|\bstem[\s-]?opt\b.{0,40}(extension|eligib)",
    re.I,
)


def is_ai_matching_opt_out_question(text: str) -> bool:
    """Employer may use AI to match/screen candidates; the applicant can opt out (or consent)."""
    t = text or ""
    if not _AI_TERM.search(t):
        return False
    if _AI_OPT_OUT.search(t) and (_AI_RECRUITING.search(t) or _AI_MATCH_VERB.search(t)):
        return True
    return bool(_AI_MATCH_VERB.search(t) and _AI_RECRUITING.search(t))


def is_ai_notetaker_question(text: str) -> bool:
    """AI meeting-notetaker / interview-recording consent — auto-answered as consent / opt in."""
    t = text or ""
    return bool(_AI_TERM.search(t) and _AI_NOTETAKER.search(t))


# Employer use of AI to process the application or personal data, asking for consent.
_CONSENT_VERB = re.compile(r"\b(consent|agree|authori[sz]e|opt[\s-]?in|permission)\b", re.I)
_AI_PROCESS = re.compile(
    r"\b(process(ing)?|handle)\b.{0,80}\b((my|your|the|personal|applicant|candidate)\s+)?"
    r"(data|information|application|candidacy)\b"
    r"|\b((my|your|personal)\s+(data|information|application))\b",
    re.I,
)


def is_ai_processing_consent_question(text: str) -> bool:
    """Consent to the employer using AI to process the application or personal data.

    Not 'did you use AI to fill this application', and not matching/notetaker (those have
    their own classifiers; notetaker is also covered by is_ai_consent_question).
    """
    t = text or ""
    if is_ai_notetaker_question(t) or is_ai_matching_opt_out_question(t):
        return False
    return bool(_AI_TERM.search(t) and _CONSENT_VERB.search(t) and _AI_PROCESS.search(t))


def is_ai_consent_question(text: str) -> bool:
    """Notetaker / interview-recording or AI-processing consent — auto-answer consent / opt in."""
    return is_ai_notetaker_question(text) or is_ai_processing_consent_question(text)


def is_ai_use_question(text: str) -> bool:
    """True for 'did you use AI to fill/complete this application' style questions.

    Does not include employer AI-matching opt-out, AI notetaker / recording consent,
    or AI-processing consent (those are auto-answered as consent / opt in).
    """
    t = text or ""
    if is_ai_matching_opt_out_question(t) or is_ai_notetaker_question(t) or is_ai_processing_consent_question(t):
        return False
    return bool(_AI_TERM.search(t) and _AI_VERB.search(t) and _AI_OBJECT.search(t))


def is_stem_opt_extension_question(text: str) -> bool:
    """'Are you eligible for a 24-month OPT extension based upon a US degree in STEM?'"""
    return bool(_STEM_OPT_EXT.search(text or ""))


def allow_ai_matching_option(options: list[str], label: str = "") -> Optional[str]:
    """Pick the option that does NOT opt out — i.e. allows the employer to use AI matching."""
    opts = [o for o in (options or [])
            if o and not re.fullmatch(r"\s*(select(\s+one)?|choose|please select).*\s*", o, re.I)]
    for o in opts:
        if _ALLOW_AI_MATCH_OPT.search(o) and not (
            _OPT_OUT_OPT.search(o) and not re.search(r"do not (wish to )?opt|don'?t opt", o, re.I)
        ):
            return o
    # two-way Yes/No (or similar): opt-out framing -> No (do not opt out); consent framing -> Yes
    yes = next((o for o in opts if re.match(r"^\s*yes\b", o, re.I)), None)
    no = next((o for o in opts if re.match(r"^\s*no\b", o, re.I)), None)
    if yes and no:
        t = label or ""
        if _AI_OPT_OUT.search(t) and not re.search(r"\b(consent|agree|authorize|opt[\s-]?in)\b", t, re.I):
            return no
        return yes
    # leftover non-opt-out option when the other option is an explicit opt-out
    leftover = [o for o in opts if not (_OPT_OUT_OPT.search(o) and not re.search(r"do not (wish to )?opt|don'?t opt", o, re.I))]
    if len(opts) >= 2 and len(leftover) == 1:
        return leftover[0]
    return None


_SSN = re.compile(r"\b(ssn|social security( number| no\.?| #)?|social insurance number|sin number)\b|社会安全号|社保号", re.I)


def is_ssn_question(text: str) -> bool:
    return bool(_SSN.search(text or ""))


_DOB = re.compile(r"\b(date of birth|d\.?o\.?b\.?|birth ?date|born on|birthday)\b|出生日期|出生年月", re.I)


def is_dob_question(text: str) -> bool:
    return bool(_DOB.search(text or ""))


# WOTC / tax-credit eligibility (Work Opportunity Tax Credit and similar surveys)
_WOTC_CTX = re.compile(
    r"\bwotc\b|work opportunity tax credit|tax ?credit (co\b|company|program|survey|questionnaire|screening)"
    r"|eligibility for (federal and state )?tax credit|taxcreditco"
    r"|federal and state tax credit programs",
    re.I,
)
# Unambiguous WOTC screening items — safe to treat as WOTC even without the acronym
_WOTC_SCREEN = re.compile(
    r"\bsnap\b|supplemental nutrition assistance|food stamps?"
    r"|\btanf\b|temporary assistance for needy families|(referred to as|also (called|known as)) welfare"
    r"|\bwelfare\b"
    r"|convicted of a felony|felony conviction"
    r"|collected unemployment|unemployment benefits?"
    r"|are you unemployed",
    re.I,
)
# Veteran / disability only count as WOTC eligibility when the page/question is a tax-credit survey
_WOTC_VET_DISABILITY = re.compile(
    r"veteran of the (u\.?s\.?\s*)?(military|armed forces)|are you a veteran"
    r"|person who has a disability|do you have a disability|are you (a person )?with a disability",
    re.I,
)


def is_wotc_context(text: str) -> bool:
    return bool(_WOTC_CTX.search(text or ""))


def is_wotc_eligibility_question(text: str, context: str = "", options: Optional[list[str]] = None) -> bool:
    """True for WOTC/tax-credit screening questions that Ryan answers No (SNAP, TANF, veteran, disability, felony, unemployment).

    Veteran / disability questions are only treated as WOTC when the question or surrounding context
    is a tax-credit questionnaire, or when they use the Yes/No tax-credit wording with no
    'decline / prefer not to say' option, so voluntary self-ID on a regular application is unchanged.
    """
    t = text or ""
    if not t or is_ssn_question(t) or is_dob_question(t):
        return False
    if _WOTC_SCREEN.search(t):
        return True
    if not _WOTC_VET_DISABILITY.search(t):
        return False
    if is_wotc_context(t) or is_wotc_context(context):
        return True
    # Tax Credit Co wording: Yes/No only (no decline-to-self-identify option)
    if re.search(r"veteran of the (u\.?s\.?\s*)?(military|armed forces)|are you a person who has a disability", t, re.I):
        if options and any(re.search(r"decline|prefer not|do not wish|don't wish|self[ -]?identif", o or "", re.I) for o in options):
            return False
        return True
    return False


AI_USE_PENDING = "AI-use question: never answered automatically (FORM_ANSWERS rule)"
WOTC_DOB_PENDING = "date of birth on a WOTC/tax-credit questionnaire: never filled automatically (FORM_ANSWERS rule)"


def forced_pending_reason(label: str, context: str = "") -> Optional[str]:
    if is_ai_use_question(label):
        return AI_USE_PENDING
    if is_ssn_question(label):
        return "SSN: never filled automatically (FORM_ANSWERS rule)"
    if is_dob_question(label) and (is_wotc_context(label) or is_wotc_context(context)):
        return WOTC_DOB_PENDING
    return None


GITHUB_URL = "https://github.com/RyanZhou123232"
_GITHUB_Q = re.compile(r"\bgit ?hub\b(?![.\w/-]*\.(io|com)\b)(?!\.)", re.I)
_LINK_ASK = re.compile(r"\b(link|url|profile|username|handle|account|portfolio)\b", re.I)
_TRANSCRIPT_Q = re.compile(r"\btranscripts?\b|成绩单", re.I)
TRANSCRIPT_PENDING = "transcript required"


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def picker_date(value: str) -> str:
    """Normalize a date for MM/DD/YYYY date pickers. Month-only dates (05/2028, May 2028, 2028-05) become the 1st."""
    v = (value or "").strip()
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", v)
    if m:
        return f"{int(m[1]):02d}/{int(m[2]):02d}/{m[3]}"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        return f"{int(m[2]):02d}/{int(m[3]):02d}/{m[1]}"
    m = re.fullmatch(r"(\d{1,2})[/-](\d{4})", v) or re.fullmatch(r"(\d{4})-(\d{1,2})", v)
    if m:
        mo, yr = (m[1], m[2]) if len(m[2]) == 4 else (m[2], m[1])
        return f"{int(mo):02d}/01/{yr}"
    m = re.fullmatch(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})", v)
    if m and m[1].lower() in _MONTHS:
        return f"{_MONTHS[m[1].lower()]:02d}/{int(m[2]):02d}/{m[3]}"
    m = re.fullmatch(r"([A-Za-z]{3})[a-z]*\.?,? (\d{4})", v)
    if m and m[1].lower() in _MONTHS:
        return f"{_MONTHS[m[1].lower()]:02d}/01/{m[2]}"
    return v


def is_github_question(text: str) -> bool:
    """A field asking for the applicant's GitHub (not a label that merely contains a github.io/github.com link)."""
    t = re.sub(r"https?://\S+", " ", text or "")
    return bool(_GITHUB_Q.search(t) and (_LINK_ASK.search(t) or len(t.strip()) <= 40))


def is_resume_label(text: str) -> bool:
    return bool(re.search(r"\b(resume|résumé|cv)\b|简历", text or "", re.I))


def is_transcript_question(text: str) -> bool:
    return bool(_TRANSCRIPT_Q.search(text or ""))


_SOURCE_Q = re.compile(r"how did you (hear|learn|find)|where did you (hear|learn|find)|how did you first (hear|learn)|referral source|source of (application|referral)|从哪里得知", re.I)


_MAJOR_Q = re.compile(r"\b(major|discipline|field of study|area of study|concentration)\b|专业", re.I)
MAJOR_EXACT = "Statistics & Machine Learning"
MAJOR_FALLBACK = "Machine Learning"


def is_major_question(text: str) -> bool:
    return bool(_MAJOR_Q.search(text or ""))


def major_value_ok(value: str) -> bool:
    """Only Ryan's real major (Statistics & Machine Learning) or the standing fallback (Machine Learning) are allowed."""
    n = _norm(value).replace("and", "")
    return n in (_norm(MAJOR_EXACT).replace("and", ""), _norm(MAJOR_FALLBACK)) or n == _norm("Statistics and Machine Learning").replace("and", "")


def is_source_question(text: str) -> bool:
    return bool(_SOURCE_Q.search(text or ""))


def source_answer(options: list[str], field_type: str) -> Optional[str]:
    """FORM_ANSWERS: 'Job board'; if no such option, 'Other'; if neither exists, an option naming job boards (e.g. 'Indeed / Glassdoor')."""
    if options:
        for i, o in enumerate(options):
            if re.search(r"job ?board", o, re.I):
                return options[i]
        for i, o in enumerate(options):
            if re.fullmatch(r"\s*other\b.*", o, re.I):
                return options[i]
        # no 'Job board' and no 'Other': an option naming job boards (e.g. 'Indeed / Glassdoor') is the job-board answer
        for i, o in enumerate(options):
            if re.search(r"\bjob (site|website|posting site)|\bindeed\b|glassdoor|ziprecruiter|online job", o, re.I):
                return options[i]
        return None
    return "Other" if field_type == "combobox" else ("Job board" if field_type in ("text", "textarea") else None)


# ------------------------------------------------------------------ salary
HOURS_PER_YEAR = 2080.0
HOURS_PER_MONTH = 173.3
DEFAULT_HOURLY = {"SWE": 30.0, "AI": 30.0, "DATA": 25.0}

_SALARY_LABEL = re.compile(
    r"salary|compensation|pay rate|pay expectation|expected pay|desired pay|hourly rate|"
    r"rate expectation|desired rate|expected rate|wage|期望薪资|薪资|薪酬|工资",
    re.I,
)


def is_salary_question(text: str) -> bool:
    return bool(_SALARY_LABEL.search(text or ""))


def detect_unit(text: str) -> Optional[str]:
    t = (text or "").lower()
    if re.search(r"annual|per year|yearly|/ ?yr|/ ?year|a year|年薪|每年", t):
        return "year"
    if re.search(r"month|/ ?mo\b|月薪|每月", t):
        return "month"
    if re.search(r"hour|/ ?hr|hourly|时薪|每小时", t):
        return "hour"
    return None


def normalize_unit(unit: str) -> str:
    u = (unit or "hour").lower().strip()
    if u in ("hour", "hr", "hourly", "h"):
        return "hour"
    if u in ("year", "yr", "annual", "yearly", "annually", "y"):
        return "year"
    if u in ("month", "mo", "monthly", "m"):
        return "month"
    raise ValueError(f"unknown salary unit: {unit}")


def convert_salary(amount: float, from_unit: str, to_unit: str) -> float:
    fu, tu = normalize_unit(from_unit), normalize_unit(to_unit)
    hourly = {"hour": amount, "year": amount / HOURS_PER_YEAR, "month": amount / HOURS_PER_MONTH}[fu]
    return {"hour": hourly, "year": hourly * HOURS_PER_YEAR, "month": hourly * HOURS_PER_MONTH}[tu]


def format_amount(amount: float, unit: str) -> str:
    unit = normalize_unit(unit)
    if unit == "hour":
        r = round(amount, 2)
        return str(int(r)) if r == int(r) else f"{r:.2f}".rstrip("0").rstrip(".")
    return str(int(round(amount)))


@dataclass
class SalaryChoice:
    amount: float
    unit: str
    source: str


def choose_salary(role_category: str, salary_hint: Optional[dict] = None, jd_pay: Optional[dict] = None) -> SalaryChoice:
    """JD pay > supplied hint (e.g. Levels.fyi median) > default by role category."""
    for src in (jd_pay, salary_hint):
        if src and src.get("amount") not in (None, ""):
            return SalaryChoice(float(src["amount"]), normalize_unit(src.get("unit", "hour")),
                                src.get("source") or ("JD" if src is jd_pay else "hint"))
    cat = (role_category or "").upper()
    if cat not in DEFAULT_HOURLY:
        raise ValueError(f"role_category must be one of {sorted(DEFAULT_HOURLY)}")
    return SalaryChoice(DEFAULT_HOURLY[cat], "hour", f"default {cat}")


def salary_for_field(choice: SalaryChoice, field_text: str) -> tuple[str, str]:
    """Return (value string, unit used) for a salary field, converting to the field's unit."""
    unit = detect_unit(field_text) or choice.unit
    return format_amount(convert_salary(choice.amount, choice.unit, unit), unit), unit


# --------------------------------------------------------- buttons / pages
_SUBMIT = re.compile(r"\b(submit|send application|finish|complete application|complete & submit)\b|提交", re.I)
_NEXT = re.compile(r"\b(next|continue|proceed|save (and|&) continue|save & next|review|go to next)\b|下一步|继续", re.I)
_IGNORE = re.compile(r"^\+|\b(back|previous|prev|cancel|upload|attach|add|remove|delete|clear|sign in|log ?in|withdraw|save (as )?draft|dropbox|google drive|enter manually)\b", re.I)


def classify_button(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t or len(t) > 60 or _IGNORE.search(t):
        return None
    if _SUBMIT.search(t):
        return "submit"
    if _NEXT.search(t):
        return "next"
    return None


_ENTRY = re.compile(r"^(apply|apply now|apply for this (job|position|role)|apply to this job|i'?m interested|start (your )?application|apply online)$", re.I)


def is_entry_button(text: str) -> bool:
    """'Apply' / "I'm interested" buttons that open the application form from a job description page."""
    return bool(_ENTRY.match(re.sub(r"[^\w' ]+", "", (text or "")).strip()))


_CLOSED = re.compile(r"job (has )?expired|no longer (accepting applications|available|open)|position (has been )?(filled|closed)|"
                     r"job (posting )?(is )?(closed|not found)|this job is no longer|"
                     r"page you('| a)re looking for (has been removed|could not be found|does(n't| not) exist|is unavailable)|职位已关闭", re.I)


def is_closed_posting(text: str) -> bool:
    return bool(_CLOSED.search(text or ""))


_BLOCKED = re.compile(r"403 error|access denied|request could not be satisfied|are you a robot|unusual traffic|"
                      r"verify you are human|attention required", re.I)


def is_blocked_page(text: str) -> bool:
    return bool(_BLOCKED.search(text or ""))


# Bot-protection interstitials. Each entry: (reason, pattern over title + visible text + iframe/script URLs).
_BOT_WALLS = [
    # DataDome serves its wall as an iframe from captcha-delivery.com (its tag script alone is on normal pages too)
    ("DataDome bot protection", re.compile(r"captcha-delivery\.com", re.I)),
    # Cloudflare interstitial (embedded Turnstile widgets on normal forms are not a wall)
    ("Cloudflare challenge", re.compile(r"just a moment\.\.\.|checking (if the site connection is secure|your browser)|"
                                         r"verifying you are human|__cf_chl_", re.I)),
    ("Imperva / Incapsula bot protection", re.compile(r"incapsula incident|request unsuccessful\. incapsula|pardon our interruption", re.I)),
    ("PerimeterX bot protection", re.compile(r"px-captcha|press (&|and) hold", re.I)),
    ("access denied / bot check", _BLOCKED),
]


def bot_block_reason(text: str, frame_urls: Iterable[str] = ()) -> Optional[str]:
    """Reason string if the page is a bot-protection wall (not a normal form), else None."""
    hay = (text or "")[:3000] + " " + " ".join(frame_urls or ())
    for reason, rx in _BOT_WALLS:
        m = rx.search(hay)
        if m:
            return f"{reason} ('{m.group(0)[:60]}')"
    return None


# Messages an ATS shows after Submit when it refuses the application (usually anti-bot scoring).
_SUBMIT_REJECT = re.compile(
    r"error (processing|submitting|verifying) your application|there was an error verifying|"
    r"(could not|unable to) (verify|validate) (that you are human|your (application|submission))|"
    r"flagged as (spam|suspicious)|something went wrong.{0,40}try again", re.I)


def submit_rejection(text: str) -> Optional[str]:
    m = _SUBMIT_REJECT.search(text or "")
    return m.group(0) if m else None


def page_advanced(before_keys: Iterable[str], after_keys: Iterable[str], url_before: str, url_after: str,
                  doc_before: str = "", doc_after: str = "") -> bool:
    """Decide whether clicking Next actually moved to a new page/step.

    Field keys embed a per-document token, so a full navigation yields new keys.
    In an SPA the old step's fields become hidden. Newly revealed conditional fields
    alone (old fields still visible) do NOT count as advancing.
    """
    b, a = set(before_keys), set(after_keys)
    if doc_before and doc_after and doc_before != doc_after:
        return True
    if not b:
        return url_before != url_after or bool(a)
    still_visible = len(b & a) / len(b)
    if still_visible < 0.5:
        return True
    if url_before.split("#")[0] != url_after.split("#")[0] and still_visible < 1.0:
        return True
    return False


def detect_login(field_types: Iterable[str]) -> bool:
    return "password" in set(field_types)


# ----------------------------------------------------------- option match
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower().rstrip("*").strip())


def best_option(target: str, options: list[str]) -> Optional[int]:
    """Index of the option best matching target: exact > prefix > contains > token match."""
    t = _norm(target)
    if not t:
        return None
    normed = [_norm(o) for o in options]
    for i, o in enumerate(normed):
        if o == t:
            return i
    for i, o in enumerate(normed):
        if o and (o.startswith(t) or t.startswith(o)) and len(o) > 1:
            return i
    for i, o in enumerate(normed):
        if o and (t in o or o in t) and len(o) > 2:
            return i
    return None


_US_STATES = dict(x.split(":") for x in (
    "al:alabama ak:alaska az:arizona ar:arkansas ca:california co:colorado ct:connecticut de:delaware fl:florida ga:georgia "
    "hi:hawaii id:idaho il:illinois in:indiana ia:iowa ks:kansas ky:kentucky la:louisiana me:maine md:maryland "
    "ma:massachusetts mi:michigan mn:minnesota ms:mississippi mo:missouri mt:montana ne:nebraska nv:nevada "
    "nh:new_hampshire nj:new_jersey nm:new_mexico ny:new_york nc:north_carolina nd:north_dakota oh:ohio ok:oklahoma "
    "or:oregon pa:pennsylvania ri:rhode_island sc:south_carolina sd:south_dakota tn:tennessee tx:texas ut:utah vt:vermont "
    "va:virginia wa:washington wv:west_virginia wi:wisconsin wy:wyoming dc:district_of_columbia").split())


def _place_parts(s: str) -> list[str]:
    parts = [_norm(p) for p in (s or "").split(",") if p.strip()]
    out = []
    for p in parts:
        p = _US_STATES.get(p, p.replace(" ", "_")).replace("_", " ")
        out.append({"usa": "united states", "us": "united states", "u.s.": "united states"}.get(p, p))
    return out


def best_place_option(target: str, options: list[str]) -> Optional[int]:
    """'Pittsburgh, PA, USA' vs 'Pittsburgh, Pennsylvania, United States': same city, then most matching parts."""
    want = _place_parts(target)
    if not want:
        return None
    best, score = None, 0
    for i, o in enumerate(options):
        have = _place_parts(o)
        if not have or have[0] != want[0]:
            continue
        sc = 1 + sum(1 for w in want[1:] if w in have[1:])
        if sc > score:
            best, score = i, sc
    return best


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def values_match(expected: str, actual: str) -> bool:
    e, a = _norm(str(expected)), _norm(str(actual))
    if e == a:
        return True
    de, da = digits(e), digits(a)
    if len(de) >= 7 and (de == da or da.endswith(de) or de.endswith(da)):  # phone masks
        return True
    return False


# ----------------------------------------------- employers / current company
# Lucina is a personal project (Independent Developer), never a current employer.
FORBIDDEN_EMPLOYER_PATTERNS = re.compile(
    r"lucina|personal ai agent|fumount|福山集",
    re.I,
)

# Exact names as they appear on the resume / ALL_INFO. Experience & employer fields
# should only use these three (never Lucina or other personal projects).
ALLOWED_EMPLOYERS = (
    "PricewaterhouseCoopers (PwC)",
    "SDIC Securities",
    "Carnegie Mellon University Human-Computer Interaction Institute — Augmented Design Capability Studio",
)

# Most recent of the three real employers; used for "Current company" / "Current employer".
CURRENT_COMPANY = "PricewaterhouseCoopers (PwC)"

_CURRENT_COMPANY_Q = re.compile(
    r"current (company|employer|organization|organisation|org)\b|present (company|employer)|"
    r"where (do|are) you (currently )?work|employer name|company name|"
    r"目前(的)?(公司|雇主|单位)|现任(公司|雇主)",
    re.I,
)
_EMPLOYER_Q = re.compile(
    r"\b(company|employer|organization|organisation|org|workplace)\b|"
    r"(公司|雇主|单位|任职)",
    re.I,
)


def is_forbidden_employer(value: str) -> bool:
    """True if value names Lucina / other personal projects that must not be employers."""
    return bool(FORBIDDEN_EMPLOYER_PATTERNS.search(value or ""))


def is_current_company_question(text: str) -> bool:
    return bool(_CURRENT_COMPANY_Q.search(text or ""))


def is_employer_question(text: str) -> bool:
    """Broader than current-company: any company/employer field on experience sections."""
    return bool(_EMPLOYER_Q.search(text or ""))


def current_company_value() -> str:
    return CURRENT_COMPANY


def sanitize_employer_value(value: str, *, current: bool = False) -> Optional[str]:
    """Replace forbidden personal-project names; for current-company fields, force CURRENT_COMPANY.

    Returns None when the value is fine as-is (caller should keep the original).
    """
    v = (value or "").strip()
    if is_forbidden_employer(v) or (current and not v):
        return CURRENT_COMPANY
    if current and v:
        # If the model invented some other employer, still prefer the standing current company
        # only when it is clearly not one of the allowed three.
        if any(_norm(v) == _norm(a) or _norm(v) in _norm(a) or _norm(a) in _norm(v) for a in ALLOWED_EMPLOYERS):
            return None
        # e.g. bare "PwC" / "SDIC" / "CMU HCII" short forms — keep
        if re.search(r"\b(pwc|pricewaterhouse|sdic|hcii?|human.?computer)\b", v, re.I):
            return None
        return CURRENT_COMPANY
    return None


_LOCATION_LABEL = re.compile(
    r"current location|location|city|where (are|do) you (live|based)|所在地|地点|城市",
    re.I,
)


def is_location_question(text: str) -> bool:
    return bool(_LOCATION_LABEL.search(text or ""))


# ------------------------------------------------- multi-entry (repeatable) sections
# Canonical work-experience entries, most recent first. Only these three are ever entered as employers.
EXPERIENCE_ENTRIES = (
    {"company": "PricewaterhouseCoopers (PwC)", "short": "PwC", "title": "IT Consulting Intern",
     "location": "Shenzhen, China", "start": "2026-07", "end": "2026-09", "current": False},
    {"company": "SDIC Securities", "short": "SDIC Securities",
     "title": "Assistant to Chief Analyst, Chemical Industry Research",
     "location": "Shenzhen, China", "start": "2026-06", "end": "2026-07", "current": False},
    {"company": "Carnegie Mellon University Human-Computer Interaction Institute — Augmented Design Capability Studio",
     "short": "CMU HCII", "title": "Research Assistant", "location": "Pittsburgh, PA, USA",
     "start": "2026-01", "end": "2026-06", "current": False},
)
# How many entries to open per section kind when the form starts with fewer (education: CMU only).
ENTRY_TARGETS = {"experience": len(EXPERIENCE_ENTRIES), "education": 1}

_KIND_RE = (
    ("education", re.compile(r"academic (experience|history|background)|education", re.I)),
    ("experience", re.compile(r"work experience|experience|employment|work history|employer|positions? held|工作经历|实习经历", re.I)),
    ("education", re.compile(r"education|school|degree|universit|academic|教育", re.I)),
)
_ADD_RE = re.compile(r"^\s*(\+\s*)?(add|\+)(\s+(another|more|new|an?))?\b(\s+[\w ]{0,40})?\s*$", re.I)


def section_kind(text: str) -> Optional[str]:
    """'experience' / 'education' for a section heading or Add-button text, else None."""
    t = (text or "")[:120]
    for kind, rx in _KIND_RE:
        if rx.search(t):
            return kind
    return None


def is_add_entry_button(text: str) -> bool:
    """'Add', '+ Add another', 'Add Work Experience', 'Add education' ... (not 'Add file' / 'Add cover letter')."""
    t = (text or "").strip()
    if len(t) > 50 or not _ADD_RE.match(t):
        return False
    return not re.search(r"file|attachment|resume|cv|cover letter|photo|link|website|url|dropbox|drive", t, re.I)


def _entry_number(heading: str) -> Optional[int]:
    m = re.search(r"(?:#\s*)?(\d{1,2})\s*$", heading or "")
    return int(m.group(1)) if m else None


def assign_entries(items: list[dict]) -> list[Optional[tuple[str, int]]]:
    """Map each field (dict with 'section' heading and 'label') to (kind, entry_index) or None.

    Entry index comes from a trailing number in the heading ("Work Experience 2") when present;
    otherwise a new entry starts whenever the first label of the current entry repeats.
    """
    out: list[Optional[tuple[str, int]]] = []
    state: dict[str, dict] = {}
    for it in items:
        kind = section_kind(it.get("section", ""))
        if not kind:
            out.append(None)
            continue
        st = state.setdefault(kind, {"idx": 0, "first": None, "numbered": False, "started": False})
        n = _entry_number(it.get("section", ""))
        label = _norm(it.get("label", ""))
        if n is not None:
            st.update(idx=max(n - 1, 0), numbered=True, started=True)
        elif not st["started"]:
            st.update(started=True, first=label)
        elif label and label == st["first"]:
            st["idx"] += 1
        out.append((kind, st["idx"]))
    return out


def experience_entry(index: int) -> Optional[dict]:
    return EXPERIENCE_ENTRIES[index] if 0 <= index < len(EXPERIENCE_ENTRIES) else None


def is_company_label(text: str) -> bool:
    return bool(re.search(r"\b(company|employer|organi[sz]ation|firm)\b|公司|雇主|单位", text or "", re.I))


# ------------------------------------------------ standing answers (2026-11-09, batch 2 follow-up)
_TEXT_CONSENT_Q = re.compile(r"\b(sms|whats ?app|text messages?|text message updates|texts?|mobile messages?)\b", re.I)
_CONSENT_WORDS = re.compile(r"\b(consent|agree|opt.?in|receive|notifications?|updates?|contact(ed)? (me )?(via|by)|communicat\w*|messag\w*)\b", re.I)
_OTHER_ROLES_Q = re.compile(r"\b(consider|considered|considering)\b.{0,60}\b(other|additional|future|similar)\b.{0,40}\b(roles?|positions?|opportunit\w+|jobs?|openings?)\b"
                            r"|\b(other|additional)\b.{0,30}\b(roles?|positions?|opportunit\w+|jobs?)\b.{0,40}\b(consider\w*|share|talent pool)\b", re.I)
_YES_OPT = re.compile(r"^\s*(yes|y|i (do )?(consent|agree|authorize|accept|opt.?in|would like)|opt.?in|agree|accept)\b", re.I)
_NO_OPT = re.compile(r"\b(no|not|don'?t|do not|decline|opt.?out)\b", re.I)


def is_text_consent_question(text: str) -> bool:
    """SMS / WhatsApp / text-message notification consent (standing answer: Yes)."""
    t = text or ""
    return bool(_TEXT_CONSENT_Q.search(t) and (_CONSENT_WORDS.search(t) or len(t.strip()) <= 20))


def is_other_roles_question(text: str) -> bool:
    """'Consider me for other roles / positions' (standing answer: Yes)."""
    return bool(_OTHER_ROLES_Q.search(text or ""))


def affirmative_option(options: list[str]) -> Optional[str]:
    """First 'Yes' / 'I consent' / 'I agree' option (judged on the part before any dash or comma)."""
    for o in options or []:
        head = re.split(r"\s[-–]\s|[,:(]", o, maxsplit=1)[0]
        if _YES_OPT.search(head) and not _NO_OPT.search(head):
            return o
    return None


# graduation date / year / class standing — tied to the uploaded resume template
# 2028 templates (default, and every non-SWE direction): May 2028.
# 2029 SWE template (sophomore-allowed jobs): June 2029, matching the resume.
DEFAULT_GRAD_YEAR = 2028
GRAD_YEAR = "2028"  # backward-compatible alias; prefer effective_grad_year()
NO_GRAD_YEAR_PENDING = "no 2028 graduation year option"
GRAD_MONTH_BY_YEAR = {2028: 5, 2029: 6}
CLASS_STANDING_BY_YEAR = {2028: "Junior", 2029: "Sophomore"}
MONTH_NAMES = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
               7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}
MONTH_ABBR = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
              7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
_GRAD_Q = re.compile(r"graduat\w*|class of|expected (end|completion)|completion (date|year)|end (date|year)|毕业", re.I)
_GRAD_EXPLICIT_Q = re.compile(
    r"graduat\w*|class of|expected (end|completion)|completion (date|year)|毕业|"
    r"degree (completion|conferral)|graduating class|expected class", re.I)
_EDU_END_Q = re.compile(r"^(expected )?end (date|month|year)$", re.I)
_MONTH_FIELD_Q = re.compile(r"\bmonth\b|月份", re.I)
_YEAR_FIELD_Q = re.compile(r"\byear\b|年份", re.I)
_CLASS_YEAR_Q = re.compile(r"class of|graduation class|expected class year|graduating class|\bclass year\b", re.I)
_STANDING_Q = re.compile(
    r"\b(academic|class) standing\b|\byear (in|of) (school|study|college|university|program)\b|"
    r"\bcurrent (class|standing|year in school)\b|\bstudent (classification|level|year)\b|"
    r"\b(freshman|sophomore|junior|senior)\b.{0,30}\b(year|standing|status|class)\b|"
    r"\bwhat year (are you|in school)\b|\byear of study\b", re.I)
_FT_AVAIL_Q = re.compile(
    r"full[\s-]?time.{0,60}(start|availab|begin)|"
    r"(start|availab|begin).{0,60}full[\s-]?time|"
    r"(upon|after|following) graduation|"
    r"available to start (a )?full[\s-]?time|"
    r"earliest (date )?(for )?full[\s-]?time", re.I)
_STANDING_WORD = re.compile(r"\b(freshman|sophomore|junior|senior)\b", re.I)
_YN_STANDING_Q = re.compile(
    r"\b(are you|will you|have you)\b|\bcurrently a\b|\bclass of\b|\bgraduat", re.I)


def no_grad_year_pending(year: Optional[int] = None) -> str:
    y = int(year or DEFAULT_GRAD_YEAR)
    return f"no {y} graduation year option"


def grad_month_year(year: Optional[int] = None) -> tuple[int, int]:
    y = int(year or DEFAULT_GRAD_YEAR)
    if y not in GRAD_MONTH_BY_YEAR:
        y = DEFAULT_GRAD_YEAR
    return GRAD_MONTH_BY_YEAR[y], y


def class_standing_for_year(year: Optional[int] = None) -> str:
    y = int(year or DEFAULT_GRAD_YEAR)
    return CLASS_STANDING_BY_YEAR.get(y, CLASS_STANDING_BY_YEAR[DEFAULT_GRAD_YEAR])


def infer_grad_year_from_resume_path(path: str) -> Optional[int]:
    """2029 only for the SWE mother template `SWE\\Ryan Zhou Resume.pdf`; everything else is 2028."""
    if not (path or "").strip():
        return None
    try:
        from .p2.selector import grad_year_from_pdf
        return grad_year_from_pdf(path)
    except Exception:
        p = path.replace("/", "\\").lower()
        return 2029 if p.endswith(r"\swe\ryan zhou resume.pdf") else 2028


def effective_grad_year(explicit=None, resume_path: str = "") -> int:
    if explicit in (2028, 2029) or str(explicit).strip() in ("2028", "2029"):
        return int(explicit)
    return infer_grad_year_from_resume_path(resume_path) or DEFAULT_GRAD_YEAR



def year_like_options(options: list[str]) -> bool:
    return sum(bool(re.fullmatch(r"\s*(19|20)\d\d\s*", o or "")) for o in options or []) >= 3


def month_like_options(options: list[str]) -> bool:
    n = 0
    for o in options or []:
        if re.fullmatch(r"\s*(0?[1-9]|1[0-2])\s*", o or ""):
            n += 1
        elif re.match(r"\s*(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
                      r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
                      o or "", re.I):
            n += 1
    return n >= 6


def _explicit_grad(label: str) -> bool:
    return bool(_GRAD_EXPLICIT_Q.search(label or "") or _CLASS_YEAR_Q.search(label or ""))


def is_grad_question(label: str, entry_kind: Optional[str] = None, options: Optional[list[str]] = None,
                     planned_value: str = "") -> bool:
    """Graduation / expected graduation / education end-date (not experience, not internship start)."""
    if entry_kind == "experience":
        return False
    lab = label or ""
    if re.search(r"\bstart\b", lab, re.I) and not _explicit_grad(lab):
        return False
    if _explicit_grad(lab):
        return True
    if (entry_kind == "education" or not entry_kind) and _EDU_END_Q.search(lab.strip()):
        if entry_kind == "education":
            return True
        if year_like_options(options or []) or month_like_options(options or []):
            return True
        if re.search(r"\b(2028|2029)\b", str(planned_value or "")):
            return True
    return False


def is_grad_month_field(label: str, options: Optional[list[str]] = None,
                        entry_kind: Optional[str] = None, planned_value: str = "") -> bool:
    if not is_grad_question(label, entry_kind, options, planned_value):
        return False
    if _YEAR_FIELD_Q.search(label or "") and not _MONTH_FIELD_Q.search(label or ""):
        return False
    return bool(_MONTH_FIELD_Q.search(label or "")) or month_like_options(options or [])


def is_grad_year_field(label: str, options: Optional[list[str]] = None,
                       entry_kind: Optional[str] = None, planned_value: str = "") -> bool:
    if not is_grad_question(label, entry_kind, options, planned_value):
        return False
    if is_grad_month_field(label, options, entry_kind, planned_value) and not _YEAR_FIELD_Q.search(label or ""):
        return False
    return bool(_YEAR_FIELD_Q.search(label or "") or year_like_options(options or []) or _CLASS_YEAR_Q.search(label or ""))


def is_class_standing_question(label: str, options: Optional[list[str]] = None) -> bool:
    if _STANDING_Q.search(label or ""):
        return True
    n = sum(bool(_STANDING_WORD.search(o or "")) for o in options or [])
    return n >= 2 and bool(re.search(r"\b(class|year|standing|student|status)\b", label or "", re.I))


def is_fulltime_grad_availability_question(label: str) -> bool:
    return bool(_FT_AVAIL_Q.search(label or ""))


def missing_grad_year(label: str, options: list[str], planned_value: str = "", year=None,
                      entry_kind: Optional[str] = None) -> bool:
    """A graduation / end-date YEAR dropdown that does not offer the resume's graduation year."""
    want = str(effective_grad_year(year))
    if not year_like_options(options):
        return False
    explicit = _explicit_grad(label)
    if not (explicit or is_grad_question(label, entry_kind, options, planned_value)
            or want in str(planned_value or "") or GRAD_YEAR in str(planned_value or "")):
        return False
    if re.search(r"\bstart\b", label or "", re.I) and not explicit:
        return False
    return not any(want in (o or "") for o in options)


def pick_month_option(options: list[str], month: int) -> Optional[str]:
    full, abbr = MONTH_NAMES[month], MONTH_ABBR[month]
    exact = re.compile(rf"\s*(?:{re.escape(full)}|{re.escape(abbr)}|{month:02d}|{month})\s*$", re.I)
    loose = re.compile(rf"\s*(?:{re.escape(full)}|{re.escape(abbr)})\b", re.I)
    for o in options or []:
        if exact.fullmatch(o or ""):
            return o
    for o in options or []:
        if loose.search(o or ""):
            return o
    return None


def pick_year_option(options: list[str], year: int) -> Optional[str]:
    y = str(year)
    for o in options or []:
        if re.fullmatch(rf"\s*{y}\s*", o or ""):
            return o
    for o in options or []:
        if re.search(rf"\b{y}\b", o or ""):
            return o
    return None


def pick_standing_option(options: list[str], standing: str) -> Optional[str]:
    aliases = {
        "Junior": r"junior|\bjr\.?\b|3rd year|third year|year 3",
        "Sophomore": r"sophomore|\bsoph\.?\b|2nd year|second year|year 2",
        "Senior": r"senior|\bsr\.?\b|4th year|fourth year|year 4",
        "Freshman": r"freshman|first year|1st year|year 1",
    }
    pat = aliases.get(standing, re.escape(standing))
    for o in options or []:
        if re.search(pat, o or "", re.I) and not re.search(r"\brising\b", o or "", re.I):
            return o
    for o in options or []:
        if re.search(pat, o or "", re.I):
            return o
    return None


def standing_yes_no(label: str, year: int) -> Optional[bool]:
    """Yes/No questions that name a class year or standing. None if the label is not that kind of question."""
    t = label or ""
    mentioned_years = [int(y) for y in re.findall(r"\b(202[6-9]|2030)\b", t)]
    mentions = [m.group(1).lower() for m in _STANDING_WORD.finditer(t)]
    if not mentioned_years and not mentions:
        return None
    if not _YN_STANDING_Q.search(t):
        return None
    if mentioned_years:
        return year in mentioned_years
    return class_standing_for_year(year).lower() in mentions


def format_grad_for_field(month: int, year: int, placeholder: str = "", input_type: str = "",
                          date_picker: bool = False, options: Optional[list[str]] = None) -> str:
    """Format May 2028 / June 2029 for the field (MM/YYYY, date picker, option list, or 'Month YYYY')."""
    name, abbr, mm = MONTH_NAMES[month], MONTH_ABBR[month], f"{month:02d}"
    if options:
        if year_like_options(options):
            opt = pick_year_option(options, year)
            if opt:
                return opt
        if month_like_options(options) and not any(str(year) in (o or "") for o in options):
            opt = pick_month_option(options, month)
            if opt:
                return opt
        wanted = (f"{name} {year}", f"{abbr} {year}", f"{mm}/{year}", f"{mm}/01/{year}",
                  f"{year}-{mm}", f"{year}-{mm}-01")
        norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())
        for c in wanted:
            for o in options:
                if norm(o) == norm(c):
                    return o
        for c in wanted:
            for o in options:
                if c.lower() in (o or "").lower() and str(year) in (o or ""):
                    return o
    ph = placeholder or ""
    if input_type == "date" or re.search(r"yyyy-mm-dd", ph, re.I):
        return f"{year}-{mm}-01"
    if re.search(r"mm\s*/\s*yyyy|(?<![a-z])month\s*/\s*year", ph, re.I):
        return f"{mm}/{year}"
    if date_picker or re.search(r"mm\s*/\s*dd|\bdd\b", ph, re.I):
        return f"{mm}/01/{year}"
    if re.search(r"/", ph) and re.search(r"yyyy", ph, re.I) and not re.search(r"\bdd\b", ph, re.I):
        return f"{mm}/{year}"
    return f"{name} {year}"


# earliest start date / availability
_START_Q = re.compile(r"earliest (potential |possible |available |preferred )?start|start date (if hired|availability)|available to start|"
                      r"when (can|could|would) you (start|begin)|availability (date|to start)|date (you are|you're) available|"
                      r"earliest (date|availability)|可(以)?开始|到岗", re.I)
_NOT_SUMMER_JOB = re.compile(r"\b(fall|autumn|winter|spring) (20\d\d|semester|term|co-?op)\b|\boff[- ]cycle\b|\bco-?op\b|"
                             r"\b(start|begin)(ing)? (immediately|asap|now)\b|\bpart[- ]time during the (school|academic) year\b|"
                             r"\b(6|six)[- ]month (internship|co-?op)\b|\bstarting in (january|february|march|september|october|november|december)\b", re.I)
_SUMMER_JOB = re.compile(r"\bsummer\b", re.I)


def is_start_date_question(text: str) -> bool:
    return bool(_START_Q.search(text or ""))


def summer_availability_start(all_info: str) -> Optional[str]:
    """'Summer 2027 availability: May 1, 2027 – September 1, 2027.' -> 'May 1, 2027'."""
    m = re.search(r"Summer \d{4} availability:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", all_info or "")
    return m.group(1) if m else None


def use_summer_start(job_context: str) -> bool:
    """Summer internship unless the posting is explicitly fall / off-cycle / co-op / start-now."""
    jc = job_context or ""
    return not _NOT_SUMMER_JOB.search(jc[:6000]) or bool(_SUMMER_JOB.search(jc.split("\n", 1)[0]))


def format_date_for_field(date_text: str, placeholder: str = "", input_type: str = "", date_picker: bool = False) -> str:
    """'May 1, 2027' -> the field's format: MM/DD/YYYY for pickers / mm/dd placeholders, YYYY-MM-DD for <input type=date>."""
    norm = picker_date(date_text)  # MM/DD/YYYY when parseable
    m = re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", norm)
    if not m:
        return date_text
    if input_type == "date":
        return f"{m[3]}-{m[1]}-{m[2]}"
    if date_picker or re.search(r"mm|dd|yyyy|/", placeholder or "", re.I):
        return norm
    return date_text


# location preference (which offices / cities are you open to)
_LOC_PREF_Q = re.compile(
    r"(which|what|preferred|prefer|open to|willing|interested|select|choose|rank)\b.{0,70}\b(locations?|offices?|cities|city|hubs?|sites?)\b"
    r"|\b(locations?|offices?|hubs?|cities)\b.{0,50}\b(open to|interested|prefer\w*|willing|work (in|from)|relocat\w*|rank\w*)\b", re.I)
_CURRENT_LOC_Q = re.compile(r"current(ly)? (location|city|based|located|reside)|where (are|do) you (currently )?(live|located|based|reside)|"
                            r"city and state of residence|location \(city\)|^location$|^city$|home address", re.I)
_ANY_LOC_OPT = re.compile(r"^\s*(either|any|both|all( of the above| locations)?|no preference|open to (any|all)|flexible|anywhere)\b", re.I)
_NOT_A_LOC_OPT = re.compile(r"none of the above|^\s*none\b|^\s*other\b|not (open|willing|interested)|prefer not|^\s*no\b|n/?a\b", re.I)
LOCATION_RANKING = [  # FORM_ANSWERS: posted location first, then this order
    ("Bay Area", r"san francisco|\bsf\b|bay area|san jose|palo alto|mountain view|menlo park|sunnyvale|santa clara|redwood city|"
                 r"oakland|berkeley|cupertino|san mateo|foster city|burlingame|san bruno|fremont|milpitas|silicon valley"),
    ("Chicago", r"chicago"),
    ("New York", r"new york|\bnyc\b|\bny\b|brooklyn|manhattan|jersey city"),
    ("Pittsburgh", r"pittsburgh"),
    ("Los Angeles", r"los angeles|\bla\b|santa monica|culver city|playa vista|pasadena"),
]


def is_location_preference_question(text: str) -> bool:
    t = text or ""
    return bool(_LOC_PREF_Q.search(t)) and not _CURRENT_LOC_Q.search(t.strip())


def all_location_options(options: list[str]) -> list[str]:
    """Multi-select: every real location (skip None / Other / not-open)."""
    return [o for o in options or [] if o and o.strip() and not _NOT_A_LOC_OPT.search(o) and not re.fullmatch(r"\s*(select|choose)\.*\s*", o, re.I)]


def posted_locations(job_context: str) -> list[str]:
    """Location lines from the top of the posting (where ATS headers put them)."""
    head = (job_context or "")[:800]
    out = []
    for label, pat in LOCATION_RANKING:
        if re.search(pat, head, re.I):
            out.append(label)
    return out


def rank_location_option(options: list[str], job_context: str = "") -> Optional[str]:
    """Single choice: an 'either / any' option, else the posted location, else Bay Area > Chicago > New York > Pittsburgh > LA."""
    opts = [o for o in options or [] if o and not _NOT_A_LOC_OPT.search(o) and not re.fullmatch(r"\s*(select|choose)\.*\s*", o, re.I)]
    for o in opts:
        if _ANY_LOC_OPT.search(o):
            return o
    pats = dict(LOCATION_RANKING)
    order = posted_locations(job_context) + [l for l, _ in LOCATION_RANKING]
    for label in order:
        for o in opts:
            if re.search(pats[label], o, re.I):
                return o
    return None


# ------------------------------------------------------------------ export control (Ryan, 2026-11-09)
EXPORT_CITIZENSHIP_ANSWER = "China, citizen since birth (2006); Guinea-Bissau, permanent resident since 2018"
EXPORT_CITIZENSHIP_COUNTRY = "China"          # citizen since birth (2006)
EXPORT_RESIDENCY_COUNTRY = "Guinea-Bissau"    # permanent resident since 2018
_EMBARGO_NAMES = re.compile(r"north korea|\bdprk\b|syria|\biran\b|cuba|crimea|embargoed|sanctioned countr", re.I)
_EXPORT_CTX = re.compile(r"export|deemed export|\bEAR\b|\bITAR\b|licens", re.I)
_CITIZEN_WORDS = re.compile(r"citizen|nationalit|permanent resid|national of", re.I)
_COUNTRIES_ASK = re.compile(r"countr(y|ies)\s+(of|where)\b.{0,40}(citizen|nationalit|permanent resid)|"
                            r"\b(which|what|list|all|any)\b.{0,80}countr(y|ies)\b", re.I)


_ADULT_Q = re.compile(r"\b(18|eighteen)\s*(years|yrs)?(\s*of\s*age)?\s*(old\s*)?(or|and)\s*(older|over|above)\b|"
                       r"\bat least (18|eighteen)( years)?\b|\bover (the age of )?(18|eighteen)\b|legal (working )?age", re.I)


def is_adult_question(label: str) -> bool:
    """'Are you 18 years of age or older?' Ryan was born in 2006 (his export-control answer), so: Yes."""
    return bool(_ADULT_Q.search(label or ""))


def positive_option(options):
    for o in options or []:
        if re.match(r"\s*(yes|y)\b", str(o), re.I):
            return o
    return None


def is_embargoed_country_question(label: str) -> bool:
    """'Are you a citizen/national/resident of Cuba, Iran, North Korea, Syria ...?' -> No."""
    t = label or ""
    return bool(_EMBARGO_NAMES.search(t) and (_CITIZEN_WORDS.search(t) or re.search(r"resident|reside|born", t, re.I)))


def is_export_citizenship_question(label: str) -> bool:
    """Export-control question asking which countries he is a citizen / permanent resident of (not the embargo yes/no)."""
    t = label or ""
    if is_embargoed_country_question(t) or not _CITIZEN_WORDS.search(t) or not _COUNTRIES_ASK.search(t):
        return False
    return bool(_EXPORT_CTX.search(t) or re.search(r"permanent resid", t, re.I))


def negative_option(options: list[str]) -> Optional[str]:
    """First plain 'No' option (judged on the part before any dash or comma)."""
    for o in options or []:
        head = re.split(r"\s[-–]\s|[,:(]", o, maxsplit=1)[0]
        if re.match(r"^\s*(no|n|i am not|i'm not|not)\b", head, re.I):
            return o
    return None


def country_option(options: list[str], country: str = EXPORT_CITIZENSHIP_COUNTRY) -> Optional[str]:
    rx = re.escape(country).replace(r"\-", "[- ]?").replace("\\-", "[- ]?")
    for o in options or []:
        if re.fullmatch(rf"\s*{rx}\b.*", o, re.I) and not re.search(r"taiwan|hong kong|macau", o, re.I):
            return o
    return None


def export_country_targets(label: str) -> list[str]:
    """Countries for a select-style export question: China (citizen) and/or Guinea-Bissau (permanent resident)."""
    t = label or ""
    asks_cit = bool(re.search(r"citizen|nationalit|national of", t, re.I))
    asks_pr = bool(re.search(r"permanent resid", t, re.I))
    out = []
    if asks_cit or not asks_pr:
        out.append(EXPORT_CITIZENSHIP_COUNTRY)
    if asks_pr:
        out.append(EXPORT_RESIDENCY_COUNTRY)
    return out
