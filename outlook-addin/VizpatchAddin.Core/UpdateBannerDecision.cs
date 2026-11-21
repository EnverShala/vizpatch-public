namespace VizpatchAddin.Core
{
    /// <summary>
    /// Entscheidet, was der Update-Banner im Chat-Pane nach einem Check gegen
    /// <c>GET /addin/update-check</c> tun soll: zeigen, ausblenden oder
    /// unveraendert lassen. Reine Logik ohne UI — analog
    /// <see cref="UpdateDisplayDecision"/> — weil genau diese Priorisierung der
    /// Bug aus der v1.19.0-Abnahme war: der Banner blieb nach einem
    /// erfolgreichen Self-Update bis zum Outlook-Neustart stehen, es gab
    /// schlicht keinen Ausblend-Zweig.
    ///
    /// Warum es einen dritten Zustand <see cref="UpdateBannerAction.Leave"/>
    /// gibt und nicht nur Show/Hide: die Server-Antwort ist nur bei
    /// <c>status == "ok"</c> autoritativ. `unreachable` / `no_access` /
    /// `unknown_installed` liefern `update_available: false`, OBWOHL gar nicht
    /// geprueft wurde. Ein Ausblenden darauf waere eine Falschmeldung „alles
    /// aktuell" — typischerweise genau in dem Moment, in dem die WebUI nach
    /// einem Update noch neu startet. Ebenso jeder Netz-/TLS-/Timeout-Fehler
    /// beim Check selbst (<c>checkSucceeded == false</c>): kein Ergebnis ist
    /// kein Grund, eine bestehende Meldung zu verwerfen.
    ///
    /// Warum das nicht als if-Kette in der UI steht: gleiches Argument wie bei
    /// <see cref="UpdateDisplayDecision"/> — die Priorisierung ist der
    /// fehleranfaellige Teil und gehoert unter Test; <c>ChatView</c>
    /// (VSTO/WinForms) ist hier nicht testbar.
    /// </summary>
    public enum UpdateBannerAction
    {
        /// <summary>Banner sichtbar machen und Text auf die gemeldete Version setzen.</summary>
        Show,

        /// <summary>Banner ausblenden — autoritativ kein Update (mehr) verfuegbar.</summary>
        Hide,

        /// <summary>Zustand unveraendert lassen — kein UI-Zugriff.</summary>
        Leave,
    }

    public static class UpdateBannerDecision
    {
        /// <summary>
        /// <paramref name="checkSucceeded"/> ist false, wenn der Aufruf von
        /// <c>ChatClient.GetUpdateInfoAsync</c> geworfen hat (Netz/TLS/Timeout/
        /// Nicht-2xx). <paramref name="status"/>/<paramref name="updateAvailable"/>/
        /// <paramref name="latest"/> entsprechen <see cref="UpdateInfo"/>.
        /// </summary>
        public static UpdateBannerAction Decide(
            bool checkSucceeded,
            string status,
            bool updateAvailable,
            string latest)
        {
            if (!checkSucceeded)
            {
                return UpdateBannerAction.Leave;
            }

            if (status != "ok")
            {
                return UpdateBannerAction.Leave;
            }

            if (updateAvailable)
            {
                return string.IsNullOrEmpty(latest) ? UpdateBannerAction.Leave : UpdateBannerAction.Show;
            }

            return UpdateBannerAction.Hide;
        }
    }
}
