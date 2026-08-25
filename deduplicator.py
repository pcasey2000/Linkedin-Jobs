"""
deduplicator.py — Three-layer deduplication against seen_jobs.json.

Layers:
  1. URL exact match
  2. Job ID match (SHA256 of title+company+url)
  3. Fuzzy title+company match (difflib ratio > 0.85)

After filtering, appends new job IDs/URLs to seen_jobs.json atomically.
"""

from __future__ import annotations

import json
import os
import tempfile
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scrapers.base import Job

FUZZY_THRESHOLD = 0.85


def _load_seen(seen_jobs_file: str) -> dict:
    """Load seen_jobs.json. Returns dict with 'urls' and 'ids' sets."""
    _empty = {"urls": set(), "ids": set(), "fingerprints": set()}

    if not os.path.exists(seen_jobs_file):
        return _empty

    with open(seen_jobs_file, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return _empty

    data = json.loads(raw)

    if isinstance(data, list):
        # Legacy format: plain list of URLs
        return {"urls": set(data), "ids": set(), "fingerprints": set()}

    return {
        "urls": set(data.get("urls", [])),
        "ids": set(data.get("ids", [])),
        "fingerprints": set(data.get("fingerprints", [])),
    }


def _save_seen(seen: dict, seen_jobs_file: str) -> None:
    """Atomically write seen_jobs.json."""
    payload = {
        "urls": sorted(seen["urls"]),
        "ids": sorted(seen["ids"]),
        "fingerprints": sorted(seen["fingerprints"]),
    }
    dir_ = os.path.dirname(seen_jobs_file)
    os.makedirs(dir_, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, seen_jobs_file)
    except Exception:
        os.unlink(tmp_path)
        raise


def _fingerprint(job: "Job") -> str:
    """Normalised title+company string for fuzzy matching."""
    title = job.title.lower().strip()
    company = job.company.lower().strip()
    return f"{title}|{company}"


def _length_window(fp_len: int) -> tuple[int, int]:
    """
    Length range that could possibly hit FUZZY_THRESHOLD against a string of fp_len.

    SequenceMatcher.ratio() <= 2 * min(la, lb) / (la + lb). For ratio >= t,
    we need min/max >= t / (2 - t). Anything outside the window is provably
    below threshold and can be skipped in O(1).
    """
    lo_ratio = FUZZY_THRESHOLD / (2 - FUZZY_THRESHOLD)
    return (int(fp_len * lo_ratio), int(fp_len / lo_ratio) + 1)


def _is_fuzzy_duplicate(fp: str, fps_by_len: dict[int, list[str]], matcher: SequenceMatcher) -> bool:
    """
    Return True if fp is sufficiently similar to any indexed fingerprint.

    Uses length bucketing + real_quick_ratio/quick_ratio early-exit so the
    expensive ratio() call only fires for plausible candidates.
    """
    matcher.set_seq2(fp)
    lo, hi = _length_window(len(fp))
    for L in range(lo, hi + 1):
        bucket = fps_by_len.get(L)
        if not bucket:
            continue
        for existing in bucket:
            matcher.set_seq1(existing)
            if matcher.real_quick_ratio() < FUZZY_THRESHOLD:
                continue
            if matcher.quick_ratio() < FUZZY_THRESHOLD:
                continue
            if matcher.ratio() >= FUZZY_THRESHOLD:
                return True
    return False


def deduplicate(jobs: "list[Job]", seen_jobs_file: str) -> "list[Job]":
    """
    Filter out jobs already in the given seen_jobs.json.
    Updates the file with newly accepted jobs.
    Returns the list of new (unseen) jobs.
    """
    path = seen_jobs_file
    seen = _load_seen(path)
    new_jobs: list = []
    new_urls: list[str] = []
    new_ids: list[str] = []
    new_fps: list[str] = []

    # Build working sets to also catch duplicates within the current batch
    batch_urls: set[str] = set()
    batch_ids: set[str] = set()

    # Build a length-bucketed index of all known fingerprints (seen + batch).
    # Accepted batch fingerprints are appended in-place so within-batch dupes
    # are still caught without rebuilding the index each iteration.
    fps_by_len: dict[int, list[str]] = {}
    for fp in seen["fingerprints"]:
        fps_by_len.setdefault(len(fp), []).append(fp)

    matcher = SequenceMatcher(autojunk=False)

    for job in jobs:
        # Layer 1: URL exact match
        if job.url in seen["urls"] or job.url in batch_urls:
            continue

        # Layer 2: ID match
        if job.id in seen["ids"] or job.id in batch_ids:
            continue

        # Layer 3: Fuzzy title+company
        fp = _fingerprint(job)
        if _is_fuzzy_duplicate(fp, fps_by_len, matcher):
            continue

        # Accept
        new_jobs.append(job)
        batch_urls.add(job.url)
        batch_ids.add(job.id)
        fps_by_len.setdefault(len(fp), []).append(fp)
        new_urls.append(job.url)
        new_ids.append(job.id)
        new_fps.append(fp)

    # Persist
    if new_jobs:
        seen["urls"].update(new_urls)
        seen["ids"].update(new_ids)
        seen["fingerprints"].update(new_fps)
        _save_seen(seen, path)

    print(f"[Dedup] {len(jobs)} total → {len(new_jobs)} new (filtered {len(jobs) - len(new_jobs)} duplicates)")
    return new_jobs
