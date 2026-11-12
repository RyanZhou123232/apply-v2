"""Text-only eligibility pre-filter (runs on JD text after scraping, before any form is opened).

FORM_ANSWERS eligibility: apply unless the JD requires Master's/PhD students, U.S. person / citizenship,
security clearance, ITAR, or green card / permanent residency. Generic "no sponsorship" statements are only a
flag (Ryan answers sponsorship questions truthfully); an explicit exclusion of F-1 / CPT / OPT is ineligible.
Transcript-required jobs are PENDING (Ryan uploads transcripts himself). EEO boilerplate ("without regard to
... citizenship status") is ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ELIGIBLE, PENDING, INELIGIBLE = "eligible", "pending", "ineligible"

_HARD = [
    ("U.S. person / export-control requirement", r"\bmust be (a )?U\.?\s?S\.?\s+person|\bU\.?\s?S\.?\s+persons? (only|status (is )?required)|"
                                                 r"qualif\w+ as a U\.?\s?S\.?\s+person|\bITAR\b"),
    ("U.S. citizenship required", r"(must|required to) (be|hold) (a )?U\.?\s?S\.?\s+citizen|U\.?\s?S\.?\s+citizenship (is )?(required|mandatory)|"
                                  r"\brequires? U\.?\s?S\.?\s+citizenship|only U\.?\s?S\.?\s+citizens"),
    ("security clearance", r"(active|current|obtain|eligib\w+ (for|to obtain)|ability to obtain|hold) (an? )?(\w+ )?(security )?clearance|"
                           r"\b(secret|top secret|TS/SCI)\b clearance"),
    ("green card / permanent residency required", r"(green card|permanent resident)[^.]{0,40}(required|only|must)|must be (a )?(U\.?S\.? citizen or )?(lawful )?permanent resident"),
    ("graduate-students-only", r"(master'?s|ph\.?d\.?|doctoral|graduate) (students?|candidates?) only|"
                               r"must be (currently )?(pursuing|enrolled in) (a |an )?(master|ph\.?d|graduate)|"
                               r"currently pursuing (a |an )?(master'?s|ph\.?d)[^.]{0,40}(required|only)|seeking a ph\.?d\.? (research )?intern|"
                               r"^[^.]{0,80}\b(ph\.?d|doctoral)\b[^.]{0,20}\."),
    ("F-1 / CPT explicitly excluded", r"(unable|not able|cannot|can't|do not|don't|will not|won't) (to )?(sponsor|accept|support)[^.]{0,80}\b(F-?1|CPT|OPT)\b|"
                                      r"\b(F-?1|CPT|OPT)\b[^.]{0,60}(not eligible|ineligible|cannot be considered)|"
                                      r"ongoing, permanent authorization to work"),
]
_EEO = re.compile(r"without regard to[^.]{0,300}|regardless of[^.]{0,300}|equal (employment )?opportunity[^.]{0,400}", re.I)
_TRANSCRIPT = re.compile(r"\btranscripts?\b", re.I)
_TRANSCRIPT_OPTIONAL = re.compile(r"transcripts?[^.]{0,40}(optional|if available|not required)|(optional|if available)[^.]{0,30}transcripts?", re.I)
_NO_SPONSOR = re.compile(r"(not|unable to|cannot|won'?t|will not) (provide |offer )?(visa )?sponsor|without (the need for )?(current or future )?(visa |employer )?sponsorship", re.I)
_NON_US = re.compile(r"\b(uk|u\.k\.|england|scotland|bristol|manchester|edinburgh|amsterdam|paris|france|netherlands|canada|ontario|mississauga|toronto|vancouver|montreal|united kingdom|london|india|bangalore|bengaluru|germany|berlin|singapore|"
                     r"mexico|brazil|poland|ireland|dublin|israel|tel aviv|china|japan|tokyo|australia|sydney)\b", re.I)
_SUMMER27 = re.compile(r"summer\s*(of\s*)?2027|2027\s*summer|\b2027\b[^.]{0,40}intern|intern[^.]{0,60}\b2027\b", re.I)
_OFFCYCLE = re.compile(r"\b(fall|spring|winter|off-?cycle|co-?ops?)\b", re.I)
_SUMMER = re.compile(r"\bsummer(?:20\d{2})?\b|\bsummer[-\s]?20\d{2}", re.I)
_JAN_START = re.compile(r"\bjan(?:uary)?(?:\s*[-./]\s*|\s+)(?:20)?2\d\b|\bjan(?:uary)?\s+(?:start|intern)", re.I)
_REMOTE_LOC = re.compile(r"\bremote\b", re.I)
_PITTSBURGH = re.compile(r"pittsburgh", re.I)
_REMOTE_JD = re.compile(
    r"\b(fully\s+remote|remote\s+(?:role|position|eligible|option|ok|okay)|open to remote|work from home|"
    r"this (?:role|position|internship) is remote|remote or hybrid|hybrid or remote)\b",
    re.I,
)
_PITTSBURGH_JD = re.compile(
    r"pittsburgh,?\s*pa\b|pittsburgh,?\s*pennsylvania\b|"
    r"(?:located in|based in|office(?:s)?(?: in|:)|locations?(?: in|:)|hybrid(?: in|:)?|on-?site(?: in|:)?)"
    r"[^.]{0,40}pittsburgh",
    re.I,
)

@dataclass
class Verdict:
    status: str
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def check(title: str, jd: str, location: str = "", questions: str = "", open_: bool = True) -> Verdict:
    body = _EEO.sub(" ", jd or "")
    hay = f"{title} . {body}"
    v = Verdict(ELIGIBLE)
    if not open_:
        v.reasons.append("posting closed / not accepting applications")
    for why, rx in _HARD:
        m = re.search(rx, hay, re.I)
        if m:
            v.reasons.append(f"{why}: \"{hay[max(0, m.start() - 50): m.end() + 50].strip()}\"")
    if location and _NON_US.search(location) and not re.search(r"\b(US|USA|United States|Remote)\b", location):
        v.reasons.append(f"non-US location: {location}")
    kind = term_kind(title, body)
    if kind == "non_summer":
        ok, where = remote_or_pittsburgh(location, body)
        if ok:
            v.flags.append(f"non-summer term allowed ({where})")
        else:
            v.reasons.append(
                "non-summer role (co-op / off-cycle / winter / spring / fall / January start) "
                f"is not remote or in Pittsburgh: {location or 'location unspecified'}"
            )
    elif kind != "summer" and not re.search(r"summer", f"{title} {body[:6000]}", re.I):
        v.flags.append("no Summer 2027 mention")
    if _NO_SPONSOR.search(body):
        v.flags.append("says no visa sponsorship (answer truthfully; not a blocker for CPT internships)")
    g_status, _, g_reason = grad_year_fit(title, body)
    if g_status == INELIGIBLE:
        v.reasons.append(g_reason)
    if v.reasons:
        v.status = INELIGIBLE
        return v
    tq = f"{body} {questions}"
    if _TRANSCRIPT.search(tq) and not _TRANSCRIPT_OPTIONAL.search(tq):
        v.status = PENDING
        v.reasons.append("transcript required (Ryan uploads it himself)")
    return v



def term_kind(title: str, jd: str = "") -> str:
    """Classify the internship term: 'summer', 'non_summer', or 'unspecified'.

    A title that is clearly a summer internship (including 'Summer 2027 Internship/Co-op')
    counts as summer. Co-op / off-cycle / winter / spring / fall / January starts without
    summer are non_summer.
    """
    title_h, jd_h = title or "", jd or ""
    # Only the word "summer" marks a summer term. _SUMMER27 also matches "Intern ... 2027"
    # (e.g. "Intern - (Jan 2027)"), which is not a summer internship.
    if _SUMMER.search(title_h):
        return "summer"
    if _OFFCYCLE.search(title_h) or _JAN_START.search(title_h):
        return "non_summer"
    hay = jd_h[:6000]
    if _SUMMER.search(hay):
        return "summer"
    if _OFFCYCLE.search(hay) or _JAN_START.search(hay):
        return "non_summer"
    return "unspecified"


def remote_or_pittsburgh(location: str, jd: str = "") -> tuple[bool, str]:
    """Non-summer roles are only eligible if they can be done remotely or in Pittsburgh (onsite or hybrid)."""
    loc = location or ""
    if _REMOTE_LOC.search(loc):
        return True, "remote"
    if _PITTSBURGH.search(loc):
        return True, "Pittsburgh (onsite or hybrid)"
    head = jd or ""
    if _REMOTE_JD.search(head):
        return True, "remote (from JD)"
    if _PITTSBURGH_JD.search(head):
        return True, "Pittsburgh (from JD)"
    return False, ""


# Ryan graduates May 2028 and prefers sophomore / class-of-2029 programs.
# A JD that only accepts 2026/2027 graduates is ineligible (filtered here, not scored).
GRAD_BASE = 0.6
GRAD_SOPHOMORE = 1.0
_GRAD_CTX = re.compile(
    r"(?:graduat(?:e|ing|ion|es)|class of|expected (?:to )?graduate|"
    r"graduation (?:date|year|window|by)|degree (?:by|in|before|completion))"
    r"[^.]{0,80}\b(202[6-9])\b|"
    r"\b(202[6-9])\b[^.]{0,40}(?:graduat|class of)",
    re.I,
)
_SOPHOMORE = re.compile(r"\bsophomores?(?:\s+program)?\b|rising sophomores?", re.I)
# Internship-term years ("Summer 2027 internship") must not be read as graduation years,
# including when the same sentence later says "graduation".
_TERM_YEAR = re.compile(
    r"(?:summer|fall|spring|winter|intern(?:ship)?(?:\s+program)?)\s*(?:of\s*)?\b(202[6-9])\b|"
    r"\b(202[6-9])\b\s*(?:summer|fall|spring|winter)\s+(?:intern|internship)",
    re.I,
)


def _mask_term_years(text: str) -> str:
    return _TERM_YEAR.sub(" ", text or "")


def graduation_years(title: str, jd: str) -> set[int]:
    years: set[int] = set()
    hay = _mask_term_years(f"{title or ''} . {jd or ''}")
    for m in _GRAD_CTX.finditer(hay):
        y = m.group(1) or m.group(2)
        if y:
            years.add(int(y))
    return years


def grad_year_fit(title: str, jd: str) -> tuple[str, float, str]:
    """Return (status, score, reason). status is ELIGIBLE or INELIGIBLE.

    2028 accepted or unspecified -> 0.6; 2029 / sophomore program -> 1.0.
    Internship-term mentions such as 'Summer 2027' are not treated as graduation years.
    """
    years = graduation_years(title, jd)
    soph = bool(_SOPHOMORE.search(f"{title or ''} {jd or ''}"))
    if years and not (years & {2028, 2029}) and (years & {2026, 2027}):
        shown = "/".join(str(y) for y in sorted(years))
        return INELIGIBLE, 0.0, f"graduation window only accepts {shown} (Ryan graduates May 2028)"
    if soph or 2029 in years:
        return ELIGIBLE, GRAD_SOPHOMORE, "2029 / sophomore program"
    if 2028 in years:
        return ELIGIBLE, GRAD_BASE, "accepts 2028 graduates"
    return ELIGIBLE, GRAD_BASE, "graduation year unspecified"
