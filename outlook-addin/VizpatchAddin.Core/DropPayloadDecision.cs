using System;
using System.Collections.Generic;

namespace VizpatchAddin.Core
{
    /// <summary>Art der Nutzlast eines Drag&amp;Drop-Vorgangs.</summary>
    public enum DropPayloadKind
    {
        /// <summary>Kein unterstuetztes Dateiformat im Drop (z. B. reiner Text).</summary>
        None,
        /// <summary>Explorer-Datei(en) ueber <c>DataFormats.FileDrop</c>.</summary>
        FileDrop,
        /// <summary>Outlook-Anhang/Mail ueber FILEGROUPDESCRIPTOR(W) + FileContents.</summary>
        FileContents,
    }

    /// <summary>Ergebnis von <see cref="DropPayloadDecision.Decide"/>.</summary>
    public struct DropPayloadPlan
    {
        public DropPayloadKind Kind;

        /// <summary>Nur bei <see cref="DropPayloadKind.FileContents"/> relevant:
        /// <c>true</c> = FILEGROUPDESCRIPTORW (Unicode, 592 Byte je Eintrag),
        /// <c>false</c> = FILEGROUPDESCRIPTOR (ANSI, 332 Byte je Eintrag).</summary>
        public bool DescriptorIsUnicode;

        /// <summary>Der Clipboard-Formatname des Descriptors ("FileGroupDescriptorW"
        /// bzw. "FileGroupDescriptor"), mit dem der Aufrufer <c>IDataObject.GetData</c>
        /// aufruft. Nur bei <see cref="DropPayloadKind.FileContents"/> gesetzt.</summary>
        public string DescriptorFormat;
    }

    /// <summary>
    /// Reine, COM-freie Entscheidung, WELCHE Windows-Zwischenablage-Formate ein
    /// Drop bedient (DND-01/02, 260731-u3m) — ohne diese Klasse waere die
    /// Formatwahl nur ueber echtes Outlook/COM (in <c>ChatView</c>/
    /// <c>OutlookDropExtractor</c>) und damit auf diesem Host UNTESTBAR.
    ///
    /// Warum FileContents VOR FileDrop geprueft wird: ein aus Outlook gezogenes
    /// Item bietet teils BEIDE Formate an, aber der FileDrop-Pfad verweist auf
    /// eine erst beim Drop zu materialisierende Datei, die zum Entscheidungs-
    /// zeitpunkt noch gar nicht existiert. Der Descriptor/Contents-Pfad ist der
    /// verlaessliche und wird deshalb bevorzugt.
    /// </summary>
    public static class DropPayloadDecision
    {
        private const string FormatFileDrop = "FileDrop";
        private const string FormatDescriptorW = "FileGroupDescriptorW";
        private const string FormatDescriptorAnsi = "FileGroupDescriptor";
        private const string FormatFileContents = "FileContents";

        public static DropPayloadPlan Decide(IEnumerable<string> availableFormats)
        {
            if (availableFormats == null)
            {
                return new DropPayloadPlan { Kind = DropPayloadKind.None };
            }

            var formats = new HashSet<string>(availableFormats, StringComparer.OrdinalIgnoreCase);
            if (formats.Count == 0)
            {
                return new DropPayloadPlan { Kind = DropPayloadKind.None };
            }

            if (formats.Contains(FormatDescriptorW) && formats.Contains(FormatFileContents))
            {
                return new DropPayloadPlan
                {
                    Kind = DropPayloadKind.FileContents,
                    DescriptorIsUnicode = true,
                    DescriptorFormat = FormatDescriptorW,
                };
            }

            if (formats.Contains(FormatDescriptorAnsi) && formats.Contains(FormatFileContents))
            {
                return new DropPayloadPlan
                {
                    Kind = DropPayloadKind.FileContents,
                    DescriptorIsUnicode = false,
                    DescriptorFormat = FormatDescriptorAnsi,
                };
            }

            if (formats.Contains(FormatFileDrop))
            {
                return new DropPayloadPlan { Kind = DropPayloadKind.FileDrop };
            }

            return new DropPayloadPlan { Kind = DropPayloadKind.None };
        }
    }
}
