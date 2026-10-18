"""Aktualizator SCOUT-a: pobiera najnowszą wersję kodu i podmienia pliki programu.

Nie rusza: .env (klucze), data/cache_*.json, wyniki/, .venv.
Źródło ustawiasz w .env:
  SCOUT_UPDATE_URL=https://github.com/<konto>/<repo>/archive/refs/heads/main.zip
  (albo dowolny adres zipa z folderem programu)
Bez SCOUT_UPDATE_URL nic nie robi.

Użycie:  python update.py            – sprawdź i zaktualizuj
         python update.py --sprawdz  – tylko sprawdź, czy jest nowa wersja
"""
import hashlib
import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import requests

BASE = Path(__file__).parent
STATE = BASE / "data" / "wersja.json"
KEEP = {".env", ".venv", "wyniki", "__pycache__", ".git"}
KEEP_PREFIX = ("data/cache_", "data/booksy_miasta", "data/wersja")


def _url() -> str:
    url = os.getenv("SCOUT_UPDATE_URL", "")
    if not url:
        try:
            from dotenv import load_dotenv
            load_dotenv(BASE / ".env")
            url = os.getenv("SCOUT_UPDATE_URL", "")
        except ImportError:
            pass
    return url.strip()


def _keep(rel: str) -> bool:
    top = rel.split("/", 1)[0]
    return top in KEEP or rel.startswith(KEEP_PREFIX) or rel.endswith(".pyc")


def _download(url: str) -> bytes:
    r = requests.get(url, timeout=60, headers={"User-Agent": "SCOUT-updater"})
    r.raise_for_status()
    return r.content


def _current() -> str:
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("hash", "")
    except (OSError, json.JSONDecodeError):
        return ""


def update(check_only: bool = False, log=print) -> str:
    """Zwraca: 'brak-zrodla' | 'aktualne' | 'jest-nowa' | 'zaktualizowano' | 'blad'."""
    url = _url()
    if not url:
        log("Aktualizacje wyłączone (brak SCOUT_UPDATE_URL w .env).")
        return "brak-zrodla"
    try:
        data = _download(url)
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001 - brak internetu nie może zatrzymać programu
        log(f"Nie udało się sprawdzić aktualizacji: {type(e).__name__}")
        return "blad"
    digest = hashlib.sha256(data).hexdigest()
    if digest == _current():
        log("SCOUT jest aktualny.")
        return "aktualne"
    if check_only:
        log("Jest nowa wersja SCOUT-a.")
        return "jest-nowa"

    names = [n for n in zf.namelist() if not n.endswith("/")]
    # zip z GitHuba ma jeden folder nadrzędny (repo-main/…); szukamy folderu, w którym leży main.py
    roots = sorted({n[: -len("main.py")] for n in names if n.endswith("main.py") and n.count("/") <= 2}, key=len)
    if not roots:
        log("Zip nie wygląda na SCOUT-a (brak main.py) – pomijam aktualizację.")
        return "blad"
    root = roots[0]
    changed = 0
    for n in names:
        if not n.startswith(root):
            continue
        rel = n[len(root):]
        if not rel or _keep(rel):
            continue
        dest = BASE / rel
        content = zf.read(n)
        if dest.exists() and dest.read_bytes() == content:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".nowy")
        tmp.write_bytes(content)
        shutil.move(tmp, dest)
        changed += 1
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"hash": digest}), encoding="utf-8")
    log(f"Zaktualizowano SCOUT-a ({changed} plików). Uruchom program ponownie, jeśli był włączony.")
    return "zaktualizowano"


if __name__ == "__main__":
    update(check_only="--sprawdz" in sys.argv)
