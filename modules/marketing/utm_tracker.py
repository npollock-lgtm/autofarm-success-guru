"""
UTM Tracker — appends UTM parameters to affiliate and bio links for
conversion attribution.  Tracks click counts per campaign.

Links are stored in the ``utm_links`` table and can be queried by the
dashboard for ROI reporting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

logger = logging.getLogger("autofarm.marketing.utm_tracker")


class UTMTracker:
    """Build and track UTM-tagged URLs for each publish job.

    Parameters
    ----------
    db:
        Database helper instance.
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Build UTM URL
    # ------------------------------------------------------------------

    def build_utm_url(
        self,
        original_url: str,
        brand_id: str,
        platform: str,
        campaign: Optional[str] = None,
        content_id: Optional[str] = None,
    ) -> str:
        """Append UTM parameters to a URL.

        Parameters
        ----------
        original_url:
            The base URL (e.g. affiliate link, landing page).
        brand_id:
            Brand identifier — becomes ``utm_source``.
        platform:
            Platform name — becomes ``utm_medium``.
        campaign:
            Optional campaign name — becomes ``utm_campaign``.
            Defaults to ``autofarm``.
        content_id:
            Optional content identifier — becomes ``utm_content``.

        Returns
        -------
        str
            URL with UTM parameters appended.
        """
        parsed = urlparse(original_url)
        existing_params = parse_qs(parsed.query, keep_blank_values=True)

        # Build UTM params (don't overwrite existing ones)
        utm_params = {
            "utm_source": brand_id,
            "utm_medium": platform,
            "utm_campaign": campaign or "autofarm",
        }
        if content_id:
            utm_params["utm_content"] = content_id

        for key, value in utm_params.items():
            if key not in existing_params:
                existing_params[key] = [value]

        # Flatten back to single values for urlencode
        flat_params = {
            k: v[0] if isinstance(v, list) and len(v) == 1 else v
            for k, v in existing_params.items()
        }

        new_query = urlencode(flat_params, doseq=True)
        new_parsed = parsed._replace(query=new_query)
        return urlunparse(new_parsed)

    # ------------------------------------------------------------------
    # Track a link
    # ------------------------------------------------------------------

    async def track_link(
        self,
        brand_id: str,
        platform: str,
        publish_job_id: int,
        original_url: str,
        campaign: Optional[str] = None,
    ) -> str:
        """Create a tracked UTM link and store it.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        publish_job_id:
            ``publish_jobs.id``.
        original_url:
            Base URL to tag.
        campaign:
            Optional campaign name.

        Returns
        -------
        str
            The UTM-tagged URL.

        Side Effects
        ------------
        Inserts row into ``utm_links`` table.
        """
        utm_url = self.build_utm_url(
            original_url, brand_id, platform,
            campaign=campaign,
            content_id=str(publish_job_id),
        )

        await self.db.execute(
            """
            INSERT INTO utm_links
                (brand_id, platform, publish_job_id, original_url, utm_url)
            VALUES (?, ?, ?, ?, ?)
            """,
            (brand_id, platform, publish_job_id, original_url, utm_url),
        )

        logger.info(
            "Tracked UTM link for job %d: %s", publish_job_id, utm_url
        )
        return utm_url

    # ------------------------------------------------------------------
    # Apply UTM to caption links
    # ------------------------------------------------------------------

    async def apply_utm_to_caption(
        self,
        caption: str,
        brand_id: str,
        platform: str,
        publish_job_id: int,
    ) -> str:
        """Find URLs in a caption and replace them with UTM-tagged versions.

        Parameters
        ----------
        caption:
            Original caption text.
        brand_id:
            Brand identifier.
        platform:
            Platform name.
        publish_job_id:
            ``publish_jobs.id``.

        Returns
        -------
        str
            Caption with UTM-tagged URLs.

        Side Effects
        ------------
        Creates ``utm_links`` rows for each URL found.
        """
        import re

        url_pattern = re.compile(
            r'https?://[^\s<>"{}|\\^`\[\]]+', re.IGNORECASE
        )
        urls = url_pattern.findall(caption)

        if not urls:
            return caption

        for url in urls:
            # Skip platform-internal URLs
            if any(
                domain in url.lower()
                for domain in [
                    "tiktok.com", "instagram.com", "facebook.com",
                    "youtube.com", "snapchat.com",
                ]
            ):
                continue

            utm_url = await self.track_link(
                brand_id, platform, publish_job_id, url
            )
            caption = caption.replace(url, utm_url, 1)

        return caption

    # ------------------------------------------------------------------
    # Click tracking
    # ------------------------------------------------------------------

    async def record_click(self, utm_link_id: int) -> None:
        """Increment the click counter for a UTM link.

        Parameters
        ----------
        utm_link_id:
            ``utm_links.id``.

        Side Effects
        ------------
        Increments ``utm_links.clicks``.
        """
        await self.db.execute(
            "UPDATE utm_links SET clicks = clicks + 1 WHERE id = ?",
            (utm_link_id,),
        )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    async def get_link_stats(
        self,
        brand_id: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return tracked link statistics.

        Parameters
        ----------
        brand_id:
            Optional brand filter.
        platform:
            Optional platform filter.
        limit:
            Maximum rows to return.

        Returns
        -------
        List[Dict[str, Any]]
            UTM link rows with click counts.
        """
        conditions: List[str] = []
        params: List[Any] = []

        if brand_id:
            conditions.append("brand_id = ?")
            params.append(brand_id)
        if platform:
            conditions.append("platform = ?")
            params.append(platform)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = await self.db.fetch_all(
            f"""
            SELECT id, brand_id, platform, publish_job_id,
                   original_url, utm_url, clicks, created_at
            FROM utm_links
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple(params),
        )
        return [dict(r) for r in rows]

    async def get_campaign_summary(
        self, brand_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return click totals grouped by campaign (utm_campaign param).

        Parameters
        ----------
        brand_id:
            Optional brand filter.

        Returns
        -------
        List[Dict[str, Any]]
            ``[{platform, total_links, total_clicks}]``
        """
        if brand_id:
            rows = await self.db.fetch_all(
                """
                SELECT platform, COUNT(*) AS total_links,
                       SUM(clicks) AS total_clicks
                FROM utm_links
                WHERE brand_id = ?
                GROUP BY platform
                ORDER BY total_clicks DESC
                """,
                (brand_id,),
            )
        else:
            rows = await self.db.fetch_all(
                """
                SELECT brand_id, platform, COUNT(*) AS total_links,
                       SUM(clicks) AS total_clicks
                FROM utm_links
                GROUP BY brand_id, platform
                ORDER BY total_clicks DESC
                """
            )
        return [dict(r) for r in rows]
