using System;
using System.Collections.Generic;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// Was das Update-Fenster in einem gegebenen Zustand anzeigt, ob es
    /// geschlossen werden darf und ob der Zustand ein Fehlschlag ist — reine
    /// Logik ohne UI, damit das Verhalten testbar ist (Muster
    /// <see cref="UpdateDisplayDecision"/>).
    ///
    /// Hintergrund (Betreiber-Wunsch 2026-08-22): der Update-Ablauf lief
    /// bisher IM Einstellungen-Dialog. Nach dem Update stand der Betreiber
    /// dort vor „Speichern" und „Abbrechen" — was nach einem abgeschlossenen
    /// Update sinnlos und verwirrend ist („habe ich das Update jetzt
    /// gespeichert? Bricht Abbrechen das Update ab?"). Der Ablauf zieht
    /// deshalb in ein eigenes, nicht wegklickbares Fenster mit genau EINEM
    /// Knopf: „OK", und der wird erst im Endzustand aktiv.
    ///
    /// <see cref="IsFinished"/> ist damit doppelt belegt und das ist Absicht:
    /// es steuert die Freigabe des OK-Knopfes UND die Schliessbarkeit des
    /// Fensters. Beide Bedingungen sind dieselbe Frage („ist der Vorgang
    /// vorbei?"), und zwei getrennte Flags waeren eine Gelegenheit, sie
    /// auseinanderlaufen zu lassen.
    /// </summary>
    public sealed class UpdateWindowState
    {
        private UpdateWindowState(string headline, string detail, bool isFinished, bool isFailure)
        {
            Headline = headline ?? "";
            Detail = detail ?? "";
            IsFinished = isFinished;
            IsFailure = isFailure;
        }

        /// <summary>Kurze Zustandszeile, fett dargestellt.</summary>
        public string Headline { get; }

        /// <summary>Erklaerender Text darunter (Fortschritt, Meldung, Hinweis).</summary>
        public string Detail { get; }

        /// <summary>
        /// Der Vorgang ist vorbei: OK-Knopf aktiv UND Fenster schliessbar.
        /// Solange false, darf das Fenster NICHT geschlossen werden — auch
        /// nicht per Esc, Alt+F4 oder Fenster-X (das es nicht gibt).
        /// </summary>
        public bool IsFinished { get; }

        /// <summary>Endzustand war ein Fehlschlag (rote Darstellung).</summary>
        public bool IsFailure { get; }

        // --- Zwischenzustaende (nie schliessbar) ---------------------------

        public static UpdateWindowState Starting()
        {
            return new UpdateWindowState(
                "Update wird gestartet …",
                "Bitte dieses Fenster offen lassen.",
                false,
                false);
        }

        /// <summary>
        /// Der Dialog wurde geoeffnet, waehrend serverseitig schon ein Update
        /// lief. Es wird NICHT neu gestartet, nur beobachtet.
        /// </summary>
        public static UpdateWindowState AttachedToRunning()
        {
            return new UpdateWindowState(
                "Ein Update läuft bereits",
                "Der Fortschritt wird hier angezeigt. Bitte dieses Fenster offen lassen.",
                false,
                false);
        }

        /// <summary>
        /// Verbindungsabbruch waehrend des Umschaltens — PLANMAESSIG (der
        /// Container startet neu) und deshalb ausdruecklich KEIN Fehlschlag.
        /// </summary>
        public static UpdateWindowState ServerRestarting()
        {
            return new UpdateWindowState(
                "Server startet neu — bitte warten …",
                "Das ist ein normaler Schritt des Updates, kein Fehler.",
                false,
                false);
        }

        // --- Endzustaende (OK wird aktiv) ---------------------------------

        /// <summary>Das Update liess sich gar nicht erst starten.</summary>
        public static UpdateWindowState StartFailed(string message)
        {
            return new UpdateWindowState(
                "Update konnte nicht gestartet werden",
                string.IsNullOrEmpty(message)
                    ? "Bitte die Verbindung zum Server prüfen."
                    : message,
                true,
                true);
        }

        /// <summary>
        /// Ueber das Gesamt-Budget hinweg kam keine Antwort mehr. Bewusst NICHT
        /// als Fehlschlag dargestellt: das Update kann serverseitig
        /// weiterlaufen, und eine falsche Fehlermeldung waere schlimmer als
        /// eine ehrliche Unklarheit.
        /// </summary>
        public static UpdateWindowState Unclear()
        {
            return new UpdateWindowState(
                "Zustand unklar",
                "Es kam keine Antwort mehr vom Server. Das Update kann dort "
                + "weiterlaufen — bitte die Verbindung prüfen und später erneut "
                + "nachsehen.",
                true,
                false);
        }

        /// <summary>
        /// Abbildung eines Serverstatus auf die Fensterdarstellung. Nur die
        /// drei terminalen Zustaende geben das Fenster frei.
        /// </summary>
        public static UpdateWindowState ForProgress(UpdateProgress progress)
        {
            if (progress == null)
            {
                return Starting();
            }

            if (progress.State == "success")
            {
                return new UpdateWindowState(
                    "Update abgeschlossen",
                    string.IsNullOrEmpty(progress.Message)
                        ? "Der Server läuft jetzt auf "
                          + (string.IsNullOrEmpty(progress.VersionTo) ? "der neuen Version" : progress.VersionTo)
                          + "."
                        : progress.Message,
                    true,
                    false);
            }

            if (progress.State == "rolled_back")
            {
                return new UpdateWindowState(
                    "Update fehlgeschlagen — Vorversion wiederhergestellt",
                    string.IsNullOrEmpty(progress.Message)
                        ? "Der Server läuft unverändert weiter wie vorher."
                        : progress.Message,
                    true,
                    true);
            }

            if (progress.State == "failed")
            {
                string detail = progress.Message ?? "";
                if (!string.IsNullOrEmpty(progress.RecoveryHint))
                {
                    detail = string.IsNullOrEmpty(detail)
                        ? progress.RecoveryHint
                        : detail + Environment.NewLine + progress.RecoveryHint;
                }
                return new UpdateWindowState(
                    "Update fehlgeschlagen",
                    string.IsNullOrEmpty(detail) ? "Kein Grund gemeldet." : detail,
                    true,
                    true);
            }

            // running / idle / unbekannt -> laeuft, Fenster bleibt zu.
            string schritt = StepLabel(progress.Step);
            string fortschritt = progress.Progress + " %";
            return new UpdateWindowState(
                "Update läuft …",
                string.IsNullOrEmpty(schritt) ? fortschritt : fortschritt + " — " + schritt,
                false,
                false);
        }

        // --- Schrittbezeichnungen -----------------------------------------
        //
        // Aus SettingsDialog hierher gezogen (Quick 260822-upd): der Text
        // gehoert zur Darstellungslogik und ist hier testbar, statt in einer
        // WinForms-Datei zu liegen, die auf dieser Maschine nur der Compiler
        // sieht.
        private static readonly Dictionary<string, string> _stepLabels =
            new Dictionary<string, string>
            {
                { "preparing", "Update wird vorbereitet" },
                { "download", "Paket wird geladen" },
                { "verify", "Prüfsumme wird geprüft" },
                { "extract", "Paket wird entpackt" },
                { "load_images", "Programmdateien werden entpackt" },
                { "launch_updater", "Umschalt-Vorgang wird gestartet" },
                { "stopping_agent", "Postfach-Verarbeitung wird beendet" },
                { "switching", "Umschalten" },
                { "health", "Funktionsprüfung" },
                { "rolling_back", "Vorversion wird wiederhergestellt" },
                { "done", "Abgeschlossen" },
            };

        /// <summary>
        /// Deutscher Text zu einem Server-Schritt. Ein unbekannter Schritt
        /// wird UNVERAENDERT durchgereicht statt verschluckt — sonst wuerde ein
        /// neuer Server-Schritt im Fenster als leere Zeile erscheinen.
        /// </summary>
        public static string StepLabel(string step)
        {
            if (string.IsNullOrEmpty(step))
            {
                return "";
            }
            string label;
            return _stepLabels.TryGetValue(step, out label) ? label : step;
        }
    }
}
