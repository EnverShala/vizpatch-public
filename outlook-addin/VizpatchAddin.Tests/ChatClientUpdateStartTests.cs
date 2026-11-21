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
    /// Request-/Parsing-Tests fuer den Self-Update-Start (260728-m8g, Stufe 2)
    /// gegen injizierte <see cref="HttpMessageHandler"/> — kein echtes
    /// Netzwerk.
    /// </summary>
    public class ChatClientUpdateStartTests
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
        public void BuildStartUpdateRequest_UsesExactUrlMethodAndOrigin()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildStartUpdateRequest("v1.14.0");
                Assert.Equal("https://vizpatch.lan:8000/addin/update", req.RequestUri.ToString());
                Assert.Equal(HttpMethod.Post, req.Method);
                Assert.Contains("https://outlook.office.com", req.Headers.GetValues("Origin"));
            }
        }

        [Fact]
        public async Task StartUpdateAsync_SendsAuthorizationBasicHeaderOnClient()
        {
            // Der Basic-Auth-Header wird im ChatClient-Konstruktor als
            // Default-Header auf den HttpClient gesetzt (siehe ChatClient.cs) —
            // er liegt also auf JEDEM Request, auch auf diesem destruktiven
            // Trigger, ohne dass der Aufrufer etwas dafuer tun muss.
            var handler = new CapturingHandler(new StringContent(""), HttpStatusCode.Accepted);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                await client.StartUpdateAsync("v1.14.0", CancellationToken.None);
                Assert.NotNull(handler.LastRequest.Headers.Authorization);
                Assert.Equal("Basic", handler.LastRequest.Headers.Authorization.Scheme);
            }
        }

        [Fact]
        public async Task BuildStartUpdateRequest_FormBodyContainsVersion()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildStartUpdateRequest("v1.14.0");
                string body = await req.Content.ReadAsStringAsync();
                Assert.Equal("version=v1.14.0", body);
            }
        }

        [Fact]
        public void BuildUpdateProgressRequest_UsesExactUrlMethodAndOrigin()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildUpdateProgressRequest();
                Assert.Equal("https://vizpatch.lan:8000/addin/update-status", req.RequestUri.ToString());
                Assert.Equal(HttpMethod.Get, req.Method);
                Assert.Contains("https://outlook.office.com", req.Headers.GetValues("Origin"));
            }
        }

        [Fact]
        public void BuildAddinInstallerRequest_UsesExactUrlMethodAndOrigin()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildAddinInstallerRequest();
                Assert.Equal("https://vizpatch.lan:8000/addin/installer", req.RequestUri.ToString());
                Assert.Equal(HttpMethod.Get, req.Method);
                Assert.Contains("https://outlook.office.com", req.Headers.GetValues("Origin"));
            }
        }

        [Fact]
        public async Task StartUpdateAsync_Http202_ReturnsWithoutException()
        {
            var handler = new CapturingHandler(new StringContent("{\"ok\":true,\"started\":true}"), HttpStatusCode.Accepted);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                await client.StartUpdateAsync("v1.14.0", CancellationToken.None);
                // Kein Wurf -> Erfolg.
            }
        }

        [Fact]
        public async Task StartUpdateAsync_Http409_ThrowsUpdateAlreadyRunningException()
        {
            var handler = new CapturingHandler(
                new StringContent("Es läuft bereits ein Update (Schritt: download)."),
                HttpStatusCode.Conflict);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var ex = await Assert.ThrowsAsync<UpdateAlreadyRunningException>(
                    () => client.StartUpdateAsync("v1.14.0", CancellationToken.None));
                Assert.Contains("Update", ex.Message);
            }
        }

        [Fact]
        public async Task StartUpdateAsync_Http401_Throws()
        {
            var handler = new CapturingHandler(new StringContent("unauthorized"), HttpStatusCode.Unauthorized);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                await Assert.ThrowsAsync<HttpRequestException>(
                    () => client.StartUpdateAsync("v1.14.0", CancellationToken.None));
            }
        }

        [Fact]
        public async Task StartUpdateAsync_Http503_Throws()
        {
            var handler = new CapturingHandler(new StringContent("kein token"), HttpStatusCode.ServiceUnavailable);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                await Assert.ThrowsAsync<HttpRequestException>(
                    () => client.StartUpdateAsync("v1.14.0", CancellationToken.None));
            }
        }

        [Fact]
        public async Task GetUpdateProgressAsync_ParsesAllFields()
        {
            var handler = new CapturingHandler(new StringContent(
                "{\"state\":\"running\",\"step\":\"download\",\"progress\":42," +
                "\"version_from\":\"v1.13.0\",\"version_to\":\"v1.14.0\"," +
                "\"message\":\"Paket wird geladen\",\"recovery_hint\":\"\",\"stale\":false}"));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var progress = await client.GetUpdateProgressAsync(CancellationToken.None);
                Assert.Equal("running", progress.State);
                Assert.Equal("download", progress.Step);
                Assert.Equal(42, progress.Progress);
                Assert.Equal("v1.13.0", progress.VersionFrom);
                Assert.Equal("v1.14.0", progress.VersionTo);
                Assert.False(progress.Stale);
                Assert.False(progress.IsTerminal);
                Assert.True(progress.IsRunning);
            }
        }

        [Theory]
        [InlineData("success")]
        [InlineData("rolled_back")]
        [InlineData("failed")]
        public void UpdateProgress_IsTerminal_TrueForEndStates(string state)
        {
            var progress = UpdateProgress.FromJson("{\"state\":\"" + state + "\"}");
            Assert.True(progress.IsTerminal);
        }

        [Theory]
        [InlineData("idle")]
        [InlineData("running")]
        public void UpdateProgress_IsTerminal_FalseForNonEndStates(string state)
        {
            var progress = UpdateProgress.FromJson("{\"state\":\"" + state + "\"}");
            Assert.False(progress.IsTerminal);
        }

        [Fact]
        public void UpdateProgress_MissingFields_UsesDefaults_NoCrash()
        {
            var progress = UpdateProgress.FromJson("{}");
            Assert.Equal("", progress.State);
            Assert.Equal("", progress.Step);
            Assert.Equal(0, progress.Progress);
            Assert.Equal("", progress.VersionFrom);
            Assert.Equal("", progress.VersionTo);
            Assert.Equal("", progress.Message);
            Assert.Equal("", progress.RecoveryHint);
            Assert.False(progress.Stale);
        }

        [Fact]
        public void UpdateProgress_ProgressAsString_DefaultsToZero_NoCrash()
        {
            var progress = UpdateProgress.FromJson("{\"progress\":\"unbekannt\"}");
            Assert.Equal(0, progress.Progress);
        }

        [Fact]
        public void UpdateProgress_ProgressMissing_DefaultsToZero()
        {
            var progress = UpdateProgress.FromJson("{\"state\":\"idle\"}");
            Assert.Equal(0, progress.Progress);
        }

        [Fact]
        public void UpdateProgress_StaleAsNonBoolean_DefaultsToFalse()
        {
            var progress = UpdateProgress.FromJson("{\"stale\":\"ja\"}");
            Assert.False(progress.Stale);
        }

        [Fact]
        public async Task GetUpdateProgressAsync_BrokenBody_Throws()
        {
            var handler = new CapturingHandler(new StringContent(""));
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                // Der Aufrufer (Poll-Schleife) behandelt jede Ausnahme hier als
                // "laeuft noch" (D-5) — diese Methode selbst wirft regulaer.
                await Assert.ThrowsAnyAsync<Exception>(
                    () => client.GetUpdateProgressAsync(CancellationToken.None));
            }
        }

        // --- Test-Doubles (analog ChatClientUpdateCheckTests.cs, dort private) ----

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
