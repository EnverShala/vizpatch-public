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
    /// Tests fuer den Anhang-Upload (DND-01/02/03, 260731-u3m):
    /// <see cref="ChatClient.BuildUploadRequest"/> und
    /// <see cref="ChatClient.UploadAttachmentAsync(Stream, string, string, CancellationToken)"/>.
    /// </summary>
    public class ChatClientUploadTests
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
        public void BuildUploadRequest_UsesExactUploadUrl()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            using (var content = new MemoryStream(Encoding.UTF8.GetBytes("dummy")))
            {
                var req = client.BuildUploadRequest(content, "bericht.pdf", "sess-1");
                Assert.Equal("https://vizpatch.lan:8000/chat/default/upload",
                    req.RequestUri.ToString());
                Assert.Equal(HttpMethod.Post, req.Method);
            }
        }

        [Fact]
        public void BuildUploadRequest_EncodesAgentIdInPath()
        {
            var settings = SampleSettings();
            settings.AgentId = "team/eins raum#1";
            using (var client = new ChatClient(settings, new NoopHandler()))
            using (var content = new MemoryStream(Encoding.UTF8.GetBytes("dummy")))
            {
                var req = client.BuildUploadRequest(content, "bericht.pdf", "sess-1");
                Assert.Equal(
                    "https://vizpatch.lan:8000/chat/team%2Feins%20raum%231/upload",
                    req.RequestUri.AbsoluteUri);
            }
        }

        [Fact]
        public void BuildUploadRequest_SetsOriginHeaderToAddinOriginToken()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            using (var content = new MemoryStream(Encoding.UTF8.GetBytes("dummy")))
            {
                var req = client.BuildUploadRequest(content, "bericht.pdf", "sess-1");
                Assert.True(req.Headers.Contains("Origin"));
                Assert.Equal("https://outlook.office.com",
                    req.Headers.GetValues("Origin").Single());
            }
        }

        [Fact]
        public async Task BuildUploadRequest_MultipartBody_ContainsFileAndSessionIdParts()
        {
            using (var client = new ChatClient(SampleSettings(), new NoopHandler()))
            using (var content = new MemoryStream(Encoding.UTF8.GetBytes("PDF-INHALT")))
            {
                var req = client.BuildUploadRequest(content, "bericht.pdf", "sess-42");
                string body = await req.Content.ReadAsStringAsync();

                // .NET Framework quotet name=/filename= im Content-Disposition-Header
                // NICHT (anders als .NET Core) -> Assertions gegen das reale Format.
                Assert.Contains("name=file", body);
                Assert.Contains("filename=bericht.pdf", body);
                Assert.Contains("name=session_id", body);
                Assert.Contains("PDF-INHALT", body);
                Assert.Contains("sess-42", body);
            }
        }

        [Fact]
        public async Task UploadAttachmentAsync_SendsBasicAuthHeader()
        {
            var handler = new CapturingHandler(
                new StringContent("{\"ok\":true,\"dateiname\":\"a.pdf\",\"groesse\":10,\"mimetyp\":\"application/pdf\"}"),
                HttpStatusCode.OK);
            using (var client = new ChatClient(SampleSettings(), handler))
            using (var content = new MemoryStream(Encoding.UTF8.GetBytes("x")))
            using (var cts = new CancellationTokenSource())
            {
                await client.UploadAttachmentAsync(content, "a.pdf", "sess-1", cts.Token);

                Assert.NotNull(handler.LastRequest);
                var auth = handler.LastRequest.Headers.Authorization;
                Assert.NotNull(auth);
                Assert.Equal("Basic", auth.Scheme);
                string expected = Convert.ToBase64String(Encoding.UTF8.GetBytes("betreiber:geheim!"));
                Assert.Equal(expected, auth.Parameter);
            }
        }

        [Fact]
        public async Task UploadAttachmentAsync_200_ParsesAttachmentMeta()
        {
            var handler = new CapturingHandler(
                new StringContent("{\"ok\":true,\"dateiname\":\"bericht.pdf\",\"groesse\":12345,\"mimetyp\":\"application/pdf\"}"),
                HttpStatusCode.OK);
            using (var client = new ChatClient(SampleSettings(), handler))
            using (var content = new MemoryStream(Encoding.UTF8.GetBytes("x")))
            using (var cts = new CancellationTokenSource())
            {
                var meta = await client.UploadAttachmentAsync(content, "bericht.pdf", "sess-1", cts.Token);

                Assert.Equal("bericht.pdf", meta.Dateiname);
                Assert.Equal(12345, meta.Groesse);
                Assert.Equal("application/pdf", meta.Mimetyp);
            }
        }

        [Fact]
        public async Task UploadAttachmentAsync_413_ThrowsWithGermanDetailText()
        {
            var handler = new CapturingHandler(
                new StringContent("{\"detail\":\"Datei überschreitet das Limit von 15 MB.\"}"),
                (HttpStatusCode)413);
            using (var client = new ChatClient(SampleSettings(), handler))
            using (var content = new MemoryStream(Encoding.UTF8.GetBytes("x")))
            using (var cts = new CancellationTokenSource())
            {
                var ex = await Assert.ThrowsAsync<AttachmentUploadException>(
                    () => client.UploadAttachmentAsync(content, "gross.pdf", "sess-1", cts.Token));

                Assert.Equal(413, ex.StatusCode);
                Assert.Equal("Datei überschreitet das Limit von 15 MB.", ex.Message);
            }
        }

        [Fact]
        public async Task UploadAttachmentAsync_500_NonJsonBody_ThrowsWithNonEmptyMessage()
        {
            var handler = new CapturingHandler(
                new StringContent("<html><body>Internal Server Error</body></html>"),
                HttpStatusCode.InternalServerError);
            using (var client = new ChatClient(SampleSettings(), handler))
            using (var content = new MemoryStream(Encoding.UTF8.GetBytes("x")))
            using (var cts = new CancellationTokenSource())
            {
                var ex = await Assert.ThrowsAsync<AttachmentUploadException>(
                    () => client.UploadAttachmentAsync(content, "a.pdf", "sess-1", cts.Token));

                Assert.Equal(500, ex.StatusCode);
                Assert.False(string.IsNullOrEmpty(ex.Message));
            }
        }

        [Fact]
        public async Task UploadAttachmentAsync_200_UnusableBody_Throws()
        {
            var handler = new CapturingHandler(new StringContent("kein-json"), HttpStatusCode.OK);
            using (var client = new ChatClient(SampleSettings(), handler))
            using (var content = new MemoryStream(Encoding.UTF8.GetBytes("x")))
            using (var cts = new CancellationTokenSource())
            {
                var ex = await Assert.ThrowsAsync<AttachmentUploadException>(
                    () => client.UploadAttachmentAsync(content, "a.pdf", "sess-1", cts.Token));

                Assert.Equal(200, ex.StatusCode);
            }
        }

        // --- Test-Doubles (kopiert aus ChatClientRequestTests.cs — die dortigen
        // sind `private sealed` und nicht wiederverwendbar; die Duplikation ist
        // bewusst, kein Versehen.) --------------------------------------------

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
