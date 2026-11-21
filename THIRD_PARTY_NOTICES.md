# Hinweise zu Fremdkomponenten

## Im Repo eingebettet

| Komponente | Version | Lizenz | Quelle | Datei |
|---|---|---|---|---|
| htmx | 2.0.4 (gemessen am Versions-String in der Datei) | Zero-Clause BSD (0BSD), laut htmx-Projekt; hier nicht online geprüft | <https://github.com/bigskysoftware/htmx> | `webui/static/htmx.min.js` |

## Nicht im Repo

Python-Abhängigkeiten (`agent/pyproject.toml`, `webui/pyproject.toml`) und .NET-Abhängigkeiten (`outlook-addin/**/*.csproj`) liegen nicht im Repo. Sie werden beim Bau über die jeweiligen Paketquellen bezogen und unterliegen ihren eigenen Lizenzen.
