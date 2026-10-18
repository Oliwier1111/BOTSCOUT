"""Maile do firm: szkice z szablonu (data/szablon_maila.txt) + wysyłka przez SMTP z hamulcami.

Zasady (wbudowane, nie do wyłączenia z panelu):
- jeden mail na adres, nigdy drugi raz (data/wyslane.json),
- adresy z listy wypisanych (data/wypisani.txt: adres albo @domena) są pomijane,
- dzienny limit (SCOUT_MAILE_DZIENNIE), przerwa między mailami, automat tylko w dni robocze w godzinach,
- każdy mail ma jasnego nadawcę, możliwość odpisania „STOP” i nagłówek List-Unsubscribe,
- bez SMTP_HOST agent tylko przygotowuje szkice.
Pamiętaj: w Polsce wysyłanie ofert e-mailem wymaga zgody odbiorcy (Prawo komunikacji elektronicznej). Szablon
prosi o zgodę na przesłanie propozycji, ale odpowiedzialność za wysyłkę ponosisz Ty.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Callable

import scout_config as config
from .models import Lead

Log = Callable[[str], None]
DRAFTS = config.DATA_DIR / "maile_robocze.json"
SENT = config.DATA_DIR / "wyslane.json"
STOPPED = config.DATA_DIR / "wypisani.txt"
TEMPLATE = config.DATA_DIR / "szablon_maila.txt"
TIERS = {"A": 0, "B": 1, "C": 2}
SKIP_DOMAINS = ("booksy.com", "znanylekarz.pl", "facebook.com", "example.", "sentry.", "wixpress.com", "gmail.test")
SKIP_LOCAL = ("noreply", "no-reply", "donotreply", "mailer-daemon", "abuse", "postmaster")
_lock = threading.Lock()


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def smtp_ready() -> bool:
    return bool(config.SMTP_HOST and config.MAIL_FROM)


def stopped() -> set[str]:
    try:
        return {x.strip().lower() for x in STOPPED.read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")}
    except OSError:
        return set()


def add_stop(address: str) -> None:
    address = address.strip().lower()
    if address and address not in stopped():
        STOPPED.parent.mkdir(parents=True, exist_ok=True)
        with STOPPED.open("a", encoding="utf-8") as f:
            f.write(address + "\n")
    stop = stopped()
    with _lock:
        drafts = _read(DRAFTS, [])
        for d in drafts:
            if _is_stopped(d["to"], stop) and d["status"] == "szkic":
                d["status"] = "wypisany"
        _write(DRAFTS, drafts)


def _is_stopped(address: str, stop: set[str]) -> bool:
    a = address.lower()
    return a in stop or "@" + a.split("@")[-1] in stop


def _usable(address: str) -> bool:
    a = address.lower()
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", a)) and not any(x in a.split("@")[0] for x in SKIP_LOCAL) \
        and not any(x in a.split("@")[1] for x in SKIP_DOMAINS)


def signature() -> str:
    lines = [config.MAIL_FROM_NAME, config.MAIL_COMPANY, *(config.MAIL_SIGNATURE.replace("\\n", "\n").splitlines()), config.MAIL_FROM]
    return "\n".join(x for x in lines if x)


def _reason(l: Lead) -> str:
    n = l.business.name
    if l.lead_type == "BRAK_STRONY":
        return f"nie znalazłem strony internetowej firmy {n}"
    if l.lead_type == "TYLKO_SOCIAL":
        return f"firma {n} jest w sieci tylko jako profil, bez własnej strony"
    if l.lead_type == "STRONA_NIE_DZIALA":
        return f"link do strony firmy {n} w Google prowadzi do strony, która nie działa"
    bad = [r for r in l.reasons if not r.startswith(("aktywna", "świetne", "inne kontakty")) and "opinii" not in r][:2]
    return f"przeglądając stronę firmy {n}, zauważyłem: {'; '.join(bad)}" if bad else f"przeglądając stronę firmy {n}, widzę kilka rzeczy do poprawy"


def render(l: Lead) -> tuple[str, str]:
    raw = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.exists() else "Temat: {nazwa}\n---\n{powod}\n\n{podpis}"
    head, _, body = raw.partition("\n---\n")
    subject = head.replace("Temat:", "", 1).strip()
    fill = {"nazwa": l.business.name, "powod": _reason(l), "miasto": l.business.city, "podpis": signature(),
            "nadawca": config.MAIL_FROM_NAME, "firma": config.MAIL_COMPANY}
    f = lambda t: re.sub(r"\{(\w+)\}", lambda m: fill.get(m.group(1), m.group(0)), t)
    return f(subject), f(body).strip()


def prepare(leads: list[Lead], log: Log = lambda _: None) -> list[dict]:
    """Szkice dla leadów z adresem e-mail. Zachowuje status istniejących szkiców."""
    with _lock:
        old = {d["id"]: d for d in _read(DRAFTS, [])}
        done = {x["email"].lower() for x in _read(SENT, [])}
        stop = stopped()
        out, new, seen = [], 0, set()
        for l in sorted(leads, key=lambda l: -l.score):
            if TIERS.get(l.tier, 9) > TIERS.get(config.OUTREACH_MIN_TIER, 1):
                continue
            addrs = list(dict.fromkeys((l.business.emails or []) + ((l.audit.emails if l.audit else []) or [])))
            addr = next((a for a in addrs if _usable(a)), "")
            if not addr or addr.lower() in seen:
                continue
            seen.add(addr.lower())
            did = hashlib.sha1(addr.lower().encode()).hexdigest()[:12]
            if did in old:
                out.append(old[did]); continue
            status = "wyslano" if addr.lower() in done else "wypisany" if _is_stopped(addr, stop) else "szkic"
            subject, body = render(l)
            out.append({"id": did, "to": addr, "firma": l.business.name, "miasto": l.business.city, "typ": l.lead_type,
                        "wynik": l.score, "subject": subject, "body": body, "status": status, "blad": "", "t": ""})
            new += status == "szkic"
        keep = [d for d in old.values() if d["id"] not in {x["id"] for x in out}]
        _write(DRAFTS, keep + out)
    log(f"Maile: {new} nowych szkiców (łącznie {len(out)} firm z adresem e-mail)")
    return out


def drafts() -> list[dict]:
    return _read(DRAFTS, [])


def sent_today() -> int:
    today = dt.date.today().isoformat()
    return sum(1 for x in _read(SENT, []) if x.get("t", "").startswith(today) and x.get("status") == "sent")


def _in_window(now: dt.datetime) -> bool:
    try:
        a, b = (int(x) for x in config.OUTREACH_HOURS.split("-"))
    except ValueError:
        a, b = 9, 17
    return now.weekday() < 5 and a <= now.hour < b


def build_message(d: dict) -> EmailMessage:
    m = EmailMessage()
    m["From"] = f"{config.MAIL_FROM_NAME} <{config.MAIL_FROM}>" if config.MAIL_FROM_NAME else config.MAIL_FROM
    m["To"] = d["to"]
    m["Subject"] = d["subject"]
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=config.MAIL_FROM.split("@")[-1])
    reply = config.MAIL_REPLY_TO or config.MAIL_FROM
    m["Reply-To"] = reply
    m["List-Unsubscribe"] = f"<mailto:{reply}?subject=STOP>"
    m.set_content(d["body"])
    return m


def _connect():
    if config.SMTP_TLS == "ssl":
        s = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30, context=ssl.create_default_context())
    else:
        s = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
        if config.SMTP_TLS == "starttls":
            s.starttls(context=ssl.create_default_context())
    if config.SMTP_USER:
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
    return s


def send(ids: list[str] | None = None, log: Log = print, auto: bool = False, sleep=time.sleep) -> dict:
    """Wysyła szkice (wskazane id albo wszystkie), z zachowaniem limitów. Zwraca {'wyslano': n, 'pominieto': n, 'powod': ''}."""
    res = {"wyslano": 0, "pominieto": 0, "powod": ""}
    if not smtp_ready():
        res["powod"] = "Brak ustawień poczty (SMTP_HOST i MAIL_FROM w .env)."
        return res
    if auto and not _in_window(dt.datetime.now()):
        res["powod"] = "poza godzinami wysyłki"
        return res
    stop = stopped()
    with _lock:
        todo = [d for d in _read(DRAFTS, []) if d["status"] == "szkic" and (ids is None or d["id"] in ids)]
    left = config.OUTREACH_DAILY_LIMIT - sent_today()
    if left <= 0:
        res["powod"] = f"osiągnięto dzienny limit ({config.OUTREACH_DAILY_LIMIT})"
        return res
    if len(todo) > left:
        res["powod"] = f"dzienny limit {config.OUTREACH_DAILY_LIMIT}: wysyłam {left}, reszta jutro"
        todo = todo[:left]
    try:
        smtp = _connect()
    except Exception as e:  # noqa: BLE001 - złe hasło / host nie może zawiesić agenta
        res["powod"] = f"nie udało się połączyć z pocztą: {type(e).__name__}: {e}"
        log(res["powod"])
        return res
    try:
        for i, d in enumerate(todo):
            if _is_stopped(d["to"], stop) or d["to"].lower() in {x["email"].lower() for x in _read(SENT, [])}:
                _mark(d["id"], "pominieto"); res["pominieto"] += 1; continue
            try:
                smtp.send_message(build_message(d))
            except Exception as e:  # noqa: BLE001
                _mark(d["id"], "blad", f"{type(e).__name__}: {e}")
                log(f"Mail do {d['firma']} nie poszedł: {type(e).__name__}")
                continue
            _mark(d["id"], "wyslano")
            with _lock:
                sent = _read(SENT, [])
                sent.append({"email": d["to"], "firma": d["firma"], "subject": d["subject"], "status": "sent",
                             "t": dt.datetime.now().isoformat(timespec="seconds")})
                _write(SENT, sent)
            res["wyslano"] += 1
            log(f"Wysłano mail do {d['firma']} ({d['to']})")
            if i < len(todo) - 1:
                sleep(config.OUTREACH_DELAY_S)
    finally:
        try:
            smtp.quit()
        except Exception:  # noqa: BLE001
            pass
    return res


def _mark(did: str, status: str, err: str = "") -> None:
    with _lock:
        drafts = _read(DRAFTS, [])
        for d in drafts:
            if d["id"] == did:
                d["status"], d["blad"], d["t"] = status, err, dt.datetime.now().isoformat(timespec="seconds")
        _write(DRAFTS, drafts)
