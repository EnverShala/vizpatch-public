#!/usr/bin/env bash
#
# linux-probelauf.sh — lokaler Linux-Probelauf der Test-Suiten (Phase 15, PITFALLS P9).
#
# Entwickelt wird auf Windows, CI und Kundeninstallationen laufen unter Linux
# (CRLF, TZ, Pfade, Rechte). Dieses Skript fuehrt scripts/ci-test.sh in einem
# frischen python:3.13-slim-Container als Nicht-root-Benutzer aus.
#
# WICHTIG: getestet wird der COMMITTETE Stand aus einem frischen Klon —
# nicht committete Aenderungen werden NICHT geprueft.
#
# Aufruf:  bash scripts/linux-probelauf.sh [modus] [tz]
#          Default: all Europe/Berlin
# Log:     dist/linux-probelauf/<modus>-<tz-ohne-schraegstrich>.log (dist/ ist gitignored)
# Exit:    Exit-Code des Containers; 2 wenn Docker nicht erreichbar.

set -euo pipefail

MODUS="${1:-all}"
TZ_WERT="${2:-Europe/Berlin}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! docker version > /dev/null 2>&1; then
  echo "FEHLER: Docker-Daemon nicht erreichbar — bitte Docker Desktop starten." >&2
  exit 2
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "WARNUNG: Arbeitsbaum ist nicht sauber — getestet wird nur der COMMITTETE Stand." >&2
fi

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    REPO="$(pwd -W)"
    export MSYS_NO_PATHCONV=1
    ;;
  *) REPO="$(pwd)" ;;
esac

LOGDIR="$ROOT/dist/linux-probelauf"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/${MODUS}-${TZ_WERT//\//}.log"

INNEN='set -e
apt-get update -qq
apt-get install -y -qq --no-install-recommends git tzdata ca-certificates > /dev/null
useradd -m ci
runuser -u ci -- bash -c "set -e
git config --global --add safe.directory \"*\"
git clone -q --no-hardlinks file:///repo /home/ci/work
cd /home/ci/work
bash scripts/ci-test.sh '"$MODUS"'"'

rc=0
docker run --rm -e "TZ=$TZ_WERT" -e LANG=C.UTF-8 -v "$REPO:/repo:ro" \
  python:3.13-slim bash -c "$INNEN" > "$LOG" 2>&1 || rc=$?

tail -n 25 "$LOG"
echo "Log: $LOG"
echo "Exit-Code: $rc"
exit "$rc"
