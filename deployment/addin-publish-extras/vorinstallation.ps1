# vorinstallation.ps1 — laeuft VOR setup.exe, aufgerufen aus INSTALLIEREN.cmd.
#
# Aufgabe: eine bereits installierte Fassung entfernen, WENN sie von einem
# anderen Speicherort kommt als der, aus dem jetzt installiert wird.
#
# Warum das sein muss (belegt am 2026-08-27): ClickOnce bindet eine
# Installation an ihren Herkunftspfad. Ein Update aus einem anderen Pfad wird
# abgelehnt — der Office-Anpassungs-Installer meldet dann:
#
#   Die Anpassung kann nicht installiert werden, weil derzeit eine andere
#   Version installiert ist und von diesem Speicherort nicht aktualisiert
#   werden kann. Zum Installieren [...] muessen Sie [...] deinstallieren.
#
# Genau dieser Fall tritt bei JEDER bestehenden Kundeninstallation beim ersten
# Selbstupdate auf: installiert wurde damals aus dem entpackten ZIP im
# Download-Ordner, das Update kommt aus dem dauerhaften Ort. Einmal
# deinstallieren, einmal neu installieren — danach sind beide Pfade identisch
# und es passiert nie wieder.
#
# Sicherheitsregel: Scheitert die Deinstallation, wird mit Exit-Code 1
# abgebrochen. Dann bleibt die ALTE Fassung installiert und lauffaehig — das
# ist der sichere Zustand. Lieber kein Update als kein Add-in.
[CmdletBinding()]
param(
    # Die .vsto, aus der jetzt installiert werden soll.
    [Parameter(Mandatory = $true)][string]$NeueVsto
)

$ErrorActionPreference = 'Stop'
$KEY = 'HKCU:\SOFTWARE\Microsoft\Office\Outlook\Addins\VizpatchAddin'

function AlsPfad([string]$manifest) {
    if ([string]::IsNullOrWhiteSpace($manifest)) { return $null }
    $m = $manifest -replace '\|vstolocal$', ''
    try { return ([Uri]$m).LocalPath } catch { return $m }
}

$vorhanden = $null
if (Test-Path $KEY) { $vorhanden = (Get-ItemProperty $KEY -EA SilentlyContinue).Manifest }

if ([string]::IsNullOrWhiteSpace($vorhanden)) {
    Write-Host "Keine bestehende Registrierung - nichts zu entfernen."
    exit 0
}

$altPfad = AlsPfad $vorhanden
$neuPfad = AlsPfad $NeueVsto
Write-Host "Bisher registriert: $altPfad"
Write-Host "Neu installieren  : $neuPfad"

if ($altPfad -and $neuPfad -and ($altPfad.TrimEnd('\') -ieq $neuPfad.TrimEnd('\'))) {
    Write-Host "Gleicher Speicherort - normales Update, keine Deinstallation noetig."
    exit 0
}

# Eine Dev-Registrierung (|vstolocal) ist KEINE ClickOnce-Installation. Sie
# wird beim Installieren einfach ueberschrieben; hier gibt es nichts zu
# deinstallieren, und ein Uninstall-Versuch waere ins Blaue.
if ($vorhanden -match '\|vstolocal$') {
    Write-Host "Dev-Registrierung erkannt - keine ClickOnce-Installation, nichts zu entfernen."
    exit 0
}

# Den registrierten Uninstall-Weg nutzen, nicht selbst zusammenbauen: er
# funktioniert auch dann, wenn die alte Quelldatei nicht mehr existiert
# (am 2026-08-27 genau so belegt).
$eintrag = Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -EA SilentlyContinue |
    ForEach-Object { Get-ItemProperty $_.PSPath -EA SilentlyContinue } |
    Where-Object { $_.DisplayName -eq 'VizpatchAddin' } | Select-Object -First 1

if (-not $eintrag -or [string]::IsNullOrWhiteSpace($eintrag.UninstallString)) {
    Write-Host "WARNUNG: kein Uninstall-Eintrag gefunden. Es wird ohne Deinstallation weitergemacht."
    Write-Host "Schlaegt die Installation danach mit 'von diesem Speicherort nicht aktualisiert'"
    Write-Host "fehl, muss VizpatchAddin einmal ueber 'Apps & Features' entfernt werden."
    exit 0
}

# UninstallString hat die Form: "<exe>" /Uninstall <url>
$s = $eintrag.UninstallString
$i = $s.IndexOf('/Uninstall', [StringComparison]::OrdinalIgnoreCase)
if ($i -lt 1) { Write-Host "FEHLER: UninstallString unverstaendlich: $s"; exit 1 }
$exe = $s.Substring(0, $i).Trim().Trim('"')
$url = $s.Substring($i + '/Uninstall'.Length).Trim().Trim('"')

Write-Host "Deinstalliere bestehende Fassung ..."
Write-Host "  $exe /Uninstall $url /Silent"
$p = Start-Process -FilePath $exe -ArgumentList @('/Uninstall', $url, '/Silent') -PassThru -Wait
if ($p.ExitCode -ne 0) {
    Write-Host "FEHLER: Deinstallation endete mit Code $($p.ExitCode)."
    Write-Host "Die bisherige Fassung bleibt installiert - das Update wird abgebrochen."
    exit 1
}
Write-Host "Deinstallation erfolgreich."
exit 0
