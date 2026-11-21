#!/usr/bin/env bash
#
# Gegenproben fuer scripts/check-secrets.sh (Phase 16, PUB-01).
# Das Fake-Token wird zur Laufzeit zusammengesetzt; im Testfile steht kein Muster.
# Ohne Docker und ohne GITLEAKS_BIN: bei CI=true Fehler, sonst werden die
# Docker-Faelle als UEBERSPRUNGEN gemeldet (z. B. im Linux-Probelauf-Container).
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUARD="$ROOT/scripts/check-secrets.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fehler=0
ok() { echo "OK: $1"; }
fail() { echo "FAIL: $1"; fehler=$((fehler + 1)); }

# Gegenprobe Digest: braucht weder Docker noch Binary
cat > "$TMP/ohne-digest.env" << 'ENV'
GITLEAKS_IMAGE=ghcr.io/gitleaks/gitleaks:v8.30.1
ENV
mkdir -p "$TMP/leer"
rc=0
SCAN_TOOLS_ENV="$TMP/ohne-digest.env" bash "$GUARD" dir "$TMP/leer" > "$TMP/out-digest.txt" 2>&1 || rc=$?
if [ "$rc" -eq 2 ] && grep -q "sha256" "$TMP/out-digest.txt"; then
  ok "Image ohne Digest wird verweigert (Exit 2)"
else
  fail "Image ohne Digest: Exit $rc statt 2"
fi

# Docker-Faelle
if [ -z "${GITLEAKS_BIN:-}" ] && ! docker version > /dev/null 2>&1; then
  if [ "${CI:-}" = "true" ]; then
    echo "FAIL: kein Docker in CI"
    exit 1
  fi
  echo "UEBERSPRUNGEN (kein Docker): Verzeichnis- und Historien-Faelle"
  [ "$fehler" -eq 0 ] && exit 0 || exit 1
fi

PRAEFIX="gh""p_"
WERT="$(head -c 600 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 36)"
FAKE="${PRAEFIX}${WERT}"

lauf() { # lauf <name> <erwarteter-exit> <modus> <pfad>
  local name="$1" erwartet="$2" modus="$3" pfad="$4" r=0
  bash "$GUARD" "$modus" "$pfad" > "$TMP/out.txt" 2>&1 || r=$?
  if [ "$r" -ne "$erwartet" ]; then
    fail "$name (erwartet Exit $erwartet, war $r)"
    sed 's/^/    /' "$TMP/out.txt"
  elif grep -qF "$WERT" "$TMP/out.txt"; then
    fail "$name: Ausgabe enthaelt den Token-Wert"
  else
    ok "$name"
  fi
}

mkdir -p "$TMP/sauber" "$TMP/mitfund"
echo "nichts zu sehen" > "$TMP/sauber/a.txt"
lauf "sauberes Verzeichnis" 0 dir "$TMP/sauber"

echo "token = \"$FAKE\"" > "$TMP/mitfund/b.txt"
lauf "Verzeichnis mit Fake-Token" 1 dir "$TMP/mitfund"

REPO="$TMP/repo"
git init -q "$REPO"
git -C "$REPO" config user.email "t@example.invalid"
git -C "$REPO" config user.name "test"
git -C "$REPO" config core.autocrlf false
echo "token = \"$FAKE\"" > "$REPO/f.txt"
git -C "$REPO" add f.txt
git -C "$REPO" commit -q -m "mit"
git -C "$REPO" rm -q f.txt
git -C "$REPO" commit -q -m "ohne"
lauf "Arbeitsbaum nach Loeschung sauber" 0 dir "$REPO"
lauf "Historie findet geloeschtes Token" 1 history "$REPO"

# Baseline (nur privat, 16-07): hingenommener Altfund ist still, ein neuer Fund bleibt rot,
# und mit CI_SCAN_HISTORY=1 (oeffentliches Repo) wird die Baseline ignoriert.
mkdir -p "$TMP/basis"
echo "token = \"$FAKE\"" > "$TMP/basis/alt.txt"
SECRETS_REPORT_COPY="$TMP/basis-bericht.json" bash "$GUARD" dir "$TMP/basis" > "$TMP/out-b.txt" 2>&1 || true
cp "$TMP/basis-bericht.json" "$TMP/basis/.gitleaks-baseline.private.json"
lauf "Baseline nimmt genannten Altfund hin" 0 dir "$TMP/basis"
CI_SCAN_HISTORY=1 lauf "Baseline wird mit CI_SCAN_HISTORY=1 ignoriert" 1 dir "$TMP/basis"
NEU_WERT="$(head -c 600 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 36)"
echo "token2 = \"${PRAEFIX}${NEU_WERT}\"" > "$TMP/basis/neu.txt"
lauf "neuer Fund trotz Baseline rot" 1 dir "$TMP/basis"

[ "$fehler" -eq 0 ] || { echo "$fehler Fehler"; exit 1; }
echo "Alle Faelle gruen."
