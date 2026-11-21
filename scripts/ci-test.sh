#!/usr/bin/env bash
#
# ci-test.sh — einzige Quelle fuer die Test-Suite-Aufrufe (Phase 15, Plan 15-01).
#
# Zweck
# -----
# Der lokale Linux-Probelauf (scripts/linux-probelauf.sh) und der spaetere
# GitHub-Workflow rufen GENAU dieses Skript — es gibt keinen zweiten,
# abweichenden Testaufruf.
#
# Aufruf:  bash scripts/ci-test.sh <agent|webui|shell|guards|ruff|secrets|all>
#
# Exit: 0 gruen / 1 mindestens ein Teil rot / 2 Umgebungs- oder Nutzungsfehler
#
# Hinweis: Testausgaben werden NICHT durch eine Pipe geleitet (Falle aus
# release.sh: der Exit-Code der Pipe verdeckt den Exit-Code des Tests).
#
# agent und webui heissen intern beide `src` -> getrennte venvs, nie gemeinsam.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export TZ="${TZ:-Europe/Berlin}"
export LANG="${LANG:-C.UTF-8}"
export PYTHONDONTWRITEBYTECODE=1

PYTHON="${PYTHON:-python3}"
MODE="${1:-}"

usage() {
  echo "Nutzung: bash scripts/ci-test.sh <agent|webui|shell|guards|ruff|secrets|all>" >&2
  exit 2
}

case "$MODE" in
  agent | webui | shell | guards | ruff | secrets | all) ;;
  *) usage ;;
esac

# secrets (Phase 16): gitleaks ueber den Arbeitsbaum, mit CI_SCAN_HISTORY=1 auch
# ueber die gesamte Historie. Gehoert NICHT zu `all`: der Linux-Probelauf-
# Container hat kein Docker, check-secrets.sh braucht es fuer gitleaks.
# Exit 2 des Waechters (Werkzeugfehler) ist ebenfalls rot.
# Braucht weder Python noch TZ und laeuft deshalb VOR den Vorpruefungen.
suite_secrets() {
  bash scripts/check-secrets.sh dir "$ROOT" || return 1
  if [ "${CI_SCAN_HISTORY:-0}" = "1" ]; then
    bash scripts/check-secrets.sh history "$ROOT" || return 1
  fi
}

if [ "$MODE" = "secrets" ]; then
  suite_secrets
  exit $?
fi

# Vorpruefung TZ: Die Urlaubslogik entscheidet am Kalendertag (CLAUDE.md).
tz_abk="$(date +%Z)"
case "$tz_abk" in
  CET | CEST) ;;
  *)
    echo "FEHLER: TZ=$TZ liefert '$tz_abk' statt CET/CEST — tzdata fehlt oder TZ greift nicht." >&2
    exit 2
    ;;
esac

# Vorpruefung Python: 3.13 Pflicht.
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)'; then
  echo "FEHLER: $PYTHON ist nicht Python 3.13." >&2
  exit 2
fi

venv_bereit() {
  # $1 = Suite (agent|webui)
  local venv="$ROOT/.venv-ci-$1"
  if [ ! -x "$venv/bin/python" ]; then
    "$PYTHON" -m venv "$venv"
  fi
  "$venv/bin/python" -m pip install --quiet --upgrade pip
  "$venv/bin/python" -m pip install --quiet -e "./$1[dev]"
}

suite_agent() {
  venv_bereit agent
  (cd "$ROOT/agent" && "$ROOT/.venv-ci-agent/bin/python" -m pytest -p no:cacheprovider -q)
}

suite_webui() {
  venv_bereit webui
  (cd "$ROOT/webui" && "$ROOT/.venv-ci-webui/bin/python" -m pytest -p no:cacheprovider -q)
}

suite_shell() {
  local rot=0 namen=""
  local f
  for f in tests/test_*.sh; do
    echo "== bash $f"
    if ! bash "$f"; then
      rot=$((rot + 1))
      namen="$namen $f"
    fi
  done
  if [ "$rot" -gt 0 ]; then
    echo "ROT: $rot Shell-Test(s):$namen" >&2
    return 1
  fi
}

suite_guards() {
  if [ ! -d "$ROOT/agent/src" ]; then
    echo "FEHLER: agent/src fehlt im Checkout — SMTP-Waechter waere wirkungslos." >&2
    return 1
  fi
  bash scripts/check-addin-no-autosend.sh || return 1
  bash scripts/check-no-dist-token.sh || return 1
  bash scripts/check-actions-pinned.sh || return 1
  venv_bereit webui
  local log rc=0
  log="$(mktemp)"
  (cd "$ROOT/webui" && "$ROOT/.venv-ci-webui/bin/python" -m pytest -p no:cacheprovider -q -rs \
    tests/test_chat_tools.py::test_agent_quellbaum_bleibt_smtp_frei \
    tests/test_chat_tools.py::test_nur_mail_send_modul_importiert_smtplib) > "$log" 2>&1 || rc=$?
  cat "$log"
  if grep -q "skipped" "$log"; then
    echo "FEHLER: SMTP-Wächter wurde übersprungen — agent/src fehlt im Checkout?" >&2
    rm -f "$log"
    return 1
  fi
  if [ "$rc" -ne 0 ] || ! grep -q "2 passed" "$log"; then
    echo "FEHLER: SMTP-Waechter rot oder nicht '2 passed' (rc=$rc)." >&2
    rm -f "$log"
    return 1
  fi
  rm -f "$log"
}

suite_ruff() {
  local venv="$ROOT/.venv-ci-ruff"
  if [ ! -x "$venv/bin/python" ]; then
    "$PYTHON" -m venv "$venv"
  fi
  "$venv/bin/python" -m pip install --quiet --upgrade pip
  "$venv/bin/python" -m pip install --quiet -r "$ROOT/requirements-ci.txt"
  "$venv/bin/ruff" check .
}

if [ "$MODE" != "all" ]; then
  "suite_$MODE"
  exit $?
fi

r_agent="$(suite_agent >&2 && echo OK || echo ROT)"
r_webui="$(suite_webui >&2 && echo OK || echo ROT)"
r_shell="$(suite_shell >&2 && echo OK || echo ROT)"
r_guards="$(suite_guards >&2 && echo OK || echo ROT)"
r_ruff="$(suite_ruff >&2 && echo OK || echo ROT)"
echo "agent=$r_agent webui=$r_webui shell=$r_shell guards=$r_guards ruff=$r_ruff"
if [ "$r_agent$r_webui$r_shell$r_guards$r_ruff" = "OKOKOKOKOK" ]; then
  exit 0
fi
exit 1
