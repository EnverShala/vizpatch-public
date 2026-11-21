@echo off
REM Doppelklick genuegt: kopiert das Paket an einen dauerhaften Ort, entfernt
REM die Download-Sperre, installiert das Zertifikat und startet Setup.
setlocal
cd /d "%~dp0"

REM ==== 1. An einen DAUERHAFTEN Ort kopieren ==============================
REM Outlook merkt sich nach der Installation die .vsto AN DEM ORT, aus dem
REM installiert wurde (Registry: ...\Outlook\Addins\VizpatchAddin\Manifest).
REM Verschwindet der Ort, ist das Add-in nicht mehr ladbar. Genau das droht
REM zweimal: beim Selbstupdate wird aus einem Staging-Verzeichnis installiert,
REM das der Aufrufer danach aufraeumt (belegt 2026-08-26), und beim manuellen
REM Doppelklick ist es der Download-Ordner, den der Kunde irgendwann leert.
REM Deshalb wird ZUERST kopiert und danach ausschliesslich aus %ZIEL%
REM gearbeitet.
set "ZIEL=%LOCALAPPDATA%\Vizpatch\addin-installiert"
if /i "%~dp0"=="%ZIEL%\" goto installieren

if not exist "%ZIEL%" md "%ZIEL%" >nul 2>nul
REM /MIR spiegelt (alte Versionsordner verschwinden mit). Robocopy meldet
REM 0-7 als Erfolg, ab 8 echte Fehler — deshalb NICHT auf errorlevel 1 pruefen.
robocopy "%~dp0." "%ZIEL%" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo.
  echo FEHLER: Paket konnte nicht nach "%ZIEL%" kopiert werden.
  echo Setup wird NICHT gestartet.
  pause
  exit /b 1
)
if not exist "%ZIEL%\setup.exe" (
  echo.
  echo FEHLER: "%ZIEL%\setup.exe" fehlt nach dem Kopieren.
  pause
  exit /b 1
)

:installieren
cd /d "%ZIEL%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ZIEL%\vertrauen-einrichten.ps1"
if errorlevel 1 (
  echo.
  echo FEHLER beim Einrichten des Zertifikats - Setup wird NICHT gestartet.
  pause
  exit /b 1
)

REM ==== 2. Bestehende Installation von einem ANDEREN Ort entfernen =======
REM ClickOnce bindet eine Installation an ihren Herkunftspfad; ein Update aus
REM einem anderen Pfad wird abgelehnt ("... von diesem Speicherort nicht
REM aktualisiert werden kann", belegt 2026-08-27). Das Skript entfernt die
REM alte Fassung NUR in genau diesem Fall und bricht ab, wenn das misslingt —
REM dann bleibt die alte Fassung lauffaehig.
powershell -NoProfile -ExecutionPolicy Bypass -File "%ZIEL%\vorinstallation.ps1" -NeueVsto "%ZIEL%\VizpatchAddin.vsto"
if errorlevel 1 (
  echo.
  echo Update abgebrochen - die bisherige Fassung bleibt installiert.
  pause
  exit /b 1
)
echo.
echo Starte Setup...
REM /wait wartet nur auf den BOOTSTRAPPER. Gemessen am 2026-08-26: setup.exe
REM endet nach rund 1,25 s mit Rueckgabewert 0 und uebergibt an den
REM Office-Anpassungs-Installer (VSTOInstaller.exe), der danach weiterlaeuft.
start /wait "" "%ZIEL%\setup.exe"

REM ==== 3. Auf den ECHTEN Installer warten ================================
REM Wer hier zurueckkehrt, waehrend VSTOInstaller.exe noch laeuft, riskiert,
REM dass ein aufraeumender Aufrufer ihm die Dateien wegloescht — genau so
REM entstand "VizpatchAddin.vsto konnte nicht gedownloadet werden". Diese
REM Schleife ist der einzige Hebel, der BESTEHENDE Installationen erreicht:
REM dieses Skript kommt aus dem Server-Bundle, der aufraeumende Helfer aus dem
REM schon installierten Add-in.
REM Erst bis zu 30 s auf das Erscheinen warten (Bootstrapper-Uebergabe),
REM danach bis zu 5 min auf das Ende. Beide Schleifen haben einen Deckel:
REM erscheint der Prozess nie oder haengt er, laeuft das Skript weiter statt
REM ewig zu warten.
set /a ERSCHEINT=0
:warte_start
tasklist /FI "IMAGENAME eq VSTOInstaller.exe" /NH | find /I "VSTOInstaller.exe" >nul
if not errorlevel 1 goto warte_ende
set /a ERSCHEINT+=1
if %ERSCHEINT% GEQ 15 goto fertig
ping -n 3 127.0.0.1 >nul
goto warte_start

:warte_ende
set /a LAEUFT=0
:warte_ende_schleife
tasklist /FI "IMAGENAME eq VSTOInstaller.exe" /NH | find /I "VSTOInstaller.exe" >nul
if errorlevel 1 goto fertig
set /a LAEUFT+=1
if %LAEUFT% GEQ 150 goto fertig
ping -n 3 127.0.0.1 >nul
goto warte_ende_schleife

:fertig
echo Installation abgeschlossen.
exit /b 0
