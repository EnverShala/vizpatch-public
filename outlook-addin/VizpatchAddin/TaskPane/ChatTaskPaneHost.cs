using System.Windows.Forms;

namespace VizpatchAddin.TaskPane
{
    /// <summary>
    /// WinForms-Host der Custom Task Pane (D-83: reines WinForms, kein WPF/ElementHost).
    /// <see cref="Microsoft.Office.Tools.CustomTaskPaneCollection.Add(UserControl, string)"/>
    /// erwartet einen <see cref="UserControl"/>; dieser Host fuellt die Pane und
    /// nimmt den eigentlichen Chat-Bereich (<c>ChatView</c>, Plan 08-02 Task 2) auf.
    ///
    /// Absichtlich schlank. Zwei Effekte am oberen/seitlichen Pane-Rand wurden am
    /// 2026-07-28 ausgiebig untersucht und sind NICHT aus dem Add-in loesbar — bitte
    /// nicht erneut versuchen:
    ///
    /// 1. Duenner Strich an der Pane-Oberkante. Unser Fenster sitzt buendig in
    ///    <c>CMMOcxHost</c>, DER aber 1 px unter <c>CMMOcxHostChildWindowMixedMode</c>
    ///    (gemessen: T=-1571 gegen T=-1572). Diese Zeile gehoert also einem Outlook-
    ///    Fenster. Erfolglos blieben: <c>RDW_INVALIDATE|RDW_ERASE|RDW_ALLCHILDREN</c>
    ///    auf drei Vorfahren-Ebenen (die Office-Container haben keinen Hintergrund-
    ///    Brush), das eigene Fenster per <c>SetWindowPos</c> 1 px hochziehen (im
    ///    direkten Parent gibt es gar keinen Spalt), und die Zeile im Geraetekontext
    ///    des Vorfahren selbst zu fuellen (griff nachweislich — Log
    ///    "gap=1@level1-gefuellt" — blieb aber unsichtbar, weil Outlook danach erneut
    ///    darueber zeichnet).
    ///
    /// 2. Flackern der Pane-RAENDER beim Splitter-Ziehen. Erfolglos blieben:
    ///    <c>WS_EX_COMPOSITED</c> auf der ChatView, dasselbe auf diesem Host, und
    ///    Zeichnen/Layout waehrend der Drag-Serie stilllegen (<c>WM_SETREDRAW</c> +
    ///    <c>SuspendLayout</c> + Debounce-Repaint). Ursache liegt in Outlooks eigenem
    ///    Splitter-Redraw.
    ///
    /// Geblieben sind nur die messbaren Sparmassnahmen in der ChatView (Logo-Cache,
    /// Breiten-Cache fuers Blasen-Layout, Composer-Hoehe nur bei Aenderung,
    /// BufferedFlowPanel): ~17,7 -> ~10,4 ms pro Resize-Schritt.
    /// </summary>
    public class ChatTaskPaneHost : UserControl
    {
        private ChatView _chatView;

        public ChatTaskPaneHost()
        {
            this.DoubleBuffered = true;
            InitializeLayout();
        }

        /// <summary>
        /// Reicht das Sichtbarwerden der Task-Pane an die ChatView durch, damit die
        /// Begruessung erst dann getippt wird, wenn der Betreiber sie auch sieht.
        /// Wird von <c>ThisAddIn</c> am <c>CustomTaskPane.VisibleChanged</c> gerufen —
        /// das WinForms-<c>Visible</c> dieses Hosts taugt dafuer NICHT: die Pane wird
        /// von Outlook ueber ihr Container-Fenster ein-/ausgeblendet, das Control hier
        /// bleibt dabei durchgehend "sichtbar".
        /// </summary>
        public void NotifyPaneShown()
        {
            if (_chatView != null)
            {
                _chatView.NotifyPaneShown();
            }
        }

        private void InitializeLayout()
        {
            this.Dock = DockStyle.Fill;
            this.Padding = new Padding(0);
            _chatView = new ChatView { Dock = DockStyle.Fill };
            // Host-Hintergrund = Pane-Farbe der ChatView, NICHT SystemColors.Window:
            // das ist immer Weiss und wuerde im Dark Mode als heller Rand aufblitzen.
            // Beim Theme-Wechsel mitziehen.
            this.BackColor = _chatView.BackColor;
            _chatView.BackColorChanged += (s, e) => this.BackColor = _chatView.BackColor;
            this.Controls.Add(_chatView);
        }
    }
}
