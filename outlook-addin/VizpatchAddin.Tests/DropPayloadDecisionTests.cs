using VizpatchAddin.Core;
using Xunit;

namespace VizpatchAddin.Tests
{
    /// <summary>Tests fuer <see cref="DropPayloadDecision.Decide"/> (DND-01/02, 260731-u3m).</summary>
    public class DropPayloadDecisionTests
    {
        [Fact]
        public void ExplorerDropFormate_ErgebenFileDrop()
        {
            var plan = DropPayloadDecision.Decide(new[] { "FileDrop", "FileNameW", "FileName" });
            Assert.Equal(DropPayloadKind.FileDrop, plan.Kind);
        }

        [Fact]
        public void OutlookDropFormate_ErgebenFileContentsUnicode()
        {
            var plan = DropPayloadDecision.Decide(new[]
            {
                "RenPrivateMessages", "FileGroupDescriptorW", "FileContents", "FileGroupDescriptor",
            });
            Assert.Equal(DropPayloadKind.FileContents, plan.Kind);
            Assert.True(plan.DescriptorIsUnicode);
            Assert.Equal("FileGroupDescriptorW", plan.DescriptorFormat);
        }

        [Fact]
        public void NurAnsiDescriptor_ErgibtFileContentsAnsi()
        {
            var plan = DropPayloadDecision.Decide(new[] { "FileGroupDescriptor", "FileContents" });
            Assert.Equal(DropPayloadKind.FileContents, plan.Kind);
            Assert.False(plan.DescriptorIsUnicode);
            Assert.Equal("FileGroupDescriptor", plan.DescriptorFormat);
        }

        [Fact]
        public void DescriptorUndFileContentsUndFileDropGleichzeitig_BevorzugtFileContents()
        {
            var plan = DropPayloadDecision.Decide(new[]
            {
                "FileGroupDescriptorW", "FileContents", "FileDrop",
            });
            Assert.Equal(DropPayloadKind.FileContents, plan.Kind);
        }

        [Fact]
        public void ReineTextFormate_ErgebenNone()
        {
            var plan = DropPayloadDecision.Decide(new[] { "Text", "UnicodeText" });
            Assert.Equal(DropPayloadKind.None, plan.Kind);
        }

        [Fact]
        public void NullFormate_ErgebenNone()
        {
            var plan = DropPayloadDecision.Decide(null);
            Assert.Equal(DropPayloadKind.None, plan.Kind);
        }

        [Fact]
        public void LeereFormate_ErgebenNone()
        {
            var plan = DropPayloadDecision.Decide(new string[0]);
            Assert.Equal(DropPayloadKind.None, plan.Kind);
        }
    }
}
