"""Datumsangaben in den EIGENEN Eingaben des Betreibers (Befund 2026-08-25).

Der Betreiber tippte im Chat „trag mir Urlaub ein von 2026-08-24 bis
2026-08-25". Beim Modell kam „von [DATUM_1] bis [DATUM_2]" an; es konnte den
Zeitraum nicht eintragen und antwortete, es stuenden „nur Platzhalter" da — was
die De-Anonymisierung dann wieder in die echten Daten zurueckverwandelte. Der
Betreiber las also einen Satz, der sich selbst widersprach, und die Empfehlung
darin („im Format JJJJ-MM-TT") war genau das Format, das anonymisiert wurde.

Betroffen war jede konkrete Schreibweise — und damit auch jede Suche wie
„Mails vom 20.08.2026", nicht nur der Urlaub.

Die Grenze, die diese Tests ziehen: Datumsangaben bleiben in BETREIBER-Eingaben
roh, alles andere bleibt dort geschuetzt, und MAILINHALTE bleiben vollstaendig
unangetastet.
"""
from __future__ import annotations


def test_datum_bleibt_in_eigener_eingabe_stehen():
    from src import pii

    a = pii.Anonymizer()
    satz = "trag mir Urlaub ein von 2026-08-24 bis 2026-08-25"
    assert a.anonymize(satz, ohne=pii.EIGENE_EINGABE_AUSNAHMEN) == satz


def test_alle_datumsschreibweisen_bleiben_stehen():
    """ISO, deutsch und mit Schraegstrichen — alle drei waren betroffen."""
    from src import pii

    for probe in ("2026-08-24", "24.08.2026", "24/08/2026", "1.9.26"):
        a = pii.Anonymizer()
        assert a.anonymize(probe, ohne=pii.EIGENE_EINGABE_AUSNAHMEN) == probe, probe


def test_uebrige_pii_bleibt_in_eigener_eingabe_geschuetzt():
    """Die Ausnahme gilt NUR fuer Datumsangaben — wer eine Telefonnummer oder
    eine IBAN in den Chat tippt, bekommt sie weiterhin pseudonymisiert."""
    from src import pii

    a = pii.Anonymizer()
    text = (
        "Schreib an kunde@example.com, Tel 0711 1234567, "
        "IBAN DE89370400440532013000, Termin am 2026-08-24"
    )
    ergebnis = a.anonymize(text, ohne=pii.EIGENE_EINGABE_AUSNAHMEN)

    assert "kunde@example.com" not in ergebnis
    assert "DE89370400440532013000" not in ergebnis
    assert "[EMAIL_1]" in ergebnis
    assert "[IBAN_1]" in ergebnis
    assert "[TELEFON_1]" in ergebnis
    # ... aber das Datum steht noch da:
    assert "2026-08-24" in ergebnis


def test_ohne_parameter_bleibt_alles_geschuetzt():
    """RUECKWAERTSKOMPATIBILITAET: der Default `ohne=()` darf das bisherige
    Verhalten nicht veraendern. Jeder Aufrufer, der nichts angibt — allen voran
    die Mailinhalte in `chat_tools` — schuetzt weiterhin auch Datumsangaben."""
    from src import pii

    a = pii.Anonymizer()
    assert a.anonymize("Termin am 2026-08-24") == "Termin am [DATUM_1]"


def test_deanonymize_funktioniert_weiterhin():
    from src import pii

    a = pii.Anonymizer()
    anonym = a.anonymize("Mail an kunde@example.com am 2026-08-24",
                         ohne=pii.EIGENE_EINGABE_AUSNAHMEN)
    assert a.deanonymize(anonym) == "Mail an kunde@example.com am 2026-08-24"


def test_chat_prompt_laesst_datum_in_der_nachricht_stehen(tmp_path, monkeypatch):
    """Der Weg, auf dem der Befund entstand: die Betreiber-Nachricht durch
    `build_chat_prompt`."""
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))

    import src.agents_io as agents_io
    from src import chat, pii

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})

    prompt = chat.build_chat_prompt(
        "info",
        "trag mir Urlaub ein von 2026-08-24 bis 2026-08-25",
        history=[],
        mail_context=None,
        anonymizer=pii.Anonymizer(),
    )
    assert "2026-08-24" in prompt
    assert "[DATUM_" not in prompt


def test_mailkontext_bleibt_geschuetzt(tmp_path, monkeypatch):
    """Die Gegenprobe und der eigentliche Zweck der Pseudonymisierung: eine
    echte Mail bleibt vollstaendig geschuetzt — auch ihre Datumsangaben."""
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("WEBUI_ENV_PATH", str(tmp_path / "root.env"))

    import src.agents_io as agents_io
    from src import chat, pii

    agents_io.write_env("info", {"AGENT_ENABLED": "false"})

    prompt = chat.build_chat_prompt(
        "info",
        "fasse zusammen",
        history=[],
        mail_context={
            "subject": "Termin",
            "from": "kunde@example.com",
            "body": "Ich wurde am 01.01.1980 geboren und komme am 2026-09-01.",
        },
        anonymizer=pii.Anonymizer(),
    )
    assert "01.01.1980" not in prompt
    assert "2026-09-01" not in prompt
    assert "[DATUM_" in prompt
