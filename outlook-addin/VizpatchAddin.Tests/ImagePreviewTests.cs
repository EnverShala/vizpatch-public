using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>
    /// Tests fuer <see cref="ImagePreview"/> (defensives Parsen, Bildunterschrift)
    /// und <see cref="ImagePreviewDecision"/> (Anzeige-Entscheidung, Quick 260813-wea
    /// Task 3).
    /// </summary>
    public class ImagePreviewTests
    {
        // --- FromJson: defensives Parsen ------------------------------------------

        [Theory]
        [InlineData(null)]
        [InlineData("")]
        [InlineData("kein json")]
        [InlineData("[1,2]")]
        public void FromJson_GarbageInput_ReturnsNull(string json)
        {
            Assert.Null(ImagePreview.FromJson(json));
        }

        [Fact]
        public void FromJson_AllFieldsPresent_ParsesExactly()
        {
            var vorschau = ImagePreview.FromJson(
                "{\"uid\":\"1234\",\"ordner\":\"INBOX\",\"index\":2,\"dateiname\":\"foto.jpg\"," +
                "\"typ\":\"image/jpeg\",\"groesse\":83421}");

            Assert.NotNull(vorschau);
            Assert.Equal("1234", vorschau.Uid);
            Assert.Equal("INBOX", vorschau.Ordner);
            Assert.Equal(2, vorschau.Index);
            Assert.Equal("foto.jpg", vorschau.Dateiname);
            Assert.Equal("image/jpeg", vorschau.Typ);
            Assert.Equal(83421, vorschau.Groesse);
        }

        [Fact]
        public void FromJson_MissingFields_FallsBackToEmptyOrZero()
        {
            var vorschau = ImagePreview.FromJson("{\"uid\":\"42\"}");

            Assert.NotNull(vorschau);
            Assert.Equal("42", vorschau.Uid);
            Assert.Equal("", vorschau.Ordner);
            Assert.Equal(0, vorschau.Index);
            Assert.Equal("", vorschau.Dateiname);
            Assert.Equal("", vorschau.Typ);
            Assert.Equal(0, vorschau.Groesse);
        }

        // --- AnzeigeText -----------------------------------------------------------

        [Theory]
        [InlineData(512, "512 B")]
        [InlineData(2048, "2 KB")]
        [InlineData(3_000_000, "2,9 MB")]
        public void AnzeigeText_FormatiertGroessenDeutsch(long groesse, string erwartetGroesse)
        {
            var vorschau = new ImagePreview { Dateiname = "foto.jpg", Groesse = groesse };
            Assert.Equal("foto.jpg (" + erwartetGroesse + ")", vorschau.AnzeigeText());
        }

        // --- ImagePreviewDecision ---------------------------------------------------

        [Theory]
        [InlineData("image/jpeg")]
        [InlineData("image/png")]
        [InlineData("image/gif")]
        [InlineData("image/webp")]
        public void Pruefe_ErlaubteTypenInnerhalbGroesse_Ja(string typ)
        {
            var vorschau = new ImagePreview { Uid = "1", Typ = typ, Groesse = 1024 };
            var ergebnis = ImagePreviewDecision.Pruefe(vorschau, maxBytes: 10 * 1024 * 1024);
            Assert.True(ergebnis.Anzeigen);
        }

        [Fact]
        public void Pruefe_Null_Nein()
        {
            var ergebnis = ImagePreviewDecision.Pruefe(null, maxBytes: 1024);
            Assert.False(ergebnis.Anzeigen);
            Assert.False(string.IsNullOrEmpty(ergebnis.Grund));
        }

        [Fact]
        public void Pruefe_LeereUid_Nein()
        {
            var vorschau = new ImagePreview { Uid = "", Typ = "image/jpeg", Groesse = 1024 };
            var ergebnis = ImagePreviewDecision.Pruefe(vorschau, maxBytes: 10 * 1024 * 1024);
            Assert.False(ergebnis.Anzeigen);
        }

        [Fact]
        public void Pruefe_ApplicationPdf_Nein()
        {
            var vorschau = new ImagePreview { Uid = "1", Typ = "application/pdf", Groesse = 1024 };
            var ergebnis = ImagePreviewDecision.Pruefe(vorschau, maxBytes: 10 * 1024 * 1024);
            Assert.False(ergebnis.Anzeigen);
            Assert.Contains("application/pdf", ergebnis.Grund);
        }

        [Fact]
        public void Pruefe_LeererTyp_Nein()
        {
            var vorschau = new ImagePreview { Uid = "1", Typ = "", Groesse = 1024 };
            var ergebnis = ImagePreviewDecision.Pruefe(vorschau, maxBytes: 10 * 1024 * 1024);
            Assert.False(ergebnis.Anzeigen);
        }

        [Fact]
        public void Pruefe_GroesseUeberGrenze_Nein()
        {
            var vorschau = new ImagePreview { Uid = "1", Typ = "image/jpeg", Groesse = 20 * 1024 * 1024 };
            var ergebnis = ImagePreviewDecision.Pruefe(vorschau, maxBytes: 10 * 1024 * 1024);
            Assert.False(ergebnis.Anzeigen);
        }

        [Fact]
        public void Pruefe_GroesseNullOderNegativ_Nein()
        {
            var vorschau = new ImagePreview { Uid = "1", Typ = "image/jpeg", Groesse = 0 };
            var ergebnis = ImagePreviewDecision.Pruefe(vorschau, maxBytes: 10 * 1024 * 1024);
            Assert.False(ergebnis.Anzeigen);
        }

        [Fact]
        public void Pruefe_GroesseGenauAnDerGrenze_Ja()
        {
            var vorschau = new ImagePreview { Uid = "1", Typ = "image/jpeg", Groesse = 1024 };
            var ergebnis = ImagePreviewDecision.Pruefe(vorschau, maxBytes: 1024);
            Assert.True(ergebnis.Anzeigen);
        }

        [Fact]
        public void StandardMaxBytes_MatchesServerDefault()
        {
            Assert.Equal(10L * 1024 * 1024, ImagePreview.StandardMaxBytes);
        }
    }
}
