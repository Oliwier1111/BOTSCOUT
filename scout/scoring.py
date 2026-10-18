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


def _tier(score: int) -> str:
    return "A" if score >= 75 else "B" if score >= 55 else "C"


def score_lead(b: Business, a: Audit | None, pitch: str = "") -> Lead:
    bonus, extra = _activity_bonus(b)

    if not b.website:
        s = min(100, 85 + bonus)
        found = [n for n, ok in (("e-mail", b.emails), ("Facebook", any("facebook" in x for x in b.socials)),
                                 ("Instagram", any("instagram" in x for x in b.socials)),
                                 (", ".join(x.split(":")[0] for x in b.other_contacts), b.other_contacts)) if ok]
        if found:
            extra = [f"inne kontakty: {', '.join(found)}"] + extra
        return Lead(b, a, s, _tier(s), "BRAK_STRONY", ["firma nie ma strony internetowej"] + extra,
                    pitch or "Klienci szukają Was w Google, a nie ma strony – zróbmy prostą wizytówkę z telefonem, cennikiem i mapą.")
    if a and a.is_social_only:
        s = min(100, 80 + bonus)
        return Lead(b, a, s, _tier(s), "TYLKO_SOCIAL", [f"zamiast strony tylko profil: {b.website}"] + extra,
                    pitch or "Macie tylko profil w social mediach/portalu – własna strona da lepszą pozycję w Google i zaufanie.")
    if a and (not a.reachable or a.is_parked):
        why = "domena zaparkowana / pusta" if a.is_parked else f"strona nie działa ({a.error or a.status_code})"
        s = min(100, 82 + bonus)
        return Lead(b, a, s, _tier(s), "STRONA_NIE_DZIALA", [why] + extra,
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
    if a.visual_score is not None:
        vs = {1: 32, 2: 30, 3: 26, 4: 20, 5: 13, 6: 6, 7: 0, 8: -8, 9: -15, 10: -20}[a.visual_score]
        if vs > 0:
            add(vs, f"ocena wyglądu {a.visual_score}/10 ({a.visual_summary})")
        else:
            pts += vs
        weighted += [(1, f"widać: {x}") for x in a.visual_issues[:3]]

    reasons = [w for _, w in sorted(weighted, key=lambda x: -x[0])]
    s = max(0, min(95, pts + bonus))
    lead_type = "SLABA_STRONA" if s >= 35 else "OK"
    return Lead(b, a, s, _tier(s), lead_type, reasons + extra, pitch or _auto_pitch(reasons))


def _auto_pitch(reasons: list[str]) -> str:
    if not reasons:
        return ""
    top = "; ".join(r.split(" (")[0] for r in reasons[:3])
    return f"Na Waszej stronie widzimy: {top}. Możemy to poprawić albo zrobić nową, nowoczesną stronę."
