using Newtonsoft.Json.Linq;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// DTO fuer die Antwort von <c>GET {BackendUrl}/addin/update-check</c>
    /// (260728-irn, Stufe 1). Der Semver-Vergleich passiert bewusst
    /// SERVER-seitig (D-1/D-4) — der Client konsumiert nur das Boolean
    /// <see cref="UpdateAvailable"/>, damit im Add-in keine zweite, driftende
    /// Vergleichslogik entsteht und **kein GitHub-Token auf den Kundenrechner**
    /// kommt.
    /// </summary>
    public sealed class UpdateInfo
    {
        public string Status { get; set; } = "";
        public string Installed { get; set; } = "";
        public string Latest { get; set; } = "";
        public string Message { get; set; } = "";
        public bool UpdateAvailable { get; set; } = false;

        /// <summary>
        /// Parst den JSON-Body defensiv (Newtonsoft, wie im Rest des Core bereits
        /// in Gebrauch): jedes String-Feld fehlertolerant per Cast mit
        /// `?? ""`-Fallback, `update_available` nur uebernommen, wenn das
        /// JSON-Token tatsaechlich ein Boolean ist (sonst `false`, kein Crash).
        /// Bei kaputtem/leerem Body wirft `JObject.Parse` — der Aufrufer
        /// (<see cref="ChatClient.GetUpdateInfoAsync"/>) behandelt das wie
        /// "kein Update".
        /// </summary>
        public static UpdateInfo FromJson(string body)
        {
            var parsed = JObject.Parse(body);
            var info = new UpdateInfo
            {
                Status = (string)parsed["status"] ?? "",
                Installed = (string)parsed["installed"] ?? "",
                Latest = (string)parsed["latest"] ?? "",
                Message = (string)parsed["message"] ?? "",
            };

            var updateAvailableToken = parsed["update_available"];
            info.UpdateAvailable = updateAvailableToken != null
                && updateAvailableToken.Type == JTokenType.Boolean
                && (bool)updateAvailableToken;

            return info;
        }
    }
}
