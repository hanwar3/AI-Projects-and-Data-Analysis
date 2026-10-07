#!/usr/bin/env bash
# Same launcher for macOS and Linux.  chmod +x job-search-app.sh  &&  ./job-search-app.sh
set -euo pipefail
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
    echo "Python 3 not found. Install it from python.org or your package manager."
    exit 1
fi

[ -f data/jobs.db ] || "$PY" jobhunt.py init

case "${1:-dashboard}" in
    dashboard|"") exec "$PY" jobhunt.py serve ;;
    search)       exec "$PY" jobhunt.py search --free-only ;;
    *)            exec "$PY" jobhunt.py "$@" ;;
esac
