from datetime import date, timedelta

from apply.p2 import eligibility, fit, jd_fetch, rank, run, selector, tracker_read


def test_eligibility_rejects_and_pending():
    v = eligibility.check("Software Engineer Intern", "Applicants must be U.S. citizens due to ITAR. Summer 2027.", "Austin, TX")
    assert v.status != "eligible" and v.reasons
    v = eligibility.check("Data Intern", "Summer 2027 internship. Please upload your unofficial transcript.", "New York, NY")
    assert v.status == "pending"
    # EEO boilerplate must not reject
    v = eligibility.check("SWE Intern", "Summer 2027. We do not discriminate on the basis of race, citizenship status, or national origin.", "Chicago, IL")
    assert v.status == "eligible", v


def test_graduation_window():
    # Internship term 2027 is not a graduation constraint
    st, sc, why = eligibility.grad_year_fit("SWE Intern", "Summer 2027 internship in New York.")
    assert st == eligibility.ELIGIBLE and sc == eligibility.GRAD_BASE and "unspecified" in why
    # 2027-only graduates are ineligible, not scored
    v = eligibility.check("SWE Intern", "Applicants must be graduating in December 2027. Summer 2027 internship.", "NYC")
    assert v.status == eligibility.INELIGIBLE
    assert any("2027" in r and "May 2028" in r for r in v.reasons)
    # 2028 accepted = base score
    st, sc, why = eligibility.grad_year_fit("SWE Intern", "Open to students graduating in 2028.")
    assert st == eligibility.ELIGIBLE and sc == eligibility.GRAD_BASE
    # 2027 or 2028 still eligible
    st, sc, _ = eligibility.grad_year_fit("SWE Intern", "Candidates graduating in 2027 or 2028.")
    assert st == eligibility.ELIGIBLE and sc == eligibility.GRAD_BASE
    # 2029 / sophomore = 1.0
    st, sc, why = eligibility.grad_year_fit("SWE Intern", "Sophomore program for the class of 2029.")
    assert st == eligibility.ELIGIBLE and sc == eligibility.GRAD_SOPHOMORE and "sophomore" in why.lower()
    st, sc, _ = eligibility.grad_year_fit("Rising Sophomore Intern", "Summer 2027 internship, no graduation year stated.")
    assert st == eligibility.ELIGIBLE and sc == eligibility.GRAD_SOPHOMORE


def test_rivian_cap():
    today = date(2026, 11, 10)
    ok, _ = rank.cap_ok("Rivian", [today - timedelta(days=i) for i in range(4)], today)
    assert ok
    ok, why = rank.cap_ok("Rivian Automotive", [today - timedelta(days=i) for i in range(5)], today)
    assert not ok and "5/5" in why
    ok, _ = rank.cap_ok("Rivian", [today - timedelta(days=100)] * 9, today)
    assert ok


def test_direction():
    assert selector.direction("Data Engineering Intern")[0].lower().startswith("data")
    assert selector.direction("Machine Learning Intern")[0].lower().startswith("ai")


def test_fit_has_no_direction_term():
    out = fit.score("Python SQL Spark data pipelines", "Data Engineer Intern",
                    "Python SQL pandas", "Python SQL Spark pandas")
    assert "direction_ok" not in out
    assert 0 <= out["score"] <= 1
    assert out["score"] == round(fit.text_fit(out["skill_coverage"], out["text_similarity"]), 3)
    assert "spark" in out["evidence_gaps"] or "spark" in out["matched"]


def test_location_and_freshness_and_weights():
    assert rank.location_score("San Francisco, CA")[0] == 1.0
    assert rank.location_score("Chicago, IL")[1] == "Chicago"
    assert rank.location_score("Remote - US")[1] == "Remote"
    assert rank.location_score("Bellevue, Washington")[1] == "other"
    today = date(2026, 11, 10)
    assert rank.freshness_days("0d", today) == 0
    assert rank.freshness_days("2w", today) == 14
    assert rank.freshness_days("1mo", today) == 30
    assert rank.freshness_days("Nov 09", today) == 1
    assert rank.freshness_days("Oct 30", today) == 11
    assert rank.freshness_score("0d", today)[0] == 1.0
    assert rank.freshness_score("", today)[0] == rank.UNKNOWN_FRESHNESS
    # weights sum to 1
    assert abs(sum(rank.WEIGHTS.values()) - 1.0) < 1e-9
    s = rank.final_score(1, 1, 1, 1, 1, 1)
    assert s == 1.0
    s = rank.final_score(1, 0, 0.6, 1.0, 1.0, 1.0)
    assert abs(s - (0.50 + 0.10 * 0.6 + 0.10 + 0.05 + 0.05)) < 1e-9


def test_rank_per_company_cap_and_no_lever_weight():
    assert "lever" not in rank.FEASIBILITY
    today = date(2026, 11, 10)
    cands = []
    for i, (company, loc, ats, age) in enumerate([
        ("Acme", "San Francisco, CA", "greenhouse", "0d"),
        ("Acme", "Chicago, IL", "ashby", "1d"),
        ("Acme", "Austin, TX", "workday", "2d"),  # third role at same company should be dropped
        ("Beta", "New York, NY", "greenhouse", "0d"),
    ]):
        cands.append({
            "company": company, "role": f"Intern {i}", "location": loc, "ats": ats,
            "listed_age": age, "skill_coverage": 1.0, "text_similarity": 0.8,
            "grad_year_score": 0.6, "applied_dates": [], "flags": [],
            "fit": {"skill_coverage": 1.0, "text_similarity": 0.8},
        })
    out = rank.rank(cands, top_n=50, per_company=2, today=today)
    acme = [c for c in out if c["company"] == "Acme"]
    assert len(acme) == 2
    assert all(c["company"] != "Acme" or c["location_pref"] != "other" for c in out)
    assert out[0]["rank"] == 1
    assert "rank_score" in out[0]


def test_prefilter_drops_lever_and_fetcher_refuses():
    rows = [
        {"company": "Zoox", "title": "Software Engineer Intern", "apply_url": "https://jobs.lever.co/zoox/abc/apply",
         "location": "SF", "closed": False},
        {"company": "Acme", "title": "Software Engineer Intern", "apply_url": "https://job-boards.greenhouse.io/acme/jobs/1",
         "location": "SF", "closed": False},
        {"company": "EUCo", "title": "Data Intern", "apply_url": "https://jobs.eu.lever.co/euco/xyz",
         "location": "NYC", "closed": False},
    ]
    out = run.prefilter(rows, tracker=None)
    assert len(out) == 1 and out[0]["company"] == "Acme"
    assert jd_fetch.is_lever_url("https://jobs.lever.co/zoox/abc")
    assert jd_fetch.ats_of("https://jobs.lever.co/zoox/abc") == "lever"
    try:
        jd_fetch.fetch("https://jobs.lever.co/zoox/abc")
        assert False, "should refuse"
    except ValueError as e:
        assert "Lever" in str(e)
    assert "lever" not in jd_fetch.FETCHERS


def test_runner_skips_lever_without_browser():
    from apply.runner import LEVER_SKIP_REASON, run
    from apply import rules
    res = run("https://jobs.lever.co/zoox/8b32157b-ab44-4078-85ea-1d8d7b747a20/apply",
              "/tmp/none.pdf", "SWE")
    assert res.status == rules.FAILED
    assert res.ryan_status == rules.RYAN_NOT_SUBMITTED
    assert LEVER_SKIP_REASON in res.reason

def test_same_company_and_url_key():
    assert tracker_read.same_company("Brain Co.", "BrainCo")
    assert tracker_read.same_company("Rivian and Volkswagen Group Technologies", "Rivian")
    assert tracker_read.same_company("HP Inc.", "HP")
    assert not tracker_read.same_company("Western Magnetics", "Netic")
    assert not tracker_read.same_company("HP Inc.", "HPE")
    a = "https://jobs.ashbyhq.com/notion/3fba1c39-c5cb-47d7-9ad2-1cec4d7e9d0c/application"
    b = "https://jobs.ashbyhq.com/notion/3fba1c39-c5cb-47d7-9ad2-1cec4d7e9d0c"
    assert tracker_read.apply_url_key(a) == tracker_read.apply_url_key(b)
    assert tracker_read.apply_url_key("https://job-boards.greenhouse.io/figma/jobs/6143238004") == \
           tracker_read.apply_url_key("https://boards.greenhouse.io/figma/jobs/6143238004?gh_jid=6143238004")
    assert tracker_read.apply_url_key(a) != tracker_read.apply_url_key(
        "https://jobs.ashbyhq.com/notion/27090d0f-07d3-419b-adaa-837b5e22f947/application")

    # Same requisition may differ by "Summer 2027" punctuation; a different specialty must not match.
    assert tracker_read.titles_same(
        "Software Development Engineer Intern, Machine Learning/Automated Reasoning- Summer 2027",
        "Software Development Engineer Intern, Machine Learning/Automated Reasoning")
    assert not tracker_read.titles_same(
        "Software Development Engineer Intern, Machine Learning/Automated Reasoning- Summer 2027",
        "Software Development Engineer Intern, Amazon Leo")
    assert not tracker_read.titles_same(
        "Software Engineer Intern (Summer 2027)",
        "Software Engineering Intern, iOS (Summer 2027)")


def test_cap_counts_tracker_applications_in_window():
    today = date(2026, 11, 10)

    def jobs(company_dates):
        out = []
        for i, (company, dates, loc) in enumerate(company_dates):
            out.append({
                "company": company, "role": f"Intern {i}", "location": loc, "ats": "greenhouse",
                "listed_age": "0d", "skill_coverage": 1.0, "text_similarity": 1.0,
                "grad_year_score": 0.6, "applied_dates": dates, "flags": [],
                "fit": {"skill_coverage": 1.0, "text_similarity": 1.0},
            })
        return out

    # 1 prior application this window: only 1 new role allowed
    out = rank.rank(jobs([
        ("Acme", [today - timedelta(days=5)], "San Francisco, CA"),
        ("Acme", [today - timedelta(days=5)], "Chicago, IL"),
        ("Beta", [], "New York, NY"),
    ]), top_n=50, per_company=2, today=today)
    assert [c["company"] for c in out] == ["Acme", "Beta"]
    assert out[0]["tracker_applied"] == 1
    # already at cap 2: no new roles
    out = rank.rank(jobs([
        ("Figma", [today, today - timedelta(days=1)], "San Francisco, CA"),
        ("Figma", [today, today - timedelta(days=1)], "New York, NY"),
        ("Beta", [], "New York, NY"),
    ]), top_n=50, per_company=2, today=today)
    assert all(c["company"] != "Figma" for c in out)
    ok, why = rank.cap_ok("Figma", [today, today], today, per_company=2)
    assert not ok and "2/2" in why
    # applications outside the 90-day window do not count
    old = today - timedelta(days=100)
    out = rank.rank(jobs([
        ("Acme", [old, old], "San Francisco, CA"),
        ("Acme", [old, old], "Chicago, IL"),
    ]), top_n=50, per_company=2, today=today)
    assert len([c for c in out if c["company"] == "Acme"]) == 2


def test_prefilter_drops_same_posting(monkeypatch):
    def fake_dup(path, company, role, url):
        if company == "Notion":
            return [(60, "same url key", "Notion", "Software Engineer Intern (Summer 2027)")]
        return []
    monkeypatch.setattr(run.tracker_read, "find_dup", fake_dup)
    dropped = []
    rows = [
        {"company": "Notion", "title": "Software Engineer Intern (Summer 2027)",
         "apply_url": "https://jobs.ashbyhq.com/notion/3fba1c39-c5cb-47d7-9ad2-1cec4d7e9d0c/application",
         "location": "NYC", "closed": False},
        {"company": "Enova", "title": "Analytics Internship Summer 2027",
         "apply_url": "https://job-boards.greenhouse.io/enova/jobs/8366036",
         "location": "Chicago, IL", "closed": False},
    ]
    out = run.prefilter(rows, tracker="dummy.xlsx", dropped=dropped)
    assert len(out) == 1 and out[0]["company"] == "Enova"
    assert len(dropped) == 1 and dropped[0]["status"] == "already_applied" and dropped[0]["company"] == "Notion"


def test_swe_template_year():
    pdf, _, year, why, key = selector.choose_template("SWE", "SWE Intern", "Sophomore program for the class of 2029.")
    assert year == 2029 and key == "SWE_2029" and pdf.endswith(r"\SWE\Ryan Zhou Resume.pdf")
    assert "sophomore" in why.lower() or "2029" in why
    pdf, _, year, why, key = selector.choose_template("SWE", "SWE Intern", "Open to juniors graduating in 2028.")
    assert year == 2028 and key == "SWE_2028" and pdf.endswith(r"\SWE\Ryan Zhou Resume 28.pdf")
    _, _, year, why, key = selector.choose_template("SWE", "SWE Intern", "Summer 2027 internship, no class year stated.")
    assert year == 2028 and key == "SWE_2028" and "unspecified" in why
    # both 2028 and sophomore / 2029 mentioned -> 2029 (matches scoring preference)
    _, _, year, _, key = selector.choose_template(
        "SWE", "SWE Intern", "Open to sophomores and juniors graduating in 2028 or 2029.")
    assert year == 2029 and key == "SWE_2029"
    # other directions only have the 2028 template, even if the JD allows sophomores
    pdf, _, year, why, key = selector.choose_template("AI", "ML Intern", "Sophomore program for the class of 2029.")
    assert year == 2028 and key == "AI" and pdf.endswith(r"\AI\Ryan Zhou Resume.pdf")


def test_non_summer_location_rule():
    # Non-summer away from Pittsburgh and not remote is ineligible
    v = eligibility.check("Software Engineer Intern - (Jan 2027)", "Software engineering internship starting January 2027.", "Bay Area, CA")
    assert v.status == eligibility.INELIGIBLE
    assert any("non-summer" in r and "Pittsburgh" in r for r in v.reasons)
    v = eligibility.check("Co-op Applied AI Engineer", "A co-op in applied AI.", "New York, NY")
    assert v.status == eligibility.INELIGIBLE
    v = eligibility.check("AI Research Winter Co-Op", "Winter co-op in AI research.", "San Francisco, CA")
    assert v.status == eligibility.INELIGIBLE
    # Remote or Pittsburgh (onsite / hybrid) is allowed
    v = eligibility.check("AI Research Winter Co-Op", "Winter co-op in AI research.", "Remote - US")
    assert v.status == eligibility.ELIGIBLE, v
    assert any("remote" in f.lower() for f in v.flags)
    v = eligibility.check("Winter Co-Op Engineer", "Winter co-op.", "Pittsburgh, PA")
    assert v.status == eligibility.ELIGIBLE, v
    v = eligibility.check("Fall Intern", "Fall internship, hybrid.", "Pittsburgh, PA (Hybrid)")
    assert v.status == eligibility.ELIGIBLE, v
    v = eligibility.check("Winter Intern", "This internship is fully remote. Start in January.", "New York, NY")
    assert v.status == eligibility.ELIGIBLE, v
    # Summer (including Summer ... Co-op) is unchanged — location scoring still applies, not this filter
    v = eligibility.check("Summer 2027 Internship/Co-op", "Summer 2027 internship / co-op.", "New York, NY")
    assert v.status == eligibility.ELIGIBLE, v
    v = eligibility.check("Software Engineer Intern (Summer 2027)", "Summer 2027 internship.", "Austin, TX")
    assert v.status == eligibility.ELIGIBLE, v
    # Unspecified term (no summer, no off-cycle keywords) stays eligible with a flag
    v = eligibility.check("Software Engineer Intern", "A software internship. No term named.", "Austin, TX")
    assert v.status == eligibility.ELIGIBLE, v
    assert any("Summer 2027" in f for f in v.flags)
    assert eligibility.term_kind("Software Engineer Intern - (Jan 2027)", "") == "non_summer"
    assert eligibility.term_kind("Summer 2027 Internship/Co-op", "") == "summer"
    # Glued "Summer2027" in a title is still a summer term (must not fall through to JD off-cycle words)
    assert eligibility.term_kind(
        "Internship-Software Engineering:Platform & Applications-Summer2027 Undergrad",
        "This posting also mentions a spring information session.") == "summer"
    v = eligibility.check(
        "Innovation & Technology Internship-Software Engineering:Platform & Applications-Summer2027 Undergrad",
        "A summer 2027 internship. Campus sessions in the spring.",
        "AZ - Scottsdale",
    )
    assert v.status == eligibility.ELIGIBLE, v
    assert eligibility.term_kind("Software Engineer Intern", "") == "unspecified"



def test_grad_year_from_pdf():
    assert selector.grad_year_from_pdf(r"E:\求职\简历\Tech\SWE\Ryan Zhou Resume.pdf") == 2029
    assert selector.grad_year_from_pdf(r"E:\求职\简历\Tech\SWE\Ryan Zhou Resume 28.pdf") == 2028
    assert selector.grad_year_from_pdf(r"E:\求职\简历\Tech\AI\Ryan Zhou Resume.pdf") == 2028
    assert selector.grad_year_from_pdf("") == 2028
