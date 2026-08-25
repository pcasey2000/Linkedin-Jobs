"""
scrapers/linkedin.py — LinkedIn Jobs Guest API scraper (no login required).

Uses the public guest API endpoint that returns JSON-embedded HTML.
Search keywords and regions come from the user's profile (users/<name>/config.yaml),
passed in by the orchestrator.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from scrapers.base import BaseScraper, Job
from config import LINKEDIN_PAGE_SIZE, LINKEDIN_MAX_PAGES


SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

# Time filter: r86400 = last 24 hours
TIME_FILTER = "r86400"


class LinkedInScraper(BaseScraper):
    """Scrape LinkedIn job listings via the guest API for all configured regions.

    skip_title: optional predicate called with the job title before the detail
        fetch — return True to drop the job without spending a request on it.
        Must mirror the scorer's title dealbreakers so no scoreable job is lost.
    skip_urls: job URLs already in seen_jobs.json — dedup would drop them
        after scraping anyway, so skip their detail fetch up front.
    """

    def __init__(self, skip_title=None, skip_urls: frozenset[str] | set[str] = frozenset()):
        super().__init__()
        self.skip_title = skip_title
        self.skip_urls = skip_urls

    def scrape(self, keywords: list[str], region_config: dict) -> list[Job]:
        """Scrape a single region. Called per-region by scrape_all_regions()."""
        region_name = region_config["_region_name"]
        location = region_config["linkedin_loc"]
        jobs: list[Job] = []
        seen_ids: set[str] = set()

        for keyword in keywords:
            for page in range(LINKEDIN_MAX_PAGES):
                start = page * LINKEDIN_PAGE_SIZE
                params = {
                    "keywords": keyword,
                    "location": location,
                    "f_TPR": TIME_FILTER,
                    "start": start,
                    "count": LINKEDIN_PAGE_SIZE,
                }
                try:
                    resp = self.fetch_with_backoff(SEARCH_URL, params=params)
                except RuntimeError as e:
                    print(f"[LinkedIn] Skipping {keyword}/{location}: {e}")
                    break

                listings = self._parse_listings(resp.text)
                if not listings:
                    break  # no more results for this keyword

                for item in listings:
                    job_id = str(item.get("jobPostingId") or item.get("id", ""))
                    if not job_id or job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)

                    job = self._build_job(item, region_name, job_id)
                    if job:
                        jobs.append(job)

                if len(listings) < LINKEDIN_PAGE_SIZE:
                    break  # last page

        print(f"[LinkedIn] {region_name}: {len(jobs)} jobs scraped")
        return jobs

    def scrape_all_regions(
        self,
        role_keywords: list[str],
        regions: dict,
    ) -> list[Job]:
        """
        Scrape LinkedIn across all of the user's regions.

        role_keywords: search terms from the user profile.
        regions: {region_id: {"linkedin_loc": "..."}} from the user profile.

        Regions run concurrently, each in its own scraper instance so the
        per-domain rate limiter throttles per worker, not globally.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        region_items = [
            (name, dict(cfg, _region_name=name))
            for name, cfg in regions.items()
            if "linkedin_loc" in cfg
        ]
        all_jobs: list[Job] = []
        workers = 4

        def _scrape_region(index: int, region_cfg: dict) -> list[Job]:
            # Stagger the first wave of workers. Each instance has its own
            # rate limiter, so with no stagger all workers fire their first
            # request at the same instant and trip LinkedIn's per-IP
            # throttle (the 429 burst always seen at the start of a scrape).
            time.sleep((index % workers) * 1.5)
            with LinkedInScraper(skip_title=self.skip_title, skip_urls=self.skip_urls) as s:
                return s.scrape(role_keywords, region_cfg)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_scrape_region, i, cfg): name
                for i, (name, cfg) in enumerate(region_items)
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    all_jobs.extend(future.result())
                except Exception as e:
                    print(f"[LinkedIn] {name} failed: {e}")

        return all_jobs

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_listings(self, html: str) -> list[dict[str, Any]]:
        """
        LinkedIn embeds job data as JSON inside the HTML response.
        Extract the job card data from script tags or the card HTML directly.
        """
        # Attempt 1: JSON in <code> tags (common pattern)
        matches = re.findall(r'<code[^>]*>(.*?)</code>', html, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match)
                if isinstance(data, dict) and "data" in data:
                    return data["data"].get("elements", [])
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, TypeError):
                continue

        # Attempt 2: Parse job cards from HTML directly
        return self._parse_cards_from_html(html)

    def _parse_cards_from_html(self, html: str) -> list[dict[str, Any]]:
        """Fallback: extract job data from job card HTML elements."""
        jobs = []
        # Find job card divs — LinkedIn uses data-entity-urn for job IDs
        job_ids = re.findall(r'data-entity-urn="urn:li:jobPosting:(\d+)"', html)
        titles = re.findall(r'class="base-search-card__title"[^>]*>(.*?)</h3>', html, re.DOTALL)
        companies = re.findall(r'class="base-search-card__subtitle"[^>]*>.*?<a[^>]*>(.*?)</a>', html, re.DOTALL)
        locations = re.findall(r'class="job-search-card__location"[^>]*>(.*?)</span>', html, re.DOTALL)

        for i, job_id in enumerate(job_ids):
            jobs.append({
                "jobPostingId": job_id,
                "title": titles[i].strip() if i < len(titles) else "",
                "companyName": companies[i].strip() if i < len(companies) else "",
                "formattedLocation": locations[i].strip() if i < len(locations) else "",
            })
        return jobs

    def _build_job(self, item: dict[str, Any], region_name: str, job_id: str) -> Job | None:
        """Build a Job from a LinkedIn API result dict, fetching description if needed."""
        title = self._clean(item.get("title") or item.get("jobTitle", ""))
        company = self._clean(
            item.get("companyName") or
            item.get("company", {}).get("name", "") if isinstance(item.get("company"), dict) else item.get("companyName", "")
        )
        location = self._clean(item.get("formattedLocation") or item.get("location", ""))
        url = f"https://www.linkedin.com/jobs/view/{job_id}/"

        if not title or not company:
            return None

        # Skip the detail fetch for titles the scorer will reject outright
        if self.skip_title is not None and self.skip_title(title):
            return None

        # Skip the detail fetch for jobs already in seen_jobs.json
        if url in self.skip_urls:
            return None

        # Fetch description from detail endpoint
        description, salary_raw, remote_type = self._fetch_job_detail(job_id)

        return Job(
            title=title,
            company=company,
            location=location,
            region=region_name,
            url=url,
            source="linkedin",
            description=description,
            salary_raw=salary_raw,
            remote_type=remote_type,
        )

    def _fetch_job_detail(self, job_id: str) -> tuple[str, str, str]:
        """Fetch full description from the job detail endpoint."""
        try:
            resp = self.fetch_with_backoff(JOB_DETAIL_URL.format(job_id=job_id))
            html = resp.text
        except RuntimeError:
            return "", "", "unknown"
        except httpx.HTTPStatusError as e:
            # Posting was pulled (404) or otherwise unavailable between
            # the search-listing call and this detail fetch — skip silently
            # so one dead job doesn't abort the whole scrape.
            if e.response.status_code in (404, 410):
                return "", "", "unknown"
            raise

        # Extract description
        desc_match = re.search(
            r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL | re.IGNORECASE
        )
        description = ""
        if desc_match:
            raw = desc_match.group(1)
            description = re.sub(r'<[^>]+>', ' ', raw).strip()
            description = re.sub(r'\s+', ' ', description)

        # Extract salary if present. Amounts must start with a digit (a bare
        # comma must not match) and currency codes need a trailing word
        # boundary — without it "EUR" matches inside "Europe" and "AUD"
        # inside "audit", dragging description text into salary_raw.
        _amount = r'(?:[\$€£]\s?\d[\d,.]*(?:k\b)?|\d[\d,.]*(?:k\b)?\s?(?:EUR|USD|GBP|CAD|SGD|AUD|CHF)\b)'
        salary_match = re.search(
            _amount + r'(?:\s*(?:-|–|—|to)\s*' + _amount + r')?'
            r'(?:\s*(?:per\s+year|per\s+annum|/yr|annually|p\.a\.))?',
            html, re.IGNORECASE
        )
        salary_raw = salary_match.group(0).strip() if salary_match else ""

        # Remote type from workRemoteAllowed or description text
        remote_type = "unknown"
        if '"workRemoteAllowed":true' in html:
            remote_type = "remote"
        elif "hybrid" in html.lower():
            remote_type = "hybrid"
        elif "on-site" in html.lower() or "onsite" in html.lower():
            remote_type = "onsite"

        return description[:3000], salary_raw, remote_type

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r'\s+', ' ', str(text)).strip()
