"""Google PageSpeed Insights (Lighthouse) – wynik mobilny, SEO, dostępność."""
from __future__ import annotations

import requests

import scout_config as config
from .models import Audit

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def run_pagespeed(a: Audit, log=lambda _: None) -> None:
    url = a.final_url or a.url
    if not url:
        return
    params = [
        ("url", url),
        ("strategy", "mobile"),
        ("locale", "pl"),
        ("category", "performance"),
        ("category", "seo"),
        ("category", "accessibility"),
        ("category", "best-practices"),
    ]
    if config.PAGESPEED_API_KEY:
        params.append(("key", config.PAGESPEED_API_KEY))
    try:
        r = requests.get(PSI_URL, params=params, timeout=90)
    except requests.RequestException as e:
        log(f"PageSpeed: brak odpowiedzi dla {url} ({type(e).__name__})")
        return
    if r.status_code != 200:
        log(f"PageSpeed {r.status_code} dla {url}: {r.text[:120]}")
        return
    lh = r.json().get("lighthouseResult", {})
    cats = lh.get("categories", {})

    def score(key: str):
        v = (cats.get(key) or {}).get("score")
        return int(round(v * 100)) if v is not None else None

    a.pagespeed_mobile = score("performance")
    a.pagespeed_seo = score("seo")
    a.pagespeed_accessibility = score("accessibility")
    a.pagespeed_best_practices = score("best-practices")
    lcp = (lh.get("audits", {}).get("largest-contentful-paint") or {}).get("numericValue")
    a.lcp_s = round(lcp / 1000, 1) if lcp else None
