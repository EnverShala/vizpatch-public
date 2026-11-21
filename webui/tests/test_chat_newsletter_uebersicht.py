"""Tests fuer `newsletter_uebersicht` (Quick 260822-nlu).

Betreiber-Frage vom 2026-08-22: „kann er auch das Postfach durchsuchen und
Newsletter finden und auflisten?" Erste Fassung meldete JEDE Mail mit
`List-Unsubscribe` als Newsletter — der Betreiber fand darunter Inkasso-Post,
Rechnungen und Versandmeldungen.

**Am echten Postfach nachgemessen, warum ein Header-Filter das nicht heilen
kann:** die Musterversorger-RECHNUNG traegt `list-unsubscribe` + `list-id` + `x-mailer`
+ `feedback-id` + `x-csa-complaints`, also die volle Marketing-Signatur; die
Musterversand-VERSANDMELDUNGEN tragen `feedback-id` + `x-csa-complaints`;
`Precedence: bulk` kommt in keiner geprueften Mail vor. Deshalb zwei Schritte:
Kopfzeilen-Vorfilter (kostenlos) + EIN Klassifikations-Aufruf ueber die
Kandidaten.

Zwei Testprinzipien dieser Datei:

* Der Fake wertet `reverse` UND `headers_only` AUS. Lehre aus Quick
  260822-4ay/Befund 1: ein Fake, der das gepruefte Argument ignoriert, ist
  gruen gegen jedes Verhalten.
* Das Modell wird IMMER gemockt (`src.chat_tools.Anthropic`). Eine Testsuite
  darf keine echten API-Aufrufe machen — beim ersten Lauf dieser Datei ging
  genau das schief (401 gegen die echte API mit dem Test-Key).

`_setup_env`/`_write_agent_env`/`_msg` sind lokale Kopien der etablierten
Helfer (Muster `test_chat_abmelden.py`).
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest


def _setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEBUI_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("VIZPATCH_SECRET_KEY_FILE", str(tmp_path / ".secret_key"))


def _write_agent_env(agent_id, provider="anthropic", api_key="sk-ant-test-key"):
    import src.agents_io as agents_io

    agents_io.write_env(
        agent_id,
        {
            "IMAP_USER": "info@ionos.de",
            "IMAP_PASSWORD": "imap-pw",
            "LLM_API_KEY": api_key,
            "LLM_PROVIDER": provider,
        },
    )


def _msg(uid="1", subject="Unser Newsletter", from_="news@versender.example", headers=None):
    m = MagicMock()
    m.uid = uid
    m.subject = subject
    m.from_ = from_
    m.text = ""
    m.html = None
    m.date = None
    m.headers = headers or {}
    m.attachments = []
    return m


def _newsletter_headers(mailto=True, https=True, one_click=True):
    teile = []
    if mailto:
        teile.append("<mailto:unsubscribe@versender.example>")
    if https:
        teile.append("<https://versender.example/unsub?id=123>")
    headers = {}
    if teile:
        headers["list-unsubscribe"] = ", ".join(teile)
    if one_click:
        headers["list-unsubscribe-post"] = "List-Unsubscribe=One-Click"
    return headers


class _FakeMailbox:
    """Simuliert einen Server, der `reverse` und `headers_only` AUSWERTET.

    `messages` wird wie im Postfach von ALT nach NEU uebergeben.
    """

    def __init__(self, messages=None, fetch_raises=False, folder_set_raises=False):
        self.messages = list(messages or [])
        self.fetch_raises = fetch_raises
        self.fetch_kwargs: dict = {}
        self.folder = MagicMock()
        if folder_set_raises:
            self.folder.set.side_effect = RuntimeError("kein solcher Ordner")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def login(self, *_a, **_kw):
        return self

    def fetch(self, criteria, **kwargs):
        if self.fetch_raises:
            raise RuntimeError("fetch kaputt")
        self.fetch_kwargs = kwargs
        result = list(self.messages)
        if kwargs.get("reverse"):
            result = list(reversed(result))
        return result


def _patch_mailbox(mocker, mailbox):
    mocker.patch("src.chat_tools.MailBox", return_value=mailbox)
    return mailbox


def _modell_mock(mocker, antwort_text: str):
    """Patcht den Anthropic-Client mit einer festen Batch-Antwort."""
    client = MagicMock()
    block = types.SimpleNamespace(type="text", text=antwort_text)
    client.messages.create.return_value = types.SimpleNamespace(content=[block])
    mocker.patch("src.chat_tools.Anthropic", return_value=client)
    return client


def _modell_kaputt(mocker):
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("Modell weg")
    mocker.patch("src.chat_tools.Anthropic", return_value=client)
    return client


# --- Der Betreiber-Befund: Inkasso ist kein Newsletter ----------------------


def test_inkasso_und_rechnung_landen_nicht_unter_werbung(mocker, tmp_path, monkeypatch):
    """DER Befund vom 2026-08-22. Alle vier Mails tragen denselben
    Abmeldeweg — nur eine davon ist Werbung."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mails = [
        _msg(uid="1", from_="service@absender-a.example",
             subject="Einmalzahlung oder Rate? Gestalten Sie jetzt Ihren Zahlungsplan!",
             headers=_newsletter_headers()),
        _msg(uid="2", from_="Service@service.absender-c.example",
             subject="Ihre Rechnung zur Auftragsnummer 707273235",
             headers=_newsletter_headers()),
        _msg(uid="3", from_="info@absender-d.example",
             subject="Ihre Sendung zur Bestellung 5604350 wird heute versendet",
             headers=_newsletter_headers()),
        _msg(uid="4", from_="werbung@shop.example",
             subject="20% Rabatt auf alles - nur dieses Wochenende!",
             headers=_newsletter_headers()),
    ]
    _patch_mailbox(mocker, _FakeMailbox(mails))
    # reverse=True -> Reihenfolge im Prompt ist 4,3,2,1
    _modell_mock(mocker, "1|werbung\n2|transaktion\n3|transaktion\n4|transaktion")

    e = chat_tools.newsletter_uebersicht("info")

    assert e["werbung_gesamt"] == 1
    assert e["transaktion_gesamt"] == 3
    nach = {a["von"]: a for a in e["absender"]}
    assert nach["werbung@shop.example"]["werbung"] == 1
    for inkasso in ("service@absender-a.example", "Service@service.absender-c.example", "info@absender-d.example"):
        assert nach[inkasso]["werbung"] == 0
        assert nach[inkasso]["transaktion"] == 1
        assert "nicht abmelden" in nach[inkasso]["warnung"].lower()


def test_gemischter_absender_bekommt_eine_warnung(mocker, tmp_path, monkeypatch):
    """Der gefaehrliche Fall: derselbe Absender schickt Werbung UND Rechnungen
    ueber denselben Abmeldelink. Eine Abmeldung koennte den Rechnungsversand
    abschalten — das MUSS im Ergebnis stehen."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mails = [
        _msg(uid="1", from_="shop@example.com", subject="Ihre Rechnung 123",
             headers=_newsletter_headers()),
        _msg(uid="2", from_="shop@example.com", subject="Newsletter: neue Produkte",
             headers=_newsletter_headers()),
    ]
    _patch_mailbox(mocker, _FakeMailbox(mails))
    _modell_mock(mocker, "1|werbung\n2|transaktion")

    e = chat_tools.newsletter_uebersicht("info")
    a = e["absender"][0]

    assert a["werbung"] == 1 and a["transaktion"] == 1
    assert "BEIDES" in a["warnung"]
    assert "Rechnung" in a["warnung"]


def test_reiner_werbeabsender_bekommt_keine_warnung(mocker, tmp_path, monkeypatch):
    """Gegenprobe — sonst stuende unter jedem Absender eine Warnung."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="news@a.example", subject="Angebote der Woche",
             headers=_newsletter_headers()),
    ]))
    _modell_mock(mocker, "1|werbung")

    a = chat_tools.newsletter_uebersicht("info")["absender"][0]

    assert a["werbung"] == 1
    assert "warnung" not in a


def test_prompt_traegt_betreff_und_absender_und_die_zwei_kategorien(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="news@a.example", subject="Rabatt-Aktion",
             headers=_newsletter_headers()),
    ]))
    client = _modell_mock(mocker, "1|werbung")

    chat_tools.newsletter_uebersicht("info")

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Rabatt-Aktion" in prompt
    assert "news@a.example" in prompt
    assert "werbung" in prompt and "transaktion" in prompt
    # Der Zweifelsfall muss im Prompt geregelt sein, nicht dem Modell freistehen.
    assert "Zweifel" in prompt
    assert "INKASSO" in prompt


def test_nur_ein_modellaufruf_fuer_den_ganzen_ordner(mocker, tmp_path, monkeypatch):
    """Der Kostenpunkt des Werkzeugs: EIN Aufruf, nicht einer pro Mail."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mails = [
        _msg(uid=str(i), from_=f"news{i}@a.example", subject=f"Angebot {i}",
             headers=_newsletter_headers())
        for i in range(12)
    ]
    _patch_mailbox(mocker, _FakeMailbox(mails))
    client = _modell_mock(mocker, "\n".join(f"{i}|werbung" for i in range(1, 13)))

    chat_tools.newsletter_uebersicht("info")

    assert client.messages.create.call_count == 1


def test_mails_ohne_abmeldeweg_kosten_keinen_modellaufruf(mocker, tmp_path, monkeypatch):
    """Der Vorfilter ist der Grund, warum das Werkzeug billig ist: Mails ohne
    Abmeldeweg erreichen das Modell nie."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="kunde@a.example", subject="Frage zur Bestellung", headers={}),
        _msg(uid="2", from_="news@b.example", subject="Angebote", headers=_newsletter_headers()),
    ]))
    client = _modell_mock(mocker, "1|werbung")

    e = chat_tools.newsletter_uebersicht("info")

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Frage zur Bestellung" not in prompt
    assert e["mails_ohne_abmeldeweg"] == 1
    assert e["mails_mit_abmeldeweg"] == 1


def test_kein_kandidat_kein_modellaufruf(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([_msg(uid="1", headers={})]))
    client = _modell_mock(mocker, "1|werbung")

    e = chat_tools.newsletter_uebersicht("info")

    assert client.messages.create.call_count == 0
    assert e["absender"] == []


# --- Fehlschlag der Einordnung: ehrlich statt geraten ----------------------


def test_modellfehler_laesst_alles_unklar_und_sagt_es(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="news@a.example", subject="Angebote", headers=_newsletter_headers()),
    ]))
    _modell_kaputt(mocker)

    e = chat_tools.newsletter_uebersicht("info")

    assert e["unklar_gesamt"] == 1
    assert e["werbung_gesamt"] == 0
    assert "einordnung_fehlgeschlagen" in e
    assert "NICHT getrennt" in e["einordnung_fehlgeschlagen"]
    # Die Liste kommt trotzdem — das Werkzeug scheitert nicht.
    assert len(e["absender"]) == 1


def test_fremder_provider_liefert_liste_ohne_einordnung(mocker, tmp_path, monkeypatch):
    """Bei OpenAI/Google gibt es die Einordnung nicht (wie mails_sichten) — das
    Werkzeug liefert dann die Kopfzeilen-Liste und sagt warum."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="openai", api_key="sk-openai-test-key")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="news@a.example", subject="Angebote", headers=_newsletter_headers()),
    ]))

    e = chat_tools.newsletter_uebersicht("info")

    assert e["unklar_gesamt"] == 1
    assert "einordnung_fehlgeschlagen" in e
    assert "openai" in e["einordnung_fehlgeschlagen"]


def test_erfundene_kategorie_wird_unklar(mocker, tmp_path, monkeypatch):
    """Antwortet das Modell mit einer nicht angefragten Kategorie, darf daraus
    NIE 'werbung' werden."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="news@a.example", subject="Angebote", headers=_newsletter_headers()),
    ]))
    _modell_mock(mocker, "1|spam-vielleicht")

    e = chat_tools.newsletter_uebersicht("info")

    assert e["unklar_gesamt"] == 1
    assert e["werbung_gesamt"] == 0


def test_einordnung_ist_gedeckelt_und_sagt_es(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    monkeypatch.setattr(chat_tools, "MAX_NEWSLETTER_KLASSIFIKATION", 3)
    mails = [
        _msg(uid=str(i), from_=f"news{i}@a.example", subject=f"Angebot {i}",
             headers=_newsletter_headers())
        for i in range(6)
    ]
    _patch_mailbox(mocker, _FakeMailbox(mails))
    client = _modell_mock(mocker, "1|werbung\n2|werbung\n3|werbung")

    e = chat_tools.newsletter_uebersicht("info")

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert prompt.count("| Betreff:") == 3
    assert "einordnung_gedeckelt" in e
    assert e["unklar_gesamt"] == 3


# --- Beispiel-Mail: die Wahl ist fachlich ----------------------------------


def test_beispiel_mail_ist_eine_werbemail_mit_one_click(mocker, tmp_path, monkeypatch):
    """Die Bezugsmail ist die, an der eine Abmeldung haengt. Eine Rechnung als
    Bezugsmail waere irrefuehrend, eine ohne One-Click nicht ausloesbar."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mails = [
        _msg(uid="10", from_="shop@a.example", subject="Newsletter Mai",
             headers=_newsletter_headers(one_click=True)),
        _msg(uid="11", from_="shop@a.example", subject="Ihre Rechnung 42",
             headers=_newsletter_headers(one_click=True)),
    ]
    _patch_mailbox(mocker, _FakeMailbox(mails))
    # reverse -> Prompt-Reihenfolge: 1=Rechnung(uid 11), 2=Newsletter(uid 10)
    _modell_mock(mocker, "1|transaktion\n2|werbung")

    a = chat_tools.newsletter_uebersicht("info")["absender"][0]

    assert a["beispiel_uid"] == "10"
    assert a["beispiel_betreff"] == "Newsletter Mai"


def test_ohne_werbung_dient_eine_one_click_mail_als_bezug(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mails = [
        _msg(uid="20", from_="shop@a.example", subject="Rechnung alt",
             headers=_newsletter_headers(one_click=True)),
        _msg(uid="21", from_="shop@a.example", subject="Rechnung neu",
             headers=_newsletter_headers(mailto=True, https=False, one_click=False)),
    ]
    _patch_mailbox(mocker, _FakeMailbox(mails))
    _modell_mock(mocker, "1|transaktion\n2|transaktion")

    a = chat_tools.newsletter_uebersicht("info")["absender"][0]

    assert a["beispiel_uid"] == "20"
    assert a["one_click"] is True


def test_fetch_liest_neueste_zuerst_und_ohne_body(mocker, tmp_path, monkeypatch):
    """Leserichtung und Kostenaussage haengen an diesen zwei Argumenten."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mailbox = _patch_mailbox(mocker, _FakeMailbox([_msg(headers=_newsletter_headers())]))
    _modell_mock(mocker, "1|werbung")

    chat_tools.newsletter_uebersicht("info")

    assert mailbox.fetch_kwargs["reverse"] is True
    assert mailbox.fetch_kwargs["headers_only"] is True
    assert mailbox.fetch_kwargs["mark_seen"] is False


# --- Kopfzeilen-Auswertung -------------------------------------------------


def test_nur_mailto_ist_kein_one_click(mocker, tmp_path, monkeypatch):
    """RFC 8058: One-Click setzt eine https-URL voraus."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="nurmailto@a.example", subject="Angebote",
             headers=_newsletter_headers(mailto=True, https=False, one_click=True)),
    ]))
    _modell_mock(mocker, "1|werbung")

    a = chat_tools.newsletter_uebersicht("info")["absender"][0]

    assert a["one_click"] is False
    assert a["mailto"] == "mailto:unsubscribe@versender.example"
    assert a["https"] is None


def test_kaputter_header_zaehlt_als_ohne_abmeldeweg(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="kaputt@a.example", headers={"list-unsubscribe": "voelliger Unsinn"}),
        _msg(uid="2", from_="kaputt@a.example", headers={"list-unsubscribe": ""}),
    ]))
    client = _modell_mock(mocker, "1|werbung")

    e = chat_tools.newsletter_uebersicht("info")

    assert e["absender"] == []
    assert e["mails_ohne_abmeldeweg"] == 2
    assert client.messages.create.call_count == 0


def test_gruppiert_nach_absender_mit_anzahl(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mails = [
        _msg(uid="1", from_="news@a.example", subject="A1", headers=_newsletter_headers()),
        _msg(uid="2", from_="news@a.example", subject="A2", headers=_newsletter_headers()),
        _msg(uid="3", from_="news@a.example", subject="A3", headers=_newsletter_headers()),
        _msg(uid="4", from_="werbung@b.example", subject="B1", headers=_newsletter_headers()),
    ]
    _patch_mailbox(mocker, _FakeMailbox(mails))
    _modell_mock(mocker, "1|werbung\n2|werbung\n3|werbung\n4|werbung")

    e = chat_tools.newsletter_uebersicht("info")

    assert [a["von"] for a in e["absender"]] == ["news@a.example", "werbung@b.example"]
    assert e["absender"][0]["anzahl"] == 3
    assert e["absender_gesamt"] == 2


# --- Ehrlichkeit: Deckel, Zeitraum, Nichts-getan-Hinweis ------------------


def test_deckel_wird_benannt_statt_verschwiegen(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    anzahl = chat_tools.MAX_NEWSLETTER_ABSENDER + 5
    mails = [
        _msg(uid=str(i), from_=f"news{i}@a.example", subject=f"Angebot {i}",
             headers=_newsletter_headers())
        for i in range(anzahl)
    ]
    _patch_mailbox(mocker, _FakeMailbox(mails))
    _modell_mock(mocker, "\n".join(f"{i}|werbung" for i in range(1, anzahl + 1)))

    e = chat_tools.newsletter_uebersicht("info")

    assert len(e["absender"]) == chat_tools.MAX_NEWSLETTER_ABSENDER
    assert e["absender_gesamt"] == anzahl
    assert "gedeckelt" in e


def test_kein_deckel_hinweis_wenn_alles_reinpasst(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([_msg(headers=_newsletter_headers())]))
    _modell_mock(mocker, "1|werbung")

    e = chat_tools.newsletter_uebersicht("info")

    assert "gedeckelt" not in e
    assert "einordnung_gedeckelt" not in e
    assert "einordnung_fehlgeschlagen" not in e


def test_hinweis_warnt_vor_der_gleichsetzung_abmeldeweg_gleich_werbung(mocker, tmp_path, monkeypatch):
    """Der Hinweis ist die Stelle, an der das Modell den Befund erfaehrt."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([_msg(headers=_newsletter_headers())]))
    _modell_mock(mocker, "1|werbung")

    h = chat_tools.newsletter_uebersicht("info")["hinweis"]

    assert "NICHT" in h and "Werbung" in h
    assert "Inkasso" in h
    assert "mail_abmelden" in h


def test_standardzeitraum_wird_genannt(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([_msg(headers=_newsletter_headers())]))
    _modell_mock(mocker, "1|werbung")

    e = chat_tools.newsletter_uebersicht("info")

    assert "seit" in e or "zeitraum" in e


def test_ungueltiges_datum_erreicht_das_postfach_nie(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mailbox = _patch_mailbox(mocker, _FakeMailbox([_msg(headers=_newsletter_headers())]))
    client = _modell_mock(mocker, "1|werbung")

    e = chat_tools.newsletter_uebersicht("info", seit="irgendwann")

    assert "fehler" in e
    assert e["absender"] == []
    assert not mailbox.fetch_kwargs
    assert client.messages.create.call_count == 0


# --- Fehlerpfade: crasht nie hart (T-09-05-Muster) ------------------------


def test_unbekannter_ordner_liefert_fehlerfeld(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([], folder_set_raises=True))

    e = chat_tools.newsletter_uebersicht("info", folder="GibtsNicht")

    assert "GibtsNicht" in e["fehler"]
    assert e["absender"] == []


def test_fetch_fehler_liefert_fehlerfeld(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([], fetch_raises=True))

    e = chat_tools.newsletter_uebersicht("info")

    assert "fehler" in e
    assert e["absender"] == []


def test_imap_verbindungsfehler_liefert_fehlerfeld(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    mocker.patch("src.chat_tools.MailBox", side_effect=RuntimeError("kein Netz"))

    e = chat_tools.newsletter_uebersicht("info")

    assert e["fehler"] == "IMAP-Verbindung fehlgeschlagen."
    assert e["absender"] == []


def test_invalider_agent_id_wirft_value_error(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    with pytest.raises(ValueError):
        chat_tools.newsletter_uebersicht("../evil")


def test_leerer_ordner_ist_kein_fehler(mocker, tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([]))

    e = chat_tools.newsletter_uebersicht("info")

    assert "fehler" not in e
    assert e["absender"] == []
    assert e["ausgewertete_mails"] == 0


# --- Pseudonymisierung + Registrierung ------------------------------------


def test_absender_links_und_prompt_laufen_durch_den_anonymizer(mocker, tmp_path, monkeypatch):
    """Absender, Betreffs und die Abmelde-Links sind Mailinhalt — sie duerfen
    das Modell nur pseudonymisiert erreichen, auch im Klassifikations-Prompt."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools
    import src.pii as pii

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="news@versender.example", subject="Angebote",
             headers=_newsletter_headers()),
    ]))
    client = _modell_mock(mocker, "1|werbung")
    anonymizer = pii.Anonymizer()

    e = chat_tools.newsletter_uebersicht("info", anonymizer=anonymizer)

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "news@versender.example" not in prompt
    roh = str(e)
    assert "news@versender.example" not in roh
    assert "unsubscribe@versender.example" not in roh
    assert "news@versender.example" in anonymizer.deanonymize(e["absender"][0]["von"])


def test_ohne_anonymizer_bleiben_die_werte_roh(mocker, tmp_path, monkeypatch):
    """Flag aus -> Alt-Verhalten, kein Absinken unter den Ist-Zustand (D-78)."""
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    import src.chat_tools as chat_tools

    _patch_mailbox(mocker, _FakeMailbox([
        _msg(uid="1", from_="news@versender.example", subject="Angebote",
             headers=_newsletter_headers()),
    ]))
    _modell_mock(mocker, "1|werbung")

    e = chat_tools.newsletter_uebersicht("info")

    assert e["absender"][0]["von"] == "news@versender.example"


def test_registrierung_vollstaendig(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    assert "newsletter_uebersicht" in chat_tools.TOOL_HANDLERS
    assert "newsletter_uebersicht" in {s["name"] for s in chat_tools.TOOL_SCHEMAS}
    assert "newsletter_uebersicht" in chat_tools._TOOL_ACTIVITY_LABELS
    assert "newsletter_uebersicht" in chat_tools._ANON_AWARE_TOOLS


def test_schema_warnt_vor_der_gleichsetzung(tmp_path, monkeypatch):
    """Das Schema ist der einzige Ort, an dem das Modell VOR dem Aufruf
    erfaehrt, dass ein Abmeldeweg keine Werbung beweist."""
    _setup_env(tmp_path, monkeypatch)
    import src.chat_tools as chat_tools

    b = next(s for s in chat_tools.TOOL_SCHEMAS if s["name"] == "newsletter_uebersicht")["description"]

    assert "Inkasso" in b
    assert "werbung>0" in b
    assert "mail_abmelden" in b
    assert "mail_abmeldeweg_lesen" in b
    assert "NICHTS ABGEMELDET" in b.upper()


def test_system_prompt_nennt_das_werkzeug():
    from pathlib import Path

    prompt = (
        Path(__file__).resolve().parent.parent / "prompts" / "chat-system.txt"
    ).read_text(encoding="utf-8")

    assert "newsletter_uebersicht" in prompt
