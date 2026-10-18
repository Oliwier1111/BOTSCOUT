"""Techniczny audyt strony: dostępność, SSL, mobile, SEO, przestarzała technologia, kontakt."""
from __future__ import annotations

import re
import time
import datetime as dt
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

import scout_config as config
from .filters import domain_of, is_social
from .models import Audit

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?48[\s-]?)?(?:\d{3}[\s-]?\d{3}[\s-]?\d{3}|\(?\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2})")
YEAR_RE = re.compile(r"(?:©|&copy;|copyright|\(c\))\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", re.I)
BAD_EMAIL_END = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", "sentry.io", "example.com", "wixpress.com", "domain.com")

PARKED_MARKERS = [
    "domena jest na sprzedaż",
    "domena na sprzedaż",
    "domena na sprzedaz",
    "ta domena jest zaparkowana",
    "domain is for sale",
    "this domain is parked",
    "buy this domain",
    "parking domeny",
    "domena utrzymywana",
    "sedoparking",
    "parkingcrew",
    "hugedomains",
    "aftermarket.pl",
    "domena została zarejestrowana",
    "default web site page",
    "index of /",
    "apache2 ubuntu default page",
    "welcome to nginx",
    "account suspended",
    "konto zostało zablokowane",
    "strona została zawieszona",
]
CONSTRUCTION_MARKERS = [
    "strona w budowie",
    "strona w przygotowaniu",
    "under construction",
    "coming soon",
    "wkrótce otwarcie strony",
    "trwają prace",
    "przerwa techniczna",
]
CONTACT_PATHS = ["kontakt", "contact", "kontakt.html", "kontakt.php", "o-nas"]
SOCIAL_RE = re.compile(r"https?://(?:www\.)?(facebook|instagram|tiktok|linkedin|youtube)\.com/[^\s\"'<>]+", re.I)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.5"})
    return s


def _get(s: requests.Session, url: str, verify: bool = True):
    return s.get(url, timeout=config.REQUEST_TIMEOUT, allow_redirects=True, verify=verify)


def _clean_emails(text: str, site_domain: str) -> list[str]:
    found = []
    for e in EMAIL_RE.findall(text):
        e = e.strip(".").lower()
        if e.endswith(BAD_EMAIL_END) or e in found:
            continue
        found.append(e)
    # adresy z domeny firmy na początek
    found.sort(key=lambda e: 0 if site_domain and e.endswith(site_domain) else 1)
    return found[:5]


def _clean_phones(text: str) -> list[str]:
    out = []
    for p in PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", p)
        if len(digits) in (9, 11) and digits not in [re.sub(r"\D", "", x) for x in out]:
            out.append(p.strip())
    return out[:3]


def _detect_tech(html: str, soup: BeautifulSoup) -> tuple[str, list[str]]:
    issues: list[str] = []
    low = html.lower()
    gen = ""
    m = soup.find("meta", attrs={"name": re.compile("^generator$", re.I)})
    if m and m.get("content"):
        gen = m["content"].strip()[:60]
    g = gen.lower()
    mv = re.search(r"wordpress\s*(\d+)\.(\d+)", g)
    if mv and int(mv.group(1)) < 6:
        issues.append(f"stary WordPress {mv.group(1)}.{mv.group(2)}")
    mj = re.search(r"joomla!?\s*(?:-\s*open source content management\s*)?(?:version\s*)?(\d+)(?:\.(\d+))?", g)
    if mj and int(mj.group(1)) < 4:
        issues.append(f"stara Joomla {mj.group(1)}")
    if "joomla" in g and not mj:
        issues.append("Joomla (często przestarzała)")
    if any(x in g for x in ("frontpage", "dreamweaver", "word", "web page maker", "iweb", "kompozer")):
        issues.append(f"strona robiona w {gen}")
    if "<frameset" in low or "<frame " in low:
        issues.append("ramki (frameset)")
    if ".swf" in low or "shockwave-flash" in low:
        issues.append("Flash")
    if "<marquee" in low or "<blink" in low:
        issues.append("marquee/blink")
    if len(soup.find_all("font")) > 3:
        issues.append("tagi <font>")
    if len(soup.find_all("center")) > 1:
        issues.append("tagi <center>")
    if re.search(r"<!--\[if (lt )?ie", low):
        issues.append("hacki pod Internet Explorera")
    if re.search(r'<!doctype html public "-//w3c//dtd (x?html) (4|1\.0)', low):
        issues.append("stary DOCTYPE (HTML4/XHTML)")
    jq = re.search(r"jquery[.-]?(\d)\.(\d+)", low)
    if jq and (int(jq.group(1)), int(jq.group(2))) < (3, 0):
        issues.append(f"stary jQuery {jq.group(1)}.{jq.group(2)}")
    layout_tables = [t for t in soup.find_all("table") if t.get("width") or t.get("bgcolor") or t.get("cellpadding")]
    if len(layout_tables) >= 2:
        issues.append("układ strony na tabelkach")
    if "visitor counter" in low or "licznik odwiedzin" in low or "liczba odwiedzin" in low:
        issues.append("licznik odwiedzin")
    return gen, issues


def audit_site(url: str) -> Audit:
    a = Audit(url=url)
    if not url:
        return a
    if is_social(url):
        a.is_social_only = True
        a.socials = [url]
        return a
    if "://" not in url:
        url = "http://" + url
    s = _session()
    t0 = time.time()
    try:
        r = _get(s, url)
        a.ssl_ok = urlparse(r.url).scheme == "https"
    except requests.exceptions.SSLError:
        a.error = "błąd certyfikatu SSL"
        try:
            r = _get(s, url, verify=False)  # noqa: S501 - tylko do oceny wyglądu
        except requests.RequestException as e:
            a.error = f"strona nie odpowiada ({type(e).__name__})"
            return a
    except requests.RequestException as e:
        a.error = f"strona nie odpowiada ({type(e).__name__})"
        return a

    a.response_ms = int((time.time() - t0) * 1000)
    if "charset" not in r.headers.get("content-type", "").lower():
        r.encoding = r.apparent_encoding or "utf-8"
    a.status_code = r.status_code
    a.final_url = r.url
    a.https = urlparse(r.url).scheme == "https"
    a.reachable = r.status_code < 400
    a.page_kb = len(r.content) // 1024
    if not a.reachable:
        a.error = f"HTTP {r.status_code}"
        return a
    if is_social(r.url):
        a.is_social_only = True
        a.socials = [r.url]
        return a

    # Czy wersja http przekierowuje na https?
    host = urlparse(r.url).hostname or ""
    try:
        rh = s.get(f"http://{host}/", timeout=8, allow_redirects=True)
        a.http_redirects_to_https = urlparse(rh.url).scheme == "https"
    except requests.RequestException:
        a.http_redirects_to_https = None

    html = r.text
    low = html.lower()
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    tl = text.lower()

    a.is_parked = any(m in low for m in PARKED_MARKERS) or (len(text) < 300 and "domain" in tl)
    a.is_under_construction = any(m in tl for m in CONSTRUCTION_MARKERS) and len(text) < 2500
    a.has_viewport = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}) is not None
    a.title = (soup.title.string or "").strip()[:120] if soup.title and soup.title.string else ""
    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    a.meta_description = bool(md and md.get("content", "").strip())
    a.has_h1 = soup.find("h1") is not None
    a.has_og_tags = soup.find("meta", attrs={"property": re.compile("^og:", re.I)}) is not None
    a.has_schema_org = "schema.org" in low
    a.has_favicon = soup.find("link", rel=lambda v: bool(v) and "icon" in (v if isinstance(v, str) else " ".join(v)).lower()) is not None
    a.has_contact_form = any(
        f.find("textarea") is not None or f.find("input", attrs={"type": "email"}) is not None
        for f in soup.find_all("form")
    )
    a.has_click_to_call = "href=\"tel:" in low or "href='tel:" in low
    a.images_without_alt = sum(1 for img in soup.find_all("img") if not (img.get("alt") or "").strip())
    if a.https:
        a.mixed_content = bool(re.search(r'(?:src)=["\']http://', low))

    years = [int(y) for y in YEAR_RE.findall(html)]
    if years:
        a.copyright_year = max(y for y in years if y <= dt.date.today().year) if any(
            y <= dt.date.today().year for y in years
        ) else None
    a.generator, a.outdated_tech = _detect_tech(html, soup)

    site_domain = domain_of(r.url)
    blob = html
    a.socials = list(dict.fromkeys(m.group(0).rstrip("/\\\"'") for m in SOCIAL_RE.finditer(html)))[:5]

    # Podstrona kontakt – często tam jest e-mail
    if not EMAIL_RE.search(html):
        links = [x.get("href", "") for x in soup.find_all("a")]
        cand = [l for l in links if any(p in l.lower() for p in ("kontakt", "contact"))][:1]
        cand += [urljoin(r.url, "/" + p) for p in CONTACT_PATHS[:2]]
        for c in cand:
            try:
                rc = _get(s, urljoin(r.url, c), verify=False)
                if rc.status_code < 400 and EMAIL_RE.search(rc.text):
                    blob += rc.text
                    break
            except requests.RequestException:
                continue
    mailto = re.findall(r"mailto:([^\"'?>\s]+)", blob, re.I)
    a.emails = _clean_emails(" ".join(mailto) + " " + BeautifulSoup(blob, "html.parser").get_text(" "), site_domain)
    tel = re.findall(r"tel:([^\"'>\s]+)", blob, re.I)
    a.phones = _clean_phones(" ".join(tel) + " " + text)
    return a
