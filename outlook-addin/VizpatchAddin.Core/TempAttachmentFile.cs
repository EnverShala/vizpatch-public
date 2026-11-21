using System;
using System.IO;
using System.Text;

namespace VizpatchAddin.Core
{
    /// <summary>Ergebnis von <see cref="TempAttachmentFile.Write"/> bzw. einer
    /// aufgeloesten Explorer-Datei (siehe <c>OutlookDropExtractor</c>).</summary>
    public sealed class TempAttachment
    {
        public string Pfad;
        public string Dateiname;
        public long Groesse;

        /// <summary>true = die Datei liegt im Temp-Verzeichnis und MUSS nach dem
        /// Upload per <see cref="TempAttachmentFile.CleanupQuietly"/> entfernt
        /// werden; false = eine echte Explorer-Datei, die NIE geloescht wird.</summary>
        public bool IstTemporaer;
    }

    /// <summary>
    /// Dateinamen-Sanitizing + sicheres Schreiben eines gedroppten Anhangs in
    /// eine Temp-Datei (DND-01/02, 260731-u3m, T-u3m-01).
    ///
    /// <see cref="SanitizeFileName"/> hat zwei UNABHAENGIGE Gruende:
    /// (1) Sicherheit — der Name stammt aus einem Fremdprozess (Explorer/
    /// Outlook ueber die Zwischenablage) und wird zum Bilden eines lokalen
    /// Pfades benutzt; Traversal muss ausgeschlossen sein.
    /// (2) Protokoll — <c>.NET Framework</c> kodiert nicht-ASCII-Dateinamen im
    /// Content-Disposition-Header nach RFC 2047, was Pythons Multipart-Parser
    /// NICHT dekodiert (siehe <see cref="ChatClient.BuildUploadRequest"/>) — ein
    /// "Angebot Müller.pdf" kaeme sonst als <c>=?utf-8?B?…?=</c> beim Server an.
    /// </summary>
    public static class TempAttachmentFile
    {
        private const string DefaultName = "anhang";
        private const int MaxNameLength = 100;
        private const int MaxExtensionLength = 10;

        public static string SanitizeFileName(string proposed)
        {
            if (string.IsNullOrWhiteSpace(proposed))
            {
                return DefaultName;
            }

            // Pfadanteile entfernen: an '\' UND '/' splitten, nur das letzte
            // Segment behalten. NICHT Path.GetFileName: das wirft bei ungueltigen
            // Zeichen bzw. behandelt '/' unter Windows nicht zuverlaessig, und
            // der Name kommt aus einem fremden Prozess.
            string[] parts = proposed.Split('\\', '/');
            string name = parts[parts.Length - 1];

            name = TransliterateGerman(name);

            var sb = new StringBuilder(name.Length);
            var invalid = Path.GetInvalidFileNameChars();
            foreach (char c in name)
            {
                bool bad = c < 0x20 || c > 0x7E
                    || Array.IndexOf(invalid, c) >= 0
                    || c == ':' || c == '*' || c == '?' || c == '"' || c == '<' || c == '>' || c == '|';
                sb.Append(bad ? '_' : c);
            }

            string collapsed = CollapseUnderscores(sb.ToString()).Trim('_', ' ');
            if (collapsed.Length == 0)
            {
                return DefaultName;
            }

            if (collapsed.Length > MaxNameLength)
            {
                collapsed = TruncateKeepingExtension(collapsed);
            }

            return collapsed;
        }

        private static string TransliterateGerman(string s)
        {
            return s
                .Replace("ä", "ae").Replace("ö", "oe").Replace("ü", "ue")
                .Replace("Ä", "Ae").Replace("Ö", "Oe").Replace("Ü", "Ue")
                .Replace("ß", "ss");
        }

        private static string CollapseUnderscores(string s)
        {
            var sb = new StringBuilder(s.Length);
            bool lastWasUnderscore = false;
            foreach (char c in s)
            {
                if (c == '_')
                {
                    if (lastWasUnderscore) continue;
                    lastWasUnderscore = true;
                }
                else
                {
                    lastWasUnderscore = false;
                }
                sb.Append(c);
            }
            return sb.ToString();
        }

        private static string TruncateKeepingExtension(string name)
        {
            string ext = Path.GetExtension(name);
            if (ext.Length > MaxExtensionLength)
            {
                ext = "";
            }
            int baseLength = MaxNameLength - ext.Length;
            if (baseLength < 1) baseLength = 1;
            string basePart = name.Substring(0, Math.Min(name.Length - ext.Length, baseLength));
            return basePart + ext;
        }

        /// <summary>
        /// Reserviert einen Zielpfad unter <c>%TEMP%\vizpatch-addin-drop\</c>,
        /// OHNE die Datei anzulegen — fuer Konsumenten, die die Datei SELBST
        /// erzeugen (z.B. <c>StgCreateDocfile</c> beim ISTORAGE-Materialisieren
        /// in <c>OutlookDropExtractor</c>, das mit <c>STGM_CREATE</c> einen
        /// FREIEN Pfad braucht und ihn nicht ueberschreiben darf). Namens-
        /// Sanitizing und GUID-Praefix gelten identisch zu <see cref="Write"/> —
        /// der Name stammt aus einem Fremdprozess (T-u3m-01).
        /// </summary>
        public static string ReserveTempPath(string vorschlagsName, out string dateiname)
        {
            dateiname = SanitizeFileName(vorschlagsName);
            string dir = Path.Combine(Path.GetTempPath(), "vizpatch-addin-drop");
            Directory.CreateDirectory(dir);
            return Path.Combine(dir, Guid.NewGuid().ToString("N") + "_" + dateiname);
        }

        /// <summary>
        /// Schreibt <paramref name="content"/> STREAMEND in eine neue Datei unter
        /// <c>%TEMP%\vizpatch-addin-drop\</c>. Der Dateiname bekommt einen
        /// GUID-Praefix — das garantiert Kollisionsfreiheit UND macht die Datei
        /// unabhaengig davon, was der Fremdprozess als Namen behauptet.
        /// </summary>
        public static TempAttachment Write(Stream content, string vorschlagsName)
        {
            string path = ReserveTempPath(vorschlagsName, out string sanitized);

            long size;
            using (var dest = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                content.CopyTo(dest);
                size = dest.Length;
            }

            return new TempAttachment
            {
                Pfad = path,
                Dateiname = sanitized,
                Groesse = size,
                IstTemporaer = true,
            };
        }

        /// <summary>
        /// Loescht die Datei unter <paramref name="pfad"/> best-effort. Ein
        /// haengendes Handle darf den Chat nicht stoeren — die Datei liegt
        /// ohnehin im Temp-Verzeichnis. (<c>File.Delete(</c> steht bewusst auf
        /// der Allowlist von <c>scripts/check-addin-no-autosend.sh</c>.)
        /// </summary>
        public static void CleanupQuietly(string pfad)
        {
            if (string.IsNullOrEmpty(pfad))
            {
                return;
            }
            try
            {
                File.Delete(pfad);
            }
            catch
            {
                // bewusst geschluckt — best effort.
            }
        }
    }
}
