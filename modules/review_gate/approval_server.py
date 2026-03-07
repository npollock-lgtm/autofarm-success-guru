"""
Approval Server — HTTP server on proxy-vm (port 8080) handling review decisions.

Routes:
  /approve/{token}          — Approve a video
  /reject/{token}           — Reject a video
  /review/{token}           — Full-quality review page
  /health                   — System health JSON
  /dashboard                — Admin dashboard home
  /dashboard/brand/{id}     — Brand-specific dashboard
  /dashboard/queue          — Review queue view
  /dashboard/schedule       — Publishing schedule
  /dashboard/analytics      — Performance metrics
  /dashboard/health         — System health dashboard
  /dashboard/compliance     — Rate limit status

Lightweight: Flask + plain HTML/CSS/JS — no React/Vue.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("autofarm.review_gate.approval_server")


class ApprovalServer:
    """Flask-based approval server for the proxy VM.

    Parameters
    ----------
    db:
        Database helper instance.
    approval_tracker:
        ``ApprovalTracker`` for token validation.
    review_gate:
        ``ReviewGate`` for decision handling.
    health_monitor:
        Optional ``HealthMonitor`` for /health endpoint.
    host:
        Bind host.
    port:
        Bind port.
    """

    def __init__(
        self,
        db: Any,
        approval_tracker: Any,
        review_gate: Any,
        health_monitor: Optional[Any] = None,
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        self.db = db
        self.tracker = approval_tracker
        self.gate = review_gate
        self.health = health_monitor
        self.host = host
        self.port = port
        self.app = self._create_app()

    # ------------------------------------------------------------------
    # Flask app factory
    # ------------------------------------------------------------------

    def _create_app(self) -> Any:
        """Create and configure the Flask application.

        Returns
        -------
        Flask
            Configured Flask app with all routes registered.
        """
        try:
            from flask import Flask, request, jsonify, render_template_string
        except ImportError:
            logger.error("Flask not installed — approval server unavailable")
            return None

        app = Flask(__name__)
        server = self  # closure reference

        @app.route("/approve/<token>")
        def approve(token: str):
            """Handle approval via token link."""
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                valid = loop.run_until_complete(server.tracker.validate_token(token))
                if not valid:
                    return render_template_string(
                        server._error_page("Invalid or expired token.")
                    ), 400

                review_id = loop.run_until_complete(
                    server.tracker.get_review_id_for_token(token)
                )
                if review_id is None:
                    return render_template_string(
                        server._error_page("Review not found.")
                    ), 404

                loop.run_until_complete(server.tracker.mark_approved(token))
                loop.run_until_complete(server.gate.handle_approval(review_id))

                logger.info("Approved review %d via web", review_id)
                return render_template_string(
                    server._success_page(
                        "✅ Approved",
                        "Video has been added to the publish queue.",
                    )
                )
            finally:
                loop.close()

        @app.route("/reject/<token>")
        def reject(token: str):
            """Handle rejection via token link."""
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                valid = loop.run_until_complete(server.tracker.validate_token(token))
                if not valid:
                    return render_template_string(
                        server._error_page("Invalid or expired token.")
                    ), 400

                review_id = loop.run_until_complete(
                    server.tracker.get_review_id_for_token(token)
                )
                if review_id is None:
                    return render_template_string(
                        server._error_page("Review not found.")
                    ), 404

                reason = request.args.get("reason", "")
                loop.run_until_complete(
                    server.tracker.mark_rejected(token, reason)
                )
                loop.run_until_complete(
                    server.gate.handle_rejection(review_id, reason)
                )

                logger.info("Rejected review %d via web", review_id)
                return render_template_string(
                    server._success_page(
                        "❌ Rejected",
                        "Video has been removed from the queue.",
                    )
                )
            finally:
                loop.close()

        @app.route("/review/<token>")
        def review_page(token: str):
            """Render full-quality review page."""
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                status = loop.run_until_complete(
                    server.tracker.get_token_status(token)
                )
                if not status:
                    return render_template_string(
                        server._error_page("Review not found.")
                    ), 404

                review_id = status["review_id"]
                review = loop.run_until_complete(
                    server.db.fetch_one(
                        "SELECT * FROM reviews WHERE id = ?", (review_id,)
                    )
                )
                if not review:
                    return render_template_string(
                        server._error_page("Review data not found.")
                    ), 404

                return render_template_string(
                    server._review_page(dict(review), token, status["status"])
                )
            finally:
                loop.close()

        @app.route("/health")
        def health_check():
            """Return system health JSON."""
            if server.health:
                import asyncio

                loop = asyncio.new_event_loop()
                try:
                    report = loop.run_until_complete(
                        server.health.full_health_check()
                    )
                    return jsonify(report)
                except Exception as exc:
                    return jsonify({"status": "error", "message": str(exc)}), 500
                finally:
                    loop.close()
            return jsonify(
                {
                    "status": "ok",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        @app.route("/dashboard")
        def dashboard():
            """Admin dashboard home."""
            return render_template_string(server._dashboard_home())

        @app.route("/dashboard/brand/<brand_id>")
        def dashboard_brand(brand_id: str):
            """Brand-specific dashboard."""
            return render_template_string(
                server._dashboard_brand_page(brand_id)
            )

        @app.route("/dashboard/queue")
        def dashboard_queue():
            """Review queue visualisation."""
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                pending = loop.run_until_complete(
                    server.gate.get_pending_reviews()
                )
            except Exception:
                pending = []
            finally:
                loop.close()
            return render_template_string(
                server._dashboard_queue_page(pending)
            )

        @app.route("/dashboard/schedule")
        def dashboard_schedule():
            """Publishing schedule view."""
            return render_template_string(server._dashboard_schedule_page())

        @app.route("/dashboard/analytics")
        def dashboard_analytics():
            """Performance analytics."""
            return render_template_string(server._dashboard_analytics_page())

        @app.route("/dashboard/health")
        def dashboard_health():
            """System health dashboard."""
            return render_template_string(server._dashboard_health_page())

        @app.route("/dashboard/compliance")
        def dashboard_compliance():
            """Rate limit / compliance status."""
            return render_template_string(
                server._dashboard_compliance_page()
            )

        return app

    # ------------------------------------------------------------------
    # Run server
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the Flask development server.

        Side Effects
        ------------
        Blocks the calling thread.
        """
        if self.app is None:
            logger.error("Cannot start — Flask not available")
            return
        logger.info("Starting approval server on %s:%d", self.host, self.port)
        self.app.run(host=self.host, port=self.port, debug=False)

    # ------------------------------------------------------------------
    # HTML helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _base_html(title: str, body: str) -> str:
        """Wrap *body* in a styled HTML shell.

        Parameters
        ----------
        title:
            Page ``<title>``.
        body:
            Inner HTML.

        Returns
        -------
        str
        """
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — AutoFarm</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f5f5;color:#333;padding:20px}}
.c{{max-width:900px;margin:0 auto}}
.card{{background:#fff;border-radius:12px;padding:24px;margin:16px 0;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
h1{{font-size:24px;margin-bottom:16px}}h2{{font-size:20px;margin-bottom:12px}}
.btn{{display:inline-block;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;margin:4px;font-size:15px}}
.btn-g{{background:#28a745;color:#fff}}.btn-r{{background:#dc3545;color:#fff}}.btn-b{{background:#007bff;color:#fff}}
.nav{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}}
.nav a{{padding:8px 16px;background:#fff;border-radius:6px;text-decoration:none;color:#333;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.nav a:hover{{background:#007bff;color:#fff}}
table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:8px 12px;border-bottom:1px solid #eee}}
th{{background:#f8f8f8;font-weight:600}}
.sp{{color:#ffc107}}.sa{{color:#28a745}}.sr{{color:#dc3545}}
</style>
</head>
<body><div class="c">{body}</div></body></html>"""

    def _success_page(self, heading: str, message: str) -> str:
        """Render a success confirmation page."""
        body = f"""
<div class="card" style="text-align:center;padding:60px 24px">
<h1 style="font-size:48px;margin-bottom:20px">{heading}</h1>
<p style="font-size:18px;color:#666">{message}</p><br>
<a href="/dashboard" class="btn btn-b">Go to Dashboard</a>
</div>"""
        return self._base_html(heading, body)

    def _error_page(self, message: str) -> str:
        """Render an error page."""
        body = f"""
<div class="card" style="text-align:center;padding:60px 24px">
<h1 style="font-size:48px">⚠️</h1>
<p style="font-size:18px;color:#dc3545">{message}</p><br>
<a href="/dashboard" class="btn btn-b">Go to Dashboard</a>
</div>"""
        return self._base_html("Error", body)

    def _review_page(
        self, review: Dict[str, Any], token: str, status: str
    ) -> str:
        """Render the full-quality review page."""
        brand = review.get("brand_id", "unknown")
        buttons = ""
        if status == "pending":
            buttons = f"""
<div style="text-align:center;margin:30px 0">
<a href="/approve/{token}" class="btn btn-g">✅ Approve</a>
<a href="/reject/{token}" class="btn btn-r">❌ Reject</a>
</div>"""
        else:
            buttons = f'<p style="text-align:center">Decision: <b>{status}</b></p>'

        body = f"""
<h1>Review — {brand}</h1>
<div class="card">
<p><b>Review ID:</b> {review.get("id","?")}</p>
<p><b>Video ID:</b> {review.get("video_id","?")}</p>
<p><b>Brand:</b> {brand}</p>
<p><b>Platforms:</b> {review.get("platforms","?")}</p>
<p><b>Created:</b> {review.get("created_at","?")}</p>
</div>{buttons}"""
        return self._base_html(f"Review {brand}", body)

    def _dashboard_home(self) -> str:
        """Render admin dashboard home."""
        body = """
<h1>AutoFarm Dashboard</h1>
<div class="nav">
<a href="/dashboard/queue">📋 Queue</a>
<a href="/dashboard/schedule">📅 Schedule</a>
<a href="/dashboard/analytics">📊 Analytics</a>
<a href="/dashboard/health">💚 Health</a>
<a href="/dashboard/compliance">📏 Compliance</a>
</div>
<div class="card"><h2>Welcome</h2><p>AutoFarm V6.0 Content Management Dashboard</p></div>"""
        return self._base_html("Dashboard", body)

    def _dashboard_brand_page(self, brand_id: str) -> str:
        """Brand-specific dashboard."""
        body = f"""
<h1>Brand: {brand_id}</h1>
<div class="nav"><a href="/dashboard">← Back</a></div>
<div class="card"><h2>Queue Status</h2><p>Loading…</p></div>"""
        return self._base_html(f"Brand {brand_id}", body)

    def _dashboard_queue_page(self, pending: list) -> str:
        """Review queue page."""
        rows = ""
        for r in pending:
            rows += (
                f'<tr><td>{r.get("id","?")}</td><td>{r.get("brand_id","?")}</td>'
                f'<td>{r.get("video_id","?")}</td><td class="sp">{r.get("status","?")}</td>'
                f'<td>{r.get("created_at","?")}</td></tr>'
            )
        if not rows:
            rows = '<tr><td colspan="5" style="text-align:center">No pending reviews</td></tr>'
        body = f"""
<h1>Review Queue</h1><div class="nav"><a href="/dashboard">← Back</a></div>
<div class="card"><table>
<tr><th>ID</th><th>Brand</th><th>Video</th><th>Status</th><th>Created</th></tr>
{rows}</table></div>"""
        return self._base_html("Queue", body)

    def _dashboard_schedule_page(self) -> str:
        """Publishing schedule page."""
        body = """
<h1>Publishing Schedule</h1><div class="nav"><a href="/dashboard">← Back</a></div>
<div class="card"><p>Schedule view — populated by content_queue data.</p></div>"""
        return self._base_html("Schedule", body)

    def _dashboard_analytics_page(self) -> str:
        """Analytics page."""
        body = """
<h1>Analytics</h1><div class="nav"><a href="/dashboard">← Back</a></div>
<div class="card"><p>Performance metrics per brand and platform.</p></div>"""
        return self._base_html("Analytics", body)

    def _dashboard_health_page(self) -> str:
        """System health dashboard."""
        body = """
<h1>System Health</h1><div class="nav"><a href="/dashboard">← Back</a></div>
<div class="card"><div id="hd">Loading…</div></div>
<script>
fetch('/health').then(r=>r.json()).then(d=>{
document.getElementById('hd').innerHTML='<pre>'+JSON.stringify(d,null,2)+'</pre>'
}).catch(e=>{document.getElementById('hd').textContent='Error: '+e})
</script>"""
        return self._base_html("Health", body)

    def _dashboard_compliance_page(self) -> str:
        """Compliance / rate-limit status page."""
        body = """
<h1>Compliance</h1><div class="nav"><a href="/dashboard">← Back</a></div>
<div class="card"><p>Rate limit status and daily API usage counters.</p></div>"""
        return self._base_html("Compliance", body)


if __name__ == "__main__":
    from database.db import Database
    db = Database()
    server = ApprovalServer(db=db)
    server.start()
