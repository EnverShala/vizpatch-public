using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using Office = Microsoft.Office.Core;

namespace VizpatchAddin.Ribbon
{
    /// <summary>
    /// Ribbon (XML-Ansatz, RESEARCH.md Pattern 1): ein einzelner Toggle-Button
    /// "Vizpatch-Chat", der die <see cref="ThisAddIn.ChatPane"/> ein-/ausblendet.
    /// Der XML-Ansatz kommt ohne den Ribbon-Designer/Design-Time-Codegen aus und
    /// ist damit per MSBuild-Kommandozeile baubar.
    ///
    /// Bidirektionale Synchronisation:
    ///  - Klick auf den Toggle -> <c>OnToggleChat</c> setzt <c>ChatPane.Visible</c>.
    ///  - Schliesst der Nutzer die Pane ueber ihr "X", ruft ThisAddIn
    ///    <see cref="InvalidateToggle"/> -> Outlook fragt <c>GetChatPressed</c> neu
    ///    ab und der Button-Zustand folgt der tatsaechlichen Sichtbarkeit.
    /// </summary>
    [ComVisible(true)]
    public class ChatRibbon : Office.IRibbonExtensibility
    {
        private const string ToggleControlId = "vizpatchChatToggle";

        /// <summary>`image="..."` des Toggle-Buttons in ChatRibbon.xml.</summary>
        private const string LogoImageId = "vizpatchLogo";

        private const string EmblemResource = "VizpatchAddin.Resources.vizpatch_emblem.png";

        /// <summary>
        /// Kantenlaenge eines `size="large"`-Ribbon-Icons bei 100 % Skalierung.
        /// Outlook skaliert das gelieferte Bild auf diese Groesse — deshalb wird
        /// es unten DPI-passend gerendert statt hochskaliert (Hochskalieren war
        /// der Grund, warum das Logo in der Pane einmal unscharf war).
        /// </summary>
        private const int BaseIconEdge = 32;

        private Office.IRibbonUI _ribbon;

        /// <summary>
        /// Haelt das gerenderte Icon am Leben. Das von
        /// <c>GetIPictureDispFromPicture</c> gelieferte COM-Objekt umschliesst
        /// das <see cref="Bitmap"/>, ohne dessen Lebensdauer zu verlaengern —
        /// wird das Bitmap eingesammelt, zeichnet Outlook einen leeren Button.
        /// </summary>
        private static Bitmap _logoIcon;

        /// <summary>Liefert das Ribbon-XML (eingebettete Ressource) an Outlook.</summary>
        public string GetCustomUI(string ribbonID)
        {
            return GetResourceText("VizpatchAddin.Ribbon.ChatRibbon.xml");
        }

        /// <summary>onLoad-Callback: haelt die Ribbon-Referenz fuer Invalidation.</summary>
        public void OnRibbonLoad(Office.IRibbonUI ribbonUI)
        {
            _ribbon = ribbonUI;
        }

        /// <summary>toggleButton onAction: steuert die Pane-Sichtbarkeit.</summary>
        public void OnToggleChat(Office.IRibbonControl control, bool pressed)
        {
            var pane = Globals.ThisAddIn != null ? Globals.ThisAddIn.ChatPane : null;
            if (pane != null)
            {
                pane.Visible = pressed;
            }
        }

        /// <summary>toggleButton getPressed: spiegelt die tatsaechliche Sichtbarkeit.</summary>
        public bool GetChatPressed(Office.IRibbonControl control)
        {
            var pane = Globals.ThisAddIn != null ? Globals.ThisAddIn.ChatPane : null;
            return pane != null && pane.Visible;
        }

        /// <summary>
        /// Erzwingt eine Neuabfrage von <see cref="GetChatPressed"/> — von
        /// ThisAddIn beim <c>VisibleChanged</c>-Event der Pane aufgerufen, damit
        /// der Toggle-Zustand der Pane-Sichtbarkeit folgt.
        /// </summary>
        public void InvalidateToggle()
        {
            if (_ribbon != null)
            {
                _ribbon.InvalidateControl(ToggleControlId);
            }
        }

        /// <summary>
        /// customUI-`loadImage`: liefert das Vizpatch-Emblem als Button-Icon.
        /// Zusammen mit `showLabel="false"` steht "Vizpatch" dann nur noch
        /// einmal im Ribbon — als Gruppenname unter dem Button, wie bei den
        /// Outlook-eigenen Gruppen ("Sprache", "Senden/Empfangen").
        ///
        /// Rueckgabetyp ist bewusst <c>object</c>: der Wert ist ein
        /// <c>stdole.IPictureDisp</c>-COM-Objekt, aber so braucht diese Datei
        /// keine stdole-Referenz (die im csproj nicht steht).
        ///
        /// Faellt das Rendern aus, wird <c>null</c> zurueckgegeben — der Button
        /// bleibt dann ohne Bild bedienbar. Eine Exception aus einem
        /// Ribbon-Callback verschluckt Outlook still und deaktiviert im
        /// schlimmsten Fall das ganze Add-in.
        /// </summary>
        public object OnLoadImage(string imageId)
        {
            if (!string.Equals(imageId, LogoImageId, StringComparison.Ordinal))
            {
                return null;
            }
            try
            {
                if (_logoIcon == null)
                {
                    _logoIcon = RenderEmblemIcon(IconEdgeForCurrentDpi());
                }
                return PictureConverter.ToPictureDisp(_logoIcon);
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>
        /// Zielkantenlaenge in Pixeln fuer die aktuelle Bildschirmskalierung.
        /// Liest nur die DPI zum Rastern — es wird KEINE DPI-Awareness
        /// veraendert (der Per-Monitor-Versuch hat das WinForms-Layout des
        /// Add-ins zerlegt und wurde zurueckgenommen).
        /// </summary>
        private static int IconEdgeForCurrentDpi()
        {
            try
            {
                using (var g = Graphics.FromHwnd(IntPtr.Zero))
                {
                    var kante = (int)Math.Round(BaseIconEdge * (g.DpiX / 96f));
                    return Math.Max(BaseIconEdge, Math.Min(kante, 256));
                }
            }
            catch (Exception)
            {
                return BaseIconEdge;
            }
        }

        /// <summary>
        /// Rendert das Emblem zentriert auf ein quadratisches, transparentes
        /// Canvas. Die Quelldatei ist 245x278 (hochkant) — ohne das Quadrat
        /// wuerde Outlook das Bild auf ein quadratisches Icon verzerren.
        /// </summary>
        private static Bitmap RenderEmblemIcon(int kante)
        {
            using (var stream = Assembly.GetExecutingAssembly()
                       .GetManifestResourceStream(EmblemResource))
            {
                if (stream == null)
                {
                    throw new InvalidOperationException(
                        "Emblem-Ressource nicht gefunden: " + EmblemResource);
                }
                using (var quelle = new Bitmap(stream))
                {
                    var faktor = Math.Min(
                        kante / (float)quelle.Width, kante / (float)quelle.Height);
                    var breite = Math.Max(1, (int)Math.Round(quelle.Width * faktor));
                    var hoehe = Math.Max(1, (int)Math.Round(quelle.Height * faktor));

                    var ziel = new Bitmap(kante, kante, PixelFormat.Format32bppArgb);
                    using (var g = Graphics.FromImage(ziel))
                    {
                        g.Clear(Color.Transparent);
                        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                        g.SmoothingMode = SmoothingMode.HighQuality;
                        g.DrawImage(
                            quelle,
                            new Rectangle((kante - breite) / 2, (kante - hoehe) / 2, breite, hoehe));
                    }
                    return ziel;
                }
            }
        }

        /// <summary>
        /// Bruecke <see cref="Image"/> -> <c>IPictureDisp</c>. Der Umweg ueber
        /// <see cref="AxHost"/> ist der einzige Weg im Framework:
        /// <c>GetIPictureDispFromPicture</c> ist dort <c>protected static</c>,
        /// also braucht es eine Ableitung, um daran zu kommen.
        /// </summary>
        private sealed class PictureConverter : AxHost
        {
            private PictureConverter() : base(string.Empty)
            {
            }

            public static object ToPictureDisp(Image image)
            {
                return GetIPictureDispFromPicture(image);
            }
        }

        private static string GetResourceText(string resourceName)
        {
            var asm = Assembly.GetExecutingAssembly();
            using (var stream = asm.GetManifestResourceStream(resourceName))
            {
                if (stream == null)
                {
                    throw new InvalidOperationException(
                        "Ribbon-Ressource nicht gefunden: " + resourceName);
                }
                using (var reader = new StreamReader(stream))
                {
                    return reader.ReadToEnd();
                }
            }
        }
    }
}
