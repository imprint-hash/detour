#!/bin/bash
# Start (or restart) the live screen. The PID file keeps restarts from guessing.
cd "$(dirname "$0")"
[ -f .server.pid ] && kill "$(cat .server.pid)" 2>/dev/null
set -a; . ./.env; set +a
nohup .venv/bin/python -m detour.server > .server.log 2>&1 &
echo $! > .server.pid
