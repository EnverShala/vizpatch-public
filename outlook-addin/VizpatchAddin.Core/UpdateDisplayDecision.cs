namespace VizpatchAddin.Core
{
    /// <summary>
    /// Entscheidet, WAS der Einstellungen-Dialog im Update-Bereich zeigt.
    /// Reine Logik ohne UI — genau diese Priorisierung war der Bug aus der
    /// v1.17.0-Abnahme und ist deshalb hier unter Test gestellt.
    ///
    /// Der Fehler damals: Lag ein terminaler Update-Status vor (z. B. „success"
    /// vom vorherigen Lauf), zeigte der Dialog dessen Meldung und brach ab —
    /// ein inzwischen verfuegbares NEUES Update wurde nie geprueft. Da die
    /// Statusdatei nie zurueckgesetzt wird, blieb der Button ab dem zweiten
    /// Update dauerhaft grau: das Chat-Banner meldete „Update auf v1.17.0
    /// verfuegbar", der Dialog „Update auf v1.16.0 abgeschlossen".
    ///
    /// Regel: Ein laufendes Update hat Vorrang (es darf nicht unterbrochen
    /// werden). Danach gewinnt immer die frische Erkennung — ein terminaler
    /// Status ist nur die Nachwirkung des letzten Lauf.
    /// </summary>
    public enum UpdateDisplayMode
    {
        /// <summary>Update laeuft gerade — Fortschritt anzeigen, Button aus.</summary>
        Running,

        /// <summary>Neues Update verfuegbar — Button aktiv, Version nennen.</summary>
        OfferUpdate,

        /// <summary>Kein neues Update — Ergebnis des letzten Lauf anzeigen.</summary>
        LastResult,

        /// <summary>Weder Lauf noch Ergebnis — „Kein Update verfuegbar".</summary>
        Unavailable,
    }

    public static class UpdateDisplayDecision
    {
        /// <summary>
        /// <paramref name="progressState"/> ist der `state` aus
        /// <c>GET /addin/update-status</c> (`idle|running|success|failed|rolled_back`),
        /// <paramref name="progressStale"/> das gleichnamige Feld daraus (ein
        /// haengengebliebener Lauf zaehlt nicht als laufend).
        /// <paramref name="hasProgress"/> ist false, wenn der Status nicht
        /// abrufbar war.
        /// </summary>
        public static UpdateDisplayMode Decide(
            bool hasProgress,
            string progressState,
            bool progressStale,
            bool updateAvailable,
            string latest)
        {
            bool running = hasProgress && progressState == "running" && !progressStale;
            if (running)
            {
                return UpdateDisplayMode.Running;
            }

            // Frische Erkennung schlaegt jeden terminalen Altstatus.
            if (updateAvailable && !string.IsNullOrEmpty(latest))
            {
                return UpdateDisplayMode.OfferUpdate;
            }

            bool terminal = hasProgress
                && (progressState == "success"
                    || progressState == "failed"
                    || progressState == "rolled_back");
            return terminal ? UpdateDisplayMode.LastResult : UpdateDisplayMode.Unavailable;
        }
    }
}
