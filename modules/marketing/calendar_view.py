"""
Calendar View — generates a visual HTML calendar of scheduled and
published content.  Served by the approval server at ``GET /calendar``.

The calendar shows upcoming publishes, past performance, and queue depth
per brand per day.  Pure Python + HTML/CSS — no external JS dependencies.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("autofarm.marketing.calendar_view")

# Brand colours for the calendar
BRAND_COLOURS: Dict[str, str] = {
    "human_success_guru": "#4A90D9",
    "wealth_success_guru": "#50C878",
    "zen_success_guru": "#9B59B6",
    "social_success_guru": "#E67E22",
    "habits_success_guru": "#E74C3C",
    "relationships_success_guru": "#E91E63",
}

# Platform icons (emoji)
PLATFORM_ICONS: Dict[str, str] = {
    "tiktok": "\U0001f3b5",
    "instagram": "\U0001f4f7",
    "facebook": "\U0001f310",
    "youtube": "\U0001f3ac",
    "snapchat": "\U0001f47b",
}


class CalendarView:
    """Generate HTML calendar views for the content schedule.

    Parameters
    ----------
    db:
        Database helper instance.
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Main calendar page
    # ------------------------------------------------------------------

    async def render_calendar(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
        brand_filter: Optional[str] = None,
    ) -> str:
        """Render a full-page HTML calendar.

        Parameters
        ----------
        year:
            Calendar year. Defaults to current.
        month:
            Calendar month. Defaults to current.
        brand_filter:
            Optional brand to filter by.

        Returns
        -------
        str
            Complete HTML page string.
        """
        now = datetime.now(timezone.utc)
        year = year or now.year
        month = month or now.month

        events = await self._get_month_events(year, month, brand_filter)
        queue_depth = await self._get_queue_depth()

        html = self._build_html(year, month, events, queue_depth, brand_filter)
        return html

    # ------------------------------------------------------------------
    # Data queries
    # ------------------------------------------------------------------

    async def _get_month_events(
        self,
        year: int,
        month: int,
        brand_filter: Optional[str] = None,
    ) -> Dict[int, List[Dict[str, Any]]]:
        """Get all scheduled/published events for a month.

        Parameters
        ----------
        year:
            Calendar year.
        month:
            Calendar month.
        brand_filter:
            Optional brand filter.

        Returns
        -------
        Dict[int, List[Dict[str, Any]]]
            ``{day_number: [{brand_id, platform, status, scheduled_for, title}]}``
        """
        start = datetime(year, month, 1, tzinfo=timezone.utc).isoformat()
        _, last_day = calendar.monthrange(year, month)
        end = datetime(year, month, last_day, 23, 59, 59,
                       tzinfo=timezone.utc).isoformat()

        params: list = [start, end]
        brand_clause = ""
        if brand_filter:
            brand_clause = " AND pj.brand_id = ?"
            params.append(brand_filter)

        rows = await self.db.fetch_all(
            f"""
            SELECT pj.id, pj.brand_id, pj.platform, pj.status,
                   pj.scheduled_for, pj.published_at, pj.title,
                   s.hook, s.hook_type
            FROM publish_jobs pj
            LEFT JOIN videos v ON v.id = pj.video_id
            LEFT JOIN scripts s ON s.id = v.script_id
            WHERE (pj.scheduled_for BETWEEN ? AND ?
                   OR pj.published_at BETWEEN ? AND ?){brand_clause}
            ORDER BY pj.scheduled_for ASC
            """,
            tuple(params[:2] + params[:2] + params[2:]),
        )

        events: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            ts = r["published_at"] or r["scheduled_for"]
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                day = dt.day
            except (ValueError, AttributeError):
                continue

            if day not in events:
                events[day] = []

            events[day].append({
                "id": r["id"],
                "brand_id": r["brand_id"],
                "platform": r["platform"],
                "status": r["status"],
                "time": dt.strftime("%H:%M"),
                "title": r["title"] or r["hook"] or "Untitled",
                "hook_type": r.get("hook_type", ""),
            })

        return events

    async def _get_queue_depth(self) -> Dict[str, int]:
        """Get current queue depth per brand.

        Returns
        -------
        Dict[str, int]
            ``{brand_id: count}``
        """
        rows = await self.db.fetch_all(
            """
            SELECT brand_id, COUNT(*) AS cnt
            FROM content_queue
            WHERE status = 'ready'
            GROUP BY brand_id
            """
        )
        return {r["brand_id"]: r["cnt"] for r in rows}

    async def get_calendar_data_json(
        self,
        year: int,
        month: int,
        brand_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return calendar data as JSON-serialisable dict.

        Parameters
        ----------
        year:
            Calendar year.
        month:
            Calendar month.
        brand_filter:
            Optional brand filter.

        Returns
        -------
        Dict[str, Any]
            ``{year, month, events, queue_depth}``
        """
        events = await self._get_month_events(year, month, brand_filter)
        queue_depth = await self._get_queue_depth()
        return {
            "year": year,
            "month": month,
            "events": events,
            "queue_depth": queue_depth,
        }

    # ------------------------------------------------------------------
    # HTML rendering
    # ------------------------------------------------------------------

    def _build_html(
        self,
        year: int,
        month: int,
        events: Dict[int, List[Dict[str, Any]]],
        queue_depth: Dict[str, int],
        brand_filter: Optional[str],
    ) -> str:
        """Build the complete HTML calendar page.

        Parameters
        ----------
        year:
            Calendar year.
        month:
            Calendar month.
        events:
            Events dict from ``_get_month_events``.
        queue_depth:
            Queue depth per brand.
        brand_filter:
            Active brand filter.

        Returns
        -------
        str
            HTML page string.
        """
        month_name = calendar.month_name[month]
        cal = calendar.Calendar(firstweekday=0)  # Monday start
        weeks = cal.monthdayscalendar(year, month)

        # Navigation
        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1

        brand_param = f"&brand={brand_filter}" if brand_filter else ""

        # Build table rows
        table_rows = ""
        for week in weeks:
            table_rows += "<tr>"
            for day in week:
                if day == 0:
                    table_rows += '<td class="empty"></td>'
                    continue

                day_events = events.get(day, [])
                today_class = ""
                now = datetime.now(timezone.utc)
                if year == now.year and month == now.month and day == now.day:
                    today_class = " today"

                events_html = ""
                for ev in day_events[:4]:  # Max 4 events per cell
                    colour = BRAND_COLOURS.get(ev["brand_id"], "#888")
                    icon = PLATFORM_ICONS.get(ev["platform"], "")
                    status_class = ev["status"]
                    title_short = (ev["title"] or "")[:25]
                    events_html += (
                        f'<div class="event {status_class}" '
                        f'style="border-left:3px solid {colour}">'
                        f'<span class="time">{ev["time"]}</span> '
                        f'{icon} {title_short}'
                        f'</div>'
                    )
                if len(day_events) > 4:
                    events_html += (
                        f'<div class="more">+{len(day_events) - 4} more</div>'
                    )

                table_rows += (
                    f'<td class="day{today_class}">'
                    f'<div class="day-number">{day}</div>'
                    f'{events_html}'
                    f'</td>'
                )
            table_rows += "</tr>"

        # Queue depth sidebar
        queue_html = ""
        for brand_id, count in sorted(queue_depth.items()):
            colour = BRAND_COLOURS.get(brand_id, "#888")
            short_name = brand_id.replace("_success_guru", "").replace("_", " ").title()
            queue_html += (
                f'<div class="queue-item">'
                f'<span class="queue-dot" style="background:{colour}"></span>'
                f'{short_name}: <strong>{count}</strong> ready'
                f'</div>'
            )

        # Brand filter buttons
        filter_html = f'<a href="/calendar?year={year}&month={month}" class="brand-btn{"" if brand_filter else " active"}">All</a>'
        for bid, colour in BRAND_COLOURS.items():
            short = bid.replace("_success_guru", "").replace("_", " ").title()
            active = " active" if brand_filter == bid else ""
            filter_html += (
                f'<a href="/calendar?year={year}&month={month}&brand={bid}" '
                f'class="brand-btn{active}" '
                f'style="border-color:{colour}">{short}</a>'
            )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoFarm Calendar — {month_name} {year}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#1a1a2e; color:#e0e0e0; }}
  .container {{ max-width:1400px; margin:0 auto; padding:20px; }}
  .header {{ display:flex; justify-content:space-between; align-items:center;
             margin-bottom:20px; }}
  .header h1 {{ font-size:24px; }}
  .nav {{ display:flex; gap:10px; }}
  .nav a {{ color:#4A90D9; text-decoration:none; padding:8px 16px;
            background:#16213e; border-radius:6px; }}
  .nav a:hover {{ background:#1a2a4e; }}
  .filters {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }}
  .brand-btn {{ padding:6px 12px; border-radius:4px; text-decoration:none;
                color:#e0e0e0; background:#16213e; border:2px solid #333;
                font-size:13px; }}
  .brand-btn.active {{ background:#1a2a4e; font-weight:bold; }}
  .layout {{ display:flex; gap:20px; }}
  .calendar-wrap {{ flex:1; }}
  .sidebar {{ width:220px; }}
  table {{ width:100%; border-collapse:collapse; background:#16213e;
           border-radius:8px; overflow:hidden; }}
  th {{ padding:10px; background:#0f3460; text-align:center;
       font-size:13px; text-transform:uppercase; }}
  td {{ padding:6px; vertical-align:top; min-height:100px; height:110px;
       border:1px solid #1a1a2e; width:14.28%; }}
  td.empty {{ background:#111; }}
  td.today {{ background:#1a2a4e; }}
  .day-number {{ font-weight:bold; font-size:14px; margin-bottom:4px;
                 color:#aaa; }}
  td.today .day-number {{ color:#4A90D9; }}
  .event {{ font-size:11px; padding:2px 4px; margin:2px 0;
            border-radius:3px; background:#1a1a2e;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .event.published {{ opacity:0.7; }}
  .event.pending {{ background:#2a2a3e; }}
  .event .time {{ color:#888; }}
  .more {{ font-size:10px; color:#666; padding:2px 4px; }}
  .sidebar-card {{ background:#16213e; border-radius:8px; padding:16px;
                   margin-bottom:16px; }}
  .sidebar-card h3 {{ font-size:14px; margin-bottom:10px; color:#4A90D9; }}
  .queue-item {{ font-size:13px; padding:4px 0; display:flex;
                 align-items:center; gap:8px; }}
  .queue-dot {{ width:8px; height:8px; border-radius:50%;
                display:inline-block; }}
  .legend {{ display:flex; gap:12px; margin-top:12px; flex-wrap:wrap; }}
  .legend-item {{ font-size:12px; display:flex; align-items:center; gap:4px; }}
  .legend-dot {{ width:10px; height:10px; border-radius:2px; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>\U0001f4c5 {month_name} {year}</h1>
    <div class="nav">
      <a href="/calendar?year={prev_year}&month={prev_month}{brand_param}">&larr; Prev</a>
      <a href="/calendar?year={next_year}&month={next_month}{brand_param}">Next &rarr;</a>
      <a href="/dashboard">\U0001f4ca Dashboard</a>
    </div>
  </div>
  <div class="filters">{filter_html}</div>
  <div class="layout">
    <div class="calendar-wrap">
      <table>
        <thead>
          <tr><th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th>
              <th>Fri</th><th>Sat</th><th>Sun</th></tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
      <div class="legend">
        {''.join(
            f'<div class="legend-item"><div class="legend-dot" style="background:{c}"></div>{b.replace("_success_guru","").replace("_"," ").title()}</div>'
            for b, c in BRAND_COLOURS.items()
        )}
      </div>
    </div>
    <div class="sidebar">
      <div class="sidebar-card">
        <h3>Queue Depth</h3>
        {queue_html if queue_html else '<div style="color:#666">No items</div>'}
      </div>
      <div class="sidebar-card">
        <h3>Platforms</h3>
        {''.join(
            f'<div class="queue-item">{icon} {name.title()}</div>'
            for name, icon in PLATFORM_ICONS.items()
        )}
      </div>
    </div>
  </div>
</div>
</body>
</html>"""

        return html
