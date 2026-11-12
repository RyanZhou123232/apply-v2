"""P2 prototype: feed rows -> JD fetch (read-only APIs) -> eligibility -> direction/template -> fit -> rank.

python -m apply.p2.run --feeds feed_jobs.json --tracker tech投递2027.xlsx --resume-text resume.txt \
    --all-info all_info.txt --out candidates.json --top 50 --max-fetch 200 --jd-cache jd_cache

Outputs a ranked candidate list (JSON + CSV). It never opens an application form and never submits.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from . import eligibility, fit, jd_fetch, rank, selector, tracker_read

RELEVANT = re.compile(r"software|engineer|developer|\bswe\b|machine learning|\bml\b|\bai\b|data|analytics|scien|backend|frontend|"
                      r"full.?stack|platform|infrastructure|research", re.I)
EXCLUDE = re.compile(r"\b(phd|ph\.d|mba|hardware|mechanical|electrical|civil|chemical|manufacturing|sales|marketing|recruit|legal|"
                     r"finance intern|accounting|design intern|product design)\b", re.I)


def prefilter(rows: list[dict], tracker: str | None, dropped: list[dict] | None = None) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        if r.get("closed") or not RELEVANT.search(r.get("title", "")) or EXCLUDE.search(r.get("title", "")):
            continue
        key = (r.get("company", "").lower(), re.sub(r"\W", "", r.get("title", "").lower()))
        if key in seen:
            continue
        seen.add(key)
        r = dict(r, ats=jd_fetch.ats_of(r.get("apply_url", "")))
        if r["ats"] == "lever" or jd_fetch.is_lever_url(r.get("apply_url", "")):
            continue
        if tracker:
            hits = tracker_read.find_dup(tracker, r["company"], r["title"], r["apply_url"])
            if hits:
                if dropped is not None:
                    dropped.append({"company": r["company"], "title": r.get("title", ""), "url": r.get("apply_url", ""),
                                    "status": "already_applied",
                                    "reasons": [f"{why} (tracker row {rn}: {co} / {role})" for rn, why, co, role in hits[:4]]})
                continue
        out.append(r)
    return out



def _cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / (hashlib.sha256((url or "").encode()).hexdigest()[:24] + ".json")


def load_or_fetch(url: str, cache_dir: Path | None) -> dict:
    if cache_dir:
        path = _cache_path(cache_dir, url)
        if path.exists():
            return json.loads(path.read_text())
    d = jd_fetch.fetch(url)
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache_path(cache_dir, url).write_text(json.dumps(d, ensure_ascii=False))
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeds", required=True)
    ap.add_argument("--tracker")
    ap.add_argument("--resume-text", required=True, help="text of the template(s); one file, or a JSON {direction: path}")
    ap.add_argument("--all-info", required=True)
    ap.add_argument("--out", default="candidates.json")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--max-fetch", type=int, default=120)
    ap.add_argument("--ats", default="greenhouse,ashby,workday")
    ap.add_argument("--exclude-urls", help="file with one URL per line already attempted (not submitted) to skip")
    ap.add_argument("--jd-cache", help="directory to reuse previously fetched JDs")
    ap.add_argument("--per-company", type=int, default=2)
    a = ap.parse_args(argv)
    rows = json.loads(Path(a.feeds).read_text())
    rt = Path(a.resume_text)
    templates = {k: Path(v).read_text() for k, v in json.loads(rt.read_text()).items()} if rt.suffix == ".json" else {}
    default_text = "" if templates else rt.read_text()
    evidence = Path(a.all_info).read_text()
    want_ats = set(a.ats.split(","))
    skip = set()
    if a.exclude_urls and Path(a.exclude_urls).exists():
        skip = {re.sub(r"[?#].*$", "", u.strip()).rstrip("/") for u in Path(a.exclude_urls).read_text().splitlines() if u.strip()}
    already = []
    pre = [r for r in prefilter(rows, a.tracker, already) if r["ats"] in want_ats
           and re.sub(r"[?#].*$", "", r["apply_url"]).rstrip("/").removesuffix("/application").removesuffix("/apply") not in
           {u.removesuffix("/application").removesuffix("/apply") for u in skip}]
    # spread fetches across platforms so every ATS is represented
    by = {}
    for r in pre:
        by.setdefault(r["ats"], []).append(r)
    order = [x for grp in zip(*[iter(v) for v in by.values()]) for x in grp] if by else []
    order += [r for r in pre if r not in order]
    stats = {"feed_rows": len(rows), "after_prefilter": len(pre), "fetched": 0, "fetch_failed": 0,
             "eligible": 0, "pending": 0, "ineligible": 0, "already_applied": len(already)}
    cands, rejected = [], list(already)
    for r in order[: a.max_fetch]:
        try:
            d = load_or_fetch(r["apply_url"], Path(a.jd_cache) if a.jd_cache else None)
            stats["fetched"] += 1
        except Exception as e:
            stats["fetch_failed"] += 1
            rejected.append({"company": r["company"], "title": r["title"], "url": r["apply_url"], "status": "fetch_failed",
                             "reasons": [f"{type(e).__name__}: {str(e)[:80]}"]})
            continue
        if EXCLUDE.search(d["title"] or ""):
            rejected.append({"company": r["company"], "title": d["title"], "url": d["apply_url"], "status": "ineligible",
                             "reasons": ["title outside SWE/AI/Data or graduate-only"]})
            continue
        v = eligibility.check(d["title"] or r["title"], d["jd"], d.get("location") or r.get("location", ""),
                              d.get("questions", ""), d.get("open", True))
        stats[v.status] += 1
        base = {"company": r["company"], "role": d["title"] or r["title"], "url": d["apply_url"], "ats": d["ats"],
                "location": d.get("location") or r.get("location", ""), "pay": d.get("pay") or jd_fetch.pay_from(d["jd"]),
                "feed": r.get("feed", ""), "listed_age": r.get("listed_age", ""),
                "eligibility": v.status, "eligibility_reasons": v.reasons, "flags": v.flags}
        if v.status == eligibility.INELIGIBLE:
            rejected.append(dict(base, status="ineligible"))
            continue
        if a.tracker:
            hits = tracker_read.find_dup(a.tracker, base["company"], base["role"], base["url"] or r["apply_url"])
            if hits:
                stats["already_applied"] += 1
                rejected.append(dict(base, status="already_applied",
                                     eligibility_reasons=[f"{why} (tracker row {rn}: {co} / {role})"
                                                          for rn, why, co, role in hits[:4]]))
                continue
        direc, why = selector.direction(base["role"], d["jd"])
        pdf, docx, grad_on_resume, twhy, tkey = selector.choose_template(direc, base["role"], d["jd"])
        ttext = templates.get(tkey) or templates.get(direc, default_text)
        f = fit.score(d["jd"], base["role"], ttext, evidence)
        need, nwhy = selector.needs_tailoring(f, base["company"], direc)
        prior = tracker_read.company_rows(a.tracker, base["company"]) if a.tracker else []
        if prior:
            base["flags"] = base["flags"] + [f"already applied to {base['company']} (tracker rows {', '.join(str(p[0]) for p in prior[:4])})"]
        _, gscore, greason = eligibility.grad_year_fit(base["role"], d["jd"])
        cands.append(dict(base, direction=direc, direction_reason=why, template_pdf=pdf, template_docx=docx,
                          needs_tailoring=need, tailoring_reason=nwhy, score=f["score"], fit=f,
                          grad_year_score=gscore, grad_year_reason=greason,
                          grad_year_on_resume=grad_on_resume, template_reason=twhy, template_key=tkey,
                          applied_dates=tracker_read.applied_dates(a.tracker, base["company"]) if a.tracker else []))
        time.sleep(0.2)
    ready = [c for c in cands if c["eligibility"] == eligibility.ELIGIBLE]
    pend = [c for c in cands if c["eligibility"] == eligibility.PENDING]
    ranked = rank.rank(ready, a.top, a.per_company)
    for c in ranked + pend:
        c["applied_dates"] = [str(x) for x in c["applied_dates"]]
    def _year_split(items):
        split = {"2028": 0, "2029": 0, "SWE_2028": 0, "SWE_2029": 0, "other_2028": 0}
        for c in items:
            y = int(c.get("grad_year_on_resume") or 0)
            if y == 2028:
                split["2028"] += 1
            elif y == 2029:
                split["2029"] += 1
            if c.get("direction") == "SWE" and y in (2028, 2029):
                split[f"SWE_{y}"] += 1
            elif y == 2028:
                split["other_2028"] += 1
        return split

    stats["non_summer_dropped"] = sum(
        1 for r in rejected
        if any("non-summer role" in str(x) for x in (r.get("reasons") or r.get("eligibility_reasons") or []))
    )
    stats["template_split_ranked"] = _year_split(ranked)
    stats["template_split_eligible"] = _year_split(ready)
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "stats": stats, "ranked": ranked,
           "pending_transcript": pend, "rejected": rejected, "note": "P2 prototype: nothing was opened or submitted."}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    with open(Path(a.out).with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "company", "role", "ats", "location", "pay", "listed_age", "rank_score", "score",
                    "skill_coverage", "text_similarity", "grad_year_score", "location_score", "feasibility", "freshness",
                    "direction", "grad_year_on_resume", "template_pdf", "needs_tailoring", "tailoring_reason",
                    "location_pref", "flags", "url"])
        for c in ranked:
            w.writerow([c["rank"], c["company"], c["role"], c["ats"], c["location"], c["pay"], c.get("listed_age", ""),
                        c["rank_score"], c["score"], c["fit"]["skill_coverage"], c["fit"]["text_similarity"],
                        c.get("grad_year_score"), c.get("location_score"), c["feasibility"], c.get("freshness"),
                        c["direction"], c.get("grad_year_on_resume"), c["template_pdf"], c["needs_tailoring"],
                        c["tailoring_reason"], c["location_pref"],
                        "; ".join(c["flags"]), c["url"]])
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
