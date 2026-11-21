from __future__ import annotations

from dns.resolver import resolve, NoAnswer, NXDOMAIN, NoNameservers

STATIC_PROVIDERS: dict[str, dict] = {
    "gmx.de":         {"host": "imap.gmx.net",           "port": 993, "ssl": True, "drafts": "Entwürfe",       "sent": "Gesendet"},
    "gmx.net":        {"host": "imap.gmx.net",           "port": 993, "ssl": True, "drafts": "Entwürfe",       "sent": "Gesendet"},
    "web.de":         {"host": "imap.web.de",            "port": 993, "ssl": True, "drafts": "Entwürfe",       "sent": "Gesendet"},
    "ionos.de":       {"host": "imap.ionos.de",          "port": 993, "ssl": True, "drafts": "Drafts",         "sent": "Gesendete Objekte"},
    "1und1.de":       {"host": "imap.ionos.de",          "port": 993, "ssl": True, "drafts": "Drafts",         "sent": "Gesendete Objekte"},
    "t-online.de":    {"host": "secureimap.t-online.de", "port": 993, "ssl": True, "drafts": "Entwürfe",       "sent": "Gesendet"},
    "gmail.com":      {"host": "imap.gmail.com",         "port": 993, "ssl": True, "drafts": "[Gmail]/Drafts", "sent": "[Gmail]/Sent Mail"},
    "googlemail.com": {"host": "imap.gmail.com",         "port": 993, "ssl": True, "drafts": "[Gmail]/Drafts", "sent": "[Gmail]/Sent Mail"},
    "outlook.com":    {"host": "outlook.office365.com",  "port": 993, "ssl": True, "drafts": "Drafts",         "sent": "Sent"},
    "hotmail.com":    {"host": "outlook.office365.com",  "port": 993, "ssl": True, "drafts": "Drafts",         "sent": "Sent"},
    "hotmail.de":     {"host": "outlook.office365.com",  "port": 993, "ssl": True, "drafts": "Drafts",         "sent": "Sent"},
    "mailbox.org":    {"host": "imap.mailbox.org",       "port": 993, "ssl": True, "drafts": "Drafts",         "sent": "Sent"},
}

MX_PATTERNS: list[tuple[str, dict]] = [
    ("emig.gmx.net",            STATIC_PROVIDERS["gmx.de"]),
    (".web.de",                 STATIC_PROVIDERS["web.de"]),
    ("1and1.com",               STATIC_PROVIDERS["ionos.de"]),
    ("kundenserver.de",         STATIC_PROVIDERS["ionos.de"]),
    ("ionos.",                  STATIC_PROVIDERS["ionos.de"]),
    (".t-online.de",            STATIC_PROVIDERS["t-online.de"]),
    ("l.google.com",            STATIC_PROVIDERS["gmail.com"]),
    ("protection.outlook.com",  STATIC_PROVIDERS["outlook.com"]),
    ("strato.de",               {"host": "imap.strato.de",      "port": 993, "ssl": True, "drafts": "Drafts",       "sent": "Sent"}),
    ("your-server.de",          {"host": "imap.your-server.de", "port": 993, "ssl": True, "drafts": "INBOX.Drafts", "sent": "Sent"}),
    ("alfahosting",             {"host": "imap.alfahosting.de", "port": 993, "ssl": True, "drafts": "INBOX.Drafts", "sent": "Sent"}),
    (".mailbox.org",            STATIC_PROVIDERS["mailbox.org"]),
]


def _get_mx_host(domain: str) -> str | None:
    """Niedrigste-Priorität MX-Hostname für Domain, oder None bei Fehler."""
    try:
        answers = resolve(domain, 'MX')
        records = sorted(answers, key=lambda r: r.preference)
        return str(records[0].exchange).lower().rstrip('.')
    except (NoAnswer, NXDOMAIN, NoNameservers, Exception):
        return None


def resolve_imap_config(email_address: str) -> dict:
    """
    Liefert {'host', 'port', 'ssl', 'drafts', 'sent'} für die Email-Domain.
    Priorität: 1) Statische Tabelle, 2) MX-Lookup, 3) RuntimeError.

    Gibt bewusst eine KOPIE zurück (nie das geteilte STATIC_PROVIDERS/MX_PATTERNS-Objekt):
    Aufrufer (z. B. agent/src/config.py::_build_config) mutieren das Ergebnis-Dict, um
    Overrides (IMAP_DRAFTS_FOLDER, IMAP_SENT_FOLDER) einzutragen. Ohne Kopie würde ein
    Agent mit explizitem Override den Provider-Default für alle weiteren Agenten
    derselben Domain (prozessweit, bis zum Neustart) verändern.
    """
    domain = email_address.split('@', 1)[-1].lower()

    if domain in STATIC_PROVIDERS:
        return dict(STATIC_PROVIDERS[domain])

    mx_host = _get_mx_host(domain)
    if mx_host:
        for pattern, cfg in MX_PATTERNS:
            if pattern in mx_host:
                return dict(cfg)

    raise RuntimeError(
        f"Kann IMAP-Config für Domain '{domain}' nicht auto-detektieren. "
        f"Bitte IMAP_HOST, IMAP_PORT, IMAP_USE_SSL in .env setzen."
    )


class InvalidDraftsFolder(RuntimeError):
    """Ein Entwurf darf NIE im Posteingang landen. Ein ungültiger Zielordner (leer,
    whitespace-only oder der Posteingang selbst) ist ein lauter Fehler, kein stiller
    Fallback. Bewusst von RuntimeError abgeleitet, damit die bestehende
    `except RuntimeError`-Isolierung in agent/src/main.py::_run_cycle den Fehler pro
    Agent auffängt, statt den ganzen Prozess mitzureißen."""


def _normalize_folder_for_comparison(folder: str) -> str:
    """Trimmt, schneidet abschließende '/'/'.' ab und casefold()t für den Vergleich.
    NUR für die Gleichheitsprüfung gegen 'inbox' — der Original-Wert wird nie
    zurückgegeben."""
    return folder.strip().rstrip('/.').casefold()


def is_valid_drafts_folder(folder: str | None, inbox_folder: str = "INBOX") -> bool:
    """True, wenn `folder` als Entwürfe-Zielordner akzeptabel ist: nicht leer/whitespace
    und nicht (case-insensitiv, ohne abschließende '/'/'.') identisch mit `inbox_folder`.
    Provider-Defaults wie 'INBOX.Drafts', 'INBOX/Drafts' oder '[Gmail]/Drafts' bleiben
    gültig — es wird NUR auf exakte Gleichheit geprüft, kein Prefix-/Substring-Check."""
    if folder is None:
        return False
    stripped = folder.strip()
    if not stripped:
        return False
    normalized = _normalize_folder_for_comparison(stripped)
    if normalized == "inbox":
        return False
    if normalized == _normalize_folder_for_comparison(inbox_folder):
        return False
    return True


def ensure_valid_drafts_folder(folder: str | None, inbox_folder: str = "INBOX") -> str:
    """Gibt den getrimmten Ordnernamen zurück oder wirft InvalidDraftsFolder mit einer
    Meldung, die den abgelehnten Wert nennt. Harte Grenze VOR jedem IMAP-APPEND/CREATE."""
    if not is_valid_drafts_folder(folder, inbox_folder=inbox_folder):
        raise InvalidDraftsFolder(
            f"Ungültiger Entwürfe-Ordner: {folder!r}. Ein Entwurf darf nicht im "
            f"Posteingang ({inbox_folder!r}) oder in einem leeren Ordner landen."
        )
    return folder.strip()
