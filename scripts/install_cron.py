"""
Install Cron — installs crontab entries for the appropriate VM.

Usage::

    python scripts/install_cron.py --vm content
    python scripts/install_cron.py --vm proxy
"""

import argparse
import subprocess
import sys


# Content-VM cron jobs (all times UTC)
CONTENT_CRON = """
# AutoFarm V6 — Content VM Cron Jobs
# Installed by scripts/install_cron.py

# Core pipeline: scan trends + generate content (every 2 hours)
0 */2 * * *  cd /app && .venv/bin/python jobs/scan_and_generate.py >> /app/logs/generate.log 2>&1

# Review processing (every 15 min)
*/15 * * * *  cd /app && .venv/bin/python jobs/process_review_queue.py >> /app/logs/review.log 2>&1

# Auto-approval check (every 30 min)
*/30 * * * *  cd /app && .venv/bin/python jobs/check_auto_approvals.py >> /app/logs/review.log 2>&1

# Publish due videos (every 5 min)
*/5 * * * *  cd /app && .venv/bin/python jobs/publish_due.py >> /app/logs/publish.log 2>&1

# Token refresh (daily 04:45 — before first publish window)
45 4 * * *  cd /app && .venv/bin/python jobs/refresh_tokens.py >> /app/logs/tokens.log 2>&1

# Analytics pull (daily 03:00)
0 3 * * *  cd /app && .venv/bin/python jobs/pull_analytics.py >> /app/logs/analytics.log 2>&1

# Background library maintenance (weekly Monday 02:00)
0 2 * * 1  cd /app && .venv/bin/python jobs/maintain_backgrounds.py >> /app/logs/backgrounds.log 2>&1

# Schedule reoptimization (weekly Monday 04:00)
0 4 * * 1  cd /app && .venv/bin/python jobs/reoptimise_schedule.py >> /app/logs/schedule.log 2>&1

# Daily resets (midnight)
0 0 * * *  cd /app && .venv/bin/python jobs/reset_daily_counts.py >> /app/logs/reset.log 2>&1
1 0 * * *  cd /app && .venv/bin/python jobs/reset_api_quotas.py >> /app/logs/reset.log 2>&1

# Daily digest (08:00)
0 8 * * *  cd /app && .venv/bin/python jobs/send_daily_digest.py >> /app/logs/digest.log 2>&1

# Storage check (06:00)
0 6 * * *  cd /app && .venv/bin/python jobs/check_storage.py >> /app/logs/health.log 2>&1

# Queue depth monitor (hourly)
0 * * * *  cd /app && .venv/bin/python jobs/check_queue_depth.py >> /app/logs/queue.log 2>&1

# Database backup (02:30)
30 2 * * *  cd /app && .venv/bin/python jobs/backup_database.py >> /app/logs/backup.log 2>&1

# Google Drive cleanup (05:00)
0 5 * * *  cd /app && .venv/bin/python jobs/cleanup_gdrive.py >> /app/logs/gdrive.log 2>&1

# Orphan file cleanup (04:00)
0 4 * * *  cd /app && .venv/bin/python jobs/cleanup_orphans.py >> /app/logs/cleanup.log 2>&1

# Config validation (05:30)
30 5 * * *  cd /app && .venv/bin/python jobs/validate_config.py >> /app/logs/health.log 2>&1

# User agent refresh (monthly 1st at 03:00)
0 3 1 * *  cd /app && .venv/bin/python jobs/refresh_user_agents.py >> /app/logs/health.log 2>&1
"""

PROXY_CRON = """
# AutoFarm V6 — Proxy VM Cron Jobs
# Installed by scripts/install_cron.py

# Keepalive check (every 5 min)
*/5 * * * *  /app/scripts/keepalive_proxy.sh >> /var/log/keepalive.log 2>&1

# Log rotation (daily)
0 0 * * *  /usr/sbin/logrotate /app/config/logrotate.conf
"""


def main() -> None:
    """Install crontab entries for the specified VM.

    Parameters
    ----------
    --vm : str
        Either ``content`` or ``proxy``.

    Side Effects
    ------------
    Replaces the current user's crontab with the appropriate entries.
    """
    parser = argparse.ArgumentParser(description="Install AutoFarm cron jobs")
    parser.add_argument(
        "--vm", required=True, choices=["content", "proxy"],
        help="Which VM to install cron for",
    )
    args = parser.parse_args()

    cron_content = CONTENT_CRON if args.vm == "content" else PROXY_CRON

    print(f"\n  INSTALLING CRON JOBS ({args.vm}-vm)")
    print("=" * 50)
    print(cron_content)

    confirm = input("Install these cron jobs? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    # Install via crontab
    try:
        proc = subprocess.run(
            ["crontab", "-"],
            input=cron_content,
            text=True,
            capture_output=True,
        )
        if proc.returncode == 0:
            print(f"  Cron jobs installed for {args.vm}-vm")
        else:
            print(f"  ERROR: {proc.stderr}")
            sys.exit(1)
    except FileNotFoundError:
        print("  ERROR: crontab command not found")
        sys.exit(1)


if __name__ == "__main__":
    main()
