"""SCOUT – lokalna aplikacja w przeglądarce + API dla panelu agentów.

Najprościej: uruchom start.bat (Windows) albo start.command (Mac).
Ręcznie:     python server.py      -> otwiera http://127.0.0.1:8010

Endpointy:
  GET  /                  – strona do wyszukiwania i przeglądania leadów
  GET  /api/scout/state   – status, postęp, log, wydany budżet (format jak /api/state)
  POST /api/scout/run     – start w tle, body JSON: {"miasta": [...], "branze": [...], "limit": 40}
  GET  /api/scout/leads   – ostatnie leady (JSON)
  GET  /api/scout/download/csv|json|html – ostatni plik z wynikami
  GET  /api/scout/config  – które klucze są ustawione + domyślne branże
  POST /api/scout/settings – {"free_only": true/false} tryb tylko darmowy
  POST /api/scout/keys    – zapis kluczy do pliku .env (tylko lokalnie)
"""
import base64
import os
import secrets
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import scout_config as config
from scout.agent import ScoutAgent

app = FastAPI(title="SCOUT – agent do szukania stron")

# Na hostingu (np. Render) ustaw SCOUT_HASLO – wtedy przeglądarka zapyta o hasło (login dowolny).
PASSWORD = os.getenv("SCOUT_HASLO", "")


@app.middleware("http")
async def password_guard(request: Request, call_next):
    if PASSWORD:
        try:
            _, _, pw = base64.b64decode(request.headers.get("authorization", "")[6:]).decode().partition(":")
        except Exception:  # noqa: BLE001 - zły nagłówek = brak dostępu
            pw = ""
        if not secrets.compare_digest(pw.encode(), PASSWORD.encode()):
            return Response("Podaj hasło do SCOUT-a", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="SCOUT", charset="UTF-8"'})
    return await call_next(request)
agent = ScoutAgent()
WEB = Path(__file__).parent / "web"
SHOTS = config.OUTPUT_DIR / "zrzuty"
SHOTS.mkdir(parents=True, exist_ok=True)
app.mount("/zrzuty", StaticFiles(directory=SHOTS), name="zrzuty")

KEY_NAMES = {
    "google": "GOOGLE_PLACES_API_KEY",
    "pagespeed": "PAGESPEED_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class RunRequest(BaseModel):
    miasta: list[str] | None = None
    branze: list[str] | None = None
    limit: int | None = None
    min_wynik: int | None = None
    ocena_ai: bool | None = None
    pagespeed: bool | None = None
    ai_top: int = 25
    mock: bool | None = None
    kontakty: bool | None = None
    segment: str | None = None


class CrmRequest(BaseModel):
    key: str
    status: str | None = None
    notatka: str | None = None
    oddzwonic: str | None = None


class SettingsRequest(BaseModel):
    free_only: bool


class KeysRequest(BaseModel):
    google: str | None = None
    pagespeed: str | None = None
    anthropic: str | None = None


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/scout/state")
def state():
    return agent.get_state()


@app.post("/api/scout/run")
def run(req: RunRequest):
    started = agent.start(cities=req.miasta, categories=req.branze, max_per_query=req.limit,
                          min_score=req.min_wynik, visual_ai=req.ocena_ai, use_pagespeed=req.pagespeed,
                          visual_top=req.ai_top, mock=req.mock, find_contacts=req.kontakty,
                          segment=req.segment if req.segment in ("duze", "male") else None)
    if not started:
        raise HTTPException(409, "SCOUT już pracuje")
    return {"ok": True, "status": "PRACUJE"}


@app.get("/api/scout/leads")
def leads(limit: int = 500):
    out = []
    for l in agent.leads[:limit]:
        d = l.to_dict()
        shot = (d.get("audit") or {}).get("screenshot_path") or ""
        if shot:
            d["screenshot_desktop"] = "/zrzuty/" + Path(shot).name
            d["screenshot_mobile"] = "/zrzuty/" + Path(shot).name.replace("_desktop", "_mobile")
        out.append(d)
    return out


def _who(request: Request) -> str:
    """Kto zmienia status: login z HQ (Caddy przekazuje X-HQ-User), lokalnie „lokalnie”."""
    return (request.headers.get("x-hq-user") or "lokalnie")[:40]


def _is_boss(request: Request) -> bool:
    """Widok aktywności zespołu: tylko Prezes (Caddy przekazuje X-HQ-Rola); lokalnie bez nagłówka – tak."""
    rola = request.headers.get("x-hq-rola")
    return rola is None or rola.lower() == "prezes"


@app.get("/api/scout/aktywnosc")
def aktywnosc(request: Request, dni: int = 14):
    from scout import baza

    if not _is_boss(request):
        raise HTTPException(403, "Ten widok jest tylko dla Prezesa")
    return baza.activity(max(1, min(dni, 90)))


@app.get("/api/scout/baza")
def baza_list(request: Request):
    from scout import baza

    baza.touch(_who(request))
    recs = baza.all_records()
    for r in recs:
        shot = ((r["lead"].get("audit") or {}).get("screenshot_path")) or ""
        if shot:
            r["lead"]["screenshot_desktop"] = "/zrzuty/" + Path(shot).name
            r["lead"]["screenshot_mobile"] = "/zrzuty/" + Path(shot).name.replace("_desktop", "_mobile")
    return {"ja": _who(request), "boss": _is_boss(request), "statusy": baza.STATUSY, "rekordy": recs}


@app.post("/api/scout/crm")
def crm_update(req: CrmRequest, request: Request):
    from scout import baza

    try:
        return baza.update(req.key, _who(request), req.status, req.notatka, req.oddzwonic)
    except KeyError:
        raise HTTPException(404, "Nie ma takiej firmy w bazie")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/scout/baza.csv")
def baza_csv():
    from scout import baza
    import datetime as dt

    return Response(baza.csv_export(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="baza_leadow_{dt.date.today()}.csv"'})


@app.get("/api/scout/download/{kind}")
def download(kind: str):
    path = agent.get_state()["last_files"].get(kind)
    if kind not in ("csv", "json", "html") or not path or not Path(path).exists():
        raise HTTPException(404, "Brak pliku – najpierw uruchom wyszukiwanie")
    return FileResponse(path, filename=Path(path).name)


@app.get("/api/scout/config")
def get_config():
    return {
        "keys": {k: bool(getattr(config, v)) for k, v in KEY_NAMES.items()},
        "categories": config.categories_for(),
        "categories_duze": config.DEFAULT_CATEGORIES_DUZE,
        "categories_male": config.DEFAULT_CATEGORIES_MALE,
        "segment": config.SEGMENT,
        "cities": config.DEFAULT_CITIES,
        "budget_usd": config.TOTAL_BUDGET_USD,
        "mode": config.MODE,
        "free_only": config.FREE_ONLY,
    }


def _write_env(values: dict[str, str]) -> None:
    env_path = config.BASE_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    for name, value in values.items():
        lines = [l for l in lines if not l.startswith(name + "=")] + [f"{name}={value}"]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@app.post("/api/scout/settings")
def save_settings(req: SettingsRequest):
    config.FREE_ONLY = req.free_only
    _write_env({"SCOUT_TYLKO_DARMOWE": "1" if req.free_only else "0"})
    return get_config()


@app.post("/api/scout/keys")
def save_keys(req: KeysRequest):
    env_path = config.BASE_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    for field, name in KEY_NAMES.items():
        value = (getattr(req, field) or "").strip()
        if not value:
            continue
        lines = [l for l in lines if not l.startswith(name + "=")] + [f"{name}={value}"]
        setattr(config, name, value)
        if name == "GOOGLE_PLACES_API_KEY" and not config.PAGESPEED_API_KEY:
            config.PAGESPEED_API_KEY = value
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return get_config()


class OutreachSend(BaseModel):
    ids: list[str] | None = None


class OutreachSettings(BaseModel):
    auto: bool


class OutreachStop(BaseModel):
    email: str


def _outreach_status() -> dict:
    from scout import outreach

    return {"smtp": outreach.smtp_ready(), "auto": config.OUTREACH_AUTO, "limit": config.OUTREACH_DAILY_LIMIT,
            "sent_today": outreach.sent_today(), "hours": config.OUTREACH_HOURS, "from": config.MAIL_FROM,
            "drafts": outreach.drafts()}


@app.get("/api/scout/outreach")
def outreach_state():
    return _outreach_status()


@app.post("/api/scout/outreach/send")
def outreach_send(req: OutreachSend):
    from scout import outreach

    r = outreach.send(req.ids, log=agent.log)
    return {**r, **_outreach_status()}


@app.post("/api/scout/outreach/settings")
def outreach_settings(req: OutreachSettings):
    config.OUTREACH_AUTO = req.auto
    _write_env({"SCOUT_AUTOWYSYLKA": "1" if req.auto else "0"})
    return _outreach_status()


@app.post("/api/scout/outreach/stop")
def outreach_stop(req: OutreachStop):
    from scout import outreach

    outreach.add_stop(req.email)
    return _outreach_status()


def _outreach_loop():
    """Gdy włączona automatyczna wysyłka: co 10 min wysyła szkice w limicie dziennym i w godzinach pracy."""
    import time

    from scout import outreach

    while True:
        time.sleep(600)
        if config.OUTREACH_AUTO and outreach.smtp_ready():
            outreach.send(log=agent.log, auto=True)


UPDATE_STATUS = {"stan": "", "czas": ""}


def _update_loop():
    """Co godzinę sprawdza, czy jest nowa wersja (tylko gdy ustawiono SCOUT_UPDATE_URL)."""
    import datetime as dt
    import time

    import update as updater

    while True:
        res = updater.update(check_only=True, log=lambda _: None)
        UPDATE_STATUS.update(stan=res, czas=dt.datetime.now().strftime("%H:%M"))
        time.sleep(3600)


@app.get("/api/scout/update")
def update_state():
    return UPDATE_STATUS


@app.post("/api/scout/update")
def update_now():
    import update as updater

    res = updater.update(log=lambda _: None)
    UPDATE_STATUS["stan"] = res
    return UPDATE_STATUS


if __name__ == "__main__":
    import uvicorn

    threading.Thread(target=_update_loop, daemon=True).start()
    threading.Thread(target=_outreach_loop, daemon=True).start()

    if os.getenv("PORT"):  # hosting w chmurze (Render, Railway…) podaje port sam
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ["PORT"]), log_level="warning")
    else:
        url = "http://127.0.0.1:8010"
        print(f"\n  SCOUT działa: {url}  (zamknij to okno, żeby wyłączyć)\n")
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")
