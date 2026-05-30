#!/usr/bin/env bash
#
# Cron wrapper for the Luxembourg property monitor.
# Activates the project venv, runs the full pipeline, and appends to logs/.
#
# Make executable once:   chmod +x scripts/run_lux.sh
# Example crontab entries (edit with `crontab -e`):
#   # every day at 07:15
#   15 7 * * *  /ABSOLUTE/PATH/TO/Immo/scripts/run_lux.sh
#   # or weekdays only, twice a day (08:00 and 18:00)
#   0 8,18 * * 1-5  /ABSOLUTE/PATH/TO/Immo/scripts/run_lux.sh
#
# Don't use `sleep`/loops here — let cron schedule it.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# shellcheck disable=SC1091
source .venv/bin/activate

mkdir -p logs
ts="$(date +%Y-%m-%dT%H:%M:%S)"
{
  echo "=== run_lux start $ts ==="
  python -m src.lux_monitor run
  rc=$?
  echo "=== run_lux done $ts (exit $rc) ==="
} >> logs/lux_run.log 2>&1

exit "${rc:-0}"
