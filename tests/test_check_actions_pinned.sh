#!/usr/bin/env bash
#
# Gegenproben für scripts/check-actions-pinned.sh — ein Wächter, der nie rot
# wird, ist keiner. Jeder Fall schreibt eine Workflow-Datei in ein Temp-
# Verzeichnis und prüft den Exit-Code des Wächters.
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUARD="$ROOT/scripts/check-actions-pinned.sh"

SHA40="0123456789abcdef0123456789abcdef01234567"
SHA39="0123456789abcdef0123456789abcdef0123456"
SHA64="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

TMPDIRS=()
cleanup() { for t in "${TMPDIRS[@]:-}"; do [ -n "$t" ] && rm -rf "$t"; done; }
trap cleanup EXIT

fehler=0

# fall <name> <erwarteter exit> <inhalt-printf-format> [args...]
fall() {
  local name="$1" erwartet="$2" fmt="$3"
  shift 3
  local d
  d="$(mktemp -d)"
  TMPDIRS+=("$d")
  # shellcheck disable=SC2059
  printf "$fmt" "$@" > "$d/ci.yml"
  local rc
  set +e
  bash "$GUARD" "$d" > "$d/out.txt" 2>&1
  rc=$?
  set -e
  if [ "$rc" -ne "$erwartet" ]; then
    echo "FAIL: $name (erwartet Exit $erwartet, war $rc)"
    sed 's/^/    /' "$d/out.txt"
    fehler=$((fehler + 1))
  else
    echo "ok:   $name"
  fi
}

PERM='permissions:\n  contents: read\n'

# 1. gepinnt + permissions -> 0
fall "gepinnt" 0 "${PERM}jobs:\n  a:\n    steps:\n      - uses: actions/checkout@%s # v7.0.1\n" "$SHA40"
# 2. Tag statt SHA -> 1
fall "tag v7" 1 "${PERM}jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v7\n"
# 3. SHA ohne Versionskommentar -> 1
fall "sha ohne kommentar" 1 "${PERM}jobs:\n  a:\n    steps:\n      - uses: actions/checkout@%s\n" "$SHA40"
# 4. 39-stellige SHA -> 1
fall "sha 39 stellig" 1 "${PERM}jobs:\n  a:\n    steps:\n      - uses: actions/checkout@%s # v7.0.1\n" "$SHA39"
# 5. pull_request_target -> 1
fall "pull_request_target" 1 "on: pull_request_target\n${PERM}jobs:\n  a:\n    steps:\n      - run: echo hi\n"
# 6. ohne top-level permissions -> 1
fall "ohne permissions" 1 "jobs:\n  a:\n    steps:\n      - run: echo hi\n"
# 7. permissions: write-all -> 1
fall "write-all" 1 "permissions: write-all\njobs:\n  a:\n    steps:\n      - run: echo hi\n"
# 8. lokale Action + docker mit sha256 -> 0
fall "lokal und docker@sha256" 0 "${PERM}jobs:\n  a:\n    steps:\n      - uses: ./.github/actions/x\n      - uses: docker://alpine@sha256:%s\n" "$SHA64"
# 9. docker mit Tag -> 1
fall "docker:tag" 1 "${PERM}jobs:\n  a:\n    steps:\n      - uses: docker://alpine:3.20\n"
# 10. auskommentierte Zeile -> 0
fall "auskommentiert" 0 "${PERM}jobs:\n  a:\n    steps:\n      # uses: foo/bar@v1\n      - run: echo hi\n"

# 11. leeres Verzeichnis -> 2
d="$(mktemp -d)"; TMPDIRS+=("$d")
set +e
bash "$GUARD" "$d" > /dev/null 2>&1
rc=$?
set -e
if [ "$rc" -ne 2 ]; then
  echo "FAIL: leeres Verzeichnis (erwartet Exit 2, war $rc)"
  fehler=$((fehler + 1))
else
  echo "ok:   leeres Verzeichnis"
fi

if [ "$fehler" -gt 0 ]; then
  echo "FAIL: $fehler Fehler in test_check_actions_pinned.sh"
  exit 1
fi
echo "PASS: test_check_actions_pinned.sh"
