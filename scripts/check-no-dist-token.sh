#!/usr/bin/env bash
#
# check-no-dist-token.sh — Waechter gegen versehentlich eingecheckte
# GitHub-Token (260728-m8g, Stufe 2, D-2/T-m8g-05).
#
# Zweck
# -----
# Der Dist-Repo-Read-only-Token (`VIZPATCH_DIST_TOKEN`) wird beim Paketbau
# NUR in `dist/…/.env` geschrieben (gitignored) — NIE in eine eingecheckte
# Datei. Dieses Skript scannt ausschliesslich GIT-GETRACKTE Dateien
# (`git ls-files`) nach GitHub-Token-Mustern und nach einem nicht-leeren
# `VIZPATCH_DIST_TOKEN=`-Eintrag. Es folgt demselben Muster wie
# `check-addin-no-autosend.sh`: konkrete, eindeutige Muster statt blinder
# Substrings, damit es nicht auf sich selbst (dieses Skript enthaelt die
# Muster als Text) anschlaegt.
#
# Verbotene Muster
# ----------------
#   ghp_[A-Za-z0-9]{30,}          Classic Personal Access Token
#   gho_[A-Za-z0-9]{30,}          OAuth Access Token
#   ghs_[A-Za-z0-9]{30,}          Server-to-Server Token (GitHub App)
#   github_pat_[A-Za-z0-9_]{30,}  Fine-grained PAT
#   VIZPATCH_DIST_TOKEN=<nicht-leer>   direkt eingetragener Token-Wert
#
# `*.example`-Dateien duerfen `VIZPATCH_DIST_TOKEN=` enthalten, aber NUR mit
# leerem Wert (das ist die dokumentierte Vorlage fuer den Kunden) — ein
# nicht-leerer Wert in einer `.example`-Datei ist trotzdem ein Verstoss.
#
# Selbstausnahme: dieses Skript selbst wird NICHT gescannt (es enthaelt die
# Muster als Text, siehe oben) — die Musterzeilen werden im Regex weiter
# unten durch Zeichenklassen aufgebrochen, damit ein Blick in dieses Skript
# selbst keinen Fund erzeugt.
#
# Exit-Code: 0 = sauber, 1 = mindestens ein Fund (Fundstellen ausgegeben).
#
# Nutzung:
#   bash scripts/check-no-dist-token.sh          # scannt das vizpatch-Repo
#   bash scripts/check-no-dist-token.sh <dir>    # scannt <dir> (Gegenprobe)
#
# Gegenprobe (dokumentiertes Rot-Verhalten):
#   d=$(mktemp -d); cd "$d" && git init -q
#   printf 'VIZPATCH_DIST_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz012345\n' > tmp.env
#   git add -N tmp.env
#   bash /pfad/zu/scripts/check-no-dist-token.sh "$d"   # -> Exit 1
#
set -euo pipefail

DEFAULT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${1:-$DEFAULT_ROOT}"
cd "$ROOT"

if ! command -v git >/dev/null 2>&1; then
  echo "FEHLER: git nicht gefunden." >&2
  exit 2
fi

mapfile -t TRACKED_FILES < <(git ls-files)

SELF_PATH="scripts/check-no-dist-token.sh"

FINDINGS=""
CHECKED=0

# Token-Muster (Praefix + Mindestlaenge 30). Dieses Skript scannt sich selbst
# NICHT (siehe Selbstausnahme oben) — daher matcht das Muster hier nicht auf
# die eigene Kopf-Dokumentation.
GH_TOKEN_PATTERN='gh(p|o|s)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}'

for f in "${TRACKED_FILES[@]}"; do
  [ "$f" = "$SELF_PATH" ] && continue
  [ -f "$f" ] || continue
  # Binaerdateien ueberspringen (z. B. Bilder) — grep -I macht das robust.
  CHECKED=$((CHECKED + 1))

  hits="$(grep -InE "$GH_TOKEN_PATTERN" "$f" 2>/dev/null || true)"
  if [ -n "$hits" ]; then
    FINDINGS+="${f}: Token-Muster gefunden:"$'\n'"$hits"$'\n'
  fi

  # VIZPATCH_DIST_TOKEN=<nicht-leer> — auch in *.example-Dateien verboten.
  dist_hits="$(grep -InE '^[[:space:]]*VIZPATCH_DIST_TOKEN=[^[:space:]]' "$f" 2>/dev/null || true)"
  if [ -n "$dist_hits" ]; then
    FINDINGS+="${f}: nicht-leerer VIZPATCH_DIST_TOKEN-Eintrag:"$'\n'"$dist_hits"$'\n'
  fi
done

if [ -n "$FINDINGS" ]; then
  echo "TOKEN-WÄCHTER: VERSTOSS gefunden" >&2
  printf '%s\n' "$FINDINGS" >&2
  echo "-> Der Dist-Repo-Token darf NIE in einer eingecheckten Datei stehen." >&2
  echo "   Nur 'dist/…/.env' (gitignored) darf ihn zur Build-Zeit tragen." >&2
  exit 1
fi

echo "TOKEN-WÄCHTER: OK — ${CHECKED} getrackte Dateien geprüft, kein Dist-Repo-Token gefunden."
exit 0
