"""Dane rejestrowe firmy z darmowych, oficjalnych źródeł (bez klucza):

1. Biała lista VAT (wl-api.mf.gov.pl) – po NIP: nazwa prawna, status VAT, KRS, data rejestracji.
2. KRS (api-krs.ms.gov.pl) – po numerze KRS: forma prawna, kapitał, przeważające PKD, funkcje w zarządzie.
   Uwaga: odpis KRS z API ma zamaskowane imiona i nazwiska – do rozmowy mamy tylko funkcję („PREZES ZARZĄDU”).

Na tej podstawie szacujemy wielkość firmy (MAŁA / ŚREDNIA / DUŻA). To szacunek, nie dane GUS o zatrudnieniu.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time

import requests

import scout_config as config
from .models import Audit, Business

WL_URL = "https://wl-api.mf.gov.pl/api/search/nip/{nip}?date={date}"
KRS_URL = "https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs}?rejestr=P&format=json"
CACHE_PATH = config.DATA_DIR / "cache_rejestrow.json"
CACHE_DAYS = 30
_lock = threading.Lock()

FORMY = (
    ("spółka akcyjna", "S.A."),
    ("prosta spółka akcyjna", "P.S.A."),
    ("spółka z ograniczoną odpowiedzialnością spółka komandytowa", "sp. z o.o. sp.k."),
    ("spółka z ograniczoną odpowiedzialnością", "sp. z o.o."),
    ("spółka komandytowo-akcyjna", "S.K.A."),
    ("spółka komandytowa", "sp.k."),
    ("spółka jawna", "sp.j."),
    ("spółka partnerska", "sp.p."),
    ("fundacja", "fundacja"),
    ("stowarzyszenie", "stowarzyszenie"),
)
NAME_FORM_RE = re.compile(r"\b(s\.?\s?a\.?|sp\.?\s?z\s?o\.?\s?o\.?|sp\.?\s?k\.?|sp\.?\s?j\.?|spółka)\b", re.I)


def _load() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _form_from_name(name: str) -> str:
    low = name.lower()
    for full, short in FORMY:
        if full in low:
            return short
    m = NAME_FORM_RE.search(name)
    if m:
        t = re.sub(r"[\s.]", "", m.group(1).lower())
        return {"sa": "S.A.", "spk": "sp.k.", "spj": "sp.j.", "spółka": ""}.get(t, "sp. z o.o.")
    return ""


def lookup(nip: str = "", krs: str = "") -> dict:
    """Zwraca słownik z danymi rejestrowymi (pusty, gdy nic nie znaleziono). Wynik w pamięci na 30 dni."""
    key = ("nip:" + nip) if nip else ("krs:" + krs)
    with _lock:
        cache = _load()
        hit = cache.get(key)
    if hit and (dt.datetime.now() - dt.datetime.fromisoformat(hit["_t"])).days < CACHE_DAYS:
        return hit["d"]
    d: dict = {}
    s = requests.Session()
    s.headers.update({"User-Agent": "SparkScaleScout/1.1 (+https://spark-scale.pl)"})
    if nip:
        try:
            r = s.get(WL_URL.format(nip=nip, date=dt.date.today().isoformat()), timeout=15)
            subj = (r.json().get("result") or {}).get("subject") if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            subj = None
        if subj:
            d.update(nip=nip, legal_name=subj.get("name") or "", vat_status=subj.get("statusVat") or "",
                     krs=subj.get("krs") or "", registered=subj.get("registrationLegalDate") or "")
            if not d["krs"]:
                d["legal_form"] = _form_from_name(d["legal_name"]) or "jednoosobowa działalność / s.c."
        elif r is not None and getattr(r, "status_code", 0) == 429:
            d["_limit"] = True
    krs = d.get("krs") or krs
    if krs:
        try:
            r = s.get(KRS_URL.format(krs=krs.zfill(10)), timeout=20)
            odpis = r.json().get("odpis", {}) if r.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            odpis = {}
        dane = odpis.get("dane") or {}
        d1, d2, d3 = dane.get("dzial1") or {}, dane.get("dzial2") or {}, dane.get("dzial3") or {}
        podm = d1.get("danePodmiotu") or {}
        d["krs"] = krs
        d["legal_name"] = d.get("legal_name") or podm.get("nazwa", "")
        forma = (podm.get("formaPrawna") or "").lower()
        d["legal_form"] = next((short for full, short in FORMY if full == forma), "") or _form_from_name(d["legal_name"]) or forma
        kap = ((d1.get("kapital") or {}).get("wysokoscKapitaluZakladowego") or {}).get("wartosc")
        if kap:
            try:
                d["capital_pln"] = float(kap.replace(" ", "").replace(",", "."))
            except ValueError:
                pass
        pkd = ((d3.get("przedmiotDzialalnosci") or {}).get("przedmiotPrzewazajacejDzialalnosci") or [{}])[0]
        if pkd.get("opis"):
            d["pkd"] = pkd["opis"].capitalize()[:120]
        board = [(x.get("funkcjaWOrganie") or "").strip() for x in ((d2.get("reprezentacja") or {}).get("sklad") or [])]
        d["board"] = [b for b in board if b][:6]
        reg = (odpis.get("naglowekA") or {}).get("dataRejestracjiWKRS")
        if reg and not d.get("registered"):
            d["registered"] = "-".join(reversed(reg.split(".")))  # DD.MM.RRRR -> RRRR-MM-DD
    if d and not d.get("_limit"):
        with _lock:
            cache = _load()
            cache[key] = {"_t": dt.datetime.now().isoformat(), "d": d}
            _save(cache)
    return d


def estimate_size(b: Business, a: Audit | None) -> tuple[str, list[str]]:
    """Szacunek wielkości z rejestru, strony i opinii. Punkty -> MAŁA (<3) / ŚREDNIA (3–5) / DUŻA (6+)."""
    pts, why = 0, []
    form = b.legal_form or _form_from_name(b.name)
    if form in ("S.A.", "S.K.A.", "P.S.A."):
        pts += 4
        why.append(f"spółka akcyjna ({form})")
    elif form.startswith("sp. z o.o."):
        pts += 2
        why.append(form)
    elif form in ("sp.k.", "sp.j.", "sp.p."):
        pts += 1
        why.append(form)
    elif form:
        why.append(form)
    if b.capital_pln:
        if b.capital_pln >= 1_000_000:
            pts += 3
            why.append(f"kapitał {b.capital_pln:,.0f} zł".replace(",", " "))
        elif b.capital_pln >= 100_000:
            pts += 2
            why.append(f"kapitał {b.capital_pln:,.0f} zł".replace(",", " "))
        elif b.capital_pln > 5000:
            pts += 1
    if b.registered[:4].isdigit():
        age = dt.date.today().year - int(b.registered[:4])
        if age >= 10:
            pts += 1
            why.append(f"na rynku od {b.registered[:4]}")
    if len(b.board) >= 3:
        pts += 1
        why.append(f"zarząd {len(b.board)}-osobowy")
    if a:
        if a.internal_links >= 60:
            pts += 1
            why.append(f"rozbudowana strona ({a.internal_links} podstron w menu)")
        if a.has_careers:
            pts += 1
            why.append("prowadzi rekrutację (zakładka kariera)")
        if a.has_english:
            pts += 1
            why.append("ma wersję obcojęzyczną (eksport/klienci z zagranicy)")
        if a.has_linkedin:
            why.append("profil firmowy na LinkedIn")
    if b.reviews:
        if b.reviews >= 300:
            pts += 2
            why.append(f"{b.reviews} opinii w Google")
        elif b.reviews >= 80:
            pts += 1
    known = bool(b.nip or b.krs or form or b.reviews or (a and a.reachable))
    if not known:
        return "NIEZNANA", ["brak danych – sprawdź w rozmowie"]
    size = "DUŻA" if pts >= 6 else "ŚREDNIA" if pts >= 3 else "MAŁA"
    return size, why


def enrich(businesses: list[Business], audits: dict[int, Audit], log=lambda _: None, limit: int | None = None) -> None:
    """Uzupełnia dane rejestrowe (gdy na stronie jest NIP) i szacuje wielkość każdej firmy."""
    limit = config.REGISTRY_MAX if limit is None else limit
    todo = [(i, b) for i, b in enumerate(businesses) if audits.get(i) and audits[i].nips][:limit]
    if todo:
        log(f"Rejestry (Biała lista VAT + KRS): sprawdzam {len(todo)} firm z NIP-em na stronie")
    found = 0
    for n, (i, b) in enumerate(todo):
        d = lookup(nip=audits[i].nips[0])
        if d.get("_limit"):
            log("Biała lista VAT: dzienny limit zapytań wyczerpany – reszta firm bez danych rejestrowych")
            break
        if d:
            found += 1
            for k in ("nip", "krs", "legal_name", "legal_form", "capital_pln", "pkd", "registered", "board", "vat_status"):
                if d.get(k) not in (None, "", []):
                    setattr(b, k, d[k])
        if n % 5 == 4:
            time.sleep(1)  # grzecznie wobec darmowych API
    if todo:
        log(f"Rejestry: dane znalezione dla {found} z {len(todo)} firm")
    for i, b in enumerate(businesses):
        if not b.legal_form:
            b.legal_form = _form_from_name(b.name)
        b.size, b.size_reasons = estimate_size(b, audits.get(i))
