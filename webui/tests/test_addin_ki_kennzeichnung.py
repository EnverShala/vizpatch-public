"""Struktureller Wächter über die Begrüßung in `ChatView.cs`.

Zwei Dinge werden hier festgenagelt:

1. **Die KI-Kennzeichnung.** Die Begrüßung MUSS „(KI)" enthalten. Das ist keine
   Formulierungsfrage, sondern eine Kennzeichnungspflicht: der Nutzer muss
   erkennen, dass er mit einem KI-System spricht (KI-Transparenzpflichten, seit
   2026-08-02 anwendbar). Ohne diesen Test würde die Kennzeichnung bei der
   nächsten Textpolitur unbemerkt verschwinden.

2. **Die Aufräumdisziplin des Tipp-Effekts.** Der Effekt läuft über einen
   WinForms-Timer gegen eine konkrete Blase. Wird diese Blase entfernt (Reset)
   oder die Pane geschlossen, ohne den Timer zu stoppen, tickt er verwaist
   weiter — genau der Fehler, der in dieser Datei schon einmal mit dem
   Denk-Animations-Timer den Reset „hängen" ließ (siehe Kommentar dort).

Das VSTO-Projekt lässt sich zwar auf dieser Maschine kompilieren, ein Compiler
prüft diese beiden Punkte aber nicht — er sähe eine fehlende Kennzeichnung und
einen vergessenen Stop-Aufruf nicht als Fehler.
"""
from __future__ import annotations

import re
from pathlib import Path

_ADDIN = (
    Path(__file__).resolve().parent.parent.parent / "outlook-addin" / "VizpatchAddin"
)
CHATVIEW_CS = _ADDIN / "TaskPane" / "ChatView.cs"
HOST_CS = _ADDIN / "TaskPane" / "ChatTaskPaneHost.cs"
THISADDIN_CS = _ADDIN / "ThisAddIn.cs"


def _quelltext() -> str:
    return CHATVIEW_CS.read_text(encoding="utf-8-sig")


def _chatview_konstruktor(text: str) -> str:
    """Body des `ChatView()`-Konstruktors bis zur ersten Zeile, die die Methode
    auf Einrückungsebene schließt — OHNE reine Kommentarzeilen.

    Die Kommentare müssen raus, sonst prüft der Wächter Prosa statt Code: der
    Kommentar an dieser Stelle erklärt gerade, warum `ShowGreeting()` hier NICHT
    stehen darf, und würde die Prüfung sonst selbst auslösen.
    """
    treffer = re.search(r"public ChatView\(\)\s*\{(.*?)\n        \}", text, re.DOTALL)
    assert treffer is not None, "ChatView()-Konstruktor nicht gefunden"
    return "\n".join(
        zeile
        for zeile in treffer.group(1).splitlines()
        if not zeile.lstrip().startswith("//")
    )


def _greeting_konstante(text: str) -> str:
    treffer = re.search(
        r"private const string GreetingText\s*=\s*(.*?);", text, re.DOTALL
    )
    assert treffer is not None, "GreetingText-Konstante nicht gefunden"
    return treffer.group(1)


def test_chatview_datei_existiert():
    assert CHATVIEW_CS.is_file(), f"ChatView.cs nicht gefunden: {CHATVIEW_CS}"


def test_begruessung_traegt_die_ki_kennzeichnung():
    """Kernprüfung: ohne „(KI)" fehlt die Kennzeichnung gegenüber dem Nutzer."""
    konstante = _greeting_konstante(_quelltext())

    assert "(KI)" in konstante, (
        "Die Begruessung muss die KI-Kennzeichnung '(KI)' tragen — "
        "Kennzeichnungspflicht, keine Stilfrage. Gefunden: " + konstante.strip()
    )


def test_begruessung_bleibt_eine_begruessung():
    """Absicherung gegen ein versehentliches Leeren der Konstante."""
    konstante = _greeting_konstante(_quelltext())

    assert "Assistent" in konstante
    assert len(konstante.strip()) > 20


def test_gegenprobe_der_kennzeichnungspruefung():
    """Belegt, dass die Prüfung kein Blindgänger ist: an einer Fassung OHNE
    Kennzeichnung muss sie anschlagen."""
    ohne_kennzeichnung = (
        'private const string GreetingText =\n'
        '    "Hallo, ich bin Ihr E-Mail-Assistent. Wie kann ich Ihnen behilflich sein?";'
    )
    konstante = _greeting_konstante(ohne_kennzeichnung)

    assert "(KI)" not in konstante


def test_tipp_effekt_wird_ueberall_gestoppt():
    """`StopGreetingAnimation` muss an allen drei Abräum-Stellen stehen: beim
    Reset (vor dem Entfernen der Blasen), beim Verwerfen der Pane (Dispose) und
    beim Neustart des Effekts selbst."""
    text = _quelltext()

    assert "private void StopGreetingAnimation()" in text, "Stop-Methode fehlt"
    # Definition + mindestens drei Aufrufstellen.
    assert text.count("StopGreetingAnimation()") >= 4, (
        "StopGreetingAnimation() wird zu selten aufgerufen — erwartet mindestens "
        "Reset, Dispose und der Neustart in ShowGreeting."
    )


def test_stop_steht_im_dispose():
    """Die Pane lebt so lange wie Outlook — ein laufender Timer darf sie nicht
    überleben."""
    text = _quelltext()
    block = re.search(
        r"protected override void Dispose\(bool disposing\)(.*?)base\.Dispose",
        text,
        re.DOTALL,
    )
    assert block is not None, "Dispose(bool) nicht gefunden"
    assert "StopGreetingAnimation()" in block.group(1)


def test_stop_laeuft_vor_dem_leeren_des_logs():
    """Reihenfolge-Prüfung: erst den Timer stoppen, dann die Blasen entsorgen.
    Umgekehrt tickt er gegen ein entferntes Control."""
    text = _quelltext()
    # Am Reset-Pfad festmachen, NICHT an exaktem Whitespace: der bisherige
    # Literal-Vergleich brach, sobald zwischen Stop und `_history.Clear()` eine
    # Zeile dazukam (2026-08-16, Aufräumen des zweiten Timers) — obwohl die
    # geprüfte Reihenfolge völlig intakt war.
    reset_pos = text.find("_history.Clear();")
    assert reset_pos != -1, "Reset-Pfad nicht gefunden"

    stop_pos = text.rfind("StopGreetingAnimation();", 0, reset_pos)
    assert stop_pos != -1, "Stop-Aufruf fehlt im Reset-Pfad"

    leeren_pos = text.find("while (_log.Controls.Count > 0)", reset_pos)
    assert leeren_pos != -1, "Log-Leerschleife nicht gefunden"
    assert stop_pos < leeren_pos, (
        "StopGreetingAnimation() muss VOR dem Entfernen der Blasen laufen."
    )


def test_begruessung_startet_nicht_im_konstruktor():
    """Der Tipp-Effekt darf NICHT im Konstruktor anlaufen. Die ChatView entsteht im
    `ThisAddIn_Startup`, also beim Start von Outlook und mit `Visible = false` —
    der Effekt waere abgelaufen, bevor der Betreiber die Pane einblendet, und er
    saehe nur den fertigen Text (Betreiber-Meldung 2026-08-16)."""
    body = _chatview_konstruktor(_quelltext())

    assert "ShowGreeting()" not in body, (
        "ShowGreeting() steht wieder im ChatView-Konstruktor — dann laeuft der "
        "Tipp-Effekt beim Outlook-Start gegen eine unsichtbare Pane ab. Er gehoert "
        "an NotifyPaneShown()."
    )


def test_gegenprobe_der_konstruktor_pruefung():
    """Belegt, dass die Konstruktor-Pruefung kein Blindgaenger ist: an einer Fassung
    MIT dem Aufruf muss sie anschlagen."""
    mit_aufruf = (
        "        public ChatView()\n"
        "        {\n"
        "            BuildUi();\n"
        "            ShowGreeting();\n"
        "        }\n"
    )
    body = _chatview_konstruktor(mit_aufruf)

    assert "ShowGreeting()" in body


def test_begruessung_wiederholt_sich_solange_nicht_gechattet_wurde():
    """Betreiber-Wunsch 2026-08-16: die Begrüßung läuft bei JEDEM Öffnen, solange
    der Betreiber noch nichts geschrieben hat — und schweigt, sobald ein
    Wortwechsel im Verlauf steht. Ein Einmal-Flag wäre die falsche Bedingung."""
    text = _quelltext()
    block = re.search(
        r"public void NotifyPaneShown\(\)(.*?)\n        \}", text, re.DOTALL
    )
    assert block is not None, "NotifyPaneShown nicht gefunden"
    body = block.group(1)

    assert "_history.Count" in body, (
        "NotifyPaneShown muss am VERLAUF entscheiden (schon gechattet?), nicht an "
        "einem Einmal-Flag — sonst erscheint die Begruessung nur beim allerersten "
        "Oeffnen."
    )
    assert "RestartGreeting()" in body, (
        "Ohne Austausch der alten Blase haengt beim zweiten Oeffnen eine zweite "
        "Begruessung unter der ersten."
    )


def test_antwort_tipp_effekt_ist_gepuffert():
    """Die Antwort kommt gestreamt an; der Effekt muss sich als Puffer dazwischen
    legen, statt Chunks direkt in die Blase zu schreiben."""
    text = _quelltext()

    assert "private void AppendAssistantChunk(string chunk)" in text
    block = re.search(
        r"private void AppendAssistantChunk\(string chunk\)(.*?)\n        \}",
        text,
        re.DOTALL,
    )
    assert block is not None
    assert "EnqueueAssistantText" in block.group(1), (
        "Antwort-Chunks muessen in den Tipp-Puffer wandern, sonst steht die Antwort "
        "wieder sofort ganz da."
    )


def test_antwort_tipp_effekt_wird_ueberall_aufgeraeumt():
    """Dieselbe Disziplin wie beim Begrüßungs-Timer — hier ist sie schon dreimal
    verletzt worden (Denk-Animation, Reset, Begrüßung). Der Timer tickt gegen eine
    konkrete Blase; wird sie entfernt, ohne ihn zu stoppen, tickt er verwaist
    weiter."""
    text = _quelltext()

    assert "private void StopTypingAnimation()" in text, "Stop-Methode fehlt"
    assert "private void FinishTypingNow()" in text, (
        "Ohne FinishTypingNow geht ein noch gepufferter Rest beim Blasenwechsel "
        "verloren oder landet in der falschen Blase."
    )

    dispose = re.search(
        r"protected override void Dispose\(bool disposing\)(.*?)base\.Dispose",
        text,
        re.DOTALL,
    )
    assert dispose is not None
    assert "StopTypingAnimation()" in dispose.group(1), (
        "Die Pane lebt so lange wie Outlook — ein laufender Timer darf sie nicht "
        "ueberleben."
    )


def test_typing_stop_laeuft_vor_dem_leeren_des_logs():
    """Reihenfolge-Prüfung, exakt analog zum Begrüßungs-Timer."""
    text = _quelltext()
    stop_pos = text.find("StopTypingAnimation();")
    leeren_pos = text.find("while (_log.Controls.Count > 0)")

    assert stop_pos != -1 and leeren_pos != -1
    assert stop_pos < leeren_pos, (
        "StopTypingAnimation() muss VOR dem Entfernen der Blasen laufen."
    )


def test_blasenwechsel_gibt_gepufferten_rest_aus():
    """Jede Stelle, die `_currentAssistant = null` setzt, beendet eine Antwortblase.
    Steht dort noch Text im Puffer, gehoert er in die ALTE Blase — sonst geht das
    Ende der Antwort verloren."""
    text = _quelltext()

    for methode in ("AppendUserBubble", "BeginAssistantBubble", "AppendErrorLine"):
        block = re.search(
            r"private void " + methode + r"\([^)]*\)(.*?)\n        \}", text, re.DOTALL
        )
        assert block is not None, f"{methode} nicht gefunden"
        assert "FinishTypingNow()" in block.group(1), (
            f"{methode} wechselt die Blase, ohne den Tipp-Puffer auszugeben — "
            "der Rest der vorigen Antwort ginge verloren."
        )


def test_begruessung_haengt_am_sichtbarwerden_der_pane():
    """Die Kette Pane-sichtbar -> Host -> ChatView muss vollstaendig verdrahtet sein.
    Reisst ein Glied, erscheint gar keine Begruessung mehr — schlimmer als der
    Fehler, der hier behoben wurde."""
    assert "public void NotifyPaneShown()" in _quelltext(), (
        "ChatView.NotifyPaneShown() fehlt — nichts startet die Begruessung mehr."
    )
    assert "public void NotifyPaneShown()" in HOST_CS.read_text(encoding="utf-8-sig"), (
        "ChatTaskPaneHost reicht das Sichtbarwerden nicht an die ChatView durch."
    )

    thisaddin = THISADDIN_CS.read_text(encoding="utf-8-sig")
    block = re.search(
        r"private void ChatPane_VisibleChanged\(object sender, EventArgs e\)(.*?)\n        \}",
        thisaddin,
        re.DOTALL,
    )
    assert block is not None, "ChatPane_VisibleChanged nicht gefunden"
    assert "NotifyPaneShown()" in block.group(1), (
        "ThisAddIn meldet das Sichtbarwerden der Pane nicht — die Begruessung "
        "erscheint dann nie."
    )


def test_timer_ist_ein_winforms_timer():
    """Ein Threadpool-Timer würde die Blase aus einem fremden Thread anfassen —
    in Outlook ein Absturz, kein Rendering-Fehler."""
    text = _quelltext()

    assert "System.Windows.Forms.Timer _greetingTimer" in text, (
        "Der Tipp-Effekt muss einen WinForms-Timer nutzen (tickt auf dem UI-Thread)."
    )
