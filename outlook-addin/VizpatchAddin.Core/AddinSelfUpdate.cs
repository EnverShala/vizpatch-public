using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// Add-in-Selbstupdate — Staging + Helfer-Skript (260728-m8g, Stufe 2,
    /// D-6). <b>Warum strikt zweistufig:</b> ein VSTO-Add-in kann sich NICHT
    /// installieren, waehrend sein Host laeuft. `setup.exe`/die `.vsto` aus
    /// dem laufenden Outlook heraus zu starten, waehrend das Add-in selbst
    /// geladen ist, fuehrt in einen halb-installierten Zustand. Deshalb:
    ///
    /// 1. Im LAUFENDEN Outlook wird ausschliesslich HERUNTERGELADEN und
    ///    ABGELEGT (<see cref="Stage"/>) — kein Installer-Aufruf hier.
    /// 2. Ein EIGENSTAENDIGER, von Outlook geloester Helfer-Prozess
    ///    (<see cref="BuildHelperScript"/>) wartet auf das Ende von
    ///    <c>OUTLOOK.EXE</c> und startet ERST DANACH <c>INSTALLIEREN.cmd</c>
    ///    aus dem gestagten Publish (NIE <c>setup.exe</c> direkt — das
    ///    Skript uebernimmt Mark-of-the-Web-Entsperrung und
    ///    Zertifikatsvertrauen, siehe `deployment/addin-publish-extras/`).
    ///
    /// Diese Trennung ist die fragilste Stelle des gesamten Features — bitte
    /// nicht "vereinfachen".
    /// </summary>
    public static class AddinSelfUpdate
    {
        /// <summary>Anfuehrungszeichen — haelt die Batch-Zeilen unten lesbar.</summary>
        private const string Q = "\"";
        /// <summary>
        /// Harte Obergrenze der Warteschleife im Helfer-Skript: 2880 Runden
        /// zu je 5s ≈ 4h. Ohne Obergrenze wuerde ein Helfer, der aus
        /// irgendeinem Grund niemals ein Outlook-Ende sieht (z. B. weil der
        /// Betreiber den Rechner stattdessen herunterfaehrt), unbegrenzt als
        /// Hintergrundprozess weiterlaufen.
        /// </summary>
        private const int DefaultWaitRounds = 2880;


        /// <summary>
        /// Staging liegt bewusst unter <c>%LOCALAPPDATA%</c> — kein
        /// Programmverzeichnis, keine Admin-Rechte fuer das Ablegen noetig
        /// (nur `INSTALLIEREN.cmd`/ClickOnce selbst braucht ggf. eine
        /// Rueckfrage, aber das ist ein spaeterer Schritt, nicht das Staging).
        /// </summary>
        public static string StagingRoot(string rootOverride = null)
        {
            if (!string.IsNullOrEmpty(rootOverride))
            {
                return rootOverride;
            }
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Vizpatch", "addin-update");
        }

        /// <summary>
        /// Pfad des Selbstupdate-Protokolls. Liegt BEWUSST neben dem Staging,
        /// nicht darin: der Helfer loescht das Staging am Ende, und genau dann
        /// braucht man das Protokoll noch.
        ///
        /// <b>Warum es das gibt:</b> am 2026-08-26 ist der Mechanismus zweimal
        /// gescheitert, und beide Male musste die Ursache aus Zeitstempeln und
        /// uebrig gebliebenen Dateien rekonstruiert werden. Beim zweiten Mal
        /// blieb gar nichts uebrig (das Aufraeumen war inzwischen korrekt) —
        /// der Weg war damit vollstaendig blind. Bei drei Kundeninstallationen
        /// ist das untragbar: der Betreiber sieht nur "hat nicht geklappt".
        /// Angehaengt, nicht ueberschrieben — die Historie mehrerer Versuche
        /// ist genau das Interessante.
        /// </summary>
        public static string LogPath(string rootOverride = null)
        {
            string staging = StagingRoot(rootOverride);
            string parent = Path.GetDirectoryName(staging);
            return Path.Combine(string.IsNullOrEmpty(parent) ? staging : parent,
                "addin-update.log");
        }

        /// <summary>
        /// Entscheidet per INHALTSVERGLEICH, ob das gestagte Bundle ueberhaupt
        /// eine andere Add-in-Fassung enthaelt als die gerade laufende.
        ///
        /// <b>Warum Hash und nicht Versionsvergleich:</b> Das laufende Add-in
        /// kann seine eigene ClickOnce-Version nicht verlaesslich ermitteln —
        /// `AssemblyVersion`/`AssemblyFileVersion` stehen im VSTO-Projekt
        /// statisch auf 1.0.0.0, waehrend die `ApplicationVersion` bei jedem
        /// Publish hochzaehlt, und `ApplicationDeployment.IsNetworkDeployed` ist
        /// fuer VSTO-Add-ins typischerweise false. Ein Versionsvergleich waere
        /// damit geraten. Der Hash der geladenen DLL gegen die DLL im Bundle
        /// beantwortet die eigentliche Frage dagegen zweifelsfrei.
        ///
        /// Rueckgabe true = Bundle unterscheidet sich (oder ist nicht
        /// vergleichbar) -> Installation anbieten. false = identisches
        /// Kompilat -> Staging verwerfen, den Betreiber NICHT zum
        /// Outlook-Neustart auffordern. Genau dieser Fall trat bisher bei jedem
        /// Server-Update auf, das gar kein neues Add-in enthielt.
        ///
        /// Bei jedem Zweifel (Datei fehlt, nicht lesbar) wird true geliefert —
        /// lieber einmal zu oft anbieten als ein echtes Add-in-Update
        /// verschlucken.
        /// </summary>
        public static bool BundleDiffersFromInstalled(string installedAssemblyPath, string publishDir)
        {
            string installedHash = TryFileHash(installedAssemblyPath);
            if (installedHash == null)
            {
                return true;
            }

            string bundleDll = FindBundleAssembly(publishDir, Path.GetFileName(installedAssemblyPath));
            string bundleHash = TryFileHash(bundleDll);
            if (bundleHash == null)
            {
                return true;
            }

            return !string.Equals(installedHash, bundleHash, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>
        /// Sucht im entpackten Bundle die Assembly mit dem gegebenen Dateinamen.
        /// ClickOnce legt die Dateien unter
        /// `Application Files/VizpatchAddin_x_y_z_n/` ab und haengt `.deploy` an
        /// (MapFileExtensions) — beide Formen werden akzeptiert. Gibt es mehrere
        /// Versionsordner, gewinnt der zuletzt geschriebene; im ausgeduennten
        /// Paket ist es ohnehin genau einer.
        /// </summary>
        public static string FindBundleAssembly(string publishDir, string assemblyFileName)
        {
            if (string.IsNullOrEmpty(publishDir)
                || string.IsNullOrEmpty(assemblyFileName)
                || !Directory.Exists(publishDir))
            {
                return null;
            }

            var candidates = new List<string>();
            try
            {
                foreach (var name in new[] { assemblyFileName, assemblyFileName + ".deploy" })
                {
                    candidates.AddRange(Directory.GetFiles(publishDir, name, SearchOption.AllDirectories));
                }
            }
            catch (IOException)
            {
                return null;
            }
            catch (UnauthorizedAccessException)
            {
                return null;
            }

            if (candidates.Count == 0)
            {
                return null;
            }

            string best = null;
            DateTime bestStamp = DateTime.MinValue;
            foreach (var candidate in candidates)
            {
                DateTime stamp;
                try
                {
                    stamp = File.GetLastWriteTimeUtc(candidate);
                }
                catch (IOException)
                {
                    continue;
                }
                if (best == null || stamp > bestStamp)
                {
                    best = candidate;
                    bestStamp = stamp;
                }
            }
            return best;
        }

        /// <summary>Pfad des entpackten Publish-Verzeichnisses im Staging, oder
        /// null wenn kein (vollstaendiges) Staging vorliegt.</summary>
        public static string StagedPublishDir(string rootOverride = null)
        {
            string extractedRoot = Path.Combine(StagingRoot(rootOverride), "extracted");
            return FindPublishDir(extractedRoot);
        }

        /// <summary>SHA256 als Hex, oder null bei jedem Lese-/Pfadproblem.</summary>
        private static string TryFileHash(string path)
        {
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                return null;
            }
            try
            {
                using (var sha = System.Security.Cryptography.SHA256.Create())
                using (var stream = File.OpenRead(path))
                {
                    byte[] hash = sha.ComputeHash(stream);
                    var sb = new StringBuilder(hash.Length * 2);
                    foreach (byte b in hash)
                    {
                        sb.Append(b.ToString("x2"));
                    }
                    return sb.ToString();
                }
            }
            catch (IOException)
            {
                return null;
            }
            catch (UnauthorizedAccessException)
            {
                return null;
            }
        }

        /// <summary>Idempotent — wirft NIE, auch wenn nichts existiert (z. B.
        /// wenn ein anderweitig erfolgtes Update kein Staging mehr braucht).</summary>
        public static void ClearStaging(string rootOverride = null)
        {
            try
            {
                string root = StagingRoot(rootOverride);
                if (Directory.Exists(root))
                {
                    Directory.Delete(root, true);
                }
            }
            catch
            {
                // Best-Effort-Aufraeumschritt — darf niemals werfen.
            }
        }

        /// <summary>
        /// Baut den Helfer-Skripttext (pure, testbar ohne Dateisystem). CRLF
        /// ist Pflicht — cmd.exe ist bei LF in <c>if</c>-Bloecken empfindlich
        /// (bekanntes Projekt-Muster, siehe `build-deployment-package.sh`).
        /// Startet NIEMALS <c>setup.exe</c> direkt, sondern
        /// <c>INSTALLIEREN.cmd</c> aus <paramref name="publishDir"/> — dieser
        /// Pfad liegt INNERHALB des Staging-Verzeichnisses (keine externen Pfade).
        /// </summary>
        public static string BuildHelperScript(string stagingDir, string publishDir,
            string logPath = null)
        {
            string log = string.IsNullOrEmpty(logPath) ? LogPath() : logPath;
            string logDir = Path.GetDirectoryName(log);
            var lines = new List<string>
            {
                "@echo off",
                "chcp 65001 >nul",
                "set " + Q + "VIZLOG=" + log + Q,
                "if not exist " + Q + logDir + Q + " md " + Q + logDir + Q + " >nul 2>nul",
                "echo [%DATE% %TIME%] Helfer gestartet, wartet auf das Ende von Outlook >>" + Q + "%VIZLOG%" + Q + " 2>&1",
                "set WAITROUNDS=0",
                ":wait",
                "tasklist /FI \"IMAGENAME eq OUTLOOK.EXE\" /NH | find /I \"OUTLOOK.EXE\" >nul",
                "if errorlevel 1 goto go",
                "set /a WAITROUNDS+=1",
                "if %WAITROUNDS% GEQ " + DefaultWaitRounds + " goto giveup",
                // `ping` statt `timeout`: timeout bricht mit "Die Eingabeumleitung wird
                // nicht unterstuetzt" ab, sobald stdin umgeleitet ist — die
                // Warteschleife lief dann als Busy-Loop durch (belegt
                // 2026-08-27). ping wartet unabhaengig vom Kontext.
                "ping -n 6 127.0.0.1 >nul",
                "goto wait",
                ":go",
                "echo [%DATE% %TIME%] Outlook beendet, starte INSTALLIEREN.cmd >>" + Q + "%VIZLOG%" + Q + " 2>&1",
                "pushd \"" + publishDir + "\"",
                "call INSTALLIEREN.cmd >>" + Q + "%VIZLOG%" + Q + " 2>&1",
                "set INSTRC=%ERRORLEVEL%",
                "echo [%DATE% %TIME%] INSTALLIEREN.cmd beendet, Rueckgabewert %INSTRC% >>" + Q + "%VIZLOG%" + Q + " 2>&1",
                "popd",
                // KEIN Aufraeumen hier — dreimal begruendet (2026-08-26):
                //
                // 1. helper.cmd liegt SELBST im Staging, und cmd.exe liest
                //    Batch-Dateien zeilenweise vom Datentraeger nach. Ein
                //    rmdir auf das eigene Verzeichnis beendet das Skript mit
                //    "Die Batchdatei kann nicht gefunden werden." — im
                //    Testlauf blieb genau dieses Fenster stehen.
                // 2. setup.exe ist nur ein BOOTSTRAPPER. Er endete im Protokoll
                //    1,25 s nach dem Start mit Rueckgabewert 0 und uebergab an
                //    den Office-Anpassungs-Installer (VSTOInstaller.exe). Wer
                //    auf setup.exe wartet, wartet auf den Falschen — 9 s
                //    spaeter griff der echte Installer ins Leere.
                // 3. Jede Wette auf einen Prozessnamen ist die naechste Version
                //    dieses Fehlers. Also gar nicht wetten.
                //
                // Aufgeraeumt wird beim NAECHSTEN Update: Stage() ruft als
                // erstes ClearStaging(). Bis dahin liegen ~2 MB unter
                // %LOCALAPPDATA% — der Preis fuer einen Weg, der funktioniert.
                "echo [%DATE% %TIME%] Fertig. Staging bleibt liegen und wird beim naechsten Update ersetzt >>" + Q + "%VIZLOG%" + Q + " 2>&1",
                // `exit`, NICHT `exit /b`: der Helfer wird per `start` in einer
                // eigenen, INTERAKTIVEN cmd gestartet. `exit /b` beendet nur die
                // Batch-Ebene — das Fenster blieb mit Prompt stehen und hat den
                // Betreiber irritiert (Befund 2026-08-26). `exit` beendet die
                // Shell und schliesst das Fenster.
                "exit 0",
                ":giveup",
                "echo [%DATE% %TIME%] Outlook lief 4 h durch - Helfer gibt auf >>" + Q + "%VIZLOG%" + Q + " 2>&1",
                "exit 1",
            };
            return string.Join("\r\n", lines) + "\r\n";
        }

        /// <summary>Zip-Slip-Schutz (pure) — identische Regel wie serverseitig
        /// (<c>update_dist.is_safe_zip_member</c>, T-m8g-03).</summary>
        public static bool IsSafeZipMember(string name)
        {
            if (string.IsNullOrEmpty(name))
            {
                return false;
            }
            if (name.StartsWith("/") || name.StartsWith("\\"))
            {
                return false;
            }
            if (name.Contains(":"))
            {
                return false; // Laufwerksbuchstabe, z. B. "C:\..."
            }
            string normalized = name.Replace('\\', '/');
            foreach (var part in normalized.Split('/'))
            {
                if (part == "..")
                {
                    return false;
                }
            }
            return true;
        }

        /// <summary>
        /// Entpackt das Add-in-Publish-ZIP ins Staging-Verzeichnis, prueft
        /// Vollstaendigkeit (<c>setup.exe</c> UND <c>INSTALLIEREN.cmd</c>
        /// muessen existieren) und legt <c>helper.cmd</c> +
        /// <c>pending.json</c> an. Bei einem Zip-Slip-Eintrag ODER einem
        /// unvollstaendigen Publish wird eine Ausnahme geworfen, BEVOR
        /// <c>helper.cmd</c>/<c>pending.json</c> geschrieben werden — kein
        /// halb fertiger Zustand.
        /// </summary>
        public static string Stage(string zipPath, string targetVersion, string rootOverride = null)
        {
            string root = StagingRoot(rootOverride);
            ClearStaging(rootOverride);
            Directory.CreateDirectory(root);

            string extractedRoot = Path.Combine(root, "extracted");
            ExtractZipSafely(zipPath, extractedRoot);

            string publishDir = FindPublishDir(extractedRoot);
            if (publishDir == null
                || !File.Exists(Path.Combine(publishDir, "setup.exe"))
                || !File.Exists(Path.Combine(publishDir, "INSTALLIEREN.cmd")))
            {
                throw new InvalidOperationException(
                    "Add-in-Publish unvollständig — setup.exe und/oder INSTALLIEREN.cmd fehlen im Paket.");
            }

            string helperPath = Path.Combine(root, "helper.cmd");
            File.WriteAllText(helperPath,
                BuildHelperScript(root, publishDir, LogPath(rootOverride)),
                new UTF8Encoding(false));

            string pendingPath = Path.Combine(root, "pending.json");
            string pendingJson =
                "{\"version\":\"" + EscapeJson(targetVersion) + "\",\"staged_at\":\""
                + DateTime.UtcNow.ToString("o") + "\"}";
            File.WriteAllText(pendingPath, pendingJson, new UTF8Encoding(false));

            return helperPath;
        }

        private static string FindPublishDir(string extractedRoot)
        {
            if (File.Exists(Path.Combine(extractedRoot, "setup.exe")))
            {
                return extractedRoot;
            }
            if (!Directory.Exists(extractedRoot))
            {
                return null;
            }
            foreach (var dir in Directory.GetDirectories(extractedRoot))
            {
                if (File.Exists(Path.Combine(dir, "setup.exe")))
                {
                    return dir;
                }
            }
            return null;
        }

        private static string EscapeJson(string value) =>
            (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");

        /// <summary>Prueft ALLE Zip-Mitglieder VOR dem Entpacken (Zip-Slip-Schutz)
        /// — wirft, OHNE vorher irgendetwas auf die Platte zu schreiben.</summary>
        private static void ExtractZipSafely(string zipPath, string targetDir)
        {
            using (var archive = ZipFile.OpenRead(zipPath))
            {
                foreach (var entry in archive.Entries)
                {
                    if (!IsSafeZipMember(entry.FullName))
                    {
                        throw new InvalidOperationException(
                            "Unsicherer Zip-Eintrag (Zip-Slip): " + entry.FullName);
                    }
                }

                Directory.CreateDirectory(targetDir);

                foreach (var entry in archive.Entries)
                {
                    string destPath = Path.Combine(
                        targetDir, entry.FullName.Replace('/', Path.DirectorySeparatorChar));
                    if (string.IsNullOrEmpty(entry.Name))
                    {
                        // Verzeichnis-Eintrag (FullName endet auf '/').
                        Directory.CreateDirectory(destPath);
                        continue;
                    }
                    string destDir = Path.GetDirectoryName(destPath);
                    if (!string.IsNullOrEmpty(destDir))
                    {
                        Directory.CreateDirectory(destDir);
                    }
                    entry.ExtractToFile(destPath, overwrite: true);
                }
            }
        }
    }
}
