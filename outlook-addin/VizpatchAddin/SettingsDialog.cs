using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using VizpatchAddin.Core;
using VizpatchAddin.TaskPane;

namespace VizpatchAddin
{
    /// <summary>
    /// Konfigurations-Dialog des Add-ins (D-85, RESEARCH.md Pattern 4). Bearbeitet
    /// Backend-URL, Agent-ID, Basic-Auth-Zugangsdaten, den CSRF-Origin-Token und die
    /// Zertifikats-Optionen (Thumbprint-Pinning / Blanket-Trust) und persistiert
    /// alles ueber <see cref="SecureSettingsStore"/> — das Passwort landet
    /// ausschliesslich DPAPI-verschluesselt auf der Platte, nie im Klartext.
    ///
    /// UX-Regel "leer = unveraendert" fuer das Passwortfeld (analog dem
    /// WebUI-Konfig-Formular): Das Feld wird beim Oeffnen bewusst NICHT mit dem
    /// gespeicherten Passwort vorbelegt; bleibt es beim Speichern leer, wird der
    /// bereits gespeicherte Wert beibehalten statt geleert.
    /// </summary>
    public sealed class SettingsDialog : Form
    {
        private TextBox _backendUrl;
        private TextBox _agentId;
        private TextBox _username;
        private TextBox _password;
        private TextBox _originToken;
        private TextBox _certThumbprint;
        private CheckBox _trustAnyCertificate;

        // 260725-tqr: Backend-gebundene Checkbox "Automatische Entwürfe". Der
        // Zustand kommt AUSSCHLIESSLICH vom Backend (kein DPAPI-Wert); ohne
        // Verbindung bleibt die Checkbox deaktiviert und _draftFlagLoaded false,
        // sodass beim Speichern NICHTS ans Backend geschrieben wird.
        private CheckBox _enableDraftCreation;
        private Label _draftHint;
        private bool _draftFlagLoaded;

        // URL-01..09: Urlaubs-Autoantwort. Wie beim Draft-Flag kommt der Zustand
        // AUSSCHLIESSLICH vom Backend; ohne Verbindung bleiben die Felder
        // deaktiviert und _urlaubLoaded false, sodass beim Speichern NICHTS
        // geschrieben wird.
        //
        // Warum das ueberhaupt im Dialog steht und nicht im Chat: die FREIGABE
        // ist das Gate fuer die einzige Funktion des Produkts, die selbstaendig
        // Mails VERSCHICKT. Sie gehoert deshalb — wie Zugangsdaten und die
        // uebrigen Freigabe-Gates — in den Einstellungen-Dialog und bewusst
        // NICHT in die per Chat aenderbaren Betriebsschalter. Der ZEITRAUM ist
        // zusaetzlich per Chat setzbar ("ich bin naechste Woche weg"), weil er
        // nur sagt WANN, nicht OB.
        private CheckBox _enableUrlaub;
        private CheckBox _urlaubDryRun;
        private TextBox _urlaubVon;
        private TextBox _urlaubBis;
        private Label _urlaubHint;
        private TextBox _urlaubVorlage;
        // Merkt den beim Laden gezeigten Text. Nur wenn der Betreiber
        // ihn AENDERT, wird `vorlage` mitgeschickt — sonst bliebe ein
        // ungelesener Vorschlag beim blossen Klick auf Speichern haengen
        // und waere ploetzlich der Text, der in seinem Namen hinausgeht.
        private string _urlaubVorlageGeladen = "";
        private bool _urlaubLoaded;
        private ComboBox _intelligenzModus;
        private Label _intelligenzHint;
        private bool _intelligenzLoaded;

        // 260728-irn: Update-Bereich (Stufe 1 — Erkennung + UI). Der Zustand kommt
        // AUSSCHLIESSLICH vom Backend-Proxy-Endpoint (kein GitHub-Token, keine
        // GitHub-URL im Add-in). Startet deaktiviert mit Ladehinweis; der
        // eigenstaendige, nicht-blockierende Check in OnShown liefert das Ergebnis.
        private RoundButton _updateButton;
        private Label _updateHint;

        // 260728-m8g (Stufe 2): der Update-Button fuehrt das Update jetzt
        // tatsaechlich aus. `_updateRunning` sperrt Doppelklicks clientseitig
        // (der Server hat ohnehin den Statusdatei-Lock, D-5, aber ein Klick,
        // der einfach nichts tut, ist besser als ein zweiter Request).
        // `_latestVersion` merkt die vom letzten Check gemeldete Zielversion
        // fuer den Bestaetigungsdialog und die Uebergabe an den UpdateDialog.
        private bool _updateRunning;
        private string _latestVersion = "";

        // Die Schritt-Bezeichnungen liegen jetzt testbar in
        // VizpatchAddin.Core.UpdateWindowState.StepLabel — eine zweite Kopie
        // hier wuerde beim naechsten neuen Server-Schritt auseinanderlaufen.

        // Beim Oeffnen geladener Ist-Zustand — Quelle fuer "leer = unveraendert".
        private readonly AddinSettings _loaded;

        public SettingsDialog()
        {
            _loaded = LoadSafe();
            BuildUi();
            PopulateFrom(_loaded);
        }

        private static AddinSettings LoadSafe()
        {
            try
            {
                return SecureSettingsStore.Load();
            }
            catch
            {
                // Defekte/leere Settings duerfen den Dialog nicht abstuerzen lassen.
                return new AddinSettings();
            }
        }

        // Moderne Palette (identisch zur ChatView-Anmutung).
        private static readonly Color DlgBg = Color.White;
        private static readonly Color PrimaryBg = Color.FromArgb(0x25, 0x63, 0xEB);
        private static readonly Color PrimaryHover = Color.FromArgb(0x1D, 0x4E, 0xD8);
        private static readonly Color SecondaryBg = Color.FromArgb(0xEE, 0xF1, 0xF5);
        private static readonly Color SecondaryHover = Color.FromArgb(0xDD, 0xE3, 0xEC);
        private static readonly Color SecondaryFg = Color.FromArgb(0x1E, 0x3A, 0x5F);

        private void BuildUi()
        {
            Text = "Vizpatch — Einstellungen";
            // Vizpatch-Emblem statt WinForms-Standardsymbol in der Titelleiste.
            FensterIcon.Anwenden(this);
            // Sizable statt FixedDialog: der Betreiber kann das Fenster bei Bedarf
            // vergroessern; der Inhalt scrollt ohnehin (AutoScroll unten), sodass
            // nichts mehr abgeschnitten wird.
            FormBorderStyle = FormBorderStyle.Sizable;
            StartPosition = FormStartPosition.CenterParent;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(560, 620);
            MinimumSize = new Size(480, 420);
            Font = new Font("Segoe UI", 9.5f);
            BackColor = DlgBg;

            // Der Formularinhalt (Table) liegt in einem AutoScroll-Panel: reicht die
            // Hoehe nicht, erscheint ein Scrollbalken statt abgeschnittener Zeilen.
            var scrollHost = new Panel
            {
                Dock = DockStyle.Fill,
                AutoScroll = true,
                BackColor = DlgBg,
            };

            var layout = new TableLayoutPanel
            {
                Dock = DockStyle.Top,
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                Padding = new Padding(16),
                ColumnCount = 2,
                BackColor = DlgBg,
            };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 150));
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));

            _backendUrl = AddRow(layout, "Backend-URL:", new TextBox { Dock = DockStyle.Fill });
            _agentId = AddRow(layout, "Agent-ID:", new TextBox { Dock = DockStyle.Fill });
            _username = AddRow(layout, "Benutzername:", new TextBox { Dock = DockStyle.Fill });
            _password = AddRow(layout, "Passwort:",
                new TextBox { Dock = DockStyle.Fill, UseSystemPasswordChar = true });

            AddHint(layout,
                "Passwort leer lassen = unveraendert. Wird DPAPI-verschluesselt "
                + "gespeichert (nie Klartext).");

            _originToken = AddRow(layout, "Origin-Token:", new TextBox { Dock = DockStyle.Fill });
            AddHint(layout,
                "Muss serverseitig in ADDIN_FRAME_ANCESTORS gelistet sein "
                + "(CSRF-Origin-Workaround). Default: https://outlook.office.com");

            _certThumbprint = AddRow(layout, "Zertifikat-Thumbprint:",
                new TextBox { Dock = DockStyle.Fill });
            AddHint(layout,
                "Optional: TLS-Pinning gegen ein selbstsigniertes Backend-Zertifikat. "
                + "Leer = normale System-Zertifikatskette.");

            _trustAnyCertificate = new ModernCheckBox
            {
                Text = "Jedes Zertifikat akzeptieren (TrustAnyCertificate)",
                AutoSize = true,
                ForeColor = SecondaryFg,
            };
            AddRow(layout, "", _trustAnyCertificate);

            var warning = new Label
            {
                Text = "Achtung: Deaktiviert die TLS-Zertifikatspruefung fuer diesen "
                    + "Client vollstaendig (MITM-Risiko). Nur im vertrauenswuerdigen, "
                    + "isolierten LAN aktivieren — sonst Thumbprint-Pinning nutzen.",
                ForeColor = Color.FromArgb(178, 34, 34),
                AutoSize = true,
                MaximumSize = new Size(360, 0),
                Margin = new Padding(0, 2, 0, 4),
            };
            AddRow(layout, "", warning);

            // 260725-tqr: Backend-gebundene Draft-Checkbox. Startet deaktiviert
            // mit Ladehinweis; der OnShown-Handler liest den Zustand vom Backend.
            _enableDraftCreation = new ModernCheckBox
            {
                Text = "Automatische Entwürfe erstellen",
                AutoSize = true,
                ForeColor = SecondaryFg,
                Enabled = false,
            };
            AddRow(layout, "", _enableDraftCreation);

            _draftHint = new Label
            {
                Text = "Wird vom Backend geladen …",
                ForeColor = Color.FromArgb(110, 110, 110),
                AutoSize = true,
                Margin = new Padding(0, 0, 0, 8),
                MaximumSize = new Size(360, 0),
            };
            AddRow(layout, "", _draftHint);

            // Intelligenz-Modus (Betreiber-Wunsch 2026-08-24). Der Hinweis nennt
            // ausdruecklich, WO der Modus wirkt und wo NICHT — ein Schalter, von
            // dem niemand weiss, was er tut, wird entweder nie oder aus falschen
            // Erwartungen benutzt.
            _intelligenzModus = new ComboBox
            {
                Dock = DockStyle.Fill,
                DropDownStyle = ComboBoxStyle.DropDownList,
                Enabled = false,
            };
            AddRow(layout, "Intelligenz:", _intelligenzModus);
            _intelligenzModus.Items.Add("Standard — schnell und günstig");
            _intelligenzModus.Items.Add("Hoch — genauer, mehr Tokenverbrauch");
            _intelligenzModus.SelectedIndex = 0;

            _intelligenzHint = new Label
            {
                Text = "Wird vom Backend geladen …",
                ForeColor = Color.FromArgb(110, 110, 110),
                AutoSize = true,
                Margin = new Padding(0, 0, 0, 8),
                MaximumSize = new Size(360, 0),
            };
            AddRow(layout, "", _intelligenzHint);

            // URL-01..09: Urlaubs-Autoantwort. Der Warnsatz steht bewusst VOR den
            // Bedienelementen — er ist der Unterschied zu allem anderen in diesem
            // Dialog.
            AddRow(layout, "", new Label
            {
                Text = "Urlaubs-Autoantwort — die einzige Funktion, die selbständig "
                    + "Mails VERSCHICKT. Antwortet im Zeitraum unten jedem Absender "
                    + "genau einmal mit der Vorlage „## Urlaub“.",
                ForeColor = Color.FromArgb(178, 34, 34),
                AutoSize = true,
                MaximumSize = new Size(360, 0),
                Margin = new Padding(0, 8, 0, 4),
            });

            _urlaubVon = AddRow(layout, "Urlaub von:",
                new TextBox { Dock = DockStyle.Fill, Enabled = false });
            _urlaubBis = AddRow(layout, "Urlaub bis:",
                new TextBox { Dock = DockStyle.Fill, Enabled = false });

            _enableUrlaub = new ModernCheckBox
            {
                Text = "Urlaubs-Autoantwort aktivieren",
                AutoSize = true,
                ForeColor = SecondaryFg,
                Enabled = false,
            };
            AddRow(layout, "", _enableUrlaub);

            _urlaubDryRun = new ModernCheckBox
            {
                // Der Haken ist eine doppelte Verneinung: ANGEHAKT heisst „geht
                // nicht raus". Der Betreiber hat am 2026-08-24 gemeldet, dass
                // genau das nicht ankommt — deshalb steht die Folge des
                // Entfernens jetzt IM Label und nicht nur im Hinweistext.
                Text = "Probelauf — es wird NICHTS verschickt (Haken entfernen zum Scharfschalten)",
                AutoSize = true,
                ForeColor = SecondaryFg,
                Enabled = false,
            };
            AddRow(layout, "", _urlaubDryRun);

            _urlaubVorlage = new TextBox
            {
                Multiline = true,
                ScrollBars = ScrollBars.Vertical,
                Height = 92,
                Dock = DockStyle.Fill,
                Enabled = false,
            };
            AddRow(layout, "Text der Antwort:", _urlaubVorlage);

            _urlaubHint = new Label
            {
                Text = "Wird vom Backend geladen …",
                ForeColor = Color.FromArgb(110, 110, 110),
                AutoSize = true,
                Margin = new Padding(0, 0, 0, 8),
                MaximumSize = new Size(360, 0),
            };
            AddRow(layout, "", _urlaubHint);

            // 260728-irn: Update-Bereich. Sekundär-Stil (wie "Abbrechen") — der
            // Update-Button ist ein Zusatzangebot, kein Primäraktion des Dialogs.
            _updateButton = new RoundButton(SecondaryBg, SecondaryHover, SecondaryFg)
            {
                Text = "Jetzt updaten",
                Height = 34,
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                Font = new Font("Segoe UI Semibold", 9.5f),
                Enabled = false,
            };
            _updateButton.Click += UpdateButton_Click;
            _updateButton.Margin = new Padding(0, 0, 10, 0);

            _updateHint = new Label
            {
                Text = "Update-Prüfung läuft …",
                ForeColor = Color.FromArgb(110, 110, 110),
                AutoSize = true,
                // Oben statt exakt mittig ausgerichtet: die Fehler-/Fortschritts-
                // texte sind mehrzeilig und wachsen nach unten. Bei einzeiligem
                // Text sitzt der Hinweis so etwa auf Button-Mitte.
                Margin = new Padding(0, 9, 0, 0),
                MaximumSize = new Size(230, 0),
            };

            // Button und Statustext in EINER Zeile (Betreiber-Vorgabe "daneben").
            // FlowLayoutPanel statt zweier AddRow-Zeilen, weil AddRow das Margin
            // des uebergebenen Controls ueberschreibt.
            var updateRow = new FlowLayoutPanel
            {
                FlowDirection = FlowDirection.LeftToRight,
                WrapContents = false,
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
            };
            updateRow.Controls.Add(_updateButton);
            updateRow.Controls.Add(_updateHint);
            AddRow(layout, "", updateRow);

            // Hoehe muss das Padding auf BEIDEN Seiten tragen:
            // 12 (oben) + 34 (Buttonhoehe) + 12 (unten) = 58 als Minimum. Mit den
            // vorherigen 52 wurden die unteren 6 px abgeschnitten, und die Buttons
            // klebten am Fensterrand (Betreiber-Meldung 2026-08-25). 62 gibt
            // darueber hinaus 4 px Luft nach unten.
            var buttonBar = new FlowLayoutPanel
            {
                Dock = DockStyle.Bottom,
                FlowDirection = FlowDirection.RightToLeft,
                Height = 62,
                Padding = new Padding(12),
                BackColor = DlgBg,
            };
            var saveButton = new RoundButton(PrimaryBg, PrimaryHover, Color.White)
            {
                Text = "Speichern",
                Height = 34,
                AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink,
                Margin = new Padding(6, 0, 0, 0),
                Font = new Font("Segoe UI Semibold", 9.5f),
                DialogResult = DialogResult.None,
            };
            saveButton.Click += SaveButton_Click;
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

            buttonBar.Controls.Add(saveButton);
            buttonBar.Controls.Add(cancelButton);

            scrollHost.Controls.Add(layout);
            // Reihenfolge: Fill-Host zuerst, Bottom-Buttonleiste zuletzt — so dockt
            // die Leiste unten und der scrollbare Inhalt fuellt den Rest darueber.
            Controls.Add(scrollHost);
            Controls.Add(buttonBar);
            AcceptButton = saveButton;
            CancelButton = cancelButton;
        }

        private static TextBox AddRow(TableLayoutPanel layout, string label, Control field)
        {
            int row = layout.RowCount;
            layout.RowCount = row + 1;
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            layout.Controls.Add(new Label
            {
                Text = label,
                Dock = DockStyle.Fill,
                TextAlign = ContentAlignment.MiddleLeft,
                ForeColor = SecondaryFg,
                Margin = new Padding(0, 7, 8, 7),
            }, 0, row);
            // Flacher, moderner Feld-Look (WinForms-Default Fixed3D wirkt altmodisch).
            if (field is TextBox tb)
            {
                tb.BorderStyle = BorderStyle.FixedSingle;
                tb.BackColor = Color.White;
                tb.Font = new Font("Segoe UI", 10f);
            }
            else if (field is ComboBox cb)
            {
                // Dieselbe Schrift wie die Textfelder, sonst sitzt die Auswahlbox
                // sichtbar kleiner in derselben Spalte. Der Rahmen kommt bei
                // DropDownList vom System und ist nicht setzbar.
                cb.BackColor = Color.White;
                cb.Font = new Font("Segoe UI", 10f);
            }
            field.Margin = new Padding(0, 4, 0, 4);
            layout.Controls.Add(field, 1, row);
            return field as TextBox;
        }

        private static void AddHint(TableLayoutPanel layout, string text)
        {
            int row = layout.RowCount;
            layout.RowCount = row + 1;
            layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            var hint = new Label
            {
                Text = text,
                ForeColor = Color.FromArgb(110, 110, 110),
                AutoSize = true,
                Margin = new Padding(0, 0, 0, 8),
                MaximumSize = new Size(360, 0),
            };
            layout.Controls.Add(hint, 1, row);
        }

        private void PopulateFrom(AddinSettings s)
        {
            _backendUrl.Text = s.BackendUrl ?? "";
            _agentId.Text = s.AgentId ?? "";
            _username.Text = s.Username ?? "";
            // "leer = unveraendert": Passwortfeld bewusst NICHT vorbelegen.
            // (WinForms net48 kennt kein PlaceholderText; der Hinweis steht als
            // Label unter dem Feld.)
            _password.Text = "";
            _originToken.Text = string.IsNullOrEmpty(s.AddinOriginToken)
                ? new AddinSettings().AddinOriginToken
                : s.AddinOriginToken;
            _certThumbprint.Text = s.CertThumbprint ?? "";
            _trustAnyCertificate.Checked = s.TrustAnyCertificate;
        }

        /// <summary>
        /// 260725-tqr: Lädt beim Öffnen den Draft-Zustand VOM Backend (Wahrheits-
        /// quelle). Bei Erfolg wird die Checkbox aktiviert und gesetzt; bei fehlender
        /// Backend-URL/Agent-ID oder jedem Netz-/TLS-/HTTP-Fehler bleibt sie
        /// deaktiviert mit grauem Hinweis — dann greift die Einstellung nicht und es
        /// wird beim Speichern nichts geschrieben (_draftFlagLoaded bleibt false).
        /// </summary>
        protected override async void OnShown(EventArgs e)
        {
            base.OnShown(e);

            // VSTO: Der modale Dialog hat keinen echten Top-Level-Owner und öffnet
            // sonst HINTER dem Outlook-Fenster (der Betreiber muss ihn erst über die
            // Taskleiste nach vorn holen). In den Vordergrund zwingen: TopMost=true
            // bringt das Fenster nach vorn, das sofortige false löst die Dauer-
            // Pinnung wieder (z-Order bleibt vorn, ohne "immer im Vordergrund").
            TopMost = true;
            Activate();
            BringToFront();
            TopMost = false;

            // 260728-irn: eigenstaendiger, NICHT blockierender Update-Check — bewusst
            // NICHT in die await-Kette des Draft-Flags eingehaengt. Der Check darf
            // das Oeffnen und die Bedienbarkeit der Einstellungen niemals verzoegern
            // (D-1).
            CheckForUpdateAsync();

            if (string.IsNullOrWhiteSpace(_loaded.BackendUrl)
                || string.IsNullOrWhiteSpace(_loaded.AgentId))
            {
                SetDraftUnavailable();
                SetUrlaubUnavailable();
                return;
            }

            try
            {
                using (var client = new ChatClient(_loaded))
                using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15)))
                {
                    bool enabled = await client.GetDraftEnabledAsync(_loaded.AgentId, cts.Token);
                    _enableDraftCreation.Checked = enabled;
                    _enableDraftCreation.Enabled = true;
                    _draftFlagLoaded = true;
                    _draftHint.Text = "Steuert, ob dieser Agent automatisch Entwürfe erstellt.";
                    _draftHint.ForeColor = Color.FromArgb(110, 110, 110);
                }
            }
            catch
            {
                SetDraftUnavailable();
            }

            // Eigener try/catch: ein aelteres Backend ohne den Urlaubs-Endpoint
            // (404) darf die bereits geladene Draft-Checkbox nicht mit
            // deaktivieren. Die zwei Einstellungen sind unabhaengig.
            try
            {
                using (var client = new ChatClient(_loaded))
                using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15)))
                {
                    var stand = await client.GetUrlaubAsync(_loaded.AgentId, cts.Token);
                    _enableUrlaub.Checked = stand.Enabled;
                    _urlaubDryRun.Checked = stand.DryRun;
                    _urlaubVon.Text = stand.Von;
                    _urlaubBis.Text = stand.Bis;
                    // Gespeicherter Text gewinnt. Gibt es keinen, steht der
                    // Vorschlag zum Lesen und Aendern da — wirksam wird er erst
                    // durch Speichern, der Riegel „ohne Vorlage kein Versand"
                    // bleibt also unangetastet.
                    _urlaubVorlage.Text = string.IsNullOrWhiteSpace(stand.VorlageText)
                        ? stand.VorlageVorschlag
                        : stand.VorlageText;
                    _urlaubVorlageGeladen = _urlaubVorlage.Text;
                    _enableUrlaub.Enabled = true;
                    _urlaubDryRun.Enabled = true;
                    _urlaubVon.Enabled = true;
                    _urlaubBis.Enabled = true;
                    _urlaubVorlage.Enabled = true;
                    _urlaubLoaded = true;
                    ApplyUrlaubHint(stand.VorlageVorhanden);
                }
            }
            catch
            {
                SetUrlaubUnavailable();
            }

            // Wieder ein eigener try/catch: ein aelteres Backend ohne den
            // Intelligenz-Endpoint (404) darf die beiden bereits geladenen
            // Bereiche oberhalb nicht mit deaktivieren.
            try
            {
                using (var client = new ChatClient(_loaded))
                using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15)))
                {
                    string modus = await client.GetIntelligenzModusAsync(_loaded.AgentId, cts.Token);
                    _intelligenzModus.SelectedIndex = modus == "hoch" ? 1 : 0;
                    _intelligenzModus.Enabled = true;
                    _intelligenzLoaded = true;
                    _intelligenzHint.Text =
                        "Wirkt auf: Erkennen von Kundenanfragen, automatisches Einsortieren, "
                        + "Urlaubs-Automatenfilter — und die Entwürfe werden mit dem stärksten "
                        + "Modell geschrieben. „Hoch“ kostet mehr beim KI-Anbieter. Ändert "
                        + "NICHT, ob überhaupt gesendet oder sortiert wird.";
                    _intelligenzHint.ForeColor = Color.FromArgb(110, 110, 110);
                }
            }
            catch
            {
                SetIntelligenzUnavailable();
            }
        }

        private void SetIntelligenzUnavailable()
        {
            _intelligenzLoaded = false;
            _intelligenzModus.Enabled = false;
            _intelligenzHint.Text =
                "Backend nicht erreichbar — Einstellung kann nur bei Verbindung geändert werden.";
            _intelligenzHint.ForeColor = Color.FromArgb(110, 110, 110);
        }

        private void SetDraftUnavailable()
        {
            _draftFlagLoaded = false;
            _enableDraftCreation.Enabled = false;
            _draftHint.Text = "Backend nicht erreichbar — Einstellung kann nur bei Verbindung geändert werden.";
            _draftHint.ForeColor = Color.FromArgb(110, 110, 110);
        }

        private void SetUrlaubUnavailable()
        {
            _urlaubLoaded = false;
            _intelligenzLoaded = false;
            _enableUrlaub.Enabled = false;
            _urlaubDryRun.Enabled = false;
            _urlaubVorlage.Enabled = false;
            _urlaubVon.Enabled = false;
            _urlaubBis.Enabled = false;
            _urlaubHint.Text = "Backend nicht erreichbar — Einstellung kann nur bei Verbindung geändert werden.";
            _urlaubHint.ForeColor = Color.FromArgb(110, 110, 110);
        }

        /// <summary>
        /// Setzt den Hinweistext unter den Urlaubsfeldern. Fehlt der Abschnitt
        /// <c>## Urlaub</c> in den Vorlagen, wird NICHTS gesendet — das muss der
        /// Betreiber sehen, BEVOR er in den Urlaub faehrt und sich auf eine
        /// Antwort verlaesst, die nie kommt. Deshalb rot statt grau.
        /// </summary>
        private void ApplyUrlaubHint(bool vorlageVorhanden)
        {
            if (!vorlageVorhanden)
            {
                _urlaubHint.Text = UrlaubHintText.MissingTemplate;
                _urlaubHint.ForeColor = Color.FromArgb(178, 34, 34);
                return;
            }
            _urlaubHint.Text = UrlaubHintText.Ready;
            _urlaubHint.ForeColor = Color.FromArgb(110, 110, 110);
        }

        // --- 260728-irn/260728-m8g: Update-Check + Self-Update (Stufe 1+2) -------

        /// <summary>
        /// Prueft beim Oeffnen ZUERST, ob bereits ein Update laeuft oder ein
        /// Endergebnis aus einem fruheren Lauf vorliegt (260728-m8g, D-5) —
        /// der Betreiber startet Outlook nach dem Server-Update oft neu und
        /// muss dann das ERGEBNIS sehen, nicht bloss "Version ist gleich
        /// geblieben". Nur wenn KEIN laufendes/abgeschlossenes Update
        /// gefunden wird, folgt der normale Erkennungs-Check aus Stufe 1
        /// (<see cref="ChatClient.GetUpdateInfoAsync"/>). Fehlender
        /// <see cref="AddinSettings.BackendUrl"/> oder jeder Netz-/TLS-/
        /// HTTP-Fehler landet identisch im "Kein Update verfuegbar"-Zustand —
        /// kein Popup, kein Blockieren. Alle UI-Aktualisierungen laufen ueber
        /// <see cref="MarshalToUi"/>, da der Dialog beim Antworten des Checks
        /// bereits geschlossen sein kann.
        /// </summary>
        private async void CheckForUpdateAsync()
        {
            if (string.IsNullOrWhiteSpace(_loaded.BackendUrl))
            {
                SetUpdateUnavailable("");
                return;
            }

            try
            {
                UpdateProgress progress = null;
                try
                {
                    using (var client = new ChatClient(_loaded))
                    using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8)))
                    {
                        progress = await client.GetUpdateProgressAsync(cts.Token);
                    }
                }
                catch
                {
                    progress = null; // Kein Update-Status ermittelbar -> normaler Check unten.
                }

                bool isRunning = progress != null && progress.IsRunning && !progress.Stale;

                // Erkennungs-Check auch dann holen, wenn ein TERMINALER Status
                // vorliegt. Genau das fehlte in der v1.17.0-Abnahme: das
                // Chat-Banner meldete korrekt "Update auf v1.17.0 verfuegbar",
                // der Dialog aber "Update auf v1.16.0 abgeschlossen" mit grauem
                // Button. Die Statusdatei wird nie zurueckgesetzt, der alte
                // Status gewann also jedes Mal erneut — ab dem zweiten Update
                // war der Button dauerhaft unklickbar. Bei laufendem Update
                // wird der Check gespart: er wuerde die Anzeige nicht aendern.
                UpdateInfo info = null;
                if (!isRunning)
                {
                    try
                    {
                        using (var client = new ChatClient(_loaded))
                        using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8)))
                        {
                            info = await client.GetUpdateInfoAsync(cts.Token);
                        }
                    }
                    catch
                    {
                        info = null; // Kein Check moeglich -> Altstatus/Unavailable.
                    }
                }

                // EINE Entscheidung, in VizpatchAddin.Core getestet
                // (UpdateDisplayDecisionTests) — die Priorisierung war der Bug,
                // deshalb liegt sie nicht mehr als if-Kette in der UI.
                var mode = UpdateDisplayDecision.Decide(
                    progress != null,
                    progress == null ? null : progress.State,
                    progress != null && progress.Stale,
                    info != null && info.UpdateAvailable,
                    info == null ? null : info.Latest);

                if (mode == UpdateDisplayMode.Running)
                {
                    if (!string.IsNullOrEmpty(progress.VersionTo))
                    {
                        _latestVersion = progress.VersionTo;
                    }
                    // Ein LAUFENDES Update gehoert ins Update-Fenster, nicht in
                    // diesen Dialog: sonst stuende der Fortschritt wieder ueber
                    // „Speichern"/„Abbrechen" — genau die Verwechslungsgefahr,
                    // die der Betreiber beanstandet hat. Dieser Dialog schliesst
                    // deshalb und uebergibt im Beobachtungs-Modus (es wird NICHT
                    // erneut gestartet).
                    MarshalToUi(() =>
                    {
                        ShowUpdateRunning(progress);
                        RequestUpdateWindow(_latestVersion, true);
                    });
                    return;
                }

                if (mode == UpdateDisplayMode.OfferUpdate)
                {
                    // Ein fehlgeschlagener Vorlauf bleibt sichtbar, auch wenn
                    // inzwischen eine neuere Version bereitliegt.
                    string note = progress == null ? "" : PreviousAttemptNote(progress);
                    MarshalToUi(() => ApplyUpdateInfo(info, note));
                    return;
                }

                if (mode == UpdateDisplayMode.LastResult)
                {
                    MarshalToUi(() => ShowUpdateResult(progress));
                    return;
                }

                // Bewusst ueber ApplyUpdateInfo, wenn ein Check-Ergebnis vorliegt:
                // die Methode raeumt zusaetzlich veraltetes Add-in-Staging auf
                // (D-6 Punkt 7) und endet dann selbst in SetUpdateUnavailable.
                if (info != null)
                {
                    MarshalToUi(() => ApplyUpdateInfo(info, ""));
                    return;
                }
                MarshalToUi(() => SetUpdateUnavailable(""));
            }
            catch
            {
                MarshalToUi(() => SetUpdateUnavailable(""));
            }
        }

        /// <summary>
        /// Kurznotiz zum vorherigen Update-Versuch, wenn dieser schiefging.
        /// Ein Fehlschlag darf nicht verschwinden, nur weil inzwischen eine
        /// neuere Version bereitliegt — der Betreiber soll sehen, dass sein
        /// letzter Anlauf nicht durchlief. Nach Erfolg ist keine Notiz noetig.
        /// </summary>
        private static string PreviousAttemptNote(UpdateProgress progress)
        {
            string version = string.IsNullOrEmpty(progress.VersionTo)
                ? "" : " (" + progress.VersionTo + ")";
            if (progress.State == "rolled_back")
            {
                return "Letzter Versuch" + version
                    + " fehlgeschlagen — Vorversion wurde wiederhergestellt.";
            }
            if (progress.State == "failed")
            {
                return "Letzter Versuch" + version + " fehlgeschlagen.";
            }
            return "";
        }

        private void ApplyUpdateInfo(UpdateInfo info)
        {
            ApplyUpdateInfo(info, "");
        }

        private void ApplyUpdateInfo(UpdateInfo info, string previousAttemptNote)
        {
            // 260728-m8g, D-6 Punkt 7: kein Update mehr verfuegbar/vorhanden ->
            // ein evtl. aus einem fruheren Lauf uebrig gebliebenes Staging ist
            // veraltet und wird verworfen (best effort, nie blockierend).
            ClearStaleStagingIfNoUpdateAvailable(info.UpdateAvailable);

            if (info.UpdateAvailable && !string.IsNullOrEmpty(info.Latest))
            {
                _latestVersion = info.Latest;
                _updateButton.Enabled = true;
                // Nicht den Button-Text wiederholen ("Jetzt updaten" steht schon
                // daneben) — nur die verfuegbare Version nennen.
                _updateHint.Text = "Version " + info.Latest + " verfügbar";
                if (!string.IsNullOrEmpty(previousAttemptNote))
                {
                    _updateHint.Text += Environment.NewLine + previousAttemptNote;
                }
                _updateHint.ForeColor = Color.FromArgb(110, 110, 110);
                return;
            }
            SetUpdateUnavailable(info.Message ?? "");
        }

        private void SetUpdateUnavailable(string extraMessage)
        {
            _updateButton.Enabled = false;
            _updateHint.Text = string.IsNullOrEmpty(extraMessage)
                ? "Kein Update verfügbar"
                : "Kein Update verfügbar" + Environment.NewLine + extraMessage;
            _updateHint.ForeColor = Color.FromArgb(110, 110, 110);
        }

        private void ShowUpdateRunning(UpdateProgress progress)
        {
            _updateRunning = true;
            _updateButton.Enabled = false;
            _updateHint.Text = "Update läuft … " + progress.Progress + " % ("
                + UpdateWindowState.StepLabel(progress.Step) + ")";
            _updateHint.ForeColor = Color.FromArgb(110, 110, 110);
        }

        private void ShowUpdateResult(UpdateProgress progress)
        {
            _updateRunning = false;
            // Deaktiviert ist hier korrekt: diese Methode laeuft nur, wenn der
            // frische Erkennungs-Check KEIN neues Update gemeldet hat (siehe
            // CheckForUpdateAsync). Es gibt also nichts zu klicken.
            // Frueher stand hier die Annahme, der naechste Dialog-Aufruf hole
            // "automatisch die aktuelle Lage" — das war der Grund, warum der
            // Button ab dem zweiten Update dauerhaft grau blieb.
            _updateButton.Enabled = false;

            if (progress.State == "success")
            {
                _updateHint.Text = string.IsNullOrEmpty(progress.Message)
                    ? "Server erfolgreich aktualisiert."
                    : progress.Message;
                _updateHint.ForeColor = Color.FromArgb(110, 110, 110);
                return;
            }

            if (progress.State == "rolled_back")
            {
                _updateHint.Text = "Update fehlgeschlagen — Vorversion wiederhergestellt. "
                    + progress.Message;
                _updateHint.ForeColor = Color.FromArgb(178, 34, 34);
                return;
            }

            // "failed"
            _updateHint.Text = "Update fehlgeschlagen: " + progress.Message
                + (string.IsNullOrEmpty(progress.RecoveryHint)
                    ? ""
                    : Environment.NewLine + progress.RecoveryHint);
            _updateHint.ForeColor = Color.FromArgb(178, 34, 34);
        }


        /// <summary>
        /// Loest das echte Server-Update aus (260728-m8g, Stufe 2). GENAU EINE
        /// Rueckfrage (Fakt 8 aus CONTEXT.md — der Betreiber will keine
        /// mehrstufigen Bestaetigungsdialoge). Vollstaendig in try/catch
        /// (VSTO-Threading-Pflicht: kein <c>async void</c> ohne umschliessendes
        /// try/catch).
        /// </summary>
        /// <summary>
        /// Der Betreiber hat das Update angefordert; dieser Dialog ist bereits
        /// geschlossen. Der Aufrufer (<c>ChatView.SettingsButton_Click</c>)
        /// oeffnet daraufhin den <see cref="UpdateDialog"/>.
        /// </summary>
        public bool UpdateRequested { get; private set; }

        /// <summary>Zielversion fuer den Start; leer im Beobachtungs-Fall.</summary>
        public string UpdateTargetVersion { get; private set; } = "";

        /// <summary>
        /// true = es lief serverseitig schon ein Update, das Fenster soll es
        /// nur BEOBACHTEN und nicht erneut starten.
        /// </summary>
        public bool UpdateAttachOnly { get; private set; }

        /// <summary>
        /// Betreiber-Wunsch 2026-08-22: der Update-Vorgang laeuft NICHT mehr in
        /// diesem Dialog. Fruehr blieb nach dem Update ein Einstellungen-Fenster
        /// mit „Speichern" und „Abbrechen" stehen — nach einem abgeschlossenen
        /// Update sinnlos und verwirrend. Dieser Dialog schliesst jetzt und
        /// uebergibt an den <see cref="UpdateDialog"/> (nicht wegklickbar, ein
        /// einziger Knopf „OK").
        /// </summary>
        private void UpdateButton_Click(object sender, EventArgs e)
        {
            try
            {
                if (_updateRunning || string.IsNullOrEmpty(_latestVersion))
                {
                    return;
                }

                // Der Hinweis auf verworfene Eingaben haengt im SELBEN Dialog
                // wie die Update-Bestaetigung — ein zweiter Rueckfrage-Dialog
                // waere genau die Doppelung, die hier abgeschafft wird.
                string aenderungsHinweis = HasUnsavedChanges()
                    ? Environment.NewLine + Environment.NewLine
                      + "Achtung: nicht gespeicherte Änderungen in diesem Fenster werden "
                      + "dabei verworfen."
                    : "";

                var confirm = MessageBox.Show(
                    this,
                    "Vizpatch auf " + _latestVersion + " aktualisieren?" + Environment.NewLine
                    + Environment.NewLine
                    + "Der Server startet dabei kurz neu; laufende Postfach-Verarbeitung wird "
                    + "vorher sauber beendet. Bei einem Problem wird automatisch die Vorversion "
                    + "wiederhergestellt."
                    + aenderungsHinweis,
                    "Vizpatch — Update",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question);
                if (confirm != DialogResult.Yes)
                {
                    return;
                }

                RequestUpdateWindow(_latestVersion, false);
            }
            catch
            {
                // Vollstaendig geschluckt — ein Fehlerdialog hier wuerde den
                // Betreiber ohne Mehrwert blockieren.
            }
        }

        /// <summary>
        /// Schliesst diesen Dialog und markiert, dass das Update-Fenster
        /// geoeffnet werden soll. <see cref="DialogResult.Cancel"/> ist hier
        /// korrekt: es wurde NICHTS gespeichert (der Aufrufer behandelt nur
        /// <see cref="DialogResult.OK"/> als „Einstellungen gespeichert").
        /// </summary>
        private void RequestUpdateWindow(string targetVersion, bool attachOnly)
        {
            UpdateRequested = true;
            UpdateTargetVersion = targetVersion ?? "";
            UpdateAttachOnly = attachOnly;
            DialogResult = DialogResult.Cancel;
            Close();
        }

        /// <summary>
        /// Weicht mindestens ein Eingabefeld vom geladenen Stand ab? Verglichen
        /// wird gegen <c>_loaded</c> mit derselben Lesart wie
        /// <c>SaveButton_Click</c> — insbesondere gilt ein LEERES Passwortfeld
        /// als „unveraendert" (die „leer = unveraendert"-Regel des Dialogs),
        /// sonst waere jeder Dialog sofort als geaendert gemeldet.
        /// </summary>
        private bool HasUnsavedChanges()
        {
            var standard = new AddinSettings();
            string originSoll = string.IsNullOrWhiteSpace(_originToken.Text)
                ? standard.AddinOriginToken
                : _originToken.Text.Trim();

            return _backendUrl.Text.Trim() != (_loaded.BackendUrl ?? "")
                || _agentId.Text.Trim() != (_loaded.AgentId ?? "")
                || _username.Text.Trim() != (_loaded.Username ?? "")
                || !string.IsNullOrEmpty(_password.Text)
                || originSoll != (_loaded.AddinOriginToken ?? "")
                || _certThumbprint.Text.Trim() != (_loaded.CertThumbprint ?? "")
                || _trustAnyCertificate.Checked != _loaded.TrustAnyCertificate;
        }

        // PollUpdateAsync und BeginAddinSelfUpdate sind nach UpdateDialog.cs
        // gewandert (Betreiber-Wunsch 2026-08-22): der Vorgang laeuft in einem
        // eigenen, nicht wegklickbaren Fenster mit einem einzigen "OK"-Knopf,
        // nicht mehr in diesem Dialog neben "Speichern"/"Abbrechen". Hier
        // bleibt nur die ANZEIGE des Zustands (ShowUpdateRunning/
        // ShowUpdateResult/ApplyUpdateInfo) sowie das Anfordern des Fensters
        // ueber RequestUpdateWindow.

        /// <summary>
        /// Verwirft ein bereits gestagtes, aber nicht mehr passendes Staging
        /// (260728-m8g, D-6, Punkt 7): sobald der Server KEIN Update mehr
        /// anbietet (<paramref name="updateAvailable"/> == false), ist jede
        /// vorhandene Staging-Ablage aus einem fruheren Lauf zwangslaeufig
        /// veraltet — sonst wuerde ein alter Helfer nach einem anderweitig
        /// erfolgten Update noch etwas installieren. Best-Effort, nie werfend.
        /// </summary>
        private static void ClearStaleStagingIfNoUpdateAvailable(bool updateAvailable)
        {
            if (updateAvailable)
            {
                return;
            }
            AddinSelfUpdate.ClearStaging();
        }


        /// <summary>
        /// Marshallt UI-Aktualisierungen nach einem Netzwerk-Call auf den
        /// UI-Thread (VSTO: kein SynchronizationContext im Outlook-Thread, jede
        /// await-Fortsetzung landet sonst auf dem Threadpool -> Crash, 260725-tqr).
        /// Prueft zusaetzlich <see cref="Form.IsDisposed"/>/<see cref="Form.Disposing"/>,
        /// da der Dialog geschlossen sein kann, bevor der Check antwortet.
        /// </summary>
        private void MarshalToUi(Action action)
        {
            if (IsDisposed || Disposing)
            {
                return;
            }
            if (IsHandleCreated && InvokeRequired)
            {
                try
                {
                    BeginInvoke(action);
                }
                catch (ObjectDisposedException)
                {
                    // Dialog wurde zwischen Netzwerk-Call und Marshalling
                    // geschlossen — nichts mehr zu tun.
                }
                catch (InvalidOperationException)
                {
                    // Handle wurde zerstoert (Dialog schliesst gerade) — ebenfalls
                    // sicher ignorierbar.
                }
            }
            else
            {
                action();
            }
        }

        private async void SaveButton_Click(object sender, EventArgs e)
        {
            var settings = new AddinSettings
            {
                BackendUrl = _backendUrl.Text.Trim(),
                AgentId = _agentId.Text.Trim(),
                Username = _username.Text.Trim(),
                // "leer = unveraendert": leeres Feld behaelt den gespeicherten Wert.
                Password = string.IsNullOrEmpty(_password.Text)
                    ? (_loaded.Password ?? "")
                    : _password.Text,
                AddinOriginToken = string.IsNullOrWhiteSpace(_originToken.Text)
                    ? new AddinSettings().AddinOriginToken
                    : _originToken.Text.Trim(),
                CertThumbprint = _certThumbprint.Text.Trim(),
                TrustAnyCertificate = _trustAnyCertificate.Checked,
                // Reine UI-Praeferenz (Dark Mode) wird im Pane-Header umgeschaltet,
                // nicht in diesem Dialog — beim Speichern der Verbindungsdaten den
                // bereits gespeicherten Wert unveraendert uebernehmen, sonst wuerde
                // das Theme bei jeder Konfig-Aenderung zurueckgesetzt.
                DarkMode = _loaded.DarkMode,
            };

            try
            {
                SecureSettingsStore.Save(settings);
            }
            catch (Exception ex)
            {
                MessageBox.Show(this,
                    "Speichern fehlgeschlagen: " + ex.Message,
                    "Vizpatch", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            // 260725-tqr: Draft-Flag NUR ans Backend schreiben, wenn es zuvor
            // erfolgreich geladen wurde (Backend erreichbar). Fehler beim Schreiben
            // sind NICHT-blockierend — der Dialog schließt trotzdem mit OK. Das Flag
            // wird bewusst NICHT lokal (DPAPI) persistiert; Wahrheitsquelle ist das
            // Backend.
            // 260728-m8g: waehrend eines laufenden Self-Updates wird der Schreib-
            // Versuch uebersprungen — das Backend startet gerade neu, ein Request
            // wuerde nur unnoetig fehlschlagen (kein Fehlerdialog gewuenscht).
            if (_draftFlagLoaded && !_updateRunning)
            {
                try
                {
                    using (var client = new ChatClient(settings))
                    using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15)))
                    {
                        await client.SetDraftEnabledAsync(
                            settings.AgentId, _enableDraftCreation.Checked, cts.Token);
                    }
                }
                catch (Exception ex)
                {
                    MessageBox.Show(this,
                        "Entwurf-Einstellung konnte nicht ans Backend geschrieben werden: "
                        + ex.Message,
                        "Vizpatch", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            }

            // URL-01..09: Urlaubs-Autoantwort. Anders als beim Draft-Flag ist ein
            // Fehler hier BLOCKIEREND — der Dialog bleibt offen und der Betreiber
            // korrigiert. Grund: ein still verworfener Zeitraum faellt erst im
            // Urlaub auf, wenn niemand mehr hinsieht, und der Betreiber verlaesst
            // sich auf Antworten, die nie herausgehen.
            if (_urlaubLoaded && !_updateRunning)
            {
                string von = (_urlaubVon.Text ?? "").Trim();
                string bis = (_urlaubBis.Text ?? "").Trim();

                // Vorpruefung im Client, damit der Fehler ohne Roundtrip sichtbar
                // wird. Das Backend prueft denselben Fall noch einmal — ein Client
                // ist nie die letzte Instanz.
                string fehler = UrlaubHintText.ValidateZeitraum(
                    von, bis, _enableUrlaub.Checked);
                if (fehler != null)
                {
                    MessageBox.Show(this, fehler, "Vizpatch",
                        MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    _urlaubVon.Focus();
                    return;
                }

                try
                {
                    using (var client = new ChatClient(settings))
                    using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15)))
                    {
                        // Nur bei echter Aenderung mitschicken (siehe
                        // `_urlaubVorlageGeladen`): `null` laesst die
                        // gespeicherte Vorlage im Backend unberuehrt.
                        string vorlage = (_urlaubVorlage.Text ?? "") == _urlaubVorlageGeladen
                            ? null
                            : (_urlaubVorlage.Text ?? "");

                        await client.SetUrlaubAsync(
                            settings.AgentId,
                            _enableUrlaub.Checked,
                            _urlaubDryRun.Checked,
                            von,
                            bis,
                            cts.Token,
                            vorlage);
                    }
                }
                catch (InvalidOperationException ex)
                {
                    // Serverseitige Zeitraum-Ablehnung: deutsche Meldung, Dialog
                    // bleibt offen.
                    MessageBox.Show(this, ex.Message, "Vizpatch",
                        MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    _urlaubVon.Focus();
                    return;
                }
                catch (Exception ex)
                {
                    MessageBox.Show(this,
                        "Urlaubs-Einstellung konnte nicht ans Backend geschrieben werden: "
                        + ex.Message,
                        "Vizpatch", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
            }

            // Intelligenz-Modus. Wie bei den Bereichen oberhalb wird NUR
            // geschrieben, wenn der Wert vorher auch geladen werden konnte —
            // sonst wuerde ein nicht erreichbares Backend beim Speichern
            // stillschweigend "standard" schreiben und eine bestehende
            // Einstellung "hoch" ueberschreiben.
            if (_intelligenzLoaded && !_updateRunning)
            {
                try
                {
                    using (var client = new ChatClient(settings))
                    using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15)))
                    {
                        await client.SetIntelligenzModusAsync(
                            settings.AgentId,
                            _intelligenzModus.SelectedIndex == 1 ? "hoch" : "standard",
                            cts.Token);
                    }
                }
                catch (InvalidOperationException ex)
                {
                    MessageBox.Show(this, ex.Message, "Vizpatch",
                        MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                catch (Exception ex)
                {
                    MessageBox.Show(this,
                        "Intelligenz-Modus konnte nicht ans Backend geschrieben werden: "
                        + ex.Message,
                        "Vizpatch", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
            }

            DialogResult = DialogResult.OK;
            Close();
        }
    }
}
