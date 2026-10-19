from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Business:
    """Firma znaleziona w Google Maps / OpenStreetMap."""

    name: str
    address: str = ""
    city: str = ""
    category: str = ""
    phone: str = ""
    website: str = ""
    maps_url: str = ""
    rating: Optional[float] = None
    reviews: Optional[int] = None
    source: str = ""
    source_id: str = ""
    types: list[str] = field(default_factory=list)
    # Inne kanały kontaktu (z OpenStreetMap i z wyszukiwarki – scout/contacts.py)
    emails: list[str] = field(default_factory=list)
    socials: list[str] = field(default_factory=list)
    other_contacts: list[str] = field(default_factory=list)  # np. "ZnanyLekarz: https://..."
    contacts_note: str = ""  # skąd są kontakty i czy są pewne
    owner_name: str = ""  # imię i nazwisko właściciela / lekarza (z nazwy firmy albo ze ZnanyLekarz)
    owner_info: str = ""  # np. "lekarz dentysta, ZnanyLekarz 4.9 (120 opinii) – sprawdź, czy właściciel"
    # Rejestry (Biała lista VAT + KRS) – scout/firma.py
    nip: str = ""
    krs: str = ""
    legal_name: str = ""
    legal_form: str = ""  # np. "sp. z o.o.", "S.A.", "jednoosobowa działalność"
    capital_pln: Optional[float] = None
    pkd: str = ""
    registered: str = ""  # data rejestracji RRRR-MM-DD
    board: list[str] = field(default_factory=list)  # funkcje w zarządzie, np. "PREZES ZARZĄDU" (KRS ukrywa nazwiska)
    vat_status: str = ""
    size: str = ""  # MAŁA | ŚREDNIA | DUŻA (szacunek)
    size_reasons: list[str] = field(default_factory=list)


def _uniq(items: list[str]) -> list[str]:
    return list(dict.fromkeys(x for x in items if x))


@dataclass
class Audit:
    """Wynik audytu strony internetowej."""

    url: str = ""
    final_url: str = ""
    reachable: bool = False
    status_code: Optional[int] = None
    https: bool = False
    ssl_ok: bool = False
    http_redirects_to_https: Optional[bool] = None
    response_ms: Optional[int] = None
    page_kb: Optional[int] = None
    has_viewport: bool = False
    title: str = ""
    meta_description: bool = False
    has_h1: bool = False
    copyright_year: Optional[int] = None
    generator: str = ""
    outdated_tech: list[str] = field(default_factory=list)
    is_parked: bool = False
    is_under_construction: bool = False
    is_social_only: bool = False
    mixed_content: bool = False
    images_without_alt: int = 0
    has_contact_form: bool = False
    has_click_to_call: bool = False
    has_og_tags: bool = False
    has_schema_org: bool = False
    has_favicon: bool = False
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    socials: list[str] = field(default_factory=list)
    pagespeed_mobile: Optional[int] = None
    pagespeed_seo: Optional[int] = None
    pagespeed_accessibility: Optional[int] = None
    pagespeed_best_practices: Optional[int] = None
    lcp_s: Optional[float] = None
    visual_score: Optional[int] = None
    visual_summary: str = ""
    visual_issues: list[str] = field(default_factory=list)
    screenshot_path: str = ""
    nips: list[str] = field(default_factory=list)  # NIP-y znalezione na stronie
    has_english: bool = False
    has_careers: bool = False
    has_linkedin: bool = False
    internal_links: int = 0
    error: str = ""


@dataclass
class Lead:
    business: Business
    audit: Optional[Audit]
    score: int
    tier: str
    lead_type: str  # BRAK_STRONY | TYLKO_SOCIAL | STRONA_NIE_DZIALA | SLABA_STRONA | OK
    reasons: list[str]
    pitch: str = ""

    @property
    def key(self) -> str:
        """Stały identyfikator firmy między wyszukiwaniami (do statusów rozmów)."""
        from .filters import _norm, domain_of

        b = self.business
        if b.nip:
            return "nip:" + b.nip
        d = domain_of(b.website)
        if d and "." in d and not any(h in d for h in ("facebook", "instagram", "google", "booksy")):
            return "www:" + d
        if b.source_id:
            return b.source + ":" + b.source_id
        return "na:" + _norm(b.name) + "|" + _norm(b.city)

    def to_row(self) -> dict:
        b, a = self.business, self.audit or Audit()
        socials = _uniq(b.socials + a.socials)[:4]
        return {
            "wynik": self.score,
            "priorytet": self.tier,
            "typ": self.lead_type,
            "nazwa": b.name,
            "branza": b.category,
            "miasto": b.city,
            "adres": b.address,
            "telefon": b.phone or (a.phones[0] if a.phones else ""),
            "wlasciciel": b.owner_name,
            "wlasciciel_info": b.owner_info,
            "wielkosc": b.size,
            "forma_prawna": b.legal_form,
            "nip": b.nip,
            "krs": b.krs,
            "kapital_zl": b.capital_pln if b.capital_pln is not None else "",
            "od_roku": b.registered[:4],
            "pkd": b.pkd,
            "zarzad": ", ".join(b.board),
            "email": ", ".join(_uniq(b.emails + a.emails)[:3]),
            "strona": b.website,
            "google_maps": b.maps_url,
            "ocena_google": b.rating if b.rating is not None else "",
            "liczba_opinii": b.reviews if b.reviews is not None else "",
            "pagespeed_mobile": a.pagespeed_mobile if a.pagespeed_mobile is not None else "",
            "ocena_wizualna_1_10": a.visual_score if a.visual_score is not None else "",
            "rok_copyright": a.copyright_year or "",
            "powody": " | ".join(self.reasons),
            "propozycja_kontaktu": self.pitch,
            "facebook": ", ".join(x for x in socials if "facebook.com" in x or "fb.com" in x),
            "instagram": ", ".join(x for x in socials if "instagram.com" in x),
            "inne_kontakty": " | ".join(
                [x for x in socials if not any(h in x for h in ("facebook.com", "fb.com", "instagram.com"))] + b.other_contacts
            ),
            "kontakty_uwagi": b.contacts_note,
            "zrodlo": b.source,
        }

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "score": self.score,
            "tier": self.tier,
            "lead_type": self.lead_type,
            "reasons": self.reasons,
            "pitch": self.pitch,
            "business": asdict(self.business),
            "audit": asdict(self.audit) if self.audit else None,
        }
