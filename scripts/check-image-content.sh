#!/usr/bin/env bash
#
# check-image-content.sh — Inhaltspruefung fuer Container-Images (Phase 16, PUB-03).
#
# Zweck
# -----
# Ein Image darf weder Kundenpaket-Dateien noch .env, Schluesseldateien oder
# Token-Muster tragen. Die Root-.dockerignore greift fuer die Builds nicht (die
# Build-Kontexte sind agent/ und webui/); geschuetzt wird ueber die expliziten
# COPY-Zeilen der Dockerfiles. Dieses Skript prueft das FERTIGE Ergebnis, und
# zwar jede Schicht einzeln: eine in einer spaeteren Schicht geloeschte Datei
# steckt in der frueheren weiter drin.
#
# Aufruf
# ------
#   bash scripts/check-image-content.sh --dir <pfad>    # Dateisystem (Schicht oder Fixture)
#   bash scripts/check-image-content.sh --image <ref>   # alle Schichten + History + Config
#
# Exit: 0 sauber / 1 Fund / 2 Werkzeug- oder Nutzungsfehler.
# Ausgabe: je Befund Regel + Pfad (+ Zeile), nie der Treffer selbst.
#
set -uo pipefail

usage() {
  echo "Nutzung: bash scripts/check-image-content.sh --dir <pfad> | --image <ref>" >&2
  exit 2
}

# Inhaltsregeln (ERE). Zeichenklassen statt Literalen, damit dieses Skript
# selbst keinen Fund in den Token-Waechtern erzeugt.
RE_DIST='VIZPATCH_DIST_TOKEN=[^[:space:]]'
RE_GHP='gh[p]_[A-Za-z0-9]{36}'
RE_PAT='github_pa[t]_[A-Za-z0-9_]{20,}'
RE_ANT='sk-an[t]-[A-Za-z0-9_-]{20,}'
RE_ALLE="$RE_DIST|$RE_GHP|$RE_PAT|$RE_ANT"

# pruefe_dir <pfad>  -> Anzahl Befunde auf stdout-Zaehler ueber globale Variable
FUNDE=0
melde() { # melde <regel> <pfad>
  echo "FUND [$1]: $2"
  FUNDE=$((FUNDE + 1))
}

pruefe_dir() {
  local root="$1" rel base name
  [ -d "$root" ] || { echo "FEHLER: kein Verzeichnis: $root" >&2; exit 2; }

  # Namensregeln ueber den ganzen Baum
  while IFS= read -r -d '' pfad; do
    rel="${pfad#"$root"/}"
    base="${rel##*/}"
    name="$base"
    case "$name" in
      .wh..wh..*) continue ;;
      .wh.*) name="${name#.wh.}" ;;
    esac
    case "$name" in
      .env | .secret_key | .vizpatch-dist-token) melde "datei-name" "$rel" ;;
      *.pfx | *.p12 | *.snk | *.jks | *.keystore) melde "schluesseldatei" "$rel" ;;
      deployment-paket*) melde "kundenpaket" "$rel" ;;
      kunde-env*) melde "kunden-env" "$rel" ;;
      context.md) melde "firmenwissen" "$rel" ;;
      *.eml) melde "mail-datei" "$rel" ;;
    esac
  done < <(find "$root" -mindepth 1 \( -type f -o -type l \) -print0 2> /dev/null)

  # Verbotene Verzeichnisse
  local d
  for d in app/tests app/deployment app/.git app/.planning; do
    if [ -e "$root/$d" ]; then
      melde "verzeichnis" "$d"
    fi
  done

  # Inhaltsregeln nur unter app/, home/, root/
  local teil
  for teil in app home root; do
    [ -d "$root/$teil" ] || continue
    while IFS= read -r treffer; do
      [ -n "$treffer" ] || continue
      # treffer = pfad:zeile  (grep -n ohne Inhalt, siehe cut unten)
      melde "token-muster" "${treffer#"$root"/}"
    done < <(grep -rInE "$RE_ALLE" "$root/$teil" 2> /dev/null | cut -d: -f1,2)
  done
}

# Text (History/Config) gegen die Inhaltsregeln, ohne den Treffer auszugeben
pruefe_text() { # pruefe_text <quelle> <datei>
  if grep -qE "$RE_ALLE" "$2" 2> /dev/null; then
    melde "token-muster" "$1"
  fi
}

[ $# -eq 2 ] || usage

case "$1" in
  --dir)
    pruefe_dir "$2"
    ;;
  --image)
    REF="$2"
    command -v docker > /dev/null 2>&1 || { echo "FEHLER: docker nicht gefunden." >&2; exit 2; }
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    docker save "$REF" -o "$TMP/image.tar" || { echo "FEHLER: docker save $REF" >&2; exit 2; }
    mkdir -p "$TMP/save"
    tar -xf "$TMP/image.tar" -C "$TMP/save" 2> /dev/null || true
    rm -f "$TMP/image.tar"

    n=0
    while IFS= read -r -d '' kandidat; do
      # Nur Dateien, die sich als tar oder tar.gz lesen lassen, sind Schichten
      # (Configs und Indizes im OCI-Layout fallen hier heraus).
      tar -tf "$kandidat" > /dev/null 2>&1 || continue
      n=$((n + 1))
      mkdir -p "$TMP/schicht-$n"
      tar -xf "$kandidat" -C "$TMP/schicht-$n" 2> /dev/null || true
      echo "== Schicht $n (${kandidat#"$TMP/save"/})"
      pruefe_dir "$TMP/schicht-$n"
      rm -rf "$TMP/schicht-$n"
    done < <(find "$TMP/save" -type f ! -name '*.json' ! -name 'oci-layout' ! -name 'repositories' -print0)

    if [ "$n" -eq 0 ]; then
      echo "FEHLER: keine Schicht im Image gefunden." >&2
      exit 2
    fi

    docker history --no-trunc "$REF" > "$TMP/history.txt" 2> /dev/null || { echo "FEHLER: docker history" >&2; exit 2; }
    docker image inspect "$REF" > "$TMP/inspect.json" 2> /dev/null || { echo "FEHLER: docker image inspect" >&2; exit 2; }
    pruefe_text "docker history" "$TMP/history.txt"
    pruefe_text "image config" "$TMP/inspect.json"
    echo "Schichten geprueft: $n"
    ;;
  *)
    usage
    ;;
esac

if [ "$FUNDE" -gt 0 ]; then
  echo "IMAGE-INHALT: $FUNDE Befund(e)." >&2
  exit 1
fi
echo "IMAGE-INHALT: OK"
exit 0
