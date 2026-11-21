import io
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from . import agents_io, auth, chat, chat_history, chat_tools, config_io, crypto, docker_ctrl, llm_detect, llm_seed, state_reader, style_extract, timefmt, update_check, update_dist, update_status, urlaub_relay, validate_conn
from .logging_setup import setup_logging

setup_logging(os.getenv("LOG_LEVEL", "INFO"))

logger = logging.getLogger(__name__)


def _rate_limit_key(request: Request) -> str:
    """WR-05: slowapi-Schluessel ueber die Trusted-Proxy-bewusste Client-IP statt
    `get_remote_address` (das blind `request.client.host` nimmt). Hinter einem
    konfigurierten `TRUSTED_PROXY` wird so die echte Client-IP aus X-Forwarded-For
    genutzt (sonst teilen sich alle Clients die Proxy-IP = Selbst-DoS)."""
    return auth.client_ip(request)


limiter = Limiter(key_func=_rate_limit_key)

app = FastAPI(title="Vizpatch WebUI", version="1.2.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
templates = Jinja2Templates(directory="src/templates")
# Zeitanzeige in deutscher Ortszeit (MEZ/MESZ) — {{ wert | localzeit }}.
templates.env.filters["localzeit"] = timefmt.to_local_str
app.mount("/static", StaticFiles(directory="static"), name="static")

PROVIDER_LABELS = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google"}

# Review IN-04: serverseitige Obergrenze fuer context.md/style.md ueber /save.
MAX_CONFIG_MD_BYTES = 64 * 1024

# Phase 12 (ATT-01, D-94): Default-Obergrenze fuer Chat-Datei-Uploads (Rohgroesse,
# NICHT die spaeter base64-aufgeblähte Anhang-Groesse — siehe 12-RESEARCH.md Pitfall 4).
# Ueberschreibbar via MAX_ATTACHMENT_MB (agent/docker-compose.yml, globales Muster wie
# CHAT_MAX_TOKENS, Assumption A1 — kein per-Agent-Wert).
MAX_ATTACHMENT_MB_DEFAULT = 15

# tmp-Zielverzeichnis fuer Chat-Uploads (Streaming-Ziel vor der Pending-Upload-
# Registrierung, chat_tools.register_pending_upload). Container-lokal, kein
# /data-Volume-Bezug (Assumption A3, Single-Container-Deployment).
_CHAT_UPLOAD_TMP_DIRNAME = "vizpatch-uploads"

# Datenschutz-Zustimmung (D-68): Stand der Bestimmungen, mit denen ein
# gegebenes PRIVACY_CONSENT_ACCEPTED=true korrespondiert (siehe _datenschutz.html).
PRIVACY_CONSENT_VERSION = "2026-07-17"
_CONSENT_TRUTHY = {"true", "on", "1"}

# Add-in-/Embed-Pfad-Klassifikation (Phase 8, D-66/T-08-02): NUR diese Pfade
# bekommen eine gelockerte CSP (Outlook muss die Taskpane + das darin
# geschachtelte Chat-Embed iframen können). Alle anderen Pfade bleiben strikt.
_ADDIN_EMBED_PATH_RE = re.compile(r"^/chat/[^/]+/embed$")

DEFAULT_ADDIN_FRAME_ANCESTORS = (
    "'self' https://outlook.office.com https://outlook.office365.com "
    "https://outlook.live.com https://outlook-sdf.office.com "
    "https://*.office.com https://*.office365.com"
)

# T-08-06: ADDIN_BASE_URL wird per Textreplace ins Manifest-XML eingesetzt —
# vor dem Einsetzen validiert (https://-Pflicht, keine XML-Sonderzeichen),
# sonst waere eine XML-Injection ueber einen fehlerhaft/boesartig gesetzten
# Env-Wert moeglich.
_XML_SPECIAL_CHARS_RE = re.compile(r'[<>"&]')
DEFAULT_ADDIN_BASE_URL = "https://CHANGE-ME.example"


def _is_addin_embeddable_path(path: str) -> bool:
    return path.startswith("/addin/") or bool(_ADDIN_EMBED_PATH_RE.match(path))


# --- CSRF-/Same-Origin-Enforcement (Review CR-01) ---
# Session-lose, zu Basic-Auth (keine Cookies) passende Abwehr: fuer jede unsichere
# Methode muss die Anfrage same-origin sein (Origin bevorzugt, sonst Referer-Host
# == Host). Fehlen beide -> abgelehnt. Ein echter Browser sendet bei Form-POSTs
# (auch cross-site) IMMER Origin bzw. Referer — nur ein CSRF-Angriff von einer
# fremden Seite traegt eine fremde Origin/Referer.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Add-in-Chat-Pfade (Phase 8): NUR diese duerfen zusaetzlich von den
# konfigurierten Office/Outlook-Origins cross-origin angesprochen werden — sonst
# funktioniert das Outlook-Add-in (Taskpane iframed das Chat-Embed und postet
# nach /chat/{id}/send) nicht. Alle anderen state-aendernden Routen bleiben
# strikt same-origin.
# `upload` gehoert dazu (260731-u3m Nachtrag): der Drag&Drop-Anhang des VSTO-
# Add-ins postet nach /chat/{id}/upload — ohne den Eintrag lief der Upload in
# 401 (Session-Gate via _is_addin_session_exempt_path nutzt DENSELBEN Ausdruck),
# waehrend send/embed funktionierten. Schutzniveau bleibt identisch zu send:
# Origin-Allowlist + require_setup + Rate-Limit.
# `anhang` (Quick 260813-wea, Task 1): der neue Bild-Vorschau-Endpoint ist
# GET-only und damit fuer `enforce_same_origin` ohnehin eine sichere Methode
# (der Origin-Check greift nur fuer nicht-sichere Methoden) — die Erweiterung
# dient hier ausschliesslich der `_is_addin_session_exempt_path`-Ausnahme
# (dasselbe VSTO-WebBrowser-Control traegt keinen Session-Cookie). Der
# Rest-Schutz bleibt `require_setup` + Rate-Limit + strikte Bildbeschraenkung.
_ADDIN_CHAT_PATH_RE = re.compile(r"^/chat/[^/]+/(send|embed|upload|anhang)$")

# 260725-tqr: Add-in-Einstellungs-Endpoints (aktuell der Auto-Entwurf-Schalter).
# Zustandsändernde POSTs kommen aus dem VSTO-Add-in mit fremdem Office/Outlook-
# Origin und müssen — wie der Chat-Pfad und /addin/verify-password — in der
# Origin-Allowlist von enforce_same_origin stehen, sonst 403. Das GET ist eine
# sichere Methode und wird ohnehin nicht origin-geprüft.
# Add-in-Einstellungspfade, die aus der Outlook-Origin erreichbar sein muessen.
# `urlaub` kam mit der Urlaubs-Autoantwort dazu (URL-01..09): die Freigabe ist
# bewusst nicht per Chat schaltbar, und der Kunde arbeitet nur in Outlook —
# ohne diesen Pfad koennte er die Funktion gar nicht einschalten.
_ADDIN_SETTINGS_PATH_RE = re.compile(
    r"^/addin/agents/[^/]+/(draft-enabled|urlaub|intelligenz)$"
)

# 260728-m8g (Stufe 2): der destruktive Self-Update-Trigger kommt ebenfalls
# aus dem Add-in mit fremder Office/Outlook-Origin — ohne diesen Eintrag
# laeuft der POST in 403, VOR jeder Auth-Pruefung (require_basic_password
# wuerde nie erreicht).
_ADDIN_UPDATE_PATH = "/addin/update"


def _addin_allowed_origins() -> list[str]:
    """Leitet die erlaubten Add-in-Origins aus ADDIN_FRAME_ANCESTORS (bzw. dem
    Default) ab — dieselben `https://…`-Hosts, die auch in der gelockerten CSP
    das Framing durch Outlook erlauben. `'self'` wird verworfen (Same-Origin ist
    ohnehin generell erlaubt)."""
    # Ein gesetzter, aber LEERER Env-Wert (z. B. `ADDIN_FRAME_ANCESTORS: ${...:-}`
    # aus der Compose) darf den Default NICHT überschreiben — sonst wären gar keine
    # Add-in-Origins erlaubt und jeder Add-in-Request liefe in 403. `or`-Fallback +
    # strip() fangen leere/whitespace-Werte ab.
    raw = (os.getenv("ADDIN_FRAME_ANCESTORS") or "").strip() or DEFAULT_ADDIN_FRAME_ANCESTORS
    return [tok for tok in raw.split() if tok.startswith("https://")]


def _origin_pattern_match(origin: str, pattern: str) -> bool:
    """Vergleicht eine Origin (`https://host`) gegen ein Frame-Ancestor-Pattern.
    Exakter Treffer oder Wildcard-Subdomain (`https://*.office.com` deckt jede
    echte Subdomain von office.com ab, NICHT office.com selbst)."""
    if origin == pattern:
        return True
    scheme, _, host = origin.partition("://")
    p_scheme, _, p_host = pattern.partition("://")
    if scheme != p_scheme or not host:
        return False
    if p_host.startswith("*."):
        suffix = p_host[1:]  # ".office.com"
        return host.endswith(suffix) and len(host) > len(suffix)
    return False


def _origin_allowed_for_addin(origin: str) -> bool:
    if not origin:
        return False
    return any(_origin_pattern_match(origin, pat) for pat in _addin_allowed_origins())


# Container-interner Relay-Pfad der Urlaubs-Autoantwort (URL-01..09). EXAKTES
# Muster, bewusst NICHT `startswith("/intern/")` — eine Praefix-Ausnahme wuerde
# jeden kuenftigen Pfad unter `/intern/` still mit ausnehmen.
_URLAUB_RELAY_PATH_RE = re.compile(r"^/intern/urlaubsantwort/[a-z0-9-]{1,64}$")


def _is_urlaub_relay_path(path: str) -> bool:
    """Dieser eine Pfad ist von ZWEI Browser-Schutzmechanismen ausgenommen —
    dem Session-Gate (`enforce_auth`) und der CSRF-Abwehr
    (`enforce_same_origin`). Der Grund ist bei beiden derselbe: der Aufrufer
    ist der agent-Container, kein Browser. Er hat kein Session-Cookie und kann
    keins bekommen, und er sendet weder `Origin` noch `Referer`.

    Das ist KEINE Aufweichung, sondern eine Verschiebung der Autorisierung an
    eine Stelle, die fuer diesen Aufrufer ueberhaupt greifen kann:

    * **Statt Session-Cookie** eine HMAC-Signatur ueber den Request-Body, mit
      einem aus dem gemeinsamen Fernet-Key (`/config/.secret_key`) abgeleiteten,
      ZWECKGEBUNDENEN Schluessel plus Zeitstempel-Frische gegen Replay.
      `urlaub_relay.pruefe_und_sende` faellt ohne gueltige Signatur ab, bevor es
      eine Datei anfasst.
    * **Statt CSRF-Origin-Pruefung** dieselbe Signatur: die Sorge der
      CSRF-Abwehr ist, dass der Browser eines eingeloggten Betreibers eine
      fremde Anfrage MIT SEINEN COOKIES ausfuehrt. Dieser Pfad autorisiert aber
      gar nicht ueber Cookies — eine fremde Seite gewinnt nichts, weil sie den
      Fernet-Key nicht hat und die Signatur nicht erzeugen kann.
    * **Zusaetzlich fail-closed** am Agenten-Flag `ENABLE_URLAUBSANTWORT` und am
      eingetragenen Urlaubszeitraum.

    Muster wie `_is_addin_session_exempt_path`, nur enger (ein Pfad, ein Zweck).
    """
    return bool(_URLAUB_RELAY_PATH_RE.match(path))


@app.middleware("http")
async def enforce_same_origin(request: Request, call_next):
    """Review CR-01: CSRF-Abwehr fuer alle zustandsaendernden Requests. Same-Origin
    ist fuer ALLE Pfade erlaubt; die konfigurierten Add-in-Origins zusaetzlich NUR
    fuer die Add-in-Chat-Pfade (`POST /chat/{id}/send` bzw. das Embed). Alle
    uebrigen state-aendernden Routen (`/save`, `/reset`, `/agents*`, `/agent/*`,
    `/context/generate`, `/style/relearn`) bleiben strikt same-origin."""
    if request.method not in _SAFE_METHODS:
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        referer = request.headers.get("referer")
        ok = False
        if origin:
            ok = urlparse(origin).netloc == host
        elif referer:
            ok = urlparse(referer).netloc == host
        # Add-in-Ausnahme (Origin-basiert): fremde Office/Outlook-Origins duerfen
        # AUSSCHLIESSLICH den Add-in-Chat-Pfad ansprechen.
        if not ok and origin and (
            _ADDIN_CHAT_PATH_RE.match(request.url.path)
            or request.url.path == "/addin/verify-password"
            or request.url.path == _ADDIN_UPDATE_PATH
            or _ADDIN_SETTINGS_PATH_RE.match(request.url.path)
        ):
            ok = _origin_allowed_for_addin(origin)
        # Container-interner Relay: kein Browser, kein Origin, keine
        # Cookie-Autorisierung — siehe `_is_urlaub_relay_path`.
        if not ok and _is_urlaub_relay_path(request.url.path):
            ok = True
        if not ok:
            return PlainTextResponse("cross-origin request rejected", status_code=403)
    return await call_next(request)


_PUBLIC_PATHS = {"/login", "/logout", "/setup", "/healthz"}


def _is_public_path(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith("/static/")


def _is_addin_session_exempt_path(path: str) -> bool:
    """Add-in-Pfade (T-jrq-06, 260722-jrq): das VSTO-WebBrowser-Control kann per
    Design keinen SameSite=Strict-Session-Cookie tragen (kein geteilter Cookie-Jar
    mit dem Browser, in dem der Betreiber eingeloggt ist) -- deshalb vom
    Session-Gate ausgenommen. Rest-Schutz bleibt ueber `require_setup` (Passwort
    muss gesetzt sein, Route-Dependency) + `enforce_same_origin` (Office-Origin-
    Allowlist fuer `/chat/*/send`)."""
    return path.startswith("/addin/") or bool(_ADDIN_CHAT_PATH_RE.match(path))


@app.middleware("http")
async def enforce_auth(request: Request, call_next):
    """Session-Login-Gate (D-03, 260722-jrq): ersetzt die vormalige
    Basic-Auth-Durchsetzung in `auth.require_auth`. Oeffentliche Pfade sowie die
    Add-in-Session-Ausnahme laufen ungegatet durch die Middleware (dort greift
    `require_setup` als Route-Dependency); alle uebrigen Pfade brauchen ein
    gesetztes WEBUI_PASSWORD UND eine gueltige Session (Cookie
    `auth.SESSION_COOKIE_NAME`). Ohne Passwort: Redirect auf `/setup` (Erststart-
    Zwang). Mit Passwort, aber ohne gueltige Session: volle Seiten-GETs (kein
    `HX-Request`-Header) werden auf `/login` umgeleitet, alle uebrigen
    (HTMX/POST/API) erhalten 401 -- das bestehende
    `hx-on::response-error 401 -> location.reload()` in base.html fuehrt
    HTMX-Polls dann sauber zum Login."""
    path = request.url.path
    if (
        _is_public_path(path)
        or _is_addin_session_exempt_path(path)
        or _is_urlaub_relay_path(path)
    ):
        return await call_next(request)

    if not auth.password_is_set():
        return RedirectResponse("/setup", status_code=303)

    token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if auth.session_valid(token):
        return await call_next(request)

    if request.method == "GET" and request.headers.get("HX-Request") != "true":
        return RedirectResponse("/login", status_code=303)
    return PlainTextResponse("Unauthorized", status_code=401)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"

    if _is_addin_embeddable_path(path):
        # Gelockerte Policy NUR für /addin/-Seiten + /chat/*/embed (T-08-01/T-08-02):
        # X-Frame-Options entfaellt komplett (kann nur DENY/SAMEORIGIN, wuerde
        # Cross-Origin-Framing durch Outlook blockieren) — die Kontrolle liegt bei
        # frame-ancestors, das per Default NUR 'self' + feste Office/Outlook-Origins
        # zulaesst (kein `*`-Wildcard, Clickjacking-Schutz).
        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]
        frame_ancestors = (os.getenv("ADDIN_FRAME_ANCESTORS") or "").strip() or DEFAULT_ADDIN_FRAME_ANCESTORS
        script_src = "'self' 'unsafe-inline'"
        frame_src_directive = ""
        if path.startswith("/addin/"):
            # Nur die Taskpane selbst darf office.js laden + das Embed schachteln;
            # /chat/*/embed bleibt ohne CDN-Freigabe (T-08-03).
            script_src += " https://appsforoffice.microsoft.com"
            frame_src_directive = "frame-src 'self'; "
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src {script_src}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            f"{frame_src_directive}"
            f"frame-ancestors {frame_ancestors}"
        )
    else:
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
    return response


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/_auth_check", dependencies=[Depends(auth.require_auth)])
def auth_check() -> dict:
    return {"ok": True}


# --- Session-Login: /setup /login /logout /password (260722-jrq) -----------


def _validate_new_password(password: str, password_confirm: str) -> str:
    """Gemeinsame Validierung fuer /setup und /password (neues Passwort): min. 8
    Zeichen UND beide Felder identisch. Gibt eine deutsche Fehlermeldung zurueck
    (leer = gueltig)."""
    errors: list[str] = []
    if len(password) < 8:
        errors.append("Das Passwort muss mindestens 8 Zeichen lang sein.")
    if password != password_confirm:
        errors.append("Die beiden Passwörter stimmen nicht überein.")
    return " ".join(errors)


@app.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request):
    if auth.password_is_set():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"error": ""})


@app.post("/setup", response_class=HTMLResponse)
def setup_post(
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    if auth.password_is_set():
        return RedirectResponse("/login", status_code=303)
    error = _validate_new_password(password, password_confirm)
    if error:
        return templates.TemplateResponse(
            request, "setup.html", {"error": error}, status_code=400
        )
    config_io.write_env({"WEBUI_PASSWORD": auth.hash_password(password)})
    token = auth.create_session()
    resp = RedirectResponse("/", status_code=303)
    auth.set_session_cookie(resp, token)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if not auth.password_is_set():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, password: str = Form(...)):
    if not auth.password_is_set():
        return RedirectResponse("/setup", status_code=303)
    auth._check_login_lockout(request)
    if auth.verify_password(password):
        token = auth.create_session()
        resp = RedirectResponse("/", status_code=303)
        auth.set_session_cookie(resp, token)
        return resp
    auth._record_login_failure(request)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Falsches Passwort."}, status_code=401
    )


@app.post("/logout")
def logout_post(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    auth.destroy_session(token)
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(resp)
    return resp


@app.get("/password", response_class=HTMLResponse)
def password_get(request: Request):
    """Passwort-aendern-Popup-Partial (fuer #agent-dialog-body) -- die Middleware
    verlangt hierfuer bereits eine gueltige Session (kein Public-Pfad)."""
    return templates.TemplateResponse(request, "_password_form.html", {})


def _password_response(ok: bool, message: str) -> HTMLResponse:
    """Kleines Ergebnis-Fragment fuer #password-change-result (analog
    `_save_response`). Bei Erfolg traegt die Antwort `HX-Trigger:
    vizpatch-password-changed`, auf den ein globaler Listener (index.html)
    reagiert und den #agent-dialog schliesst."""
    css = "save-ok" if ok else "save-err"
    icon = "&#10003;" if ok else "&#9888;"
    headers = {"HX-Trigger": "vizpatch-password-changed"} if ok else None
    return HTMLResponse(f'<span class="{css}">{icon} {message}</span>', headers=headers)


@app.post("/password", response_class=HTMLResponse)
def password_post(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
):
    if not auth.verify_password(current_password):
        return _password_response(False, "Aktuelles Passwort ist falsch.")
    error = _validate_new_password(new_password, new_password_confirm)
    if error:
        return _password_response(False, error)
    config_io.write_env({"WEBUI_PASSWORD": auth.hash_password(new_password)})
    return _password_response(True, "Passwort geändert.")


def _build_agent_statuses() -> list[dict]:
    """Baut die Status-Übersicht ALLER Agenten (D-50): Flag + Heartbeat + letzter Poll + fehlende Config."""
    result: list[dict] = []
    for aid in agents_io.list_agent_ids():
        enabled = agents_io.get_agent_enabled(aid)
        status_json = state_reader.get_agent_status_json(aid)
        result.append(
            {
                "id": aid,
                "enabled": enabled,
                "running": state_reader.is_running(enabled, status_json),
                "last_poll": state_reader.get_last_poll(aid),
                "status_json": status_json,
                "missing": agents_io.get_missing_config(aid),
            }
        )
    return result


def _privacy_consent_state() -> tuple[bool, str]:
    """Datenschutz-Zustimmung (D-68) lebt GLOBAL im Root-.env, nicht per Agent —
    wird sowohl von index() als auch von den neuen Popup-Partial-Routen
    (agent_edit/agent_new) benoetigt."""
    root_env_raw = config_io.read_env_raw()
    accepted = (root_env_raw.get("PRIVACY_CONSENT_ACCEPTED") or "").strip().lower() == "true"
    at = root_env_raw.get("PRIVACY_CONSENT_AT") or ""
    return accepted, at


def _autostart_enabled() -> bool:
    """Autostart-Flag (global, Root-.env). Wird in die Agenten-Uebersicht
    (_status_card.html) gerendert — daher an allen Render-Stellen der Card
    (index, /agents/status, Start/Stop, /agent/*) mitzugeben."""
    return (config_io.read_env_raw().get("AUTOSTART_ENABLED") or "").strip().lower() == "true"


def _agent_form_ctx(agent_id: str) -> dict:
    """Baut den per-Agent-Kontext fuers Config-Formular (Task 1, UMBAU-Plan
    260722-h9e): aus `index()` extrahiert, damit die neue `agent_edit`-Route
    (Popup-Partial) dieselbe Zusammenstellung wiederverwenden kann statt sie zu
    duplizieren. Secrets bleiben maskiert (`agents_io.read_env_masked`)."""
    # `read_env_masked` reicht fuer die Anzeige, aber NICHT fuer die
    # Urlaubs-Lage: die braucht die Rohwerte (die drei Urlaubs-Felder sind
    # keine Secrets und kommen unmaskiert durch, das Muster hier bleibt
    # trotzdem der Rohleser, damit die Lage-Berechnung nicht an einer kuenftig
    # geaenderten Maskierung haengt).
    roh = agents_io.read_env_raw(agent_id)
    return {
        "env": agents_io.read_env_masked(agent_id),
        "context_md": agents_io.read_context_md(agent_id),
        "style_md": agents_io.read_style_md(agent_id),
        "vorlagen_md": agents_io.read_vorlagen_md(agent_id),
        "sortierregeln_md": agents_io.read_sortierregeln_md(agent_id),
        "style_note": agents_io.read_style_note(agent_id),
        "drafts_status": state_reader.get_agent_status_json(agent_id),
        "missing": agents_io.get_missing_config(agent_id),
        # Der Haken allein sagt nicht, ob GERADE geantwortet wird — nach dem
        # letzten Urlaubstag bleibt er stehen und greift trotzdem nicht mehr
        # (der Agent hoert von selbst auf, Betreiber-Vorgabe 2026-08-23).
        "urlaub_lage": chat_tools._urlaubslage(
            (roh.get("ENABLE_URLAUBSANTWORT") or "false").strip().lower() == "true",
            (roh.get("URLAUB_VON") or "").strip(),
            (roh.get("URLAUB_BIS") or "").strip(),
        ),
    }


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    user: str = Depends(auth.require_auth),
    agent_id: str = "",
    new: int = 0,
    saved: int = 0,
    reset: int = 0,
    error: str = "",
):
    agents = agents_io.list_agent_ids()
    # `new=1` (Dropdown „-- Neuen Agent anlegen --") erzwingt die Anlege-Maske
    # statt automatisch agents[0] auszuwählen — sonst ist das Anlegen eines
    # zweiten Agenten unerreichbar, weil `/` ohne agent_id immer den ersten
    # Agenten selektiert.
    if new:
        active_id = ""
    else:
        active_id = agent_id or (agents[0] if agents else "")
    agent_statuses = _build_agent_statuses()
    privacy_consent_accepted, privacy_consent_at = _privacy_consent_state()

    if active_id and active_id in agents:
        form_ctx = _agent_form_ctx(active_id)
        env_vals = form_ctx["env"]
        context_md = form_ctx["context_md"]
        style_md = form_ctx["style_md"]
        vorlagen_md = form_ctx["vorlagen_md"]
        sortierregeln_md = form_ctx["sortierregeln_md"]
        style_note = form_ctx["style_note"]
        drafts_status = form_ctx["drafts_status"]
        missing = form_ctx["missing"]
    else:
        active_id = ""
        env_vals = {}
        context_md = ""
        style_md = ""
        vorlagen_md = ""
        sortierregeln_md = ""
        style_note = ""
        drafts_status = {}
        missing = []

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "agent_id": active_id,
            "agents": agents,
            "agent_statuses": agent_statuses,
            "env": env_vals,
            "global_env": config_io.read_env_masked(),
            "autostart_enabled": _autostart_enabled(),
            "context_md": context_md,
            "style_md": style_md,
            "vorlagen_md": vorlagen_md,
            "sortierregeln_md": sortierregeln_md,
            "style_note": style_note,
            "saved": saved,
            "reset": reset,
            "error": error,
            "service_status": docker_ctrl.get_agent_status(),
            "drafts_status": drafts_status,
            "configured": not missing,
            "missing": missing,
            "privacy_consent_accepted": privacy_consent_accepted,
            "privacy_consent_at": privacy_consent_at,
        },
    )


@app.get("/datenschutz", response_class=HTMLResponse)
def datenschutz(request: Request, user: str = Depends(auth.require_auth)):
    """Eigenständige Datenschutz-Seite (D-68) — dasselbe Fragment wie das
    aufklappbare Inline-Vorkommen in index.html, hier als Vollseite (z. B. zum
    Verlinken/Ausdrucken)."""
    return templates.TemplateResponse(request, "datenschutz.html", {})


@app.get("/agents/status", response_class=HTMLResponse)
def agents_status(request: Request, user: str = Depends(auth.require_auth)):
    return templates.TemplateResponse(
        request,
        "_status_card.html",
        {
            "agent_statuses": _build_agent_statuses(),
            "service_status": docker_ctrl.get_agent_status(),
            "autostart_enabled": _autostart_enabled(),
        },
    )


@app.get("/agents/{agent_id}/edit", response_class=HTMLResponse)
def agent_edit(request: Request, agent_id: str, user: str = Depends(auth.require_auth)):
    """Bearbeiten-Popup-Partial (UMBAU-D3): lazy per hx-get geladen, NUR fuer den
    angeklickten Agenten (kein Vorab-Rendern aller Agenten-Formulare — T-h9e-01).
    Secrets bleiben maskiert (`_agent_form_ctx` -> `read_env_masked`)."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="agent not found")
    privacy_consent_accepted, privacy_consent_at = _privacy_consent_state()
    return templates.TemplateResponse(
        request,
        "_agent_form.html",
        {
            "mode": "edit",
            "agent_id": agent_id,
            **_agent_form_ctx(agent_id),
            "privacy_consent_accepted": privacy_consent_accepted,
            "privacy_consent_at": privacy_consent_at,
        },
    )


@app.get("/agents/new", response_class=HTMLResponse)
def agent_new(request: Request, user: str = Depends(auth.require_auth)):
    """Anlege-Popup-Partial (UMBAU-D5): dasselbe Formular wie `agent_edit`, aber
    leer + mit Namensfeld — der eigentliche Agent wird erst beim Speichern
    (POST /save mit new_agent_name) angelegt."""
    privacy_consent_accepted, privacy_consent_at = _privacy_consent_state()
    return templates.TemplateResponse(
        request,
        "_agent_form.html",
        {
            "mode": "create",
            "agent_id": "",
            "env": {},
            "context_md": "",
            "style_md": "",
            "vorlagen_md": "",
            "sortierregeln_md": "",
            "style_note": "",
            "drafts_status": {},
            "missing": [],
            "privacy_consent_accepted": privacy_consent_accepted,
            "privacy_consent_at": privacy_consent_at,
        },
    )


@app.post("/agents", dependencies=[Depends(auth.require_setup)])
@limiter.limit("30/minute")
def create_agent(
    request: Request,
    name_or_email: str = Form(...),
    user: str = Depends(auth.require_auth),
):
    slug = agents_io.slugify(name_or_email)
    # Neuer Agent startet GESTOPPT (Einrichtungs-Flow: erst konfigurieren, dann Start-Klick).
    agents_io.write_env(slug, {"AGENT_ENABLED": "false"})
    return RedirectResponse(f"/?agent_id={slug}", status_code=303)


@app.post("/agents/{agent_id}/rename", dependencies=[Depends(auth.require_setup)])
@limiter.limit("30/minute")
def rename_agent_endpoint(
    request: Request,
    agent_id: str,
    new_name: str = Form(...),
    user: str = Depends(auth.require_auth),
):
    new_slug = agents_io.slugify(new_name)
    try:
        agents_io.rename_agent(agent_id, new_slug)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(f"/?agent_id={new_slug}", status_code=303)


@app.post("/agents/{agent_id}/delete", dependencies=[Depends(auth.require_setup)])
@limiter.limit("30/minute")
def delete_agent_endpoint(
    request: Request,
    agent_id: str,
    confirmation: str = Form(""),
    user: str = Depends(auth.require_auth),
):
    if confirmation != "LÖSCHEN":
        return RedirectResponse(
            f"/?agent_id={agent_id}&error={quote('Löschen abgebrochen: Bestätigungswort war nicht ‚LÖSCHEN‘.')}",
            status_code=303,
        )
    try:
        agents_io.delete_agent(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse("/", status_code=303)


@app.post(
    "/agents/{agent_id}/{action}",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_setup)],
)
@limiter.limit("30/minute")
def agent_flag_toggle(
    request: Request,
    agent_id: str,
    action: str,
    user: str = Depends(auth.require_auth),
):
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="invalid action")
    try:
        agents_io.set_agent_enabled(agent_id, action == "start")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return templates.TemplateResponse(
        request,
        "_status_card.html",
        {
            "agent_statuses": _build_agent_statuses(),
            "service_status": docker_ctrl.get_agent_status(),
            "autostart_enabled": _autostart_enabled(),
            "action_result": "wirkt ab dem nächsten Poll-Zyklus",
        },
    )


@app.post(
    "/agent/{action}",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_setup)],
)
@limiter.limit("30/minute")
def agent_action(request: Request, action: str, user: str = Depends(auth.require_auth)):
    """Globale Admin-Funktion (Phase-4-Umfang): steuert den EINEN agent-Service via Docker."""
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="invalid action")
    result = docker_ctrl.control_agent(action)
    return templates.TemplateResponse(
        request,
        "_status_card.html",
        {
            "agent_statuses": _build_agent_statuses(),
            "service_status": docker_ctrl.get_agent_status(),
            "autostart_enabled": _autostart_enabled(),
            "action_result": result,
        },
    )


@app.post("/context/generate", dependencies=[Depends(auth.require_setup)])
@limiter.limit("10/minute")
def context_generate(
    request: Request,
    agent_id: str = Form(...),
    firma_input: str = Form(..., max_length=5000),
    user: str = Depends(auth.require_auth),
):
    try:
        env = agents_io.read_env_raw(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid agent_id")

    provider = (env.get("LLM_PROVIDER") or "").strip()
    if provider != "anthropic":
        raise HTTPException(
            status_code=400,
            detail=(
                "Der Context-Assistent nutzt Anthropic — dieser Agent verwendet "
                f"{provider or 'keinen erkannten Provider'}. context.md bitte manuell pflegen "
                "oder einen Anthropic-Key hinterlegen."
            ),
        )
    raw_key = (env.get("LLM_API_KEY") or "").strip()
    if not raw_key:
        raise HTTPException(status_code=400, detail="Kein API-Key für diesen Agenten gespeichert")

    try:
        api_key = crypto.decrypt_value(raw_key)
        seed_text = llm_seed.generate(firma_input, api_key=api_key)
    except ValueError as e:
        # Review WR-06: generische Meldung nach außen, Details nur serverseitig.
        logger.warning("context_generate_value_error", extra={"agent_id": agent_id, "error": str(e)})
        raise HTTPException(status_code=400, detail="Anfrage ungültig oder Secret nicht entschlüsselbar.")
    except RuntimeError as e:
        logger.warning("context_generate_runtime_error", extra={"agent_id": agent_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="LLM-Dienst nicht erreichbar.")
    except Exception as e:
        logger.warning("context_generate_failed", extra={"agent_id": agent_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Interner Fehler.")
    return PlainTextResponse(seed_text)


@app.post("/test-connection", dependencies=[Depends(auth.require_setup)])
@limiter.limit("10/minute")
def test_connection_endpoint(
    request: Request,
    target: str = Form(...),          # "imap" oder "llm"
    agent_id: str = Form(""),
    imap_user: str = Form(None),
    imap_password: str = Form(None),
    llm_api_key: str = Form(None),
    user: str = Depends(auth.require_auth),
):
    """Live-Verbindungstest für IMAP bzw. LLM OHNE zu speichern (Betreiber-Wunsch:
    „Verbindung testen"-Button im Agent-Formular). Nutzt exakt dieselbe Probe-Logik
    wie POST /save (`validate_conn`), persistiert aber nichts. Ein leer gelassenes
    Passwort-/Key-Feld fällt auf den bereits gespeicherten (entschlüsselten) Wert
    zurück — genau wie beim Speichern. Erfolg -> 200 + kurze Meldung; Fehlschlag ->
    400 + betreiber-lesbare Meldung (beides ohne Secrets)."""
    existing_agent_env: dict[str, str] = {}
    if agent_id:
        try:
            existing_agent_env = agents_io.read_env_raw(agent_id)
        except ValueError:
            existing_agent_env = {}

    if target == "imap":
        submitted_pw = (imap_password or "").strip()
        if submitted_pw:
            pw_plain = imap_password
        else:
            enc = existing_agent_env.get("IMAP_PASSWORD", "") or ""
            try:
                pw_plain = crypto.decrypt_value(enc) if enc else ""
            except Exception:
                pw_plain = ""
        probe_env = {
            "IMAP_HOST": existing_agent_env.get("IMAP_HOST", ""),
            "IMAP_PORT": existing_agent_env.get("IMAP_PORT", ""),
            "IMAP_USE_SSL": existing_agent_env.get("IMAP_USE_SSL", ""),
            "IMAP_USER": (imap_user if imap_user is not None else existing_agent_env.get("IMAP_USER", "")) or "",
            "IMAP_PASSWORD": pw_plain,
        }
        try:
            validate_conn.check_imap(probe_env)
        except validate_conn.ConnectionCheckError as e:
            return PlainTextResponse(str(e), status_code=400)
        return PlainTextResponse("IMAP-Verbindung erfolgreich — Anmeldung akzeptiert.")

    if target == "llm":
        submitted_key = (llm_api_key or "").strip()
        if submitted_key and submitted_key != agents_io.MASKED:
            key_plain = llm_api_key
            provider = llm_detect.detect_llm_provider(llm_api_key) or ""
        else:
            enc = existing_agent_env.get("LLM_API_KEY", "") or ""
            try:
                key_plain = crypto.decrypt_value(enc) if enc else ""
            except Exception:
                key_plain = ""
            provider = (existing_agent_env.get("LLM_PROVIDER") or "").strip()
        if not key_plain:
            return PlainTextResponse("Kein API-Key vorhanden — bitte Key eintragen.", status_code=400)
        if provider not in ("anthropic", "openai", "google"):
            return PlainTextResponse(
                "API-Key-Format nicht erkannt — erwartet sk-ant-… (Anthropic), "
                "sk-… (OpenAI) oder AIza… (Google).",
                status_code=400,
            )
        try:
            validate_conn.check_llm(provider, key_plain)
        except validate_conn.ConnectionCheckError as e:
            return PlainTextResponse(str(e), status_code=400)
        label = PROVIDER_LABELS.get(provider, provider)
        return PlainTextResponse(f"{label}-API erreichbar — Key gültig.")

    raise HTTPException(status_code=400, detail="unbekanntes Testziel")


# Ablageort des mitgelieferten Add-in-ClickOnce-Bundles (setup.exe + Application
# Files + .vsto/Manifeste). Das Deployment-Paket legt `addin-publish/` neben die
# docker-compose.yml; die WebUI mountet das Paketverzeichnis read-only nach
# /compose (siehe docker-compose.yml `- .:/compose:ro`), daher der Default. In der
# lokalen Test-Instanz fehlt der Ordner i. d. R. -> der Endpoint antwortet dann
# sauber mit 404 (kein Absturz).
ADDIN_BUNDLE_DIR = os.getenv("ADDIN_BUNDLE_DIR", "/compose/addin-publish")


def _build_addin_installer_zip() -> bytes:
    """Zippt das mitgelieferte `addin-publish/`-Bundle (setup.exe +
    Application Files + .vsto/Manifeste) — ClickOnce braucht den KOMPLETTEN
    Ordner, ein einzelnes setup.exe genügt nicht. Das ZIP behält die
    `addin-publish/`-Ordnerstruktur. Herausgezogen aus
    `addin_installer_endpoint` (260728-m8g Task 6), damit der Session-freie
    `/addin/installer`-Endpoint fürs Add-in denselben Bau-Schritt nutzt, ohne
    Logik zu duplizieren. Wirft `HTTPException(404, …)` wenn `addin-publish/`
    fehlt oder leer ist — 404-Verhalten bewusst unverändert beibehalten."""
    bundle = Path(ADDIN_BUNDLE_DIR)
    if not bundle.is_dir() or not any(bundle.rglob("*")):
        raise HTTPException(
            status_code=404,
            detail="Add-in-Installer ist auf diesem Server nicht hinterlegt (addin-publish/ fehlt).",
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                # relative_to(bundle.parent) -> ZIP-Wurzel enthält den Ordner
                # "addin-publish/…", damit ClickOnce die relativen Pfade findet.
                zf.write(path, path.relative_to(bundle.parent).as_posix())
    return buf.getvalue()


@app.get("/addin-installer", dependencies=[Depends(auth.require_setup)])
@limiter.limit("30/minute")
def addin_installer_endpoint(
    request: Request,
    user: str = Depends(auth.require_auth),
):
    """Lädt den Outlook-Add-in-Installer als ZIP herunter (Betreiber-Wunsch:
    Add-in bequem auf einem weiteren PC installieren) — Browser-Button in der
    WebUI, SESSION-Zugriff (Login-Cookie).

    Hinweis Mark-of-the-Web: ein per Browser geladenes ZIP ist „aus dem Internet"
    markiert; am Ziel-PC einmal ZIP → Eigenschaften → „Zulassen", dann entpacken,
    dann `setup.exe`. Enthält keine Secrets."""
    return Response(
        content=_build_addin_installer_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="vizpatch-addin-installer.zip"'},
    )


@app.get(
    "/addin/installer",
    dependencies=[Depends(auth.require_setup), Depends(auth.require_basic_password)],
)
@limiter.limit("10/minute")
def addin_installer_for_addin(
    request: Request,
    user: str = Depends(auth.require_auth),
):
    """Session-FREIER Installer-Download fürs Add-in selbst (260728-m8g, D-6):
    nach einem erfolgreichen Server-Update lädt das Add-in hierüber sein
    eigenes neues Publish. `/addin-installer` (ohne `/`-Trenner nach `addin`)
    ist der BROWSER-Weg und beginnt bewusst NICHT mit `/addin/` —
    `_is_addin_session_exempt_path` würde also nicht greifen und das native
    Add-in liefe in den Login-Redirect. Deshalb dieser zweite, eigenständige
    Endpoint unter dem `/addin/`-Präfix, zusätzlich mit
    `auth.require_basic_password` geschützt (dasselbe Passwort-Gate wie
    `POST /addin/update` — kein Grund, den Installer-Download laxer zu
    behandeln als den Update-Trigger selbst).

    **Bewusst KEINE Verschmelzung mit `/addin-installer`** — die Doppelung
    ist Absicht, kein Versehen; bitte nicht "aufräumen"."""
    return Response(
        content=_build_addin_installer_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="vizpatch-addin-installer.zip"'},
    )


STY05_HINT = (
    "Zu wenig verwertbares Mail-Material und kein Freitext — bitte Stil kurz im "
    "Feld beschreiben oder später erneut versuchen."
)


@app.post("/style/relearn", dependencies=[Depends(auth.require_setup)])
@limiter.limit("10/minute")
def style_relearn(
    request: Request,
    agent_id: str = Form(...),
    style_note: str = Form("", max_length=5000),
    user: str = Depends(auth.require_auth),
):
    """Schreibstil neu lernen (STY-03). Provider-agnostisch (D-55) — bewusst KEIN
    Anthropic-only-Gate wie bei /context/generate. Persistiert style_note VOR der
    Extraktion, damit die manuelle Angabe auch bei einem Fehlschlag erhalten bleibt
    (überlebt Re-Learn laut D-54)."""
    try:
        agents_io.read_env_raw(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid agent_id")

    agents_io.write_style_note_atomic(agent_id, style_note)

    try:
        style_md = style_extract.extract_style(agent_id)
    except style_extract.StyleExtractionEmpty:
        raise HTTPException(status_code=400, detail=STY05_HINT)
    except ValueError as e:
        # Review WR-06: generische Meldung nach außen, Details nur serverseitig.
        logger.warning("style_relearn_value_error", extra={"agent_id": agent_id, "error": str(e)})
        raise HTTPException(status_code=400, detail="Anfrage ungültig.")
    except RuntimeError as e:
        logger.warning("style_relearn_runtime_error", extra={"agent_id": agent_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="LLM-Dienst nicht erreichbar.")
    except Exception as e:
        logger.warning("style_relearn_failed", extra={"agent_id": agent_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Stil-Extraktion fehlgeschlagen.")

    agents_io.write_style_md_atomic(agent_id, style_md)
    return PlainTextResponse(style_md)


@app.get(
    "/chat/{agent_id}/embed",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_setup)],
)
def chat_embed(request: Request, agent_id: str, user: str = Depends(auth.require_auth)):
    """Chrome-loses, einbettbares Chat-Partial (D-61/CHAT-05) — eigener Rahmen,
    KEIN base.html-Erbe. Phase 8 (Outlook-Add-in) bindet dieselbe Route ein.
    T-jrq-06 (260722-jrq): Session-Gate-Ausnahme (Add-in-Pfad) — `require_setup`
    bleibt hier als Rest-Schutz (Passwort muss trotzdem gesetzt sein)."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="agent not found")
    return templates.TemplateResponse(request, "chat.html", {"agent_id": agent_id})


@app.get("/chat/{agent_id}/panel", response_class=HTMLResponse)
def chat_panel(request: Request, agent_id: str, user: str = Depends(auth.require_auth)):
    """Chat-Swap-Partial (UMBAU-D2): wird per hx-get in #chat-panel geladen, wenn
    der Betreiber das Radio einer anderen Agenten-Zeile klickt — derselbe
    404-Guard wie `chat_embed`."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="agent not found")
    return templates.TemplateResponse(request, "_chat_panel.html", {"agent_id": agent_id})


@app.get(
    "/addin/taskpane.html",
    response_class=HTMLResponse,
    dependencies=[Depends(auth.require_setup)],
)
def addin_taskpane(request: Request, user: str = Depends(auth.require_auth)):
    """Outlook-Office.js-Taskpane (D-66/OUT-02): same-origin ausgeliefert, iframed
    das bestehende Chat-Embed. Kein 404-Guard nötig — die Seite listet nur die
    Agenten fürs Dropdown, der Existenz-Guard sitzt bereits in chat_embed().
    T-jrq-06 (260722-jrq): Session-Gate-Ausnahme (/addin/*) — `require_setup`
    bleibt hier als Rest-Schutz (Passwort muss trotzdem gesetzt sein)."""
    agents = agents_io.list_agent_ids()
    initial_agent = agents[0] if agents else ""
    return templates.TemplateResponse(
        request,
        "addin_taskpane.html",
        {"agents": agents, "initial_agent": initial_agent},
    )


@app.get("/addin/manifest.xml", dependencies=[Depends(auth.require_setup)])
def addin_manifest(user: str = Depends(auth.require_auth)):
    """Klassisches XML-Add-in-Manifest (D-67/OUT-01), pro Installation über
    ADDIN_BASE_URL templatisiert. Wird als reine Textdatei geladen und per
    str.replace ersetzt (NICHT über TemplateResponse/Jinja2 gerendert) — kein
    Autoescape-Konflikt mit XML. ADDIN_BASE_URL wird VOR dem Einsetzen
    validiert (T-08-06): muss mit https:// beginnen und darf keine
    XML-Sonderzeichen enthalten, sonst 400 statt eines kaputten/injizierbaren
    Manifests.
    T-jrq-06 (260722-jrq): Session-Gate-Ausnahme (/addin/*) — `require_setup`
    bleibt hier als Rest-Schutz (Passwort muss trotzdem gesetzt sein)."""
    base_url = os.getenv("ADDIN_BASE_URL", DEFAULT_ADDIN_BASE_URL).strip()
    if not base_url.startswith("https://") or _XML_SPECIAL_CHARS_RE.search(base_url):
        raise HTTPException(
            status_code=400,
            detail=(
                "ADDIN_BASE_URL ist ungültig — muss mit https:// beginnen und darf "
                'keine Zeichen <, >, " oder & enthalten.'
            ),
        )
    manifest_path = Path("src/templates/addin_manifest.xml")
    xml_text = manifest_path.read_text(encoding="utf-8").replace("{ADDIN_BASE_URL}", base_url)
    return PlainTextResponse(xml_text, media_type="application/xml")


@app.post("/addin/verify-password", dependencies=[Depends(auth.require_setup)])
@limiter.limit("10/minute")
def addin_verify_password(request: Request, password: str = Form("")):
    """Add-in-Einstellungs-Gate: prüft ein FRISCH eingegebenes WebUI-Passwort
    gegen `WEBUI_PASSWORD` (bcrypt). 200 `{ok:true}` bei korrekt, sonst 401. Das
    Add-in ruft dies vor dem Öffnen des Einstellungsdialogs auf und gibt die
    Felder nur bei 200 frei.

    Session-Gate-Ausnahme über den `/addin/`-Präfix (das VSTO-WebBrowser-Control
    trägt keinen Session-Cookie); Origin über die Add-in-Allowlist erlaubt (siehe
    `enforce_same_origin`). `require_setup` stellt sicher, dass überhaupt ein
    Passwort gesetzt ist. Das Passwort wird NIE geloggt; Rate-Limit gegen
    Brute-Force."""
    if auth.verify_password((password or "").strip()):
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Falsches Passwort.")


@app.get("/addin/agents", dependencies=[Depends(auth.require_setup)])
@limiter.limit("60/minute")
def addin_agents(request: Request, user: str = Depends(auth.require_auth)):
    """Liefert die konfigurierten Agenten-IDs als JSON fürs Add-in. Das native
    VSTO-Add-in zeigt damit im Pane-Header den REAL konfigurierten Agent-Namen an
    (Betreiber-Wunsch: „Name aus dem Backend lesen"), statt sich allein auf die
    lokal gespeicherte AgentId zu verlassen — der Agent hat serverseitig keinen
    separaten Anzeigenamen, die ID (Slug) IST der Name. Das Add-in gleicht die
    Liste mit seiner konfigurierten AgentId ab und faellt bei Nicht-Treffer/
    Nichterreichbarkeit auf die lokale ID zurueck.

    Session-Gate-Ausnahme über den `/addin/`-Präfix (das native Add-in trägt
    keinen Session-Cookie); Origin über die Add-in-Allowlist erlaubt. `require_setup`
    bleibt als Rest-Schutz."""
    return {"agents": agents_io.list_agent_ids()}


@app.get("/addin/agents/{agent_id}/draft-enabled", dependencies=[Depends(auth.require_setup)])
@limiter.limit("60/minute")
def addin_draft_enabled_get(agent_id: str, request: Request, user: str = Depends(auth.require_auth)):
    """Liefert den Zustand der automatischen Entwurf-Erstellung eines Agenten
    (260725-tqr) als JSON `{"enabled": bool}` fürs Add-in. Default true, falls
    das Flag ENABLE_DRAFT_CREATION nicht gesetzt ist. Wahrheitsquelle ist
    ausschließlich das Backend — das Add-in hält keinen lokalen Wert.

    Schutz wie /addin/agents: Session-Gate-Ausnahme über den `/addin/`-Präfix,
    Origin über die Add-in-Allowlist, Basic-Auth via require_auth.

    Unbekannter Agent → 404: explizite Existenzprüfung wie /agents/{id}/edit.
    read_env_raw liefert für ein fehlendes .env stillschweigend {} (kein
    ValueError), daher wäre ein try/except hier wirkungslos und der Endpoint
    würde für tote Agent-IDs fälschlich enabled=true melden."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="Unbekannter Agent.")
    env = agents_io.read_env_raw(agent_id)
    enabled = (env.get("ENABLE_DRAFT_CREATION") or "true").strip().lower() != "false"
    return {"enabled": enabled}


@app.post("/addin/agents/{agent_id}/draft-enabled", dependencies=[Depends(auth.require_setup)])
@limiter.limit("60/minute")
def addin_draft_enabled_set(
    agent_id: str,
    request: Request,
    enabled: str = Form(...),
    user: str = Depends(auth.require_auth),
):
    """Schreibt den Zustand der automatischen Entwurf-Erstellung eines Agenten
    (260725-tqr) ins Backend-.env (Klartext-Flag, kein Secret). POST statt PUT —
    spiegelt das bestehende /addin/verify-password-Muster und dessen CSRF-
    Freischaltung über den `/addin/`-Präfix.

    Unbekannter Agent → 404: explizite Existenzprüfung VOR dem Write, sonst
    würde write_env für eine tote Agent-ID ein Phantom-Verzeichnis mit nur
    diesem Flag anlegen (write_env legt fehlende Agent-Ordner an)."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="Unbekannter Agent.")
    normalized = "true" if enabled in ("true", "on", "1") else "false"
    agents_io.write_env(agent_id, {"ENABLE_DRAFT_CREATION": normalized})
    return {"ok": True, "enabled": normalized == "true"}


@app.get("/addin/agents/{agent_id}/intelligenz", dependencies=[Depends(auth.require_setup)])
@limiter.limit("60/minute")
def addin_intelligenz_get(agent_id: str, request: Request, user: str = Depends(auth.require_auth)):
    """Liefert den Intelligenz-Modus eines Agenten als JSON fürs Add-in
    (Betreiber-Wunsch 2026-08-24) — Muster 1:1 wie `/draft-enabled`.

    Ein unbekannter oder von Hand verpfuschter Wert in der `.env` wird als
    „standard" gemeldet, genau wie der Agent ihn behandelt
    (`config.normalisiere_intelligenz_modus`). Der Dialog soll nicht etwas
    anderes anzeigen als der Agent tut."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="Unbekannter Agent.")
    env = agents_io.read_env_raw(agent_id)
    roh = (env.get("INTELLIGENZ_MODUS") or "").strip().lower()
    return {"modus": roh if roh in ("standard", "hoch") else "standard"}


@app.post("/addin/agents/{agent_id}/intelligenz", dependencies=[Depends(auth.require_setup)])
@limiter.limit("60/minute")
def addin_intelligenz_set(
    agent_id: str,
    request: Request,
    modus: str = Form(...),
    user: str = Depends(auth.require_auth),
):
    """Schreibt den Intelligenz-Modus ins Agenten-.env (Klartext, kein Secret).

    Ein unzulässiger Wert wird mit 400 abgelehnt statt auf „standard" gesetzt:
    hier klickt ein Mensch bewusst, und eine stille Korrektur würde ihm etwas
    anderes speichern, als er im Dialog gewählt hat. Existenzprüfung VOR dem
    Write, sonst legt `write_env` für eine tote Agent-ID ein Phantom-Verzeichnis
    an (dieselbe Falle wie bei `/draft-enabled`)."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="Unbekannter Agent.")
    normalisiert = (modus or "").strip().lower()
    if normalisiert not in ("standard", "hoch"):
        raise HTTPException(
            status_code=400, detail="modus muss 'standard' oder 'hoch' sein."
        )
    agents_io.write_env(agent_id, {"INTELLIGENZ_MODUS": normalisiert})
    return {"ok": True, "modus": normalisiert}


@app.get("/addin/agents/{agent_id}/urlaub", dependencies=[Depends(auth.require_setup)])
@limiter.limit("60/minute")
def addin_urlaub_get(agent_id: str, request: Request, user: str = Depends(auth.require_auth)):
    """Liefert Freigabe, Probelauf und Zeitraum der Urlaubs-Autoantwort fürs
    Add-in — Muster 1:1 wie `/addin/agents/{id}/draft-enabled`.

    **Warum das Add-in diesen Endpoint braucht:** der Betreiber soll den Stand
    im Einstellungen-Fenster SEHEN und dort auch umlegen können, ohne die
    Browser-Oberfläche zu öffnen — der Kunde arbeitet ausschließlich in
    Outlook. Seit v1.29.1 geht dasselbe zusätzlich über den Chat
    (`urlaub_eintragen`/`urlaub_beenden`, Betreiber-Auftrag 2026-08-23); die
    beiden Wege schreiben in dieselbe Agent-`.env` und prüfen denselben
    Zeitraum über `_pruefe_urlaubszeitraum`.

    Wahrheitsquelle ist ausschließlich das Backend — das Add-in hält keinen
    lokalen Wert. Unbekannter Agent → 404 (gleiche Begründung wie dort:
    `read_env_raw` liefert für ein fehlendes `.env` stillschweigend `{}`)."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="Unbekannter Agent.")
    env = agents_io.read_env_raw(agent_id)
    return {
        "enabled": (env.get("ENABLE_URLAUBSANTWORT") or "false").strip().lower() == "true",
        "dry_run": (env.get("URLAUB_DRY_RUN") or "true").strip().lower() != "false",
        "von": (env.get("URLAUB_VON") or "").strip(),
        "bis": (env.get("URLAUB_BIS") or "").strip(),
        # Damit der Dialog warnen kann, BEVOR der Betreiber in den Urlaub fährt
        # und sich auf eine Antwort verlässt, die nie kommt.
        "vorlage_vorhanden": chat_tools._abschnittsnamen_enthalten_urlaub(
            agents_io.read_vorlagen_md(agent_id) or ""
        ),
        # Betreiber-Auftrag 2026-08-24: der Kunde arbeitet nur in Outlook und
        # konnte den Text, der in seinem Namen hinausgeht, im Dialog bisher
        # weder sehen noch aendern — der Hinweis verwies ihn auf Chat oder
        # Weboberflaeche. `vorlage_text` ist der gespeicherte Baustein,
        # `vorlage_vorschlag` ein unverbindlicher StartText fuer den Fall, dass
        # es noch keinen gibt. Der Riegel bleibt: gesendet wird nur, was
        # tatsaechlich gespeichert ist.
        "vorlage_text": chat_tools.urlaubstext_aus(
            agents_io.read_vorlagen_md(agent_id) or ""
        ),
        "vorlage_vorschlag": chat_tools.URLAUB_VORLAGE_VORSCHLAG,
        # Wo der Urlaub HEUTE steht: "aus" | "unvollstaendig" | "spaeter" |
        # "laeuft" | "vorbei". Der Haken allein sagt das nicht — nach dem
        # letzten Urlaubstag steht er weiter auf "an" und greift trotzdem nicht
        # mehr (der Agent hört von selbst auf). Ein Dialog, der nur den Haken
        # zeigt, behauptet sonst einen Zustand, den es nicht gibt.
        "lage": chat_tools._urlaubslage(
            (env.get("ENABLE_URLAUBSANTWORT") or "false").strip().lower() == "true",
            (env.get("URLAUB_VON") or "").strip(),
            (env.get("URLAUB_BIS") or "").strip(),
        ),
    }


@app.post("/addin/agents/{agent_id}/urlaub", dependencies=[Depends(auth.require_setup)])
@limiter.limit("60/minute")
def addin_urlaub_set(
    agent_id: str,
    request: Request,
    enabled: str = Form(...),
    dry_run: str = Form("true"),
    von: str = Form(""),
    bis: str = Form(""),
    # Betreiber-Auftrag 2026-08-24. Der Marker entscheidet, OB geschrieben wird
    # — nicht der Textwert selbst: FastAPI liefert fuer ein leeres Formularfeld
    # denselben Wert (`None`) wie fuer ein fehlendes, ein Bestands-Add-in ohne
    # das Feld waere also von einem ausdruecklichen Leeren nicht zu
    # unterscheiden und wuerde die gespeicherte Vorlage mit loeschen. Gleiches
    # Marker-Muster wie die `*_section_submitted`-Felder im Browser-Formular,
    # und aus genau demselben Grund.
    vorlage_gesetzt: str = Form(""),
    vorlage: str = Form(""),
    user: str = Depends(auth.require_auth),
):
    """Schreibt Freigabe, Probelauf und Zeitraum ins Agenten-`.env`.

    Validiert den Zeitraum mit DEMSELBEN Helfer wie das Browser-Formular
    (`_pruefe_urlaubszeitraum`) — ein Client darf nicht an der Prüfung
    vorbeikommen, nur weil er ein anderer Client ist. Bei ungültigem Zeitraum
    400 mit der deutschen Meldung, die der Dialog unverändert anzeigt.

    Unbekannter Agent → 404 VOR dem Write, sonst legte `write_env` ein
    Phantom-Verzeichnis an (gleiche Begründung wie bei `draft-enabled`)."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="Unbekannter Agent.")

    an = enabled in ("true", "on", "1")
    von = (von or "").strip()
    bis = (bis or "").strip()

    fehler = _pruefe_urlaubszeitraum(von, bis, an)
    if fehler:
        raise HTTPException(status_code=400, detail=fehler)

    # Die Vorlage ZUERST schreiben: sie ist die Voraussetzung dafuer, dass die
    # Freigabe ueberhaupt etwas bewirkt. Umgekehrte Reihenfolge liesse bei einem
    # Fehler im Vorlagen-Schreibvorgang eine eingeschaltete Autoantwort ohne
    # Text zurueck — sie wuerde dann schweigen, ohne dass der Dialog es sagt.
    if vorlage_gesetzt in ("true", "on", "1"):
        vorher = agents_io.read_vorlagen_md(agent_id) or ""
        agents_io.write_vorlagen_md_atomic(
            agent_id, chat_tools.vorlagen_md_mit_urlaub(vorher, vorlage)
        )

    agents_io.write_env(
        agent_id,
        {
            "ENABLE_URLAUBSANTWORT": "true" if an else "false",
            "URLAUB_DRY_RUN": "true" if dry_run in ("true", "on", "1") else "false",
            "URLAUB_VON": von,
            "URLAUB_BIS": bis,
        },
    )
    return {"ok": True, "enabled": an, "von": von, "bis": bis}


@app.get("/addin/update-check", dependencies=[Depends(auth.require_setup)])
@limiter.limit("30/minute")
def addin_update_check(request: Request, user: str = Depends(auth.require_auth)):
    """Prueft (260728-irn, D-1) server-seitig gegen den oeffentlichen
    Versions-Index (`EnverShala/vizpatch-releases/latest.json`), ob eine neuere
    Produktversion existiert, und liefert
    `{"status", "installed", "latest", "update_available", "message"}`.

    Bewusst tokenfrei: der Index enthaelt nur die Versionsnummer, keinen Code —
    so liegt kein Secret auf Kundenhardware (siehe `update_check`-Docstring).

    Session-Gate-Ausnahme ueber den `/addin/`-Praefix (das native Add-in traegt
    keinen Session-Cookie); GET ist eine sichere Methode -> `enforce_same_origin`
    greift nicht, keine Aenderung an der Origin-Allowlist noetig. `require_setup`
    bleibt als Rest-Schutz.

    Der Endpoint darf NIE 500 werfen (D-1) — jeder unerwartete Fehler im
    Logikmodul faellt auf `status: "unreachable"` zurueck, das Add-in behandelt
    das identisch zu jedem anderen nicht-`ok`-Status ("kein Update")."""
    try:
        return update_check.build_update_status()
    except Exception as e:
        logger.warning(
            "addin_update_check_failed", extra={"exception_type": type(e).__name__}
        )
        return {
            "status": "unreachable",
            "installed": None,
            "latest": None,
            "update_available": False,
            "message": "Update-Prüfung fehlgeschlagen.",
        }


# --- Self-Update (260728-m8g, Stufe 2, D-1..D-6) -----------------------------

_UPDATE_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")

_UPDATE_ASSET_ERROR_MESSAGES = {
    "no_token": "Kein Update-Zugang konfiguriert (VIZPATCH_DIST_TOKEN fehlt) — bitte Enver Shala kontaktieren.",
    "no_access": "Zugang zum Update-Server abgelaufen oder entzogen — bitte Enver Shala kontaktieren.",
    "unreachable": "Update-Server nicht erreichbar.",
}


@app.get("/addin/update-status", dependencies=[Depends(auth.require_setup)])
@limiter.limit("60/minute")
def addin_update_status(request: Request, user: str = Depends(auth.require_auth)):
    """Liest die Statusdatei (260728-m8g, D-5) — IMMER 200, nie 500. Bewusst
    OHNE Basic-Passwort-Pflicht: reines Lesen von Versions-/Fortschrittsangaben,
    und das Add-in muss diesen Endpoint auch dann pollen koennen, wenn gerade
    umgeschaltet wird (genau dann ist die alte WebUI weg und die neue noch
    nicht bereit — ein Verbindungsabbruch ist erwartet, siehe D-5)."""
    try:
        current = update_status.read_status()
        current["stale"] = update_status.is_stale(current)
        return current
    except Exception as e:
        logger.warning(
            "addin_update_status_failed", extra={"exception_type": type(e).__name__}
        )
        idle = {
            "state": update_status.STATE_IDLE, "step": "", "progress": 0,
            "version_from": None, "version_to": None, "message": "",
            "recovery_hint": "", "started_at": None, "updated_at": None,
        }
        idle["stale"] = False
        return idle


def _run_update_job(version: str, version_from: Optional[str]) -> None:
    """Laeuft im Hintergrund-Thread, angestossen von `POST /addin/update`
    (260728-m8g). Schreibt die Statusdatei bis inklusive `launch_updater` —
    DANACH gehoert sie EXKLUSIV dem Sidecar (`updater.py`). Dieser Worker
    schreibt ab dort NICHTS mehr, sonst ueberschreiben sich zwei Schreiber und
    der Fortschritt im Add-in liefe rueckwaerts."""
    work_dir = Path(os.getenv("VIZPATCH_UPDATE_DIR", "/data/updates"))
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)

        token = update_dist.read_dist_token()

        update_status.write_status(
            step="preparing", progress=update_status.progress_for("preparing"),
            message="Release-Assets werden ermittelt …",
        )
        assets, asset_status = update_dist.find_release_assets(version, token)
        if asset_status != "ok":
            if asset_status == "asset_missing":
                message = f"Release {version} enthält kein Deployment-Paket."
            else:
                message = _UPDATE_ASSET_ERROR_MESSAGES.get(
                    asset_status, "Update-Vorbereitung fehlgeschlagen."
                )
            update_status.write_status(
                state=update_status.STATE_FAILED, step="preparing", message=message,
            )
            return

        free_bytes = shutil.disk_usage(work_dir).free
        if not update_dist.has_enough_space(free_bytes, assets["zip_size"]):
            needed_gb = max(2.5 * assets["zip_size"], 2 * 1024**3) / (1024**3)
            update_status.write_status(
                state=update_status.STATE_FAILED, step="preparing",
                message=f"Nicht genug freier Speicherplatz (mind. {needed_gb:.1f} GB benötigt).",
            )
            return

        zip_name, sha_name = update_dist.asset_names(version)
        zip_path = work_dir / zip_name
        sha_path = work_dir / sha_name

        # Fortschritt hoechstens alle 2s bzw. 5 MB in die Statusdatei
        # schreiben — sonst wird sie zum Schreib-Hotspot im Volume.
        progress_state = {"t": 0.0, "bytes_done": -(5 * 1024 * 1024)}

        def _on_download_progress(done: int, total: int) -> None:
            now = time.monotonic()
            enough_time = now - progress_state["t"] >= 2.0
            enough_bytes = done - progress_state["bytes_done"] >= 5 * 1024 * 1024
            is_last = total > 0 and done >= total
            if not (enough_time or enough_bytes or is_last):
                return
            progress_state["t"] = now
            progress_state["bytes_done"] = done
            update_status.write_status(
                step="download",
                progress=update_status.progress_for("download", done, total),
                message="Paket wird geladen …",
            )

        update_status.write_status(
            step="download", progress=update_status.progress_for("download"),
            message="Paket wird geladen …",
        )
        update_dist.download_asset(
            assets["zip_id"], token, zip_path, on_progress=_on_download_progress
        )
        update_dist.download_asset(assets["sha_id"], token, sha_path)

        update_status.write_status(
            step="verify", progress=update_status.progress_for("verify"),
            message="Prüfsumme wird geprüft …",
        )
        expected_hash = update_dist.parse_sha256_file(sha_path.read_text(encoding="utf-8"))
        if not update_dist.verify_sha256(zip_path, expected_hash):
            update_status.write_status(
                state=update_status.STATE_FAILED, step="verify",
                message="Prüfsumme des Update-Pakets stimmt nicht — Download verworfen, es wurde nichts geändert.",
            )
            return

        update_status.write_status(
            step="extract", progress=update_status.progress_for("extract"),
            message="Paket wird entpackt …",
        )
        source_dir = update_dist.extract_zip(zip_path, work_dir)

        update_status.write_status(
            step="load_images", progress=update_status.progress_for("load_images"),
            message="Programmdateien werden geladen …",
        )
        agent_tar = source_dir / f"vizpatch-{version}.tar"
        webui_tar = source_dir / f"vizpatch-webui-{version}.tar"
        for tar_path in (agent_tar, webui_tar):
            ok, _log = docker_ctrl.load_image_tarball(tar_path)
            if not ok:
                update_status.write_status(
                    state=update_status.STATE_FAILED, step="load_images",
                    message=f"Laden des Images fehlgeschlagen: {tar_path.name}",
                )
                return
        for tag in (f"vizpatch:{version}", f"vizpatch-webui:{version}"):
            if not docker_ctrl.image_exists(tag):
                update_status.write_status(
                    state=update_status.STATE_FAILED, step="load_images",
                    message=f"Image nach dem Laden nicht gefunden: {tag}",
                )
                return

        update_status.write_status(
            step="launch_updater", progress=update_status.progress_for("launch_updater"),
            message="Umschalt-Vorgang wird gestartet …",
        )
        layout = docker_ctrl.get_self_layout()
        if not layout:
            update_status.write_status(
                state=update_status.STATE_FAILED, step="launch_updater",
                message="Deploy-Verzeichnis nicht ermittelbar.",
            )
            return

        docker_ctrl.remove_container_quietly(docker_ctrl.UPDATER_CONTAINER_NAME)
        run_args = docker_ctrl.build_updater_run_args(
            layout,
            image=f"vizpatch-webui:{version}",
            source_dir=source_dir,
            version_to=version,
            version_from=version_from or "",
            docker_gid=docker_ctrl.docker_socket_gid(),
        )
        started, _log = docker_ctrl.start_updater(run_args)
        if not started:
            update_status.write_status(
                state=update_status.STATE_FAILED, step="launch_updater",
                message="Umschalt-Sidecar konnte nicht gestartet werden.",
            )
            return
        # AB HIER NICHTS MEHR SCHREIBEN — der Sidecar (updater.py) hat jetzt
        # die exklusive Schreibhoheit ueber die Statusdatei.
    except Exception as e:
        logger.warning("run_update_job_failed", extra={"exception_type": type(e).__name__})
        update_status.write_status(
            state=update_status.STATE_FAILED, step="preparing",
            message=f"Unerwarteter Fehler bei der Update-Vorbereitung: {type(e).__name__}",
        )


@app.post(
    "/addin/update",
    dependencies=[Depends(auth.require_setup), Depends(auth.require_basic_password)],
)
@limiter.limit("5/minute")
def addin_update_start(
    request: Request,
    version: str = Form(...),
    user: str = Depends(auth.require_auth),
):
    """Startet das Self-Update (260728-m8g, D-1..D-6). Zustandsaendernd UND
    DESTRUKTIV — startet beide Container neu — deshalb ZUSAETZLICH zur
    Origin-Allowlist ueber `auth.require_basic_password` geschuetzt
    (T-m8g-01). Der eigentliche Download-/Umschalt-Ablauf laeuft in einem
    Hintergrund-Thread (`_run_update_job`); der Request darf nicht auf einen
    mehrminuetigen Download warten — das Add-in pollt
    `GET /addin/update-status`."""
    if not _UPDATE_VERSION_RE.match(version or ""):
        raise HTTPException(status_code=400, detail="Ungültiges Versionsformat.")

    current_status = update_status.read_status()
    can_start, reason = update_status.can_start(current_status)
    if not can_start:
        raise HTTPException(status_code=409, detail=reason)

    token = update_dist.read_dist_token()
    if not token:
        message = "Kein Update-Zugang konfiguriert (VIZPATCH_DIST_TOKEN fehlt) — bitte Enver Shala kontaktieren."
        update_status.write_status(state=update_status.STATE_FAILED, step="preparing", message=message)
        raise HTTPException(status_code=503, detail=message)

    version_from = update_check.resolve_installed_version()
    update_status.write_status(
        state=update_status.STATE_RUNNING, step="preparing",
        progress=update_status.progress_for("preparing"),
        version_to=version, version_from=version_from,
        message="Update wird vorbereitet …", recovery_hint="",
    )

    thread = threading.Thread(target=_run_update_job, args=(version, version_from), daemon=True)
    thread.start()

    return JSONResponse({"ok": True, "started": True}, status_code=202)


def _sse_data_frame(text: str) -> str:
    """Kodiert einen Text-Chunk als SSE-`data:`-Frame — eingebettete Newlines
    werden zu mehreren `data:`-Fortsetzungszeilen desselben Events (SSE-Spec)."""
    body = "\n".join(f"data: {line}" for line in text.split("\n"))
    return f"{body}\n\n"


def _parse_chat_history(raw: str) -> list[dict]:
    """Parst den vom Browser gehaltenen Verlauf (D-58) defensiv aus dem
    JSON-Formfeld — bei Parse-Fehler oder falscher Struktur: leere Liste statt
    500 (T-07-09, manipulierte/kaputte history ist vollstaendig untrusted).
    Nur Turns mit str-role/str-content werden uebernommen, alles andere
    verworfen (kein Crash bei fremdartigen Einträgen)."""
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    parsed: list[dict] = []
    for turn in data:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = turn.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        parsed.append({"role": role, "content": content})
    return parsed


def _parse_mail_context(raw: str) -> dict | None:
    """Parst das optionale mail_context-Formfeld (D-65) defensiv — bei
    Parse-Fehler oder falscher Struktur: `None` (kein Mail-Block, kein Crash).
    In Phase 7 ungenutzt/leer; Phase 8 (OUT-03) fuellt es via Office.js."""
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "subject": str(data.get("subject") or ""),
        "sender": str(data.get("sender") or ""),
        "body": str(data.get("body") or ""),
    }


def _parse_attachment_meta(raw: str) -> dict | None:
    """Parst das optionale attachment_meta-Formfeld (Phase 12, ATT-03) defensiv —
    streng nach dem Muster von `_parse_mail_context`: bei Parse-Fehler oder
    falscher Struktur `None` (kein DATEN-Block, kein Crash, Rueckwaertskompat).
    Traegt NUR Metadaten (Dateiname/Groesse/Mimetyp) einer zuvor per
    `/chat/{agent_id}/upload` hochgeladenen Datei — NIE den Dateiinhalt (D-96)."""
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        groesse = int(data.get("groesse") or 0)
    except (TypeError, ValueError):
        groesse = 0
    return {
        "dateiname": str(data.get("dateiname") or ""),
        "groesse": groesse,
        "mimetyp": str(data.get("mimetyp") or ""),
    }


def _parse_client_features(raw: str) -> set[str]:
    """Quick 260813-wea (Task 2c): Fähigkeits-Aushandlung — parst das optionale
    `client_features`-Formfeld (kommaseparierte Kleinbuchstaben-Marken, diese
    Runde kennt genau eine: `bild`) defensiv, wirft NIE. Fehlt das Feld oder
    fehlt die Marke, bleibt das Set leer bzw. ohne `bild` — der `bild`-Zweig
    in `_stream()` liefert dann KEIN `event: bild` (Rückwärtskompatibilität:
    ein altes, bereits installiertes Add-in schickt dieses Feld nicht und
    bekommt einen byte-gleichen Stream wie vor diesem Quick)."""
    return {tok.strip().lower() for tok in (raw or "").split(",") if tok.strip()}


@app.post("/chat/{agent_id}/send", dependencies=[Depends(auth.require_setup)])
@limiter.limit(
    # Review IN-02: Default aus chat.CHAT_RATE_LIMIT_PER_MIN_DEFAULT statt
    # hier erneut hartkodiert — kein Konstanten-Drift zwischen den Modulen.
    lambda: f"{os.getenv('CHAT_RATE_LIMIT_PER_MIN', str(chat.CHAT_RATE_LIMIT_PER_MIN_DEFAULT))}/minute"
)
def chat_send(
    request: Request,
    agent_id: str,
    # Review WR-03: Groessen-Limits gegen Kosten-/Speicher-DoS (analog
    # firma_input/style_note). message zusaetzlich in chat_tools hart gekappt.
    message: str = Form(..., max_length=8000),
    history: str = Form("", max_length=200_000),
    mail_context: str = Form(""),
    session_id: str = Form(""),
    attachment_meta: str = Form(""),
    client_features: str = Form("", max_length=200),
    user: str = Depends(auth.require_auth),
):
    """Streamt eine agentische Chat-Antwort via SSE (D-62/D-72/D-80). Provider/
    Key/Modell werden GENAU für `agent_id` aufgelöst (D-59-Intent) —
    `chat_tools.run_agentic_chat()` (CTOOL-01/02) läuft für Anthropic-Agenten
    die Tool-Use-Schleife (mit `mails_suchen`, D-74 Teil 1) und fällt für alle
    anderen Provider sauber auf den beratenden, werkzeuglosen Chat zurück
    (D-72/T-09-06, kein Absturz). Rate-Limit CHAT_RATE_LIMIT_PER_MIN (D-60, per
    Remote-Address) greift serverseitig weiter.

    `client_features` (Quick 260813-wea, Task 2): Fähigkeits-Aushandlung,
    damit Server und Add-in NICHT im Gleichschritt ausgeliefert werden
    müssen — ein altes Add-in schickt das Feld nicht, bekommt kein
    `event: bild` und verhält sich byte-gleich wie vor dieser Änderung
    (sein `default:`-Zweig würde ein unbekanntes Ereignis sonst als
    Text-Chunk in die Antwortblase schreiben). Ein neues Add-in/der Browser
    kündigt `bild` an und bekommt bei einem ausgewerteten Bild-Anhang das
    zusätzliche SSE-Ereignis.

    `session_id` (Session-Autorisierung Papierkorb-Werkzeuge): vom Browser
    (`chat.js`) je Chat-Sitzung erzeugtes Formfeld, unverändert an
    `chat_tools.run_agentic_chat()` durchgereicht — dort entscheidet es, ob eine
    Verschiebung in den Papierkorb erneut bestätigt werden muss oder ob die
    Sitzung bereits (durch eine frühere bestätigte Verschiebung) autorisiert ist.

    `attachment_meta` (Phase 12, ATT-03): optionales JSON-Formfeld mit den
    Metadaten ({"dateiname", "groesse", "mimetyp"}) einer zuvor per
    `/chat/{agent_id}/upload` hochgeladenen Datei. Defensiv über
    `_parse_attachment_meta` geparst (analog `_parse_mail_context`) und
    unverändert als `attachment_meta=` an `chat_tools.run_agentic_chat()`
    durchgereicht — NIE der Dateiinhalt (D-96).

    Verlaufs-Präzedenz (Quick 260812-hhm, Task 4): liegt für `(agent_id,
    session_id)` bereits ein serverseitig persistierter Verlauf vor
    (`chat_history.load_history`), GEWINNT er gegen das `history`-Formfeld —
    der Store ist die Single Source of Truth (Muster
    `_effective_attachment_meta`/Befund A). Ein leerer Store fällt auf das
    Formfeld zurück (Add-in/ältere Clients ohne Persistenz bleiben
    kompatibel). Das `Form(max_length=200_000)`-Limit für `history` bleibt
    unverändert (`test_chat_size_limits.py` prüft den 422 darauf).

    PLAN-CHECKER-W1: `run_agentic_chat` ist ein Generator — dessen Rumpf läuft
    erst beim ersten `next()`, also NACH dem 200-Commit der StreamingResponse.
    Damit ein invalider `agent_id` (ValueError) oder ein fehlender Key
    (`ChatConfigError`) weiterhin EAGER als 400 ankommt (Phase-7-Regression,
    T-07-01), löst chat_send die Provider-Auflösung HIER — VOR dem Aufbau der
    StreamingResponse — bewusst noch einmal separat auf, bevor der Generator
    startet."""
    parsed_history = _parse_chat_history(history)
    parsed_mail_context = _parse_mail_context(mail_context)
    parsed_attachment_meta = _parse_attachment_meta(attachment_meta)
    features = _parse_client_features(client_features)
    stored_history = chat_history.load_history(agent_id, session_id) if session_id else []
    effective_history = stored_history or parsed_history

    try:
        chat.resolve_chat_target(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid agent_id")
    except chat.ChatConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # Review WR-06: crypto.decrypt_value wirft bei falschem/rotiertem Key
        # (SEC-03-Fall) einen RuntimeError — als verstaendlicher 400 statt
        # generischem 500. ChatConfigError (Subklasse von RuntimeError) wird
        # oben bereits spezifischer behandelt. Detail nur serverseitig loggen,
        # nach außen generisch (kein roher Exception-Text).
        logger.warning("chat_send_secret_error", extra={"agent_id": agent_id, "error": str(e)})
        raise HTTPException(status_code=400, detail="Secret nicht entschlüsselbar.")

    def _stream():
        # Quick 260812-hhm (Task 4): serverseitige Persistenz nach fehlerfreiem
        # Stream — konsistent mit der bestehenden Frontend-Regel (chat.js
        # haengt den Verlauf nur bei !sawError an). Task 5 (D-hhm-08): `traces`
        # sammelt die kompakten Werkzeug-Transkript-Zeilen serverseitig — sie
        # werden NIE als SSE-Text an den Browser gestreamt (sonst laeuft das
        # Transkript ungefiltert in den `else`-Zweig unten), sondern nur beim
        # Persistieren an den gespeicherten assistant-Turn angehaengt.
        assistant_parts: list[str] = []
        traces: list[str] = []
        saw_error = False
        # Quick 260822-4ay (Befund 4), zweite Schicht: `_run_anthropic_tool_loop`
        # stellt fuer den agentischen Pfad selbst sicher, dass ein Zug nie
        # textlos endet. Dieses Netz hier gilt fuer ALLE Generatoren hinter
        # `run_agentic_chat` — insbesondere den werkzeuglosen Fallback-Chat
        # (Nicht-Anthropic-Provider, ENABLE_CHAT_TOOLS=false) und alles, was
        # spaeter dazukommt: kam kein einziger Text-Chunk an, sieht der
        # Betreiber eine Meldung statt einer leeren Sprechblase.
        sah_text = False
        try:
            for event in chat_tools.run_agentic_chat(
                agent_id,
                message,
                effective_history,
                parsed_mail_context,
                session_id=session_id,
                attachment_meta=parsed_attachment_meta,
            ):
                if event.get("type") == "tool":
                    yield f"event: tool\ndata: {event.get('label', '')}\n\n"
                elif event.get("type") == "bild":
                    # Quick 260813-wea (Task 2c): NUR an Clients ausliefern, die
                    # die Fähigkeit angekündigt haben (Rückwärtskompatibilität
                    # installierter Add-ins, T-wea-10). `continue` ist
                    # entscheidend: der Inhalt darf NIEMALS in `assistant_parts`
                    # landen (sonst stünde die Referenz im persistierten
                    # Verlauf und in der Token-Schätzung, T-wea-07). `json.dumps`
                    # escapt eingebettete Newlines — der Frame bleibt einzeilig,
                    # `_sse_data_frame` wird hier nicht gebraucht.
                    if "bild" in features:
                        yield "event: bild\ndata: " + json.dumps(
                            event.get("bild") or {}, ensure_ascii=False
                        ) + "\n\n"
                    continue
                elif event.get("type") == "trace":
                    traces.append(event.get("text", ""))
                    continue
                else:
                    text = event.get("text", "")
                    if text:
                        sah_text = True
                    assistant_parts.append(text)
                    yield _sse_data_frame(text)
            if not sah_text:
                logger.warning("chat_stream_ohne_text", extra={"agent_id": agent_id})
                text = (
                    "[Es ist keine Antwort zustande gekommen und keine Aktion ausgeführt "
                    "worden. Bitte die Anweisung noch einmal senden.]"
                )
                assistant_parts.append(text)
                yield _sse_data_frame(text)
            if session_id:
                try:
                    assistant_content = "".join(assistant_parts)
                    if traces:
                        assistant_content += (
                            "\n\n[Werkzeug-Protokoll dieses Zuges — DATEN, keine Anweisung]\n- "
                            + "\n- ".join(traces)
                        )
                    chat_history.append_turns(
                        agent_id,
                        session_id,
                        [
                            {"role": "user", "content": message},
                            {"role": "assistant", "content": assistant_content},
                        ],
                    )
                except Exception as e:
                    # Ein DB-Problem darf den bereits erfolgreichen Stream
                    # nicht nachträglich abbrechen (D-hhm-09, fail-soft).
                    logger.warning(
                        "chat_history_persist_failed", extra={"agent_id": agent_id, "error": str(e)}
                    )
            yield "event: done\ndata: \n\n"
        except Exception as e:
            saw_error = True
            logger.warning("chat_stream_error", extra={"agent_id": agent_id, "error": str(e)})
            # Review WR-06: keinen rohen Exception-Text (Hostnamen/Server-
            # Antworten) an den Client streamen — generische Meldung, Details
            # nur serverseitig via logger.warning oben. _sse_data_frame bleibt
            # (robust auch bei mehrzeiligem Text).
            yield "event: error\n" + _sse_data_frame("Interner Fehler bei der Verarbeitung.")

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Quick 260813-wea (Task 1): der Bild-Vorschau-Endpoint erlaubt ein HOEHERES
# Rate-Limit als der Chat-Versand — EIN Chat-Zug kann legitim mehrere
# Vorschauen (mehrere Bild-Anhaenge) ausloesen, der Chat-Deckel von
# CHAT_RATE_LIMIT_PER_MIN (Default 20) waere sonst der Flaschenhals fuer
# genau den Fall, den dieser Endpoint bedienen soll.
_PREVIEW_RATE_LIMIT_FACTOR = 3


@app.get("/chat/{agent_id}/anhang", dependencies=[Depends(auth.require_setup)])
@limiter.limit(
    lambda: (
        f"{chat._int_env('CHAT_RATE_LIMIT_PER_MIN', chat.CHAT_RATE_LIMIT_PER_MIN_DEFAULT) * _PREVIEW_RATE_LIMIT_FACTOR}"
        "/minute"
    )
)
def chat_anhang(
    request: Request,
    agent_id: str,
    uid: str = "",
    ordner: str = "INBOX",
    index: str = "0",
    user: str = Depends(auth.require_auth),
):
    """Liefert die ROHEN Bytes eines einzelnen Bild-Anhangs für die Vorschau
    in der Chat-Pane (Quick 260813-wea, Task 1) — GET-only, dieselbe
    Session-Gate-Ausnahme wie `chat_upload`/`chat_send` (das VSTO-Control
    trägt keinen Session-Cookie, `_is_addin_session_exempt_path`).

    KEIN allgemeiner Anhang-Download: liefert AUSSCHLIESSLICH Bilder, deren
    `Content-Type` aus den echten Magic Bytes stammt (T-wea-01/04,
    `chat_tools._detect_vision_media_type`), nie aus der Mail-Deklaration.
    Statuscode-Vertrag (Reihenfolge ist Teil des Vertrags, siehe Plan
    `<vertrag>`): `agent_id` unbekannt -> 404; `ENABLE_BILD_VORSCHAU=false`
    -> 404 (OHNE dass `anhang_vorschau_bytes` eine IMAP-Verbindung öffnet);
    danach `chat_tools.anhang_vorschau_bytes()` selbst (400/404/413/415/502).

    Logging: GENAU EIN `logger.info`-Aufruf am Ende mit `agent_id`/`uid`/
    `ordner`/`groesse`/`status` — NIE Dateiname, NIE Bytes (T-wea-06)."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="agent not found")
    if not chat_tools._bild_vorschau_allowed(agent_id):
        raise HTTPException(status_code=404, detail="not found")

    ergebnis = chat_tools.anhang_vorschau_bytes(agent_id, uid, index, ordner)
    status = ergebnis.get("status", 500)
    groesse = ergebnis.get("groesse", 0)
    logger.info(
        "chat_anhang_ausgeliefert",
        extra={"agent_id": agent_id, "uid": uid, "ordner": ordner, "groesse": groesse, "status": status},
    )
    if status != 200:
        raise HTTPException(status_code=status, detail=ergebnis.get("fehler", "Fehler."))
    return Response(
        content=ergebnis["bytes"],
        media_type=ergebnis["typ"],
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@app.post("/intern/urlaubsantwort/{agent_id}")
async def urlaubsantwort_relay(request: Request, agent_id: str):
    """Container-interner Endpoint: nimmt EINE fertige Urlaubsantwort vom
    Polling-Agenten an und stellt sie per SMTP zu (URL-01..09).

    **Warum es diesen Endpoint gibt:** der Agent darf strukturell kein SMTP
    sprechen (CLAUDE.md, AST-Waechter ueber `agent/src/`). Die Urlaubs-
    Autoantwort ist die eine vom Betreiber freigegebene Ausnahme von „kein
    Auto-Send" — gebaut so, dass die Invariante woertlich stehen bleibt: der
    Agent baut die Mail, der Versand bleibt an der einen auditierten Stelle
    (`mail_send.py`). Siehe `webui/src/urlaub_relay.py`.

    **Warum KEIN `auth.require_auth`:** der Aufrufer ist kein Browser und hat
    kein Sitzungs-Cookie. Authentifiziert wird ueber eine HMAC-Signatur mit
    einem aus dem gemeinsamen Fernet-Key abgeleiteten, zweckgebundenen
    Schluessel plus Zeitstempel-Frische (Replay-Schutz) — beides in
    `urlaub_relay.pruefe_und_sende`, das ohne gueltige Signatur nicht einmal
    eine Datei anfasst. Zusaetzlich fail-closed am Agenten-Flag
    `ENABLE_URLAUBSANTWORT` und am eingetragenen Zeitraum, also derselbe
    Riegel wie im Agenten, nur ein zweites Mal.

    Der Endpoint ist im Compose nicht nach aussen gemappt (nur Port 8080 des
    webui-Service ist es), aber das ist ausdruecklich NICHT die Absicherung —
    die Signatur ist es."""
    body = await request.body()
    try:
        ergebnis = urlaub_relay.pruefe_und_sende(
            agent_id,
            body,
            request.headers.get(urlaub_relay.TIMESTAMP_HEADER, ""),
            request.headers.get(urlaub_relay.SIGNATUR_HEADER, ""),
        )
    except urlaub_relay.RelayAbgelehnt as e:
        return JSONResponse(status_code=e.status, content={"fehler": e.meldung})
    return JSONResponse(content=ergebnis)


@app.post("/chat/{agent_id}/upload", dependencies=[Depends(auth.require_setup)])
@limiter.limit(
    # Dieselbe Rate-Limit-Konstruktion wie chat_send (T-12-06, DoS-Schutz).
    lambda: f"{os.getenv('CHAT_RATE_LIMIT_PER_MIN', str(chat.CHAT_RATE_LIMIT_PER_MIN_DEFAULT))}/minute"
)
def chat_upload(
    request: Request,
    agent_id: str,
    file: UploadFile = File(...),
    session_id: str = Form(""),
    user: str = Depends(auth.require_auth),
):
    """Streaming-Datei-Upload für den Chat-Tool-Loop (ATT-01, D-92/94/96): legt die
    hochgeladene Datei server-generiert benannt in einem tmp-Verzeichnis ab und
    registriert sie im Pending-Upload-Store (`chat_tools.register_pending_upload`) —
    das eigentliche MIME-/IMAP-APPEND übernimmt `entwurf_mit_anhang` (Plan 12-01) im
    nächsten Chat-Turn.

    Dieselbe Auth-Kombination wie `chat_send` (`auth.require_setup` +
    `auth.require_auth`, D-95/ASVS V2). `agent_id` läuft durch denselben
    Existenz-Guard wie `chat_embed` (ASVS V4). Der Client-Dateiname wird NUR über
    `os.path.basename()` sanitized für Anzeige/Content-Disposition genutzt — das
    tmp-File selbst bekommt einen server-generierten Namen via `tempfile.mkstemp`
    (T-12-07/ASVS V5, Path-Traversal ausgeschlossen).

    Streaming: `file.file.read(1 MB)`-Chunks (KEIN `file.read()` — Full-Memory-Load,
    verletzt D-96) mit einem Live-Byte-Zähler gegen `MAX_ATTACHMENT_MB` (Default 15,
    T-12-06/Pitfall 4 aus 12-RESEARCH.md: Prüfung gegen die ROHE Byte-Anzahl, nicht
    die spätere base64-aufgeblähte Größe). Bei Überschreitung: 413 + tmp-Cleanup."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="agent not found")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id fehlt")

    max_bytes = chat._int_env("MAX_ATTACHMENT_MB", MAX_ATTACHMENT_MB_DEFAULT) * 1024 * 1024
    filename = os.path.basename(file.filename or "anhang")

    tmp_dir = Path(tempfile.gettempdir()) / _CHAT_UPLOAD_TMP_DIRNAME
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=tmp_dir, suffix=".upload")
    tmp_path = Path(tmp_name)
    os.close(tmp_fd)

    written = 0
    try:
        with tmp_path.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Datei überschreitet das Limit von {max_bytes // (1024 * 1024)} MB.",
                    )
                out.write(chunk)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        logger.warning("chat_upload_failed", extra={"agent_id": agent_id, "error": str(e)})
        raise HTTPException(status_code=400, detail="Upload fehlgeschlagen.")

    mimetyp, _ = mimetypes.guess_type(filename)
    mimetyp = mimetyp or "application/octet-stream"
    chat_tools.register_pending_upload(agent_id, session_id, tmp_path, filename, written, mimetyp)
    return {"ok": True, "dateiname": filename, "groesse": written, "mimetyp": mimetyp}


@app.get("/chat/{agent_id}/history", dependencies=[Depends(auth.require_setup)])
@limiter.limit(
    lambda: f"{os.getenv('CHAT_RATE_LIMIT_PER_MIN', str(chat.CHAT_RATE_LIMIT_PER_MIN_DEFAULT))}/minute"
)
def chat_history_get(
    request: Request,
    agent_id: str,
    session_id: str = "",
    user: str = Depends(auth.require_auth),
):
    """Liefert den serverseitig persistierten Verlauf für `(agent_id,
    session_id)` (Quick 260812-hhm, Task 4) — `chat.js` ruft dies beim Init
    auf, damit der Betreiber nach einem Browser-Reload sein Gespräch weiter
    sieht. Dieselbe Auth-/Guard-Kombination wie `chat_upload`
    (`auth.require_setup`/`auth.require_auth` + Agent-Existenz-Guard, 404 bei
    unbekanntem Agent). Fehlende `session_id` -> leere Liste (kein Fehler).
    """
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="agent not found")
    if not session_id:
        return {"turns": []}
    return {"turns": chat_history.load_history(agent_id, session_id)}


@app.post("/chat/{agent_id}/history/reset", dependencies=[Depends(auth.require_setup)])
@limiter.limit(
    lambda: f"{os.getenv('CHAT_RATE_LIMIT_PER_MIN', str(chat.CHAT_RATE_LIMIT_PER_MIN_DEFAULT))}/minute"
)
def chat_history_reset(
    request: Request,
    agent_id: str,
    session_id: str = Form(""),
    user: str = Depends(auth.require_auth),
):
    """Löscht den serverseitig persistierten Verlauf für `(agent_id,
    session_id)` (Quick 260812-hhm, Task 4) — der „Verlauf zurücksetzen"-
    Button ruft dies VOR dem Erzeugen einer neuen `session_id` auf, damit
    „Verlauf zurücksetzen" auch die Server-Kopie löscht, nicht nur den
    Browser-Tab. Same-Origin-Middleware greift für POST automatisch (kein
    Sonderfall nötig). Dieselbe Auth-/Guard-Kombination wie `chat_upload`."""
    if agent_id not in agents_io.list_agent_ids():
        raise HTTPException(status_code=404, detail="agent not found")
    if not session_id:
        return {"ok": True, "geloescht": 0}
    geloescht = chat_history.clear_session(agent_id, session_id)
    return {"ok": True, "geloescht": geloescht}


def _save_response(request: Request, is_htmx: bool, ok: bool, message: str, redirect_query: str) -> object:
    """Section-Save (HTMX) → inline HTML-Fragment.
    Full-Form-Save → Redirect zu / mit Query-Flag."""
    if is_htmx:
        css = "save-ok" if ok else "save-err"
        icon = "&#10003;" if ok else "&#9888;"
        return HTMLResponse(f'<span class="{css}">{icon} {message}</span>')
    return RedirectResponse(f"/?{redirect_query}={quote(message) if not ok else '1'}", status_code=303)


def _pruefe_urlaubszeitraum(von: str, bis: str, aktiviert: bool) -> str | None:
    """Validiert den ueber das Formular eingetragenen Urlaubszeitraum.
    Rueckgabe: Fehlermeldung fuer den Betreiber oder `None`, wenn alles passt.

    Leer + leer ist gueltig, SOLANGE die Funktion nicht aktiviert wird — so
    laesst sich ein vergangener Urlaub austragen. Wird sie aktiviert, sind
    beide Daten Pflicht: eine aktive Urlaubsantwort ohne Zeitraum wuerde nie
    greifen und der Betreiber saehe nur einen gesetzten Haken."""
    from datetime import date as _date

    if not von and not bis:
        if aktiviert:
            return (
                "Für die Urlaubs-Autoantwort müssen Von- und Bis-Datum gesetzt sein — "
                "ohne Zeitraum würde sie nie greifen."
            )
        return None

    if not von or not bis:
        return "Bitte Von- UND Bis-Datum des Urlaubs angeben (Format JJJJ-MM-TT)."

    try:
        von_datum = _date.fromisoformat(von)
        bis_datum = _date.fromisoformat(bis)
    except ValueError:
        return "Urlaubsdatum bitte im Format JJJJ-MM-TT angeben (z. B. 2026-09-01)."

    if bis_datum < von_datum:
        return "Das Bis-Datum des Urlaubs liegt vor dem Von-Datum."
    return None


@app.post("/save")
@limiter.limit("20/minute")
def save(
    request: Request,
    agent_id: str = Form(""),
    imap_user: str | None = Form(None),
    imap_password: str | None = Form(None),
    llm_api_key: str | None = Form(None),
    imap_drafts_folder: str | None = Form(None),
    autostart_enabled: str | None = Form(None),
    context_md: str | None = Form(None),
    style_md: str | None = Form(None),
    style_note: str | None = Form(None),
    vorlagen_md: str | None = Form(None),
    sortierregeln_md: str | None = Form(None),
    enable_style_adaption: str | None = Form(None),
    enable_draft_creation: str | None = Form(None),
    draft_section_submitted: str | None = Form(None),
    enable_auto_sort: str | None = Form(None),
    auto_sort_dry_run: str | None = Form(None),
    auto_sort_section_submitted: str | None = Form(None),
    intelligenz_modus: str | None = Form(None),
    intelligenz_section_submitted: str | None = Form(None),
    enable_urlaubsantwort: str | None = Form(None),
    urlaub_dry_run: str | None = Form(None),
    urlaub_von: str | None = Form(None),
    urlaub_bis: str | None = Form(None),
    urlaub_section_submitted: str | None = Form(None),
    privacy_consent: str | None = Form(None),
    new_agent_name: str | None = Form(None),
    user: str = Depends(auth.require_auth),
):
    is_htmx = request.headers.get("HX-Request") == "true"

    # UMBAU-D5: „Neuer Agent"-Popup schickt beim ersten Speichern KEINE agent_id
    # mit (der Agent existiert ja noch nicht) — stattdessen new_agent_name. VOR
    # jeglicher weiteren Verarbeitung (Consent-Gate, agent-spezifische Updates)
    # wird der Agent hier angelegt und agent_id gesetzt, damit der restliche
    # Handler unveraendert mit einem echten agent_id weiterlaeuft (fuehlt sich fuer
    # den Nutzer wie EIN Submit an — die Zweistufigkeit des Backends bleibt intern).
    if not agent_id and (new_agent_name or "").strip():
        agent_id = agents_io.slugify(new_agent_name)
        agents_io.write_env(agent_id, {"AGENT_ENABLED": "false"})

    # Review IN-04: context_md/style_md serverseitig auf 64 KB begrenzen
    # (Disk-DoS + spaeter voller Inhalt in jedem Prompt). VOR jeglichem Write
    # dieses Requests geprueft -> kein Teil-Schreiben ueber dem Limit.
    for field_label, field_val in (("context.md", context_md), ("style.md", style_md)):
        if field_val is not None and len(field_val.encode("utf-8")) > MAX_CONFIG_MD_BYTES:
            return _save_response(
                request, is_htmx, False,
                f"{field_label} ist zu groß (max. 64 KB) — nicht gespeichert.",
                "error",
            )

    existing = config_io.read_env_raw()

    # --- Datenschutz-Zustimmung (D-68): gated NUR den echten Agent-Konfig-Save
    # (IMAP-Creds/API-Key/context.md) — WebUI-Login-, Autostart- oder
    # Schreibstil-Section-Saves duerfen NIE daran scheitern. Ist der Consent
    # bereits im Root-.env persistiert, ist keine erneute Zustimmung noetig.
    consent_relevant_submitted = any(
        v is not None for v in (imap_user, imap_password, llm_api_key, context_md)
    )
    consent_already_persisted = (existing.get("PRIVACY_CONSENT_ACCEPTED") or "").strip().lower() == "true"
    consent_truthy_this_request = (privacy_consent or "").strip().lower() in _CONSENT_TRUTHY
    if consent_relevant_submitted and not consent_already_persisted:
        if not consent_truthy_this_request:
            return _save_response(
                request, is_htmx, False,
                "Bitte stimmen Sie den Datenschutzbestimmungen zu, bevor Sie Zugangsdaten speichern.",
                "error",
            )
        config_io.write_env(
            {
                "PRIVACY_CONSENT_ACCEPTED": "true",
                "PRIVACY_CONSENT_AT": datetime.now(timezone.utc).isoformat(),
                "PRIVACY_CONSENT_VERSION": PRIVACY_CONSENT_VERSION,
            }
        )
        existing = config_io.read_env_raw()

    # --- Cred-Transition-Erfassung (Migrations-Guard, D-53/D-54/SC5): Ist-Zustand VOR
    # jeglichem Write dieses Requests. "style.md fehlt" ist BEWUSST NICHT der
    # Auto-Trigger unten — nur der echte Cred-Uebergang unvollstaendig->vollstaendig
    # durch DIESES Request-Delta. Ein migrierter Agent, dessen Creds schon vorher
    # komplett waren, kann diese Bedingung nie erfuellen (creds_before_complete=True).
    existing_agent_env: dict[str, str] = {}
    if agent_id:
        try:
            existing_agent_env = agents_io.read_env_raw(agent_id)
        except ValueError:
            existing_agent_env = {}
    creds_before_complete = all(
        (existing_agent_env.get(k) or "").strip() for k in ("IMAP_USER", "IMAP_PASSWORD", "LLM_API_KEY")
    )

    # WebUI-Login-Passwort lebt seit 260722-jrq exklusiv in POST /setup bzw.
    # POST /password — /save entschlackt (D-09), kein WEBUI_USER/WEBUI_PASSWORD
    # mehr in dieser Route.
    global_updates: dict[str, str] = {}
    if autostart_enabled is not None:
        global_updates["AUTOSTART_ENABLED"] = "true" if autostart_enabled in ("true", "on", "1") else "false"
    if global_updates:
        config_io.write_env(global_updates)

    # --- Schreibstil-Section (STY-03): eigenes Fieldset, unabhaengig speicherbar ---
    style_fields_submitted = any(v is not None for v in (style_md, style_note, enable_style_adaption))
    if style_fields_submitted:
        if not agent_id:
            return _save_response(request, is_htmx, False, "Kein Agent ausgewählt.", "error")
        try:
            if style_md is not None:
                agents_io.write_style_md_atomic(agent_id, style_md)
            if style_note is not None:
                agents_io.write_style_note_atomic(agent_id, style_note)
            if enable_style_adaption is not None:
                agents_io.write_env(
                    agent_id,
                    {
                        "ENABLE_STYLE_ADAPTION": (
                            "true" if enable_style_adaption in ("true", "on", "1") else "false"
                        )
                    },
                )
        except ValueError:
            return _save_response(request, is_htmx, False, "Ungültiger Agent.", "error")

    # --- Vorlagen-Section (Phase 13, MBOX-16): eigener submitted-Guard nach dem
    # Muster der Schreibstil-Section, damit die Vorlagen unabhaengig von den
    # uebrigen Abschnitten gespeichert werden koennen.
    if vorlagen_md is not None:
        if not agent_id:
            return _save_response(request, is_htmx, False, "Kein Agent ausgewählt.", "error")
        try:
            agents_io.write_vorlagen_md_atomic(agent_id, vorlagen_md)
        except ValueError:
            return _save_response(request, is_htmx, False, "Ungültiger Agent.", "error")

    # --- Sortierregeln-Section (Phase 14, Plan 14-01, AUTO-02): eigener
    # submitted-Guard nach demselben Muster wie die Vorlagen-Section — die
    # Kategorien-Datei ist unabhängig von der Checkbox weiter unten speicherbar
    # (z. B. um Kategorien vorzubereiten, bevor die Funktion aktiviert wird).
    if sortierregeln_md is not None:
        if not agent_id:
            return _save_response(request, is_htmx, False, "Kein Agent ausgewählt.", "error")
        try:
            agents_io.write_sortierregeln_md_atomic(agent_id, sortierregeln_md)
        except ValueError:
            return _save_response(request, is_htmx, False, "Ungültiger Agent.", "error")

    # --- Automatische-Entwürfe-Section (260725-tqr): eigener submitted-Guard, damit
    # unabhängige Section-Saves das Flag nicht überschreiben. Flag ist kein Secret
    # (Klartext-.env via generischem write_env). Default an (Absenz = kein Write).
    #
    # WICHTIG (Fix 260725-tqr): Der Guard hängt am verborgenen Marker-Feld
    # `draft_section_submitted`, NICHT an der Checkbox selbst. Eine nicht
    # angehakte Checkbox sendet im HTML gar nichts (enable_draft_creation=None) —
    # hinge der Guard an der Checkbox, ließe sich das Flag über die WebUI nie
    # ausschalten. Der versteckte Marker wird immer mitgesendet, wenn das
    # Agentenformular abgeschickt wird (analog zum expliziten 'false' beim
    # Autostart-Umschalter), und unterscheidet so "Section abgeschickt, Box aus"
    # von "andere Section / Teil-Fetch an /save". ---
    if draft_section_submitted is not None:
        if not agent_id:
            return _save_response(request, is_htmx, False, "Kein Agent ausgewählt.", "error")
        try:
            agents_io.write_env(
                agent_id,
                {
                    "ENABLE_DRAFT_CREATION": (
                        "true" if enable_draft_creation in ("true", "on", "1") else "false"
                    )
                },
            )
        except ValueError:
            return _save_response(request, is_htmx, False, "Ungültiger Agent.", "error")

    # --- Automatisches-Einsortieren-Section (Phase 14, Plan 14-01, AUTO-01/05):
    # gleiches Marker-Muster wie die Automatische-Entwürfe-Section — der Guard
    # hängt am verborgenen Feld `auto_sort_section_submitted`, NICHT an den
    # Checkboxen selbst (eine nicht angehakte Checkbox sendet gar nichts, sonst
    # ließen sich beide Flags über die WebUI nie ausschalten). BEIDE Flags
    # teilen sich EINEN Marker, weil sie im selben Fieldset zusammen abgeschickt
    # werden. Defaults unterscheiden sich bewusst (siehe config.py): fehlt
    # ENABLE_AUTO_SORT -> aus; fehlt AUTO_SORT_DRY_RUN -> an (sicherer Default,
    # solange die Kategorien noch nicht geprüft sind).
    if auto_sort_section_submitted is not None:
        if not agent_id:
            return _save_response(request, is_htmx, False, "Kein Agent ausgewählt.", "error")
        try:
            agents_io.write_env(
                agent_id,
                {
                    "ENABLE_AUTO_SORT": (
                        "true" if enable_auto_sort in ("true", "on", "1") else "false"
                    ),
                    "AUTO_SORT_DRY_RUN": (
                        "true" if auto_sort_dry_run in ("true", "on", "1") else "false"
                    ),
                },
            )
        except ValueError:
            return _save_response(request, is_htmx, False, "Ungültiger Agent.", "error")

    # --- Intelligenz-Modus (Betreiber-Wunsch 2026-08-24): dasselbe Marker-Muster.
    # Ein unbekannter Wert wird hier ABGELEHNT statt still auf "standard"
    # gesetzt: das Formular ist die Stelle, an der ein Mensch bewusst waehlt —
    # eine stille Korrektur wuerde ihm etwas anderes speichern als er sieht.
    # (Der Agent selbst faellt beim LESEN auf "standard" zurueck, damit ein von
    # Hand editierter Tippfehler ihn nicht am Starten hindert.)
    if intelligenz_section_submitted is not None:
        if not agent_id:
            return _save_response(request, is_htmx, False, "Kein Agent ausgewählt.", "error")
        modus = (intelligenz_modus or "standard").strip().lower()
        if modus not in ("standard", "hoch"):
            return _save_response(
                request, is_htmx, False,
                f"'{intelligenz_modus}' ist kein Intelligenz-Modus (standard oder hoch).",
                "error",
            )
        try:
            agents_io.write_env(agent_id, {"INTELLIGENZ_MODUS": modus})
        except ValueError:
            return _save_response(request, is_htmx, False, "Ungültiger Agent.", "error")

    # --- Urlaubs-Autoantwort-Section (URL-01..09): dasselbe Marker-Muster wie
    # die beiden Sections oberhalb. Der Zeitraum wird HIER validiert und nicht
    # nur im Agenten: ein Tippfehler wie "01.09.2026" (deutsche Schreibweise)
    # oder ein Bis-vor-Von wuerde den Lauf sonst still nie starten, und der
    # Betreiber saehe im Formular einen eingetragenen Urlaub, der nie greift.
    if urlaub_section_submitted is not None:
        if not agent_id:
            return _save_response(request, is_htmx, False, "Kein Agent ausgewählt.", "error")
        von = (urlaub_von or "").strip()
        bis = (urlaub_bis or "").strip()
        an = enable_urlaubsantwort in ("true", "on", "1")

        fehler = _pruefe_urlaubszeitraum(von, bis, an)
        if fehler:
            return _save_response(request, is_htmx, False, fehler, "error")

        try:
            agents_io.write_env(
                agent_id,
                {
                    "ENABLE_URLAUBSANTWORT": "true" if an else "false",
                    "URLAUB_DRY_RUN": (
                        "true" if urlaub_dry_run in ("true", "on", "1") else "false"
                    ),
                    "URLAUB_VON": von,
                    "URLAUB_BIS": bis,
                },
            )
        except ValueError:
            return _save_response(request, is_htmx, False, "Ungültiger Agent.", "error")

    # --- Agent-spezifische Updates (D-51: Provider-Autodetect aus llm_api_key) ---
    agent_fields_submitted = any(
        v is not None for v in (imap_user, imap_password, llm_api_key, imap_drafts_folder, context_md)
    )
    if agent_fields_submitted:
        if not agent_id:
            return _save_response(request, is_htmx, False, "Kein Agent ausgewählt.", "error")

        updates: dict[str, str] = {}
        if imap_user is not None:
            updates["IMAP_USER"] = imap_user
            # Own-Sender-Filter: IMAP_USER == OWN_EMAIL_ADDRESS für 99% aller Setups.
            # WR-05: eine BEWUSST abweichend gesetzte OWN_EMAIL_ADDRESS (z.B. Shared-
            # Alias statt Login) darf ein IMAP-Section-Save nicht stillschweigend
            # zurücksetzen — nur defaulten, wenn sie fehlt oder bisher an IMAP_USER
            # gekoppelt war.
            try:
                existing_env = agents_io.read_env_raw(agent_id)
            except ValueError:
                existing_env = {}  # ungültige agent_id -> Fehler kommt unten aus write_env
            existing_own = (existing_env.get("OWN_EMAIL_ADDRESS") or "").strip()
            existing_imap_user = (existing_env.get("IMAP_USER") or "").strip()
            if not existing_own or existing_own == existing_imap_user:
                updates["OWN_EMAIL_ADDRESS"] = imap_user
        if imap_drafts_folder is not None and imap_drafts_folder.strip() != "":
            updates["IMAP_DRAFTS_FOLDER"] = imap_drafts_folder.strip()
        if imap_password is not None and imap_password.strip() != "":
            updates["IMAP_PASSWORD"] = imap_password
        if llm_api_key is not None and llm_api_key.strip() != "" and llm_api_key != agents_io.MASKED:
            provider = llm_detect.detect_llm_provider(llm_api_key)
            if provider is None:
                return _save_response(
                    request, is_htmx, False,
                    "API-Key-Format nicht erkannt — erwartet sk-ant-… (Anthropic), sk-… (OpenAI) oder AIza… (Google)",
                    "error",
                )
            updates["LLM_API_KEY"] = llm_api_key
            updates["LLM_PROVIDER"] = provider

        # API-Key-Pflicht (beide Popup-Modi, Bearbeiten UND Erstellen): der Agent
        # MUSS am Ende einen erkannten Provider haben. Ein in DIESEM Request neu
        # gesetzter Key wurde oben validiert (provider != None). Wird KEIN neuer Key
        # geschickt (Feld leer/maskiert), muss bereits ein erkannter Provider
        # vorliegen — sonst (z. B. „Neuer Agent" ohne/mit unerkanntem Key) Fehler.
        effective_provider = updates.get("LLM_PROVIDER") or (existing_agent_env.get("LLM_PROVIDER") or "").strip()
        if effective_provider not in ("anthropic", "openai", "google"):
            return _save_response(
                request, is_htmx, False,
                "Bitte einen gültigen API-Key hinterlegen — erwartet sk-ant-… (Anthropic), "
                "sk-… (OpenAI) oder AIza… (Google).",
                "error",
            )

        # --- Live-Verbindungsprüfung VOR dem Persistieren (Betreiber-Wunsch: kein
        # kaputter Agent). Geprüft wird NUR, was DIESES Request tatsächlich
        # ändert; bei Fehlschlag wird hart geblockt (nichts geschrieben). Die
        # effektive Konfig = bestehende (entschlüsselte) Agent-.env + Request-Delta.
        imap_changed = imap_user is not None or (imap_password is not None and imap_password.strip() != "")
        llm_key_changed = (
            llm_api_key is not None and llm_api_key.strip() != "" and llm_api_key != agents_io.MASKED
        )
        if imap_changed:
            enc_existing_pw = existing_agent_env.get("IMAP_PASSWORD", "") or ""
            try:
                existing_pw_plain = crypto.decrypt_value(enc_existing_pw) if enc_existing_pw else ""
            except Exception:
                existing_pw_plain = ""
            probe_env = {
                "IMAP_HOST": existing_agent_env.get("IMAP_HOST", ""),
                "IMAP_PORT": existing_agent_env.get("IMAP_PORT", ""),
                "IMAP_USE_SSL": existing_agent_env.get("IMAP_USE_SSL", ""),
                "IMAP_USER": updates.get("IMAP_USER") or (existing_agent_env.get("IMAP_USER") or ""),
                "IMAP_PASSWORD": (
                    imap_password
                    if (imap_password is not None and imap_password.strip() != "")
                    else existing_pw_plain
                ),
            }
            try:
                validate_conn.check_imap(probe_env)
            except validate_conn.ConnectionCheckError as e:
                return _save_response(request, is_htmx, False, f"Nicht gespeichert — {e}", "error")
        if llm_key_changed:
            effective_provider_for_check = updates.get("LLM_PROVIDER") or (
                existing_agent_env.get("LLM_PROVIDER") or ""
            )
            try:
                validate_conn.check_llm(effective_provider_for_check, llm_api_key)
            except validate_conn.ConnectionCheckError as e:
                return _save_response(request, is_htmx, False, f"Nicht gespeichert — {e}", "error")

        try:
            if updates:
                agents_io.write_env(agent_id, updates)
            if context_md is not None:
                agents_io.write_context_md_atomic(agent_id, context_md)
        except ValueError:
            return _save_response(request, is_htmx, False, "Ungültiger Agent.", "error")

        # --- Auto-Extraktion bei Neuanlage-Transition (STY-01, Migrations-Guard) ---
        # Feuert best-effort NUR wenn Creds durch DIESES Request-Delta von
        # unvollstaendig auf vollstaendig wechseln, noch kein style.md existiert
        # und ENABLE_STYLE_ADAPTION != "false". Vollstaendig in try/except (T-06-07,
        # graceful) — ein Fehlschlag der Extraktion darf den Save nie blockieren.
        try:
            agent_env_after = agents_io.read_env_raw(agent_id)
        except ValueError:
            agent_env_after = {}
        creds_after_complete = all(
            (agent_env_after.get(k) or "").strip() for k in ("IMAP_USER", "IMAP_PASSWORD", "LLM_API_KEY")
        )
        style_adaption_enabled = (agent_env_after.get("ENABLE_STYLE_ADAPTION") or "true").strip().lower() != "false"
        if (
            not creds_before_complete
            and creds_after_complete
            and not agents_io.read_style_md(agent_id)
            and style_adaption_enabled
        ):
            try:
                auto_style_md = style_extract.extract_style(agent_id)
                agents_io.write_style_md_atomic(agent_id, auto_style_md)
            except Exception as e:
                logger.warning("auto_style_extract_failed", extra={"agent_id": agent_id, "error": str(e)})

        if "LLM_PROVIDER" in updates:
            provider_label = PROVIDER_LABELS.get(updates["LLM_PROVIDER"], updates["LLM_PROVIDER"])
            return _save_response(request, is_htmx, True, f"Gespeichert — LLM-Provider erkannt: {provider_label}", "saved")

    return _save_response(request, is_htmx, True, "Gespeichert", "saved")


@app.post("/reset", dependencies=[Depends(auth.require_setup)])
@limiter.limit("3/minute")
def reset_all_endpoint(
    request: Request,
    password: str = Form(""),
    user: str = Depends(auth.require_auth),
):
    # Zero-Reset ist irreversibel -> zusätzlich zum Session-Login wird das
    # WebUI-Admin-Passwort erneut abgefragt (Frontend: roter Button -> Ja/Nein ->
    # Passwort-Prompt). Falsches/leeres Passwort blockt hart; es wird nichts
    # gelöscht. Ersetzt das frühere „LÖSCHEN"-Eintippen.
    if not auth.verify_password(password):
        return RedirectResponse(
            f"/?error={quote('Zero-Reset abgebrochen: falsches WebUI-Passwort.')}",
            status_code=303,
        )
    # Alle Agenten (Config + State) entfernen (MA-01/D-50).
    for aid in agents_io.list_agent_ids():
        agents_io.delete_agent(aid)
    # Fernet-Key-Datei löschen (SEC-03: Zero-Reset löscht den Key mit).
    key_file = Path(os.getenv("VIZPATCH_SECRET_KEY_FILE", "/config/.secret_key"))
    key_file.unlink(missing_ok=True)
    # Den einen Agent-Container stoppen/entfernen (Phase-4-Signatur, parameterlos).
    docker_ctrl.stop_and_remove_agent()
    # Root-.env (WebUI-globale Settings) leeren.
    config_io.reset_all()
    return RedirectResponse("/?reset=1", status_code=303)
