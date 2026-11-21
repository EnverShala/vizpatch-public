"""Drift-Guard (260728-m8g, Stufe 2): `VIZPATCH_DIST_TOKEN`/`VIZPATCH_DIST_REPO`
muessen in BEIDEN Compose-Dateien UND in `deployment/kunde-env.example`
vorkommen — sonst faellt eine der drei Konfigurationsstellen beim naechsten
Umbau unbemerkt hinten runter (Muster `test_model_defaults_sync.py`).

Zusaetzlich: `kunde-env.example` darf NIRGENDS einen echten Token-WERT tragen
(nur die leere Vorlage — der Token wird beim Paketbau vorbefuellt, D-2), und
`VIZPATCH_VERSION` muss dort leer bleiben (sonst meldet die Installation nach
einem Update dauerhaft "Update verfügbar", siehe `update_check`-Docstring).
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_COMPOSE = _REPO_ROOT / "agent" / "docker-compose.yml"
_PHASE4_COMPOSE = _REPO_ROOT / "deployment" / "docker-compose.phase4.yml"
_KUNDE_ENV_EXAMPLE = _REPO_ROOT / "deployment" / "kunde-env.example"


def test_compose_files_carry_dist_token_and_repo_vars():
    for path in (_AGENT_COMPOSE, _PHASE4_COMPOSE):
        text = path.read_text(encoding="utf-8")
        assert "VIZPATCH_DIST_TOKEN" in text, f"VIZPATCH_DIST_TOKEN fehlt in {path}"
        assert "VIZPATCH_DIST_REPO" in text, f"VIZPATCH_DIST_REPO fehlt in {path}"


def test_kunde_env_example_carries_dist_token_and_repo_vars():
    text = _KUNDE_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "VIZPATCH_DIST_TOKEN" in text
    assert "VIZPATCH_DIST_REPO" in text


def test_kunde_env_example_dist_token_value_is_empty():
    text = _KUNDE_ENV_EXAMPLE.read_text(encoding="utf-8")
    match = re.search(r"^VIZPATCH_DIST_TOKEN=(.*)$", text, re.MULTILINE)
    assert match is not None, "VIZPATCH_DIST_TOKEN-Zeile fehlt in kunde-env.example"
    assert match.group(1).strip() == "", (
        "kunde-env.example darf keinen Dist-Token-WERT tragen — der Token wird "
        "beim Paketbau vorbefuellt (D-2), nicht hier eingecheckt."
    )


def test_kunde_env_example_vizpatch_version_stays_empty():
    text = _KUNDE_ENV_EXAMPLE.read_text(encoding="utf-8")
    match = re.search(r"^VIZPATCH_VERSION=(.*)$", text, re.MULTILINE)
    assert match is not None, "VIZPATCH_VERSION-Zeile fehlt in kunde-env.example"
    assert match.group(1).strip() == ""


# --- Werkzeug-/Anhang-Schalter (Fix 2026-08-13) -------------------------------
# Befund: keiner der ENABLE_*-Schalter stand in den Compose-Dateien, ein Wert in
# der Installations-.env erreichte den Container also gar nicht — dieselbe Falle
# wie zuvor bei CHAT_HISTORY_TOKEN_BUDGET. MAX_ATTACHMENT_MB stand ausserdem nur
# in agent/docker-compose.yml, nicht in der Kundenvorlage (Drift). Dieser Guard
# haelt alle drei Konfigurationsstellen zusammen.
_TOOL_ENV_VARS = (
    "ENABLE_FOLDER_DELETE",
    "ENABLE_ATTACHMENT_VISION",
    "MAX_VISION_MB",
    "MAX_VISION_PDF_PAGES",
    "MAX_ATTACHMENT_MB",
    "ENABLE_BILD_VORSCHAU",
    "MAX_PREVIEW_MB",
)


def test_compose_files_carry_tool_env_vars():
    for path in (_AGENT_COMPOSE, _PHASE4_COMPOSE):
        text = path.read_text(encoding="utf-8")
        for var in _TOOL_ENV_VARS:
            assert var in text, (
                f"{var} fehlt in {path} — ein Wert aus der Installations-.env "
                "erreicht den Container dann NICHT (global nicht schaltbar)."
            )


def test_kunde_env_example_documents_tool_env_vars():
    text = _KUNDE_ENV_EXAMPLE.read_text(encoding="utf-8")
    for var in _TOOL_ENV_VARS:
        assert re.search(rf"^{var}=", text, re.MULTILINE), (
            f"{var} ist in kunde-env.example nicht dokumentiert — der Betreiber "
            "kann den Schalter dann nicht finden."
        )


def test_enable_flags_default_to_empty_in_kunde_env_example():
    """Die beiden ENABLE-Flags muessen LEER vorbelegt sein: ein eingecheckter
    Wert wuerde den Code-Default ueberschreiben und waere eine stille
    Verhaltensaenderung beim Kunden."""
    text = _KUNDE_ENV_EXAMPLE.read_text(encoding="utf-8")
    for var in ("ENABLE_FOLDER_DELETE", "ENABLE_ATTACHMENT_VISION", "ENABLE_BILD_VORSCHAU"):
        match = re.search(rf"^{var}=(.*)$", text, re.MULTILINE)
        assert match is not None, f"{var}-Zeile fehlt"
        assert match.group(1).strip() == "", (
            f"{var} muss in der Vorlage leer bleiben (Code-Default gilt)."
        )
