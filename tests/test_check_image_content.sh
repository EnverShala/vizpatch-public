#!/usr/bin/env bash
#
# Gegenproben fuer scripts/check-image-content.sh im --dir-Modus (Phase 16, PUB-03).
# Laeuft ohne Docker. Token-Werte werden zur Laufzeit zusammengesetzt.
#
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUARD="$ROOT/scripts/check-image-content.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fehler=0
ok() { echo "OK: $1"; }
fail() { echo "FAIL: $1"; fehler=$((fehler + 1)); }

WERT="$(head -c 600 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 36)"
GHP="gh""p_${WERT}"
ANT="sk-an""t-${WERT}"

neu() { # neu <name> -> frisches Fixture mit sauberem /app
  rm -rf "$TMP/$1"
  mkdir -p "$TMP/$1/app/src"
  echo "[project]" > "$TMP/$1/app/pyproject.toml"
  echo "print('hi')" > "$TMP/$1/app/src/main.py"
}

lauf() { # lauf <name> <erwarteter-exit> <fixture> [erwarteter-text]
  local name="$1" erwartet="$2" fix="$3" text="${4:-}" r=0
  bash "$GUARD" --dir "$TMP/$fix" > "$TMP/out.txt" 2>&1 || r=$?
  if [ "$r" -ne "$erwartet" ]; then
    fail "$name (erwartet Exit $erwartet, war $r)"
    sed 's/^/    /' "$TMP/out.txt"
  elif [ -n "$text" ] && ! grep -qF "$text" "$TMP/out.txt"; then
    fail "$name: Pfad '$text' fehlt in der Ausgabe"
  elif grep -qF "$WERT" "$TMP/out.txt"; then
    fail "$name: Ausgabe enthaelt den Token-Wert"
  else
    ok "$name"
  fi
}

neu sauber
mkdir -p "$TMP/sauber/etc/ssl/certs"
echo "zertifikat" > "$TMP/sauber/etc/ssl/certs/ca.pem"
echo "VIZPATCH_DIST_TOKEN=" > "$TMP/sauber/app/leer.example"
lauf "sauberes Fixture (inkl. System-.pem, leerer Token-Eintrag)" 0 sauber

neu f1; echo "x" > "$TMP/f1/app/.env"
lauf ".env" 1 f1 "app/.env"

neu f2; mkdir -p "$TMP/f2/x"; echo "x" > "$TMP/f2/x/k.pfx"
lauf "Schluesseldatei pfx" 1 f2 "x/k.pfx"

neu f3; echo "x" > "$TMP/f3/app/deployment-paket-v9.9.9.zip"
lauf "Kundenpaket-Archiv" 1 f3 "deployment-paket-v9.9.9.zip"

neu f4; echo "x" > "$TMP/f4/app/kunde-env.example"
lauf "kunde-env" 1 f4 "kunde-env.example"

neu f5; mkdir -p "$TMP/f5/app/tests"
lauf "Verzeichnis app/tests" 1 f5 "app/tests"

neu f6; mkdir -p "$TMP/f6/app/deployment"
lauf "Verzeichnis app/deployment" 1 f6 "app/deployment"

neu f7; echo "VIZPATCH_DIST_TOKEN=abc123" > "$TMP/f7/app/src/cfg.txt"
lauf "Dist-Token mit Wert" 1 f7 "app/src/cfg.txt"

neu f8; echo "t = $GHP" > "$TMP/f8/app/src/t.py"
lauf "GitHub-Token-Muster" 1 f8 "app/src/t.py"

neu f9; echo "k = $ANT" > "$TMP/f9/app/src/k.py"
lauf "API-Key-Muster" 1 f9 "app/src/k.py"

neu f10; echo "x" > "$TMP/f10/app/.wh..env"
lauf "Whiteout einer .env wird erkannt" 1 f10 ".wh..env"

neu f11; echo "x" > "$TMP/f11/app/context.md"
lauf "context.md" 1 f11 "app/context.md"

[ "$fehler" -eq 0 ] || { echo "$fehler Fehler"; exit 1; }
echo "Alle Faelle gruen."
