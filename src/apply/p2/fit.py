"""Resume–JD text fit (deterministic, offline, Jobscan-style keyword coverage + TF-IDF cosine).

The 6-component final ranking score lives in rank.py. This module only measures how well a
template covers the JD: JD-frequency-weighted hard-skill coverage and TF-IDF cosine.
`score` on the returned dict is those two terms renormalized to 0–1 (0.50 coverage + 0.20
cosine, divided by 0.70) so needs_tailoring can use a stable threshold. It is not an ATS score.
Hard skills come from a curated alias dictionary. "evidence_gaps" are JD skills the template
text lacks but ALL_INFO shows real evidence for (what a truthful P3 tailoring could add);
"missing" are JD skills with no evidence at all (never to be added).
"""
from __future__ import annotations

import math
import re
from collections import Counter

SKILLS = {
    "python": [r"python"], "java": [r"java(?!script)"], "c++": [r"c\+\+"], "c#": [r"c#", r"\.net"], "go": [r"golang", r"\bgo\b(?= |,)"],
    "javascript": [r"javascript", r"\bjs\b"], "typescript": [r"typescript"], "sql": [r"\bsql\b"], "r": [r"\bR\b(?=[ ,/])"],
    "react": [r"react"], "next.js": [r"next\.?js"], "node.js": [r"node\.?js", r"\bnode\b"], "html/css": [r"\bhtml\b", r"\bcss\b"],
    "fastapi": [r"fastapi"], "flask": [r"flask"], "rest api": [r"rest(ful)? apis?", r"\bapis?\b"], "graphql": [r"graphql"],
    "postgresql": [r"postgres"], "mysql": [r"mysql"], "mongodb": [r"mongo"], "redis": [r"redis"], "supabase": [r"supabase"],
    "aws": [r"\baws\b", r"amazon web services"], "azure": [r"azure"], "gcp": [r"\bgcp\b", r"google cloud"],
    "docker": [r"docker"], "kubernetes": [r"kubernetes", r"\bk8s\b"], "git": [r"\bgit\b"], "ci/cd": [r"ci/cd", r"continuous integration"],
    "linux": [r"linux", r"unix"], "testing": [r"unit test", r"integration test", r"\btesting\b", r"pytest"],
    "machine learning": [r"machine learning", r"\bml\b"], "deep learning": [r"deep learning", r"neural network"],
    "llm": [r"\bllms?\b", r"large language model", r"gpt", r"generative ai", r"genai"], "rag": [r"\brag\b", r"retrieval.augmented"],
    "embeddings / vector search": [r"embedding", r"vector (search|database|db)", r"pgvector"], "pytorch": [r"pytorch"],
    "tensorflow": [r"tensorflow"], "computer vision": [r"computer vision", r"\bvision\b"], "nlp": [r"\bnlp\b", r"natural language"],
    "pandas/numpy": [r"pandas", r"numpy"], "spark": [r"spark"], "airflow": [r"airflow"], "etl": [r"\betl\b", r"data pipeline"],
    "tableau": [r"tableau"], "power bi": [r"power ?bi"], "excel": [r"excel"], "statistics": [r"statistic", r"a/b test", r"hypothesis test"],
    "data structures & algorithms": [r"data structures", r"algorithms"], "distributed systems": [r"distributed systems?"],
    "system design": [r"system design", r"scalab"], "mobile": [r"\bios\b", r"android", r"mobile"], "oauth": [r"oauth"],
}
_C = {k: re.compile("|".join(v), re.I) for k, v in SKILLS.items()}
_W = re.compile(r"[a-z][a-z+#.]{1,}")
_STOP = set("the and for with you our are will this that from have your their into about who what can all able work team role "
            "intern internship experience including such using use new more years year strong skills ability".split())
# Renormalize 0.50 coverage + 0.20 cosine onto a 0–1 scale for needs_tailoring.
_COV_W, _COS_W = 0.50, 0.20


def skills_in(text: str) -> Counter:
    return Counter({k: len(rx.findall(text or "")) for k, rx in _C.items() if rx.search(text or "")})


def _tf(text: str) -> Counter:
    return Counter(w for w in _W.findall((text or "").lower()) if w not in _STOP)


def cosine(a: str, b: str) -> float:
    ta, tb = _tf(a), _tf(b)
    common = set(ta) & set(tb)
    num = sum(ta[w] * tb[w] for w in common)
    den = math.sqrt(sum(v * v for v in ta.values())) * math.sqrt(sum(v * v for v in tb.values()))
    return num / den if den else 0.0


def text_fit(coverage: float, cosine_sim: float) -> float:
    return ( _COV_W * coverage + _COS_W * cosine_sim) / (_COV_W + _COS_W)


def score(jd: str, title: str, template_text: str, evidence_text: str) -> dict:
    need = skills_in(f"{title} {jd}")
    have = skills_in(template_text)
    ev = skills_in(evidence_text)
    total = sum(min(c, 3) for c in need.values()) or 1
    covered = sum(min(c, 3) for k, c in need.items() if k in have)
    cov = covered / total
    cos = min(1.0, cosine(jd, template_text) * 2.5)  # resume/JD cosines rarely exceed ~0.4
    return {"score": round(text_fit(cov, cos), 3), "skill_coverage": round(cov, 3), "text_similarity": round(cos, 3),
            "jd_skills": sorted(need, key=lambda k: -need[k]), "matched": sorted(k for k in need if k in have),
            "evidence_gaps": sorted(k for k in need if k not in have and k in ev),
            "missing": sorted(k for k in need if k not in have and k not in ev)}
