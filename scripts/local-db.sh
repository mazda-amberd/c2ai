#!/usr/bin/env bash
# Project-local PostgreSQL for development: data in .local/postgres, port 55432,
# localhost only, no password (trust auth). Usage: scripts/local-db.sh start|stop|status
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/.local/postgres"
PORT="${C2AI_DB_PORT:-55432}"
PG_BIN="${PG_BIN:-$(dirname "$(command -v pg_ctl 2>/dev/null || echo /Library/PostgreSQL/17/bin/pg_ctl)")}"

case "${1:-status}" in
  start)
    if [ ! -d "$DATA" ]; then
      mkdir -p "$ROOT/.local"
      "$PG_BIN/initdb" -D "$DATA" -U c2ai --auth=trust -E UTF8 >/dev/null
      echo "Initialised $DATA — next: cd backend && python -m c2ai.db.init_db --yes"
    fi
    "$PG_BIN/pg_ctl" -D "$DATA" -l "$ROOT/.local/postgres.log" \
      -o "-p $PORT -k $ROOT/.local -c listen_addresses=127.0.0.1" start
    ;;
  stop)
    "$PG_BIN/pg_ctl" -D "$DATA" stop -m fast
    ;;
  status)
    "$PG_BIN/pg_ctl" -D "$DATA" status || true
    ;;
  *)
    echo "usage: $0 start|stop|status" >&2
    exit 2
    ;;
esac
