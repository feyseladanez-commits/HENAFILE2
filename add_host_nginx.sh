#!/usr/bin/env bash
# Adds (or updates) the nginx location block that exposes one host's
# SMS-webhook publicly, then reloads nginx.
#
# Usage:
#   sudo deploy/add_host_nginx.sh <HOST_ID> <WEBHOOK_PORT>
#
# Get <HOST_ID> and <WEBHOOK_PORT> from the Super Admin bot:
#   /host <HOST_ID>   -> shows "SMS Webhook ወደብ (ለNginx): <port>"
#
# Example:
#   sudo deploy/add_host_nginx.sh H1 9001
#
# After this, the public webhook URL for the host's SMS-forwarder app is:
#   https://sms.yourdomain.com/<HOST_ID>/sms-webhook

set -euo pipefail

CONF="/etc/nginx/sites-available/jemo"
START_MARKER="# === JEMO_HOST_BLOCKS_START ==="
END_MARKER="# === JEMO_HOST_BLOCKS_END ==="

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <HOST_ID> <WEBHOOK_PORT>" >&2
    exit 1
fi

HOST_ID="$1"
PORT="$2"

if [[ ! -f "$CONF" ]]; then
    echo "❌ $CONF not found. Copy deploy/nginx_jemo.conf.template there first (see DEPLOY.md)." >&2
    exit 1
fi

if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
    echo "❌ WEBHOOK_PORT must be a number, got: $PORT" >&2
    exit 1
fi

BLOCK="    location /${HOST_ID}/sms-webhook {\n        proxy_pass http://127.0.0.1:${PORT}/sms-webhook;\n        proxy_set_header Host \$host;\n        proxy_set_header X-Real-IP \$remote_addr;\n        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto \$scheme;\n    }"

if grep -q "location /${HOST_ID}/sms-webhook {" "$CONF"; then
    echo "ℹ️  A block for ${HOST_ID} already exists in $CONF - remove it by hand first if you want to change the port."
    exit 0
fi

# Insert the new block right before the END marker (POSIX-safe with awk,
# since sed's multi-line insertion is fiddly to keep portable).
TMP="$(mktemp)"
awk -v block="$BLOCK" -v end="$END_MARKER" '
    index($0, end) && !done { print block; done=1 }
    { print }
' "$CONF" > "$TMP"
mv "$TMP" "$CONF"

nginx -t
systemctl reload nginx

echo "✅ Added ${HOST_ID} -> 127.0.0.1:${PORT} and reloaded nginx."
echo "🔗 Public webhook URL: https://sms.yourdomain.com/${HOST_ID}/sms-webhook"
