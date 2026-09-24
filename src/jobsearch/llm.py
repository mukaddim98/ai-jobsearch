from __future__ import annotations

import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors, types


class QuotaExhausted(RuntimeError):
    """Gemini can't be called right now: our daily cap, Google's free-tier quota, or a paid project out of credit."""


def quota_day() -> str:
    """Gemini's daily quotas reset at midnight Pacific time."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


class Gemini:
    def __init__(self, api_key: str, requests_per_minute: float, budget: int):
        self.client = genai.Client(api_key=api_key)
        self.interval = 60.0 / requests_per_minute
        self.budget = budget  # calls this process may still make today
        self.calls = 0
        self._last = 0.0

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.calls)

    def generate(self, model: str, system: str, prompt: str, schema=None, temperature: float = 0.2):
        """Plain text, or a parsed instance of `schema` (a pydantic model) when one is given."""
        config = types.GenerateContentConfig(system_instruction=system, temperature=temperature)
        if schema is not None:
            config.response_mime_type = "application/json"
            config.response_schema = schema
        for attempt in range(5):
            if self.remaining == 0:
                raise QuotaExhausted("Daily Gemini cap (scoring.daily_request_cap) reached.")
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1  # count every request sent, including ones that fail
            try:
                resp = self.client.models.generate_content(model=model, contents=prompt, config=config)
            except errors.APIError as e:
                detail = f"{e.message} {e.details}"
                if e.code == 402:
                    raise QuotaExhausted(
                        "This Gemini key's project has prepaid billing with no credit left, so it is not on "
                        "the free tier. Disable billing on the project in AI Studio, or use a key from a "
                        "project without billing.") from e
                if e.code == 429 and "PerDay" in detail:
                    raise QuotaExhausted("Gemini's free daily quota is used up (resets midnight Pacific).") from e
                if e.code not in (429, 500, 503):
                    raise
                if attempt == 4:
                    if e.code == 429:
                        raise QuotaExhausted(f"Gemini kept rate-limiting: {e.message}") from e
                    raise
                # Per-minute limit or transient error: wait as long as Google asks, else back off.
                m = re.search(r"retryDelay\W+(\d+(?:\.\d+)?)s", detail)
                time.sleep(float(m.group(1)) + 1 if m else 15 * 2 ** attempt)
                continue
            if schema is None:
                return resp.text
            if resp.parsed is None:
                raise ValueError(f"Model returned unparseable output: {resp.text[:200]!r}")
            return resp.parsed
