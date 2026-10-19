"""Wynik szansy sprzedażowej 0-100 (im wyżej, tym lepszy lead) + powody po polsku."""
from __future__ import annotations

import datetime as dt

from .models import Audit, Business, Lead


def _activity_bonus(b: Business) -> tuple[int, list[str]]:
    """Firma aktywna (opinie, oceny) = ma klientów i budżet na stronę."""
    reasons = []
    bonus = 0
    where = " w Google" if b.source == "google_places" else " na Booksy" if any("Booksy" in c for c in b.other_contacts) else ""
    if b.reviews:
        if b.reviews >= 100:
            bonus += 8
            reasons.append(f"aktywna firma ({b.reviews} opinii{where})")
        elif b.reviews >= 25:
            bonus += 5
            reasons.append(f"{b.reviews} opinii{where}")
        elif b.reviews >= 5:
            bonus += 2
    if b.rating and b.rating >= 4.5 and (b.reviews or 0) >= 10:
        bonus += 3
        reasons.append(f"świetne oceny ({b.rating}) – zadowoleni klienci, warto pokazać to na stronie")
    return bonus, reasons


SIZE_BONUS = {"DUŻA": 25, "ŚREDNIA": 15, "MAŁA": 0, "NIEZNANA": 0}


def _size_adjust(b: Business, segment: str) -> tuple[int, list[str]]:
    """W segmencie „duze” większa firma = większy budżet; mała dostaje karę, żeby spadła na dół listy."""
    if segment != "duze" or not b.size:
        return 0, []
    if b.size == "MAŁA":
        return -15, []
    if b.size == "NIEZNANA":
        return 0, []
    return SIZE_BONUS[b.size], [f"{b.size.lower()} firma: " + ", ".join(b.size_reasons[:3])] if b.size_reasons else []


def _tier(score: int, segment: str = "male") -> str:
    if segment == "duze":  # duże firmy rzadko mają „katastrofalną” stronę – liczy się wielkość × słabość strony
        return "A" if score >= 60 else "B" if score >= 42 else "C"
    return "A" if score >= 75 else "B" if score >= 55 else "C"


def score_lead(b: Business, a: Audit | None, pitch: str = "", segment: str | None = None) -> Lead:
    lead = _score_lead(b, a, pitch, segment)
    import scout_config as config

    if (segment or config.SEGMENT) == "duze" and lead.score > 45 and b.size in ("MAŁA", "NIEZNANA") and not b.nip:
        # w trybie większych firm mała / nieznana firma nie może wyprzedzić średnich i dużych
        lead.score = 45 if b.size == "NIEZNANA" else 38
        lead.tier = _tier(lead.score, "duze")
    return lead


def _score_lead(b: Business, a: Audit | None, pitch: str = "", segment: str | None = None) -> Lead:
    import scout_config as config

    segment = segment or config.SEGMENT
    bonus, extra = _activity_bonus(b)
    sb, sx = _size_adjust(b, segment)
    bonus += sb
    extra = sx + extra

    if not b.website and segment == "duze" and b.source == "openstreetmap":
        # Większe firmy prawie zawsze mają stronę – w OSM po prostu jej nie wpisano. To lead „do sprawdzenia”, nie pewniak.
        s = max(0, min(60, (50 if b.phone else 30) + bonus))
        return Lead(b, a, s, _tier(s, segment), "BRAK_STRONY",
                    ["w mapie nie ma adresu strony – sprawdź w Google, czy firma ma stronę"] + extra,
                    pitch or "Szukając firm z Waszej branży, nie trafiliśmy na Waszą stronę – czy macie stronę, która zbiera zapytania ofertowe?")
    if not b.website:
        s = min(100, 85 + bonus)
        found = [n for n, ok in (("e-mail", b.emails), ("Facebook", any("facebook" in x for x in b.socials)),
                                 ("Instagram", any("instagram" in x for x in b.socials)),
                                 (", ".join(x.split(":")[0] for x in b.other_contacts), b.other_contacts)) if ok]
        if found:
            extra = [f"inne kontakty: {', '.join(found)}"] + extra
        return Lead(b, a, s, _tier(s, segment), "BRAK_STRONY", ["firma nie ma strony internetowej"] + extra,
                    pitch or "Klienci szukają Was w Google, a nie ma strony – zróbmy prostą wizytówkę z telefonem, cennikiem i mapą.")
    if a and a.is_social_only:
        s = min(100, 80 + bonus)
        return Lead(b, a, s, _tier(s, segment), "TYLKO_SOCIAL", [f"zamiast strony tylko profil: {b.website}"] + extra,
                    pitch or "Macie tylko profil w social mediach/portalu – własna strona da lepszą pozycję w Google i zaufanie.")
    if a and not a.reachable and not a.is_parked and segment == "duze" and b.source == "openstreetmap" and b.size in ("", "NIEZNANA"):
        # adres z mapy bywa nieaktualny – firma mogła zmienić domenę; najpierw sprawdzić w Google
        s = max(0, min(60, (50 if b.phone else 30) + bonus))
        return Lead(b, a, s, _tier(s, segment), "STRONA_NIE_DZIALA",
                    [f"adres strony z mapy nie działa ({a.error or a.status_code}) – sprawdź w Google, czy firma nie ma nowej domeny"] + extra,
                    pitch or "Link do Waszej strony, który jest w mapach, nie działa – klienci trafiają na błąd. Czy macie nową stronę?")
    if a and (not a.reachable or a.is_parked):
        why = "domena zaparkowana / pusta" if a.is_parked else f"strona nie działa ({a.error or a.status_code})"
        s = min(100, 82 + bonus)
        return Lead(b, a, s, _tier(s, segment), "STRONA_NIE_DZIALA", [why] + extra,
                    pitch or "Link do strony w Google prowadzi do niedziałającej strony – tracicie klientów, naprawmy to.")

    a = a or Audit()
    pts = 0
    weighted: list[tuple[int, str]] = []

    def add(p: int, why: str):
        nonlocal pts
        pts += p
        weighted.append((p, why))

    if a.is_under_construction:
        add(30, "strona „w budowie” / prawie pusta")
    if not a.https:
        add(14, "brak HTTPS (przeglądarka pokazuje „niezabezpieczona”)")
    elif not a.ssl_ok:
        add(14, "błędny certyfikat SSL")
    elif a.http_redirects_to_https is False:
        add(4, "wersja http nie przekierowuje na https")
    if not a.has_viewport:
        add(18, "brak wersji mobilnej (brak meta viewport)")
    if a.outdated_tech:
        add(min(20, 7 * len(a.outdated_tech)), "przestarzała technologia: " + ", ".join(a.outdated_tech[:4]))
    if a.copyright_year:
        age = dt.date.today().year - a.copyright_year
        if age >= 6:
            add(14, f"stopka © {a.copyright_year} – strona nieaktualizowana od lat")
        elif age >= 3:
            add(7, f"stopka © {a.copyright_year}")
    if a.pagespeed_mobile is not None:
        if a.pagespeed_mobile < 30:
            add(14, f"bardzo wolna na telefonie (PageSpeed {a.pagespeed_mobile}/100)")
        elif a.pagespeed_mobile < 50:
            add(8, f"wolna na telefonie (PageSpeed {a.pagespeed_mobile}/100)")
    elif a.response_ms and a.response_ms > 4000:
        add(6, f"wolna odpowiedź serwera ({a.response_ms/1000:.1f} s)")
    if a.lcp_s and a.lcp_s > 6:
        add(4, f"główna treść ładuje się {a.lcp_s} s")
    if a.pagespeed_seo is not None and a.pagespeed_seo < 70:
        add(6, f"słabe SEO (Lighthouse {a.pagespeed_seo}/100)")
    if not a.title:
        add(6, "brak tytułu strony")
    if not a.meta_description:
        add(4, "brak opisu meta (słabszy wygląd w Google)")
    if not a.has_h1:
        add(2, "brak nagłówka H1")
    if not a.has_click_to_call:
        add(3, "brak klikalnego telefonu")
    if not a.has_contact_form and not a.emails:
        add(3, "brak formularza i e-maila")
    if a.mixed_content:
        add(3, "mieszana treść http/https")
    if a.page_kb and a.page_kb > 5000:
        add(3, f"ciężka strona główna ({a.page_kb//1024} MB)")
    if not a.has_favicon:
        add(2, "brak ikony strony (favicon)")
    if segment == "duze":
        if not a.has_english:
            add(6, "brak wersji angielskiej – klienci i partnerzy z zagranicy nie przeczytają oferty")
        if not a.has_contact_form:
            add(4, "brak formularza zapytania ofertowego – zapytania B2B uciekają")
        if not a.has_schema_org:
            add(2, "brak danych strukturalnych (schema.org) – słabsza wizytówka w Google")
        if not a.has_og_tags:
            add(2, "brak podglądu linku (Open Graph) – link w LinkedIn/mailu wygląda źle")
    if a.visual_score is not None:
        vs = {1: 32, 2: 30, 3: 26, 4: 20, 5: 13, 6: 6, 7: 0, 8: -8, 9: -15, 10: -20}[a.visual_score]
        if vs > 0:
            add(vs, f"ocena wyglądu {a.visual_score}/10 ({a.visual_summary})")
        else:
            pts += vs
        weighted += [(1, f"widać: {x}") for x in a.visual_issues[:3]]

    reasons = [w for _, w in sorted(weighted, key=lambda x: -x[0])]
    lead_type = "SLABA_STRONA" if pts >= 20 else "OK"
    s = max(0, min(95, pts + bonus))
    if lead_type == "OK":
        s = min(s, 34)  # strona w porządku = słaby lead niezależnie od wielkości firmy
    return Lead(b, a, s, _tier(s, segment), lead_type, reasons + extra, pitch or _auto_pitch(reasons, segment))


def _auto_pitch(reasons: list[str], segment: str = "male") -> str:
    if not reasons:
        return ""
    top = "; ".join(r.split(" (")[0].split(" – ")[0] for r in reasons[:3])
    if segment == "duze":
        return (f"Przejrzeliśmy Waszą stronę pod kątem pozyskiwania zapytań: {top}. "
                "Dla firmy Waszej skali strona to handlowiec 24/7 – pokażemy w 15 minut, co konkretnie poprawić.")
    return f"Na Waszej stronie widzimy: {top}. Możemy to poprawić albo zrobić nową, nowoczesną stronę."


def call_script(lead: Lead, segment: str = "duze") -> dict:
    """Skrypt rozmowy dla handlowca: otwarcie, pytanie o decydenta, hak z audytu, cel rozmowy, odpowiedzi na obiekcje."""
    b, a = lead.business, lead.audit
    from .filters import domain_of

    site = domain_of(b.website) or "Waszej strony"
    hooks = [r.split(" (")[0].split(" – ")[0] for r in lead.reasons if not r.startswith(("aktywna firma", "świetne oceny", "duża firma", "średnia firma"))][:3]
    who = "osobą odpowiedzialną za marketing albo stronę internetową"
    if b.board:
        who += f" (w KRS: {b.board[0].lower()})"
    if lead.lead_type == "BRAK_STRONY":
        hook = "Szukając firm z Waszej branży w okolicy, nie znaleźliśmy Waszej strony internetowej."
    elif lead.lead_type == "STRONA_NIE_DZIALA":
        hook = f"Link do strony, który jest w Google, prowadzi do strony, która nie działa ({site})."
    elif lead.lead_type == "TYLKO_SOCIAL":
        hook = "W Google zamiast strony firmy pokazuje się tylko profil w social mediach."
    else:
        hook = f"Zrobiliśmy krótki audyt {site} i wyszły trzy rzeczy: " + "; ".join(hooks) + "."
    return {
        "otwarcie": f"Dzień dobry, [imię] ze Spark Scale. Czy rozmawiam z {who}? "
                    "Jeśli nie – kto u Państwa o tym decyduje i jak najlepiej się z tą osobą skontaktować?",
        "zgoda": "Mam jedną konkretną obserwację o Waszej stronie – czy mogę zająć 2 minuty?",
        "hak": hook,
        "pytanie": "Ile zapytań ofertowych miesięcznie przychodzi dziś przez stronę? Kto je obsługuje?"
                   if segment == "duze" else "Skąd dziś przychodzą do Was nowi klienci?",
        "cel": "Proponuję 15 minut online – pokażę audyt na ekranie i 2–3 rzeczy do poprawy od ręki. Pasuje wtorek czy czwartek?",
        "obiekcje": [
            ["Mamy firmę, która robi nam stronę", "Rozumiem. Audyt jest niezależny – może się przydać także Waszej firmie. Czy mogę go wysłać mailem do przekazania?"],
            ["Nie mamy teraz budżetu", "Jasne. Część poprawek to godzina pracy. Pokażę, co da efekt najtaniej – decyzja zostaje po Waszej stronie."],
            ["Proszę wysłać maila", "Chętnie. Na jaki adres i do kogo – żeby trafiło do osoby, która decyduje? Wyślę audyt w PDF."],
            ["Strona nam wystarczy", "Rozumiem. Dla porównania: firmy z Waszej branży mają formularz zapytań i wersję EN – sprawdźmy, czy to Wam coś daje."],
        ],
    }
