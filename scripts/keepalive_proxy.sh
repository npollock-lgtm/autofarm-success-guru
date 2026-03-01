#!/bin/bash
# scripts/keepalive_proxy.sh — Runs on proxy-vm every 5 minutes
# Verifies all Squid proxy instances are running and restarts any that died.
# Also checks the approval server (Flask) on port 8080.

set -u

BRANDS=(
    "human_success_guru"
    "wealth_success_guru"
    "zen_success_guru"
    "social_success_guru"
    "habits_success_guru"
    "relationships_success_guru"
)

PORTS=(3128 3129 3130 3131 3132 3133)
APPROVAL_SERVER_PORT=8080
LOG_PREFIX="[keepalive]"

echo "$LOG_PREFIX $(date -u +%Y-%m-%dT%H:%M:%SZ) Starting keepalive check"

# Check each Squid instance
for i in "${!BRANDS[@]}"; do
    brand="${BRANDS[$i]}"
    port="${PORTS[$i]}"
    pid_file="/var/run/squid/${brand}.pid"

    if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        echo "$LOG_PREFIX $brand (port $port): OK"
    else
        echo "$LOG_PREFIX $brand (port $port): DOWN — restarting"
        squid -f "/etc/squid/${brand}/squid.conf" &
        sleep 2

        if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
            echo "$LOG_PREFIX $brand: restarted successfully"
        else
            echo "$LOG_PREFIX $brand: FAILED to restart"
        fi
    fi
done

# Check approval server (Flask on port 8080)
if curl -sf "http://localhost:${APPROVAL_SERVER_PORT}/health" > /dev/null 2>&1; then
    echo "$LOG_PREFIX approval_server (port $APPROVAL_SERVER_PORT): OK"
else
    echo "$LOG_PREFIX approval_server (port $APPROVAL_SERVER_PORT): DOWN — restarting"
    cd /app && .venv/bin/python -m modules.review_gate.approval_server &
    sleep 3

    if curl -sf "http://localhost:${APPROVAL_SERVER_PORT}/health" > /dev/null 2>&1; then
        echo "$LOG_PREFIX approval_server: restarted successfully"
    else
        echo "$LOG_PREFIX approval_server: FAILED to restart"
    fi
fi

echo "$LOG_PREFIX $(date -u +%Y-%m-%dT%H:%M:%SZ) Keepalive check complete"
