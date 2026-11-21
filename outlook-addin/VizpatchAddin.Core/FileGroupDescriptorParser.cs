using System;
using System.Collections.Generic;
using System.Text;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// Reines Byte-Parsing des Windows-Zwischenablage-Formats
    /// FILEGROUPDESCRIPTOR(W) (DND-02, 260731-u3m). Layout laut Shell-Doku:
    /// <c>UINT cItems</c> (4 Byte) gefolgt von <c>cItems</c> Eintraegen. Ein
    /// FILEDESCRIPTORW-Eintrag ist 592 Byte lang, ein FILEDESCRIPTORA-Eintrag
    /// 332 Byte; <c>cFileName</c> liegt bei Offset 72 (dwFlags 4 + CLSID 16 +
    /// SIZEL 8 + POINTL 8 + dwFileAttributes 4 + 3x FILETIME 24 +
    /// nFileSizeHigh 4 + nFileSizeLow 4 = 72) und ist WCHAR[260]/CHAR[260],
    /// NUL-terminiert.
    ///
    /// ALLES hier ist defensiv statt per <c>Marshal.PtrToStructure</c>: der
    /// Puffer kommt aus einem FREMDEN Prozess (Outlook) ueber die
    /// Zwischenablage — ein unerwarteter/abgeschnittener Puffer darf niemals
    /// eine Exception im Outlook-UI-Thread ausloesen.
    /// </summary>
    public static class FileGroupDescriptorParser
    {
        public const int UnicodeDescriptorSize = 592;
        public const int AnsiDescriptorSize = 332;
        private const int FileNameOffset = 72;

        // Ein groesserer Wert als dieser ist Datenmuell oder ein Angriffsversuch,
        // kein realistischer Drop (T-u3m-02).
        private const int MaxItems = 256;

        public static IList<string> ParseFileNames(byte[] raw, bool unicode)
        {
            var result = new List<string>();
            if (raw == null || raw.Length < 4)
            {
                return result;
            }

            int count = BitConverter.ToInt32(raw, 0);
            if (count <= 0)
            {
                return result;
            }
            if (count > MaxItems)
            {
                count = MaxItems;
            }

            int entrySize = unicode ? UnicodeDescriptorSize : AnsiDescriptorSize;
            Encoding encoding = unicode ? Encoding.Unicode : Encoding.Default;
            int nameBytes = entrySize - FileNameOffset;

            for (int i = 0; i < count; i++)
            {
                int entryOffset = 4 + i * entrySize;
                if (entryOffset + entrySize > raw.Length)
                {
                    // Abgeschnittener Puffer: die vorhandenen Namen behalten,
                    // NICHT werfen.
                    break;
                }

                int nameOffset = entryOffset + FileNameOffset;
                string name = encoding.GetString(raw, nameOffset, nameBytes);
                int nul = name.IndexOf('\0');
                if (nul >= 0)
                {
                    name = name.Substring(0, nul);
                }
                name = name.Trim();
                if (name.Length > 0)
                {
                    result.Add(name);
                }
            }

            return result;
        }
    }
}
