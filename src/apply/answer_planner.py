"""Ask OpenAI what to put in each field; then enforce hard rules in code."""
from __future__ import annotations

import json
import re
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import rules
from .form_reader import Field, Snapshot

log = logging.getLogger(__name__)

DEFAULT_KEY_FILE = "/home/box/.secrets/apply-v2/openai-key.txt"
ACTIONS = {"fill", "select", "check", "uncheck", "upload_resume", "pending", "skip"}

SYSTEM_PROMPT = """You fill out job application forms for one applicant, LinFeng (Ryan) Zhou.
You get: the applicant's fact base (ALL_INFO), standing answer rules (FORM_ANSWERS, Chinese), resume text,
job context, and the list of form fields currently visible on one page. Decide one action per field.

Hard rules:
1. Never invent facts. Use only ALL_INFO, FORM_ANSWERS and the resume. ALL_INFO wins on conflicts; FORM_ANSWERS wins over both for the topics it covers.
   If a REQUIRED field needs a fact you do not have, return "pending" with a short reason. If an OPTIONAL field needs an unknown fact, return "skip".
2. Questions asking whether AI / ChatGPT / an automated tool was used to fill, write or prepare this application are ALWAYS "pending". Never answer them.
   AI meeting-notetaker / interview-recording / meeting-transcription consent is also ALWAYS "pending".
   Exception: if the employer asks whether you want to opt out of (or consent to) their use of AI to match / screen your candidacy, do NOT opt out — choose the option that allows AI matching.
   "Are you eligible for a 24-month OPT extension based upon a US degree in STEM?" (and close variants) = Yes.
   WOTC / Work Opportunity Tax Credit / tax-credit eligibility questions (SNAP/food stamps, TANF/welfare, veteran, disability, felony, unemployed / unemployment benefits, and close variants) = No.
   On a WOTC/tax-credit questionnaire, SSN and date of birth are ALWAYS "pending". Never fill them. If any other required WOTC question is not one of those Yes/No eligibility items, "pending".
3. SSN / Social Security Number fields are ALWAYS "pending".
4. Salary: use the SALARY value given in the job context, converted to the field's unit (annual = hourly x 2080, monthly = hourly x 173.3). Numbers only for numeric inputs.
5. For select / radio / button_group / checkbox_group, the value MUST be copied exactly from the field's options. For checkbox_group the value is a JSON list of option labels.
   A combobox is usually searchable and its listed options may be only the first part of a long list (schools, cities, degrees):
   if the right answer is listed copy it exactly; otherwise still return action "select" with the true value as the text to type
   (e.g. "Carnegie Mellon University"). Only use "pending" if no truthful option could exist.
   If the exact fact is not among the listed options, follow FORM_ANSWERS if it covers the topic (e.g. major/discipline);
   otherwise return its key term so the search can surface the closest real option; never pick an unrelated broad option like "Science".
6. File fields for resume/CV: "upload_resume". Cover letter file fields: "skip" unless required, then "pending".
7. Fields that already hold the correct value: "skip". Do not re-type them.
8. Respect format hints (placeholder, pattern, maxlength, date formats like MM/YYYY). Phone: +1 412-680-9048 unless a format is implied.
9. Voluntary self-identification (gender, race, veteran, disability, pronouns, sexual orientation): use FORM_ANSWERS / ALL_INFO if they state an answer; otherwise choose the "decline / prefer not to say" option if one exists; otherwise "pending" if required, "skip" if optional.
10. Consent / acknowledgement / privacy-policy checkboxes required to apply: "check".
11. Free-text questions (e.g. "Why are you interested"): write 2-4 concise, truthful sentences grounded in the resume and the job context. No fabricated claims.
12. If the page FAILED TO ADVANCE on a previous attempt: the usual cause is a missed required field or an invalid value. Read every field's "errors" and the page errors,
    look for required fields that are still empty (including ones that just appeared), and fix them. Do not repeat a value that produced an error; fix its format instead.
13. Current company / current employer: NEVER use "Lucina", "Lucina - Personal AI Agent", or any personal project. Lucina is a personal project, not employment.
    For Current company use exactly: PricewaterhouseCoopers (PwC). For other employer/company/experience fields use ONLY these three:
    PricewaterhouseCoopers (PwC); SDIC Securities; Carnegie Mellon University Human-Computer Interaction Institute — Augmented Design Capability Studio
    (or the short forms PwC / SDIC Securities / CMU HCII when the field is short).
14. location_autocomplete fields (and any Current location / city typeahead): return the city to search (e.g. "Pittsburgh" or "Pittsburgh, PA, USA").
    The filler will pick a matching suggestion from the dropdown; do not leave these as free text that was never selected.
15. Repeatable sections: a field with "entry": {"section": "experience", "index": i} belongs to work-experience entry i (0-based).
    Fill entry i ONLY from EXPERIENCE_ENTRIES[i] (company, title, location, start/end dates in the field's format, current=false),
    with a 1-2 sentence description grounded in the resume for that role. Never use Lucina or any personal project as an entry.
    If i is beyond EXPERIENCE_ENTRIES, "skip". "education" entries come from ALL_INFO (Carnegie Mellon University); extra education entries: "skip".

16. GitHub profile / GitHub URL fields: https://github.com/RyanZhou123232 . Transcript upload fields: never upload anything;
    if required return "pending" with reason "transcript required", otherwise "skip".
17. Multi-select fields ("multiple": true) take a JSON list of exact option labels. Location-preference multi-selects: all locations.
    Single-choice location preference: the job's posted location, else Bay Area > Chicago > New York > Pittsburgh > Los Angeles.
18. SMS / WhatsApp / text-message consent: Yes. "Consider me for other roles": Yes.
    Graduation date / expected graduation / class year must match GRADUATION in the job context (May 2028 by default; June 2029 when the 2029 SWE resume is uploaded). Class standing derived from that year: Junior for 2028, Sophomore for 2029. Full-time start tied to graduation uses the same month/year. A graduation-year dropdown without that year: "pending".
    Earliest start date for a summer internship: the Summer 2027 availability start in ALL_INFO (May 1, 2027), not the remote date, and not a full-time-after-graduation date.

Return ONLY JSON: {"actions":[{"key":"<field key>","action":"fill|select|check|uncheck|upload_resume|pending|skip","value":<string or list or null>,"reason":"<short>"}]}
Include every field key exactly once."""


def _read_key() -> str:
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"].strip()
    path = Path(os.environ.get("APPLY_OPENAI_KEY_FILE", DEFAULT_KEY_FILE))
    if not path.exists():
        raise RuntimeError(f"OpenAI key not found (set OPENAI_API_KEY or APPLY_OPENAI_KEY_FILE); looked at {path}")
    return path.read_text(encoding="utf-8").strip()


@dataclass
class LLMStats:
    calls: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        # gpt-5.4 list price (USD per 1M tokens, Nov 2026): input 2.50, cached 0.25, output 15.00; override via env
        pin, pc, pout = (float(os.environ.get(k, v)) for k, v in
                         (("OPENAI_PRICE_IN", 2.5), ("OPENAI_PRICE_CACHED", 0.25), ("OPENAI_PRICE_OUT", 15.0)))
        d["est_cost_usd"] = round(((self.prompt_tokens - self.cached_tokens) * pin + self.cached_tokens * pc
                                   + self.completion_tokens * pout) / 1e6, 4)
        d["seconds"] = round(self.seconds, 1)
        return d


class OpenAIJSONClient:
    """Thin wrapper; anything with `.complete_json(messages) -> dict` and `.stats` can replace it in tests."""

    def __init__(self, model: Optional[str] = None):
        from openai import OpenAI
        self.client = OpenAI(api_key=_read_key(), timeout=180, max_retries=2)
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5.4")
        self.stats = LLMStats()

    def complete_json(self, messages: list[dict]) -> dict:
        t0 = time.time()
        kwargs: dict[str, Any] = dict(model=self.model, messages=messages, response_format={"type": "json_object"})
        effort = os.environ.get("OPENAI_REASONING_EFFORT", "low")
        try:
            resp = self.client.chat.completions.create(reasoning_effort=effort, **kwargs)
        except Exception as e:  # model without reasoning_effort support
            if "reasoning" not in str(e).lower():
                raise
            resp = self.client.chat.completions.create(**kwargs)
        self.stats.calls += 1
        self.stats.seconds += time.time() - t0
        u = resp.usage
        if u:
            self.stats.prompt_tokens += u.prompt_tokens or 0
            self.stats.completion_tokens += u.completion_tokens or 0
            details = getattr(u, "prompt_tokens_details", None)
            self.stats.cached_tokens += (getattr(details, "cached_tokens", 0) or 0) if details else 0
        return json.loads(resp.choices[0].message.content or "{}")


@dataclass
class PlannedAction:
    key: str
    action: str
    value: Any = None
    reason: str = ""
    enforced: str = ""  # set when code overrode the model

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "")}


@dataclass
class Context:
    all_info: str
    form_answers: str
    resume_text: str
    role_category: str
    salary: rules.SalaryChoice
    job_context: str = ""
    resume_path: str = ""
    grad_year_on_resume: Optional[int] = None  # 2028 or 2029; None -> infer from resume_path, else 2028


def build_messages(ctx: Context, snap: Snapshot, attempt: int, history: list[str]) -> list[dict]:
    static = (f"ALL_INFO:\n{ctx.all_info}\n\nFORM_ANSWERS:\n{ctx.form_answers}\n\nRESUME TEXT:\n{ctx.resume_text}")
    gy = rules.effective_grad_year(ctx.grad_year_on_resume, ctx.resume_path)
    gm, gy = rules.grad_month_year(gy)
    page = {
        "job_context": ctx.job_context[:5000],
        "role_category": ctx.role_category,
        "SALARY": {"amount": ctx.salary.amount, "unit": ctx.salary.unit, "source": ctx.salary.source},
        "GRADUATION": {
            "year": gy,
            "month": rules.MONTH_NAMES[gm],
            "month_year": f"{rules.MONTH_NAMES[gm]} {gy}",
            "class_standing": rules.class_standing_for_year(gy),
            "class_of": gy,
        },
        "page": {"url": snap.url, "heading": snap.heading, "page_errors": snap.page_errors},
        "attempt": attempt,
        "previous_attempt_notes": history[-6:],
        "fields": [f.for_llm() for f in snap.fields],
    }
    if any(f.entry_kind == "experience" for f in snap.fields):
        page["EXPERIENCE_ENTRIES"] = [dict(e, index=i) for i, e in enumerate(rules.EXPERIENCE_ENTRIES)]
    if attempt > 0:
        page["notice"] = ("THIS PAGE FAILED TO ADVANCE after the previous attempt. Usually a required field was missed "
                          "or a value is invalid. Check errors and empty required fields.")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": static},
            {"role": "user", "content": json.dumps(page, ensure_ascii=False)}]


def enforce(fields: list[Field], raw_actions: list[dict], ctx: Context) -> list[PlannedAction]:
    """Validate model output and apply code-level rules (AI-use, SSN, salary)."""
    by_key = {}
    for a in raw_actions or []:
        if isinstance(a, dict) and a.get("key"):
            by_key[a["key"]] = a
    out: list[PlannedAction] = []
    for f in fields:
        a = by_key.get(f.key, {})
        act = str(a.get("action", "")).lower()
        pa = PlannedAction(f.key, act if act in ACTIONS else "skip", a.get("value"), str(a.get("reason", ""))[:200])
        if not act:
            pa.reason = "model returned no action"
        forced = f.forced_pending
        if forced:
            pa = PlannedAction(f.key, "pending", None, forced, enforced="forced_pending")
        elif rules.is_salary_question(f.label + " " + f.placeholder) and f.type in ("text", "textarea") \
                and pa.action in ("fill", "skip", "pending") and not (pa.action == "skip" and f.value):
            value, unit = rules.salary_for_field(ctx.salary, f.label + " " + f.placeholder)
            if f.type == "textarea":
                value = f"${value} per {unit} (USD)"
            pa = PlannedAction(f.key, "fill", value, f"salary rule: {ctx.salary.source}, unit={unit}", enforced="salary")
        if rules.is_source_question(f.label) and not f.forced_pending \
                and (pa.action in ("pending", "skip", "select") or (pa.action == "fill" and f.type not in ("text", "textarea"))):
            ans = rules.source_answer(f.options, f.type)
            if ans and (f.required or pa.action != "skip") and not (f.value and rules.values_match(ans, f.value)):
                pa = PlannedAction(f.key, "fill" if f.type in ("text", "textarea") else "select", ans,
                                   "FORM_ANSWERS: Job board, else Other", enforced="source")
        if rules.is_major_question(f.label) and f.type in ("combobox", "select", "radio") and pa.action in ("fill", "select") \
                and not rules.major_value_ok(str(pa.value or "")):
            pa = PlannedAction(f.key, "select", rules.MAJOR_FALLBACK,
                               "FORM_ANSWERS: no exact major option -> Machine Learning", enforced="major")
        if f.entry_kind == "experience":
            pa = _enforce_experience_entry(f, pa)
        elif rules.is_current_company_question(f.label):
            if pa.action in ("fill", "select"):
                repl = rules.sanitize_employer_value(str(pa.value or ""), current=True)
                if repl:
                    pa = PlannedAction(f.key, "fill", repl,
                                       "FORM_ANSWERS: current company = PwC (not Lucina/personal project)",
                                       enforced="current_company")
            elif pa.action in ("skip", "pending") and (f.required or not f.value):
                # Required, or empty optional that we still want to avoid Lucina later — fill standing value when required
                if f.required:
                    pa = PlannedAction(f.key, "fill", rules.current_company_value(),
                                       "FORM_ANSWERS: current company = PwC", enforced="current_company")
        elif rules.is_employer_question(f.label) and pa.action in ("fill", "select")                 and rules.is_forbidden_employer(str(pa.value or "")):
            pa = PlannedAction(f.key, "fill", rules.current_company_value(),
                               "replaced Lucina/personal project with PwC", enforced="employer")
        if f.type == "location_autocomplete" and pa.action in ("fill", "select") and pa.value:
            # keep value; filler selects from suggestions. Normalize bare skips on required location.
            pass
        if not f.forced_pending:
            pa = _standing_answers(f, pa, ctx)
        if f.type == "file" and rules.is_transcript_question(f.label) and not rules.is_resume_label(f.label):
            # FORM_ANSWERS: never upload a transcript; required -> Ryan uploads it himself (pending)
            pa = (PlannedAction(f.key, "pending", None, rules.TRANSCRIPT_PENDING, enforced="transcript") if f.required
                  else PlannedAction(f.key, "skip", None, "optional transcript upload left empty", enforced="transcript"))
        elif rules.is_github_question(f.label + " " + f.placeholder) and f.type in ("text", "textarea") \
                and not rules.is_ai_use_question(f.label):
            pa = PlannedAction(f.key, "fill", rules.GITHUB_URL, "FORM_ANSWERS: GitHub link", enforced="github")
        if pa.action == "upload_resume" and f.type != "file":
            pa = PlannedAction(f.key, "skip", None, "upload_resume on non-file field ignored", enforced="invalid")
        if pa.action in ("fill", "select") and (pa.value is None or pa.value == "" or pa.value == []):
            pa = PlannedAction(f.key, "pending" if f.required else "skip", None, "model gave no value", enforced="invalid")
        out.append(pa)
    return out


def _is_multi(f: Field) -> bool:
    return f.type in ("checkbox_group", "multiselect") or (f.type == "combobox" and bool(f.extra.get("multi")))


def _standing_answers(f: Field, pa: PlannedAction, ctx: Context) -> PlannedAction:
    """FORM_ANSWERS rules that must not depend on the model (Ryan, 2026-11-09)."""
    label = f.label or ""
    multi = _is_multi(f)
    # export control: citizenship countries = China since birth; embargoed-country questions = No
    if f.entry_kind is None and rules.is_adult_question(label) and f.type in ("radio", "select", "button_group", "combobox", "checkbox"):
        why = "FORM_ANSWERS: 18 or older = Yes (born 2006)"
        if f.type == "checkbox":
            return PlannedAction(f.key, "check", None, why, enforced="adult")
        opt = rules.positive_option(f.options) if f.options else ("Yes" if f.type == "combobox" else None)
        if opt:
            return PlannedAction(f.key, "select", opt, why, enforced="adult")
    if f.entry_kind is None and rules.is_embargoed_country_question(label):
        why = "FORM_ANSWERS: embargoed-country (Cuba/Iran/North Korea/Syria) question = No"
        if f.type in ("radio", "select", "button_group", "combobox"):
            opt = rules.negative_option(f.options) if f.options else ("No" if f.type == "combobox" else None)
            if opt:
                return PlannedAction(f.key, "select", opt, why, enforced="export_embargo")
        elif f.type in ("text", "textarea"):
            return PlannedAction(f.key, "fill", "No", why, enforced="export_embargo")
        elif f.type == "checkbox":
            return PlannedAction(f.key, "uncheck", None, why, enforced="export_embargo")
    elif f.entry_kind is None and rules.is_export_citizenship_question(label):
        why = "FORM_ANSWERS: export control = China citizen since birth (2006); Guinea-Bissau permanent resident since 2018"
        if f.type in ("text", "textarea"):
            return PlannedAction(f.key, "fill", rules.EXPORT_CITIZENSHIP_ANSWER, why, enforced="export_citizenship")
        if f.type in ("select", "combobox", "multiselect", "checkbox_group", "radio"):
            wants = rules.export_country_targets(label)
            opts = [o for o in (rules.country_option(f.options, w) for w in wants) if o] if f.options \
                else (wants if f.type == "combobox" else [])
            if opts:
                return PlannedAction(f.key, "select", opts if multi else opts[0], why, enforced="export_citizenship")
    # AI notetaker / interview-recording / AI-processing consent: consent / opt in
    if f.entry_kind is None and rules.is_ai_consent_question(label) \
            and f.type in ("radio", "select", "button_group", "combobox", "checkbox", "checkbox_group", "multiselect"):
        why = "FORM_ANSWERS: AI notetaker / recording / processing consent = consent / opt in"
        if f.type == "checkbox":
            if re.search(r"\bopt[\s-]?out\b", label, re.I) and not re.search(r"\b(consent|agree|authorize|opt[\s-]?in)\b", label, re.I):
                return PlannedAction(f.key, "uncheck", None, why, enforced="ai_consent")
            return PlannedAction(f.key, "check", None, why, enforced="ai_consent")
        opt = rules.allow_ai_matching_option(f.options, label)
        if not opt and f.type == "combobox" and not f.options:
            opt = "No" if re.search(r"\bopt[\s-]?out\b", label, re.I) and not re.search(
                r"\b(consent|agree|authorize|opt[\s-]?in)\b", label, re.I) else "Yes"
        if opt:
            return PlannedAction(f.key, "select", [opt] if multi else opt, why, enforced="ai_consent")
    # employer AI candidate-matching opt-out: do not opt out (allow AI matching)
    if f.entry_kind is None and rules.is_ai_matching_opt_out_question(label) \
            and f.type in ("radio", "select", "button_group", "combobox", "checkbox", "checkbox_group", "multiselect"):
        why = "FORM_ANSWERS: employer AI-matching opt-out = do not opt out (allow AI matching)"
        if f.type == "checkbox":
            # a lone checkbox that opts you OUT should be left unchecked; one that consents should be checked
            if re.search(r"\bopt[\s-]?out\b", label, re.I) and not re.search(r"\b(consent|agree|authorize|opt[\s-]?in)\b", label, re.I):
                return PlannedAction(f.key, "uncheck", None, why, enforced="ai_matching")
            return PlannedAction(f.key, "check", None, why, enforced="ai_matching")
        opt = rules.allow_ai_matching_option(f.options, label)
        if not opt and f.type == "combobox" and not f.options:
            # searchable box whose options have not been harvested yet; filler will type a likely answer
            opt = "No" if re.search(r"\bopt[\s-]?out\b", label, re.I) and not re.search(
                r"\b(consent|agree|authorize|opt[\s-]?in)\b", label, re.I) else "Yes"
        if opt:
            return PlannedAction(f.key, "select", [opt] if multi else opt, why, enforced="ai_matching")
    # 24-month STEM OPT extension eligibility = Yes
    if f.entry_kind is None and rules.is_stem_opt_extension_question(label) \
            and f.type in ("radio", "select", "button_group", "combobox", "checkbox"):
        why = "FORM_ANSWERS: 24-month STEM OPT extension eligibility = Yes"
        if f.type == "checkbox":
            return PlannedAction(f.key, "check", None, why, enforced="stem_opt")
        opt = rules.positive_option(f.options) if f.options else ("Yes" if f.type == "combobox" else None)
        if opt:
            return PlannedAction(f.key, "select", opt, why, enforced="stem_opt")
    # WOTC / tax-credit eligibility screening = No; SSN/DOB on those forms stay pending
    wotc_ctx = f"{label} {ctx.job_context or ''}"
    if f.entry_kind is None and rules.is_dob_question(label) and rules.is_wotc_context(wotc_ctx):
        return PlannedAction(f.key, "pending", None, rules.WOTC_DOB_PENDING, enforced="wotc_dob")
    if f.entry_kind is None and rules.is_wotc_eligibility_question(label, wotc_ctx, f.options) \
            and f.type in ("radio", "select", "button_group", "combobox", "checkbox", "checkbox_group"):
        why = "FORM_ANSWERS: WOTC/tax-credit eligibility questions = No"
        if f.type == "checkbox":
            return PlannedAction(f.key, "uncheck", None, why, enforced="wotc")
        opt = rules.negative_option(f.options) if f.options else ("No" if f.type == "combobox" else None)
        if opt:
            return PlannedAction(f.key, "select", [opt] if multi else opt, why, enforced="wotc")
    # SMS / WhatsApp / text consent, and "consider me for other roles": Yes
    if f.entry_kind is None and (rules.is_text_consent_question(label) or rules.is_other_roles_question(label)):
        why = "FORM_ANSWERS: text/SMS/WhatsApp consent = Yes" if rules.is_text_consent_question(label) \
            else "FORM_ANSWERS: consider for other roles = Yes"
        if f.type == "checkbox":
            return PlannedAction(f.key, "check", None, why, enforced="standing_yes")
        if f.type in ("radio", "select", "button_group", "combobox", "checkbox_group", "multiselect"):
            opt = rules.affirmative_option(f.options) if f.options else ("Yes" if f.type == "combobox" else None)
            if opt:
                return PlannedAction(f.key, "select", [opt] if multi else opt, why, enforced="standing_yes")
    # graduation date / year / class standing / full-time start tied to the resume template
    g = _enforce_graduation(f, pa, ctx)
    if g is not None:
        return g
    # earliest start date on a summer internship: Summer availability from ALL_INFO
    if rules.is_start_date_question(label) and f.entry_kind is None and f.type in ("text", "textarea", "date") \
            and not rules.is_fulltime_grad_availability_question(label) \
            and rules.use_summer_start(ctx.job_context):
        start = rules.summer_availability_start(ctx.all_info)
        if start:
            val = rules.format_date_for_field(start, f.placeholder, f.input_type, bool(f.extra.get("date_picker")))
            return PlannedAction(f.key, "fill", val, "ALL_INFO: Summer 2027 availability start", enforced="start_date")
    # office / location preference
    if f.options and rules.is_location_preference_question(label):
        if multi:
            allopts = rules.all_location_options(f.options)
            if allopts:
                return PlannedAction(f.key, "select", allopts, "FORM_ANSWERS: open to all locations", enforced="locations_all")
        elif f.type in ("radio", "select", "button_group", "combobox"):
            best = rules.rank_location_option(f.options, ctx.job_context)
            if best:
                return PlannedAction(f.key, "select", best,
                                     "FORM_ANSWERS: posted location > Bay Area > Chicago > NY > Pittsburgh > LA",
                                     enforced="location_rank")
    return pa



def _enforce_graduation(f: Field, pa: PlannedAction, ctx: Context) -> Optional[PlannedAction]:
    """Override graduation-related fields to match the uploaded resume template. None = leave pa."""
    if f.entry_kind == "experience":
        return None
    year = rules.effective_grad_year(ctx.grad_year_on_resume, ctx.resume_path)
    month, year = rules.grad_month_year(year)
    label = f.label or ""
    planned = f"{pa.value or ''} {pa.reason or ''}"
    why = f"FORM_ANSWERS: graduation matches resume template ({rules.MONTH_NAMES[month]} {year})"
    types_choice = ("radio", "select", "button_group", "combobox")
    types_text = ("text", "textarea", "date")

    yn = rules.standing_yes_no(label, year)
    if yn is not None and f.type in types_choice + ("checkbox",):
        if f.type == "checkbox":
            return PlannedAction(f.key, "check" if yn else "uncheck", None, why, enforced="graduation")
        opt = (rules.positive_option(f.options) if yn else rules.negative_option(f.options)) if f.options else (
            ("Yes" if yn else "No") if f.type == "combobox" else None)
        if opt:
            return PlannedAction(f.key, "select", opt, why, enforced="graduation")

    if rules.is_class_standing_question(label, f.options) and f.type in types_choice + types_text:
        standing = rules.class_standing_for_year(year)
        if f.type in types_text:
            return PlannedAction(f.key, "fill", standing, why, enforced="graduation")
        opt = rules.pick_standing_option(f.options, standing)
        if not opt and f.type == "combobox" and not f.options:
            opt = standing
        if opt:
            return PlannedAction(f.key, "select", opt, why, enforced="graduation")

    if f.type in types_choice and rules.missing_grad_year(label, f.options, planned, year=year, entry_kind=f.entry_kind):
        return PlannedAction(f.key, "pending", None, rules.no_grad_year_pending(year), enforced="grad_year")

    grad_q = rules.is_grad_question(label, f.entry_kind, f.options, planned)
    ft_q = rules.is_fulltime_grad_availability_question(label)
    if not grad_q and not ft_q:
        return None

    if f.type in types_choice and rules.is_grad_month_field(label, f.options, f.entry_kind, planned):
        opt = rules.pick_month_option(f.options, month)
        if not opt and f.type == "combobox" and not f.options:
            opt = rules.MONTH_NAMES[month]
        if opt:
            return PlannedAction(f.key, "select", opt, why, enforced="graduation")
        return PlannedAction(f.key, "pending" if f.required else "skip", None,
                             f"no {rules.MONTH_NAMES[month]} option in graduation month dropdown",
                             enforced="graduation")

    if f.type in types_choice and rules.is_grad_year_field(label, f.options, f.entry_kind, planned):
        opt = rules.pick_year_option(f.options, year)
        if not opt and f.type == "combobox" and not f.options:
            opt = str(year)
        if opt:
            return PlannedAction(f.key, "select", opt, why, enforced="graduation")
        return PlannedAction(f.key, "pending" if f.required else "skip", None,
                             rules.no_grad_year_pending(year), enforced="grad_year")

    if f.type in types_text:
        val = rules.format_grad_for_field(
            month, year, f.placeholder, f.input_type or ("date" if f.type == "date" else ""),
            bool(f.extra.get("date_picker") or f.type == "date"), f.options)
        return PlannedAction(f.key, "fill", val, why, enforced="graduation")

    if f.type in types_choice:
        val = rules.format_grad_for_field(
            month, year, f.placeholder, f.input_type, bool(f.extra.get("date_picker")), f.options)
        action = "select"
        return PlannedAction(f.key, action, val, why, enforced="graduation")
    return None


def _enforce_experience_entry(f: Field, pa: PlannedAction) -> PlannedAction:
    """Experience entry i may only describe EXPERIENCE_ENTRIES[i]; company fields are set in code."""
    entry = rules.experience_entry(f.entry_index if f.entry_index is not None else -1)
    if entry is None:
        if pa.action in ("fill", "select") and f.type in ("text", "textarea"):
            return PlannedAction(f.key, "skip", None, "no experience entry for this index", enforced="experience")
        return pa
    if rules.is_company_label(f.label) and f.type in ("text", "textarea", "combobox"):
        want = entry["company"]
        if f.extra.get("maxlength") and len(want) > f.extra["maxlength"]:
            want = entry["short"]
        if pa.action == "skip" and f.value and not rules.is_forbidden_employer(f.value):
            return pa
        return PlannedAction(f.key, "fill" if f.type != "combobox" else "select", want,
                             f"experience entry {f.entry_index}: {entry['short']}", enforced="experience")
    if pa.action in ("fill", "select") and rules.is_forbidden_employer(str(pa.value or "")):
        return PlannedAction(f.key, "pending" if f.required else "skip", None,
                             "model used a personal project in an experience entry", enforced="experience")
    return pa


class AnswerPlanner:
    def __init__(self, ctx: Context, client=None):
        self.ctx = ctx
        self.client = client or OpenAIJSONClient()

    @property
    def stats(self):
        return self.client.stats

    def plan(self, snap: Snapshot, attempt: int = 0, history: Optional[list[str]] = None) -> list[PlannedAction]:
        todo = [f for f in snap.fields]
        if not todo:
            return []
        data = self.client.complete_json(build_messages(self.ctx, snap, attempt, history or []))
        return enforce(todo, data.get("actions", []), self.ctx)


def extract_resume_text(pdf_path: str) -> str:
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(pdf_path).pages)[:12000]
    except Exception as e:
        log.warning("could not extract resume text: %s", e)
        return ""
