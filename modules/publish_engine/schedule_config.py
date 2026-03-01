"""
Schedule Config — posting windows per brand per platform (UTC).

Each brand × platform entry contains:
  * ``windows``     : list of [hour, minute] pairs for optimal posting
  * ``best_days``   : ISO weekdays (1 = Monday … 7 = Sunday)
  * ``daily_limit`` : max posts per day on this platform
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Full posting-window schedule (UTC)
# ---------------------------------------------------------------------------

POSTING_WINDOWS_UTC: Dict[str, Dict[str, Dict[str, Any]]] = {
    "human_success_guru": {
        "tiktok":    {"windows": [[6, 0], [12, 30], [19, 0]], "best_days": [1, 2, 3, 4, 5], "daily_limit": 2},
        "instagram": {"windows": [[7, 0], [19, 30]],         "best_days": [1, 2, 3, 4, 5], "daily_limit": 1},
        "facebook":  {"windows": [[12, 0]],                  "best_days": [1, 2, 3, 5],    "daily_limit": 1},
        "youtube":   {"windows": [[8, 0], [15, 0]],          "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 2},
        "snapchat":  {"windows": [[18, 0]],                  "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 1},
    },
    "wealth_success_guru": {
        "tiktok":    {"windows": [[6, 30], [11, 0], [17, 30]], "best_days": [1, 2, 3, 4, 5], "daily_limit": 2},
        "instagram": {"windows": [[7, 30], [12, 0]],          "best_days": [1, 2, 3, 4],    "daily_limit": 1},
        "facebook":  {"windows": [[13, 0]],                   "best_days": [2, 3, 4],       "daily_limit": 1},
        "youtube":   {"windows": [[7, 0], [16, 0]],           "best_days": [1, 2, 3, 4, 5], "daily_limit": 2},
        "snapchat":  {"windows": [[17, 30]],                  "best_days": [1, 2, 3, 4, 5, 6], "daily_limit": 1},
    },
    "zen_success_guru": {
        "tiktok":    {"windows": [[6, 0], [20, 0]],  "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 1},
        "instagram": {"windows": [[7, 0]],           "best_days": [1, 3, 5, 7],          "daily_limit": 1},
        "facebook":  {"windows": [[8, 30]],          "best_days": [1, 4, 7],             "daily_limit": 1},
        "youtube":   {"windows": [[7, 30]],          "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 1},
        "snapchat":  {"windows": [[19, 0]],          "best_days": [1, 3, 5, 7],          "daily_limit": 1},
    },
    "social_success_guru": {
        "tiktok":    {"windows": [[7, 30], [12, 0], [18, 30]], "best_days": [1, 2, 3, 4, 5], "daily_limit": 2},
        "instagram": {"windows": [[8, 0], [18, 0]],           "best_days": [1, 2, 3, 4, 5], "daily_limit": 1},
        "facebook":  {"windows": [[13, 30]],                  "best_days": [2, 3, 4, 5],    "daily_limit": 1},
        "youtube":   {"windows": [[8, 30], [17, 0]],          "best_days": [1, 2, 3, 4, 5, 6], "daily_limit": 2},
        "snapchat":  {"windows": [[18, 30]],                  "best_days": [1, 2, 3, 4, 5, 6], "daily_limit": 1},
    },
    "habits_success_guru": {
        "tiktok":    {"windows": [[5, 30], [11, 30], [19, 30]], "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 2},
        "instagram": {"windows": [[6, 0], [19, 0]],            "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 1},
        "facebook":  {"windows": [[8, 0]],                     "best_days": [1, 2, 3, 4, 5],       "daily_limit": 1},
        "youtube":   {"windows": [[6, 30], [14, 0]],           "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 2},
        "snapchat":  {"windows": [[7, 0]],                     "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 1},
    },
    "relationships_success_guru": {
        "tiktok":    {"windows": [[9, 0], [20, 0], [22, 0]], "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 2},
        "instagram": {"windows": [[9, 30], [20, 30]],        "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 1},
        "facebook":  {"windows": [[14, 0]],                  "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 1},
        "youtube":   {"windows": [[10, 0], [19, 0]],         "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 2},
        "snapchat":  {"windows": [[20, 0]],                  "best_days": [1, 2, 3, 4, 5, 6, 7], "daily_limit": 1},
    },
}


def get_brand_schedule(brand_id: str) -> Dict[str, Dict[str, Any]]:
    """Return the posting schedule for a brand.

    Parameters
    ----------
    brand_id:
        Brand identifier.

    Returns
    -------
    Dict[str, Dict[str, Any]]
        Platform → window config mapping.
    """
    return POSTING_WINDOWS_UTC.get(brand_id, {})


def get_all_brands() -> List[str]:
    """Return all brand IDs in the schedule config.

    Returns
    -------
    List[str]
    """
    return list(POSTING_WINDOWS_UTC.keys())


def get_daily_limit(brand_id: str, platform: str) -> int:
    """Return the daily post limit for brand × platform.

    Parameters
    ----------
    brand_id:
        Brand identifier.
    platform:
        Platform name.

    Returns
    -------
    int
        Daily limit (defaults to 1).
    """
    brand = POSTING_WINDOWS_UTC.get(brand_id, {})
    plat = brand.get(platform, {})
    return plat.get("daily_limit", 1)
