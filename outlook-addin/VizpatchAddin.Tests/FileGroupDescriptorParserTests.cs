using System;
using System.Text;
using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>
    /// Tests fuer <see cref="FileGroupDescriptorParser.ParseFileNames"/> (DND-02,
    /// 260731-u3m). <see cref="BuildDescriptor"/> baut den Puffer byteweise nach
    /// dem in der Klasse dokumentierten Layout — die Tests sind damit die exakte
    /// Gegenprobe zum Byte-Layout, nicht nur eine Behauptung.
    /// </summary>
    public class FileGroupDescriptorParserTests
    {
        [Fact]
        public void EinName_LiefertGenauDiesenNamen()
        {
            byte[] buf = BuildDescriptor(true, "bericht.pdf");
            var names = FileGroupDescriptorParser.ParseFileNames(buf, true);
            Assert.Equal(new[] { "bericht.pdf" }, names);
        }

        [Fact]
        public void UnicodeName_UmlauteKorrekt()
        {
            byte[] buf = BuildDescriptor(true, "Angebot Müller.pdf");
            var names = FileGroupDescriptorParser.ParseFileNames(buf, true);
            Assert.Equal(new[] { "Angebot Müller.pdf" }, names);
        }

        [Fact]
        public void DreiNamen_AlleInReihenfolge()
        {
            byte[] buf = BuildDescriptor(true, "a.txt", "b.pdf", "c.docx");
            var names = FileGroupDescriptorParser.ParseFileNames(buf, true);
            Assert.Equal(new[] { "a.txt", "b.pdf", "c.docx" }, names);
        }

        [Fact]
        public void AnsiDescriptor_LiefertNamen()
        {
            byte[] buf = BuildDescriptor(false, "bericht.pdf");
            var names = FileGroupDescriptorParser.ParseFileNames(buf, false);
            Assert.Equal(new[] { "bericht.pdf" }, names);
        }

        [Fact]
        public void AbgeschnittenerPuffer_LiefertVorhandeneNamenKeinWurf()
        {
            // cItems behauptet 3, der Puffer reicht nur fuer 1 vollstaendigen Eintrag.
            byte[] full = BuildDescriptor(true, "a.txt", "b.pdf", "c.docx");
            byte[] truncated = new byte[4 + FileGroupDescriptorParser.UnicodeDescriptorSize];
            Array.Copy(full, truncated, truncated.Length);
            // cItems auf 3 setzen (BuildDescriptor setzt es passend zur echten Anzahl).
            BitConverter.GetBytes(3).CopyTo(truncated, 0);

            var names = FileGroupDescriptorParser.ParseFileNames(truncated, true);
            Assert.Equal(new[] { "a.txt" }, names);
        }

        [Fact]
        public void CItemsNull_LiefertLeereListe()
        {
            byte[] buf = BuildDescriptor(true);
            var names = FileGroupDescriptorParser.ParseFileNames(buf, true);
            Assert.Empty(names);
        }

        [Fact]
        public void PufferKuerzerAlsVierByte_LiefertLeereListeKeinWurf()
        {
            var names = FileGroupDescriptorParser.ParseFileNames(new byte[] { 1, 2 }, true);
            Assert.Empty(names);
        }

        [Fact]
        public void NullPuffer_LiefertLeereListeKeinWurf()
        {
            var names = FileGroupDescriptorParser.ParseFileNames(null, true);
            Assert.Empty(names);
        }

        [Fact]
        public void AbsurdesCItems_LiefertLeereListeKeinWurf()
        {
            byte[] buf = new byte[8];
            BitConverter.GetBytes(0x7FFFFFFF).CopyTo(buf, 0);
            var names = FileGroupDescriptorParser.ParseFileNames(buf, true);
            Assert.Empty(names);
        }

        /// <summary>Baut einen FILEGROUPDESCRIPTOR(W)-Puffer: cItems (4 Byte) +
        /// je Name ein 592- (Unicode) bzw. 332-Byte-Eintrag (ANSI), Name ab
        /// Offset 72, NUL-terminiert.</summary>
        private static byte[] BuildDescriptor(bool unicode, params string[] names)
        {
            int entrySize = unicode
                ? FileGroupDescriptorParser.UnicodeDescriptorSize
                : FileGroupDescriptorParser.AnsiDescriptorSize;
            const int fileNameOffset = 72;

            byte[] buf = new byte[4 + names.Length * entrySize];
            BitConverter.GetBytes(names.Length).CopyTo(buf, 0);

            Encoding encoding = unicode ? Encoding.Unicode : Encoding.Default;
            for (int i = 0; i < names.Length; i++)
            {
                int entryOffset = 4 + i * entrySize;
                byte[] nameBytes = encoding.GetBytes(names[i] + "\0");
                int maxLen = entrySize - fileNameOffset;
                int copyLen = Math.Min(nameBytes.Length, maxLen);
                Array.Copy(nameBytes, 0, buf, entryOffset + fileNameOffset, copyLen);
            }

            return buf;
        }
    }
}
