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
    /// Request-/Parsing-Tests fuer den Update-Check (260728-irn, Stufe 1) gegen
    /// injizierte <see cref="HttpMessageHandler"/> — kein echtes Netzwerk.
    /// </summary>
    public class ChatClientUpdateCheckTests
    {
        private static AddinSettings SampleSettings() => new AddinSettings
        {
            BackendUrl = "https://vizpatch.lan:8000/",
            AgentId = "default",
            Username = "betreiber",
            Password = "geheim!",
            AddinOriginToken = "https://outlook.office.com",
        };

        [Fact]
        public void BuildUpdateCheckRequest_UsesExactUrlMethodAndOrigin()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildUpdateCheckRequest();
                // Trailing-Slash der BackendUrl wird getrimmt.
                Assert.Equal("https://vizpatch.lan:8000/addin/update-check",
                    req.RequestUri.ToString());
                Assert.Equal(HttpMethod.Get, req.Method);
                Assert.Contains("https://outlook.office.com", req.Headers.GetValues("Origin"));
            }
        }

        [Fact]
        public async Task GetUpdateInfoAsync_UpdateAvailable_ParsesAllFields()
        {
            var handler = new CapturingHandler(new StringContent(
                "{\"status\":\"ok\",\"installed\":\"v1.2.0\",\"latest\":\"v1.15.0\",\"update_available\":true,\"message\":\"\"}"));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var info = await client.GetUpdateInfoAsync(CancellationToken.None);
                Assert.True(info.UpdateAvailable);
                Assert.Equal("v1.15.0", info.Latest);
                Assert.Equal("ok", info.Status);
                Assert.Equal("https://vizpatch.lan:8000/addin/update-check",
                    handler.LastRequest.RequestUri.ToString());
            }
        }

        [Fact]
        public async Task GetUpdateInfoAsync_UpdateNotAvailable_ReturnsFalse()
        {
            var handler = new CapturingHandler(new StringContent(
                "{\"status\":\"ok\",\"installed\":\"v1.15.0\",\"latest\":\"v1.15.0\",\"update_available\":false,\"message\":\"\"}"));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var info = await client.GetUpdateInfoAsync(CancellationToken.None);
                Assert.False(info.UpdateAvailable);
            }
        }

        [Fact]
        public async Task GetUpdateInfoAsync_DegradedStatus_PassesThroughMessage()
        {
            // Jeder nicht-"ok"-Status wird identisch behandelt (kein Update) —
            // die Meldung reicht das Add-in nur durch. "unreachable" steht hier
            // stellvertretend fuer no_access/unknown_installed.
            var handler = new CapturingHandler(new StringContent(
                "{\"status\":\"unreachable\",\"installed\":\"v1.14.0\",\"latest\":null,\"update_available\":false,\"message\":\"Versions-Index war nicht erreichbar — Update-Prüfung übersprungen.\"}"));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var info = await client.GetUpdateInfoAsync(CancellationToken.None);
                Assert.False(info.UpdateAvailable);
                Assert.Equal("Versions-Index war nicht erreichbar — Update-Prüfung übersprungen.", info.Message);
                Assert.Equal("unreachable", info.Status);
            }
        }

        [Fact]
        public async Task GetUpdateInfoAsync_MissingOrNonBoolField_ReturnsFalse_NoCrash()
        {
            var handler = new CapturingHandler(new StringContent(
                "{\"status\":\"ok\",\"update_available\":\"nope\"}"));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var info = await client.GetUpdateInfoAsync(CancellationToken.None);
                Assert.False(info.UpdateAvailable);
                Assert.Equal("", info.Latest);
            }
        }

        [Fact]
        public async Task GetUpdateInfoAsync_EmptyBody_Throws()
        {
            var handler = new CapturingHandler(new StringContent(""));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                await Assert.ThrowsAnyAsync<Exception>(
                    () => client.GetUpdateInfoAsync(CancellationToken.None));
            }
        }

        [Fact]
        public async Task GetUpdateInfoAsync_Http500_Throws()
        {
            var handler = new CapturingHandler(new StringContent("err"), HttpStatusCode.InternalServerError);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                await Assert.ThrowsAsync<HttpRequestException>(
                    () => client.GetUpdateInfoAsync(CancellationToken.None));
            }
        }

        [Fact]
        public async Task GetUpdateInfoAsync_Http403_Throws()
        {
            var handler = new CapturingHandler(new StringContent("forbidden"), HttpStatusCode.Forbidden);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                await Assert.ThrowsAsync<HttpRequestException>(
                    () => client.GetUpdateInfoAsync(CancellationToken.None));
            }
        }

        // --- Test-Doubles (analog ChatClientRequestTests.cs, dort private) --------

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
                var resp = new HttpResponseMessage(_status) { Content = _body };
                return Task.FromResult(resp);
            }
        }
    }
}
