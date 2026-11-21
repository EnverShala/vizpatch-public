using System;
using System.IO;
using System.Text;
using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>Tests fuer <see cref="TempAttachmentFile"/> (DND-01/02, 260731-u3m,
    /// T-u3m-01). Geschriebene Dateien werden am Testende ueber
    /// <see cref="TempAttachmentFile.CleanupQuietly"/> wieder entfernt.</summary>
    public class TempAttachmentFileTests
    {
        [Fact]
        public void SanitizeFileName_Traversal_EntferntPfadanteile()
        {
            Assert.Equal("evil.txt", TempAttachmentFile.SanitizeFileName("..\\..\\evil.txt"));
        }

        [Fact]
        public void SanitizeFileName_Umlaute_AsciiNormalisiert()
        {
            Assert.Equal("Angebot Mueller.pdf", TempAttachmentFile.SanitizeFileName("Angebot Müller.pdf"));
        }

        [Fact]
        public void SanitizeFileName_VerboteneZeichen_Entfernt()
        {
            string result = TempAttachmentFile.SanitizeFileName("a/b:c*?.txt");
            foreach (char c in Path.GetInvalidFileNameChars())
            {
                Assert.DoesNotContain(c.ToString(), result);
            }
            Assert.DoesNotContain(":", result);
            Assert.DoesNotContain("*", result);
            Assert.DoesNotContain("?", result);
        }

        [Theory]
        [InlineData(null)]
        [InlineData("")]
        [InlineData("   ")]
        public void SanitizeFileName_LeerOderNull_LiefertAnhang(string input)
        {
            Assert.Equal("anhang", TempAttachmentFile.SanitizeFileName(input));
        }

        [Fact]
        public void SanitizeFileName_SehrLangerName_GekuerztMitEndung()
        {
            string longName = new string('x', 300) + ".pdf";
            string result = TempAttachmentFile.SanitizeFileName(longName);
            Assert.True(result.Length <= 100);
            Assert.EndsWith(".pdf", result);
        }

        [Fact]
        public void Write_LegtDateiUnterhalbTempPathAn_KopiertBytesKorrekt()
        {
            byte[] payload = Encoding.UTF8.GetBytes("Testinhalt");
            TempAttachment result;
            using (var stream = new MemoryStream(payload))
            {
                result = TempAttachmentFile.Write(stream, "Bericht.pdf");
            }

            try
            {
                Assert.True(File.Exists(result.Pfad));
                Assert.StartsWith(Path.GetTempPath().TrimEnd('\\'), result.Pfad, StringComparison.OrdinalIgnoreCase);
                Assert.Equal(payload.Length, result.Groesse);
                Assert.Equal("Bericht.pdf", result.Dateiname);
                Assert.True(result.IstTemporaer);
                Assert.Equal(payload, File.ReadAllBytes(result.Pfad));
            }
            finally
            {
                TempAttachmentFile.CleanupQuietly(result.Pfad);
            }
        }

        [Fact]
        public void Write_ZweimalMitDemselbenNamen_KollidiertNicht()
        {
            TempAttachment first;
            TempAttachment second;
            using (var s1 = new MemoryStream(Encoding.UTF8.GetBytes("erste")))
            {
                first = TempAttachmentFile.Write(s1, "gleich.txt");
            }
            using (var s2 = new MemoryStream(Encoding.UTF8.GetBytes("zweite")))
            {
                second = TempAttachmentFile.Write(s2, "gleich.txt");
            }

            try
            {
                Assert.NotEqual(first.Pfad, second.Pfad);
                Assert.True(File.Exists(first.Pfad));
                Assert.True(File.Exists(second.Pfad));
            }
            finally
            {
                TempAttachmentFile.CleanupQuietly(first.Pfad);
                TempAttachmentFile.CleanupQuietly(second.Pfad);
            }
        }

        [Fact]
        public void CleanupQuietly_LoeschtDatei()
        {
            TempAttachment result;
            using (var stream = new MemoryStream(Encoding.UTF8.GetBytes("x")))
            {
                result = TempAttachmentFile.Write(stream, "weg.txt");
            }
            Assert.True(File.Exists(result.Pfad));
            TempAttachmentFile.CleanupQuietly(result.Pfad);
            Assert.False(File.Exists(result.Pfad));
        }

        [Fact]
        public void CleanupQuietly_NichtExistierenderPfad_WirftNicht()
        {
            TempAttachmentFile.CleanupQuietly(Path.Combine(Path.GetTempPath(), Guid.NewGuid() + ".txt"));
        }

        [Fact]
        public void CleanupQuietly_Null_WirftNicht()
        {
            TempAttachmentFile.CleanupQuietly(null);
        }

        // --- Quick 260801-2d4 (FIX-B1): ReserveTempPath — Zielpfad OHNE die Datei
        // anzulegen (StgCreateDocfile braucht einen FREIEN Pfad, den es SELBST
        // per STGM_CREATE anlegt) ------------------------------------------------

        [Fact]
        public void ReserveTempPath_LiegtUnterTempPath_VerzeichnisExistiertDateiNicht()
        {
            string path = TempAttachmentFile.ReserveTempPath("mail.msg", out string dateiname);

            try
            {
                Assert.StartsWith(Path.GetTempPath().TrimEnd('\\'), path, StringComparison.OrdinalIgnoreCase);
                Assert.True(Directory.Exists(Path.GetDirectoryName(path)));
                Assert.False(File.Exists(path));
                Assert.Equal("mail.msg", dateiname);
            }
            finally
            {
                TempAttachmentFile.CleanupQuietly(path);
            }
        }

        [Fact]
        public void ReserveTempPath_Traversal_LiefertSanitizedDateinameOhnePfadanteil()
        {
            string path = TempAttachmentFile.ReserveTempPath("..\\..\\evil.msg", out string dateiname);

            try
            {
                Assert.Equal("evil.msg", dateiname);
                Assert.DoesNotContain("..", Path.GetFileName(path));
            }
            finally
            {
                TempAttachmentFile.CleanupQuietly(path);
            }
        }

        [Fact]
        public void ReserveTempPath_ZweimalMitDemselbenNamen_LiefertVerschiedenePfade()
        {
            string first = TempAttachmentFile.ReserveTempPath("gleich.msg", out _);
            string second = TempAttachmentFile.ReserveTempPath("gleich.msg", out _);

            try
            {
                Assert.NotEqual(first, second);
            }
            finally
            {
                TempAttachmentFile.CleanupQuietly(first);
                TempAttachmentFile.CleanupQuietly(second);
            }
        }
    }
}
