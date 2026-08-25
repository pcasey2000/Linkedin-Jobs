"""
scrapers/base.py — Abstract base class and Job dataclass shared by all scrapers.
"""

from __future__ import annotations

import hashlib
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from config import MAX_RETRIES, BASE_DELAY, JITTER_MAX, RATE_LIMITS


# ---------------------------------------------------------------------------
# Job dataclass
# ---------------------------------------------------------------------------

@dataclass
class Job:
    title: str
    company: str
    location: str
    region: str          # region id from the user's config.yaml
    url: str
    source: str          # e.g. "linkedin"
    description: str = ""
    salary_raw: str = ""
    remote_type: str = "unknown"   # "remote" | "hybrid" | "onsite" | "unknown"
    posted_date: str = ""          # ISO date string if available
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    id: str = field(init=False)

    def __post_init__(self):
        self.id = self._make_id()
        self.remote_type = self._infer_remote_type()

    def _make_id(self) -> str:
        raw = f"{self.title.lower().strip()}|{self.company.lower().strip()}|{self.url.strip()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _infer_remote_type(self) -> str:
        if self.remote_type not in ("unknown", ""):
            return self.remote_type
        text = f"{self.title} {self.location} {self.description}".lower()
        if "fully remote" in text or "100% remote" in text or "remote only" in text:
            return "remote"
        if "remote" in text and "hybrid" not in text:
            return "remote"
        if "hybrid" in text:
            return "hybrid"
        if "on-site" in text or "onsite" in text or "office" in text:
            return "onsite"
        return "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Base scraper
# ---------------------------------------------------------------------------

class BaseScraper(ABC):
    """
    All scrapers inherit from this class.

    Provides:
    - fetch_with_backoff(): HTTP GET with exponential backoff + jitter
    - _sleep_for_domain(): per-domain rate limiting
    """

    def __init__(self):
        self._last_request: dict[str, float] = {}
        self._client = httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

    # ------------------------------------------------------------------
    # HTTP with exponential backoff
    # ------------------------------------------------------------------

    def fetch_with_backoff(
        self,
        url: str,
        method: str = "GET",
        params: dict | None = None,
        headers: dict | None = None,
        **kwargs,
    ) -> httpx.Response:
        """
        Fetch a URL with exponential backoff on 429 / 5xx / connection errors.
        Respects per-domain rate limiting.
        """
        domain = urlparse(url).netloc
        self._sleep_for_domain(domain)

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.request(
                    method, url, params=params, headers=headers, **kwargs
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = self._backoff(attempt)
                    # Prefer the server's own Retry-After (seconds) when given —
                    # guessing shorter burns retry attempts, guessing longer
                    # wastes time. Capped so a hostile value can't stall the run.
                    retry_after = resp.headers.get("retry-after")
                    if retry_after:
                        try:
                            wait = min(max(float(retry_after), 1.0), 60.0) + random.uniform(0, JITTER_MAX)
                        except ValueError:
                            pass
                    print(f"[{self.__class__.__name__}] HTTP {resp.status_code} — retrying in {wait:.1f}s (attempt {attempt+1}/{MAX_RETRIES})")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                self._last_request[domain] = time.monotonic()
                return resp
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                last_exc = e
                wait = self._backoff(attempt)
                print(f"[{self.__class__.__name__}] {type(e).__name__} — retrying in {wait:.1f}s (attempt {attempt+1}/{MAX_RETRIES})")
                time.sleep(wait)

        raise RuntimeError(
            f"[{self.__class__.__name__}] Failed after {MAX_RETRIES} attempts for {url}: {last_exc}"
        )

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff: BASE_DELAY * 2^attempt + random jitter."""
        return BASE_DELAY * (2 ** attempt) + random.uniform(0, JITTER_MAX)

    def _sleep_for_domain(self, domain: str) -> None:
        """Wait so we don't exceed per-domain rate limit."""
        min_interval = RATE_LIMITS.get(domain, RATE_LIMITS["default"])
        last = self._last_request.get(domain, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed + random.uniform(0, 0.3))

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def scrape(self, keywords: list[str], region_config: dict) -> list[Job]:
        """
        Scrape jobs for the given keywords and region config.
        Returns a list of Job objects.
        """
        ...

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
