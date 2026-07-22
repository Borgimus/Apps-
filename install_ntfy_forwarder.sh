#!/usr/bin/env bash
# Install a persistent ntfy forwarder for Phase 3 push events.
#
# Reads ~/trader/logs/push_events.jsonl and publishes new events to ntfy.
# Installs a systemd service, a manual test command, and keeps credentials/topic
# outside the git repository.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/trader}"
ENV_FILE="$HOME/.config/trader-ntfy.env"
FORWARDER="$HOME/ntfy_forwarder.py"
NOTIFY_HELPER="$HOME/notify_trader.sh"
SERVICE_NAME="trader-ntfy-forwarder"

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -d "$REPO_DIR" ] || fail "Trading directory not found at $REPO_DIR"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v systemctl >/dev/null 2>&1 || fail "systemd is required"
command -v sudo >/dev/null 2>&1 || [ "$(id -u)" = "0" ] || fail "sudo or root is required"

mkdir -p "$HOME/.config" "$REPO_DIR/logs"

DEFAULT_URL="https://ntfy.sh"
read -rp "ntfy server URL [$DEFAULT_URL]: " NTFY_URL
NTFY_URL="${NTFY_URL:-$DEFAULT_URL}"
NTFY_URL="${NTFY_URL%/}"

read -rsp "ntfy topic name: " NTFY_TOPIC; echo
[ -n "$NTFY_TOPIC" ] || fail "Topic cannot be blank"

read -rsp "ntfy access token (press Enter if topic is anonymous): " NTFY_TOKEN; echo

umask 177
cat > "$ENV_FILE" <<EOF
NTFY_URL=$NTFY_URL
NTFY_TOPIC=$NTFY_TOPIC
NTFY_TOKEN=$NTFY_TOKEN
TRADER_REPO=$REPO_DIR
EOF
chmod 600 "$ENV_FILE"
umask 022

say "Writing manual notification helper"
cat > "$NOTIFY_HELPER" <<'HELPER'
#!/usr/bin/env bash
set -euo pipefail
ENV_FILE="$HOME/.config/trader-ntfy.env"
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
MESSAGE="${1:-Trader notification test}"
TITLE="${2:-Options Trader}"
PRIORITY="${3:-default}"
TAGS="${4:-chart_with_upwards_trend}"
ARGS=(-fsS -X POST "${NTFY_URL%/}/${NTFY_TOPIC}" -H "Title: $TITLE" -H "Priority: $PRIORITY" -H "Tags: $TAGS" --data-binary "$MESSAGE")
if [ -n "${NTFY_TOKEN:-}" ]; then
  ARGS+=(-H "Authorization: Bearer ${NTFY_TOKEN}")
fi
curl "${ARGS[@]}"
HELPER
chmod +x "$NOTIFY_HELPER"

say "Writing persistent event forwarder"
cat > "$FORWARDER" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("ntfy_forwarder")

HOME = Path.home()
REPO = Path(os.environ.get("TRADER_REPO", HOME / "trader"))
EVENTS = REPO / "logs" / "push_events.jsonl"
STATE = HOME / ".ntfy_forwarder_state.json"
BASE_URL = os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
TOKEN = os.environ.get("NTFY_TOKEN", "").strip()
POLL_SECONDS = 2
STOP = False

if not TOPIC:
    raise SystemExit("NTFY_TOPIC is missing")


def stop_handler(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, stop_handler)
signal.signal(signal.SIGINT, stop_handler)


def load_state() -> dict[str, int]:
    try:
        data = json.loads(STATE.read_text())
        return {"inode": int(data.get("inode", 0)), "offset": int(data.get("offset", 0))}
    except Exception:
        return {"inode": 0, "offset": 0}


def save_state(inode: int, offset: int) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"inode": inode, "offset": offset}))
    tmp.replace(STATE)


def event_headers(event_type: str) -> dict[str, str]:
    mapping = {
        "fill": ("Trade filled", "high", "moneybag"),
        "exit": ("Position exited", "high", "checkered_flag"),
        "eod_warning": ("EOD warning", "urgent", "warning"),
        "session_end": ("Session complete", "high", "bar_chart"),
        "heartbeat": ("Trading heartbeat", "low", "heartbeat"),
    }
    title, priority, tags = mapping.get(event_type, ("Options Trader", "default", "chart_with_upwards_trend"))
    return {"Title": title, "Priority": priority, "Tags": tags}


def publish(event: dict[str, Any]) -> None:
    event_type = str(event.get("event", "notification"))
    message = str(event.get("message") or json.dumps(event, separators=(",", ":")))
    headers = event_headers(event_type)
    headers["Content-Type"] = "text/plain; charset=utf-8"
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    req = urllib.request.Request(
        f"{BASE_URL}/{TOPIC}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")


def main() -> int:
    state = load_state()
    first_seen = not STATE.exists()
    log.info("forwarding %s to %s/<redacted-topic>", EVENTS, BASE_URL)

    while not STOP:
        if not EVENTS.exists():
            time.sleep(POLL_SECONDS)
            continue

        try:
            stat = EVENTS.stat()
            inode = int(stat.st_ino)
            size = int(stat.st_size)

            if first_seen:
                # Test delivery is sent during install. Start at EOF so old sessions
                # are not replayed to the phone.
                state = {"inode": inode, "offset": size}
                save_state(inode, size)
                first_seen = False
                log.info("initialised at current EOF offset=%d", size)

            if state["inode"] != inode or state["offset"] > size:
                state = {"inode": inode, "offset": 0}
                save_state(inode, 0)
                log.info("event log rotated; restarting at offset 0")

            with EVENTS.open("r", encoding="utf-8") as fh:
                fh.seek(state["offset"])
                while not STOP:
                    line_start = fh.tell()
                    line = fh.readline()
                    if not line:
                        break
                    line_end = fh.tell()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("skipping malformed JSON line at offset %d", line_start)
                        state["offset"] = line_end
                        save_state(inode, line_end)
                        continue

                    try:
                        publish(event)
                    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                        # Do not advance the offset. The event will retry next pass.
                        log.error("publish failed at offset %d: %s", line_start, exc)
                        time.sleep(10)
                        break

                    state["offset"] = line_end
                    save_state(inode, line_end)
                    log.info("published event=%s", event.get("event"))
        except Exception:
            log.exception("forwarder loop error")
            time.sleep(10)

        time.sleep(POLL_SECONDS)

    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY
chmod +x "$FORWARDER"
python3 -m py_compile "$FORWARDER"

say "Sending immediate test notification"
if "$NOTIFY_HELPER" \
  "ntfy is connected to the options trader. Future fills, exits, heartbeats, EOD warnings, and session summaries will appear here." \
  "Options Trader Connected" \
  "high" \
  "white_check_mark"; then
  echo
  echo "Test publish accepted by ntfy. Check the Android notification now."
else
  fail "Test publish failed. Verify URL, topic, token, and server internet access."
fi

say "Installing systemd service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SUDO="sudo"; [ "$(id -u)" = "0" ] && SUDO=""
CURRENT_USER="$(id -un)"
CURRENT_GROUP="$(id -gn)"
CURRENT_HOME="$HOME"

$SUDO tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=Options trader ntfy push-event forwarder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
Group=$CURRENT_GROUP
Environment=HOME=$CURRENT_HOME
EnvironmentFile=$ENV_FILE
WorkingDirectory=$REPO_DIR
ExecStart=/usr/bin/python3 $FORWARDER
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$CURRENT_HOME $REPO_DIR

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now "$SERVICE_NAME.service"
sleep 2
$SUDO systemctl --no-pager --full status "$SERVICE_NAME.service" || true

say "Installed"
echo "Manual test:"
echo "  ~/notify_trader.sh 'Manual test from trader'"
echo
echo "Service status:"
echo "  sudo systemctl status $SERVICE_NAME"
echo
echo "Recent forwarder logs:"
echo "  sudo journalctl -u $SERVICE_NAME -n 50 --no-pager"
echo
echo "The forwarder starts at the current end of push_events.jsonl, so it will not flood your phone with old session events."
