"""
orchestrator.py — Daily run master script.

Workflow:
  1. Scrape LinkedIn for the user's keywords across their regions
  2. Deduplicate against users/<name>/seen_jobs.json
  3. Filter non-English postings, cap jobs per region
  4. Score every job against the user's profile → candidates_scored.json
  5. Email the shortlist via send_morning_email.py (Gmail API)

Usage:
  python orchestrator.py                     # full run for users/me/config.yaml
  python orchestrator.py --user alex         # full run for users/alex/config.yaml
  python orchestrator.py --dry-run           # scrape + score but don't send email
  python orchestrator.py --skip-scrape       # use existing candidates_new.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

from config import LOGS_DIR, MAX_JOBS_PER_REGION
from deduplicator import deduplicate


# ---------------------------------------------------------------------------
# English-language filter
# ---------------------------------------------------------------------------

_GERMAN_STOPWORDS = {
    "und", "mit", "für", "auf", "der", "die", "das", "ist", "im",
    "zu", "von", "wir", "sie", "sich", "auch", "werden", "eine",
    "eines", "einem", "einen", "oder", "nicht", "haben", "können",
}
_FRENCH_STOPWORDS = {
    "les", "des", "une", "pour", "sur", "dans", "est", "par",
    "qui", "que", "nous", "vous", "avec", "sont", "cette", "leur",
}
_DUTCH_STOPWORDS = {
    "het", "een", "zijn", "wij", "naar", "ook", "niet", "bij",
    "voor", "dat", "worden", "heeft", "kunnen", "jouw", "jij",
}
_SPANISH_STOPWORDS = {
    "los", "las", "una", "para", "con", "por", "del", "que",
    "como", "más", "nuestro", "nuestra", "empresa", "trabajo",
    "experiencia", "años", "sobre", "será",
}
_PORTUGUESE_STOPWORDS = {
    "uma", "dos", "das", "com", "pelo", "pela", "seu", "sua",
    "nosso", "nossa", "equipa", "equipe", "anos", "voce", "tem",
    "desenvolvimento", "conhecimento", "requisitos", "vaga",
    "oportunidade", "candidato",
}


def _is_english(job) -> bool:
    """Return False if the job title+description appears to be non-English."""
    text = f"{job.title} {job.description}".lower()
    words = set(re.findall(r'\b[a-z]{2,}\b', text))
    for stopwords in (
        _GERMAN_STOPWORDS, _FRENCH_STOPWORDS, _DUTCH_STOPWORDS,
        _SPANISH_STOPWORDS, _PORTUGUESE_STOPWORDS,
    ):
        if len(words & stopwords) >= 3:
            return False
    return True


# ---------------------------------------------------------------------------
# Per-region cap
# ---------------------------------------------------------------------------

def _cap_per_region(jobs: list, max_per_region: int) -> list:
    """Keep at most max_per_region jobs per region, preserving scrape order."""
    counts: dict[str, int] = {}
    result = []
    for job in jobs:
        region = job.region if hasattr(job, "region") else job.get("region", "")
        n = counts.get(region, 0)
        if n < max_per_region:
            result.append(job)
            counts[region] = n + 1
    return result


# ---------------------------------------------------------------------------
# Scrape
# ---------------------------------------------------------------------------

def run_user_scrape(profile) -> list:
    """LinkedIn scrape for the given UserProfile."""
    from scrapers.linkedin import LinkedInScraper
    from scorer import _matches_word
    from deduplicator import _load_seen

    # Build regions dict in the format the linkedin scraper expects
    regions = {r.id: {"linkedin_loc": r.linkedin_loc} for r in profile.regions}

    # Titles the scorer rejects unconditionally (seniority filter + wrong-track
    # role names). Skipping them at scrape time saves a rate-limited detail
    # fetch per job; matching mirrors the scorer exactly (word boundaries plus
    # "&"→"and" normalisation) so no scoreable job is lost.
    skip_terms = profile.senior_title_filter() | {
        t.lower() for t in profile.excluded_role_terms
    }

    def _skip_title(title: str) -> bool:
        title_lower = title.lower()
        return (_matches_word(title_lower, skip_terms)
                or _matches_word(title_lower.replace("&", "and"), skip_terms))

    # Jobs already recorded in this user's seen_jobs.json don't need a fresh
    # detail fetch either — dedup would discard them right after the scrape.
    seen_urls = frozenset(_load_seen(profile.seen_jobs_file)["urls"])

    with LinkedInScraper(skip_title=_skip_title, skip_urls=seen_urls) as s:
        jobs = s.scrape_all_regions(
            role_keywords=profile.all_search_keywords,
            regions=regions,
        )
    print(f"[Orchestrator] LinkedIn ({profile.name}): {len(jobs)} jobs")
    return jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Daily job search run")
    parser.add_argument("--dry-run", action="store_true", help="Score but don't send email")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping, use existing candidates_new.json")
    parser.add_argument("--user", default="me", help="Which user to run for: loads users/<name>/config.yaml")
    args = parser.parse_args()

    from user_profile import load_profile
    from scorer import score_job, _load_avoid_companies

    profile = load_profile(args.user)
    os.makedirs(profile.data_dir, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)

    raw_file = os.path.join(profile.data_dir, "candidates_raw.json")
    new_file = os.path.join(profile.data_dir, "candidates_new.json")
    scored_file = os.path.join(profile.data_dir, "candidates_scored.json")

    start = datetime.now()
    print(f"\n{'='*60}")
    print(f"Job Search — {profile.name} — {start.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    if not args.skip_scrape:
        print(f"[Orchestrator] Scraping LinkedIn for {profile.name}...")
        all_jobs = run_user_scrape(profile)
        print(f"[Orchestrator] Total scraped: {len(all_jobs)} jobs")

        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump([j.to_dict() for j in all_jobs], f, indent=2, ensure_ascii=False)

        new_jobs = deduplicate(all_jobs, seen_jobs_file=profile.seen_jobs_file)

        before_lang = len(new_jobs)
        new_jobs = [j for j in new_jobs if _is_english(j)]
        print(f"[Orchestrator] Language filter: {before_lang} → {len(new_jobs)}")

        new_jobs = _cap_per_region(new_jobs, MAX_JOBS_PER_REGION)
        print(f"[Orchestrator] After region cap: {len(new_jobs)} jobs")

        if not new_jobs:
            print("[Orchestrator] No new jobs today. Exiting.")
            return

        with open(new_file, "w", encoding="utf-8") as f:
            json.dump([j.to_dict() for j in new_jobs], f, indent=2, ensure_ascii=False)
    else:
        if not os.path.exists(new_file):
            print(f"[Orchestrator] --skip-scrape: {new_file} not found", file=sys.stderr)
            sys.exit(1)
        with open(new_file, encoding="utf-8") as f:
            new_jobs = json.load(f)
        print(f"[Orchestrator] --skip-scrape: loaded {len(new_jobs)} jobs")

    # Score
    print(f"[Orchestrator] Scoring jobs for {profile.name}...")
    avoid = _load_avoid_companies(profile.companies_to_avoid_file)
    scored, skipped = [], 0
    job_dicts = new_jobs if isinstance(new_jobs[0], dict) else [j.to_dict() for j in new_jobs]
    for job in job_dicts:
        result = score_job(job, avoid, profile)
        if result:
            scored.append(result)
        else:
            skipped += 1

    scored.sort(key=lambda j: (0 if j["tier"] == "A" else 1, -j["score"]))
    with open(scored_file, "w", encoding="utf-8") as f:
        json.dump(scored, f, indent=2, ensure_ascii=False)

    a = sum(1 for j in scored if j["tier"] == "A")
    b = sum(1 for j in scored if j["tier"] == "B")
    print(f"[Orchestrator] Scoring complete: {a} A-tier, {b} B-tier, {skipped} skipped.")

    # Email
    if args.dry_run:
        print(f"[Orchestrator] --dry-run: skipping email. Check {scored_file}")
    else:
        print(f"[Orchestrator] Sending email to {profile.email}...")
        result = subprocess.run(
            [sys.executable, "send_morning_email.py", "--user", args.user],
            capture_output=False,
            text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if result.returncode != 0:
            print("[Orchestrator] Email send failed.", file=sys.stderr)
            sys.exit(1)

    elapsed = (datetime.now() - start).seconds
    print(f"\n[Orchestrator] Run complete in {elapsed}s")


if __name__ == "__main__":
    main()
