using System;
using Newtonsoft.Json.Linq;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// DTO fuer die Antwort von <c>GET {BackendUrl}/addin/update-status</c>
    /// (260728-m8g, Stufe 2, D-5). Der Client enthaelt bewusst KEINE eigene
    /// Fortschrittslogik — Wahrheitsquelle ist die Statusdatei auf dem Server
    /// (im <c>agent-data</c>-Volume), damit nach einem Outlook- ODER
    /// WebUI-Neustart derselbe Zustand sichtbar bleibt.
    /// </summary>
    public sealed class UpdateProgress
    {
        public string State { get; set; } = "";
        public string Step { get; set; } = "";
        public int Progress { get; set; } = 0;
        public string VersionFrom { get; set; } = "";
        public string VersionTo { get; set; } = "";
        public string Message { get; set; } = "";
        public string RecoveryHint { get; set; } = "";
        public bool Stale { get; set; } = false;

        /// <summary>Endzustand — der Update-Vorgang ist abgeschlossen (Erfolg,
        /// Rollback ODER endgueltiger Fehlschlag).</summary>
        public bool IsTerminal =>
            State == "success" || State == "rolled_back" || State == "failed";

        /// <summary>Ein Update laeuft aktuell (Server hat den Lock).</summary>
        public bool IsRunning => State == "running";

        /// <summary>
        /// Parst den JSON-Body defensiv (Newtonsoft, wie im Rest des Core in
        /// Gebrauch): jedes String-Feld per Cast mit <c>?? ""</c>-Fallback,
        /// <see cref="Progress"/> nur uebernommen wenn das JSON-Token ein
        /// Integer ist (sonst 0), <see cref="Stale"/> nur wenn Boolean (sonst
        /// false). Kein Feld darf einen Crash ausloesen — fehlende Felder
        /// sind der Normalfall bei einem noch nicht gestarteten Update
        /// (Server liefert das idle-Skelett). Bei kaputtem/leerem Body wirft
        /// <c>JObject.Parse</c> — der Aufrufer (Poll-Schleife) behandelt das
        /// wie "laeuft noch" (D-5).
        /// </summary>
        public static UpdateProgress FromJson(string body)
        {
            var parsed = JObject.Parse(body);
            var info = new UpdateProgress
            {
                State = (string)parsed["state"] ?? "",
                Step = (string)parsed["step"] ?? "",
                VersionFrom = (string)parsed["version_from"] ?? "",
                VersionTo = (string)parsed["version_to"] ?? "",
                Message = (string)parsed["message"] ?? "",
                RecoveryHint = (string)parsed["recovery_hint"] ?? "",
            };

            var progressToken = parsed["progress"];
            info.Progress = progressToken != null && progressToken.Type == JTokenType.Integer
                ? (int)progressToken
                : 0;

            var staleToken = parsed["stale"];
            info.Stale = staleToken != null && staleToken.Type == JTokenType.Boolean && (bool)staleToken;

            return info;
        }
    }

    /// <summary>
    /// Eigener Ausnahmetyp fuer HTTP 409 bei <c>POST /addin/update</c>
    /// (260728-m8g, D-5-Doppelklickschutz) — der Aufrufer muss "laeuft
    /// schon" von einem echten Fehler unterscheiden koennen, um bei einem
    /// bereits laufenden Update direkt ins Polling zu wechseln statt einen
    /// Fehlerdialog zu zeigen.
    /// </summary>
    public sealed class UpdateAlreadyRunningException : Exception
    {
        public UpdateAlreadyRunningException(string message) : base(message)
        {
        }
    }
}
