#!/usr/bin/env bash
# Waechter fuer deployment/addin-publish-extras/INSTALLIEREN.cmd.
#
# Warum es diesen Test gibt — drei Befunde vom 2026-08-26, alle im scharfen
# Test des Add-in-Selbstupdates an der Testinstanz belegt:
#
# 1. setup.exe ist nur ein BOOTSTRAPPER. Er endete im Protokoll nach 1,25 s mit
#    Rueckgabewert 0 und uebergab an den Office-Anpassungs-Installer
#    (VSTOInstaller.exe). Wer nach setup.exe zurueckkehrt, meldet "fertig",
#    waehrend die Installation gerade erst anfaengt.
# 2. Der Helfer des Selbstupdates raeumte danach das Staging ab und loeschte
#    dem echten Installer die Dateien weg:
#      'file:///.../addin-update/extracted/addin-publish/VizpatchAddin.vsto'
#      konnte nicht gedownloadet werden.
# 3. Outlook merkt sich die .vsto AN DEM ORT, aus dem installiert wurde. Nach
#    dem ersten erfolgreichen Selbstupdate stand dort das Staging-Verzeichnis —
#    also ein Pfad, den der naechste Update-Lauf planmaessig loescht.
#
# Dieses Skript ist der einzige Hebel, der BESTEHENDE Kundeninstallationen
# erreicht: es kommt aus dem Server-Bundle, waehrend der aufraeumende Helfer aus
# dem schon installierten (alten) Add-in stammt. Deshalb stehen die Riegel hier
# und nicht nur im C#-Code.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CMD="${ROOT_DIR}/deployment/addin-publish-extras/INSTALLIEREN.cmd"

[ -f "$CMD" ] || { echo "FAIL: $CMD fehlt"; exit 1; }

fehler=0
pruefe() {  # pruefe <beschreibung> <erwartet: 0=vorhanden> <grep-args...>
  local text="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "==> OK: $text"
  else
    echo "FAIL: $text"
    fehler=1
  fi
}

# --- Dauerhafter Installationsort (Befund 3) --------------------------------
pruefe "Ziel ist %LOCALAPPDATA%\Vizpatch\addin-installiert" \
  grep -qF 'LOCALAPPDATA%\Vizpatch\addin-installiert' "$CMD"
pruefe "Paket wird dorthin kopiert (robocopy)" \
  grep -qi 'robocopy' "$CMD"
pruefe "Setup laeuft AUS dem dauerhaften Ort, mit /wait" \
  grep -qF 'start /wait "" "%ZIEL%\setup.exe"' "$CMD"
pruefe "kein Setup-Start aus dem Skriptverzeichnis (%~dp0setup.exe)" \
  bash -c "! grep -qF '%~dp0setup.exe' '$CMD'"

# --- Robocopy-Exit-Codes: 0-7 sind Erfolg ----------------------------------
pruefe "robocopy wird ab errorlevel 8 als Fehler gewertet" \
  grep -qF 'if errorlevel 8' "$CMD"

# --- Warten auf den echten Installer (Befund 1 + 2) ------------------------
pruefe "es wird auf VSTOInstaller.exe gewartet" \
  grep -qF 'IMAGENAME eq VSTOInstaller.exe' "$CMD"
anzahl_deckel=$(grep -cE 'if %(ERSCHEINT|LAEUFT)% GEQ [0-9]+ goto fertig' "$CMD" || true)
pruefe "beide Warteschleifen haben einen Deckel (gefunden: $anzahl_deckel)" \
  test "$anzahl_deckel" -ge 2

# --- Deinstallation bei Pfadwechsel (Befund 2026-08-27) -------------------
# ClickOnce lehnt ein Update ab, dessen Quellpfad sich geaendert hat. Genau das
# trifft JEDE bestehende Kundeninstallation beim ersten Selbstupdate.
pruefe "vorinstallation.ps1 wird aufgerufen"   grep -qF 'vorinstallation.ps1' "$CMD"
pruefe "Abbruch, wenn die Deinstallation misslingt (alte Fassung bleibt)"   bash -c "grep -A3 -F 'vorinstallation.ps1' '$CMD' | grep -qF 'if errorlevel 1'"
pruefe "vorinstallation.ps1 liegt im Paket-Extras-Ordner"   test -f "${ROOT_DIR}/deployment/addin-publish-extras/vorinstallation.ps1"

# --- Warten ohne timeout (Befund 2026-08-27) ------------------------------
# `timeout` bricht mit "Die Eingabeumleitung wird nicht unterstuetzt" ab, sobald
# stdin umgeleitet ist — die Warteschleife lief dann als Busy-Loop durch und der
# Deckel war in Sekunden erreicht.
pruefe "kein 'timeout' in den Warteschleifen"   bash -c "! grep -qE '^[[:space:]]*timeout ' '$CMD'"
pruefe "gewartet wird mit ping"   grep -qF 'ping -n' "$CMD"

# --- Zertifikat vor dem Setup, Abbruch bei Fehler -------------------------
pruefe "vertrauen-einrichten.ps1 ist eingebunden" \
  grep -qF 'vertrauen-einrichten.ps1' "$CMD"
zeile_cert=$(grep -nF 'vertrauen-einrichten.ps1' "$CMD" | tail -1 | cut -d: -f1)
zeile_setup=$(grep -nF 'start /wait "" "%ZIEL%\setup.exe"' "$CMD" | head -1 | cut -d: -f1)
pruefe "Zertifikat wird VOR dem Setup eingerichtet ($zeile_cert < $zeile_setup)" \
  test "$zeile_cert" -lt "$zeile_setup"
pruefe "Fehler beim Zertifikat bricht ab (exit /b 1)" \
  grep -qF 'exit /b 1' "$CMD"

if [ "$fehler" -ne 0 ]; then
  echo "INSTALLIEREN.cmd-Waechter: FEHLGESCHLAGEN"
  exit 1
fi
echo "INSTALLIEREN.cmd-Waechter: alle Pruefungen gruen"
