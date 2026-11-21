"""IMAP-Client Wrapper. Login, INBOX-Fetch, Drafts-APPEND."""
from __future__ import annotations

import email
import logging
from datetime import datetime, timedelta
from typing import Iterator, Optional

from imap_tools import MailBox, MailBoxUnencrypted, MailMessage, MailMessageFlags, AND
from imap_tools import OR
from imap_tools.query import H
from imap_tools.errors import MailboxAppendError, MailboxMoveError
from imap_tools.utils import check_command_status, encode_folder

from .config import Config
from .provider_config import InvalidDraftsFolder, ensure_valid_drafts_folder, is_valid_drafts_folder


class ImapClient:
    def __init__(
        self,
        config: Config,
        logger: Optional[logging.Logger] = None,
        timeout: Optional[float] = None,
    ):
        """timeout: Socket-Timeout in Sekunden für die IMAP-Verbindung (T-05-29).

        Begrenzt einen hängenden Agenten im Multi-Account-Zyklus, damit dieser nicht
        die Verarbeitung der übrigen Agenten blockiert. LLM-SDKs bringen ihre eigenen
        HTTP-Timeouts mit — dieser Parameter betrifft ausschließlich die IMAP-Socket-Ebene.
        """
        self.config = config
        self.logger = logger or logging.getLogger("vizpatch.imap")
        self.timeout = timeout
        self._mailbox: Optional[MailBox] = None

    def __enter__(self) -> "ImapClient":
        if self.config.imap_use_ssl:
            self._mailbox = MailBox(host=self.config.imap_host, port=self.config.imap_port, timeout=self.timeout)
        else:
            self._mailbox = MailBoxUnencrypted(host=self.config.imap_host, port=self.config.imap_port, timeout=self.timeout)
        self._mailbox.login(self.config.imap_user, self.config.imap_password, initial_folder=self.config.imap_inbox_folder)
        self.logger.info("imap_connected", extra={"host": self.config.imap_host, "user": self.config.imap_user})
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._mailbox is not None:
            try:
                self._mailbox.logout()
            except Exception as e:
                self.logger.warning("imap_logout_failed", extra={"error": str(e)})
            self._mailbox = None

    def fetch_new_messages(self, since: datetime, own_address: str) -> Iterator[MailMessage]:
        """Fetch INBOX messages since `since`, excluding messages from own_address."""
        assert self._mailbox is not None, "Use inside 'with' block"
        self._mailbox.folder.set(self.config.imap_inbox_folder)
        criteria = AND(date_gte=since.date())
        for msg in self._mailbox.fetch(criteria, mark_seen=False, reverse=False):
            if msg.from_ and msg.from_.lower() == own_address.lower():
                continue
            yield msg

    def detect_drafts_folder(self) -> Optional[str]:
        """Auto-Discovery via IMAP SPECIAL-USE (RFC 6154).
        Sucht nach einem Ordner mit \\Drafts-Flag. Gibt None zurück wenn der
        Server das Feature nicht announciert oder kein Draft-Ordner markiert ist.
        """
        assert self._mailbox is not None, "Use inside 'with' block"
        try:
            for folder_info in self._mailbox.folder.list():
                flags = tuple(str(f) for f in (folder_info.flags or ()))
                if any("Drafts" in f for f in flags):
                    self.logger.info(
                        "drafts_folder_detected_via_special_use",
                        extra={"folder": folder_info.name, "flags": flags},
                    )
                    return folder_info.name
        except Exception as e:
            self.logger.warning("special_use_detection_failed", extra={"error": str(e)})
        return None

    def append_to_drafts(self, raw_msg_bytes: bytes, verify: bool = False) -> None:
        """APPEND mit Auto-CREATE-Fallback bei fehlendem Drafts-Ordner (D-25).

        verify (Default False): wenn True, läuft nach dem erfolgreichen APPEND eine
        Nachkontrolle (siehe _verify_draft_in_folder). Die dafür nötige Message-ID
        wird BEWUSST hier aus `raw_msg_bytes` extrahiert (Review 260731-s49) und
        nicht vom Aufrufer entgegengenommen: der einzige Aufrufer (_poll_once) hält
        die Message-ID der ORIGINAL-Mail (für das State-Dedupe) — der Draft trägt
        aber eine NEUE Message-ID aus make_msgid(). Eine durchgereichte Original-ID
        würde im Drafts-Ordner nie gefunden und die Nachkontrolle zum
        Dauer-Fehlalarm machen. Keine extrahierbare Message-ID -> Nachkontrolle
        wird mit Warnung übersprungen, die Ablage selbst ist davon unberührt.
        """
        assert self._mailbox is not None, "Use inside 'with' block"
        message_id = ""
        if verify:
            try:
                message_id = str(email.message_from_bytes(raw_msg_bytes).get("Message-ID") or "").strip()
            except Exception:
                message_id = ""
            if not message_id:
                self.logger.warning("draft_verify_skipped_no_message_id", extra={})
        # Letzte Verteidigungslinie (BUG-2): ein Entwurf im Posteingang ist schlimmer
        # als ein ausgebliebener Entwurf — der Zielordner wird VOR jedem IMAP-Aufruf
        # (auch vor dem Auto-CREATE-Fallback) geprüft. Ab hier durchgehend die lokale
        # `folder`-Variable statt self.config.imap_drafts_folder verwenden.
        folder = ensure_valid_drafts_folder(
            self.config.imap_drafts_folder, inbox_folder=self.config.imap_inbox_folder
        )
        try:
            self._mailbox.append(
                raw_msg_bytes,
                folder=folder,
                flag_set=[MailMessageFlags.DRAFT],
            )
            self.logger.info("draft_appended", extra={"folder": folder})
        except MailboxAppendError as err:
            err_lower = str(err).lower()
            is_missing = any(p in err_lower for p in (
                "[trycreate]", "does not exist", "no such mailbox",
                "non-existent", "trying to append to non-existent mailbox",
            ))
            if not is_missing:
                raise  # anderer Fehler (Auth, Quota) — nicht self-heilen
            self.logger.warning("drafts_folder_missing_creating", extra={"folder": folder})
            self._mailbox.folder.create(folder)
            self.logger.info("drafts_folder_created", extra={"folder": folder})
            # Retry — MailboxFolderCreateError propagiert unbehandelt
            self._mailbox.append(
                raw_msg_bytes,
                folder=folder,
                flag_set=[MailMessageFlags.DRAFT],
            )
            self.logger.info("draft_appended", extra={"folder": folder})
            if message_id:
                self._verify_draft_in_folder(folder, message_id)
            return
        if message_id:
            self._verify_draft_in_folder(folder, message_id)

    def _verify_draft_in_folder(self, folder: str, message_id: str) -> None:
        """Nachkontrolle nach dem APPEND: bestätigt anhand der Message-ID, dass der
        Entwurf tatsächlich im Zielordner liegt (T-qjr-03, Repudiation-Mitigation).
        Loggt AUSSCHLIESSLICH Ordner, Message-ID und Ergebnis — nie Betreff, Absender
        oder Body. Ein fehlgeschlagener Verifikations-Fetch bricht die Ablage NICHT ab
        (die Ablage selbst gilt als erfolgt, sobald der APPEND quittiert wurde).

        Kosten: ein zusätzlicher SEARCH pro tatsächlich erzeugtem Entwurf (nicht pro
        geprüfter Mail) bei 5-Minuten-Poll — vertretbar (T-qjr-05).
        """
        assert self._mailbox is not None, "Use inside 'with' block"
        try:
            self._mailbox.folder.set(folder)
            found = False
            for _ in self._mailbox.fetch(
                AND(header=H("Message-ID", message_id)), mark_seen=False, limit=1
            ):
                found = True
                break
            if found:
                self.logger.info(
                    "draft_append_verified", extra={"folder": folder, "message_id": message_id}
                )
            else:
                self.logger.error(
                    "draft_not_in_target_folder", extra={"folder": folder, "message_id": message_id}
                )
        except Exception as e:
            self.logger.warning(
                "draft_append_verification_failed",
                extra={"folder": folder, "message_id": message_id, "error": str(e)},
            )
        finally:
            # INBOX wiederherstellen, exakt das Muster aus fetch_thread_history —
            # ohne das bricht der äußere fetch_new_messages-Generator.
            try:
                self._mailbox.folder.set(self.config.imap_inbox_folder)
            except Exception:
                pass

    def fetch_thread_history(
        self, references: list[str], max_messages: int = 6
    ) -> list[MailMessage]:
        """Sucht INBOX + Sent nach Thread-Messages via In-Reply-To / References."""
        assert self._mailbox is not None, "Use inside 'with' block"
        if not references:
            return []
        results: list[MailMessage] = []

        for folder in [self.config.imap_inbox_folder, self.config.imap_sent_folder]:
            try:
                self._mailbox.folder.set(folder)
            except Exception:
                self.logger.warning("history_folder_not_found", extra={"folder": folder})
                continue
            for ref_id in references:
                q = OR(
                    AND(header=H("In-Reply-To", ref_id)),
                    AND(header=H("References", ref_id)),
                )
                try:
                    for msg in self._mailbox.fetch(q, mark_seen=False, charset="UTF-8"):
                        results.append(msg)
                except Exception:
                    self.logger.warning("history_search_failed", extra={"folder": folder})

        # INBOX wiederherstellen damit der äußere fetch_new_messages-Generator stabil bleibt
        try:
            self._mailbox.folder.set(self.config.imap_inbox_folder)
        except Exception:
            pass

        # Chronologisch sortieren, Message-ID-Dedup
        seen_ids: set[str] = set()
        unique: list[MailMessage] = []
        for msg in sorted(results, key=lambda m: m.date or datetime.min):
            mid = (msg.headers.get("message-id") or [""])[0] or str(msg.uid)
            if mid not in seen_ids:
                seen_ids.add(mid)
                unique.append(msg)
        return unique[-max_messages:]

    # --- Automatisches Einsortieren beim Polling (Phase 14, AUTO-01..08) ---

    # SPECIAL-USE-Rollen (RFC 6154), die als Sortier-ZIEL NIE erlaubt sind
    # (AUTO-07) — 1:1 dieselbe Schutzliste wie webui/src/chat_tools.py
    # _PROTECTED_FOLDER_ROLES (Ordnerlöschen/-umbenennen, Phase 13). Eigene
    # Konstante statt Import: chat_tools.py gehört zum webui-Service, der
    # Agent-Container darf davon nicht abhängen.
    _PROTECTED_FOLDER_ROLES = frozenset(
        {"inbox", "drafts", "sent", "trash", "junk", "archive", "all", "flagged", "important"}
    )

    def resolve_protected_folder_names(self) -> set[str]:
        """Liefert alle Ordnernamen (casefold), die als Sortier-ZIEL gesperrt
        sind (AUTO-07): der konfigurierte Posteingang/Entwürfe/Gesendet-Ordner
        UND jeder Ordner, den der Server per SPECIAL-USE als Posteingang/
        Entwürfe/Gesendet/Papierkorb/Spam/Archiv/etc. kennzeichnet. Ein
        LIST-Fehler wird geloggt und führt NICHT zum Absturz — die
        konfigurierten Namen bleiben in jedem Fall gesperrt (Fail-Closed:
        weniger erkannte Schutz-Ordner sind sicherer als ein IMAP-Fehler,
        der die Schutzprüfung ganz ausfallen lässt)."""
        assert self._mailbox is not None, "Use inside 'with' block"
        names: set[str] = {
            "INBOX",
            self.config.imap_inbox_folder,
            self.config.imap_drafts_folder,
            self.config.imap_sent_folder,
        }
        try:
            for folder_info in self._mailbox.folder.list():
                flags = tuple(str(f).lstrip("\\") for f in (getattr(folder_info, "flags", None) or ()))
                if any(f.casefold() in self._PROTECTED_FOLDER_ROLES for f in flags):
                    names.add(str(getattr(folder_info, "name", "") or ""))
        except Exception as e:
            self.logger.warning("auto_sort_protected_folder_list_failed", extra={"error": str(e)})
        return {n.casefold() for n in names if n}

    def fetch_seen_candidates(self, limit: int) -> list[MailMessage]:
        """Fetch bereits GELESENER Mails in INBOX (AUTO-03: nur gelesene
        Mails kommen für das automatische Einsortieren überhaupt infrage) —
        gedeckelt auf `limit` (AUTO-06). `mark_seen=False` ist hier Pflicht,
        nicht nur Stil (harte Auflage #9): der Sortierlauf selbst darf NIE
        eine Mail als gelesen markieren, sonst würde er sich seine eigene
        AUTO-03-Vorbedingung für eine bis dahin ungelesene Mail selbst
        erzeugen.

        `reverse=True` ist hier eine FACHLICHE Entscheidung, kein Stil (Befund
        der Live-Abnahme vom 2026-08-20, gefixt in Quick 260822-4ay): mit
        `reverse=False` las der Lauf von der ÄLTESTEN Mail her. Bei 3385 Mails
        im Postfach und `limit=50` wäre eine heute eingetroffene Mail erst nach
        ~68 Zyklen an der Reihe — der Sortierlauf hätte am Archiv gearbeitet
        statt am Tagesgeschäft. `mails_sichten` im Chat
        (webui/src/chat_tools.py) fetcht seit Phase 13 aus genau diesem Grund
        mit `reverse=True`; der Sortierlauf war die Ausnahme."""
        assert self._mailbox is not None, "Use inside 'with' block"
        self._mailbox.folder.set(self.config.imap_inbox_folder)
        return list(
            self._mailbox.fetch(AND(seen=True), mark_seen=False, reverse=True, limit=limit)
        )

    def fetch_urlaub_candidates(self, since: datetime, limit: int) -> list[MailMessage]:
        """Kandidaten fuer die Urlaubs-Autoantwort: INBOX-Mails ab `since`,
        neueste zuerst, gedeckelt.

        `mark_seen=False` ist hier — wie bei `fetch_seen_candidates` — Pflicht
        und nicht Stil: der Urlaubslauf darf keine Mail als gelesen markieren.
        Sonst kaeme der Betreiber aus dem Urlaub zurueck in ein Postfach, in
        dem alles schon "gesehen" aussieht.

        `reverse=True` aus demselben Grund wie beim Sortierlauf (Befund der
        Live-Abnahme vom 2026-08-20): mit `reverse=False` liefe der Deckel am
        Archiv statt am Tagesgeschaeft.

        `AND(date_gte=...)` arbeitet auf TAGES-Granularitaet (IMAP SEARCH kennt
        keine Uhrzeit) — die Feinauswahl macht der Aufrufer ohnehin ueber die
        Merkliste, hier geht es nur darum, nicht das ganze Postfach zu ziehen.
        """
        assert self._mailbox is not None, "Use inside 'with' block"
        self._mailbox.folder.set(self.config.imap_inbox_folder)
        return list(
            self._mailbox.fetch(
                AND(date_gte=since.date()), mark_seen=False, reverse=True, limit=limit
            )
        )

    def _raw_uid_move(self, uid: str, target_folder: str) -> None:
        """Ein rohes `UID MOVE` (RFC 6851) — NIE `MailBox.move()`.

        `MailBox.move()` prüft `'MOVE' in client.capabilities` und fällt sonst
        STILL auf `copy()` + `delete()` zurück; `delete()` hängt (wie
        `MailBox.flag()`) ein folder-weites EXPUNGE an und würde damit FREMDE,
        von anderen Mail-Programmen `\\Deleted`-markierte Nachrichten im
        Quellordner endgültig entfernen. Genau dieselbe Härtung wie bei
        `mark_important` (rohes `UID STORE`) und `_store_flags` in
        `webui/src/chat_tools.py` (Phase 13, Plan 13-01).

        Der rohe Befehl geht ohne Capability-Vorprüfung raus. Beherrscht der
        Server MOVE nicht wirklich, antwortet er mit `NO`/`BAD` und
        `check_command_status` wirft `MailboxMoveError` — es gibt keinen
        stillen COPY+delete-Weg, das Risiko bleibt also ausgeschlossen.

        Befund der Live-Abnahme vom 2026-08-19: Gmail führt `MOVE` NICHT in
        seiner CAPABILITY-Antwort, beherrscht `UID MOVE` aber. Die vorherige
        Vorprüfung auf die Capability-Liste hat deshalb an einem Gmail-Postfach
        JEDE Verschiebung abgelehnt — die Kategorie-Aktion `verschieben` war
        dort vollständig wirkungslos, sichtbar nur als Log-Warnung."""
        assert self._mailbox is not None, "Use inside 'with' block"
        result = self._mailbox.client.uid("MOVE", str(uid), encode_folder(target_folder))
        check_command_status(result, MailboxMoveError)

    def move_message(self, uid: str, source_folder: str, target_folder: str) -> bool:
        """Verschiebt EINE Mail nach `target_folder` — Auto-CREATE-Fallback
        wie bei `append_to_drafts`, Move über `_raw_uid_move` (siehe dort:
        rohes `UID MOVE`, nie der COPY+delete-Fallback von imap-tools).

        Gibt True bei ausgeführtem Move zurück, False wenn der Server das
        Verschieben verweigert hat — die Mail bleibt dann unangetastet liegen,
        nichts wird gelöscht."""
        assert self._mailbox is not None, "Use inside 'with' block"
        self._mailbox.folder.set(source_folder)
        try:
            self._raw_uid_move(uid, target_folder)
        except MailboxMoveError as err:
            err_lower = str(err).lower()
            is_missing = any(
                p in err_lower
                for p in ("[trycreate]", "does not exist", "no such mailbox", "non-existent")
            )
            if not is_missing:
                # Nur ein `BAD` heißt "Server kennt UID MOVE nicht" — dann
                # überspringen statt auf den expunge-behafteten COPY+delete-
                # Weg auszuweichen. Jeder andere Fehler (Quota, Berechtigung,
                # Netz) fliegt weiter wie bisher und landet beim Aufrufer als
                # `auto_sort_verschieben_failed`; ihn hier zu schlucken würde
                # ein echtes Problem als "nicht unterstützt" tarnen.
                if "bad" not in err_lower:
                    raise
                self.logger.warning(
                    "auto_sort_move_unsupported",
                    extra={
                        "uid": uid,
                        "source_folder": source_folder,
                        "target_folder": target_folder,
                        "error": str(err),
                    },
                )
                return False
            self.logger.warning("auto_sort_target_folder_missing_creating", extra={"folder": target_folder})
            self._mailbox.folder.create(target_folder)
            self._mailbox.folder.set(source_folder)
            self._raw_uid_move(uid, target_folder)
        self.logger.info(
            "auto_sort_moved",
            extra={"uid": uid, "source_folder": source_folder, "target_folder": target_folder},
        )
        return True

    def mark_important(self, uid: str) -> None:
        """Setzt \\Flagged ("wichtig") für EINE Mail über ein rohes `UID
        STORE` — ausdrücklich NICHT über `MailBox.flag()`, das laut
        installiertem imap-tools nach jedem STORE ein folder-weites
        `expunge()` anhängt (dieselbe Härtung wie `_store_flags` in
        `webui/src/chat_tools.py`, Phase 13, Plan 13-01). Rein additiv: bewegt
        nichts, löscht nichts, kann auch UNGELESENE Mails treffen (AUTO-03
        gilt nur für 'verschieben')."""
        assert self._mailbox is not None, "Use inside 'with' block"
        self._mailbox.client.uid("STORE", str(uid), "+FLAGS", f"({MailMessageFlags.FLAGGED})")
        self.logger.info("auto_sort_marked_important", extra={"uid": uid})

    def fetch_sender_history(
        self, from_address: str, days: int = 30, max_messages: int = 6
    ) -> list[MailMessage]:
        """Absender-Fallback: FROM x in INBOX, TO x in Sent, max 30 Tage."""
        assert self._mailbox is not None, "Use inside 'with' block"
        since = (datetime.utcnow() - timedelta(days=days)).date()
        results: list[MailMessage] = []
        for folder, query in [
            (self.config.imap_inbox_folder, AND(from_=from_address, date_gte=since)),
            (self.config.imap_sent_folder,  AND(to=from_address,   date_gte=since)),
        ]:
            try:
                self._mailbox.folder.set(folder)
                for msg in self._mailbox.fetch(query, mark_seen=False, charset="UTF-8"):
                    results.append(msg)
            except Exception:
                self.logger.warning("history_fetch_failed", extra={"folder": folder})

        # INBOX wiederherstellen damit der äußere fetch_new_messages-Generator stabil bleibt
        try:
            self._mailbox.folder.set(self.config.imap_inbox_folder)
        except Exception:
            pass

        seen_ids: set[str] = set()
        unique: list[MailMessage] = []
        for msg in sorted(results, key=lambda m: m.date or datetime.min):
            mid = (msg.headers.get("message-id") or [""])[0] or str(msg.uid)
            if mid not in seen_ids:
                seen_ids.add(mid)
                unique.append(msg)
        return unique[-max_messages:]
