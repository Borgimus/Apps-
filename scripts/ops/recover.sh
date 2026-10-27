#!/usr/bin/env bash
# Fetch the reviewed, exact public release without relying on an expiring PAT.
set -Eeuo pipefail
umask 077
release="${1:-}"
[[ "$release" =~ ^[0-9a-f]{40}$ ]] || { echo 'Supply the exact 40-character release SHA.'; exit 64; }
shift
test "$(id -u)" = 0 || { echo 'Run from the root VPS console.'; exit 77; }
test -d /root/trader/.git || { echo 'Existing /root/trader repository is missing.'; exit 78; }
recovery_source="$(mktemp -d /root/trader-recovery-source.XXXXXX)"
trap 'rm -rf "$recovery_source"' EXIT
GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c http.extraHeader= clone --quiet --no-checkout https://github.com/Borgimus/Apps-.git "$recovery_source/repo"
git -C "$recovery_source/repo" checkout --quiet --detach "$release"
test "$(git -C "$recovery_source/repo" rev-parse HEAD)" = "$release"
python_bin=/root/trader/.venv/bin/python
if ! test -x "$python_bin"; then
    echo 'Existing Python environment is unavailable. Install python3-venv and restore /root/trader/.venv.'
    exit 78
fi
"$python_bin" "$recovery_source/repo/scripts/ops/recover.py" --release "$release" --source "$recovery_source/repo" "$@"
