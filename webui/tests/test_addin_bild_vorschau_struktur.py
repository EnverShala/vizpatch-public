"""Struktureller Wächter über `ChatView.cs` (Quick 260813-wea, Task 4).

Das VSTO-Projekt `outlook-addin/VizpatchAddin/` ist auf dieser Maschine NICHT
baubar (kein Outlook-COM-Interop verfügbar) — dieser Wächter ist der Ersatz
für den fehlenden Compiler: Python prüft den C#-Quelltext strukturell,
dieselbe Idee wie `test_addin_readonly.py`.

Deckt ab:
- `case "bild":` kommt genau einmal vor.
- `class ImageBubble` existiert und trägt ein `protected override void Dispose`.
- `_bild` UND `_quelle` werden mit `.Dispose()` freigegeben.
- `ImageBubble` ist sowohl in `ApplyTheme` als auch in `RelayoutBubbles`
  referenziert (Abschnittsgrenzen: vom Methodenkopf bis zum nächsten
  `private `-Methodenkopf).
- Der `LadeVorschauAsync`-Block enthält mindestens ein `try`, mindestens
  zwei `catch` und mindestens ein `MarshalToUi`.
- `PictureBoxSizeMode.Zoom` und `280` kommen vor.
- Gegenprobe: der Prüf-Helfer schlägt tatsächlich an, wenn `MarshalToUi` im
  `LadeVorschauAsync`-Block fehlt (belegt, dass der Wächter kein Blindgänger
  ist — Muster `test_guard_detects_injected_forbidden_write_pattern`).
"""
from __future__ import annotations

import re
from pathlib import Path

CHATVIEW_CS = (
    Path(__file__).resolve().parent.parent.parent
    / "outlook-addin"
    / "VizpatchAddin"
    / "TaskPane"
    / "ChatView.cs"
)

_COMMENT_LINE_RE = re.compile(r"^\s*(///|//|\*)")
_METHOD_HEADER_RE = re.compile(r"\n\s*private\s")


def _filtered_text(text: str) -> str:
    """Entfernt reine Kommentarzeilen (`^\\s*//`, `^\\s*///`, `^\\s*\\*`) — sonst
    könnte ein erklärender Kommentar (z. B. "kein MarshalToUi hier nötig")
    den Wächter selbst invalidieren (False-Positive-Schutz, Muster
    `test_addin_readonly.py`)."""
    lines = [line for line in text.splitlines() if not _COMMENT_LINE_RE.match(line)]
    return "\n".join(lines)


def _method_block(filtered_text: str, method_marker: str) -> str:
    """Schneidet den Abschnitt ab `method_marker` bis zum NÄCHSTEN
    `private `-Methodenkopf (exklusiv) heraus — Abschnittsgrenze für
    ApplyTheme/RelayoutBubbles/LadeVorschauAsync laut Plan."""
    start = filtered_text.index(method_marker)
    rest = filtered_text[start + len(method_marker) :]
    match = _METHOD_HEADER_RE.search(rest)
    end = start + len(method_marker) + (match.start() if match else len(rest))
    return filtered_text[start:end]


def _lade_vorschau_async_ok(text: str) -> bool:
    """Der eigentliche Prüf-Helfer für den `LadeVorschauAsync`-Block — als
    eigene Funktion geschrieben, damit die Gegenprobe unten DENSELBEN Code
    benutzt (Plan-Vorgabe)."""
    filtered = _filtered_text(text)
    if "private async void LadeVorschauAsync" not in filtered:
        return False
    block = _method_block(filtered, "private async void LadeVorschauAsync")
    has_try = "try" in block
    catch_count = len(re.findall(r"\bcatch\b", block))
    has_marshal = "MarshalToUi" in block
    return has_try and catch_count >= 2 and has_marshal


def _read_filtered_chatview() -> str:
    return _filtered_text(CHATVIEW_CS.read_text(encoding="utf-8"))


# --- case "bild": ---------------------------------------------------------


def test_case_bild_appears_exactly_once():
    filtered = _read_filtered_chatview()
    assert filtered.count('case "bild":') == 1


# --- ImageBubble-Klasse -----------------------------------------------------


def test_image_bubble_class_exists_with_dispose_override():
    filtered = _read_filtered_chatview()
    assert "class ImageBubble" in filtered
    idx = filtered.index("class ImageBubble")
    # Ab der Klassendeklaration bis zum naechsten "private sealed class" (die
    # naechste verschachtelte Klasse) oder Dateiende absuchen.
    rest = filtered[idx:]
    next_class = re.search(r"\n\s*private sealed class", rest[len("class ImageBubble") :])
    end = len(rest) if next_class is None else len("class ImageBubble") + next_class.start()
    class_block = rest[:end]
    assert "protected override void Dispose" in class_block


def test_image_bubble_disposes_bild_and_quelle():
    filtered = _read_filtered_chatview()
    assert re.search(r"_bild[?]?\.Dispose\(\)", filtered)
    assert re.search(r"_quelle[?]?\.Dispose\(\)", filtered)


# --- Einhaengung in ApplyTheme/RelayoutBubbles ------------------------------


def test_image_bubble_referenced_in_apply_theme_section():
    filtered = _read_filtered_chatview()
    block = _method_block(filtered, "private void ApplyTheme")
    assert "ImageBubble" in block


def test_image_bubble_referenced_in_relayout_bubbles_section():
    filtered = _read_filtered_chatview()
    block = _method_block(filtered, "private void RelayoutBubbles")
    assert "ImageBubble" in block


# --- LadeVorschauAsync: try/catch/MarshalToUi -------------------------------


def test_lade_vorschau_async_has_try_two_catch_and_marshal_to_ui():
    text = CHATVIEW_CS.read_text(encoding="utf-8")
    assert _lade_vorschau_async_ok(text) is True


def test_guard_detects_missing_marshal_to_ui_in_lade_vorschau_async():
    """Gegenprobe (Muster `test_guard_detects_injected_forbidden_write_pattern`):
    ein künstlich präparierter Text OHNE `MarshalToUi` im
    `LadeVorschauAsync`-Block muss den Prüf-Helfer tatsächlich zum Anschlagen
    bringen — sonst wäre der Wächter wertlos."""
    poisoned = (
        "private async void LadeVorschauAsync(ImagePreview vorschau)\n"
        "{\n"
        "    try\n"
        "    {\n"
        "        var x = 1;\n"
        "    }\n"
        "    catch (AnhangDownloadException)\n"
        "    {\n"
        "    }\n"
        "    catch (Exception)\n"
        "    {\n"
        "    }\n"
        "}\n"
        "\n"
        "private void NaechsteMethode()\n"
        "{\n"
        "}\n"
    )
    assert _lade_vorschau_async_ok(poisoned) is False


def test_guard_ignores_marshal_to_ui_mentioned_only_in_comments():
    """False-Positive-Schutz: ein Kommentar, der `MarshalToUi` nur ERWAEHNT
    (kein echter Aufruf), darf den Wächter nicht faelschlich gruen schalten."""
    poisoned = (
        "private async void LadeVorschauAsync(ImagePreview vorschau)\n"
        "{\n"
        "    // kein MarshalToUi noetig, weil...\n"
        "    try\n"
        "    {\n"
        "        var x = 1;\n"
        "    }\n"
        "    catch (AnhangDownloadException)\n"
        "    {\n"
        "    }\n"
        "    catch (Exception)\n"
        "    {\n"
        "    }\n"
        "}\n"
        "\n"
        "private void NaechsteMethode()\n"
        "{\n"
        "}\n"
    )
    assert _lade_vorschau_async_ok(poisoned) is False


# --- PictureBoxSizeMode.Zoom / 280 -------------------------------------------


def test_picture_box_zoom_and_280_present():
    filtered = _read_filtered_chatview()
    assert "PictureBoxSizeMode.Zoom" in filtered
    assert "280" in filtered
