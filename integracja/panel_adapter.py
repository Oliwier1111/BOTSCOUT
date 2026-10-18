"""Adapter do wpięcia SCOUT-a w istniejący panel agentów (backend w Pythonie).

Skopiuj cały folder projektu obok backendu panelu (np. jako `agents/scout/`)
i w kodzie panelu:

    from agents.scout.integracja.panel_adapter import SCOUT_META, scout_state, scout_run

    AGENTS.append(SCOUT_META)                    # pojawi się na liście agentów
    state["agents"]["SCOUT"] = scout_state()     # w endpointcie /api/state
    scout_run(miasta=["Poznań"], branze=["dentysta"])   # gdy JARVIS/FRIDAY zleci zadanie

Wszystko działa w tle (wątek), więc nie blokuje serwera panelu.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scout_config as config  # noqa: E402
from scout.agent import ScoutAgent  # noqa: E402

SCOUT_META = {
    "id": "scout",
    "name": config.AGENT_NAME,
    "role": config.AGENT_ROLE,
    "section": config.AGENT_SECTION,
    "initials": "SC",
}

_agent = ScoutAgent()


def scout_state() -> dict:
    """Stan do pokazania w panelu: status (GOTOWY/PRACUJE/BŁĄD), postęp, log, budżet."""
    return _agent.get_state()


def scout_run(miasta=None, branze=None, limit=None, min_wynik=None, ocena_ai=None, pagespeed=None) -> bool:
    """Startuje wyszukiwanie w tle. Zwraca False, jeśli agent już pracuje."""
    return _agent.start(cities=miasta, categories=branze, max_per_query=limit, min_score=min_wynik,
                        visual_ai=ocena_ai, use_pagespeed=pagespeed)


def scout_leads(limit: int = 100) -> list[dict]:
    return [l.to_dict() for l in _agent.leads[:limit]]
