"""
user_profile.py — Per-user configuration loaded from YAML.

Each user has a config file at users/<name>/config.yaml.
The orchestrator loads this with --user <name> and passes it through
the scraper, deduplicator, scorer, and email sender.

Minimal required YAML fields:
    name, email, regions (list), role_keywords (list)

Everything else has sensible defaults.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise ImportError("PyYAML is required: pip install pyyaml") from exc


@dataclass
class RegionConfig:
    id: str
    linkedin_loc: str
    currency: str
    min_salary: int
    target_salary: tuple[int, int]
    urban_center: bool = False


@dataclass
class CVConfig:
    path: str
    match_terms: list[str]  # lowercased terms used to select this CV


@dataclass
class UserProfile:
    # Identity
    name: str
    email: str

    # LinkedIn search terms
    role_keywords: list[str]
    tech_keywords: list[str]

    # Scoring signals
    tech_terms: list[str]           # flat list of tech to match in job text
    role_titles: list[str]          # title keywords that flag a target role
    excluded_role_terms: list[str]  # dealbreaker terms checked against the job title
    excluded_description_terms: list[str]  # dealbreaker terms checked against the full description

    # Domain confirmation: subset of tech_terms that uniquely signal the target
    # domain. When non-empty, at least one of these must appear in the job text
    # for the role-match score to be non-zero. Prevents generic terms like
    # "reporting" or "kpi" from carrying non-domain roles into A-tier.
    # Empty list = no domain check (legacy behaviour).
    core_tech_terms: list[str]

    # Incompatible language filtering
    incompatible_languages: list[str]

    # Seniority: graduate | junior | mid | senior
    seniority: str
    max_years_exp: int              # filter roles requiring more than this

    # Visa/location
    requires_sponsorship: bool
    regions: list[RegionConfig]
    urban_centers: dict[str, list[str]]  # broad_region_id → city list

    # Visa signal terms per region
    visa_signals: dict[str, list[str]]
    visa_large_mncs: dict[str, list[str]]

    # CVs (pick best match per job)
    cvs: list[CVConfig]

    # File paths
    companies_to_avoid_file: str
    data_dir: str
    seen_jobs_file: str

    # Priority/secondary cluster bias — lets a user prefer one sub-field over
    # an adjacent one (e.g. planning roles over procurement roles).
    # Empty lists = no bias applied (the default).
    priority_role_titles: list[str] = field(default_factory=list)
    priority_tech_terms: list[str] = field(default_factory=list)
    secondary_role_titles: list[str] = field(default_factory=list)

    # Optional: explicit override for senior_title_filter().
    # When set, this list of lowercase substrings replaces the seniority-based default.
    # Useful for fields where "Manager" is a junior/mid title (e.g. supply chain).
    excluded_title_terms: list[str] | None = None

    # Derived: compiled regex patterns for incompatible languages
    _lang_patterns: dict[str, re.Pattern] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._lang_patterns = {
            lang: re.compile(rf'\b{re.escape(lang.lower())}\b', re.IGNORECASE)
            for lang in self.incompatible_languages
        }

    @property
    def all_search_keywords(self) -> list[str]:
        """Deduplicated union of role and tech keywords for LinkedIn."""
        seen: set[str] = set()
        result = []
        for kw in self.role_keywords + self.tech_keywords:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result

    def select_cv(self, title: str, description: str) -> str | None:
        """Return the best-matching CV path for a given job, or None if no CVs configured."""
        if not self.cvs:
            return None
        if len(self.cvs) == 1:
            return self.cvs[0].path
        text = f"{title} {description}".lower()
        best = max(self.cvs, key=lambda cv: sum(1 for t in cv.match_terms if t in text))
        return best.path

    def senior_title_filter(self) -> set[str]:
        """
        Return title terms to filter out as too-senior for the user.

        If excluded_title_terms is set in the config, use it verbatim — this lets
        users override the seniority default (e.g. supply-chain roles where
        "Manager" is a junior/mid title, not a people-management role).
        """
        if self.excluded_title_terms is not None:
            return {t.lower() for t in self.excluded_title_terms}
        if self.seniority in ("graduate", "junior"):
            return {"senior", "lead ", "principal", "staff engineer",
                    "head of", "director", "vp ", "vice president", "manager"}
        if self.seniority == "mid":
            return {"head of", "director", "vp ", "vice president"}
        return set()


def load_profile(user_name: str, base_dir: str | None = None) -> UserProfile:
    """Load and parse users/<user_name>/config.yaml into a UserProfile."""
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    config_path = os.path.join(base_dir, "users", user_name, "config.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"User config not found: {config_path}\n"
            f"Create it at users/{user_name}/config.yaml"
        )

    with open(config_path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    return _parse_profile(raw, base_dir, user_name)


def _parse_profile(raw: dict[str, Any], base_dir: str, user_name: str) -> UserProfile:
    def resolve(path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(base_dir, path)

    # Regions
    regions: list[RegionConfig] = []
    for r in raw.get("regions", []):
        target = r.get("target_salary", [None, None])
        regions.append(RegionConfig(
            id=r["id"],
            linkedin_loc=r["linkedin_loc"],
            currency=r.get("currency", ""),
            min_salary=int(r.get("min_salary", 0)),
            target_salary=(int(target[0]) if target[0] else 0,
                           int(target[1]) if target[1] else 0),
            urban_center=bool(r.get("urban_center", False)),
        ))

    # CVs
    cvs: list[CVConfig] = []
    for cv in raw.get("cvs", []):
        cvs.append(CVConfig(
            path=resolve(cv["path"]),
            match_terms=[t.lower() for t in cv.get("match_terms", [])],
        ))

    # Default paths (per-user subdirectory)
    user_dir = os.path.join(base_dir, "users", user_name)
    default_data_dir = os.path.join(user_dir, "data")
    default_seen = os.path.join(user_dir, "seen_jobs.json")
    default_avoid = os.path.join(base_dir, "companies_to_avoid.md")

    raw_excluded_title = raw.get("excluded_title_terms")
    excluded_title_terms = (
        [t.lower() for t in raw_excluded_title]
        if raw_excluded_title is not None
        else None
    )

    return UserProfile(
        name=raw["name"],
        email=raw["email"],
        role_keywords=raw.get("role_keywords", []),
        tech_keywords=raw.get("tech_keywords", []),
        tech_terms=[t.lower() for t in raw.get("tech_terms", [])],
        role_titles=[t.lower() for t in raw.get("role_titles", [])],
        excluded_role_terms=[t.lower() for t in raw.get("excluded_role_terms", [])],
        excluded_description_terms=[t.lower() for t in raw.get("excluded_description_terms", [])],
        core_tech_terms=[t.lower() for t in raw.get("core_tech_terms", [])],
        incompatible_languages=raw.get("incompatible_languages", []),
        seniority=raw.get("seniority", "junior"),
        max_years_exp=int(raw.get("max_years_exp", 99)),
        requires_sponsorship=bool(raw.get("requires_sponsorship", False)),
        regions=regions,
        urban_centers={
            k: [c.lower() for c in v]
            for k, v in raw.get("urban_centers", {}).items()
        },
        visa_signals={
            k: [t.lower() for t in v]
            for k, v in raw.get("visa_signals", {}).items()
        },
        visa_large_mncs={
            k: [t.lower() for t in v]
            for k, v in raw.get("visa_large_mncs", {}).items()
        },
        cvs=cvs,
        companies_to_avoid_file=resolve(
            raw.get("companies_to_avoid_file", default_avoid)
        ),
        data_dir=resolve(raw.get("data_dir", default_data_dir)),
        seen_jobs_file=resolve(raw.get("seen_jobs_file", default_seen)),
        excluded_title_terms=excluded_title_terms,
        priority_role_titles=[t.lower() for t in raw.get("priority_role_titles", [])],
        priority_tech_terms=[t.lower() for t in raw.get("priority_tech_terms", [])],
        secondary_role_titles=[t.lower() for t in raw.get("secondary_role_titles", [])],
    )
