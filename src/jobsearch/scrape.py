"""Fetch LinkedIn postings through the curious_coder/linkedin-jobs-scraper Apify actor."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

APIFY = "https://api.apify.com/v2"
ACTOR = "curious_coder~linkedin-jobs-scraper"
DESCRIPTION_LIMIT = 40_000  # Sheets caps a cell at 50k characters


def lane_queries(cfg: dict, tiers: list[int]) -> list[tuple[str, str]]:
    """(lane name, full LinkedIn query) for each lane in the given tiers, in config order."""
    suffix = " ".join(cfg.get("append_to_every_query") or [])
    return [(lane["name"], f"{lane['query']} {suffix}".strip())
            for lane in cfg["lanes"] if lane["tier"] in tiers]


def build_url(query: str, search: dict, days: int) -> str:
    params = {"keywords": query, "location": search["location"]}
    if search.get("geo_id"):
        params["geoId"] = search["geo_id"]
    params["f_TPR"] = f"r{days * 86400}"  # posted within the last N seconds
    params.update(search.get("params") or {})
    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)


@dataclass
class ApifyUsage:
    used_usd: float
    limit_usd: float
    cycle_end: str  # YYYY-MM-DD


def _session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    return s


def monthly_usage(token: str) -> ApifyUsage:
    r = _session(token).get(f"{APIFY}/users/me/limits", timeout=30)
    r.raise_for_status()
    d = r.json()["data"]
    return ApifyUsage(d["current"]["monthlyUsageUsd"], d["limits"]["maxMonthlyUsageUsd"],
                      d["monthlyUsageCycle"]["endAt"][:10])


def run_cap_usd(usage: ApifyUsage, monthly_budget: float, max_run: float) -> float:
    """What this run may spend: the per-run ceiling, or whatever is left of the monthly budget."""
    remaining = min(monthly_budget, usage.limit_usd) - usage.used_usd
    return max(0.0, round(min(max_run, remaining), 2))


def run_actor(token: str, urls: list[str], limit: int, max_charge_usd: float,
              max_items: int, timeout: int = 1200) -> list[dict]:
    s = _session(token)
    r = s.post(
        f"{APIFY}/acts/{ACTOR}/runs",
        # Apify stops charging at these limits whatever the actor does.
        params={"maxTotalChargeUsd": max_charge_usd, "maxItems": max_items},
        json={"urls": urls, "scrapeCompany": False, "limitPerSource": limit},
        timeout=60,
    )
    r.raise_for_status()
    run = r.json()["data"]
    deadline = time.monotonic() + timeout
    while run["status"] in ("READY", "RUNNING", "TIMING-OUT", "ABORTING"):
        if time.monotonic() > deadline:
            raise RuntimeError(f"Apify run {run['id']} still {run['status']} after {timeout}s")
        r = s.get(f"{APIFY}/actor-runs/{run['id']}", params={"waitForFinish": 60}, timeout=90)
        r.raise_for_status()
        run = r.json()["data"]
    r = s.get(f"{APIFY}/datasets/{run['defaultDatasetId']}/items",
              params={"clean": "true", "format": "json"}, timeout=120)
    r.raise_for_status()
    items = r.json()
    if run["status"] != "SUCCEEDED":
        if not items:
            raise RuntimeError(f"Apify run {run['id']} ended with status {run['status']}")
        # Results already paid for are worth keeping, e.g. when the cost cap stopped the run.
        print(f"  Apify run ended {run['status']}; keeping the {len(items)} results it returned")
    return items


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(t for t in (_text(x) for x in v) if t)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {t}" for k, x in v.items() if (t := _text(x)))
    return str(v).strip()


def job_id(item: dict) -> str:
    if item.get("id"):
        return str(item["id"])
    m = re.search(r"(\d{6,})", item.get("link") or "")
    return m.group(1) if m else ""


def normalize(item: dict) -> dict:
    workplace = _text(item.get("workplaceTypes"))
    # This actor doesn't report workplace type, but a search with LinkedIn's remote-only filter does tell us.
    if not workplace and (item.get("workRemoteAllowed") or "f_WT=2" in (item.get("inputUrl") or "")):
        workplace = "Remote"
    return {
        "id": job_id(item),
        "posted_at": _text(item.get("postedAt")),
        "title": _text(item.get("title")),
        "company": _text(item.get("companyName")),
        "location": _text(item.get("location")),
        "workplace": workplace,
        "seniority": _text(item.get("seniorityLevel")),
        "employment_type": _text(item.get("employmentType")),
        "salary": _text(item.get("salary") or item.get("salaryInfo")),
        "applicants": _text(item.get("applicantsCount")),
        "link": _text(item.get("link")),
        "apply_url": _text(item.get("applyUrl")),
        "description": _text(item.get("descriptionText"))[:DESCRIPTION_LIMIT],
    }


def dedupe(postings) -> list[dict]:
    """Drop postings without an ID and repeats (the same job can match more than one search)."""
    seen, out = set(), []
    for p in postings:
        if p["id"] and p["id"] not in seen:
            seen.add(p["id"])
            out.append(p)
    return out
