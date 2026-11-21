using System;
using System.Globalization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// Anhang-Metadaten-DTO (DND-03, 260731-u3m). Serialisiert EXAKT die Felder
    /// <c>dateiname</c>/<c>groesse</c>/<c>mimetyp</c>, die
    /// <c>webui/src/main.py::_parse_attachment_meta</c> erwartet — jede andere
    /// Benennung fiele serverseitig still auf einen leeren String bzw. 0 zurueck
    /// (Vorbild: <see cref="MailContext"/>). Traegt NIE den Dateiinhalt (D-96) —
    /// nur Name/Groesse/Typ landen im DATEN-Block des Prompts.
    /// </summary>
    public sealed class AttachmentMeta
    {
        [JsonProperty("dateiname")]
        public string Dateiname { get; set; } = "";

        [JsonProperty("groesse")]
        public long Groesse { get; set; } = 0;

        [JsonProperty("mimetyp")]
        public string Mimetyp { get; set; } = "";

        /// <summary>Serialisiert die drei Felder als JSON (Newtonsoft.Json).</summary>
        public string ToJson()
        {
            return JsonConvert.SerializeObject(this);
        }

        /// <summary>
        /// Parst die Server-Antwort defensiv: <c>null</c>/leer, ein Parse-Fehler
        /// oder ein Nicht-Objekt liefern <c>null</c> zurueck — das ist das Signal
        /// "Antwort unbrauchbar" fuer den Aufrufer (der daraus eine
        /// <see cref="AttachmentUploadException"/> macht). Fehlende Einzelfelder
        /// im ansonsten gueltigen Objekt fallen auf leeren String bzw. 0 zurueck.
        /// </summary>
        public static AttachmentMeta FromJson(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return null;
            }

            JObject parsed;
            try
            {
                parsed = JObject.Parse(json);
            }
            catch (JsonReaderException)
            {
                return null;
            }

            var groesseToken = parsed["groesse"];
            long groesse = groesseToken != null && groesseToken.Type == JTokenType.Integer
                ? (long)groesseToken
                : 0;

            return new AttachmentMeta
            {
                Dateiname = (string)parsed["dateiname"] ?? "",
                Groesse = groesse,
                Mimetyp = (string)parsed["mimetyp"] ?? "",
            };
        }

        /// <summary>
        /// Formatiert "name (Größe)" fuer die Chat-Notiz. Die Groessenformatierung
        /// nutzt bewusst FEST <c>de-DE</c> statt <c>CurrentCulture</c>: die
        /// Pane-Texte sind durchgaengig deutsch, und ein fester Kulturkreis macht
        /// die Ausgabe unabhaengig von der Windows-Spracheinstellung (und die
        /// Tests deterministisch).
        /// </summary>
        public string AnzeigeText()
        {
            return Dateiname + " (" + FormatGroesse(Groesse) + ")";
        }

        private static readonly CultureInfo DeDe = new CultureInfo("de-DE");

        private static string FormatGroesse(long bytes)
        {
            if (bytes < 1024)
            {
                return bytes.ToString(DeDe) + " B";
            }
            if (bytes < 1024 * 1024)
            {
                long kb = bytes / 1024;
                if (kb < 1) kb = 1;
                return kb.ToString(DeDe) + " KB";
            }
            double mb = bytes / 1048576.0;
            return mb.ToString("0.0", DeDe) + " MB";
        }
    }

    /// <summary>
    /// Eigener Ausnahmetyp fuer einen fehlgeschlagenen Anhang-Upload (Vorbild:
    /// <see cref="UpdateAlreadyRunningException"/> in <c>UpdateProgress.cs</c>).
    /// Trennt "Server hat die Datei abgelehnt" (413/400/404 — der <see cref="Message"/>-
    /// Text kommt vom Server und ist fuer den Betreiber gedacht) von einem
    /// generischen Netzfehler. <see cref="StatusCode"/> == 0 steht fuer "gar
    /// keine HTTP-Antwort erhalten".
    /// </summary>
    public sealed class AttachmentUploadException : Exception
    {
        public int StatusCode { get; }

        public AttachmentUploadException(int statusCode, string message) : base(message)
        {
            StatusCode = statusCode;
        }
    }
}
