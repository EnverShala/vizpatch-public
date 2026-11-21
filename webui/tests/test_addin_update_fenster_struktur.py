"""Struktureller Waechter ueber `UpdateDialog.cs` und `SettingsDialog.cs`
(Betreiber-Wunsch 2026-08-22).

Die Anforderung: klickt der Betreiber im Einstellungen-Dialog auf „Jetzt
updaten", schliesst dieser Dialog, ein eigenes Fenster uebernimmt den Vorgang,
kann NICHT weggeklickt werden und bietet am Ende genau einen Knopf: „OK".
Grund: bisher lief das Update im Einstellungen-Dialog, und danach standen dort
„Speichern" und „Abbrechen" — was nach einem abgeschlossenen Update sinnlos
und verwirrend ist.

Der C#-Compiler kann davon NICHTS pruefen: dass `ControlBox = false` gesetzt
ist, dass `OnFormClosing` einen laufenden Vorgang abbricht, dass es keinen
zweiten Knopf gibt — das sind alles Aussagen ueber Absicht, nicht ueber Typen.
Die Solution baut hier zwar (MSBuild via VS 18, Core 208 Tests gruen), aber ein
gruener Build sagt nur, dass es kompiliert. Deshalb dieser Waechter, Muster
`test_addin_bild_vorschau_struktur.py`.

Was NICHT geprueft werden kann und die Abnahme in echtem Outlook braucht:
Fensterposition/-groesse, Lesbarkeit der Texte, und ob Outlook das modale
Fenster wirklich vor der Pane haelt.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADDIN = Path(__file__).resolve().parent.parent.parent / "outlook-addin" / "VizpatchAddin"
UPDATE_DIALOG_CS = _ADDIN / "UpdateDialog.cs"
SETTINGS_DIALOG_CS = _ADDIN / "SettingsDialog.cs"
CHATVIEW_CS = _ADDIN / "TaskPane" / "ChatView.cs"
CSPROJ = _ADDIN / "VizpatchAddin.csproj"

_COMMENT_LINE_RE = re.compile(r"^\s*(///|//|\*)")


def _ohne_kommentare(text: str) -> str:
    """Reine Kommentarzeilen entfernen — sonst koennte ein erklaerender
    Kommentar („kein ControlBox hier") den Waechter selbst invalidieren
    (False-Positive-Schutz, Muster der bestehenden Add-in-Waechter)."""
    return "\n".join(
        line for line in text.splitlines() if not _COMMENT_LINE_RE.match(line)
    )


@pytest.fixture(scope="module")
def update_dialog() -> str:
    if not UPDATE_DIALOG_CS.exists():
        pytest.skip("Add-in-Quellen nicht im Dateisystem (Docker-Image-Build).")
    return _ohne_kommentare(UPDATE_DIALOG_CS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def settings_dialog() -> str:
    if not SETTINGS_DIALOG_CS.exists():
        pytest.skip("Add-in-Quellen nicht im Dateisystem (Docker-Image-Build).")
    return _ohne_kommentare(SETTINGS_DIALOG_CS.read_text(encoding="utf-8"))


# --- Das Fenster ist nicht wegklickbar ------------------------------------


def test_kein_fenster_x_und_kein_minimieren(update_dialog: str):
    assert "ControlBox = false" in update_dialog
    assert "MinimizeBox = false" in update_dialog
    assert "MaximizeBox = false" in update_dialog


def test_formclosing_bricht_ab_solange_der_vorgang_laeuft(update_dialog: str):
    """Der eigentliche Schutz: `ControlBox = false` entfernt nur das X.
    Alt+F4 und ein `Close()` aus Code gehen weiterhin durch — genau die faengt
    `OnFormClosing` ab."""
    assert "protected override void OnFormClosing" in update_dialog
    block = update_dialog.split("protected override void OnFormClosing", 1)[1]
    block = block.split("protected override", 1)[0]
    assert "_finished" in block
    assert "e.Cancel = true" in block


def test_windows_herunterfahren_wird_nicht_blockiert(update_dialog: str):
    """Ein Fenster, das das Herunterfahren von Windows verhindert, waere ein
    Defekt und kein Schutz."""
    block = update_dialog.split("protected override void OnFormClosing", 1)[1]
    block = block.split("protected override", 1)[0]
    assert "CloseReason.WindowsShutDown" in block
    assert "CloseReason.TaskManagerClosing" in block


def test_kein_cancelbutton_damit_esc_nicht_schliesst(update_dialog: str):
    assert "CancelButton" not in update_dialog


# --- Genau ein Knopf, erst am Ende aktiv ---------------------------------


def test_es_gibt_genau_einen_knopf_und_der_heisst_ok(update_dialog: str):
    knopf_texte = re.findall(r'Text = "([^"]+)"', update_dialog)
    # Fenstertitel ist auch ein Text = "…" — deshalb gegen die verbotenen
    # Beschriftungen pruefen statt gegen die Anzahl.
    assert "OK" in knopf_texte
    for verboten in ("Speichern", "Abbrechen", "Schließen", "Schliessen"):
        assert verboten not in knopf_texte, (
            f"'{verboten}' als Knopf im Update-Fenster — genau diese Auswahl "
            f"war der beanstandete Zustand."
        )
    assert update_dialog.count("new RoundButton(") == 1


def test_ok_startet_deaktiviert(update_dialog: str):
    """Ein aktiver OK-Knopf waehrend des Updates waere die Einladung, den
    Vorgang fuer beendet zu halten."""
    block = update_dialog.split("_okButton = new RoundButton", 1)[1].split("};", 1)[0]
    assert "Enabled = false" in block
    assert "DialogResult = DialogResult.OK" in block


def test_freigabe_haengt_am_zustand_nicht_an_einem_timer(update_dialog: str):
    """OK-Freigabe und Schliessbarkeit muessen dieselbe Quelle haben
    (`UpdateWindowState.IsFinished`), sonst laufen sie auseinander."""
    assert "_okButton.Enabled = state.IsFinished" in update_dialog
    assert "_finished = state.IsFinished" in update_dialog


# --- Der Einstellungen-Dialog fuehrt das Update nicht mehr selbst aus -----


def test_settingsdialog_startet_kein_update_mehr(settings_dialog: str):
    """Die Ausfuehrung ist nach UpdateDialog.cs gewandert. Bleibt hier ein
    zweiter Pfad zurueck, gaebe es wieder ein Update ueber
    „Speichern"/„Abbrechen"."""
    assert "StartUpdateAsync" not in settings_dialog
    assert "PollUpdateAsync" not in settings_dialog
    assert "BeginAddinSelfUpdate" not in settings_dialog


def test_settingsdialog_fordert_das_fenster_an_und_schliesst_sich(settings_dialog: str):
    assert "UpdateRequested" in settings_dialog
    assert "private void RequestUpdateWindow" in settings_dialog
    block = settings_dialog.split("private void RequestUpdateWindow", 1)[1]
    block = block.split("private ", 1)[0]
    assert "UpdateRequested = true" in block
    assert "Close()" in block


def test_settingsdialog_warnt_vor_verworfenen_eingaben(settings_dialog: str):
    """Der Dialog schliesst ohne zu speichern — hatte der Betreiber etwas
    eingetippt, muss er es erfahren, und zwar im SELBEN Dialog wie die
    Update-Bestaetigung (kein zweiter Rueckfrage-Dialog)."""
    assert "private bool HasUnsavedChanges" in settings_dialog
    block = settings_dialog.split("private void UpdateButton_Click", 1)[1]
    block = block.split("private void RequestUpdateWindow", 1)[0]
    assert "HasUnsavedChanges()" in block
    assert "verworfen" in block


def test_laufendes_update_wandert_auch_ins_fenster(settings_dialog: str):
    """Wird der Dialog geoeffnet, waehrend serverseitig schon ein Update
    laeuft, gehoert der Fortschritt ebenfalls ins eigene Fenster — sonst stuende
    er wieder ueber „Speichern"/„Abbrechen"."""
    block = settings_dialog.split("UpdateDisplayMode.Running", 1)[1]
    block = block.split("UpdateDisplayMode.OfferUpdate", 1)[0]
    assert "RequestUpdateWindow" in block
    assert "true" in block  # attachOnly


def test_keine_zweite_schrittliste_im_dialog(settings_dialog: str):
    """Die Schritt-Bezeichnungen liegen testbar im Core. Eine zweite Kopie
    hier wuerde beim naechsten neuen Server-Schritt auseinanderlaufen."""
    assert "_stepLabels" not in settings_dialog
    assert "UpdateWindowState.StepLabel" in settings_dialog


# --- Verdrahtung ----------------------------------------------------------


def test_chatview_oeffnet_das_fenster_nach_dem_dialog(update_dialog: str):
    if not CHATVIEW_CS.exists():
        pytest.skip("Add-in-Quellen nicht im Dateisystem.")
    text = _ohne_kommentare(CHATVIEW_CS.read_text(encoding="utf-8"))
    assert "dialog.UpdateRequested" in text
    assert "new UpdateDialog(" in text
    # Das Fenster darf NICHT innerhalb des using-Blocks des Einstellungen-
    # Dialogs geoeffnet werden (verschachtelte Modals mit unklarem Besitzer).
    vor_using_ende = text.split("private void ShowUpdateWindow", 1)[0]
    assert "new UpdateDialog(" not in vor_using_ende


def test_datei_ist_im_projekt_eingetragen():
    """Das VSTO-Projekt nutzt das alte csproj-Format mit expliziter
    Dateiliste — eine neue .cs-Datei wird sonst STILL nicht kompiliert. Genau
    das ist beim ersten Build dieser Aenderung passiert."""
    if not CSPROJ.exists():
        pytest.skip("Add-in-Quellen nicht im Dateisystem.")
    assert 'Include="UpdateDialog.cs"' in CSPROJ.read_text(encoding="utf-8")


def test_ui_zugriffe_laufen_ueber_marshaltoui(update_dialog: str):
    """VSTO: kein SynchronizationContext im Outlook-Thread, jede
    await-Fortsetzung landet auf dem Threadpool. Ein direkter Control-Zugriff
    von dort beendet Outlook (Memory vsto_addin_async_threadpool_ui_crash)."""
    assert "private void MarshalToUi" in update_dialog
    # Auf die DEFINITION splitten, nicht auf den Namen: der erste Treffer waere
    # sonst die AUFRUFSTELLE (`PollUpdateAsync();`) und der geprüfte Block
    # damit ein paar Zeilen Rauschen — beim ersten Lauf genau so passiert.
    definitionen = (
        "private async void StartUpdateAsync()",
        "private async void PollUpdateAsync()",
        "private async Task BeginAddinSelfUpdateAsync(",
    )
    for definition in definitionen:
        assert definition in update_dialog, f"Methode {definition} nicht gefunden"
        block = update_dialog.split(definition, 1)[1].split("\n        private ", 1)[0]
        assert "MarshalToUi" in block, f"{definition} berührt die UI ohne MarshalToUi"


def test_kein_zusaetzlicher_messagebox_am_ende(update_dialog: str):
    """Ein Hinweisdialog ueber einem Fenster, das ohnehin nur „OK" anbietet,
    war genau die Doppelung, die abgeschafft wurde."""
    assert "MessageBox.Show" not in update_dialog


# --- Gegenprobe: der Waechter ist kein Blindgaenger ----------------------


def test_waechter_erkennt_entfernten_schliess_schutz():
    """Beweist, dass die Pruefung anschlaegt — ohne sie waere jeder obige
    Test wertlos."""
    if not UPDATE_DIALOG_CS.exists():
        pytest.skip("Add-in-Quellen nicht im Dateisystem.")
    manipuliert = _ohne_kommentare(
        UPDATE_DIALOG_CS.read_text(encoding="utf-8")
    ).replace("e.Cancel = true", "e.Cancel = false")
    block = manipuliert.split("protected override void OnFormClosing", 1)[1]
    block = block.split("protected override", 1)[0]
    assert "e.Cancel = true" not in block


def test_waechter_erkennt_zweiten_knopf():
    if not UPDATE_DIALOG_CS.exists():
        pytest.skip("Add-in-Quellen nicht im Dateisystem.")
    manipuliert = _ohne_kommentare(UPDATE_DIALOG_CS.read_text(encoding="utf-8")).replace(
        'Text = "OK"', 'Text = "Abbrechen"'
    )
    assert "Abbrechen" in re.findall(r'Text = "([^"]+)"', manipuliert)
