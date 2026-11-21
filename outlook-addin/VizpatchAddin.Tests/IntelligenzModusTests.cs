using System;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>
    /// Intelligenz-Modus im Add-in (Betreiber-Wunsch 2026-08-24): Request-Bau und
    /// Parsing gegen injizierte <see cref="HttpMessageHandler"/>, kein echtes
    /// Netzwerk. Muster 1:1 wie <see cref="UrlaubTests"/>.
    ///
    /// Der wichtigste Test hier ist
    /// <c>GetIntelligenzModusAsync_UnbekannterWert_WirdStandard</c>: der
    /// GUENSTIGE Modus ist der sichere Rückfall. Eine falsch angezeigte Stufe
    /// „hoch" würde einen Betreiber glauben lassen, er zahle bereits dafür — und
    /// eine falsch angezeigte Stufe „standard" verleitet zu einem Klick, der
    /// nichts ändert.
    /// </summary>
    public class IntelligenzModusTests
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
        public void BuildIntelligenzGetRequest_UsesExactUrlMethodAndOrigin()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildIntelligenzGetRequest("default");
                Assert.Equal("https://vizpatch.lan:8000/addin/agents/default/intelligenz",
                    req.RequestUri.ToString());
                Assert.Equal(HttpMethod.Get, req.Method);
                Assert.Contains("https://outlook.office.com", req.Headers.GetValues("Origin"));
            }
        }

        [Fact]
        public void BuildIntelligenzGetRequest_EscapedAgentId()
        {
            // Gelesen wird AbsoluteUri, nicht ToString(): letzteres liefert eine
            // teilweise DEKODIERTE Darstellung und wuerde die Escape-Pruefung
            // still wertlos machen (dieselbe Falle wie in UrlaubTests).
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildIntelligenzGetRequest("a b/c");
                Assert.DoesNotContain(" ", req.RequestUri.AbsoluteUri);
                Assert.Contains("a%20b%2Fc", req.RequestUri.AbsoluteUri);
            }
        }

        [Theory]
        [InlineData("hoch", "hoch")]
        [InlineData("standard", "standard")]
        [InlineData("HOCH", "standard")]   // nicht exakt "hoch" -> guenstiger Wert
        [InlineData("turbo", "standard")]
        [InlineData("", "standard")]
        [InlineData(null, "standard")]
        public async Task BuildIntelligenzSetRequest_NormalisiertDenWert(string eingabe, string erwartet)
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildIntelligenzSetRequest("default", eingabe);
                Assert.Equal(HttpMethod.Post, req.Method);
                string body = await req.Content.ReadAsStringAsync();
                Assert.Equal("modus=" + erwartet, body);
            }
        }

        // --- Parsing -------------------------------------------------------

        [Fact]
        public async Task GetIntelligenzModusAsync_LiestHoch()
        {
            var handler = new CapturingHandler(new StringContent("{\"modus\": \"hoch\"}"));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                Assert.Equal("hoch",
                    await client.GetIntelligenzModusAsync("default", CancellationToken.None));
            }
        }

        [Theory]
        [InlineData("{\"modus\": \"standard\"}")]
        [InlineData("{\"modus\": \"ganz-hoch\"}")]
        [InlineData("{\"modus\": 7}")]
        [InlineData("{\"etwas_anderes\": \"hoch\"}")]
        [InlineData("{}")]
        public async Task GetIntelligenzModusAsync_UnbekannterWert_WirdStandard(string json)
        {
            var handler = new CapturingHandler(new StringContent(json));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                Assert.Equal("standard",
                    await client.GetIntelligenzModusAsync("default", CancellationToken.None));
            }
        }

        [Fact]
        public async Task SetIntelligenzModusAsync_BadRequest_WirftMitServerMeldung()
        {
            var handler = new CapturingHandler(
                new StringContent("{\"detail\": \"modus muss 'standard' oder 'hoch' sein.\"}"),
                HttpStatusCode.BadRequest);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var ex = await Assert.ThrowsAsync<InvalidOperationException>(
                    () => client.SetIntelligenzModusAsync("default", "hoch", CancellationToken.None));
                Assert.Contains("standard", ex.Message);
            }
        }

        [Fact]
        public async Task SetIntelligenzModusAsync_ServerFehler_WirftHttpFehler()
        {
            var handler = new CapturingHandler(
                new StringContent("kaputt"), HttpStatusCode.InternalServerError);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                await Assert.ThrowsAsync<HttpRequestException>(
                    () => client.SetIntelligenzModusAsync("default", "hoch", CancellationToken.None));
            }
        }

        // --- Test-Doubles --------------------------------------------------

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
