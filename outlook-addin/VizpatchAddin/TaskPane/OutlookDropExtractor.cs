using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using VizpatchAddin.Core;
using ComTypes = System.Runtime.InteropServices.ComTypes;

namespace VizpatchAddin.TaskPane
{
    /// <summary>
    /// COM-Extraktion eines Drag&amp;Drop-Anhangs aus Outlook (DND-01/02,
    /// 260731-u3m). Wandelt die WinForms-<c>IDataObject</c>-Nutzlast eines Drops
    /// in eine lokale <see cref="TempAttachment"/> um — entweder direkt eine
    /// Explorer-Datei (<c>DataFormats.FileDrop</c>) oder einen aus Outlook
    /// gezogenen Anhang/eine ganze Mail (FILEGROUPDESCRIPTOR(W) + FileContents).
    ///
    /// Diese Klasse ist bewusst NICHT in <c>VizpatchAddin.Core</c>: sie braucht
    /// echtes COM-Interop (STGMEDIUM/IStream), das ohne Outlook/Windows-Host
    /// weder baubar noch testbar ist (siehe Klassen-Doc von
    /// <see cref="DropPayloadDecision"/>/<see cref="FileGroupDescriptorParser"/>
    /// fuer den getesteten, COM-freien Anteil).
    ///
    /// Kein-Auto-Send (D-87): rein lesend. Es werden keinerlei Outlook-Schreib-,
    /// Versand-, Verschiebe- oder Loesch-APIs und keine Item-Erzeugung aufgerufen;
    /// zum Schreiben der Temp-Datei wird ausschliesslich
    /// <see cref="TempAttachmentFile.Write"/> benutzt.
    /// </summary>
    internal static class OutlookDropExtractor
    {
        /// <summary>
        /// Versucht, aus <paramref name="data"/> einen Anhang zu extrahieren.
        /// Liefert bei Erfolg <c>true</c> und den <see cref="TempAttachment"/>;
        /// bei jedem Fehler (nicht unterstuetzter Drop, kaputter Descriptor,
        /// leerer Inhalt, COM-Ausnahme) <c>false</c> und einen deutschen
        /// <paramref name="fehler"/>-Text. <paramref name="gefundeneDateien"/>
        /// meldet die Gesamtzahl der im Drop enthaltenen Dateien (Explorer:
        /// Laenge des Pfad-Arrays; Outlook: Anzahl der Descriptor-Eintraege) —
        /// der Aufrufer nutzt das fuer den "nur die erste Datei"-Hinweis, weil
        /// der Server genau EINEN Pending-Upload je Sitzung haelt.
        ///
        /// Die gesamte Methode ist try/catch-umschlossen: wir laufen im
        /// Outlook-Prozess — eine durchgereichte COM-Exception aus dem
        /// Drop-Handler wuerde Outlook mit sich reissen.
        /// </summary>
        public static bool TryExtract(
            IDataObject data, out TempAttachment anhang, out int gefundeneDateien, out string fehler)
        {
            anhang = null;
            gefundeneDateien = 0;
            fehler = null;

            try
            {
                var plan = DropPayloadDecision.Decide(data?.GetFormats());

                if (plan.Kind == DropPayloadKind.None)
                {
                    fehler = "Dieser Inhalt lässt sich nicht als Datei übernehmen.";
                    return false;
                }

                if (plan.Kind == DropPayloadKind.FileDrop)
                {
                    return TryExtractFileDrop(data, out anhang, out gefundeneDateien, out fehler);
                }

                return TryExtractFileContents(data, plan, out anhang, out gefundeneDateien, out fehler);
            }
            catch (Exception ex)
            {
                anhang = null;
                fehler = "Anhang konnte nicht gelesen werden: " + ex.Message;
                return false;
            }
        }

        /// <summary>Explorer-Zweig: den ersten tatsaechlich existierenden Pfad aus
        /// <c>DataFormats.FileDrop</c> nehmen (Verzeichnisse ueberspringen). KEINE
        /// Kopie — die Explorer-Datei liegt bereits vor, wird nur gelesen und darf
        /// danach NICHT geloescht werden (<c>IstTemporaer = false</c>).</summary>
        private static bool TryExtractFileDrop(
            IDataObject data, out TempAttachment anhang, out int gefundeneDateien, out string fehler)
        {
            anhang = null;
            fehler = null;

            var paths = data.GetData(DataFormats.FileDrop) as string[];
            gefundeneDateien = paths != null ? paths.Length : 0;

            string match = null;
            if (paths != null)
            {
                foreach (var p in paths)
                {
                    if (File.Exists(p))
                    {
                        match = p;
                        break;
                    }
                }
            }

            if (match == null)
            {
                fehler = "Es wurde keine Datei im Drop gefunden.";
                return false;
            }

            anhang = new TempAttachment
            {
                Pfad = match,
                Dateiname = TempAttachmentFile.SanitizeFileName(match),
                Groesse = new FileInfo(match).Length,
                IstTemporaer = false,
            };
            return true;
        }

        /// <summary>Outlook-Zweig: Descriptor-Puffer lesen, Dateinamen parsen,
        /// Inhalt von Index 0 in eine Temp-Datei schreiben.</summary>
        private static bool TryExtractFileContents(
            IDataObject data, DropPayloadPlan plan,
            out TempAttachment anhang, out int gefundeneDateien, out string fehler)
        {
            anhang = null;
            fehler = null;
            gefundeneDateien = 0;

            byte[] descriptorBytes = ExtractDescriptorBytes(data, plan.DescriptorFormat);
            var namen = FileGroupDescriptorParser.ParseFileNames(descriptorBytes, plan.DescriptorIsUnicode);
            if (namen.Count == 0)
            {
                fehler = "Der Name des Anhangs konnte nicht gelesen werden.";
                return false;
            }
            gefundeneDateien = namen.Count;

            var written = MaterializeFileContents(data, 0, namen[0]);
            if (written.Groesse == 0)
            {
                TempAttachmentFile.CleanupQuietly(written.Pfad);
                fehler = "Der Anhang war leer.";
                return false;
            }
            anhang = written;
            return true;
        }

        /// <summary>Liest den FILEGROUPDESCRIPTOR(W)-Puffer ueber die
        /// WinForms-<c>GetData</c>-Bequemlichkeit — das ist (anders als
        /// <see cref="ReadFileContents"/>) unproblematisch, weil der Descriptor
        /// immer als Ganzes (kein <c>lindex</c>-Element) angefragt wird.</summary>
        private static byte[] ExtractDescriptorBytes(IDataObject data, string format)
        {
            if (string.IsNullOrEmpty(format))
            {
                return null;
            }
            object raw = data.GetData(format);
            var stream = raw as MemoryStream;
            if (stream != null)
            {
                return stream.ToArray();
            }
            return raw as byte[];
        }

        /// <summary>
        /// Liest/materialisiert den Inhalt des <paramref name="index"/>-ten
        /// FileContents-Eintrags als fertige <see cref="TempAttachment"/>-Datei.
        /// Bevorzugt den echten COM-Weg ueber
        /// <see cref="ComTypes.IDataObject"/>: genau hierfuer braucht es COM — die
        /// WinForms-<c>GetData("FileContents")</c>-Bequemlichkeit fragt immer mit
        /// <c>lindex = -1</c> an, was <c>CFSTR_FILECONTENTS</c> per Definition
        /// nicht bedienen muss; bei Mehrfach-Anhaengen liefert sie deshalb nichts
        /// oder das falsche Element. Wirft der COM-Weg oder liefert er nichts,
        /// faellt die Methode auf die WinForms-Bequemlichkeit zurueck (Index 0
        /// bei genau einem Anhang funktioniert damit meist trotzdem). Ein
        /// IStorage (Quick 260801-2d4/Befund B, ganze Mails) laesst sich nicht
        /// sinnvoll als <see cref="Stream"/> durchreichen — deshalb liefert
        /// dieser Pfad direkt eine fertige <see cref="TempAttachment"/> statt
        /// eines Streams.
        /// </summary>
        private static TempAttachment MaterializeFileContents(IDataObject data, int index, string vorschlagsName)
        {
            string comFehler = null;
            var com = data as ComTypes.IDataObject;
            if (com != null)
            {
                try
                {
                    return MaterializeViaCom(com, index, vorschlagsName);
                }
                catch (Exception ex)
                {
                    // Fallback unten versuchen -- der COM-Weg ist die bevorzugte,
                    // aber nicht die einzige Route. Die Ursache MERKEN: schlaegt
                    // auch der Fallback fehl, ist die COM-Meldung (HRESULT) der
                    // einzige diagnostische Anhaltspunkt (Live-Befund 2026-08-01:
                    // ohne sie war nur "konnte nicht gelesen werden" sichtbar).
                    comFehler = ex.Message;
                }
            }

            var fallback = data.GetData("FileContents") as MemoryStream;
            if (fallback != null)
            {
                return TempAttachmentFile.Write(fallback, vorschlagsName);
            }

            throw new InvalidOperationException(
                "Der Inhalt des Anhangs konnte nicht gelesen werden."
                + (comFehler != null ? " (" + comFehler + ")" : ""));
        }

        private static TempAttachment MaterializeViaCom(ComTypes.IDataObject com, int index, string vorschlagsName)
        {
            var fmt = new ComTypes.FORMATETC
            {
                cfFormat = (short)DataFormats.GetFormat("FileContents").Id,
                ptd = IntPtr.Zero,
                dwAspect = ComTypes.DVASPECT.DVASPECT_CONTENT,
                lindex = index,
                // Quick 260801-2d4/Befund B: Outlook liefert GANZE Mails als
                // Compound-Document (.msg) AUSSCHLIESSLICH ueber TYMED_ISTORAGE;
                // fehlte das Bit in der Anfrage, antwortete GetData mit
                // DV_E_FORMATETC ("Ungueltige FORMATETC-Struktur", Live-Test
                // 2026-08-01) — der Anhang-Drop funktionierte deshalb, der
                // Mail-Drop nicht.
                tymed = ComTypes.TYMED.TYMED_ISTREAM | ComTypes.TYMED.TYMED_HGLOBAL | ComTypes.TYMED.TYMED_ISTORAGE,
            };

            ComTypes.STGMEDIUM medium;
            com.GetData(ref fmt, out medium);
            // ALLES nach GetData in try/finally mit ReleaseStgMedium: ohne das
            // leckt der Drop Speicher/Handles im Outlook-Prozess.
            try
            {
                if (medium.tymed == ComTypes.TYMED.TYMED_ISTREAM)
                {
                    using (var stream = ReadFromIStreamMedium(medium))
                    {
                        return TempAttachmentFile.Write(stream, vorschlagsName);
                    }
                }
                if (medium.tymed == ComTypes.TYMED.TYMED_HGLOBAL)
                {
                    using (var stream = ReadFromHGlobalMedium(medium))
                    {
                        return TempAttachmentFile.Write(stream, vorschlagsName);
                    }
                }
                if (medium.tymed == ComTypes.TYMED.TYMED_ISTORAGE)
                {
                    // Ein IStorage schreibt sich nicht ueber TempAttachmentFile.Write
                    // (das braucht einen Stream) -- StgCreateDocfile schreibt direkt
                    // in eine Datei, siehe MaterializeStorage.
                    return MaterializeStorage(medium.unionmember, vorschlagsName);
                }
                throw new InvalidOperationException("Unerwartetes TYMED: " + medium.tymed);
            }
            finally
            {
                ReleaseStgMedium(ref medium);
            }
        }

        /// <summary>
        /// Materialisiert ein via <c>TYMED_ISTORAGE</c> geliefertes Quell-IStorage
        /// (Outlooks Compound-Document-Form einer GANZEN Mail) als gueltige
        /// <c>.msg</c>-Datei: <c>StgCreateDocfile</c> legt das ZIEL an (der Pfad
        /// MUSS frei sein — daher <see cref="TempAttachmentFile.ReserveTempPath"/>
        /// statt <see cref="TempAttachmentFile.Write"/>), <c>CopyTo</c> kopiert
        /// die komplette Storage-Struktur, <c>Commit</c> schreibt sie durch. Das
        /// Ergebnis laeuft danach ueber EXAKT denselben Upload-/Cleanup-Pfad wie
        /// jeder andere Drop (<c>ChatView.UploadDroppedAsync</c> loescht
        /// temporaere Dateien im <c>finally</c>).
        /// </summary>
        private static TempAttachment MaterializeStorage(IntPtr unionmember, string vorschlagsName)
        {
            string pfad = TempAttachmentFile.ReserveTempPath(vorschlagsName, out string dateiname);

            IStorage quelle = null;
            IStorage ziel = null;
            try
            {
                quelle = (IStorage)Marshal.GetObjectForIUnknown(unionmember);
                ziel = StgCreateDocfile(pfad, StgmCreateReadWriteExclusive, 0);
                quelle.CopyTo(0, null, IntPtr.Zero, ziel);
                ziel.Commit(0);
            }
            catch
            {
                // Halbfertige Zieldatei nicht liegen lassen -- der aeussere
                // try/catch in TryExtract macht daraus die deutsche Fehlernotiz.
                TempAttachmentFile.CleanupQuietly(pfad);
                throw;
            }
            finally
            {
                // Freigabe-Disziplin wie im bestehenden IStream-Pfad: beide RCWs
                // hier freigeben, BEVOR der Aufrufer (MaterializeViaCom) im
                // eigenen finally ReleaseStgMedium ausfuehrt.
                if (ziel != null)
                {
                    try { Marshal.ReleaseComObject(ziel); }
                    catch { /* bewusst geschluckt */ }
                }
                if (quelle != null)
                {
                    try { Marshal.ReleaseComObject(quelle); }
                    catch { /* bewusst geschluckt */ }
                }
            }

            return new TempAttachment
            {
                Pfad = pfad,
                // Der Descriptor liefert bei Mail-Drops bereits einen auf .msg
                // endenden Namen (den setzt Outlook); Sanitizing/Endung laufen
                // unveraendert ueber TempAttachmentFile.
                Dateiname = dateiname,
                Groesse = new FileInfo(pfad).Length,
                IstTemporaer = true,
            };
        }

        private static Stream ReadFromIStreamMedium(ComTypes.STGMEDIUM medium)
        {
            object obj = Marshal.GetObjectForIUnknown(medium.unionmember);
            var comStream = obj as ComTypes.IStream;
            if (comStream == null)
            {
                throw new InvalidOperationException("Kein IStream im STGMEDIUM erhalten.");
            }
            try
            {
                return ReadFromIStream(comStream);
            }
            finally
            {
                try { Marshal.ReleaseComObject(comStream); }
                catch { /* bewusst geschluckt */ }
            }
        }

        /// <summary>Kopiert den IStream-Inhalt in 64-KB-Bloecken in einen
        /// <see cref="MemoryStream"/>. Die gelesene Byte-Zahl wird ueber einen
        /// kurzlebigen <c>Marshal.AllocHGlobal(4)</c>-Puffer ausgelesen (die
        /// COM-<c>IStream.Read</c>-Signatur verlangt einen Zeiger statt eines
        /// <c>out int</c>).</summary>
        private static Stream ReadFromIStream(ComTypes.IStream stream)
        {
            // KEIN stream.Stat() vorab: Outlooks Anhang-IStreams implementieren
            // Stat teils nicht (E_NOTIMPL) — der Aufruf riss den kompletten
            // COM-Pfad, obwohl Read problemlos funktioniert haette; die Groesse
            // wird ohnehin nicht gebraucht (Read-bis-0-Schleife).
            var result = new MemoryStream();
            byte[] buffer = new byte[65536];
            IntPtr bytesReadPtr = Marshal.AllocHGlobal(sizeof(int));
            try
            {
                while (true)
                {
                    stream.Read(buffer, buffer.Length, bytesReadPtr);
                    int bytesRead = Marshal.ReadInt32(bytesReadPtr);
                    if (bytesRead <= 0)
                    {
                        break;
                    }
                    result.Write(buffer, 0, bytesRead);
                }
            }
            finally
            {
                Marshal.FreeHGlobal(bytesReadPtr);
            }

            result.Position = 0;
            return result;
        }

        private static Stream ReadFromHGlobalMedium(ComTypes.STGMEDIUM medium)
        {
            IntPtr ptr = GlobalLock(medium.unionmember);
            if (ptr == IntPtr.Zero)
            {
                throw new InvalidOperationException("GlobalLock auf den Anhang-Puffer fehlgeschlagen.");
            }
            try
            {
                int size = (int)GlobalSize(medium.unionmember).ToUInt32();
                byte[] bytes = new byte[size];
                Marshal.Copy(ptr, bytes, 0, size);
                return new MemoryStream(bytes);
            }
            finally
            {
                GlobalUnlock(medium.unionmember);
            }
        }

        [DllImport("ole32.dll")]
        private static extern void ReleaseStgMedium(ref ComTypes.STGMEDIUM medium);

        [DllImport("kernel32.dll")]
        private static extern IntPtr GlobalLock(IntPtr hMem);

        [DllImport("kernel32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GlobalUnlock(IntPtr hMem);

        [DllImport("kernel32.dll")]
        private static extern UIntPtr GlobalSize(IntPtr hMem);

        // --- Quick 260801-2d4 (FIX-B1): TYMED_ISTORAGE-Materialisierung -----------

        /// <summary>STGM_CREATE (0x1000) | STGM_READWRITE (0x2) |
        /// STGM_SHARE_EXCLUSIVE (0x10) — <c>StgCreateDocfile</c> legt die
        /// Zieldatei damit selbst neu an (der Pfad muss frei sein, siehe
        /// <see cref="TempAttachmentFile.ReserveTempPath"/>) und oeffnet sie
        /// exklusiv zum Schreiben.</summary>
        private const int StgmCreateReadWriteExclusive = 0x1012;

        [DllImport("ole32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
        // PreserveSig = false: ein HRESULT-Fehler kommt als COMException und
        // laeuft in den bestehenden catch-Fehlerpfad (MaterializeStorage/
        // MaterializeFileContents), statt still einen Fehlercode zurueckzugeben.
        private static extern IStorage StgCreateDocfile(string pwcsName, int grfMode, int reserved);

        /// <summary>
        /// Minimale COM-Interop-Deklaration von IStorage (IID
        /// {0000000B-0000-0000-C000-000000000046}) — NUR fuer
        /// <see cref="MaterializeStorage"/> gebraucht (CopyTo + Commit). Alle 15
        /// Vtable-Slots MUESSEN in der EXAKTEN Original-Reihenfolge deklariert
        /// sein (1 CreateStream, 2 OpenStream, 3 CreateStorage, 4 OpenStorage,
        /// 5 CopyTo, 6 MoveElementTo, 7 Commit, 8 Revert, 9 EnumElements,
        /// 10 DestroyElement, 11 RenameElement, 12 SetElementTimes, 13 SetClass,
        /// 14 SetStateBits, 15 Stat), weil COM ueber den Vtable-INDEX aufruft,
        /// nicht ueber den Methodennamen — nur Slot 5 (CopyTo) und Slot 7
        /// (Commit) bekommen die echte Signatur, die uebrigen Slots sind reine
        /// Abstandshalter und werden NIRGENDS aufgerufen: ein Aufruf wuerde die
        /// falsche Methoden-Adresse treffen und den Stack zerlegen. Die
        /// Platzhalternamen sind bewusst neutral (kein "Move"/"Delete" als
        /// Methodenname) — nur die REIHENFOLGE zaehlt, siehe
        /// scripts/check-addin-no-autosend.sh (Deklarationen ohne fuehrenden
        /// Punkt sind fuer den Waechter ohnehin unproblematisch, neutrale Namen
        /// sind trotzdem die sichere Wahl).
        /// </summary>
        [ComImport]
        [Guid("0000000B-0000-0000-C000-000000000046")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IStorage
        {
            void Slot1_Ungenutzt();
            void Slot2_Ungenutzt();
            void Slot3_Ungenutzt();
            void Slot4_Ungenutzt();
            void CopyTo(int ciidExclude, Guid[] rgiidExclude, IntPtr snbExclude, IStorage pstgDest);
            void Slot6_Ungenutzt();
            void Commit(int grfCommitFlags);
            void Slot8_Ungenutzt();
            void Slot9_Ungenutzt();
            void Slot10_Ungenutzt();
            void Slot11_Ungenutzt();
            void Slot12_Ungenutzt();
            void Slot13_Ungenutzt();
            void Slot14_Ungenutzt();
            void Slot15_Ungenutzt();
        }
    }
}
