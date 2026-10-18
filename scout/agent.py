"""SCOUT – orkiestrator: wyszukaj -> odsiej sieciówki -> audytuj -> oceń -> zapisz."""
from __future__ import annotations

import datetime as dt
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, fields

import scout_config as config
from . import contacts, discovery, export, filters, outreach, pagespeed, scoring, visual
from .audit import audit_site
from .models import Audit, Business, Lead

CACHE_PATH = config.DATA_DIR / "cache_audytow.json"


class ScoutAgent:
    def __init__(self):
        self._lock = threading.Lock()
        self.state = {
            "name": config.AGENT_NAME,
            "role": config.AGENT_ROLE,
            "section": config.AGENT_SECTION,
            "status": "GOTOWY",  # GOTOWY | PRACUJE | BŁĄD
            "mode": config.MODE,
            "progress": {"etap": "", "zrobione": 0, "wszystkie": 0},
            "spent_usd": 0.0,
            "budget_usd": config.TOTAL_BUDGET_USD,
            "last_run": None,
            "last_files": {},
            "log": [],
        }
        self.budget = visual.Budget(config.TOTAL_BUDGET_USD)
        self.leads: list[Lead] = []
        self._running = threading.Lock()

    def start(self, **kwargs) -> bool:
        """Uruchamia run() w tle. Zwraca False, jeśli agent już pracuje."""
        if not self._running.acquire(blocking=False):
            return False

        def job():
            try:
                self.run(**kwargs)
            except Exception:  # noqa: BLE001 - błąd jest już w logu i w statusie
                pass
            finally:
                self._running.release()

        with self._lock:
            self.state["status"] = "PRACUJE"
        threading.Thread(target=job, daemon=True).start()
        return True

    # ----------------------------------------------------------- panel
    def log(self, msg: str) -> None:
        line = {"czas": dt.datetime.now().strftime("%H:%M:%S"), "agent": config.AGENT_NAME, "tekst": msg}
        with self._lock:
            self.state["log"] = (self.state["log"] + [line])[-200:]
        text = f"[{line['czas']}] {config.AGENT_NAME}  {msg}"
        try:
            print(text, flush=True)
        except Exception:  # noqa: BLE001 - konsola bez polskich znaków nie może zatrzymać agenta
            pass
        try:
            config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with (config.OUTPUT_DIR / "scout_log.txt").open("a", encoding="utf-8") as f:
                f.write(f"{dt.date.today()} {text}\n")
        except OSError:
            pass

    def _progress(self, etap: str, done: int, total: int) -> None:
        with self._lock:
            self.state["progress"] = {"etap": etap, "zrobione": done, "wszystkie": total}

    def get_state(self) -> dict:
        with self._lock:
            s = json.loads(json.dumps(self.state, default=str))
        s["spent_usd"] = round(self.budget.spent, 4)
        return s

    # ----------------------------------------------------------- cache
    def _load_cache(self) -> dict:
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_cache(self, cache: dict) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    # ----------------------------------------------------------- run
    def run(
        self,
        cities: list[str] | None = None,
        categories: list[str] | None = None,
        max_per_query: int | None = None,
        min_score: int | None = None,
        visual_ai: bool | None = None,
        use_pagespeed: bool | None = None,
        visual_top: int = 25,
        mock: bool | None = None,
        find_contacts: bool | None = None,
    ) -> list[Lead]:
        cities = cities or config.DEFAULT_CITIES
        categories = categories or config.DEFAULT_CATEGORIES
        max_per_query = max_per_query or config.MAX_RESULTS_PER_QUERY
        min_score = config.MIN_LEAD_SCORE if min_score is None else min_score
        visual_ai = config.USE_VISUAL_AI if visual_ai is None else visual_ai
        use_pagespeed = config.USE_PAGESPEED if use_pagespeed is None else use_pagespeed
        mock = config.MODE == "mock" if mock is None else mock
        find_contacts = config.FIND_CONTACTS if find_contacts is None else find_contacts
        if use_pagespeed and not config.PAGESPEED_API_KEY and not mock:
            use_pagespeed = False
            self.log("Brak klucza PageSpeed – pomijam test szybkości, reszta audytu działa normalnie.")
        self.budget = visual.Budget(config.TOTAL_BUDGET_USD)  # budżet liczony na jedno uruchomienie

        with self._lock:
            self.state["status"] = "PRACUJE"
            self.state["mode"] = "mock" if mock else "real"
        try:
            self.log(f"Start ({'MOCK' if mock else 'REAL'}): {', '.join(cities)} × {len(categories)} branż")
            self._progress("szukanie firm", 0, len(cities) * len(categories))
            found = _mock_businesses() if mock else discovery.discover(cities, categories, max_per_query, self.log)
            local = filters.filter_local(found, self.log)

            audits = self._audit_all(local, use_pagespeed and not mock, mock)

            # Firmy bez strony, ale z telefonem: e-mail, Facebook, Instagram, portale (i czy nie mają jednak strony)
            if find_contacts and not mock:
                self._progress("szukanie kontaktów", 0, 0)
                contacts.enrich_booksy(local, self.log)
                audits.update(contacts.enrich(local, self.log))
                contacts.enrich_owners(local, self.log)
                contacts.enrich_linkedin(local, self.log)

            # Ocena wizualna tylko dla najbardziej obiecujących stron (oszczędza budżet)
            pitches: dict[int, str] = {}
            ok, why = visual.available()
            if visual_ai and not mock and not ok:
                self.log(f"Ocena wizualna AI wyłączona: {why}")
            if visual_ai and not mock and ok:
                pre = [
                    (i, scoring.score_lead(b, audits.get(i)))
                    for i, b in enumerate(local)
                    if audits.get(i) and audits[i].reachable and not audits[i].is_parked and not audits[i].is_social_only
                ]
                # strony "na granicy" zyskują najwięcej na ocenie wizualnej
                pre.sort(key=lambda x: -x[1].score)
                chosen = pre[:visual_top]
                self._progress("ocena wizualna", 0, len(chosen))
                for n, (i, _) in enumerate(chosen, 1):
                    if not self.budget.can_spend():
                        self.log(f"Budżet ${config.TOTAL_BUDGET_USD:.2f} wyczerpany – reszta bez oceny wizualnej")
                        break
                    pitches[i] = visual.review(audits[i], local[i].name, local[i].category, self.budget, self.log)
                    self._progress("ocena wizualna", n, len(chosen))

            leads = [scoring.score_lead(b, audits.get(i), pitches.get(i, "")) for i, b in enumerate(local)]
            leads = sorted((l for l in leads if l.score >= min_score), key=lambda l: -l.score)
            if not found:
                self.log("Nie znaleziono żadnych firm. Sprawdź pisownię miasta (np. „Poznań”, „Komorniki”) i połączenie z internetem.")
            self.leads = leads
            if not mock:
                outreach.prepare(leads, self.log)  # tylko szkice; nic nie jest wysyłane

            files = export.save(leads, config.OUTPUT_DIR, tag=_slug(cities[0]) if len(cities) == 1 else "")
            summary: dict[str, int] = {}
            for l in leads:
                summary[export.TYPE_LABEL[l.lead_type].lower()] = summary.get(export.TYPE_LABEL[l.lead_type].lower(), 0) + 1
            parts = ", ".join(f"{k}: {v}" for k, v in summary.items()) or "brak"
            self.log(f"Gotowe: {len(leads)} leadów ({parts}). Plik: {files['csv'].name}")
            self._progress("gotowe", len(leads), len(leads))
            with self._lock:
                self.state.update(status="GOTOWY", last_run=dt.datetime.now().isoformat(timespec="seconds"),
                                  last_files={k: str(v) for k, v in files.items()})
            return leads
        except Exception as e:
            import traceback

            self.log(f"BŁĄD: {type(e).__name__}: {e}")
            self.log(traceback.format_exc().replace("\n", " | ")[-1500:])
            with self._lock:
                self.state["status"] = "BŁĄD"
            raise

    def _audit_all(self, local: list[Business], use_pagespeed: bool, mock: bool) -> dict[int, Audit]:
        cache = {} if mock else self._load_cache()
        now = dt.datetime.now()
        results: dict[int, Audit] = {}
        todo = []
        for i, b in enumerate(local):
            if not b.website:
                continue
            if mock:
                results[i] = _mock_audit(b)
                continue
            key = filters.domain_of(b.website)
            hit = cache.get(key)
            if hit and (now - dt.datetime.fromisoformat(hit["_t"])).days < config.CACHE_DAYS:
                results[i] = Audit(**{k: v for k, v in hit.items() if k in {f.name for f in fields(Audit)}})
            else:
                todo.append(i)
        self.log(f"Audyt {len(todo)} stron" + ("" if mock else f" ({len(results)} z pamięci podręcznej)"))
        self._progress("audyt stron", 0, len(todo))

        def work(i: int) -> tuple[int, Audit]:
            a = audit_site(local[i].website)
            if use_pagespeed and a.reachable and not a.is_parked and not a.is_social_only:
                pagespeed.run_pagespeed(a, self.log)
            return i, a

        done = 0
        with ThreadPoolExecutor(max_workers=config.AUDIT_WORKERS) as ex:
            for fut in as_completed([ex.submit(work, i) for i in todo]):
                i, a = fut.result()
                results[i] = a
                cache[filters.domain_of(local[i].website)] = {**asdict(a), "_t": now.isoformat()}
                done += 1
                self._progress("audyt stron", done, len(todo))
        if not mock:
            self._save_cache(cache)
        return results


def _slug(s: str) -> str:
    import re
    import unicodedata

    s = "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.replace("ł", "l")).strip("-")


# ----------------------------------------------------------- tryb mock
def _mock_data() -> list[dict]:
    return json.loads((config.DATA_DIR / "mock_firmy.json").read_text(encoding="utf-8"))


def _mock_businesses() -> list[Business]:
    names = {f.name for f in fields(Business)}
    return [Business(**{k: v for k, v in d.items() if k in names}) for d in _mock_data()]


def _mock_audit(b: Business) -> Audit:
    for d in _mock_data():
        if d["name"] == b.name:
            return Audit(url=b.website, final_url=b.website, **d.get("mock_audit", {}))
    return Audit(url=b.website)
