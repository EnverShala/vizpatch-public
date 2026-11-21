using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace VizpatchAddin
{
    /// <summary>
    /// Setzt das Vizpatch-Emblem als Fenster-Symbol (Titelleiste, Alt-Tab).
    ///
    /// <b>Warum das Emblem und nicht das Logo:</b> in der Titelleiste stehen
    /// 16x16 Pixel zur Verfuegung. Der Schriftzug des Voll-Logos waere dort
    /// unleserlicher Matsch — gebraucht wird das reine Symbol
    /// (Betreiber-Wunsch 2026-08-27: „das Vizpatch Logo als Icon, ohne
    /// Schriftzug"). Vorher zeigten die Dialoge das WinForms-Standardsymbol.
    ///
    /// <b>Warum das Icon zwischengespeichert wird:</b> <c>GetHicon()</c> legt
    /// ein GDI-Handle an, das der Aufrufer freigeben muss. Ein Icon pro
    /// Dialog-Oeffnung waere ein Leck in einem Prozess, der wochenlang laeuft
    /// (Outlook). Es wird deshalb genau EINMAL erzeugt und fuer die Lebensdauer
    /// des Add-ins behalten — konstanter Verbrauch statt wachsendem.
    ///
    /// Jeder Fehler wird geschluckt: ein fehlendes Symbol ist ein
    /// Schoenheitsfehler, ein Absturz beim Oeffnen der Einstellungen waere ein
    /// Ausfall.
    /// </summary>
    internal static class FensterIcon
    {
        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool DestroyIcon(IntPtr hIcon);

        private const string EmblemResource = "vizpatch_emblem.png";

        private static readonly object Sperre = new object();
        private static Icon _icon;
        private static bool _versucht;

        /// <summary>Setzt das Emblem als Symbol des Fensters. Tut nichts, wenn
        /// die Ressource fehlt oder nicht lesbar ist.</summary>
        internal static void Anwenden(Form form)
        {
            if (form == null)
            {
                return;
            }

            try
            {
                Icon icon = Holen();
                if (icon != null)
                {
                    form.Icon = icon;
                    form.ShowIcon = true;
                }
            }
            catch
            {
                // Symbol ist Kosmetik — niemals ein Grund, den Dialog nicht zu zeigen.
            }
        }

        private static Icon Holen()
        {
            lock (Sperre)
            {
                if (_versucht)
                {
                    return _icon;
                }
                _versucht = true;

                IntPtr handle = IntPtr.Zero;
                try
                {
                    using (Image quelle = LadeEmblem())
                    {
                        if (quelle == null)
                        {
                            return null;
                        }

                        // Auf 32x32 rechnen: Windows skaliert daraus die 16x16 der
                        // Titelleiste sauberer herunter als aus der Originalgroesse.
                        using (var bmp = new Bitmap(32, 32))
                        {
                            using (Graphics g = Graphics.FromImage(bmp))
                            {
                                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                                g.SmoothingMode = SmoothingMode.HighQuality;
                                g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                                g.Clear(Color.Transparent);
                                g.DrawImage(quelle, new Rectangle(0, 0, 32, 32));
                            }

                            handle = bmp.GetHicon();
                            // Kopie ziehen und das Handle sofort wieder freigeben:
                            // `Icon.FromHandle` besitzt das Handle NICHT.
                            using (Icon temp = Icon.FromHandle(handle))
                            {
                                _icon = (Icon)temp.Clone();
                            }
                        }
                    }
                }
                catch
                {
                    _icon = null;
                }
                finally
                {
                    if (handle != IntPtr.Zero)
                    {
                        DestroyIcon(handle);
                    }
                }

                return _icon;
            }
        }

        private static Image LadeEmblem()
        {
            Assembly asm = Assembly.GetExecutingAssembly();
            foreach (string name in asm.GetManifestResourceNames())
            {
                if (!name.EndsWith(EmblemResource, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                using (var stream = asm.GetManifestResourceStream(name))
                {
                    if (stream == null)
                    {
                        return null;
                    }
                    using (var img = Image.FromStream(stream))
                    {
                        return new Bitmap(img);
                    }
                }
            }
            return null;
        }
    }
}
