"""Inne kanały kontaktu dla firm bez strony: e-mail, Facebook, Instagram, portale (ZnanyLekarz, Booksy…).

Dla firmy z telefonem, ale bez strony, agent szuka jej w wyszukiwarce (nazwa + miasto, a gdy nic nie
pasuje – numer telefonu). Wynik przyjmuje tylko wtedy, gdy zgadza się telefon albo nazwa i miasto.
Przy okazji sprawdza, czy firma nie ma jednak własnej strony, której brakowało w OpenStreetMap/Google.

Wyszukiwarka: DuckDuckGo (darmowa, bez klucza). Opcjonalnie Brave Search API (BRAVE_API_KEY),
używane tylko poza trybem darmowym.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

import scout_config as config
from .audit import EMAIL_RE, audit_site
from .filters import SOCIAL_HOSTS, _norm
from .models import Audit, Business

Log = Callable[[str], None]
SearchFn = Callable[[str], list[dict]]  # zwraca [{"url", "title", "snippet"}]

CACHE_PATH = config.DATA_DIR / "cache_kontaktow.json"
DDG_URL = "https://html.duckduckgo.com/html/"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

# Portale, które same w sobie są kanałem kontaktu z firmą (rezerwacja, wiadomość)
PORTALS = {
    "znanylekarz.pl": "ZnanyLekarz",
    "booksy.com": "Booksy",
    "docplanner": "ZnanyLekarz",
    "medicover": "",
    "mediclub": "",
    "linkedin.com": "LinkedIn",
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube",
    "panoramafirm.pl": "Panorama Firm",
    "pkt.pl": "PKT",
    "aleo.com": "Aleo",
    "oferteo.pl": "Oferteo",
    "fixly.pl": "Fixly",
}
# Strony, które nigdy nie są stroną konkretnej firmy (katalogi, mapy, media, rejestry)
NOT_OWN_SITE = SOCIAL_HOSTS + (
    "duckduckgo.com", "bing.com", "wikipedia.org", "gowork.pl", "cylex", "firmy.net", "zumi.pl",
    "kontakt.pl", "owg.pl", "ceidg", "krs", "rejestr.io", "infoveriti", "biznes-polska", "e-mapa",
    "targeo.pl", "yelp", "tripadvisor", "otodom", "olx", "gratka", "jakdojade", "mapcarta", "foursquare",
    "poznan.pl", "nfz", "rpwdl", "lekarzebezkolejki", "kliniki.pl", "medme", "doctoralia", "rankinglekarzy",
    "pkt.pl", "aleo.com", "dentysta.eu", "stomatologia.pl", "gabinet.pl", "swiatprzychodni.pl", "whatclinic",
    "pracuj.pl", "medicover", "luxmed", "enel", "fresha.com", "gov.pl", "nil.org.pl", "znanylekarz", "ranking",
    "opinie", "mapy.", "maps.", "apple.com", "uni-", "edu.pl", "youtube", "tiktok", "x.com", "twitter.com",
)
# Słowa, które nic nie mówią o konkretnej firmie (do porównywania nazw)
GENERIC = {
    "gabinet", "gabinety", "stomatologiczny", "stomatologiczna", "stomatologia", "stomatolog", "dentysta",
    "dental", "dent", "clinic", "klinika", "centrum", "praktyka", "lekarska", "lekarz", "specjalistyczny",
    "specjalistyczna", "grupowa", "rodzinna", "fizjoterapia", "fizjoterapii", "fizjoterapeuta", "rehabilitacja",
    "rehabilitacji", "terapii", "gabinet", "poznan", "poznaniu", "studio", "salon", "firma", "uslugi", "medical",
    "med", "estetycznej", "ortodontyczna", "ortodoncja", "protetyki", "zebowej", "chirurgia", "szczekowa",
    "fryzur", "fryzjer", "fryzjerski", "fryzjerska", "fryzjerskie", "fryzjersko", "kosmetyczny", "hair", "beauty",
    "barber", "shop", "urody", "group", "the", "and", "dzieci", "niemowlat", "naturalnej", "kinezyterapii", "fizykoterapii", "twoj", "magic",
}
SKIP_FB_PATHS = ("sharer", "share", "groups", "events", "posts", "photo", "watch", "login", "hashtag", "people", "pages")


# ------------------------------------------------------------------ wyszukiwarki
class SearchBlocked(Exception):
    pass


def search_duckduckgo(query: str) -> list[dict]:
    r = requests.post(
        DDG_URL,
        data={"q": query, "kl": "pl-pl"},
        headers={"User-Agent": config.USER_AGENT, "Accept-Language": "pl-PL,pl;q=0.9", "Referer": DDG_URL},
        timeout=config.REQUEST_TIMEOUT,
    )
    if r.status_code != 200:
        raise SearchBlocked(f"DuckDuckGo {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for res in soup.select(".result"):
        a = res.select_one("a.result__a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if "uddg=" in href:  # link przekierowujący DuckDuckGo
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        if href.startswith("//"):
            href = "https:" + href
        sn = res.select_one(".result__snippet")
        out.append({"url": href, "title": a.get_text(" ", strip=True), "snippet": sn.get_text(" ", strip=True) if sn else ""})
    if not out and "anomaly" in r.text.lower():
        raise SearchBlocked("DuckDuckGo poprosił o captcha")
    return out


def search_brave(query: str) -> list[dict]:
    r = requests.get(
        BRAVE_URL,
        params={"q": query, "country": "PL", "search_lang": "pl", "count": 10},
        headers={"X-Subscription-Token": config.BRAVE_API_KEY, "Accept": "application/json"},
        timeout=config.REQUEST_TIMEOUT,
    )
    if r.status_code != 200:
        raise SearchBlocked(f"Brave Search {r.status_code}")
    return [
        {"url": x.get("url", ""), "title": x.get("title", ""), "snippet": re.sub(r"<[^>]+>", "", x.get("description", ""))}
        for x in (r.json().get("web") or {}).get("results", [])
    ]


def default_search() -> tuple[SearchFn, str]:
    if config.BRAVE_API_KEY and not config.FREE_ONLY:
        return search_brave, "Brave Search"
    return search_duckduckgo, "DuckDuckGo"


# ------------------------------------------------------------------ dopasowanie
def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _phones9(b: Business) -> list[str]:
    """Ostatnie 9 cyfr każdego numeru firmy (numery w OSM bywają rozdzielone ';')."""
    return [d[-9:] for d in (_digits(p) for p in re.split(r"[;,/]", b.phone)) if len(d) >= 9]


def _national(p9: str) -> str:
    """Numer w formie, w jakiej zwykle jest na stronach: 61 874 00 73 albo 601 735 287."""
    if p9[:2] in ("12", "13", "14", "15", "16", "17", "18", "22", "23", "24", "25", "29", "32", "33", "34", "41",
                  "42", "43", "44", "46", "48", "52", "54", "55", "56", "58", "59", "61", "62", "63", "65", "67",
                  "68", "71", "74", "75", "76", "77", "81", "82", "83", "84", "85", "86", "87", "89", "91", "94", "95"):
        return f"{p9[:2]} {p9[2:5]} {p9[5:7]} {p9[7:]}"
    return f"{p9[:3]} {p9[3:6]} {p9[6:]}"


def _name_tokens(name: str) -> list[str]:
    return [t for t in _norm(name).replace(".", " ").replace("-", " ").split() if len(t) >= 3 and t not in GENERIC]


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("pl-pl.")


def _host_in(host: str, items) -> str | None:
    """Pierwszy pasujący wpis: „poznan.pl” = ta domena lub jej subdomena, „ceidg” = fragment nazwy hosta."""
    labels = host.split(".")[:-1]
    for x in items:
        if "." in x.strip("."):
            if host == x or host.endswith("." + x):
                return x
        elif any(x.strip(".-") in label for label in labels):
            return x
    return None


def _clean_social(url: str) -> str:
    """https://www.facebook.com/NazwaStrony/?ref=… -> https://www.facebook.com/NazwaStrony"""
    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    if not parts:
        return ""
    host = _host(url)
    if host in ("facebook.com", "fb.com"):
        if parts[0] == "profile.php":
            fid = parse_qs(p.query).get("id", [""])[0]
            return f"https://www.facebook.com/profile.php?id={fid}" if fid else ""
        if parts[0] == "p" and len(parts) > 1:  # nowy format: /p/Nazwa-123456
            return f"https://www.facebook.com/p/{parts[1]}"
        if parts[0].lower() in SKIP_FB_PATHS:
            return ""
        return f"https://www.facebook.com/{parts[0]}"
    if host == "instagram.com":
        if parts[0] in ("p", "reel", "explore", "accounts", "stories"):
            return ""
        return f"https://www.instagram.com/{parts[0]}"
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")


def _matches(b: Business, r: dict) -> tuple[bool, bool]:
    """(pasuje, telefon_się_zgadza)"""
    text = f"{r['title']} {r['snippet']}"
    phone_hit = any(p in _digits(text) for p in _phones9(b))
    hay = re.sub(r"[^a-z0-9]+", " ", _norm(f"{text} {unquote(r['url'])}"))
    words = set(hay.split())
    tokens = _name_tokens(b.name)
    # całe słowo, a dłuższe nazwy także jako część słowa („laser” w „laserdent”)
    hit = lambda t: t in words or (len(t) >= 5 and t in hay)
    name_hit = bool(tokens) and sum(map(hit, tokens)) >= min(2, len(tokens))
    city_hit = all(w in words for w in _norm(b.city).split()) if b.city else True
    return phone_hit or (name_hit and city_hit), phone_hit


# ------------------------------------------------------------------ główna funkcja
def find_contacts(b: Business, search: SearchFn, log: Log = lambda _: None, delay: float = 2.0) -> dict:
    """Szuka kanałów kontaktu firmy. Zwraca słownik do zapisania w Business i w pamięci podręcznej."""
    found = {"emails": [], "socials": [], "other_contacts": [], "website": "", "confirmed_by_phone": False}
    queries = []
    if _name_tokens(b.name):  # nazwa typu „Stomatologia” nic nie da – wtedy tylko telefon
        cat = b.category if _norm(b.category) not in _norm(b.name) else ""
        queries.append(" ".join(x for x in [b.name, cat, b.city] if x))
    queries += [f"\"{_national(p)}\"" for p in _phones9(b)[:1]]

    for qi, q in enumerate(queries):
        if qi and (found["socials"] or found["website"]):
            break  # po nazwie coś znaleźliśmy – nie trzeba szukać po telefonie
        results = search(q)
        time.sleep(delay)  # grzecznie wobec darmowej wyszukiwarki
        for r in results[:10]:
            ok, phone_hit = _matches(b, r)
            if not ok:
                continue
            found["confirmed_by_phone"] |= phone_hit
            for e in EMAIL_RE.findall(r["snippet"]):
                e = e.strip(".").lower()
                if e not in found["emails"]:
                    found["emails"].append(e)
            host = _host(r["url"])
            if host in ("facebook.com", "fb.com", "instagram.com"):
                u = _clean_social(r["url"])
                if u and u not in found["socials"]:
                    found["socials"].append(u)
                continue
            key = _host_in(host, PORTALS)
            if key is not None:
                portal = PORTALS[key]
                if portal:
                    entry = f"{portal}: {_clean_social(r['url'])}"
                    if not any(x.startswith(portal + ":") for x in found["other_contacts"]):
                        found["other_contacts"].append(entry)
                continue
            if _host_in(host, NOT_OWN_SITE) or found["website"]:
                continue
            # Może firma ma jednak własną stronę – przyjmujemy ją tylko, gdy jest na niej ten sam telefon
            a = audit_site(f"https://{urlparse(r['url']).netloc}")
            if a.reachable and any(p in _digits(" ".join(a.phones)) for p in _phones9(b)):
                found["website"] = a.final_url or r["url"]
                found["confirmed_by_phone"] = True
                found["audit"] = a
                found["emails"] += [e for e in a.emails if e not in found["emails"]]
                found["socials"] += [s for s in (_clean_social(x) for x in a.socials) if s and s not in found["socials"]]
    found["emails"] = found["emails"][:3]
    found["socials"] = found["socials"][:4]
    return found


def apply(b: Business, found: dict) -> None:
    b.emails = list(dict.fromkeys(b.emails + found["emails"]))
    b.socials = list(dict.fromkeys(b.socials + found["socials"]))
    b.other_contacts = list(dict.fromkeys(b.other_contacts + found["other_contacts"]))
    got = found["emails"] or found["socials"] or found["other_contacts"] or found["website"]
    if got:
        how = "zgadza się telefon" if found["confirmed_by_phone"] else "zgadza się nazwa i miasto – sprawdź przed kontaktem"
        b.contacts_note = "; ".join(x for x in [b.contacts_note, f"znalezione w wyszukiwarce ({how})"] if x)
    elif not b.contacts_note:
        b.contacts_note = "w wyszukiwarce nie znaleziono innych kontaktów"


def enrich(businesses: list[Business], log: Log = lambda _: None, search: SearchFn | None = None,
           limit: int | None = None) -> dict[int, Audit]:
    """Uzupełnia kontakty firm (bez strony, z telefonem). Zwraca audyty stron znalezionych przy okazji
    (indeks firmy -> Audit), żeby agent mógł ocenić je jak każdą inną stronę."""
    name = "własna wyszukiwarka"
    if search is None:
        search, name = default_search()
    limit = config.CONTACTS_MAX if limit is None else limit
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    now = dt.datetime.now()
    todo = [i for i, b in enumerate(businesses) if not b.website and b.phone][:limit]
    log(f"Szukam innych kontaktów (e-mail, Facebook, Instagram, portale) dla {len(todo)} firm bez strony – {name}")
    audits: dict[int, Audit] = {}
    hits = 0
    for n, i in enumerate(todo, 1):
        b = businesses[i]
        key = f"{_norm(b.name)}|{''.join(_phones9(b))}"
        hit = cache.get(key)
        if hit and (now - dt.datetime.fromisoformat(hit["_t"])).days < config.CACHE_DAYS:
            found = hit
        else:
            try:
                found = find_contacts(b, search, log)
            except (SearchBlocked, requests.RequestException) as e:
                log(f"Wyszukiwarka przestała odpowiadać ({e}) – pomijam szukanie kontaktów dla pozostałych firm.")
                break
            a = found.pop("audit", None)
            if a:
                audits[i] = a
            cache[key] = {**found, "_t": now.isoformat()}
        if found.get("website"):
            if i not in audits:
                audits[i] = audit_site(found["website"])
            b.website = found["website"]
            log(f"{b.name}: jednak ma stronę – {b.website} (tego samego telefonu używa strona)")
        apply(b, found)
        hits += bool(found["emails"] or found["socials"] or found["other_contacts"])
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    log(f"Inne kontakty znalezione dla {hits} z {len(todo)} firm, {len(audits)} firm ma jednak własną stronę")
    return audits


# ------------------------------------------------------------------ Booksy (bez wyszukiwarki)
# Booksy pokazuje salony z danego miasta i ich profile: Facebook, Instagram, ocena. Telefonu firmy Booksy
# nie udostępnia publicznie, ale sam profil jest kanałem kontaktu (wiadomość / rezerwacja).
BOOKSY = "https://booksy.com/pl-pl"
BOOKSY_CATS = {
    "fryzjer": ["fryzjer", "barber-shop"],
    "barber": ["barber-shop"],
    "salon kosmetyczny": ["salon-kosmetyczny"],
    "kosmetyczka": ["salon-kosmetyczny"],
    "paznokcie": ["paznokcie"],
    "fizjoterapeuta": ["fizjoterapia"],
    "masaż": ["masaz"],
}
BOOKSY_CITIES_PATH = config.DATA_DIR / "booksy_miasta.json"
_BIZ_RE = re.compile(r"https://booksy\.com/pl-pl/\d+_[a-z0-9-]+_[a-z0-9-]+_\d+_[a-z0-9-]+")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm(s)).strip("-")


def _booksy_get(s: requests.Session, url: str) -> str:
    r = s.get(url, timeout=config.REQUEST_TIMEOUT)
    return r.text if r.status_code == 200 else ""


def booksy_city_id(s: requests.Session, city: str) -> str:
    """„Swarzędz” -> „16974_swarzedz”. Duże miasta i okolice zbierane raz, potem z pliku."""
    try:
        cities = json.loads(BOOKSY_CITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cities = {}
    slug = _slug(city)
    if slug not in cities:
        big = set(re.findall(r"/pl-pl/s/fryzjer/(\d+_[a-z0-9-]+)", _booksy_get(s, f"{BOOKSY}/s/fryzjer")))
        for cid in big:
            cities[cid.split("_", 1)[1]] = cid
            for near in re.findall(r"/pl-pl/s/[a-z-]+/(\d+_[a-z0-9-]+)", _booksy_get(s, f"{BOOKSY}/s/fryzjer/{cid}")):
                cities.setdefault(near.split("_", 1)[1], near)
            if slug in cities:
                break
            time.sleep(0.5)
        BOOKSY_CITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        BOOKSY_CITIES_PATH.write_text(json.dumps(cities, ensure_ascii=False), encoding="utf-8")
    return cities.get(slug, "")


def booksy_listing(s: requests.Session, city_id: str, cat_slug: str, max_pages: int = 15) -> dict[str, str]:
    """Nazwa salonu -> adres profilu, ze wszystkich stron wyników."""
    out: dict[str, str] = {}
    for page in range(1, max_pages + 1):
        html = _booksy_get(s, f"{BOOKSY}/s/{cat_slug}/{city_id}" + (f"?businessesPage={page}" if page > 1 else ""))
        new = 0
        for m in re.finditer(r'"item":\{"@type":"[A-Za-z]+","@id":"(https://booksy\.com/pl-pl/[^"]+)","name":"((?:[^"\\]|\\.)*)"', html):
            try:
                url, name = m.group(1), json.loads(f'"{m.group(2)}"')
            except json.JSONDecodeError:
                continue
            if url not in out.values() and city_id in url:
                out[name] = url
                new += 1
        if not new:
            break
        time.sleep(0.5)
    return out


def _same_business(osm_name: str, booksy_name: str, booksy_url: str) -> bool:
    a = _norm(osm_name)
    hay = re.sub(r"[^a-z0-9]+", " ", _norm(booksy_name) + " " + booksy_url.rsplit("/", 1)[-1].replace("-", " "))
    if re.sub(r"[^a-z0-9]", "", a) == re.sub(r"[^a-z0-9]", "", _norm(booksy_name)):
        return True
    tokens = _name_tokens(osm_name)
    if not tokens:
        return False
    words = set(hay.split())
    if len(tokens) == 1:  # jedno słowo („Magda”) musi wystąpić w całości, inaczej „Magdalena” też by pasowała
        return tokens[0] in words and (len(tokens[0]) >= 5 or len(_name_tokens(booksy_name)) <= 2)
    return all(t in words or (len(t) >= 6 and t in hay) for t in tokens)


def booksy_profile(s: requests.Session, url: str) -> dict:
    html = _booksy_get(s, url)
    out = {"socials": [], "rating": None, "reviews": None}
    m = re.search(r'"sameAs":(\[[^\]]*\])', html)
    if m:
        try:
            out["socials"] = [u for u in json.loads(m.group(1)) if "booksy" not in u]
        except json.JSONDecodeError:
            pass
    m = re.search(r'"ratingValue":([\d.]+),"reviewCount":(\d+)', html)
    if m:
        out["rating"], out["reviews"] = round(float(m.group(1)), 1), int(m.group(2))
    return out


def enrich_booksy(businesses: list[Business], log: Log = lambda _: None) -> None:
    """Dla firm bez strony z branż obecnych na Booksy: profil Booksy, Facebook, Instagram, ocena."""
    todo = [b for b in businesses if (not b.website or "booksy.com" in b.website) and b.category.lower() in BOOKSY_CATS]
    if not todo:
        return
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "pl-PL,pl;q=0.9"})
    hits = 0
    try:
        for city in sorted({b.city for b in todo}):
            cid = booksy_city_id(s, city)
            if not cid:
                log(f"Booksy: nie znam miasta „{city}” – pomijam")
                continue
            for cat in sorted({b.category.lower() for b in todo if b.city == city}):
                listing: dict[str, str] = {}
                for slug in BOOKSY_CATS[cat]:
                    listing.update(booksy_listing(s, cid, slug))
                log(f"Booksy: {len(listing)} profili „{cat}” w {city}")
                for b in (x for x in todo if x.city == city and x.category.lower() == cat):
                    match = b.website if "booksy.com" in b.website else next(
                        (u for n, u in listing.items() if _same_business(b.name, n, u)), None)
                    if not match:
                        continue
                    p = booksy_profile(s, match)
                    hits += 1
                    b.other_contacts = list(dict.fromkeys(b.other_contacts + [f"Booksy: {match}"]))
                    b.socials = list(dict.fromkeys(b.socials + p["socials"]))
                    if b.rating is None and p["rating"] is not None:
                        b.rating, b.reviews = p["rating"], p["reviews"]
                    b.contacts_note = "; ".join(x for x in [b.contacts_note, "profil na Booksy (nazwa i miasto się zgadzają)"] if x)
                    time.sleep(0.5)
    except requests.RequestException as e:
        log(f"Booksy nie odpowiada ({type(e).__name__}) – pomijam")
    log(f"Booksy: znaleziono profile {hits} z {len(todo)} firm bez strony")


# ------------------------------------------------------------------ LinkedIn właściciela
# Szukamy publicznych profili: osoby (/in/) i strony firmy (/company/). Przy jednoosobowych firmach nazwa
# często jest nazwiskiem właściciela („Michał Wawrzyniak”) – wtedy szukamy tej osoby w danym mieście.
# Profil zapisujemy tylko wtedy, gdy w tytule/opisie wyniku jest cała nazwa; zawsze do sprawdzenia przed użyciem.
def _person_like(name: str) -> bool:
    words = name.split()
    return 2 <= len(words) <= 3 and all(w.isalpha() and len(w) >= 3 and w[0].isupper() for w in words) and not any(
        _norm(w) in GENERIC for w in words)  # „Orto-Aidi” (z myślnikiem) to nazwa marki, nie nazwisko


def find_linkedin(b: Business, search: SearchFn, delay: float = 2.0) -> list[str]:
    person = _person_like(b.name)
    queries = []
    if person:
        queries.append(f'{b.name} {b.city} {b.category} linkedin')
    else:
        queries.append(f'{b.name} {b.city} właściciel linkedin')
    out: list[str] = []
    for q in queries:
        results = search(q)
        time.sleep(delay)
        for r in results[:10]:
            if _host(r["url"]) != "linkedin.com" and not _host(r["url"]).endswith(".linkedin.com"):
                continue
            path = [x for x in urlparse(r["url"]).path.split("/") if x]
            if len(path) < 2 or path[0] not in ("in", "company"):
                continue
            text = _norm(f"{r['title']} {r['snippet']}")
            text = re.sub(r"[^a-z0-9]+", " ", text)
            tokens = _name_tokens(b.name) if not person else [_norm(w) for w in b.name.split()]
            if not tokens or not all(t in text.split() for t in tokens):
                continue
            if _norm(b.city) not in text and "poland" not in text and "polska" not in text:
                continue
            kind = "właściciel" if path[0] == "in" else "firma"
            entry = f"LinkedIn ({kind}): https://www.linkedin.com/{path[0]}/{path[1]}"
            if not any(e.startswith(f"LinkedIn ({kind})") for e in out):
                out.append(entry)
    return out


def enrich_linkedin(businesses: list[Business], log: Log = lambda _: None, search: SearchFn | None = None,
                    limit: int | None = None) -> None:
    """Dla firm z listy szuka LinkedIna właściciela i firmy (wyniki zapamiętane w cache_kontaktow.json)."""
    if search is None:
        search, name = default_search()
    limit = config.CONTACTS_MAX if limit is None else limit
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    now = dt.datetime.now()
    todo = [b for b in businesses if not b.website][:limit]
    log(f"Szukam LinkedIna właściciela dla {len(todo)} firm bez strony")
    hits = 0
    for b in todo:
        key = f"li|{_norm(b.name)}|{_norm(b.city)}"
        hit = cache.get(key)
        if hit and (now - dt.datetime.fromisoformat(hit["_t"])).days < config.CACHE_DAYS:
            found = hit["linkedin"]
        else:
            try:
                found = find_linkedin(b, search)
            except (SearchBlocked, requests.RequestException) as e:
                log(f"Wyszukiwarka nie odpowiada ({e}) – pomijam LinkedIna dla pozostałych firm.")
                break
            cache[key] = {"linkedin": found, "_t": now.isoformat()}
        if found:
            hits += 1
            b.other_contacts = list(dict.fromkeys(b.other_contacts + found))
            b.contacts_note = "; ".join(x for x in [b.contacts_note, "LinkedIn z wyszukiwarki – sprawdź, czy to właściwa osoba"]
                                        if x and x not in b.contacts_note) or b.contacts_note
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    log(f"LinkedIn znaleziony dla {hits} z {len(todo)} firm")


# ------------------------------------------------------------------ Właściciel / lekarz (ZnanyLekarz.pl)
# Używamy tylko publicznych profili lekarzy, które wyszukiwarka sama zwraca (robots.txt ZnanyLekarz zabrania
# ich wewnętrznej wyszukiwarki /szukaj, więc jej nie odpytujemy). Z profilu bierzemy: imię i nazwisko z tytułem,
# specjalizację, miasto i ocenę. Nie zbieramy nic poza tym, co lekarz sam publikuje jako wizytówkę zawodową.
ZL_DOCTOR_RE = re.compile(r"^https?://(?:www\.)?znanylekarz\.pl/([a-z0-9-]+)/([a-z0-9-]+)/([a-z0-9-]+)/?$")
ZL_MEDICAL_CATS = {"dentysta", "gabinet stomatologiczny", "fizjoterapeuta", "weterynarz", "psycholog", "optyk"}
ZL_SKIP_FIRST = {"placowki", "profil", "szukaj", "ranking-lekarzy", "choroby", "uslugi-zabiegi", "blog", "pytania"}


def zl_parse_profile(html: str) -> dict:
    """Z publicznego profilu: {'name': 'dr n. med. Jan Kowalski', 'rating': 4.9, 'reviews': 120}."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {}
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or "")
        except json.JSONDecodeError:
            continue
        for x in data if isinstance(data, list) else [data]:
            if isinstance(x, dict) and x.get("@type") in ("Physician", "Dentist", "Person") and x.get("name"):
                out["name"] = re.sub(r"\s+", " ", str(x["name"])).strip()
                r = x.get("aggregateRating") or {}
                if r.get("ratingValue") is not None:
                    out["rating"], out["reviews"] = r.get("ratingValue"), r.get("reviewCount")
                return out
    return out


def find_owner(b: Business, search: SearchFn, fetch=None, delay: float = 2.0) -> dict:
    """Właściciel / lekarz firmy. Zwraca {'owner_name', 'owner_info', 'zl_url'} (puste, gdy brak)."""
    fetch = fetch or (lambda url: requests.get(url, timeout=config.REQUEST_TIMEOUT,
                                              headers={"User-Agent": config.USER_AGENT, "Accept-Language": "pl-PL,pl"}).text)
    res: dict = {}
    person = _person_like(b.name)
    if person:  # jednoosobowa firma pod nazwiskiem
        res = {"owner_name": b.name, "owner_info": "nazwa firmy jest nazwiskiem – prawdopodobnie właściciel", "zl_url": ""}
    medical = b.category.lower() in ZL_MEDICAL_CATS
    if not medical:
        return res
    q = f"{b.name} {b.city} znanylekarz" if not person else f"{b.name} {b.category} {b.city} znanylekarz"
    results = search(q)
    time.sleep(delay)
    city = _slug(b.city)
    toks = [_slug(w) for w in b.name.split()] if person else [_slug(t) for t in _name_tokens(b.name)]
    for r in results[:10]:
        m = ZL_DOCTOR_RE.match(r["url"].split("?")[0])
        if not m or m.group(1) in ZL_SKIP_FIRST or m.group(3) != city:
            continue
        slug = m.group(1)
        if person and not all(t in slug.split("-") for t in toks):
            continue
        html = fetch(r["url"]); time.sleep(delay)
        prof = zl_parse_profile(html)
        if not prof.get("name"):
            continue
        page = re.sub(r"[^a-z0-9]+", " ", _norm(BeautifulSoup(html, "html.parser").get_text(" ")))
        if not person and not (toks and all(t in page.split() for t in toks)):
            continue  # profil lekarza, który nie wspomina nazwy gabinetu – to pewnie ktoś inny
        spec = m.group(2).replace("-", " ")
        info = f"{spec}"
        if prof.get("rating") is not None:
            info += f", ZnanyLekarz {prof['rating']} ({prof.get('reviews', 0)} opinii)"
        info += " – lekarz z profilu ZnanyLekarz" + ("" if person else ", pracuje w gabinecie (sprawdź, czy to właściciel)")
        return {"owner_name": prof["name"], "owner_info": info, "zl_url": r["url"].split("?")[0]}
    return res


def enrich_owners(businesses: list[Business], log: Log = lambda _: None, search: SearchFn | None = None,
                  fetch=None, limit: int | None = None) -> None:
    """Dla firm bez strony: imię i nazwisko właściciela (z nazwy firmy albo z profilu na ZnanyLekarz)."""
    if search is None:
        search, _ = default_search()
    limit = config.CONTACTS_MAX if limit is None else limit
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {}
    now = dt.datetime.now()
    todo = [b for b in businesses if not b.website][:limit]
    hits = 0
    blocked = False
    for b in todo:
        key = f"own|{_norm(b.name)}|{_norm(b.city)}"
        hit = cache.get(key)
        if hit and (now - dt.datetime.fromisoformat(hit["_t"])).days < config.CACHE_DAYS:
            res = hit["res"]
        elif blocked:
            res = {"owner_name": b.name, "owner_info": "nazwa firmy jest nazwiskiem – prawdopodobnie właściciel", "zl_url": ""} \
                if _person_like(b.name) else {}
        else:
            try:
                res = find_owner(b, search, fetch)
            except (SearchBlocked, requests.RequestException) as e:
                log(f"Wyszukiwarka/ZnanyLekarz nie odpowiada ({e}) – pomijam szukanie właścicieli w lekarzach.")
                blocked = True
                res = find_owner(b, lambda q: [], fetch)
            else:
                cache[key] = {"res": res, "_t": now.isoformat()}
        if res.get("owner_name"):
            hits += 1
            b.owner_name, b.owner_info = res["owner_name"], res["owner_info"]
            if res.get("zl_url"):
                b.other_contacts = list(dict.fromkeys(b.other_contacts + [f"ZnanyLekarz: {res['zl_url']}"]))
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    log(f"Właściciel / lekarz znaleziony dla {hits} z {len(todo)} firm bez strony")
