#!/usr/bin/env bash
#
# check-actions-pinned.sh — Lieferketten-Wächter für GitHub-Workflows
# (Phase 15, Plan 15-02, CI-03/CI-05).
#
# Zweck
# -----
# Jede Action in .github/workflows/ muss per vollständiger 40-stelliger
# Commit-SHA mit Versionskommentar gepinnt sein (Trivy-Action-Vorfall März
# 2026: bewegliche Tags wurden umgebogen). Zusätzlich: top-level
# `permissions:` ist Pflicht, `write-all` und `pull_request_target` sind
# verboten.
#
# Erlaubt
#   uses: owner/repo[/pfad]@<40 Hex klein> # v1.2.3   (oder "# 1.2.3")
#   uses: ./lokaler/pfad
#   uses: docker://image@sha256:<64 Hex>
# Alles andere in `uses:` ist ein Verstoß. Kommentarzeilen werden ignoriert.
#
# Exit-Code: 0 = sauber, 1 = mindestens ein Verstoß, 2 = nichts zu prüfen.
#
# Nutzung:
#   bash scripts/check-actions-pinned.sh [verzeichnis]   # Default .github/workflows
#
# Gegenprobe (dokumentiertes Rot-Verhalten):
#   d=$(mktemp -d); printf 'permissions:\n  contents: read\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@v7\n' > "$d/x.yml"
#   bash scripts/check-actions-pinned.sh "$d"   # -> Exit 1
#   rm -rf "$d"
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCAN_DIR="${1:-$ROOT/.github/workflows}"

if [ ! -d "$SCAN_DIR" ]; then
  echo "FEHLER: Scan-Verzeichnis nicht gefunden: $SCAN_DIR" >&2
  exit 2
fi

shopt -s nullglob
FILES=("$SCAN_DIR"/*.yml "$SCAN_DIR"/*.yaml)
shopt -u nullglob

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "FEHLER: keine *.yml/*.yaml in $SCAN_DIR" >&2
  exit 2
fi

RE_SHA='^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(/[A-Za-z0-9._/-]+)?@[0-9a-f]{40}$'
RE_DOCKER='^docker://[^[:space:]]+@sha256:[0-9a-f]{64}$'
RE_COMMENT='^[[:space:]]*#[[:space:]]*v?[0-9]'

verstoesse=0
uses_n=0

melde() {
  echo "$1:$2: $3"
  verstoesse=$((verstoesse + 1))
}

for f in "${FILES[@]}"; do
  name="$(basename "$f")"
  hat_perm=0
  nr=0
  while IFS= read -r zeile || [ -n "$zeile" ]; do
    nr=$((nr + 1))
    zeile="${zeile%$'\r'}"
    # Kommentarzeilen verwerfen
    if [[ "$zeile" =~ ^[[:space:]]*# ]]; then
      continue
    fi

    if [[ "$zeile" =~ ^permissions:[[:space:]]*(.*)$ ]]; then
      hat_perm=1
    fi
    if [[ "$zeile" =~ ^[[:space:]]*permissions:[[:space:]]*write-all ]]; then
      melde "$name" "$nr" "permissions: write-all ist verboten"
    fi
    if [[ "$zeile" == *pull_request_target* ]]; then
      melde "$name" "$nr" "pull_request_target ist verboten"
    fi

    if [[ "$zeile" =~ ^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*(.*)$ ]]; then
      uses_n=$((uses_n + 1))
      rest="${BASH_REMATCH[2]}"
      # Referenz und Restkommentar trennen
      kommentar=""
      if [[ "$rest" == *"#"* ]]; then
        kommentar="#${rest#*#}"
        rest="${rest%%#*}"
      fi
      # Leerraum und Anführungszeichen entfernen
      ref="$(printf '%s' "$rest" | sed -E "s/^[[:space:]]+//; s/[[:space:]]+\$//; s/^[\"']//; s/[\"']\$//")"
      if [[ "$ref" == ./* ]]; then
        continue
      elif [[ "$ref" =~ $RE_DOCKER ]]; then
        continue
      elif [[ "$ref" =~ $RE_SHA ]]; then
        if [[ ! "$kommentar" =~ $RE_COMMENT ]]; then
          melde "$name" "$nr" "SHA-Pin ohne Versionskommentar (# v...): $ref"
        fi
      else
        melde "$name" "$nr" "Action nicht per 40-stelliger Commit-SHA gepinnt: $ref"
      fi
    fi
  done < "$f"

  if [ "$hat_perm" -eq 0 ]; then
    melde "$name" 1 "kein top-level 'permissions:'"
  fi
done

if [ "$verstoesse" -gt 0 ]; then
  echo "FEHLER: $verstoesse Verstoß/Verstöße" >&2
  exit 1
fi

echo "OK: ${#FILES[@]} Workflow-Dateien, $uses_n uses-Zeilen geprüft"
exit 0
