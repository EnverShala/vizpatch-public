"""Kompatibilitaets-Wissen ueber die Anthropic-Messages-API.

Bewusst ein EIGENES Modul und nicht Teil von `llm.py`: `llm.py` ist der
Provider-Dispatcher (Anthropic/OpenAI/Google), und `chat_tools.py` darf ihn per
Drift-Waechter NICHT importieren — es baut den Dispatch-Sonderweg bewusst nicht
nach (D-73). Die Modelltabelle hier ist aber kein Dispatch, sondern eine
schlichte Eigenschaft der API, die JEDER Anthropic-Aufrufer braucht: `llm.py`,
der Streaming-Chat, die Sichtungs-/Newsletter-Klassifikation und der
Kontext-Vorschlag. In einem neutralen Modul koennen alle sie benutzen, ohne dass
ein Waechter aufgeweicht werden muss.

Diese Datei existiert in `agent/src/` und `webui/src/` als byte-identische
Kopie (beide Container haben eigene `src`-Baeume) — ein Drift-Test haelt sie
synchron, Muster wie bei `llm.py` selbst.
"""
from __future__ import annotations


# Anthropic-Modelle, die `temperature` NOCH akzeptieren (Praefix-Match).
#
# Ab Sonnet 5 / Opus 5 ist der Parameter abgeschafft: JEDER mitgesendete Wert —
# auch 0.0 — wird mit `400 '`temperature` is deprecated for this model.'`
# abgewiesen. Das hat am 2026-08-24 im scharfen Betrieb zugeschlagen, als der
# Intelligenz-Modus die Draft-Spalte von Sonnet 4.6 auf Sonnet 5 zog: die
# Entwuerfe waren damit in BEIDEN Modi tot (Standard entwirft mit Sonnet 5), und
# der Urlaubs-Automatenfilter fiel im Hoch-Modus in sein fail-open — es ging
# eine Urlaubsantwort an einen Transaktions-Absender, den er sonst abgefangen
# haette.
#
# Die Liste nennt bewusst die AKZEPTIERER, nicht die Ablehner: ein unbekanntes
# (also kuenftiges) Modell bekommt dann KEIN `temperature` und funktioniert,
# statt beim naechsten Modellwechsel dieselbe Bruchstelle erneut aufzureissen.
# Der Preis ist gering — ohne den Parameter faehrt das Modell seinen Default.
ANTHROPIC_MODELLE_MIT_TEMPERATURE: tuple[str, ...] = (
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4-0",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-opus-4-1",
    "claude-opus-4-0",
    "claude-3-",
)


def temperature_kwargs(model: str, temperature: float) -> dict[str, float]:
    """`{"temperature": ...}` nur fuer Modelle, die den Parameter noch nehmen.

    Als `**kwargs` in `messages.create(...)`/`messages.stream(...)` gedacht. Fuer
    alles Unbekannte ein leeres dict — siehe `ANTHROPIC_MODELLE_MIT_TEMPERATURE`.
    Nur fuer den Anbieter Anthropic gedacht: OpenAI hat denselben Bruch mit einer
    eigenen Regel (`llm.py::_call_openai` laesst `temperature` generell weg),
    Google akzeptiert den Parameter weiterhin.
    """
    m = (model or "").strip().lower()
    if any(m.startswith(praefix) for praefix in ANTHROPIC_MODELLE_MIT_TEMPERATURE):
        return {"temperature": temperature}
    return {}
