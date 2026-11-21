using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    public class SseLineParserTests
    {
        [Fact]
        public void TextChunk_SingleDataLine_YieldsMessageFrame()
        {
            var p = new SseLineParser();
            Assert.Null(p.Feed("data: Hallo"));
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal("message", frame.Value.EventType);
            Assert.Equal("Hallo", frame.Value.Data);
        }

        [Fact]
        public void TextChunk_MultipleDataLines_JoinedWithNewline()
        {
            var p = new SseLineParser();
            Assert.Null(p.Feed("data: Zeile eins"));
            Assert.Null(p.Feed("data: Zeile zwei"));
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal("message", frame.Value.EventType);
            Assert.Equal("Zeile eins\nZeile zwei", frame.Value.Data);
        }

        [Fact]
        public void ToolEvent_YieldsToolFrameWithLabel()
        {
            var p = new SseLineParser();
            Assert.Null(p.Feed("event: tool"));
            Assert.Null(p.Feed("data: mails_suchen"));
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal("tool", frame.Value.EventType);
            Assert.Equal("mails_suchen", frame.Value.Data);
        }

        [Fact]
        public void DoneEvent_YieldsDoneFrameWithEmptyData()
        {
            // Server sendet: "event: done\ndata: \n\n"
            var p = new SseLineParser();
            Assert.Null(p.Feed("event: done"));
            Assert.Null(p.Feed("data: "));
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal("done", frame.Value.EventType);
            Assert.Equal("", frame.Value.Data);
        }

        [Fact]
        public void ErrorEvent_YieldsErrorFrameWithMessage()
        {
            var p = new SseLineParser();
            Assert.Null(p.Feed("event: error"));
            Assert.Null(p.Feed("data: Interner Fehler bei der Verarbeitung."));
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal("error", frame.Value.EventType);
            Assert.Equal("Interner Fehler bei der Verarbeitung.", frame.Value.Data);
        }

        [Theory]
        // Genau EIN Leerzeichen ist Trennzeichen (SSE-Spec) — jedes weitere gehoert
        // zum Text. Diese Zeile stand frueher andersherum hier ("dreifach") und hat
        // damit den Betreiber-Befund vom 2026-08-16 festgeschrieben.
        [InlineData("data:   dreifach", "  dreifach")]
        [InlineData("data: eins", "eins")]
        [InlineData("data:ohne", "ohne")]
        public void DataPrefix_StripsExactlyOneLeadingSpace(string line, string expected)
        {
            var p = new SseLineParser();
            p.Feed(line);
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal(expected, frame.Value.Data);
        }

        [Fact]
        public void DataPrefix_KeepsWordBoundarySpace()
        {
            // Der reale Fall: die Tool-Schleife streamt, ein Chunk beginnt mitten im
            // Satz mit einem Leerzeichen. Frisst der Parser es, klebt das naechste
            // Wort am vorherigen ("obBildanhaenge").
            var p = new SseLineParser();
            p.Feed("data:  korrekt ankommen.");
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal(" korrekt ankommen.", frame.Value.Data);
        }

        [Fact]
        public void DataPrefix_KeepsTrailingSpace()
        {
            // Das Gegenstueck am anderen Chunk-Ende: ein Chunk, der MIT Leerzeichen
            // endet, darf es ebenfalls nicht verlieren.
            var p = new SseLineParser();
            p.Feed("data: ob Bildanhaenge ");
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal("ob Bildanhaenge ", frame.Value.Data);
        }

        [Fact]
        public void StreamedChunks_ReassembleWithoutLosingSpaces()
        {
            // Ende-zu-Ende ueber mehrere Frames: so setzt die ChatView den Text
            // zusammen. Genau diese Kette hat der Betreiber als fehlende
            // Leerzeichen gesehen.
            var p = new SseLineParser();
            var text = "";
            foreach (var chunk in new[] { "Es sieht wie", " eine Test-Mail", " aus." })
            {
                p.Feed("data: " + chunk);
                var f = p.Feed("");
                Assert.NotNull(f);
                text += f.Value.Data;
            }
            Assert.Equal("Es sieht wie eine Test-Mail aus.", text);
        }

        [Fact]
        public void EventPrefix_StripsExactlyOneLeadingSpace()
        {
            var p = new SseLineParser();
            p.Feed("event: tool");
            p.Feed("data: x");
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal("tool", frame.Value.EventType);
        }

        [Fact]
        public void BlankLineWithoutContent_YieldsNothing()
        {
            var p = new SseLineParser();
            Assert.Null(p.Feed(""));
            Assert.Null(p.Feed(""));
        }

        [Fact]
        public void OtherFields_id_retry_AreIgnored()
        {
            var p = new SseLineParser();
            Assert.Null(p.Feed("id: 42"));
            Assert.Null(p.Feed("retry: 1000"));
            Assert.Null(p.Feed("data: text"));
            var frame = p.Feed("");
            Assert.NotNull(frame);
            Assert.Equal("message", frame.Value.EventType);
            Assert.Equal("text", frame.Value.Data);
        }
    }
}
