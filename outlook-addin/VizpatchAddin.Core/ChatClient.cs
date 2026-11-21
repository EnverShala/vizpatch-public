using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Security;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// Thin-Client (D-82/D-84) gegen die bestehende Chat-API
    /// <c>POST {BackendUrl}/chat/{AgentId}/send</c>. Baut den Form-encoded
    /// Request (message/history/mail_context/session_id), setzt den
    /// CSRF-Origin-Header (RESEARCH.md Common Pitfall 1) und Basic-Auth, liest den
    /// <c>text/event-stream</c>-Body inkrementell (ResponseHeadersRead) und speist
    /// ihn zeilenweise in den <see cref="SseLineParser"/>. Jeder fertige Frame
    /// wird als <c>(EventType, Data)</c> an den Aufrufer-Callback gereicht.
    /// </summary>
    public sealed class ChatClient : IDisposable
    {
        private readonly AddinSettings _settings;
        private readonly HttpClient _http;

        /// <summary>Produktions-Konstruktor: baut den TLS-gescopten HttpClientHandler.</summary>
        public ChatClient(AddinSettings settings)
            : this(settings, BuildDefaultHandler(settings))
        {
        }

        /// <summary>
        /// Testbarer Konstruktor mit injizierbarem <see cref="HttpMessageHandler"/>
        /// (Request-Assertions ohne echtes Netzwerk).
        /// </summary>
        public ChatClient(AddinSettings settings, HttpMessageHandler handler)
        {
            _settings = settings ?? throw new ArgumentNullException(nameof(settings));
            _http = new HttpClient(handler)
            {
                // SSE-Stream ist langlebig -> kein Gesamt-Timeout (die einzelnen
                // Reads werden ueber den CancellationToken abgebrochen).
                Timeout = Timeout.InfiniteTimeSpan,
            };

            if (!string.IsNullOrEmpty(_settings.Username) || !string.IsNullOrEmpty(_settings.Password))
            {
                string raw = (_settings.Username ?? "") + ":" + (_settings.Password ?? "");
                string base64 = Convert.ToBase64String(Encoding.UTF8.GetBytes(raw));
                _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Basic", base64);
            }
        }

        /// <summary>
        /// Baut den TLS-Trust GESCOPED auf DIESEN einen Handler — niemals
        /// prozessweit/global gesetzt (Anti-Pattern, RESEARCH.md Common Pitfall 5).
        /// Thumbprint-Pinning ist Default; Blanket-Trust nur bei explizitem
        /// <see cref="AddinSettings.TrustAnyCertificate"/>.
        /// </summary>
        private static HttpClientHandler BuildDefaultHandler(AddinSettings settings)
        {
            var handler = new HttpClientHandler();
            handler.ServerCertificateCustomValidationCallback = (request, cert, chain, errors) =>
            {
                // Blanket-Trust: JEDES Zertifikat akzeptieren — bewusst nur, wenn
                // der Betreiber es im Settings-Dialog explizit aktiviert hat.
                if (settings.TrustAnyCertificate)
                {
                    return true;
                }
                // Pinning: exakter Thumbprint-Vergleich (Doppelpunkte/Spaces egal).
                if (!string.IsNullOrEmpty(settings.CertThumbprint) && cert != null)
                {
                    string want = settings.CertThumbprint
                        .Replace(":", "").Replace(" ", "").Trim();
                    return string.Equals(cert.GetCertHashString(), want,
                        StringComparison.OrdinalIgnoreCase);
                }
                // Sonst normale System-Zertifikatskette.
                return errors == SslPolicyErrors.None;
            };
            return handler;
        }

        /// <summary>
        /// Sendet den Chat-POST und ruft <paramref name="onFrame"/> fuer jeden
        /// eintreffenden SSE-Frame auf. Bewusst OHNE das await-Kontext-Detachment
        /// (RESEARCH.md Anti-Pattern) — die await-Fortsetzungen (und damit
        /// <paramref name="onFrame"/>) bleiben auf dem aufrufenden (UI-)Kontext,
        /// damit der Chat-Log direkt aktualisiert werden darf.
        /// </summary>
        public async Task StreamChatAsync(
            string message,
            IEnumerable<ChatTurn> history,
            MailContext mailContext,
            string sessionId,
            Action<string, string> onFrame,
            CancellationToken ct,
            AttachmentMeta attachmentMeta = null)
        {
            if (onFrame == null) throw new ArgumentNullException(nameof(onFrame));

            var request = BuildRequest(message, history, mailContext, sessionId, attachmentMeta);

            using (var response = await _http.SendAsync(
                request, HttpCompletionOption.ResponseHeadersRead, ct))
            {
                response.EnsureSuccessStatusCode();

                using (var stream = await response.Content.ReadAsStreamAsync())
                using (var reader = new StreamReader(stream, Encoding.UTF8))
                {
                    var parser = new SseLineParser();
                    // Terminierung AUSSCHLIESSLICH ueber den Rueckgabewert von
                    // ReadLineAsync() (== null am Stream-Ende). KEIN reader.EndOfStream:
                    // dessen Getter liest bei Bedarf synchron das naechste Byte aus dem
                    // Netzwerk-Stream vor und wuerde auf dem (bewusst nicht abgekoppelten)
                    // UI-Thread blockieren, bis das Backend das naechste Byte sendet.
                    // So bleibt der gesamte Lesepfad durchgehend asynchron.
                    string line;
                    while ((line = await reader.ReadLineAsync()) != null)
                    {
                        ct.ThrowIfCancellationRequested();

                        var frame = parser.Feed(line);
                        if (frame != null)
                        {
                            onFrame(frame.Value.EventType, frame.Value.Data);
                        }
                    }
                    var tail = parser.Flush();
                    if (tail != null)
                    {
                        onFrame(tail.Value.EventType, tail.Value.Data);
                    }
                }
            }
        }

        /// <summary>
        /// Baut die <see cref="HttpRequestMessage"/> (URL, Form-Felder,
        /// Origin-Header). Oeffentlich fuer deterministische Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildRequest(
            string message,
            IEnumerable<ChatTurn> history,
            MailContext mailContext,
            string sessionId,
            AttachmentMeta attachmentMeta = null)
        {
            // AgentId ist frei konfigurierbar -> als Pfadsegment enkodieren, damit
            // Leerzeichen/Sonderzeichen (/, #, ?) keine falsche URL erzeugen.
            string url = _settings.BackendUrl.TrimEnd('/')
                + "/chat/" + Uri.EscapeDataString(_settings.AgentId ?? "") + "/send";

            var fields = new List<KeyValuePair<string, string>>
            {
                new KeyValuePair<string, string>("message", message ?? ""),
                new KeyValuePair<string, string>(
                    "history",
                    JsonConvert.SerializeObject(history ?? Enumerable.Empty<ChatTurn>())),
                new KeyValuePair<string, string>(
                    "mail_context",
                    mailContext != null ? mailContext.ToJson() : ""),
                new KeyValuePair<string, string>("session_id", sessionId ?? ""),
            };

            // attachment_meta NUR anhaengen, wenn tatsaechlich ein Anhang gemerkt
            // ist (DND-03): der Server parst das Feld zwar defensiv, aber "gar
            // nicht senden" haelt den Request identisch zum bisherigen Verhalten
            // und macht die Rueckwaertskompatibilitaet an der Leitung sichtbar.
            if (attachmentMeta != null)
            {
                fields.Add(new KeyValuePair<string, string>(
                    "attachment_meta", attachmentMeta.ToJson()));
            }

            // Faehigkeits-Aushandlung (Quick 260813-wea, Task 3): dieses
            // Add-in KANN das SSE-Ereignis `event: bild` verarbeiten (case
            // "bild" in ChatView.cs) -- deshalb wird `bild` UNBEDINGT
            // mitgeschickt, nicht optional. Ein AELTERER Server ignoriert
            // unbekannte Formfelder (FastAPI parst nur explizit deklarierte
            // Felder), ein NEUERER Server liest daraus die Faehigkeit — die
            // Kompatibilitaet gilt in BEIDE Richtungen, deshalb ist keine
            // gleichschrittige Auslieferung von Server und Add-in noetig.
            fields.Add(new KeyValuePair<string, string>("client_features", "bild"));

            var request = new HttpRequestMessage(HttpMethod.Post, url)
            {
                Content = new FormUrlEncodedContent(fields),
            };

            // CSRF-Origin-Workaround (Common Pitfall 1): ein natives HttpClient
            // sendet standardmaessig KEINEN Origin-Header -> enforce_same_origin
            // wuerde den POST mit 403 abweisen, VOR der Auth-Pruefung. Der
            // AddinOriginToken ist in ADDIN_FRAME_ANCESTORS gelistet, sodass die
            // bestehende _origin_allowed_for_addin-Ausnahme greift.
            request.Headers.Add("Origin", _settings.AddinOriginToken);

            return request;
        }

        /// <summary>
        /// Baut den Anhang-Upload-Request (<c>POST {BackendUrl}/chat/{AgentId}/upload</c>,
        /// DND-01/02/03, 260731-u3m). Multipart-Body mit den Parts <c>file</c>
        /// (Streaminhalt + Dateiname) und <c>session_id</c>. Oeffentlich fuer
        /// deterministische Request-Assertions.
        ///
        /// ASCII-Erwartung des Dateinamens: <c>.NET Framework</c> kodiert
        /// nicht-ASCII-Dateinamen im Content-Disposition-Header nach RFC 2047
        /// (<c>=?utf-8?B?…?=</c>), was Pythons Multipart-Parser NICHT dekodiert.
        /// Der Aufrufer uebergibt deshalb einen bereits ASCII-normalisierten
        /// Namen (<c>TempAttachmentFile.SanitizeFileName</c>).
        /// </summary>
        public HttpRequestMessage BuildUploadRequest(Stream content, string fileName, string sessionId)
        {
            string url = _settings.BackendUrl.TrimEnd('/')
                + "/chat/" + Uri.EscapeDataString(_settings.AgentId ?? "") + "/upload";

            var multipart = new MultipartFormDataContent();

            var fileContent = new StreamContent(content);
            // Den echten MIME-Typ bestimmt ohnehin der Server per
            // mimetypes.guess_type — hier reicht ein neutraler Platzhalter.
            fileContent.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
            multipart.Add(fileContent, "file", fileName ?? "");
            multipart.Add(new StringContent(sessionId ?? ""), "session_id");

            var request = new HttpRequestMessage(HttpMethod.Post, url)
            {
                Content = multipart,
            };

            // Gleicher CSRF-Origin-Workaround wie bei allen /chat- und
            // /addin-Requests: ein natives HttpClient sendet standardmaessig
            // keinen Origin-Header -> enforce_same_origin wiese den Request
            // sonst mit 403 ab, VOR der Auth-Pruefung.
            request.Headers.Add("Origin", _settings.AddinOriginToken);

            return request;
        }

        /// <summary>
        /// Laedt <paramref name="content"/> STREAMEND als Anhang hoch (DND-01/02/03).
        /// Der Stream gehoert dem AUFRUFER (wird hier NICHT disposed). <c>_http.Timeout</c>
        /// ist <c>InfiniteTimeSpan</c> (SSE-bedingt) — der Upload wird also
        /// ausschliesslich ueber den <paramref name="ct"/> begrenzt; der Aufrufer
        /// MUSS einen befristeten Token uebergeben.
        /// </summary>
        public async Task<AttachmentMeta> UploadAttachmentAsync(
            Stream content, string fileName, string sessionId, CancellationToken ct)
        {
            using (var response = await _http.SendAsync(
                BuildUploadRequest(content, fileName, sessionId), ct))
            {
                string body = await response.Content.ReadAsStringAsync();

                if (!response.IsSuccessStatusCode)
                {
                    string detail = ExtractDetailOrFallback(body, (int)response.StatusCode);
                    throw new AttachmentUploadException((int)response.StatusCode, detail);
                }

                var meta = AttachmentMeta.FromJson(body);
                if (meta == null)
                {
                    throw new AttachmentUploadException(
                        (int)response.StatusCode,
                        "Unerwartete Antwort des Servers beim Hochladen.");
                }
                return meta;
            }
        }

        /// <summary>
        /// Ueberladung, die den Anhang aus einer Datei STREAMEND liest (kein
        /// <c>File.ReadAllBytes</c> — eine 15-MB-Datei komplett in den Speicher zu
        /// laden ist unnoetig; Muster wie <see cref="DownloadAddinInstallerAsync"/>).
        /// </summary>
        public async Task<AttachmentMeta> UploadAttachmentAsync(
            string filePath, string fileName, string sessionId, CancellationToken ct)
        {
            using (var stream = new FileStream(
                filePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                return await UploadAttachmentAsync(stream, fileName, sessionId, ct);
            }
        }

        /// <summary>
        /// Baut den GET-Request fuer die Bild-Vorschau-Bytes (Quick 260813-wea,
        /// Task 3, <c>GET {BackendUrl}/chat/{AgentId}/anhang?uid=...&amp;ordner=...
        /// &amp;index=...</c>) — Muster wie <see cref="BuildAgentsRequest"/>
        /// (GET + Origin-Header). Oeffentlich fuer deterministische
        /// Request-Assertions. <paramref name="ordner"/> wird query-enkodiert
        /// (Sonderzeichen wie <c>[Gmail]/Drafts</c> oder <c>INBOX.Entwürfe</c>
        /// bleiben dadurch als EIN Query-Wert erhalten); <c>AgentId</c> bleibt
        /// pfad-enkodiert wie in den uebrigen Methoden.
        /// </summary>
        public HttpRequestMessage BuildAnhangRequest(string uid, string ordner, int index)
        {
            string url = _settings.BackendUrl.TrimEnd('/')
                + "/chat/" + Uri.EscapeDataString(_settings.AgentId ?? "") + "/anhang"
                + "?uid=" + Uri.EscapeDataString(uid ?? "")
                + "&ordner=" + Uri.EscapeDataString(ordner ?? "")
                + "&index=" + index;
            var request = new HttpRequestMessage(HttpMethod.Get, url);
            // Gleicher CSRF-Origin-Workaround wie bei den uebrigen GET-Endpoints.
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Laedt die Bild-Vorschau-Bytes fuer <paramref name="vorschau"/>
        /// (Quick 260813-wea, Task 3). Muster <see cref="DownloadAddinInstallerAsync"/>
        /// (<c>ResponseHeadersRead</c>-Streaming), aber GEDECKELT statt
        /// unbegrenzt in einen <see cref="MemoryStream"/> gelesen (64-KB-Chunks
        /// mit Live-Byte-Zaehler gegen <paramref name="maxBytes"/>) — der
        /// Server begrenzt zwar bereits serverseitig (<c>MAX_PREVIEW_MB</c>),
        /// aber ein manipulierter/kompromittierter Server duerfte den Client
        /// trotzdem nicht mit unbegrenztem Speicherverbrauch treffen koennen
        /// (Defense-in-Depth, T-wea-05). Bei <c>!IsSuccessStatusCode</c> wird
        /// der Body gelesen und als <see cref="AnhangDownloadException"/> mit
        /// Statuscode + Servertext geworfen.
        ///
        /// <c>_http.Timeout</c> ist <c>InfiniteTimeSpan</c> (SSE-bedingt) — der
        /// Aufrufer MUSS einen befristeten <paramref name="ct"/> uebergeben
        /// (identische Auflage wie bei <see cref="UploadAttachmentAsync(Stream, string, string, CancellationToken)"/>).
        /// </summary>
        public async Task<byte[]> DownloadAnhangAsync(ImagePreview vorschau, long maxBytes, CancellationToken ct)
        {
            if (vorschau == null) throw new ArgumentNullException(nameof(vorschau));

            using (var response = await _http.SendAsync(
                BuildAnhangRequest(vorschau.Uid, vorschau.Ordner, vorschau.Index),
                HttpCompletionOption.ResponseHeadersRead, ct))
            {
                if (!response.IsSuccessStatusCode)
                {
                    string errBody = await response.Content.ReadAsStringAsync();
                    string detail = ExtractDetailOrFallback(errBody, (int)response.StatusCode);
                    throw new AnhangDownloadException((int)response.StatusCode, detail);
                }

                using (var source = await response.Content.ReadAsStreamAsync())
                using (var buffer = new MemoryStream())
                {
                    byte[] chunk = new byte[64 * 1024];
                    long total = 0;
                    int read;
                    while ((read = await source.ReadAsync(chunk, 0, chunk.Length, ct)) > 0)
                    {
                        total += read;
                        if (total > maxBytes)
                        {
                            throw new AnhangDownloadException(0, "Anhang überschreitet das Vorschau-Limit beim Herunterladen.");
                        }
                        buffer.Write(chunk, 0, read);
                    }
                    return buffer.ToArray();
                }
            }
        }

        /// <summary>
        /// Extrahiert das <c>detail</c>-Feld einer FastAPI-Fehlerantwort. Ein
        /// Proxy/Fehlerfall kann statt JSON auch HTML liefern — daher in try/catch
        /// und mit Fallback auf den rohen (auf ~300 Zeichen gekappten) Body; ist
        /// auch der leer, bleibt nur "HTTP {code}" uebrig. Nie ein Parse-Crash.
        /// </summary>
        private static string ExtractDetailOrFallback(string body, int statusCode)
        {
            try
            {
                var parsed = JObject.Parse(body);
                string detail = (string)parsed["detail"];
                if (!string.IsNullOrEmpty(detail))
                {
                    return detail;
                }
            }
            catch (JsonReaderException)
            {
                // Kein JSON (z. B. HTML-Fehlerseite eines Proxys) -> Fallback unten.
            }

            if (string.IsNullOrEmpty(body))
            {
                return "HTTP " + statusCode;
            }
            return body.Length > 300 ? body.Substring(0, 300) : body;
        }

        /// <summary>
        /// Baut den Verify-Password-Request (URL + Form-Feld <c>password</c> +
        /// Origin-Header). Oeffentlich fuer deterministische Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildVerifyPasswordRequest(string password)
        {
            string url = _settings.BackendUrl.TrimEnd('/') + "/addin/verify-password";

            var fields = new List<KeyValuePair<string, string>>
            {
                new KeyValuePair<string, string>("password", password ?? ""),
            };

            var request = new HttpRequestMessage(HttpMethod.Post, url)
            {
                Content = new FormUrlEncodedContent(fields),
            };

            // Gleicher CSRF-Origin-Workaround wie beim Chat-POST: ohne Origin-Header
            // weist enforce_same_origin den Request mit 403 ab. Der Endpoint ist
            // serverseitig in der Add-in-Origin-Allowlist freigeschaltet.
            request.Headers.Add("Origin", _settings.AddinOriginToken);

            return request;
        }

        /// <summary>
        /// Prueft ein FRISCH eingegebenes WebUI-Passwort gegen
        /// <c>POST {BackendUrl}/addin/verify-password</c> (Add-in-Einstellungs-Gate).
        /// Liefert <c>true</c> bei HTTP 200, <c>false</c> bei 401. Alle anderen
        /// Faelle (Netz-/TLS-Fehler, sonstige 4xx/5xx) werfen eine Ausnahme, damit
        /// der Aufrufer „Backend nicht erreichbar" vom „falschen Passwort" trennen
        /// kann.
        /// </summary>
        public async Task<bool> VerifyPasswordAsync(string password, CancellationToken ct)
        {
            using (var response = await _http.SendAsync(
                BuildVerifyPasswordRequest(password), ct))
            {
                if (response.StatusCode == HttpStatusCode.OK)
                {
                    return true;
                }
                if (response.StatusCode == HttpStatusCode.Unauthorized)
                {
                    return false;
                }
                response.EnsureSuccessStatusCode(); // wirft bei allem uebrigen
                return false;
            }
        }

        /// <summary>
        /// Baut den GET-Request fuer die Agentenliste (<c>/addin/agents</c>) inkl.
        /// Origin-Header. Oeffentlich fuer deterministische Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildAgentsRequest()
        {
            string url = _settings.BackendUrl.TrimEnd('/') + "/addin/agents";
            var request = new HttpRequestMessage(HttpMethod.Get, url);
            // Gleicher CSRF-Origin-Workaround wie bei den POST-Endpoints.
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Liest die serverseitig konfigurierten Agenten-IDs
        /// (<c>GET {BackendUrl}/addin/agents</c> -> <c>{"agents": [...]}</c>). Wird
        /// vom Pane-Header genutzt, um den REAL konfigurierten Agent-Namen
        /// anzuzeigen. Wirft bei Netz-/TLS-/HTTP-Fehlern — der Aufrufer faellt dann
        /// bewusst auf die lokal gespeicherte AgentId zurueck.
        /// </summary>
        public async Task<List<string>> GetAgentIdsAsync(CancellationToken ct)
        {
            using (var response = await _http.SendAsync(BuildAgentsRequest(), ct))
            {
                response.EnsureSuccessStatusCode();
                string body = await response.Content.ReadAsStringAsync();
                var parsed = JObject.Parse(body);
                var result = new List<string>();
                var arr = parsed["agents"] as JArray;
                if (arr != null)
                {
                    foreach (var item in arr)
                    {
                        string id = (string)item;
                        if (!string.IsNullOrEmpty(id))
                        {
                            result.Add(id);
                        }
                    }
                }
                return result;
            }
        }

        /// <summary>
        /// Baut den GET-Request fuer den Draft-Zustand eines Agenten
        /// (<c>/addin/agents/{id}/draft-enabled</c>) inkl. Origin-Header.
        /// Oeffentlich fuer deterministische Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildDraftEnabledGetRequest(string agentId)
        {
            string url = _settings.BackendUrl.TrimEnd('/')
                + "/addin/agents/" + Uri.EscapeDataString(agentId ?? "") + "/draft-enabled";
            var request = new HttpRequestMessage(HttpMethod.Get, url);
            // Gleicher CSRF-Origin-Workaround wie bei den uebrigen /addin-Endpoints.
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Liest den serverseitigen Zustand der automatischen Entwurf-Erstellung
        /// (<c>GET {BackendUrl}/addin/agents/{id}/draft-enabled</c> ->
        /// <c>{"enabled": bool}</c>). Default <c>true</c>, falls das Feld fehlt oder
        /// nicht als bool interpretierbar ist. Wirft bei Netz-/TLS-/HTTP-Fehlern —
        /// der Aufrufer schaltet die Checkbox dann bewusst deaktiviert.
        /// </summary>
        public async Task<bool> GetDraftEnabledAsync(string agentId, CancellationToken ct)
        {
            using (var response = await _http.SendAsync(
                BuildDraftEnabledGetRequest(agentId), ct))
            {
                response.EnsureSuccessStatusCode();
                string body = await response.Content.ReadAsStringAsync();
                var parsed = JObject.Parse(body);
                var token = parsed["enabled"];
                if (token != null && token.Type == JTokenType.Boolean)
                {
                    return (bool)token;
                }
                return true; // Default an, wenn Feld fehlt/kein bool
            }
        }

        /// <summary>
        /// Baut den POST-Request, der den Draft-Zustand eines Agenten setzt
        /// (<c>/addin/agents/{id}/draft-enabled</c>, Formfeld <c>enabled</c>) inkl.
        /// Origin-Header. Oeffentlich fuer deterministische Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildDraftEnabledSetRequest(string agentId, bool enabled)
        {
            string url = _settings.BackendUrl.TrimEnd('/')
                + "/addin/agents/" + Uri.EscapeDataString(agentId ?? "") + "/draft-enabled";
            var fields = new List<KeyValuePair<string, string>>
            {
                new KeyValuePair<string, string>("enabled", enabled ? "true" : "false"),
            };
            var request = new HttpRequestMessage(HttpMethod.Post, url)
            {
                Content = new FormUrlEncodedContent(fields),
            };
            // Gleicher CSRF-Origin-Workaround wie bei den uebrigen /addin-POSTs.
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Schreibt den Zustand der automatischen Entwurf-Erstellung ins Backend
        /// (<c>POST {BackendUrl}/addin/agents/{id}/draft-enabled</c>). Wirft bei
        /// Netz-/TLS-/HTTP-Fehlern — der Aufrufer behandelt den Schreib-Fehler
        /// nicht-blockierend.
        /// </summary>
        public async Task SetDraftEnabledAsync(string agentId, bool enabled, CancellationToken ct)
        {
            using (var response = await _http.SendAsync(
                BuildDraftEnabledSetRequest(agentId, enabled), ct))
            {
                response.EnsureSuccessStatusCode();
            }
        }

        /// <summary>
        /// Zustand der Urlaubs-Autoantwort eines Agenten (URL-01..09), wie ihn
        /// <c>GET /addin/agents/{id}/urlaub</c> liefert.
        /// </summary>
        public sealed class UrlaubState
        {
            /// <summary>Ist die Autoantwort freigegeben? (Backend-Flag ENABLE_URLAUBSANTWORT)</summary>
            public bool Enabled;

            /// <summary>Probelauf: protokolliert nur, verschickt nichts.</summary>
            public bool DryRun = true;

            /// <summary>Erster Urlaubstag, <c>JJJJ-MM-TT</c> oder leer.</summary>
            public string Von = "";

            /// <summary>Letzter Urlaubstag (einschliesslich), <c>JJJJ-MM-TT</c> oder leer.</summary>
            public string Bis = "";

            /// <summary>Gibt es einen Abschnitt <c>## Urlaub</c> in den Vorlagen?
            /// Ohne ihn wird NICHTS gesendet — der Dialog warnt dann.</summary>
            public bool VorlageVorhanden;

            /// <summary>Der gespeicherte Urlaubstext (Abschnitt <c>## Urlaub</c>),
            /// oder leer. Genau der Text, den der Agent verschicken wuerde.</summary>
            public string VorlageText = "";

            /// <summary>Unverbindlicher Vorschlag fuer den Fall, dass noch kein
            /// Text existiert. NUR zur Anzeige — er wird erst wirksam, wenn der
            /// Betreiber ihn speichert.</summary>
            public string VorlageVorschlag = "";
        }

        /// <summary>
        /// Baut den GET-Request fuer den Urlaubs-Zustand eines Agenten
        /// (<c>/addin/agents/{id}/urlaub</c>) inkl. Origin-Header.
        /// Oeffentlich fuer deterministische Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildUrlaubGetRequest(string agentId)
        {
            string url = _settings.BackendUrl.TrimEnd('/')
                + "/addin/agents/" + Uri.EscapeDataString(agentId ?? "") + "/urlaub";
            var request = new HttpRequestMessage(HttpMethod.Get, url);
            // Gleicher CSRF-Origin-Workaround wie bei den uebrigen /addin-Endpoints.
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Liest den serverseitigen Zustand der Urlaubs-Autoantwort
        /// (<c>GET {BackendUrl}/addin/agents/{id}/urlaub</c>). Fehlende Felder
        /// fallen auf den SICHEREN Wert zurueck: <c>Enabled=false</c>,
        /// <c>DryRun=true</c> — bei der einzigen sendenden Funktion des Produkts
        /// darf ein unvollstaendig geparste Antwort nie als "an" durchgehen.
        /// Wirft bei Netz-/TLS-/HTTP-Fehlern; der Aufrufer deaktiviert die
        /// Bedienelemente dann bewusst.
        /// </summary>
        public async Task<UrlaubState> GetUrlaubAsync(string agentId, CancellationToken ct)
        {
            using (var response = await _http.SendAsync(BuildUrlaubGetRequest(agentId), ct))
            {
                response.EnsureSuccessStatusCode();
                var parsed = JObject.Parse(await response.Content.ReadAsStringAsync());
                return new UrlaubState
                {
                    Enabled = ReadBool(parsed, "enabled", false),
                    DryRun = ReadBool(parsed, "dry_run", true),
                    Von = ReadString(parsed, "von"),
                    Bis = ReadString(parsed, "bis"),
                    VorlageVorhanden = ReadBool(parsed, "vorlage_vorhanden", false),
                    VorlageText = ReadString(parsed, "vorlage_text"),
                    VorlageVorschlag = ReadString(parsed, "vorlage_vorschlag"),
                };
            }
        }

        private static bool ReadBool(JObject parsed, string name, bool fallback)
        {
            var token = parsed[name];
            return token != null && token.Type == JTokenType.Boolean ? (bool)token : fallback;
        }

        private static string ReadString(JObject parsed, string name)
        {
            var token = parsed[name];
            return token != null && token.Type == JTokenType.String ? (string)token : "";
        }

        /// <summary>
        /// Baut den POST-Request, der die Urlaubs-Autoantwort setzt
        /// (<c>/addin/agents/{id}/urlaub</c>, Formfelder <c>enabled</c>,
        /// <c>dry_run</c>, <c>von</c>, <c>bis</c>) inkl. Origin-Header.
        /// Oeffentlich fuer deterministische Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildUrlaubSetRequest(
            string agentId, bool enabled, bool dryRun, string von, string bis,
            string vorlage = null)
        {
            string url = _settings.BackendUrl.TrimEnd('/')
                + "/addin/agents/" + Uri.EscapeDataString(agentId ?? "") + "/urlaub";
            var fields = new List<KeyValuePair<string, string>>
            {
                new KeyValuePair<string, string>("enabled", enabled ? "true" : "false"),
                new KeyValuePair<string, string>("dry_run", dryRun ? "true" : "false"),
                new KeyValuePair<string, string>("von", von ?? ""),
                new KeyValuePair<string, string>("bis", bis ?? ""),
            };
            // `null` heisst "nicht geaendert" — dann bleibt der Marker weg und
            // das Backend laesst die gespeicherte Vorlage unberuehrt. Ein leerer
            // String wird dagegen MIT Marker gesendet und entfernt sie bewusst.
            // Der Marker ist noetig, weil ein leeres und ein fehlendes Formfeld
            // serverseitig nicht unterscheidbar sind.
            if (vorlage != null)
            {
                fields.Add(new KeyValuePair<string, string>("vorlage_gesetzt", "true"));
                fields.Add(new KeyValuePair<string, string>("vorlage", vorlage));
            }
            var request = new HttpRequestMessage(HttpMethod.Post, url)
            {
                Content = new FormUrlEncodedContent(fields),
            };
            // Gleicher CSRF-Origin-Workaround wie bei den uebrigen /addin-POSTs.
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Schreibt die Urlaubs-Autoantwort ins Backend
        /// (<c>POST {BackendUrl}/addin/agents/{id}/urlaub</c>).
        ///
        /// Bei <c>400</c> (unbrauchbarer Zeitraum) wird die DEUTSCHE
        /// Server-Meldung aus dem <c>detail</c>-Feld als
        /// <see cref="InvalidOperationException"/> weitergereicht — der Betreiber
        /// soll lesen, WAS an seiner Eingabe nicht stimmt, statt eines rohen
        /// HTTP-Status. Alle uebrigen Fehler fliegen wie gehabt.
        /// </summary>
        public async Task SetUrlaubAsync(
            string agentId, bool enabled, bool dryRun, string von, string bis, CancellationToken ct,
            string vorlage = null)
        {
            using (var response = await _http.SendAsync(
                BuildUrlaubSetRequest(agentId, enabled, dryRun, von, bis, vorlage), ct))
            {
                if (response.StatusCode == System.Net.HttpStatusCode.BadRequest)
                {
                    throw new InvalidOperationException(
                        ExtractDetail(await response.Content.ReadAsStringAsync()));
                }
                response.EnsureSuccessStatusCode();
            }
        }

        /// <summary>
        /// Baut den GET-Request fuer den Intelligenz-Modus
        /// (<c>GET {BackendUrl}/addin/agents/{id}/intelligenz</c>).
        /// </summary>
        public HttpRequestMessage BuildIntelligenzGetRequest(string agentId)
        {
            string url = _settings.BackendUrl.TrimEnd('/')
                + "/addin/agents/" + Uri.EscapeDataString(agentId ?? "") + "/intelligenz";
            var request = new HttpRequestMessage(HttpMethod.Get, url);
            // Gleicher CSRF-Origin-Workaround wie bei den uebrigen /addin-Endpoints.
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Liest den serverseitigen Intelligenz-Modus. Ein unbekannter oder
        /// fehlender Wert faellt auf <c>"standard"</c> zurueck — der guenstige
        /// Modus ist hier der sichere: eine falsch angezeigte Stufe „hoch"
        /// wuerde einen Betreiber glauben lassen, er zahle schon dafuer.
        /// Wirft bei Netz-/TLS-/HTTP-Fehlern; der Aufrufer deaktiviert das
        /// Bedienelement dann bewusst.
        /// </summary>
        public async Task<string> GetIntelligenzModusAsync(string agentId, CancellationToken ct)
        {
            using (var response = await _http.SendAsync(BuildIntelligenzGetRequest(agentId), ct))
            {
                response.EnsureSuccessStatusCode();
                var parsed = JObject.Parse(await response.Content.ReadAsStringAsync());
                string modus = ReadString(parsed, "modus");
                return modus == "hoch" ? "hoch" : "standard";
            }
        }

        /// <summary>
        /// Baut den POST-Request, der den Intelligenz-Modus setzt.
        /// </summary>
        public HttpRequestMessage BuildIntelligenzSetRequest(string agentId, string modus)
        {
            string url = _settings.BackendUrl.TrimEnd('/')
                + "/addin/agents/" + Uri.EscapeDataString(agentId ?? "") + "/intelligenz";
            var request = new HttpRequestMessage(HttpMethod.Post, url);
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            request.Content = new FormUrlEncodedContent(new[]
            {
                new KeyValuePair<string, string>("modus", modus == "hoch" ? "hoch" : "standard"),
            });
            return request;
        }

        /// <summary>
        /// Schreibt den Intelligenz-Modus ins Backend. Ein 400 wird als
        /// <see cref="InvalidOperationException"/> mit der Servermeldung
        /// weitergegeben, damit der Dialog sie anzeigen kann statt eines
        /// generischen HTTP-Fehlers.
        /// </summary>
        public async Task SetIntelligenzModusAsync(
            string agentId, string modus, CancellationToken ct)
        {
            using (var response = await _http.SendAsync(
                BuildIntelligenzSetRequest(agentId, modus), ct))
            {
                if (response.StatusCode == System.Net.HttpStatusCode.BadRequest)
                {
                    throw new InvalidOperationException(
                        ExtractDetail(await response.Content.ReadAsStringAsync()));
                }
                response.EnsureSuccessStatusCode();
            }
        }

        /// <summary>
        /// Zieht die deutsche Meldung aus einem FastAPI-Fehlerkoerper
        /// (<c>{"detail": "..."}</c>). Faellt auf einen neutralen Satz zurueck,
        /// wenn der Koerper kein JSON ist — NIE der rohe Koerper, der sonst
        /// unkontrolliert in einer MessageBox landen wuerde.
        /// </summary>
        public static string ExtractDetail(string body)
        {
            try
            {
                var token = JObject.Parse(body ?? "")["detail"];
                if (token != null && token.Type == JTokenType.String)
                {
                    string text = (string)token;
                    if (!string.IsNullOrWhiteSpace(text))
                    {
                        return text;
                    }
                }
            }
            catch
            {
                // absichtlich verschluckt — Fallback unten
            }
            return "Der Urlaubszeitraum wurde vom Server abgelehnt.";
        }

        /// <summary>
        /// Baut den GET-Request fuer den Update-Check (<c>/addin/update-check</c>)
        /// inkl. Origin-Header. Oeffentlich fuer deterministische
        /// Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildUpdateCheckRequest()
        {
            string url = _settings.BackendUrl.TrimEnd('/') + "/addin/update-check";
            var request = new HttpRequestMessage(HttpMethod.Get, url);
            // Gleicher CSRF-Origin-Workaround wie bei den uebrigen /addin-Endpoints.
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Liest den serverseitigen Update-Status
        /// (<c>GET {BackendUrl}/addin/update-check</c>, 260728-irn) und parst ihn zu
        /// <see cref="UpdateInfo"/>. Wirft bei Netz-/TLS-/HTTP-Fehlern oder kaputtem
        /// Body — der Aufrufer behandelt das bewusst wie "kein Update" und zeigt
        /// keine Fehlermeldung, die den Betreiber blockiert.
        /// </summary>
        public async Task<UpdateInfo> GetUpdateInfoAsync(CancellationToken ct)
        {
            using (var response = await _http.SendAsync(BuildUpdateCheckRequest(), ct))
            {
                response.EnsureSuccessStatusCode();
                string body = await response.Content.ReadAsStringAsync();
                return UpdateInfo.FromJson(body);
            }
        }

        /// <summary>
        /// Baut den Start-Update-Request (<c>POST /addin/update</c>,
        /// 260728-m8g, Formfeld <c>version</c>) inkl. Origin-Header.
        /// Oeffentlich fuer deterministische Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildStartUpdateRequest(string version)
        {
            string url = _settings.BackendUrl.TrimEnd('/') + "/addin/update";
            var fields = new List<KeyValuePair<string, string>>
            {
                new KeyValuePair<string, string>("version", version ?? ""),
            };
            var request = new HttpRequestMessage(HttpMethod.Post, url)
            {
                Content = new FormUrlEncodedContent(fields),
            };
            // Gleicher CSRF-Origin-Workaround wie bei den uebrigen /addin-POSTs.
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Startet das Self-Update auf dem Server (<c>POST
        /// {BackendUrl}/addin/update</c>, 260728-m8g). Der
        /// <c>Authorization</c>-Basic-Header liegt bereits ueber den
        /// Konstruktor an — der Server verlangt fuer diesen destruktiven
        /// Trigger ZUSAETZLICH das WebUI-Passwort (T-m8g-01). Bei HTTP 409
        /// (laeuft bereits) wirft diese Methode
        /// <see cref="UpdateAlreadyRunningException"/>, damit der Aufrufer
        /// direkt ins Polling wechseln kann statt einen Fehlerdialog zu
        /// zeigen. Andere Fehlerstatus (401 = falsches/veraltetes Passwort,
        /// 503 = kein Update-Zugang konfiguriert, …) werfen regulaer ueber
        /// <c>EnsureSuccessStatusCode</c>.
        /// </summary>
        public async Task StartUpdateAsync(string version, CancellationToken ct)
        {
            using (var response = await _http.SendAsync(BuildStartUpdateRequest(version), ct))
            {
                if (response.StatusCode == HttpStatusCode.Conflict)
                {
                    string body = await response.Content.ReadAsStringAsync();
                    throw new UpdateAlreadyRunningException(body);
                }
                response.EnsureSuccessStatusCode();
            }
        }

        /// <summary>
        /// Baut den GET-Request fuer den Update-Fortschritt
        /// (<c>/addin/update-status</c>) inkl. Origin-Header. Oeffentlich
        /// fuer deterministische Request-Assertions.
        /// </summary>
        public HttpRequestMessage BuildUpdateProgressRequest()
        {
            string url = _settings.BackendUrl.TrimEnd('/') + "/addin/update-status";
            var request = new HttpRequestMessage(HttpMethod.Get, url);
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Liest den Update-Fortschritt (<c>GET
        /// {BackendUrl}/addin/update-status</c>, 260728-m8g) und parst ihn zu
        /// <see cref="UpdateProgress"/>. <b>Netz-/TLS-Fehler waehrend eines
        /// laufenden Updates sind ERWARTET</b> (die WebUI startet gerade
        /// neu) — diese Methode wirft in diesem Fall regulaer, und der
        /// AUFRUFER (die Poll-Schleife) muss das als "laeuft noch" behandeln,
        /// nicht als Fehler (D-5).
        /// </summary>
        public async Task<UpdateProgress> GetUpdateProgressAsync(CancellationToken ct)
        {
            using (var response = await _http.SendAsync(BuildUpdateProgressRequest(), ct))
            {
                response.EnsureSuccessStatusCode();
                string body = await response.Content.ReadAsStringAsync();
                return UpdateProgress.FromJson(body);
            }
        }

        /// <summary>
        /// Baut den GET-Request fuer den passwortgeschuetzten Add-in-Installer
        /// (<c>/addin/installer</c>, 260728-m8g Task 6 — Gegenstueck zum
        /// browserseitigen <c>/addin-installer</c>) inkl. Origin-Header.
        /// </summary>
        public HttpRequestMessage BuildAddinInstallerRequest()
        {
            string url = _settings.BackendUrl.TrimEnd('/') + "/addin/installer";
            var request = new HttpRequestMessage(HttpMethod.Get, url);
            request.Headers.Add("Origin", _settings.AddinOriginToken);
            return request;
        }

        /// <summary>
        /// Laedt das neue Add-in-Publish-Bundle STREAMEND in <paramref
        /// name="destPath"/> (mehrere MB — kein <c>ReadAsByteArrayAsync</c>).
        /// Wird nach einem erfolgreichen Server-Update aufgerufen
        /// (<see cref="AddinSelfUpdate"/>, D-6).
        /// </summary>
        public async Task DownloadAddinInstallerAsync(string destPath, CancellationToken ct)
        {
            using (var response = await _http.SendAsync(
                BuildAddinInstallerRequest(), HttpCompletionOption.ResponseHeadersRead, ct))
            {
                response.EnsureSuccessStatusCode();
                using (var source = await response.Content.ReadAsStreamAsync())
                using (var destination = new FileStream(
                    destPath, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    await source.CopyToAsync(destination);
                }
            }
        }

        public void Dispose()
        {
            _http.Dispose();
        }
    }
}
