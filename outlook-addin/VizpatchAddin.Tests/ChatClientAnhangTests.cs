using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>
    /// Tests fuer die Bild-Vorschau-Download-Strecke (Quick 260813-wea, Task 3):
    /// <see cref="ChatClient.BuildAnhangRequest"/> und
    /// <see cref="ChatClient.DownloadAnhangAsync"/>.
    /// </summary>
    public class ChatClientAnhangTests
    {
        private static AddinSettings SampleSettings() => new AddinSettings
        {
            BackendUrl = "https://vizpatch.lan:8000",
            AgentId = "default",
            Username = "betreiber",
            Password = "geheim!",
            AddinOriginToken = "https://outlook.office.com",
        };

        [Fact]
        public void BuildAnhangRequest_UsesExactUrlAndMethod()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildAnhangRequest("1234", "INBOX", 0);
                Assert.Equal(
                    "https://vizpatch.lan:8000/chat/default/anhang?uid=1234&ordner=INBOX&index=0",
                    req.RequestUri.ToString());
                Assert.Equal(HttpMethod.Get, req.Method);
            }
        }

        [Fact]
        public void BuildAnhangRequest_SetsOriginHeaderToAddinOriginToken()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildAnhangRequest("1", "INBOX", 0);
                Assert.True(req.Headers.Contains("Origin"));
                Assert.Equal("https://outlook.office.com", req.Headers.GetValues("Origin").Single());
            }
        }

        [Theory]
        [InlineData("[Gmail]/Drafts")]
        [InlineData("INBOX.Entwürfe")]
        public void BuildAnhangRequest_EncodesFolderWithSpecialCharacters(string ordner)
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            {
                var req = client.BuildAnhangRequest("1", ordner, 0);
                string expectedOrdner = Uri.EscapeDataString(ordner);
                Assert.Contains("ordner=" + expectedOrdner, req.RequestUri.AbsoluteUri);
            }
        }

        [Fact]
        public void BuildAnhangRequest_EncodesAgentIdInPath()
        {
            var settings = SampleSettings();
            settings.AgentId = "team/eins raum#1";
            using (var client = new ChatClient(settings, new NoopHandler()))
            {
                var req = client.BuildAnhangRequest("1", "INBOX", 0);
                Assert.StartsWith(
                    "https://vizpatch.lan:8000/chat/team%2Feins%20raum%231/anhang",
                    req.RequestUri.AbsoluteUri);
            }
        }

        [Fact]
        public async Task DownloadAnhangAsync_200_ReturnsBytes()
        {
            byte[] payload = Encoding.UTF8.GetBytes("bild-bytes");
            var handler = new CapturingHandler(new ByteArrayContent(payload), HttpStatusCode.OK);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var vorschau = new ImagePreview { Uid = "1", Ordner = "INBOX", Index = 0 };
                byte[] result = await client.DownloadAnhangAsync(
                    vorschau, ImagePreview.StandardMaxBytes, CancellationToken.None);
                Assert.Equal(payload, result);
            }
        }

        [Theory]
        [InlineData(HttpStatusCode.NotFound)]
        [InlineData((HttpStatusCode)413)]
        [InlineData((HttpStatusCode)415)]
        public async Task DownloadAnhangAsync_ErrorStatus_ThrowsWithStatusCodeAndServerText(HttpStatusCode status)
        {
            var handler = new CapturingHandler(
                new StringContent("{\"detail\":\"Fehlertext vom Server\"}"), status);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var vorschau = new ImagePreview { Uid = "1", Ordner = "INBOX", Index = 0 };
                var ex = await Assert.ThrowsAsync<AnhangDownloadException>(
                    () => client.DownloadAnhangAsync(vorschau, ImagePreview.StandardMaxBytes, CancellationToken.None));

                Assert.Equal((int)status, ex.StatusCode);
                Assert.Equal("Fehlertext vom Server", ex.Message);
            }
        }

        [Fact]
        public async Task DownloadAnhangAsync_ByteCeilingExceeded_AbortsInsteadOfUnboundedBuffer()
        {
            byte[] payload = new byte[2048];
            var handler = new CapturingHandler(new ByteArrayContent(payload), HttpStatusCode.OK);
            using (var client = new ChatClient(SampleSettings(), handler))
            {
                var vorschau = new ImagePreview { Uid = "1", Ordner = "INBOX", Index = 0 };
                var ex = await Assert.ThrowsAsync<AnhangDownloadException>(
                    () => client.DownloadAnhangAsync(vorschau, maxBytes: 100, ct: CancellationToken.None));

                Assert.Equal(0, ex.StatusCode);
                Assert.False(string.IsNullOrEmpty(ex.Message));
            }
        }

        // --- Test-Doubles (Muster ChatClientUploadTests.cs) ------------------------

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
