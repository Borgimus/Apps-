#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Installs the phone-notification forwarder on the trading VPS.
#
# The session runner writes every notable event (entry fills, position exits,
# heartbeats, EOD warning, session end) to logs/push_events.jsonl. This
# forwarder tails that file and pushes each event to your phone through
# ntfy.sh. Fills/exits/session-end arrive as sounding notifications;
# heartbeats arrive silently (visible in the drawer, no buzz).
#
# Prereq: install the "ntfy" app on your phone and subscribe to the same
# topic name you enter here. The topic name acts as the only password —
# make it long and unguessable.
#
# Run:  bash install_notifications.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ENV_FILE="$HOME/trader/.env"
[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE not found — run vps_bootstrap.sh first"; exit 1; }

# ── Topic ────────────────────────────────────────────────────────────────────
CURRENT_TOPIC="$(grep -oP '^NTFY_TOPIC=\K.*' "$ENV_FILE" 2>/dev/null || true)"
if [ -n "$CURRENT_TOPIC" ]; then
  echo "Existing topic found: $CURRENT_TOPIC (keeping it)"
  TOPIC="$CURRENT_TOPIC"
else
  read -rp "ntfy topic name (same one you subscribed to in the app): " TOPIC
  [ -n "$TOPIC" ] || { echo "ERROR: empty topic"; exit 1; }
  echo "NTFY_TOPIC=$TOPIC" >> "$ENV_FILE"
fi

# ── Forwarder (stdlib-only Python) ───────────────────────────────────────────
cat > "$HOME/notify_forwarder.py" <<'EOF'
#!/usr/bin/env python3
"""Tail trader/logs/push_events.jsonl and push each event to ntfy."""
import json, pathlib, time, urllib.request

HOME = pathlib.Path.home()
EVENTS = HOME / "trader" / "logs" / "push_events.jsonl"
OFFSET = HOME / ".notify_offset"

# event type -> (notification title, ntfy priority)
STYLES = {
    "fill":        ("Entry filled",        "high"),
    "exit":        ("Position closed",     "high"),
    "eod_warning": ("EOD exit approaching","default"),
    "session_end": ("Session ended",       "high"),
    "heartbeat":   ("Session heartbeat",   "min"),   # silent
}

def topic() -> str:
    for line in (HOME / "trader" / ".env").read_text().splitlines():
        if line.startswith("NTFY_TOPIC="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("NTFY_TOPIC not set in trader/.env")

def send(t: str, title: str, prio: str, msg: str) -> None:
    req = urllib.request.Request(
        f"https://ntfy.sh/{t}",
        data=msg.encode(),
        headers={"Title": title, "Priority": prio},
    )
    urllib.request.urlopen(req, timeout=10)

def main() -> None:
    t = topic()
    off = int(OFFSET.read_text()) if OFFSET.exists() else 0
    while True:
        try:
            if EVENTS.exists():
                size = EVENTS.stat().st_size
                if size < off:      # file replaced/truncated
                    off = 0
                if size > off:
                    with EVENTS.open() as f:
                        f.seek(off)
                        chunk = f.read()
                        off = f.tell()
                    for line in chunk.splitlines():
                        try:
                            e = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        title, prio = STYLES.get(e.get("event"), ("Trading update", "default"))
                        try:
                            send(t, title, prio, e.get("message", ""))
                        except Exception:
                            pass          # network hiccup: retry cycle picks it up
                    OFFSET.write_text(str(off))
        except Exception:
            pass
        time.sleep(15)

if __name__ == "__main__":
    main()
EOF

cat > "$HOME/notify_forwarder.sh" <<'EOF'
#!/usr/bin/env bash
exec 9>"$HOME/.notify_forwarder.lock"
flock -n 9 || exit 0
PY="$HOME/trader/.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" "$HOME/notify_forwarder.py"
EOF
chmod +x "$HOME/notify_forwarder.sh"

# ── Keep it running across reboots ───────────────────────────────────────────
{ crontab -l 2>/dev/null | grep -v notify_forwarder || true; \
  echo "@reboot $HOME/notify_forwarder.sh"; } | crontab -

# ── Start now + test push ────────────────────────────────────────────────────
nohup "$HOME/notify_forwarder.sh" >/dev/null 2>&1 &
sleep 1
curl -fsS -H "Title: Trader notifications connected" -H "Priority: high" \
  -d "If you can read this on your phone, session alerts are live." \
  "https://ntfy.sh/$TOPIC" >/dev/null && echo "Test notification sent — check your phone."

echo "Forwarder installed and running. It survives reboots (@reboot cron)."
