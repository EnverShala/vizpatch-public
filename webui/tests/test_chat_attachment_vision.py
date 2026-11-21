"""Tests für den Anhang-Vision-Pfad (Quick 260813-06b, Teil B):
Magic-Byte-Formaterkennung (`_detect_vision_media_type`), eigene Limits
(`_vision_size_limit_bytes`/`_vision_pdf_page_limit`), Flag
(`_attachment_vision_allowed`), PDF-Seiten/Dünnheit
(`_pdf_page_count`/`_pdf_text_is_thin`) und Blockbau (`_build_vision_blocks`,
Task 2). Task 3 ergänzt `mail_anhang_lesen` selbst (Text-zuerst/Vision-
nur-wenn-nötig), Task 4 den Tool-Loop (pop/cache_control/Dedup/Fail-soft).

Deckt ab (Task 2):
1. `_detect_vision_media_type`: alle fünf Signaturen, Magic-Bytes schlagen
   `content_type`, keine bekannte Signatur -> `None` (TIFF/BMP/HEIC/SVG/ZIP/
   leer/zu kurz), unabhängig von `content_type`/Dateiendung.
2. `_vision_size_limit_bytes`/`_vision_pdf_page_limit`: Default-Werte,
   Env-änderbar, unabhängig von `MAX_ATTACHMENT_MB`.
3. `_attachment_vision_allowed`: Default an, per-Agent/global `false`,
   per-Agent gewinnt, andere Werte bleiben an.
4. `_pdf_page_count`/`_pdf_text_is_thin`: Seitenzahl, fehlendes pypdf,
   kaputtes PDF, Dünnheits-Schwelle (Boden + Pro-Seite).
5. `_build_vision_blocks`: JPEG/PNG/GIF/WebP/PDF-Erfolg, unbekanntes Format,
   Größen-/Seiten-Gate (KEINE base64-Kodierung bei Ablehnung), fehlendes
   pypdf/verschlüsseltes PDF, wirft nie.
"""
from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock

import pytest

from .anthropic_stream_fake import as_streaming_client
from .test_chat_attachment_tools import _fake_attachment, _msg_with_attachments, _setup_attachment_env
from .test_chat_tools import (
    _chat_system_prompt,
    _fake_response,
    _setup_env,
    _text_block,
    _tool_use_block,
    _write_agent_env,
)

# --- _detect_vision_media_type -------------------------------------------------


def test_detect_vision_media_type_jpeg():
    import src.chat_tools as chat_tools

    payload = b"\xff\xd8\xff" + b"x" * 20
    assert chat_tools._detect_vision_media_type(payload, "image/jpeg", "foto.jpg") == "image/jpeg"


def test_detect_vision_media_type_png():
    import src.chat_tools as chat_tools

    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    assert chat_tools._detect_vision_media_type(payload, "image/png", "foto.png") == "image/png"


@pytest.mark.parametrize("magic", [b"GIF87a", b"GIF89a"])
def test_detect_vision_media_type_gif(magic):
    import src.chat_tools as chat_tools

    payload = magic + b"x" * 20
    assert chat_tools._detect_vision_media_type(payload, "image/gif", "a.gif") == "image/gif"


def test_detect_vision_media_type_webp():
    import src.chat_tools as chat_tools

    payload = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"x" * 10
    assert chat_tools._detect_vision_media_type(payload, "image/webp", "a.webp") == "image/webp"


def test_detect_vision_media_type_pdf():
    import src.chat_tools as chat_tools

    payload = b"%PDF-1.4" + b"x" * 20
    assert chat_tools._detect_vision_media_type(payload, "application/pdf", "a.pdf") == "application/pdf"


def test_detect_vision_media_type_magic_bytes_override_wrong_content_type():
    """JPEG-Bytes trotz behauptetem `content_type=image/png` -> JPEG.
    Magic-Bytes sind die EINZIGE Autorität (T-06b-06)."""
    import src.chat_tools as chat_tools

    payload = b"\xff\xd8\xff" + b"x" * 20
    assert chat_tools._detect_vision_media_type(payload, "image/png", "a.png") == "image/jpeg"


def test_detect_vision_media_type_octet_stream_content_type_still_detected():
    """Absender setzen alltäglich `application/octet-stream` — Magic-Bytes
    tragen die Erkennung trotzdem."""
    import src.chat_tools as chat_tools

    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    result = chat_tools._detect_vision_media_type(payload, "application/octet-stream", "a.bin")
    assert result == "image/png"


@pytest.mark.parametrize(
    "payload,content_type,filename",
    [
        (b"II*\x00" + b"x" * 20, "image/tiff", "a.tiff"),
        (b"BM" + b"x" * 20, "image/bmp", "a.bmp"),
        (b"ftypheic" + b"x" * 20, "image/heic", "a.heic"),
        (b"<svg xmlns='x'>" + b"x" * 20, "image/svg+xml", "a.svg"),
        (b"PK\x03\x04" + b"x" * 20, "application/zip", "a.zip"),
        (b"", "image/jpeg", "leer.jpg"),
        (b"\xff\xd8", "image/jpeg", "kurz.jpg"),  # < 12 Bytes
    ],
)
def test_detect_vision_media_type_unsupported_formats_return_none(payload, content_type, filename):
    import src.chat_tools as chat_tools

    assert chat_tools._detect_vision_media_type(payload, content_type, filename) is None


# --- Limits ----------------------------------------------------------------------


def test_vision_size_limit_bytes_default_seven_mb():
    import src.chat_tools as chat_tools

    assert chat_tools._vision_size_limit_bytes() == 7 * 1024 * 1024


def test_vision_size_limit_bytes_env_overridable_and_independent_of_attachment_limit(monkeypatch):
    import src.chat_tools as chat_tools

    monkeypatch.setenv("MAX_VISION_MB", "1")
    assert chat_tools._vision_size_limit_bytes() == 1 * 1024 * 1024
    # MAX_ATTACHMENT_MB bleibt unabhängig -- unverändert bei 15 MB Default.
    assert chat_tools._attachment_size_limit_bytes() == 15 * 1024 * 1024


def test_vision_pdf_page_limit_default_twenty():
    import src.chat_tools as chat_tools

    assert chat_tools._vision_pdf_page_limit() == 20


def test_vision_pdf_page_limit_env_overridable(monkeypatch):
    import src.chat_tools as chat_tools

    monkeypatch.setenv("MAX_VISION_PDF_PAGES", "5")
    assert chat_tools._vision_pdf_page_limit() == 5


# --- _attachment_vision_allowed --------------------------------------------------


def test_attachment_vision_allowed_default_true(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")

    import src.chat_tools as chat_tools

    assert chat_tools._attachment_vision_allowed("info") is True


def test_attachment_vision_allowed_per_agent_false(tmp_path, monkeypatch):
    import src.agents_io as agents_io

    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    agents_io.write_env("info", {"ENABLE_ATTACHMENT_VISION": "false"})

    import src.chat_tools as chat_tools

    assert chat_tools._attachment_vision_allowed("info") is False


def test_attachment_vision_allowed_global_false(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    monkeypatch.setenv("ENABLE_ATTACHMENT_VISION", "false")

    import src.chat_tools as chat_tools

    assert chat_tools._attachment_vision_allowed("info") is False


def test_attachment_vision_allowed_per_agent_wins_over_global(tmp_path, monkeypatch):
    import src.agents_io as agents_io

    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    agents_io.write_env("info", {"ENABLE_ATTACHMENT_VISION": "true"})
    monkeypatch.setenv("ENABLE_ATTACHMENT_VISION", "false")

    import src.chat_tools as chat_tools

    assert chat_tools._attachment_vision_allowed("info") is True


@pytest.mark.parametrize("value", ["1", "yes", "FALSCH", ""])
def test_attachment_vision_allowed_other_values_stay_true(value, tmp_path, monkeypatch):
    import src.agents_io as agents_io

    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    if value:
        agents_io.write_env("info", {"ENABLE_ATTACHMENT_VISION": value})

    import src.chat_tools as chat_tools

    assert chat_tools._attachment_vision_allowed("info") is True


@pytest.mark.parametrize("value", ["FALSE", " false "])
def test_attachment_vision_allowed_false_variants_disable(value, tmp_path, monkeypatch):
    import src.agents_io as agents_io

    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info")
    agents_io.write_env("info", {"ENABLE_ATTACHMENT_VISION": value})

    import src.chat_tools as chat_tools

    assert chat_tools._attachment_vision_allowed("info") is False


# --- _pdf_page_count / _pdf_text_is_thin -----------------------------------------


def _make_pdf_bytes(n_pages: int = 1) -> bytes:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_page_count_returns_correct_number():
    pytest.importorskip("pypdf")
    import src.chat_tools as chat_tools

    assert chat_tools._pdf_page_count(_make_pdf_bytes(3)) == 3


def test_pdf_page_count_missing_pypdf_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)
    import src.chat_tools as chat_tools

    assert chat_tools._pdf_page_count(b"%PDF-fake") is None


def test_pdf_page_count_broken_pdf_returns_none():
    pytest.importorskip("pypdf")
    import src.chat_tools as chat_tools

    assert chat_tools._pdf_page_count(b"%PDF-not-really-a-pdf") is None


def test_pdf_page_count_encrypted_pdf_returns_none(mocker):
    pytest.importorskip("pypdf")
    import src.chat_tools as chat_tools

    fake_reader = mocker.MagicMock()
    fake_reader.is_encrypted = True
    mocker.patch("pypdf.PdfReader", return_value=fake_reader)

    assert chat_tools._pdf_page_count(b"%PDF-whatever") is None


@pytest.mark.parametrize(
    "text,seiten,expected",
    [
        ("x" * 50, 1, True),  # 1 Seite, 50 Zeichen -> dünn
        ("x" * 2000, 1, False),  # 1 Seite, 2000 Zeichen -> dick
        ("x" * 1500, 20, True),  # 20 Seiten, 1500 Zeichen -> dünn
        ("", 1, True),  # leer -> dünn
    ],
)
def test_pdf_text_is_thin_thresholds(text, seiten, expected):
    import src.chat_tools as chat_tools

    assert chat_tools._pdf_text_is_thin(text, seiten) is expected


def test_pdf_text_is_thin_strips_whitespace_before_counting():
    import src.chat_tools as chat_tools

    # 300 Leerzeichen sind KEIN Text -- nach dem Entfernen bleibt "" -> dünn.
    assert chat_tools._pdf_text_is_thin(" " * 300, 1) is True


# --- _build_vision_blocks ---------------------------------------------------------


def test_build_vision_blocks_jpeg_success_roundtrip_base64():
    import base64

    import src.chat_tools as chat_tools

    payload = b"\xff\xd8\xff" + b"x" * 50
    blocks, fehler = chat_tools._build_vision_blocks(payload, "image/jpeg", "foto.jpg")

    assert fehler is None
    assert len(blocks) == 1
    block = blocks[0]
    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(block["source"]["data"]) == payload


def test_build_vision_blocks_one_page_pdf_success():
    pdf_bytes = _make_pdf_bytes(1)
    import src.chat_tools as chat_tools

    blocks, fehler = chat_tools._build_vision_blocks(pdf_bytes, "application/pdf", "scan.pdf")

    assert fehler is None
    assert len(blocks) == 1
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"


def test_build_vision_blocks_unknown_format_names_content_type_and_filename():
    import src.chat_tools as chat_tools

    blocks, fehler = chat_tools._build_vision_blocks(b"II*\x00" + b"x" * 20, "image/tiff", "scan.tiff")

    assert blocks == []
    assert fehler is not None
    assert "image/tiff" in fehler
    assert "scan.tiff" in fehler


def test_build_vision_blocks_over_size_limit_no_base64_encoding(monkeypatch, mocker):
    import src.chat_tools as chat_tools

    monkeypatch.setenv("MAX_VISION_MB", "1")
    spy = mocker.spy(chat_tools.base64, "b64encode")
    payload = b"\xff\xd8\xff" + b"x" * (2 * 1024 * 1024)  # 2 MB > 1 MB Limit

    blocks, fehler = chat_tools._build_vision_blocks(payload, "image/jpeg", "gross.jpg")

    assert blocks == []
    assert fehler is not None
    assert "MB" in fehler
    spy.assert_not_called()


def test_build_vision_blocks_pdf_too_many_pages_no_encoding(monkeypatch, mocker):
    pytest.importorskip("pypdf")
    import src.chat_tools as chat_tools

    monkeypatch.setenv("MAX_VISION_PDF_PAGES", "2")
    pdf_bytes = _make_pdf_bytes(3)
    spy = mocker.spy(chat_tools.base64, "b64encode")

    blocks, fehler = chat_tools._build_vision_blocks(pdf_bytes, "application/pdf", "lang.pdf")

    assert blocks == []
    assert fehler is not None
    assert "3" in fehler
    assert "2" in fehler
    spy.assert_not_called()


def test_build_vision_blocks_pdf_missing_pypdf_returns_fehler(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)
    import src.chat_tools as chat_tools

    blocks, fehler = chat_tools._build_vision_blocks(b"%PDF-1.4" + b"x" * 20, "application/pdf", "a.pdf")

    assert blocks == []
    assert fehler is not None


def test_build_vision_blocks_pdf_encrypted_returns_fehler(mocker):
    pytest.importorskip("pypdf")
    import src.chat_tools as chat_tools

    fake_reader = mocker.MagicMock()
    fake_reader.is_encrypted = True
    mocker.patch("pypdf.PdfReader", return_value=fake_reader)

    blocks, fehler = chat_tools._build_vision_blocks(b"%PDF-1.4" + b"x" * 20, "application/pdf", "a.pdf")

    assert blocks == []
    assert fehler is not None


def test_build_vision_blocks_never_raises_on_garbage_input():
    import src.chat_tools as chat_tools

    # Kaputte/leere Eingaben duerfen nie eine Exception werfen.
    blocks, fehler = chat_tools._build_vision_blocks(b"", "", "")
    assert blocks == []
    assert fehler is not None


# --- Task 3: mail_anhang_lesen — Text zuerst, Vision nur wenn nötig -------------


def test_mail_anhang_lesen_text_file_unchanged_no_vision_key(mocker, tmp_path, monkeypatch):
    att = _fake_attachment("notiz.txt", "text/plain", b"hallo welt")
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="notiz.txt")

    assert result["modus"] == "text"
    assert "_vision_blocks" not in result
    assert "_vision_key" not in result
    assert result["text_redigiert"] == "hallo welt"


def test_mail_anhang_lesen_pdf_with_thick_text_stays_text_mode_no_vision_call(
    mocker, tmp_path, monkeypatch
):
    pytest.importorskip("pypdf")
    pdf_bytes = _make_pdf_bytes(1)
    att = _fake_attachment("rechnung.pdf", "application/pdf", pdf_bytes)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    dicker_text = "Rechnungstext mit vielen Zeichen. " * 50  # weit über der Schwelle
    mocker.patch("src.chat_tools._extract_attachment_text", return_value=(dicker_text, None))
    spy = mocker.spy(chat_tools, "_build_vision_blocks")

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="rechnung.pdf")

    assert result["modus"] == "text"
    assert "_vision_blocks" not in result
    assert result["text_redigiert"] == dicker_text[: chat_tools.MAX_TOOL_RESULT_BODY_CHARS]
    spy.assert_not_called()


def test_mail_anhang_lesen_scan_pdf_thin_text_triggers_vision_with_fallback_text(
    mocker, tmp_path, monkeypatch
):
    pytest.importorskip("pypdf")
    pdf_bytes = _make_pdf_bytes(1)  # add_blank_page -> leere Textebene = duenn
    att = _fake_attachment("scan.pdf", "application/pdf", pdf_bytes)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="scan.pdf")

    assert "fehler" not in result
    assert result["modus"] == "vision"
    assert result["_vision_blocks"][0]["type"] == "document"
    assert result["_vision_key"] == "42|INBOX|scan.pdf"
    assert "text_redigiert" in result  # Fail-soft-Rückfall (Task 4e)
    assert "pseudonymisiert" in result["hinweis"]


def test_mail_anhang_lesen_image_triggers_vision_no_fehler(mocker, tmp_path, monkeypatch):
    payload = b"\xff\xd8\xff" + b"x" * 30
    att = _fake_attachment("foto.jpg", "image/jpeg", payload)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="foto.jpg")

    assert "fehler" not in result
    assert result["modus"] == "vision"
    assert result["_vision_blocks"][0]["type"] == "image"
    assert result["_vision_key"] == "42|INBOX|foto.jpg"


def test_mail_anhang_lesen_vision_key_stable_same_call_differs_by_folder(mocker, tmp_path, monkeypatch):
    payload = b"\xff\xd8\xff" + b"x" * 30
    att = _fake_attachment("foto.jpg", "image/jpeg", payload)
    msg = _msg_with_attachments([att], uid="7")
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    r1 = chat_tools.mail_anhang_lesen("info", uid="7", dateiname_oder_index="foto.jpg", folder="INBOX")
    r2 = chat_tools.mail_anhang_lesen("info", uid="7", dateiname_oder_index="foto.jpg", folder="INBOX")
    assert r1["_vision_key"] == r2["_vision_key"]

    r3 = chat_tools.mail_anhang_lesen("info", uid="7", dateiname_oder_index="foto.jpg", folder="Kunden")
    assert r3["_vision_key"] != r1["_vision_key"]


def test_mail_anhang_lesen_image_vision_fails_oversize_keeps_old_fehler_plus_reason(
    mocker, tmp_path, monkeypatch
):
    payload = b"\xff\xd8\xff" + b"x" * (2 * 1024 * 1024)
    att = _fake_attachment("gross.jpg", "image/jpeg", payload)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)
    monkeypatch.setenv("MAX_VISION_MB", "1")

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="gross.jpg")

    assert "fehler" in result
    assert "nicht lesbar" in result["fehler"]
    assert "MB" in result["fehler"]
    assert "_vision_blocks" not in result
    assert result["dateiname"] == "gross.jpg"


def test_mail_anhang_lesen_pdf_vision_fails_too_many_pages_keeps_thin_text_with_hinweis(
    mocker, tmp_path, monkeypatch
):
    pytest.importorskip("pypdf")
    pdf_bytes = _make_pdf_bytes(3)
    att = _fake_attachment("scan.pdf", "application/pdf", pdf_bytes)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)
    monkeypatch.setenv("MAX_VISION_PDF_PAGES", "2")

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="scan.pdf")

    assert "fehler" not in result
    assert result["modus"] == "text"
    assert "_vision_blocks" not in result
    assert "text_redigiert" in result
    assert "hinweis" in result
    assert "3" in result["hinweis"]


def test_mail_anhang_lesen_vision_disabled_per_agent_image_stays_old_behavior(
    mocker, tmp_path, monkeypatch
):
    import src.agents_io as agents_io

    payload = b"\xff\xd8\xff" + b"x" * 30
    att = _fake_attachment("foto.jpg", "image/jpeg", payload)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)
    agents_io.write_env("info", {"ENABLE_ATTACHMENT_VISION": "false"})

    import src.chat_tools as chat_tools

    spy = mocker.spy(chat_tools, "_build_vision_blocks")
    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="foto.jpg")

    assert "fehler" in result
    assert "nicht lesbar" in result["fehler"]
    assert "_vision_blocks" not in result
    spy.assert_not_called()


def test_mail_anhang_lesen_vision_disabled_global_scan_pdf_returns_thin_text_no_blocks(
    mocker, tmp_path, monkeypatch
):
    pytest.importorskip("pypdf")
    pdf_bytes = _make_pdf_bytes(1)
    att = _fake_attachment("scan.pdf", "application/pdf", pdf_bytes)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)
    monkeypatch.setenv("ENABLE_ATTACHMENT_VISION", "false")

    import src.chat_tools as chat_tools

    spy = mocker.spy(chat_tools, "_build_vision_blocks")
    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="scan.pdf")

    assert "fehler" not in result
    assert result["modus"] == "text"
    assert "_vision_blocks" not in result
    spy.assert_not_called()


def test_mail_anhang_lesen_vision_mode_anonymizes_filename_and_notes_no_pseudonymization(
    mocker, tmp_path, monkeypatch
):
    payload = b"\xff\xd8\xff" + b"x" * 30
    filename = "foto-DE89370400440532013000.jpg"
    att = _fake_attachment(filename, "image/jpeg", payload)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools
    import src.pii as pii

    anonymizer = pii.Anonymizer()
    result = chat_tools.mail_anhang_lesen(
        "info", uid="42", dateiname_oder_index=filename, anonymizer=anonymizer
    )

    assert result["dateiname"] != filename
    assert "DE89370400440532013000" not in result["dateiname"]
    assert "pseudonymisiert" in result["hinweis"]


def test_mail_anhang_lesen_gif_hinweis_mentions_first_frame_only(mocker, tmp_path, monkeypatch):
    payload = b"GIF89a" + b"x" * 30
    att = _fake_attachment("anim.gif", "image/gif", payload)
    msg = _msg_with_attachments([att])
    _setup_attachment_env(mocker, tmp_path, monkeypatch, msg)

    import src.chat_tools as chat_tools

    result = chat_tools.mail_anhang_lesen("info", uid="42", dateiname_oder_index="anim.gif")

    assert result["modus"] == "vision"
    assert "erste" in result["hinweis"] or "erster" in result["hinweis"]


# --- Task 4: Tool-Loop — pop vor wrap, Listen-content, cache_control, ----------
# --- Dedup, Fail-soft -----------------------------------------------------------


def _vision_payload(vision_key="42|INBOX|foto.jpg", data="QUFBQQ=="):
    """Ein `mail_anhang_lesen`-Erfolgs-Payload MIT Vision-Blöcken, wie es der
    Tool-Loop vom Handler bekäme — `data` ist bewusst kein echtes base64
    (nur ein Marker-String), damit Tests präzise auf SEINE Ab- bzw.
    Anwesenheit prüfen können. `_vision_preview` (Quick 260813-wea, Task 2)
    ist ein WEITERER out-of-band-Schlüssel mit derselben Pop-Pflicht."""
    return {
        "uid": "42",
        "ordner": "INBOX",
        "dateiname": "foto.jpg",
        "typ": "image/jpeg",
        "groesse": 123,
        "modus": "vision",
        "hinweis": "Anhang liegt als Bild-/Dokument-Block bei.",
        "_vision_blocks": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
        ],
        "_vision_key": vision_key,
        "_vision_preview": {
            "uid": "42",
            "ordner": "INBOX",
            "index": 0,
            "dateiname": "foto.jpg",
            "typ": "image/jpeg",
            "groesse": 123,
        },
    }


def test_tool_loop_vision_blocks_out_of_band_no_base64_in_wrap_or_trace(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "foto.jpg"})],
    )
    round2 = _fake_response("end_turn", [_text_block("Auf dem Bild ist ein Auto zu sehen.")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload()),
    )

    events = list(chat_tools.run_agentic_chat("info", "Was ist auf dem Bild im Anhang?"))

    second_call_messages = mock_client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result_entry = second_call_messages[-1]["content"][0]
    assert isinstance(tool_result_entry["content"], list)
    text_part = tool_result_entry["content"][0]["text"]
    assert "QUFBQQ==" not in text_part
    assert "_vision_blocks" not in text_part
    assert "_vision_key" not in text_part
    image_part = tool_result_entry["content"][1]
    assert image_part["type"] == "image"
    assert image_part["source"]["data"] == "QUFBQQ=="

    for event in events:
        if event.get("type") in ("text", "trace"):
            assert isinstance(event["text"], str)
            assert "QUFBQQ==" not in event["text"]


def test_tool_loop_cache_control_only_on_vision_tool_result(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [
            _tool_use_block("mails_suchen", {"query": "x"}, tool_id="toolu_a"),
            _tool_use_block(
                "mail_anhang_lesen",
                {"uid": "42", "dateiname_oder_index": "foto.jpg"},
                tool_id="toolu_b",
            ),
        ],
    )
    round2 = _fake_response("end_turn", [_text_block("ok")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(chat_tools.TOOL_HANDLERS, "mails_suchen", MagicMock(return_value={"treffer": []}))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload()),
    )

    list(chat_tools.run_agentic_chat("info", "Suche und zeig mir das Bild"))

    second_call_messages = mock_client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result_entries = second_call_messages[-1]["content"]
    normal_entry = next(e for e in tool_result_entries if e["tool_use_id"] == "toolu_a")
    vision_entry = next(e for e in tool_result_entries if e["tool_use_id"] == "toolu_b")
    assert "cache_control" not in normal_entry
    assert vision_entry.get("cache_control") == {"type": "ephemeral"}


def test_tool_loop_deckelt_vision_cache_breakpoints(mocker, tmp_path, monkeypatch):
    """Quick 260816-0or (D-0or-02): der Deckel ist von 4 auf
    `MAX_VISION_CACHE_BREAKPOINTS` (== 2) gesunken — System-Prompt und
    Tool-Schemas (T-0or-04) beanspruchen jetzt ZWEI der insgesamt vier
    API-seitig erlaubten `cache_control`-Breakpoints, der Vision-Anteil
    tritt zwei davon an das Prompt-Präfix ab."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    assert chat_tools.MAX_VISION_CACHE_BREAKPOINTS == 2

    tool_blocks = [
        _tool_use_block(
            "mail_anhang_lesen",
            {"uid": "42", "dateiname_oder_index": f"foto{i}.jpg"},
            tool_id=f"toolu_{i}",
        )
        for i in range(5)
    ]
    round1 = _fake_response("tool_use", tool_blocks)
    round2 = _fake_response("end_turn", [_text_block("ok")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    def _handler(agent_id, uid, dateiname_oder_index, folder="INBOX", **_kwargs):
        return _vision_payload(vision_key=f"key-{dateiname_oder_index}")

    monkeypatch.setitem(chat_tools.TOOL_HANDLERS, "mail_anhang_lesen", _handler)

    list(chat_tools.run_agentic_chat("info", "Zeig mir alle fünf Bilder"))

    second_call_messages = mock_client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result_entries = second_call_messages[-1]["content"]
    breakpoints = [e for e in tool_result_entries if e.get("cache_control")]
    assert len(breakpoints) == chat_tools.MAX_VISION_CACHE_BREAKPOINTS


def test_tool_loop_dedup_same_vision_key_second_call_stays_string(mocker, tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [
            _tool_use_block(
                "mail_anhang_lesen",
                {"uid": "42", "dateiname_oder_index": "foto.jpg"},
                tool_id="toolu_1",
            ),
            _tool_use_block(
                "mail_anhang_lesen",
                {"uid": "42", "dateiname_oder_index": "foto.jpg"},
                tool_id="toolu_2",
            ),
        ],
    )
    round2 = _fake_response("end_turn", [_text_block("ok")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload()),
    )

    list(chat_tools.run_agentic_chat("info", "Zeig mir das Bild zweimal"))

    second_call_messages = mock_client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result_entries = second_call_messages[-1]["content"]
    first_entry = next(e for e in tool_result_entries if e["tool_use_id"] == "toolu_1")
    second_entry = next(e for e in tool_result_entries if e["tool_use_id"] == "toolu_2")
    assert isinstance(first_entry["content"], list)
    assert isinstance(second_entry["content"], str)
    assert "bereits ausgewertet" in second_entry["content"]


def test_tool_loop_fail_soft_retry_on_api_status_error_with_vision_blocks(mocker, tmp_path, monkeypatch):
    import httpx
    from anthropic import APIStatusError

    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "scan.pdf"})],
    )
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(400, request=req)
    api_error = APIStatusError("document block rejected", response=resp, body=None)
    round2b = _fake_response(
        "end_turn", [_text_block("Ich konnte den Anhang leider nicht als Bild auswerten.")]
    )

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, api_error, round2b]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload(vision_key="42|INBOX|scan.pdf")),
    )

    events = list(chat_tools.run_agentic_chat("info", "Was steht im Scan?"))

    assert mock_client.messages.create.call_count == 3
    third_call_messages = mock_client.messages.create.call_args_list[2].kwargs["messages"]
    tool_result_entry = third_call_messages[-1]["content"][0]
    assert isinstance(tool_result_entry["content"], str)
    assert "cache_control" not in tool_result_entry
    assert events[-1]["type"] == "text"
    assert "nicht als Bild auswerten" in events[-1]["text"]


def test_tool_loop_no_retry_without_vision_blocks_on_api_status_error(mocker, tmp_path, monkeypatch):
    import httpx
    from anthropic import APIStatusError

    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(500, request=req)
    api_error = APIStatusError("server error", response=resp, body=None)

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [api_error]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))

    events = list(chat_tools.run_agentic_chat("info", "Hallo"))

    assert mock_client.messages.create.call_count == 1
    assert events[-1]["type"] == "text"
    assert "Fehler beim LLM-Aufruf" in events[-1]["text"]


def test_tool_trace_line_for_vision_payload_has_metadata_no_base64():
    import src.chat_tools as chat_tools

    payload = _vision_payload()
    payload.pop("_vision_blocks")
    payload.pop("_vision_key")
    trace = chat_tools._tool_trace_line("mail_anhang_lesen", payload)

    assert "modus=vision" in trace
    assert "typ=" in trace
    assert "groesse=" in trace
    assert "QUFBQQ==" not in trace
    assert "_vision_blocks" not in trace
    assert "_vision_key" not in trace


def test_tool_trace_field_allowlist_has_no_underscore_prefixed_entries():
    import src.chat_tools as chat_tools

    assert all(not field.startswith("_") for field in chat_tools._TOOL_TRACE_FIELD_ALLOWLIST)


def test_tool_loop_pop_removes_all_underscore_keys_structural(mocker, tmp_path, monkeypatch):
    """Nach dem Dispatch darf das Payload-Dict keinen `_`-präfigierten
    Schlüssel mehr enthalten -- geprüft über den GEWRAPPTEN String, der
    `json.dumps(payload)` enthält: enthielte `payload` noch `_vision_blocks`,
    stünde der Schlüsselname (als JSON-Key) im String."""
    monkeypatch.setenv("WEBUI_CHAT_SYSTEM_PROMPT", str(_chat_system_prompt(tmp_path)))
    _setup_env(tmp_path, monkeypatch)
    _write_agent_env("info", provider="anthropic")

    import src.chat_tools as chat_tools

    round1 = _fake_response(
        "tool_use",
        [_tool_use_block("mail_anhang_lesen", {"uid": "42", "dateiname_oder_index": "foto.jpg"})],
    )
    round2 = _fake_response("end_turn", [_text_block("ok")])
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [round1, round2]
    mocker.patch("src.chat_tools.Anthropic", return_value=as_streaming_client(mock_client))
    monkeypatch.setitem(
        chat_tools.TOOL_HANDLERS,
        "mail_anhang_lesen",
        MagicMock(side_effect=lambda *a, **k: _vision_payload()),
    )

    list(chat_tools.run_agentic_chat("info", "Was ist auf dem Bild?"))

    second_call_messages = mock_client.messages.create.call_args_list[1].kwargs["messages"]
    tool_result_entry = second_call_messages[-1]["content"][0]
    wrapped_text = tool_result_entry["content"][0]["text"]
    assert not any(
        f'"{key}"' in wrapped_text for key in ("_vision_blocks", "_vision_key", "_vision_preview")
    )
