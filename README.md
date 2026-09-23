# jobsearch

This tool runs one command, `jobsearch 3`, and does the following:

1. Scrapes LinkedIn postings from the last 3 days, using your boolean query in each search listed in `config.yaml`. Scraping goes through Apify.
2. Drops postings that are already in the sheet, matched by LinkedIn job ID.
3. Prefilters out internships, co-ops and any exclusions you configure.
4. Scores each new posting from 0 to 100 against your candidate profile with Gemini Flash-Lite, on the free tier. The score has four parts: core skills (40), domain (25), seniority (20) and hard requirements (15).
5. Writes to Google Sheets:
   - **Raw Postings**: every posting, with its score and reasoning.
   - **Shortlist**: postings at or above the threshold, sorted by score. It is rebuilt on each run. Your `My Status` and `Notes` columns are kept.
   - **Runs**: one log line per run.

## One-time setup

### 1. Apify
Sign up at apify.com on the Free plan. **Don't add a payment card.** Copy your API token from **Settings → API & Integrations**.

### 2. Gemini
1. Create a key at https://aistudio.google.com/apikey.
2. **Never link a billing account to its project.** Without billing, the key cannot be charged. On the API keys page, the project's **Plan** column should say Free, not Paid.
3. Open https://aistudio.google.com/rate-limit and note the requests-per-day limit for `gemini-3.5-flash-lite`. Set `scoring.daily_request_cap` in `config.yaml` just below it.

### 3. Google Sheets (service account)
1. At https://console.cloud.google.com, create a project, for example `jobsearch`.
2. Go to **APIs & Services → Library** and enable the **Google Sheets API**.
3. Go to **IAM & Admin → Service Accounts**, create one (no roles needed), then open **Keys → Add key → JSON**. The Sheets API is free and needs no billing. If Cloud Console prompts you to enable billing, decline.
4. Save the downloaded file as `secrets/google-service-account.json` in this folder.
5. Create an empty Google Sheet. Share it, as **Editor**, with the service account's email (the `client_email` value in the JSON file).

The tabs are created automatically on the first run.

### 4. `.env`
Copy `.env.example` to `.env` and fill in the values.

### 5. Your corpus
Put your resume, project write-ups and current job description in `corpus/`. Alternatively, set `CORPUS_DIR` in `.env` to a Google Drive for desktop folder, so both machines share the same files. The first scoring run condenses them into `profile.md`. Read that file: it is everything the matcher knows about you.

### 6. Install and add the command

**Windows (PowerShell)**
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e .
Add-Content $PROFILE "`nfunction jobsearch { & '$PWD\.venv\Scripts\jobsearch.exe' @args }"
```

**macOS (zsh)**
```bash
python3 -m venv .venv
.venv/bin/pip install -e .
echo "alias jobsearch='$PWD/.venv/bin/jobsearch'" >> ~/.zshrc
```

Open a new terminal afterwards so the command is picked up.

## Usage

```
jobsearch               last 7 days (default_days in config.yaml)
jobsearch 3             last 3 days
jobsearch 3 --dry-run   scrape and prefilter only, then print; no sheet writes or LLM calls, but still uses Apify credit
jobsearch --no-score    fill Raw Postings without scoring
jobsearch rescore       score anything left pending (for example after a quota or network error)
jobsearch rescore --all re-score everything after changing the prompt, rubric or corpus
jobsearch profile       force a rebuild of profile.md
jobsearch usage         Apify spend this cycle, Gemini calls today
```

Choose the day window to cover the time since your last run. Postings that are already in the sheet are skipped before scoring, so an overlapping window costs no Gemini calls. It does cost Apify credit, because the duplicates are still fetched.

## Staying on the free tiers

Neither service can charge you as long as you add no card to Apify and link no billing to the Gemini project. When a free allowance runs out, the service refuses requests instead of billing you. The limits below stop the tool from using a whole month's or day's allowance in one go.

| | Free allowance | What the tool does |
|---|---|---|
| **Apify** | $5 per monthly cycle; the account is blocked once it's used up | Before each run it checks this cycle's spend and refuses to start past `apify.monthly_budget_usd` ($4.50). Each run passes a `maxTotalChargeUsd` ceiling (`max_run_usd`, $0.50, or whatever budget is left), which Apify enforces itself. |
| **Gemini** | A daily request limit per model per project, reset at midnight Pacific | Throttles to `requests_per_minute`. Every Gemini call is logged in the Runs tab, so both machines share one daily count, and the tool stops at `daily_request_cap`. If Google reports the daily quota is used up, the tool stops at once rather than retrying. Unscored postings stay `pending` for `jobsearch rescore`. |

At the defaults, a run of 2 searches × 100 results costs about $0.20, so $4.50 covers roughly 20 full runs a cycle. Smaller day windows use fewer results.

**Privacy note:** on the free tier, Google may use your prompts (your profile and the job postings) to improve its products. Only a paid Gemini project avoids that.

## Tuning

- **Query, locations, exclusions, threshold**: edit `config.yaml`.
- **Scoring rubric and prompt**: edit `src/jobsearch/score.py`, then run `jobsearch rescore --all`.
- Every posting gets a score, not just the shortlisted ones. Sort Raw Postings by Fit Score to check whether the threshold is set right.
