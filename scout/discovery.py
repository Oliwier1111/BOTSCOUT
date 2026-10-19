"""Wyszukiwanie lokalnych firm.

1. Google Places API (New) - najlepsze dane (telefon, strona, opinie).
2. OpenStreetMap (Nominatim + Overpass) - darmowy fallback bez klucza.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable

import requests

import scout_config as config
from .models import Business

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELDS = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.rating",
        "places.userRatingCount",
        "places.types",
        "places.primaryType",
        "places.googleMapsUri",
        "places.businessStatus",
        "nextPageToken",
    ]
)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
# Overpass odrzuca (HTTP 406) nagłówki udające przeglądarkę – przedstawiamy się uczciwie, jak każe regulamin OSM.
OSM_UA = "SparkScaleScout/1.1 (+https://spark-scale.pl)"
OSM_CACHE = config.DATA_DIR / "cache_osm.json"
OSM_CACHE_H = 24
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Polska nazwa branży -> tagi OpenStreetMap
OSM_TAGS = {
    "dentysta": ['"amenity"="dentist"', '"healthcare"="dentist"'],
    "gabinet stomatologiczny": ['"amenity"="dentist"'],
    "fizjoterapeuta": ['"healthcare"="physiotherapist"'],
    "weterynarz": ['"amenity"="veterinary"'],
    "kancelaria prawna": ['"office"="lawyer"'],
    "prawnik": ['"office"="lawyer"'],
    "notariusz": ['"office"="notary"'],
    "komornik": ['"office"="bailiff"', '"office"="government"]["name"~"[Kk]omornik"'],
    "biuro rachunkowe": ['"office"="accountant"', '"office"="tax_advisor"'],
    "mechanik samochodowy": ['"shop"="car_repair"'],
    "hydraulik": ['"craft"="plumber"'],
    "elektryk": ['"craft"="electrician"'],
    "fryzjer": ['"shop"="hairdresser"'],
    "salon kosmetyczny": ['"shop"="beauty"'],
    "restauracja": ['"amenity"="restaurant"'],
    "piekarnia": ['"shop"="bakery"'],
    "kwiaciarnia": ['"shop"="florist"'],
    "szkoła językowa": ['"amenity"="language_school"'],
    "psycholog": ['"healthcare"="psychotherapist"', '"healthcare"="psychologist"'],
    "optyk": ['"shop"="optician"'],
    "firma remontowa": ['"craft"="builder"', '"office"="construction_company"'],
    # --- segment: większe firmy
    # biura firm (office=company) – najliczniejsza darmowa grupa firm B2B w OpenStreetMap
    "biura firm": ['"office"="company"'],
    "producent": ['"office"="company"]["industrial"', '"man_made"="works"]["name"', '"industrial"="factory"]["name"'],
    "zakład produkcyjny": ['"man_made"="works"]["name"', '"industrial"="factory"]["name"', '"building"="industrial"]["name"'],
    "hurtownia": ['"shop"="wholesale"', '"wholesale"', '"office"="company"]["name"~"[Hh]urtowni"'],
    "firma budowlana": ['"office"="construction_company"', '"craft"="builder"]["name"~"[Ss]p[.] |S[.]A[.]"'],
    "deweloper": ['"office"="property_developer"', '"office"="company"]["name"~"[Dd]evelop|[Dd]ewelop"'],
    "firma transportowa": ['"office"="logistics"', '"office"="company"]["name"~"[Tt]rans"'],
    "spedycja": ['"office"="logistics"', '"office"="company"]["name"~"[Ss]pedy|[Ll]ogist"'],
    "firma logistyczna": ['"office"="logistics"', '"industrial"="warehouse"]["name"', '"building"="warehouse"]["name"'],
    "salon samochodowy": ['"shop"="car"'],
    "hotel": ['"tourism"="hotel"'],
    "centrum medyczne": ['"amenity"="clinic"', '"healthcare"="clinic"', '"healthcare"="centre"'],
    "klinika": ['"amenity"="clinic"', '"amenity"="hospital"]["operator:type"="private"'],
    "biuro nieruchomości": ['"office"="estate_agent"'],
    "firma IT": ['"office"="it"', '"office"="telecommunication"'],
    "agencja pracy": ['"office"="employment_agency"'],
    "producent mebli": ['"craft"="carpenter"', '"shop"="furniture"]["craft"', '"office"="company"]["name"~"[Mm]eb"'],
    "okna i drzwi": ['"shop"="doors"', '"shop"="windows"', '"craft"="window_construction"'],
    "firma instalacyjna": ['"craft"="hvac"', '"craft"="plumber"]["name"~"[Ss]p[.] |S[.]A[.]"', '"craft"="electrician"]["name"~"[Ss]p[.] |S[.]A[.]"'],
    "maszyny i urządzenia": ['"shop"="trade"', '"office"="company"]["name"~"[Mm]aszyn|[Tt]echni"'],
    "szkoła prywatna": ['"amenity"="school"]["operator:type"="private"', '"amenity"="school"]["name"~"[Pp]rywatn|[Nn]iepubliczn|[Ss]połeczn"'],
    "biuro projektowe": ['"office"="architect"', '"office"="engineer"'],
    "firma ochroniarska": ['"office"="security"'],
    "agencja reklamowa": ['"office"="advertising_agency"'],
    "ubezpieczenia": ['"office"="insurance"'],
}

Log = Callable[[str], None]


def _noop(_: str) -> None:
    pass


# ---------------------------------------------------------------- Google
def search_google_places(query: str, city: str, category: str, max_results: int, log: Log = _noop) -> list[Business]:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": PLACES_FIELDS,
    }
    body = {"textQuery": query, "languageCode": "pl", "regionCode": "PL", "pageSize": 20}
    out: list[Business] = []
    while len(out) < max_results:
        r = requests.post(PLACES_URL, json=body, headers=headers, timeout=config.REQUEST_TIMEOUT)
        if r.status_code != 200:
            log(f"Google Places błąd {r.status_code}: {r.text[:200]}")
            break
        data = r.json()
        for p in data.get("places", []):
            if p.get("businessStatus") and p["businessStatus"] != "OPERATIONAL":
                continue
            out.append(
                Business(
                    name=(p.get("displayName") or {}).get("text", ""),
                    address=p.get("formattedAddress", ""),
                    city=city,
                    category=category,
                    phone=p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber", ""),
                    website=p.get("websiteUri", ""),
                    maps_url=p.get("googleMapsUri", ""),
                    rating=p.get("rating"),
                    reviews=p.get("userRatingCount"),
                    source="google_places",
                    source_id=p.get("id", ""),
                    types=p.get("types", []),
                )
            )
        token = data.get("nextPageToken")
        if not token:
            break
        body["pageToken"] = token
        time.sleep(1.5)  # token potrzebuje chwili, zanim zadziała
    return out[:max_results]


# ---------------------------------------------------------------- OSM
def _overpass_query(city: str, tags: Iterable[str]) -> str:
    parts = "".join(f"nwr(area.a)[{t}];" for t in tags)
    return (
        f'[out:json][timeout:110];area["name"="{city}"]["boundary"="administrative"]->.a;'
        f"({parts});out center tags;"
    )


def osm_contacts(t: dict) -> dict:
    """E-mail i profile social zapisane w tagach OpenStreetMap."""
    emails = [e.strip() for k in ("email", "contact:email") for e in t.get(k, "").split(";") if "@" in e]
    socials = []
    for k, base in (("facebook", "https://www.facebook.com/"), ("instagram", "https://www.instagram.com/")):
        for v in (t.get(f"contact:{k}"), t.get(k)):
            if v:
                socials.append(v if v.startswith("http") else base + v.lstrip("@/"))
    return {"emails": list(dict.fromkeys(emails)), "socials": list(dict.fromkeys(socials)),
            "contacts_note": "kontakty z OpenStreetMap" if emails or socials else ""}


def _overpass_fetch(city: str, category: str, tags, log: Log) -> dict | None:
    """Zapytanie do Overpass z pamięcią na dobę i ponawianiem po kolejnych serwerach (3 rundy)."""
    import datetime as dt
    import json

    key = f"{city.lower()}|{category.lower()}"
    try:
        cache = json.loads(OSM_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(key)
    if hit and (dt.datetime.now() - dt.datetime.fromisoformat(hit["_t"])).total_seconds() < OSM_CACHE_H * 3600:
        log(f"OpenStreetMap: {category} / {city} z pamięci (do {OSM_CACHE_H} h)")
        return hit["data"]
    q = _overpass_query(city, tags)
    for rnd in range(3):
        for url in OVERPASS_URLS:
            host = url.split("/")[2]
            try:
                r = requests.post(url, data={"data": q}, headers={"User-Agent": OSM_UA}, timeout=120)
            except requests.RequestException as e:
                log(f"OpenStreetMap ({host}) nie odpowiada: {type(e).__name__}")
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    log(f"OpenStreetMap ({host}) zwrócił nieczytelną odpowiedź")
                    continue
                remark = str(data.get("remark") or "")
                if "error" in remark.lower() or "timed out" in remark.lower():
                    log(f"OpenStreetMap ({host}) przerwał zapytanie: {remark[:80]}")
                    continue  # pusty wynik z błędem – nie zapisujemy go w pamięci
                cache[key] = {"_t": dt.datetime.now().isoformat(), "data": data}
                try:
                    OSM_CACHE.parent.mkdir(parents=True, exist_ok=True)
                    OSM_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
                return data
            log(f"OpenStreetMap ({host}) błąd {r.status_code}, próbuję inny serwer")
        if rnd < 2:
            log(f"Wszystkie serwery OpenStreetMap zajęte – czekam {15 * (rnd + 1)} s i próbuję ponownie")
            time.sleep(15 * (rnd + 1))
    return None


def search_osm(city: str, category: str, max_results: int, log: Log = _noop) -> list[Business]:
    tags = {k.lower(): v for k, v in OSM_TAGS.items()}.get(category.lower())
    if not tags:
        log(f"OSM: brak mapowania dla branży „{category}” (dodaj ją do OSM_TAGS)")
        return []
    data = _overpass_fetch(city, category, tags, log)
    if data is None:
        log("Nie udało się pobrać firm z OpenStreetMap – darmowe serwery są przeciążone, spróbuj za kilka minut.")
        return []
    out: list[Business] = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        name = t.get("name")
        if not name:
            continue
        street = " ".join(x for x in [t.get("addr:street", ""), t.get("addr:housenumber", "")] if x)
        address = ", ".join(x for x in [street, t.get("addr:postcode", ""), t.get("addr:city", city)] if x)
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        out.append(
            Business(
                name=name,
                address=address,
                city=city,
                category=category,
                phone=t.get("phone") or t.get("contact:phone", ""),
                website=t.get("website") or t.get("contact:website") or t.get("url", ""),
                maps_url=f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat else "",
                source="openstreetmap",
                source_id=f"{el.get('type')}/{el.get('id')}",
                types=[k + "=" + v for k, v in t.items() if k in ("amenity", "shop", "office", "craft", "healthcare", "industrial", "man_made", "tourism")],
                **osm_contacts(t),
            )
        )
        if len(out) >= max_results:
            break
    return out


# ---------------------------------------------------------------- API
def discover(cities: list[str], categories: list[str], max_per_query: int, log: Log = _noop) -> list[Business]:
    use_google = bool(config.GOOGLE_PLACES_API_KEY) and not config.FREE_ONLY
    if config.FREE_ONLY:
        log("Tryb darmowy: szukam w OpenStreetMap (bez Google Places).")
    elif not use_google:
        log("Brak GOOGLE_PLACES_API_KEY – używam OpenStreetMap (mniej danych o firmach).")
    found: list[Business] = []
    for city in cities:
        for cat in categories:
            try:
                if use_google:
                    batch = search_google_places(f"{cat} {city}", city, cat, max_per_query, log)
                else:
                    batch = search_osm(city, cat, max_per_query, log)
                    time.sleep(1)  # grzecznie wobec darmowego serwera
            except requests.RequestException as e:
                log(f"Błąd wyszukiwania „{cat} {city}”: {e}")
                batch = []
            log(f"Znaleziono {len(batch)} firm: {cat} / {city}")
            found.extend(batch)
    return found
