"""Standing answers: form graduation follows the uploaded resume template (May 2028 / June 2029)."""
from apply import rules
from apply.answer_planner import enforce, Context, build_messages
from apply.form_reader import Field
from apply.p2 import selector

ALL_INFO = "Remote availability: from November 2, 2026\nSummer 2027 availability: May 1, 2027 – September 1, 2027.\n"
YEARS = ["Year..."] + [str(y) for y in range(2027, 1907, -1)]
YEARS_BOTH = YEARS + ["2028", "2029"]
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
JOBS_2029 = ["Dow Jones", "GlobalFoundries", "Covestro", "xAI", "Allstate"]


def ctx(year=None, resume_path="", job="Acme - Software Engineer Intern (Summer 2027)\nLocation San Francisco, CA"):
    return Context(all_info=ALL_INFO, form_answers="", resume_text="", role_category="SWE",
                   salary=rules.choose_salary("SWE"), job_context=job,
                   resume_path=resume_path, grad_year_on_resume=year)


def F(key, typ, label, req=True, options=None, **kw):
    f = Field(key=key, frame_index=0, apply_id=key, type=typ, label=label, required=req,
              value="", options=options or [], errors=[])
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def one(f, action="pending", value=None, reason="", c=None):
    return enforce([f], [{"key": f.key, "action": action, "value": value, "reason": reason}], c or ctx())[0]


def test_default_still_may_2028():
    a = one(F("g", "text", "Expected graduation date", placeholder="MM/YYYY"), "fill", "06/2029")
    assert a.value == "05/2028" and a.enforced == "graduation"
    b = one(F("y", "select", "Graduation Year", options=YEARS_BOTH), "select", "2029")
    assert b.action == "select" and b.value == "2028"
    c = one(F("m", "select", "Expected graduation month", options=MONTHS), "select", "June")
    assert c.value == "May"


def test_2029_template_plans_june_2029():
    c = ctx(2029)
    a = one(F("g", "text", "What is your expected graduation date? (MM/YYYY)", placeholder="MM/YYYY"),
            "fill", "05/2028", c=c)
    assert a.value == "06/2029" and a.enforced == "graduation"
    b = one(F("t", "text", "Expected graduation"), "fill", "May 2028", c=c)
    assert b.value == "June 2029"
    d = one(F("d", "text", "Graduation date", placeholder="MM/DD/YYYY", extra={"date_picker": True}),
            "fill", "05/01/2028", c=c)
    assert d.value == "06/01/2029"
    y = one(F("y", "select", "Expected graduation year", options=YEARS_BOTH), "select", "2028", c=c)
    assert y.value == "2029"
    m = one(F("m", "select", "Graduation month", options=MONTHS), "select", "May", c=c)
    assert m.value == "June"


def test_year_dropdown_missing_needed_year_is_pending():
    assert one(F("y", "select", "End Date", options=YEARS), "select", "2028").reason == "no 2028 graduation year option"
    c = ctx(2029)
    # dropdown has 2028 but not 2029
    a = one(F("y", "select", "Expected graduation year", options=YEARS + ["2028"]), "select", "2028", c=c)
    assert a.action == "pending" and a.reason == "no 2029 graduation year option" and a.enforced == "grad_year"


def test_education_end_date_follows_template_experience_untouched():
    edu = F("e", "select", "End Date", options=YEARS_BOTH, entry_kind="education", entry_index=0)
    assert one(edu, "select", "2027").value == "2028"
    assert one(edu, "select", "2027", c=ctx(2029)).value == "2029"
    exp = F("x", "select", "End Date", options=YEARS, entry_kind="experience", entry_index=0)
    assert one(exp, "select", "2026").action == "select" and one(exp, "select", "2026").value == "2026"


def test_class_year_and_standing():
    opts = ["Freshman", "Sophomore", "Junior", "Senior"]
    s = F("s", "select", "Academic standing / year in school", options=opts)
    assert one(s, "select", "Senior").value == "Junior"
    assert one(s, "select", "Junior", c=ctx(2029)).value == "Sophomore"
    y = F("c", "select", "Class of", options=YEARS_BOTH)
    assert one(y, "pending").value == "2028"
    assert one(y, "pending", c=ctx(2029)).value == "2029"
    yn = F("q", "radio", "Are you currently a sophomore?", options=["Yes", "No"])
    assert one(yn, "select", "Yes").value == "No"
    assert one(yn, "select", "No", c=ctx(2029)).value == "Yes"
    jr = F("j", "radio", "Are you a junior or senior?", options=["Yes", "No"])
    assert one(jr, "select", "No").value == "Yes"
    assert one(jr, "select", "Yes", c=ctx(2029)).value == "No"


def test_summer_start_unchanged_fulltime_uses_graduation():
    start = F("d", "text", "What is your earliest potential start date?", placeholder="Pick date...",
              extra={"date_picker": True})
    assert one(start, "fill", "11/02/2026").value == "05/01/2027"
    assert one(start, "fill", "11/02/2026", c=ctx(2029)).value == "05/01/2027"
    ft = F("f", "text", "When would you be available to start a full-time position after graduation?",
           placeholder="MM/YYYY")
    assert one(ft, "fill", "05/01/2027").value == "05/2028"
    assert one(ft, "fill", "05/01/2027", c=ctx(2029)).value == "06/2029"


def test_infer_year_from_swe_resume_path_only():
    swe29 = r"E:\求职\简历\Tech\SWE\Ryan Zhou Resume.pdf"
    swe28 = r"E:\求职\简历\Tech\SWE\Ryan Zhou Resume 28.pdf"
    ai = r"E:\求职\简历\Tech\AI\Ryan Zhou Resume.pdf"
    assert selector.grad_year_from_pdf(swe29) == 2029
    assert selector.grad_year_from_pdf(swe28) == 2028
    assert selector.grad_year_from_pdf(ai) == 2028
    assert rules.effective_grad_year(None, swe29) == 2029
    assert rules.effective_grad_year(None, swe28) == 2028
    assert rules.effective_grad_year(None, ai) == 2028
    assert rules.effective_grad_year(2028, swe29) == 2028  # explicit wins
    a = one(F("g", "text", "Expected graduation", placeholder="MM/YYYY"), "pending",
            c=ctx(resume_path=swe29))
    assert a.value == "06/2029"
    b = one(F("g", "text", "Expected graduation", placeholder="MM/YYYY"), "pending",
            c=ctx(resume_path=ai))
    assert b.value == "05/2028"


def test_build_messages_includes_graduation():
    from types import SimpleNamespace
    snap = SimpleNamespace(url="", heading="", page_errors=[], fields=[])
    msgs = build_messages(ctx(2029), snap, 0, [])
    import json
    page = json.loads(msgs[-1]["content"])
    assert page["GRADUATION"]["year"] == 2029
    assert page["GRADUATION"]["month"] == "June"
    assert page["GRADUATION"]["month_year"] == "June 2029"
    assert page["GRADUATION"]["class_standing"] == "Sophomore"


def test_2029_template_jobs_plan_june_2029():
    """The five P2 jobs that use the 2029 SWE template must plan June 2029, not May 2028."""
    fields = [
        F("g", "text", "Expected graduation date", placeholder="MM/YYYY"),
        F("y", "select", "Graduation year", options=YEARS_BOTH),
        F("m", "select", "Graduation month", options=MONTHS),
        F("s", "select", "Academic standing", options=["Freshman", "Sophomore", "Junior", "Senior"]),
        F("f", "text", "Earliest date you can start full-time after graduation", placeholder="MM/YYYY"),
    ]
    for company in JOBS_2029:
        c = ctx(2029, job=f"{company} - Software Engineering Intern (sophomore / class of 2029)")
        planned = enforce(fields, [{"key": f.key, "action": "fill", "value": "May 2028"} for f in fields], c)
        by = {a.key: a for a in planned}
        assert by["g"].value == "06/2029", company
        assert by["y"].value == "2029", company
        assert by["m"].value == "June", company
        assert by["s"].value == "Sophomore", company
        assert by["f"].value == "06/2029", company
