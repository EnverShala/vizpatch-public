using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>
    /// Regressionstests fuer den Bug aus der v1.19.0-Abnahme: der Update-Banner
    /// blieb nach einem erfolgreichen Self-Update bis zum Outlook-Neustart
    /// stehen, weil <c>CheckForUpdateBannerAsync()</c> keinen Ausblend-Zweig
    /// kannte.
    /// </summary>
    public class UpdateBannerDecisionTests
    {
        [Fact]
        public void NachErfolgreichemUpdate_BlendetBannerAus()
        {
            // DER Regressionsfall aus der v1.19.0-Abnahme: Server autoritativ
            // geprueft (status=ok), kein Update mehr verfuegbar -> Banner weg.
            var action = UpdateBannerDecision.Decide(
                checkSucceeded: true, status: "ok", updateAvailable: false, latest: "v1.19.0");
            Assert.Equal(UpdateBannerAction.Hide, action);
        }

        [Fact]
        public void VerfuegbaresUpdate_ZeigtBanner()
        {
            var action = UpdateBannerDecision.Decide(
                checkSucceeded: true, status: "ok", updateAvailable: true, latest: "v1.20.0");
            Assert.Equal(UpdateBannerAction.Show, action);
        }

        [Fact]
        public void NetzfehlerBeimCheck_LaesstBannerUnveraendert()
        {
            var action = UpdateBannerDecision.Decide(
                checkSucceeded: false, status: "ok", updateAvailable: false, latest: "");
            Assert.Equal(UpdateBannerAction.Leave, action);
        }

        [Theory]
        [InlineData("unreachable")]
        [InlineData("no_access")]
        [InlineData("unknown_installed")]
        [InlineData("")]
        public void NichtAutoritativerStatus_LaesstBannerUnveraendert(string status)
        {
            var action = UpdateBannerDecision.Decide(
                checkSucceeded: true, status: status, updateAvailable: false, latest: "");
            Assert.Equal(UpdateBannerAction.Leave, action);
        }

        [Fact]
        public void UpdateOhneVersionsangabe_LaesstBannerUnveraendert()
        {
            var action = UpdateBannerDecision.Decide(
                checkSucceeded: true, status: "ok", updateAvailable: true, latest: "");
            Assert.Equal(UpdateBannerAction.Leave, action);
        }

        [Fact]
        public void StatusNull_LaesstBannerUnveraendert()
        {
            var action = UpdateBannerDecision.Decide(
                checkSucceeded: true, status: null, updateAvailable: false, latest: "");
            Assert.Equal(UpdateBannerAction.Leave, action);
        }
    }
}
