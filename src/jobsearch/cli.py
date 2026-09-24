from __future__ import annotations

import argparse

from . import config, pipeline
from .llm import QuotaExhausted

USAGE = """\
jobsearch [DAYS]        scrape the last DAYS days (default from config.yaml), then score and shortlist
jobsearch 3 --tiers 1   only search the tier-1 lanes (cheaper)
jobsearch rescore       score postings left pending (quota ran out, errors, --no-score)
jobsearch rescore --all re-score every posting, e.g. after changing the prompt or your corpus
jobsearch profile       rebuild profile.md from your corpus now
jobsearch usage         show Apify spend this cycle and Gemini calls today"""


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="jobsearch", usage=USAGE)
    ap.add_argument("target", nargs="?", help="number of days, or: rescore, profile, usage")
    ap.add_argument("--dry-run", action="store_true",
                    help="scrape and prefilter only (still uses Apify credit); no sheet writes, no Gemini")
    ap.add_argument("--no-score", action="store_true", help="scrape into the sheet but skip LLM scoring")
    ap.add_argument("--all", action="store_true", help="with rescore: re-score everything")
    ap.add_argument("--tiers", help="comma-separated lane tiers to search, e.g. 1 or 1,2 (default from config.yaml)")
    args = ap.parse_args(argv)
    s = config.load()

    try:
        if args.target is None or args.target.isdigit():
            days = int(args.target) if args.target else s.cfg["default_days"]
            if not 1 <= days <= 30:
                ap.error("DAYS must be between 1 and 30")
            try:
                tiers = [int(t) for t in args.tiers.split(",")] if args.tiers else s.cfg["tiers"]
            except ValueError:
                ap.error("--tiers takes numbers, e.g. --tiers 1,2")
            pipeline.run(s, days, tiers, dry_run=args.dry_run, no_score=args.no_score)
        elif args.target == "rescore":
            pipeline.rescore(s, args.all)
        elif args.target == "profile":
            pipeline.rebuild_profile(s)
        elif args.target == "usage":
            pipeline.usage(s)
        else:
            ap.error(f"unknown command {args.target!r}")
    except QuotaExhausted as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
