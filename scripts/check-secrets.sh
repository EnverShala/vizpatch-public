#!/usr/bin/env bash
#
# check-secrets.sh — einziger Aufrufweg fuer gitleaks (Phase 16, PUB-01).
#
# Aufruf:  check-secrets.sh dir <pfad>       scannt Dateien eines Verzeichnisses
#          check-secrets.sh history <repo>   scannt alle Refs eines Git-Repos
# Exit:    0 sauber, 1 Fund, 2 Werkzeug-/Aufruffehler
#
# Werkzeug: das per Digest gepinnte Image aus scripts/scan-tools.env (GITLEAKS_IMAGE),
# ohne Netz und mit Read-only-Mount. Ersatzweise ein lokales Binary (GITLEAKS_BIN).
# Ein Image ohne @sha256-Digest wird verweigert (Exit 2, bevor Docker laeuft).
# Die Ausgabe nennt je Fund RuleID, Datei, Zeile, Commit, Fingerprint — nie den Wert.
# Konfiguration: <pfad>/.gitleaks.toml, sonst die .gitleaks.toml dieses Repos.
# SCAN_TOOLS_ENV ueberschreibt den Ort der Werkzeugdatei (fuer Tests).
#
# Baseline (nur privat, 16-07): liegt <pfad>/.gitleaks-baseline.private.json und ist
# CI_SCAN_HISTORY nicht 1, gelten die dort per Fingerprint genannten Altfunde im
# Modus `dir` als hingenommen; jeder andere Fund bleibt rot. Die Datei ist vom
# Public-Export ausgeschlossen, im oeffentlichen Repo (CI_SCAN_HISTORY=1) gibt es
# sie nicht und sie wuerde ignoriert. Fingerprints tragen den Container-Pfad /scan,
# gelten also fuer den Docker-Weg (nicht fuer GITLEAKS_BIN).

set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODUS="${1:-}"
ZIEL="${2:-}"

if { [ "$MODUS" != "dir" ] && [ "$MODUS" != "history" ]; } || [ -z "$ZIEL" ]; then
  echo "Aufruf: check-secrets.sh dir <pfad> | history <repo-pfad>" >&2
  exit 2
fi
[ -d "$ZIEL" ] || { echo "FEHLER: $ZIEL ist kein Verzeichnis." >&2; exit 2; }
ZIEL="$(cd "$ZIEL" && pwd)"

ENVDATEI="${SCAN_TOOLS_ENV:-$HIER/scan-tools.env}"
[ -f "$ENVDATEI" ] || { echo "FEHLER: $ENVDATEI fehlt." >&2; exit 2; }
GITLEAKS_IMAGE="$(sed -n 's/^GITLEAKS_IMAGE=//p' "$ENVDATEI" | head -n 1)"
case "$GITLEAKS_IMAGE" in
  *@sha256:*) ;;
  *) echo "FEHLER: GITLEAKS_IMAGE in $ENVDATEI hat keinen @sha256-Digest — verweigert." >&2; exit 2 ;;
esac

if [ -f "$ZIEL/.gitleaks.toml" ]; then CFG="$ZIEL/.gitleaks.toml"; else CFG="$HIER/../.gitleaks.toml"; fi
[ -f "$CFG" ] || { echo "FEHLER: keine .gitleaks.toml gefunden." >&2; exit 2; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp "$CFG" "$WORK/gitleaks.toml"
: > "$WORK/report.json"
chmod 666 "$WORK/report.json" 2> /dev/null || true

if [ "$MODUS" = "dir" ]; then SUB=(dir /scan); else SUB=(git --log-opts="--all" /scan); fi
GL=(--config /work/gitleaks.toml --redact --no-banner --exit-code 7 --report-format json --report-path /work/report.json)

BASELINE="$ZIEL/.gitleaks-baseline.private.json"
if [ "$MODUS" = "dir" ] && [ "${CI_SCAN_HISTORY:-0}" != "1" ] && [ -z "${GITLEAKS_BIN:-}" ] && [ -f "$BASELINE" ]; then
  cp "$BASELINE" "$WORK/baseline.json"
  GL+=(--baseline-path /work/baseline.json)
  echo "Hinweis: Baseline .gitleaks-baseline.private.json aktiv (nur privat)."
fi

rc=0
if [ -n "${GITLEAKS_BIN:-}" ]; then
  # Binary-Weg: Pfade direkt (kein Container)
  if [ "$MODUS" = "dir" ]; then SUB=(dir "$ZIEL"); else SUB=(git --log-opts="--all" "$ZIEL"); fi
  "$GITLEAKS_BIN" "${SUB[@]}" --config "$WORK/gitleaks.toml" --redact --no-banner --exit-code 7 \
    --report-format json --report-path "$WORK/report.json" > "$WORK/log.txt" 2>&1 || rc=$?
else
  if ! docker version > /dev/null 2>&1; then
    echo "FEHLER: Docker nicht erreichbar und kein GITLEAKS_BIN gesetzt." >&2
    exit 2
  fi
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*)
      export MSYS_NO_PATHCONV=1
      ZIEL_MNT="$(cd "$ZIEL" && pwd -W)"
      WORK_MNT="$(cd "$WORK" && pwd -W)"
      ;;
    *) ZIEL_MNT="$ZIEL"; WORK_MNT="$WORK" ;;
  esac
  docker run --rm --network none \
    -v "$ZIEL_MNT:/scan:ro" -v "$WORK_MNT:/work" \
    "$GITLEAKS_IMAGE" "${SUB[@]}" "${GL[@]}" > "$WORK/log.txt" 2>&1 || rc=$?
fi

if [ "$rc" -ne 0 ] && [ "$rc" -ne 7 ]; then
  echo "FEHLER: gitleaks endete mit Exit $rc:" >&2
  sed 's/^/    /' "$WORK/log.txt" >&2
  exit 2
fi

# Test-/Baseline-Erzeugung: Bericht (redigiert) an einen Ort kopieren
if [ -n "${SECRETS_REPORT_COPY:-}" ]; then cp "$WORK/report.json" "$SECRETS_REPORT_COPY"; fi

# Zusammenfassung ohne Secret/Match
ZUSAMMENFASSUNG='
import json, sys
try:
    daten = json.load(open(sys.argv[1], encoding="utf-8")) or []
except Exception:
    daten = []
for f in daten:
    print("FUND: %s | %s:%s | commit %s | %s" % (
        f.get("RuleID"), f.get("File"), f.get("StartLine"),
        (f.get("Commit") or "-")[:10], f.get("Fingerprint")))
print("%d Fund(e)" % len(daten))
'
PY=""
for kand in python3 python; do
  if "$kand" -c "import sys" > /dev/null 2>&1; then PY="$kand"; break; fi
done
if [ -n "$PY" ]; then
  (cd "$WORK" && "$PY" -I -c "$ZUSAMMENFASSUNG" report.json)
else
  echo "Hinweis: kein Python fuer die Zusammenfassung, Exit-Code $rc gilt."
fi
[ "$rc" -eq 7 ] && exit 1
exit 0
