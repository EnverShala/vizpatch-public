using System.Collections.Generic;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// Zeilenbasierte SSE-Frame-Zustandsmaschine (RESEARCH.md Pattern 2 /
    /// „Don't Hand-Roll"). Zerlegt exakt das Server-Framing aus
    /// <c>webui/src/main.py</c> (<c>_sse_data_frame</c> + der <c>chat_send</c>-
    /// Generator):
    /// <list type="bullet">
    ///   <item>Text-Chunk: je Zeile <c>data: {line}</c>, Frame endet mit Leerzeile</item>
    ///   <item>Werkzeug: <c>event: tool</c> + <c>data: {label}</c></item>
    ///   <item>Abschluss: <c>event: done</c> + <c>data: </c></item>
    ///   <item>Fehler:   <c>event: error</c> + <c>data: {line}</c></item>
    /// </list>
    /// Rein string-basiert — kein Netzwerk-/Transport-Bezug (kein System.Net-Client),
    /// damit ohne Backend per xUnit testbar. .NET Framework 4.8 hat keinen eingebauten SSE-Parser
    /// (erst ab .NET 9), daher dieser kleine, an der SSE-Spec orientierte Parser.
    /// </summary>
    public sealed class SseLineParser
    {
        private const string DefaultEventType = "message";

        private string _eventType = DefaultEventType;
        private readonly List<string> _dataLines = new List<string>();

        /// <summary>
        /// Speist eine einzelne Zeile ein. Eine Leerzeile beendet den aktuellen
        /// Frame und liefert <c>(EventType, Data)</c> zurueck; alle anderen Zeilen
        /// liefern <c>null</c> (Frame noch nicht komplett). Mehrere <c>data:</c>-
        /// Zeilen desselben Frames werden mit <c>"\n"</c> verbunden. Nach
        /// <c>event:</c>/<c>data:</c> wird GENAU EIN fuehrendes Leerzeichen
        /// entfernt (SSE-Spec), nicht alle.
        /// <c>id:</c>/<c>retry:</c> und sonstige Felder werden ignoriert.
        ///
        /// Betreiber-Befund 2026-08-16 („er vergisst manchmal Leerzeichen"):
        /// hier stand frueher <c>TrimStart()</c>, das ALLE fuehrenden Leerzeichen
        /// frass. Der Server rahmt jeden Chunk als <c>"data: " + text</c> — ein
        /// Leerzeichen ist also reines Trennzeichen, jedes WEITERE gehoert zum
        /// Text. Beginnt ein Chunk an einer Wortgrenze (haeufig, seit die
        /// Tool-Schleife streamt: 13 statt 2 Text-Ereignisse pro Antwort), ging
        /// genau dieses Leerzeichen verloren und zwei Woerter klebten zusammen.
        /// Der Browser-Client machte es die ganze Zeit richtig
        /// (<c>chat.js</c>: <c>.replace(/^ /, '')</c>) — der Fehler trat also nur
        /// im Add-in auf, also genau dort, wo der Kunde arbeitet.
        /// </summary>
        public (string EventType, string Data)? Feed(string line)
        {
            if (string.IsNullOrEmpty(line))
            {
                return CompleteFrame();
            }

            if (line.StartsWith("event:"))
            {
                _eventType = StripOneLeadingSpace(line.Substring("event:".Length));
            }
            else if (line.StartsWith("data:"))
            {
                _dataLines.Add(StripOneLeadingSpace(line.Substring("data:".Length)));
            }
            // andere SSE-Felder (id:, retry:, Kommentarzeilen ":…") werden ignoriert.
            return null;
        }

        /// <summary>
        /// Entfernt GENAU EIN fuehrendes Leerzeichen (SSE-Spec), nie mehr.
        /// Bewusst kein <c>TrimStart()</c> und kein <c>Trim()</c>: alles nach dem
        /// Trennzeichen ist Nutztext, auch weitere Leerzeichen und jedes
        /// abschliessende Leerzeichen am Chunk-Ende.
        /// </summary>
        private static string StripOneLeadingSpace(string value)
        {
            if (value.Length > 0 && value[0] == ' ')
            {
                return value.Substring(1);
            }
            return value;
        }

        /// <summary>
        /// Schliesst einen ggf. offenen Frame am Stream-Ende ohne abschliessende
        /// Leerzeile ab (defensiv). Liefert den Frame oder <c>null</c>.
        /// </summary>
        public (string EventType, string Data)? Flush()
        {
            return CompleteFrame();
        }

        private (string EventType, string Data)? CompleteFrame()
        {
            // Nur dispatchen, wenn der Frame Inhalt trug (data-Zeilen ODER ein
            // gesetztes event:) — reine Leerzeilen zwischen Frames erzeugen nichts.
            bool hasContent = _dataLines.Count > 0 || _eventType != DefaultEventType;
            if (!hasContent)
            {
                Reset();
                return null;
            }

            var frame = (_eventType, string.Join("\n", _dataLines));
            Reset();
            return frame;
        }

        private void Reset()
        {
            _eventType = DefaultEventType;
            _dataLines.Clear();
        }
    }
}
