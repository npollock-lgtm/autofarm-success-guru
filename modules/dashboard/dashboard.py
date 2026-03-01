"""
Dashboard — lightweight web dashboard served by the approval server at
``http://{PROXY_VM_PUBLIC_IP}:8080/dashboard``.

Pure Python + HTML/CSS/JS.  No external dependencies.

Pages
-----
- ``/dashboard``                 — Overview (total videos, brands, queue)
- ``/dashboard/brand/{id}``      — Brand-specific analytics
- ``/dashboard/queue``           — Content queue status
- ``/dashboard/schedule``        — Publishing schedule
- ``/dashboard/analytics``       — Performance metrics
- ``/dashboard/health``          — System health details
- ``/dashboard/compliance``      — Compliance & rate-limit status

Each page method returns a complete HTML string that the approval server
renders directly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.dashboard")

# Brand display config
BRAND_COLOURS: Dict[str, str] = {
    "human_success_guru": "#4A90D9",
    "wealth_success_guru": "#50C878",
    "zen_success_guru": "#9B59B6",
    "social_success_guru": "#E67E22",
    "habits_success_guru": "#E74C3C",
    "relationships_success_guru": "#E91E63",
}

PLATFORM_ICONS: Dict[str, str] = {
    "tiktok": "\U0001f3b5",
    "instagram": "\U0001f4f7",
    "facebook": "\U0001f310",
    "youtube": "\U0001f3ac",
    "snapchat": "\U0001f47b",
}


class Dashboard:
    """Generate dashboard HTML pages from database queries.

    Parameters
    ----------
    db:
        Database helper instance.
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    # ==================================================================
    # Page: Overview
    # ==================================================================

    async def render_overview(self) -> str:
        """Render the main dashboard overview page.

        Returns
        -------
        str
            Complete HTML page.
        """
        # Aggregate stats
        total_videos = await self._count("videos")
        total_published = await self._count("publish_jobs", "status = 'published'")
        total_pending = await self._count("publish_jobs", "status = 'pending'")
        total_scripts = await self._count("scripts")
        pending_reviews = await self._count("reviews", "status = 'pending'")
        queue_depth = await self._count("content_queue", "status = 'ready'")

        # Per-brand stats
        brand_rows = await self.db.fetch_all(
            """
            SELECT brand_id, COUNT(*) AS total,
                   SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
            FROM publish_jobs
            GROUP BY brand_id
            """
        )

        brand_cards = ""
        for br in brand_rows:
            bid = br["brand_id"]
            colour = BRAND_COLOURS.get(bid, "#888")
            name = bid.replace("_success_guru", "").replace("_", " ").title()
            brand_cards += f"""
            <div class="card" style="border-top:3px solid {colour}">
              <h3>{name}</h3>
              <div class="stat-row"><span>Published</span><strong>{br['published']}</strong></div>
              <div class="stat-row"><span>Total Jobs</span><strong>{br['total']}</strong></div>
              <a href="/dashboard/brand/{bid}" class="link">View Details &rarr;</a>
            </div>"""

        # Recent activity
        recent = await self.db.fetch_all(
            """
            SELECT brand_id, platform, status, published_at, title
            FROM publish_jobs
            WHERE published_at IS NOT NULL
            ORDER BY published_at DESC LIMIT 10
            """
        )
        recent_html = ""
        for r in recent:
            icon = PLATFORM_ICONS.get(r["platform"], "")
            colour = BRAND_COLOURS.get(r["brand_id"], "#888")
            ts = (r["published_at"] or "")[:16]
            title = (r["title"] or "Untitled")[:40]
            recent_html += (
                f'<div class="activity-item">'
                f'<span class="dot" style="background:{colour}"></span>'
                f'{icon} {title} <span class="time">{ts}</span></div>'
            )

        content = f"""
        <div class="stats-grid">
          <div class="stat-card"><div class="stat-number">{total_videos}</div><div class="stat-label">Videos Created</div></div>
          <div class="stat-card"><div class="stat-number">{total_published}</div><div class="stat-label">Published</div></div>
          <div class="stat-card"><div class="stat-number">{total_pending}</div><div class="stat-label">Pending</div></div>
          <div class="stat-card"><div class="stat-number">{pending_reviews}</div><div class="stat-label">Awaiting Review</div></div>
          <div class="stat-card"><div class="stat-number">{queue_depth}</div><div class="stat-label">Queue Depth</div></div>
          <div class="stat-card"><div class="stat-number">{total_scripts}</div><div class="stat-label">Scripts Written</div></div>
        </div>
        <h2>Brands</h2>
        <div class="brand-grid">{brand_cards}</div>
        <h2>Recent Activity</h2>
        <div class="activity-list">{recent_html}</div>
        """
        return self._wrap_page("Dashboard", content)

    # ==================================================================
    # Page: Brand detail
    # ==================================================================

    async def render_brand(self, brand_id: str) -> str:
        """Render the brand-specific analytics page.

        Parameters
        ----------
        brand_id:
            Brand identifier.

        Returns
        -------
        str
            Complete HTML page.
        """
        colour = BRAND_COLOURS.get(brand_id, "#888")
        name = brand_id.replace("_success_guru", "").replace("_", " ").title()

        # Platform breakdown
        platform_rows = await self.db.fetch_all(
            """
            SELECT platform, COUNT(*) AS total,
                   SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
            FROM publish_jobs
            WHERE brand_id = ?
            GROUP BY platform
            """,
            (brand_id,),
        )

        plat_html = ""
        for pr in platform_rows:
            icon = PLATFORM_ICONS.get(pr["platform"], "")
            plat_html += (
                f'<div class="stat-row">{icon} {pr["platform"].title()}: '
                f'{pr["published"]}/{pr["total"]}</div>'
            )

        # Performance
        perf = await self.db.fetch_one(
            """
            SELECT AVG(cps_score) AS avg_cps,
                   AVG(views) AS avg_views,
                   AVG(engagement_rate) AS avg_eng,
                   COUNT(*) AS total
            FROM analytics WHERE brand_id = ?
            """,
            (brand_id,),
        )
        avg_cps = round(perf["avg_cps"] or 0, 2) if perf else 0
        avg_views = int(perf["avg_views"] or 0) if perf else 0
        avg_eng = round((perf["avg_eng"] or 0) * 100, 2) if perf else 0

        # Hook performance
        hooks = await self.db.fetch_all(
            """
            SELECT hook_type, weight, avg_cps_score, sample_count
            FROM hook_performance
            WHERE brand_id = ?
            ORDER BY weight DESC
            """,
            (brand_id,),
        )
        hook_html = ""
        for h in hooks:
            bar_width = int(h["weight"] * 100) if h["weight"] <= 1 else 100
            hook_html += (
                f'<div class="hook-row">'
                f'<span class="hook-name">{h["hook_type"]}</span>'
                f'<div class="hook-bar" style="width:{bar_width}%"></div>'
                f'<span class="hook-val">{h["weight"]:.2f} ({h["sample_count"]} samples)</span>'
                f'</div>'
            )

        content = f"""
        <h1 style="color:{colour}">{name} Success Guru</h1>
        <div class="stats-grid">
          <div class="stat-card"><div class="stat-number">{avg_cps}</div><div class="stat-label">Avg CPS</div></div>
          <div class="stat-card"><div class="stat-number">{avg_views:,}</div><div class="stat-label">Avg Views</div></div>
          <div class="stat-card"><div class="stat-number">{avg_eng}%</div><div class="stat-label">Avg Engagement</div></div>
        </div>
        <h2>Platforms</h2>
        <div class="card">{plat_html}</div>
        <h2>Hook Performance</h2>
        <div class="card">{hook_html if hook_html else '<div class="muted">No hook data yet</div>'}</div>
        """
        return self._wrap_page(f"{name} — Dashboard", content)

    # ==================================================================
    # Page: Queue
    # ==================================================================

    async def render_queue(self) -> str:
        """Render the content queue page.

        Returns
        -------
        str
            Complete HTML page.
        """
        rows = await self.db.fetch_all(
            """
            SELECT cq.id, cq.brand_id, cq.status, cq.priority,
                   cq.created_at, s.hook, s.hook_type
            FROM content_queue cq
            LEFT JOIN scripts s ON s.id = cq.script_id
            ORDER BY cq.priority DESC, cq.created_at ASC
            LIMIT 50
            """
        )

        table_rows = ""
        for r in rows:
            colour = BRAND_COLOURS.get(r["brand_id"], "#888")
            name = r["brand_id"].replace("_success_guru", "").replace("_", " ").title()
            hook = (r["hook"] or "—")[:50]
            status_class = r["status"] or "pending"
            table_rows += (
                f'<tr>'
                f'<td><span class="dot" style="background:{colour}"></span>{name}</td>'
                f'<td>{hook}</td>'
                f'<td>{r.get("hook_type", "—")}</td>'
                f'<td><span class="badge {status_class}">{status_class}</span></td>'
                f'<td>{r.get("priority", 0)}</td>'
                f'<td>{(r.get("created_at","") or "")[:16]}</td>'
                f'</tr>'
            )

        content = f"""
        <h1>Content Queue</h1>
        <table class="data-table">
          <thead><tr><th>Brand</th><th>Hook</th><th>Type</th><th>Status</th><th>Priority</th><th>Created</th></tr></thead>
          <tbody>{table_rows}</tbody>
        </table>
        """
        return self._wrap_page("Queue — Dashboard", content)

    # ==================================================================
    # Page: Schedule
    # ==================================================================

    async def render_schedule(self) -> str:
        """Render the publishing schedule page.

        Returns
        -------
        str
            Complete HTML page.
        """
        # Upcoming publishes
        now = datetime.now(timezone.utc).isoformat()
        upcoming = await self.db.fetch_all(
            """
            SELECT pj.id, pj.brand_id, pj.platform, pj.scheduled_for,
                   pj.status, pj.title, s.hook
            FROM publish_jobs pj
            LEFT JOIN videos v ON v.id = pj.video_id
            LEFT JOIN scripts s ON s.id = v.script_id
            WHERE pj.scheduled_for >= ? AND pj.status IN ('pending','scheduled')
            ORDER BY pj.scheduled_for ASC
            LIMIT 50
            """,
            (now,),
        )

        rows_html = ""
        for r in upcoming:
            colour = BRAND_COLOURS.get(r["brand_id"], "#888")
            icon = PLATFORM_ICONS.get(r["platform"], "")
            name = r["brand_id"].replace("_success_guru", "").replace("_", " ").title()
            title = (r["title"] or r["hook"] or "Untitled")[:40]
            ts = (r["scheduled_for"] or "")[:16]
            rows_html += (
                f'<tr>'
                f'<td>{ts}</td>'
                f'<td><span class="dot" style="background:{colour}"></span>{name}</td>'
                f'<td>{icon} {r["platform"].title()}</td>'
                f'<td>{title}</td>'
                f'<td><span class="badge {r["status"]}">{r["status"]}</span></td>'
                f'</tr>'
            )

        content = f"""
        <h1>Publishing Schedule</h1>
        <a href="/calendar" class="btn">\U0001f4c5 Calendar View</a>
        <table class="data-table">
          <thead><tr><th>Scheduled</th><th>Brand</th><th>Platform</th><th>Title</th><th>Status</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
        """
        return self._wrap_page("Schedule — Dashboard", content)

    # ==================================================================
    # Page: Analytics
    # ==================================================================

    async def render_analytics(self) -> str:
        """Render the performance analytics page.

        Returns
        -------
        str
            Complete HTML page.
        """
        # Overall metrics
        overall = await self.db.fetch_one(
            """
            SELECT AVG(cps_score) AS avg_cps,
                   AVG(views) AS avg_views,
                   AVG(engagement_rate) AS avg_eng,
                   SUM(views) AS total_views,
                   COUNT(*) AS total_rows
            FROM analytics
            WHERE pulled_at >= datetime('now', '-30 days')
            """
        )

        avg_cps = round(overall["avg_cps"] or 0, 2) if overall else 0
        avg_views = int(overall["avg_views"] or 0) if overall else 0
        total_views = int(overall["total_views"] or 0) if overall else 0

        # Per-brand breakdown
        brand_stats = await self.db.fetch_all(
            """
            SELECT brand_id,
                   AVG(cps_score) AS avg_cps,
                   AVG(views) AS avg_views,
                   COUNT(*) AS cnt
            FROM analytics
            WHERE pulled_at >= datetime('now', '-30 days')
            GROUP BY brand_id
            ORDER BY avg_cps DESC
            """
        )

        brand_html = ""
        for bs in brand_stats:
            colour = BRAND_COLOURS.get(bs["brand_id"], "#888")
            name = bs["brand_id"].replace("_success_guru", "").replace("_", " ").title()
            bar_width = min(int((bs["avg_cps"] or 0) * 10), 100)
            brand_html += (
                f'<div class="analytics-row">'
                f'<span class="dot" style="background:{colour}"></span>'
                f'<span class="name">{name}</span>'
                f'<div class="bar-bg"><div class="bar-fill" style="width:{bar_width}%;background:{colour}"></div></div>'
                f'<span class="val">CPS {bs["avg_cps"]:.2f} | {int(bs["avg_views"] or 0):,} avg views</span>'
                f'</div>'
            )

        # Top performing videos
        top_vids = await self.db.fetch_all(
            """
            SELECT a.views, a.cps_score, a.engagement_rate,
                   pj.brand_id, pj.platform, pj.title, s.hook
            FROM analytics a
            JOIN publish_jobs pj ON pj.id = a.publish_job_id
            LEFT JOIN videos v ON v.id = pj.video_id
            LEFT JOIN scripts s ON s.id = v.script_id
            ORDER BY a.cps_score DESC
            LIMIT 10
            """
        )

        top_html = ""
        for tv in top_vids:
            colour = BRAND_COLOURS.get(tv["brand_id"], "#888")
            icon = PLATFORM_ICONS.get(tv["platform"], "")
            title = (tv["title"] or tv["hook"] or "Untitled")[:40]
            top_html += (
                f'<tr>'
                f'<td><span class="dot" style="background:{colour}"></span>{title}</td>'
                f'<td>{icon} {tv["platform"].title()}</td>'
                f'<td>{int(tv["views"] or 0):,}</td>'
                f'<td>{tv["cps_score"]:.2f}</td>'
                f'<td>{(tv["engagement_rate"] or 0)*100:.2f}%</td>'
                f'</tr>'
            )

        content = f"""
        <h1>Performance Analytics (30 days)</h1>
        <div class="stats-grid">
          <div class="stat-card"><div class="stat-number">{avg_cps}</div><div class="stat-label">Avg CPS</div></div>
          <div class="stat-card"><div class="stat-number">{avg_views:,}</div><div class="stat-label">Avg Views</div></div>
          <div class="stat-card"><div class="stat-number">{total_views:,}</div><div class="stat-label">Total Views</div></div>
        </div>
        <h2>Brand Performance</h2>
        <div class="card">{brand_html}</div>
        <h2>Top Videos</h2>
        <table class="data-table">
          <thead><tr><th>Title</th><th>Platform</th><th>Views</th><th>CPS</th><th>Engagement</th></tr></thead>
          <tbody>{top_html}</tbody>
        </table>
        """
        return self._wrap_page("Analytics — Dashboard", content)

    # ==================================================================
    # Page: Health
    # ==================================================================

    async def render_health(self) -> str:
        """Render the system health page.

        Returns
        -------
        str
            Complete HTML page.
        """
        # Circuit breakers
        breakers = await self.db.fetch_all(
            "SELECT * FROM circuit_breakers ORDER BY service_name"
        )
        cb_html = ""
        for cb in breakers:
            state = cb["state"] or "closed"
            state_color = {"closed": "#50C878", "open": "#E74C3C", "half_open": "#E67E22"}.get(state, "#888")
            cb_html += (
                f'<div class="health-item">'
                f'<span class="status-dot" style="background:{state_color}"></span>'
                f'{cb["service_name"]} — {state} (failures: {cb.get("failure_count", 0)})'
                f'</div>'
            )

        # Recent metrics
        metrics = await self.db.fetch_all(
            """
            SELECT metric_name, metric_value, recorded_at
            FROM system_metrics
            ORDER BY recorded_at DESC LIMIT 10
            """
        )
        metrics_html = ""
        for m in metrics:
            metrics_html += (
                f'<div class="health-item">'
                f'{m["metric_name"]}: {str(m["metric_value"])[:80]} '
                f'<span class="time">{(m["recorded_at"] or "")[:16]}</span>'
                f'</div>'
            )

        # Job states
        job_states = await self.db.fetch_all(
            """
            SELECT current_state, COUNT(*) AS cnt
            FROM job_states
            GROUP BY current_state
            ORDER BY cnt DESC
            """
        )
        js_html = ""
        for js in job_states:
            js_html += f'<div class="stat-row"><span>{js["current_state"]}</span><strong>{js["cnt"]}</strong></div>'

        content = f"""
        <h1>System Health</h1>
        <h2>Circuit Breakers</h2>
        <div class="card">{cb_html if cb_html else '<div class="muted">No circuit breakers</div>'}</div>
        <h2>Job States</h2>
        <div class="card">{js_html if js_html else '<div class="muted">No jobs</div>'}</div>
        <h2>Recent Metrics</h2>
        <div class="card">{metrics_html if metrics_html else '<div class="muted">No metrics yet</div>'}</div>
        """
        return self._wrap_page("Health — Dashboard", content)

    # ==================================================================
    # Page: Compliance
    # ==================================================================

    async def render_compliance(self) -> str:
        """Render the compliance status page.

        Returns
        -------
        str
            Complete HTML page.
        """
        # Rate limit status
        rate_limits = await self.db.fetch_all(
            """
            SELECT brand_id, platform, endpoint, window_type,
                   count, units, window_start, window_end
            FROM rate_limits
            ORDER BY brand_id, platform
            """
        )

        rl_html = ""
        for rl in rate_limits:
            colour = BRAND_COLOURS.get(rl["brand_id"], "#888")
            name = rl["brand_id"].replace("_success_guru", "").replace("_", " ").title()
            rl_html += (
                f'<tr>'
                f'<td><span class="dot" style="background:{colour}"></span>{name}</td>'
                f'<td>{rl["platform"]}</td>'
                f'<td>{rl["endpoint"]}</td>'
                f'<td>{rl["window_type"]}</td>'
                f'<td>{rl["count"]}/{rl.get("units", "—")}</td>'
                f'</tr>'
            )

        # AI disclosure compliance
        ai_disclosure = await self.db.fetch_one(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN ai_disclosure_applied = 1 THEN 1 ELSE 0 END) AS disclosed
            FROM publish_jobs WHERE status = 'published'
            """
        )
        disclosure_pct = 0
        if ai_disclosure and ai_disclosure["total"]:
            disclosure_pct = round(
                (ai_disclosure["disclosed"] or 0) / ai_disclosure["total"] * 100, 1
            )

        # Anti-spam variation
        anti_spam = await self.db.fetch_one(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN anti_spam_varied = 1 THEN 1 ELSE 0 END) AS varied
            FROM publish_jobs WHERE status = 'published'
            """
        )
        varied_pct = 0
        if anti_spam and anti_spam["total"]:
            varied_pct = round(
                (anti_spam["varied"] or 0) / anti_spam["total"] * 100, 1
            )

        content = f"""
        <h1>Compliance Status</h1>
        <div class="stats-grid">
          <div class="stat-card"><div class="stat-number">{disclosure_pct}%</div><div class="stat-label">AI Disclosure Rate</div></div>
          <div class="stat-card"><div class="stat-number">{varied_pct}%</div><div class="stat-label">Anti-Spam Variation</div></div>
        </div>
        <h2>Rate Limits</h2>
        <table class="data-table">
          <thead><tr><th>Brand</th><th>Platform</th><th>Endpoint</th><th>Window</th><th>Usage</th></tr></thead>
          <tbody>{rl_html if rl_html else '<tr><td colspan="5" class="muted">No rate limit data</td></tr>'}</tbody>
        </table>
        """
        return self._wrap_page("Compliance — Dashboard", content)

    # ==================================================================
    # Page: Review queue
    # ==================================================================

    async def render_review_queue(self) -> str:
        """Render the review queue page.

        Returns
        -------
        str
            Complete HTML page.
        """
        rows = await self.db.fetch_all(
            """
            SELECT r.id, r.brand_id, r.review_token, r.review_method,
                   r.status, r.auto_approve_at, r.created_at,
                   v.duration_seconds, s.hook, s.hook_type
            FROM reviews r
            JOIN videos v ON v.id = r.video_id
            LEFT JOIN scripts s ON s.id = v.script_id
            WHERE r.status = 'pending'
            ORDER BY r.created_at ASC
            """
        )

        table_html = ""
        for r in rows:
            colour = BRAND_COLOURS.get(r["brand_id"], "#888")
            name = r["brand_id"].replace("_success_guru", "").replace("_", " ").title()
            hook = (r["hook"] or "—")[:40]
            table_html += (
                f'<tr>'
                f'<td><span class="dot" style="background:{colour}"></span>{name}</td>'
                f'<td>{hook}</td>'
                f'<td>{r["review_method"]}</td>'
                f'<td>{r.get("duration_seconds", 0):.0f}s</td>'
                f'<td><a href="/review/{r["review_token"]}" class="link">Review</a></td>'
                f'<td><a href="/approve/{r["review_token"]}" class="btn-sm approve">Approve</a> '
                f'<a href="/reject/{r["review_token"]}" class="btn-sm reject">Reject</a></td>'
                f'</tr>'
            )

        content = f"""
        <h1>Review Queue ({len(rows)} pending)</h1>
        <table class="data-table">
          <thead><tr><th>Brand</th><th>Hook</th><th>Method</th><th>Duration</th><th>Preview</th><th>Actions</th></tr></thead>
          <tbody>{table_html if table_html else '<tr><td colspan="6" class="muted">No pending reviews</td></tr>'}</tbody>
        </table>
        """
        return self._wrap_page("Review Queue — Dashboard", content)

    # ==================================================================
    # Helpers
    # ==================================================================

    async def _count(
        self, table: str, where: Optional[str] = None
    ) -> int:
        """Count rows in a table with optional WHERE clause.

        Parameters
        ----------
        table:
            Table name.
        where:
            Optional SQL WHERE clause (without 'WHERE').

        Returns
        -------
        int
            Row count.
        """
        clause = f"WHERE {where}" if where else ""
        row = await self.db.fetch_one(
            f"SELECT COUNT(*) AS cnt FROM {table} {clause}"
        )
        return row["cnt"] if row else 0

    def _wrap_page(self, title: str, content: str) -> str:
        """Wrap content in a full HTML page with navigation and styling.

        Parameters
        ----------
        title:
            Page title.
        content:
            HTML body content.

        Returns
        -------
        str
            Complete HTML document.
        """
        nav_links = [
            ("/dashboard", "\U0001f3e0 Overview"),
            ("/dashboard/queue", "\U0001f4e6 Queue"),
            ("/dashboard/schedule", "\U0001f4c5 Schedule"),
            ("/dashboard/analytics", "\U0001f4ca Analytics"),
            ("/dashboard/health", "\U0001f3e5 Health"),
            ("/dashboard/compliance", "\U00002705 Compliance"),
            ("/review/queue", "\U0001f50d Reviews"),
            ("/calendar", "\U0001f5d3 Calendar"),
        ]
        nav_html = "".join(
            f'<a href="{url}">{label}</a>' for url, label in nav_links
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#1a1a2e; color:#e0e0e0; }}
  .topnav {{ background:#0f3460; padding:12px 20px; display:flex; gap:12px;
             flex-wrap:wrap; align-items:center; position:sticky; top:0; z-index:100; }}
  .topnav a {{ color:#e0e0e0; text-decoration:none; padding:6px 12px;
               border-radius:4px; font-size:14px; }}
  .topnav a:hover {{ background:#16213e; }}
  .container {{ max-width:1200px; margin:0 auto; padding:24px; }}
  h1 {{ font-size:24px; margin-bottom:16px; }}
  h2 {{ font-size:18px; margin:24px 0 12px; color:#aaa; }}
  .stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
                 gap:12px; margin-bottom:24px; }}
  .stat-card {{ background:#16213e; border-radius:8px; padding:20px; text-align:center; }}
  .stat-number {{ font-size:28px; font-weight:bold; color:#4A90D9; }}
  .stat-label {{ font-size:12px; color:#888; margin-top:4px; text-transform:uppercase; }}
  .brand-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
                 gap:12px; }}
  .card {{ background:#16213e; border-radius:8px; padding:16px; margin-bottom:12px; }}
  .card h3 {{ font-size:16px; margin-bottom:8px; }}
  .stat-row {{ display:flex; justify-content:space-between; padding:6px 0;
               border-bottom:1px solid #1a1a2e; font-size:14px; }}
  .link {{ color:#4A90D9; text-decoration:none; font-size:13px; display:inline-block; margin-top:8px; }}
  .link:hover {{ text-decoration:underline; }}
  .btn {{ display:inline-block; padding:8px 16px; background:#4A90D9; color:white;
          border-radius:6px; text-decoration:none; font-size:14px; margin-bottom:16px; }}
  .btn:hover {{ background:#3A80C9; }}
  .btn-sm {{ padding:4px 10px; border-radius:4px; text-decoration:none;
             font-size:12px; color:white; }}
  .btn-sm.approve {{ background:#50C878; }}
  .btn-sm.reject {{ background:#E74C3C; }}
  .activity-list {{ background:#16213e; border-radius:8px; padding:12px; }}
  .activity-item {{ padding:8px 0; border-bottom:1px solid #1a1a2e; font-size:14px;
                    display:flex; align-items:center; gap:8px; }}
  .dot {{ width:8px; height:8px; border-radius:50%; display:inline-block; flex-shrink:0; }}
  .status-dot {{ width:10px; height:10px; border-radius:50%; display:inline-block; }}
  .time {{ color:#666; font-size:12px; margin-left:auto; }}
  .data-table {{ width:100%; border-collapse:collapse; background:#16213e;
                 border-radius:8px; overflow:hidden; }}
  .data-table th {{ padding:10px 12px; background:#0f3460; text-align:left;
                    font-size:12px; text-transform:uppercase; }}
  .data-table td {{ padding:8px 12px; border-bottom:1px solid #1a1a2e; font-size:14px; }}
  .badge {{ padding:2px 8px; border-radius:3px; font-size:11px;
            text-transform:uppercase; }}
  .badge.pending {{ background:#E67E22; color:white; }}
  .badge.published {{ background:#50C878; color:white; }}
  .badge.scheduled {{ background:#4A90D9; color:white; }}
  .badge.failed {{ background:#E74C3C; color:white; }}
  .badge.ready {{ background:#50C878; color:white; }}
  .muted {{ color:#666; font-style:italic; padding:12px; }}
  .health-item {{ padding:8px 0; border-bottom:1px solid #1a1a2e;
                  font-size:14px; display:flex; align-items:center; gap:8px; }}
  .analytics-row {{ display:flex; align-items:center; gap:8px; padding:8px 0;
                    border-bottom:1px solid #1a1a2e; }}
  .analytics-row .name {{ width:120px; font-size:14px; }}
  .bar-bg {{ flex:1; height:20px; background:#1a1a2e; border-radius:3px; overflow:hidden; }}
  .bar-fill {{ height:100%; border-radius:3px; }}
  .analytics-row .val {{ font-size:12px; color:#aaa; width:200px; text-align:right; }}
  .hook-row {{ display:flex; align-items:center; gap:8px; padding:6px 0;
               border-bottom:1px solid #1a1a2e; }}
  .hook-name {{ width:120px; font-size:13px; text-transform:capitalize; }}
  .hook-bar {{ height:16px; background:#4A90D9; border-radius:3px; min-width:4px; }}
  .hook-val {{ font-size:12px; color:#aaa; }}
</style>
</head>
<body>
<nav class="topnav">{nav_html}</nav>
<div class="container">{content}</div>
</body>
</html>"""
