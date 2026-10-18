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
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
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
        f'[out:json][timeout:60];area["name"="{city}"]["boundary"="administrative"]->.a;'
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


def search_osm(city: str, category: str, max_results: int, log: Log = _noop) -> list[Business]:
    tags = OSM_TAGS.get(category.lower())
    if not tags:
        log(f"OSM: brak mapowania dla branży „{category}” (dodaj ją do OSM_TAGS)")
        return []
    headers = {"User-Agent": config.USER_AGENT}
    r = None
    for url in OVERPASS_URLS:  # darmowe serwery bywają przeciążone – próbujemy kolejnego
        try:
            r = requests.post(url, data={"data": _overpass_query(city, tags)}, headers=headers, timeout=90)
        except requests.RequestException as e:
            log(f"OpenStreetMap ({url.split('/')[2]}) nie odpowiada: {type(e).__name__}")
            continue
        if r.status_code == 200:
            break
        log(f"OpenStreetMap ({url.split('/')[2]}) błąd {r.status_code}, próbuję inny serwer")
    if r is None or r.status_code != 200:
        log("Nie udało się pobrać firm z OpenStreetMap – sprawdź internet albo spróbuj za kilka minut.")
        return []
    out: list[Business] = []
    for el in r.json().get("elements", []):
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
                types=[k + "=" + v for k, v in t.items() if k in ("amenity", "shop", "office", "craft", "healthcare")],
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
