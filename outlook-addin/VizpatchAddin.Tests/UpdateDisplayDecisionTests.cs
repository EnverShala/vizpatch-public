using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>
    /// Regressionstests fuer den Bug aus der v1.17.0-Abnahme: ein terminaler
    /// Update-Status verdeckte ein neu verfuegbares Update, wodurch der
    /// „Jetzt updaten"-Button ab dem zweiten Update dauerhaft grau blieb.
    /// </summary>
    public class UpdateDisplayDecisionTests
    {
        [Fact]
        public void LaufendesUpdate_HatVorrangVorAllem()
        {
            var mode = UpdateDisplayDecision.Decide(
                hasProgress: true, progressState: "running", progressStale: false,
                updateAvailable: true, latest: "v1.17.0");
            Assert.Equal(UpdateDisplayMode.Running, mode);
        }

        [Fact]
        public void HaengendesUpdate_ZaehltNichtAlsLaufend()
        {
            var mode = UpdateDisplayDecision.Decide(
                hasProgress: true, progressState: "running", progressStale: true,
                updateAvailable: true, latest: "v1.17.0");
            Assert.Equal(UpdateDisplayMode.OfferUpdate, mode);
        }

        [Theory]
        [InlineData("success")]
        [InlineData("failed")]
        [InlineData("rolled_back")]
        public void NeuesUpdate_SchlaegtTerminalenAltstatus(string state)
        {
            // DER Regressionsfall: nach einem Update auf v1.16.0 stand
            // state=success in der Statusdatei; v1.17.0 war verfuegbar.
            // Vorher gewann der Altstatus und der Button blieb grau.
            var mode = UpdateDisplayDecision.Decide(
                hasProgress: true, progressState: state, progressStale: false,
                updateAvailable: true, latest: "v1.17.0");
            Assert.Equal(UpdateDisplayMode.OfferUpdate, mode);
        }

        [Theory]
        [InlineData("success")]
        [InlineData("failed")]
        [InlineData("rolled_back")]
        public void OhneNeuesUpdate_ZeigtErgebnisDesLetztenLauf(string state)
        {
            var mode = UpdateDisplayDecision.Decide(
                hasProgress: true, progressState: state, progressStale: false,
                updateAvailable: false, latest: "");
            Assert.Equal(UpdateDisplayMode.LastResult, mode);
        }

        [Fact]
        public void KeinStatusAbrufbar_UndKeinUpdate_IstUnavailable()
        {
            var mode = UpdateDisplayDecision.Decide(
                hasProgress: false, progressState: null, progressStale: false,
                updateAvailable: false, latest: "");
            Assert.Equal(UpdateDisplayMode.Unavailable, mode);
        }

        [Fact]
        public void KeinStatusAbrufbar_AberUpdateVerfuegbar_BietetAn()
        {
            var mode = UpdateDisplayDecision.Decide(
                hasProgress: false, progressState: null, progressStale: false,
                updateAvailable: true, latest: "v1.17.0");
            Assert.Equal(UpdateDisplayMode.OfferUpdate, mode);
        }

        [Fact]
        public void IdleStatus_OhneUpdate_IstUnavailable()
        {
            var mode = UpdateDisplayDecision.Decide(
                hasProgress: true, progressState: "idle", progressStale: false,
                updateAvailable: false, latest: "");
            Assert.Equal(UpdateDisplayMode.Unavailable, mode);
        }

        [Fact]
        public void UpdateAvailableAberOhneVersion_BietetNichtAn()
        {
            // Defensiv: update_available=true ohne `latest` waere eine kaputte
            // Backend-Antwort — dann gibt es nichts sinnvoll anzuzeigen.
            var mode = UpdateDisplayDecision.Decide(
                hasProgress: true, progressState: "idle", progressStale: false,
                updateAvailable: true, latest: "");
            Assert.Equal(UpdateDisplayMode.Unavailable, mode);
        }
    }
}
