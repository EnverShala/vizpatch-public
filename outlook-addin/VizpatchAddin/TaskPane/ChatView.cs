using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using VizpatchAddin;
using VizpatchAddin.Core;

namespace VizpatchAddin.TaskPane
{
    /// <summary>
    /// Nativer Chat-Bereich der Custom Task Pane (MVP-Slice 1, D-82/D-84).
    /// Reicht eine getippte Nachricht an <see cref="ChatClient.StreamChatAsync"/>
    /// (Plan 08-01) und rendert den SSE-Stream inkrementell:
    /// Text-Chunks (default) werden an die laufende Antwort angehaengt,
    /// <c>event: tool</c> als Aktivitaets-Karte, <c>event: done</c>
    /// schliesst den Turn ab, <c>event: error</c> zeigt eine Fehlermeldung.
    ///
    /// Layout-Ueberarbeitung (Design "Vizpatch Add-in", 2026-07-24): kompakter
    /// Header (Emblem + Titel/Untertitel + Icon-Buttons Einstellungen/Theme/Reset),
    /// Agent-Chip (Name aus dem Backend gelesen), Mail-Bezug als klickbarer
    /// Toggle-Chip (Opt-in, Default AUS -> Datensparsamkeit D-86 bleibt gewahrt),
    /// getailte Sprechblasen, gruene Aktivitaets-Karten, Quick-Action-Chips und ein
    /// Composer mit rundem Rahmen + Icon-Sende-Button. Voller Hell/Dunkel-Umschalter
    /// (<see cref="Palette"/>), Wahl wird ueber <see cref="AddinSettings.DarkMode"/>
    /// persistiert.
    ///
    /// Sitzung: pro Task-Pane-Sitzung eine <c>session_id</c> (GUID) via
    /// <see cref="SessionIdGenerator"/>; bei jedem Send unveraendert mitgeschickt.
    /// "Zuruecksetzen" erzeugt eine NEUE session_id und leert den In-Memory-Verlauf.
    ///
    /// Mail-Kontext (D-86, Plan 08-03): ist der Mail-Chip aktiv, wird vor jedem Send
    /// ueber <see cref="MailContextReader.TryBuildMailContext"/> der Kontext
    /// (subject/sender/body) aus dem aktiven <c>MailItem</c> gebaut und als
    /// <c>mail_context</c> uebergeben.
    ///
    /// Kein-Auto-Send (D-87): diese View ruft KEINE Outlook-Schreib-/Versand-APIs
    /// auf und erzeugt keine MailItems — sie liest nur die offene Mail und spricht
    /// ausschliesslich die Chat-API.
    ///
    /// Anhänge per Drag&amp;Drop (DND-01..06, 260731-u3m): der Betreiber zieht
    /// eine Explorer-Datei ODER einen Outlook-Anhang/eine ganze Mail (wird zu
    /// <c>.msg</c>) in die Chat-Pane. <see cref="OutlookDropExtractor"/>
    /// extrahiert die Datei in eine Temp-Kopie (Explorer-Originale bleiben
    /// unberuehrt), <see cref="ChatClient.UploadAttachmentAsync(string, string, string, CancellationToken)"/>
    /// laedt sie hoch; die zurueckgegebenen Metadaten (Name/Groesse/Typ, NIE der
    /// Inhalt, D-96) werden gemerkt und beim naechsten Send mitgeschickt. Kein-
    /// Auto-Send bleibt dadurch unberuehrt: es entsteht ausschliesslich ein
    /// serverseitiger Pending-Upload — kein Outlook-Item, kein Versand.
    /// </summary>
    public class ChatView : UserControl
    {
        // Aktuelle Farbpalette (hell/dunkel). Alle selbstgezeichneten Controls
        // lesen ihre Farben hieraus; ApplyTheme() tauscht sie und faerbt alles um.
        private Palette _pal;

        private BottomBorderPanel _header;
        private EmblemBox _logo;
        // 260728-irn: dezenter, nicht-modaler Update-Hinweis oben im Pane (D-3).
        private Panel _updateBanner;
        private Label _updateBannerLabel;
        private Label _updateBannerChevron;
        private IconButton _mailButton;
        private IconButton _settingsButton;
        private IconButton _themeButton;
        private IconButton _resetButton;
        private Label _agentCaption;
        private AgentChip _agentChip;

        private BufferedFlowPanel _log;
        private Bubble _currentAssistant;
        private ThinkingDots _thinking;

        private Panel _composerPanel;
        private RoundedPanel _composerFrame;
        private TextBox _input;
        private SendButton _sendButton;
        private Label _footer;

        // In-Memory-Verlauf (D-58) — lebt nur mit der Sitzung.
        private readonly List<ChatTurn> _history = new List<ChatTurn>();
        private string _sessionId;
        private AddinSettings _settings;

        // Anhang per Drag&Drop (DND-01..04, 260731-u3m): NUR Metadaten (Name/
        // Groesse/Typ), NIE der Dateiinhalt (D-96). Der Server haelt genau EINEN
        // Pending-Upload je (agent_id, session_id) — ein zweiter Drop ersetzt den
        // ersten, "Zuruecksetzen" verwirft ihn zusammen mit der neuen session_id.
        private AttachmentMeta _pendingAttachment;
        private bool _uploadLaeuft;

        // Mail-Bezug: Opt-in, Default AUS (Datensparsamkeit, D-86).
        private bool _mailActive;

        // Abbruch des laufenden SSE-Streams. Wird bei "Zuruecksetzen" und beim
        // Dispose der Pane gecancelt, damit kein Request haengt (WR-05).
        private CancellationTokenSource _activeCts;

        // Blase nimmt hoechstens diesen Anteil der Log-Breite ein.
        private const double BubbleMaxWidthFraction = 0.82;

        public ChatView()
        {
            _settings = LoadSettingsSafe();
            _pal = _settings.DarkMode ? Palette.Dark : Palette.Light;
            // Doppelpuffer fuer die Pane selbst. Hilft bei einzelnen Repaints; das
            // Flackern beim Splitter-Ziehen kommt aus Outlooks Redraw und ist damit
            // NICHT behoben (siehe die Liste der Fehlversuche in ChatTaskPaneHost).
            this.DoubleBuffered = true;
            BuildUi();
            ApplyTheme();
            _sessionId = SessionIdGenerator.NewSessionId();
            ShowConfigHintIfNeeded();
            // KEIN ShowGreeting() hier. Die Pane wird im ThisAddIn_Startup erzeugt,
            // also beim Start von Outlook und mit Visible = false — der Tipp-Effekt
            // waere komplett abgelaufen, bevor der Betreiber die Pane ueberhaupt
            // einblendet, und er saehe nur den fertigen Text. Den Effekt startet
            // deshalb NotifyPaneShown(), sobald die Pane das erste Mal sichtbar wird.
            LoadAgentNameAsync();
            CheckForUpdateBannerAsync();
        }

        private void BuildUi()
        {
            this.Dock = DockStyle.Fill;

            // --- Chat-Log (Fill) -------------------------------------------------
            // BufferedFlowPanel statt FlowLayoutPanel: der Standard-FlowLayoutPanel ist
            // NICHT doppelgepuffert und malt beim Reflow (jede Breitenaenderung ordnet
            // alle Blasen neu an) sichtbar Zwischenzustaende.
            _log = new BufferedFlowPanel
            {
                Dock = DockStyle.Fill,
                FlowDirection = FlowDirection.TopDown,
                WrapContents = false,
                AutoScroll = true,
                Padding = new Padding(14),
                // Der FlowLayoutPanel bezieht seinen Bottom-Padding NICHT in den
                // AutoScroll-Bereich ein -> die unterste Blase wird sonst am Composer
                // "abgehackt". AutoScrollMargin reserviert unten explizit Platz, sodass
                // die letzte Nachricht immer vollständig (mit Luft) sichtbar wird.
                AutoScrollMargin = new Size(0, 16),
                Font = new Font("Segoe UI", 10f),
            };
            _log.Resize += (s, e) => RelayoutBubbles();

            // --- Composer (Bottom) ----------------------------------------------
            _composerPanel = new Panel
            {
                Dock = DockStyle.Bottom,
                Height = 132,
                Padding = new Padding(14, 10, 14, 12),
            };

            _footer = new Label
            {
                Dock = DockStyle.Bottom,
                Height = 18,
                TextAlign = ContentAlignment.MiddleCenter,
                Font = new Font("Segoe UI", 8.25f),
                Text = "Vizpatch sendet nie automatisch · Entwürfe bleiben in Outlook",
            };

            // Rechts einen festen Gutter (46 px) fuer den Sende-Button reservieren:
            // das Textfeld (Dock Fill) respektiert das Padding und reicht damit NIE
            // in den Button- oder Rahmenbereich — verhindert den ueberlappenden
            // Button und das "zerfranste" Nachzeichnen des runden Rahmens bei
            // mehrzeiliger Eingabe (die native TextBox-Scrollbar bleibt so weit vom
            // gezeichneten Rahmen entfernt).
            _composerFrame = new RoundedPanel(Color.Gray)
            {
                Dock = DockStyle.Fill,
                Padding = new Padding(12, 8, 46, 8),
            };
            _input = new TextBox
            {
                Dock = DockStyle.Fill,
                Multiline = true,
                // Kein Scrollbalken im Eingabefeld (Wunsch): das Feld wächst über
                // AdjustComposerHeight mit dem Text mit; bei sehr langem Text scrollt
                // der Inhalt weiterhin mit dem Cursor mit — nur eben ohne sichtbare
                // Scrollbar (hell wie dunkel).
                ScrollBars = ScrollBars.None,
                Font = new Font("Segoe UI", 10.5f),
                BorderStyle = BorderStyle.None,
            };
            _input.KeyDown += Input_KeyDown;
            _input.TextChanged += (s, e) => AdjustComposerHeight();
            _input.Enter += (s, e) => { _composerFrame.BorderColor = _pal.Brand; _composerFrame.Invalidate(); };
            _input.Leave += (s, e) => { _composerFrame.BorderColor = _pal.BorderStrong; _composerFrame.Invalidate(); };
            // Native Textfeld-Scrollbar ans Theme koppeln (sonst hellgraue Scrollbar
            // auf dunklem Feld im Dark Mode). Beim (erneuten) Handle-Aufbau anwenden.
            _input.HandleCreated += (s, e) => ApplyInputScrollbarTheme();

            _sendButton = new SendButton { Size = new Size(34, 34) };
            _sendButton.Click += SendButton_Click;

            // Textfeld zuerst (Dock Fill), Button danach (schwebt im rechten Gutter,
            // per Layout-Event positioniert -> immer korrekt, auch beim ersten Paint).
            _composerFrame.Controls.Add(_input);
            _composerFrame.Controls.Add(_sendButton);
            _composerFrame.Layout += (s, e) => PositionSendButton();

            _composerPanel.Controls.Add(_composerFrame);
            _composerPanel.Controls.Add(_footer);

            // --- Header (Top) ----------------------------------------------------
            // Top-Padding 4 (urspruenglich 12, zwischenzeitlich 0 und 3 getestet):
            // 12 stand als deutlicher weisser Streifen ueber dem Logo, 0 liess das Logo
            // direkt an der Pane-Oberkante kleben. 4 px sind der Kompromiss.
            // Header-Hoehe = 4 (Top) + 40 (row1) + 36 (row2) + 10 (Bottom) = 90.
            _header = new BottomBorderPanel { Dock = DockStyle.Top, Height = 90, Padding = new Padding(14, 4, 14, 10) };

            var row2 = new Panel { Dock = DockStyle.Top, Height = 36, Padding = new Padding(0, 6, 0, 0) };
            // Beschriftung "Agent": Breite am REAL gerenderten Text messen. Eine fixe
            // Breite (vorher 46 px) reicht bei DPI-Skalierung > 100 % nicht mehr — das
            // Label bricht dann mitten im Wort um ("Age"/"nt"). TextRenderer misst mit
            // derselben Engine, die das Label auch zeichnet (UseCompatibleTextRendering
            // = false ist WinForms-Default).
            var captionFont = new Font("Segoe UI Semibold", 8.5f);
            int captionWidth = TextRenderer.MeasureText("Agent", captionFont).Width + 6;
            _agentCaption = new Label
            {
                Dock = DockStyle.Left,
                AutoSize = false,
                Width = captionWidth,
                TextAlign = ContentAlignment.MiddleLeft,
                Font = captionFont,
                Text = "Agent",
            };
            _agentChip = new AgentChip { Dock = DockStyle.Fill, Text = _settings.AgentId ?? "" };
            row2.Controls.Add(_agentChip);
            row2.Controls.Add(_agentCaption);

            var row1 = new Panel { Dock = DockStyle.Top, Height = 40 };
            // Komplettes Vizpatch-Logo (transparent freigestellt, HQ-skaliert),
            // linksbuendig — ersetzt Emblem + "Assistent"/Untertitel. Wird die Pane
            // zu schmal fuer das volle Logo, zeigt die EmblemBox stattdessen nur das
            // Emblem (statt das Logo zu verkleinern).
            _logo = new EmblemBox(LoadLogoSafe(), LoadEmblemImageSafe())
            {
                Dock = DockStyle.Fill,
                AlignLeft = true,
            };

            var iconBar = new FlowLayoutPanel
            {
                Dock = DockStyle.Right,
                FlowDirection = FlowDirection.LeftToRight,
                WrapContents = false,
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                // Dock Right streckt das Panel auf die volle row1-Hoehe (40); die
                // 30-px-Buttons wuerden sonst oben anfliegen. Top-Padding (40-30)/2=5
                // zentriert sie vertikal -> fluchtet mit der Mitte des Logos daneben.
                Padding = new Padding(0, 5, 0, 0),
            };
            // Mail-Bezug als hervorhebbarer Umschalt-Button (Opt-in, Default AUS):
            // aktiv = in Markenfarbe gefuellt; Tooltip erklaert die Funktion.
            _mailButton = new IconButton("✉", "Aktuelle Mail einbeziehen — Bezug auf die geöffnete Mail (an/aus)")
            {
                Toggleable = true,
            };
            _mailButton.Click += (s, e) => ToggleMailContext();
            _settingsButton = new IconButton("⚙", "Einstellungen");   // Zahnrad ⚙
            _settingsButton.Click += SettingsButton_Click;
            _themeButton = new IconButton("☾", "Design wechseln");     // Mond ☾ (Hell -> Dunkel)
            _themeButton.Click += ThemeButton_Click;
            _resetButton = new IconButton("↻", "Verlauf zurücksetzen"); // ↻
            _resetButton.Click += ResetButton_Click;
            iconBar.Controls.Add(_mailButton);
            iconBar.Controls.Add(_settingsButton);
            iconBar.Controls.Add(_themeButton);
            iconBar.Controls.Add(_resetButton);

            row1.Controls.Add(_logo);
            row1.Controls.Add(iconBar);

            _header.Controls.Add(row2);
            _header.Controls.Add(row1);

            // KEINE Akzentleiste am oberen Rand (2026-07-28 eingebaut und wieder
            // entfernt): Sie sollte der Kante der Pane gegen Outlooks dunklen Rahmen
            // den Kontrast nehmen, hat sie aber im Dark Mode erst sichtbar gemacht —
            // dort ist unser Header selbst dunkel, die Kante fiel vorher nicht auf.
            // Betreiber-Befund: "Light Mode heller Strich, Dark Mode blau-tuerkis" =
            // die oberste Pane-Zeile schlaegt jeweils durch.

            // --- Update-Banner (Top, D-3) -----------------------------------------
            // Dezente, klickbare Hinweiszeile — nicht modal, nicht blockierend.
            // Standardmaessig unsichtbar; CheckForUpdateBannerAsync() zeigt sie bei
            // verfuegbarem Update.
            _updateBannerLabel = new Label
            {
                Dock = DockStyle.Fill,
                TextAlign = ContentAlignment.MiddleLeft,
                Font = new Font("Segoe UI", 8.75f),
                Cursor = Cursors.Hand,
                AutoEllipsis = true,
            };
            // Chevron rechts als sichtbares Klick-Signal — der Bannertext selbst
            // bleibt dadurch kurz und laeuft nicht in die AutoEllipsis (die
            // Pane-Mindestbreite schneidet laengere Texte sonst ab).
            _updateBannerChevron = new Label
            {
                Dock = DockStyle.Right,
                Width = 14,
                Text = "›",
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI", 11f),
                Cursor = Cursors.Hand,
            };
            _updateBanner = new Panel
            {
                Dock = DockStyle.Top,
                Height = 26,
                Visible = false,
                Padding = new Padding(14, 4, 14, 4),
                Cursor = Cursors.Hand,
            };
            // Fill zuerst, dann Right — gleiches Docking-Muster wie auf `this`
            // (siehe Kommentar unten): das zuletzt hinzugefuegte Control bekommt
            // seinen Rand-Platz, Fill nimmt den Rest.
            _updateBanner.Controls.Add(_updateBannerLabel);
            _updateBanner.Controls.Add(_updateBannerChevron);
            _updateBanner.Click += (s, e) => SettingsButton_Click(this, EventArgs.Empty);
            _updateBannerLabel.Click += (s, e) => SettingsButton_Click(this, EventArgs.Empty);
            _updateBannerChevron.Click += (s, e) => SettingsButton_Click(this, EventArgs.Empty);

            // Docking-Reihenfolge auf `this`: Fill zuerst, dann Bottom (quick vor
            // composer -> composer ganz unten), dann Top IN UMGEKEHRTER
            // Sichtbarkeitsreihenfolge (bei DockStyle.Top liegt das ZULETZT
            // hinzugefuegte Control oben) -> Banner vor Header, damit der Header
            // ganz oben bleibt. Ergebnis oben->unten: Header, Update-Banner, Log,
            // Composer. NICHT versehentlich umsortieren.
            this.Controls.Add(_log);
            this.Controls.Add(_composerPanel);
            this.Controls.Add(_updateBanner);
            this.Controls.Add(_header);

            this.Resize += (s, e) => AdjustComposerHeight();
            AdjustComposerHeight();

            // --- Drag&Drop-Ziele (DND-01/02) --------------------------------------
            // Drop-Ziel = die gesamte Pane: this, Log, Composer-Panel/-Rahmen und
            // das Eingabefeld selbst.
            EnableDropTarget(this);
            EnableDropTarget(_log);
            EnableDropTarget(_composerPanel);
            EnableDropTarget(_composerFrame);
            EnableDropTarget(_input);
            // Drag&Drop ist in WinForms pro Fensterhandle — jede Sprechblase/Notiz
            // im Log ist ein eigenes Control und wuerde den Drop sonst schlucken,
            // sobald der Chat gefuellt ist. Der Hook zieht jedes neu hinzugefuegte
            // Kind automatisch nach (Bubbles sind Labels ohne eigene Kinder, eine
            // Rekursion ist daher nicht noetig).
            _log.ControlAdded += (s, e) => EnableDropTarget(e.Control);
        }

        /// <summary>Aktiviert Drag&amp;Drop auf <paramref name="c"/> fuer den
        /// Anhang-Drop (DND-01/02). Null-safe, frueher return bei bereits
        /// aktiviertem <c>AllowDrop</c> (kein Doppel-Binding, z. B. wenn
        /// <see cref="BuildUi"/> das Control schon direkt registriert hat und
        /// danach der <c>ControlAdded</c>-Hook erneut feuert).</summary>
        private void EnableDropTarget(Control c)
        {
            if (c == null || c.AllowDrop)
            {
                return;
            }
            c.AllowDrop = true;
            c.DragEnter += DropTarget_DragEnter;
            c.DragOver += DropTarget_DragEnter; // gleiche Logik: Kopier-Cursor stabil halten
            c.DragDrop += DropTarget_DragDrop;
        }

        private void DropTarget_DragEnter(object sender, DragEventArgs e)
        {
            try
            {
                var kind = DropPayloadDecision.Decide(e.Data == null ? null : e.Data.GetFormats()).Kind;
                e.Effect = kind == DropPayloadKind.None ? DragDropEffects.None : DragDropEffects.Copy;
            }
            catch
            {
                e.Effect = DragDropEffects.None;
            }
        }

        private void DropTarget_DragDrop(object sender, DragEventArgs e)
        {
            try
            {
                HandleDrop(e.Data);
            }
            catch (Exception ex)
            {
                AppendSystemLine("Anhang konnte nicht übernommen werden: " + ex.Message);
            }
        }

        /// <summary>Wertet einen abgeschlossenen Drop aus (UI-Thread) und stoesst
        /// bei Erfolg den Upload an (DND-01/02/03).</summary>
        private void HandleDrop(IDataObject data)
        {
            if (_uploadLaeuft)
            {
                AppendSystemLine("Es wird bereits ein Anhang hochgeladen — bitte einen Moment warten.");
                return;
            }
            if (string.IsNullOrEmpty(_settings.BackendUrl) || string.IsNullOrEmpty(_settings.AgentId))
            {
                AppendSystemLine("Bitte zuerst Backend-URL und Agent in den Einstellungen hinterlegen.");
                return;
            }

            TempAttachment anhang;
            int gefundeneDateien;
            string fehler;
            if (!OutlookDropExtractor.TryExtract(data, out anhang, out gefundeneDateien, out fehler))
            {
                AppendSystemLine("Anhang konnte nicht übernommen werden: " + fehler);
                return;
            }

            if (gefundeneDateien > 1)
            {
                // Der Server haelt genau EINEN Pending-Upload je (agent_id,
                // session_id) — Mehrfach-Anhaenge sind bewusst nicht unterstuetzt
                // (known_limits).
                AppendSystemLine(
                    "Es wird nur die erste Datei übernommen: " + anhang.Dateiname
                    + " (je Nachricht ist genau ein Anhang möglich).");
            }

            UploadDroppedAsync(anhang);
        }

        /// <summary>Laedt den extrahierten Anhang hoch und merkt bei Erfolg dessen
        /// Metadaten fuer den naechsten Send (DND-03). <c>async void</c> ist hier
        /// VSTO-Pflicht (Event-Handler-Signatur) und daher vollstaendig von
        /// try/catch umschlossen — eine unbeobachtete Ausnahme wuerde sonst
        /// Outlook mit sich reissen.</summary>
        private async void UploadDroppedAsync(TempAttachment anhang)
        {
            _uploadLaeuft = true;
            // _sessionId in ein Local ziehen, BEVOR der erste await laeuft: die
            // Fortsetzung kann im VSTO-Host auf einem Threadpool-Thread liegen
            // (kein WindowsFormsSynchronizationContext) — Felder erst NACH dem
            // await zu lesen waere ein threadfremder Zugriff (260725-tqr).
            string sessionId = _sessionId;

            AppendSystemLine("Anhang wird hochgeladen: " + anhang.Dateiname + " …");

            try
            {
                using (var client = new ChatClient(_settings))
                // 2-Minuten-Timeout, weil der Chat-HttpClient bewusst
                // Timeout.InfiniteTimeSpan hat (SSE-bedingt) und der Upload sich
                // daher ausschliesslich ueber diesen Token begrenzt.
                using (var cts = new CancellationTokenSource(TimeSpan.FromMinutes(2)))
                {
                    var meta = await client.UploadAttachmentAsync(
                        anhang.Pfad, anhang.Dateiname, sessionId, cts.Token);

                    MarshalToUi(() =>
                    {
                        bool ersetzt = _pendingAttachment != null;
                        _pendingAttachment = meta;
                        AppendSystemLine(
                            (ersetzt ? "Anhang ersetzt: " : "Anhang bereit: ") + meta.AnzeigeText());
                    });
                }
            }
            catch (AttachmentUploadException ex)
            {
                MarshalToUi(() =>
                    AppendSystemLine("Anhang abgelehnt (HTTP " + ex.StatusCode + "): " + ex.Message));
            }
            catch (Exception ex)
            {
                MarshalToUi(() => AppendSystemLine("Anhang fehlgeschlagen: " + ex.Message));
            }
            finally
            {
                MarshalToUi(() => { _uploadLaeuft = false; });
                // Explorer-Originale (IstTemporaer = false) werden NIE geloescht —
                // nur die von TempAttachmentFile.Write erzeugte Temp-Kopie.
                if (anhang.IstTemporaer)
                {
                    TempAttachmentFile.CleanupQuietly(anhang.Pfad);
                }
            }
        }

        /// <summary>
        /// Laedt die Bild-Vorschau-Bytes fuer <paramref name="vorschau"/> und
        /// haengt bei Erfolg eine <see cref="ImageBubble"/> an (Quick 260813-wea,
        /// Task 4). <c>async void</c> ist hier — wie bei
        /// <see cref="UploadDroppedAsync"/> — VSTO-bedingt (der SSE-Callback,
        /// von dem aus dieser Aufruf kommt, kann keinen <c>Task</c>
        /// zurueckgeben) und deshalb VOLLSTAENDIG von try/catch umschlossen:
        /// eine unbeobachtete Ausnahme in einer <c>async void</c>-Methode risse
        /// den gesamten Outlook-Prozess mit (260725-tqr, CLR20r3-Crash-Historie
        /// dieser Datei).
        /// </summary>
        private async void LadeVorschauAsync(ImagePreview vorschau)
        {
            // Felder VOR dem ersten await in Locals ziehen (identisches Muster
            // wie UploadDroppedAsync): die await-Fortsetzung kann im VSTO-Host
            // auf einem Threadpool-Thread liegen (kein
            // WindowsFormsSynchronizationContext) — ein threadfremder
            // Feldzugriff waere derselbe Fehler, der Outlook schon einmal
            // abgeschossen hat.
            var settings = _settings;
            var preview = vorschau;

            try
            {
                using (var client = new ChatClient(settings))
                // Eigener befristeter Token: der Chat-HttpClient hat bewusst
                // Timeout.InfiniteTimeSpan (SSE-bedingt), der Download begrenzt
                // sich deshalb ausschliesslich ueber diesen Token.
                using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(30)))
                {
                    byte[] bytes = await client.DownloadAnhangAsync(
                        preview, ImagePreview.StandardMaxBytes, cts.Token);

                    // JEDE UI-Beruehrung nach dem await laeuft ueber MarshalToUi —
                    // im Outlook-Thread gibt es keinen Synchronisationskontext,
                    // ein threadfremdes Controls.Add hat Outlook bereits einmal
                    // abgeschossen (CLR20r3, 260725-tqr).
                    MarshalToUi(() =>
                    {
                        MemoryStream ms = null;
                        try
                        {
                            ms = new MemoryStream(bytes);
                            Image bild = Image.FromStream(ms);
                            // ms wird NICHT hier disposed -- Image.FromStream
                            // verlangt den offenen Stream fuer die gesamte
                            // Lebensdauer des Bildes (Klassen-Doc-Kommentar
                            // ImageBubble). AddImageBubble/ImageBubble.Dispose
                            // uebernehmen die Freigabe.
                            AddImageBubble(preview, bild, ms);
                        }
                        catch (Exception)
                        {
                            // Kaputte Bytes (Image.FromStream schlaegt fehl) --
                            // eine Vorschau ist Komfort, kein Fehlerfall, der
                            // die Antwort stoeren darf: still verwerfen. Den
                            // Stream selbst disposen, sonst bleibt er haengen.
                            ms?.Dispose();
                        }
                    });
                }
            }
            catch (AnhangDownloadException)
            {
                // Vorschau ist Komfort, kein Fehlerfall -- still verwerfen,
                // NIE die Antwortblase verunstalten.
            }
            catch (Exception)
            {
                // Netz-/TLS-Fehler: ebenfalls still verwerfen (kein
                // Statuscode-Kauderwelsch in der Antwortblase).
            }
        }

        /// <summary>Faerbt die gesamte Pane (inkl. aller selbstgezeichneten Controls)
        /// gemaess der aktuellen <see cref="_pal"/> und zeichnet neu.</summary>
        private void ApplyTheme()
        {
            this.BackColor = _pal.Pane;
            _log.BackColor = _pal.Pane;

            _header.BackColor = _pal.Pane;
            _header.LineColor = _pal.Border;
            foreach (Control c in _header.Controls) c.BackColor = _pal.Pane;
            _logo.BackColor = _pal.Pane;
            _logo.Invalidate();
            _agentCaption.ForeColor = _pal.Faint;
            _agentCaption.BackColor = _pal.Pane;
            _agentChip.Pal = _pal;

            // Verschachtelte Panels (row1/row2) mit einfaerben.
            RecolorContainers(_header);

            // 260728-irn: Update-Banner mitfaerben — sonst bleibt es beim
            // Theme-Wechsel in der alten Farbe stehen (bekanntes Muster in dieser
            // Datei, siehe ThinkingDots/Bubble weiter unten).
            _updateBanner.BackColor = _pal.Surface2;
            _updateBannerLabel.BackColor = _pal.Surface2;
            _updateBannerLabel.ForeColor = _pal.Brand;
            _updateBannerChevron.BackColor = _pal.Surface2;
            _updateBannerChevron.ForeColor = _pal.Brand;

            _mailButton.Pal = _pal;
            _settingsButton.Pal = _pal;
            _themeButton.Pal = _pal;
            _resetButton.Pal = _pal;
            // Mond im Hellmodus (Klick -> Dunkel), Sonne im Dunkelmodus.
            _themeButton.Glyph = _settings.DarkMode ? "☀" : "☾"; // ☀ / ☾

            _composerPanel.BackColor = _pal.Pane;
            _composerFrame.BackColor = _pal.Surface2;
            _composerFrame.BorderColor = _input != null && _input.Focused ? _pal.Brand : _pal.BorderStrong;
            _input.BackColor = _pal.Surface2;
            _input.ForeColor = _pal.Text;
            ApplyInputScrollbarTheme();
            _sendButton.Pal = _pal;
            _footer.BackColor = _pal.Pane;
            _footer.ForeColor = _pal.Faint;

            // Bereits gerenderte Log-Inhalte umfaerben.
            foreach (Control c in _log.Controls)
            {
                if (c is Bubble b)
                {
                    b.BackColor = _pal.Pane;
                    b.BubbleColor = b.IsUser ? _pal.Brand : _pal.AssistantBg;
                    b.ForeColor = b.IsUser ? _pal.OnBrand : _pal.AssistantInk;
                }
                else if (c is ThinkingDots td)
                {
                    td.Pal = _pal;
                    // Wie bei Bubble/Label auch den Hintergrund UM die Blase
                    // mitfärben — sonst bleibt beim Theme-Wechsel während der
                    // laufenden Denk-Animation die Fläche hinter der Blase in der
                    // alten Farbe stehen (OnPaint löscht mit BackColor).
                    td.BackColor = _pal.Pane;
                    td.Invalidate();
                }
                else if (c is ImageBubble ib)
                {
                    // Quick 260813-wea: Panel-Hintergrund + Bildunterschrift
                    // separat mitfaerben (dieselbe Idee wie bei ThinkingDots
                    // direkt oberhalb) — sonst bleibt die Flaeche um das
                    // Vorschaubild beim Theme-Wechsel in der alten Farbe stehen.
                    // Der `is Label`-Zweig darf ImageBubble NICHT schlucken —
                    // ImageBubble erbt von Panel, es besteht also keine
                    // Verwechslungsgefahr; dieser Zweig steht trotzdem VOR dem
                    // Label-Zweig, damit die Reihenfolge zur Bubble-Behandlung
                    // passt.
                    ib.BackColor = _pal.Pane;
                    ib.Caption.BackColor = _pal.Pane;
                    ib.Caption.ForeColor = _pal.Faint;
                    ib.Invalidate();
                }
                else if (c is Label l)
                {
                    l.BackColor = _pal.Pane;
                    // Fehlernotizen bleiben rot, System-/Werkzeugnotizen faint.
                    l.ForeColor = (l.Tag as string) == "error"
                        ? Color.FromArgb(0xE0, 0x5A, 0x5A)
                        : _pal.Faint;
                }
            }

            if (_bottomSpacer != null && !_bottomSpacer.IsDisposed)
            {
                _bottomSpacer.BackColor = _pal.Pane;
            }

            this.Invalidate(true);
        }

        private void RecolorContainers(Control parent)
        {
            foreach (Control c in parent.Controls)
            {
                if (c is Panel && !(c is RoundedPanel) && !(c is BottomBorderPanel))
                {
                    c.BackColor = _pal.Pane;
                }
                if (c.HasChildren)
                {
                    RecolorContainers(c);
                }
            }
        }

        private void ThemeButton_Click(object sender, EventArgs e)
        {
            _settings.DarkMode = !_settings.DarkMode;
            _pal = _settings.DarkMode ? Palette.Dark : Palette.Light;
            ApplyTheme();
            // force: der Theme-Wechsel aendert die Breite nicht — ohne force wuerde der
            // Breiten-Cache das Neuvermessen ueberspringen.
            RelayoutBubbles(force: true);
            // Wahl persistieren (best effort — ein Speicherfehler darf die UI nicht
            // stoeren; das Theme greift trotzdem fuer die laufende Sitzung).
            try { SecureSettingsStore.Save(_settings); }
            catch { /* bewusst geschluckt */ }
        }

        [DllImport("uxtheme.dll", CharSet = CharSet.Unicode)]
        private static extern int SetWindowTheme(IntPtr hWnd, string pszSubAppName, string pszSubIdList);

        /// <summary>Koppelt die native vertikale Scrollbar des Eingabefelds ans Theme:
        /// im Dark Mode „DarkMode_Explorer" (dunkle Scrollbar), sonst „Explorer".
        /// Ohne das bleibt die Scrollbar auf dem dunklen Feld hellgrau. Best effort —
        /// jeder Fehler (aeltere Windows-Version, uxtheme fehlt) wird geschluckt.</summary>
        private void ApplyInputScrollbarTheme()
        {
            if (_input == null || !_input.IsHandleCreated)
            {
                return;
            }
            try
            {
                SetWindowTheme(_input.Handle,
                    _settings.DarkMode ? "DarkMode_Explorer" : "Explorer", null);
            }
            catch { /* bewusst geschluckt */ }
        }

        /// <summary>Laedt den REAL konfigurierten Agent-Namen aus dem Backend
        /// (<see cref="ChatClient.GetAgentIdsAsync"/>) und zeigt ihn im Chip. Faellt
        /// bei Nichterreichbarkeit/Nicht-Treffer still auf die lokale AgentId zurueck
        /// (der Chip ist bereits mit ihr vorbelegt).</summary>
        private async void LoadAgentNameAsync()
        {
            if (string.IsNullOrEmpty(_settings.BackendUrl))
            {
                return;
            }
            try
            {
                using (var client = new ChatClient(_settings))
                using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10)))
                {
                    var ids = await client.GetAgentIdsAsync(cts.Token);
                    string configured = _settings.AgentId ?? "";
                    string match = ids.Find(id =>
                        string.Equals(id, configured, StringComparison.OrdinalIgnoreCase));
                    if (!string.IsNullOrEmpty(match))
                    {
                        MarshalToUi(() => { _agentChip.Text = match; _agentChip.Invalidate(); });
                    }
                }
            }
            catch
            {
                // Backend nicht erreichbar -> lokale AgentId im Chip belassen.
            }
        }

        /// <summary>Prueft, ob ein neueres Produkt-Release existiert, und
        /// zeigt/aktualisiert/versteckt bei Bedarf den dezenten, nicht-modalen
        /// Banner (260728-irn, D-3; Ausblenden + Re-Check 260731-tu1). Laeuft
        /// nicht mehr nur einmal beim Pane-Start, sondern zusaetzlich nach
        /// jedem Schliessen des Einstellungen-Dialogs (<see cref="SettingsButton_Click"/>),
        /// weil ein erfolgreiches Self-Update den Banner sonst bis zum
        /// Outlook-Neustart stehen liess. Fire-and-forget wie
        /// <see cref="LoadAgentNameAsync"/> — kein `await` im Konstruktor, der
        /// Check darf den Pane-/Outlook-Start nicht verzoegern. Die eigentliche
        /// Show/Hide/Leave-Entscheidung liegt in <see cref="UpdateBannerDecision"/>
        /// (unter Test): nur ein autoritativer Server-Status (`status == "ok"`)
        /// darf den Banner ausblenden — bei Netz-/HTTP-Fehler oder
        /// nicht-autoritativer Antwort (z. B. `unreachable`, weil die WebUI
        /// gerade neu startet) bleibt er unveraendert stehen.</summary>
        private async void CheckForUpdateBannerAsync()
        {
            if (string.IsNullOrEmpty(_settings.BackendUrl))
            {
                // Kein Check moeglich -> Zustand UNVERAENDERT lassen, nicht
                // ausblenden (BAN-02): fehlende Konfiguration ist keine
                // Aussage ueber Update-Verfuegbarkeit.
                return;
            }

            // VSTO-Pflicht: `async void` ohne umschliessendes try/catch kann
            // Outlook abschiessen, falls doch mal etwas Unerwartetes ausserhalb
            // des Netzwerk-Aufrufs wirft (Vorbild: UpdateButton_Click in
            // SettingsDialog).
            try
            {
                UpdateInfo info = null;
                bool checkSucceeded = false;
                try
                {
                    using (var client = new ChatClient(_settings))
                    using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8)))
                    {
                        info = await client.GetUpdateInfoAsync(cts.Token);
                        checkSucceeded = true;
                    }
                }
                catch
                {
                    // Netz-/HTTP-Fehler -> Bannerzustand bewusst UNVERAENDERT
                    // lassen (BAN-02). Die WebUI startet nach einem Update kurz
                    // neu — ein Abbruch hier ist ERWARTET und darf nicht als
                    // "alles aktuell" gedeutet werden.
                }

                var action = UpdateBannerDecision.Decide(
                    checkSucceeded, info?.Status, info != null && info.UpdateAvailable, info?.Latest);
                if (action == UpdateBannerAction.Leave)
                {
                    // Bewusst VOR dem Marshalling: im Leave-Fall findet gar
                    // kein UI-Zugriff statt.
                    return;
                }

                string latest = info == null ? "" : info.Latest;
                MarshalToUi(() =>
                {
                    if (action == UpdateBannerAction.Show)
                    {
                        // Kurz halten: das Banner ist selbst klickbar und
                        // fuehrt in die Einstellungen — ein Hinweis darauf
                        // wuerde bei schmalem Pane nur abgeschnitten. Text bei
                        // jedem Show neu setzen, damit eine inzwischen andere
                        // gemeldete Version nicht als alter Text stehenbleibt.
                        _updateBannerLabel.Text = "Update auf " + latest + " verfügbar";
                        _updateBanner.Visible = true;
                    }
                    else
                    {
                        _updateBanner.Visible = false;
                    }
                });
            }
            catch
            {
                // Siehe Klassenkommentar VSTO-Pflicht oben — Banner bleibt in
                // seinem letzten bekannten Zustand.
            }
        }

        // --- Logo/Settings-Helfer ----------------------------------------------

        /// <summary>Laedt das komplette, transparent freigestellte Vizpatch-Logo als
        /// eingebettete Ressource (funktioniert in Hell und Dunkel), faellt auf das
        /// alte grau hinterlegte Logo bzw. das Emblem zurueck. Jeder Fehler ->
        /// <c>null</c> (keine Anzeige, nie Absturz).</summary>
        private static Image LoadLogoSafe()
        {
            // Bevorzugt das transparent freigestellte Voll-Logo; faellt auf das alte
            // (grau hinterlegte) Logo bzw. das Emblem zurueck, falls es fehlt.
            return LoadEmbeddedPng("vizpatch_logo_transparent.png")
                ?? LoadEmbeddedPng("vizpatch_logo.png")
                ?? LoadEmbeddedPng("vizpatch_emblem.png");
        }

        /// <summary>Laedt das kompakte, transparent freigestellte Emblem (nur das
        /// Hexagon-Symbol) fuer die schmale Header-Darstellung. Null bei Fehler.</summary>
        private static Image LoadEmblemImageSafe()
        {
            return LoadEmbeddedPng("vizpatch_emblem.png");
        }

        private static Image LoadEmbeddedPng(string suffix)
        {
            try
            {
                var asm = Assembly.GetExecutingAssembly();
                foreach (var name in asm.GetManifestResourceNames())
                {
                    if (name.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
                    {
                        using (var stream = asm.GetManifestResourceStream(name))
                        {
                            if (stream != null)
                            {
                                using (var img = Image.FromStream(stream))
                                {
                                    return new Bitmap(img);
                                }
                            }
                        }
                    }
                }
            }
            catch
            {
                // bewusst geschluckt — kein Bild ist besser als ein Absturz.
            }
            return null;
        }

        private static AddinSettings LoadSettingsSafe()
        {
            try
            {
                return SecureSettingsStore.Load();
            }
            catch
            {
                return new AddinSettings();
            }
        }

        private void ShowConfigHintIfNeeded()
        {
            if (string.IsNullOrEmpty(_settings.BackendUrl) || string.IsNullOrEmpty(_settings.AgentId))
            {
                AppendSystemLine(
                    "Hinweis: Backend-URL/Agent-ID noch nicht konfiguriert. "
                    + "Bitte oben über das Zahnrad (Einstellungen) hinterlegen.");
            }
        }

        // Feste Start-Begrüßung — bewusst OHNE LLM (statischer Text, kein Backend-Call).
        // Das "(KI)" ist KEINE Stilfrage, sondern Kennzeichnungspflicht: der Nutzer muss
        // erkennen, dass er mit einem KI-System spricht (KI-Transparenzpflichten, seit
        // 2026-08-02 anwendbar). Nicht entfernen und nicht umformulieren, ohne das zu
        // prüfen — der Regressionstest im Backend hängt an genau dieser Kennzeichnung.
        private const string GreetingText =
            "Hallo, ich bin Ihr (KI) E-Mail-Assistent. Wie kann ich Ihnen behilflich sein?";

        // Tipp-Effekt der Begrüßung: die Blase erscheint nicht fertig, sondern wird
        // Zeichen für Zeichen aufgebaut. Bewusst über System.Windows.Forms.Timer (tickt
        // auf dem UI-Thread) — dasselbe Muster wie ThinkingDots. Ein Threadpool-Timer
        // würde die Blase aus einem fremden Thread anfassen und Outlook abschießen.
        private const int GreetingTickMs = 25;
        private const int GreetingCharsPerTick = 2;
        private System.Windows.Forms.Timer _greetingTimer;
        private Bubble _greetingBubble;
        private string _greetingFullText;
        private int _greetingShownChars;

        // Die zuletzt angelegte Begrüßungsblase — auch NACH dem Ende des Tipp-Effekts
        // noch gültig (anders als `_greetingBubble`, das StopGreetingAnimation
        // aufräumt). Nötig, um sie beim erneuten Öffnen der Pane gegen eine frisch
        // getippte auszutauschen, statt eine zweite darunter zu hängen.
        private Bubble _lastGreetingBubble;

        /// <summary>
        /// Meldet, dass die Task-Pane sichtbar geworden ist, und spielt die Begrüßung
        /// ab — der Tipp-Effekt soll vor Augen ablaufen, nicht schon beim Start von
        /// Outlook gegen eine unsichtbare Pane.
        ///
        /// Das passiert bei JEDEM Einblenden, solange der Betreiber in dieser Sitzung
        /// noch nichts geschrieben hat (Betreiber-Wunsch 2026-08-16). Sobald ein
        /// Wortwechsel im Verlauf steht, bleibt die Pane still: dort ist die
        /// Begrüßung nur noch die oberste Zeile eines laufenden Gesprächs, und sie
        /// neu zu tippen sähe aus, als hätte der Verlauf von vorn begonnen.
        /// </summary>
        public void NotifyPaneShown()
        {
            if (_history.Count > 0)
            {
                return;
            }
            RestartGreeting();
        }

        /// <summary>
        /// Tauscht eine bereits stehende Begrüßung gegen eine frisch getippte aus.
        /// Ohne das Entfernen hinge beim zweiten Öffnen eine zweite Begrüßung unter
        /// der ersten.
        ///
        /// Reihenfolge ist Pflicht: erst den Timer stoppen, DANN die Blase entsorgen.
        /// Umgekehrt tickt der Effekt gegen ein entferntes Control weiter — derselbe
        /// Fehler, der in dieser Datei schon zweimal aufgetreten ist (Denk-Animation,
        /// Reset-Pfad).
        /// </summary>
        private void RestartGreeting()
        {
            StopGreetingAnimation();

            if (_lastGreetingBubble != null)
            {
                if (!_lastGreetingBubble.IsDisposed)
                {
                    _log.Controls.Remove(_lastGreetingBubble);
                    _lastGreetingBubble.Dispose();
                }
                _lastGreetingBubble = null;
            }

            ShowGreeting();
        }

        /// <summary>
        /// Zeigt eine statische Begrüßung als Assistenten-Blase — beim ersten Einblenden
        /// der Pane (über <see cref="NotifyPaneShown"/>) und nach „Neuer Chat". Rein
        /// clientseitig, kein LLM-/Backend-Call.
        /// Nur wenn ein Agent konfiguriert ist — sonst steht bereits der
        /// Konfigurations-Hinweis und ein „Wie kann ich helfen?" wäre irreführend.
        /// Landet NICHT in <c>_history</c>, geht also nie in den LLM-Kontext.
        /// </summary>
        /// <returns><c>true</c>, wenn der Effekt angelaufen ist; <c>false</c>, wenn
        /// mangels Konfiguration bewusst nichts gezeigt wurde.</returns>
        private bool ShowGreeting()
        {
            if (string.IsNullOrEmpty(_settings.BackendUrl) || string.IsNullOrEmpty(_settings.AgentId))
            {
                return false;
            }

            // Einen ggf. noch laufenden Effekt zuerst abräumen: sonst tickt der alte
            // Timer gegen eine bereits entfernte Blase weiter (genau der Fehler, der
            // beim Reset schon einmal mit dem Denk-Animations-Timer passiert ist).
            StopGreetingAnimation();

            _greetingFullText = GreetingText;
            // Mit dem ERSTEN Zeichen starten, nicht mit einem Leerstring: eine leere
            // Blase misst sich zu Nullgröße und würde kurz als Artefakt aufblitzen.
            _greetingShownChars = 1;
            _greetingBubble = AddBubble(_greetingFullText.Substring(0, 1), false);
            // Zweite Referenz, die den Effekt überlebt: StopGreetingAnimation räumt
            // `_greetingBubble` auf, beim nächsten Öffnen der Pane muss die Blase aber
            // noch auffindbar sein, um sie gegen eine frisch getippte zu tauschen.
            _lastGreetingBubble = _greetingBubble;

            _greetingTimer = new System.Windows.Forms.Timer { Interval = GreetingTickMs };
            _greetingTimer.Tick += GreetingTimer_Tick;
            _greetingTimer.Start();
            return true;
        }

        /// <summary>
        /// Ein Tick des Tipp-Effekts: hängt die nächsten Zeichen an und misst die Blase
        /// neu. Läuft auf dem UI-Thread (WinForms-Timer), deshalb ist der direkte
        /// Control-Zugriff hier zulässig.
        /// </summary>
        private void GreetingTimer_Tick(object sender, EventArgs e)
        {
            if (_greetingBubble == null || _greetingBubble.IsDisposed || _greetingFullText == null)
            {
                StopGreetingAnimation();
                return;
            }

            _greetingShownChars = Math.Min(
                _greetingShownChars + GreetingCharsPerTick, _greetingFullText.Length);
            _greetingBubble.Text = _greetingFullText.Substring(0, _greetingShownChars);
            LayoutBubble(_greetingBubble);

            if (_greetingShownChars >= _greetingFullText.Length)
            {
                StopGreetingAnimation();
            }
        }

        /// <summary>
        /// Zeigt die Begrüßung sofort vollständig und beendet den Effekt. Wird
        /// aufgerufen, sobald der Betreiber selbst etwas sendet — eine halb getippte
        /// Begrüßung über der eigenen Nachricht stehen zu lassen, sähe nach einem
        /// Fehler aus.
        /// </summary>
        private void FinishGreetingAnimation()
        {
            if (_greetingBubble != null && !_greetingBubble.IsDisposed && _greetingFullText != null)
            {
                _greetingBubble.Text = _greetingFullText;
                LayoutBubble(_greetingBubble);
            }
            StopGreetingAnimation();
        }

        /// <summary>
        /// Stoppt den Tipp-Effekt und gibt den Timer frei. Idempotent — darf mehrfach
        /// aufgerufen werden. MUSS laufen, BEVOR die Blasen aus dem Log entfernt werden
        /// (Reset), sonst tickt der Timer verwaist gegen ein entferntes Control weiter.
        /// </summary>
        private void StopGreetingAnimation()
        {
            if (_greetingTimer != null)
            {
                _greetingTimer.Stop();
                _greetingTimer.Tick -= GreetingTimer_Tick;
                _greetingTimer.Dispose();
                _greetingTimer = null;
            }
            _greetingBubble = null;
            _greetingFullText = null;
            _greetingShownChars = 0;
        }

        // --- Tipp-Effekt der Agenten-Antworten -----------------------------------
        //
        // Betreiber-Wunsch 2026-08-16: eine Antwort soll nach der Denk-Animation
        // getippt erscheinen wie die Begrüßung — aber NUR beim Eintreffen. Schließt
        // man die Pane und öffnet sie wieder, stehen alle Nachrichten einfach da.
        // Das ergibt sich von selbst: getippt wird im Empfangspfad, die fertigen
        // Blasen bleiben unangetastet im Log.
        //
        // Der Haken: die Antwort kommt bereits gestreamt an (seit Quick 260816-0or
        // in vielen kleinen Chunks). Ein Tipp-Effekt kann deshalb nicht auf den
        // fertigen Text warten — er legt sich als PUFFER dazwischen: ankommende
        // Chunks wandern in `_typeQueue`, der Timer gibt sie zeichenweise aus.
        //
        // Damit lange Antworten nicht minutenlang tröpfeln, ist die Rate am
        // Rückstand orientiert (`TypeCatchUpDivisor`) und nach oben gedeckelt
        // (`TypeMaxCharsPerTick`): ~1,3 s für eine sehr lange Antwort, bei kurzen
        // bleibt das ruhige Zeichen-für-Zeichen. Fester Wert wäre hier falsch —
        // 2 Zeichen/Tick sind bei 2.000 Zeichen 25 Sekunden Warten.
        private const int TypeTickMs = 25;
        private const int TypeMinCharsPerTick = 2;
        private const int TypeMaxCharsPerTick = 40;
        private const int TypeCatchUpDivisor = 20;
        private System.Windows.Forms.Timer _typeTimer;
        private readonly System.Text.StringBuilder _typeQueue = new System.Text.StringBuilder();

        /// <summary>
        /// Nimmt einen Text-Chunk der laufenden Antwort entgegen und lässt ihn
        /// tippen, statt ihn sofort ganz anzuzeigen.
        /// </summary>
        private void EnqueueAssistantText(string chunk)
        {
            if (string.IsNullOrEmpty(chunk))
            {
                return;
            }
            _typeQueue.Append(chunk);
            if (_typeTimer == null)
            {
                _typeTimer = new System.Windows.Forms.Timer { Interval = TypeTickMs };
                _typeTimer.Tick += TypeTimer_Tick;
                _typeTimer.Start();
            }
        }

        /// <summary>
        /// Ein Tick: gibt die nächsten Zeichen aus dem Puffer aus. Läuft auf dem
        /// UI-Thread (WinForms-Timer) — derselbe Grund wie beim Begrüßungs-Effekt:
        /// ein Threadpool-Timer würde die Blase aus einem fremden Thread anfassen
        /// und Outlook abschießen.
        /// </summary>
        private void TypeTimer_Tick(object sender, EventArgs e)
        {
            if (_typeQueue.Length == 0)
            {
                StopTypingAnimation();
                return;
            }

            int schritt = Math.Max(TypeMinCharsPerTick, _typeQueue.Length / TypeCatchUpDivisor);
            if (schritt > TypeMaxCharsPerTick)
            {
                schritt = TypeMaxCharsPerTick;
            }
            if (schritt > _typeQueue.Length)
            {
                schritt = _typeQueue.Length;
            }

            string naechste = _typeQueue.ToString(0, schritt);
            _typeQueue.Remove(0, schritt);
            WriteAssistantText(naechste);

            if (_typeQueue.Length == 0)
            {
                StopTypingAnimation();
            }
        }

        /// <summary>
        /// Schreibt Text unmittelbar in die laufende Antwortblase (ohne Effekt) und
        /// legt sie an, falls sie noch nicht existiert. Start mit dem ersten Zeichen
        /// statt leer — eine leere Blase misst sich zu Nullgröße und blitzt als
        /// Artefakt auf (dieselbe Lehre wie beim Begrüßungs-Effekt).
        /// </summary>
        private void WriteAssistantText(string text)
        {
            if (string.IsNullOrEmpty(text))
            {
                return;
            }
            if (_currentAssistant == null)
            {
                _currentAssistant = AddBubble(text, false);
            }
            else
            {
                _currentAssistant.Text += text;
                LayoutBubble(_currentAssistant);
                ScrollToEnd();
            }
        }

        /// <summary>
        /// Gibt einen noch gepufferten Rest SOFORT vollständig aus und beendet den
        /// Effekt. MUSS vor jedem Wechsel der Antwortblase laufen (neue Nutzer-Blase,
        /// Werkzeug-Schritt, Bild, Fehler) — sonst landet der Rest der alten Antwort
        /// in der falschen Blase oder geht ganz verloren.
        /// </summary>
        private void FinishTypingNow()
        {
            if (_typeQueue.Length > 0)
            {
                WriteAssistantText(_typeQueue.ToString());
                _typeQueue.Clear();
            }
            StopTypingAnimation();
        }

        /// <summary>
        /// Stoppt den Tipp-Effekt und gibt den Timer frei. Idempotent. Verwirft einen
        /// noch gepufferten Rest — nur dort aufrufen, wo der Text ohnehin wegfällt
        /// (Reset, Dispose, Abbruch). Wo der Text erhalten bleiben soll, gehört
        /// <see cref="FinishTypingNow"/> hin.
        ///
        /// MUSS laufen, BEVOR die Blasen aus dem Log entfernt werden (Reset), sonst
        /// tickt der Timer gegen ein entferntes Control weiter — derselbe Fehler, der
        /// in dieser Datei schon mit der Denk-Animation und mit dem Begrüßungs-Timer
        /// aufgetreten ist.
        /// </summary>
        private void StopTypingAnimation()
        {
            if (_typeTimer != null)
            {
                _typeTimer.Stop();
                _typeTimer.Tick -= TypeTimer_Tick;
                _typeTimer.Dispose();
                _typeTimer = null;
            }
            _typeQueue.Clear();
        }

        // --- Mail-Bezug-Chip ----------------------------------------------------

        /// <summary>Schaltet den Mail-Bezug um (Opt-in, Default AUS -> D-86). Der
        /// Header-Button wird bei aktivem Bezug in Markenfarbe hervorgehoben.</summary>
        private void ToggleMailContext()
        {
            _mailActive = !_mailActive;
            _mailButton.Active = _mailActive;
            _mailButton.Invalidate();
        }

        // --- Eingabe / Senden ---------------------------------------------------

        private void Input_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Enter && !e.Shift)
            {
                e.SuppressKeyPress = true;
                TriggerSend();
            }
        }

        private void SendButton_Click(object sender, EventArgs e)
        {
            TriggerSend();
        }

        private void ResetButton_Click(object sender, EventArgs e)
        {
            CancelActiveRequest();

            // Denk-Animation SAUBER stoppen (stoppt den Timer + disposed das Control)
            // BEVOR der Log geleert wird. Vorher wurde nur `_thinking = null` gesetzt —
            // der laufende Animations-Timer blieb dann verwaist am (nicht disposten)
            // Control hängen und tickte weiter gegen ein entferntes Control, was den
            // Reset "hängen" ließ.
            HideThinking();
            // Aus demselben Grund auch die beiden Tipp-Effekte: die Blasen, gegen die
            // sie ticken, werden weiter unten aus dem Log entfernt und entsorgt. Hier
            // bewusst StopTypingAnimation (verwirft den Rest) statt FinishTypingNow —
            // der Verlauf wird ohnehin komplett geleert.
            StopGreetingAnimation();
            StopTypingAnimation();
            // Die Begrüßung wird gleich neu getippt; die alte Referenz zeigt danach auf
            // ein entsorgtes Control.
            _lastGreetingBubble = null;

            _history.Clear();
            _sessionId = SessionIdGenerator.NewSessionId();
            // Die neue session_id macht den serverseitigen Pending-Upload ohnehin
            // unerreichbar — ein weiter gemerktes Meta wuerde nur eine Datei
            // versprechen, die der naechste Turn nicht mehr findet (DND-04).
            _pendingAttachment = null;

            // Controls nicht nur entfernen, sondern entsorgen (GDI-Handles/Ressourcen
            // freigeben) — sonst akkumulieren verwaiste Controls über viele Resets.
            _log.SuspendLayout();
            while (_log.Controls.Count > 0)
            {
                var c = _log.Controls[_log.Controls.Count - 1];
                _log.Controls.Remove(c);
                c.Dispose();
            }
            _log.ResumeLayout();

            _currentAssistant = null;
            ShowConfigHintIfNeeded();
            // Nach „Neuer Chat" ist die Pane sichtbar, der Effekt laeuft also vor Augen
            // ab. Der Verlauf ist jetzt leer — ein spaeteres Einblenden tippt die
            // Begruessung deshalb wieder neu, statt eine zweite darunter zu haengen
            // (RestartGreeting tauscht sie aus).
            ShowGreeting();
        }

        // Frueher standen hier feste Chrome-/Padding-Werte (36 bzw. 16). Sie sind
        // entfallen: AdjustComposerHeight liest die Abstaende jetzt direkt aus Footer,
        // Panel und Rahmen — eine der Konstanten war aus dem Ruder gelaufen und machte
        // das Textfeld dauerhaft 4 px zu niedrig.

        /// <summary>Haelt das Eingabefeld klein und laesst es mit dem Text mitwachsen
        /// (Start ~2 Zeilen, gedeckelt auf hoechstens 32% der Pane-Hoehe).</summary>
        private void AdjustComposerHeight()
        {
            if (_composerPanel == null || _input == null)
            {
                return;
            }
            int total = this.ClientSize.Height;
            if (total <= 0)
            {
                return;
            }

            // Alle Abstaende AUS DEN CONTROLS lesen statt aus Konstanten: die frueheren
            // Festwerte (Chrome 36 = "Footer 18 + Panel-Padding 8+10") passten nicht
            // mehr zum echten Panel-Padding (10+12=22) -> Chrome ist 40. Das Textfeld
            // war dadurch immer 4 px zu niedrig: die letzte Zeile wurde angeschnitten,
            // die TextBox scrollte zum Cursor und die ERSTE Zeile rutschte aus dem
            // Blickfeld (Betreiber-Meldung "bei zwei Zeilen sieht man nur die zweite").
            int chromeHeight = _footer.Height + _composerPanel.Padding.Vertical;
            int framePadding = _composerFrame.Padding.Vertical;

            int textWidth = _composerPanel.ClientSize.Width
                - _composerPanel.Padding.Horizontal
                - _composerFrame.Padding.Horizontal;
            if (textWidth < 40) textWidth = 40;

            string probe = _input.Text.Length > 0 ? _input.Text : "Ag";
            int textHeight = TextRenderer.MeasureText(
                probe, _input.Font, new Size(textWidth, int.MaxValue),
                TextFormatFlags.WordBreak | TextFormatFlags.TextBoxControl).Height;

            int lineHeight = _input.Font.Height;
            int minTextHeight = lineHeight * 2;
            if (textHeight < minTextHeight) textHeight = minTextHeight;

            int panelHeight = textHeight + framePadding + chromeHeight;
            int minPanel = minTextHeight + framePadding + chromeHeight;
            int cap = (int)Math.Round(total * 0.32);
            int maxPanel = Math.Max(cap, minPanel);
            if (panelHeight < minPanel) panelHeight = minPanel;
            if (panelHeight > maxPanel) panelHeight = maxPanel;

            // Nur bei echter Aenderung zuweisen: ein Height-Setter mit identischem Wert
            // loest zwar kein Layout aus, der Aufruf kommt aber bei JEDEM Resize-Schritt
            // — und jede echte Zuweisung reflowt Composer + Log (Dock Fill) komplett.
            if (_composerPanel.Height != panelHeight)
            {
                _composerPanel.Height = panelHeight;
            }
            PositionSendButton();
        }

        /// <summary>Setzt den 34x34-Sende-Button fix in den rechten Gutter unten;
        /// er ueberlappt so nie das Textfeld (das Dock-Fill den linken Bereich
        /// fuellt). Wird bei jedem Layout des Rahmens aufgerufen -> auch beim ersten
        /// Paint korrekt platziert.</summary>
        private void PositionSendButton()
        {
            if (_composerFrame == null || _sendButton == null)
            {
                return;
            }
            int w = _composerFrame.ClientSize.Width;
            int h = _composerFrame.ClientSize.Height;
            if (w < 40 || h < 40) return;

            var target = new Point(w - 40, h - 34 - 8);
            if (_sendButton.Location != target)
            {
                _sendButton.Location = target;
                // Beim Mitwachsen des Feldes wandert der Button nach unten. Ohne
                // explizites Neuzeichnen blieb an der alten Stelle ein Rest stehen und
                // der Button wirkte oben "abgeschnitten" (Betreiber-Screenshot mit
                // weissem Balken ueber dem blauen Kreis). Rahmen zuerst, dann Button.
                _composerFrame.Invalidate();
                _sendButton.Invalidate();
            }
            _sendButton.BringToFront();
        }

        private void TriggerSend()
        {
            string message = _input.Text.Trim();
            if (message.Length == 0 || !_sendButton.Enabled)
            {
                return;
            }
            _input.Clear();
            SendAsync(message);
        }

        private async void SendAsync(string message)
        {
            CancelActiveRequest();
            var cts = new CancellationTokenSource();
            _activeCts = cts;

            _sendButton.Enabled = false;

            var assistantText = new System.Text.StringBuilder();
            bool sawError = false;

            try
            {
                MailContext mailContext = null;
                if (_mailActive)
                {
                    mailContext = TryBuildMailContextSafe();
                    if (mailContext == null)
                    {
                        AppendSystemLine(
                            "Hinweis: Keine Mail als Kontext gefunden "
                            + "(keine Mail geöffnet/markiert oder Nicht-Mail-Element).");
                    }
                }

                // Tippt die Begrüßung noch, sofort fertigstellen — eine halb getippte
                // Blase über der eigenen Nachricht sähe nach einem Fehler aus.
                FinishGreetingAnimation();

                AppendUserBubble(message);
                BeginAssistantBubble();
                ShowThinking();

                // Vor dem Await festhalten (260725-tqr): dieser Send konsumiert
                // genau den JETZT gemerkten Anhang — ein zwischenzeitlicher neuer
                // Drop waehrend des laufenden Sends darf den bereits laufenden
                // Turn nicht mehr veraendern.
                var anhangFuerDiesenSend = _pendingAttachment;

                try
                {
                    using (var client = new ChatClient(_settings))
                    {
                        await client.StreamChatAsync(
                            message,
                            _history,
                            mailContext,
                            _sessionId,
                            (evt, data) => MarshalToUi(() =>
                            {
                                switch (evt)
                                {
                                    case "tool":
                                        // Postfach-/Werkzeug-Schritte NICHT anzeigen
                                        // (Betreiber-Wunsch): der Agent arbeitet ->
                                        // die "…"-Denk-Animation laeuft einfach weiter.
                                        // Eine ggf. bereits laufende Antwortblase
                                        // abschliessen, damit spaeterer Text neu beginnt,
                                        // und die Animation sicher (wieder) anzeigen.
                                        // FinishTypingNow zuerst: ein noch getippter
                                        // Rest gehoert in die ALTE Blase, nicht in die
                                        // naechste und schon gar nicht in den Papierkorb.
                                        FinishTypingNow();
                                        _currentAssistant = null;
                                        ShowThinking();
                                        break;
                                    case "done":
                                        HideThinking();
                                        break;
                                    case "error":
                                        HideThinking();
                                        AppendErrorLine(data);
                                        sawError = true;
                                        break;
                                    case "bild":
                                        // Quick 260813-wea: Referenz-Ereignis, NIE
                                        // Bilddaten. Rumpf bewusst minimal — die
                                        // Anzeige-Entscheidung liegt in
                                        // ImagePreviewDecision (Core, testbar ohne
                                        // Outlook), der eigentliche Download in
                                        // LadeVorschauAsync.
                                        var vorschau = ImagePreview.FromJson(data);
                                        if (vorschau == null) break;
                                        var entscheidung = ImagePreviewDecision.Pruefe(
                                            vorschau, ImagePreview.StandardMaxBytes);
                                        if (!entscheidung.Anzeigen) break;
                                        LadeVorschauAsync(vorschau);
                                        break;
                                    default: // "message" = Text-Chunk
                                        HideThinking();
                                        assistantText.Append(data);
                                        AppendAssistantChunk(data);
                                        break;
                                }
                            }),
                            cts.Token,
                            anhangFuerDiesenSend);
                    }
                }
                catch (OperationCanceledException)
                {
                    sawError = true;
                }
                catch (Exception ex)
                {
                    AppendErrorLine(ex.Message);
                    sawError = true;
                }

                if (!sawError)
                {
                    _history.Add(new ChatTurn("user", message));
                    _history.Add(new ChatTurn("assistant", assistantText.ToString()));

                    if (anhangFuerDiesenSend != null)
                    {
                        // Bewusst NUR im Erfolgsfall zuruecksetzen — exakt wie
                        // webui/static/chat.js: bei Fehler bleibt der Anhang
                        // gemerkt, ein erneuter Send nutzt denselben, noch nicht
                        // konsumierten Pending-Upload.
                        _pendingAttachment = null;
                        AppendSystemLine("Anhang mitgesendet: " + anhangFuerDiesenSend.Dateiname);
                    }
                }
            }
            finally
            {
                HideThinking();
                if (_activeCts == cts)
                {
                    _sendButton.Enabled = true;
                    _activeCts = null;
                }
                cts.Dispose();
            }
        }

        private void CancelActiveRequest()
        {
            var cts = _activeCts;
            if (cts == null)
            {
                return;
            }
            try { cts.Cancel(); }
            catch (ObjectDisposedException) { /* bereits abgeschlossen */ }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                CancelActiveRequest();
                _activeCts = null;
                _thinking?.StopAnimation();
                StopGreetingAnimation();
                StopTypingAnimation();
            }
            base.Dispose(disposing);
        }

        private MailContext TryBuildMailContextSafe()
        {
            try
            {
                var addin = Globals.ThisAddIn;
                var app = addin != null ? addin.Application : null;
                return app != null ? MailContextReader.TryBuildMailContext(app) : null;
            }
            catch
            {
                return null;
            }
        }

        // --- Einstellungs-Gate --------------------------------------------------

        private async void SettingsButton_Click(object sender, EventArgs e)
        {
            var current = LoadSettingsSafe();
            if (!string.IsNullOrWhiteSpace(current.BackendUrl))
            {
                if (!await AuthorizeSettingsAsync(current))
                {
                    return;
                }
            }

            bool updateAngefordert = false;
            string updateZielVersion = "";
            bool updateNurBeobachten = false;

            using (var dialog = new SettingsDialog())
            {
                var result = dialog.ShowDialog(HostOwnerWindow());
                // Betreiber-Wunsch 2026-08-22: hat der Betreiber im Dialog
                // „Jetzt updaten" gedrueckt (oder lief serverseitig schon ein
                // Update), ist der Dialog bereits GESCHLOSSEN und das
                // Update-Fenster uebernimmt. Die Flags werden hier
                // ausgelesen, das Fenster aber erst NACH dem using-Block
                // geoeffnet — ein zweiter modaler Dialog aus dem noch nicht
                // freigegebenen ersten heraus waere ein verschachtelter
                // Modal-Aufruf mit unklarem Besitzer.
                updateAngefordert = dialog.UpdateRequested;
                updateZielVersion = dialog.UpdateTargetVersion;
                updateNurBeobachten = dialog.UpdateAttachOnly;

                // Diese Fortsetzung kann im VSTO-Host auf einem Threadpool-Thread
                // laufen (kein WindowsFormsSynchronizationContext) — alle
                // UI-Zugriffe (Chip-Text, Notiz, Hinweis, Banner-Re-Check) daher
                // explizit auf den UI-Thread marshallen, sonst threadfremder
                // Control-Zugriff -> ArgumentException -> Outlook-Crash (260725-tqr).
                MarshalToUi(() =>
                {
                    if (result == DialogResult.OK)
                    {
                        // DarkMode ist eine reine UI-Praeferenz und wird vom Dialog
                        // unveraendert durchgereicht — die laufende Wahl beibehalten.
                        bool dark = _settings.DarkMode;
                        _settings = LoadSettingsSafe();
                        _settings.DarkMode = dark;
                        _agentChip.Text = _settings.AgentId ?? "";
                        AppendSystemLine("Einstellungen gespeichert.");
                        ShowConfigHintIfNeeded();
                        LoadAgentNameAsync();
                    }

                    // Re-Check UNABHAENGIG vom DialogResult (BAN-03): der
                    // Update-Flow endet nicht zwingend mit OK — der Betreiber
                    // schliesst den Dialog nach dem Update oft mit X/Abbrechen.
                    // Genau dann muss der Banner trotzdem verschwinden.
                    // Bewusst INNERHALB von MarshalToUi: der Aufruf liest
                    // _settings und laeuft damit garantiert NACH dem
                    // _settings = LoadSettingsSafe() weiter oben in derselben
                    // Action — sonst prueft er gegen eine gerade geaenderte,
                    // noch nicht uebernommene Backend-URL. Zusaetzlich sind wir
                    // so sicher auf dem UI-Thread (260725-tqr). Kehrt am ersten
                    // `await` zurueck (async void) und blockiert den UI-Thread
                    // daher nicht.
                    if (updateAngefordert)
                    {
                        // Blockierendes Update-Fenster: erst danach den Banner
                        // pruefen, sonst liefe der Check gegen den Zustand VOR
                        // dem Update und der Banner blieb stehen.
                        ShowUpdateWindow(updateZielVersion, updateNurBeobachten);
                    }
                    CheckForUpdateBannerAsync();
                });
            }
        }

        /// <summary>
        /// Oeffnet das nicht wegklickbare Update-Fenster (Betreiber-Wunsch
        /// 2026-08-22) und laeuft erst weiter, wenn der Betreiber dort „OK"
        /// gedrueckt hat. Ein Fehler beim Oeffnen darf die Pane nicht
        /// mitnehmen — dann bleibt nur eine Systemzeile im Chat.
        /// </summary>
        private void ShowUpdateWindow(string zielVersion, bool nurBeobachten)
        {
            try
            {
                using (var fenster = new UpdateDialog(LoadSettingsSafe(), zielVersion, nurBeobachten))
                {
                    fenster.ShowDialog(HostOwnerWindow());
                }
            }
            catch (Exception ex)
            {
                AppendSystemLine("Update-Fenster konnte nicht geöffnet werden: " + ex.Message);
            }
        }

        private async Task<bool> AuthorizeSettingsAsync(AddinSettings current)
        {
            string entered;
            using (var prompt = new PasswordPrompt())
            {
                if (prompt.ShowDialog(HostOwnerWindow()) != DialogResult.OK)
                {
                    return false;
                }
                entered = prompt.EnteredPassword;
            }

            try
            {
                using (var client = new ChatClient(current))
                using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15)))
                {
                    bool ok = await client.VerifyPasswordAsync(entered, cts.Token);
                    if (!ok)
                    {
                        MessageBox.Show(this,
                            "Falsches WebUI-Passwort — Einstellungen bleiben gesperrt.",
                            "Vizpatch", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    }
                    return ok;
                }
            }
            catch (Exception ex)
            {
                var choice = MessageBox.Show(this,
                    "Das WebUI-Backend unter\r\n    " + (current.BackendUrl ?? "(keine URL)")
                    + "\r\nist nicht erreichbar, daher kann das Passwort nicht geprueft werden:\r\n"
                    + ex.Message + "\r\n\r\n"
                    + "Einstellungen trotzdem oeffnen, um die Verbindung (z. B. die "
                    + "Backend-URL) zu korrigieren?",
                    "Vizpatch — Verbindung pruefen",
                    MessageBoxButtons.YesNo, MessageBoxIcon.Warning,
                    MessageBoxDefaultButton.Button2);
                return choice == DialogResult.Yes;
            }
        }

        // --- UI-Thread-Marshalling ----------------------------------------------

        private void MarshalToUi(Action action)
        {
            if (this.IsHandleCreated && this.InvokeRequired)
            {
                this.BeginInvoke(action);
            }
            else
            {
                action();
            }
        }

        /// <summary>
        /// Owner für modale Dialoge: das Outlook-Hauptfenster (echtes Top-Level-
        /// Fenster). Nötig, damit ShowDialog WIRKLICH modal ist — mit dem
        /// Task-Pane-UserControl als Owner ließe sich der Dialog nach Outlook
        /// wegklicken und offen im Hintergrund liegen lassen. WinForms deaktiviert
        /// das Owner-Fenster für die Lebensdauer des Dialogs, sodass Outlook
        /// gesperrt ist, bis X/Abbrechen/Speichern gedrückt wurde. Fällt auf das
        /// Control zurück, wenn kein Fensterhandle ermittelbar ist.
        /// </summary>
        private IWin32Window HostOwnerWindow()
        {
            try
            {
                IntPtr h = System.Diagnostics.Process.GetCurrentProcess().MainWindowHandle;
                if (h != IntPtr.Zero)
                {
                    return new Win32WindowHandle(h);
                }
            }
            catch
            {
                // MainWindowHandle kann in seltenen Fällen werfen -> Control-Fallback.
            }
            return this;
        }

        private sealed class Win32WindowHandle : IWin32Window
        {
            public Win32WindowHandle(IntPtr handle) { Handle = handle; }
            public IntPtr Handle { get; }
        }

        // --- Log-Ausgabe --------------------------------------------------------

        private int AvailableWidth()
        {
            int w = _log.ClientSize.Width - _log.Padding.Horizontal;
            return w < 80 ? 80 : w;
        }

        private void LayoutBubble(Bubble b)
        {
            const int gap = 2;
            int avail = AvailableWidth();
            int maxW = (int)(avail * BubbleMaxWidthFraction);
            if (maxW < 60) maxW = 60;
            b.MaximumSize = new Size(maxW, 0);
            int bw = b.PreferredSize.Width;
            if (bw > avail) bw = avail;
            int rest = avail - bw - gap;
            if (rest < gap) rest = gap;
            b.Margin = b.IsUser
                ? new Padding(rest, 5, gap, 5)   // nach rechts
                : new Padding(gap, 5, rest, 5);  // links
        }

        // Unsichtbarer Abstandshalter, der IMMER das letzte Element im Log bleibt.
        // Grund: Der FlowLayoutPanel bezieht weder sein Bottom-Padding noch
        // AutoScrollMargin verlässlich in den scrollbaren Bereich ein und
        // ScrollControlIntoView greift hier nicht zuverlässig — die unterste
        // echte Blase wurde dadurch am Composer "abgehackt". Mit einem Spacer als
        // letztem Kind wird bei zu kurzem Scrollen höchstens der leere Spacer
        // angeschnitten, nie mehr eine Nachricht.
        private Panel _bottomSpacer;

        private void EnsureBottomSpacer()
        {
            if (_bottomSpacer == null || _bottomSpacer.IsDisposed)
            {
                _bottomSpacer = new Panel
                {
                    Width = 1,
                    Height = 16,
                    Margin = new Padding(0),
                    BackColor = _pal.Pane,
                };
            }
            if (!_log.Controls.Contains(_bottomSpacer))
            {
                _log.Controls.Add(_bottomSpacer);
            }
            // Immer ans Ende schieben (nach der zuletzt hinzugefügten Nachricht).
            _log.Controls.SetChildIndex(_bottomSpacer, _log.Controls.Count - 1);
        }

        private Bubble AddBubble(string text, bool isUser)
        {
            var b = new Bubble
            {
                IsUser = isUser,
                BackColor = _pal.Pane,
                BubbleColor = isUser ? _pal.Brand : _pal.AssistantBg,
                ForeColor = isUser ? _pal.OnBrand : _pal.AssistantInk,
                Font = new Font("Segoe UI", 10f),
                Text = text ?? "",
            };
            _log.Controls.Add(b);
            LayoutBubble(b);
            EnsureBottomSpacer();
            ScrollToEnd();
            return b;
        }

        /// <summary>
        /// Haengt eine Bild-Vorschau-Blase an (Quick 260813-wea) — analog
        /// <see cref="AddBubble"/>. <c>_currentAssistant = null</c> davor
        /// sorgt dafuer, dass nachfolgender Text eine FRISCHE Antwortblase
        /// beginnt (dasselbe Verhalten wie im <c>case "tool":</c>-Zweig).
        /// </summary>
        private void AddImageBubble(ImagePreview vorschau, Image bild, Stream quelle)
        {
            FinishTypingNow();
            _currentAssistant = null;
            var ib = new ImageBubble { BackColor = _pal.Pane };
            ib.Caption.BackColor = _pal.Pane;
            ib.Caption.ForeColor = _pal.Faint;
            ib.SetzeBild(bild, quelle, vorschau.AnzeigeText());
            ib.Vermesse(AvailableWidth());
            _log.Controls.Add(ib);
            EnsureBottomSpacer();
            ScrollToEnd();
        }

        private void AppendUserBubble(string text)
        {
            FinishTypingNow();
            _currentAssistant = null;
            AddBubble(text, true);
        }

        private void BeginAssistantBubble()
        {
            FinishTypingNow();
            _currentAssistant = null;
        }

        /// <summary>
        /// Nimmt einen Text-Chunk der Antwort an. Schreibt ihn NICHT direkt in die
        /// Blase, sondern lässt ihn tippen (Betreiber-Wunsch 2026-08-16) — siehe den
        /// Kommentarblock über <see cref="EnqueueAssistantText"/>.
        /// </summary>
        private void AppendAssistantChunk(string chunk)
        {
            EnqueueAssistantText(chunk);
        }

        private void AppendErrorLine(string text)
        {
            FinishTypingNow();
            _currentAssistant = null;
            AddNote("Fehler: " + text, "error");
        }

        private void AppendSystemLine(string text)
        {
            AddNote(text, "system");
        }

        private void AddNote(string text, string role)
        {
            // Muss auf dem UI-Thread laufen (Control.Add). Async-Handler wie
            // SettingsButton_Click setzen ihre await-Continuation im VSTO-Host
            // mangels WindowsFormsSynchronizationContext teils auf einem
            // Threadpool-Thread fort; ein threadfremdes Controls.Add warf dann eine
            // ArgumentException und riss Outlook mit (CLR20r3-Crash 260725-tqr).
            if (this.IsHandleCreated && this.InvokeRequired)
            {
                this.BeginInvoke(new Action(() => AddNote(text, role)));
                return;
            }

            var lbl = new Label
            {
                AutoSize = true,
                Tag = role,
                BackColor = _pal.Pane,
                ForeColor = role == "error"
                    ? Color.FromArgb(0xE0, 0x5A, 0x5A)
                    : _pal.Faint,
                Font = new Font("Segoe UI", 9f, role == "system" ? FontStyle.Italic : FontStyle.Regular),
                Margin = new Padding(4, 3, 6, 3),
                MaximumSize = new Size((int)(AvailableWidth() * 0.95), 0),
                Text = text ?? "",
            };
            _log.Controls.Add(lbl);
            EnsureBottomSpacer();
            ScrollToEnd();
        }

        // --- Denk-Indikator -----------------------------------------------------

        private void ShowThinking()
        {
            if (_thinking != null)
            {
                return;
            }
            _thinking = new ThinkingDots
            {
                Pal = _pal,
                BackColor = _pal.Pane,
                Margin = new Padding(2, 5, 2, 5),
            };
            _log.Controls.Add(_thinking);
            _thinking.StartAnimation();
            EnsureBottomSpacer();
            ScrollToEnd();
        }

        private void HideThinking()
        {
            if (_thinking == null)
            {
                return;
            }
            _thinking.StopAnimation();
            _log.Controls.Remove(_thinking);
            _thinking.Dispose();
            _thinking = null;
        }

        private bool _relayouting;
        // Breite, fuer die die Blasen zuletzt vermessen wurden (-1 = noch nie).
        private int _lastLayoutWidth = -1;

        /// <summary>Vermisst alle Blasen neu. <paramref name="force"/> = true umgeht den
        /// Breiten-Cache (Theme-Wechsel, Zuruecksetzen — da aendert sich der Inhalt,
        /// nicht die Breite).</summary>
        private void RelayoutBubbles(bool force = false)
        {
            // Re-Entrancy-Schutz: ResumeLayout() kann durch das Ein-/Ausblenden der
            // vertikalen Scrollbar die Client-Breite ändern -> feuert _log.Resize ->
            // ruft erneut RelayoutBubbles. Ohne Guard oszilliert das (Scrollbar an/aus/
            // an ...) und die UI friert ein ("nicht mehr scrollbar / hängt sich auf",
            // besonders sichtbar beim Theme-Wechsel, der ebenfalls RelayoutBubbles ruft).
            // MUSS vor dem Breiten-Cache stehen: sonst wuerde die verworfene Breite als
            // "schon vermessen" gemerkt und das echte Relayout nie nachgeholt.
            if (_relayouting)
            {
                return;
            }
            // Ein Resize meldet JEDE Aenderung — auch reine Hoehenaenderungen (z. B.
            // mitwachsender Composer) und wiederholte Events mit identischer Breite.
            // Die Blasenbreite haengt aber ausschliesslich an der Log-Breite: ohne
            // diesen Cache lief bei jedem Event der komplette Reflow samt
            // PreferredSize-Messung pro Blase -> "rendert ständig neu".
            int width = AvailableWidth();
            if (!force && width == _lastLayoutWidth)
            {
                return;
            }
            _lastLayoutWidth = width;
            _relayouting = true;
            try
            {
                _log.SuspendLayout();
                foreach (Control c in _log.Controls)
                {
                    if (c is Bubble b)
                    {
                        LayoutBubble(b);
                    }
                    else if (c is ImageBubble ib)
                    {
                        // Quick 260813-wea — historisch wunder Punkt (Flackern
                        // beim Schmal-/Breitziehen der Pane): der bestehende
                        // Re-Entrancy-Guard `_relayouting` und der Breiten-Cache
                        // `_lastLayoutWidth` (oben in dieser Methode) bleiben
                        // UNANGETASTET — keine zusaetzlichen
                        // PerformLayout/Invalidate-Aufrufe in dieser Schleife.
                        ib.Vermesse(AvailableWidth());
                    }
                    else if (c is Label l)
                    {
                        l.MaximumSize = new Size((int)(AvailableWidth() * 0.95), 0);
                    }
                }
                _log.ResumeLayout();
            }
            finally
            {
                _relayouting = false;
            }
        }

        private void ScrollToEnd()
        {
            if (_log.Controls.Count == 0)
            {
                return;
            }
            _log.PerformLayout();
            // Grob ans Ende scrollen ...
            _log.AutoScrollPosition = new Point(0, _log.DisplayRectangle.Height);
            // ... und das letzte Control GARANTIERT vollständig einblenden. Ohne das
            // schneidet der FlowLayoutPanel-AutoScroll die unterste Blase um die
            // Bottom-Padding-Höhe an (letzte Nachricht wirkte "abgehackt" über dem
            // Composer).
            var last = _log.Controls[_log.Controls.Count - 1];
            _log.ScrollControlIntoView(last);
        }

        // ====================================================================
        //  Sprechblase
        // ====================================================================

        /// <summary>Eine Chat-Sprechblase mit getailter (asymmetrischer) Rundung:
        /// Nutzer rechts (untere rechte Ecke spitz), Assistent links (untere linke
        /// Ecke spitz) — analog dem Web-Design.</summary>
        private sealed class Bubble : Label
        {
            public bool IsUser;
            public Color BubbleColor;
            private const int R = 14;
            private const int Tail = 4;

            public Bubble()
            {
                // Das Label misst und zeichnet seinen Text SELBST (AutoSize): das
                // Framework garantiert damit, dass die Blase immer hoch genug fuer
                // den (umgebrochenen) Text ist — nichts wird abgeschnitten, und die
                // gezeichnete Rundung passt immer zur echten Hoehe. Wir zeichnen in
                // OnPaint NUR den gerundeten Hintergrund und lassen danach das Label
                // den Text rendern.
                AutoSize = true;
                Padding = new Padding(13, 9, 13, 10);
                Margin = new Padding(2, 5, 2, 5);
                TextAlign = ContentAlignment.TopLeft;
                UseCompatibleTextRendering = false; // GDI (TextRenderer) — konsistent Messung/Zeichnung
                DoubleBuffered = true;
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                var rect = new Rectangle(0, 0, Width - 1, Height - 1);
                // Getailte Ecken: Nutzer = unten-rechts spitz, Assistent = unten-links.
                using (var path = IsUser
                    ? ModernShapes.RoundedRect(rect, R, R, Tail, R)
                    : ModernShapes.RoundedRect(rect, R, R, R, Tail))
                using (var brush = new SolidBrush(BubbleColor))
                {
                    e.Graphics.FillPath(brush, path);
                }
                // Danach zeichnet das Label seinen Text (korrekt bemessen).
                base.OnPaint(e);
            }
        }

        // ====================================================================
        //  Bild-Vorschau-Blase (Quick 260813-wea — Bild-Anhänge als
        //  Vorschaubild in der Pane)
        // ====================================================================

        /// <summary>
        /// Kleines Vorschaubild fuer einen vom Agenten bereits inhaltlich
        /// ausgewerteten Bild-Anhang (Quick 260813-wea) — Panel + PictureBox +
        /// Bildunterschrift, IMMER links positioniert (Bilder kommen nur vom
        /// Assistenten, nie vom Betreiber). Zweck ist Wiedererkennung/Kontrolle,
        /// NICHT Lesbarkeit: kein Klick-Vergroessern, kein Bildbetrachter
        /// (ausdruecklich ausserhalb des Umfangs, siehe Plan-Objective).
        ///
        /// GDI+-Falle (unbedingt beachten): ein <see cref="Image"/>, das aus
        /// einem <see cref="MemoryStream"/> entsteht, verlangt, dass der
        /// Stream fuer die GESAMTE Lebensdauer des Bildes offen bleibt — diese
        /// Blase haelt deshalb BEIDE Referenzen (<see cref="_bild"/> UND
        /// <see cref="_quelle"/>) und gibt in <see cref="Dispose(bool)"/> auch
        /// BEIDE frei. Ein <c>using</c>-Block um den Stream wuerde spaeter beim
        /// Zeichnen eine <c>GenericGdiException</c>/<c>ArgumentException</c>
        /// ausloesen.
        /// </summary>
        private sealed class ImageBubble : Panel
        {
            // Symmetrisch zu Bubble.IsUser (immer false) — haelt die Einhaengung
            // in ApplyTheme/RelayoutBubbles strukturell parallel zu Bubble.
            public bool IsUser = false;

            private readonly PictureBox _picture;
            private readonly Label _caption;
            private Image _bild;
            private Stream _quelle;

            public ImageBubble()
            {
                Margin = new Padding(2, 5, 2, 5);
                AutoSize = false;

                _picture = new PictureBox
                {
                    SizeMode = PictureBoxSizeMode.Zoom,
                    Dock = DockStyle.Top,
                };
                _caption = new Label
                {
                    AutoSize = false,
                    Dock = DockStyle.Bottom,
                    Height = CaptionHeight,
                    Font = new Font("Segoe UI", 8f, FontStyle.Italic),
                    TextAlign = ContentAlignment.MiddleLeft,
                };
                Controls.Add(_picture);
                Controls.Add(_caption);
            }

            private const int CaptionHeight = 16;

            /// <summary>Fuer ApplyTheme (Muster: Bubble.BackColor/ForeColor dort
            /// direkt gesetzt) — Untertitel-Label folgt der Palette separat vom
            /// Panel-Hintergrund.</summary>
            public Label Caption => _caption;

            /// <summary>Merkt BEIDE Referenzen (Bild + Quell-Stream, s. Klassen-
            /// Doc-Kommentar) und haengt das Bild in die PictureBox.</summary>
            public void SetzeBild(Image bild, Stream quelle, string beschriftung)
            {
                _bild = bild;
                _quelle = quelle;
                _picture.Image = bild;
                _caption.Text = beschriftung ?? "";
            }

            /// <summary>
            /// Bemisst Breite (hoechstens 280 px, mindestens 60 px, gedeckelt auf
            /// <see cref="BubbleMaxWidthFraction"/> der verfuegbaren Breite) und
            /// Hoehe aus dem SEITENVERHAELTNIS des Originalbildes — kein
            /// Hochskalieren ueber die Originalbreite hinaus. Division durch 0
            /// (Bild ohne bekannte Breite) wird abgefangen.
            /// </summary>
            public void Vermesse(int verfuegbareBreite)
            {
                int breite = Math.Min(280, (int)(verfuegbareBreite * BubbleMaxWidthFraction));
                if (breite < 60) breite = 60;
                if (_bild != null && _bild.Width > 0 && breite > _bild.Width)
                {
                    // Kein Hochskalieren ueber die Originalbreite hinaus.
                    breite = _bild.Width;
                }

                int bildHoehe;
                if (_bild != null && _bild.Width > 0)
                {
                    bildHoehe = (int)((long)_bild.Height * breite / _bild.Width);
                }
                else
                {
                    bildHoehe = breite; // Fallback ohne bekannte Originalgroesse.
                }
                if (bildHoehe < 1) bildHoehe = 1;

                this.Width = breite;
                this.Height = bildHoehe + CaptionHeight;
                _picture.Height = bildHoehe;
            }

            protected override void Dispose(bool disposing)
            {
                if (disposing)
                {
                    // Beide Ressourcen null-sicher freigeben (Klassen-Doc-Kommentar) —
                    // greift automatisch ueber die bestehende Reset-Schleife
                    // (ResetButton_Click: Remove + c.Dispose()) und jedes spaetere
                    // Entfernen einer Blase. Ohne diesen Override sammeln sich
                    // GDI-Handles bis zum Handle-Leak (die Pane lebt so lange wie
                    // Outlook).
                    _bild?.Dispose();
                    _quelle?.Dispose();
                }
                base.Dispose(disposing);
            }
        }

        // ====================================================================
        //  Denk-Indikator (drei pulsierende Punkte)
        // ====================================================================

        private sealed class ThinkingDots : Control
        {
            public Palette Pal = Palette.Light;
            private readonly System.Windows.Forms.Timer _timer;
            private int _phase;

            public ThinkingDots()
            {
                DoubleBuffered = true;
                Size = new Size(50, 30);
                _timer = new System.Windows.Forms.Timer { Interval = 300 };
                _timer.Tick += (s, e) => { _phase = (_phase + 1) % 3; Invalidate(); };
            }

            public void StartAnimation() { _timer.Start(); }
            public void StopAnimation() { _timer.Stop(); }

            protected override void OnPaint(PaintEventArgs e)
            {
                var g = e.Graphics;
                g.Clear(BackColor);
                g.SmoothingMode = SmoothingMode.AntiAlias;
                var rect = new Rectangle(0, 0, Width - 1, Height - 1);
                using (var path = ModernShapes.RoundedRect(rect, 12, 12, 12, 4))
                using (var b = new SolidBrush(Pal.AssistantBg))
                {
                    g.FillPath(b, path);
                }
                for (int i = 0; i < 3; i++)
                {
                    int alpha = i == _phase ? 255 : 90;
                    using (var b = new SolidBrush(Color.FromArgb(alpha, Pal.Faint)))
                    {
                        g.FillEllipse(b, 13 + i * 9, Height / 2 - 3, 6, 6);
                    }
                }
            }

            protected override void Dispose(bool disposing)
            {
                if (disposing) { _timer.Dispose(); }
                base.Dispose(disposing);
            }
        }
    }

    // ========================================================================
    //  Farbpalette (hell/dunkel) — Werte aus dem Design "Vizpatch Add-in".
    // ========================================================================

    internal sealed class Palette
    {
        public Color App, Pane, Pane2, Surface, Surface2, Border, BorderStrong;
        public Color Text, Muted, Faint, Brand, BrandInk, Navy;
        public Color AssistantBg, AssistantInk, Chip, ChipInk, ChipBorder;
        public Color ToolBg, ToolBorder, ToolInk, Ok, OnBrand;

        private static Color H(string hex) => ColorTranslator.FromHtml(hex);

        public static readonly Palette Light = new Palette
        {
            App = H("#e7ebf2"), Pane = H("#ffffff"), Pane2 = H("#f4f7fb"),
            Surface = H("#eef3f9"), Surface2 = H("#f7fafd"),
            Border = H("#e4e9f1"), BorderStrong = H("#d3dbe6"),
            Text = H("#16202f"), Muted = H("#5c6a80"), Faint = H("#8a97ab"),
            Brand = H("#2563eb"), BrandInk = H("#1d4ed8"), Navy = H("#1e3a5f"),
            AssistantBg = H("#f1f5fa"), AssistantInk = H("#1b2534"),
            Chip = H("#eef3fa"), ChipInk = H("#2b4a74"), ChipBorder = H("#dbe6f4"),
            ToolBg = H("#f6faf7"), ToolBorder = H("#e0efe5"), ToolInk = H("#1c6b46"),
            Ok = H("#1a7f4b"), OnBrand = Color.White,
        };

        public static readonly Palette Dark = new Palette
        {
            App = H("#0c1015"), Pane = H("#141a23"), Pane2 = H("#19212c"),
            Surface = H("#1c2531"), Surface2 = H("#1a222d"),
            Border = H("#28313d"), BorderStrong = H("#36414f"),
            Text = H("#e8edf4"), Muted = H("#9aa8bc"), Faint = H("#6f7d92"),
            Brand = H("#4e8cf6"), BrandInk = H("#3f7fe8"), Navy = H("#cdd9ea"),
            AssistantBg = H("#212b38"), AssistantInk = H("#e6ecf4"),
            Chip = H("#212c3a"), ChipInk = H("#a9c6ee"), ChipBorder = H("#2e3b4d"),
            ToolBg = H("#152018"), ToolBorder = H("#26402e"), ToolInk = H("#63c98d"),
            Ok = H("#5bcd8c"), OnBrand = Color.White,
        };
    }

    /// <summary>Gemeinsame Zeichen-Helfer fuer die modernen Add-in-Controls.</summary>
    internal static class ModernShapes
    {
        /// <summary>Gerundetes Rechteck (gleicher Radius an allen Ecken).</summary>
        public static GraphicsPath RoundedRect(Rectangle r, int radius)
        {
            return RoundedRect(r, radius, radius, radius, radius);
        }

        /// <summary>Gerundetes Rechteck mit einzeln steuerbaren Ecken-Radien
        /// (Reihenfolge: oben-links, oben-rechts, unten-rechts, unten-links).</summary>
        public static GraphicsPath RoundedRect(Rectangle r, int tl, int tr, int br, int bl)
        {
            var path = new GraphicsPath();
            if (r.Width < 2 || r.Height < 2)
            {
                path.AddRectangle(r);
                return path;
            }
            int max = Math.Min(r.Width, r.Height) / 2;
            tl = Math.Min(tl, max); tr = Math.Min(tr, max);
            br = Math.Min(br, max); bl = Math.Min(bl, max);

            AddCorner(path, r.X, r.Y, tl, 180, true);
            AddCorner(path, r.Right - tr * 2, r.Y, tr, 270, true);
            AddCorner(path, r.Right - br * 2, r.Bottom - br * 2, br, 0, true);
            AddCorner(path, r.X, r.Bottom - bl * 2, bl, 90, true);
            path.CloseFigure();
            return path;
        }

        private static void AddCorner(GraphicsPath path, int x, int y, int radius, int startAngle, bool _)
        {
            if (radius <= 0)
            {
                // Spitze Ecke: der Bogen entartet zu einem Punkt.
                int px = startAngle == 180 || startAngle == 90 ? x : x;
                int py = y;
                // Punkt der jeweiligen Ecke bestimmen.
                switch (startAngle)
                {
                    case 180: px = x; py = y; break;               // oben-links
                    case 270: px = x; py = y; break;               // oben-rechts (x ist bereits Right)
                    case 0: px = x; py = y; break;                 // unten-rechts
                    case 90: px = x; py = y; break;                // unten-links
                }
                path.AddLine(px, py, px, py);
                return;
            }
            path.AddArc(x, y, radius * 2, radius * 2, startAngle, 90);
        }
    }

    // ========================================================================
    //  Doppelgepufferter FlowLayoutPanel (Chat-Log)
    // ========================================================================

    /// <summary>FlowLayoutPanel mit aktivem Doppelpuffer. <c>DoubleBuffered</c> ist
    /// protected und beim Standard-FlowLayoutPanel aus — beim Reflow (jede
    /// Breitenaenderung ordnet alle Blasen neu an) wurden dadurch Zwischenzustaende
    /// sichtbar gemalt (Flackern beim Ziehen der Pane-Breite).</summary>
    internal sealed class BufferedFlowPanel : FlowLayoutPanel
    {
        public BufferedFlowPanel() { DoubleBuffered = true; }
    }

    // ========================================================================
    //  Header-Panel mit unterer Trennlinie
    // ========================================================================

    internal sealed class BottomBorderPanel : Panel
    {
        public Color LineColor = Color.Gainsboro;

        public BottomBorderPanel() { DoubleBuffered = true; }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            using (var pen = new Pen(LineColor, 1f))
            {
                e.Graphics.DrawLine(pen, 0, Height - 1, Width, Height - 1);
            }
        }
    }

    // ========================================================================
    //  Emblem-Anzeige mit hochwertiger Skalierung
    // ========================================================================

    /// <summary>Zeigt das freigestellte Emblem seitenverhaeltnistreu und mit
    /// HighQualityBicubic-Interpolation (die Standard-PictureBox skaliert das
    /// detailreiche Emblem auf ~30 px sichtbar pixelig herunter). Transparenter
    /// Hintergrund uebernimmt die Elternfarbe -> passt in Hell und Dunkel.</summary>
    internal sealed class EmblemBox : Control
    {
        private readonly Image _img;
        // Kompaktes Ersatzbild (Emblem): wird gezeigt, wenn das volle Logo bei
        // voller Hoehe nicht mehr in die Breite passt (statt es zu schrumpfen).
        private readonly Image _compact;
        // true = linksbuendig (Voll-Logo im Header), sonst mittig.
        public bool AlignLeft;

        // Zwischenspeicher fuer das fertig skalierte Bild (siehe GetScaled).
        private Bitmap _cache;
        private Image _cacheSource;

        public EmblemBox(Image img, Image compact = null)
        {
            _img = img;
            _compact = compact;
            DoubleBuffered = true;
            // Bei Groessenaenderung komplett neu zeichnen -> der Logo/Emblem-Wechsel
            // greift live beim Enger-/Breiterziehen der Pane.
            SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.ResizeRedraw, true);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.Clear(Parent != null ? Parent.BackColor : BackColor);
            if (_img == null && _compact == null)
            {
                return;
            }

            // Linksbuendig KEIN horizontaler Rand -> Bild sitzt bündig am Container-
            // Rand (fluchtet exakt mit der "Agent"-Zeile darunter). Vertikal ein
            // kleiner Rand fuers Anti-Aliasing.
            int padY = 1;
            int availW = Width;
            int availH = Height - padY * 2;
            if (availW < 1 || availH < 1)
            {
                return;
            }

            // Bild waehlen: volles Logo, solange es bei voller Hoehe in die Breite
            // passt; sonst das kompakte Emblem.
            Image img = _img ?? _compact;
            if (_img != null && _compact != null)
            {
                float fullNaturalW = _img.Width * ((float)availH / _img.Height);
                if (fullNaturalW > availW)
                {
                    img = _compact;
                }
            }

            // Das Emblem (kompaktes Ersatzbild) wird NIE verkleinert: immer auf volle
            // Höhe skalieren, unabhängig von der Breite. Ist die Box dafür zu schmal,
            // wird das Emblem links angeschnitten statt geschrumpft (Betreiber-Wunsch —
            // ein hartes Pane-Mindestbreiten-Lock ist in VSTO nicht ruckelfrei möglich).
            // Das volle Logo skaliert weiterhin seitenverhältnistreu in die Box (es wird
            // ohnehin vorher aufs Emblem umgeschaltet, bevor es schrumpfen müsste).
            float scale = (_compact != null && img == _compact)
                ? (float)availH / img.Height
                : Math.Min((float)availW / img.Width, (float)availH / img.Height);
            int w = Math.Max(1, (int)(img.Width * scale));
            int h = Math.Max(1, (int)(img.Height * scale));
            int x = AlignLeft ? 0 : (availW - w) / 2;
            int y = padY + (availH - h) / 2;
            var scaled = GetScaled(img, w, h);
            if (scaled != null)
            {
                g.DrawImageUnscaled(scaled, x, y);
            }
        }

        /// <summary>Liefert das auf <paramref name="w"/>x<paramref name="h"/> skalierte
        /// Bild aus dem Zwischenspeicher und rechnet es nur bei echter Groessen- oder
        /// Bildwechsel-Aenderung neu.
        ///
        /// Grund: ResizeRedraw laesst OnPaint beim Ziehen der Pane-Breite pro
        /// Maus-Pixel feuern. Eine HighQualityBicubic-Skalierung des ~974x278-Logos
        /// kostet dabei jedes Mal Millisekunden — sichtbar als ruckelndes,
        /// "staendig neu gerendertes" Logo.</summary>
        private Bitmap GetScaled(Image src, int w, int h)
        {
            if (_cache != null && ReferenceEquals(_cacheSource, src)
                && _cache.Width == w && _cache.Height == h)
            {
                return _cache;
            }

            var bmp = new Bitmap(w, h, System.Drawing.Imaging.PixelFormat.Format32bppPArgb);
            using (var bg = Graphics.FromImage(bmp))
            {
                bg.InterpolationMode = InterpolationMode.HighQualityBicubic;
                bg.PixelOffsetMode = PixelOffsetMode.HighQuality;
                bg.SmoothingMode = SmoothingMode.AntiAlias;
                bg.CompositingQuality = CompositingQuality.HighQuality;
                bg.DrawImage(src, new Rectangle(0, 0, w, h));
            }

            if (_cache != null)
            {
                _cache.Dispose();
            }
            _cache = bmp;
            _cacheSource = src;
            return _cache;
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && _cache != null)
            {
                _cache.Dispose();
                _cache = null;
                _cacheSource = null;
            }
            base.Dispose(disposing);
        }
    }

    // ========================================================================
    //  Icon-Button (30x30, gerundet) fuer den Header
    // ========================================================================

    internal sealed class IconButton : Button
    {
        public Palette Pal = Palette.Light;
        public string Glyph;
        // Umschalt-Button (z. B. Mail-Bezug): bei Active in Markenfarbe hervorgehoben.
        public bool Toggleable;
        public bool Active;
        private bool _hover;
        private const int R = 8;

        public IconButton(string glyph, string tooltip)
        {
            Glyph = glyph;
            Size = new Size(30, 30);
            Margin = new Padding(4, 0, 0, 0);
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            FlatAppearance.MouseOverBackColor = Color.Transparent;
            FlatAppearance.MouseDownBackColor = Color.Transparent;
            Cursor = Cursors.Hand;
            DoubleBuffered = true;
            TabStop = false;
            var tip = new ToolTip();
            tip.SetToolTip(this, tooltip);
        }

        protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hover = false; Invalidate(); base.OnMouseLeave(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.Clear(Parent != null ? Parent.BackColor : Pal.Pane);
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = new Rectangle(0, 0, Width - 1, Height - 1);

            Color fill, border, glyphColor;
            if (Toggleable && Active)
            {
                // Aktiver Umschalt-Button: in Markenfarbe gefuellt, weisses Glyph.
                fill = _hover ? Pal.BrandInk : Pal.Brand;
                border = Pal.Brand;
                glyphColor = Pal.OnBrand;
            }
            else
            {
                fill = _hover ? Pal.Surface : Pal.Surface2;
                border = Pal.Border;
                glyphColor = Pal.Muted;
            }

            using (var path = ModernShapes.RoundedRect(rect, R))
            {
                using (var b = new SolidBrush(fill)) g.FillPath(b, path);
                using (var pen = new Pen(border, 1f)) g.DrawPath(pen, path);
            }
            using (var f = new Font("Segoe UI Symbol", 11f))
            {
                TextRenderer.DrawText(g, Glyph, f, ClientRectangle, glyphColor,
                    TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding);
            }
        }
    }

    // ========================================================================
    //  Agent-Chip (statisch): zeigt den konfigurierten Agent-Namen
    // ========================================================================

    internal sealed class AgentChip : Control
    {
        public Palette Pal = Palette.Light;
        private const int R = 8;

        public AgentChip()
        {
            DoubleBuffered = true;
            Height = 30;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.Clear(Parent != null ? Parent.BackColor : Pal.Pane);
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = new Rectangle(0, 2, Width - 1, Height - 5);
            using (var path = ModernShapes.RoundedRect(rect, R))
            {
                using (var b = new SolidBrush(Pal.Chip)) g.FillPath(b, path);
                using (var pen = new Pen(Pal.ChipBorder, 1f)) g.DrawPath(pen, path);
            }
            using (var f = new Font("Segoe UI Semibold", 9.5f))
            {
                var textRect = new Rectangle(11, rect.Y, rect.Width - 22, rect.Height);
                TextRenderer.DrawText(g, string.IsNullOrEmpty(Text) ? "—" : Text, f, textRect,
                    Pal.ChipInk,
                    TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix);
            }
        }
    }

    // ========================================================================
    //  Sende-Button (gerundet, Markenfarbe, gezeichneter Aufwaerts-Pfeil)
    // ========================================================================

    internal sealed class SendButton : Button
    {
        public Palette Pal = Palette.Light;
        private bool _hover;
        private const int R = 10;

        public SendButton()
        {
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            FlatAppearance.MouseOverBackColor = Color.Transparent;
            FlatAppearance.MouseDownBackColor = Color.Transparent;
            Cursor = Cursors.Hand;
            DoubleBuffered = true;
            TabStop = false;
        }

        protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hover = false; Invalidate(); base.OnMouseLeave(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.Clear(Parent != null ? Parent.BackColor : Pal.Surface2);
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = new Rectangle(0, 0, Width - 1, Height - 1);
            Color bg = !Enabled ? Pal.BorderStrong : (_hover ? Pal.BrandInk : Pal.Brand);
            using (var path = ModernShapes.RoundedRect(rect, R))
            using (var b = new SolidBrush(bg))
            {
                g.FillPath(b, path);
            }
            // Aufwaerts-Pfeil (Senden).
            int cx = Width / 2;
            int top = Height / 2 - 6;
            int bot = Height / 2 + 6;
            using (var pen = new Pen(Pal.OnBrand, 2.2f) { StartCap = LineCap.Round, EndCap = LineCap.Round })
            {
                g.DrawLine(pen, cx, top, cx, bot);
                g.DrawLine(pen, cx - 5, top + 5, cx, top);
                g.DrawLine(pen, cx + 5, top + 5, cx, top);
            }
        }
    }

    /// <summary>
    /// Flacher, gerundeter Button mit eigener Zeichnung (WinForms-Standard-Buttons
    /// wirken altmodisch). Wird vom <see cref="SettingsDialog"/> und
    /// <see cref="PasswordPrompt"/> genutzt.
    /// </summary>
    internal sealed class RoundButton : Button
    {
        private readonly Color _bg;
        private readonly Color _hover;
        private bool _hovering;
        private const int Radius = 8;

        public RoundButton(Color bg, Color hover, Color fore)
        {
            _bg = bg;
            _hover = hover;
            ForeColor = fore;
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            FlatAppearance.MouseOverBackColor = Color.Transparent;
            FlatAppearance.MouseDownBackColor = Color.Transparent;
            BackColor = Color.White;
            Padding = new Padding(14, 4, 14, 4);
            Cursor = Cursors.Hand;
            DoubleBuffered = true;
        }

        protected override void OnMouseEnter(EventArgs e) { _hovering = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { _hovering = false; Invalidate(); base.OnMouseLeave(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(Parent != null ? Parent.BackColor : BackColor);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = new Rectangle(0, 0, Width - 1, Height - 1);
            using (var path = ModernShapes.RoundedRect(rect, Radius))
            using (var brush = new SolidBrush(Enabled ? (_hovering ? _hover : _bg) : Color.FromArgb(0xC7, 0xCE, 0xD8)))
            {
                e.Graphics.FillPath(brush, path);
            }
            TextRenderer.DrawText(
                e.Graphics, Text, Font, ClientRectangle, ForeColor,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
        }
    }

    /// <summary>
    /// Panel mit weichem, gerundetem 1px-Rahmen (fuer Eingabefeld-Container).
    /// <see cref="BorderColor"/> kann z. B. bei Fokus umgesetzt werden.
    /// </summary>
    internal sealed class RoundedPanel : Panel
    {
        public Color BorderColor;
        private const int Radius = 12;

        public RoundedPanel(Color border)
        {
            BorderColor = border;
            DoubleBuffered = true;
            // Bei jeder Groessenaenderung KOMPLETT neu zeichnen. Ohne das repaint
            // Panel beim (schnellen, z. B. Strg+V-getriebenen) Wachsen nur die neu
            // freigelegten Streifen -> der gerundete Rahmen wird mehrfach/versetzt
            // gezeichnet ("verzerrt"). ResizeRedraw invalidiert die ganze Flaeche.
            SetStyle(ControlStyles.ResizeRedraw, true);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = new Rectangle(0, 0, Width - 1, Height - 1);
            using (var path = ModernShapes.RoundedRect(rect, Radius))
            using (var pen = new Pen(BorderColor, 1.4f))
            {
                e.Graphics.DrawPath(pen, path);
            }
        }
    }

    /// <summary>
    /// Moderne, selbst gezeichnete Checkbox: gerundetes Kaestchen, im aktiven
    /// Zustand blau gefuellt mit weissem Haken. Wird vom <see cref="SettingsDialog"/>
    /// genutzt.
    /// </summary>
    internal sealed class ModernCheckBox : CheckBox
    {
        private static readonly Color Accent = Color.FromArgb(0x25, 0x63, 0xEB);
        private static readonly Color BorderCol = Color.FromArgb(0xB4, 0xBD, 0xCB);
        private const int BoxSize = 18;
        private const int TextGap = 8;

        public ModernCheckBox()
        {
            AutoSize = true;
            Cursor = Cursors.Hand;
            BackColor = Color.White;
            SetStyle(ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer
                     | ControlStyles.ResizeRedraw | ControlStyles.SupportsTransparentBackColor, true);
        }

        protected override void OnCheckedChanged(EventArgs e)
        {
            base.OnCheckedChanged(e);
            Invalidate();
        }

        public override Size GetPreferredSize(Size proposedSize)
        {
            Size t = TextRenderer.MeasureText(Text, Font);
            return new Size(BoxSize + TextGap + t.Width + 4, Math.Max(BoxSize + 4, t.Height + 4));
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.Clear(Parent != null ? Parent.BackColor : BackColor);
            g.SmoothingMode = SmoothingMode.AntiAlias;

            int top = (Height - BoxSize) / 2;
            var box = new Rectangle(0, top, BoxSize, BoxSize);
            using (var path = ModernShapes.RoundedRect(box, 4))
            {
                if (Checked)
                {
                    using (var b = new SolidBrush(Accent)) g.FillPath(b, path);
                    using (var pen = new Pen(Color.White, 2f)
                    { StartCap = LineCap.Round, EndCap = LineCap.Round })
                    {
                        g.DrawLines(pen, new[]
                        {
                            new Point(box.X + 4, box.Y + 9),
                            new Point(box.X + 8, box.Y + 13),
                            new Point(box.X + 14, box.Y + 5),
                        });
                    }
                }
                else
                {
                    using (var b = new SolidBrush(Color.White)) g.FillPath(b, path);
                    using (var pen = new Pen(BorderCol, 1.4f)) g.DrawPath(pen, path);
                }
            }

            var textRect = new Rectangle(BoxSize + TextGap, 0, Width - BoxSize - TextGap, Height);
            TextRenderer.DrawText(g, Text, Font, textRect, ForeColor,
                TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.WordEllipsis);
        }
    }

    /// <summary>
    /// Kleiner modaler Passwort-Prompt fuer das Add-in-Einstellungs-Gate
    /// (Feature B). Fragt das WebUI-Passwort ab; die eigentliche Pruefung erfolgt
    /// beim Aufrufer serverseitig (<see cref="ChatClient.VerifyPasswordAsync"/>).
    /// Das Passwort wird nur im Speicher gehalten und nie persistiert.
    /// </summary>
    internal sealed class PasswordPrompt : Form
    {
        private static readonly Color PrimaryBg = Color.FromArgb(0x25, 0x63, 0xEB);
        private static readonly Color PrimaryHover = Color.FromArgb(0x1D, 0x4E, 0xD8);
        private static readonly Color SecondaryBg = Color.FromArgb(0xEE, 0xF1, 0xF5);
        private static readonly Color SecondaryHover = Color.FromArgb(0xDD, 0xE3, 0xEC);
        private static readonly Color SecondaryFg = Color.FromArgb(0x1E, 0x3A, 0x5F);

        private readonly TextBox _password;

        public string EnteredPassword => _password.Text;

        public PasswordPrompt()
        {
            Text = "Vizpatch — Einstellungen entsperren";
            // Gleiches Fenstersymbol wie in den anderen Dialogen.
            VizpatchAddin.FensterIcon.Anwenden(this);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterParent;
            MinimizeBox = false;
            MaximizeBox = false;
            ClientSize = new Size(400, 170);
            Font = new Font("Segoe UI", 9.5f);
            BackColor = Color.White;

            var layout = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                ColumnCount = 1,
                RowCount = 2,
                Padding = new Padding(16),
                BackColor = Color.White,
            };
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));

            var label = new Label
            {
                Text = "WebUI-Passwort eingeben, um die Einstellungen zu aendern:",
                AutoSize = true,
                MaximumSize = new Size(352, 0),
                ForeColor = SecondaryFg,
                Margin = new Padding(0, 0, 0, 12),
            };
            _password = new TextBox
            {
                UseSystemPasswordChar = true,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 10f),
                Dock = DockStyle.Fill,
            };
            layout.Controls.Add(label, 0, 0);
            layout.Controls.Add(_password, 0, 1);

            var buttonBar = new FlowLayoutPanel
            {
                Dock = DockStyle.Bottom,
                FlowDirection = FlowDirection.RightToLeft,
                Height = 52,
                Padding = new Padding(12),
                BackColor = Color.White,
            };
            var okButton = new RoundButton(PrimaryBg, PrimaryHover, Color.White)
            {
                Text = "Entsperren",
                Height = 34,
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                Margin = new Padding(6, 0, 0, 0),
                Font = new Font("Segoe UI Semibold", 9.5f),
                DialogResult = DialogResult.OK,
            };
            var cancelButton = new RoundButton(SecondaryBg, SecondaryHover, SecondaryFg)
            {
                Text = "Abbrechen",
                Height = 34,
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                Margin = new Padding(6, 0, 0, 0),
                Font = new Font("Segoe UI Semibold", 9.5f),
                DialogResult = DialogResult.Cancel,
            };
            buttonBar.Controls.Add(okButton);
            buttonBar.Controls.Add(cancelButton);

            Controls.Add(layout);
            Controls.Add(buttonBar);
            AcceptButton = okButton;
            CancelButton = cancelButton;
        }
    }
}
