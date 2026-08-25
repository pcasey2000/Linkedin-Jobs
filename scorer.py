"""
scorer.py — Pure-Python job scoring driven entirely by the user's profile
(users/<name>/config.yaml). No hardcoded preferences, no LLM required.

Scoring rubric:
  1. Role match           (0–40 pts)
  2. Salary               (0–25 pts)
  3. Remote/hybrid        (5–10 pts)
  4. Seniority fit        (10–20 pts)
  5. Company quality      (3 pts, static)
  6. Visa/sponsorship     (0–10 pts, only for regions with visa_signals configured)

Tiers: A=80+, B=60-79, C=40-59, skip=<40 or any dealbreaker.
C-tier jobs are scored and logged but never emailed.

Usage:
  python scorer.py --user me
  python scorer.py --user me --input foo.json --output bar.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from user_profile import UserProfile


# ---------------------------------------------------------------------------
# Salary parsing
# ---------------------------------------------------------------------------

_SALARY_RE = re.compile(
    r"(\d[\d,\.]+)\s*(?:k\b)?",
    re.IGNORECASE,
)


def _parse_salary_range(raw: str) -> tuple[float | None, float | None]:
    """
    Return (low, high) in the base unit (not thousands).
    Returns (None, None) if no salary found.
    Handles "k" suffix, US-style "60,000.00" (comma thousands, dot decimal),
    and European-style "60.000" (dot thousands).
    """
    if not raw:
        return None, None

    raw_lower = raw.lower()
    has_k = "k" in raw_lower

    nums = []
    for m in _SALARY_RE.finditer(raw_lower):
        s = m.group(1).replace(",", "")
        # Heuristic for dots: if there are multiple dots, or a single dot
        # followed by exactly 3 digits, treat dots as European thousands
        # separators and strip them. Otherwise the dot is a decimal point
        # (e.g. "60000.00" → 60000.0) — leave it for float() to parse.
        if s.count(".") > 1 or (s.count(".") == 1 and len(s.rsplit(".", 1)[-1]) == 3):
            s = s.replace(".", "")
        try:
            val = float(s)
        except ValueError:
            continue
        if has_k and val < 10000:
            val *= 1000
        elif val < 500:          # bare number like "45" without k — treat as thousands
            val *= 1000
        nums.append(val)

    if not nums:
        return None, None
    nums.sort()
    return nums[0], nums[-1]


# ---------------------------------------------------------------------------
# Experience requirement parsing
# ---------------------------------------------------------------------------

_EXP_RE = re.compile(
    r"(\d+)\s*\+?\s*(?:to|-)\s*(\d+)\s*years?|"   # "3 to 5 years" / "3-5 years"
    r"(\d+)\s*\+\s*years?|"                         # "5+ years"
    r"(\d+)\s*years?\s+(?:of\s+)?experience",        # "3 years experience"
    re.IGNORECASE,
)


def _years_required(text: str) -> tuple[int | None, int | None]:
    """
    Return (min_years, max_years) of experience required, or (None, None).
    For ranges like "3-5 years": min=3, max=5.
    For "3+ years" or "3 years experience": min=max=3.
    """
    min_val: int | None = None
    max_val: int | None = None
    for m in _EXP_RE.finditer(text):
        lo_s, hi_s, plus_s, exact_s = m.groups()
        if hi_s:            # range pattern: lo_s and hi_s both set
            lo, hi = int(lo_s), int(hi_s)
        elif plus_s:        # "X+ years"
            lo = hi = int(plus_s)
        elif exact_s:       # "X years experience"
            lo = hi = int(exact_s)
        else:
            continue
        if min_val is None or lo < min_val:
            min_val = lo
        if max_val is None or hi > max_val:
            max_val = hi
    return min_val, max_val


# ---------------------------------------------------------------------------
# Word-boundary title matcher
# ---------------------------------------------------------------------------

# Cache compiled patterns so we don't rebuild them on every job.
_WORD_BOUNDARY_CACHE: dict[str, re.Pattern] = {}


def _matches_word(text: str, terms) -> bool:
    """
    True if any term appears in text as a whole word/phrase (regex \\b...\\b).

    Multi-word terms like "head of" or "staff engineer" are matched as phrases,
    so "Head of Engineering" matches but "ahead office" does not. Punctuation
    counts as a word boundary, so "Lead," and "Sr." match "lead" / "sr".
    """
    for term in terms:
        stripped = term.strip().lower()
        if not stripped:
            continue
        pattern = _WORD_BOUNDARY_CACHE.get(stripped)
        if pattern is None:
            pattern = re.compile(rf'\b{re.escape(stripped)}\b', re.IGNORECASE)
            _WORD_BOUNDARY_CACHE[stripped] = pattern
        if pattern.search(text):
            return True
    return False


# ---------------------------------------------------------------------------
# Companies to avoid
# ---------------------------------------------------------------------------

def _load_avoid_companies(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        text = f.read()
    companies = set()
    for line in text.splitlines():
        line = line.strip().lstrip("-").strip()
        if line and not line.startswith("#"):
            companies.add(line.lower())
    return companies


# ---------------------------------------------------------------------------
# Core scorer
# ---------------------------------------------------------------------------

def score_job(
    job: dict[str, Any],
    avoid_companies: set[str],
    profile: "UserProfile",
) -> dict[str, Any] | None:
    """
    Score a single job dict against the user's profile.
    Returns a scored dict, or None if the job should be skipped.
    """
    title: str = job.get("title", "")
    company: str = job.get("company", "")
    description: str = job.get("description", "")
    salary_raw: str = job.get("salary_raw", "") or ""
    remote_type: str = (job.get("remote_type", "") or "").lower()
    region: str = (job.get("region", "") or "").lower()

    title_lower = title.lower()
    desc_lower = description.lower()
    full_text = f"{title_lower} {desc_lower}"

    reasons: list[str] = []
    score = 0

    # ---- Resolve scoring config from the profile ---------------------------
    _tech_terms = set(profile.tech_terms)
    _core_tech_terms = set(profile.core_tech_terms)
    _role_titles = set(profile.role_titles)
    _excluded_title_terms = set(profile.excluded_role_terms)
    _excluded_desc_terms = set(profile.excluded_description_terms)
    _lang_patterns = profile._lang_patterns
    _max_years_exp = profile.max_years_exp
    _title_filter = profile.senior_title_filter()
    _urban_centers = profile.urban_centers
    _visa_signals = profile.visa_signals
    _visa_mncs = profile.visa_large_mncs
    # Salary config: look up by region id from profile
    _region_cfg = next(
        (r for r in profile.regions if r.id == region), None
    )
    _min_sal = _region_cfg.min_salary if _region_cfg else None
    _target_lo = _region_cfg.target_salary[0] if _region_cfg else None

    # ---- Dealbreaker: company in avoid list --------------------------------
    if company.lower() in avoid_companies:
        return None

    # ---- Dealbreaker: urban center filter ----------------------------------
    # For broad country-level regions, filter to the configured cities.
    location_lower = (job.get("location", "") or "").lower()
    if region in _urban_centers:
        if location_lower and not any(city in location_lower for city in _urban_centers[region]):
            return None

    # ---- Dealbreaker: excluded role terms in the title ---------------------
    # These are wrong-track role names (e.g. "helpdesk" for an infrastructure
    # engineer, "software engineer" for a supply-chain analyst). Matching the
    # TITLE only avoids false positives where the description mentions
    # adjacent teams or technologies in passing.
    # Word-boundary regex so "Lead," / "Sr." / "- VP" all match correctly while
    # "leadership" is still safe from "lead".
    #
    # Titles are also checked with "&" normalised to "and" so exclusion terms
    # written either way both hit (e.g. "Analyst, Financial Planning &
    # Analysis" vs a "financial planning and analysis" exclusion).
    title_amp_norm = title_lower.replace("&", "and")
    if (_matches_word(title_lower, _excluded_title_terms)
            or _matches_word(title_amp_norm, _excluded_title_terms)):
        return None

    # ---- Dealbreaker: excluded description terms anywhere in the text ------
    # Reserved for niche signals that uniquely identify a wrong-fit role even
    # when the title is generic (e.g. "earthworks" → civil engineering).
    if _excluded_desc_terms and any(t in full_text for t in _excluded_desc_terms):
        return None

    # ---- Dealbreaker: seniority title filter --------------------------------
    if _title_filter and (_matches_word(title_lower, _title_filter)
                          or _matches_word(title_amp_norm, _title_filter)):
        return None

    # ---- 1. Role match (0-40) ----------------------------------------------
    all_tech_hits = [t for t in _tech_terms if t in full_text]
    tech_count = len(all_tech_hits)
    title_matches_role = any(t in title_lower for t in _role_titles)

    # Domain confirmation: without at least one core domain term hit, generic
    # business vocabulary alone (reporting/kpi/excel/governance) can carry a
    # non-domain role into A-tier. Zero out title and tech credit in that case.
    if _core_tech_terms and not any(t in full_text for t in _core_tech_terms):
        title_matches_role = False
        tech_count = 0

    if title_matches_role and tech_count >= 3:
        role_score = 40
    elif title_matches_role and tech_count >= 1:
        role_score = 32
    elif title_matches_role:
        role_score = 24
    elif tech_count >= 4:
        role_score = 32
    elif tech_count >= 2:
        role_score = 20
    elif tech_count >= 1:
        role_score = 12
    else:
        role_score = 0

    score += role_score
    if all_tech_hits:
        reasons.append(f"Tech: {', '.join(list(all_tech_hits)[:3])}")

    # ---- Priority cluster bias ---------------------------------------------
    # Boosts roles in the user's primary sub-field and demotes titles that
    # only match the secondary cluster (see priority_* fields in config.yaml).
    priority_titles = set(profile.priority_role_titles or [])
    priority_terms = set(profile.priority_tech_terms or [])
    secondary_titles = set(profile.secondary_role_titles or [])

    title_has_priority = any(t in title_lower for t in priority_titles)
    desc_has_priority = any(t in full_text for t in priority_terms)

    if title_has_priority:
        score += 8
        reasons.append("Priority track (title)")
    if desc_has_priority:
        score += 5
        if not title_has_priority:
            reasons.append("Priority signal in description")

    title_has_secondary_only = (
        any(t in title_lower for t in secondary_titles)
        and not title_has_priority
    )
    if title_has_secondary_only and not desc_has_priority:
        cap = 24
        if role_score > cap:
            score -= (role_score - cap)
            reasons.append("Secondary track (role score capped)")
    # Secondary-only titles can still reach the B-tier email via salary/visa/
    # remote bonuses, and a passing priority-term mention in the description is
    # enough to escape the role-score cap. Demote harder: any secondary-only
    # TITLE is hard-capped to C-tier at the end of scoring unless the TITLE
    # itself has a priority signal. C-tier jobs are logged but never emailed.
    secondary_tier_capped = title_has_secondary_only

    # ---- Incompatible language check ----------------------------------------
    lang_hits: list[str] = []
    total_lang_count = 0
    for lang, pattern in _lang_patterns.items():
        count = len(pattern.findall(full_text))
        if count >= 1:
            lang_hits.append(lang)
            total_lang_count += count

    # Dealbreaker: language-dominated role with no target tech signal at all
    if total_lang_count >= 3 and len(all_tech_hits) == 0:
        return None

    if lang_hits:
        reasons.append(f"Requires: {', '.join(lang_hits)}")

    # ---- 2. Salary (0-25) --------------------------------------------------
    sal_lo, sal_hi = _parse_salary_range(salary_raw)

    if sal_lo is None:
        salary_score = 15  # benefit of doubt
        reasons.append("Salary not listed")
    elif _min_sal and sal_lo < _min_sal:
        return None  # Dealbreaker: below minimum
    elif _target_lo and sal_lo >= _target_lo:
        salary_score = 25
        reasons.append(f"Salary in target range ({salary_raw})")
    elif _min_sal and sal_lo >= _min_sal:
        salary_score = 20
        reasons.append(f"Salary meets minimum ({salary_raw})")
    else:
        salary_score = 15

    score += salary_score

    # ---- 3. Remote/hybrid (5-10) -------------------------------------------
    if remote_type == "hybrid":
        remote_score = 10
        reasons.append("Hybrid")
    elif remote_type in ("remote", "fully remote"):
        remote_score = 10
        reasons.append("Fully remote")
    elif remote_type in ("onsite", "on-site", "on site"):
        remote_score = 5
        reasons.append("Onsite")
    else:
        remote_score = 5

    score += remote_score

    # ---- 4. Seniority fit (10-20) ------------------------------------------
    min_yrs, _ = _years_required(full_text)

    grad_terms = {"graduate", "junior", "associate", "entry level", "entry-level", "trainee"}
    is_grad_role = any(t in full_text for t in grad_terms)

    # Dealbreaker: role requires more experience than the user has
    if min_yrs is not None and min_yrs > _max_years_exp:
        return None

    if min_yrs is not None and min_yrs <= _max_years_exp:
        seniority_score = 20
        reasons.append(f"{min_yrs}yr exp required")
    elif is_grad_role:
        seniority_score = 15
        reasons.append("Graduate/junior level")
    else:
        seniority_score = 10  # unspecified — neutral

    score += seniority_score

    # ---- 5. Company quality (3 pts, static) --------------------------------
    score += 3

    # ---- 6. Visa / sponsorship bonus (0-10) --------------------------------
    bonus = 0
    region_visa_signals = _visa_signals.get(region, [])
    region_visa_mncs = _visa_mncs.get(region, [])
    if region_visa_signals and any(t in full_text for t in region_visa_signals):
        bonus = 10
        reasons.append("Visa sponsorship mentioned")
    elif region_visa_mncs and any(mnc in company.lower() for mnc in region_visa_mncs):
        bonus = 5
        reasons.append("Large MNC (likely visa sponsor)")
    score += bonus

    # ---- Secondary-track tier cap (see priority cluster block above) -------
    if secondary_tier_capped and score > 55:
        score = 55
        reasons.append("Secondary track — held to C-tier")

    # ---- Tier ---------------------------------------------------------------
    if score >= 80:
        tier = "A"
    elif score >= 60:
        tier = "B"
    elif score >= 40:
        tier = "C"
    else:
        tier = "skip"

    if tier == "skip":
        return None

    match_reason = ". ".join(reasons) + f". Score: {score}."

    return {
        "id": job.get("id", ""),
        "title": title,
        "company": company,
        "location": job.get("location", ""),
        "region": region,
        "url": job.get("url", ""),
        "salary_raw": salary_raw,
        "remote_type": remote_type,
        "source": job.get("source", ""),
        "score": score,
        "tier": tier,
        "match_reason": match_reason,
        "cv_to_use": profile.select_cv(title, description),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Score job candidates")
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--user", default="me", help="Load scoring config from users/<name>/config.yaml")
    args = parser.parse_args()

    from user_profile import load_profile
    profile = load_profile(args.user)
    input_file = args.input or os.path.join(profile.data_dir, "candidates_new.json")
    output_file = args.output or os.path.join(profile.data_dir, "candidates_scored.json")
    avoid_file = profile.companies_to_avoid_file

    with open(input_file, encoding="utf-8") as f:
        jobs = json.load(f)

    avoid = _load_avoid_companies(avoid_file)

    scored = []
    skipped = 0
    for job in jobs:
        result = score_job(job, avoid, profile)
        if result:
            scored.append(result)
        else:
            skipped += 1

    # Sort: tier A first, then by score descending
    scored.sort(key=lambda j: (0 if j["tier"] == "A" else 1, -j["score"]))

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(scored, f, indent=2, ensure_ascii=False)

    tier_a = sum(1 for j in scored if j["tier"] == "A")
    tier_b = sum(1 for j in scored if j["tier"] == "B")
    tier_c = sum(1 for j in scored if j["tier"] == "C")
    print(f"Scoring complete: {len(jobs)} jobs scored, {tier_a} tier-A, {tier_b} tier-B, {tier_c} tier-C, {skipped} skipped.")


if __name__ == "__main__":
    main()
