"""Ranking with the approved P2 metric (weights are heuristic, not a real ATS score).

final = 0.50 keyword coverage + 0.20 TF-IDF cosine + 0.10 grad-year fit
      + 0.10 location + 0.05 platform feasibility + 0.05 freshness
Per-company cap defaults to 2; top N defaults to 50. Lever is not scored (dropped upstream).
"""
from __future__ import annotations

import re
from datetime import date, timedelta

LOCATION_ORDER = [("Bay Area", r"san francisco|bay area|palo alto|mountain view|menlo park|sunnyvale|san jose|san mateo|redwood city|"
                               r"foster city|cupertino|santa clara|oakland|berkeley|los altos|burlingame|south san francisco"),
                  ("Chicago", r"chicago"), ("New York", r"new york|nyc|brooklyn|manhattan"), ("Pittsburgh", r"pittsburgh"),
                  ("Los Angeles", r"los angeles|santa monica|irvine|culver city|pasadena|el segundo")]
LOCATION_SCORE = {
    "Bay Area": 1.0, "Chicago": 0.85, "New York": 0.70, "Pittsburgh": 0.55,
    "Los Angeles": 0.40, "Remote": 0.25, "other": 0.10,
}
# auto-apply feasibility from batches 2-3 (1.0 = submits unattended; lower = known blockers)
# Lever is omitted: those URLs are dropped at scrape and in P2 prefilter.
FEASIBILITY = {"greenhouse": (1.0, "unattended submits worked (5/6 in batch 3 incl. Nex retry)"),
               "ashby": (0.9, "unattended submits worked; some forms need transcript / AI consent"),
               "workday": (0.6, "needs a per-company account + email verification"),
               "icims": (0.3, "account + not yet supported by the runner"), "oracle": (0.3, "account + not yet supported"),
               "smartrecruiters": (0.1, "DataDome bot wall"), "workable": (0.4, "not yet tested"), "other": (0.3, "not yet tested")}
WEIGHTS = {"keyword": 0.50, "tfidf": 0.20, "grad_year": 0.10, "location": 0.10, "platform": 0.05, "freshness": 0.05}
FRESHNESS_HORIZON_DAYS = 45
UNKNOWN_FRESHNESS = 0.5
COMPANY_CAPS = {"rivian": (5, 90)}  # company key -> (max applications, window days)
DEFAULT_CAP_WINDOW_DAYS = 90  # default per-company cap (2) also uses this window
_MONTHS = {name.lower(): i for i, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
_MONTHS.update({name[:3].lower(): i for name, i in list(_MONTHS.items())})


def location_rank(location: str) -> tuple[int, str]:
    for i, (name, rx) in enumerate(LOCATION_ORDER):
        if re.search(rx, location or "", re.I):
            return i, name
    if re.search(r"remote", location or "", re.I):
        return len(LOCATION_ORDER), "Remote"
    return len(LOCATION_ORDER) + 1, "other"


def location_score(location: str) -> tuple[float, str, int]:
    idx, name = location_rank(location)
    return LOCATION_SCORE[name], name, idx


def freshness_days(listed_age: str, today: date | None = None) -> int | None:
    """Parse feed listed_age values ('0d', '2w', '1mo', 'Nov 09') to days since posting."""
    today = today or date.today()
    s = (listed_age or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d+)\s*d(?:ays?)?", s, re.I)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d+)\s*w(?:eeks?)?", s, re.I)
    if m:
        return int(m.group(1)) * 7
    m = re.fullmatch(r"(\d+)\s*mo(?:nths?)?", s, re.I)
    if m:
        return int(m.group(1)) * 30
    m = re.fullmatch(r"(\d+)\s*y(?:rs?|ears?)?", s, re.I)
    if m:
        return int(m.group(1)) * 365
    m = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{1,2})(?:,?\s*(\d{4}))?", s)
    if m and m.group(1).lower() in _MONTHS:
        month, day = _MONTHS[m.group(1).lower()], int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            posted = date(year, month, day)
        except ValueError:
            return None
        if m.group(3) is None and posted > today:
            posted = date(year - 1, month, day)
        return max(0, (today - posted).days)
    return None


def freshness_score(listed_age: str, today: date | None = None) -> tuple[float, int | None]:
    days = freshness_days(listed_age, today)
    if days is None:
        return UNKNOWN_FRESHNESS, None
    return max(0.0, min(1.0, 1.0 - days / FRESHNESS_HORIZON_DAYS)), days


def final_score(keyword: float, tfidf: float, grad_year: float, location: float,
                platform: float, freshness: float) -> float:
    return (WEIGHTS["keyword"] * keyword + WEIGHTS["tfidf"] * tfidf + WEIGHTS["grad_year"] * grad_year
            + WEIGHTS["location"] * location + WEIGHTS["platform"] * platform + WEIGHTS["freshness"] * freshness)


def cap_limit(company: str, per_company: int = 2) -> tuple[int, int]:
    """Return (max applications, window days) for this company."""
    key = re.sub(r"[^a-z]", "", (company or "").lower())
    for k, (n, days) in COMPANY_CAPS.items():
        if k in key:
            return n, days
    return per_company, DEFAULT_CAP_WINDOW_DAYS


def recent_applied(applied_dates: list[date], today: date | None, window_days: int) -> list[date]:
    today = today or date.today()
    cutoff = today - timedelta(days=window_days)
    return [d for d in applied_dates if d >= cutoff]


def cap_ok(company: str, applied_dates: list[date], today: date | None = None,
           per_company: int = 2) -> tuple[bool, str]:
    """False when tracker applications in the window already fill the per-company cap."""
    n, days = cap_limit(company, per_company)
    recent = recent_applied(applied_dates, today, days)
    if len(recent) >= n:
        return False, f"cap reached: {len(recent)}/{n} applications in {days} days"
    return True, f"{len(recent)}/{n} in {days} days"


def rank(cands: list[dict], top_n: int = 50, per_company: int = 2, today: date | None = None) -> list[dict]:
    """cands need: skill_coverage or fit.skill_coverage, text_similarity, grad_year_score,
    location, ats, listed_age, company, applied_dates. Ineligible, Lever, and same-posting
    tracker duplicates must already be gone. The per-company cap counts tracker applications
    in the cap window as well as jobs taken in this run."""
    today = today or date.today()
    for c in cands:
        fit = c.get("fit") or {}
        keyword = c.get("skill_coverage", fit.get("skill_coverage", 0.0))
        tfidf = c.get("text_similarity", fit.get("text_similarity", 0.0))
        loc_s, loc_name, loc_i = location_score(c.get("location", ""))
        ats = c.get("ats", "other")
        f, fwhy = FEASIBILITY.get(ats, FEASIBILITY["other"])
        fresh, fresh_days = freshness_score(c.get("listed_age", ""), today)
        grad = c.get("grad_year_score", 0.6)
        c["location_pref"], c["location_rank"], c["location_score"] = loc_name, loc_i, loc_s
        c["feasibility"], c["feasibility_note"] = f, fwhy
        c["freshness"], c["freshness_days"] = fresh, fresh_days
        n, days = cap_limit(c.get("company", ""), per_company)
        used = len(recent_applied(c.get("applied_dates", []), today, days))
        c["tracker_applied"], c["cap_limit"], c["cap_window_days"] = used, n, days
        ok, why = cap_ok(c.get("company", ""), c.get("applied_dates", []), today, per_company)
        c["cap_ok"], c["cap_note"] = ok, why
        c["rank_score"] = round(final_score(keyword, tfidf, grad, loc_s, f, fresh), 3)
    pool = [c for c in cands if c["cap_ok"]]
    pool.sort(key=lambda c: (-c["rank_score"], c["location_rank"], -c["feasibility"]))
    out, seen = [], {}
    for c in pool:
        k = c.get("company", "").lower()
        n = c.get("cap_limit", per_company)
        used = c.get("tracker_applied", 0)
        if used + seen.get(k, 0) >= n:
            continue
        seen[k] = seen.get(k, 0) + 1
        out.append(c)
        if len(out) >= top_n:
            break
    for i, c in enumerate(out, 1):
        c["rank"] = i
    return out
