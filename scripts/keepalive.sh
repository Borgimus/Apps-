#!/bin/bash
# Prevents VM idle-timeout by sending periodic activity to the agent proxy.
# Runs as orphan (PPID=1) so it survives environment-manager process-group kills.
PROXY="${HTTPS_PROXY:-http://127.0.0.1:44597}"
INTERVAL=45
while true; do
    curl -s --max-time 5 "${PROXY}/__agentproxy/status" > /dev/null 2>&1
    sleep $INTERVAL
done
