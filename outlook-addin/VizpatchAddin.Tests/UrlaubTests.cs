using System;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>
    /// Urlaubs-Autoantwort im Add-in (URL-01..09): Request-Bau, Parsing und die
    /// clientseitige Zeitraum-Vorpruefung — gegen injizierte
    /// <see cref="HttpMessageHandler"/>, kein echtes Netzwerk.
    ///
    /// Der wichtigste Test hier ist
    /// <c>GetUrlaubAsync_FehlendeFelder_FallenAufSicherenWertZurueck</c>: bei der
    /// einzigen Funktion des Produkts, die selbstaendig Mails verschickt, darf
    /// eine unvollstaendig geparste Antwort niemals als „an" durchgehen.
    /// </summary>
    public class UrlaubTests
    {
        private static AddinSettings SampleSettings() => new AddinSettings
        {
            BackendUrl = "https://vizpatch.lan:8000/",
            AgentId = "default",
            Username = "betreiber",
            Password = "geheim!",
            AddinOriginToken = "https://outlook.office.com",
        };

        // --- Request-Bau ---------------------------------------------------

        [Fact]
        public void BuildUrlaubGetRequest_UsesExactUrlMethodAndOrigin()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildUrlaubGetRequest("default");
                Assert.Equal("https://vizpatch.lan:8000/addin/agents/default/urlaub",
                    req.RequestUri.ToString());
                Assert.Equal(HttpMethod.Get, req.Method);
                Assert.Contains("https://outlook.office.com", req.Headers.GetValues("Origin"));
            }
        }

        [Fact]
        public async Task BuildUrlaubSetRequest_SendetAlleVierFelder()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildUrlaubSetRequest("default", true, false, "2026-09-01", "2026-09-14");
                Assert.Equal(HttpMethod.Post, req.Method);
                string body = await req.Content.ReadAsStringAsync();
                Assert.Contains("enabled=true", body);
                Assert.Contains("dry_run=false", body);
                Assert.Contains("von=2026-09-01", body);
                Assert.Contains("bis=2026-09-14", body);
                Assert.Contains("https://outlook.office.com", req.Headers.GetValues("Origin"));
            }
        }

        [Fact]
        public async Task BuildUrlaubSetRequest_OhneVorlage_SchicktWederMarkerNochText()
        {
            // RUECKWAERTSKOMPATIBILITAET und Schutz vor versehentlichem Loeschen:
            // wer die Vorlage nicht angefasst hat, darf sie nicht ueberschreiben.
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildUrlaubSetRequest("default", true, false, "2026-09-01", "2026-09-14");
                string body = await req.Content.ReadAsStringAsync();
                Assert.DoesNotContain("vorlage", body);
            }
        }

        [Fact]
        public async Task BuildUrlaubSetRequest_MitVorlage_SchicktMarkerUndText()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildUrlaubSetRequest(
                    "default", true, false, "2026-09-01", "2026-09-14", "Bin weg");
                string body = await req.Content.ReadAsStringAsync();
                Assert.Contains("vorlage_gesetzt=true", body);
                Assert.Contains("Bin+weg", body);
            }
        }

        [Fact]
        public async Task BuildUrlaubSetRequest_LeereVorlage_SchicktMarkerMitLeeremText()
        {
            // Ausdruecklich geleert heisst geleert — der Marker macht das vom
            // Fall "nicht mitgeschickt" unterscheidbar.
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildUrlaubSetRequest(
                    "default", false, true, "", "", "");
                string body = await req.Content.ReadAsStringAsync();
                Assert.Contains("vorlage_gesetzt=true", body);
                Assert.Contains("vorlage=", body);
            }
        }

        [Fact]
        public void BuildUrlaubGetRequest_EscapedAgentId()
        {
            // Gelesen wird AbsoluteUri, nicht ToString(): letzteres liefert eine
            // teilweise DEKODIERTE Darstellung und wuerde die Escape-Pruefung
            // still wertlos machen.
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildUrlaubGetRequest("a b/c");
                Assert.DoesNotContain(" ", req.RequestUri.AbsoluteUri);
                Assert.Contains("a%20b%2Fc", req.RequestUri.AbsoluteUri);
            }
        }

        // --- Parsing -------------------------------------------------------

        [Fact]
        public async Task GetUrlaubAsync_LiestAlleFelder()
        {
            var handler = new CapturingHandler(new StringContent(
                "{\"enabled\":true,\"dry_run\":false,\"von\":\"2026-09-01\","
                + "\"bis\":\"2026-09-14\",\"vorlage_vorhanden\":true}"));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var stand = await client.GetUrlaubAsync("default", CancellationToken.None);
                Assert.True(stand.Enabled);
                Assert.False(stand.DryRun);
                Assert.Equal("2026-09-01", stand.Von);
                Assert.Equal("2026-09-14", stand.Bis);
                Assert.True(stand.VorlageVorhanden);
            }
        }

        [Fact]
        public async Task GetUrlaubAsync_FehlendeFelder_FallenAufSicherenWertZurueck()
        {
            // Der Kern-Sicherheitstest: was nicht eindeutig „an" sagt, ist aus —
            // und der Probelauf bleibt an.
            var handler = new CapturingHandler(new StringContent("{}"));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var stand = await client.GetUrlaubAsync("default", CancellationToken.None);
                Assert.False(stand.Enabled);
                Assert.True(stand.DryRun);
                Assert.Equal("", stand.Von);
                Assert.Equal("", stand.Bis);
                Assert.False(stand.VorlageVorhanden);
            }
        }

        [Fact]
        public async Task GetUrlaubAsync_FalscherTyp_FaelltAufSicherenWertZurueck()
        {
            var handler = new CapturingHandler(new StringContent(
                "{\"enabled\":\"true\",\"dry_run\":1,\"von\":42}"));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var stand = await client.GetUrlaubAsync("default", CancellationToken.None);
                Assert.False(stand.Enabled);   // String "true" ist kein bool
                Assert.True(stand.DryRun);     // Zahl 1 ist kein bool
                Assert.Equal("", stand.Von);
            }
        }

        [Fact]
        public async Task SetUrlaubAsync_BadRequest_ReichtDieDeutscheMeldungWeiter()
        {
            var handler = new CapturingHandler(
                new StringContent("{\"detail\":\"Das Bis-Datum des Urlaubs liegt vor dem Von-Datum.\"}"),
                HttpStatusCode.BadRequest);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var ex = await Assert.ThrowsAsync<InvalidOperationException>(
                    () => client.SetUrlaubAsync("default", true, false, "2026-09-14", "2026-09-01",
                        CancellationToken.None));
                Assert.Contains("Bis-Datum", ex.Message);
            }
        }

        [Fact]
        public void ExtractDetail_OhneJson_LiefertNeutralenSatz()
        {
            // Der rohe Koerper darf NIE in einer MessageBox landen.
            string text = ChatClient.ExtractDetail("<html>500 Internal Server Error</html>");
            Assert.DoesNotContain("html", text);
            Assert.Contains("abgelehnt", text);
        }

        [Fact]
        public void ExtractDetail_LeeresDetail_LiefertNeutralenSatz()
        {
            Assert.Contains("abgelehnt", ChatClient.ExtractDetail("{\"detail\":\"\"}"));
        }

        // --- Zeitraum-Vorpruefung ------------------------------------------

        [Fact]
        public void Zeitraum_LeerUndAus_IstGueltig()
        {
            // So laesst sich ein vergangener Urlaub austragen.
            Assert.Null(UrlaubHintText.ValidateZeitraum("", "", false));
        }

        [Fact]
        public void Zeitraum_LeerUndAn_WirdAbgelehnt()
        {
            // Sonst saehe der Betreiber einen gesetzten Haken und bekaeme nie
            // eine Antwort verschickt.
            Assert.NotNull(UrlaubHintText.ValidateZeitraum("", "", true));
        }

        [Fact]
        public void Zeitraum_NurEinDatum_WirdAbgelehnt()
        {
            Assert.NotNull(UrlaubHintText.ValidateZeitraum("2026-09-01", "", false));
            Assert.NotNull(UrlaubHintText.ValidateZeitraum("", "2026-09-14", false));
        }

        [Theory]
        [InlineData("01.09.2026", "14.09.2026")]
        [InlineData("09/01/2026", "09/14/2026")]
        [InlineData("2026-13-01", "2026-13-14")]
        [InlineData("2026-02-31", "2026-03-01")]
        [InlineData("morgen", "uebermorgen")]
        public void Zeitraum_NichtIso_WirdAbgelehnt(string von, string bis)
        {
            // Kein Raten: ein um Monate verschobener Urlaub verschickt entweder
            // keine oder ganz falsche Autoantworten.
            string fehler = UrlaubHintText.ValidateZeitraum(von, bis, true);
            Assert.NotNull(fehler);
            Assert.Contains("JJJJ-MM-TT", fehler);
        }

        [Fact]
        public void Zeitraum_BisVorVon_WirdAbgelehnt()
        {
            Assert.NotNull(UrlaubHintText.ValidateZeitraum("2026-09-14", "2026-09-01", true));
        }

        [Fact]
        public void Zeitraum_GleicherTag_IstGueltig()
        {
            // Ein Tag Urlaub ist ein Urlaub.
            Assert.Null(UrlaubHintText.ValidateZeitraum("2026-09-01", "2026-09-01", true));
        }

        [Fact]
        public void Zeitraum_MitLeerzeichen_WirdGetrimmt()
        {
            Assert.Null(UrlaubHintText.ValidateZeitraum(" 2026-09-01 ", " 2026-09-14 ", true));
        }

        [Fact]
        public void Zeitraum_Null_VerhaeltSichWieLeer()
        {
            Assert.Null(UrlaubHintText.ValidateZeitraum(null, null, false));
            Assert.NotNull(UrlaubHintText.ValidateZeitraum(null, null, true));
        }

        // --- Hinweistexte ---------------------------------------------------

        [Fact]
        public void HinweisOhneVorlage_SagtDassNichtsVerschicktWird()
        {
            // Die eine Warnung, die der Betreiber sehen MUSS, bevor er faehrt.
            Assert.Contains("NICHTS", UrlaubHintText.MissingTemplate);
            Assert.Contains("## Urlaub", UrlaubHintText.MissingTemplate);
        }

        [Fact]
        public void HinweisBereit_NenntWerKeineAntwortBekommt()
        {
            Assert.Contains("Newsletter", UrlaubHintText.Ready);
            Assert.Contains("No-Reply", UrlaubHintText.Ready);
            Assert.Contains("Kopie-Feld", UrlaubHintText.Ready);
        }

        [Fact]
        public void HinweisBereit_WiederholtDenWarnblockNicht()
        {
            // Der rote Warnblock oberhalb der Felder sagt bereits, dass im
            // Zeitraum jeder Absender genau EINMAL die Vorlage bekommt. Stand
            // dasselbe auch hier, las der Betreiber den zweiten Absatz nicht
            // mehr — und damit auch nicht den einen Satz, der nur hier steht
            // (gemeldet am 2026-08-24: "die 2 Erklaerungen sind im Prinzip die
            // selben").
            Assert.DoesNotContain("genau einmal", UrlaubHintText.Ready);
            Assert.DoesNotContain("## Urlaub", UrlaubHintText.Ready);
        }

        // --- Test-Doubles ---------------------------------------------------

        private sealed class NoopHandler : HttpMessageHandler
        {
            protected override Task<HttpResponseMessage> SendAsync(
                HttpRequestMessage request, CancellationToken cancellationToken)
            {
                throw new InvalidOperationException("NoopHandler darf nicht senden");
            }
        }

        private sealed class CapturingHandler : HttpMessageHandler
        {
            private readonly HttpContent _body;
            private readonly HttpStatusCode _status;
            public HttpRequestMessage LastRequest { get; private set; }

            public CapturingHandler(HttpContent body, HttpStatusCode status = HttpStatusCode.OK)
            {
                _body = body;
                _status = status;
            }

            protected override Task<HttpResponseMessage> SendAsync(
                HttpRequestMessage request, CancellationToken cancellationToken)
            {
                LastRequest = request;
                return Task.FromResult(new HttpResponseMessage(_status) { Content = _body });
            }
        }
    }
}
