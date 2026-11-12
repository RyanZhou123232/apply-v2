"""Standing answers added 2026-11-10: AI-matching opt-out, STEM OPT eligibility."""
from apply import rules
from apply.answer_planner import enforce, Context, PlannedAction
from apply.form_reader import Field

KLA_AI = (
    "As part of the recruiting process, KLA Corporation may use artificial intelligence to match your "
    "candidate information to the requirements of the role you have applied for. All selection decisions "
    "will always be made by a member of the hiring team. As a candidate, you can choose to opt out of "
    "the use of artificial intelligence, and this will not negatively impact your application."
)
STEM_OPT = "Are you eligible for a 24-month OPT extension based upon a US degree in STEM?"
CURRENT_OPT = "Are you currently in a period of Optional Practical Training (OPT)?"
NOTE = "Do you consent to an AI notetaker recording and transcribing this interview / meeting?"
AI_USE = "Did you use AI to complete this application?"


def ctx():
    return Context(all_info="", form_answers="", resume_text="", role_category="SWE",
                   salary=rules.choose_salary("SWE"))


def F(key, typ, label, req=True, options=None, **kw):
    f = Field(key=key, frame_index=0, apply_id=key, type=typ, label=label, required=req,
              value="", options=options or [], errors=[])
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def one(f, action="pending", value=None, reason=""):
    return enforce([f], [{"key": f.key, "action": action, "value": value, "reason": reason}], ctx())[0]


def test_kla_ai_matching_detected_and_not_ai_use():
    assert rules.is_ai_matching_opt_out_question(KLA_AI)
    assert not rules.is_ai_use_question(KLA_AI)
    assert rules.forced_pending_reason(KLA_AI) is None


def test_ai_use_still_pending():
    assert rules.is_ai_use_question(AI_USE)
    assert not rules.is_ai_matching_opt_out_question(AI_USE)
    a = one(F("a", "radio", AI_USE, options=["Yes", "No"]), "select", "No")
    assert a.action == "pending" and a.value is None and a.enforced == "forced_pending"


def test_ai_notetaker_consents_opt_in():
    assert rules.is_ai_notetaker_question(NOTE)
    assert rules.is_ai_consent_question(NOTE)
    assert not rules.is_ai_use_question(NOTE)
    assert not rules.is_ai_matching_opt_out_question(NOTE)
    assert rules.forced_pending_reason(NOTE) is None
    # even if the model leaves it pending or picks opt-out, standing rule forces consent
    a = one(F("n", "radio", NOTE, options=["Yes", "No"]), "pending")
    assert a.action == "select" and a.value == "Yes" and a.enforced == "ai_consent"


def test_uipath_interview_recording_consent_opt_in():
    label = (
        "Interview Recording Consent About Interview Recording To keep your interview experience "
        "seamless and distraction-free, we use an AI Notetaker to record and transcribe our interviews. "
        "This helps interviewers focus fully on the conversation and ensures your responses are captured "
        "accurately. The recording will only be used internally for evaluation. Opting out will not impact "
        "your candidacy in any way."
    )
    opts = ["Yes, I consent to be recorded", "Opt out of recording"]
    assert rules.is_ai_notetaker_question(label)
    assert rules.is_ai_consent_question(label)
    assert not rules.is_ai_use_question(label)
    assert rules.forced_pending_reason(label) is None
    a = one(F("u", "radio", label, options=opts), "pending")
    assert a.action == "select" and a.value == "Yes, I consent to be recorded" and a.enforced == "ai_consent"


def test_ai_processing_consent_opt_in():
    label = "Do you consent to the company using artificial intelligence to process your application and personal data?"
    assert rules.is_ai_processing_consent_question(label)
    assert rules.is_ai_consent_question(label)
    assert not rules.is_ai_use_question(label)
    assert rules.forced_pending_reason(label) is None
    a = one(F("p", "radio", label, options=["I consent", "I do not consent"]), "pending")
    assert a.action == "select" and a.value == "I consent" and a.enforced == "ai_consent"


def test_ai_matching_yes_no_opt_out_framing_picks_no():
    # KLA-style: the prompt is an opt-out statement with Yes/No options -> No = do not opt out
    a = one(F("k", "select", KLA_AI, options=["Select One", "Yes", "No"]), "pending")
    assert a.action == "select" and a.value == "No" and a.enforced == "ai_matching"


def test_ai_matching_consent_framing_picks_yes():
    label = "Do you consent to the company using artificial intelligence to match your candidate information during recruiting?"
    a = one(F("c", "radio", label, options=["Yes", "No"]), "select", "No")
    assert a.action == "select" and a.value == "Yes" and a.enforced == "ai_matching"


def test_ai_matching_explicit_wording():
    opts = ["I want to opt out of AI matching", "I do not wish to opt out"]
    label = "You may opt out of our use of AI in the recruiting process to match candidates."
    a = one(F("x", "radio", label, options=opts), "pending")
    assert a.value == "I do not wish to opt out"


def test_stem_opt_yes_overrides_model():
    assert rules.is_stem_opt_extension_question(STEM_OPT)
    assert not rules.is_stem_opt_extension_question(CURRENT_OPT)
    a = one(F("s", "select", STEM_OPT, options=["Yes", "No"]), "select", "No")
    assert a.action == "select" and a.value == "Yes" and a.enforced == "stem_opt"


def test_current_opt_period_not_overridden():
    a = one(F("c", "select", CURRENT_OPT, options=["Yes", "No"]), "select", "No")
    assert a.action == "select" and a.value == "No" and not a.enforced


# --- WOTC / tax-credit eligibility = No; SSN/DOB stay pending ---
SNAP = "In the last year, have you or anyone you've lived with received SNAP (Supplemental Nutrition Assistance Program also referred to as food stamps)?"
TANF = "In the last two years, have you or anyone you've lived with received TANF (Temporary Assistance for Needy Families also referred to as welfare)?"
VET = "Are you a veteran of the U.S. Military/Armed Forces?"
DIS = "Are you a person who has a disability?"
FELONY = "Have you ever been convicted of a felony?"
UNEMP = "Are you unemployed? And Have you collected unemployment benefits at any time during your unemployment period?"
WOTC_CTX = "Please answer a few questions to determine this company's eligibility for federal and state tax credit programs. WOTC survey."


def test_wotc_six_questions_all_no():
    for label in (SNAP, TANF, VET, DIS, FELONY, UNEMP):
        assert rules.is_wotc_eligibility_question(label, WOTC_CTX, ["Yes", "No"]), label
        a = one(F("w", "radio", label, options=["Yes", "No"]), "select", "Yes")
        # even if the model said Yes, standing rule forces No
        assert a.action == "select" and a.value == "No" and a.enforced == "wotc", label


def test_wotc_ssn_and_dob_pending():
    assert rules.is_ssn_question("Social Security Number")
    a = one(F("s", "text", "Social Security Number"), "fill", "123-45-6789")
    assert a.action == "pending" and a.value is None
    c = Context(all_info="", form_answers="", resume_text="", role_category="SWE",
                salary=rules.choose_salary("SWE"), job_context=WOTC_CTX)
    from apply.answer_planner import enforce
    dob = F("d", "text", "Date of Birth")
    b = enforce([dob], [{"key": "d", "action": "fill", "value": "01/01/2006"}], c)[0]
    assert b.action == "pending" and b.enforced in ("wotc_dob", "forced_pending")
    # DOB on a regular (non-WOTC) form is not forced pending by this rule
    assert rules.forced_pending_reason("Date of Birth") is None


def test_voluntary_disability_self_id_not_treated_as_wotc():
    label = "Do you have a disability? You may decline to self-identify."
    opts = ["Yes, I have a disability", "No, I do not have a disability", "I do not wish to answer"]
    assert not rules.is_wotc_eligibility_question(label, "Software Engineer Intern application", opts)
    a = one(F("d", "radio", label, options=opts), "select", "I do not wish to answer")
    assert a.enforced != "wotc"
