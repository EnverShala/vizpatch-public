# CI/CD (Phase 15)

Dieses Dokument erklärt, wie die automatische Prüfung und der Image-Bau von Vizpatch aufgebaut sind und wie man sie lokal nachstellt. Versionen von Actions und Werkzeugen stehen bewusst nicht hier, sondern in den Workflow-Dateien unter `.github/workflows/`. Wer eine Version braucht, liest sie dort.

## 1. Überblick

| Workflow | Datei | Trigger | Aufgabe |
|---|---|---|---|
| `CI` | `ci.yml` | Push auf jeden Branch, Pull Request, manuell | Tests, Wächter, Lint, Image-Bau mit Sicherheits-Scan |
| `Release-Images` | `release.yml` | Tag der Form `vX.Y.Z`, manuell | Multi-Arch-Images nach GHCR |
| `Add-in` | `addin.yml` | Änderungen unter `outlook-addin/` und an der Workflow-Datei, manuell | Core-Tests des Add-ins auf Windows |

Jobs in `CI` und warum es sie gibt:

- **tests (agent)** und **tests (webui)**: die beiden Suiten laufen getrennt, weil beide Pakete intern `src` heißen und sich ein gemeinsames Environment nicht vertragen.
- **shell**: die Shell-Tests der Skripte unter `scripts/` und `tests/`.
- **guards**: die Sicherheits-Wächter (siehe Abschnitt 3). Sie sichern Invarianten ab, die im Code nicht sichtbar sind, etwa „der Agent versendet nie selbst".
- **ruff**: nur Fehlerklassen, die echte Programmfehler anzeigen (Syntaxfehler, undefinierte Namen). Keine Stilregeln.
- **images**: baut beide Images für amd64 ohne Push und lässt Trivy darüber laufen. So fällt ein kaputtes Dockerfile oder ein bekanntes kritisches Sicherheitsproblem vor dem Release auf, nicht beim Kunden. Danach prüft `check-image-content.sh` jede Image-Schicht auf Kundenpaket-Dateien, `.env`, Schlüsseldateien und Token-Muster.
- **secrets**: gitleaks über `scripts/ci-test.sh secrets` (ruft `scripts/check-secrets.sh`). Der Arbeitsbaum wird immer geprüft; die gesamte Historie nur im öffentlichen Repo (Abschnitt 10). Der Modus gehört nicht zu `all`, weil der Linux-Probelauf-Container kein Docker hat.

Alle Workflows laufen mit top-level `permissions: contents: read`. `packages: write` steht ausschließlich im Release-Job von `release.yml`. `pull_request_target` wird nirgends benutzt.

## 2. Lokal reproduzieren

Die Suite-Aufrufe gibt es genau einmal, in `scripts/ci-test.sh`. CI und lokaler Lauf rufen dasselbe Skript.

```
bash scripts/ci-test.sh <agent|webui|shell|guards|ruff|secrets|all>
bash scripts/linux-probelauf.sh [modus] [tz]
```

`scripts/linux-probelauf.sh` ist der Normalfall auf Windows: Es klont den **committeten** Stand frisch in einen `python:3.13-slim`-Container, führt dort als Nicht-root-Benutzer `ci-test.sh` aus und setzt `TZ=Europe/Berlin`. Nicht committete Änderungen werden nicht geprüft. Das Log landet unter `dist/linux-probelauf/`. Entwickelt wird auf Windows, ausgeliefert wird nach Linux; Zeilenenden, Zeitzone, Pfade und Dateirechte sind die typischen Fehlerquellen.

Regel: Testausgaben nicht durch Pipes leiten. Der Exit-Code der Pipe verdeckt den Exit-Code des Tests (Falle aus `release.sh`).

## 3. Wächter

| Wächter | Was er sichert | Gegenprobe |
|---|---|---|
| `scripts/check-addin-no-autosend.sh` | Das Add-in versendet nie selbst | eine verbotene Versand-Stelle in eine Arbeitskopie einfügen, Skript muss Exit 1 liefern |
| `scripts/check-no-dist-token.sh` | Kein Dist-Token in getrackten Dateien | ein Token-Muster in eine Arbeitskopie einfügen |
| `scripts/check-actions-pinned.sh` | Jede Action per vollständiger Commit-SHA gepinnt | an einer Kopie einer Workflow-Datei eine Action auf einen Tag zurücksetzen (`@v7`), Exit 1 |
| `scripts/check-secrets.sh` | Keine Geheimnisse im Arbeitsbaum und (öffentlich) in der Historie; einziger Aufrufweg für gitleaks, Werkzeug per Digest gepinnt (`scripts/scan-tools.env`) | zur Laufzeit ein Fake-Token in eine Kopie schreiben, Exit 1; nach dem Löschen im Arbeitsbaum grün, in der Historie weiter rot (`tests/test_check_secrets.sh`) |
| `scripts/check-image-content.sh` | Keine Kundenpaket-Dateien, `.env`, Schlüsseldateien oder Token-Muster in einer Image-Schicht, auch nicht in einer später gelöschten Datei | eine `.env` in eine Fixture-Schicht legen, Exit 1 (`tests/test_check_image_content.sh`, Modus `--dir`) |
| SMTP-Drift-Test (`agent/src/` ohne SMTP) | Der Agent importiert kein SMTP | `import smtplib` in eine neue Datei unter `agent/src/` committen, `bash scripts/linux-probelauf.sh guards` muss Exit 1 liefern |

Der SMTP-Wächter darf nicht übersprungen werden: Ein Skip zählt als rot. Das Skript erwartet `2 passed`, nichts anderes. Dieselbe Verletzung lässt auch einen Test in der webui-Suite fehlschlagen, es ist also kein Einzelposten.

Gegenproben nie auf `master` fahren. Auf einem Wegwerf-Branch und danach Branch und Pull Request wieder entfernen. Für lokale Proben reicht eine Kopie der Datei in einem temporären Verzeichnis.

## 4. Lieferkette

**Pinning-Methode für Actions:**

1. Neuestes Release der Action per `gh release view` messen und prüfen, dass es mindestens 14 Tage alt ist (Karenz).
2. Den Commit zum Tag per `git ls-remote` lesen. Bei annotierten Tags zählt die Zeile mit `^{}` (der Commit hinter dem Tag-Objekt), nicht die des Tag-Objekts.
3. Gegenprobe: `gh api repos/<owner>/<repo>/commits/<sha> --jq .sha` muss dieselbe SHA zurückgeben.
4. Schreibweise in der Workflow-Datei: `uses: owner/action@<volle-sha> # <tag>`.

Auch transitive Aktionen zählen. Die Trivy-Action bezieht Trivy über eine weitere Action, die ihrerseits per SHA gepinnt ist. Die Karenz und die Prüfung gelten daher auch für Werkzeug-Binaries wie Trivy selbst.

**Dependabot** (`.github/dependabot.yml`) prüft wöchentlich pip, Docker und GitHub Actions, gruppiert die Updates und erzeugt Pull Requests, die durch `ci.yml` laufen. Kein Auto-Merge.

- `anthropic` und `mcp`: Major-Sprünge sind ausgeschlossen. Ein Major kann das API des Produkts brechen und gehört in eine bewusste Entscheidung.
- Python im Basis-Image: Major und Minor sind ausgeschlossen. Python 3.13 ist eine Stack-Entscheidung (siehe CLAUDE.md).
- Änderungen an `agent/pyproject.toml` und `webui/pyproject.toml` ändern das Kundenpaket und gehören in ein Release mit Release-Notes. Dependabot-PRs werden deshalb nicht blind gemergt.
- Das Docker-Basis-Image ist nicht per Digest gepinnt. Das würde die Dockerfiles und damit das Kundenpaket ändern und ist nicht Teil der Phase.

**Repo-Einstellungen (Entscheidung, kein Messwert):** SHA-Pflicht für Actions eingeschaltet, Standardrechte des Workflow-Tokens nur lesend, Workflows dürfen keine Pull Requests freigeben. Wer das prüfen will, liest es per `gh api repos/EnverShala/vizpatch/actions/permissions` und `.../actions/permissions/workflow` neu aus.

## 5. Trivy

Der Scan im `images`-Job und im Release-Job hat feste Regeln:

- Schweregrad **CRITICAL** bricht ab.
- `ignore-unfixed`: Probleme ohne verfügbare Korrektur brechen nicht ab.
- Scanner `vuln` und `secret`.

Ausnahmen stehen in `.trivyignore`. Standard: keine Einträge. Format je Eintrag: die Begründung als Kommentarzeile darüber (warum nicht behebbar, Datum, wer entschieden hat), dann `CVE-JJJJ-NNNNN exp:JJJJ-MM-TT`. Höchstfrist 90 Tage. Ein abgelaufener Eintrag greift nicht mehr, damit eine Ausnahme nie still dauerhaft wird.

Bei rotem Scan: zuerst prüfen, ob die Ursache eine veraltete Schicht ist. Lokal gebaute Images mit Layer-Cache tragen alte Debian-Pakete und alt aufgelöste Python-Pakete mit sich. Ein Neubau mit `docker buildx build --pull --no-cache` zeigt den Stand, den ein frischer Runner sieht. Ist das Problem dort weg, genügt ein erneuter Lauf. Ist es echt und behebbar, wird das Basis-Image oder die Abhängigkeit aktualisiert (Release mit Notes). Nur wenn es nicht behebbar ist und der Betreiber entscheidet, kommt ein befristeter `.trivyignore`-Eintrag dazu. Das Gate kann an einem Tag mit neuer behebbarer CVE im Basis-Image rot werden, bis das Basis-Image nachzieht. Das ist gewollt.

## 6. Release

`scripts/release.sh` bleibt die Quelle für Tag, GitHub-Release, Kundenpaket, Dist-Repo und Versions-Index. `release.yml` ersetzt das nicht. Der Workflow macht genau eines: Er pusht Multi-Arch-Images nach GHCR.

- Auslöser: ein Tag `vX.Y.Z` oder `gh workflow run release.yml --ref vX.Y.Z`.
- Image-Tags: der Git-Tag mit `v` und `latest`.
- Plattformen: amd64 und arm64 (arm64 per QEMU).
- Image-Namen (P16-04): Repo-Name kleingeschrieben für das Agent-Image, plus `-webui` für die Weboberfläche, berechnet aus `GITHUB_REPOSITORY`. Im privaten Repo ergibt das unverändert `vizpatch` und `vizpatch-webui`. Ein Owner kann keine zwei Repos gleichen Namens haben, also kollidieren die Pakete eines öffentlichen Repos nie mit den privaten. Das Paket wird über das Label `org.opencontainers.image.source` mit dem bauenden Repo verknüpft.
- Ablauf: Scan-Build, Trivy-Gate, Inhalts-Gate (`check-image-content.sh --image`), danach erst der Push, danach ein Architektur-Smoke (das gepushte Image startet auf beiden Plattformen).
- Der Workflow schreibt weder in das Dist-Repo noch in den Versions-Index.

**Vermutung:** Ob ein Tag, das `release.sh` über die GitHub-API anlegt (`gh release create`), den `push`-Trigger auslöst, ist nicht gemessen. Gemessen ist nur der Lauf nach einem normalen `git push` des Tags. Fallback: `gh workflow run release.yml --ref vX.Y.Z`. Der Nachweis kommt mit dem ersten echten Release nach Einführung der Phase.

Kunden beziehen weiterhin die Tarballs (amd64) aus dem Kundenpaket, nicht die GHCR-Images. Die Images in GHCR sind für spätere Ausbaustufen gedacht.

## 7. Test-Tag-Verfahren

Wenn `release.yml` geändert wird, braucht es einen Nachweis auf echtem Runner, ohne ein echtes Release anzulegen. Verfahren, wie in Plan 15-05 erprobt:

1. **Name:** `v0.0.1`. Er liegt unter jeder echten Version (die Zählung beginnt bei v2.0.0), kann also nie mit dem Update-Vergleich der Kundeninstallationen kollidieren und passt trotzdem zum Muster `vX.Y.Z`.
2. **Vorher messen:** der Tag existiert weder lokal noch auf dem Remote, es gibt kein Release `v0.0.1` im Hauptrepo und im Dist-Repo, der Hash von `latest.json` im Versions-Index, die Paketliste in GHCR.
3. **Auslösen:** annotierten Tag auf den geprüften Commit setzen und pushen. Ausgelöst wird nur `release.yml`.
4. **Prüfen:** Lauf success, Index-Manifest beider Images mit genau amd64 und arm64, `v0.0.1` und `latest` mit demselben Digest, Paket-Sichtbarkeit, Label `org.opencontainers.image.source`.
5. **Kundenweg unberührt:** `gh release view v0.0.1` in beiden Repos meldet „not found", der Hash von `latest.json` ist unverändert.
6. **Aufräumen:** Tag remote und lokal löschen. GHCR-Paket nur löschen, wenn alle Versionen darin aus dem Lauf stammen (Zeitstempel vergleichen). Die API lehnt das Löschen der letzten getaggten Version ab, dann hilft nur das Löschen des ganzen Pakets. Danach `docker logout ghcr.io`.

Der CLI-Token braucht dafür die Scopes `read:packages` und `delete:packages`. Sie werden nur für diesen Nachweis ergänzt (`gh auth refresh -s read:packages,delete:packages`, braucht eine Bestätigung im Browser) und sind keine Dauer-Ausstattung.

**Vermutung (offen):** Ob ein gelöschter GHCR-Paketname beim nächsten Release wieder frei nutzbar ist, ist nicht gemessen. Wenn nicht, scheitert `release.yml` sichtbar, Kunden sind nicht betroffen.

## 8. Add-in

Ergebnis des Spikes (CI-08), Ausgang **B**: Die Core-Tests (255 von 255) laufen auf `windows-latest` grün, das VSTO-Projekt baut dort nicht innerhalb der Zeitbox. `addin.yml` enthält nur den Test-Teil, keinen VSTO-Platzhalter.

Das VSTO-Projekt wird lokal gebaut, die Core-Tests laufen in CI. Der Build scheitert in den OfficeTools-BuildTasks am Laden weiterer VSTO-Assemblies (nacheinander gemessen: Hosting, die Signatur-Prüfung der Manifeste, danach `Microsoft.VisualStudio.Tools.Office.Runtime`). Der Compiler-Schritt des Add-ins selbst war grün. **Vermutung:** weitere Assemblies aus den Referenz-Assemblies müssten dem OfficeTools-Ordner beigelegt werden. Gemessen ist das nicht, auch nicht, ob nach Office.Runtime weitere Fehler folgen. Ein neuer Versuch ist ein eigener Auftrag.

Fallstrick im Test-Schritt: `dotnet test --no-build` nach einem MSBuild-Build meldete im ersten Lauf „No test is available" und endete trotzdem mit Exit 0. Die Stufe war grün, ohne einen Test auszuführen. Deshalb ruft der Workflow `dotnet test` ohne `--no-build` auf und bricht ab, wenn `Test Run Successful` im Log fehlt.

Keine Bundle-Hash-Erwartungen in CI: `BundleDiffersFromInstalled` vergleicht SHA256, ein CI-Build ist nie byte-gleich mit dem Publish.

## 9. Offene Punkte für spätere Phasen

- Native arm64-Runner statt QEMU, falls die Build-Zeit zum Problem wird.
- Provenance und SBOM der Images (bewusst aus, P-09).
- Branch Protection und Paket-Sichtbarkeit: siehe Phase 16. Die GHCR-Pakete waren im Test-Tag-Lauf privat.
- Eine getrackte Entwicklungs-Schlüsseldatei des Add-ins (`outlook-addin/VizpatchAddin/VizpatchAddin_TemporaryKey.pfx`) liegt im Repo. Prüfung und Rotation: siehe Phase 16. Der Inhalt der Datei gehört nicht in Dokumente.
- Produktbefunde aus ruff: in Plan 15-02 keine gemeldet.

## 10. Öffentliches Repo (Phase 16)

Das öffentliche Repo entsteht als Einweg-Export aus dem privaten Repo; die Workflows kommen mit. Was dort dauerhaft schützen soll, steht deshalb schon hier und läuft hier grün.

- **History-Scan nur öffentlich (P16-05):** Der Job `secrets` setzt `CI_SCAN_HISTORY` aus `github.event.repository.private`. Ist das Repo privat, wird nur der Arbeitsbaum geprüft; sonst (auch wenn das Feld fehlt) zusätzlich die gesamte Historie. Die Altlasten der privaten Historie gehören zur Bereinigung, nicht in einen dauerhaft roten Job. Der Checkout holt dafür die volle Historie (`fetch-depth: 0`).
- **Fork-PRs:** Der Job braucht kein Secret, nur `contents: read`, und der Checkout behält keine Zugangsdaten (`persist-credentials: false`). `pull_request_target` bleibt verboten.
- **`.dockerignore`:** Die `.dockerignore` im Repo-Wurzelverzeichnis greift für die Image-Builds nicht, weil die Build-Kontexte `agent/` und `webui/` sind. Geschützt wird über die expliziten `COPY`-Zeilen der Dockerfiles. In `agent/` und `webui/` liegt bewusst keine `.dockerignore` (der Build-Kontext des Kundenpakets bleibt unverändert). Statt dessen prüft `check-image-content.sh` das Ergebnis, im `images`-Job und im Release-Job vor dem Push.
- **Lokal prüfen:** `bash scripts/ci-test.sh secrets` scannt das Dateisystem, nicht nur getrackte Dateien. Lokal ignorierte Dateien (virtuelle Umgebungen, `.env`, `dist/`) erzeugen deshalb Funde, die ein frischer Checkout nicht hat. Für den CI-Stand ein frisches `git clone` prüfen.
