"""Read-only JD fetchers for public ATS job-board APIs (no login, no form opened)."""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request

H = {"User-Agent": "Mozilla/5.0 Ryan-Local-Internship-Workflow/0.2", "Accept": "application/json"}


def _get(u: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=25) as r:
        return json.loads(r.read().decode())


def text(h: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(h or "")))).strip()


def greenhouse(u: str) -> dict:
    p = urllib.parse.urlsplit(u); q = dict(urllib.parse.parse_qsl(p.query))
    jid = q.get("gh_jid") or q.get("token") or re.search(r"/jobs/(\d+)", p.path).group(1)
    board = q.get("for") or [b for b in p.path.split("/") if b and b not in ("embed", "job_app", "jobs")][0]
    d = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}?questions=true")
    qs = " | ".join(x.get("label", "") for x in d.get("questions", []))
    return dict(title=d["title"], location=(d.get("location") or {}).get("name", ""), jd=text(d.get("content")),
                questions=qs, apply_url=f"https://job-boards.greenhouse.io/{board}/jobs/{jid}", id=str(jid), open=True)


def ashby(u: str) -> dict:
    p = urllib.parse.urlsplit(u).path.strip("/").split("/"); org, jid = p[0], p[1]
    d = _get(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true")
    j = next((x for x in d["jobs"] if x["id"] == jid), None)
    if j is None:
        return dict(open=False, title="", location="", jd="", apply_url=u, id=jid)
    pay = (j.get("compensation") or {}).get("compensationTierSummary") or ""
    return dict(title=j["title"], location=j.get("location", ""), jd=text(j.get("descriptionHtml")), pay=pay, questions="",
                apply_url=f"https://jobs.ashbyhq.com/{org}/{jid}/application", id=jid, open=j.get("isListed", True) is not False)


def workday(u: str) -> dict:
    p = urllib.parse.urlsplit(u); host = p.netloc; parts = p.path.strip("/").split("/")
    if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        parts = parts[1:]
    site, tenant, jobpath = parts[0], host.split(".")[0], "/".join(parts[1:])
    i = _get(f"https://{host}/wday/cxs/{tenant}/{site}/{jobpath}")["jobPostingInfo"]
    return dict(title=i["title"], location=i.get("location", ""), jd=text(i.get("jobDescription")), pay="", questions="",
                apply_url=f"https://{host}/{site}/{jobpath}", id=i.get("jobReqId", ""), open=i.get("canApply") is not False)


FETCHERS = {"greenhouse": greenhouse, "ashby": ashby, "workday": workday}


def is_lever_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return host == "lever.co" or host.endswith(".lever.co")


def ats_of(url: str) -> str:
    u = (url or "").lower()
    for k, rx in (("greenhouse", r"greenhouse\.io"), ("ashby", r"ashbyhq\.com"), ("lever", r"lever\.co"),
                  ("workday", r"myworkdayjobs\.com|myworkdaysite\.com"), ("icims", r"icims\.com"),
                  ("oracle", r"oraclecloud\.com"), ("smartrecruiters", r"smartrecruiters\.com"), ("workable", r"workable\.com")):
        if re.search(rx, u):
            return k
    return "other"


def fetch(url: str) -> dict:
    if is_lever_url(url):
        raise ValueError("Lever postings are not fetched")
    a = ats_of(url)
    if a not in FETCHERS:
        raise ValueError(f"no JD fetcher for {a}")
    d = FETCHERS[a](url)
    d["ats"] = a
    return d


def pay_from(jd: str) -> str:
    m = re.findall(r"\$\s?\d[\d,.]*(?:\s?[kK])?(?:\s?(?:-|–|to)\s?\$?\s?\d[\d,.]*(?:\s?[kK])?)?\s?(?:/|per|an|a)?\s?(?:hour|hr|year|annually|month|mo)\b", jd or "")
    return "; ".join(dict.fromkeys(m))[:120]
