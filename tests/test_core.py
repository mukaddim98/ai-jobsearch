from urllib.parse import parse_qs, urlparse

from jobsearch.prefilter import skip_reason
from jobsearch.scrape import build_url, dedupe, normalize
from jobsearch.score import Fit, bullets, total
from jobsearch.sheets import _literal


def test_build_url_encodes_boolean_query_and_window():
    url = build_url('("data engineer" OR ML) NOT intern',
                    {"location": "United States", "geo_id": "103644278", "params": {"f_WT": "2"}}, 3)
    q = parse_qs(urlparse(url).query)
    assert q["keywords"] == ['("data engineer" OR ML) NOT intern']
    assert q["f_TPR"] == ["r259200"]
    assert q["geoId"] == ["103644278"] and q["f_WT"] == ["2"]


def test_normalize_handles_lists_missing_fields_and_id_from_link():
    p = normalize({"link": "https://www.linkedin.com/jobs/view/senior-dev-at-acme-4012345678",
                   "title": "Senior Dev", "workplaceTypes": ["Hybrid"], "salaryInfo": ["$100K", "$120K"]})
    assert p["id"] == "4012345678"
    assert p["workplace"] == "Hybrid"
    assert p["salary"] == "$100K, $120K"
    assert p["company"] == "" and p["description"] == ""


def test_dedupe_drops_repeats_and_missing_ids():
    rows = [{"id": "1"}, {"id": "1"}, {"id": ""}, {"id": "2"}]
    assert [r["id"] for r in dedupe(rows)] == ["1", "2"]


def _posting(**kw):
    return {"title": "", "employment_type": "Full-time", "company": "", "description": "", **kw}


def test_prefilter_whole_words_only():
    ex = {"title_keywords": ["intern", "co-op"], "description_keywords": ["security clearance"]}
    assert skip_reason(_posting(title="Software Intern"), ex) == "title: intern"
    assert skip_reason(_posting(title="Co-op Developer"), ex) == "title: co-op"
    assert skip_reason(_posting(title="Internal Tools Engineer"), ex) is None
    assert skip_reason(_posting(description="Requires Security Clearance."), ex) == "description: security clearance"
    assert skip_reason(_posting(employment_type="Internship"), {"employment_types": ["Internship"]}) == "type: Internship"


def test_total_clamps_each_part():
    fit = Fit(core_skills=55, domain=20, seniority=-3, hard_requirements=15, why_fit=[], gaps=[])
    assert total(fit) == 40 + 20 + 0 + 15


def test_bullets_and_literal():
    assert bullets(["a", " ", "b "]) == "• a\n• b"
    assert _literal("=SUM(A1)") == "'=SUM(A1)"
    assert _literal(85) == 85 and _literal("") == ""
