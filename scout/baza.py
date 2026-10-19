"""Stała baza leadów + statusy rozmów handlowca (data/baza_leadow.json).

Każde wyszukiwanie dopisuje/aktualizuje firmy (klucz = NIP, domena albo id z mapy), ale NIGDY nie kasuje
statusów, notatek i historii rozmów. Dzięki temu lista „do dzwonienia” przetrwa restart serwera i kolejne
wyszukiwania, a dwie osoby widzą te same statusy.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import threading

import scout_config as config
from .models import Lead

PATH = config.DATA_DIR / "baza_leadow.json"
LOG_PATH = config.DATA_DIR / "aktywnosc.jsonl"   # dziennik: kto, kiedy, co zmienił (tylko dopisywanie)
SEEN_PATH = config.DATA_DIR / "obecnosc.json"    # kiedy ostatnio kto otworzył panel
_lock = threading.Lock()

STATUSY = {
    "nowy": "Do zadzwonienia",
    "nie_odebral": "Nie odebrał",
    "oddzwonic": "Oddzwonić",
    "wyslac_oferte": "Wysłać audyt / ofertę",
    "spotkanie": "Spotkanie umówione",
    "zainteresowany": "Zainteresowany",
    "odmowa": "Odmowa",
    "zly_numer": "Zły numer / nie ta firma",
    "nie_dzwonic": "Nie dzwonić",
}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(db: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PATH)  # atomowo – przerwany zapis nie psuje bazy


def merge(leads: list[Lead], segment: str, scripts: dict[str, dict]) -> int:
    """Dopisuje leady z wyszukiwania. Zwraca liczbę NOWYCH firm."""
    new = 0
    with _lock:
        db = _load()
        for l in leads:
            k = l.key
            rec = db.get(k)
            d = l.to_dict()
            d["segment"] = segment
            d["script"] = scripts.get(k)
            if rec is None:
                new += 1
                db[k] = {"lead": d, "first_seen": _now(), "last_seen": _now(),
                         "crm": {"status": "nowy", "notatka": "", "oddzwonic": "", "kto": "", "czas": "", "historia": []}}
            else:
                rec["lead"], rec["last_seen"] = d, _now()
        _save(db)
    return new


def all_records() -> list[dict]:
    with _lock:
        db = _load()
    out = [{"key": k, **v} for k, v in db.items()]
    out.sort(key=lambda r: -(r["lead"].get("score") or 0))
    return out


def update(key: str, who: str, status: str | None = None, notatka: str | None = None, oddzwonic: str | None = None) -> dict:
    with _lock:
        db = _load()
        rec = db.get(key)
        if rec is None:
            raise KeyError(key)
        c = rec["crm"]
        if status is not None:
            if status not in STATUSY:
                raise ValueError("nieznany status")
            if status != c["status"]:
                c["historia"].append({"czas": _now(), "kto": who, "status": status})
            c["status"] = status
        if notatka is not None:
            c["notatka"] = notatka[:4000]
        if oddzwonic is not None:
            c["oddzwonic"] = oddzwonic[:16]
        c["kto"], c["czas"] = who, _now()
        c["historia"] = c["historia"][-50:]
        _save(db)
        b = rec["lead"]["business"]
        for akcja, wartosc in (("status", STATUSY.get(status, status) if status is not None else None),
                               ("notatka", notatka), ("oddzwonic", oddzwonic)):
            if wartosc is not None and not (akcja == "oddzwonic" and wartosc == ""):
                _log(who, key, b.get("name", ""), akcja, str(wartosc)[:300])
        return {"key": key, **rec}


def _log(who: str, key: str, firma: str, akcja: str, wartosc: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"czas": _now(), "kto": who, "key": key, "firma": firma, "akcja": akcja, "wartosc": wartosc},
                               ensure_ascii=False) + chr(10))
    except OSError:
        pass


def touch(who: str) -> None:
    """Zapamiętuje, że użytkownik właśnie ma otwarty panel (wywoływane przy odświeżaniu listy)."""
    with _lock:
        try:
            seen = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            seen = {}
        seen[who] = _now()
        try:
            SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            SEEN_PATH.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def activity(days: int = 14, limit: int = 300) -> dict:
    """Podsumowanie pracy zespołu: per osoba i dzień (zmiany statusów, notatki, spotkania…) + ostatnie zdarzenia."""
    rows = []
    try:
        with LOG_PATH.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    try:
        seen = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()
    rows = [r for r in rows if r["czas"] >= since]
    users: dict[str, dict] = {}
    for r in rows:
        u = users.setdefault(r["kto"], {"kto": r["kto"], "dni": {}, "razem": {}})
        d = u["dni"].setdefault(r["czas"][:10], {})
        key = r["wartosc"] if r["akcja"] == "status" else r["akcja"]
        for bucket in (d, u["razem"]):
            bucket[key] = bucket.get(key, 0) + 1
    for k in seen:
        users.setdefault(k, {"kto": k, "dni": {}, "razem": {}})
    for u in users.values():
        u["ostatnio_w_panelu"] = seen.get(u["kto"], "")
    return {"uzytkownicy": sorted(users.values(), key=lambda u: u["ostatnio_w_panelu"], reverse=True),
            "zdarzenia": rows[-limit:][::-1], "dni": days}


def csv_export() -> str:
    buf = io.StringIO()
    rows = []
    for r in all_records():
        l, c = r["lead"], r["crm"]
        b, a = l["business"], l.get("audit") or {}
        rows.append({
            "status": STATUSY.get(c["status"], c["status"]), "oddzwonic": c["oddzwonic"], "notatka": c["notatka"],
            "ostatnio": c["czas"], "kto": c["kto"], "wynik": l["score"], "priorytet": l["tier"],
            "wielkosc": b.get("size", ""), "nazwa": b["name"], "nazwa_prawna": b.get("legal_name", ""),
            "forma": b.get("legal_form", ""), "nip": b.get("nip", ""), "krs": b.get("krs", ""),
            "branza": b["category"], "miasto": b["city"], "adres": b["address"],
            "telefon": b.get("phone") or (a.get("phones") or [""])[0],
            "email": ", ".join(dict.fromkeys((b.get("emails") or []) + (a.get("emails") or []))),
            "strona": b.get("website", ""), "powody": " | ".join(l["reasons"][:6]), "pierwsze_zdanie": l.get("pitch", ""),
        })
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    return "﻿" + buf.getvalue()
