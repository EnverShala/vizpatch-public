using System;
using System.IO;
using System.IO.Compression;
using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>
    /// Tests fuer das Add-in-Selfupdate-Staging (260728-m8g, Stufe 2, D-6).
    /// Nutzt ausschliesslich den ueberschreibbaren <c>rootOverride</c>-
    /// Parameter — kein Test schreibt in das echte <c>%LOCALAPPDATA%</c>.
    /// </summary>
    public class AddinSelfUpdateTests : IDisposable
    {
        private readonly string _root;

        public AddinSelfUpdateTests()
        {
            _root = Path.Combine(Path.GetTempPath(), "vizpatch-addin-update-tests-" + Guid.NewGuid());
        }

        public void Dispose()
        {
            try
            {
                if (Directory.Exists(_root))
                {
                    Directory.Delete(_root, true);
                }
            }
            catch
            {
                // Best-Effort-Aufraeumen nach dem Test.
            }
        }

        [Fact]
        public void StagingRoot_DefaultLiesUnderLocalAppData()
        {
            string root = AddinSelfUpdate.StagingRoot();
            string expectedPrefix = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Vizpatch");
            Assert.StartsWith(expectedPrefix, root);
            Assert.EndsWith("addin-update", root);
        }

        [Fact]
        public void StagingRoot_RootOverrideWins()
        {
            Assert.Equal(_root, AddinSelfUpdate.StagingRoot(_root));
        }

        // --- BundleDiffersFromInstalled -------------------------------------
        // Ersetzt das fruehere ShouldStage: das verglich SERVER-Versionen und
        // wurde ausserdem nirgends aufgerufen. Verglichen wird jetzt der Inhalt,
        // weil das laufende Add-in seine ClickOnce-Version nicht kennt.

        private string MakeBundle(string dllContent, string dllName = "VizpatchAddin.dll")
        {
            string publishDir = Path.Combine(_root, "bundle", "Application Files", "VizpatchAddin_1_0_0_24");
            Directory.CreateDirectory(publishDir);
            File.WriteAllText(Path.Combine(publishDir, dllName + ".deploy"), dllContent);
            return Path.Combine(_root, "bundle");
        }

        private string MakeInstalledDll(string content)
        {
            string dir = Path.Combine(_root, "installed");
            Directory.CreateDirectory(dir);
            string path = Path.Combine(dir, "VizpatchAddin.dll");
            File.WriteAllText(path, content);
            return path;
        }

        [Fact]
        public void BundleDiffers_GleichesKompilat_KeinStaging()
        {
            // Der Alltagsfall: Server-Update ohne Add-in-Aenderung. Vorher wurde
            // hier zum Outlook-Neustart aufgefordert, ohne dass sich etwas
            // aenderte.
            string installed = MakeInstalledDll("IDENTISCHER-INHALT");
            string bundle = MakeBundle("IDENTISCHER-INHALT");
            Assert.False(AddinSelfUpdate.BundleDiffersFromInstalled(installed, bundle));
        }

        [Fact]
        public void BundleDiffers_AnderesKompilat_StagingNoetig()
        {
            string installed = MakeInstalledDll("ALTE-FASSUNG");
            string bundle = MakeBundle("NEUE-FASSUNG");
            Assert.True(AddinSelfUpdate.BundleDiffersFromInstalled(installed, bundle));
        }

        [Fact]
        public void BundleDiffers_OhneDeployEndung_WirdAuchGefunden()
        {
            // MapFileExtensions kann abgeschaltet sein -> Dateien ohne .deploy.
            string installed = MakeInstalledDll("GLEICH");
            string publishDir = Path.Combine(_root, "b2", "Application Files", "VizpatchAddin_1_0_0_24");
            Directory.CreateDirectory(publishDir);
            File.WriteAllText(Path.Combine(publishDir, "VizpatchAddin.dll"), "GLEICH");
            Assert.False(AddinSelfUpdate.BundleDiffersFromInstalled(installed, Path.Combine(_root, "b2")));
        }

        [Fact]
        public void BundleDiffers_InstallierteDateiFehlt_LieberAnbieten()
        {
            string bundle = MakeBundle("EGAL");
            Assert.True(AddinSelfUpdate.BundleDiffersFromInstalled(
                Path.Combine(_root, "gibtsnicht.dll"), bundle));
        }

        [Fact]
        public void BundleDiffers_BundleOhneAssembly_LieberAnbieten()
        {
            string installed = MakeInstalledDll("EGAL");
            string leer = Path.Combine(_root, "leer");
            Directory.CreateDirectory(leer);
            Assert.True(AddinSelfUpdate.BundleDiffersFromInstalled(installed, leer));
        }

        [Fact]
        public void BundleDiffers_NullArgumente_LieberAnbieten()
        {
            Assert.True(AddinSelfUpdate.BundleDiffersFromInstalled(null, null));
        }

        [Fact]
        public void FindBundleAssembly_LeererPfad_IstNull()
        {
            Assert.Null(AddinSelfUpdate.FindBundleAssembly("", "VizpatchAddin.dll"));
            Assert.Null(AddinSelfUpdate.FindBundleAssembly(_root, ""));
        }

        [Fact]
        public void BuildHelperScript_UsesCrlfLineEndings()
        {
            string script = AddinSelfUpdate.BuildHelperScript(@"C:\staging", @"C:\staging\extracted\addin-publish");
            Assert.Contains("\r\n", script);
            // Jedes Zeilenende ist CRLF — nach Entfernen aller "\r\n" darf
            // kein einzelnes "\n" oder "\r" mehr uebrig sein.
            string withoutCrlf = script.Replace("\r\n", "");
            Assert.DoesNotContain("\n", withoutCrlf);
            Assert.DoesNotContain("\r", withoutCrlf);
        }

        [Fact]
        public void BuildHelperScript_ContainsWaitLoopOnOutlookExe()
        {
            string script = AddinSelfUpdate.BuildHelperScript(@"C:\staging", @"C:\staging\publish");
            Assert.Contains("OUTLOOK.EXE", script);
            Assert.Contains(":wait", script);
            // Die Schleife muss zwischen den Runden WARTEN — womit, prueft
            // BuildHelperScript_WartetMitPing_NichtMitTimeout. Hier stand
            // frueher "timeout"; das ist seit dem Wechsel auf ping falsch,
            // die Absicht (es wird gewartet) bleibt aber gueltig.
            Assert.Contains("ping -n", script);
        }

        [Fact]
        public void BuildHelperScript_CallsInstallierenCmd_NeverSetupExeDirectly()
        {
            string script = AddinSelfUpdate.BuildHelperScript(@"C:\staging", @"C:\staging\publish");
            Assert.Contains("INSTALLIEREN.cmd", script);

            // Frueher prueste dieser Test "setup.exe kommt im Helfer gar nicht
            // vor". Seit der Helfer vor dem Aufraeumen auf das Ende von
            // setup.exe wartet (Regression 2026-08-26), ist eine ERWAEHNUNG
            // zulaessig — ein START weiterhin nicht. Genau das wird jetzt
            // geprueft, statt das Verbot aufzuweichen: setup.exe darf
            // ausschliesslich in der tasklist-Abfrage auftauchen.
            foreach (var line in script.Split(new[] { "\r\n" }, StringSplitOptions.None))
            {
                if (line.IndexOf("setup.exe", StringComparison.OrdinalIgnoreCase) < 0)
                {
                    continue;
                }
                // Erlaubt sind genau zwei Rollen: die tasklist-Abfrage der
                // Warteschleife und eine Protokollzeile. Verboten bleibt, was
                // setup.exe AUSFUEHRT — darum ging es dem Test immer.
                bool nurBeobachtend =
                    line.IndexOf("tasklist", StringComparison.OrdinalIgnoreCase) >= 0
                    || line.TrimStart().StartsWith("echo", StringComparison.OrdinalIgnoreCase);
                Assert.True(nurBeobachtend,
                    "Zeile nennt setup.exe, ist aber weder tasklist-Abfrage noch Protokoll: " + line);
                Assert.DoesNotContain("start ", line, StringComparison.OrdinalIgnoreCase);
                Assert.DoesNotContain("call ", line, StringComparison.OrdinalIgnoreCase);
            }
        }

        // Regression 2026-08-26, in zwei Runden gelernt. Der Helfer raeumte
        // das Staging am Ende mit `rmdir /s /q` ab. Das ist aus zwei
        // unabhaengigen Gruenden falsch, und beide wurden im Testlauf belegt:
        //
        // 1. helper.cmd liegt SELBST im Staging. cmd.exe liest Batch-Dateien
        //    zeilenweise vom Datentraeger nach — nach dem rmdir stand im
        //    Helfer-Fenster "Die Batchdatei kann nicht gefunden werden."
        // 2. setup.exe ist nur ein Bootstrapper (Protokoll: nach 1,25 s
        //    beendet, Rueckgabewert 0) und uebergibt an VSTOInstaller.exe.
        //    Der griff 9 s spaeter ins Leere: "VizpatchAddin.vsto konnte
        //    nicht gedownloadet werden". Ein `start /wait` auf setup.exe half
        //    deshalb NICHT.
        //
        // Aufgeraeumt wird beim naechsten Update durch ClearStaging() in
        // Stage(). Dieser Test haelt fest, dass der Helfer NICHTS loescht.
        [Fact]
        public void BuildHelperScript_LoeschtNiemalsEtwas()
        {
            string script = AddinSelfUpdate.BuildHelperScript(@"C:\staging", @"C:\staging\publish");

            // Zeilenweise und am Zeilenanfang pruefen: "rd " als bloszer
            // Teilstring steckt auch in "wird beim naechsten Update ersetzt".
            foreach (var line in script.Split(new[] { "\r\n" }, StringSplitOptions.None))
            {
                string befehl = line.TrimStart();
                foreach (var verb in new[] { "rmdir", "rd ", "del ", "erase", "Remove-Item" })
                {
                    Assert.False(befehl.StartsWith(verb, StringComparison.OrdinalIgnoreCase),
                        "Der Helfer darf nichts loeschen — er liegt selbst im Staging: " + line);
                }
            }
            Assert.DoesNotContain("rmdir", script, StringComparison.OrdinalIgnoreCase);
        }

        // Der Helfer darf sich auch nicht auf einen Prozessnamen der
        // Installation verlassen — jede solche Wette ist die naechste Version
        // des Fehlers. Auf OUTLOOK.EXE zu warten ist dagegen der Sinn des
        // Helfers und bleibt erlaubt.
        [Fact]
        public void BuildHelperScript_WartetNurAufOutlook_NichtAufInstallerProzesse()
        {
            string script = AddinSelfUpdate.BuildHelperScript(@"C:\staging", @"C:\staging\publish");

            foreach (var line in script.Split(new[] { "\r\n" }, StringSplitOptions.None))
            {
                if (line.IndexOf("tasklist", StringComparison.OrdinalIgnoreCase) < 0)
                {
                    continue;
                }
                Assert.Contains("OUTLOOK.EXE", line, StringComparison.OrdinalIgnoreCase);
            }
            foreach (var p in new[] { "VSTOInstaller", "dfsvc" })
            {
                Assert.DoesNotContain(p, script, StringComparison.OrdinalIgnoreCase);
            }
        }

        // Der Helfer laeuft per `start` in einer eigenen, INTERAKTIVEN cmd.
        // `exit /b` beendet nur die Batch-Ebene — das Fenster blieb mit
        // Prompt stehen ("C:\Windows\System32>") und irritiert den Kunden.
        // `exit` beendet die Shell und schliesst das Fenster.
        [Fact]
        public void BuildHelperScript_BeendetDieShell_NichtNurDieBatchEbene()
        {
            string script = AddinSelfUpdate.BuildHelperScript(@"C:\staging", @"C:\staging\publish");

            Assert.DoesNotContain("exit /b", script);
            Assert.Contains("exit 0", script);
            Assert.Contains("exit 1", script);
        }

        // `timeout` bricht mit "Die Eingabeumleitung wird nicht unterstuetzt"
        // ab, sobald stdin umgeleitet ist — die Warteschleife wird dann zum
        // Busy-Loop und der Deckel ist in Sekunden erreicht (belegt
        // 2026-08-27). `ping -n` wartet unabhaengig vom Kontext.
        [Fact]
        public void BuildHelperScript_WartetMitPing_NichtMitTimeout()
        {
            string script = AddinSelfUpdate.BuildHelperScript(@"C:\staging", @"C:\staging\publish");

            foreach (var line in script.Split(new[] { "\r\n" }, StringSplitOptions.None))
            {
                Assert.False(line.TrimStart().StartsWith("timeout ", StringComparison.OrdinalIgnoreCase),
                    "timeout taugt nicht als Warteschleife: " + line);
            }
            Assert.Contains("ping -n", script);
        }

        [Fact]
        public void BuildHelperScript_HasHardUpperBoundOnWaitTime()
        {
            string script = AddinSelfUpdate.BuildHelperScript(@"C:\staging", @"C:\staging\publish");
            Assert.Contains("GEQ", script);
            Assert.Contains(":giveup", script);
        }

        [Fact]
        public void BuildHelperScript_NoPathOutsideStagingDirectory()
        {
            string publishDir = Path.Combine(_root, "extracted", "addin-publish");
            string script = AddinSelfUpdate.BuildHelperScript(_root, publishDir);
            Assert.Contains(publishDir, script);
            Assert.Contains(_root, script);
        }

        [Theory]
        [InlineData("../x", false)]
        [InlineData("/etc/x", false)]
        [InlineData("a/../../x", false)]
        [InlineData("addin-publish/x/y.dll", true)]
        [InlineData(@"C:\evil\path", false)]
        public void IsSafeZipMember_Cases(string name, bool expected)
        {
            Assert.Equal(expected, AddinSelfUpdate.IsSafeZipMember(name));
        }

        private static string CreateValidPublishZip(string zipPath)
        {
            using (var archive = ZipFile.Open(zipPath, ZipArchiveMode.Create))
            {
                archive.CreateEntry("addin-publish/setup.exe").Open().Dispose();
                using (var writer = new StreamWriter(archive.CreateEntry("addin-publish/INSTALLIEREN.cmd").Open()))
                {
                    writer.Write("@echo off\r\ncall setup.exe\r\n");
                }
                using (var writer = new StreamWriter(archive.CreateEntry("addin-publish/VizpatchAddin.vsto").Open()))
                {
                    writer.Write("<vsto/>");
                }
            }
            return zipPath;
        }

        [Fact]
        public void Stage_ValidZip_ExtractsAndWritesHelperAndPending()
        {
            Directory.CreateDirectory(_root);
            string zipPath = Path.Combine(_root, "addin.zip");
            CreateValidPublishZip(zipPath);

            string stagingRoot = Path.Combine(_root, "staging-target");
            string helperPath = AddinSelfUpdate.Stage(zipPath, "v1.14.0", stagingRoot);

            Assert.True(File.Exists(helperPath));
            Assert.True(File.Exists(Path.Combine(stagingRoot, "pending.json")));
            string pending = File.ReadAllText(Path.Combine(stagingRoot, "pending.json"));
            Assert.Contains("v1.14.0", pending);

            string extractedPublish = Path.Combine(stagingRoot, "extracted", "addin-publish");
            Assert.True(File.Exists(Path.Combine(extractedPublish, "setup.exe")));
            Assert.True(File.Exists(Path.Combine(extractedPublish, "INSTALLIEREN.cmd")));

            string helperContent = File.ReadAllText(helperPath);
            Assert.Contains(extractedPublish, helperContent);
        }

        [Fact]
        public void Stage_MissingInstallierenCmd_ThrowsAndWritesNoPendingJson()
        {
            Directory.CreateDirectory(_root);
            string zipPath = Path.Combine(_root, "incomplete.zip");
            using (var archive = ZipFile.Open(zipPath, ZipArchiveMode.Create))
            {
                archive.CreateEntry("addin-publish/setup.exe").Open().Dispose();
                // Kein INSTALLIEREN.cmd im Paket.
            }

            string stagingRoot = Path.Combine(_root, "staging-target-2");
            Assert.Throws<InvalidOperationException>(
                () => AddinSelfUpdate.Stage(zipPath, "v1.14.0", stagingRoot));

            Assert.False(File.Exists(Path.Combine(stagingRoot, "pending.json")));
            Assert.False(File.Exists(Path.Combine(stagingRoot, "helper.cmd")));
        }

        [Fact]
        public void Stage_MissingSetupExe_ThrowsAndWritesNoPendingJson()
        {
            Directory.CreateDirectory(_root);
            string zipPath = Path.Combine(_root, "incomplete2.zip");
            using (var archive = ZipFile.Open(zipPath, ZipArchiveMode.Create))
            {
                using (var writer = new StreamWriter(archive.CreateEntry("addin-publish/INSTALLIEREN.cmd").Open()))
                {
                    writer.Write("@echo off\r\n");
                }
            }

            string stagingRoot = Path.Combine(_root, "staging-target-3");
            Assert.Throws<InvalidOperationException>(
                () => AddinSelfUpdate.Stage(zipPath, "v1.14.0", stagingRoot));

            Assert.False(File.Exists(Path.Combine(stagingRoot, "pending.json")));
        }

        [Fact]
        public void Stage_ZipSlipEntry_ThrowsAndWritesNothingOutsideTarget()
        {
            Directory.CreateDirectory(_root);
            string zipPath = Path.Combine(_root, "evil.zip");
            using (var archive = ZipFile.Open(zipPath, ZipArchiveMode.Create))
            {
                archive.CreateEntry("addin-publish/setup.exe").Open().Dispose();
                using (var writer = new StreamWriter(archive.CreateEntry("addin-publish/INSTALLIEREN.cmd").Open()))
                {
                    writer.Write("@echo off\r\n");
                }
                archive.CreateEntry("../evil.txt").Open().Dispose();
            }

            string stagingRoot = Path.Combine(_root, "staging-target-4");
            Assert.Throws<InvalidOperationException>(
                () => AddinSelfUpdate.Stage(zipPath, "v1.14.0", stagingRoot));

            // Nichts ausserhalb des Zielverzeichnisses geschrieben.
            Assert.False(File.Exists(Path.Combine(_root, "evil.txt")));
        }

        [Fact]
        public void ClearStaging_IsIdempotent_DoesNotThrowWhenNothingExists()
        {
            string stagingRoot = Path.Combine(_root, "never-created");
            AddinSelfUpdate.ClearStaging(stagingRoot);
            AddinSelfUpdate.ClearStaging(stagingRoot); // zweimal — muss idempotent sein
        }

        [Fact]
        public void ClearStaging_RemovesExistingDirectory()
        {
            string stagingRoot = Path.Combine(_root, "to-clear");
            Directory.CreateDirectory(stagingRoot);
            File.WriteAllText(Path.Combine(stagingRoot, "leftover.txt"), "x");

            AddinSelfUpdate.ClearStaging(stagingRoot);

            Assert.False(Directory.Exists(stagingRoot));
        }
    }
}
