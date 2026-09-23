"""Google Sheets storage: Raw Postings (every posting ever seen), Shortlist (rebuilt each run), Runs (log)."""
from __future__ import annotations

from pathlib import Path

import gspread
from gspread.utils import rowcol_to_a1

RAW, SHORT, RUNS = "Raw Postings", "Shortlist", "Runs"

RAW_COLS = [
    "Job ID", "Found At", "Posted At", "Title", "Company", "Location", "Workplace",
    "Seniority", "Employment Type", "Salary", "Applicants", "Link", "Apply URL",
    "Status", "Fit Score", "Core Skills", "Domain", "Seniority Fit", "Hard Reqs",
    "Why It Fits", "Gaps", "Scored At", "Description",
]
# Columns Status..Scored At are rewritten in place when a posting is scored.
SCORE_COLS = RAW_COLS[RAW_COLS.index("Status"):RAW_COLS.index("Scored At") + 1]

SHORT_COLS = [
    "Fit Score", "Title", "Company", "Location", "Workplace", "Posted At", "Link",
    "Why It Fits", "Gaps", "My Status", "Notes", "Job ID",
]
# Your own columns on the Shortlist, carried over by Job ID when it is rebuilt.
MANUAL_COLS = ["My Status", "Notes"]

RUN_COLS = [
    "Run At", "Command", "Days", "Scraped", "New", "Filtered", "Scored", "Shortlisted",
    "Pending", "Errors", "Gemini Calls", "Quota Day", "Apify Cycle USD",
]


def _literal(v):
    """Make a value safe for USER_ENTERED input: numbers pass, text never becomes a formula."""
    if isinstance(v, (int, float)):
        return v
    return "'" + str(v) if v else ""


def as_int(v) -> int | None:
    try:
        return int(str(v).strip())
    except ValueError:
        return None


class Sheet:
    def __init__(self, service_account_file: Path, sheet_id: str):
        self.book = gspread.service_account(filename=str(service_account_file)).open_by_key(sheet_id)
        self.raw = self._tab(RAW, RAW_COLS)
        self.short = self._tab(SHORT, SHORT_COLS, wrap="H:I")
        self.runs = self._tab(RUNS, RUN_COLS)

    def _tab(self, title: str, cols: list[str], wrap: str | None = None):
        try:
            ws = self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(title, rows=1000, cols=len(cols))
        header = ws.row_values(1)
        if not header:
            ws.update(range_name="A1", values=[cols])
            ws.freeze(rows=1)
            if wrap:
                ws.format(wrap, {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"})
        elif header[:len(cols)] != cols:
            raise SystemExit(f"Tab '{title}' has unexpected headers {header}. Expected {cols}.")
        return ws

    def raw_rows(self) -> list[dict]:
        """Every Raw Postings row as a dict, with its sheet row number under '_row'."""
        rows = []
        for i, values in enumerate(self.raw.get_all_values()[1:], start=2):
            values += [""] * (len(RAW_COLS) - len(values))
            rows.append({**dict(zip(RAW_COLS, values)), "_row": i})
        return rows

    def append_raw(self, postings: list[dict], found_at: str) -> None:
        if not postings:
            return
        rows = [[
            p["id"], found_at, p["posted_at"], p["title"], p["company"], p["location"],
            p["workplace"], p["seniority"], p["employment_type"], p["salary"], p["applicants"],
            p["link"], p["apply_url"], p["status"], "", "", "", "", "", "", "", "", p["description"],
        ] for p in postings]
        self.raw.append_rows(rows, value_input_option="RAW", table_range="A1")

    def write_scores(self, updates: list[tuple[int, dict]]) -> None:
        """updates: (sheet row, {column name: value}) for columns in SCORE_COLS."""
        if not updates:
            return
        first = RAW_COLS.index(SCORE_COLS[0]) + 1
        last = first + len(SCORE_COLS) - 1
        self.raw.batch_update([{
            "range": f"{rowcol_to_a1(row, first)}:{rowcol_to_a1(row, last)}",
            "values": [[vals.get(c, "") for c in SCORE_COLS]],
        } for row, vals in updates], value_input_option="RAW")

    def rebuild_shortlist(self, threshold: int) -> int:
        existing = self.short.get_all_values()
        manual = {}
        if existing:
            idx = {c: i for i, c in enumerate(existing[0])}
            for r in existing[1:]:
                r += [""] * (len(existing[0]) - len(r))
                manual[r[idx["Job ID"]]] = {c: r[idx[c]] for c in MANUAL_COLS if c in idx}

        picked = [r for r in self.raw_rows()
                  if r["Status"] == "scored" and (as_int(r["Fit Score"]) or 0) >= threshold]
        picked.sort(key=lambda r: as_int(r["Fit Score"]), reverse=True)

        rows = []
        for r in picked:
            keep = manual.get(r["Job ID"], {})
            link = r["Link"].replace('"', "%22")
            rows.append([
                as_int(r["Fit Score"]), _literal(r["Title"]), _literal(r["Company"]),
                _literal(r["Location"]), _literal(r["Workplace"]), _literal(r["Posted At"]),
                f'=HYPERLINK("{link}", "Open")' if link else "",
                _literal(r["Why It Fits"]), _literal(r["Gaps"]),
                _literal(keep.get("My Status", "")), _literal(keep.get("Notes", "")),
                _literal(r["Job ID"]),
            ])
        self.short.batch_clear([f"A2:{rowcol_to_a1(max(len(existing), 2), len(SHORT_COLS))}"])
        if rows:
            self.short.update(range_name="A2", values=rows, value_input_option="USER_ENTERED")
        return len(rows)

    def pending_count(self) -> int:
        return sum(r["Status"] == "pending" for r in self.raw_rows())

    def log_run(self, values: dict) -> None:
        self.runs.append_row([values.get(c, "") for c in RUN_COLS], value_input_option="RAW", table_range="A1")

    def gemini_calls_on(self, quota_day: str) -> int:
        """Gemini calls logged for one quota day by any machine; the free quota is per project."""
        day, calls = RUN_COLS.index("Quota Day"), RUN_COLS.index("Gemini Calls")
        return sum(as_int(r[calls]) or 0 for r in self.runs.get_all_values()[1:]
                   if len(r) > day and r[day] == quota_day)
