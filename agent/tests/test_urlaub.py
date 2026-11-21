"""Tests fuer die Urlaubs-Autoantwort (URL-01..09).

Schwerpunkt ist der SCHLEIFENSCHUTZ. Alles andere an dieser Funktion ist
gewoehnlicher Code; der Teil, der bei einem Fehler echten Schaden anrichtet,
ist die Frage "koennen zwei Postfaecher sich gegenseitig endlos beantworten".
Deshalb pruefen die Tests hier jede einzelne Abwehrlinie und zusaetzlich, dass
die letzte Linie (einmal pro Absender) auch dann haelt, wenn alle vorherigen
versagen.
"""
from __future__ import annotations

import email
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from src import mail_relay, state, urlaub


VORLAGEN = "## Urlaub\nIch bin bis zum 14. September nicht erreichbar.\n"


class FakeImap:
    """Minimal-Doppel: liefert Kandidaten und merkt sich den `since`-Wert."""

    def __init__(self, kandidaten=None):
        self.kandidaten = kandidaten or []
        self.gefragt_since = None
        self.gefragt_limit = None

    def fetch_urlaub_candidates(self, since, limit):
        self.gefragt_since = since
        self.gefragt_limit = limit
        return self.kandidaten[:limit]


def _msg(
    from_="kunde@web.de",
    subject="Frage zum Angebot",
    to="chef@firma.de",
    headers=None,
    message_id="<abc@web.de>",
):
    kopf = {"message-id": [message_id], "to": [to]}
    kopf.update(headers or {})
    return SimpleNamespace(uid="1", subject=subject, from_=from_, to=(to,), text="", headers=kopf)


@pytest.fixture
def cfg(mock_config, tmp_path):
    db = tmp_path / "state.db"
    state.init_db(db)
    return replace(
        mock_config,
        state_db=db,
        own_email_address="chef@firma.de",
        imap_user="chef@firma.de",
        enable_urlaubsantwort=True,
        urlaub_dry_run=False,
        urlaub_von="2026-09-01",
        urlaub_bis="2026-09-14",
        urlaub_max_pro_zyklus=20,
        vorlagen_md=VORLAGEN,
    )


@pytest.fixture
def im_urlaub(mocker):
    """Setzt `heute` auf einen Tag INNERHALB des Fixture-Zeitraums."""
    mocker.patch.object(
        urlaub, "datetime", _FestesDatum(datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc))
    )


class _FestesDatum:
    """Ersetzt `urlaub.datetime` — `now()` mit und ohne tz liefert denselben
    festen Zeitpunkt, `datetime(...)` bleibt der echte Konstruktor."""

    def __init__(self, jetzt):
        self._jetzt = jetzt

    def now(self, tz=None):
        return self._jetzt if tz else self._jetzt.replace(tzinfo=None)

    def __call__(self, *args, **kwargs):
        return datetime(*args, **kwargs)


# --------------------------------------------------------------------------
# Zeitraum
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wert,erwartet",
    [
        ("2026-09-01", date(2026, 9, 1)),
        ("  2026-09-01  ", date(2026, 9, 1)),
        ("01.09.2026", None),
        ("09/01/2026", None),
        ("2026-13-01", None),
        ("2026-02-31", None),
        ("", None),
        ("morgen", None),
    ],
)
def test_parse_datum_akzeptiert_nur_iso(wert, erwartet):
    """Deutsche Schreibweise wird bewusst NICHT geraten: ein um Monate
    verschobener Urlaub verschickt entweder keine oder ganz falsche
    Autoantworten."""
    assert urlaub.parse_datum(wert) == erwartet


@pytest.mark.parametrize(
    "heute,erwartet",
    [
        (date(2026, 8, 31), False),
        (date(2026, 9, 1), True),   # erster Urlaubstag EINSCHLIESSLICH
        (date(2026, 9, 7), True),
        (date(2026, 9, 14), True),  # letzter Urlaubstag EINSCHLIESSLICH
        (date(2026, 9, 15), False),
    ],
)
def test_zeitraum_aktiv_beide_grenzen_einschliesslich(heute, erwartet):
    assert urlaub.zeitraum_aktiv("2026-09-01", "2026-09-14", heute) is erwartet


def test_zeitraum_mit_bis_vor_von_ist_nie_aktiv():
    assert urlaub.zeitraum_aktiv("2026-09-14", "2026-09-01", date(2026, 9, 7)) is False


def test_zeitraum_ohne_daten_ist_nie_aktiv():
    assert urlaub.zeitraum_aktiv("", "", date(2026, 9, 7)) is False
    assert urlaub.zeitraum_aktiv("2026-09-01", "", date(2026, 9, 7)) is False


def test_zeitraum_id_unterscheidet_urlaube():
    """Ein neuer Urlaub muss ein neuer Schluessel sein — sonst bekaeme
    derselbe Kunde im naechsten Urlaub keine Antwort mehr."""
    assert urlaub.zeitraum_id("2026-09-01", "2026-09-14") != urlaub.zeitraum_id(
        "2026-12-20", "2027-01-06"
    )


# --------------------------------------------------------------------------
# Schleifenschutz — Kopfzeilen
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers,stichwort",
    [
        ({"auto-submitted": ["auto-replied"]}, "Auto-Submitted"),
        ({"auto-submitted": ["auto-generated"]}, "Auto-Submitted"),
        ({"precedence": ["bulk"]}, "Precedence"),
        ({"precedence": ["list"]}, "Precedence"),
        ({"precedence": ["junk"]}, "Precedence"),
        ({"list-id": ["<news.example.de>"]}, "Listenpost"),
        ({"list-unsubscribe": ["<https://x.de/u>"]}, "Listenpost"),
        ({"x-autoreply": ["yes"]}, "x-autoreply"),
        ({"x-autorespond": ["1"]}, "x-autorespond"),
        ({"return-path": ["<>"]}, "Return-Path"),
        ({"x-failed-recipients": ["a@b.de"]}, "Zustellmeldung"),
    ],
)
def test_maschinelle_mails_werden_erkannt(headers, stichwort):
    automatisch, grund = urlaub.ist_automatische_mail(_msg(headers=headers))
    assert automatisch is True
    assert stichwort.lower() in grund.lower()


def test_auto_submitted_no_ist_keine_automatik():
    """`Auto-Submitted: no` ist der RFC-3834-Weg zu sagen 'ich bin ein Mensch'
    — wer das setzt, darf eine Antwort bekommen."""
    automatisch, _ = urlaub.ist_automatische_mail(_msg(headers={"auto-submitted": ["no"]}))
    assert automatisch is False


@pytest.mark.parametrize(
    "betreff",
    [
        "Automatische Antwort: Ihre Anfrage",
        "Abwesenheitsnotiz",
        "Out of Office",
        "Re: Out of office",
        "Undeliverable: Ihre Nachricht",
        "Mail delivery failed: returning message to sender",
    ],
)
def test_abwesenheits_betreff_wird_erkannt(betreff):
    """Letzte Verteidigungslinie fuer Gegenstellen ohne RFC-3834-Header."""
    automatisch, _ = urlaub.ist_automatische_mail(_msg(subject=betreff))
    assert automatisch is True


def test_normaler_betreff_loest_nicht_aus():
    automatisch, _ = urlaub.ist_automatische_mail(_msg(subject="Angebot für die Renovierung"))
    assert automatisch is False


@pytest.mark.parametrize(
    "adresse",
    [
        "no-reply@shop.de",
        "noreply@shop.de",
        "NoReply@Shop.de",
        "donotreply@shop.de",
        "mailer-daemon@provider.de",
        "postmaster@provider.de",
        "bounces+123@newsletter.de",
        "Shop <noreply-service@shop.de>",
    ],
)
def test_no_reply_adressen_bekommen_nie_antwort(adresse):
    assert urlaub.ist_no_reply_adresse(adresse) is True


@pytest.mark.parametrize("adresse", ["kunde@web.de", "Max Muster <max@firma.de>", "info@firma.de"])
def test_echte_adressen_sind_kein_no_reply(adresse):
    assert urlaub.ist_no_reply_adresse(adresse) is False


def test_nur_im_cc_bekommt_keine_antwort(cfg):
    """Sonst platzt die Autoantwort in einen fremden Thread und schreibt
    Menschen an, die den Betreiber gar nicht angeschrieben haben."""
    msg = _msg(to="jemand-anders@firma.de")
    msg.headers["cc"] = ["chef@firma.de"]
    faellig, grund = urlaub.antwort_faellig(msg, cfg, urlaub.eigene_adressen(cfg))
    assert faellig is False
    assert "To" in grund


def test_direkt_adressiert_erkennt_mehrere_empfaenger(cfg):
    msg = _msg(to="kollege@firma.de, chef@firma.de")
    assert urlaub.direkt_adressiert(msg, urlaub.eigene_adressen(cfg)) is True


def test_eigene_mail_bekommt_keine_antwort(cfg):
    faellig, grund = urlaub.antwort_faellig(
        _msg(from_="chef@firma.de"), cfg, urlaub.eigene_adressen(cfg)
    )
    assert faellig is False
    assert "eigenen Postfach" in grund


def test_alias_adresse_gilt_als_eigene(mock_config, tmp_path):
    """Bei einem Alias-Setup weichen IMAP_USER und OWN_EMAIL_ADDRESS ab —
    beide sind eigene Adressen."""
    cfg = replace(
        mock_config, own_email_address="info@firma.de", imap_user="chef@firma.de"
    )
    eigene = urlaub.eigene_adressen(cfg)
    assert {"info@firma.de", "chef@firma.de"} <= eigene


def test_normale_kundenmail_ist_faellig(cfg):
    faellig, grund = urlaub.antwort_faellig(_msg(), cfg, urlaub.eigene_adressen(cfg))
    assert faellig is True
    assert grund == ""


# --------------------------------------------------------------------------
# Merkliste — einmal pro Absender pro Zeitraum
# --------------------------------------------------------------------------


def test_merkliste_blockt_den_zweiten_versuch(cfg):
    zeitraum = urlaub.zeitraum_id(cfg.urlaub_von, cfg.urlaub_bis)
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum) is False
    state.mark_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum, "<a@b>", dry_run=False)
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum) is True


def test_merkliste_ist_gross_klein_egal(cfg):
    """Ohne Normalisierung bekaeme derselbe Mensch zwei Autoantworten."""
    zeitraum = urlaub.zeitraum_id(cfg.urlaub_von, cfg.urlaub_bis)
    state.mark_urlaubsantwort(cfg.state_db, "Kunde@Web.DE", zeitraum)
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum) is True


def test_merkliste_gilt_nur_fuer_diesen_zeitraum(cfg):
    state.mark_urlaubsantwort(cfg.state_db, "kunde@web.de", "2026-09-01..2026-09-14")
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", "2026-12-20..2027-01-06") is False


def test_probelauf_eintrag_blockt_scharfen_lauf_nicht(cfg):
    """Sonst haette der Probelauf seine eigenen Treffer verbraucht — derselbe
    Befund wie beim Sortierlauf (Quick 260822-4ay)."""
    zeitraum = urlaub.zeitraum_id(cfg.urlaub_von, cfg.urlaub_bis)
    state.mark_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum, dry_run=True)
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum, dry_run_lauf=False) is False
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum, dry_run_lauf=True) is True


# --------------------------------------------------------------------------
# Mail-Bau
# --------------------------------------------------------------------------


def _gebaut(**kwargs):
    roh = urlaub.build_urlaubsantwort(
        _msg(**kwargs), "Ich bin weg.", "chef@firma.de", "Chef Firma"
    )
    return email.message_from_bytes(roh)


def test_antwort_traegt_rfc3834_kopfzeilen():
    """Ohne `Auto-Submitted: auto-replied` antwortet eine regelkonforme
    Gegenstelle zurueck — das ist die halbe Schleife."""
    gebaut = _gebaut()
    assert gebaut["Auto-Submitted"] == "auto-replied"
    assert gebaut["X-Auto-Response-Suppress"] == "All"
    assert gebaut["Precedence"] == "bulk"


def test_antwort_zitiert_das_original_nicht():
    """Eine Abwesenheitsnotiz schickt dem Absender seine eigene Mail nicht
    zurueck — je weniger hinausgeht, desto kleiner der Datenschutz-Fussabdruck
    dieser Ausnahme."""
    gebaut = _gebaut(subject="Vertrauliches Angebot")
    inhalt = gebaut.get_payload(decode=True).decode("utf-8")
    assert inhalt.strip() == "Ich bin weg."
    assert "schrieb" not in inhalt
    assert ">" not in inhalt


def test_antwort_haengt_am_richtigen_thread():
    gebaut = _gebaut(message_id="<original@web.de>")
    assert gebaut["In-Reply-To"] == "<original@web.de>"
    assert "<original@web.de>" in gebaut["References"]


def test_betreff_bekommt_re_prefix():
    assert _gebaut(subject="Angebot")["Subject"] == "Re: Angebot"


def test_bestehendes_re_wird_nicht_verdoppelt():
    assert _gebaut(subject="Re: Angebot")["Subject"] == "Re: Angebot"
    assert _gebaut(subject="AW: Angebot")["Subject"] == "AW: Angebot"


def test_leerer_betreff_bekommt_ersatz():
    assert _gebaut(subject="")["Subject"] == "Abwesenheitsnotiz"


def test_header_injection_wird_entschaerft():
    gebaut = _gebaut(subject="Angebot\r\nBcc: opfer@x.de")
    assert "opfer@x.de" not in (gebaut["Bcc"] or "")
    assert gebaut["Bcc"] is None


# --------------------------------------------------------------------------
# Lauf
# --------------------------------------------------------------------------


def test_flag_aus_macht_gar_nichts(cfg, mocker):
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    imap = FakeImap([_msg()])
    urlaub.run_urlaubsantwort(replace(cfg, enable_urlaubsantwort=False), imap)
    assert relay.call_count == 0
    assert imap.gefragt_since is None  # nicht einmal ein Fetch


def test_ausserhalb_des_zeitraums_passiert_nichts(cfg, mocker):
    mocker.patch.object(
        urlaub, "datetime", _FestesDatum(datetime(2026, 10, 1, tzinfo=timezone.utc))
    )
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    imap = FakeImap([_msg()])
    urlaub.run_urlaubsantwort(cfg, imap)
    assert relay.call_count == 0
    assert imap.gefragt_since is None


def test_ohne_vorlage_wird_nichts_gesendet(cfg, im_urlaub, mocker):
    """Der wichtigste Verzichtstest: lieber gar keine Antwort als eine
    erfundene an einen echten Dritten."""
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    imap = FakeImap([_msg()])
    urlaub.run_urlaubsantwort(replace(cfg, vorlagen_md="## Terminanfrage\nHallo\n"), imap)
    assert relay.call_count == 0
    assert imap.gefragt_since is None


def test_scharfer_lauf_liefert_aus_und_merkt_vor(cfg, im_urlaub, mocker):
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    urlaub.run_urlaubsantwort(cfg, FakeImap([_msg()]))

    assert relay.call_count == 1
    _, empfaenger, raw_bytes, *_ = relay.call_args[0]
    assert empfaenger == ["kunde@web.de"]
    assert b"Auto-Submitted: auto-replied" in raw_bytes

    zeitraum = urlaub.zeitraum_id(cfg.urlaub_von, cfg.urlaub_bis)
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum) is True


def test_probelauf_liefert_nichts_aus(cfg, im_urlaub, mocker):
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    urlaub.run_urlaubsantwort(replace(cfg, urlaub_dry_run=True), FakeImap([_msg()]))

    assert relay.call_count == 0
    zeitraum = urlaub.zeitraum_id(cfg.urlaub_von, cfg.urlaub_bis)
    # vorgemerkt, damit derselbe Absender nicht Zyklus fuer Zyklus erneut
    # gemeldet wird — aber NUR als Probelauf-Eintrag.
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum, dry_run_lauf=True) is True
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum) is False


def test_zweiter_zyklus_antwortet_demselben_absender_nicht_erneut(cfg, im_urlaub, mocker):
    """Der harte Riegel: selbst wenn alle Kopfzeilen-Pruefungen versagen,
    entsteht zwischen zwei Postfaechern hoechstens EINE Antwort je Richtung."""
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    for _ in range(3):
        urlaub.run_urlaubsantwort(cfg, FakeImap([_msg(message_id="<neu@web.de>")]))
    assert relay.call_count == 1


def test_zwei_verschiedene_absender_bekommen_beide_antwort(cfg, im_urlaub, mocker):
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    urlaub.run_urlaubsantwort(
        cfg, FakeImap([_msg(from_="a@web.de"), _msg(from_="b@web.de")])
    )
    assert relay.call_count == 2


def test_automatische_mail_wird_uebersprungen(cfg, im_urlaub, mocker):
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    urlaub.run_urlaubsantwort(
        cfg, FakeImap([_msg(headers={"auto-submitted": ["auto-replied"]})])
    )
    assert relay.call_count == 0


def test_deckel_begrenzt_antworten_pro_zyklus(cfg, im_urlaub, mocker):
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    kandidaten = [_msg(from_=f"k{i}@web.de", message_id=f"<{i}@web.de>") for i in range(10)]
    urlaub.run_urlaubsantwort(replace(cfg, urlaub_max_pro_zyklus=3), FakeImap(kandidaten))
    assert relay.call_count == 3


def test_fehlgeschlagene_auslieferung_sperrt_den_absender_nicht(cfg, im_urlaub, mocker):
    """Sonst staende ein Kunde wegen eines einmaligen SMTP-Aussetzers den
    ganzen Urlaub ohne Antwort da."""
    relay = mocker.patch.object(
        mail_relay, "urlaubsantwort_ausliefern", side_effect=mail_relay.RelayError("SMTP weg")
    )
    urlaub.run_urlaubsantwort(cfg, FakeImap([_msg()]))

    zeitraum = urlaub.zeitraum_id(cfg.urlaub_von, cfg.urlaub_bis)
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum) is False

    # naechster Zyklus: neuer Versuch
    relay.side_effect = None
    urlaub.run_urlaubsantwort(cfg, FakeImap([_msg()]))
    assert state.hat_urlaubsantwort(cfg.state_db, "kunde@web.de", zeitraum) is True


def test_ein_fehler_stoppt_den_lauf_nicht(cfg, im_urlaub, mocker):
    relay = mocker.patch.object(
        mail_relay,
        "urlaubsantwort_ausliefern",
        side_effect=[mail_relay.RelayError("weg"), None],
    )
    urlaub.run_urlaubsantwort(
        cfg, FakeImap([_msg(from_="a@web.de"), _msg(from_="b@web.de")])
    )
    assert relay.call_count == 2


def test_since_beginnt_fruehestens_am_ersten_urlaubstag(cfg, im_urlaub):
    """Sonst beantwortet ein am ersten Urlaubstag eingeschalteter Agent auch
    Mails vom Vortag, die der Betreiber laengst gelesen hat."""
    imap = FakeImap([])
    urlaub.run_urlaubsantwort(cfg, imap)
    assert imap.gefragt_since.date() >= date(2026, 9, 1)


def test_since_bleibt_im_lookback_wenn_der_urlaub_laenger_laeuft(cfg, mocker):
    """Am zehnten Urlaubstag darf nicht der ganze Urlaub erneut abgearbeitet
    werden — dann greift das 24-Stunden-Fenster."""
    mocker.patch.object(
        urlaub, "datetime", _FestesDatum(datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc))
    )
    imap = FakeImap([])
    urlaub.run_urlaubsantwort(cfg, imap)
    assert imap.gefragt_since == datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)


def test_fetch_fehler_bricht_nicht_hart(cfg, im_urlaub, mocker):
    class KaputtesImap:
        def fetch_urlaub_candidates(self, since, limit):
            raise RuntimeError("IMAP weg")

    mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    urlaub.run_urlaubsantwort(cfg, KaputtesImap())  # darf nicht werfen


# --------------------------------------------------------------------------
# "Er soll selbst verstehen, wann der Urlaub vorbei ist"
# --------------------------------------------------------------------------
#
# Betreiber-Vorgabe 2026-08-23: "man soll nach Urlaub nicht sagen muessen 'ich
# bin zurueck', er sollte das je nach Datum selber verstehen." Die Zusicherung
# haengt daran, dass `run_urlaubsantwort` den KALENDERTAG prueft und nicht das
# Flag — das Flag bleibt nach dem Urlaub stehen und ist wirkungslos.
#
# Bezugstag ist die Ortszeit des Containers. Damit das die deutsche und nicht
# die UTC-Tagesgrenze ist, setzen beide Compose-Dateien `TZ=Europe/Berlin`
# (Zonendaten liegen im Image). Ohne das waere der letzte Urlaubstag erst um
# 02:00 des Folgetags zu Ende.


def test_agent_hoert_am_tag_nach_dem_urlaub_von_selbst_auf(cfg, mocker):
    """Kern der Zusicherung: das Flag steht noch auf AN, der Zeitraum ist
    vorbei — es geht nichts mehr hinaus, ohne dass jemand etwas sagen muss."""
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")

    # Letzter Urlaubstag: es wird geantwortet.
    mocker.patch.object(
        urlaub, "datetime", _FestesDatum(datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc))
    )
    urlaub.run_urlaubsantwort(cfg, FakeImap([_msg(from_="a@web.de")]))
    assert relay.call_count == 1

    # Ein Tag spaeter: nichts mehr — cfg ist UNVERAENDERT, enable bleibt True.
    mocker.patch.object(
        urlaub, "datetime", _FestesDatum(datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))
    )
    imap = FakeImap([_msg(from_="b@web.de")])
    urlaub.run_urlaubsantwort(cfg, imap)

    assert cfg.enable_urlaubsantwort is True  # Flag steht weiterhin
    assert relay.call_count == 1              # trotzdem keine zweite Antwort
    assert imap.gefragt_since is None         # nicht einmal ein Fetch


def test_agent_faengt_am_ersten_urlaubstag_von_selbst_an(cfg, mocker):
    """Die andere Richtung: vorab eingetragen, und er startet punktgenau."""
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")

    mocker.patch.object(
        urlaub, "datetime", _FestesDatum(datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc))
    )
    urlaub.run_urlaubsantwort(cfg, FakeImap([_msg(from_="a@web.de")]))
    assert relay.call_count == 0

    mocker.patch.object(
        urlaub, "datetime", _FestesDatum(datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))
    )
    urlaub.run_urlaubsantwort(cfg, FakeImap([_msg(from_="a@web.de")]))
    assert relay.call_count == 1


# --------------------------------------------------------------------------
# Anrede + Grussformel in der AUSGELIEFERTEN Mail
# (Befund aus dem scharfen Live-Test am 2026-08-23)
# --------------------------------------------------------------------------


def _ausgelieferter_text(relay) -> str:
    """Der Klartext-Rumpf der Mail, die tatsaechlich an den Relay ging.
    Dekodiert wird bewusst — Umlaute stehen als quoted-printable in den Bytes,
    eine Rohsuche nach "Freundliche Grüße" wuerde also immer fehlschlagen."""
    _, _, raw_bytes, *_ = relay.call_args[0]
    return email.message_from_bytes(raw_bytes).get_payload(decode=True).decode("utf-8")


def test_ausgelieferte_mail_hat_anrede_und_gruss(cfg, im_urlaub, mocker):
    """Der Live-Test am 2026-08-23 lieferte eine Mail aus, die beim Empfaenger
    mitten im Satz begann ("vielen Dank für Ihre Nachricht.") und ohne Gruss
    endete — die Vorlage ging 1:1 hinaus. Geprueft werden die Bytes, die den
    Agenten tatsaechlich verlassen, nicht der Rueckgabewert eines Helfers."""
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    urlaub.run_urlaubsantwort(replace(cfg, own_display_name="Chef Firma"), FakeImap([_msg()]))

    assert relay.call_count == 1
    text = _ausgelieferter_text(relay)
    zeilen = [z for z in text.splitlines() if z.strip()]
    assert zeilen[0] == "Guten Tag,"
    assert "Ich bin bis zum 14. September nicht erreichbar." in text
    assert zeilen[-2] == "Freundliche Grüße"
    assert zeilen[-1] == "Chef Firma"


def test_vorlage_mit_eigener_anrede_wird_nicht_doppelt_angeredet(cfg, im_urlaub, mocker):
    """Idempotenz am echten Sendeweg: wer die volle Mail in die Vorlage
    schreibt, darf kein "Guten Tag," ueber seiner eigenen Anrede bekommen."""
    eigen = (
        "## Urlaub\n"
        "Sehr geehrte Damen und Herren,\n\n"
        "ich bin bis zum 14. September nicht erreichbar.\n\n"
        "Mit freundlichen Grüßen\nEnver Shala\n"
    )
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    urlaub.run_urlaubsantwort(replace(cfg, vorlagen_md=eigen), FakeImap([_msg()]))

    text = _ausgelieferter_text(relay)
    assert "Guten Tag," not in text
    assert text.count("Sehr geehrte Damen und Herren,") == 1
    assert text.count("Mit freundlichen Grüßen") == 1
    assert "Freundliche Grüße\n" not in text


def test_ohne_vorlage_geht_weiter_NICHTS_hinaus(cfg, im_urlaub, mocker):
    """Gegenprobe zur Anreden-Ergaenzung: sie darf den wichtigsten Riegel nicht
    unterlaufen. Ohne `## Urlaub` waere sonst eine Mail denkbar, die nur aus
    "Guten Tag," + "Freundliche Grüße" besteht — inhaltsleer, an echte
    Dritte."""
    relay = mocker.patch.object(mail_relay, "urlaubsantwort_ausliefern")
    imap = FakeImap([_msg()])
    urlaub.run_urlaubsantwort(replace(cfg, vorlagen_md="## Terminanfrage\nHallo\n"), imap)
    assert relay.call_count == 0
    assert imap.gefragt_since is None
