# config.py — pipeline-wide constants shared by every user.
#
# Nothing personal lives here. Everything about *you* — search keywords,
# regions, salary targets, filters — lives in users/<name>/config.yaml.

import os

# ---------------------------------------------------------------------------
# Rate limits (seconds between requests per domain)
# ---------------------------------------------------------------------------

RATE_LIMITS = {
    "linkedin.com": 2.5,
    "www.linkedin.com": 2.5,
    "default": 2.0,
}

# ---------------------------------------------------------------------------
# Scraper settings
# ---------------------------------------------------------------------------

MAX_RETRIES = 5
BASE_DELAY = 2.0       # seconds, exponential backoff base
JITTER_MAX = 1.0       # seconds of random jitter added to backoff
LINKEDIN_PAGE_SIZE = 25
LINKEDIN_MAX_PAGES = 3     # 75 results per keyword/region combo
MAX_JOBS_PER_REGION = 25   # cap applied after dedup, before scoring
MAX_EMAIL_JOBS = 20        # top N jobs (by score) included in the daily email

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
