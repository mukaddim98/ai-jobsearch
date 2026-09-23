from __future__ import annotations

from datetime import datetime

from .config import Settings
from .llm import Gemini, QuotaExhausted, quota_day
from .prefilter import skip_reason
from .profile import load_profile
from .scrape import build_url, dedupe, monthly_usage, normalize, run_actor, run_cap_usd
from .score import bullets, score_posting, total
from .sheets import Sheet

FLUSH_EVERY = 10
MIN_RUN_USD = 0.05  # below this a run can't fetch enough results to be worth starting


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def open_sheet(s: Settings) -> Sheet:
    return Sheet(s.path("GOOGLE_SERVICE_ACCOUNT_FILE"), s.secret("SHEET_ID"))


def gemini(s: Settings, sheet: Sheet) -> Gemini:
    """A Gemini client allowed only what is left of today's cap, counting calls from every machine."""
    sc = s.cfg["scoring"]
    used = sheet.gemini_calls_on(quota_day())
    return Gemini(s.secret("GEMINI_API_KEY"), sc["requests_per_minute"], sc["daily_request_cap"] - used)


def scrape(s: Settings, days: int) -> tuple[list[dict], float]:
    """Returns unique postings and Apify's usage for the cycle after the run."""
    token, ap = s.secret("APIFY_TOKEN"), s.cfg["apify"]
    usage = monthly_usage(token)
    cap = run_cap_usd(usage, ap["monthly_budget_usd"], ap["max_run_usd"])
    if cap < MIN_RUN_USD:
        raise SystemExit(f"Apify budget reached: ${usage.used_usd:.2f} used this cycle against a "
                         f"${ap['monthly_budget_usd']:.2f} budget. The cycle resets {usage.cycle_end}.")

    urls = [build_url(s.cfg["query"], search, days) for search in s.cfg["searches"]]
    names = ", ".join(x["name"] for x in s.cfg["searches"])
    print(f"Searching LinkedIn ({names}), last {days} day(s)...")
    print(f"  Apify: ${usage.used_usd:.2f} used this cycle; this run is capped at ${cap:.2f}")
    max_items = int(cap / ap["price_per_1000_results"] * 1000)
    items = run_actor(token, urls, s.cfg["max_results_per_search"], cap, max_items)
    postings = dedupe(normalize(i) for i in items)
    after = monthly_usage(token).used_usd
    print(f"  {len(items)} results, {len(postings)} unique postings; Apify now at ${after:.2f} this cycle")
    return postings, after


def score_rows(s: Settings, sheet: Sheet, llm: Gemini, rescore_all: bool = False) -> tuple[int, int]:
    """Score pending rows (or every non-filtered row). Returns (scored, errors)."""
    targets = [r for r in sheet.raw_rows() if r["Status"] == "pending"
               or (rescore_all and not r["Status"].startswith("filtered"))]
    if not targets:
        return 0, 0
    sc = s.cfg["scoring"]
    scored = errors = 0
    batch: list[tuple[int, dict]] = []
    try:
        profile = load_profile(s.corpus_dir, llm, sc["profile_model"])
        print(f"Scoring {len(targets)} posting(s) with {sc['model']} "
              f"({llm.remaining} Gemini calls left today)...")
        for r in targets:
            posting = {
                "title": r["Title"], "company": r["Company"], "location": r["Location"],
                "workplace": f"({r['Workplace']})" if r["Workplace"] else "", "seniority": r["Seniority"],
                "employment_type": r["Employment Type"], "description": r["Description"],
            }
            try:
                fit = score_posting(llm, sc["model"], profile, posting)
            except QuotaExhausted:
                raise
            except Exception as e:  # one bad posting shouldn't sink the run
                errors += 1
                print(f"  ! {r['Title']} @ {r['Company']}: {e}")
                continue
            scored += 1
            batch.append((r["_row"], {
                "Status": "scored", "Fit Score": total(fit), "Core Skills": fit.core_skills,
                "Domain": fit.domain, "Seniority Fit": fit.seniority, "Hard Reqs": fit.hard_requirements,
                "Why It Fits": bullets(fit.why_fit), "Gaps": bullets(fit.gaps), "Scored At": _now(),
            }))
            print(f"  {total(fit):>3}  {r['Title']} @ {r['Company']}")
            if len(batch) >= FLUSH_EVERY:
                sheet.write_scores(batch)
                batch = []
    except QuotaExhausted as e:
        print(f"  {e} Stopped with {len(targets) - scored - errors} posting(s) not scored; "
              "`jobsearch rescore` picks up pending ones after the reset.")
    finally:
        sheet.write_scores(batch)
    return scored, errors


def finish(s: Settings, sheet: Sheet, stats: dict, llm: Gemini | None, rescore_all: bool = False) -> None:
    """Score (when given a Gemini client), rebuild the shortlist and log the run, even if scoring fails."""
    scored = errors = 0
    try:
        if llm:
            scored, errors = score_rows(s, sheet, llm, rescore_all)
    finally:
        shortlisted = sheet.rebuild_shortlist(s.cfg["scoring"]["threshold"])
        pending = sheet.pending_count()
        sheet.log_run({
            **stats, "Run At": _now(), "Scored": scored, "Shortlisted": shortlisted, "Pending": pending,
            "Errors": errors, "Gemini Calls": llm.calls if llm else 0, "Quota Day": quota_day(),
        })
        print(f"Done: {scored} scored, {shortlisted} on the shortlist, {pending} pending, {errors} errors.")


def run(s: Settings, days: int, dry_run: bool = False, no_score: bool = False) -> None:
    postings, apify_used = scrape(s, days)
    exclude = s.cfg.get("exclude") or {}
    for p in postings:
        reason = skip_reason(p, exclude)
        p["status"] = f"filtered ({reason})" if reason else "pending"

    if dry_run:
        for p in postings:
            print(f"  [{p['status']}] {p['title']} @ {p['company']} ({p['location']})")
        print("Dry run: nothing written to the sheet, no Gemini calls.")
        return

    sheet = open_sheet(s)
    seen = {r["Job ID"] for r in sheet.raw_rows()}
    new = [p for p in postings if p["id"] not in seen]
    filtered = sum(p["status"] != "pending" for p in new)
    sheet.append_raw(new, _now())
    print(f"  {len(new)} new ({filtered} filtered out), {len(postings) - len(new)} already in the sheet")

    stats = {"Command": "run", "Days": days, "Scraped": len(postings), "New": len(new),
             "Filtered": filtered, "Apify Cycle USD": round(apify_used, 2)}
    finish(s, sheet, stats, None if no_score else gemini(s, sheet))


def rescore(s: Settings, rescore_all: bool) -> None:
    sheet = open_sheet(s)
    finish(s, sheet, {"Command": "rescore --all" if rescore_all else "rescore"}, gemini(s, sheet), rescore_all)


def rebuild_profile(s: Settings) -> None:
    sheet = open_sheet(s)
    llm = gemini(s, sheet)
    try:
        load_profile(s.corpus_dir, llm, s.cfg["scoring"]["profile_model"], force=True)
    finally:
        sheet.log_run({"Run At": _now(), "Command": "profile", "Gemini Calls": llm.calls, "Quota Day": quota_day()})


def usage(s: Settings) -> None:
    ap, cap = s.cfg["apify"], s.cfg["scoring"]["daily_request_cap"]
    u = monthly_usage(s.secret("APIFY_TOKEN"))
    print(f"Apify   ${u.used_usd:.2f} of your ${ap['monthly_budget_usd']:.2f} budget used this cycle "
          f"(free plan: ${u.limit_usd:.2f}); resets {u.cycle_end}")
    day = quota_day()
    used = open_sheet(s).gemini_calls_on(day)
    print(f"Gemini  {used} of {cap} calls used for {day}; resets at midnight Pacific")
