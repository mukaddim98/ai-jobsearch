from __future__ import annotations

from pydantic import BaseModel

from .llm import Gemini

# Part name -> maximum points. Totals 100.
RUBRIC = {"core_skills": 40, "domain": 25, "seniority": 20, "hard_requirements": 15}

SYSTEM = """You screen job postings for one specific candidate. Judge how well the candidate \
fits each posting. Be strict and evidence-based: credit only experience the profile below actually \
shows, and never assume skills that are not there.

Score four parts:
- core_skills (0-40): overlap between the posting's must-have skills/tools and the candidate's \
demonstrated ones. Nice-to-haves count for little.
- domain (0-25): how relevant the candidate's industry and problem-domain experience is.
- seniority (0-20): match between the level and years the posting asks for and the candidate's. \
Penalise both under- and over-qualification.
- hard_requirements (0-15): 15 if every hard requirement is met (degree, licence, work \
authorisation, security clearance, language, on-site location). Deduct heavily for each one \
clearly unmet; 0 if one is disqualifying. Do not deduct for requirements the posting does not state.

why_fit: 3-5 bullets, strongest first. Each pairs one concrete requirement from the posting with \
specific evidence from the profile, in the form "<requirement> -> <candidate evidence>", e.g. \
"Production Kubernetes experience -> ran EKS clusters serving 2M req/day at Acme (3 yrs)". \
Leave it empty if nothing genuinely fits.
gaps: 0-3 bullets naming the most important requirements the candidate does not clearly meet.

CANDIDATE PROFILE
=================
{profile}"""

POSTING = """JOB POSTING
Title: {title}
Company: {company}
Location: {location} {workplace}
Seniority: {seniority}
Employment type: {employment_type}

{description}"""

DESCRIPTION_CHARS = 12_000


class Fit(BaseModel):
    core_skills: int
    domain: int
    seniority: int
    hard_requirements: int
    why_fit: list[str]
    gaps: list[str]


def total(fit: Fit) -> int:
    return sum(max(0, min(getattr(fit, part), cap)) for part, cap in RUBRIC.items())


def bullets(items: list[str]) -> str:
    return "\n".join(f"• {s.strip()}" for s in items if s.strip())


def score_posting(llm: Gemini, model: str, profile: str, p: dict) -> Fit:
    prompt = POSTING.format(**{**p, "description": p["description"][:DESCRIPTION_CHARS]})
    return llm.generate(model, SYSTEM.format(profile=profile), prompt, schema=Fit)
