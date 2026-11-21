using System;
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
    /// Eigenes Fenster fuer den Update-Vorgang (Betreiber-Wunsch 2026-08-22).
    ///
    /// <b>Warum es das gibt:</b> der Ablauf lief bisher IM Einstellungen-Dialog.
    /// Nach dem Update stand der Betreiber dort vor „Speichern" und
    /// „Abbrechen" — nach einem abgeschlossenen Update sinnlos und
    /// verwirrend („habe ich das Update gespeichert? Bricht Abbrechen es
    /// ab?"). Jetzt schliesst der Einstellungen-Dialog, dieses Fenster
    /// uebernimmt, und es hat genau EINEN Knopf: „OK".
    ///
    /// <b>Nicht wegklickbar, solange es laeuft</b> — und zwar dreifach
    /// abgesichert, weil ein einzelner Mechanismus jeweils nur einen Weg
    /// schliesst:
    /// <list type="bullet">
    ///   <item><c>ControlBox = false</c> entfernt das Fenster-X.</item>
    ///   <item>Kein <c>CancelButton</c> gesetzt -> Esc schliesst nicht.</item>
    ///   <item><see cref="OnFormClosing"/> bricht JEDEN Schliessversuch ab,
    ///     solange der Vorgang laeuft — das faengt Alt+F4 und ein
    ///     <c>Close()</c> aus Code ab.</item>
    /// </list>
    /// Ausnahme: <see cref="CloseReason.WindowsShutDown"/> und
    /// <c>TaskManagerClosing</c> werden NICHT blockiert. Ein Fenster, das das
    /// Herunterfahren von Windows verhindert, waere ein Defekt und kein
    /// Schutz.
    ///
    /// <b>Threading:</b> alle UI-Zugriffe aus <c>async</c>-Fortsetzungen
    /// laufen ueber <see cref="MarshalToUi"/>. Im VSTO-Host gibt es keinen
    /// WindowsFormsSynchronizationContext, eine Fortsetzung landet also auf
    /// einem Threadpool-Thread — ein direkter Control-Zugriff von dort
    /// beendet Outlook (Muster aus 260725-tqr, dieselbe Absicherung wie im
    /// SettingsDialog).
    /// </summary>
    internal sealed class UpdateDialog : Form
    {
        private static readonly Color DlgBg = Color.White;
        private static readonly Color PrimaryBg = Color.FromArgb(0x25, 0x63, 0xEB);
        private static readonly Color PrimaryHover = Color.FromArgb(0x1D, 0x4E, 0xD8);
        private static readonly Color MutedFg = Color.FromArgb(110, 110, 110);
        private static readonly Color ErrorFg = Color.FromArgb(178, 34, 34);
        private static readonly Color HeadFg = Color.FromArgb(0x1E, 0x3A, 0x5F);

        private readonly AddinSettings _settings;
        private readonly string _targetVersion;
        private readonly bool _attachOnly;

        private readonly Label _headline;
        private readonly Label _detail;
        private readonly ProgressBar _bar;
        // Wirt des Balkens — traegt den Abstand. Beim Ausblenden MUSS er
        // mit verschwinden, sonst bleibt im Endzustand eine leere Luecke
        // stehen, wo vorher der Balken war.
        private readonly Panel _barHost;
        private readonly RoundButton _okButton;

        private bool _finished;
        private CancellationTokenSource _pollCts;

        /// <param name="attachOnly">true = es laeuft bereits ein Update, es
        /// wird NUR beobachtet und NICHT erneut gestartet.</param>
        public UpdateDialog(AddinSettings settings, string targetVersion, bool attachOnly)
        {
            _settings = settings ?? new AddinSettings();
            _targetVersion = targetVersion ?? "";
            _attachOnly = attachOnly;

            Text = "Vizpatch — Update";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            // Kein X, kein Minimieren/Maximieren: der Vorgang darf nicht
            // mittendrin abgebrochen werden.
            ControlBox = false;
            MinimizeBox = false;
            MaximizeBox = false;
            ShowInTaskbar = false;
            // Vizpatch-Emblem statt WinForms-Standardsymbol; `Anwenden` setzt
            // ShowIcon selbst auf true, wenn das Emblem gelesen werden konnte.
            ShowIcon = false;
            FensterIcon.Anwenden(this);
            StartPosition = FormStartPosition.CenterParent;
            BackColor = DlgBg;
            ClientSize = new Size(460, 210);
            Padding = new Padding(18, 16, 18, 12);

            _headline = new Label
            {
                Dock = DockStyle.Top,
                AutoSize = false,
                Height = 26,
                Font = new Font("Segoe UI Semibold", 10.5f),
                ForeColor = HeadFg,
                Text = "",
            };

            // Der Balken steckt in einem Wirt mit Padding statt eigenem Margin:
            // `Margin` wirkt bei `Dock` NICHT (nur in Layout-Containern wie
            // FlowLayoutPanel), der frueher hier gesetzte Abstand von 6 px kam
            // also nie an — der Balken klebte direkt unter der Ueberschrift
            // (Betreiber-Meldung 2026-08-25 mit Screenshot). Der Wirt ist so
            // hoch wie Abstand + Balken + Abstand.
            _bar = new ProgressBar
            {
                Dock = DockStyle.Fill,
                Style = ProgressBarStyle.Continuous,
                Minimum = 0,
                Maximum = 100,
                Value = 0,
            };
            _barHost = new Panel
            {
                Dock = DockStyle.Top,
                Height = 12 + 8 + 10,
                Padding = new Padding(0, 12, 0, 10),
                BackColor = DlgBg,
            };
            _barHost.Controls.Add(_bar);

            _detail = new Label
            {
                Dock = DockStyle.Fill,
                AutoSize = false,
                Font = new Font("Segoe UI", 9f),
                ForeColor = MutedFg,
                Padding = new Padding(0, 10, 0, 0),
                Text = "",
            };

            _okButton = new RoundButton(PrimaryBg, PrimaryHover, Color.White)
            {
                Text = "OK",
                Height = 34,
                Width = 96,
                Font = new Font("Segoe UI Semibold", 9.5f),
                // Erst im Endzustand klickbar — vorher gibt es nichts zu
                // bestaetigen, und ein aktiver Knopf waere die Einladung, den
                // Vorgang fuer beendet zu halten.
                Enabled = false,
                DialogResult = DialogResult.OK,
            };
            var buttonBar = new Panel { Dock = DockStyle.Bottom, Height = 46, BackColor = DlgBg };
            _okButton.Location = new Point(buttonBar.Width - _okButton.Width, 6);
            _okButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            buttonBar.Controls.Add(_okButton);
            buttonBar.Resize += (s, e) =>
                _okButton.Left = Math.Max(0, buttonBar.ClientSize.Width - _okButton.Width);

            Controls.Add(buttonBar);
            Controls.Add(_detail);
            Controls.Add(_barHost);
            Controls.Add(_headline);

            // AcceptButton bewusst NICHT gesetzt: solange OK deaktiviert ist,
            // wuerde Enter sonst ins Leere laufen; im Endzustand genuegt der
            // Fokus auf dem einzigen Knopf.
            Apply(_attachOnly ? UpdateWindowState.AttachedToRunning() : UpdateWindowState.Starting());
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            // Erst NACH dem Anzeigen starten: der Vorgang schreibt sofort in
            // die Labels, und vor OnShown gibt es noch kein Handle zum
            // Marshallen.
            if (_attachOnly)
            {
                PollUpdateAsync();
            }
            else
            {
                StartUpdateAsync();
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            // Windows faehrt herunter oder der Taskmanager greift ein: nicht
            // blockieren. Alles andere (Alt+F4, Close() aus Code) wird
            // abgelehnt, solange der Vorgang laeuft.
            bool erzwungen = e.CloseReason == CloseReason.WindowsShutDown
                || e.CloseReason == CloseReason.TaskManagerClosing;
            if (!_finished && !erzwungen)
            {
                e.Cancel = true;
                return;
            }
            base.OnFormClosing(e);
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            _pollCts?.Cancel();
            _pollCts?.Dispose();
            _pollCts = null;
            base.OnFormClosed(e);
        }

        // --- Darstellung ---------------------------------------------------

        /// <summary>
        /// Zieht die Fensterhoehe nach, wenn der Meldungstext mehr Platz
        /// braucht als das Detail-Label hat.
        ///
        /// <b>Warum das noetig ist:</b> `_detail` ist `Dock = Fill` und
        /// `AutoSize = false` — es bekommt, was uebrig bleibt, und waechst
        /// NICHT mit dem Text. Bei der mehrzeiligen Erfolgsmeldung fiel
        /// deshalb die letzte Zeile heraus; beim Betreiber endete sie mitten
        /// im Satz („... automatisch aktualisiert — bitte Outlook",
        /// Screenshot 2026-08-27). Aufgezogen wird die FORM, nicht das Label:
        /// bei Dock-Layout wuerde eine Label-Hoehe ohnehin ueberschrieben.
        ///
        /// Waechst nur, schrumpft nie — sonst zappelt das Fenster bei jedem
        /// Zwischentext. Der Deckel verhindert, dass eine unerwartet lange
        /// Fehlermeldung das Fenster ueber den Bildschirm hinaus zieht.
        /// </summary>
        private void DetailHoeheSicherstellen()
        {
            int breite = _detail.ClientSize.Width - _detail.Padding.Horizontal;
            if (breite <= 0 || string.IsNullOrEmpty(_detail.Text))
            {
                return;
            }

            Size gebraucht = TextRenderer.MeasureText(
                _detail.Text, _detail.Font, new Size(breite, 0),
                TextFormatFlags.WordBreak | TextFormatFlags.TextBoxControl);

            int verfuegbar = _detail.ClientSize.Height - _detail.Padding.Vertical;
            int fehlt = gebraucht.Height - verfuegbar;
            if (fehlt <= 0)
            {
                return;
            }

            int maxHoehe = Screen.FromControl(this).WorkingArea.Height - 80;
            Height = Math.Min(Height + fehlt + 4, Math.Max(Height, maxHoehe));
        }

        private void Apply(UpdateWindowState state)
        {
            _headline.Text = state.Headline;
            _detail.Text = state.Detail;
            _detail.ForeColor = state.IsFailure ? ErrorFg : MutedFg;
            _finished = state.IsFinished;
            _okButton.Enabled = state.IsFinished;
            if (state.IsFinished)
            {
                _barHost.Visible = false;
                _okButton.Focus();
            }
            // NACH dem Ausblenden des Balkens messen — dadurch gewinnt das
            // Detail-Label 30 px, die sonst faelschlich als fehlend gelten.
            DetailHoeheSicherstellen();
        }

        private void ApplyProgressValue(int prozent)
        {
            int wert = prozent < 0 ? 0 : (prozent > 100 ? 100 : prozent);
            _bar.Value = wert;
        }

        /// <summary>
        /// Ein Zwischenschritt, der KEIN Zustandsuebergang ist (z. B. das
        /// Add-in-Nachladen nach erfolgreichem Server-Update): Text ersetzen,
        /// aber das Fenster NICHT freigeben.
        /// </summary>
        private void ApplyZwischentext(string headline, string detail)
        {
            _headline.Text = headline;
            _detail.Text = detail;
            _detail.ForeColor = MutedFg;
            DetailHoeheSicherstellen();
        }

        // --- Ablauf --------------------------------------------------------

        private async void StartUpdateAsync()
        {
            try
            {
                try
                {
                    using (var client = new ChatClient(_settings))
                    using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(30)))
                    {
                        await client.StartUpdateAsync(_targetVersion, cts.Token);
                    }
                }
                catch (UpdateAlreadyRunningException)
                {
                    // Kein Fehler: ein anderer Anlauf laeuft schon (D-5) —
                    // direkt ins Beobachten wechseln.
                }
                catch (Exception ex)
                {
                    MarshalToUi(() => Apply(UpdateWindowState.StartFailed(ex.Message)));
                    return;
                }

                PollUpdateAsync();
            }
            catch (Exception ex)
            {
                MarshalToUi(() => Apply(UpdateWindowState.StartFailed(ex.Message)));
            }
        }

        /// <summary>
        /// Pollt <c>GET /addin/update-status</c> bis zu einem Endzustand oder
        /// dem Gesamt-Budget. <b>Ein Verbindungsabbruch waehrend des Updates
        /// ist ERWARTET</b> (der Container startet neu) und wird als „laeuft
        /// noch" behandelt — kein Fehlerzustand, kein Abbruch. Erst wenn ueber
        /// das gesamte Budget hinweg keine einzige Antwort mehr eintrifft,
        /// wird auf „Zustand unklar" umgeschaltet (D-5).
        /// </summary>
        private async void PollUpdateAsync()
        {
            _pollCts?.Cancel();
            _pollCts?.Dispose();
            _pollCts = new CancellationTokenSource();
            var ct = _pollCts.Token;

            var budget = TimeSpan.FromMinutes(20);
            var lastSuccessAt = DateTime.UtcNow;

            try
            {
                while (!ct.IsCancellationRequested)
                {
                    UpdateProgress progress = null;
                    try
                    {
                        using (var client = new ChatClient(_settings))
                        using (var innerCts = CancellationTokenSource.CreateLinkedTokenSource(ct))
                        {
                            innerCts.CancelAfter(TimeSpan.FromSeconds(8));
                            progress = await client.GetUpdateProgressAsync(innerCts.Token);
                        }
                        lastSuccessAt = DateTime.UtcNow;
                    }
                    catch (Exception ex) when (!(ex is OperationCanceledException && ct.IsCancellationRequested))
                    {
                        MarshalToUi(() => Apply(UpdateWindowState.ServerRestarting()));
                    }

                    if (ct.IsCancellationRequested)
                    {
                        return;
                    }

                    if (progress != null && progress.IsTerminal)
                    {
                        bool erfolg = progress.State == "success";
                        string ziel = string.IsNullOrEmpty(progress.VersionTo)
                            ? _targetVersion
                            : progress.VersionTo;
                        if (erfolg)
                        {
                            // Der Server ist fertig, das Add-in noch nicht —
                            // Fenster bleibt zu, bis auch das geklaert ist.
                            MarshalToUi(() => ApplyZwischentext(
                                "Server aktualisiert",
                                "Add-in wird geprüft und vorbereitet …"));
                            await BeginAddinSelfUpdateAsync(ziel, progress);
                        }
                        else
                        {
                            MarshalToUi(() => Apply(UpdateWindowState.ForProgress(progress)));
                        }
                        return;
                    }

                    if (progress != null)
                    {
                        var snapshot = progress;
                        MarshalToUi(() =>
                        {
                            Apply(UpdateWindowState.ForProgress(snapshot));
                            ApplyProgressValue(snapshot.Progress);
                        });
                    }

                    if (DateTime.UtcNow - lastSuccessAt > budget)
                    {
                        MarshalToUi(() => Apply(UpdateWindowState.Unclear()));
                        return;
                    }

                    await Task.Delay(3000, ct);
                }
            }
            catch (OperationCanceledException)
            {
                // Fenster wurde geschlossen — sauberer Abbruch.
            }
            catch (Exception ex)
            {
                MarshalToUi(() => Apply(UpdateWindowState.StartFailed(
                    "Fortschritt konnte nicht ermittelt werden: " + ex.Message)));
            }
        }

        /// <summary>
        /// Nach erfolgreichem Server-Update: laedt das neue Add-in-Publish,
        /// stagt es und startet einen VON OUTLOOK GELOESTEN Helfer, der die
        /// Installation erst nach dem Beenden von Outlook durchfuehrt
        /// (260728-m8g, D-6). <b>Ein Fehlschlag hier kippt NICHT das
        /// Gesamtergebnis</b> — der Server IST aktualisiert; das Fenster sagt
        /// dann, dass der Installer manuell aus der WebUI zu laden ist.
        ///
        /// Der frueher hier gezeigte zusaetzliche <c>MessageBox</c> entfaellt:
        /// die Aussage steht jetzt IM Fenster. Ein Hinweisdialog ueber einem
        /// Fenster, das ohnehin nur „OK" anbietet, war genau die Doppelung,
        /// die der Betreiber beanstandet hat.
        /// </summary>
        private async Task BeginAddinSelfUpdateAsync(string targetVersion, UpdateProgress progress)
        {
            try
            {
                string tempZip = Path.Combine(
                    Path.GetTempPath(), "vizpatch-addin-" + targetVersion + ".zip");
                using (var client = new ChatClient(_settings))
                using (var cts = new CancellationTokenSource(TimeSpan.FromSeconds(120)))
                {
                    await client.DownloadAddinInstallerAsync(tempZip, cts.Token);
                }

                string helperPath = AddinSelfUpdate.Stage(tempZip, targetVersion);

                string installedAssembly = Assembly.GetExecutingAssembly().Location;
                string stagedPublishDir = AddinSelfUpdate.StagedPublishDir();
                if (!AddinSelfUpdate.BundleDiffersFromInstalled(installedAssembly, stagedPublishDir))
                {
                    AddinSelfUpdate.ClearStaging();
                    MarshalToUi(() => ApplyFertig(
                        "Update abgeschlossen",
                        Meldung(progress, targetVersion)
                        + Environment.NewLine + Environment.NewLine
                        + "Das Add-in war bereits aktuell — kein Neustart von Outlook nötig."));
                    return;
                }

                var startInfo = new ProcessStartInfo
                {
                    FileName = "cmd.exe",
                    Arguments = "/c start \"Vizpatch-Update\" /min \"" + helperPath + "\"",
                    UseShellExecute = true,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                };
                Process.Start(startInfo);

                MarshalToUi(() => ApplyFertig(
                    "Update abgeschlossen",
                    Meldung(progress, targetVersion)
                    + Environment.NewLine + Environment.NewLine
                    + "Das Add-in wird beim nächsten Beenden von Outlook automatisch "
                    + "aktualisiert — bitte Outlook danach einmal neu starten."));
            }
            catch (Exception ex)
            {
                MarshalToUi(() => ApplyFertig(
                    "Server aktualisiert — Add-in-Update offen",
                    Meldung(progress, targetVersion)
                    + Environment.NewLine + Environment.NewLine
                    + "Das Add-in-Update konnte nicht vorbereitet werden (" + ex.Message
                    + "). Bitte den Installer manuell aus der WebUI laden "
                    + "(Button „Add-in herunterladen“)."));
            }
        }

        private static string Meldung(UpdateProgress progress, string targetVersion)
        {
            if (progress != null && !string.IsNullOrEmpty(progress.Message))
            {
                return progress.Message;
            }
            return "Der Server läuft jetzt auf "
                + (string.IsNullOrEmpty(targetVersion) ? "der neuen Version" : targetVersion)
                + ".";
        }

        /// <summary>
        /// Endzustand setzen, ohne dafuer einen eigenen
        /// <see cref="UpdateWindowState"/>-Fall zu brauchen: die
        /// Add-in-Nachbereitung erzeugt zusammengesetzte Texte, deren
        /// Bausteine (Servermeldung + Add-in-Hinweis) erst hier vorliegen.
        /// </summary>
        private void ApplyFertig(string headline, string detail)
        {
            _headline.Text = headline;
            _detail.Text = detail;
            _detail.ForeColor = MutedFg;
            _barHost.Visible = false;
            _finished = true;
            _okButton.Enabled = true;
            _okButton.Focus();
            // Die Endmeldung ist die laengste, die hier je steht — genau sie
            // wurde abgeschnitten.
            DetailHoeheSicherstellen();
        }

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
                }
                catch (InvalidOperationException)
                {
                }
            }
            else
            {
                action();
            }
        }
    }
}
