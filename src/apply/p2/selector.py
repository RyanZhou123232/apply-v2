"""Direction + resume-template selection and the needs_tailoring decision (no tailoring here: that is P3).

Templates (Ryan's PC, read-only): E:\\求职\\简历\\Tech\\<category>\\ — the file at a category's top level is the
template; company-named subfolders hold tailored versions. Data has two sub-templates (Data Engineer,
General Data). SWE has two mother templates: Ryan Zhou Resume 28.pdf (Expected Jun 2028) and
Ryan Zhou Resume.pdf (Expected Jun 2029). Tailoring rule (memory/WORKFLOW_PREFERENCES.md): template +
small pivot by default; a tailored version only when the JD needs it (RESUME_STANDARDS.md: JD hard
skills that Ryan has real evidence for but the template does not show).
"""
from __future__ import annotations

import re

ROOT = r"E:\求职\简历\Tech"
# Default SWE template is the 2028 version (juniors / 2027-2028 / unspecified class year).
TEMPLATES = {  # direction -> (template PDF to upload, editable source, tailored-versions folder)
    "SWE": (ROOT + r"\SWE\Ryan Zhou Resume 28.pdf", ROOT + r"\SWE\Ryan Zhou swe.docx", ROOT + r"\SWE"),
    "AI": (ROOT + r"\AI\Ryan Zhou Resume.pdf", ROOT + r"\AI\Ryan Zhou AI.docx", ROOT + r"\AI"),
    "Data Engineer": (ROOT + r"\Data\Data Engineer\Ryan Zhou Resume.pdf", ROOT + r"\Data\Data Engineer\Ryan Zhou Data Engineer.docx",
                      ROOT + r"\Data\Data Engineer"),
    "General Data": (ROOT + r"\Data\General Data\Ryan Zhou Resume.pdf", ROOT + r"\Data\General Data\Ryan Zhou Data.docx",
                     ROOT + r"\Data\General Data"),
}
# SWE only: 2029 version is used when the JD allows sophomores / class of 2029.
SWE_TEMPLATES = {
    2028: (ROOT + r"\SWE\Ryan Zhou Resume 28.pdf", ROOT + r"\SWE\Ryan Zhou swe.docx", ROOT + r"\SWE"),
    2029: (ROOT + r"\SWE\Ryan Zhou Resume.pdf", ROOT + r"\SWE\Ryan Zhou swe.docx", ROOT + r"\SWE"),
}
# company subfolders seen 2026-11-09 (tailored versions that may be reused for the same company)
TAILORED = {
    "AI": ["Adobe", "Arango", "Brain Co", "Hex", "nba intern", "Sandisk", "Schonfeld", "Sephora", "Shure", "Tractian"],
    "SWE": ["Lyft", "NBA", "Netic", "NVIDIA", "Pinterest", "Quest Ventures", "Realm", "Tessera Labs", "Vercel", "visa"],
    "Data Engineer": ["Greystar"],
    "General Data": ["Citadel", "Duolingo", "HRT", "strip"],
}
_AI = re.compile(r"\b(machine learning|ML|AI|artificial intelligence|LLM|deep learning|applied scien\w+|computer vision|NLP|"
                 r"research (engineer|scientist)|generative|agents?)\b", re.I)
_DE = re.compile(r"\bdata (engineer\w*|platform|infrastructure|pipeline)|\bETL\b|analytics engineer", re.I)
_DATA = re.compile(r"\bdata (scien\w+|analy\w+)|\banalytics\b|business intelligence|\bBI\b|quantitative analyst|statistic\w*", re.I)
_SWE = re.compile(r"software|backend|back-end|frontend|front-end|full.?stack|developer|SWE\b|platform engineer|mobile|devops|infrastructure", re.I)


def direction(title: str, jd: str = "") -> tuple[str, str]:
    t = title or ""
    if _DE.search(t):
        return "Data Engineer", "title names data engineering"
    if _DATA.search(t) and not _SWE.search(t):
        return "General Data", "title names data science / analytics"
    if _AI.search(t):
        return "AI", "title names AI / ML"
    if _SWE.search(t):
        return "SWE", "title names software engineering"
    head = (jd or "")[:4000]
    n_ai, n_data, n_de = len(_AI.findall(head)), len(_DATA.findall(head)), len(_DE.findall(head))
    if max(n_ai, n_data, n_de) >= 3:
        k = max((n_ai, "AI"), (n_de, "Data Engineer"), (n_data, "General Data"))[1]
        return k, f"JD mentions ({k}) most often"
    return "SWE", "default (generic engineering intern)"


def resume_grad_year(direc: str, title: str, jd: str = "") -> tuple[int, str]:
    """Which graduation year the uploaded template should show.

    Other directions only have a 2028 template. SWE uses the 2029 mother template when the JD
    allows sophomores / class of 2029; otherwise the 2028 template (juniors, 2027/2028, or unspecified).
    """
    if direc != "SWE":
        return 2028, f"{direc} has only the 2028 template"
    # Imported lazily to avoid a circular import at module load if eligibility ever imported selector.
    from . import eligibility
    st, _, why = eligibility.grad_year_fit(title, jd)
    if st == eligibility.ELIGIBLE and (why.startswith("2029") or "sophomore" in why.lower()):
        return 2029, "JD allows sophomores / 2029 graduates"
    if st == eligibility.ELIGIBLE and (
        why.startswith("accepts 2028") or re.search(r"\bjunior", f"{title or ''} {jd or ''}", re.I)
    ):
        return 2028, "JD asks for juniors or 2027/2028 graduates"
    return 2028, "class year unspecified; default 2028 SWE template"


def grad_year_from_pdf(path: str) -> int:
    """Infer the form graduation year from the uploaded template path. Default 2028.

    Only the SWE 2029 mother template (`SWE\\Ryan Zhou Resume.pdf`) maps to 2029.
    Other directions also have a file named `Ryan Zhou Resume.pdf`, but those are 2028 templates.
    """
    pth = (path or "").replace("/", "\\").lower()
    if not pth:
        return 2028
    pdf_2029 = SWE_TEMPLATES[2029][0].replace("/", "\\").lower()
    if pth == pdf_2029 or pth.endswith("\\swe\\ryan zhou resume.pdf"):
        return 2029
    return 2028


def choose_template(direc: str, title: str, jd: str = "") -> tuple[str, str, int, str, str]:
    """Return (pdf, docx, grad_year_on_resume, reason, text_key). text_key indexes --resume-text JSON."""
    year, why = resume_grad_year(direc, title, jd)
    if direc == "SWE":
        pdf, docx, _ = SWE_TEMPLATES[year]
        key = f"SWE_{year}"
    else:
        pdf, docx, _ = TEMPLATES[direc]
        key = direc
    return pdf, docx, year, why, key


def tailored_version_for(company: str, direc: str) -> str:
    c = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    for name in TAILORED.get(direc, []):
        if c and re.sub(r"[^a-z0-9]", "", name.lower()) == c:
            return TEMPLATES[direc][2] + "\\" + name
    return ""


def needs_tailoring(fit: dict, company: str, direc: str, threshold: float = 0.6) -> tuple[bool, str]:
    """True only when tailoring could truthfully raise the match: JD hard skills that ALL_INFO has evidence for
    but the template text does not show, or a low score with such gaps."""
    prior = tailored_version_for(company, direc)
    gaps = fit.get("evidence_gaps", [])
    if prior:
        return False, f"company-tailored version already exists: {prior} (reuse after checking it fits this role)"
    if len(gaps) >= 3 or (gaps and fit.get("score", 1) < threshold):
        return True, f"template misses JD skills Ryan has evidence for: {', '.join(gaps[:8])}"
    if fit.get("score", 1) < threshold * 0.6:
        return False, "low fit but no truthful gaps to add; tailoring would not help (consider skipping)"
    return False, "template already covers the JD's core skills (small pivot only)"
