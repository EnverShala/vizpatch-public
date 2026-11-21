"""Streaming-faehiger Adapter fuer die bestehenden Anthropic-Mocks (Quick
260816-0or, T-0or-01).

Ausgangslage: 43 Testfunktionen ueber sechs Dateien patchen
`mocker.patch("src.chat_tools.Anthropic", return_value=mock_client)` und
steuern die Runden ausschliesslich ueber
`mock_client.messages.create.side_effect = [...]`. Viele Tests lesen
anschliessend `mock_client.messages.create.call_args_list[N].kwargs[...]`
aus. Damit T-0or-02 den Loop auf `client.messages.stream(...)` umstellen
kann, OHNE diese ~20 Assertions umzuschreiben, wird hier KEIN neuer
Mock-Mechanismus eingefuehrt, sondern ein duenner Adapter um denselben
`mock_client` gelegt: `messages.stream(**kwargs)` ruft in seinem
`__enter__` weiterhin `mock_client.messages.create(**kwargs)` auf. Dadurch
bleiben `side_effect`-Listen, `call_count` und `call_args_list`
byte-gleich zum Ist-Zustand — ein als `side_effect` hinterlegter
`APIStatusError` fliegt genau dort, wo ihn auch das echte SDK wirft (beim
Verbindungsaufbau, also im `__enter__`).

Fake-API (bewusst minimal, drei Faehigkeiten):
- `text_stream` yieldet die Texte aller `type == "text"`-Bloecke der
  Antwort. Traegt das Antwort-Objekt zusaetzlich das optionale Attribut
  `_stream_chunks` (Liste von Strings), gewinnt DIESES — damit koennen
  Tests exakte Chunk-Grenzen erzwingen (z.B. um einen ueber zwei Chunks
  zerrissenen Pseudonym-Tag nachzustellen, T-0or-03).
- `get_final_message()` liefert genau dieses Antwort-Objekt zurueck (wie
  `client.messages.stream(...).get_final_message()` im echten SDK).
- Traegt das Antwort-Objekt das optionale Attribut `_stream_error`, wird
  es NACH dem letzten Chunk geworfen — fuer den Mitten-im-Stream-
  Fehlerfall (T-0or-03: kein Retry nach bereits gesendetem Text).

Diese Datei aendert KEINEN Produktionscode; solange `chat_tools.py` noch
`messages.create(...)` direkt aufruft (vor T-0or-02), ist der Umbau ein
No-op fuer alle 43 Aufrufstellen.
"""
from __future__ import annotations

from typing import Any, Iterator


class _FakeMessagesStream:
    """Kontextmanager-Ersatz fuer `client.messages.stream(**kwargs)`."""

    def __init__(self, mock_client: Any, kwargs: dict) -> None:
        self._mock_client = mock_client
        self._kwargs = kwargs
        self._response: Any = None

    def __enter__(self) -> "_FakeMessagesStream":
        # Der HTTP-Request ginge beim echten SDK hier raus — hier rufen wir
        # stattdessen `messages.create(**kwargs)` auf demselben `mock_client`
        # auf, damit `side_effect`/`call_args_list` unveraendert funktionieren.
        self._response = self._mock_client.messages.create(**self._kwargs)
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False

    @property
    def text_stream(self) -> Iterator[str]:
        chunks = getattr(self._response, "_stream_chunks", None)
        if chunks is None:
            chunks = [
                block.text
                for block in (self._response.content or [])
                if getattr(block, "type", None) == "text"
            ]
        yield from chunks
        fehler = getattr(self._response, "_stream_error", None)
        if fehler is not None:
            raise fehler

    def get_final_message(self) -> Any:
        return self._response


def as_streaming_client(mock_client: Any) -> Any:
    """Verpasst `mock_client` ein streaming-faehiges `messages.stream(**kwargs)`
    und gibt DENSELBEN `mock_client` zurueck (kein neues Objekt) — dadurch
    bleiben alle bestehenden `mocker.patch("src.chat_tools.Anthropic",
    return_value=mock_client)`-Stellen nach Ersetzen von `return_value=
    mock_client` durch `return_value=as_streaming_client(mock_client)`
    funktional identisch zum Ist-Zustand."""
    mock_client.messages.stream.side_effect = (
        lambda **kwargs: _FakeMessagesStream(mock_client, kwargs)
    )
    return mock_client
