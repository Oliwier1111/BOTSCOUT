"""Odsiewanie sieciówek, dużych marek i duplikatów."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from urllib.parse import urlparse

import scout_config as config
from .models import Business

# Platformy, które NIE są własną stroną firmy (traktujemy jak brak strony)
SOCIAL_HOSTS = (
    "facebook.com",
    "fb.com",
    "instagram.com",
    "tiktok.com",
    "linkedin.com",
    "booksy.com",
    "znanylekarz.pl",
    "docplanner",
    "olx.pl",
    "oferteo.pl",
    "fixly.pl",
    "google.com",
    "business.site",
    "g.page",
    "linktr.ee",
    "panoramafirm.pl",
    "pkt.pl",
    "allegro.pl",
    "youtube.com",
    "wolt.com",
    "pyszne.pl",
    "glovoapp.com",
    "ubereats.com",
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c)).replace("ł", "l")
    return re.sub(r"[^a-z0-9.\-' ]+", " ", s).strip()


def load_chains() -> list[str]:
    path = config.DATA_DIR / "sieciowki.txt"
    if not path.exists():
        return []
    return [
        _norm(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def domain_of(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_social(url: str) -> bool:
    d = domain_of(url)
    return any(d == h or d.endswith("." + h) or h in d for h in SOCIAL_HOSTS)


def chain_reason(b: Business, chains: list[str], segment: str | None = None) -> str | None:
    name = f" {_norm(b.name)} "
    dom = domain_of(b.website)
    for c in chains:
        if dom and (dom == c or dom.endswith("." + c) or dom.split(".")[0] == c.replace(" ", "")):
            return f"domena sieci ({c})"
        if f" {c} " in name:
            return f"sieć/marka „{c}”"
    limit = config.MAX_REVIEWS_DUZE if (segment or config.SEGMENT) == "duze" else config.MAX_REVIEWS_FOR_LOCAL
    if b.reviews and b.reviews > limit:
        return f"bardzo dużo opinii ({b.reviews}) – raczej duża firma"
    return None


def dedupe(items: list[Business]) -> list[Business]:
    seen: set[str] = set()
    out = []
    for b in items:
        keys = {f"id:{b.source_id}"} if b.source_id else set()
        keys.add("na:" + _norm(b.name) + "|" + _norm(b.address)[:25])
        if keys & seen:
            continue
        seen |= keys
        out.append(b)
    return out


def filter_local(items: list[Business], log=lambda _: None, segment: str | None = None) -> list[Business]:
    chains = load_chains()
    before = len(items)
    items = dedupe(items)
    dups = before - len(items)
    # ta sama (nie-społecznościowa) domena w wielu wizytówkach = sieć
    dom_count = Counter(domain_of(b.website) for b in items if b.website and not is_social(b.website))
    chain_domains = {d for d, n in dom_count.items() if n >= config.CHAIN_DOMAIN_THRESHOLD}
    kept = []
    skipped = 0
    for b in items:
        why = chain_reason(b, chains, segment)
        if not why and domain_of(b.website) in chain_domains:
            why = "wiele placówek pod jedną domeną"
        if why:
            skipped += 1
            continue
        kept.append(b)
    log(f"Odrzucono {skipped} sieciówek i {dups} duplikatów, zostało {len(kept)} lokalnych firm")
    return kept
