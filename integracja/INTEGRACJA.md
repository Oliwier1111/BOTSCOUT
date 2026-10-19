# Wpięcie SCOUT-a w panel agentów

SCOUT jest samodzielnym projektem w Pythonie, więc da się go dodać do panelu na dwa sposoby.
Nie ma konfliktu z `config.py` panelu: konfiguracja SCOUT-a nazywa się `scout_config.py`.

Proponowane miejsce w panelu: sekcja **RESEARCH / ANALIZA**, obok KAREN.
Nazwa: **SCOUT**, rola: *Łowca klientów / audyt stron*, inicjały w kafelku: **SC**.

## Sposób 1: import w backendzie panelu (najprościej)

1. Skopiuj cały folder do repozytorium panelu, np. jako `agents/scout_agent/` (bez spacji w nazwie).
2. Doinstaluj pakiety: `pip install -r agents/scout_agent/requirements.txt` i `python -m playwright install chromium`.
3. Dopisz klucze `GOOGLE_PLACES_API_KEY`, `PAGESPEED_API_KEY`, `ANTHROPIC_API_KEY` do `.env` panelu
   albo do `agents/scout_agent/.env`.
4. W backendzie panelu:

```python
import sys
sys.path.insert(0, "agents/scout_agent")          # ścieżka do folderu SCOUT-a

from integracja.panel_adapter import SCOUT_META, scout_state, scout_run, scout_leads

# rejestracja agenta na liście
AGENTS.append(SCOUT_META)

# w endpointcie /api/state
state["agents"]["SCOUT"] = scout_state()
#   -> {"status": "GOTOWY" | "PRACUJE" | "BŁĄD", "progress": {...}, "log": [...],
#       "spent_usd": 0.42, "budget_usd": 2.0, "last_files": {"csv": ..., "json": ..., "html": ...}}

# gdy JARVIS / FRIDAY zleca zadanie (działa w tle, nie blokuje serwera)
scout_run(miasta=["Poznań", "Komorniki"], branze=["dentysta", "komornik"])

# lista leadów do pokazania w panelu
scout_leads(50)
```

Wpisy w `log` mają pola `czas`, `agent`, `tekst`, więc można je dokleić do „LOG ZDARZEŃ BIURA”.

Tryb mock/real: ustaw `SCOUT_MODE=mock` (albo przełącz razem z trybem panelu). Budżet na Claude:
`SCOUT_BUDGET_USD` – po jego wyczerpaniu agent kończy pracę bez oceny wizualnej, tak jak inne agenty przechodzą w mock.

## Sposób 2: osobny serwis HTTP

```bash
cd "Ai agent do szukania stron internetowych"
python server.py          # albo start.bat / start.command
```

Pod http://127.0.0.1:8010 jest gotowa strona do wyszukiwania; panel może też korzystać z API:

| Endpoint | Opis |
|---|---|
| `GET /api/scout/state` | status, postęp, log, wydany budżet |
| `POST /api/scout/run` | start, body: `{"miasta": ["Poznań"], "branze": ["dentysta"], "limit": 40}` |
| `GET /api/scout/leads` | ostatnie leady (ze ścieżkami do zrzutów ekranu) |
| `GET /api/scout/download/csv` | ostatnia lista jako plik CSV |

Panel może co kilka sekund pytać `/api/scout/state`, tak jak pyta swoje `/api/state`.
