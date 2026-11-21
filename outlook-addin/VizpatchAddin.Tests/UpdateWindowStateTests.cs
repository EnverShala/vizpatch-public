using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>
    /// Tests fuer <see cref="UpdateWindowState"/> (Betreiber-Wunsch
    /// 2026-08-22): der Update-Vorgang laeuft in einem eigenen, nicht
    /// wegklickbaren Fenster mit genau einem „OK"-Knopf.
    ///
    /// Der Kern dieser Datei ist EINE Zusicherung: <c>IsFinished</c> ist genau
    /// dann true, wenn der Vorgang wirklich vorbei ist. Daran haengt beides —
    /// die Freigabe des OK-Knopfes UND die Schliessbarkeit des Fensters. Ein
    /// Fehler hier heisst entweder „Betreiber sitzt im Fenster fest" oder
    /// „Betreiber klickt das laufende Update weg".
    /// </summary>
    public class UpdateWindowStateTests
    {
        private static UpdateProgress Progress(
            string state, string step = "", int progress = 0,
            string message = "", string recoveryHint = "", string versionTo = "")
        {
            return new UpdateProgress
            {
                State = state,
                Step = step,
                Progress = progress,
                Message = message,
                RecoveryHint = recoveryHint,
                VersionTo = versionTo,
            };
        }

        // --- Zwischenzustaende: Fenster bleibt zu -------------------------

        [Fact]
        public void Starting_ist_nicht_abgeschlossen()
        {
            var state = UpdateWindowState.Starting();
            Assert.False(state.IsFinished);
            Assert.False(state.IsFailure);
            Assert.NotEqual("", state.Headline);
        }

        [Fact]
        public void AttachedToRunning_ist_nicht_abgeschlossen_und_sagt_dass_schon_eines_laeuft()
        {
            var state = UpdateWindowState.AttachedToRunning();
            Assert.False(state.IsFinished);
            Assert.Contains("läuft", state.Headline);
        }

        [Fact]
        public void ServerRestarting_ist_KEIN_fehlschlag()
        {
            // Der Verbindungsabbruch beim Umschalten ist planmaessig. Ihn rot
            // zu zeigen wuerde den Betreiber mitten im Update erschrecken.
            var state = UpdateWindowState.ServerRestarting();
            Assert.False(state.IsFinished);
            Assert.False(state.IsFailure);
        }

        [Theory]
        [InlineData("running")]
        [InlineData("idle")]
        [InlineData("")]
        [InlineData("etwas-neues-vom-server")]
        public void Laufende_und_unbekannte_zustaende_geben_das_fenster_nicht_frei(string state)
        {
            var result = UpdateWindowState.ForProgress(Progress(state));
            Assert.False(result.IsFinished);
        }

        [Fact]
        public void Null_progress_wird_wie_start_behandelt_statt_zu_werfen()
        {
            var result = UpdateWindowState.ForProgress(null);
            Assert.False(result.IsFinished);
        }

        [Fact]
        public void Laufender_zustand_nennt_prozent_und_schritt()
        {
            var result = UpdateWindowState.ForProgress(Progress("running", "download", 42));
            Assert.Contains("42 %", result.Detail);
            Assert.Contains("Paket wird geladen", result.Detail);
        }

        // --- Endzustaende: OK wird aktiv ---------------------------------

        [Fact]
        public void Success_gibt_das_fenster_frei_und_ist_kein_fehler()
        {
            var result = UpdateWindowState.ForProgress(
                Progress("success", message: "Server läuft auf v1.27.3"));
            Assert.True(result.IsFinished);
            Assert.False(result.IsFailure);
            Assert.Contains("v1.27.3", result.Detail);
        }

        [Fact]
        public void Success_ohne_meldung_nennt_die_zielversion()
        {
            var result = UpdateWindowState.ForProgress(Progress("success", versionTo: "v1.27.3"));
            Assert.True(result.IsFinished);
            Assert.Contains("v1.27.3", result.Detail);
        }

        [Fact]
        public void RolledBack_ist_ein_fehler_aber_sagt_dass_alles_weiterlaeuft()
        {
            var result = UpdateWindowState.ForProgress(Progress("rolled_back"));
            Assert.True(result.IsFinished);
            Assert.True(result.IsFailure);
            Assert.Contains("Vorversion", result.Headline);
        }

        [Fact]
        public void Failed_haengt_den_wiederherstellungs_hinweis_an()
        {
            var result = UpdateWindowState.ForProgress(
                Progress("failed", message: "Prüfsumme falsch", recoveryHint: "Paket erneut laden"));
            Assert.True(result.IsFinished);
            Assert.True(result.IsFailure);
            Assert.Contains("Prüfsumme falsch", result.Detail);
            Assert.Contains("Paket erneut laden", result.Detail);
        }

        [Fact]
        public void Failed_ohne_grund_bleibt_nicht_leer()
        {
            // Eine leere Fehlermeldung waere fuer den Betreiber dasselbe wie
            // gar keine Antwort.
            var result = UpdateWindowState.ForProgress(Progress("failed"));
            Assert.True(result.IsFinished);
            Assert.NotEqual("", result.Detail);
        }

        [Fact]
        public void StartFailed_gibt_das_fenster_frei()
        {
            // Sonst sitzt der Betreiber in einem Fenster fest, in dem nie
            // etwas passieren wird.
            var result = UpdateWindowState.StartFailed("Server nicht erreichbar");
            Assert.True(result.IsFinished);
            Assert.True(result.IsFailure);
            Assert.Contains("Server nicht erreichbar", result.Detail);
        }

        [Fact]
        public void StartFailed_ohne_meldung_bleibt_nicht_leer()
        {
            var result = UpdateWindowState.StartFailed("");
            Assert.True(result.IsFinished);
            Assert.NotEqual("", result.Detail);
        }

        [Fact]
        public void Unclear_gibt_frei_ist_aber_KEIN_fehlschlag()
        {
            // Das Update kann serverseitig weiterlaufen — es als Fehler zu
            // melden waere eine Falschaussage.
            var result = UpdateWindowState.Unclear();
            Assert.True(result.IsFinished);
            Assert.False(result.IsFailure);
            Assert.Contains("weiterlaufen", result.Detail);
        }

        // --- Schrittbezeichnungen ----------------------------------------

        [Theory]
        [InlineData("preparing", "Update wird vorbereitet")]
        [InlineData("switching", "Umschalten")]
        [InlineData("rolling_back", "Vorversion wird wiederhergestellt")]
        public void StepLabel_uebersetzt_bekannte_schritte(string step, string erwartet)
        {
            Assert.Equal(erwartet, UpdateWindowState.StepLabel(step));
        }

        [Fact]
        public void StepLabel_reicht_unbekannten_schritt_durch()
        {
            // Ein neuer Server-Schritt darf NICHT als leere Zeile erscheinen —
            // dann waere im Fenster nicht erkennbar, was gerade passiert.
            Assert.Equal("neuer_schritt", UpdateWindowState.StepLabel("neuer_schritt"));
        }

        [Fact]
        public void StepLabel_bei_leer_bleibt_leer()
        {
            Assert.Equal("", UpdateWindowState.StepLabel(""));
            Assert.Equal("", UpdateWindowState.StepLabel(null));
        }

        // --- Gegenprobe zum Kernkontrakt ---------------------------------

        [Fact]
        public void Genau_die_drei_terminalen_serverzustaende_geben_frei()
        {
            // Diese Aufzaehlung ist der Vertrag mit UpdateProgress.IsTerminal.
            // Laufen die beiden auseinander, wartet das Fenster entweder
            // ewig oder gibt zu frueh frei.
            foreach (var state in new[] { "success", "failed", "rolled_back" })
            {
                Assert.True(UpdateWindowState.ForProgress(Progress(state)).IsFinished, state);
                Assert.True(Progress(state).IsTerminal, state);
            }
            foreach (var state in new[] { "running", "idle", "" })
            {
                Assert.False(UpdateWindowState.ForProgress(Progress(state)).IsFinished, state);
                Assert.False(Progress(state).IsTerminal, state);
            }
        }
    }
}
