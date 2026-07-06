#!/usr/bin/env bash
# WSL-side half of "Stop HIVE": kills the uvicorn backend and any orphaned
# HIVE claude workers, then reports what remains running for this user.
# Called by scripts/stop-hive.ps1 — safe to run standalone too.
#
# Worker match is deliberately narrow: HIVE workers (backend/workers/claude_cli.py)
# always run with `--output-format stream-json ... --dangerously-skip-permissions`.
# Interactive `claude` sessions never carry that combination, so they survive.

set -u

KILLED=0

kill_matching() {
    local label="$1" pattern="$2"
    # pgrep -f matches full command lines; exclude this script's own process tree.
    local pids
    pids=$(pgrep -f -- "$pattern" || true)
    if [ -z "$pids" ]; then
        echo "  (no $label running)"
        return
    fi
    for pid in $pids; do
        local cmd
        cmd=$(ps -p "$pid" -o args= 2>/dev/null | cut -c1-110)
        [ -z "$cmd" ] && continue
        echo "  killing $label pid $pid: $cmd"
        kill "$pid" 2>/dev/null && KILLED=$((KILLED + 1))
    done
}

echo "[WSL] stopping HIVE backend..."
kill_matching "uvicorn backend" "uvicorn backend\.main:app"

echo "[WSL] stopping orphaned HIVE claude workers..."
kill_matching "claude worker" "claude.*--output-format stream-json.*--dangerously-skip-permissions"

# Give processes a moment, then force anything that ignored SIGTERM.
if [ "$KILLED" -gt 0 ]; then
    sleep 2
    for pattern in "uvicorn backend\.main:app" "claude.*--output-format stream-json.*--dangerously-skip-permissions"; do
        pids=$(pgrep -f -- "$pattern" || true)
        for pid in $pids; do
            echo "  SIGKILL pid $pid (ignored SIGTERM)"
            kill -9 "$pid" 2>/dev/null
        done
    done
fi

echo "[WSL] killed $KILLED process(es)."

# Report what else this user is running (so the Windows script can decide
# whether to offer `wsl --shutdown`). Exclude kernel/system noise and the
# transient processes of this very invocation.
echo "[WSL] remaining user processes:"
# Exclude kernel threads, this script's own process tree, and the always-on
# per-user infra (systemd --user, sd-pam) that would otherwise make WSL look
# permanently busy. Open shells and claude sessions DO count as busy.
REMAINING=$(ps -u "$(id -un)" -o pid=,args= 2>/dev/null \
    | awk '{ cmd = $2; sub(".*/", "", cmd)
             if (cmd ~ /^\[/) next
             if (cmd ~ /^(ps|awk|sleep|systemd|\(sd-pam\)|init)$/) next
             print }' \
    | grep -v -F "stop-hive-wsl.sh" || true)
if [ -z "$REMAINING" ]; then
    echo "  (none)"
    echo "WSL_IDLE=1"
else
    echo "$REMAINING" | sed 's/^/  /'
    echo "WSL_IDLE=0"
fi
