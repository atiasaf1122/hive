#!/usr/bin/env bash
# Daemonize the HIVE backend (uvicorn on 127.0.0.1:8765) so it survives the
# wsl.exe that launched it. Called by scripts/launch-hive.ps1 — safe standalone.
#
# Why the setsid dance: a plain `nohup ... &` from `wsl.exe -- bash -lc` dies
# in a race — bash exits immediately, wsl.exe tears the session down, and the
# backgrounded subshell is killed before it even runs. setsid puts the child
# in a new session, and the trailing sleep keeps wsl.exe alive long enough
# for the child to escape.

set -u
LOG=/tmp/hive-backend.log

if pgrep -f 'uvicorn backend\.main:app' >/dev/null; then
    echo "backend already running"
    exit 0
fi

setsid bash -c 'cd ~/hive && source .venv/bin/activate && exec python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765' \
    >> "$LOG" 2>&1 < /dev/null &
sleep 1
echo "backend starting (log: $LOG)"
