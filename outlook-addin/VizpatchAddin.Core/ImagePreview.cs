using System;
using System.Globalization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// Bild-Vorschau-Referenz-DTO (Quick 260813-wea) — serialisiert EXAKT die
    /// Feldnamen, die <c>webui/src/main.py</c> im SSE-Ereignis <c>event: bild</c>
    /// sendet: <c>uid</c>/<c>ordner</c>/<c>index</c>/<c>dateiname</c>/<c>typ</c>/
    /// <c>groesse</c> (Vorbild fuer Aufbau/Doc-Kommentar-Ton: <see cref="AttachmentMeta"/>).
    /// Das DTO trägt NIE Bilddaten, nur eine Referenz — die Bytes holt der
    /// Aufrufer separat über <see cref="ChatClient.DownloadAnhangAsync"/>.
    /// </summary>
    public sealed class ImagePreview
    {
        /// <summary>
        /// Spiegelt den Server-Default <c>MAX_PREVIEW_MB=10</c>
        /// (<c>webui/src/chat_tools.py::_preview_size_limit_bytes</c>) — der
        /// Server bleibt die AUTORITATIVE Grenze (T-wea-05), dieser Wert
        /// erspart dem Client nur den sinnlosen Request/Download-Versuch bei
        /// einer offensichtlich zu großen Referenz.
        /// </summary>
        public const long StandardMaxBytes = 10L * 1024 * 1024;

        [JsonProperty("uid")]
        public string Uid { get; set; } = "";

        [JsonProperty("ordner")]
        public string Ordner { get; set; } = "";

        [JsonProperty("index")]
        public int Index { get; set; } = 0;

        [JsonProperty("dateiname")]
        public string Dateiname { get; set; } = "";

        [JsonProperty("typ")]
        public string Typ { get; set; } = "";

        [JsonProperty("groesse")]
        public long Groesse { get; set; } = 0;

        /// <summary>
        /// Parst das SSE-Ereignis defensiv: <c>null</c>/leer, ein Parse-Fehler
        /// oder ein Nicht-Objekt liefern <c>null</c> zurueck — das Signal
        /// "Ereignis unbrauchbar" fuer den Aufrufer (der es dann still
        /// verwirft, siehe <c>ChatView.cs</c> `case "bild":`). Fehlende
        /// Einzelfelder im ansonsten gueltigen Objekt fallen auf leeren
        /// String bzw. 0 zurueck (Muster <see cref="AttachmentMeta.FromJson"/>).
        /// </summary>
        public static ImagePreview FromJson(string json)
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

            var indexToken = parsed["index"];
            int index = indexToken != null && indexToken.Type == JTokenType.Integer
                ? (int)indexToken
                : 0;

            var groesseToken = parsed["groesse"];
            long groesse = groesseToken != null && groesseToken.Type == JTokenType.Integer
                ? (long)groesseToken
                : 0;

            return new ImagePreview
            {
                Uid = (string)parsed["uid"] ?? "",
                Ordner = (string)parsed["ordner"] ?? "",
                Index = index,
                Dateiname = (string)parsed["dateiname"] ?? "",
                Typ = (string)parsed["typ"] ?? "",
                Groesse = groesse,
            };
        }

        /// <summary>
        /// Formatiert "name (Größe)" fuer die Bildunterschrift in der
        /// Bild-Blase. Die Groessenformatierung nutzt bewusst FEST
        /// <c>de-DE</c> statt <c>CurrentCulture</c> — dasselbe Argument wie
        /// bei <see cref="AttachmentMeta.AnzeigeText"/> (durchgaengig
        /// deutsche Pane-Texte, deterministische Tests).
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

    /// <summary>Ergebnis von <see cref="ImagePreviewDecision.Pruefe"/>.</summary>
    public struct ImagePreviewErgebnis
    {
        public bool Anzeigen;
        public string Grund;
    }

    /// <summary>
    /// Reine, COM-freie Entscheidungsfunktion (Vorbild: <see cref="DropPayloadDecision"/>):
    /// zeigt die Pane ein Vorschaubild fuer diese <see cref="ImagePreview"/>, oder
    /// nicht? Client-seitiges GEGENSTUECK zur serverseitigen Magic-Byte-Autoritaet
    /// (T-wea-01/04) — der Server bleibt die autoritative Grenze, dieser Check
    /// spart dem Client nur den sinnlosen Request bei offensichtlich unbrauchbaren
    /// Referenzen (kaputtes JSON, unbekannter Typ, Größe außerhalb des Rahmens).
    /// </summary>
    public static class ImagePreviewDecision
    {
        // Allowlist, KEINE Blockliste (T-wea-01-Muster): nur Formate, die die
        // Chat-Pane als Bild rendern kann. Ein serverseitig NEU unterstütztes
        // Format müsste hier explizit ergänzt werden — sicherer als eine
        // Blockliste, die ein unbekanntes/gefährliches Format durchließe.
        private static readonly string[] ErlaubteTypen =
        {
            "image/jpeg", "image/png", "image/gif", "image/webp",
        };

        public static ImagePreviewErgebnis Pruefe(ImagePreview vorschau, long maxBytes)
        {
            if (vorschau == null)
            {
                return new ImagePreviewErgebnis { Anzeigen = false, Grund = "Keine Vorschau-Referenz." };
            }
            if (string.IsNullOrEmpty(vorschau.Uid))
            {
                return new ImagePreviewErgebnis { Anzeigen = false, Grund = "Keine uid in der Vorschau-Referenz." };
            }
            if (string.IsNullOrEmpty(vorschau.Typ) || Array.IndexOf(ErlaubteTypen, vorschau.Typ) < 0)
            {
                return new ImagePreviewErgebnis
                {
                    Anzeigen = false,
                    Grund = "Kein von der Vorschau unterstütztes Bildformat: " + (vorschau.Typ ?? ""),
                };
            }
            if (vorschau.Groesse <= 0)
            {
                return new ImagePreviewErgebnis { Anzeigen = false, Grund = "Ungültige Größenangabe." };
            }
            if (vorschau.Groesse > maxBytes)
            {
                return new ImagePreviewErgebnis
                {
                    Anzeigen = false,
                    Grund = "Anhang überschreitet das Vorschau-Limit.",
                };
            }
            return new ImagePreviewErgebnis { Anzeigen = true, Grund = "" };
        }
    }

    /// <summary>
    /// Eigener Ausnahmetyp fuer einen fehlgeschlagenen Anhang-Download
    /// (Vorbild: <see cref="AttachmentUploadException"/>). <see cref="StatusCode"/>
    /// == 0 steht fuer "gar keine HTTP-Antwort erhalten" bzw. "Byte-Deckel beim
    /// Streamen überschritten" (kein echter HTTP-Status in diesem Fall).
    /// </summary>
    public sealed class AnhangDownloadException : Exception
    {
        public int StatusCode { get; }

        public AnhangDownloadException(int statusCode, string message) : base(message)
        {
            StatusCode = statusCode;
        }
    }
}
